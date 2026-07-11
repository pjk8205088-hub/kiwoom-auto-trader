from __future__ import annotations

import tempfile
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

from .kiwoom_api import (
    KIWOOM_HOME_PAGE,
    KIWOOM_MULTI_LOGIN_HELP,
    KIWOOM_OPENAPI_INSTALLER,
    KIWOOM_OPENAPI_PAGE,
)
from .models import PatternState, StrategySettings
from .service import AutoTradingService
from .storage import Storage
from .symbols import clean_account_number, mask_account_number, normalize_symbol


PATTERN_LABEL_TO_VALUE: dict[str, PatternState] = {
    "없음": "NONE",
    "강세": "BULLISH",
    "약세": "BEARISH",
}
PATTERN_VALUE_TO_LABEL = {value: label for label, value in PATTERN_LABEL_TO_VALUE.items()}
ACTION_LABELS = {"BUY": "매수", "SELL": "매도", "HOLD": "대기", "NONE": "없음"}
SIDE_LABELS = {"BUY": "매수", "SELL": "매도"}
LEVEL_LABELS = {"INFO": "정보", "WARN": "주의", "ERROR": "오류"}


class KiwoomLoginDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, default_user_id: str = "pjk82050") -> None:
        super().__init__(parent)
        self.title("키움 ID 로그인")
        self.resizable(False, False)
        self.result: tuple[str, str] | None = None
        self.user_id_var = tk.StringVar(value=default_user_id)
        self.password_var = tk.StringVar(value="")

        self.transient(parent)
        self.grab_set()

        body = ttk.Frame(self, padding=18)
        body.grid(row=0, column=0, sticky="nsew")
        ttk.Label(body, text="키움증권 ID 로그인", font=("Malgun Gothic", 14, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )
        ttk.Label(body, text="ID").grid(row=1, column=0, sticky="w", pady=(0, 8))
        user_entry = ttk.Entry(body, textvariable=self.user_id_var, width=28)
        user_entry.grid(row=1, column=1, sticky="ew", pady=(0, 8))
        ttk.Label(body, text="비밀번호").grid(row=2, column=0, sticky="w", pady=(0, 8))
        password_entry = ttk.Entry(body, textvariable=self.password_var, width=28, show="*")
        password_entry.grid(row=2, column=1, sticky="ew", pady=(0, 8))
        ttk.Label(
            body,
            text="로그인을 누르면 키움 OpenAPI+ 공식 로그인 절차를 시작합니다. 홈페이지 로그인과 별도이며, 비밀번호는 저장하지 않습니다.",
            wraplength=360,
            foreground="#555555",
        ).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(2, 14))

        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        ttk.Button(
            buttons,
            text="키움 홈페이지 열기",
            command=lambda: webbrowser.open(KIWOOM_HOME_PAGE),
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(buttons, text="로그인", command=self._submit).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(buttons, text="취소", command=self._cancel).grid(row=0, column=2, padx=(8, 0))

        password_entry.focus_set()
        self.bind("<Return>", lambda _event: self._submit())
        self.bind("<Escape>", lambda _event: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _submit(self) -> None:
        self.result = (self.user_id_var.get().strip(), self.password_var.get())
        self.password_var.set("")
        self.destroy()

    def _cancel(self) -> None:
        self.password_var.set("")
        self.result = None
        self.destroy()


class TraderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("키움 자동매매")
        self.geometry("1280x820")
        self.minsize(1180, 700)

        db_path = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_ko.sqlite3"
        self.service = AutoTradingService(storage=Storage(db_path))
        self._refresh_after_id: str | None = None
        self._account_after_id: str | None = None
        self._symbol_lookup_after_id: str | None = None
        self._account_poll_count = 0
        self._selected_account_full = ""
        self._account_info_window: tk.Toplevel | None = None
        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.connection_state_var = tk.StringVar(value="OFF 연결 안됨")

        header = ttk.Frame(self, padding=12)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="키움 자동매매 - 계좌/시세/주문 준비", font=("Malgun Gothic", 16, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.account_button = ttk.Button(header, text="키움 로그인", command=self._open_login_dialog)
        self.account_button.grid(row=0, column=1, padx=(0, 8))
        self.connection_light = tk.Canvas(header, width=18, height=18, highlightthickness=0, bd=0)
        self.connection_light.grid(row=0, column=2, padx=(0, 6))
        self._connection_light_id = self.connection_light.create_oval(
            2,
            2,
            16,
            16,
            fill="#b0b0b0",
            outline="#808080",
        )
        self.connection_badge = tk.Label(
            header,
            textvariable=self.connection_state_var,
            bg="#7a7a7a",
            fg="white",
            font=("Malgun Gothic", 10, "bold"),
            padx=12,
            pady=4,
        )
        self.connection_badge.grid(row=0, column=3, padx=(0, 8))
        ttk.Button(header, text="연결 상태 확인", command=self._check_account_environment).grid(
            row=0, column=4, padx=(0, 8)
        )
        ttk.Button(header, text="OpenAPI+ 설치파일 받기", command=self._open_kiwoom_installer).grid(
            row=0, column=5, padx=(0, 8)
        )
        ttk.Button(header, text="키움 공식 안내", command=self._open_kiwoom_openapi_page).grid(
            row=0, column=6, padx=(0, 8)
        )
        ttk.Button(header, text="멀티로그인 안내", command=self._open_multi_login_help).grid(
            row=0, column=7, padx=(0, 8)
        )
        ttk.Button(header, text="긴급 정지", command=self._emergency_stop).grid(row=0, column=8)
        self._update_connection_badge(False)

        controls = ttk.LabelFrame(self, text="1. 종목/전략/계좌 설정", padding=12)
        controls.grid(row=1, column=0, sticky="ew", padx=12)
        for idx in range(9):
            controls.columnconfigure(idx, weight=1)

        self.symbol_var = tk.StringVar(value="005930")
        self.symbol_name_var = tk.StringVar(value="삼성전자")
        self.capital_var = tk.StringVar(value="1000000")
        self.price_var = tk.StringVar(value="72000")
        self.period_var = tk.StringVar(value="20")
        self.upper_var = tk.StringVar(value="100")
        self.pattern_var = tk.StringVar(value="없음")
        self.use_cci_var = tk.BooleanVar(value=True)
        self.account_var = tk.StringVar(value="")
        self.account_first_var = tk.StringVar(value="")
        self.account_last_var = tk.StringVar(value="")
        self.account_password_var = tk.StringVar(value="")
        self.order_qty_var = tk.StringVar(value="1")
        self.allow_real_order_var = tk.BooleanVar(value=False)
        self.kiwoom_auto_order_var = tk.BooleanVar(value=True)
        self.account_summary_var = tk.StringVar(
            value="계좌 창: 로그인 전입니다. 키움 로그인 후 계좌번호 앞4자리+뒤4자리와 잔고가 표시됩니다."
        )
        self.trade_ready_var = tk.StringVar(value="거래 준비: 종목번호와 회사명, 계좌번호를 확인해 주세요.")
        self.symbol_var.trace_add("write", self._on_symbol_input_changed)

        self._field(controls, "종목번호(6자리)", self.symbol_var, 0)
        ttk.Label(controls, text="회사명").grid(row=0, column=1, sticky="w")
        ttk.Label(controls, textvariable=self.symbol_name_var, width=16).grid(
            row=1, column=1, sticky="ew", padx=(0, 8)
        )
        self._field(controls, "종목별 운용 한도금액", self.capital_var, 2)
        self._field(controls, "모의 현재가(테스트용)", self.price_var, 3)
        self._field(controls, "CCI 계산 기간", self.period_var, 4)
        self._field(controls, "CCI 과매수 기준", self.upper_var, 5)

        ttk.Label(controls, text="강세/약세 상태").grid(row=0, column=6, sticky="w")
        ttk.Combobox(
            controls,
            textvariable=self.pattern_var,
            values=tuple(PATTERN_LABEL_TO_VALUE.keys()),
            state="readonly",
            width=12,
        ).grid(row=1, column=6, sticky="ew", padx=(0, 8))

        ttk.Checkbutton(controls, text="CCI 필터로 잦은 매매 방지", variable=self.use_cci_var).grid(
            row=1, column=7, sticky="w"
        )
        ttk.Button(controls, text="설정 저장", command=self._save_settings).grid(row=1, column=8, sticky="ew")

        actions = ttk.Frame(controls)
        actions.grid(row=2, column=0, columnspan=9, sticky="ew", pady=(12, 0))
        ttk.Button(actions, text="자동 운용 시작", command=self._start).pack(side="left")
        ttk.Button(actions, text="자동 운용 중지", command=self._stop).pack(side="left", padx=6)
        ttk.Button(actions, text="모의 신호 1회 실행", command=self._run_tick).pack(side="left")
        ttk.Button(actions, text="다음 매도 실패로 테스트", command=self._fail_sell).pack(side="left", padx=6)
        ttk.Checkbutton(
            actions,
            text="자동운용 시 3분봉 전략을 키움 모의주문에 연결",
            variable=self.kiwoom_auto_order_var,
        ).pack(side="left", padx=(12, 0))

        kiwoom_controls = ttk.Frame(controls)
        kiwoom_controls.grid(row=3, column=0, columnspan=9, sticky="ew", pady=(12, 0))
        ttk.Label(kiwoom_controls, text="계좌번호(로그인 후 자동 표시)").pack(side="left")
        ttk.Entry(
            kiwoom_controls,
            textvariable=self.account_first_var,
            width=6,
            justify="center",
            state="readonly",
        ).pack(side="left", padx=(4, 2))
        ttk.Label(kiwoom_controls, text="-").pack(side="left")
        ttk.Entry(
            kiwoom_controls,
            textvariable=self.account_last_var,
            width=6,
            justify="center",
            state="readonly",
        ).pack(side="left", padx=(2, 8))
        ttk.Label(kiwoom_controls, text="계좌 비밀번호(저장 안 함)").pack(side="left")
        ttk.Entry(
            kiwoom_controls,
            textvariable=self.account_password_var,
            width=10,
            show="*",
        ).pack(side="left", padx=(4, 8))
        ttk.Label(kiwoom_controls, text="시장가 주문 수량").pack(side="left")
        ttk.Entry(kiwoom_controls, textvariable=self.order_qty_var, width=6).pack(side="left", padx=(4, 8))
        ttk.Checkbutton(
            kiwoom_controls,
            text="실거래 주문 허용(위험)",
            variable=self.allow_real_order_var,
            command=self._refresh,
        ).pack(side="left")

        api_actions = ttk.Frame(controls)
        api_actions.grid(row=4, column=0, columnspan=9, sticky="ew", pady=(8, 0))
        ttk.Button(api_actions, text="현재가 불러오기", command=self._request_current_price).pack(side="left")
        ttk.Button(api_actions, text="3분봉 데이터 불러오기", command=self._request_three_minute).pack(side="left", padx=4)
        ttk.Button(api_actions, text="계좌잔고 불러오기", command=self._request_balance).pack(side="left", padx=4)
        ttk.Button(api_actions, text="실시간 시세 시작", command=self._register_real_time).pack(side="left", padx=4)
        ttk.Button(api_actions, text="실시간 시세 중지", command=self._unregister_real_time).pack(side="left", padx=4)
        ttk.Button(api_actions, text="3분봉+CCI 전략 판단", command=self._evaluate_market_strategy).pack(
            side="left",
            padx=4,
        )
        self.strategy_order_button = ttk.Button(
            api_actions,
            text="전략 판단 후 모의주문",
            command=lambda: self._evaluate_and_send_order(auto=False),
        )
        self.strategy_order_button.pack(side="left", padx=4)
        self.buy_button = ttk.Button(api_actions, text="시장가 매수 주문", command=lambda: self._send_order("BUY"))
        self.buy_button.pack(
            side="left",
            padx=(10, 4),
        )
        self.sell_button = ttk.Button(api_actions, text="시장가 매도 주문", command=lambda: self._send_order("SELL"))
        self.sell_button.pack(
            side="left",
            padx=4,
        )
        ttk.Label(controls, textvariable=self.trade_ready_var).grid(
            row=5, column=0, columnspan=9, sticky="ew", pady=(8, 0)
        )

        body = ttk.Frame(self, padding=12)
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.columnconfigure(2, weight=1)
        body.rowconfigure(2, weight=1)

        self.status_text = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.status_text, font=("Malgun Gothic", 11)).grid(
            row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8)
        )
        ttk.Label(body, textvariable=self.account_summary_var, font=("Malgun Gothic", 10)).grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=(0, 12)
        )

        self.holdings = self._table(
            body,
            "계좌 잔고",
            ("종목번호", "종목명", "보유", "평균", "현재가", "평가손익", "수익률"),
            0,
        )
        self.orders = self._table(
            body,
            "최근 주문/요청 결과",
            ("시간", "종목", "구분", "수량", "가격", "결과", "메시지"),
            1,
        )
        self.logs = self._table(body, "시스템 로그", ("시간", "레벨", "분류", "메시지"), 2)

    def _field(self, parent: ttk.Frame, label: str, variable: tk.StringVar, column: int) -> None:
        ttk.Label(parent, text=label).grid(row=0, column=column, sticky="w")
        ttk.Entry(parent, textvariable=variable, width=14).grid(
            row=1, column=column, sticky="ew", padx=(0, 8)
        )

    def _table(self, parent: ttk.Frame, title: str, columns: tuple[str, ...], column: int) -> ttk.Treeview:
        frame = ttk.LabelFrame(parent, text=title, padding=8)
        frame.grid(row=2, column=column, sticky="nsew", padx=(0 if column == 0 else 6, 0))
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
        if self.symbol_var.get().strip() != self.service.symbol:
            self.symbol_var.set(self.service.symbol)
        if self.service.symbol_name:
            self.symbol_name_var.set(self.service.symbol_name)
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

    def _open_login_dialog(self) -> None:
        dialog = KiwoomLoginDialog(self)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        user_id, password = dialog.result
        if not user_id:
            messagebox.showwarning("로그인 ID 필요", "키움 ID를 입력해 주세요.")
            return
        if not password:
            messagebox.showwarning("비밀번호 필요", "비밀번호를 입력해 주세요.")
            return
        self.service.storage.log("INFO", "계좌", f"키움 ID {user_id}로 OpenAPI 로그인을 요청합니다.")
        self._connect_account()

    def _connect_account(self) -> None:
        self.account_button.configure(state="disabled")
        self._update_connection_badge(False)
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
        if self.service.account_info.accounts:
            if self._selected_account_full not in self.service.account_info.accounts:
                self._selected_account_full = self.service.account_info.accounts[0]
            self._set_account_display(self._selected_account_full)
        self._refresh()
        if self.service.account_info.connected:
            self._show_account_info_window()

    def _open_kiwoom_openapi_page(self) -> None:
        webbrowser.open(KIWOOM_OPENAPI_PAGE)
        self.service.storage.log("INFO", "계좌", "키움 OpenAPI+ 설치 페이지를 열었습니다.")
        self._refresh()

    def _open_kiwoom_installer(self) -> None:
        webbrowser.open(KIWOOM_OPENAPI_INSTALLER)
        self.service.storage.log("INFO", "계좌", "키움 공식 OpenAPI+ 설치파일 다운로드를 열었습니다.")
        self._refresh()

    def _open_multi_login_help(self) -> None:
        webbrowser.open(KIWOOM_MULTI_LOGIN_HELP)
        self.service.storage.log("INFO", "계좌", "키움 멀티로그인 안내 페이지를 열었습니다.")
        self._refresh()

    def _on_symbol_input_changed(self, *_args: object) -> None:
        if self._symbol_lookup_after_id is not None:
            self.after_cancel(self._symbol_lookup_after_id)
        self._symbol_lookup_after_id = self.after(450, self._lookup_symbol_from_input)

    def _lookup_symbol_from_input(self) -> None:
        self._symbol_lookup_after_id = None
        digits = "".join(ch for ch in self.symbol_var.get() if ch.isdigit())
        if len(digits) < 5:
            self.symbol_name_var.set("")
            self.trade_ready_var.set("거래 준비: 5~6자리 종목번호를 입력하면 회사명을 조회합니다.")
            self._update_trade_buttons()
            return

        name = self.service.lookup_symbol_name(self.symbol_var.get())
        normalized = normalize_symbol(self.symbol_var.get())
        if normalized and self.symbol_var.get().strip() != normalized:
            self.symbol_var.set(normalized)
        self.symbol_name_var.set(name or "키움 로그인 후 조회 필요")
        self._refresh()

    def _schedule_account_poll(self) -> None:
        if self._account_after_id is None:
            self._account_after_id = self.after(1000, self._poll_account_connection)

    def _poll_account_connection(self) -> None:
        self._account_after_id = None
        self._account_poll_count += 1
        account_info = self.service.refresh_account_connection()
        if account_info.accounts:
            if self._selected_account_full not in account_info.accounts:
                self._selected_account_full = account_info.accounts[0]
            self._set_account_display(self._selected_account_full)
        if account_info.connected:
            self._update_connection_badge(True)
            self.service.lookup_symbol_name(self.symbol_var.get())
            if self._account_for_api():
                self.service.request_balance(self._account_for_api(), self.account_password_var.get())
        self._refresh()
        login_failed = self.service.kiwoom_api.last_login_error not in (None, 0)
        if account_info.connected or login_failed or self._account_poll_count >= 120:
            self.account_button.configure(state="normal")
            if account_info.connected:
                self._show_account_info_window()
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
        self.service.request_balance(self._account_for_api(), self.account_password_var.get())
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
        if not self._trading_ready():
            messagebox.showwarning("주문 준비 필요", self.trade_ready_var.get())
            return
        if allow_real and not self._confirm_real_order("시장가 주문"):
            return
        self.service.configure(self.symbol_var.get(), float(self.capital_var.get()), self._settings())
        self.service.send_kiwoom_order(
            account=self._account_for_api(),
            side=side,
            quantity=max(1, int(float(self.order_qty_var.get()))),
            allow_real_order=allow_real,
        )
        self._refresh()

    def _evaluate_and_send_order(self, auto: bool = False) -> None:
        if not self._trading_ready():
            if not auto:
                messagebox.showwarning("주문 준비 필요", self.trade_ready_var.get())
            return
        allow_real = self.allow_real_order_var.get()
        if auto and allow_real:
            self.service.storage.log(
                "WARN",
                "주문",
                "자동운용 중 실거래 주문은 차단했습니다. 실거래는 수동 주문 버튼에서만 최종 확인 후 가능합니다.",
            )
            self._refresh()
            return
        if allow_real and not self._confirm_real_order("전략 판단 후 주문"):
            return
        self.service.configure(self.symbol_var.get(), float(self.capital_var.get()), self._settings())
        self.service.evaluate_and_send_order_with_market_data(
            account=self._account_for_api(),
            quantity=max(1, int(float(self.order_qty_var.get()))),
            allow_real_order=allow_real,
        )
        self._refresh()

    def _confirm_real_order(self, title: str) -> bool:
        return messagebox.askyesno(
            "실거래 주문 최종 확인",
            f"{title}을 실행하려고 합니다.\n"
            "실거래 주문 허용이 켜져 있어 모의투자 서버가 아닌 경우 실제 주문이 전송될 수 있습니다.\n"
            "계좌, 종목, 수량을 다시 확인했습니다. 계속하시겠습니까?",
        )

    def _refresh(self) -> None:
        snapshot = self.service.snapshot()
        decision_key = snapshot.decision.action if snapshot.decision else "NONE"
        decision = ACTION_LABELS.get(decision_key, decision_key)
        cci = ""
        if snapshot.decision and snapshot.decision.cci_value is not None:
            cci = f" | CCI {snapshot.decision.cci_value:.2f}"
        account = snapshot.account_info
        account_label = "연결됨" if account.connected else "미연결"
        if account.connected and account.server_type:
            account_label = f"{account_label}({account.server_type})"
        self._update_connection_badge(account.connected)
        account_detail = account.user_name or account.message
        if account.accounts:
            masked_accounts = ", ".join(mask_account_number(account_number) for account_number in account.accounts)
            account_detail = f"{account_detail} {masked_accounts}".strip()
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
                    f"내부 테스트 {snapshot.connection}",
                    f"운용 {'중' if snapshot.running else '중지'}",
                    f"종목 {snapshot.symbol} {snapshot.symbol_name}".strip(),
                    f"패턴 {PATTERN_VALUE_TO_LABEL.get(snapshot.pattern, snapshot.pattern)}",
                    f"현재가 {snapshot.price:,.0f}",
                    f"보유 {snapshot.quantity}주",
                    f"평균 {snapshot.average_price:,.0f}",
                    f"판단 {decision}{cci}{quote_label}{balance_label}",
                    snapshot.last_api_message,
                ]
            )
        )
        if snapshot.symbol_name and self.symbol_name_var.get() != snapshot.symbol_name:
            self.symbol_name_var.set(snapshot.symbol_name)
        self.account_summary_var.set(self._format_account_summary(snapshot))
        self.trade_ready_var.set(self._format_trade_ready(snapshot))
        self._update_trade_buttons()
        holdings = list(snapshot.balance_summary.holdings) if snapshot.balance_summary else []
        self._replace_rows(self.holdings, self._format_holdings(holdings))
        self._replace_rows(self.orders, self._format_orders(snapshot.orders))
        self._replace_rows(self.logs, self._format_logs(snapshot.logs))
        self._schedule_auto_tick()

    def _auto_tick(self) -> None:
        self._refresh_after_id = None
        if self.service.running:
            if self.kiwoom_auto_order_var.get() and self._trading_ready():
                self._evaluate_and_send_order(auto=True)
            else:
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

    def _format_holdings(self, rows: list) -> list[tuple]:
        formatted = []
        for holding in rows:
            formatted.append(
                (
                    normalize_symbol(holding.symbol),
                    holding.name,
                    holding.quantity,
                    f"{holding.average_price:,.0f}",
                    f"{holding.current_price:,.0f}",
                    f"{holding.profit_loss:,.0f}",
                    f"{holding.profit_rate:.2f}%",
                )
            )
        return formatted

    def _format_logs(self, rows: list[tuple]) -> list[tuple]:
        formatted = []
        for timestamp, level, category, message in rows:
            formatted.append((timestamp, LEVEL_LABELS.get(level, level), category, message))
        return formatted

    def _format_account_summary(self, snapshot) -> str:
        selected = self._account_for_api() or self.account_var.get().strip()
        if not selected and snapshot.account_info.accounts:
            selected = snapshot.account_info.accounts[0]
        selected_label = mask_account_number(selected)
        if not snapshot.account_info.connected:
            return (
                f"계좌 창: 미연결 | 선택 계좌 {selected_label} | "
                "키움 로그인 완료 후 계좌번호 앞4자리+뒤4자리와 잔고가 표시됩니다."
            )
        if not snapshot.balance_summary:
            return (
                f"계좌 창: 연결됨({snapshot.account_info.server_type}) | 선택 계좌 {selected_label} | "
                "계좌 비밀번호 입력 후 '계좌잔고 불러오기'를 누르면 잔고가 표시됩니다."
            )
        balance = snapshot.balance_summary
        return (
            f"계좌 창: 연결됨({snapshot.account_info.server_type}) | "
            f"선택 계좌 {mask_account_number(balance.account)} | "
            f"보유 {len(balance.holdings)}종목 | 추정예탁자산 {balance.estimated_assets:,.0f} | "
            f"평가금액 {balance.total_evaluation:,.0f} | 평가손익 {balance.total_profit_loss:,.0f} | "
            f"수익률 {balance.total_profit_rate:.2f}%"
        )

    def _format_trade_ready(self, snapshot) -> str:
        account = clean_account_number(self._account_for_api())
        name = snapshot.symbol_name or self.symbol_name_var.get().strip()
        quantity_ok = self._order_quantity_valid()
        server_ready = (
            snapshot.account_info.server_type == "모의투자" or self.allow_real_order_var.get()
        )
        if snapshot.account_info.connected and account and name and quantity_ok and server_ready:
            order_mode = "모의주문" if snapshot.account_info.server_type == "모의투자" else "실거래 주문"
            return (
                f"거래 준비 완료: {snapshot.symbol} {name} | 계좌 {mask_account_number(account)} | "
                f"{self.order_qty_var.get()}주 시장가 매수/매도 및 전략 판단 후 {order_mode} 가능"
            )
        missing = []
        if not snapshot.account_info.connected:
            missing.append("키움 로그인")
        if not account:
            missing.append("계좌번호")
        if not name or name == "키움 로그인 후 조회 필요":
            missing.append("회사명")
        if not quantity_ok:
            missing.append("주문수량")
        if snapshot.account_info.server_type == "실거래" and not self.allow_real_order_var.get():
            missing.append("실거래 주문 잠금 해제")
        return f"거래 준비: {', '.join(missing)} 확인이 필요합니다."

    def _trading_ready(self) -> bool:
        account = clean_account_number(self._account_for_api())
        name = self.symbol_name_var.get().strip()
        server_ready = (
            self.service.account_info.server_type == "모의투자" or self.allow_real_order_var.get()
        )
        return bool(
            self.service.account_info.connected
            and account
            and name
            and name != "키움 로그인 후 조회 필요"
            and self._order_quantity_valid()
            and server_ready
        )

    def _order_quantity_valid(self) -> bool:
        try:
            return int(float(self.order_qty_var.get())) > 0
        except ValueError:
            return False

    def _update_trade_buttons(self) -> None:
        state = "normal" if self._trading_ready() else "disabled"
        self.buy_button.configure(state=state)
        self.sell_button.configure(state=state)
        self.strategy_order_button.configure(state=state)

    def _update_connection_badge(self, connected: bool) -> None:
        if connected:
            self.connection_state_var.set("ON 연결됨")
            self.connection_badge.configure(bg="#16833a", fg="white")
            self.connection_light.itemconfigure(
                self._connection_light_id,
                fill="#2ecc71",
                outline="#1e8449",
            )
        else:
            self.connection_state_var.set("OFF 연결 안됨")
            self.connection_badge.configure(bg="#7a7a7a", fg="white")
            self.connection_light.itemconfigure(
                self._connection_light_id,
                fill="#b0b0b0",
                outline="#808080",
            )

    def _show_account_info_window(self) -> None:
        snapshot = self.service.snapshot()
        account_info = snapshot.account_info
        if not account_info.connected:
            return
        if self._account_info_window is not None and self._account_info_window.winfo_exists():
            self._account_info_window.lift()
            return

        window = tk.Toplevel(self)
        self._account_info_window = window
        window.title("키움 계좌정보")
        window.geometry("420x260")
        window.resizable(False, False)
        window.transient(self)
        window.protocol("WM_DELETE_WINDOW", window.destroy)

        body = ttk.Frame(window, padding=18)
        body.grid(row=0, column=0, sticky="nsew")
        ttk.Label(body, text="계좌정보", font=("Malgun Gothic", 15, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )
        rows = [
            ("연결 상태", "ON 연결됨"),
            ("접속 서버", account_info.server_type or "확인 필요"),
            ("고객명", account_info.user_name or "확인 필요"),
            ("사용자 ID", account_info.user_id or "확인 필요"),
            ("선택 계좌", mask_account_number(self._account_for_api() or self._selected_account_full)),
            ("계좌 수", f"{len(account_info.accounts)}개"),
        ]
        if snapshot.balance_summary:
            rows.extend(
                [
                    ("보유 종목", f"{len(snapshot.balance_summary.holdings)}종목"),
                    ("추정예탁자산", f"{snapshot.balance_summary.estimated_assets:,.0f}"),
                ]
            )

        for row_index, (label, value) in enumerate(rows, start=1):
            ttk.Label(body, text=label, width=14).grid(row=row_index, column=0, sticky="w", pady=3)
            ttk.Label(body, text=value, width=28).grid(row=row_index, column=1, sticky="w", pady=3)

        ttk.Button(body, text="닫기", command=window.destroy).grid(
            row=len(rows) + 1, column=0, columnspan=2, sticky="e", pady=(14, 0)
        )

    def _account_for_api(self) -> str:
        entered = clean_account_number(self.account_var.get())
        selected = clean_account_number(self._selected_account_full)
        if selected and entered == clean_account_number(mask_account_number(selected)):
            return selected
        return entered or selected

    def _set_account_display(self, account: str) -> None:
        digits = clean_account_number(account)
        self.account_first_var.set(digits[:4] if len(digits) >= 4 else "")
        self.account_last_var.set(digits[-4:] if len(digits) >= 4 else "")
        self.account_var.set(mask_account_number(digits) if digits else "")

    def _settings(self) -> StrategySettings:
        return StrategySettings(
            cci_period=max(1, int(float(self.period_var.get()))),
            cci_upper=float(self.upper_var.get()),
            cci_lower=-abs(float(self.upper_var.get())),
            use_cci_filter=self.use_cci_var.get(),
        )
