from __future__ import annotations

import tempfile
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

from .kiwoom_api import KIWOOM_OPENAPI_PAGE
from .models import PatternState, StrategySettings
from .service import AutoTradingService
from .storage import Storage


PATTERN_LABEL_TO_VALUE: dict[str, PatternState] = {
    "없음": "NONE",
    "강세": "BULLISH",
    "약세": "BEARISH",
}
PATTERN_VALUE_TO_LABEL = {value: label for label, value in PATTERN_LABEL_TO_VALUE.items()}
ACTION_LABELS = {"BUY": "매수", "SELL": "매도", "HOLD": "대기", "NONE": "없음"}
SIDE_LABELS = {"BUY": "매수", "SELL": "매도"}
LEVEL_LABELS = {"INFO": "정보", "WARN": "주의", "ERROR": "오류"}


class TraderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("키움 자동매매")
        self.geometry("1120x740")
        self.minsize(980, 640)

        db_path = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_ko.sqlite3"
        self.service = AutoTradingService(storage=Storage(db_path))
        self._refresh_after_id: str | None = None
        self._account_after_id: str | None = None
        self._account_poll_count = 0
        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self, padding=12)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="키움 자동매매 - 모의 운용 모드", font=("Malgun Gothic", 16, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.account_button = ttk.Button(header, text="키움 계좌 연결", command=self._connect_account)
        self.account_button.grid(row=0, column=1, padx=(0, 8))
        ttk.Button(header, text="연결환경 확인", command=self._check_account_environment).grid(
            row=0, column=2, padx=(0, 8)
        )
        ttk.Button(header, text="OpenAPI 설치", command=self._open_kiwoom_openapi_page).grid(
            row=0, column=3, padx=(0, 8)
        )
        ttk.Button(header, text="긴급 정지", command=self._emergency_stop).grid(row=0, column=4)

        controls = ttk.LabelFrame(self, text="설정", padding=12)
        controls.grid(row=1, column=0, sticky="ew", padx=12)
        for idx in range(8):
            controls.columnconfigure(idx, weight=1)

        self.symbol_var = tk.StringVar(value="005930")
        self.capital_var = tk.StringVar(value="1000000")
        self.price_var = tk.StringVar(value="72000")
        self.period_var = tk.StringVar(value="20")
        self.upper_var = tk.StringVar(value="100")
        self.pattern_var = tk.StringVar(value="없음")
        self.use_cci_var = tk.BooleanVar(value=True)
        self.account_var = tk.StringVar(value="")
        self.account_password_var = tk.StringVar(value="")
        self.order_qty_var = tk.StringVar(value="1")
        self.allow_real_order_var = tk.BooleanVar(value=False)

        self._field(controls, "종목 코드", self.symbol_var, 0)
        self._field(controls, "운용 한도", self.capital_var, 1)
        self._field(controls, "모의 현재가", self.price_var, 2)
        self._field(controls, "CCI 기간", self.period_var, 3)
        self._field(controls, "CCI 상단", self.upper_var, 4)

        ttk.Label(controls, text="패턴").grid(row=0, column=5, sticky="w")
        ttk.Combobox(
            controls,
            textvariable=self.pattern_var,
            values=tuple(PATTERN_LABEL_TO_VALUE.keys()),
            state="readonly",
            width=12,
        ).grid(row=1, column=5, sticky="ew", padx=(0, 8))

        ttk.Checkbutton(controls, text="CCI 필터 사용", variable=self.use_cci_var).grid(
            row=1, column=6, sticky="w"
        )
        ttk.Button(controls, text="저장", command=self._save_settings).grid(row=1, column=7, sticky="ew")

        actions = ttk.Frame(controls)
        actions.grid(row=2, column=0, columnspan=8, sticky="ew", pady=(12, 0))
        ttk.Button(actions, text="운용 시작", command=self._start).pack(side="left")
        ttk.Button(actions, text="운용 중지", command=self._stop).pack(side="left", padx=6)
        ttk.Button(actions, text="1틱 실행", command=self._run_tick).pack(side="left")
        ttk.Button(actions, text="매도 실패 모의", command=self._fail_sell).pack(side="left", padx=6)

        kiwoom_controls = ttk.Frame(controls)
        kiwoom_controls.grid(row=3, column=0, columnspan=8, sticky="ew", pady=(12, 0))
        ttk.Label(kiwoom_controls, text="계좌번호").pack(side="left")
        ttk.Entry(kiwoom_controls, textvariable=self.account_var, width=14).pack(side="left", padx=(4, 8))
        ttk.Label(kiwoom_controls, text="계좌비밀번호").pack(side="left")
        ttk.Entry(
            kiwoom_controls,
            textvariable=self.account_password_var,
            width=10,
            show="*",
        ).pack(side="left", padx=(4, 8))
        ttk.Label(kiwoom_controls, text="주문수량").pack(side="left")
        ttk.Entry(kiwoom_controls, textvariable=self.order_qty_var, width=6).pack(side="left", padx=(4, 8))
        ttk.Checkbutton(
            kiwoom_controls,
            text="실거래 주문 잠금 해제",
            variable=self.allow_real_order_var,
        ).pack(side="left")

        api_actions = ttk.Frame(controls)
        api_actions.grid(row=4, column=0, columnspan=8, sticky="ew", pady=(8, 0))
        ttk.Button(api_actions, text="현재가 조회", command=self._request_current_price).pack(side="left")
        ttk.Button(api_actions, text="3분봉 조회", command=self._request_three_minute).pack(side="left", padx=4)
        ttk.Button(api_actions, text="잔고 조회", command=self._request_balance).pack(side="left", padx=4)
        ttk.Button(api_actions, text="실시간 등록", command=self._register_real_time).pack(side="left", padx=4)
        ttk.Button(api_actions, text="실시간 해제", command=self._unregister_real_time).pack(side="left", padx=4)
        ttk.Button(api_actions, text="실제데이터 전략판단", command=self._evaluate_market_strategy).pack(
            side="left",
            padx=4,
        )
        ttk.Button(api_actions, text="SendOrder 매수", command=lambda: self._send_order("BUY")).pack(
            side="left",
            padx=(10, 4),
        )
        ttk.Button(api_actions, text="SendOrder 매도", command=lambda: self._send_order("SELL")).pack(
            side="left",
            padx=4,
        )

        body = ttk.Frame(self, padding=12)
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(1, weight=1)

        self.status_text = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.status_text, font=("Malgun Gothic", 11)).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12)
        )

        self.orders = self._table(
            body,
            "최근 주문",
            ("시간", "종목", "구분", "수량", "가격", "결과", "메시지"),
            0,
        )
        self.logs = self._table(body, "최근 로그", ("시간", "레벨", "분류", "메시지"), 1)

    def _field(self, parent: ttk.Frame, label: str, variable: tk.StringVar, column: int) -> None:
        ttk.Label(parent, text=label).grid(row=0, column=column, sticky="w")
        ttk.Entry(parent, textvariable=variable, width=14).grid(
            row=1, column=column, sticky="ew", padx=(0, 8)
        )

    def _table(self, parent: ttk.Frame, title: str, columns: tuple[str, ...], column: int) -> ttk.Treeview:
        frame = ttk.LabelFrame(parent, text=title, padding=8)
        frame.grid(row=1, column=column, sticky="nsew", padx=(0 if column == 0 else 6, 0))
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        table = ttk.Treeview(frame, columns=columns, show="headings", height=16)
        for name in columns:
            table.heading(name, text=name)
            table.column(name, width=90, stretch=True)
        table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        table.configure(yscrollcommand=scrollbar.set)
        return table

    def _save_settings(self) -> None:
        settings = StrategySettings(
            cci_period=max(1, int(float(self.period_var.get()))),
            cci_upper=float(self.upper_var.get()),
            cci_lower=-abs(float(self.upper_var.get())),
            use_cci_filter=self.use_cci_var.get(),
        )
        self.service.configure(self.symbol_var.get(), float(self.capital_var.get()), settings)
        self._refresh()

    def _start(self) -> None:
        self._save_settings()
        self.service.start()
        self._refresh()

    def _stop(self) -> None:
        self.service.stop()
        self._refresh()

    def _emergency_stop(self) -> None:
        self.service.emergency_stop()
        self._refresh()

    def _connect_account(self) -> None:
        self.account_button.configure(state="disabled")
        self._account_poll_count = 0
        message = self.service.start_account_connection()
        self.status_text.set(f"키움 계좌 연결: {message}")
        if "로그인 창" in message or "이미" in message:
            self._schedule_account_poll()
        else:
            self.account_button.configure(state="normal")
            self._refresh()

    def _check_account_environment(self) -> None:
        message = self.service.check_account_environment()
        self.status_text.set(f"키움 연결환경: {message}")
        self._refresh()

    def _open_kiwoom_openapi_page(self) -> None:
        webbrowser.open(KIWOOM_OPENAPI_PAGE)
        self.service.storage.log("INFO", "계좌", "키움 OpenAPI+ 설치 페이지를 열었습니다.")
        self._refresh()

    def _schedule_account_poll(self) -> None:
        if self._account_after_id is None:
            self._account_after_id = self.after(1000, self._poll_account_connection)

    def _poll_account_connection(self) -> None:
        self._account_after_id = None
        self._account_poll_count += 1
        account_info = self.service.refresh_account_connection()
        if account_info.accounts and not self.account_var.get().strip():
            self.account_var.set(account_info.accounts[0])
        self._refresh()
        if account_info.connected or self._account_poll_count >= 120:
            self.account_button.configure(state="normal")
            return
        self._schedule_account_poll()

    def _fail_sell(self) -> None:
        broker = self.service.broker
        if hasattr(broker, "fail_next_sell"):
            broker.fail_next_sell = True
        self.service.storage.log("WARN", "모의", "다음 매도 주문을 1회 실패 처리합니다.")
        self._refresh()

    def _run_tick(self) -> None:
        pattern = PATTERN_LABEL_TO_VALUE.get(self.pattern_var.get(), "NONE")
        self.service.step(pattern, float(self.price_var.get()))
        self._refresh()

    def _request_current_price(self) -> None:
        self.service.configure(self.symbol_var.get(), float(self.capital_var.get()), self._settings())
        self.service.request_current_price(self.symbol_var.get())
        self._refresh()

    def _request_three_minute(self) -> None:
        self.service.configure(self.symbol_var.get(), float(self.capital_var.get()), self._settings())
        self.service.request_three_minute_candles(self.symbol_var.get())
        self._refresh()

    def _request_balance(self) -> None:
        self.service.request_balance(self.account_var.get(), self.account_password_var.get())
        self._refresh()

    def _register_real_time(self) -> None:
        self.service.configure(self.symbol_var.get(), float(self.capital_var.get()), self._settings())
        self.service.register_real_time_price(self.symbol_var.get())
        self._refresh()

    def _unregister_real_time(self) -> None:
        self.service.unregister_real_time()
        self._refresh()

    def _evaluate_market_strategy(self) -> None:
        self.service.configure(self.symbol_var.get(), float(self.capital_var.get()), self._settings())
        self.service.evaluate_strategy_with_market_data(self.symbol_var.get())
        self._refresh()

    def _send_order(self, side: str) -> None:
        allow_real = self.allow_real_order_var.get()
        if allow_real:
            confirmed = messagebox.askyesno(
                "실거래 주문 확인",
                "실거래 주문 잠금이 해제되어 있습니다.\n"
                "모의투자 서버가 아닌 경우 실제 주문이 전송될 수 있습니다.\n"
                "계속하시겠습니까?",
            )
            if not confirmed:
                return
        self.service.configure(self.symbol_var.get(), float(self.capital_var.get()), self._settings())
        self.service.send_kiwoom_order(
            account=self.account_var.get(),
            side=side,
            quantity=max(1, int(float(self.order_qty_var.get()))),
            allow_real_order=allow_real,
        )
        self._refresh()

    def _refresh(self) -> None:
        snapshot = self.service.snapshot()
        decision_key = snapshot.decision.action if snapshot.decision else "NONE"
        decision = ACTION_LABELS.get(decision_key, decision_key)
        cci = ""
        if snapshot.decision and snapshot.decision.cci_value is not None:
            cci = f" | CCI {snapshot.decision.cci_value:.2f}"
        account = snapshot.account_info
        account_label = "연결됨" if account.connected else "미연결"
        account_detail = account.user_name or account.message
        if account.accounts:
            account_detail = f"{account_detail} {', '.join(account.accounts)}".strip()
        quote = snapshot.real_time_quote or snapshot.market_quote
        quote_label = ""
        if quote:
            quote_label = f" | 시세 {quote.symbol} {quote.current_price:,.0f}"
        balance_label = ""
        if snapshot.balance_summary:
            balance_label = (
                f" | 잔고 {len(snapshot.balance_summary.holdings)}종목 "
                f"평가 {snapshot.balance_summary.total_evaluation:,.0f}"
            )
        self.status_text.set(
            " | ".join(
                [
                    f"키움 계좌 {account_label}",
                    account_detail,
                    f"모의 연결 {snapshot.connection}",
                    f"운용 {'중' if snapshot.running else '중지'}",
                    f"종목 {snapshot.symbol}",
                    f"패턴 {PATTERN_VALUE_TO_LABEL.get(snapshot.pattern, snapshot.pattern)}",
                    f"현재가 {snapshot.price:,.0f}",
                    f"보유 {snapshot.quantity}주",
                    f"평균 {snapshot.average_price:,.0f}",
                    f"판단 {decision}{cci}{quote_label}{balance_label}",
                    snapshot.last_api_message,
                ]
            )
        )
        self._replace_rows(self.orders, self._format_orders(snapshot.orders))
        self._replace_rows(self.logs, self._format_logs(snapshot.logs))
        self._schedule_auto_tick()

    def _auto_tick(self) -> None:
        self._refresh_after_id = None
        if self.service.running:
            self._run_tick()
        else:
            self._refresh()

    def _schedule_auto_tick(self) -> None:
        if self._refresh_after_id is None:
            self._refresh_after_id = self.after(3000, self._auto_tick)

    def _replace_rows(self, table: ttk.Treeview, rows: list[tuple]) -> None:
        for item in table.get_children():
            table.delete(item)
        for row in rows:
            table.insert("", "end", values=row)

    def _format_orders(self, rows: list[tuple]) -> list[tuple]:
        formatted = []
        for timestamp, symbol, side, quantity, price, success, message in rows:
            formatted.append(
                (
                    timestamp,
                    symbol,
                    SIDE_LABELS.get(side, side),
                    quantity,
                    f"{price:,.0f}",
                    "성공" if success else "실패",
                    message,
                )
            )
        return formatted

    def _format_logs(self, rows: list[tuple]) -> list[tuple]:
        formatted = []
        for timestamp, level, category, message in rows:
            formatted.append((timestamp, LEVEL_LABELS.get(level, level), category, message))
        return formatted

    def _settings(self) -> StrategySettings:
        return StrategySettings(
            cci_period=max(1, int(float(self.period_var.get()))),
            cci_upper=float(self.upper_var.get()),
            cci_lower=-abs(float(self.upper_var.get())),
            use_cci_filter=self.use_cci_var.get(),
        )
