from __future__ import annotations

import tempfile
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .charting import moving_average, timeframe_label
from .kiwoom_api import (
    KIWOOM_HOME_PAGE,
    is_valid_account_password,
)
from .models import Candle, DmiPoint, PatternState, StrategySettings, WatchlistQuote
from .rest_api import KIWOOM_REST_PORTAL
from .service import AutoTradingService
from .storage import Storage
from .symbols import (
    clean_account_number,
    display_account_number,
    mask_account_number,
    normalize_symbol,
)


PATTERN_VALUE_TO_LABEL: dict[PatternState, str] = {
    "NONE": "계산 전",
    "BULLISH": "강세",
    "BEARISH": "약세",
}
ACTION_LABELS = {"BUY": "매수", "SELL": "매도", "HOLD": "대기", "NONE": "없음"}
SIDE_LABELS = {"BUY": "매수", "SELL": "매도"}
LEVEL_LABELS = {"INFO": "정보", "WARN": "주의", "ERROR": "오류"}
REST_LIVE_LABEL = "실전투자 (조회 전용)"
REST_MOCK_LABEL = "모의투자"


def _account_password_input_allowed(value: str) -> bool:
    return not value or (value.isdigit() and len(value) <= 8)


class KiwoomLoginDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, default_user_id: str = "") -> None:
        super().__init__(parent)
        self.title("키움 ID 로그인")
        self.resizable(False, False)
        self.result: str | None = None
        self.user_id_var = tk.StringVar(value=default_user_id)

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
        ttk.Label(
            body,
            text=(
                "다음에 열리는 키움 OpenAPI+ 공식 창에서 ID, 비밀번호, 인증서를 입력하세요. "
                "앱은 비밀번호를 받거나 저장하지 않으며, 로그인된 ID가 위 ID와 일치할 때만 연결합니다."
            ),
            wraplength=360,
            foreground="#555555",
        ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(2, 14))

        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        ttk.Button(
            buttons,
            text="키움 홈페이지 열기",
            command=lambda: webbrowser.open(KIWOOM_HOME_PAGE),
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(buttons, text="OpenAPI+ 로그인", command=self._submit).grid(
            row=0, column=1, padx=(8, 0)
        )
        ttk.Button(buttons, text="취소", command=self._cancel).grid(row=0, column=2, padx=(8, 0))

        user_entry.focus_set()
        self.bind("<Return>", lambda _event: self._submit())
        self.bind("<Escape>", lambda _event: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _submit(self) -> None:
        self.result = self.user_id_var.get().strip()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


class KiwoomRestLoginDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk) -> None:
        super().__init__(parent)
        self.title("키움 REST API 연결")
        self.resizable(False, False)
        self.result: tuple[str, str, bool] | None = None
        self.app_key_var = tk.StringVar(value="")
        self.secret_key_var = tk.StringVar(value="")
        self.environment_var = tk.StringVar(value=REST_LIVE_LABEL)
        self.heading_var = tk.StringVar()
        self.help_var = tk.StringVar()
        self.connect_button_var = tk.StringVar()
        self.key_status_var = tk.StringVar(value="키 파일을 선택하거나 다운로드 폴더에서 자동으로 찾을 수 있습니다.")

        self.transient(parent)
        self.grab_set()

        body = ttk.Frame(self, padding=18)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        ttk.Label(body, textvariable=self.heading_var, font=("Malgun Gothic", 14, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 14)
        )
        ttk.Label(body, text="연결 환경").grid(row=1, column=0, sticky="w", pady=(0, 8))
        environment = ttk.Combobox(
            body,
            textvariable=self.environment_var,
            values=(REST_LIVE_LABEL, REST_MOCK_LABEL),
            state="readonly",
            width=28,
        )
        environment.grid(row=1, column=1, columnspan=2, sticky="w", pady=(0, 8))
        environment.bind("<<ComboboxSelected>>", self._sync_environment_text)

        ttk.Label(body, text="AppKey").grid(row=2, column=0, sticky="w", pady=(0, 8))
        app_key_entry = ttk.Entry(body, textvariable=self.app_key_var, width=42, show="*")
        app_key_entry.grid(row=2, column=1, sticky="ew", pady=(0, 8))
        ttk.Button(
            body,
            text="파일 선택",
            command=lambda: self._choose_key_file(self.app_key_var, "AppKey"),
        ).grid(row=2, column=2, padx=(8, 0), pady=(0, 8))

        ttk.Label(body, text="SecretKey").grid(row=3, column=0, sticky="w", pady=(0, 8))
        ttk.Entry(body, textvariable=self.secret_key_var, width=42, show="*").grid(
            row=3, column=1, sticky="ew", pady=(0, 8)
        )
        ttk.Button(
            body,
            text="파일 선택",
            command=lambda: self._choose_key_file(self.secret_key_var, "SecretKey"),
        ).grid(row=3, column=2, padx=(8, 0), pady=(0, 8))

        ttk.Button(
            body,
            text="다운로드 키 2개 자동 찾기",
            command=self._auto_load_key_files,
        ).grid(row=4, column=1, sticky="w", pady=(0, 6))
        ttk.Label(
            body,
            textvariable=self.key_status_var,
            foreground="#555555",
            wraplength=520,
        ).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(0, 10)
        )
        ttk.Label(
            body,
            textvariable=self.help_var,
            wraplength=520,
            foreground="#555555",
        ).grid(row=6, column=0, columnspan=3, sticky="ew", pady=(2, 14))

        buttons = ttk.Frame(body)
        buttons.grid(row=7, column=0, columnspan=3, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        ttk.Button(
            buttons,
            text="REST API 포털 열기",
            command=lambda: webbrowser.open(KIWOOM_REST_PORTAL),
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(buttons, textvariable=self.connect_button_var, command=self._submit).grid(
            row=0, column=1, padx=(8, 0)
        )
        ttk.Button(buttons, text="취소", command=self._cancel).grid(row=0, column=2, padx=(8, 0))

        self._sync_environment_text()
        self._load_key_files_from_downloads(show_missing_warning=False)
        app_key_entry.focus_set()
        self.bind("<Return>", lambda _event: self._submit())
        self.bind("<Escape>", lambda _event: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _sync_environment_text(self, _event: object | None = None) -> None:
        if self.environment_var.get() == REST_MOCK_LABEL:
            self.heading_var.set("키움 REST API 모의투자 연결")
            self.connect_button_var.set("모의투자 연결")
            self.help_var.set(
                "모의투자 AppKey와 SecretKey를 사용하세요. 연결 후 현재가, 3분봉, "
                "실시간 시세, 잔고 및 모의주문을 사용할 수 있습니다."
            )
            return
        self.heading_var.set("키움 REST API 실전투자 연결")
        self.connect_button_var.set("실전 조회 연결")
        self.help_var.set(
            "실전투자 AppKey와 SecretKey를 사용하세요. 키움 포털에 현재 공인 IP가 "
            "등록되어 있어야 합니다. 실전 계좌·잔고·시세 조회만 활성화되며 실제 주문은 잠겨 있습니다."
        )

    def _choose_key_file(self, target: tk.StringVar, key_name: str) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title=f"{key_name} 파일 선택",
            initialdir=str(Path.home() / "Downloads"),
            filetypes=(("텍스트 파일", "*.txt"), ("모든 파일", "*.*")),
        )
        if not path:
            return
        try:
            target.set(self._read_key_file(Path(path)))
            self.key_status_var.set(f"{key_name} 파일을 불러왔습니다.")
        except (OSError, ValueError) as exc:
            messagebox.showerror("키 파일 오류", str(exc), parent=self)

    def _auto_load_key_files(self) -> None:
        self._load_key_files_from_downloads(show_missing_warning=True)

    def _load_key_files_from_downloads(self, show_missing_warning: bool) -> None:
        downloads = Path.home() / "Downloads"
        try:
            key_pair = self._read_latest_key_pair(downloads)
        except (OSError, ValueError) as exc:
            self.key_status_var.set("키 파일 자동 불러오기에 실패했습니다. 파일을 다시 확인해 주세요.")
            if show_missing_warning:
                messagebox.showerror("키 파일 오류", str(exc), parent=self)
            return
        if key_pair is not None:
            app_key, secret_key = key_pair
            self.app_key_var.set(app_key)
            self.secret_key_var.set(secret_key)
            self.key_status_var.set("다운로드 폴더에서 키 파일 2개를 자동으로 불러왔습니다.")
            return
        self.key_status_var.set("다운로드 폴더에서 서로 짝이 맞는 키 파일을 찾지 못했습니다.")
        if show_missing_warning:
            messagebox.showwarning(
                "키 파일 없음",
                "다운로드 폴더에서 계좌번호_appkey.txt와 SecretKey 파일을 찾지 못했습니다.",
                parent=self,
            )

    @classmethod
    def _read_latest_key_pair(cls, downloads: Path) -> tuple[str, str] | None:
        app_key_files = sorted(
            downloads.glob("*_appkey.txt"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for app_key_path in app_key_files:
            prefix = app_key_path.stem[: -len("_appkey")]
            candidates = (
                downloads / f"{prefix}_secretkey.txt",
                downloads / f"{prefix}_appsecret.txt",
            )
            secret_key_path = next((path for path in candidates if path.exists()), None)
            if secret_key_path is None:
                continue
            return cls._read_key_file(app_key_path), cls._read_key_file(secret_key_path)
        return None

    @staticmethod
    def _read_key_file(path: Path) -> str:
        try:
            value = path.read_text(encoding="utf-8-sig").strip()
        except UnicodeDecodeError:
            value = path.read_text(encoding="cp949").strip()
        if not value or any(character.isspace() for character in value):
            raise ValueError("키 파일 형식이 올바르지 않습니다.")
        return value

    def _submit(self) -> None:
        app_key = self.app_key_var.get().strip()
        secret_key = self.secret_key_var.get().strip()
        mock = self.environment_var.get() == REST_MOCK_LABEL
        self.app_key_var.set("")
        self.secret_key_var.set("")
        self.result = (app_key, secret_key, mock)
        self.destroy()

    def _cancel(self) -> None:
        self.app_key_var.set("")
        self.secret_key_var.set("")
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
        self._chart_refresh_after_id: str | None = None
        self._account_after_id: str | None = None
        self._symbol_lookup_after_id: str | None = None
        self._account_poll_count = 0
        self._selected_account_full = ""
        self._account_info_window: tk.Toplevel | None = None
        self._candle_chart_window: tk.Toplevel | None = None
        self._candle_chart_canvas: tk.Canvas | None = None
        self._watchlist_window: tk.Toplevel | None = None
        self._watchlist_table: ttk.Treeview | None = None
        self._watchlist_link_after_id: str | None = None
        self._watchlist_suppress_auto_connect = False
        self.watchlist_symbol_var: tk.StringVar | None = None
        self.watchlist_auto_link_var: tk.BooleanVar | None = None
        self.watchlist_status_var: tk.StringVar | None = None
        self.watchlist_detail_vars: dict[str, tk.StringVar] = {}
        self._chart_render_state: dict[tk.Canvas, dict] = {}
        self._chart_visible_count = 100
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
        self.account_button = ttk.Button(header, text="OpenAPI+ 로그인", command=self._open_login_dialog)
        self.account_button.grid(row=0, column=1, padx=(0, 8))
        self.rest_account_button = ttk.Button(
            header,
            text="REST API 연결",
            command=self._open_rest_login_dialog,
        )
        self.rest_account_button.grid(row=0, column=2, padx=(0, 8))
        ttk.Button(header, text="연결 상태 확인", command=self._check_account_environment).grid(
            row=0,
            column=3,
            padx=(0, 8),
        )
        self.connection_light = tk.Canvas(header, width=18, height=18, highlightthickness=0, bd=0)
        self.connection_light.grid(row=0, column=4, padx=(0, 6))
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
        self.connection_badge.grid(row=0, column=5, padx=(0, 8))
        ttk.Button(header, text="관심종목", command=self._open_watchlist_window).grid(
            row=0,
            column=6,
            padx=(0, 8),
        )
        ttk.Button(
            header,
            text="REST API 안내",
            command=lambda: webbrowser.open(KIWOOM_REST_PORTAL),
        ).grid(row=0, column=7, padx=(0, 8))
        ttk.Button(header, text="긴급 정지", command=self._emergency_stop).grid(row=0, column=8)
        self._update_connection_badge(False)

        controls = ttk.LabelFrame(self, text="1. 종목/전략/계좌 설정", padding=12)
        controls.grid(row=1, column=0, sticky="ew", padx=12)
        for idx in range(10):
            controls.columnconfigure(idx, weight=1)

        self.symbol_var = tk.StringVar(value="005930")
        self.symbol_name_var = tk.StringVar(value="삼성전자")
        self.capital_var = tk.StringVar(value="1000000")
        self.price_var = tk.StringVar(value="72000")
        self.dmi_period_var = tk.StringVar(value="14")
        self.dmi_state_var = tk.StringVar(value="계산 전")
        self.dmi_plus_var = tk.StringVar(value="-")
        self.dmi_minus_var = tk.StringVar(value="-")
        self.adx_var = tk.StringVar(value="-")
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
        self._field(controls, "DMI 계산 기간", self.dmi_period_var, 4)
        ttk.Label(controls, text="DMI 강/약 상태").grid(row=0, column=5, sticky="w")
        self.dmi_state_badge = tk.Label(
            controls,
            textvariable=self.dmi_state_var,
            bg="#777777",
            fg="white",
            padx=8,
            pady=3,
        )
        self.dmi_state_badge.grid(row=1, column=5, sticky="ew", padx=(0, 8))
        ttk.Label(controls, text="+DI").grid(row=0, column=6, sticky="w")
        ttk.Label(controls, textvariable=self.dmi_plus_var).grid(row=1, column=6, sticky="w")
        ttk.Label(controls, text="-DI").grid(row=0, column=7, sticky="w")
        ttk.Label(controls, textvariable=self.dmi_minus_var).grid(row=1, column=7, sticky="w")
        ttk.Label(controls, text="ADX").grid(row=0, column=8, sticky="w")
        ttk.Label(controls, textvariable=self.adx_var).grid(row=1, column=8, sticky="w")
        ttk.Button(controls, text="설정 저장", command=self._save_settings).grid(row=1, column=9, sticky="ew")

        actions = ttk.Frame(controls)
        actions.grid(row=2, column=0, columnspan=10, sticky="ew", pady=(12, 0))
        ttk.Button(actions, text="자동 운용 시작", command=self._start).pack(side="left")
        ttk.Button(actions, text="자동 운용 중지", command=self._stop).pack(side="left", padx=6)
        ttk.Checkbutton(
            actions,
            text="자동운용 시 3분봉 DMI 강약 전환을 키움 모의주문에 연결",
            variable=self.kiwoom_auto_order_var,
        ).pack(side="left", padx=(12, 0))

        kiwoom_controls = ttk.Frame(controls)
        kiwoom_controls.grid(row=3, column=0, columnspan=10, sticky="ew", pady=(12, 0))
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
        ttk.Label(kiwoom_controls, text="계좌 비밀번호(숫자 4~8자리·저장 안 함)").pack(side="left")
        self.account_password_entry = ttk.Entry(
            kiwoom_controls,
            textvariable=self.account_password_var,
            width=10,
            show="*",
            validate="key",
            validatecommand=(self.register(_account_password_input_allowed), "%P"),
        )
        self.account_password_entry.pack(side="left", padx=(4, 8))
        ttk.Label(kiwoom_controls, text="시장가 주문 수량").pack(side="left")
        ttk.Entry(kiwoom_controls, textvariable=self.order_qty_var, width=6).pack(side="left", padx=(4, 8))
        self.allow_real_order_checkbutton = ttk.Checkbutton(
            kiwoom_controls,
            text="실거래 주문 허용(위험)",
            variable=self.allow_real_order_var,
            command=self._refresh,
        )
        self.allow_real_order_checkbutton.pack(side="left")

        api_actions = ttk.Frame(controls)
        api_actions.grid(row=4, column=0, columnspan=10, sticky="ew", pady=(8, 0))
        ttk.Button(api_actions, text="현재가 불러오기", command=self._request_current_price).pack(side="left")
        ttk.Button(api_actions, text="3분봉 데이터 불러오기", command=self._request_three_minute).pack(side="left", padx=4)
        ttk.Button(api_actions, text="계좌잔고 불러오기", command=self._request_balance).pack(side="left", padx=4)
        ttk.Button(api_actions, text="실시간 시세 시작", command=self._register_real_time).pack(side="left", padx=4)
        ttk.Button(api_actions, text="실시간 시세 중지", command=self._unregister_real_time).pack(side="left", padx=4)
        ttk.Button(api_actions, text="3분봉+DMI 강약 판단", command=self._evaluate_market_strategy).pack(
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
        ready_row = ttk.Frame(controls)
        ready_row.grid(row=5, column=0, columnspan=10, sticky="ew", pady=(8, 0))
        self.chart_button = ttk.Button(
            ready_row,
            text="DMI 차트 확대",
            command=self._show_candle_chart,
        )
        self.chart_button.pack(side="left")
        ttk.Label(ready_row, textvariable=self.trade_ready_var).pack(side="left", padx=(10, 0))

        body = ttk.Frame(self, padding=12)
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        self.status_text = tk.StringVar(value="")
        ttk.Label(
            body,
            textvariable=self.account_summary_var,
            font=("Malgun Gothic", 10),
            wraplength=1120,
            justify="left",
        ).grid(
            row=0, column=0, sticky="ew", pady=(0, 10)
        )

        self.main_notebook = ttk.Notebook(body)
        self.main_notebook.grid(row=1, column=0, sticky="nsew")

        self.dmi_chart_tab = ttk.Frame(self.main_notebook, padding=(10, 10, 10, 8))
        self.dmi_chart_tab.columnconfigure(0, weight=1)
        self.dmi_chart_tab.rowconfigure(2, weight=1)
        self.main_notebook.add(self.dmi_chart_tab, text="멀티주기 DMI 차트")

        chart_header = ttk.Frame(self.dmi_chart_tab)
        chart_header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        chart_header.columnconfigure(0, weight=1)
        self.chart_caption_var = tk.StringVar(value="키움 3분봉 | 자동매매 기준 3분봉")
        ttk.Label(
            chart_header,
            textvariable=self.chart_caption_var,
            font=("Malgun Gothic", 11, "bold"),
        ).grid(row=0, column=0, sticky="w")

        legend = ttk.Frame(chart_header)
        legend.grid(row=0, column=1, sticky="e")
        tk.Label(
            legend,
            text="강세 (+DI 우세)",
            bg="#fde4e9",
            fg="#8d1d50",
            padx=7,
            pady=2,
        ).pack(side="left")
        tk.Label(
            legend,
            text="약세 (-DI 우세)",
            bg="#e3f2fb",
            fg="#17618f",
            padx=7,
            pady=2,
        ).pack(side="left", padx=(5, 10))
        tk.Label(legend, text="+DI", fg="#d92787").pack(side="left")
        tk.Label(legend, text="-DI", fg="#1686b8").pack(side="left", padx=(7, 0))
        tk.Label(legend, text="ADX", fg="#555555").pack(side="left", padx=(7, 0))

        chart_toolbar = ttk.Frame(self.dmi_chart_tab)
        chart_toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 7))
        self.chart_timeframe_var = tk.StringVar(value="3m")
        timeframe_groups = (
            ("초", (("1초", "1s"), ("5초", "5s"), ("10초", "10s"))),
            (
                "분",
                (
                    ("1분", "1m"),
                    ("3분", "3m"),
                    ("5분", "5m"),
                    ("10분", "10m"),
                    ("15분", "15m"),
                    ("30분", "30m"),
                    ("45분", "45m"),
                ),
            ),
            ("시", (("1시간", "60m"),)),
        )
        for group_index, (group_label, choices) in enumerate(timeframe_groups):
            if group_index:
                ttk.Separator(chart_toolbar, orient="vertical").pack(
                    side="left",
                    fill="y",
                    padx=6,
                )
            ttk.Label(chart_toolbar, text=group_label, font=("Malgun Gothic", 9, "bold")).pack(
                side="left",
                padx=(0, 4),
            )
            for label, value in choices:
                tk.Radiobutton(
                    chart_toolbar,
                    text=label,
                    variable=self.chart_timeframe_var,
                    value=value,
                    indicatoron=False,
                    width=5,
                    padx=2,
                    pady=2,
                    relief="flat",
                    overrelief="groove",
                    bg="#f0f0f0",
                    activebackground="#dbeaf5",
                    selectcolor="#cfe3f2",
                    command=self._on_chart_timeframe_changed,
                ).pack(side="left", padx=1)

        zoom_controls = ttk.Frame(chart_toolbar)
        zoom_controls.pack(side="right")
        ttk.Button(
            zoom_controls,
            text="−",
            width=3,
            command=lambda: self._change_chart_zoom(-20),
        ).pack(side="left")
        self.chart_visible_count_var = tk.StringVar(value="100봉")
        ttk.Label(zoom_controls, textvariable=self.chart_visible_count_var, width=7, anchor="center").pack(
            side="left"
        )
        ttk.Button(
            zoom_controls,
            text="+",
            width=3,
            command=lambda: self._change_chart_zoom(20),
        ).pack(side="left")
        ttk.Button(zoom_controls, text="새로고침", command=self._reload_chart_timeframe).pack(
            side="left",
            padx=(6, 0),
        )

        chart_workspace = ttk.Frame(self.dmi_chart_tab)
        chart_workspace.grid(row=2, column=0, sticky="nsew")
        chart_workspace.columnconfigure(0, minsize=150)
        chart_workspace.columnconfigure(1, weight=1)
        chart_workspace.rowconfigure(0, weight=1)

        indicator_panel = ttk.Frame(chart_workspace, width=150, padding=(4, 8, 10, 4))
        indicator_panel.grid(row=0, column=0, sticky="nsw")
        ttk.Label(indicator_panel, text="지표 설정", font=("Malgun Gothic", 10, "bold")).pack(
            anchor="w",
            pady=(0, 8),
        )
        self.show_pattern_var = tk.BooleanVar(value=True)
        self.show_ma5_var = tk.BooleanVar(value=True)
        self.show_ma20_var = tk.BooleanVar(value=True)
        self.show_dmi_chart_var = tk.BooleanVar(value=True)
        for label, variable in (
            ("강/약 배경", self.show_pattern_var),
            ("이동평균 MA5", self.show_ma5_var),
            ("이동평균 MA20", self.show_ma20_var),
            ("DMI / ADX", self.show_dmi_chart_var),
        ):
            ttk.Checkbutton(
                indicator_panel,
                text=label,
                variable=variable,
                command=self._draw_main_dmi_chart,
            ).pack(anchor="w", pady=2)
        ttk.Separator(indicator_panel, orient="horizontal").pack(fill="x", pady=10)
        ttk.Label(indicator_panel, text="양봉", foreground="#d64545").pack(anchor="w")
        ttk.Label(indicator_panel, text="음봉", foreground="#2f62bd").pack(anchor="w", pady=(3, 0))
        ttk.Label(indicator_panel, text="MA5", foreground="#d92787").pack(anchor="w", pady=(8, 0))
        ttk.Label(indicator_panel, text="MA20", foreground="#3459c7").pack(anchor="w", pady=(3, 0))

        self.main_chart_canvas = tk.Canvas(
            chart_workspace,
            background="#ffffff",
            highlightthickness=1,
            highlightbackground="#c8c8c8",
        )
        self.main_chart_canvas.grid(row=0, column=1, sticky="nsew")
        self.main_chart_canvas.bind("<Configure>", lambda _event: self._draw_main_dmi_chart())
        self.main_chart_canvas.bind(
            "<Motion>",
            lambda event: self._on_chart_motion(self.main_chart_canvas, event),
        )
        self.main_chart_canvas.bind(
            "<Leave>",
            lambda _event: self.main_chart_canvas.delete("crosshair"),
        )

        self.operations_tab = ttk.Frame(self.main_notebook, padding=(8, 10, 8, 8))
        for column in range(3):
            self.operations_tab.columnconfigure(column, weight=1)
        self.operations_tab.rowconfigure(0, weight=1)
        self.main_notebook.add(self.operations_tab, text="계좌·주문·로그")

        self.holdings = self._table(
            self.operations_tab,
            "계좌 잔고",
            ("종목번호", "종목명", "보유", "평균", "현재가", "평가손익", "수익률"),
            0,
        )
        self.orders = self._table(
            self.operations_tab,
            "최근 주문/요청 결과",
            ("시간", "종목", "구분", "수량", "가격", "결과", "메시지"),
            1,
        )
        self.logs = self._table(self.operations_tab, "시스템 로그", ("시간", "레벨", "분류", "메시지"), 2)

    def _field(self, parent: ttk.Frame, label: str, variable: tk.StringVar, column: int) -> None:
        ttk.Label(parent, text=label).grid(row=0, column=column, sticky="w")
        ttk.Entry(parent, textvariable=variable, width=14).grid(
            row=1, column=column, sticky="ew", padx=(0, 8)
        )

    def _table(self, parent: ttk.Frame, title: str, columns: tuple[str, ...], column: int) -> ttk.Treeview:
        frame = ttk.LabelFrame(parent, text=title, padding=8)
        frame.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 6, 0))
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
        settings = StrategySettings(dmi_period=max(1, int(float(self.dmi_period_var.get()))))
        self.service.configure(self.symbol_var.get(), float(self.capital_var.get()), settings)
        if self.symbol_var.get().strip() != self.service.symbol:
            self.symbol_var.set(self.service.symbol)
        if self.service.symbol_name:
            self.symbol_name_var.set(self.service.symbol_name)
        self._refresh()

    def _start(self) -> None:
        self._save_settings()
        if self.kiwoom_auto_order_var.get():
            if not self._require_live_connection():
                return
            if not self._trading_ready():
                messagebox.showwarning("자동운용 준비 필요", self.trade_ready_var.get())
                return
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
        user_id = dialog.result
        if not user_id:
            messagebox.showwarning("로그인 ID 필요", "키움 ID를 입력해 주세요.")
            return
        self.service.storage.log("INFO", "계좌", "입력한 ID와 OpenAPI+ 로그인 ID 확인을 시작합니다.")
        self._connect_account(user_id)

    def _connect_account(self, expected_user_id: str) -> None:
        self._set_login_buttons_state("disabled")
        self._update_connection_badge(False)
        self._account_poll_count = 0
        message = self.service.start_account_connection(expected_user_id)
        self.status_text.set(f"키움 계좌 연결: {message}")
        if "로그인 창" in message or "이미" in message:
            self._schedule_account_poll()
        else:
            self._set_login_buttons_state("normal")
            self._refresh()

    def _open_rest_login_dialog(self) -> None:
        dialog = KiwoomRestLoginDialog(self)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        app_key, secret_key, mock = dialog.result
        dialog.result = None
        if not app_key or not secret_key:
            app_key = ""
            secret_key = ""
            messagebox.showwarning("REST API 키 필요", "AppKey와 SecretKey를 모두 입력해 주세요.")
            return
        self._connect_rest_account(app_key, secret_key, mock)
        app_key = ""
        secret_key = ""

    def _connect_rest_account(self, app_key: str, secret_key: str, mock: bool) -> None:
        self._set_login_buttons_state("disabled")
        self._update_connection_badge(False)
        self.account_password_var.set("")
        self.allow_real_order_var.set(False)
        server_type = "모의투자" if mock else "실전투자 조회 전용"
        self.status_text.set(f"키움 REST API {server_type} 토큰과 계좌를 확인하고 있습니다.")
        self.update_idletasks()
        account_info = self.service.start_rest_connection(app_key, secret_key, mock=mock)
        app_key = ""
        secret_key = ""
        if account_info.connected and account_info.accounts:
            self._selected_account_full = account_info.accounts[0]
            self._set_account_display(self._selected_account_full)
            self.service.request_current_price(self.symbol_var.get())
            self.service.request_chart_candles(3, self.symbol_var.get())
            self.service.register_real_time_price(self.symbol_var.get())
            self.service.request_balance(self._selected_account_full)
        else:
            self._selected_account_full = ""
            self._set_account_display("")
        self._set_login_buttons_state("normal")
        self._refresh()
        if account_info.connected:
            self._show_account_info_window()

    def _set_login_buttons_state(self, state: str) -> None:
        self.account_button.configure(state=state)
        self.rest_account_button.configure(state=state)

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

    def _open_watchlist_window(self) -> None:
        if self._watchlist_window is not None and self._watchlist_window.winfo_exists():
            self._watchlist_window.deiconify()
            self._watchlist_window.lift()
            self._render_watchlist_rows()
            return

        window = tk.Toplevel(self)
        self._watchlist_window = window
        window.title("키움 관심종목")
        window.geometry("980x520")
        window.minsize(820, 420)
        window.transient(self)
        window.protocol("WM_DELETE_WINDOW", self._close_watchlist_window)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)

        self.watchlist_symbol_var = tk.StringVar(value=self.symbol_var.get())
        self.watchlist_auto_link_var = tk.BooleanVar(value=True)
        self.watchlist_status_var = tk.StringVar(value="관심종목을 선택하면 메인 차트와 자동 연결됩니다.")
        self.watchlist_detail_vars = {
            key: tk.StringVar(value="-")
            for key in (
                "종목",
                "현재가",
                "전일대비",
                "등락률",
                "거래량",
                "거래대금",
                "시가",
                "고가",
                "저가",
                "매도호가",
                "매수호가",
                "체결시간",
            )
        }

        toolbar = ttk.Frame(window, padding=(12, 12, 12, 8))
        toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Label(toolbar, text="종목코드").pack(side="left")
        entry = ttk.Entry(toolbar, textvariable=self.watchlist_symbol_var, width=10)
        entry.pack(side="left", padx=(5, 5))
        entry.bind("<Return>", lambda _event: self._register_watchlist_symbol())
        ttk.Button(toolbar, text="등록", command=self._register_watchlist_symbol).pack(side="left")
        ttk.Button(toolbar, text="삭제", command=self._remove_watchlist_symbol).pack(
            side="left",
            padx=(5, 0),
        )
        ttk.Button(toolbar, text="새로고침", command=self._refresh_watchlist_quotes).pack(
            side="left",
            padx=(5, 0),
        )
        ttk.Button(toolbar, text="선택 연결", command=self._activate_watchlist_selection).pack(
            side="left",
            padx=(5, 0),
        )
        ttk.Checkbutton(
            toolbar,
            text="선택 시 차트 자동 연결",
            variable=self.watchlist_auto_link_var,
        ).pack(side="left", padx=(12, 0))
        ttk.Button(toolbar, text="닫기", command=self._close_watchlist_window).pack(side="right")

        body = ttk.Frame(window, padding=(12, 0, 12, 8))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        table_frame = ttk.Frame(body)
        table_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("시장", "종목코드", "종목명", "현재가", "대비", "등락률", "거래량")
        table = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        self._watchlist_table = table
        widths = (55, 76, 140, 94, 82, 76, 110)
        for column, width in zip(columns, widths):
            table.heading(column, text=column)
            table.column(column, width=width, minwidth=50, anchor="e" if column not in ("시장", "종목코드", "종목명") else "center")
        table.tag_configure("up", foreground="#c92f3c")
        table.tag_configure("down", foreground="#245db5")
        table.tag_configure("flat", foreground="#343a40")
        table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        table.configure(yscrollcommand=scrollbar.set)
        table.bind("<<TreeviewSelect>>", self._on_watchlist_row_selected)
        table.bind("<Double-1>", lambda _event: self._activate_watchlist_selection())

        detail_panel = ttk.LabelFrame(body, text="선택 종목 정보", padding=12)
        detail_panel.grid(row=0, column=1, sticky="nsew")
        detail_panel.columnconfigure(1, weight=1)
        for row, key in enumerate(self.watchlist_detail_vars):
            ttk.Label(detail_panel, text=key).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Label(
                detail_panel,
                textvariable=self.watchlist_detail_vars[key],
                anchor="e",
                font=("Malgun Gothic", 9, "bold") if key in ("종목", "현재가") else None,
            ).grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=4)

        ttk.Label(
            window,
            textvariable=self.watchlist_status_var,
            padding=(12, 4, 12, 10),
        ).grid(row=2, column=0, sticky="ew")

        self._render_watchlist_rows()
        entry.focus_set()
        if self.service.account_info.connected:
            window.after_idle(self._refresh_watchlist_quotes)

    def _close_watchlist_window(self) -> None:
        if self._watchlist_link_after_id is not None:
            self.after_cancel(self._watchlist_link_after_id)
            self._watchlist_link_after_id = None
        if self._watchlist_window is not None and self._watchlist_window.winfo_exists():
            self._watchlist_window.destroy()
        self._watchlist_window = None
        self._watchlist_table = None
        self.watchlist_symbol_var = None
        self.watchlist_auto_link_var = None
        self.watchlist_status_var = None
        self.watchlist_detail_vars = {}

    def _register_watchlist_symbol(self) -> None:
        if self.watchlist_symbol_var is None:
            return
        symbol = self.service.add_watchlist_symbol(self.watchlist_symbol_var.get())
        if self.watchlist_status_var is not None:
            self.watchlist_status_var.set(self.service.last_api_message)
        if not symbol:
            return
        self.watchlist_symbol_var.set("")
        if self.service.account_info.connected:
            self._refresh_watchlist_quotes(select_symbol=symbol)
        else:
            self._render_watchlist_rows(select_symbol=symbol)

    def _remove_watchlist_symbol(self) -> None:
        symbol = self._selected_watchlist_symbol()
        if not symbol:
            messagebox.showwarning("관심종목 선택", "삭제할 관심종목을 선택해 주세요.")
            return
        if not messagebox.askyesno("관심종목 삭제", f"{symbol} 종목을 관심목록에서 삭제할까요?"):
            return
        self.service.remove_watchlist_symbol(symbol)
        if self.watchlist_status_var is not None:
            self.watchlist_status_var.set(self.service.last_api_message)
        self._render_watchlist_rows()

    def _refresh_watchlist_quotes(self, select_symbol: str = "") -> None:
        if self.service.account_info.connected:
            self.service.refresh_watchlist_quotes()
        elif self.watchlist_status_var is not None:
            self.watchlist_status_var.set("키움 API 연결 후 현재가와 거래 정보를 불러올 수 있습니다.")
        if self.watchlist_status_var is not None and self.service.account_info.connected:
            self.watchlist_status_var.set(self.service.last_api_message)
        self._render_watchlist_rows(select_symbol=select_symbol)

    def _render_watchlist_rows(self, select_symbol: str = "") -> None:
        table = self._watchlist_table
        if table is None or not table.winfo_exists():
            return
        current_selection = select_symbol or self._selected_watchlist_symbol()
        self._watchlist_suppress_auto_connect = True
        for item in table.get_children():
            table.delete(item)
        for quote in self.service.watchlist_rows():
            table.insert(
                "",
                "end",
                iid=quote.symbol,
                values=self._watchlist_values(quote),
                tags=(self._watchlist_tag(quote),),
            )
        if current_selection and table.exists(current_selection):
            table.selection_set(current_selection)
            table.focus(current_selection)
            table.see(current_selection)
            self._show_watchlist_details(self._watchlist_quote(current_selection))
        else:
            self._clear_watchlist_details()
        self.after(120, self._allow_watchlist_auto_connect)

    def _allow_watchlist_auto_connect(self) -> None:
        self._watchlist_suppress_auto_connect = False

    def _on_watchlist_row_selected(self, _event: tk.Event | None = None) -> None:
        symbol = self._selected_watchlist_symbol()
        self._show_watchlist_details(self._watchlist_quote(symbol))
        if self._watchlist_suppress_auto_connect:
            return
        if self.watchlist_auto_link_var is None or not self.watchlist_auto_link_var.get():
            return
        if not self.service.account_info.connected:
            return
        if self._watchlist_link_after_id is not None:
            self.after_cancel(self._watchlist_link_after_id)
        self._watchlist_link_after_id = self.after(350, self._activate_watchlist_selection)

    def _activate_watchlist_selection(self) -> None:
        self._watchlist_link_after_id = None
        symbol = self._selected_watchlist_symbol()
        if not symbol:
            messagebox.showwarning("관심종목 선택", "메인 차트와 연결할 관심종목을 선택해 주세요.")
            return
        if not self.service.account_info.connected:
            if self.watchlist_status_var is not None:
                self.watchlist_status_var.set("키움 API 연결 후 관심종목을 차트와 연결할 수 있습니다.")
            return

        self.symbol_var.set(symbol)
        self.service.configure(symbol, float(self.capital_var.get()), self._settings())
        self.service.request_current_price(symbol)
        timeframe = self.chart_timeframe_var.get()
        if timeframe.endswith("s"):
            self.service.register_real_time_price(symbol)
            self.service.select_realtime_chart(int(timeframe[:-1]))
        else:
            self.service.request_chart_candles(int(timeframe[:-1]), symbol)
            self.service.register_real_time_price(symbol)
        self.symbol_name_var.set(self.service.symbol_name)
        self._refresh()
        self._render_watchlist_rows(select_symbol=symbol)
        self.main_notebook.select(self.dmi_chart_tab)
        if self.watchlist_status_var is not None:
            self.watchlist_status_var.set(
                f"{symbol} {self.service.symbol_name} 시세·실시간·차트를 자동 연결했습니다."
            )

    def _selected_watchlist_symbol(self) -> str:
        table = self._watchlist_table
        if table is None or not table.winfo_exists():
            return ""
        selected = table.selection()
        return normalize_symbol(selected[0]) if selected else ""

    def _watchlist_quote(self, symbol: str) -> WatchlistQuote | None:
        normalized = normalize_symbol(symbol)
        return next(
            (quote for quote in self.service.watchlist_rows() if quote.symbol == normalized),
            None,
        )

    def _show_watchlist_details(self, quote: WatchlistQuote | None) -> None:
        if quote is None or not self.watchlist_detail_vars:
            self._clear_watchlist_details()
            return
        has_price = quote.current_price > 0
        values = {
            "종목": f"{quote.symbol} {quote.name or '미조회'}",
            "현재가": f"{quote.current_price:,.0f}원" if has_price else "-",
            "전일대비": self._format_watchlist_change(quote.change) if has_price else "-",
            "등락률": f"{quote.change_rate:+.2f}%" if has_price else "-",
            "거래량": f"{quote.volume:,}주" if has_price else "-",
            "거래대금": f"{quote.trade_value:,.0f}원" if quote.trade_value else "-",
            "시가": f"{quote.open_price:,.0f}원" if quote.open_price else "-",
            "고가": f"{quote.high_price:,.0f}원" if quote.high_price else "-",
            "저가": f"{quote.low_price:,.0f}원" if quote.low_price else "-",
            "매도호가": f"{quote.ask_price:,.0f}원" if quote.ask_price else "-",
            "매수호가": f"{quote.bid_price:,.0f}원" if quote.bid_price else "-",
            "체결시간": quote.timestamp or "-",
        }
        for key, value in values.items():
            self.watchlist_detail_vars[key].set(value)

    def _clear_watchlist_details(self) -> None:
        for variable in self.watchlist_detail_vars.values():
            variable.set("-")

    def _update_watchlist_live_row(self) -> None:
        table = self._watchlist_table
        quote = self.service.real_time_quote
        if table is None or quote is None or not table.winfo_exists() or not table.exists(quote.symbol):
            return
        watch_quote = self._watchlist_quote(quote.symbol)
        if watch_quote is None:
            return
        table.item(
            quote.symbol,
            values=self._watchlist_values(watch_quote),
            tags=(self._watchlist_tag(watch_quote),),
        )
        if self._selected_watchlist_symbol() == quote.symbol:
            self._show_watchlist_details(watch_quote)

    @staticmethod
    def _watchlist_values(quote: WatchlistQuote) -> tuple:
        has_price = quote.current_price > 0
        return (
            quote.market,
            quote.symbol,
            quote.name or "미조회",
            f"{quote.current_price:,.0f}" if has_price else "-",
            TraderApp._format_watchlist_change(quote.change) if has_price else "-",
            f"{quote.change_rate:+.2f}%" if has_price else "-",
            f"{quote.volume:,}" if has_price else "-",
        )

    @staticmethod
    def _format_watchlist_change(value: float) -> str:
        return f"{value:+,.0f}"

    @staticmethod
    def _watchlist_tag(quote: WatchlistQuote) -> str:
        if quote.change_rate > 0:
            return "up"
        if quote.change_rate < 0:
            return "down"
        return "flat"

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
            self._update_connection_badge(True, account_info.connection_method)
            self.service.lookup_symbol_name(self.symbol_var.get())
            self.service.request_current_price(self.symbol_var.get())
            self.service.request_chart_candles(3, self.symbol_var.get())
            self.service.register_real_time_price(self.symbol_var.get())
            if self._account_for_api():
                password = self._balance_password()
                if password is not None:
                    try:
                        self.service.request_balance(self._account_for_api(), password)
                    finally:
                        self.account_password_var.set("")
        self._refresh()
        login_failed = self.service.kiwoom_api.last_login_error not in (None, 0)
        identity_rejected = bool(account_info.user_id and not account_info.connected)
        if account_info.connected or login_failed or identity_rejected or self._account_poll_count >= 120:
            self._set_login_buttons_state("normal")
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
        self.service.step(float(self.price_var.get()))
        self._refresh()

    def _request_current_price(self) -> None:
        if not self._require_live_connection():
            return
        self.service.configure(self.symbol_var.get(), float(self.capital_var.get()), self._settings())
        self.service.request_current_price(self.symbol_var.get())
        self._refresh()

    def _request_three_minute(self) -> None:
        if not self._require_live_connection():
            return
        self.service.configure(self.symbol_var.get(), float(self.capital_var.get()), self._settings())
        self.chart_timeframe_var.set("3m")
        self.service.request_chart_candles(3, self.symbol_var.get())
        self._refresh()
        self.main_notebook.select(self.dmi_chart_tab)

    def _on_chart_timeframe_changed(self) -> None:
        if not self._require_live_connection():
            self.chart_timeframe_var.set(self.service.chart_timeframe)
            return
        self._load_selected_chart()
        self._refresh()
        self.main_notebook.select(self.dmi_chart_tab)

    def _reload_chart_timeframe(self) -> None:
        if not self._require_live_connection():
            return
        self._load_selected_chart()
        self._refresh()

    def _load_selected_chart(self) -> list[Candle]:
        self.service.configure(self.symbol_var.get(), float(self.capital_var.get()), self._settings())
        timeframe = self.chart_timeframe_var.get()
        if timeframe.endswith("s"):
            if self.service.real_time_symbol != normalize_symbol(self.symbol_var.get()):
                self.service.register_real_time_price(self.symbol_var.get())
            return self.service.select_realtime_chart(int(timeframe[:-1]))
        return self.service.request_chart_candles(int(timeframe[:-1]), self.symbol_var.get())

    def _change_chart_zoom(self, delta: int) -> None:
        self._chart_visible_count = max(40, min(200, self._chart_visible_count + delta))
        self.chart_visible_count_var.set(f"{self._chart_visible_count}봉")
        self._draw_main_dmi_chart()
        self._draw_popup_chart()

    def _request_balance(self) -> None:
        if not self._require_live_connection():
            return
        password = self._balance_password()
        if password is None:
            self.service.last_api_message = "계좌 비밀번호는 숫자 4~8자리로 입력해 주세요."
            self._refresh()
            self.account_password_entry.focus_set()
            return
        try:
            balance = self.service.request_balance(self._account_for_api(), password)
        finally:
            self.account_password_var.set("")
        self._refresh()
        if balance is not None:
            self._show_account_info_window(refresh=True)

    def _balance_password(self) -> str | None:
        if self.service.account_info.connection_method == "REST API":
            return ""
        password = self.account_password_var.get().strip()
        return password if is_valid_account_password(password) else None

    def _register_real_time(self) -> None:
        if not self._require_live_connection():
            return
        self.service.configure(self.symbol_var.get(), float(self.capital_var.get()), self._settings())
        self.service.register_real_time_price(self.symbol_var.get())
        self._refresh()

    def _unregister_real_time(self) -> None:
        if not self._require_live_connection():
            return
        self.service.unregister_real_time()
        self._refresh()

    def _evaluate_market_strategy(self) -> None:
        if not self._require_live_connection():
            return
        self.service.configure(self.symbol_var.get(), float(self.capital_var.get()), self._settings())
        self.service.evaluate_strategy_with_market_data(self.symbol_var.get())
        self._refresh()
        self.main_notebook.select(self.dmi_chart_tab)

    def _show_candle_chart(self) -> None:
        if not self._require_live_connection():
            return
        candles = self._load_selected_chart()
        self._refresh()
        if not candles:
            if self.chart_timeframe_var.get().endswith("s"):
                messagebox.showwarning("실시간 체결 대기", "키움 실시간 체결이 수신되면 초봉이 생성됩니다.")
            else:
                messagebox.showwarning("차트 데이터 없음", "키움에서 선택한 주기의 차트 데이터를 받지 못했습니다.")
            return

        self._close_candle_chart()
        window = tk.Toplevel(self)
        self._candle_chart_window = window
        label = timeframe_label(self.service.chart_timeframe)
        window.title(f"키움 {label}봉 차트 - {self.service.symbol} {self.service.symbol_name}")
        window.geometry("1040x680")
        window.minsize(760, 520)
        window.transient(self)
        window.protocol("WM_DELETE_WINDOW", self._close_candle_chart)

        body = ttk.Frame(window, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text=(
                f"{self.service.symbol} {self.service.symbol_name} | 키움 {label}봉 | "
                f"DMI({self.service.strategy.settings.dmi_period}) | 자동매매 기준 3분봉"
            ),
            font=("Malgun Gothic", 13, "bold"),
        ).pack(anchor="w", pady=(0, 8))
        canvas = tk.Canvas(
            body,
            background="#ffffff",
            highlightthickness=1,
            highlightbackground="#c8c8c8",
        )
        canvas.pack(fill="both", expand=True)
        self._candle_chart_canvas = canvas
        canvas.bind("<Configure>", lambda _event: self._draw_popup_chart())
        canvas.bind("<Motion>", lambda event: self._on_chart_motion(canvas, event))
        canvas.bind("<Leave>", lambda _event: canvas.delete("crosshair"))
        window.after_idle(self._draw_popup_chart)

    def _close_candle_chart(self) -> None:
        if self._candle_chart_window is not None and self._candle_chart_window.winfo_exists():
            self._candle_chart_window.destroy()
        self._candle_chart_window = None
        self._candle_chart_canvas = None

    def _draw_main_dmi_chart(self) -> None:
        candles = self._chronological_candles(self.service.chart_candles_for_display())
        self._draw_candle_chart(self.main_chart_canvas, candles)

    def _draw_popup_chart(self) -> None:
        if self._candle_chart_canvas is None or not self._candle_chart_canvas.winfo_exists():
            return
        candles = self._chronological_candles(self.service.chart_candles_for_display())
        self._draw_candle_chart(self._candle_chart_canvas, candles)

    @staticmethod
    def _chronological_candles(candles: list[Candle]) -> list[Candle]:
        def timestamp_key(candle: Candle) -> str:
            return "".join(character for character in candle.timestamp if character.isdigit())

        if not candles or not all(timestamp_key(candle) for candle in candles):
            return list(candles)
        return sorted(candles, key=timestamp_key)

    def _draw_candle_chart(self, canvas: tk.Canvas, candles: list[Candle]) -> None:
        canvas.delete("all")
        self._chart_render_state.pop(canvas, None)
        if not candles:
            width = max(1, canvas.winfo_width())
            height = max(1, canvas.winfo_height())
            empty_text = (
                "실시간 체결 대기"
                if self.service.chart_timeframe.endswith("s")
                else "차트 데이터 대기"
            )
            canvas.create_text(
                width / 2,
                height / 2,
                text=empty_text,
                fill="#777777",
                font=("Malgun Gothic", 12),
            )
            return

        width = max(600, canvas.winfo_width())
        height = max(220, canvas.winfo_height())
        left_pad, right_pad, top_pad, bottom_pad = 18, 82, 28, 38
        plot_width = max(1, width - left_pad - right_pad)
        available_height = max(150, height - top_pad - bottom_pad)
        show_dmi = self.show_dmi_chart_var.get()
        indicator_height = max(50, int(available_height * 0.28)) if show_dmi else 0
        pane_gap = (18 if available_height < 220 else 30) if show_dmi else 0
        price_height = max(82, available_height - indicator_height - pane_gap)
        price_bottom = top_pad + price_height
        dmi_top = price_bottom + pane_gap
        dmi_bottom = dmi_top + indicator_height

        display_start = max(0, len(candles) - self._chart_visible_count)
        displayed = candles[display_start:]
        dmi_series = self.service.strategy.calculate_dmi_series(candles)
        dmi_by_index = {
            point.index - display_start: point
            for point in dmi_series
            if point.index >= display_start
        }
        lowest = min(
            min(candle.low, candle.open or candle.close, candle.close) for candle in displayed
        )
        highest = max(
            max(candle.high, candle.open or candle.close, candle.close) for candle in displayed
        )
        price_span = max(1.0, highest - lowest)

        def price_y(price: float) -> float:
            return top_pad + ((highest - price) / price_span) * price_height

        def dmi_y(value: float) -> float:
            bounded = max(0.0, min(100.0, value))
            return dmi_top + ((100.0 - bounded) / 100.0) * indicator_height

        step = plot_width / max(1, len(displayed))
        if self.show_pattern_var.get():
            pattern_bottom = dmi_bottom if show_dmi else price_bottom
            for index, point in dmi_by_index.items():
                if index < 0 or index >= len(displayed) or point.pattern_state == "NONE":
                    continue
                fill = "#fde4e9" if point.pattern_state == "BULLISH" else "#e3f2fb"
                x0 = left_pad + step * index
                x1 = left_pad + step * (index + 1)
                canvas.create_rectangle(x0, top_pad, x1, pattern_bottom, fill=fill, outline="")

        for level_index in range(5):
            ratio = level_index / 4
            y = top_pad + ratio * price_height
            price = highest - ratio * price_span
            canvas.create_line(
                left_pad,
                y,
                left_pad + plot_width,
                y,
                fill="#d5dce1",
                dash=(2, 3),
            )
            canvas.create_text(
                left_pad + plot_width + 8,
                y,
                text=f"{price:,.0f}",
                anchor="w",
                fill="#3f4a52",
                font=("Malgun Gothic", 8),
            )

        label_indexes = sorted(
            {
                round(index * (len(displayed) - 1) / min(5, max(1, len(displayed) - 1)))
                for index in range(min(5, max(1, len(displayed) - 1)) + 1)
            }
        )
        chart_bottom = dmi_bottom if show_dmi else price_bottom
        for index in label_indexes:
            x = left_pad + step * (index + 0.5)
            canvas.create_line(x, top_pad, x, chart_bottom, fill="#e1e5e8", dash=(2, 3))

        def draw_price_line(values: list[float | None], color: str, width_value: int = 2) -> None:
            coordinates: list[float] = []
            for index, value in enumerate(values[display_start:]):
                if value is None:
                    if len(coordinates) >= 4:
                        canvas.create_line(*coordinates, fill=color, width=width_value, smooth=True)
                    coordinates = []
                    continue
                x = left_pad + step * (index + 0.5)
                coordinates.extend((x, price_y(value)))
            if len(coordinates) >= 4:
                canvas.create_line(*coordinates, fill=color, width=width_value, smooth=True)

        body_width = max(2.0, min(10.0, step * 0.62))
        for index, candle in enumerate(displayed):
            x = left_pad + step * (index + 0.5)
            open_price = candle.open or candle.close
            color = "#d64545" if candle.close >= open_price else "#2f62bd"
            canvas.create_line(x, price_y(candle.high), x, price_y(candle.low), fill=color, width=1)
            open_y = price_y(open_price)
            close_y = price_y(candle.close)
            top = min(open_y, close_y)
            bottom = max(open_y, close_y)
            if bottom - top < 1:
                bottom = top + 1
            canvas.create_rectangle(
                x - body_width / 2,
                top,
                x + body_width / 2,
                bottom,
                fill=color,
                outline=color,
            )

        if self.show_ma5_var.get():
            draw_price_line(moving_average(candles, 5), "#d92787")
        if self.show_ma20_var.get():
            draw_price_line(moving_average(candles, 20), "#3459c7")

        canvas.create_line(left_pad, price_bottom, left_pad + plot_width, price_bottom, fill="#6f7880")
        canvas.create_line(
            left_pad + plot_width,
            top_pad,
            left_pad + plot_width,
            chart_bottom,
            fill="#6f7880",
        )

        previous_point = None
        for index in sorted(dmi_by_index):
            point = dmi_by_index[index]
            if index < 0 or index >= len(displayed):
                continue
            if previous_point is not None:
                candle = displayed[index]
                x = left_pad + step * (index + 0.5)
                if previous_point.pattern_state == "BEARISH" and point.pattern_state == "BULLISH":
                    marker_y = min(price_bottom - 10, price_y(candle.low) + 13)
                    canvas.create_text(x, marker_y, text="▲ 매수", fill="#b51f63", font=("Malgun Gothic", 8, "bold"))
                elif previous_point.pattern_state == "BULLISH" and point.pattern_state == "BEARISH":
                    marker_y = max(top_pad + 10, price_y(candle.high) - 13)
                    canvas.create_text(x, marker_y, text="▼ 매도", fill="#1769a7", font=("Malgun Gothic", 8, "bold"))
            previous_point = point

        latest = displayed[-1]
        latest_y = price_y(latest.close)
        canvas.create_line(
            left_pad,
            latest_y,
            left_pad + plot_width,
            latest_y,
            fill="#555555",
            dash=(4, 3),
        )
        canvas.create_text(
            left_pad + plot_width + 8,
            latest_y,
            text=f"{latest.close:,.0f}",
            anchor="w",
            fill="#20262b",
            font=("Malgun Gothic", 8, "bold"),
        )

        if show_dmi:
            canvas.create_line(left_pad, dmi_top, left_pad + plot_width, dmi_top, fill="#6f7880")
            canvas.create_line(left_pad, dmi_bottom, left_pad + plot_width, dmi_bottom, fill="#6f7880")
            for level in (0, 25, 50, 75, 100):
                y = dmi_y(float(level))
                canvas.create_line(left_pad, y, left_pad + plot_width, y, fill="#d5dce1", dash=(2, 3))
                canvas.create_text(
                    left_pad + plot_width + 8,
                    y,
                    text=str(level),
                    anchor="w",
                    fill="#555555",
                    font=("Malgun Gothic", 8),
                )

            def draw_dmi_line(attribute: str, color: str, width_value: int = 2) -> None:
                coordinates: list[float] = []
                for index in sorted(dmi_by_index):
                    point = dmi_by_index[index]
                    value = getattr(point, attribute)
                    if value is None or index < 0 or index >= len(displayed):
                        if len(coordinates) >= 4:
                            canvas.create_line(*coordinates, fill=color, width=width_value, smooth=True)
                        coordinates = []
                        continue
                    x = left_pad + step * (index + 0.5)
                    coordinates.extend((x, dmi_y(float(value))))
                if len(coordinates) >= 4:
                    canvas.create_line(*coordinates, fill=color, width=width_value, smooth=True)

            draw_dmi_line("plus_di", "#d92787")
            draw_dmi_line("minus_di", "#1686b8")
            draw_dmi_line("adx", "#555555", 1)
            canvas.create_text(
                left_pad,
                dmi_top - 18,
                text="+DI  -DI  ADX",
                anchor="w",
                fill="#555555",
                font=("Malgun Gothic", 8, "bold"),
            )

        for index in label_indexes:
            timestamp = displayed[index].timestamp
            label = self._format_chart_timestamp(timestamp, compact=True)
            x = left_pad + step * (index + 0.5)
            canvas.create_text(
                x,
                chart_bottom + 14,
                text=label or f"{index + 1}",
                anchor="n",
                fill="#555555",
                font=("Malgun Gothic", 8),
            )

        self._chart_render_state[canvas] = {
            "displayed": displayed,
            "dmi_by_index": dmi_by_index,
            "left": left_pad,
            "right": left_pad + plot_width,
            "top": top_pad,
            "bottom": chart_bottom,
            "price_bottom": price_bottom,
            "step": step,
        }

    def _on_chart_motion(self, canvas: tk.Canvas, event: tk.Event) -> None:
        canvas.delete("crosshair")
        state = self._chart_render_state.get(canvas)
        if not state or event.x < state["left"] or event.x > state["right"]:
            return
        if event.y < state["top"] or event.y > state["bottom"]:
            return

        displayed = state["displayed"]
        index = int((event.x - state["left"]) / state["step"])
        index = max(0, min(len(displayed) - 1, index))
        candle = displayed[index]
        x = state["left"] + state["step"] * (index + 0.5)
        canvas.create_line(
            x,
            state["top"],
            x,
            state["bottom"],
            fill="#59666f",
            dash=(3, 3),
            tags="crosshair",
        )
        if event.y <= state["price_bottom"]:
            canvas.create_line(
                state["left"],
                event.y,
                state["right"],
                event.y,
                fill="#59666f",
                dash=(3, 3),
                tags="crosshair",
            )

        point = state["dmi_by_index"].get(index)
        dmi_text = ""
        if point is not None:
            adx = "-" if point.adx is None else f"{point.adx:.2f}"
            dmi_text = f"\n+DI {point.plus_di:.2f}  -DI {point.minus_di:.2f}  ADX {adx}"
        tooltip = (
            f"{self._format_chart_timestamp(candle.timestamp)}\n"
            f"시가 {candle.open or candle.close:,.0f}  고가 {candle.high:,.0f}\n"
            f"저가 {candle.low:,.0f}  종가 {candle.close:,.0f}\n"
            f"거래량 {candle.volume:,}{dmi_text}"
        )
        tooltip_x = x + 12 if x < state["right"] - 235 else x - 225
        tooltip_y = state["top"] + 10
        text_id = canvas.create_text(
            tooltip_x,
            tooltip_y,
            text=tooltip,
            anchor="nw",
            justify="left",
            fill="#20262b",
            font=("Malgun Gothic", 9),
            tags="crosshair",
        )
        bounds = canvas.bbox(text_id)
        if bounds:
            background = canvas.create_rectangle(
                bounds[0] - 6,
                bounds[1] - 5,
                bounds[2] + 6,
                bounds[3] + 5,
                fill="#f5f6f7",
                outline="#59666f",
                tags="crosshair",
            )
            canvas.tag_lower(background, text_id)

    @staticmethod
    def _format_chart_timestamp(timestamp: str, compact: bool = False) -> str:
        digits = "".join(character for character in str(timestamp) if character.isdigit())
        if len(digits) >= 14:
            if compact:
                return f"{digits[8:10]}:{digits[10:12]}:{digits[12:14]}"
            return (
                f"{digits[:4]}-{digits[4:6]}-{digits[6:8]} "
                f"{digits[8:10]}:{digits[10:12]}:{digits[12:14]}"
            )
        if len(digits) >= 6:
            return f"{digits[-6:-4]}:{digits[-4:-2]}:{digits[-2:]}"
        return str(timestamp)

    def _send_order(self, side: str) -> None:
        if not self._require_live_connection():
            return
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
        if not self._ensure_live_connection():
            if not auto:
                messagebox.showwarning("키움 API 연결 필요", self.service.account_info.message)
            self._refresh()
            return
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
        account = snapshot.account_info
        connection_method = account.connection_method or "OpenAPI+"
        self._update_connection_badge(account.connected, connection_method)
        password_state = "disabled" if account.connected and connection_method == "REST API" else "normal"
        self.account_password_entry.configure(state=password_state)
        real_order_state = "disabled" if connection_method == "REST API" else "normal"
        self.allow_real_order_checkbutton.configure(state=real_order_state)
        self._update_dmi_display(snapshot.dmi)
        self.status_text.set(self._format_main_status(snapshot))
        if snapshot.symbol_name and self.symbol_name_var.get() != snapshot.symbol_name:
            self.symbol_name_var.set(snapshot.symbol_name)
        self.account_summary_var.set(self._format_account_summary(snapshot))
        self.trade_ready_var.set(self._format_trade_ready(snapshot))
        if self.chart_timeframe_var.get() != snapshot.chart_timeframe:
            self.chart_timeframe_var.set(snapshot.chart_timeframe)
        chart_label = timeframe_label(snapshot.chart_timeframe)
        self.chart_caption_var.set(
            f"{snapshot.symbol} {snapshot.symbol_name} | {chart_label}봉 {len(snapshot.chart_candles)}개 | "
            f"{snapshot.chart_source} | DMI({self.service.strategy.settings.dmi_period}) | "
            "자동매매 기준 3분봉"
        )
        self._update_trade_buttons()
        holdings = list(snapshot.balance_summary.holdings) if snapshot.balance_summary else []
        self._replace_rows(self.holdings, self._format_holdings(holdings))
        self._replace_rows(self.orders, self._format_orders(snapshot.orders))
        self._replace_rows(self.logs, self._format_logs(snapshot.logs))
        if self._watchlist_window is not None and self._watchlist_window.winfo_exists():
            self._render_watchlist_rows()
        self.after_idle(self._draw_main_dmi_chart)
        self._schedule_auto_tick()
        self._schedule_chart_refresh()

    def _update_dmi_display(self, dmi: DmiPoint | None) -> None:
        if dmi is None:
            self.dmi_state_var.set("계산 전")
            self.dmi_plus_var.set("-")
            self.dmi_minus_var.set("-")
            self.adx_var.set("-")
            self.dmi_state_badge.configure(bg="#777777", fg="white")
            return
        self.dmi_plus_var.set(f"{dmi.plus_di:.2f}")
        self.dmi_minus_var.set(f"{dmi.minus_di:.2f}")
        self.adx_var.set("계산 중" if dmi.adx is None else f"{dmi.adx:.2f}")
        if dmi.pattern_state == "BULLISH":
            self.dmi_state_var.set("강세")
            self.dmi_state_badge.configure(bg="#d84f88", fg="white")
        elif dmi.pattern_state == "BEARISH":
            self.dmi_state_var.set("약세")
            self.dmi_state_badge.configure(bg="#3a84c6", fg="white")
        else:
            self.dmi_state_var.set("중립")
            self.dmi_state_badge.configure(bg="#777777", fg="white")

    @staticmethod
    def _format_main_status(snapshot) -> str:
        account = snapshot.account_info
        connection_method = account.connection_method or "OpenAPI+"
        account_label = f"{connection_method} 연결됨" if account.connected else "미연결"
        if account.connected and account.server_type:
            account_label = f"{account_label}({account.server_type})"
        decision_key = snapshot.decision.action if snapshot.decision else "NONE"
        decision = ACTION_LABELS.get(decision_key, decision_key)
        dmi = ""
        if snapshot.dmi is not None:
            adx = "-" if snapshot.dmi.adx is None else f"{snapshot.dmi.adx:.2f}"
            dmi = f" +DI {snapshot.dmi.plus_di:.2f} -DI {snapshot.dmi.minus_di:.2f} ADX {adx}"
        parts = [
            f"키움 계좌 {account_label}",
            f"운용 {'중' if snapshot.running else '중지'}",
            f"종목 {snapshot.symbol} {snapshot.symbol_name}".strip(),
            f"패턴 {PATTERN_VALUE_TO_LABEL.get(snapshot.pattern, snapshot.pattern)}",
            f"현재가 {snapshot.price:,.0f}",
            f"보유 {snapshot.quantity}주",
            f"평균 {snapshot.average_price:,.0f}",
            f"판단 {decision}{dmi}",
        ]
        quote = snapshot.real_time_quote or snapshot.market_quote
        if quote:
            parts.append(f"시세 {quote.symbol} {quote.current_price:,.0f}")
        if snapshot.balance_summary:
            parts.append(
                f"잔고 {len(snapshot.balance_summary.holdings)}종목 "
                f"평가 {snapshot.balance_summary.total_evaluation:,.0f}"
            )
        return " | ".join(parts)

    def _auto_tick(self) -> None:
        self._refresh_after_id = None
        if self.service.account_info.connected and not self._ensure_live_connection():
            self._refresh()
            return
        if self.service.running:
            if self.kiwoom_auto_order_var.get():
                if self._trading_ready():
                    self._evaluate_and_send_order(auto=True)
                else:
                    self.service.stop()
                    self.service.storage.log(
                        "ERROR",
                        "자동주문",
                        "키움 API 연결 또는 주문 준비 상태가 해제되어 자동운용을 중지했습니다.",
                    )
                    self._refresh()
            else:
                self._run_tick()
        else:
            self._refresh()

    def _schedule_auto_tick(self) -> None:
        if self._refresh_after_id is None:
            self._refresh_after_id = self.after(3000, self._auto_tick)

    def _schedule_chart_refresh(self) -> None:
        if self._chart_refresh_after_id is None:
            delay = 250 if self.service.chart_timeframe.endswith("s") else 1000
            self._chart_refresh_after_id = self.after(delay, self._chart_refresh_tick)

    def _chart_refresh_tick(self) -> None:
        self._chart_refresh_after_id = None
        if self.service.real_time_symbol:
            self.service.refresh_real_time_quote()
            self._update_watchlist_live_row()
            if self.service.chart_timeframe.endswith("s"):
                candles = self.service.chart_candles_for_display()
                label = timeframe_label(self.service.chart_timeframe)
                self.chart_caption_var.set(
                    f"{self.service.symbol} {self.service.symbol_name} | {label}봉 {len(candles)}개 | "
                    f"{self.service.chart_source} | DMI({self.service.strategy.settings.dmi_period}) | "
                    "자동매매 기준 3분봉"
                )
                self._draw_main_dmi_chart()
                self._draw_popup_chart()
        self._schedule_chart_refresh()

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
                "OpenAPI+ 로그인 또는 REST API 연결 후 계좌번호 앞4자리+뒤4자리와 잔고가 표시됩니다."
            )
        connection_method = snapshot.account_info.connection_method or "OpenAPI+"
        if not snapshot.balance_summary:
            balance_help = (
                "'계좌잔고 불러오기'를 누르면 잔고가 표시됩니다."
                if connection_method == "REST API"
                else "계좌 비밀번호 입력 후 '계좌잔고 불러오기'를 누르면 잔고가 표시됩니다."
            )
            return (
                f"계좌 창: {connection_method} 연결됨({snapshot.account_info.server_type}) | "
                f"선택 계좌 {selected_label} | {balance_help}"
            )
        balance = snapshot.balance_summary
        return (
            f"계좌 창: {connection_method} 연결됨({snapshot.account_info.server_type}) | "
            f"선택 계좌 {mask_account_number(balance.account)} | "
            f"예수금 {balance.deposit:,.0f}원 | 주문가능 {balance.orderable_amount:,.0f}원 | "
            f"출금가능 {balance.withdrawable_amount:,.0f}원 | 보유 {len(balance.holdings)}종목 | "
            f"추정예탁자산 {balance.estimated_assets:,.0f}원 | "
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
            missing.append("키움 API 로그인/연결")
        if not account:
            missing.append("계좌번호")
        if not name or name == "키움 로그인 후 조회 필요":
            missing.append("회사명")
        if not quantity_ok:
            missing.append("주문수량")
        if snapshot.account_info.server_type == "실거래" and not self.allow_real_order_var.get():
            if snapshot.account_info.connection_method == "REST API":
                missing.append("REST 실전 조회 전용(주문 잠금)")
            else:
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
        chart_state = "normal" if self.service.account_info.connected else "disabled"
        self.chart_button.configure(state=chart_state)

    def _require_live_connection(self) -> bool:
        if self._ensure_live_connection():
            return True
        self._refresh()
        messagebox.showwarning("키움 API 연결 필요", self.service.account_info.message)
        return False

    def _ensure_live_connection(self) -> bool:
        if self.service.sync_account_connection():
            return True
        self._selected_account_full = ""
        self._set_account_display("")
        if self._account_info_window is not None and self._account_info_window.winfo_exists():
            self._account_info_window.destroy()
        self._account_info_window = None
        self._close_candle_chart()
        self._update_connection_badge(False)
        self._update_trade_buttons()
        return False

    def _update_connection_badge(self, connected: bool, connection_method: str = "") -> None:
        if connected:
            method = connection_method or "키움 API"
            self.connection_state_var.set(f"ON {method} 연결됨")
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

    def _show_account_info_window(self, refresh: bool = False) -> None:
        snapshot = self.service.snapshot()
        account_info = snapshot.account_info
        if not account_info.connected:
            return
        if self._account_info_window is not None and self._account_info_window.winfo_exists():
            if not refresh:
                self._account_info_window.lift()
                return
            self._account_info_window.destroy()

        window = tk.Toplevel(self)
        self._account_info_window = window
        window.title("키움 계좌정보")
        window.geometry("680x590")
        window.resizable(False, False)
        window.transient(self)
        window.protocol("WM_DELETE_WINDOW", window.destroy)

        body = ttk.Frame(window, padding=18)
        body.grid(row=0, column=0, sticky="nsew")
        ttk.Label(body, text="계좌정보", font=("Malgun Gothic", 15, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )
        login_event = (
            "접근토큰 발급 성공"
            if account_info.connection_method == "REST API"
            else "성공 (0)"
            if account_info.login_event_code == 0
            else "기존 연결 확인"
            if account_info.login_event_code is None
            else f"오류 ({account_info.login_event_code})"
        )
        rows = [
            ("연결 상태", f"ON {account_info.connection_method or 'OpenAPI+'} 연결됨"),
            ("연결 방식", account_info.connection_method or "OpenAPI+ ActiveX"),
            ("로그인 이벤트", login_event),
            ("정보 수신", "완료" if account_info.login_data_received else "확인 필요"),
            ("접속 서버", account_info.server_type or "확인 필요"),
            ("고객명", account_info.user_name or "확인 필요"),
            ("사용자 ID", account_info.user_id or "확인 필요"),
            ("API 확인 계좌", mask_account_number(self._account_for_api() or self._selected_account_full)),
            (
                "계좌 수",
                f"{len(account_info.accounts)}개 (키움 보고 {account_info.reported_account_count}개)",
            ),
            ("정보 활용", self._account_capability_label(account_info)),
        ]
        if snapshot.balance_summary:
            balance = snapshot.balance_summary
            rows.extend(
                [
                    ("예수금", f"{balance.deposit:,.0f}원"),
                    ("주문가능금액", f"{balance.orderable_amount:,.0f}원"),
                    ("출금가능금액", f"{balance.withdrawable_amount:,.0f}원"),
                    ("D+2 추정예수금", f"{balance.d2_estimated_deposit:,.0f}원"),
                    ("보유 종목", f"{len(balance.holdings)}종목"),
                    ("추정예탁자산", f"{balance.estimated_assets:,.0f}원"),
                ]
            )
        else:
            balance_help = (
                "연결 상태 확인 후 계좌잔고 불러오기를 눌러 주세요."
                if account_info.connection_method == "REST API"
                else "계좌 비밀번호 입력 후 잔고 불러오기를 눌러 주세요."
            )
            rows.append(("계좌 금액", balance_help))

        for row_index, (label, value) in enumerate(rows, start=1):
            ttk.Label(body, text=label, width=14).grid(row=row_index, column=0, sticky="w", pady=3)
            ttk.Label(body, text=value, width=48).grid(row=row_index, column=1, sticky="w", pady=3)

        action_row = len(rows) + 1
        if account_info.connection_method != "REST API":
            cash_actions = ttk.Frame(body)
            cash_actions.grid(
                row=action_row,
                column=0,
                columnspan=2,
                sticky="ew",
                pady=(12, 0),
            )
            ttk.Label(cash_actions, text="계좌 비밀번호(숫자 4~8자리)").pack(side="left")
            ttk.Entry(
                cash_actions,
                textvariable=self.account_password_var,
                width=10,
                show="*",
                validate="key",
                validatecommand=(self.register(_account_password_input_allowed), "%P"),
            ).pack(side="left", padx=(6, 8))
            ttk.Button(
                cash_actions,
                text="예수금·잔고 조회",
                command=self._request_balance,
            ).pack(side="left")
            action_row += 1

        ttk.Button(body, text="닫기", command=window.destroy).grid(
            row=action_row, column=0, columnspan=2, sticky="e", pady=(14, 0)
        )

    @staticmethod
    def _account_capability_label(account_info) -> str:
        if account_info.connection_method == "REST API" and account_info.server_type == "실거래":
            return "계좌 / 현재가 / 0B 실시간 / 3분봉 / 잔고 (주문 잠금)"
        if account_info.connection_method == "REST API":
            return "계좌 / 현재가 / 0B 실시간 / 3분봉 / 잔고 / 모의주문"
        return "계좌 / 현재가 / 실시간 시세 / 3분봉 / 잔고 / 모의주문"

    def _account_for_api(self) -> str:
        entered = clean_account_number(self.account_var.get())
        selected = clean_account_number(self._selected_account_full)
        if selected and entered == clean_account_number(mask_account_number(selected)):
            return selected
        return entered or selected

    def _set_account_display(self, account: str) -> None:
        digits = display_account_number(account)
        self.account_first_var.set(digits[:4] if len(digits) >= 4 else "")
        self.account_last_var.set(digits[4:8] if len(digits) >= 8 else "")
        self.account_var.set(mask_account_number(digits) if digits else "")

    def _settings(self) -> StrategySettings:
        return StrategySettings(dmi_period=max(1, int(float(self.dmi_period_var.get()))))
