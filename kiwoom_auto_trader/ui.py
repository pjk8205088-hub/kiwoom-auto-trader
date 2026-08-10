from __future__ import annotations

import csv
import json
import math
import os
import shutil
import sys
import tempfile
import tkinter as tk
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic
from tkinter import filedialog, font as tkfont, messagebox, ttk

from .app_security import (
    hash_secret,
    is_valid_pin,
    is_valid_recovery_password,
    mask_account_except_last_two,
    personalized_message,
    verify_secret,
)
from .audio_alerts import OrderVoiceNotifier, TradeSoundNotifier
from .charting import moving_average, timeframe_label
from .kiwoom_api import (
    KIWOOM_HOME_PAGE,
    is_valid_account_password,
)
from .models import (
    BalanceSummary,
    Candle,
    DmiPoint,
    MAX_DMI_PERIOD,
    MarketSessionStatus,
    MIN_DMI_PERIOD,
    PatternState,
    StrategySettings,
    TradingBaseline,
    VolumeRankQuote,
    WatchlistQuote,
)
from .order_pricing import daily_return_percent, midpoint_limit_price
from .rest_api import KIWOOM_REST_GUIDE, KIWOOM_REST_PORTAL
from .service import ACCOUNT_TRADE_HISTORY_DAYS, AutoTradingService
from .storage import Storage
from .symbols import (
    clean_account_number,
    display_account_number,
    mask_account_number,
    normalize_symbol,
)
from .windows_integration import (
    WindowsTrayIcon,
    set_startup_registration,
)


PATTERN_VALUE_TO_LABEL: dict[PatternState, str] = {
    "NONE": "계산 전",
    "BULLISH": "강세",
    "BEARISH": "약세",
}
ACTION_LABELS = {"BUY": "매수", "SELL": "매도", "HOLD": "대기", "NONE": "없음"}
SIDE_LABELS = {"BUY": "매수", "SELL": "매도"}
LEVEL_LABELS = {"INFO": "정보", "WARN": "주의", "ERROR": "오류"}
REST_LIVE_LABEL = "실전투자"
REST_MOCK_LABEL = "모의투자"
HOLDING_MONITOR_REFRESH_SECONDS = 10.0
VOLUME_RANK_REFRESH_MILLISECONDS = 5_000
TRADE_VALUE_RANK_REFRESH_MILLISECONDS = 30_000
EXPANDED_SIDE_PANEL_WIDTH = 760
EXPANDED_WINDOW_MIN_WIDTH = 1680

UI_FONT = "Noto Sans KR"
UI_DISPLAY_FONT = "Noto Sans KR"
UI_WORDMARK_FONT = "Segoe Script"
UI_BACKGROUND = "#FAFAFA"
UI_SURFACE = "#FFFFFF"
UI_TEXT = "#262626"
UI_MUTED = "#737373"
UI_BORDER = "#DBDBDB"
UI_SOFT = "#F3F3F3"
UI_PINK = "#E1306C"
UI_PURPLE = "#833AB4"
UI_ORANGE = "#F77737"
UI_YELLOW = "#FCAF45"
UI_BLUE = "#405DE6"
UI_GREEN = "#16833A"
UI_RED = "#ED4956"
UI_BUTTON_RADIUS = 8
WINDOW_OPACITY_MIN_PERCENT = 0
WINDOW_OPACITY_MAX_PERCENT = 100
HUNDRED_EOK_WON = 10_000_000_000.0

WATCHLIST_FIELD_SPECS: dict[str, tuple[str, int, str]] = {
    "market": ("시장", 58, "center"),
    "symbol": ("종목코드", 82, "center"),
    "name": ("종목명", 150, "w"),
    "current_price": ("현재가", 96, "e"),
    "trade_value": ("거래대금(백억)", 118, "e"),
    "previous_trade_value": ("전일 거래대금(백억)", 142, "e"),
    "change": ("전일비", 82, "e"),
    "change_rate": ("등락률", 78, "e"),
    "volume": ("거래량", 112, "e"),
    "market_cap": ("시가총액(백억)", 120, "e"),
    "trade_to_market_cap": ("대금/시총", 88, "e"),
    "program_trading_trend": ("프로그램 매매 추이", 138, "e"),
}
WATCHLIST_REQUIRED_FIELDS = ("symbol", "name")
WATCHLIST_DEFAULT_VISIBLE_FIELDS = (
    "market",
    "symbol",
    "name",
    "current_price",
    "change",
    "change_rate",
    "volume",
    "trade_value",
    "market_cap",
    "trade_to_market_cap",
)
WATCHLIST_SUPPLEMENTAL_FIELDS = {
    "previous_trade_value",
    "program_trading_trend",
}


def normalize_watchlist_layout(
    visible_fields: object,
    column_order: object,
) -> tuple[list[str], list[str]]:
    valid = tuple(WATCHLIST_FIELD_SPECS)
    visible_input = visible_fields if isinstance(visible_fields, (list, tuple)) else ()
    visible = [str(field) for field in visible_input if str(field) in valid]
    if not visible:
        visible = list(WATCHLIST_DEFAULT_VISIBLE_FIELDS)
    for field in WATCHLIST_REQUIRED_FIELDS:
        if field not in visible:
            visible.append(field)

    order_input = column_order if isinstance(column_order, (list, tuple)) else ()
    order: list[str] = []
    for field in order_input:
        name = str(field)
        if name in valid and name not in order:
            order.append(name)
    order.extend(field for field in valid if field not in order)
    visible_order = [field for field in order if field in visible]
    return visible_order, order


def decode_json_setting(raw_value: str, default: object) -> object:
    try:
        return json.loads(raw_value) if str(raw_value or "").strip() else default
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def normalize_account_history(value: object, limit: int = 10) -> list[str]:
    rows = value if isinstance(value, (list, tuple)) else ()
    normalized: list[str] = []
    for row in rows:
        account = clean_account_number(str(row or ""))
        if account and account not in normalized:
            normalized.append(account)
    return normalized[: max(1, int(limit))]


def _application_resource(*parts: str) -> Path:
    bundle_root = Path(
        getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])
    )
    return bundle_root.joinpath(*parts)


def _show_centered_dialog(window: tk.Toplevel) -> None:
    window.update_idletasks()
    width = max(window.winfo_reqwidth(), 420)
    height = max(window.winfo_reqheight(), 220)
    left = max(0, (window.winfo_screenwidth() - width) // 2)
    top = max(0, (window.winfo_screenheight() - height) // 2)
    window.geometry(f"{width}x{height}+{left}+{top}")
    window.deiconify()
    try:
        window.wm_attributes("-topmost", True)
        window.after(600, lambda: window.wm_attributes("-topmost", False))
    except tk.TclError:
        pass
    window.lift()
    window.focus_force()


def _compact_monitor_display(
    snapshot,
    watch_quote: WatchlistQuote | None = None,
    volume_quote: VolumeRankQuote | None = None,
) -> tuple[str, str, str, str]:
    symbol = normalize_symbol(getattr(snapshot, "symbol", "")) or "000000"
    candidates = (
        getattr(snapshot, "real_time_quote", None),
        getattr(snapshot, "market_quote", None),
        watch_quote,
        volume_quote,
    )
    matching_quotes = [
        quote
        for quote in candidates
        if quote is not None
        and normalize_symbol(getattr(quote, "symbol", "")) == symbol
    ]

    selected_quote = next(
        (
            quote
            for quote in matching_quotes
            if float(getattr(quote, "current_price", 0.0) or 0.0) > 0
        ),
        None,
    )
    price = float(
        getattr(selected_quote, "current_price", 0.0)
        if selected_quote is not None
        else (
            getattr(snapshot, "price", None)
            or getattr(snapshot, "current_price", 0.0)
            or 0.0
        )
    )
    quote_name = next(
        (
            str(getattr(quote, "name", "") or "").strip()
            for quote in matching_quotes
            if str(getattr(quote, "name", "") or "").strip()
        ),
        "",
    )
    symbol_name = str(getattr(snapshot, "symbol_name", "") or quote_name).strip()
    if symbol == "000000" and not symbol_name:
        stock_text = "감시 종목 미선택"
    else:
        stock_text = f"{symbol_name or symbol} · {symbol}"
        if bool(getattr(snapshot, "running", False)):
            stock_text = f"감시중 · {stock_text}"

    price_text = f"{price:,.0f}원" if price > 0 else "현재가 미조회"
    change = float(getattr(selected_quote, "change", 0.0) or 0.0)
    change_rate = float(getattr(selected_quote, "change_rate", 0.0) or 0.0)
    if abs(change_rate) < 1e-9 and abs(change) > 1e-9:
        previous_price = price - change
        if previous_price > 0:
            change_rate = (change / previous_price) * 100.0

    direction_value = change_rate if abs(change_rate) >= 1e-9 else change
    if direction_value > 0:
        return stock_text, price_text, f"▲ 상승 +{abs(change_rate):.2f}%", "up"
    if direction_value < 0:
        return stock_text, price_text, f"▼ 하락 -{abs(change_rate):.2f}%", "down"
    return stock_text, price_text, "─ 보합 0.00%", "flat"


def _format_hundred_eok_won(value: object) -> str:
    try:
        amount = float(value or 0.0)
    except (TypeError, ValueError, OverflowError):
        amount = 0.0
    if amount <= 0:
        return "-"
    return f"{amount / HUNDRED_EOK_WON:,.2f}"


def _account_access_confirmed(
    api_connected: bool,
    connection_method: str,
    account: str,
    password_verified: bool,
) -> bool:
    has_account = bool(clean_account_number(account))
    rest_account_verified = connection_method == "REST API"
    return bool(api_connected and has_account and (rest_account_verified or password_verified))


def _parse_order_quantity(value: str) -> int:
    text = str(value or "").strip()
    return int(text) if text.isdigit() else 0


def _clamp_dmi_period(value: object) -> int:
    try:
        period = int(value)
    except (TypeError, ValueError):
        period = 14
    return max(MIN_DMI_PERIOD, min(MAX_DMI_PERIOD, period))


def _clamp_window_opacity_percent(value: object) -> int:
    try:
        percent = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        percent = WINDOW_OPACITY_MAX_PERCENT
    return max(
        WINDOW_OPACITY_MIN_PERCENT,
        min(WINDOW_OPACITY_MAX_PERCENT, percent),
    )


def _percentage_input_allowed(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if len(text) > 7 or text.count(".") > 1:
        return False
    whole, separator, fraction = text.partition(".")
    if whole and not whole.isdigit():
        return False
    if separator and fraction and not fraction.isdigit():
        return False
    return bool(whole or separator)


def _account_password_input_allowed(value: str) -> bool:
    text = str(value or "")
    return not text or (len(text) <= 8 and text.isdigit())


def _account_password_session_ready(
    connection_method: str,
    account: str,
    password_account: str,
    password: str,
) -> bool:
    if connection_method == "REST API":
        return True
    return bool(
        is_valid_account_password(password)
        and clean_account_number(account)
        and clean_account_number(account) == clean_account_number(password_account)
    )


def _parse_money_input(value: str) -> float:
    text = str(value or "").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return 0.0


def _baseline_validation_message(
    capital_limit: float,
    reference_price: float,
    available_funds: float,
) -> str:
    if available_funds <= 0:
        return "계좌의 주문가능금액을 먼저 불러와 주세요."
    if capital_limit <= 0:
        return "종목별 운용 한도금액을 0원보다 크게 입력해 주세요."
    if reference_price <= 0:
        return "키움 현재가를 먼저 불러와 주세요."
    if capital_limit >= available_funds:
        return "운용 한도금액은 계좌 주문가능금액보다 작아야 합니다."
    if capital_limit < reference_price:
        return "운용 한도금액은 현재 주식 1주 가격보다 작을 수 없습니다."
    return ""


def _regular_market_is_open(status: MarketSessionStatus | None) -> bool:
    return bool(status and status.is_open)


def _market_session_text(
    status: MarketSessionStatus | None,
    regular_market_open: bool | None = None,
    real_time_registered: bool = False,
) -> str:
    if status is None:
        if real_time_registered:
            return "실시간 등록 완료·다음 체결 대기"
        return "키움 장 시작 신호 대기"
    if status.is_open:
        if regular_market_open is False:
            return "장중 실시간 체결 갱신 대기"
        if "0B" in status.source.upper():
            return "정규장 장중(실시간 체결 확인)"
        return "정규장 장중"
    if status.operation_code == "0":
        return "정규장 시작 전"
    return f"정규장 비운영({status.operation_code or '미확인'})"


def _holding_monitor_display(
    running: bool,
    symbol: str,
    symbol_name: str,
    balance_summary: BalanceSummary | None,
) -> tuple[bool, str]:
    target = normalize_symbol(symbol)
    holding = None
    if balance_summary is not None:
        holding = next(
            (
                row
                for row in balance_summary.holdings
                if normalize_symbol(row.symbol) == target
            ),
            None,
        )

    name = str(symbol_name or "").strip()
    if holding is not None and holding.name:
        name = holding.name
    if not name:
        name = target if target and target != "000000" else "선택 종목"

    quantity = max(0, int(holding.quantity)) if holding is not None else 0
    if not running:
        if quantity > 0:
            return False, f"{name} 주식 {quantity}주 보유 · 실시간 감시 중지"
        return False, "실시간 감시를 시작하면 선택 종목의 보유수량을 확인합니다."
    if balance_summary is None:
        return False, f"{name} 주식 · 계좌 잔고 확인 중"
    if quantity > 0:
        return True, f"{name} 주식 · 소프트웨어에서 {quantity}주 감시중"
    return False, f"{name} 주식 · 보유수량 0주로 감시 대상 없음"


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
        ttk.Label(body, text="키움증권 ID 로그인", font=(UI_DISPLAY_FONT, 14, "bold")).grid(
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
            foreground=UI_MUTED,
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
        ttk.Label(body, textvariable=self.heading_var, font=(UI_DISPLAY_FONT, 14, "bold")).grid(
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
            foreground=UI_MUTED,
            wraplength=520,
        ).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(0, 10)
        )
        ttk.Label(
            body,
            textvariable=self.help_var,
            wraplength=520,
            foreground=UI_MUTED,
        ).grid(row=6, column=0, columnspan=3, sticky="ew", pady=(2, 14))

        buttons = ttk.Frame(body)
        buttons.grid(row=7, column=0, columnspan=3, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        portal_buttons = ttk.Frame(buttons)
        portal_buttons.grid(row=0, column=0, sticky="w")
        ttk.Button(
            portal_buttons,
            text="계좌·IP 등록",
            command=lambda: webbrowser.open(KIWOOM_REST_PORTAL),
        ).pack(side="left")
        ttk.Button(
            portal_buttons,
            text="API 가이드",
            command=lambda: webbrowser.open(KIWOOM_REST_GUIDE),
        ).pack(side="left", padx=(6, 0))
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
        self.connect_button_var.set("실전투자 연결")
        self.help_var.set(
            "실전투자 AppKey와 SecretKey를 사용하세요. 키움 계좌·IP 등록 페이지에 "
            "사용 계좌와 현재 공인 IP가 등록되어 있어야 합니다. 연결 후 프로그램이 "
            "토큰과 등록 계좌를 다시 확인하고 계좌·잔고·시세를 조회합니다. "
            "모의투자 연결 없이 실전투자로 바로 설정할 수 있습니다. 실제 주문은 화면의 "
            "수동 주문 버튼을 누르고 최종 확인한 경우에만 전송됩니다."
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


class PinSetupDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, nickname: str = "") -> None:
        super().__init__(parent)
        self.title("카와이 증권 보안 설정")
        self.resizable(False, False)
        self.result: tuple[str, str, str] | None = None
        self.nickname_var = tk.StringVar(value=nickname)
        self.pin_var = tk.StringVar(value="")
        self.pin_confirm_var = tk.StringVar(value="")
        self.recovery_var = tk.StringVar(value="")
        self.recovery_confirm_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="")

        self.transient(parent)
        self.grab_set()
        body = ttk.Frame(self, padding=22)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        ttk.Label(
            body,
            text="카와이 증권 시작 보안 설정",
            font=(UI_DISPLAY_FONT, 15, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Label(
            body,
            text="PIN과 복구 비밀번호는 암호화된 해시로만 저장됩니다.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 16))

        fields = (
            ("사용자 이름 또는 닉네임", self.nickname_var, "", 28),
            ("6자리 PIN", self.pin_var, "*", 16),
            ("PIN 다시 입력", self.pin_confirm_var, "*", 16),
            ("원 비밀번호(복구용)", self.recovery_var, "*", 28),
            ("원 비밀번호 다시 입력", self.recovery_confirm_var, "*", 28),
        )
        first_entry: ttk.Entry | None = None
        for offset, (label, variable, mask, width) in enumerate(fields, start=2):
            ttk.Label(body, text=label).grid(row=offset, column=0, sticky="w", pady=5)
            entry = ttk.Entry(body, textvariable=variable, show=mask, width=width)
            entry.grid(row=offset, column=1, sticky="ew", padx=(14, 0), pady=5)
            if first_entry is None:
                first_entry = entry

        ttk.Label(
            body,
            text="복구 비밀번호는 8자 이상이며 영문·숫자·특수문자를 모두 포함해야 합니다.",
            wraplength=420,
            style="Muted.TLabel",
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(7, 4))
        ttk.Label(
            body,
            textvariable=self.status_var,
            foreground=UI_RED,
            wraplength=420,
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(0, 12))
        buttons = ttk.Frame(body)
        buttons.grid(row=9, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="취소", command=self._cancel).pack(side="left")
        ttk.Button(
            buttons,
            text="보안 설정 완료",
            command=self._submit,
            style="Accent.TButton",
        ).pack(side="left", padx=(8, 0))

        if first_entry is not None:
            first_entry.focus_set()
        self.bind("<Return>", lambda _event: self._submit())
        self.bind("<Escape>", lambda _event: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.after_idle(lambda: _show_centered_dialog(self))

    def _submit(self) -> None:
        nickname = self.nickname_var.get().strip()
        pin = self.pin_var.get()
        recovery = self.recovery_var.get()
        if not nickname:
            self.status_var.set("오류 안내에 사용할 이름 또는 닉네임을 입력해 주세요.")
            return
        if not is_valid_pin(pin):
            self.status_var.set("PIN은 숫자 6자리로 입력해 주세요.")
            return
        if pin != self.pin_confirm_var.get():
            self.status_var.set("두 PIN이 서로 다릅니다.")
            return
        if not is_valid_recovery_password(recovery):
            self.status_var.set("원 비밀번호에 영문·숫자·특수문자를 모두 포함해 주세요.")
            return
        if recovery != self.recovery_confirm_var.get():
            self.status_var.set("두 원 비밀번호가 서로 다릅니다.")
            return
        self.result = (nickname, pin, recovery)
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


class PinUnlockDialog(tk.Toplevel):
    MAX_ATTEMPTS = 10

    def __init__(
        self,
        parent: tk.Tk,
        nickname: str,
        pin_hash: str,
        recovery_hash: str,
    ) -> None:
        super().__init__(parent)
        self.title("카와이 증권 PIN 입력")
        self.resizable(False, False)
        self.nickname = nickname
        self.pin_hash = pin_hash
        self.recovery_hash = recovery_hash
        self.result = False
        self.new_pin: str | None = None
        self.failures = 0
        self.pin_var = tk.StringVar(value="")
        self.recovery_var = tk.StringVar(value="")
        self.new_pin_var = tk.StringVar(value="")
        self.new_pin_confirm_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="PIN을 입력해 주세요. 남은 횟수 10회")

        self.transient(parent)
        self.grab_set()
        body = ttk.Frame(self, padding=22)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        ttk.Label(
            body,
            text="카와이 증권",
            font=(UI_DISPLAY_FONT, 17, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            body,
            text=f"{nickname}님, 6자리 PIN을 입력해 주세요.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 16))

        pin_row = ttk.Frame(body)
        pin_row.grid(row=2, column=0, sticky="ew")
        pin_row.columnconfigure(0, weight=1)
        self.pin_entry = ttk.Entry(
            pin_row,
            textvariable=self.pin_var,
            show="*",
            justify="center",
            font=(UI_DISPLAY_FONT, 16, "bold"),
            width=16,
        )
        self.pin_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(
            pin_row,
            text="접속",
            command=self._verify_pin,
            style="Accent.TButton",
        ).grid(row=0, column=1, padx=(8, 0))
        ttk.Label(
            body,
            textvariable=self.status_var,
            foreground=UI_RED,
            wraplength=420,
        ).grid(row=3, column=0, sticky="w", pady=(8, 10))
        self.recovery_button = ttk.Button(
            body,
            text="원 비밀번호로 접속",
            command=lambda: self._show_recovery(False),
        )
        self.recovery_button.grid(row=4, column=0, sticky="w")

        self.recovery_frame = ttk.LabelFrame(
            body,
            text="비밀번호 찾기 및 PIN 변경",
            padding=12,
        )
        self.recovery_frame.columnconfigure(1, weight=1)
        ttk.Label(self.recovery_frame, text="원 비밀번호").grid(
            row=0, column=0, sticky="w", pady=4
        )
        self.recovery_entry = ttk.Entry(
            self.recovery_frame,
            textvariable=self.recovery_var,
            show="*",
            width=28,
        )
        self.recovery_entry.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=4)
        ttk.Label(self.recovery_frame, text="새 6자리 PIN").grid(
            row=1, column=0, sticky="w", pady=4
        )
        ttk.Entry(
            self.recovery_frame,
            textvariable=self.new_pin_var,
            show="*",
            width=16,
        ).grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=4)
        ttk.Label(self.recovery_frame, text="새 PIN 다시 입력").grid(
            row=2, column=0, sticky="w", pady=4
        )
        ttk.Entry(
            self.recovery_frame,
            textvariable=self.new_pin_confirm_var,
            show="*",
            width=16,
        ).grid(row=2, column=1, sticky="ew", padx=(10, 0), pady=4)
        ttk.Button(
            self.recovery_frame,
            text="확인 후 접속",
            command=self._verify_recovery,
            style="Blue.TButton",
        ).grid(row=3, column=0, columnspan=2, sticky="e", pady=(8, 0))

        footer = ttk.Frame(body)
        footer.grid(row=6, column=0, sticky="e", pady=(14, 0))
        ttk.Button(footer, text="종료", command=self._cancel).pack(side="right")
        self.pin_entry.focus_set()
        self.bind("<Return>", lambda _event: self._submit_current_mode())
        self.bind("<Escape>", lambda _event: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.after_idle(lambda: _show_centered_dialog(self))

    def _submit_current_mode(self) -> None:
        if self.recovery_frame.winfo_manager():
            self._verify_recovery()
        else:
            self._verify_pin()

    def _verify_pin(self) -> None:
        if verify_secret(self.pin_var.get(), self.pin_hash):
            self.result = True
            self.destroy()
            return
        self.pin_var.set("")
        self.failures += 1
        remaining = max(0, self.MAX_ATTEMPTS - self.failures)
        if remaining == 0:
            self.status_var.set("PIN 입력이 10회 실패했습니다. 원 비밀번호로 본인을 확인하고 PIN을 변경해 주세요.")
            self._show_recovery(True)
            return
        self.status_var.set(f"PIN이 올바르지 않습니다. 남은 횟수 {remaining}회")
        self.pin_entry.focus_set()

    def _show_recovery(self, require_change: bool) -> None:
        self._recovery_change_required = require_change
        self.recovery_frame.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        if require_change:
            self.pin_entry.configure(state="disabled")
            self.recovery_button.configure(state="disabled")
        self.recovery_entry.focus_set()

    def _verify_recovery(self) -> None:
        if not verify_secret(self.recovery_var.get(), self.recovery_hash):
            self.recovery_var.set("")
            self.status_var.set("원 비밀번호가 올바르지 않습니다.")
            self.recovery_entry.focus_set()
            return
        new_pin = self.new_pin_var.get()
        confirm = self.new_pin_confirm_var.get()
        if new_pin or confirm or getattr(self, "_recovery_change_required", False):
            if not is_valid_pin(new_pin):
                self.status_var.set("새 PIN은 숫자 6자리로 입력해 주세요.")
                return
            if new_pin != confirm:
                self.status_var.set("새 PIN 두 개가 서로 다릅니다.")
                return
            self.new_pin = new_pin
        self.result = True
        self.destroy()

    def _cancel(self) -> None:
        self.result = False
        self.destroy()


class WatchlistMemoDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        symbol: str,
        name: str,
        memo: str,
    ) -> None:
        super().__init__(parent)
        self.result: str | None = None
        self.title("관심종목 메모")
        self.resizable(True, True)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        body = ttk.Frame(self, padding=16)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        ttk.Label(
            body,
            text=f"{symbol} {name or '종목명 미조회'}",
            font=(UI_DISPLAY_FONT, 12, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))
        self.memo_text = tk.Text(
            body,
            width=52,
            height=9,
            wrap="word",
            relief="solid",
            borderwidth=1,
            font=(UI_FONT, 10),
        )
        self.memo_text.grid(row=1, column=0, sticky="nsew")
        self.memo_text.insert("1.0", memo)

        buttons = ttk.Frame(body)
        buttons.grid(row=2, column=0, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="메모 삭제", command=self._delete).pack(side="left")
        ttk.Button(buttons, text="취소", command=self._cancel).pack(
            side="left",
            padx=(8, 0),
        )
        ttk.Button(buttons, text="저장", style="Primary.TButton", command=self._save).pack(
            side="left",
            padx=(8, 0),
        )

        self.bind("<Escape>", lambda _event: self._cancel())
        self.bind("<Control-Return>", lambda _event: self._save())
        _show_centered_dialog(self)
        self.memo_text.focus_set()
        self.grab_set()
        self.wait_window(self)

    def _save(self) -> None:
        self.result = self.memo_text.get("1.0", "end-1c").strip()[:2000]
        self.destroy()

    def _delete(self) -> None:
        self.result = ""
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


class TraderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.withdraw()
        self.title("카와이 증권")
        initial_width = min(
            1900,
            max(EXPANDED_WINDOW_MIN_WIDTH, self.winfo_screenwidth() - 40),
        )
        initial_height = min(1000, max(760, self.winfo_screenheight() - 80))
        self.geometry(f"{initial_width}x{initial_height}")
        self.minsize(EXPANDED_WINDOW_MIN_WIDTH, 760)
        self._compact_mode = False
        self._controls_collapsed = False
        self._compact_previous_topmost = False
        self._normal_geometry = f"{initial_width}x{initial_height}"
        self._normal_window_state = "normal"
        self._configure_visual_theme()
        self._apply_window_icon()

        legacy_db_path = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_ko.sqlite3"
        application_data_dir = Path.home() / "Documents" / "KawaiiSecurities"
        try:
            application_data_dir.mkdir(parents=True, exist_ok=True)
            db_path = application_data_dir / "kawaii_securities.sqlite3"
            if not db_path.exists() and legacy_db_path.exists():
                shutil.copy2(legacy_db_path, db_path)
        except OSError:
            db_path = legacy_db_path
        trade_history_path = Path.home() / "Documents" / "KiwoomAutoTrader" / "매수매도_이력.csv"
        self.service = AutoTradingService(
            storage=Storage(db_path, trade_history_path=trade_history_path)
        )
        self.voice_notifier = OrderVoiceNotifier()
        self.sound_notifier = TradeSoundNotifier(
            self.service.storage.get_app_setting("audio.buy_sound", ""),
            self.service.storage.get_app_setting("audio.sell_sound", ""),
        )
        self._known_execution_keys: set[tuple[object, ...]] | None = None
        self._load_persisted_preferences()
        self._refresh_after_id: str | None = None
        self._chart_refresh_after_id: str | None = None
        self._account_after_id: str | None = None
        self._trade_history_after_id: str | None = None
        self._volume_rank_after_id: str | None = None
        self._trade_value_rank_after_id: str | None = None
        self._account_poll_count = 0
        self._selected_account_full = ""
        self._account_access_verified = False
        self._session_password_account = ""
        self._suppress_password_trace = False
        self._account_info_window: tk.Toplevel | None = None
        self._candle_chart_window: tk.Toplevel | None = None
        self._candle_chart_canvas: tk.Canvas | None = None
        self._watchlist_window: tk.Toplevel | None = None
        self._watchlist_table: ttk.Treeview | None = None
        self._watchlist_link_after_id: str | None = None
        self._watchlist_suppress_auto_connect = False
        self._watchlist_visible_fields = list(WATCHLIST_DEFAULT_VISIBLE_FIELDS)
        self._watchlist_column_order = list(WATCHLIST_FIELD_SPECS)
        self._watchlist_column_widths: dict[str, int] = {}
        self._watchlist_drag_field = ""
        self._watchlist_drag_start_x = 0
        self._watchlist_hover_item = ""
        self._watchlist_tooltip_after_id: str | None = None
        self._watchlist_tooltip_window: tk.Toplevel | None = None
        self.watchlist_symbol_var: tk.StringVar | None = None
        self.watchlist_auto_link_var: tk.BooleanVar | None = None
        self.watchlist_status_var: tk.StringVar | None = None
        self.watchlist_detail_vars: dict[str, tk.StringVar] = {}
        self._chart_render_state: dict[tk.Canvas, dict] = {}
        self._chart_visible_count = 100
        self._trading_baseline: TradingBaseline | None = None
        self._real_order_session_armed = False
        self._last_auto_market_state: tuple[str, str, bool] | None = None
        self._next_holding_balance_refresh_at = 0.0
        self._holding_balance_fresh = False
        self._last_holding_monitor_state: tuple[bool, str] | None = None
        self._symbol_tag_after_id: str | None = None
        self._daily_traded_symbols: list[tuple[str, str]] = []
        self._last_risk_lock_date = ""
        self._daily_risk_handling = False
        self._selected_unfilled_side = "BUY"
        self._market_control_after_id: str | None = None
        self._volume_drag_symbol = ""
        self._volume_drag_start_y = 0
        self._trade_value_drag_symbol = ""
        self._trade_value_drag_start_y = 0
        self._active_rank_source = "volume"
        self._rank_hover_table: ttk.Treeview | None = None
        self._rank_hover_item = ""
        self._rank_tooltip_after_id: str | None = None
        self._rank_tooltip_window: tk.Toplevel | None = None
        self._rank_marker_images: dict[str, tk.PhotoImage] = {}
        self._quick_order_presets = self._load_quick_order_presets()
        self._account_history = normalize_account_history(
            decode_json_setting(
                self.service.storage.get_app_setting("account.history", "[]"),
                [],
            )
        )
        self.window_opacity_var = tk.DoubleVar(value=self._saved_window_opacity)
        self.window_opacity_display_var = tk.StringVar(
            value=f"{self._saved_window_opacity}%"
        )
        self._build_ui()
        self._apply_order_price_mode_labels()
        self._account_password_trace_id = self.account_password_var.trace_add(
            "write",
            self._on_account_password_changed,
        )
        self.protocol("WM_DELETE_WINDOW", self._close_app)
        self.deiconify()
        self.lift()
        if not self._authenticate_startup():
            self.voice_notifier.close()
            self.destroy()
            return
        self._apply_saved_window_preferences()
        self._refresh()

    def _load_quick_order_presets(self) -> list[int]:
        raw = decode_json_setting(
            self.service.storage.get_app_setting("orders.quick_quantities", "[]"),
            [],
        )
        values = raw if isinstance(raw, list) else []
        presets: list[int] = []
        for index in range(10):
            try:
                quantity = int(values[index])
            except (IndexError, TypeError, ValueError):
                quantity = index + 1
            presets.append(max(1, quantity))
        return presets

    @staticmethod
    def _setting_enabled(value: object, default: bool = False) -> bool:
        text = str(value or "").strip().casefold()
        if not text:
            return default
        return text in {"1", "true", "yes", "on", "enabled"}

    def _load_persisted_preferences(self) -> None:
        storage = self.service.storage
        self.user_nickname = storage.get_app_setting("profile.nickname", "고객").strip() or "고객"
        self.startup_auto_run_enabled = self._setting_enabled(
            storage.get_app_setting("window.startup_auto_run", "False")
        )
        self.always_on_top_enabled = self._setting_enabled(
            storage.get_app_setting("window.always_on_top", "False")
        )
        self.minimize_to_tray_enabled = self._setting_enabled(
            storage.get_app_setting("window.minimize_to_tray", "False")
        )
        self.account_mask_enabled = self._setting_enabled(
            storage.get_app_setting("privacy.mask_account", "True"),
            default=True,
        )
        self._saved_window_opacity = _clamp_window_opacity_percent(
            storage.get_app_setting(
                "window.opacity",
                str(WINDOW_OPACITY_MAX_PERCENT),
            )
        )
        self._side_panel_collapsed = False
        self._position_locked = False
        self._locked_window_position: tuple[int, int] | None = None
        self._restoring_locked_position = False
        self._window_hovered = False
        self._clock_after_id: str | None = None
        self._tray_poll_after_id: str | None = None
        self._tray_icon = None
        self._force_exit = False

    def _authenticate_startup(self) -> bool:
        if os.environ.get("KAWAII_TRADER_TEST_MODE") == "1":
            return True
        storage = self.service.storage
        pin_hash = storage.get_app_setting("security.pin_hash")
        recovery_hash = storage.get_app_setting("security.recovery_hash")
        if not pin_hash or not recovery_hash:
            dialog = PinSetupDialog(self, self.user_nickname)
            self.wait_window(dialog)
            if dialog.result is None:
                return False
            nickname, pin, recovery = dialog.result
            storage.set_app_setting("profile.nickname", nickname)
            storage.set_app_setting("security.pin_hash", hash_secret(pin))
            storage.set_app_setting("security.recovery_hash", hash_secret(recovery))
            self.user_nickname = nickname
            storage.log("INFO", "보안", "프로그램 PIN과 복구 비밀번호를 등록했습니다.")
            return True

        dialog = PinUnlockDialog(
            self,
            self.user_nickname,
            pin_hash,
            recovery_hash,
        )
        self.wait_window(dialog)
        if not dialog.result:
            return False
        if dialog.new_pin:
            storage.set_app_setting("security.pin_hash", hash_secret(dialog.new_pin))
            storage.log("WARN", "보안", "복구 비밀번호 확인 후 프로그램 PIN을 변경했습니다.")
        return True

    def _apply_saved_window_preferences(self) -> None:
        try:
            self.wm_attributes("-topmost", bool(self.always_on_top_enabled))
        except tk.TclError:
            pass
        self._apply_window_opacity(self._saved_window_opacity)
        if hasattr(self, "topmost_button"):
            self.topmost_button.configure(
                text="핀 ON" if self.always_on_top_enabled else "핀 OFF",
                style="Blue.TButton" if self.always_on_top_enabled else "TButton",
            )

    def _apply_window_icon(self) -> None:
        icon_path = _application_resource("assets", "kiwoom_trade.ico")
        png_path = _application_resource("assets", "kiwoom_trade.png")
        try:
            if icon_path.exists():
                self.iconbitmap(default=str(icon_path))
            if png_path.exists():
                self._window_icon_image = tk.PhotoImage(master=self, file=str(png_path))
                self.iconphoto(True, self._window_icon_image)
        except (OSError, tk.TclError):
            self._window_icon_image = None

    @staticmethod
    def _inside_rounded_square(
        x: int,
        y: int,
        size: int,
        radius: int,
        inset: int = 0,
    ) -> bool:
        left = inset
        top = inset
        right = size - 1 - inset
        bottom = size - 1 - inset
        if x < left or x > right or y < top or y > bottom:
            return False
        radius = max(0, min(radius, (right - left + 1) // 2, (bottom - top + 1) // 2))
        if radius == 0:
            return True
        if left + radius <= x <= right - radius or top + radius <= y <= bottom - radius:
            return True

        center_x = left + radius - 0.5 if x < left + radius else right - radius + 0.5
        center_y = top + radius - 0.5 if y < top + radius else bottom - radius + 0.5
        return (x - center_x) ** 2 + (y - center_y) ** 2 <= radius**2

    def _rounded_theme_image(
        self,
        fill: str,
        outline: str,
        size: int = 24,
        radius: int = UI_BUTTON_RADIUS,
    ) -> tk.PhotoImage:
        image = tk.PhotoImage(master=self, width=size, height=size)
        for y in range(size):
            run_color: str | None = None
            run_start = 0
            for x in range(size + 1):
                color: str | None = None
                if x < size and self._inside_rounded_square(x, y, size, radius):
                    color = (
                        fill
                        if self._inside_rounded_square(x, y, size, radius - 1, inset=1)
                        else outline
                    )
                if color == run_color:
                    continue
                if run_color is not None:
                    image.put(run_color, to=(run_start, y, x, y + 1))
                run_color = color
                run_start = x
        return image

    def _install_rounded_control_styles(self, style: ttk.Style) -> None:
        self._rounded_theme_images: list[tk.PhotoImage] = []
        button_palettes = {
            "TButton": (
                "Rounded.Button.surface",
                {
                    "normal": (UI_SURFACE, UI_BORDER),
                    "active": (UI_SOFT, "#C7C7C7"),
                    "pressed": ("#EAEAEA", "#B8B8B8"),
                    "disabled": ("#F7F7F7", "#E5E5E5"),
                },
            ),
            "Accent.TButton": (
                "Rounded.Button.accent",
                {
                    "normal": (UI_PINK, UI_PINK),
                    "active": ("#CF2763", "#CF2763"),
                    "pressed": ("#B91F56", "#B91F56"),
                    "disabled": ("#F4CCD9", "#F4CCD9"),
                },
            ),
            "Blue.TButton": (
                "Rounded.Button.blue",
                {
                    "normal": (UI_BLUE, UI_BLUE),
                    "active": ("#3654D4", "#3654D4"),
                    "pressed": ("#3046B8", "#3046B8"),
                    "disabled": ("#C9D0F8", "#C9D0F8"),
                },
            ),
            "Danger.TButton": (
                "Rounded.Button.danger",
                {
                    "normal": (UI_SURFACE, "#F3A8B4"),
                    "active": ("#FFF2F5", UI_RED),
                    "pressed": ("#FCE8EC", UI_RED),
                    "disabled": ("#F7F7F7", "#E5E5E5"),
                },
            ),
        }
        for style_name, (element_name, palette) in button_palettes.items():
            images = {
                state: self._rounded_theme_image(fill, outline)
                for state, (fill, outline) in palette.items()
            }
            self._rounded_theme_images.extend(images.values())
            style.element_create(
                element_name,
                "image",
                images["normal"],
                ("disabled", images["disabled"]),
                ("pressed", images["pressed"]),
                ("active", images["active"]),
                border=UI_BUTTON_RADIUS,
                sticky="nsew",
            )
            style.layout(
                style_name,
                [
                    (
                        element_name,
                        {
                            "sticky": "nsew",
                            "children": [
                                (
                                    "Button.padding",
                                    {
                                        "sticky": "nsew",
                                        "children": [("Button.label", {"sticky": "nsew"})],
                                    },
                                )
                            ],
                        },
                    )
                ],
            )

        pill_images = {
            "normal": self._rounded_theme_image(UI_SURFACE, UI_BORDER),
            "active": self._rounded_theme_image(UI_SOFT, "#C7C7C7"),
            "selected": self._rounded_theme_image("#FCE8F0", UI_PINK),
            "disabled": self._rounded_theme_image("#F7F7F7", "#E5E5E5"),
        }
        self._rounded_theme_images.extend(pill_images.values())
        style.element_create(
            "Rounded.Radiobutton.pill",
            "image",
            pill_images["normal"],
            ("disabled", pill_images["disabled"]),
            ("selected", pill_images["selected"]),
            ("active", pill_images["active"]),
            border=UI_BUTTON_RADIUS,
            sticky="nsew",
        )
        style.layout(
            "Pill.TRadiobutton",
            [
                (
                    "Rounded.Radiobutton.pill",
                    {
                        "sticky": "nsew",
                        "children": [
                            (
                                "Radiobutton.padding",
                                {
                                    "sticky": "nsew",
                                    "children": [("Radiobutton.label", {"sticky": "nsew"})],
                                },
                            )
                        ],
                    },
                )
            ],
        )

    def _install_rounded_surface_styles(self, style: ttk.Style) -> None:
        entry_images = {
            "normal": self._rounded_theme_image(UI_SURFACE, UI_BORDER, size=28),
            "focus": self._rounded_theme_image(UI_SURFACE, UI_BLUE, size=28),
            "readonly": self._rounded_theme_image("#F8F8F8", UI_BORDER, size=28),
            "disabled": self._rounded_theme_image("#F3F3F3", "#E5E5E5", size=28),
        }
        self._rounded_theme_images.extend(entry_images.values())
        style.element_create(
            "Rounded.Entry.field",
            "image",
            entry_images["normal"],
            ("disabled", entry_images["disabled"]),
            ("focus", entry_images["focus"]),
            ("readonly", entry_images["readonly"]),
            border=UI_BUTTON_RADIUS,
            sticky="nsew",
        )
        style.layout(
            "TEntry",
            [
                (
                    "Rounded.Entry.field",
                    {
                        "sticky": "nsew",
                        "children": [
                            (
                                "Entry.padding",
                                {
                                    "sticky": "nsew",
                                    "children": [("Entry.textarea", {"sticky": "nsew"})],
                                },
                            )
                        ],
                    },
                )
            ],
        )

        combobox_images = {
            "normal": self._rounded_theme_image(UI_SURFACE, UI_BORDER, size=28),
            "focus": self._rounded_theme_image(UI_SURFACE, UI_BLUE, size=28),
            "readonly": self._rounded_theme_image("#F8F8F8", UI_BORDER, size=28),
            "disabled": self._rounded_theme_image("#F3F3F3", "#E5E5E5", size=28),
        }
        self._rounded_theme_images.extend(combobox_images.values())
        style.element_create(
            "Rounded.Combobox.field",
            "image",
            combobox_images["normal"],
            ("disabled", combobox_images["disabled"]),
            ("focus", combobox_images["focus"]),
            ("readonly", combobox_images["readonly"]),
            border=UI_BUTTON_RADIUS,
            sticky="nsew",
        )
        style.layout(
            "TCombobox",
            [
                (
                    "Rounded.Combobox.field",
                    {
                        "sticky": "nsew",
                        "children": [
                            ("Combobox.downarrow", {"side": "right", "sticky": "ns"}),
                            (
                                "Combobox.padding",
                                {
                                    "sticky": "nsew",
                                    "children": [("Combobox.textarea", {"sticky": "nsew"})],
                                },
                            ),
                        ],
                    },
                )
            ],
        )

        panel_image = self._rounded_theme_image(UI_SURFACE, UI_BORDER, size=32)
        self._rounded_theme_images.append(panel_image)
        style.element_create(
            "Rounded.Labelframe.border",
            "image",
            panel_image,
            border=UI_BUTTON_RADIUS,
            sticky="nsew",
        )
        style.layout(
            "TLabelframe",
            [("Rounded.Labelframe.border", {"sticky": "nsew"})],
        )

        label_palettes = {
            "Value.TLabel": (UI_SURFACE, UI_BORDER, UI_TEXT),
            "Neutral.Badge.TLabel": (UI_MUTED, UI_MUTED, UI_SURFACE),
            "Success.Badge.TLabel": (UI_GREEN, UI_GREEN, UI_SURFACE),
            "Danger.Badge.TLabel": (UI_RED, UI_RED, UI_SURFACE),
            "Blue.Badge.TLabel": (UI_BLUE, UI_BLUE, UI_SURFACE),
            "Pink.Badge.TLabel": (UI_PINK, UI_PINK, UI_SURFACE),
            "PinkSoft.Badge.TLabel": ("#FCE8F0", "#FCE8F0", UI_PINK),
            "BlueSoft.Badge.TLabel": ("#EEF1FF", "#EEF1FF", UI_BLUE),
        }
        for index, (style_name, (fill, outline, foreground)) in enumerate(
            label_palettes.items()
        ):
            image = self._rounded_theme_image(fill, outline, size=28)
            self._rounded_theme_images.append(image)
            element_name = f"Rounded.Label.surface{index}"
            style.element_create(
                element_name,
                "image",
                image,
                border=UI_BUTTON_RADIUS,
                sticky="nsew",
            )
            style.layout(
                style_name,
                [
                    (
                        element_name,
                        {
                            "sticky": "nsew",
                            "children": [
                                (
                                    "Label.padding",
                                    {
                                        "sticky": "nsew",
                                        "children": [("Label.label", {"sticky": "nsew"})],
                                    },
                                )
                            ],
                        },
                    )
                ],
            )
            style.configure(style_name, foreground=foreground)

        tab_images = {
            "normal": self._rounded_theme_image(UI_SURFACE, UI_BORDER, size=28),
            "active": self._rounded_theme_image(UI_SOFT, "#C7C7C7", size=28),
            "selected": self._rounded_theme_image("#FCE8F0", UI_PINK, size=28),
        }
        self._rounded_theme_images.extend(tab_images.values())
        style.element_create(
            "Rounded.Notebook.tab",
            "image",
            tab_images["normal"],
            ("selected", tab_images["selected"]),
            ("active", tab_images["active"]),
            border=UI_BUTTON_RADIUS,
            sticky="nsew",
        )
        style.layout(
            "TNotebook.Tab",
            [
                (
                    "Rounded.Notebook.tab",
                    {
                        "sticky": "nsew",
                        "children": [
                            (
                                "Notebook.padding",
                                {
                                    "side": "top",
                                    "sticky": "nsew",
                                    "children": [("Notebook.label", {"side": "top"})],
                                },
                            )
                        ],
                    },
                )
            ],
        )

    def _configure_visual_theme(self) -> None:
        global UI_DISPLAY_FONT, UI_FONT, UI_WORDMARK_FONT

        self.configure(background=UI_BACKGROUND)
        available_fonts = {
            family.casefold(): family
            for family in tkfont.families(self)
        }

        def available_font(*candidates: str) -> str:
            for candidate in candidates:
                resolved = available_fonts.get(candidate.casefold())
                if resolved:
                    return resolved
            return candidates[-1]

        try:
            system_default_font = str(
                tkfont.nametofont("TkDefaultFont").actual("family")
            )
        except tk.TclError:
            system_default_font = "Segoe UI"
        UI_FONT = available_font(system_default_font, "Malgun Gothic", "Segoe UI")
        UI_DISPLAY_FONT = UI_FONT
        UI_WORDMARK_FONT = UI_FONT
        named_fonts = {
            "TkDefaultFont": (UI_FONT, 9, "normal"),
            "TkTextFont": (UI_FONT, 9, "normal"),
            "TkMenuFont": (UI_FONT, 9, "normal"),
            "TkHeadingFont": (UI_DISPLAY_FONT, 10, "bold"),
            "TkCaptionFont": (UI_DISPLAY_FONT, 10, "bold"),
            "TkSmallCaptionFont": (UI_FONT, 9, "normal"),
            "TkIconFont": (UI_FONT, 9, "normal"),
            "TkTooltipFont": (UI_FONT, 9, "normal"),
        }
        for name, (family, size, weight) in named_fonts.items():
            try:
                tkfont.nametofont(name).configure(
                    family=family,
                    size=size,
                    weight=weight,
                )
            except tk.TclError:
                continue
        self.option_add("*Font", (UI_FONT, 9))

        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=(UI_FONT, 9), background=UI_SURFACE, foreground=UI_TEXT)
        style.configure("TFrame", background=UI_SURFACE)
        style.configure("Header.TFrame", background=UI_SURFACE)
        style.configure("TLabel", background=UI_SURFACE, foreground=UI_TEXT)
        style.configure(
            "Muted.TLabel",
            background=UI_SURFACE,
            foreground=UI_MUTED,
            font=(UI_FONT, 9),
        )
        style.configure(
            "Brand.TLabel",
            background=UI_SURFACE,
            foreground=UI_TEXT,
            font=(UI_WORDMARK_FONT, 18, "bold"),
        )
        style.configure(
            "TLabelframe",
            background=UI_SURFACE,
            bordercolor=UI_BORDER,
            lightcolor=UI_BORDER,
            darkcolor=UI_BORDER,
            relief="solid",
            borderwidth=1,
        )
        style.configure(
            "TLabelframe.Label",
            background=UI_SURFACE,
            foreground=UI_TEXT,
            font=(UI_DISPLAY_FONT, 10, "bold"),
        )
        style.configure(
            "TButton",
            background=UI_SURFACE,
            foreground=UI_TEXT,
            bordercolor=UI_BORDER,
            lightcolor=UI_BORDER,
            darkcolor=UI_BORDER,
            focuscolor=UI_BORDER,
            focusthickness=1,
            relief="solid",
            borderwidth=1,
            padding=(10, 3),
            font=(UI_DISPLAY_FONT, 9),
            anchor="center",
            justify="center",
        )
        style.map(
            "TButton",
            background=[("pressed", "#EAEAEA"), ("active", UI_SOFT)],
            foreground=[("disabled", "#B0B0B0")],
            bordercolor=[("focus", UI_BLUE), ("active", "#C7C7C7")],
        )
        style.configure(
            "Accent.TButton",
            background=UI_PINK,
            foreground=UI_SURFACE,
            bordercolor=UI_PINK,
            lightcolor=UI_PINK,
            darkcolor=UI_PINK,
            focuscolor=UI_PINK,
            anchor="center",
            justify="center",
        )
        style.map(
            "Accent.TButton",
            background=[("pressed", "#B91F56"), ("active", "#CF2763")],
            foreground=[("disabled", UI_SURFACE)],
        )
        style.configure(
            "Blue.TButton",
            background=UI_BLUE,
            foreground=UI_SURFACE,
            bordercolor=UI_BLUE,
            lightcolor=UI_BLUE,
            darkcolor=UI_BLUE,
            focuscolor=UI_BLUE,
            anchor="center",
            justify="center",
        )
        style.map(
            "Blue.TButton",
            background=[("pressed", "#3046B8"), ("active", "#3654D4")],
            foreground=[("disabled", UI_SURFACE)],
        )
        style.configure(
            "Danger.TButton",
            background=UI_SURFACE,
            foreground=UI_RED,
            bordercolor="#F3A8B4",
            lightcolor="#F3A8B4",
            darkcolor="#F3A8B4",
            focuscolor="#F3A8B4",
            anchor="center",
            justify="center",
        )
        style.map(
            "Danger.TButton",
            background=[("pressed", "#FCE8EC"), ("active", "#FFF2F5")],
            foreground=[("disabled", "#B0B0B0")],
        )
        style.configure(
            "Pill.TRadiobutton",
            background=UI_SURFACE,
            foreground=UI_TEXT,
            padding=(10, 3),
            font=(UI_DISPLAY_FONT, 9),
            anchor="center",
            justify="center",
        )
        style.map(
            "Pill.TRadiobutton",
            foreground=[("disabled", "#B0B0B0"), ("selected", UI_PINK)],
        )
        self._install_rounded_control_styles(style)
        style.configure(
            "TEntry",
            fieldbackground=UI_SURFACE,
            foreground=UI_TEXT,
            bordercolor=UI_BORDER,
            lightcolor=UI_BORDER,
            darkcolor=UI_BORDER,
            insertcolor=UI_TEXT,
            padding=(7, 2),
        )
        style.map(
            "TEntry",
            bordercolor=[("focus", UI_BLUE)],
            lightcolor=[("focus", UI_BLUE)],
            darkcolor=[("focus", UI_BLUE)],
        )
        style.configure("TCheckbutton", background=UI_SURFACE, foreground=UI_TEXT)
        style.map("TCheckbutton", foreground=[("disabled", "#A8A8A8")])
        style.configure("TNotebook", background=UI_SURFACE, borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background=UI_SURFACE,
            foreground=UI_MUTED,
            borderwidth=0,
            padding=(14, 6),
            font=(UI_DISPLAY_FONT, 9),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", UI_SURFACE), ("active", UI_SOFT)],
            foreground=[("selected", UI_PINK), ("active", UI_TEXT)],
        )
        style.configure(
            "Treeview",
            background=UI_SURFACE,
            fieldbackground=UI_SURFACE,
            foreground=UI_TEXT,
            bordercolor=UI_BORDER,
            lightcolor=UI_BORDER,
            darkcolor=UI_BORDER,
            borderwidth=1,
            relief="solid",
            rowheight=27,
            font=(UI_FONT, 9),
        )
        style.map(
            "Treeview",
            background=[("selected", UI_PINK)],
            foreground=[("selected", UI_SURFACE)],
        )
        style.configure(
            "Treeview.Heading",
            background="#F7F7F7",
            foreground=UI_TEXT,
            bordercolor=UI_BORDER,
            lightcolor=UI_BORDER,
            darkcolor=UI_BORDER,
            relief="flat",
            padding=(6, 5),
            font=(UI_DISPLAY_FONT, 9, "bold"),
        )
        style.map("Treeview.Heading", background=[("active", UI_SOFT)])
        self._install_rounded_surface_styles(style)
        style.configure(
            "Opacity.Horizontal.TScale",
            background=UI_SURFACE,
            troughcolor="#E7E7E7",
            bordercolor=UI_BORDER,
            lightcolor=UI_PINK,
            darkcolor=UI_PINK,
            sliderlength=18,
            borderwidth=0,
        )

    def _draw_brand_rail(self, _event: tk.Event | None = None) -> None:
        canvas = self._brand_rail_canvas
        canvas.delete("all")
        width = max(1, canvas.winfo_width())
        if getattr(self, "service", None) is not None and self.service.running:
            colors = ("#0E7433", UI_GREEN, "#28A95B", "#46C777")
        elif self._window_hovered:
            colors = ("#6D2FA0", "#F0447D", "#FF8B45", "#FFD166")
        else:
            colors = (UI_PURPLE, UI_PINK, UI_ORANGE, UI_YELLOW)
        segment_width = width / len(colors)
        for index, color in enumerate(colors):
            canvas.create_rectangle(
                int(index * segment_width),
                0,
                int((index + 1) * segment_width) + 1,
                4,
                fill=color,
                outline=color,
            )

    def _apply_window_opacity(self, value: object | None = None) -> None:
        source = self.window_opacity_var.get() if value is None else value
        percent = _clamp_window_opacity_percent(source)
        self.window_opacity_display_var.set(f"{percent}%")
        try:
            self.wm_attributes("-alpha", percent / 100.0)
        except tk.TclError:
            return

    def _build_window_opacity_control(self) -> None:
        toolbar = ttk.Frame(
            self,
            padding=(12, 12, 14, 8),
            style="Header.TFrame",
        )
        self.opacity_toolbar = toolbar
        toolbar.grid(row=0, column=1, sticky="new")
        toolbar.columnconfigure(1, weight=1)

        ttk.Label(
            toolbar,
            text="창 투명도",
            font=(UI_DISPLAY_FONT, 9, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self.window_opacity_scale = ttk.Scale(
            toolbar,
            from_=WINDOW_OPACITY_MIN_PERCENT,
            to=WINDOW_OPACITY_MAX_PERCENT,
            orient="horizontal",
            variable=self.window_opacity_var,
            command=self._apply_window_opacity,
            length=150,
            style="Opacity.Horizontal.TScale",
            takefocus=True,
        )
        self.window_opacity_scale.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(10, 8),
        )
        ttk.Label(
            toolbar,
            textvariable=self.window_opacity_display_var,
            width=5,
            anchor="e",
            style="Muted.TLabel",
        ).grid(row=0, column=2, sticky="e")

    def _update_clock(self) -> None:
        self.clock_var.set(datetime.now().strftime("%Y년 %m월 %d일 %H:%M:%S"))
        self._clock_after_id = self.after(250, self._update_clock)

    def _restore_full_opacity(self) -> None:
        self.window_opacity_var.set(WINDOW_OPACITY_MAX_PERCENT)
        self._saved_window_opacity = WINDOW_OPACITY_MAX_PERCENT
        self.service.storage.set_app_setting(
            "window.opacity",
            WINDOW_OPACITY_MAX_PERCENT,
        )
        self._apply_window_opacity(WINDOW_OPACITY_MAX_PERCENT)

    def _set_window_hovered(self, hovered: bool) -> None:
        if self._window_hovered == bool(hovered):
            return
        self._window_hovered = bool(hovered)
        if hasattr(self, "_brand_rail_canvas"):
            self._draw_brand_rail()

    def _toggle_position_lock(self) -> None:
        self._position_locked = not self._position_locked
        if self._position_locked:
            self._locked_window_position = (self.winfo_x(), self.winfo_y())
            self.position_lock_button.configure(text="위치 고정 ON", style="Blue.TButton")
        else:
            self._locked_window_position = None
            self.position_lock_button.configure(text="위치 고정 OFF", style="TButton")

    def _enforce_locked_position(self, event: tk.Event | None = None) -> None:
        if (
            event is not None
            and event.widget is not self
            or not self._position_locked
            or self._locked_window_position is None
            or self._restoring_locked_position
        ):
            return
        locked_x, locked_y = self._locked_window_position
        if self.winfo_x() == locked_x and self.winfo_y() == locked_y:
            return
        self._restoring_locked_position = True

        def restore() -> None:
            try:
                self.geometry(f"+{locked_x}+{locked_y}")
            finally:
                self._restoring_locked_position = False

        self.after_idle(restore)

    def _toggle_always_on_top(self) -> None:
        self.always_on_top_enabled = not self.always_on_top_enabled
        self.service.storage.set_app_setting(
            "window.always_on_top",
            self.always_on_top_enabled,
        )
        try:
            self.wm_attributes("-topmost", self.always_on_top_enabled)
        except tk.TclError:
            pass
        self.topmost_button.configure(
            text="핀 ON" if self.always_on_top_enabled else "핀 OFF",
            style="Blue.TButton" if self.always_on_top_enabled else "TButton",
        )

    def _open_application_settings(self) -> None:
        existing = getattr(self, "_application_settings_window", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return

        window = tk.Toplevel(self)
        self._application_settings_window = window
        window.title("카와이 증권 설정")
        window.geometry("560x650")
        window.minsize(520, 600)
        window.transient(self)
        window.configure(background=UI_BACKGROUND)

        nickname_var = tk.StringVar(value=self.user_nickname)
        startup_var = tk.BooleanVar(value=self.startup_auto_run_enabled)
        topmost_var = tk.BooleanVar(value=self.always_on_top_enabled)
        tray_var = tk.BooleanVar(value=self.minimize_to_tray_enabled)
        mask_var = tk.BooleanVar(value=self.account_mask_enabled)
        opacity_var = tk.DoubleVar(value=self.window_opacity_var.get())
        opacity_text_var = tk.StringVar(value=f"{int(round(opacity_var.get()))}%")
        original_opacity = int(round(self.window_opacity_var.get()))
        buy_sound_var = tk.StringVar(
            value=self.service.storage.get_app_setting("audio.buy_sound", "") or ""
        )
        sell_sound_var = tk.StringVar(
            value=self.service.storage.get_app_setting("audio.sell_sound", "") or ""
        )

        body = ttk.Frame(window, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text="프로그램 설정",
            font=(UI_DISPLAY_FONT, 16, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            body,
            text="화면, Windows 시작 동작과 개인정보 표시를 설정합니다.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(4, 16))

        profile = ttk.LabelFrame(body, text="사용자 및 개인정보", padding=12)
        profile.pack(fill="x")
        name_row = ttk.Frame(profile)
        name_row.pack(fill="x")
        ttk.Label(name_row, text="이름 또는 닉네임", width=18).pack(side="left")
        ttk.Entry(name_row, textvariable=nickname_var).pack(side="left", fill="x", expand=True)
        ttk.Checkbutton(
            profile,
            text="계좌번호는 마지막 2자리만 보이고 나머지는 *로 표시",
            variable=mask_var,
        ).pack(anchor="w", pady=(10, 0))

        behavior = ttk.LabelFrame(body, text="Windows 및 창 동작", padding=12)
        behavior.pack(fill="x", pady=(12, 0))
        for label, variable in (
            ("Windows 로그인 후 카와이 증권 자동 실행", startup_var),
            ("항상 다른 창 위에 표시", topmost_var),
            ("최소화하거나 닫을 때 시스템 트레이로 이동", tray_var),
        ):
            ttk.Checkbutton(behavior, text=label, variable=variable).pack(
                anchor="w",
                pady=3,
            )

        opacity = ttk.LabelFrame(body, text="창 투명도 0% ~ 100%", padding=12)
        opacity.pack(fill="x", pady=(12, 0))
        opacity.columnconfigure(0, weight=1)

        def preview_opacity(value: object) -> None:
            percent = _clamp_window_opacity_percent(value)
            opacity_text_var.set(f"{percent}%")
            self.window_opacity_var.set(percent)
            self._apply_window_opacity(percent)

        ttk.Scale(
            opacity,
            from_=WINDOW_OPACITY_MIN_PERCENT,
            to=WINDOW_OPACITY_MAX_PERCENT,
            variable=opacity_var,
            orient="horizontal",
            command=preview_opacity,
            style="Opacity.Horizontal.TScale",
        ).grid(row=0, column=0, sticky="ew")
        ttk.Label(opacity, textvariable=opacity_text_var, width=6, anchor="e").grid(
            row=0,
            column=1,
            padx=(10, 0),
        )
        ttk.Label(
            opacity,
            text="화면이 보이지 않으면 Ctrl+Shift+O를 눌러 100%로 복구할 수 있습니다.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(7, 0))

        sound_frame = ttk.LabelFrame(body, text="체결 효과음 (WAV)", padding=12)
        sound_frame.pack(fill="x", pady=(12, 0))
        for row, label, variable in (
            (0, "매수 체결 효과음", buy_sound_var),
            (1, "매도 체결 효과음", sell_sound_var),
        ):
            ttk.Label(sound_frame, text=label, width=16).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(sound_frame, textvariable=variable).grid(
                row=row, column=1, sticky="ew", padx=(8, 6), pady=3
            )
            ttk.Button(
                sound_frame,
                text="찾아보기",
                command=lambda target=variable: self._choose_sound_file(target, window),
            ).grid(row=row, column=2, pady=3)
        sound_frame.columnconfigure(1, weight=1)

        def cancel() -> None:
            self.window_opacity_var.set(original_opacity)
            self._apply_window_opacity(original_opacity)
            window.destroy()

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(18, 0))
        ttk.Button(buttons, text="취소", command=cancel).pack(side="right")
        ttk.Button(
            buttons,
            text="설정 저장",
            style="Accent.TButton",
            command=lambda: self._save_application_settings(
                window,
                nickname_var.get(),
                startup_var.get(),
                topmost_var.get(),
                tray_var.get(),
                mask_var.get(),
                opacity_var.get(),
                buy_sound_var.get(),
                sell_sound_var.get(),
            ),
        ).pack(side="right", padx=(0, 8))
        window.protocol("WM_DELETE_WINDOW", cancel)

    def _choose_sound_file(self, variable: tk.StringVar, parent: tk.Misc) -> None:
        selected = filedialog.askopenfilename(
            parent=parent,
            title="체결 효과음 선택",
            filetypes=(("WAV 소리 파일", "*.wav"), ("모든 파일", "*.*")),
        )
        if selected:
            variable.set(selected)

    def _save_application_settings(
        self,
        window: tk.Toplevel,
        nickname: str,
        startup_auto_run: bool,
        always_on_top: bool,
        minimize_to_tray: bool,
        account_mask: bool,
        opacity: object,
        buy_sound: str = "",
        sell_sound: str = "",
    ) -> None:
        name = str(nickname or "").strip()
        if not name:
            self._show_warning("사용자 이름 필요", "오류 안내에 사용할 이름 또는 닉네임을 입력해 주세요.")
            return
        try:
            set_startup_registration(bool(startup_auto_run))
        except OSError as exc:
            self._show_error("자동 실행 설정 실패", f"Windows 자동 실행 설정을 저장하지 못했습니다. {exc}")
            return

        percent = _clamp_window_opacity_percent(opacity)
        values = {
            "profile.nickname": name,
            "window.startup_auto_run": bool(startup_auto_run),
            "window.always_on_top": bool(always_on_top),
            "window.minimize_to_tray": bool(minimize_to_tray),
            "privacy.mask_account": bool(account_mask),
            "window.opacity": percent,
            "audio.buy_sound": str(buy_sound or "").strip(),
            "audio.sell_sound": str(sell_sound or "").strip(),
        }
        for key, value in values.items():
            self.service.storage.set_app_setting(key, value)

        self.user_nickname = name
        self.startup_auto_run_enabled = bool(startup_auto_run)
        self.always_on_top_enabled = bool(always_on_top)
        self.minimize_to_tray_enabled = bool(minimize_to_tray)
        self.account_mask_enabled = bool(account_mask)
        self._saved_window_opacity = percent
        self.window_opacity_var.set(percent)
        self._apply_window_opacity(percent)
        self.sound_notifier.set_paths(buy_sound, sell_sound)
        try:
            self.wm_attributes("-topmost", self.always_on_top_enabled)
        except tk.TclError:
            pass
        self.topmost_button.configure(
            text="핀 ON" if self.always_on_top_enabled else "핀 OFF",
            style="Blue.TButton" if self.always_on_top_enabled else "TButton",
        )
        if not self.minimize_to_tray_enabled and self._tray_icon is not None:
            self._tray_icon.stop()
            self._tray_icon = None
        self._set_account_display(self._selected_account_full)
        self.service.storage.log("INFO", "설정", "프로그램 개인 설정을 저장했습니다.")
        window.destroy()

    def _show_warning(self, title: str, message: str, parent: tk.Misc | None = None) -> None:
        messagebox.showwarning(
            title,
            personalized_message(self.user_nickname, message),
            parent=parent or self,
        )

    def _show_error(self, title: str, message: str, parent: tk.Misc | None = None) -> None:
        if hasattr(self, "transfer_state_var"):
            self._update_transfer_status("error")
        messagebox.showerror(
            title,
            personalized_message(self.user_nickname, message),
            parent=parent or self,
        )

    def _on_window_unmap(self, event: tk.Event | None = None) -> None:
        if event is not None and event.widget is not self:
            return
        if self.minimize_to_tray_enabled and not self._force_exit:
            self.after(120, self._hide_if_minimized)

    def _hide_if_minimized(self) -> None:
        if self.state() == "iconic":
            self._hide_to_tray()

    def _ensure_tray_icon(self) -> bool:
        if self._tray_icon is not None:
            return True
        tray = WindowsTrayIcon(
            "카와이 증권",
            _application_resource("assets", "kiwoom_trade.ico"),
        )
        if not tray.start():
            return False
        self._tray_icon = tray
        self._schedule_tray_poll()
        return True

    def _hide_to_tray(self) -> bool:
        if not self._ensure_tray_icon():
            return False
        self.withdraw()
        return True

    def _schedule_tray_poll(self) -> None:
        if self._tray_poll_after_id is None:
            self._tray_poll_after_id = self.after(250, self._poll_tray_actions)

    def _poll_tray_actions(self) -> None:
        self._tray_poll_after_id = None
        if self._tray_icon is None:
            return
        action = self._tray_icon.poll_action()
        while action:
            if action == "restore":
                self._restore_from_tray()
            elif action == "exit":
                self._force_exit = True
                self._finalize_close()
                return
            action = self._tray_icon.poll_action()
        self._schedule_tray_poll()

    def _restore_from_tray(self) -> None:
        self.deiconify()
        self.state("normal")
        self.lift()
        if self.always_on_top_enabled:
            self.after_idle(lambda: self.wm_attributes("-topmost", True))

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=0, minsize=EXPANDED_SIDE_PANEL_WIDTH)
        self.rowconfigure(2, weight=1)
        self.connection_state_var = tk.StringVar(value="OFF 연결 안됨")
        self.clock_var = tk.StringVar(value="----년 --월 --일 --:--:--")
        self.transfer_state_var = tk.StringVar(value="전송 상태 대기")
        self.active_symbol_tag_var = tk.StringVar(value="")
        self.daily_traded_tags_var = tk.StringVar(value="오늘 거래 종목: 없음")

        header = ttk.Frame(self, padding=(16, 6, 16, 6), style="Header.TFrame")
        self.header = header
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        self._brand_rail_canvas = tk.Canvas(
            header,
            height=4,
            bg=UI_SURFACE,
            highlightthickness=0,
            bd=0,
        )
        self._brand_rail_canvas.grid(
            row=0,
            column=0,
            columnspan=8,
            sticky="ew",
            pady=(0, 6),
        )
        self._brand_rail_canvas.bind("<Configure>", self._draw_brand_rail)

        brand = ttk.Frame(header, style="Header.TFrame")
        brand.grid(row=1, column=0, sticky="w")
        self.compact_mode_button = ttk.Button(
            brand,
            text="−",
            width=3,
            command=self._enter_compact_mode,
            takefocus=True,
        )
        self.compact_mode_button.pack(side="left", padx=(0, 8))
        brand_mark = tk.Canvas(
            brand,
            width=38,
            height=38,
            bg=UI_SURFACE,
            highlightthickness=0,
            bd=0,
        )
        brand_mark.pack(side="left", padx=(0, 10))
        for index, color in enumerate((UI_PURPLE, UI_PINK, UI_ORANGE, UI_YELLOW)):
            brand_mark.create_rectangle(
                index * 9,
                1,
                (index + 1) * 9 + 2,
                37,
                fill=color,
                outline=color,
            )
        brand_mark.create_text(
            19,
            19,
            text="K",
            fill=UI_SURFACE,
            font=(UI_DISPLAY_FONT, 16, "bold"),
        )
        brand_copy = ttk.Frame(brand, style="Header.TFrame")
        brand_copy.pack(side="left")
        tk.Label(
            brand_copy,
            text="Kawaii Securities",
            background=UI_SURFACE,
            foreground=UI_TEXT,
            font=(UI_WORDMARK_FONT, 18, "bold"),
            borderwidth=0,
        ).pack(anchor="w")
        ttk.Label(brand_copy, text="카와이 증권", style="Muted.TLabel").pack(anchor="w")

        self.account_button = ttk.Button(
            header,
            text="OpenAPI+ 로그인",
            command=self._open_login_dialog,
        )
        self.account_button.grid(row=1, column=1, padx=(0, 8))
        self.rest_account_button = ttk.Button(
            header,
            text="REST API 연결",
            command=self._open_rest_login_dialog,
            style="Blue.TButton",
        )
        self.rest_account_button.grid(row=1, column=2, padx=(0, 8))
        ttk.Button(header, text="연결 상태 확인", command=self._check_account_environment).grid(
            row=1,
            column=3,
            padx=(0, 8),
        )
        self.connection_light = tk.Canvas(
            header,
            width=18,
            height=18,
            bg=UI_SURFACE,
            highlightthickness=0,
            bd=0,
        )
        self.connection_light.grid(row=1, column=4, padx=(0, 6))
        self._connection_light_id = self.connection_light.create_oval(
            2,
            2,
            16,
            16,
            fill="#B8B8B8",
            outline=UI_MUTED,
        )
        self.connection_badge = ttk.Label(
            header,
            textvariable=self.connection_state_var,
            font=(UI_DISPLAY_FONT, 10, "bold"),
            padding=(12, 6),
            style="Neutral.Badge.TLabel",
        )
        self.connection_badge.grid(row=1, column=5, padx=(0, 8))
        ttk.Button(header, text="관심종목", command=self._open_watchlist_window).grid(
            row=1,
            column=6,
            padx=(0, 8),
        )
        ttk.Button(
            header,
            text="긴급 일괄 청산",
            command=self._emergency_stop,
            style="Danger.TButton",
        ).grid(row=1, column=7)
        status_strip = ttk.Frame(header, style="Header.TFrame")
        status_strip.grid(row=2, column=0, columnspan=8, sticky="ew", pady=(7, 0))
        status_strip.columnconfigure(0, weight=1)
        ttk.Label(
            status_strip,
            textvariable=self.clock_var,
            font=(UI_DISPLAY_FONT, 10, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self.position_lock_button = ttk.Button(
            status_strip,
            text="위치 고정 OFF",
            command=self._toggle_position_lock,
            takefocus=True,
        )
        self.position_lock_button.grid(row=0, column=1, padx=(8, 5))
        self.side_panel_button = ttk.Button(
            status_strip,
            text="<<",
            width=4,
            command=self._toggle_side_panel,
        )
        self.side_panel_button.grid(row=0, column=2, padx=5)
        self.topmost_button = ttk.Button(
            status_strip,
            text="핀 ON" if self.always_on_top_enabled else "핀 OFF",
            width=7,
            command=self._toggle_always_on_top,
        )
        self.topmost_button.grid(row=0, column=3, padx=5)
        self.settings_button = ttk.Button(
            status_strip,
            text="설정",
            command=self._open_application_settings,
        )
        self.settings_button.grid(row=0, column=4, padx=(5, 10))
        self.controls_toggle_button = ttk.Button(
            status_strip,
            text="설정 접기",
            command=self._toggle_controls_panel,
        )
        self.controls_toggle_button.grid(row=0, column=5, padx=(0, 5))
        self.transfer_light = tk.Canvas(
            status_strip,
            width=16,
            height=16,
            bg=UI_SURFACE,
            highlightthickness=0,
            bd=0,
        )
        self.transfer_light.grid(row=0, column=6, padx=(10, 5))
        self._transfer_light_id = self.transfer_light.create_oval(
            2,
            2,
            14,
            14,
            fill="#B8B8B8",
            outline=UI_MUTED,
        )
        self.transfer_badge = ttk.Label(
            status_strip,
            textvariable=self.transfer_state_var,
            padding=(9, 3),
            style="Neutral.Badge.TLabel",
        )
        self.transfer_badge.grid(row=0, column=7, sticky="e")
        tag_strip = ttk.Frame(header, style="Header.TFrame")
        tag_strip.grid(row=3, column=0, columnspan=8, sticky="ew", pady=(5, 0))
        tag_strip.columnconfigure(1, weight=1)
        ttk.Label(
            tag_strip,
            textvariable=self.active_symbol_tag_var,
            foreground=UI_PINK,
            font=(UI_DISPLAY_FONT, 10, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            tag_strip,
            textvariable=self.daily_traded_tags_var,
            foreground=UI_MUTED,
        ).grid(row=0, column=1, sticky="e")
        self._update_connection_badge(False)
        self._build_window_opacity_control()

        controls = ttk.LabelFrame(self, text="종목 · 전략 · 계좌 설정", padding=(12, 10))
        self.controls = controls
        controls.grid(row=1, column=0, sticky="ew", padx=12)
        for idx in range(10):
            controls.columnconfigure(idx, weight=1)

        self.symbol_var = tk.StringVar(value="000000")
        self.symbol_name_var = tk.StringVar(value="")
        self.capital_var = tk.StringVar(value="1000000")
        self.baseline_status_var = tk.StringVar(value="미설정")
        self.dmi_period_var = tk.IntVar(value=14)
        self.dmi_period_display_var = tk.StringVar(value="14일")
        self.dmi_state_var = tk.StringVar(value="계산 전")
        self.dmi_plus_var = tk.StringVar(value="-")
        self.dmi_minus_var = tk.StringVar(value="-")
        self.adx_var = tk.StringVar(value="-")
        self.account_var = tk.StringVar(value="")
        self.account_history_var = tk.StringVar(value="")
        self.account_first_var = tk.StringVar(value="")
        self.account_last_var = tk.StringVar(value="")
        self.account_password_var = tk.StringVar(value="")
        self.account_password_status_var = tk.StringVar(value="미확인")
        self.order_qty_var = tk.StringVar(value="0")
        self.order_qty_display_var = tk.StringVar(value="0주")
        self.current_price_display_var = tk.StringVar(value="미조회")
        self.allow_real_order_var = tk.BooleanVar(value=False)
        self.order_price_mode_var = tk.StringVar(value="MIDPOINT")
        self.use_margin_var = tk.BooleanVar(value=False)
        self.original_order_no_var = tk.StringVar(value="")
        self.quick_slot_var = tk.IntVar(value=1)
        self.link_group_var = tk.StringVar(
            value=self.service.storage.get_app_setting("symbols.link_group", "1") or "1"
        )
        self.daily_loss_limit_var = tk.StringVar(
            value=f"{self.service.daily_loss_breaker.limit_percent:g}"
        )
        self.circuit_liquidation_var = tk.BooleanVar(value=False)
        self.log_chart_var = tk.BooleanVar(value=False)
        self.market_session_var = tk.StringVar(value="장 상태: 키움 신호 대기")
        self.auto_started_at_var = tk.StringVar(value="실시간 감시 시작 시각: 아직 시작하지 않음")
        self.auto_trade_capability_var = tk.StringVar(value="자동주문 미사용")
        self.auto_trade_detail_var = tk.StringVar(value="자동매수·자동매도 기능이 제거되었습니다. 수동 주문만 사용할 수 있습니다.")
        self.holding_monitor_state_var = tk.StringVar(value="감시 OFF")
        self.holding_monitor_detail_var = tk.StringVar(
            value="실시간 감시를 시작하면 매입한 주식의 보유수량을 감시합니다."
        )
        self.account_summary_var = tk.StringVar(
            value="계좌 창: 로그인 전입니다. 키움 로그인 후 계좌번호 앞4자리+뒤4자리와 잔고가 표시됩니다."
        )
        self.trade_ready_var = tk.StringVar(value="거래 준비: 종목번호와 회사명, 계좌번호를 확인해 주세요.")

        self._field(controls, "종목번호(6자리)", self.symbol_var, 0)
        ttk.Label(controls, text="회사명").grid(row=0, column=1, sticky="w")
        symbol_name_row = ttk.Frame(controls)
        symbol_name_row.grid(row=1, column=1, sticky="ew", padx=(0, 8))
        ttk.Label(symbol_name_row, textvariable=self.symbol_name_var, width=11).pack(
            side="left",
            fill="x",
            expand=True,
        )
        ttk.Button(symbol_name_row, text="종목 세팅", width=9, command=self._set_symbol).pack(side="right")
        ttk.Button(
            symbol_name_row,
            text="검색",
            width=5,
            command=self._open_symbol_search,
        ).pack(side="right", padx=(0, 4))
        ttk.Label(controls, text="종목별 운용 한도금액").grid(row=0, column=2, sticky="w")
        self.capital_entry = ttk.Entry(controls, textvariable=self.capital_var, width=14)
        self.capital_entry.grid(row=1, column=2, sticky="ew", padx=(0, 8))

        ttk.Label(controls, text="금액·기준가 고정").grid(row=0, column=3, sticky="w")
        baseline_controls = ttk.Frame(controls)
        baseline_controls.grid(row=1, column=3, sticky="ew", padx=(0, 8))
        baseline_buttons = ttk.Frame(baseline_controls)
        baseline_buttons.pack(anchor="w")
        self.baseline_set_button = ttk.Button(
            baseline_buttons,
            text="금액 세팅",
            width=8,
            command=self._set_trading_baseline,
        )
        self.baseline_set_button.pack(side="left")
        self.baseline_reset_button = ttk.Button(
            baseline_buttons,
            text="리세팅",
            width=6,
            command=self._reset_trading_baseline,
            state="disabled",
        )
        self.baseline_reset_button.pack(side="left", padx=(4, 0))
        ttk.Label(
            baseline_controls,
            textvariable=self.baseline_status_var,
            foreground=UI_MUTED,
            font=(UI_FONT, 8),
        ).pack(anchor="w", pady=(3, 0))
        ttk.Label(controls, text="DMI 계산 기간(1~99일)").grid(row=0, column=4, sticky="w")
        dmi_period_controls = ttk.Frame(controls)
        dmi_period_controls.grid(row=1, column=4, sticky="w", padx=(0, 8))
        ttk.Button(
            dmi_period_controls,
            text="-",
            width=3,
            command=lambda: self._change_dmi_period(-1),
        ).pack(side="left")
        ttk.Label(
            dmi_period_controls,
            textvariable=self.dmi_period_display_var,
            width=6,
            anchor="center",
            padding=(4, 3),
            style="Value.TLabel",
        ).pack(side="left", padx=3)
        ttk.Button(
            dmi_period_controls,
            text="+",
            width=3,
            command=lambda: self._change_dmi_period(1),
        ).pack(side="left")
        ttk.Label(controls, text="DMI 강/약 상태").grid(row=0, column=5, sticky="w")
        self.dmi_state_badge = ttk.Label(
            controls,
            textvariable=self.dmi_state_var,
            padding=(8, 3),
            style="Neutral.Badge.TLabel",
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
        actions.columnconfigure(4, weight=1)
        ttk.Button(
            actions,
            text="실시간 감시 시작",
            command=self._start,
            style="Accent.TButton",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(actions, text="실시간 감시 중지", command=self._stop).grid(
            row=0,
            column=1,
            sticky="w",
            padx=6,
        )
        self.auto_trade_capability_badge = ttk.Label(
            actions,
            textvariable=self.auto_trade_capability_var,
            font=(UI_DISPLAY_FONT, 9, "bold"),
            padding=(10, 3),
            style="Danger.Badge.TLabel",
        )
        self.auto_trade_capability_badge.grid(row=0, column=2, sticky="w", padx=(6, 8))
        self.holding_monitor_badge = ttk.Label(
            actions,
            textvariable=self.holding_monitor_state_var,
            font=(UI_DISPLAY_FONT, 9, "bold"),
            padding=(10, 3),
            style="Danger.Badge.TLabel",
        )
        self.holding_monitor_badge.grid(row=2, column=0, sticky="w", pady=(5, 0))
        ttk.Label(
            actions,
            text="수동 주문 전용",
            foreground=UI_MUTED,
        ).grid(row=0, column=3, sticky="w", padx=(8, 0))
        ttk.Label(actions, textvariable=self.market_session_var).grid(
            row=0,
            column=4,
            sticky="e",
            padx=(14, 0),
        )
        ttk.Label(
            actions,
            textvariable=self.auto_started_at_var,
            foreground=UI_MUTED,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 0))
        ttk.Label(
            actions,
            textvariable=self.auto_trade_detail_var,
            foreground=UI_MUTED,
        ).grid(row=1, column=2, columnspan=3, sticky="w", padx=(6, 0), pady=(5, 0))
        self.holding_monitor_detail_label = ttk.Label(
            actions,
            textvariable=self.holding_monitor_detail_var,
            foreground=UI_RED,
            font=(UI_DISPLAY_FONT, 9, "bold"),
        )
        self.holding_monitor_detail_label.grid(
            row=2,
            column=1,
            columnspan=4,
            sticky="w",
            padx=(8, 0),
            pady=(5, 0),
        )

        kiwoom_controls = ttk.Frame(controls)
        kiwoom_controls.grid(row=3, column=0, columnspan=10, sticky="ew", pady=(12, 0))
        ttk.Label(kiwoom_controls, text="최근 계좌").pack(side="left")
        self.account_history_combo = ttk.Combobox(
            kiwoom_controls,
            textvariable=self.account_history_var,
            values=self._account_history_display_values(),
            state="readonly",
            width=13,
        )
        self.account_history_combo.pack(side="left", padx=(4, 10))
        self.account_history_combo.bind(
            "<<ComboboxSelected>>",
            self._on_account_history_selected,
        )
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
        ttk.Label(kiwoom_controls, text="계좌 비밀번호").pack(side="left", padx=(6, 4))
        self.account_password_entry = ttk.Entry(
            kiwoom_controls,
            textvariable=self.account_password_var,
            width=10,
            justify="center",
            show="*",
            validate="key",
            validatecommand=(self.register(_account_password_input_allowed), "%P"),
        )
        self.account_password_entry.pack(side="left")
        self.account_password_entry.bind("<Return>", lambda _event: self._set_account_password())
        self.account_password_button = ttk.Button(
            kiwoom_controls,
            text="비밀번호 세팅",
            command=self._set_account_password,
        )
        self.account_password_button.pack(side="left", padx=(4, 4))
        ttk.Label(
            kiwoom_controls,
            textvariable=self.account_password_status_var,
            width=8,
            anchor="w",
        ).pack(side="left")
        self.allow_real_order_checkbutton = ttk.Checkbutton(
            kiwoom_controls,
            text="실거래 수동주문 허용",
            variable=self.allow_real_order_var,
            command=self._toggle_real_order_authorization,
        )
        self.allow_real_order_checkbutton.pack(side="left", padx=(12, 0))
        ttk.Checkbutton(
            kiwoom_controls,
            text="미수 사용(최대 2배)",
            variable=self.use_margin_var,
        ).pack(side="left", padx=(10, 0))

        order_controls = ttk.Frame(controls)
        order_controls.grid(row=4, column=0, columnspan=10, sticky="ew", pady=(8, 0))
        for column in range(4):
            order_controls.columnconfigure(column, weight=1, uniform="order-control")

        price_box = ttk.LabelFrame(order_controls, text="현재가", padding=8)
        price_box.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        ttk.Label(
            price_box,
            textvariable=self.current_price_display_var,
            font=(UI_DISPLAY_FONT, 11, "bold"),
            width=13,
            anchor="e",
        ).pack(side="left", padx=(0, 8))
        ttk.Button(price_box, text="불러오기", command=self._request_current_price).pack(side="left")

        quantity_box = ttk.LabelFrame(order_controls, text="주문 수량", padding=8)
        quantity_box.grid(row=0, column=1, sticky="nsew", padx=4)
        ttk.Label(
            quantity_box,
            textvariable=self.order_qty_display_var,
            width=6,
            anchor="center",
            padding=(4, 3),
            style="Value.TLabel",
        ).pack(side="left", padx=(0, 6))
        ttk.Button(quantity_box, text="-", width=4, command=lambda: self._change_order_quantity(-1)).pack(
            side="left",
            padx=2,
        )
        ttk.Button(quantity_box, text="+", width=4, command=lambda: self._change_order_quantity(1)).pack(
            side="left",
            padx=2,
        )

        manual_info = ttk.LabelFrame(order_controls, text="수동 주문 안내", padding=8)
        manual_info.grid(row=0, column=2, columnspan=2, sticky="nsew", padx=(4, 0))
        ttk.Label(
            manual_info,
            text="수동 주문 전용\n매수·매도 버튼을 눌렀을 때만 주문합니다.",
            foreground=UI_MUTED,
            justify="left",
        ).pack(anchor="w")

        api_actions = ttk.Frame(controls)
        api_actions.grid(row=5, column=0, columnspan=10, sticky="ew", pady=(8, 0))
        data_actions = ttk.Frame(api_actions)
        data_actions.pack(fill="x")
        trade_actions = ttk.Frame(api_actions)
        trade_actions.pack(fill="x", pady=(5, 0))
        ttk.Button(data_actions, text="3분봉 데이터 불러오기", command=self._request_three_minute).pack(side="left")
        ttk.Button(data_actions, text="계좌잔고 불러오기", command=self._request_balance).pack(side="left", padx=4)
        ttk.Button(data_actions, text="실시간 시세 시작", command=self._register_real_time).pack(side="left", padx=4)
        ttk.Button(data_actions, text="실시간 시세 중지", command=self._unregister_real_time).pack(side="left", padx=4)
        ttk.Button(data_actions, text="일봉+DMI 강약 판단", command=self._evaluate_market_strategy).pack(
            side="left",
            padx=4,
        )
        self.buy_button = ttk.Button(
            trade_actions,
            text="수동 매수",
            command=lambda: self._send_order("BUY", "NEW"),
        )
        self.buy_button.configure(style="Accent.TButton")
        self.buy_button.pack(
            side="left",
            padx=(10, 4),
        )
        self.sell_button = ttk.Button(
            trade_actions,
            text="수동 매도",
            command=lambda: self._send_order("SELL", "NEW"),
        )
        self.sell_button.configure(style="Blue.TButton")
        self.sell_button.pack(
            side="left",
            padx=4,
        )
        self.modify_button = ttk.Button(
            trade_actions,
            text="수동 정정",
            command=lambda: self._send_selected_order_action("MODIFY"),
        )
        self.modify_button.pack(side="left", padx=4)
        self.cancel_button = ttk.Button(
            trade_actions,
            text="주문 취소",
            command=lambda: self._send_selected_order_action("CANCEL"),
        )
        self.cancel_button.pack(side="left", padx=4)

        quick_row = ttk.Frame(controls)
        quick_row.grid(row=6, column=0, columnspan=10, sticky="ew", pady=(8, 0))
        quick_settings = ttk.Frame(quick_row)
        quick_settings.pack(fill="x")
        quick_buttons = ttk.Frame(quick_row)
        quick_buttons.pack(fill="x", pady=(5, 0))
        ttk.Label(quick_settings, text="수동 주문 전용").pack(side="left")
        ttk.Label(quick_settings, text="중간가 지정가").pack(side="left", padx=(10, 4))
        ttk.Label(quick_settings, text="연동 그룹").pack(side="left", padx=(10, 4))
        group_combo = ttk.Combobox(
            quick_settings,
            textvariable=self.link_group_var,
            values=tuple(str(value) for value in range(1, 10)),
            width=3,
            state="readonly",
        )
        group_combo.pack(side="left")
        group_combo.bind("<<ComboboxSelected>>", self._save_link_group)
        ttk.Separator(quick_settings, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Label(quick_settings, text="원주문번호").pack(side="left", padx=(0, 4))
        self.original_order_entry = ttk.Entry(
            quick_settings,
            textvariable=self.original_order_no_var,
            width=12,
        )
        self.original_order_entry.pack(side="left")
        self.original_order_entry.bind(
            "<KeyRelease>", lambda _event: self._update_trade_buttons()
        )
        ttk.Button(
            quick_settings,
            text="선택 슬롯에 현재 수량 저장",
            command=self._save_selected_quick_order,
        ).pack(side="left", padx=(8, 0))
        ttk.Label(quick_buttons, text="퀵 주문 1~10").pack(side="left")
        self.quick_order_buttons: list[ttk.Button] = []
        for slot, quantity in enumerate(self._quick_order_presets, start=1):
            button = ttk.Button(
                quick_buttons,
                text=f"{slot}·{quantity}주",
                width=6,
                command=lambda value=slot: self._activate_quick_order(value),
            )
            button.pack(side="left", padx=(3, 0))
            self.quick_order_buttons.append(button)
        ready_row = ttk.Frame(controls)
        ready_row.grid(row=7, column=0, columnspan=10, sticky="ew", pady=(8, 0))
        self.chart_button = ttk.Button(
            ready_row,
            text="DMI 차트 확대",
            command=self._show_candle_chart,
        )
        self.chart_button.pack(side="left")
        ttk.Label(ready_row, textvariable=self.trade_ready_var).pack(side="left", padx=(10, 0))

        body = ttk.Frame(self, padding=(12, 6))
        self.main_body = body
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        self.status_text = tk.StringVar(value="")
        ttk.Label(
            body,
            textvariable=self.account_summary_var,
            font=(UI_FONT, 10),
            wraplength=1120,
            justify="left",
        ).grid(
            row=0, column=0, sticky="ew", pady=(0, 10)
        )

        self.main_notebook = ttk.Notebook(body)
        self.main_notebook.grid(row=1, column=0, sticky="nsew")

        self.dmi_chart_tab = ttk.Frame(self.main_notebook, padding=(10, 6, 10, 4))
        self.dmi_chart_tab.columnconfigure(0, weight=1)
        self.dmi_chart_tab.rowconfigure(2, weight=1)
        self.main_notebook.add(self.dmi_chart_tab, text="멀티주기 DMI 차트")

        chart_header = ttk.Frame(self.dmi_chart_tab)
        chart_header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        chart_header.columnconfigure(0, weight=1)
        self.chart_caption_var = tk.StringVar(value="키움 3분봉 | 일봉 DMI 조회")
        ttk.Label(
            chart_header,
            textvariable=self.chart_caption_var,
            font=(UI_DISPLAY_FONT, 11, "bold"),
        ).grid(row=0, column=0, sticky="w")

        legend = ttk.Frame(chart_header)
        legend.grid(row=0, column=1, sticky="e")
        ttk.Label(
            legend,
            text="강세 (+DI 우세)",
            padding=(7, 2),
            style="PinkSoft.Badge.TLabel",
        ).pack(side="left")
        ttk.Label(
            legend,
            text="약세 (-DI 우세)",
            padding=(7, 2),
            style="BlueSoft.Badge.TLabel",
        ).pack(side="left", padx=(5, 10))
        ttk.Label(legend, text="+DI", foreground=UI_PINK).pack(side="left")
        ttk.Label(legend, text="-DI", foreground=UI_BLUE).pack(side="left", padx=(7, 0))
        ttk.Label(legend, text="ADX", foreground=UI_MUTED).pack(side="left", padx=(7, 0))

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
            (
                "시",
                (("1시간", "60m"), ("2시간", "120m"), ("4시간", "240m")),
            ),
        )
        for group_index, (group_label, choices) in enumerate(timeframe_groups):
            if group_index:
                ttk.Separator(chart_toolbar, orient="vertical").pack(
                    side="left",
                    fill="y",
                    padx=6,
                )
            ttk.Label(chart_toolbar, text=group_label, font=(UI_DISPLAY_FONT, 9, "bold")).pack(
                side="left",
                padx=(0, 4),
            )
            for label, value in choices:
                ttk.Radiobutton(
                    chart_toolbar,
                    text=label,
                    variable=self.chart_timeframe_var,
                    value=value,
                    width=5,
                    style="Pill.TRadiobutton",
                    command=self._on_chart_timeframe_changed,
                ).pack(side="left", padx=1)

        zoom_controls = ttk.Frame(chart_toolbar)
        zoom_controls.pack(side="right")
        ttk.Button(
            zoom_controls,
            text="-",
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
        ttk.Label(indicator_panel, text="지표 설정", font=(UI_DISPLAY_FONT, 10, "bold")).pack(
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
            ("로그 차트", self.log_chart_var),
        ):
            ttk.Checkbutton(
                indicator_panel,
                text=label,
                variable=variable,
                command=self._draw_main_dmi_chart,
            ).pack(anchor="w", pady=2)
        ttk.Separator(indicator_panel, orient="horizontal").pack(fill="x", pady=10)
        indicator_legend = ttk.Frame(indicator_panel)
        indicator_legend.pack(fill="x")
        ttk.Label(indicator_legend, text="양봉", foreground=UI_RED).grid(row=0, column=0, sticky="w")
        ttk.Label(indicator_legend, text="음봉", foreground=UI_BLUE).grid(
            row=0,
            column=1,
            sticky="w",
            padx=(14, 0),
        )
        ttk.Label(indicator_legend, text="MA5", foreground=UI_PINK).grid(
            row=1,
            column=0,
            sticky="w",
            pady=(4, 0),
        )
        ttk.Label(indicator_legend, text="MA20", foreground=UI_BLUE).grid(
            row=1,
            column=1,
            sticky="w",
            padx=(14, 0),
            pady=(4, 0),
        )

        self.main_chart_canvas = tk.Canvas(
            chart_workspace,
            background=UI_SURFACE,
            highlightthickness=1,
            highlightbackground=UI_BORDER,
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
        for column, weight in enumerate((4, 6, 4)):
            self.operations_tab.columnconfigure(
                column,
                weight=weight,
                uniform="operations",
            )
        self.operations_tab.rowconfigure(0, weight=1)
        self.main_notebook.add(self.operations_tab, text="계좌·주문·로그")

        self.holdings = self._table(
            self.operations_tab,
            "계좌 잔고",
            (
                "종목번호",
                "종목명",
                "보유",
                "매도가능",
                "평균",
                "현재가",
                "매입금액",
                "평가손익",
                "수익률",
            ),
            0,
        )
        holding_column_widths = {
            "종목번호": 65,
            "종목명": 70,
            "보유": 40,
            "매도가능": 55,
            "평균": 60,
            "현재가": 60,
            "매입금액": 72,
            "평가손익": 65,
            "수익률": 50,
        }
        for name, width in holding_column_widths.items():
            self.holdings.column(name, width=width, minwidth=38, stretch=False)
        self.orders = self._table(
            self.operations_tab,
            f"최근 {ACCOUNT_TRADE_HISTORY_DAYS}일 실제 체결 / 최근 주문요청",
            ("일시(초)", "종목", "구분", "수량", "가격", "결과", "메시지"),
            1,
        )
        order_column_widths = {
            "일시(초)": 110,
            "종목": 55,
            "구분": 45,
            "수량": 45,
            "가격": 65,
            "결과": 50,
            "메시지": 115,
        }
        for name, width in order_column_widths.items():
            self.orders.column(
                name,
                width=width,
                minwidth=40,
                stretch=name == "메시지",
            )
        self.logs = self._table(self.operations_tab, "시스템 로그", ("시간", "레벨", "분류", "메시지"), 2)
        log_column_widths = {
            "시간": 100,
            "레벨": 45,
            "분류": 65,
            "메시지": 120,
        }
        for name, width in log_column_widths.items():
            self.logs.column(
                name,
                width=width,
                minwidth=40,
                stretch=name == "메시지",
            )

        self.trade_history_tab = ttk.Frame(self.main_notebook, padding=(10, 10, 10, 8))
        self.trade_history_tab.columnconfigure(0, weight=1)
        self.trade_history_tab.rowconfigure(1, weight=1)
        self.main_notebook.add(self.trade_history_tab, text="매수·매도 이력")

        trade_toolbar = ttk.Frame(self.trade_history_tab)
        trade_toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        trade_toolbar.columnconfigure(0, weight=1)
        self.account_trade_history_status_var = tk.StringVar(
            value=(
                f"키움 최근 {ACCOUNT_TRADE_HISTORY_DAYS}일 전체 체결: "
                "계좌 연결 후 자동 조회합니다."
            )
        )
        ttk.Label(
            trade_toolbar,
            textvariable=self.account_trade_history_status_var,
            foreground=UI_BLUE,
        ).grid(row=0, column=0, sticky="w")
        self.trade_history_refresh_button = ttk.Button(
            trade_toolbar,
            text=f"최근 {ACCOUNT_TRADE_HISTORY_DAYS}일 전체 불러오기",
            command=self._request_recent_trade_history,
        )
        self.trade_history_refresh_button.grid(row=0, column=1, sticky="e")
        self.trade_history_file_var = tk.StringVar(
            value=f"프로그램 주문요청 CSV: {self.service.storage.trade_history_path}"
        )
        ttk.Label(trade_toolbar, textvariable=self.trade_history_file_var).grid(
            row=1,
            column=0,
            sticky="w",
            pady=(6, 0),
        )
        ttk.Button(
            trade_toolbar,
            text="주문요청 CSV 열기",
            command=self._open_trade_history_file,
        ).grid(row=1, column=1, sticky="e", pady=(6, 0))

        trade_columns = (
            "일시(초)",
            "구분",
            "종목번호",
            "종목명",
            "수량",
            "가격",
            "금액",
            "결과",
            "주문번호",
            "거래환경",
            "메시지",
        )
        trade_table_frame = ttk.Frame(self.trade_history_tab)
        trade_table_frame.grid(row=1, column=0, sticky="nsew")
        trade_table_frame.columnconfigure(0, weight=1)
        trade_table_frame.rowconfigure(0, weight=1)
        self.trade_history = ttk.Treeview(
            trade_table_frame,
            columns=trade_columns,
            show="headings",
            height=18,
        )
        trade_widths = {
            "일시(초)": 150,
            "구분": 60,
            "종목번호": 80,
            "종목명": 120,
            "수량": 60,
            "가격": 90,
            "금액": 110,
            "결과": 65,
            "주문번호": 95,
            "거래환경": 75,
            "메시지": 340,
        }
        for name in trade_columns:
            self.trade_history.heading(name, text=name)
            self.trade_history.column(
                name,
                width=trade_widths[name],
                minwidth=55,
                stretch=name in {"종목명", "메시지"},
                anchor="w" if name in {"일시(초)", "종목명", "메시지"} else "center",
            )
        self.trade_history.tag_configure("BUY", foreground=UI_PINK)
        self.trade_history.tag_configure("SELL", foreground=UI_BLUE)
        self.trade_history.tag_configure("FAILED", foreground=UI_RED)
        self.trade_history.grid(row=0, column=0, sticky="nsew")
        trade_scroll_y = ttk.Scrollbar(
            trade_table_frame,
            orient="vertical",
            command=self.trade_history.yview,
        )
        trade_scroll_y.grid(row=0, column=1, sticky="ns")
        trade_scroll_x = ttk.Scrollbar(
            trade_table_frame,
            orient="horizontal",
            command=self.trade_history.xview,
        )
        trade_scroll_x.grid(row=1, column=0, sticky="ew")
        self.trade_history.configure(
            yscrollcommand=trade_scroll_y.set,
            xscrollcommand=trade_scroll_x.set,
        )
        self._build_market_control_tab()
        self._build_volume_rank_panel()
        self._build_collapsed_side_summary()
        self._build_compact_monitor()
        self.bind("<Configure>", self._enforce_locked_position, add="+")
        self.bind("<Unmap>", self._on_window_unmap, add="+")
        self.bind("<Enter>", lambda _event: self._set_window_hovered(True), add="+")
        self.bind("<Leave>", lambda _event: self._set_window_hovered(False), add="+")
        self.bind("<Control-Shift-O>", lambda _event: self._restore_full_opacity())
        self.bind("<Control-Shift-Q>", lambda _event: self._emergency_stop())
        self._update_clock()

    def _build_compact_monitor(self) -> None:
        self.compact_stock_var = tk.StringVar(value="감시 종목 미선택")
        self.compact_price_var = tk.StringVar(value="현재가 미조회")
        self.compact_trend_var = tk.StringVar(value="─ 보합 0.00%")
        self.compact_assets_var = tk.StringVar(value="추정자산 -")
        self.compact_profit_var = tk.StringVar(value="당일 실현손익 -")
        self.compact_run_state_var = tk.StringVar(value="실시간 감시 OFF")

        panel = ttk.Frame(
            self,
            padding=(14, 10),
            style="Header.TFrame",
        )
        self.compact_panel = panel
        panel.columnconfigure(1, weight=1)

        brand_mark = tk.Canvas(
            panel,
            width=38,
            height=38,
            bg=UI_SURFACE,
            highlightthickness=0,
            bd=0,
        )
        brand_mark.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 12))
        for index, color in enumerate((UI_PURPLE, UI_PINK, UI_ORANGE, UI_YELLOW)):
            brand_mark.create_rectangle(
                index * 9,
                1,
                (index + 1) * 9 + 2,
                37,
                fill=color,
                outline=color,
            )
        brand_mark.create_text(
            19,
            19,
            text="K",
            fill=UI_SURFACE,
            font=(UI_DISPLAY_FONT, 16, "bold"),
        )

        ttk.Label(
            panel,
            textvariable=self.compact_stock_var,
            font=(UI_DISPLAY_FONT, 11, "bold"),
        ).grid(row=0, column=1, sticky="sw")
        ttk.Label(
            panel,
            textvariable=self.compact_price_var,
            font=(UI_DISPLAY_FONT, 17, "bold"),
        ).grid(row=1, column=1, sticky="nw")
        self.compact_trend_badge = ttk.Label(
            panel,
            textvariable=self.compact_trend_var,
            font=(UI_DISPLAY_FONT, 10, "bold"),
            padding=(12, 7),
            anchor="center",
            style="Neutral.Badge.TLabel",
        )
        self.compact_trend_badge.grid(
            row=0,
            column=2,
            rowspan=2,
            sticky="e",
            padx=(18, 10),
        )
        summary = ttk.Frame(panel, style="Header.TFrame")
        summary.grid(row=0, column=3, rowspan=2, sticky="e", padx=(8, 12))
        ttk.Label(
            summary,
            textvariable=self.compact_assets_var,
            font=(UI_DISPLAY_FONT, 9, "bold"),
        ).pack(anchor="e")
        ttk.Label(
            summary,
            textvariable=self.compact_profit_var,
            foreground=UI_MUTED,
        ).pack(anchor="e", pady=(2, 0))
        self.compact_run_state_badge = ttk.Label(
            summary,
            textvariable=self.compact_run_state_var,
            style="Danger.Badge.TLabel",
            padding=(8, 3),
        )
        self.compact_run_state_badge.pack(anchor="e", pady=(3, 0))
        self.restore_mode_button = ttk.Button(
            panel,
            text="□",
            width=3,
            command=self._exit_compact_mode,
            takefocus=True,
        )
        self.restore_mode_button.grid(row=0, column=4, rowspan=2, sticky="e")

    def _normal_layout_widgets(self) -> tuple[tk.Widget, ...]:
        return (
            self.header,
            self.opacity_toolbar,
            self.controls,
            self.main_body,
            self.volume_rank_panel,
            self.side_summary_panel,
        )

    def _enter_compact_mode(self) -> None:
        if self._compact_mode:
            return
        self.update_idletasks()
        self._normal_geometry = self.geometry()
        self._normal_window_state = self.state()
        if self._normal_window_state != "normal":
            self.state("normal")
            self.update_idletasks()

        for widget in self._normal_layout_widgets():
            widget.grid_remove()
        self._compact_mode = True
        try:
            self._compact_previous_topmost = bool(self.attributes("-topmost"))
            self.attributes("-topmost", True)
        except tk.TclError:
            self._compact_previous_topmost = False
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=0, minsize=0)
        self.rowconfigure(0, weight=1)
        self.rowconfigure(1, weight=0)
        self.rowconfigure(2, weight=0)
        self.compact_panel.grid(row=0, column=0, columnspan=2, sticky="nsew")
        self.resizable(False, False)
        self.minsize(760, 112)
        left = max(0, self.winfo_x())
        top = max(0, self.winfo_y())
        self.geometry(f"760x112+{left}+{top}")
        self._update_compact_monitor(self.service)

    def _exit_compact_mode(self) -> None:
        if not self._compact_mode:
            return
        self._compact_mode = False
        self.compact_panel.grid_remove()
        self.columnconfigure(0, weight=1)
        self.columnconfigure(
            1,
            weight=0,
            minsize=(
                300 if self._side_panel_collapsed else EXPANDED_SIDE_PANEL_WIDTH
            ),
        )
        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=0)
        self.rowconfigure(2, weight=1)
        self.header.grid()
        if not self._controls_collapsed:
            self.controls.grid()
        self.main_body.grid()
        if self._side_panel_collapsed:
            self.side_summary_panel.grid()
            self.opacity_toolbar.grid_remove()
            self.volume_rank_panel.grid_remove()
        else:
            self.opacity_toolbar.grid()
            self.volume_rank_panel.grid()
            self.side_summary_panel.grid_remove()
        self.resizable(True, True)
        try:
            self.attributes("-topmost", self._compact_previous_topmost)
        except tk.TclError:
            pass
        self.minsize(
            1240 if self._side_panel_collapsed else EXPANDED_WINDOW_MIN_WIDTH,
            760,
        )
        self.geometry(self._normal_geometry)
        if self._normal_window_state == "zoomed":
            self.after_idle(lambda: self.state("zoomed"))
        self.after_idle(self._draw_main_dmi_chart)

    def _toggle_controls_panel(self) -> None:
        self._controls_collapsed = not self._controls_collapsed
        if self._controls_collapsed:
            self.controls.grid_remove()
            self.controls_toggle_button.configure(text="설정 펼치기")
        else:
            self.controls.grid()
            self.controls_toggle_button.configure(text="설정 접기")
        self.after_idle(self._draw_main_dmi_chart)

    def _update_compact_monitor(self, snapshot) -> None:
        symbol = normalize_symbol(getattr(snapshot, "symbol", ""))
        watch_quotes = getattr(self.service, "watchlist_quotes", {})
        watch_quote = watch_quotes.get(symbol)
        if watch_quote is None:
            watch_quote = next(
                (
                    quote
                    for quote in watch_quotes.values()
                    if normalize_symbol(getattr(quote, "symbol", "")) == symbol
                ),
                None,
            )
        volume_quote = next(
            (
                quote
                for quote in getattr(self.service, "volume_ranking", ())
                if normalize_symbol(getattr(quote, "symbol", "")) == symbol
            ),
            None,
        )
        stock_text, price_text, trend_text, direction = _compact_monitor_display(
            snapshot,
            watch_quote=watch_quote,
            volume_quote=volume_quote,
        )
        self.compact_stock_var.set(stock_text)
        self.compact_price_var.set(price_text)
        self.compact_trend_var.set(trend_text)
        balance = getattr(snapshot, "balance_summary", None)
        assets = float(getattr(balance, "estimated_assets", 0.0) or 0.0)
        performance = getattr(snapshot, "daily_performance", None)
        realized = float(getattr(performance, "realized_profit", 0.0) or 0.0)
        self.compact_assets_var.set(
            f"추정자산 {assets:,.0f}원" if assets else "추정자산 -"
        )
        self.compact_profit_var.set(
            f"당일 실현손익 {realized:+,.0f}원" if performance is not None else "당일 실현손익 -"
        )
        running = bool(getattr(snapshot, "running", False))
        self.compact_run_state_var.set("실시간 감시 ON" if running else "실시간 감시 OFF")
        self.compact_run_state_badge.configure(
            style="Success.Badge.TLabel" if running else "Danger.Badge.TLabel"
        )
        style_name = {
            "up": "Pink.Badge.TLabel",
            "down": "Blue.Badge.TLabel",
            "flat": "Neutral.Badge.TLabel",
        }[direction]
        self.compact_trend_badge.configure(style=style_name)

    def _build_volume_rank_panel(self) -> None:
        panel = ttk.Frame(self, padding=(0, 0, 0, 0))
        self.volume_rank_panel = panel
        panel.grid(
            row=1,
            column=1,
            rowspan=2,
            sticky="nsew",
            padx=(0, 12),
            pady=(0, 12),
        )
        panel.columnconfigure(0, weight=1, uniform="ranking")
        panel.columnconfigure(1, weight=1, uniform="ranking")
        panel.rowconfigure(1, weight=1)

        self.volume_rank_market_var = tk.StringVar(value="000")
        self.volume_rank_status_var = tk.StringVar(
            value="REST API 연결 후 거래량 기준 종목 순위를 불러옵니다."
        )
        self.trade_value_rank_status_var = tk.StringVar(
            value="REST API 연결 후 거래대금 상위 순위를 불러옵니다."
        )
        self.volume_rank_selection_var = tk.StringVar(
            value="종목을 더블클릭하면 현재가·차트·실시간 시세가 연결됩니다."
        )
        self._create_rank_marker_images()

        market_toolbar = ttk.Frame(panel)
        market_toolbar.grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(0, 6),
        )
        for label, value in (("전체", "000"), ("코스피", "001"), ("코스닥", "101")):
            ttk.Radiobutton(
                market_toolbar,
                text=label,
                variable=self.volume_rank_market_var,
                value=value,
                width=7,
                style="Pill.TRadiobutton",
                command=self._request_rankings,
            ).pack(side="left", padx=(0, 3))
        ttk.Label(market_toolbar, text="연동 그룹").pack(side="left", padx=(8, 4))
        rank_group_combo = ttk.Combobox(
            market_toolbar,
            textvariable=self.link_group_var,
            values=tuple(str(value) for value in range(1, 10)),
            width=3,
            state="readonly",
        )
        rank_group_combo.pack(side="left")
        rank_group_combo.bind("<<ComboboxSelected>>", self._save_link_group)

        volume_panel = ttk.LabelFrame(
            panel,
            text="실시간 종목 조회수 · 거래량 기준",
            padding=(8, 8, 8, 8),
        )
        volume_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 3))
        volume_panel.columnconfigure(0, weight=1)
        volume_panel.rowconfigure(2, weight=1)

        heading = ttk.Frame(volume_panel)
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        heading.columnconfigure(1, weight=1)
        self.volume_rank_light = tk.Canvas(
            heading,
            width=16,
            height=16,
            background=UI_SURFACE,
            highlightthickness=0,
            bd=0,
        )
        self.volume_rank_light.grid(row=0, column=0, padx=(0, 6))
        self._volume_rank_light_id = self.volume_rank_light.create_oval(
            2,
            2,
            14,
            14,
            fill="#B8B8B8",
            outline=UI_MUTED,
        )
        ttk.Label(
            heading,
            text="키움 ka10030 · 5초 갱신",
            font=(UI_DISPLAY_FONT, 9, "bold"),
        ).grid(row=0, column=1, sticky="w")
        ttk.Button(
            heading,
            text="새로고침",
            command=self._request_volume_ranking,
        ).grid(row=0, column=2, sticky="e")

        ttk.Label(
            volume_panel,
            textvariable=self.volume_rank_status_var,
            foreground=UI_MUTED,
            wraplength=350,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(0, 6))

        table_frame = ttk.Frame(volume_panel)
        table_frame.grid(row=2, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = (
            "rank",
            "symbol",
            "name",
            "price",
            "rate",
            "volume",
        )
        self.volume_rank_table = ttk.Treeview(
            table_frame,
            columns=columns,
            displaycolumns=(
                "rank",
                "name",
                "price",
                "rate",
                "volume",
            ),
            show="tree headings",
            height=15,
            selectmode="browse",
        )
        self.volume_rank_table.heading("#0", text="")
        self.volume_rank_table.column(
            "#0",
            width=34,
            minwidth=34,
            stretch=False,
            anchor="center",
        )
        column_settings = {
            "rank": ("순위", 34, "center"),
            "symbol": ("종목코드", 0, "center"),
            "name": ("종목명", 82, "w"),
            "price": ("현재가", 60, "e"),
            "rate": ("등락률", 70, "e"),
            "volume": ("거래량", 76, "e"),
        }
        for key, (label, width, anchor) in column_settings.items():
            self.volume_rank_table.heading(key, text=label)
            self.volume_rank_table.column(
                key,
                width=width,
                minwidth=0 if key == "symbol" else 34,
                stretch=key == "name",
                anchor=anchor,
            )
        self.volume_rank_table.tag_configure("up", foreground=UI_PINK)
        self.volume_rank_table.tag_configure("down", foreground=UI_BLUE)
        self.volume_rank_table.tag_configure("flat", foreground=UI_TEXT)
        self.volume_rank_table.grid(row=0, column=0, sticky="nsew")
        rank_scrollbar = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.volume_rank_table.yview,
        )
        rank_scrollbar.grid(row=0, column=1, sticky="ns")
        self.volume_rank_table.configure(yscrollcommand=rank_scrollbar.set)
        self.volume_rank_table.bind("<<TreeviewSelect>>", self._on_volume_rank_selected)
        self.volume_rank_table.bind(
            "<Double-1>",
            lambda _event: self._activate_volume_rank_selection(),
        )
        self.volume_rank_table.bind("<ButtonPress-1>", self._on_volume_drag_start, add="+")
        self.volume_rank_table.bind("<ButtonRelease-1>", self._on_volume_drag_release, add="+")
        self._bind_rank_table_memo_events(self.volume_rank_table)

        trade_panel = ttk.LabelFrame(
            panel,
            text="거래대금 상위 TOP 15",
            padding=(8, 8, 8, 8),
        )
        trade_panel.grid(row=1, column=1, sticky="nsew", padx=(3, 0))
        trade_panel.columnconfigure(0, weight=1)
        trade_panel.rowconfigure(2, weight=1)

        trade_heading = ttk.Frame(trade_panel)
        trade_heading.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        trade_heading.columnconfigure(1, weight=1)
        self.trade_value_rank_light = tk.Canvas(
            trade_heading,
            width=16,
            height=16,
            background=UI_SURFACE,
            highlightthickness=0,
            bd=0,
        )
        self.trade_value_rank_light.grid(row=0, column=0, padx=(0, 6))
        self._trade_value_rank_light_id = self.trade_value_rank_light.create_oval(
            2,
            2,
            14,
            14,
            fill="#B8B8B8",
            outline=UI_MUTED,
        )
        ttk.Label(
            trade_heading,
            text="키움 ka10032 · 30초 갱신",
            font=(UI_DISPLAY_FONT, 9, "bold"),
        ).grid(row=0, column=1, sticky="w")
        ttk.Button(
            trade_heading,
            text="새로고침",
            command=self._request_trade_value_ranking,
        ).grid(row=0, column=2, sticky="e")

        ttk.Label(
            trade_panel,
            textvariable=self.trade_value_rank_status_var,
            foreground=UI_MUTED,
            wraplength=350,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(0, 6))

        trade_table_frame = ttk.Frame(trade_panel)
        trade_table_frame.grid(row=2, column=0, sticky="nsew")
        trade_table_frame.columnconfigure(0, weight=1)
        trade_table_frame.rowconfigure(0, weight=1)
        trade_columns = (
            "rank",
            "symbol",
            "name",
            "price",
            "rate",
            "trade_value",
            "change",
        )
        self.trade_value_rank_table = ttk.Treeview(
            trade_table_frame,
            columns=trade_columns,
            displaycolumns=("name", "price", "rate", "trade_value", "change"),
            show="tree headings",
            height=15,
            selectmode="browse",
        )
        self.trade_value_rank_table.heading("#0", text="")
        self.trade_value_rank_table.column(
            "#0",
            width=34,
            minwidth=34,
            stretch=False,
            anchor="center",
        )
        trade_column_settings = {
            "rank": ("순위", 0, "center"),
            "symbol": ("종목코드", 0, "center"),
            "name": ("종목명", 72, "w"),
            "price": ("현재가", 54, "e"),
            "rate": ("등락률", 64, "e"),
            "trade_value": ("거래대금\n(백억)", 68, "e"),
            "change": ("전일비", 58, "e"),
        }
        for key, (label, width, anchor) in trade_column_settings.items():
            self.trade_value_rank_table.heading(key, text=label)
            self.trade_value_rank_table.column(
                key,
                width=width,
                minwidth=0 if key in {"rank", "symbol"} else 34,
                stretch=key == "name",
                anchor=anchor,
            )
        self.trade_value_rank_table.tag_configure("up", foreground=UI_PINK)
        self.trade_value_rank_table.tag_configure("down", foreground=UI_BLUE)
        self.trade_value_rank_table.tag_configure("flat", foreground=UI_TEXT)
        self.trade_value_rank_table.grid(row=0, column=0, sticky="nsew")
        trade_scrollbar = ttk.Scrollbar(
            trade_table_frame,
            orient="vertical",
            command=self.trade_value_rank_table.yview,
        )
        trade_scrollbar.grid(row=0, column=1, sticky="ns")
        self.trade_value_rank_table.configure(yscrollcommand=trade_scrollbar.set)
        self.trade_value_rank_table.bind(
            "<<TreeviewSelect>>",
            self._on_trade_value_rank_selected,
        )
        self.trade_value_rank_table.bind(
            "<Double-1>",
            lambda _event: self._activate_trade_value_rank_selection(),
        )
        self.trade_value_rank_table.bind(
            "<ButtonPress-1>",
            self._on_trade_value_drag_start,
            add="+",
        )
        self.trade_value_rank_table.bind(
            "<ButtonRelease-1>",
            self._on_trade_value_drag_release,
            add="+",
        )
        self._bind_rank_table_memo_events(self.trade_value_rank_table)

        ttk.Label(
            panel,
            textvariable=self.volume_rank_selection_var,
            wraplength=720,
            justify="left",
            foreground=UI_BLUE,
        ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 6))
        ttk.Button(
            panel,
            text="선택 종목 연결",
            command=self._activate_selected_rank,
            style="Accent.TButton",
        ).grid(row=3, column=0, columnspan=2, sticky="ew")

    def _create_rank_marker_images(self) -> None:
        if self._rank_marker_images:
            return
        for key, has_memo, has_nxt in (
            ("blank", False, False),
            ("memo", True, False),
            ("nxt", False, True),
            ("memo_nxt", True, True),
        ):
            image = tk.PhotoImage(master=self, width=14, height=18)
            if has_memo:
                for y in range(5):
                    for x in range(9 + y, 14):
                        image.put(UI_GREEN, to=(x, y, x + 1, y + 1))
            if has_nxt:
                for y in range(13, 18):
                    width = y - 12
                    for x in range(14 - width, 14):
                        image.put(UI_RED, to=(x, y, x + 1, y + 1))
            self._rank_marker_images[key] = image

    def _build_collapsed_side_summary(self) -> None:
        self.side_evaluation_var = tk.StringVar(value="평가금액 -")
        self.side_profit_var = tk.StringVar(value="평가손익 -")
        self.side_profit_rate_var = tk.StringVar(value="수익률 -")
        self.side_account_var = tk.StringVar(value="계좌 미연결")
        panel = ttk.LabelFrame(
            self,
            text="평가 손익 · 포지션",
            padding=(10, 10, 10, 10),
        )
        self.side_summary_panel = panel
        panel.grid(
            row=0,
            column=1,
            rowspan=3,
            sticky="nsew",
            padx=(0, 12),
            pady=(8, 12),
        )
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(5, weight=1)

        heading = ttk.Frame(panel)
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        heading.columnconfigure(0, weight=1)
        ttk.Label(
            heading,
            text="접힌 화면 요약",
            font=(UI_DISPLAY_FONT, 10, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            heading,
            text=">>",
            width=4,
            command=self._toggle_side_panel,
        ).grid(row=0, column=1, sticky="e")
        ttk.Label(panel, textvariable=self.side_account_var, style="Muted.TLabel").grid(
            row=1,
            column=0,
            sticky="w",
            pady=(0, 8),
        )
        ttk.Label(
            panel,
            textvariable=self.side_evaluation_var,
            font=(UI_DISPLAY_FONT, 11, "bold"),
        ).grid(row=2, column=0, sticky="ew", pady=3)
        self.side_profit_badge = ttk.Label(
            panel,
            textvariable=self.side_profit_var,
            padding=(10, 6),
            style="Neutral.Badge.TLabel",
        )
        self.side_profit_badge.grid(row=3, column=0, sticky="ew", pady=3)
        ttk.Label(panel, textvariable=self.side_profit_rate_var).grid(
            row=4,
            column=0,
            sticky="w",
            pady=(3, 10),
        )

        table_frame = ttk.Frame(panel)
        table_frame.grid(row=5, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("name", "quantity", "profit")
        self.side_position_table = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=12,
        )
        for key, label, width, anchor in (
            ("name", "종목", 92, "w"),
            ("quantity", "보유", 52, "e"),
            ("profit", "손익", 78, "e"),
        ):
            self.side_position_table.heading(key, text=label)
            self.side_position_table.column(
                key,
                width=width,
                minwidth=42,
                anchor=anchor,
                stretch=key == "name",
            )
        self.side_position_table.tag_configure("profit", foreground=UI_PINK)
        self.side_position_table.tag_configure("loss", foreground=UI_BLUE)
        self.side_position_table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.side_position_table.yview,
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.side_position_table.configure(yscrollcommand=scrollbar.set)
        self.side_summary_panel.grid_remove()

    def _toggle_side_panel(self) -> None:
        if self._compact_mode:
            return
        self.update_idletasks()
        current_width = max(self.winfo_width(), 1)
        self._side_panel_collapsed = not self._side_panel_collapsed
        if self._side_panel_collapsed:
            self.opacity_toolbar.grid_remove()
            self.volume_rank_panel.grid_remove()
            self.side_summary_panel.grid()
            self.columnconfigure(1, weight=0, minsize=300)
            self.side_panel_button.configure(text=">>")
            self.minsize(1240, 760)
            target_width = max(1240, current_width - 460)
        else:
            self.side_summary_panel.grid_remove()
            self.opacity_toolbar.grid()
            self.volume_rank_panel.grid()
            self.columnconfigure(1, weight=0, minsize=EXPANDED_SIDE_PANEL_WIDTH)
            self.side_panel_button.configure(text="<<")
            self.minsize(EXPANDED_WINDOW_MIN_WIDTH, 760)
            target_width = max(EXPANDED_WINDOW_MIN_WIDTH, current_width + 460)
        if self.state() == "normal":
            self.geometry(f"{target_width}x{max(self.winfo_height(), 760)}")
        self.after_idle(self._draw_main_dmi_chart)

    def _update_side_summary(self, snapshot) -> None:
        balance = snapshot.balance_summary
        account = self._account_for_api() or self._selected_account_full
        self.side_account_var.set(
            f"계좌 {self._privacy_account_label(account)}"
            if account
            else "계좌 미연결"
        )
        for item in self.side_position_table.get_children():
            self.side_position_table.delete(item)
        if balance is None:
            self.side_evaluation_var.set("평가금액 -")
            self.side_profit_var.set("평가손익 -")
            self.side_profit_rate_var.set("수익률 -")
            self.side_profit_badge.configure(style="Neutral.Badge.TLabel")
            return

        self.side_evaluation_var.set(f"평가금액 {balance.total_evaluation:,.0f}원")
        self.side_profit_var.set(f"평가손익 {balance.total_profit_loss:+,.0f}원")
        self.side_profit_rate_var.set(f"수익률 {balance.total_profit_rate:+.2f}%")
        if balance.total_profit_loss > 0:
            self.side_profit_badge.configure(style="Pink.Badge.TLabel")
        elif balance.total_profit_loss < 0:
            self.side_profit_badge.configure(style="Blue.Badge.TLabel")
        else:
            self.side_profit_badge.configure(style="Neutral.Badge.TLabel")
        for index, holding in enumerate(balance.holdings):
            tag = "profit" if holding.profit_loss > 0 else "loss" if holding.profit_loss < 0 else ""
            self.side_position_table.insert(
                "",
                "end",
                iid=f"side-position-{index}",
                values=(
                    holding.name or holding.symbol,
                    f"{holding.quantity:,}주",
                    f"{holding.profit_loss:+,.0f}",
                ),
                tags=(tag,) if tag else (),
            )

    def _field(self, parent: ttk.Frame, label: str, variable: tk.StringVar, column: int) -> None:
        ttk.Label(parent, text=label).grid(row=0, column=column, sticky="w")
        ttk.Entry(parent, textvariable=variable, width=14).grid(
            row=1, column=column, sticky="ew", padx=(0, 8)
        )

    def _request_rankings(self) -> None:
        self._request_volume_ranking()
        self._request_trade_value_ranking()

    def _request_volume_ranking(self) -> None:
        if self.service.connection_mode != "REST" or not self.service.account_info.connected:
            self.service.volume_ranking = []
            self.volume_rank_status_var.set(
                "키움 REST API 연결 후 실시간 거래량 순위를 불러올 수 있습니다."
            )
            self.volume_rank_selection_var.set(
                "REST API 연결 버튼으로 계좌를 먼저 연결해 주세요."
            )
            self._set_volume_rank_light("off")
            self._render_volume_rank_rows([])
            return

        self.volume_rank_status_var.set("키움 거래량 순위를 불러오는 중입니다...")
        self.update_idletasks()
        rows = self.service.refresh_volume_ranking(
            market=self.volume_rank_market_var.get(),
            limit=15,
        )
        self._render_volume_rank_rows(rows)
        self.volume_rank_status_var.set(self.service.volume_ranking_message)
        if rows and "ka10030" in self.service.volume_ranking_message:
            self._set_volume_rank_light("on")
            self.volume_rank_selection_var.set(
                "5초마다 자동 갱신합니다. 종목을 더블클릭하면 메인 화면에 연결됩니다."
            )
        elif rows:
            self._set_volume_rank_light("warning")
            self.volume_rank_selection_var.set(
                "마지막 정상 순위를 표시 중입니다. 연결 상태를 확인해 주세요."
            )
        else:
            self._set_volume_rank_light("error")
            self.volume_rank_selection_var.set(
                "표시할 거래량 순위가 없습니다. 시장과 REST API 연결을 확인해 주세요."
            )

    def _schedule_volume_rank_refresh(self) -> None:
        if self._volume_rank_after_id is None:
            self._volume_rank_after_id = self.after(
                VOLUME_RANK_REFRESH_MILLISECONDS,
                self._volume_rank_refresh_tick,
            )

    def _volume_rank_refresh_tick(self) -> None:
        self._volume_rank_after_id = None
        self._request_volume_ranking()
        self._schedule_volume_rank_refresh()

    def _request_trade_value_ranking(self) -> None:
        if self.service.connection_mode != "REST" or not self.service.account_info.connected:
            self.service.trade_value_ranking = []
            self.trade_value_rank_status_var.set(
                "키움 REST API 연결 후 거래대금 상위 순위를 불러올 수 있습니다."
            )
            self._set_trade_value_rank_light("off")
            self._render_trade_value_rank_rows([])
            return

        self.trade_value_rank_status_var.set(
            "키움 거래대금 상위 순위를 불러오는 중입니다..."
        )
        self.update_idletasks()
        rows = self.service.refresh_trade_value_ranking(
            market=self.volume_rank_market_var.get(),
            limit=15,
        )
        self._render_trade_value_rank_rows(rows)
        self.trade_value_rank_status_var.set(
            self.service.trade_value_ranking_message
        )
        if rows and "ka10032" in self.service.trade_value_ranking_message:
            self._set_trade_value_rank_light("on")
        elif rows:
            self._set_trade_value_rank_light("warning")
        else:
            self._set_trade_value_rank_light("error")

    def _schedule_trade_value_rank_refresh(self) -> None:
        if self._trade_value_rank_after_id is None:
            self._trade_value_rank_after_id = self.after(
                TRADE_VALUE_RANK_REFRESH_MILLISECONDS,
                self._trade_value_rank_refresh_tick,
            )

    def _trade_value_rank_refresh_tick(self) -> None:
        self._trade_value_rank_after_id = None
        self._request_trade_value_ranking()
        self._schedule_trade_value_rank_refresh()

    def _render_volume_rank_rows(self, rows: list[VolumeRankQuote]) -> None:
        selected_symbol = self._selected_volume_rank_symbol()
        for item in self.volume_rank_table.get_children():
            self.volume_rank_table.delete(item)
        selected_item = ""
        for quote in rows[:15]:
            item_id = f"{quote.rank:02d}:{quote.symbol}"
            self.volume_rank_table.insert(
                "",
                "end",
                iid=item_id,
                image=self._rank_marker_image(quote),
                values=self._volume_rank_values(quote),
                tags=(self._volume_rank_tag(quote),),
            )
            if quote.symbol == selected_symbol:
                selected_item = item_id
        if selected_item:
            self.volume_rank_table.selection_set(selected_item)
            self.volume_rank_table.focus(selected_item)
            self.volume_rank_table.see(selected_item)

    @staticmethod
    def _volume_rank_values(quote: VolumeRankQuote) -> tuple:
        return (
            quote.rank,
            quote.symbol,
            quote.name or "-",
            f"{quote.current_price:,.0f}",
            TraderApp._rank_rate_text(quote),
            f"{quote.volume:,}",
        )

    @staticmethod
    def _volume_rank_tag(quote: VolumeRankQuote) -> str:
        marker = TraderApp._rank_change_marker(quote)
        if marker in {"▲", "↑"}:
            return "up"
        if marker in {"▼", "↓"}:
            return "down"
        return "flat"

    @staticmethod
    def _rank_change_marker(quote: VolumeRankQuote) -> str:
        sign = str(quote.change_sign or "").strip()
        if sign == "1":
            return "↑"
        if sign == "2":
            return "▲"
        if sign == "4":
            return "↓"
        if sign == "5":
            return "▼"
        if quote.change_rate > 0:
            return "▲"
        if quote.change_rate < 0:
            return "▼"
        return "-"

    @staticmethod
    def _rank_rate_text(quote: VolumeRankQuote) -> str:
        marker = TraderApp._rank_change_marker(quote)
        return f"{marker} {abs(quote.change_rate):.2f}%"

    def _rank_marker_image(self, quote: VolumeRankQuote) -> tk.PhotoImage:
        has_memo = bool(self.service.storage.watchlist_memo(quote.symbol))
        if has_memo and quote.nxt_available:
            key = "memo_nxt"
        elif has_memo:
            key = "memo"
        elif quote.nxt_available:
            key = "nxt"
        else:
            key = "blank"
        return self._rank_marker_images[key]

    def _render_trade_value_rank_rows(self, rows: list[VolumeRankQuote]) -> None:
        selected_symbol = self._selected_trade_value_rank_symbol()
        for item in self.trade_value_rank_table.get_children():
            self.trade_value_rank_table.delete(item)
        selected_item = ""
        for quote in rows[:15]:
            item_id = f"{quote.rank:02d}:{quote.symbol}"
            self.trade_value_rank_table.insert(
                "",
                "end",
                iid=item_id,
                image=self._rank_marker_image(quote),
                values=self._trade_value_rank_values(quote),
                tags=(self._volume_rank_tag(quote),),
            )
            if quote.symbol == selected_symbol:
                selected_item = item_id
        if selected_item:
            self.trade_value_rank_table.selection_set(selected_item)
            self.trade_value_rank_table.focus(selected_item)
            self.trade_value_rank_table.see(selected_item)

    @staticmethod
    def _trade_value_rank_values(quote: VolumeRankQuote) -> tuple:
        return (
            quote.rank,
            quote.symbol,
            quote.name or "-",
            f"{quote.current_price:,.0f}",
            TraderApp._rank_rate_text(quote),
            _format_hundred_eok_won(quote.trade_value),
            f"{quote.change:+,.0f}",
        )

    def _set_volume_rank_light(self, state: str) -> None:
        self._set_rank_light(
            self.volume_rank_light,
            self._volume_rank_light_id,
            state,
        )

    def _set_trade_value_rank_light(self, state: str) -> None:
        self._set_rank_light(
            self.trade_value_rank_light,
            self._trade_value_rank_light_id,
            state,
        )

    @staticmethod
    def _set_rank_light(canvas: tk.Canvas, item_id: int, state: str) -> None:
        colors = {
            "on": (UI_GREEN, "#0F6D31"),
            "warning": (UI_ORANGE, "#C05C24"),
            "error": (UI_RED, "#C8384A"),
            "off": ("#B8B8B8", UI_MUTED),
        }
        fill, outline = colors.get(state, colors["off"])
        canvas.itemconfigure(
            item_id,
            fill=fill,
            outline=outline,
        )

    def _selected_volume_rank_symbol(self) -> str:
        selected = self.volume_rank_table.selection()
        if not selected:
            return ""
        values = self.volume_rank_table.item(selected[0], "values")
        return normalize_symbol(values[1]) if len(values) > 1 else ""

    def _volume_rank_quote(self, symbol: str) -> VolumeRankQuote | None:
        normalized = normalize_symbol(symbol)
        return next(
            (quote for quote in self.service.volume_ranking if quote.symbol == normalized),
            None,
        )

    def _selected_trade_value_rank_symbol(self) -> str:
        selected = self.trade_value_rank_table.selection()
        if not selected:
            return ""
        values = self.trade_value_rank_table.item(selected[0], "values")
        return normalize_symbol(values[1]) if len(values) > 1 else ""

    def _trade_value_rank_quote(self, symbol: str) -> VolumeRankQuote | None:
        normalized = normalize_symbol(symbol)
        return next(
            (
                quote
                for quote in self.service.trade_value_ranking
                if quote.symbol == normalized
            ),
            None,
        )

    def _rank_quote_for_table(
        self,
        table: ttk.Treeview,
        symbol: str,
    ) -> VolumeRankQuote | None:
        if table is self.trade_value_rank_table:
            return self._trade_value_rank_quote(symbol)
        return self._volume_rank_quote(symbol)

    @staticmethod
    def _rank_table_symbol(table: ttk.Treeview, item: str) -> str:
        values = table.item(item, "values")
        return normalize_symbol(values[1]) if len(values) > 1 else ""

    def _bind_rank_table_memo_events(self, table: ttk.Treeview) -> None:
        table.bind(
            "<Motion>",
            lambda event, target=table: self._on_rank_pointer_motion(target, event),
            add="+",
        )
        table.bind(
            "<Leave>",
            lambda _event: self._cancel_rank_tooltip(),
            add="+",
        )
        table.bind(
            "<MouseWheel>",
            lambda _event: self._cancel_rank_tooltip(),
            add="+",
        )
        table.bind(
            "<Button-3>",
            lambda event, target=table: self._edit_rank_memo(target, event),
            add="+",
        )

    def _on_rank_pointer_motion(
        self,
        table: ttk.Treeview,
        event: tk.Event,
    ) -> None:
        if table.identify_region(event.x, event.y) != "cell":
            self._cancel_rank_tooltip()
            return
        item = table.identify_row(event.y)
        symbol = self._rank_table_symbol(table, item) if item else ""
        if not symbol or not self.service.storage.watchlist_memo(symbol):
            self._cancel_rank_tooltip()
            return
        if table is self._rank_hover_table and item == self._rank_hover_item:
            return
        self._cancel_rank_tooltip()
        self._rank_hover_table = table
        self._rank_hover_item = item
        self._rank_tooltip_after_id = self.after(
            2_000,
            lambda target=table, row=item: self._show_rank_tooltip(target, row),
        )

    def _show_rank_tooltip(self, table: ttk.Treeview, item: str) -> None:
        self._rank_tooltip_after_id = None
        if (
            table is not self._rank_hover_table
            or item != self._rank_hover_item
            or not table.winfo_exists()
            or not table.exists(item)
        ):
            return
        pointer_x, pointer_y = table.winfo_pointerxy()
        local_y = pointer_y - table.winfo_rooty()
        if table.identify_row(local_y) != item:
            return
        symbol = self._rank_table_symbol(table, item)
        memo = self.service.storage.watchlist_memo(symbol)
        if not memo:
            return
        quote = self._rank_quote_for_table(table, symbol)
        tooltip = tk.Toplevel(table)
        self._rank_tooltip_window = tooltip
        tooltip.overrideredirect(True)
        try:
            tooltip.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        frame = tk.Frame(
            tooltip,
            background=UI_TEXT,
            highlightbackground=UI_TEXT,
            highlightthickness=1,
            padx=10,
            pady=8,
        )
        frame.pack(fill="both", expand=True)
        tk.Label(
            frame,
            text=f"{symbol}  {(quote.name if quote else '') or '종목명 미조회'}",
            background=UI_TEXT,
            foreground=UI_SURFACE,
            font=(UI_DISPLAY_FONT, 9, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            frame,
            text=memo,
            background=UI_TEXT,
            foreground="#E8E8E8",
            font=(UI_FONT, 9),
            anchor="w",
            justify="left",
            wraplength=320,
        ).pack(fill="x", pady=(4, 0))
        tooltip.update_idletasks()
        left = min(
            pointer_x + 14,
            tooltip.winfo_screenwidth() - tooltip.winfo_reqwidth() - 8,
        )
        top = min(
            pointer_y + 18,
            tooltip.winfo_screenheight() - tooltip.winfo_reqheight() - 8,
        )
        tooltip.geometry(f"+{max(0, left)}+{max(0, top)}")

    def _cancel_rank_tooltip(self) -> None:
        if self._rank_tooltip_after_id is not None:
            try:
                self.after_cancel(self._rank_tooltip_after_id)
            except tk.TclError:
                pass
            self._rank_tooltip_after_id = None
        if (
            self._rank_tooltip_window is not None
            and self._rank_tooltip_window.winfo_exists()
        ):
            self._rank_tooltip_window.destroy()
        self._rank_tooltip_window = None
        self._rank_hover_table = None
        self._rank_hover_item = ""

    def _edit_rank_memo(
        self,
        table: ttk.Treeview,
        event: tk.Event,
    ) -> str | None:
        item = table.identify_row(event.y)
        symbol = self._rank_table_symbol(table, item) if item else ""
        if not symbol:
            return None
        self._cancel_rank_tooltip()
        table.selection_set(item)
        table.focus(item)
        quote = self._rank_quote_for_table(table, symbol)
        name = quote.name if quote is not None else ""
        self.service.storage.add_watchlist_symbol(symbol, name)
        dialog = WatchlistMemoDialog(
            self,
            symbol,
            name,
            self.service.storage.watchlist_memo(symbol),
        )
        if dialog.result is None:
            return "break"
        self.service.storage.set_watchlist_memo(symbol, dialog.result)
        self._write_watchlist_auto_snapshot()
        self._render_volume_rank_rows(self.service.volume_ranking)
        self._render_trade_value_rank_rows(self.service.trade_value_ranking)
        action = "삭제" if not dialog.result else "저장"
        self.volume_rank_selection_var.set(
            f"{symbol} {name} 메모를 {action}했습니다."
        )
        return "break"

    def _save_link_group(self, _event: tk.Event | None = None) -> None:
        value = self.link_group_var.get().strip()
        if value not in {str(number) for number in range(1, 10)}:
            value = "1"
            self.link_group_var.set(value)
        self.service.storage.set_app_setting("symbols.link_group", value)
        self.volume_rank_selection_var.set(
            f"연동 그룹 {value}번 · 종목을 더블클릭하거나 메인 화면으로 끌어 놓으세요."
        )

    def _on_volume_drag_start(self, event: tk.Event) -> None:
        row = self.volume_rank_table.identify_row(event.y)
        self._volume_drag_symbol = ""
        self._volume_drag_start_y = int(event.y_root)
        if row:
            values = self.volume_rank_table.item(row, "values")
            if len(values) > 1:
                self._volume_drag_symbol = normalize_symbol(values[1])

    def _on_volume_drag_release(self, event: tk.Event) -> None:
        moved = abs(int(event.y_root) - self._volume_drag_start_y) >= 12
        symbol = self._volume_drag_symbol
        self._volume_drag_symbol = ""
        if not moved or not symbol:
            return
        quote = self._volume_rank_quote(symbol)
        if quote is None:
            return
        if self._connect_symbol_to_main(symbol, quote.name):
            self.volume_rank_selection_var.set(
                f"연동 그룹 {self.link_group_var.get()}번으로 {symbol} {quote.name}를 연결했습니다."
            )

    def _on_volume_rank_selected(self, _event: tk.Event | None = None) -> None:
        quote = self._volume_rank_quote(self._selected_volume_rank_symbol())
        if quote is None:
            return
        self._active_rank_source = "volume"
        self.volume_rank_selection_var.set(
            f"선택 {quote.rank}위 · {quote.symbol} {quote.name} · "
            f"현재가 {quote.current_price:,.0f}원 · 거래량 {quote.volume:,}주"
        )

    def _on_trade_value_drag_start(self, event: tk.Event) -> None:
        row = self.trade_value_rank_table.identify_row(event.y)
        self._trade_value_drag_symbol = ""
        self._trade_value_drag_start_y = int(event.y_root)
        if row:
            self._trade_value_drag_symbol = self._rank_table_symbol(
                self.trade_value_rank_table,
                row,
            )

    def _on_trade_value_drag_release(self, event: tk.Event) -> None:
        moved = abs(int(event.y_root) - self._trade_value_drag_start_y) >= 12
        symbol = self._trade_value_drag_symbol
        self._trade_value_drag_symbol = ""
        if not moved or not symbol:
            return
        quote = self._trade_value_rank_quote(symbol)
        if quote is None:
            return
        if self._connect_symbol_to_main(symbol, quote.name):
            self.volume_rank_selection_var.set(
                f"연동 그룹 {self.link_group_var.get()}번으로 "
                f"{symbol} {quote.name}를 연결했습니다."
            )

    def _on_trade_value_rank_selected(
        self,
        _event: tk.Event | None = None,
    ) -> None:
        quote = self._trade_value_rank_quote(
            self._selected_trade_value_rank_symbol()
        )
        if quote is None:
            return
        self._active_rank_source = "trade_value"
        self.volume_rank_selection_var.set(
            f"거래대금 {quote.rank}위 · {quote.symbol} {quote.name} · "
            f"현재가 {quote.current_price:,.0f}원 · "
            f"거래대금 {_format_hundred_eok_won(quote.trade_value)}백억원"
        )

    def _activate_selected_rank(self) -> None:
        if self._active_rank_source == "trade_value":
            self._activate_trade_value_rank_selection()
        else:
            self._activate_volume_rank_selection()

    def _activate_volume_rank_selection(self) -> None:
        symbol = self._selected_volume_rank_symbol()
        quote = self._volume_rank_quote(symbol)
        if quote is None:
            self.volume_rank_selection_var.set("연결할 거래량 순위 종목을 먼저 선택해 주세요.")
            return
        if not self._connect_symbol_to_main(symbol, quote.name):
            self.volume_rank_selection_var.set("키움 API 계좌 연결 후 종목을 연결할 수 있습니다.")
            return
        self.volume_rank_selection_var.set(
            f"{symbol} {self.service.symbol_name} 현재가·실시간 시세·차트를 연결했습니다."
        )

    def _activate_trade_value_rank_selection(self) -> None:
        symbol = self._selected_trade_value_rank_symbol()
        quote = self._trade_value_rank_quote(symbol)
        if quote is None:
            self.volume_rank_selection_var.set(
                "연결할 거래대금 순위 종목을 먼저 선택해 주세요."
            )
            return
        if not self._connect_symbol_to_main(symbol, quote.name):
            self.volume_rank_selection_var.set(
                "키움 API 계좌 연결 후 종목을 연결할 수 있습니다."
            )
            return
        self.volume_rank_selection_var.set(
            f"{symbol} {self.service.symbol_name} 현재가·실시간 시세·차트를 연결했습니다."
        )

    def _selected_trading_baseline(self) -> TradingBaseline | None:
        baseline = self._trading_baseline
        if baseline is None or baseline.symbol != normalize_symbol(self.symbol_var.get()):
            return None
        return baseline

    def _operating_capital(self) -> float:
        baseline = self._selected_trading_baseline()
        return (
            baseline.capital_limit
            if baseline is not None
            else _parse_money_input(self.capital_var.get())
        )

    def _load_trading_baseline(self, symbol: str) -> None:
        normalized = normalize_symbol(symbol)
        previous = self._trading_baseline
        baseline = (
            self.service.storage.trading_baseline(normalized)
            if normalized and normalized != "000000"
            else None
        )
        if baseline is None and previous is not None and previous.symbol != normalized:
            self.capital_var.set("1000000")
        self._apply_trading_baseline(baseline)

    def _apply_trading_baseline(self, baseline: TradingBaseline | None) -> None:
        self._trading_baseline = baseline
        if baseline is None:
            self.capital_entry.configure(state="normal")
            self.baseline_status_var.set("미설정")
            self.baseline_set_button.configure(state="normal")
            self.baseline_reset_button.configure(state="disabled")
            return
        self.capital_var.set(f"{baseline.capital_limit:.0f}")
        self.capital_entry.configure(state="readonly")
        self.baseline_status_var.set(
            f"{baseline.set_at[:10]} / 기준 {baseline.reference_price:,.0f}원"
        )
        self.baseline_set_button.configure(state="disabled")
        self.baseline_reset_button.configure(state="normal")

    def _set_trading_baseline(self) -> None:
        if not self._require_live_connection():
            return
        if not self._selected_symbol_ready():
            messagebox.showwarning("종목 세팅 필요", "6자리 종목번호와 회사명을 먼저 세팅해 주세요.")
            return
        if not self._account_connection_confirmed(self.service.account_info):
            messagebox.showwarning("계좌 연결 필요", "계좌 연결과 잔고 확인을 먼저 완료해 주세요.")
            return
        balance = self.service.balance_summary
        if balance is None:
            messagebox.showwarning(
                "계좌 잔고 필요",
                "'계좌잔고 불러오기'로 주문가능금액을 먼저 확인해 주세요.",
            )
            return

        current_price = self._selected_current_price()
        if current_price <= 0:
            self.service.request_current_price(self.symbol_var.get())
            current_price = self._selected_current_price()
        available_funds = (
            balance.orderable_amount if balance.orderable_amount > 0 else balance.deposit
        )
        capital_limit = _parse_money_input(self.capital_var.get())
        validation_message = _baseline_validation_message(
            capital_limit,
            current_price,
            available_funds,
        )
        if validation_message:
            messagebox.showwarning("금액 세팅 확인", validation_message)
            self._refresh()
            return

        baseline = TradingBaseline(
            symbol=normalize_symbol(self.symbol_var.get()),
            capital_limit=capital_limit,
            reference_price=current_price,
            set_at=datetime.now().isoformat(timespec="seconds"),
        )
        self.service.storage.save_trading_baseline(baseline)
        self._apply_trading_baseline(baseline)
        self.service.configure(baseline.symbol, baseline.capital_limit, self._settings())
        self.service.storage.log(
            "INFO",
            "금액세팅",
            f"{baseline.symbol} 운용 한도 {baseline.capital_limit:,.0f}원과 "
            f"기준가 {baseline.reference_price:,.0f}원을 {baseline.set_at[:10]}에 고정했습니다.",
        )
        self._refresh()

    def _reset_trading_baseline(self) -> None:
        baseline = self._selected_trading_baseline()
        if baseline is None:
            return
        if self.service.running:
            self.service.stop()
        self._clear_real_order_authorization(
            "고정 운용금액 리세팅으로 실거래 세션 승인을 해제했습니다."
        )
        self.service.storage.remove_trading_baseline(baseline.symbol)
        self._apply_trading_baseline(None)
        self.service.configure(baseline.symbol, self._operating_capital(), self._settings())
        self.service.storage.log(
            "WARN",
            "금액세팅",
            f"{baseline.symbol}의 고정 운용금액과 기준가를 리세팅했습니다.",
        )
        self._refresh()

    def _build_market_control_tab(self) -> None:
        tab = ttk.Frame(self.main_notebook, padding=(10, 10, 10, 8))
        self.market_control_tab = tab
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(1, weight=1)
        self.main_notebook.add(tab, text="호가·미체결·위험관리")

        summary = ttk.Frame(tab)
        summary.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        summary.columnconfigure(2, weight=1)
        self.midpoint_status_var = tk.StringVar(value="중간가: 호가 조회 전")
        self.daily_performance_var = tk.StringVar(value="오늘 성과: 조회 전")
        self.monthly_performance_var = tk.StringVar(value="이번 달 성과: 조회 전")
        self.daily_risk_status_var = tk.StringVar(value="일일 손실 차단: 대기")
        ttk.Button(
            summary,
            text="10호가·미체결 새로고침",
            command=self._request_market_control_data,
        ).grid(row=0, column=0, rowspan=2, sticky="w")
        ttk.Label(
            summary,
            textvariable=self.midpoint_status_var,
            foreground=UI_BLUE,
        ).grid(row=0, column=1, sticky="w", padx=(10, 16))
        ttk.Label(summary, textvariable=self.daily_performance_var).grid(
            row=0, column=2, sticky="w"
        )
        ttk.Label(summary, textvariable=self.monthly_performance_var).grid(
            row=1, column=2, sticky="w", pady=(4, 0)
        )

        risk_row = ttk.Frame(summary)
        risk_row.grid(row=0, column=3, rowspan=2, sticky="e")
        ttk.Label(risk_row, text="일일 최대 손실률 -").pack(side="left")
        ttk.Entry(
            risk_row,
            textvariable=self.daily_loss_limit_var,
            width=6,
            justify="center",
        ).pack(side="left", padx=(4, 2))
        ttk.Label(risk_row, text="%").pack(side="left")
        ttk.Button(
            risk_row,
            text="손실 한도 설정",
            command=self._set_daily_loss_limit,
        ).pack(side="left", padx=(6, 8))
        self.daily_risk_badge = ttk.Label(
            risk_row,
            textvariable=self.daily_risk_status_var,
            style="Neutral.Badge.TLabel",
            padding=(8, 4),
        )
        self.daily_risk_badge.pack(side="left", padx=(8, 0))

        book_frame = ttk.LabelFrame(tab, text="현재가 기준 10호가", padding=8)
        book_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 4))
        book_frame.columnconfigure(0, weight=1)
        book_frame.rowconfigure(0, weight=1)
        book_columns = ("단계", "매도잔량", "매도호가", "매수호가", "매수잔량")
        self.order_book_table = ttk.Treeview(
            book_frame,
            columns=book_columns,
            show="headings",
            height=10,
        )
        for name in book_columns:
            self.order_book_table.heading(name, text=name)
            self.order_book_table.column(
                name,
                width=92 if name != "단계" else 52,
                anchor="center",
            )
        self.order_book_table.tag_configure("best", background="#FFF2F7")
        self.order_book_table.grid(row=0, column=0, sticky="nsew")

        unfilled_frame = ttk.LabelFrame(tab, text="실시간 미체결 주문", padding=8)
        unfilled_frame.grid(row=1, column=1, sticky="nsew", padx=(4, 0))
        unfilled_frame.columnconfigure(0, weight=1)
        unfilled_frame.rowconfigure(0, weight=1)
        unfilled_columns = (
            "원주문번호",
            "시간",
            "종목",
            "구분",
            "주문수량",
            "미체결",
            "주문가",
            "상태",
        )
        self.unfilled_table = ttk.Treeview(
            unfilled_frame,
            columns=unfilled_columns,
            show="headings",
            height=10,
        )
        for name in unfilled_columns:
            self.unfilled_table.heading(name, text=name)
            self.unfilled_table.column(
                name,
                width=100 if name == "원주문번호" else 72,
                anchor="center",
                stretch=name in {"원주문번호", "종목"},
            )
        self.unfilled_table.tag_configure("BUY", foreground=UI_PINK)
        self.unfilled_table.tag_configure("SELL", foreground=UI_BLUE)
        self.unfilled_table.grid(row=0, column=0, sticky="nsew")
        self.unfilled_table.bind("<<TreeviewSelect>>", self._on_unfilled_selected)

        tools_frame = ttk.Frame(tab)
        tools_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        tools_frame.columnconfigure(0, weight=1)
        tools_frame.columnconfigure(1, weight=1)
        calculator = ttk.LabelFrame(tools_frame, text="당일 손익률 계산기", padding=8)
        calculator.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.return_assets_var = tk.StringVar(value="")
        self.return_profit_var = tk.StringVar(value="")
        self.return_sign_var = tk.StringVar(value="+")
        self.return_result_var = tk.StringVar(value="당일 손익률: -")
        ttk.Label(calculator, text="추정자산").pack(side="left")
        ttk.Entry(calculator, textvariable=self.return_assets_var, width=14).pack(
            side="left", padx=(4, 8)
        )
        for sign in ("+", "-"):
            ttk.Radiobutton(
                calculator,
                text=sign,
                value=sign,
                variable=self.return_sign_var,
                style="Pill.TRadiobutton",
            ).pack(side="left", padx=1)
        ttk.Label(calculator, text="실현손익").pack(side="left", padx=(6, 0))
        ttk.Entry(calculator, textvariable=self.return_profit_var, width=12).pack(
            side="left", padx=(4, 6)
        )
        ttk.Button(
            calculator,
            text="계산",
            command=self._calculate_daily_return,
        ).pack(side="left")
        ttk.Label(
            calculator,
            textvariable=self.return_result_var,
            font=(UI_DISPLAY_FONT, 10, "bold"),
        ).pack(side="left", padx=(10, 0))

        links = ttk.LabelFrame(tools_frame, text="API 연동 안내", padding=8)
        links.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Button(
            links,
            text="키움 계좌·IP 등록",
            command=lambda: webbrowser.open(KIWOOM_REST_PORTAL),
        ).pack(side="left")
        ttk.Button(
            links,
            text="키움 API 가이드",
            command=lambda: webbrowser.open(KIWOOM_REST_GUIDE),
        ).pack(side="left", padx=4)
        ttk.Button(
            links,
            text="키움 공식 GitHub",
            command=lambda: webbrowser.open(
                "https://github.com/Kiwoom-Securities/Kiwoom-REST-API"
            ),
        ).pack(side="left", padx=4)

        self.main_notebook.bind(
            "<<NotebookTabChanged>>",
            self._on_notebook_tab_changed,
            add="+",
        )

    def _on_notebook_tab_changed(self, _event: tk.Event | None = None) -> None:
        if self.main_notebook.select() == str(self.market_control_tab):
            self._request_market_control_data(show_error=False)
            self._schedule_market_control_poll()
        elif self._market_control_after_id is not None:
            self.after_cancel(self._market_control_after_id)
            self._market_control_after_id = None

    def _schedule_market_control_poll(self) -> None:
        if self._market_control_after_id is None:
            self._market_control_after_id = self.after(
                3000,
                self._run_market_control_poll,
            )

    def _run_market_control_poll(self) -> None:
        self._market_control_after_id = None
        if (
            self.main_notebook.select() == str(self.market_control_tab)
            and self.service.account_info.connected
        ):
            self._request_market_control_data(show_error=False)
            self._schedule_market_control_poll()

    def _request_market_control_data(self, show_error: bool = True) -> bool:
        if not self.service.account_info.connected or not self._selected_symbol_ready():
            if show_error:
                self._show_warning(
                    "호가 조회 준비",
                    "키움 API 연결과 종목 세팅을 먼저 완료해 주세요.",
                )
            return False
        account = self._account_for_api()
        book = self.service.request_order_book(self.symbol_var.get())
        if account:
            self.service.request_unfilled_orders(account, self.symbol_var.get())
        self._refresh()
        if book is None and show_error:
            self._show_warning("10호가 조회 실패", self.service.last_api_message)
        return book is not None

    def _render_market_control(self, snapshot) -> None:
        for item in self.order_book_table.get_children():
            self.order_book_table.delete(item)
        book = getattr(snapshot, "order_book", None)
        if book is not None:
            for level in book.levels:
                self.order_book_table.insert(
                    "",
                    "end",
                    values=(
                        level.level,
                        f"{level.ask_quantity:,}",
                        f"{level.ask_price:,.0f}",
                        f"{level.bid_price:,.0f}",
                        f"{level.bid_quantity:,}",
                    ),
                    tags=(("best",) if level.level == 1 else ()),
                )
            try:
                buy_midpoint = midpoint_limit_price(book.best_ask, book.best_bid, "BUY")
                sell_midpoint = midpoint_limit_price(book.best_ask, book.best_bid, "SELL")
                self.midpoint_status_var.set(
                    f"수동 중간가 매수 {buy_midpoint:,.0f}원 / "
                    f"매도 {sell_midpoint:,.0f}원"
                )
            except ValueError:
                self.midpoint_status_var.set("중간가: 유효한 매수1·매도1호가 대기")
        else:
            self.midpoint_status_var.set("중간가: 호가 조회 전")

        for item in self.unfilled_table.get_children():
            self.unfilled_table.delete(item)
        self._unfilled_by_order_no = {}
        for index, order in enumerate(getattr(snapshot, "unfilled_orders", ())):
            order_no = str(order.order_no)
            self._unfilled_by_order_no[order_no] = order
            iid = f"{order_no}:{index}"
            self.unfilled_table.insert(
                "",
                "end",
                iid=iid,
                values=(
                    order_no,
                    order.timestamp,
                    f"{order.symbol} {order.symbol_name}".strip(),
                    SIDE_LABELS.get(order.side, order.side),
                    f"{order.order_quantity:,}",
                    f"{order.unfilled_quantity:,}",
                    f"{order.order_price:,.0f}",
                    order.status,
                ),
                tags=(order.side,),
            )

        daily = snapshot.daily_performance
        monthly = snapshot.monthly_performance
        self.daily_performance_var.set(
            f"오늘 · 체결 {daily.trade_count}건 · 승률 {daily.win_rate:.1f}% · "
            f"손익비 {daily.profit_loss_ratio:.2f} · 실현 {daily.realized_profit:+,.0f}원"
        )
        self.monthly_performance_var.set(
            f"이번 달 · 체결 {monthly.trade_count}건 · 승률 {monthly.win_rate:.1f}% · "
            f"손익비 {monthly.profit_loss_ratio:.2f} · 실현 {monthly.realized_profit:+,.0f}원"
        )
        risk_status = snapshot.daily_risk_status
        if risk_status is None:
            self.daily_risk_status_var.set(
                f"정상 대기 · 한도 -{self.service.daily_loss_breaker.limit_percent:g}%"
            )
            self.daily_risk_badge.configure(style="Neutral.Badge.TLabel")
        elif risk_status.locked:
            self.daily_risk_status_var.set(
                f"당일 주문 잠금 · {risk_status.loss_rate:.2f}%"
            )
            self.daily_risk_badge.configure(style="Danger.Badge.TLabel")
        else:
            self.daily_risk_status_var.set(
                f"정상 · {risk_status.loss_rate:.2f}% / -{risk_status.limit_percent:g}%"
            )
            self.daily_risk_badge.configure(style="Success.Badge.TLabel")

    def _on_unfilled_selected(self, _event: tk.Event | None = None) -> None:
        selected = self.unfilled_table.selection()
        if not selected:
            return
        values = self.unfilled_table.item(selected[0], "values")
        if not values:
            return
        order_no = str(values[0]).strip()
        order = getattr(self, "_unfilled_by_order_no", {}).get(order_no)
        if order is None:
            return
        self.original_order_no_var.set(order_no)
        self._selected_unfilled_side = order.side
        quantity = max(1, int(order.unfilled_quantity or order.order_quantity))
        self.order_qty_var.set(str(quantity))
        self.order_qty_display_var.set(f"{quantity}주")
        if normalize_symbol(order.symbol) != normalize_symbol(self.symbol_var.get()):
            self._connect_symbol_to_main(order.symbol, order.symbol_name)
        self._update_trade_buttons()

    def _send_selected_order_action(self, action: str) -> None:
        side = self._selected_unfilled_side if self.original_order_no_var.get().strip() else "BUY"
        self._send_order(side, action)

    def _set_daily_loss_limit(self) -> None:
        try:
            limit = float(self.daily_loss_limit_var.get().replace(",", "").strip())
            self.service.configure_daily_loss_limit(limit)
        except ValueError as exc:
            self._show_warning("일일 손실 한도", str(exc))
            return
        self.daily_loss_limit_var.set(f"{limit:g}")
        self.service.storage.log(
            "WARN",
            "위험관리",
            f"일일 최대 손실 한도를 -{limit:g}%로 설정했습니다.",
        )
        self._refresh()

    def _queue_daily_risk_liquidation(self, trade_date: str) -> None:
        return

        if (
            not self.circuit_liquidation_var.get()
            or self._daily_risk_handling
            or self._last_risk_lock_date == trade_date
        ):
            return
        self._last_risk_lock_date = trade_date
        self.after_idle(self._execute_daily_risk_liquidation)

    def _execute_daily_risk_liquidation(self) -> None:
        if self._daily_risk_handling:
            return
        self._daily_risk_handling = True
        try:
            allow_real = self._real_order_session_ready()
            if self._real_trading_account() and not allow_real:
                self.service.storage.log(
                    "ERROR",
                    "위험관리",
                    "손실 한도에 도달했지만 실거래 세션 승인이 없어 자동 전량청산은 전송하지 않았습니다.",
                )
                return
            account = self._account_for_api()
            self.service.cancel_all_unfilled_orders(
                account,
                allow_real_order=allow_real,
                account_password=self._account_password_for_order(),
            )
            self.service.emergency_liquidate(
                account,
                allow_real_order=allow_real,
                account_password=self._account_password_for_order(),
            )
            self._schedule_recent_trade_history_refresh()
        finally:
            self._daily_risk_handling = False

    def _calculate_daily_return(self) -> None:
        try:
            assets = float(self.return_assets_var.get().replace(",", "").strip())
            profit = float(self.return_profit_var.get().replace(",", "").strip())
            if self.return_sign_var.get() == "-":
                profit = -abs(profit)
            else:
                profit = abs(profit)
            result = daily_return_percent(assets, profit)
        except ValueError as exc:
            self._show_warning("당일 손익률 계산", str(exc))
            return
        self.return_result_var.set(f"당일 손익률: {result:+.2f}%")

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

    def _open_trade_history_file(self) -> None:
        path = self.service.storage.trade_history_path
        if not path.exists():
            messagebox.showwarning("매수·매도 이력", "아직 생성된 CSV 이력 파일이 없습니다.")
            return
        webbrowser.open(path.resolve().as_uri())

    def _request_recent_trade_history(self, show_error: bool = True) -> bool:
        account_info = self.service.account_info
        if not self._account_connection_confirmed(account_info):
            self.account_trade_history_status_var.set(
                f"키움 최근 {ACCOUNT_TRADE_HISTORY_DAYS}일 전체 체결: "
                "계좌 연결과 계좌 접근 확인이 필요합니다."
            )
            if show_error:
                messagebox.showwarning(
                    "최근 체결내역 조회 준비",
                    "키움 API 계좌를 먼저 연결해 주세요. OpenAPI+는 계좌 비밀번호 확인도 필요합니다.",
                )
            return False

        account = clean_account_number(self._account_for_api())
        password = self._account_password_for_order()
        self.account_trade_history_status_var.set(
            f"키움 최근 {ACCOUNT_TRADE_HISTORY_DAYS}일 전체 체결을 날짜별로 불러오는 중입니다."
        )
        self.trade_history_refresh_button.configure(state="disabled")
        self.update_idletasks()
        try:
            history = self.service.request_recent_trade_history(
                account,
                password=password,
                days=ACCOUNT_TRADE_HISTORY_DAYS,
            )
        finally:
            self.trade_history_refresh_button.configure(state="normal")
        self._refresh()
        if history is None:
            if show_error:
                messagebox.showwarning(
                    "최근 체결내역 조회 실패",
                    self.service.account_trade_history_message,
                )
            return False
        self.main_notebook.select(self.trade_history_tab)
        return True

    def _save_settings(self) -> None:
        period = _clamp_dmi_period(self.dmi_period_var.get())
        self.dmi_period_var.set(period)
        self.dmi_period_display_var.set(f"{period}일")
        settings = StrategySettings(dmi_period=period)
        self.service.configure(self.symbol_var.get(), self._operating_capital(), settings)
        if self.symbol_var.get().strip() != self.service.symbol:
            self.symbol_var.set(self.service.symbol)
        if self.service.symbol_name:
            self.symbol_name_var.set(self.service.symbol_name)
        self._refresh()

    def _start(self) -> None:
        self._save_settings()
        if not self._require_live_connection():
            return
        if self._selected_symbol_ready() and self.service.real_time_symbol != normalize_symbol(self.symbol_var.get()):
            self.service.register_real_time_price(self.symbol_var.get())
        self.service.start()
        self._mark_holding_balance_refresh_due()
        self._refresh_holding_balance_if_due(force=True)
        self.service.storage.log(
            "INFO",
            "보유감시",
            "실시간 감시를 시작했습니다. 주문은 수동 버튼으로만 전송됩니다.",
        )
        self._refresh()

    def _stop(self) -> None:
        self.service.stop()
        self._mark_holding_balance_refresh_due()
        self._clear_real_order_authorization("실시간 감시 중지로 실거래 수동주문 승인을 해제했습니다.")
        self._refresh()

    def _emergency_stop(self) -> None:
        self.service.emergency_stop()
        self._mark_holding_balance_refresh_due()
        account_ready = self._account_connection_confirmed(self.service.account_info)
        account = self._account_for_api()
        allow_real = self._real_order_session_ready()
        can_send = bool(account_ready and account)
        if self._real_trading_account() and not allow_real:
            can_send = False
        if can_send and messagebox.askyesno(
            "긴급 일괄 청산 최종 확인",
            "실시간 감시를 중지했습니다.\n"
            "이어서 모든 미체결 주문을 취소하고 보유 전 종목을 시장가로 전량 매도합니다.\n\n"
            "실제 주문이 전송될 수 있습니다. 계속하시겠습니까?",
        ):
            cancel_messages = self.service.cancel_all_unfilled_orders(
                account,
                allow_real_order=allow_real,
                account_password=self._account_password_for_order(),
            )
            liquidation_messages = self.service.emergency_liquidate(
                account,
                allow_real_order=allow_real,
                account_password=self._account_password_for_order(),
            )
            self.service.storage.log(
                "ERROR",
                "긴급청산",
                f"미체결 취소 {len(cancel_messages)}건, 보유종목 청산 {len(liquidation_messages)}건을 요청했습니다.",
            )
            self._schedule_recent_trade_history_refresh()
        elif account_ready and self._real_trading_account() and not allow_real:
            self._show_warning(
                "긴급 정지만 완료",
                "실시간 감시는 중지했지만 실거래 수동주문 승인이 없어 실제 취소·청산 주문은 보내지 않았습니다.",
            )
        self._clear_real_order_authorization("긴급 정지로 실거래 세션 승인을 해제했습니다.")
        self._refresh()

    def _close_app(self) -> None:
        if self.minimize_to_tray_enabled and not self._force_exit:
            if self._hide_to_tray():
                return
            self._show_warning(
                "시스템 트레이 실행 실패",
                "시스템 트레이 아이콘을 만들지 못해 프로그램을 종료합니다.",
            )
        self._finalize_close()

    def _finalize_close(self) -> None:
        try:
            backup_dir = self.service.storage.create_backup()
            self.service.storage.log(
                "INFO",
                "백업",
                f"종료 백업 완료: {backup_dir}",
            )
        except (OSError, ValueError) as exc:
            self.service.storage.log("ERROR", "백업", f"종료 백업 실패: {exc}")
        self._clear_real_order_authorization()
        self._clear_account_password_session()
        if self._clock_after_id is not None:
            self.after_cancel(self._clock_after_id)
            self._clock_after_id = None
        if self._volume_rank_after_id is not None:
            self.after_cancel(self._volume_rank_after_id)
            self._volume_rank_after_id = None
        if self._trade_value_rank_after_id is not None:
            self.after_cancel(self._trade_value_rank_after_id)
            self._trade_value_rank_after_id = None
        self._cancel_rank_tooltip()
        if self._market_control_after_id is not None:
            self.after_cancel(self._market_control_after_id)
            self._market_control_after_id = None
        if self._tray_poll_after_id is not None:
            self.after_cancel(self._tray_poll_after_id)
            self._tray_poll_after_id = None
        if self._tray_icon is not None:
            self._tray_icon.stop()
            self._tray_icon = None
        self.voice_notifier.close()
        self.sound_notifier.close()
        self.destroy()

    def _real_trading_account(self) -> bool:
        return self.service.account_info.server_type == "실거래"

    def _real_order_session_ready(self) -> bool:
        if not self._real_trading_account():
            return False
        return bool(self.allow_real_order_var.get() and self._real_order_session_armed)

    def _regular_market_open(self) -> bool:
        if not self._real_trading_account():
            return True
        checker = getattr(self.service, "is_regular_market_open", None)
        if callable(checker):
            return bool(checker())
        return _regular_market_is_open(self.service.latest_market_session_status())

    def _clear_real_order_authorization(self, log_message: str = "") -> None:
        was_armed = self._real_order_session_armed or self.allow_real_order_var.get()
        self._real_order_session_armed = False
        self.allow_real_order_var.set(False)
        self._last_auto_market_state = None
        if was_armed and log_message:
            self.service.storage.log("WARN", "주문", log_message)

    def _toggle_real_order_authorization(self) -> None:
        if not self.allow_real_order_var.get():
            self._clear_real_order_authorization("사용자가 실거래 세션 승인을 해제했습니다.")
            if self.service.running and self._real_trading_account():
                self.service.stop()
            self._refresh()
            return

        account = self._account_for_api()
        baseline = self._selected_trading_baseline()
        if (
            not self._real_trading_account()
            or not self._account_connection_confirmed(self.service.account_info)
            or not account
            or not self._selected_symbol_ready()
            or not self._order_quantity_valid()
            or baseline is None
        ):
            self.allow_real_order_var.set(False)
            self._real_order_session_armed = False
            messagebox.showwarning(
                "실거래 준비 필요",
                "실전 계좌 연결, 종목 세팅, 1주 이상 주문 수량, 금액 세팅을 먼저 완료해 주세요.",
            )
            self._refresh()
            return

        confirmed = messagebox.askyesno(
            "실거래 수동주문 세션 승인",
            f"계좌 {mask_account_number(account)} / 종목 {self.symbol_var.get()} "
            f"{self.symbol_name_var.get()}\n"
            f"주문 수량 {self._order_quantity()}주 / 고정 운용금액 {baseline.capital_limit:,.0f}원\n\n"
            "이 승인은 화면의 수동 주문 버튼을 눌렀을 때 실거래 전송을 허용합니다.\n"
            "자동매수·자동매도 주문은 제공하지 않습니다. 앱 종료, 감시 중지, 연결 해제 "
            "또는 긴급 정지 시 승인이 사라집니다.\n\n"
            "계좌, 종목, 수량과 운용금액을 확인했으며 이번 실행 세션의 실거래를 승인하시겠습니까?",
        )
        self._real_order_session_armed = confirmed
        self.allow_real_order_var.set(confirmed)
        if confirmed:
            self.service.storage.log(
                "WARN",
                "주문",
                f"{mask_account_number(account)} 실거래 수동주문 세션을 사용자가 승인했습니다.",
            )
        self._refresh()

    def _on_account_password_changed(self, *_args) -> None:
        if self._suppress_password_trace:
            return
        password = self.account_password_var.get().strip()
        self._session_password_account = ""
        self._account_access_verified = False
        self.account_password_status_var.set("입력 중" if password else "미확인")
        self._update_connection_badge(False)
        self._update_trade_buttons()

    def _clear_account_password_session(
        self,
        status: str = "미확인",
        clear_entry: bool = True,
    ) -> None:
        self._session_password_account = ""
        self._account_access_verified = False
        if clear_entry:
            self._suppress_password_trace = True
            try:
                self.account_password_var.set("")
            finally:
                self._suppress_password_trace = False
        self.account_password_status_var.set(status)

    def _password_session_ready(self) -> bool:
        return _account_password_session_ready(
            self.service.account_info.connection_method,
            self._account_for_api(),
            self._session_password_account,
            self.account_password_var.get(),
        )

    def _account_password_for_order(self) -> str:
        if self.service.account_info.connection_method == "REST API":
            return ""
        return self.account_password_var.get().strip() if self._password_session_ready() else ""

    def _handle_order_account_verification(self) -> None:
        if self.service.account_info.connection_method == "REST API":
            return
        if self.service.last_order_account_access_verified is False:
            self._clear_account_password_session(status="확인 실패")
            self.status_text.set(self.service.last_api_message)

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
        self._clear_real_order_authorization()
        self._clear_account_password_session()
        self._selected_account_full = ""
        self._holding_balance_fresh = False
        self._set_account_display("")
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
            self._show_warning("REST API 키 필요", "AppKey와 SecretKey를 모두 입력해 주세요.")
            return
        self._connect_rest_account(app_key, secret_key, mock)
        app_key = ""
        secret_key = ""

    def _connect_rest_account(self, app_key: str, secret_key: str, mock: bool) -> None:
        self._set_login_buttons_state("disabled")
        self._clear_real_order_authorization()
        self._clear_account_password_session(status="REST 불필요")
        self._holding_balance_fresh = False
        self._update_connection_badge(False)
        server_type = "모의투자" if mock else "실전투자"
        self.status_text.set(f"키움 REST API {server_type} 토큰과 계좌를 확인하고 있습니다.")
        self.update_idletasks()
        account_info = self.service.start_rest_connection(app_key, secret_key, mock=mock)
        app_key = ""
        secret_key = ""
        if account_info.connected and account_info.accounts:
            self._selected_account_full = account_info.accounts[0]
            self._set_account_display(self._selected_account_full)
            self._account_access_verified = True
            if self._selected_symbol_ready():
                self.service.request_current_price(self.symbol_var.get())
                self.service.request_chart_candles(3, self.symbol_var.get())
                self.service.register_real_time_price(self.symbol_var.get())
            self._holding_balance_fresh = bool(
                self.service.request_balance(self._selected_account_full)
            )
            self.service.request_recent_trade_history(
                self._selected_account_full,
                days=ACCOUNT_TRADE_HISTORY_DAYS,
            )
            self._request_rankings()
        else:
            self._account_access_verified = False
            self._selected_account_full = ""
            self._holding_balance_fresh = False
            self._set_account_display("")
            self._update_transfer_status("error")
            self._show_warning(
                "REST API 연결 실패",
                account_info.message
                or "현재 주문 네트워크 연결에 문제가 발생했습니다. 키움 API와 등록 IP를 확인해 주세요.",
            )
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
        if not self.service.account_info.connected:
            self._clear_account_password_session()
        elif self.service.account_info.connection_method == "REST API":
            self._clear_account_password_session(status="REST 불필요")
            self._account_access_verified = True
        self._refresh()
        if self._account_connection_confirmed(self.service.account_info):
            self._show_account_info_window()

    def _open_watchlist_window(self) -> None:
        if self._watchlist_window is not None and self._watchlist_window.winfo_exists():
            self._watchlist_window.deiconify()
            self._watchlist_window.lift()
            self._render_watchlist_rows()
            return

        self._load_watchlist_view_settings()
        window = tk.Toplevel(self)
        self._watchlist_window = window
        window.title("카와이 증권 관심종목")
        window.geometry("1320x620")
        window.minsize(980, 460)
        window.transient(self)
        window.protocol("WM_DELETE_WINDOW", self._close_watchlist_window)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)

        self.watchlist_symbol_var = tk.StringVar(value=self.symbol_var.get())
        self.watchlist_auto_link_var = tk.BooleanVar(value=True)
        self.watchlist_status_var = tk.StringVar(
            value="메모와 표시 설정은 프로그램 DB에 자동 저장됩니다."
        )
        self.watchlist_detail_vars = {
            key: tk.StringVar(value="-")
            for key in (
                "종목",
                "현재가",
                "전일대비",
                "등락률",
                "거래량",
                "거래대금(백억)",
                "전일 거래대금(백억)",
                "시가총액(백억)",
                "대금/시총",
                "프로그램 매매 추이",
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
        ttk.Button(
            toolbar,
            text="표시 필드",
            command=self._open_watchlist_field_selector,
        ).pack(side="left", padx=(5, 0))
        ttk.Button(
            toolbar,
            text="저장 폴더",
            command=self._choose_watchlist_sync_folder,
        ).pack(side="left", padx=(5, 0))
        ttk.Button(
            toolbar,
            text="내보내기",
            command=self._export_watchlist_data,
        ).pack(side="left", padx=(5, 0))
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
        columns = tuple(WATCHLIST_FIELD_SPECS)
        table = ttk.Treeview(
            table_frame,
            columns=columns,
            displaycolumns=tuple(self._watchlist_visible_fields),
            show="headings",
            selectmode="browse",
        )
        self._watchlist_table = table
        for field, (label, default_width, anchor) in WATCHLIST_FIELD_SPECS.items():
            width = self._watchlist_column_widths.get(field, default_width)
            table.heading(field, text=label, anchor="center")
            table.column(
                field,
                width=max(54, min(480, int(width))),
                minwidth=48,
                anchor=anchor,
                stretch=True,
            )
        table.tag_configure("up", foreground=UI_PINK)
        table.tag_configure("down", foreground=UI_BLUE)
        table.tag_configure("flat", foreground=UI_TEXT)
        table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=table.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        table.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        table.bind("<<TreeviewSelect>>", self._on_watchlist_row_selected)
        table.bind("<Double-1>", lambda _event: self._activate_watchlist_selection())
        table.bind("<Motion>", self._on_watchlist_pointer_motion)
        table.bind("<Leave>", lambda _event: self._cancel_watchlist_tooltip())
        table.bind("<Button-3>", self._edit_watchlist_memo)
        table.bind("<ButtonPress-1>", self._on_watchlist_heading_press, add="+")
        table.bind("<ButtonRelease-1>", self._on_watchlist_heading_release, add="+")
        table.bind("<MouseWheel>", lambda _event: self._cancel_watchlist_tooltip(), add="+")

        detail_panel = ttk.LabelFrame(body, text="선택 종목 정보", padding=12)
        detail_panel.grid(row=0, column=1, sticky="nsew")
        detail_panel.columnconfigure(1, weight=1)
        for row, key in enumerate(self.watchlist_detail_vars):
            ttk.Label(detail_panel, text=key).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Label(
                detail_panel,
                textvariable=self.watchlist_detail_vars[key],
                anchor="e",
                font=(UI_DISPLAY_FONT, 9, "bold") if key in ("종목", "현재가") else None,
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
        self._save_watchlist_column_widths()
        self._cancel_watchlist_tooltip()
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
        self._watchlist_drag_field = ""
        self._watchlist_hover_item = ""

    def _load_watchlist_view_settings(self) -> None:
        storage = self.service.storage
        visible = decode_json_setting(
            storage.get_app_setting("watchlist.visible_fields", ""),
            list(WATCHLIST_DEFAULT_VISIBLE_FIELDS),
        )
        order = decode_json_setting(
            storage.get_app_setting("watchlist.column_order", ""),
            list(WATCHLIST_FIELD_SPECS),
        )
        visible_order, normalized_order = normalize_watchlist_layout(visible, order)
        raw_widths = decode_json_setting(
            storage.get_app_setting("watchlist.column_widths", ""),
            {},
        )
        widths: dict[str, int] = {}
        if isinstance(raw_widths, dict):
            for field, value in raw_widths.items():
                if field not in WATCHLIST_FIELD_SPECS:
                    continue
                try:
                    widths[field] = max(54, min(480, int(value)))
                except (TypeError, ValueError):
                    continue
        self._watchlist_visible_fields = visible_order
        self._watchlist_column_order = normalized_order
        self._watchlist_column_widths = widths

    def _persist_watchlist_view_settings(self) -> None:
        storage = self.service.storage
        storage.set_app_setting(
            "watchlist.visible_fields",
            json.dumps(self._watchlist_visible_fields, ensure_ascii=False),
        )
        storage.set_app_setting(
            "watchlist.column_order",
            json.dumps(self._watchlist_column_order, ensure_ascii=False),
        )
        storage.set_app_setting(
            "watchlist.column_widths",
            json.dumps(self._watchlist_column_widths, ensure_ascii=False),
        )
        self._write_watchlist_auto_snapshot()

    def _save_watchlist_column_widths(self) -> None:
        table = self._watchlist_table
        if table is None or not table.winfo_exists():
            return
        widths: dict[str, int] = {}
        for field in WATCHLIST_FIELD_SPECS:
            try:
                widths[field] = int(table.column(field, "width"))
            except (tk.TclError, TypeError, ValueError):
                continue
        self._watchlist_column_widths.update(widths)
        self._persist_watchlist_view_settings()

    def _open_watchlist_field_selector(self) -> None:
        parent = self._watchlist_window
        if parent is None or not parent.winfo_exists():
            return
        window = tk.Toplevel(parent)
        window.title("관심종목 표시 필드")
        window.transient(parent)
        window.resizable(False, False)

        body = ttk.Frame(window, padding=16)
        body.grid(row=0, column=0, sticky="nsew")
        variables: dict[str, tk.BooleanVar] = {}
        for index, (field, (label, _width, _anchor)) in enumerate(
            WATCHLIST_FIELD_SPECS.items()
        ):
            variable = tk.BooleanVar(value=field in self._watchlist_visible_fields)
            variables[field] = variable
            checkbox = ttk.Checkbutton(body, text=label, variable=variable)
            checkbox.grid(
                row=index // 2,
                column=index % 2,
                sticky="w",
                padx=(0, 24),
                pady=5,
            )
            if field in WATCHLIST_REQUIRED_FIELDS:
                checkbox.state(["selected", "disabled"])

        buttons = ttk.Frame(body)
        buttons.grid(row=7, column=0, columnspan=2, sticky="e", pady=(14, 0))

        def restore_defaults() -> None:
            defaults = set(WATCHLIST_DEFAULT_VISIBLE_FIELDS)
            for field, variable in variables.items():
                variable.set(field in defaults or field in WATCHLIST_REQUIRED_FIELDS)

        def apply_selection() -> None:
            selected = [field for field, variable in variables.items() if variable.get()]
            visible_order, order = normalize_watchlist_layout(
                selected,
                self._watchlist_column_order,
            )
            supplemental_before = (
                set(self._watchlist_visible_fields) & WATCHLIST_SUPPLEMENTAL_FIELDS
            )
            self._watchlist_visible_fields = visible_order
            self._watchlist_column_order = order
            table = self._watchlist_table
            if table is not None and table.winfo_exists():
                table.configure(displaycolumns=tuple(visible_order))
            self._persist_watchlist_view_settings()
            window.destroy()
            supplemental_after = set(visible_order) & WATCHLIST_SUPPLEMENTAL_FIELDS
            if (
                supplemental_after != supplemental_before
                and supplemental_after
                and self.service.account_info.connected
                and self._watchlist_window is not None
            ):
                self._watchlist_window.after_idle(self._refresh_watchlist_quotes)

        ttk.Button(buttons, text="기본값", command=restore_defaults).pack(side="left")
        ttk.Button(buttons, text="취소", command=window.destroy).pack(
            side="left",
            padx=(8, 0),
        )
        ttk.Button(
            buttons,
            text="적용",
            style="Primary.TButton",
            command=apply_selection,
        ).pack(side="left", padx=(8, 0))
        window.bind("<Escape>", lambda _event: window.destroy())
        _show_centered_dialog(window)
        window.grab_set()

    def _on_watchlist_heading_press(self, event: tk.Event) -> None:
        self._cancel_watchlist_tooltip()
        table = self._watchlist_table
        if table is None or table.identify_region(event.x, event.y) != "heading":
            self._watchlist_drag_field = ""
            return
        column = table.identify_column(event.x)
        try:
            index = int(column.removeprefix("#")) - 1
            self._watchlist_drag_field = self._watchlist_visible_fields[index]
            self._watchlist_drag_start_x = int(event.x)
        except (ValueError, IndexError):
            self._watchlist_drag_field = ""

    def _on_watchlist_heading_release(self, event: tk.Event) -> None:
        table = self._watchlist_table
        field = self._watchlist_drag_field
        self._watchlist_drag_field = ""
        if table is None or not table.winfo_exists():
            return
        if (
            field
            and abs(int(event.x) - self._watchlist_drag_start_x) >= 8
            and table.identify_region(event.x, event.y) == "heading"
        ):
            target_column = table.identify_column(event.x)
            try:
                target_index = int(target_column.removeprefix("#")) - 1
                visible = list(self._watchlist_visible_fields)
                visible.remove(field)
                visible.insert(max(0, min(target_index, len(visible))), field)
                hidden = [
                    name for name in self._watchlist_column_order if name not in visible
                ]
                self._watchlist_visible_fields = visible
                self._watchlist_column_order = visible + hidden
                table.configure(displaycolumns=tuple(visible))
            except (ValueError, IndexError):
                pass
        table.after_idle(self._save_watchlist_column_widths)

    def _on_watchlist_pointer_motion(self, event: tk.Event) -> None:
        table = self._watchlist_table
        if table is None or table.identify_region(event.x, event.y) != "cell":
            self._cancel_watchlist_tooltip()
            return
        item = table.identify_row(event.y)
        if not item:
            self._cancel_watchlist_tooltip()
            return
        if item == self._watchlist_hover_item:
            return
        self._cancel_watchlist_tooltip()
        self._watchlist_hover_item = item
        self._watchlist_tooltip_after_id = self.after(
            2_000,
            lambda symbol=item: self._show_watchlist_tooltip(symbol),
        )

    def _show_watchlist_tooltip(self, symbol: str) -> None:
        self._watchlist_tooltip_after_id = None
        table = self._watchlist_table
        if (
            table is None
            or not table.winfo_exists()
            or symbol != self._watchlist_hover_item
            or not table.exists(symbol)
        ):
            return
        pointer_x, pointer_y = table.winfo_pointerxy()
        local_y = pointer_y - table.winfo_rooty()
        if table.identify_row(local_y) != symbol:
            return

        quote = self._watchlist_quote(symbol)
        memo = self.service.storage.watchlist_memo(symbol)
        name = quote.name if quote is not None else ""
        tooltip = tk.Toplevel(table)
        self._watchlist_tooltip_window = tooltip
        tooltip.overrideredirect(True)
        try:
            tooltip.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        frame = tk.Frame(
            tooltip,
            background=UI_TEXT,
            highlightbackground=UI_TEXT,
            highlightthickness=1,
            padx=10,
            pady=8,
        )
        frame.pack(fill="both", expand=True)
        tk.Label(
            frame,
            text=f"{symbol}  {name or '종목명 미조회'}",
            background=UI_TEXT,
            foreground=UI_SURFACE,
            font=(UI_DISPLAY_FONT, 9, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            frame,
            text=memo or "메모 없음",
            background=UI_TEXT,
            foreground="#E8E8E8",
            font=(UI_FONT, 9),
            anchor="w",
            justify="left",
            wraplength=360,
        ).pack(fill="x", pady=(4, 0))
        tooltip.update_idletasks()
        left = min(pointer_x + 14, tooltip.winfo_screenwidth() - tooltip.winfo_reqwidth() - 8)
        top = min(pointer_y + 18, tooltip.winfo_screenheight() - tooltip.winfo_reqheight() - 8)
        tooltip.geometry(f"+{max(0, left)}+{max(0, top)}")

    def _cancel_watchlist_tooltip(self) -> None:
        if self._watchlist_tooltip_after_id is not None:
            try:
                self.after_cancel(self._watchlist_tooltip_after_id)
            except tk.TclError:
                pass
            self._watchlist_tooltip_after_id = None
        if (
            self._watchlist_tooltip_window is not None
            and self._watchlist_tooltip_window.winfo_exists()
        ):
            self._watchlist_tooltip_window.destroy()
        self._watchlist_tooltip_window = None
        self._watchlist_hover_item = ""

    def _edit_watchlist_memo(self, event: tk.Event) -> str | None:
        table = self._watchlist_table
        parent = self._watchlist_window
        if table is None or parent is None:
            return None
        symbol = normalize_symbol(table.identify_row(event.y))
        if not symbol:
            return None
        self._cancel_watchlist_tooltip()
        table.selection_set(symbol)
        table.focus(symbol)
        quote = self._watchlist_quote(symbol)
        dialog = WatchlistMemoDialog(
            parent,
            symbol,
            quote.name if quote is not None else "",
            self.service.storage.watchlist_memo(symbol),
        )
        if dialog.result is None:
            return "break"
        self.service.storage.set_watchlist_memo(symbol, dialog.result)
        self._write_watchlist_auto_snapshot()
        if self.watchlist_status_var is not None:
            state = "삭제" if not dialog.result else "저장"
            self.watchlist_status_var.set(
                f"{symbol} 메모 {state} 완료 · 프로그램 DB 자동 저장"
            )
        return "break"

    def _watchlist_export_payload(self) -> dict[str, object]:
        quotes = {quote.symbol: quote for quote in self.service.watchlist_rows()}
        items: list[dict[str, object]] = []
        for symbol, name, memo in self.service.storage.watchlist_entries():
            quote = quotes.get(symbol, WatchlistQuote(symbol=symbol, name=name))
            items.append(
                {
                    "symbol": symbol,
                    "name": quote.name or name,
                    "memo": memo,
                    "market": quote.market,
                    "current_price": quote.current_price,
                    "change": quote.change,
                    "change_rate": quote.change_rate,
                    "volume": quote.volume,
                    "trade_value_won": quote.trade_value,
                    "previous_trade_value_won": quote.previous_trade_value,
                    "market_cap_won": quote.market_cap,
                    "program_trading_trend_won": quote.program_trading_trend,
                    "timestamp": quote.timestamp,
                }
            )
        return {
            "schema_version": 1,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "visible_fields": list(self._watchlist_visible_fields),
            "column_order": list(self._watchlist_column_order),
            "column_widths": dict(self._watchlist_column_widths),
            "items": items,
        }

    def _watchlist_csv_rows(self) -> list[dict[str, object]]:
        memos = {
            symbol: memo
            for symbol, _name, memo in self.service.storage.watchlist_entries()
        }
        rows: list[dict[str, object]] = []
        for quote in self.service.watchlist_rows():
            displayed = dict(
                zip(WATCHLIST_FIELD_SPECS, self._watchlist_row_values(quote))
            )
            row = {
                WATCHLIST_FIELD_SPECS[field][0]: displayed[field]
                for field in WATCHLIST_FIELD_SPECS
            }
            row["메모"] = memos.get(quote.symbol, "")
            rows.append(row)
        return rows

    def _write_watchlist_export(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.suffix.casefold() == ".csv":
            fieldnames = [
                WATCHLIST_FIELD_SPECS[field][0]
                for field in WATCHLIST_FIELD_SPECS
            ] + ["메모"]
            with destination.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self._watchlist_csv_rows())
            return
        with destination.open("w", encoding="utf-8") as stream:
            json.dump(
                self._watchlist_export_payload(),
                stream,
                ensure_ascii=False,
                indent=2,
            )

    def _default_watchlist_export_directory(self) -> Path:
        saved = self.service.storage.get_app_setting("watchlist.sync_directory", "")
        candidates = (
            Path(saved) if saved else None,
            Path(os.environ["OneDrive"]) if os.environ.get("OneDrive") else None,
            Path.home() / "Documents" / "KawaiiSecurities",
        )
        return next(
            (path for path in candidates if path is not None and path.exists()),
            Path.home() / "Documents",
        )

    def _choose_watchlist_sync_folder(self) -> None:
        parent = self._watchlist_window or self
        selected = filedialog.askdirectory(
            parent=parent,
            title="관심종목 자동 저장 폴더 선택",
            initialdir=str(self._default_watchlist_export_directory()),
        )
        if not selected:
            return
        self.service.storage.set_app_setting("watchlist.sync_directory", selected)
        self._write_watchlist_auto_snapshot()
        if self.watchlist_status_var is not None:
            self.watchlist_status_var.set(f"자동 저장 폴더: {selected}")

    def _write_watchlist_auto_snapshot(self) -> None:
        folder = self.service.storage.get_app_setting("watchlist.sync_directory", "")
        if not folder:
            return
        try:
            self._write_watchlist_export(
                Path(folder) / "카와이증권_관심종목_자동저장.json"
            )
        except OSError as exc:
            if self.watchlist_status_var is not None:
                self.watchlist_status_var.set(f"자동 저장 실패: {exc}")

    def _export_watchlist_data(self) -> None:
        parent = self._watchlist_window or self
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        selected = filedialog.asksaveasfilename(
            parent=parent,
            title="관심종목 내보내기",
            initialdir=str(self._default_watchlist_export_directory()),
            initialfile=f"카와이증권_관심종목_{stamp}.json",
            defaultextension=".json",
            filetypes=(
                ("JSON 설정 및 메모", "*.json"),
                ("CSV 표", "*.csv"),
            ),
        )
        if not selected:
            return
        try:
            destination = Path(selected)
            self._save_watchlist_column_widths()
            self._write_watchlist_export(destination)
        except OSError as exc:
            messagebox.showerror("관심종목 내보내기", str(exc), parent=parent)
            return
        if self.watchlist_status_var is not None:
            self.watchlist_status_var.set(f"내보내기 완료: {destination}")

    def _register_watchlist_symbol(self) -> None:
        if self.watchlist_symbol_var is None:
            return
        symbol = self.service.add_watchlist_symbol(self.watchlist_symbol_var.get())
        if self.watchlist_status_var is not None:
            self.watchlist_status_var.set(self.service.last_api_message)
        if not symbol:
            return
        self._write_watchlist_auto_snapshot()
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
        self._write_watchlist_auto_snapshot()
        if self.watchlist_status_var is not None:
            self.watchlist_status_var.set(self.service.last_api_message)
        self._render_watchlist_rows()

    def _refresh_watchlist_quotes(self, select_symbol: str = "") -> None:
        if self.service.account_info.connected:
            supplemental = set(self._watchlist_visible_fields) & WATCHLIST_SUPPLEMENTAL_FIELDS
            self.service.refresh_watchlist_quotes(supplemental_fields=supplemental)
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
                values=self._watchlist_row_values(quote),
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
        if not self._connect_symbol_to_main(symbol):
            return
        self._render_watchlist_rows(select_symbol=symbol)
        if self.watchlist_status_var is not None:
            self.watchlist_status_var.set(
                f"{symbol} {self.service.symbol_name} 시세·실시간·차트를 자동 연결했습니다."
            )

    def _connect_symbol_to_main(self, symbol: str, name: str = "") -> bool:
        normalized = normalize_symbol(symbol)
        if not normalized or not self.service.account_info.connected:
            return False
        self.symbol_var.set(normalized)
        self._load_trading_baseline(normalized)
        self.service.configure(normalized, self._operating_capital(), self._settings())
        if name:
            self.service.symbol_name = name.strip()
        self.service.request_current_price(normalized)
        timeframe = self.chart_timeframe_var.get()
        if timeframe.endswith("s"):
            self.service.register_real_time_price(normalized)
            self.service.select_realtime_chart(int(timeframe[:-1]))
        else:
            self.service.request_chart_candles(int(timeframe[:-1]), normalized)
            self.service.register_real_time_price(normalized)
        self.symbol_name_var.set(self.service.symbol_name or name)
        self._show_symbol_tag(self.service.symbol_name or name or normalized)
        self._refresh()
        self.main_notebook.select(self.dmi_chart_tab)
        return True

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
            "거래대금(백억)": self._format_hundred_eok(quote.trade_value),
            "전일 거래대금(백억)": self._format_hundred_eok(
                quote.previous_trade_value
            ),
            "시가총액(백억)": self._format_hundred_eok(quote.market_cap),
            "대금/시총": (
                f"{quote.trade_value / quote.market_cap * 100:.2f}%"
                if quote.trade_value and quote.market_cap
                else "-"
            ),
            "프로그램 매매 추이": self._format_hundred_eok(
                quote.program_trading_trend,
                signed=True,
            ),
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
            values=self._watchlist_row_values(watch_quote),
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
    def _watchlist_row_values(quote: WatchlistQuote) -> tuple[str, ...]:
        has_price = quote.current_price > 0
        trade_to_market_cap = (
            quote.trade_value / quote.market_cap * 100
            if quote.trade_value and quote.market_cap
            else 0.0
        )
        values = {
            "market": quote.market,
            "symbol": quote.symbol,
            "name": quote.name or "미조회",
            "current_price": f"{quote.current_price:,.0f}" if has_price else "-",
            "trade_value": TraderApp._format_hundred_eok(quote.trade_value),
            "previous_trade_value": TraderApp._format_hundred_eok(
                quote.previous_trade_value
            ),
            "change": (
                TraderApp._format_watchlist_change(quote.change)
                if has_price
                else "-"
            ),
            "change_rate": f"{quote.change_rate:+.2f}%" if has_price else "-",
            "volume": f"{quote.volume:,}" if has_price else "-",
            "market_cap": TraderApp._format_hundred_eok(quote.market_cap),
            "trade_to_market_cap": (
                f"{trade_to_market_cap:.2f}%" if trade_to_market_cap else "-"
            ),
            "program_trading_trend": TraderApp._format_hundred_eok(
                quote.program_trading_trend,
                signed=True,
            ),
        }
        return tuple(values[field] for field in WATCHLIST_FIELD_SPECS)

    @staticmethod
    def _format_hundred_eok(value: float, signed: bool = False) -> str:
        if not value:
            return "-"
        amount = float(value) / HUNDRED_EOK_WON
        return f"{amount:+,.2f}" if signed else f"{amount:,.2f}"

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

    def _set_symbol(self) -> None:
        digits = "".join(character for character in self.symbol_var.get() if character.isdigit())
        if len(digits) != 6 or digits == "000000":
            self.symbol_name_var.set("")
            self._load_trading_baseline("000000")
            self.current_price_display_var.set("미조회")
            messagebox.showwarning("종목번호 확인", "000000이 아닌 6자리 종목번호를 입력해 주세요.")
            return

        normalized = normalize_symbol(digits)
        previous_symbol = self.service.symbol
        if normalized != previous_symbol:
            if self.service.running:
                self.service.stop()
            self._clear_real_order_authorization(
                "종목 변경으로 실거래 세션 승인을 해제했습니다."
            )
        self.symbol_var.set(normalized)
        self._load_trading_baseline(normalized)
        self.service.configure(normalized, self._operating_capital(), self._settings())
        name = self.service.lookup_symbol_name(normalized)
        self.symbol_name_var.set(name or "조회 실패")
        if normalized != previous_symbol:
            self.current_price_display_var.set("미조회")
        self.service.storage.log(
            "INFO" if name else "WARN",
            "종목",
            f"종목 세팅: {normalized} {name or '종목명 조회 실패'}",
        )
        if name:
            self._show_symbol_tag(name)
        self._refresh()

    def _open_symbol_search(self) -> None:
        if not self._require_live_connection():
            return
        window = tk.Toplevel(self)
        window.title("종목명 검색")
        window.transient(self)
        window.geometry("560x460")
        window.minsize(480, 360)

        query_var = tk.StringVar(value=self.symbol_name_var.get().strip())
        frame = ttk.Frame(window, padding=14)
        frame.pack(fill="both", expand=True)
        search_row = ttk.Frame(frame)
        search_row.pack(fill="x", pady=(0, 10))
        entry = ttk.Entry(search_row, textvariable=query_var)
        entry.pack(side="left", fill="x", expand=True)

        table = ttk.Treeview(
            frame,
            columns=("종목코드", "종목명"),
            show="headings",
            height=14,
        )
        table.heading("종목코드", text="종목코드")
        table.heading("종목명", text="종목명")
        table.column("종목코드", width=110, anchor="center", stretch=False)
        table.column("종목명", width=330, anchor="w")
        table.pack(fill="both", expand=True)

        status_var = tk.StringVar(value="종목명 또는 6자리 종목코드를 입력해 주세요.")
        ttk.Label(frame, textvariable=status_var, foreground=UI_MUTED).pack(
            fill="x", pady=(8, 0)
        )

        def run_search(_event: tk.Event | None = None) -> None:
            query = query_var.get().strip()
            for item in table.get_children():
                table.delete(item)
            if not query:
                status_var.set("검색어를 입력해 주세요.")
                return
            rows = self.service.search_symbols(query, limit=50)
            for symbol, name in rows:
                normalized = normalize_symbol(symbol)
                table.insert("", "end", iid=normalized, values=(normalized, name))
            status_var.set(f"{len(rows)}개 종목을 찾았습니다.")

        def choose(_event: tk.Event | None = None) -> None:
            selected = table.selection()
            if not selected:
                return
            symbol = normalize_symbol(selected[0])
            values = table.item(selected[0], "values")
            name = str(values[1]) if len(values) > 1 else ""
            if self._connect_symbol_to_main(symbol, name):
                window.destroy()

        ttk.Button(search_row, text="검색", command=run_search).pack(
            side="left", padx=(6, 0)
        )
        entry.bind("<Return>", run_search)
        table.bind("<Double-1>", choose)
        ttk.Button(frame, text="선택 종목 연결", command=choose).pack(
            anchor="e", pady=(10, 0)
        )
        entry.focus_set()
        _show_centered_dialog(window)

    def _show_symbol_tag(self, name: str) -> None:
        label = str(name or "").strip()
        if not label:
            return
        self.active_symbol_tag_var.set(f"#{label}")
        if self._symbol_tag_after_id is not None:
            self.after_cancel(self._symbol_tag_after_id)
        self._symbol_tag_after_id = self.after(2200, self._clear_symbol_tag)

    def _clear_symbol_tag(self) -> None:
        self._symbol_tag_after_id = None
        self.active_symbol_tag_var.set("")

    def _update_daily_traded_tags(self, executions: list[object]) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        tagged: list[tuple[str, str]] = []
        for execution in executions:
            if not str(getattr(execution, "timestamp", "")).startswith(today):
                continue
            symbol = normalize_symbol(getattr(execution, "symbol", ""))
            name = str(getattr(execution, "symbol_name", "") or symbol).strip()
            row = (symbol, name)
            if symbol and row not in tagged:
                tagged.append(row)
            if len(tagged) >= 10:
                break
        self._daily_traded_symbols = tagged
        value = " ".join(f"#{name}" for _symbol, name in tagged)
        self.daily_traded_tags_var.set(f"오늘 거래 종목: {value or '없음'}")

    def _schedule_account_poll(self) -> None:
        if self._account_after_id is None:
            self._account_after_id = self.after(1000, self._poll_account_connection)

    def _schedule_recent_trade_history_refresh(self) -> None:
        if self._trade_history_after_id is None:
            self._trade_history_after_id = self.after(
                3000,
                self._run_scheduled_recent_trade_history_refresh,
            )

    def _run_scheduled_recent_trade_history_refresh(self) -> None:
        self._trade_history_after_id = None
        self._request_recent_trade_history(show_error=False)

    def _poll_account_connection(self) -> None:
        self._account_after_id = None
        self._account_poll_count += 1
        account_info = self.service.refresh_account_connection()
        if account_info.accounts:
            if self._selected_account_full not in account_info.accounts:
                self._selected_account_full = account_info.accounts[0]
            self._set_account_display(self._selected_account_full)
        if account_info.connected:
            if self._selected_symbol_ready():
                self.service.request_current_price(self.symbol_var.get())
                self.service.request_chart_candles(3, self.symbol_var.get())
                self.service.register_real_time_price(self.symbol_var.get())
        self._refresh()
        login_failed = self.service.kiwoom_api.last_login_error not in (None, 0)
        identity_rejected = bool(account_info.user_id and not account_info.connected)
        if account_info.connected or login_failed or identity_rejected or self._account_poll_count >= 120:
            self._set_login_buttons_state("normal")
            if self._account_connection_confirmed(account_info):
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
        current_price = self._selected_current_price()
        if current_price <= 0:
            self.service.last_api_message = "키움 실제 현재가를 불러온 뒤 수동 주문을 준비해 주세요."
            self._refresh()
            return
        self.service.step(current_price)
        self._refresh()

    def _request_current_price(self) -> None:
        if not self._require_live_connection():
            return
        if not self._selected_symbol_ready():
            messagebox.showwarning("종목 세팅 필요", "6자리 종목번호를 입력하고 '종목 세팅'을 먼저 눌러 주세요.")
            return
        self.service.configure(self.symbol_var.get(), self._operating_capital(), self._settings())
        self.service.request_current_price(self.symbol_var.get())
        self._refresh()

    def _request_three_minute(self) -> None:
        if not self._require_live_connection():
            return
        self.service.configure(self.symbol_var.get(), self._operating_capital(), self._settings())
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
        self.service.configure(self.symbol_var.get(), self._operating_capital(), self._settings())
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
        if self.service.account_info.connection_method != "REST API":
            if not self.account_password_var.get().strip():
                messagebox.showwarning(
                    "계좌 비밀번호 필요",
                    "계좌번호 옆에 숫자 4~8자리 비밀번호를 입력하고 "
                    "'비밀번호 세팅'을 눌러 주세요.",
                )
                self.account_password_entry.focus_set()
                return
            self._set_account_password()
            return

        balance = self.service.request_balance(self._account_for_api())
        self._holding_balance_fresh = balance is not None
        self._refresh()
        if balance is not None:
            self._show_account_info_window(refresh=True)

    def _set_account_password(self) -> None:
        if not self._require_live_connection():
            self._clear_account_password_session()
            return
        if self.service.account_info.connection_method == "REST API":
            self._clear_account_password_session(status="REST 불필요")
            self._request_balance()
            return

        account = clean_account_number(self._account_for_api())
        if not account:
            self._clear_account_password_session(status="계좌 대기")
            messagebox.showwarning(
                "계좌번호 확인",
                "키움 로그인 후 계좌번호가 표시되면 비밀번호를 입력해 주세요.",
            )
            return

        password = self.account_password_var.get().strip()
        if not is_valid_account_password(password):
            self._clear_account_password_session(status="미확인", clear_entry=False)
            messagebox.showwarning(
                "계좌 비밀번호 확인",
                "계좌 비밀번호는 숫자 4~8자리로 입력해 주세요.",
            )
            self.account_password_entry.focus_set()
            return

        self._clear_account_password_session(status="확인 중", clear_entry=False)
        self.account_password_entry.configure(state="disabled")
        self.account_password_button.configure(state="disabled")
        self._update_connection_badge(False)
        self.status_text.set("키움 계좌 비밀번호와 잔고 접근 권한을 확인하고 있습니다.")
        self.update_idletasks()
        balance = None
        try:
            balance = self.service.request_balance(account, password)
            if balance is not None:
                self._session_password_account = account
                self._account_access_verified = True
        finally:
            password = ""

        if balance is None:
            self._holding_balance_fresh = False
            self._clear_account_password_session(status="확인 실패")
        else:
            self._holding_balance_fresh = True
            self.account_password_status_var.set("확인됨")
            self.service.request_recent_trade_history(
                account,
                password=self._account_password_for_order(),
                days=ACCOUNT_TRADE_HISTORY_DAYS,
            )
        self._refresh()
        if balance is not None:
            self._show_account_info_window(refresh=True)
        else:
            messagebox.showwarning("계좌 연결 실패", self.service.last_api_message)

    def _register_real_time(self) -> None:
        if not self._require_live_connection():
            return
        self.service.configure(self.symbol_var.get(), self._operating_capital(), self._settings())
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
        self.service.configure(self.symbol_var.get(), self._operating_capital(), self._settings())
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
                messagebox.showwarning(
                    "초봉 데이터 대기",
                    "키움 ka10079 1틱 이력과 0B 실시간 체결을 아직 받지 못했습니다.\n"
                    "장중 새 체결이 발생하면 초봉이 자동으로 이어집니다.",
                )
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
        window.configure(background=UI_BACKGROUND)
        window.transient(self)
        window.protocol("WM_DELETE_WINDOW", self._close_candle_chart)

        body = ttk.Frame(window, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text=(
                f"{self.service.symbol} {self.service.symbol_name} | 키움 {label}봉 | "
                f"차트 DMI({self.service.strategy.settings.dmi_period}봉) | "
                f"키움 일봉 DMI({self.service.strategy.settings.dmi_period}일)"
            ),
            font=(UI_DISPLAY_FONT, 13, "bold"),
        ).pack(anchor="w", pady=(0, 8))
        canvas = tk.Canvas(
            body,
            background=UI_SURFACE,
            highlightthickness=1,
            highlightbackground=UI_BORDER,
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
                "초봉 데이터 대기\nka10079 1틱 이력 또는 장중 0B 체결 확인"
                if self.service.chart_timeframe.endswith("s")
                else "차트 데이터 대기"
            )
            canvas.create_text(
                width / 2,
                height / 2,
                text=empty_text,
                fill=UI_MUTED,
                font=(UI_FONT, 12),
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
        use_log_scale = bool(self.log_chart_var.get() and lowest > 0)
        scale_lowest = math.log(lowest) if use_log_scale else lowest
        scale_highest = math.log(highest) if use_log_scale else highest
        price_span = max(1e-9 if use_log_scale else 1.0, scale_highest - scale_lowest)

        def price_y(price: float) -> float:
            scale_price = math.log(max(price, 1e-9)) if use_log_scale else price
            return top_pad + ((scale_highest - scale_price) / price_span) * price_height

        def dmi_y(value: float) -> float:
            bounded = max(0.0, min(100.0, value))
            return dmi_top + ((100.0 - bounded) / 100.0) * indicator_height

        step = plot_width / max(1, len(displayed))
        if self.show_pattern_var.get():
            pattern_bottom = dmi_bottom if show_dmi else price_bottom
            for index, point in dmi_by_index.items():
                if index < 0 or index >= len(displayed) or point.pattern_state == "NONE":
                    continue
                fill = "#FCE8F0" if point.pattern_state == "BULLISH" else "#EEF1FF"
                x0 = left_pad + step * index
                x1 = left_pad + step * (index + 1)
                canvas.create_rectangle(x0, top_pad, x1, pattern_bottom, fill=fill, outline="")

        for level_index in range(5):
            ratio = level_index / 4
            y = top_pad + ratio * price_height
            scale_price = scale_highest - ratio * price_span
            price = math.exp(scale_price) if use_log_scale else scale_price
            canvas.create_line(
                left_pad,
                y,
                left_pad + plot_width,
                y,
                fill=UI_BORDER,
                dash=(2, 3),
            )
            canvas.create_text(
                left_pad + plot_width + 8,
                y,
                text=f"{price:,.0f}",
                anchor="w",
                fill=UI_MUTED,
                font=(UI_FONT, 8),
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
            canvas.create_line(x, top_pad, x, chart_bottom, fill=UI_SOFT, dash=(2, 3))

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
            color = UI_RED if candle.close >= open_price else UI_BLUE
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
            draw_price_line(moving_average(candles, 5), UI_PINK)
        if self.show_ma20_var.get():
            draw_price_line(moving_average(candles, 20), UI_BLUE)

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
                    canvas.create_text(
                        x,
                        marker_y,
                        text="▲ 매수",
                        fill=UI_PINK,
                        font=(UI_DISPLAY_FONT, 8, "bold"),
                    )
                elif previous_point.pattern_state == "BULLISH" and point.pattern_state == "BEARISH":
                    marker_y = max(top_pad + 10, price_y(candle.high) - 13)
                    canvas.create_text(
                        x,
                        marker_y,
                        text="▼ 매도",
                        fill=UI_BLUE,
                        font=(UI_DISPLAY_FONT, 8, "bold"),
                    )
            previous_point = point

        candle_datetimes = [self._chart_datetime(candle.timestamp) for candle in displayed]
        maximum_distance = self._chart_marker_tolerance_seconds()
        for execution in getattr(self.service, "account_trade_history", ()):
            if normalize_symbol(getattr(execution, "symbol", "")) != normalize_symbol(
                self.symbol_var.get()
            ):
                continue
            executed_at = self._chart_datetime(getattr(execution, "timestamp", ""))
            if executed_at is None:
                continue
            candidates = [
                (abs((candle_at - executed_at).total_seconds()), index)
                for index, candle_at in enumerate(candle_datetimes)
                if candle_at is not None
            ]
            if not candidates:
                continue
            distance, index = min(candidates)
            if distance > maximum_distance:
                continue
            candle = displayed[index]
            side = str(getattr(execution, "side", "")).upper()
            x = left_pad + step * (index + 0.5)
            if side == "BUY":
                y = min(price_bottom - 9, price_y(candle.low) + 18)
                marker, color = "B", UI_PINK
            elif side == "SELL":
                y = max(top_pad + 9, price_y(candle.high) - 18)
                marker, color = "S", UI_BLUE
            else:
                continue
            canvas.create_oval(x - 8, y - 8, x + 8, y + 8, fill=color, outline="")
            canvas.create_text(
                x,
                y,
                text=marker,
                fill=UI_SURFACE,
                font=(UI_DISPLAY_FONT, 8, "bold"),
            )

        latest = displayed[-1]
        latest_y = price_y(latest.close)
        canvas.create_line(
            left_pad,
            latest_y,
            left_pad + plot_width,
            latest_y,
            fill=UI_MUTED,
            dash=(4, 3),
        )
        canvas.create_text(
            left_pad + plot_width + 8,
            latest_y,
            text=f"{latest.close:,.0f}",
            anchor="w",
            fill=UI_TEXT,
            font=(UI_DISPLAY_FONT, 8, "bold"),
        )

        if show_dmi:
            canvas.create_line(left_pad, dmi_top, left_pad + plot_width, dmi_top, fill="#6f7880")
            canvas.create_line(left_pad, dmi_bottom, left_pad + plot_width, dmi_bottom, fill="#6f7880")
            for level in (0, 25, 50, 75, 100):
                y = dmi_y(float(level))
                canvas.create_line(left_pad, y, left_pad + plot_width, y, fill=UI_BORDER, dash=(2, 3))
                canvas.create_text(
                    left_pad + plot_width + 8,
                    y,
                    text=str(level),
                    anchor="w",
                    fill=UI_MUTED,
                    font=(UI_FONT, 8),
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

            draw_dmi_line("plus_di", UI_PINK)
            draw_dmi_line("minus_di", UI_BLUE)
            draw_dmi_line("adx", UI_MUTED, 1)
            canvas.create_text(
                left_pad,
                dmi_top - 18,
                text="+DI  -DI  ADX",
                anchor="w",
                fill=UI_MUTED,
                font=(UI_DISPLAY_FONT, 8, "bold"),
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
                fill=UI_MUTED,
                font=(UI_FONT, 8),
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
            "use_log_scale": use_log_scale,
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
        previous_close = displayed[index - 1].close if index > 0 else candle.close
        change_rate = (
            (candle.close - previous_close) / previous_close * 100.0
            if previous_close > 0
            else 0.0
        )
        scale_label = " · 로그축" if state.get("use_log_scale") else ""
        tooltip = (
            f"{self._format_chart_timestamp(candle.timestamp)}\n"
            f"시가 {candle.open or candle.close:,.0f}  고가 {candle.high:,.0f}\n"
            f"저가 {candle.low:,.0f}  종가 {candle.close:,.0f}\n"
            f"전 봉 대비 {change_rate:+.2f}%{scale_label}\n"
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
            fill=UI_TEXT,
            font=(UI_FONT, 9),
            tags="crosshair",
        )
        bounds = canvas.bbox(text_id)
        if bounds:
            background = canvas.create_rectangle(
                bounds[0] - 6,
                bounds[1] - 5,
                bounds[2] + 6,
                bounds[3] + 5,
                fill=UI_SOFT,
                outline=UI_MUTED,
                tags="crosshair",
            )
            canvas.tag_lower(background, text_id)

    @staticmethod
    def _chart_datetime(timestamp: object) -> datetime | None:
        digits = "".join(character for character in str(timestamp or "") if character.isdigit())
        if len(digits) < 14:
            return None
        try:
            return datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
        except ValueError:
            return None

    def _chart_marker_tolerance_seconds(self) -> float:
        timeframe = self.service.chart_timeframe
        try:
            if timeframe.endswith("s"):
                return max(1.0, float(timeframe[:-1]) * 1.5)
            if timeframe.endswith("m"):
                return max(60.0, float(timeframe[:-1]) * 90.0)
        except ValueError:
            pass
        return 180.0

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

    def _send_order(self, side: str, action: str = "NEW") -> None:
        if not self._require_live_connection():
            return
        normalized_action = str(action or "NEW").strip().upper()
        if normalized_action not in {"NEW", "MODIFY", "CANCEL"}:
            normalized_action = "NEW"
        original_order_no = self.original_order_no_var.get().strip()
        if normalized_action in {"MODIFY", "CANCEL"} and not original_order_no:
            self._show_warning(
                "원주문번호 필요",
                "미체결 주문을 선택하거나 원주문번호를 입력해 주세요.",
            )
            return
        allow_real = self._real_order_session_ready()
        if not self._trading_ready():
            self._show_warning("주문 준비 필요", self.trade_ready_var.get())
            return
        if allow_real and not self._regular_market_open():
            self._show_warning(
                "정규장 장중 확인 필요",
                "키움 장시작시간(0s) 또는 주식체결(0B)에서 정규장 장중을 "
                "확인한 뒤 실거래 주문할 수 있습니다.",
            )
            return
        action_label = {"NEW": "신규", "MODIFY": "정정", "CANCEL": "취소"}[normalized_action]
        price_label = f"{self._order_price_label()} 지정" if normalized_action != "CANCEL" else "주문 취소"
        if allow_real and not self._confirm_real_order(
            f"{price_label} {action_label} 주문",
            original_order_no=original_order_no,
        ):
            return
        self.service.configure(self.symbol_var.get(), self._operating_capital(), self._settings())
        self.service.send_kiwoom_order(
            account=self._account_for_api(),
            side=side,
            quantity=self._order_quantity(),
            allow_real_order=allow_real,
            account_password=self._account_password_for_order(),
            order_style=self._selected_order_style(),
            action=normalized_action,
            original_order_no=original_order_no,
            use_margin=self.use_margin_var.get(),
        )
        if normalized_action == "CANCEL":
            self.original_order_no_var.set("")
        self._mark_holding_balance_refresh_due()
        self._schedule_recent_trade_history_refresh()
        self._handle_order_account_verification()
        self._refresh()

    def _evaluate_and_send_order(self, auto: bool = False) -> None:
        self._show_warning(
            "자동주문 사용 안 함",
            "자동매수·자동매도 기능이 제거되었습니다. 화면의 수동 매수·매도 버튼만 사용해 주세요.",
        )
        self._refresh()

    def _confirm_real_order(self, title: str, original_order_no: str = "") -> bool:
        original_text = (
            f"\n원주문번호 {original_order_no}" if original_order_no else ""
        )
        return messagebox.askyesno(
            "실거래 주문 최종 확인",
            f"{title}을 실행하려고 합니다.\n"
            f"종목 {self.symbol_var.get()} / 주문수량 {self._order_quantity()}주"
            f"{original_text}\n"
            f"신규·정정 주문 가격은 현재 선택한 {self._order_price_label()} 방식으로 다시 계산합니다.\n"
            "실거래 세션 승인이 켜져 있어 실제 주문이 전송될 수 있습니다.\n"
            "계좌, 종목, 수량을 다시 확인했습니다. 계속하시겠습니까?",
        )

    @staticmethod
    def _execution_voice_key(execution: object) -> tuple[object, ...]:
        return (
            str(getattr(execution, "order_no", "")),
            str(getattr(execution, "timestamp", "")),
            str(getattr(execution, "side", "")),
            normalize_symbol(getattr(execution, "symbol", "")),
            int(getattr(execution, "quantity", 0) or 0),
            float(getattr(execution, "price", 0.0) or 0.0),
        )

    def _announce_new_trade_executions(self, executions: list[object]) -> None:
        current = {
            self._execution_voice_key(execution)
            for execution in executions
        }
        if self._known_execution_keys is None:
            self._known_execution_keys = current
            return
        new_executions = [
            execution
            for execution in reversed(executions)
            if self._execution_voice_key(execution) not in self._known_execution_keys
        ]
        self._known_execution_keys.update(current)
        for execution in new_executions:
            side = str(getattr(execution, "side", "")).upper()
            if self.voice_notifier.announce_execution(side):
                self.service.storage.log(
                    "INFO",
                    "음성알림",
                    f"{normalize_symbol(getattr(execution, 'symbol', ''))} "
                    f"{SIDE_LABELS.get(side, side)} 체결 음성 안내",
                )
            sound_notifier = getattr(self, "sound_notifier", None)
            if sound_notifier is not None and sound_notifier.play_execution(side):
                self.service.storage.log(
                    "INFO",
                    "음향알림",
                    f"{normalize_symbol(getattr(execution, 'symbol', ''))} "
                    f"{SIDE_LABELS.get(side, side)} 체결 효과음 안내",
                )

    def _refresh(self) -> None:
        snapshot = self.service.snapshot()
        if snapshot.account_trade_history_updated_at is not None:
            self._announce_new_trade_executions(snapshot.account_trade_history)
        self._update_daily_traded_tags(snapshot.account_trade_history)
        self._draw_brand_rail()
        self._update_compact_monitor(snapshot)
        self._update_current_price_display()
        account = snapshot.account_info
        connection_method = account.connection_method or "OpenAPI+"
        if not account.connected:
            self._account_access_verified = False
        if not self._account_connection_confirmed(account):
            self._holding_balance_fresh = False
        self._sync_account_password_controls(account)
        self._update_connection_badge(self._account_connection_confirmed(account), connection_method)
        real_order_state = (
            "normal"
            if account.connected
            and account.server_type == "실거래"
            and self._account_connection_confirmed(account)
            else "disabled"
        )
        if real_order_state == "disabled" and self.allow_real_order_var.get():
            self._clear_real_order_authorization()
        self.allow_real_order_checkbutton.configure(state=real_order_state)
        self.market_session_var.set(
            "장 상태: "
            f"{_market_session_text(snapshot.market_session_status, snapshot.regular_market_open, snapshot.real_time_registered)}"
        )
        if snapshot.started_at is not None:
            started_label = snapshot.started_at.strftime("%Y-%m-%d %H:%M:%S")
            running_label = "운용 중" if snapshot.running else "최근 시작"
            self.auto_started_at_var.set(f"실시간 감시 시작 시각: {started_label} · {running_label}")
        self._update_auto_trade_capability(snapshot)
        self._update_holding_monitor(snapshot)
        self._update_side_summary(snapshot)
        self._update_dmi_display(snapshot.dmi)
        self.status_text.set(self._format_main_status(snapshot))
        if snapshot.symbol_name and self.symbol_name_var.get() != snapshot.symbol_name:
            self.symbol_name_var.set(snapshot.symbol_name)
        self.account_summary_var.set(self._format_account_summary(snapshot))
        self.trade_ready_var.set(self._format_trade_ready(snapshot))
        self._render_market_control(snapshot)
        if self.chart_timeframe_var.get() != snapshot.chart_timeframe:
            self.chart_timeframe_var.set(snapshot.chart_timeframe)
        chart_label = timeframe_label(snapshot.chart_timeframe)
        self.chart_caption_var.set(
            f"{snapshot.symbol} {snapshot.symbol_name} | {chart_label}봉 {len(snapshot.chart_candles)}개 | "
            f"{snapshot.chart_source} | 차트 DMI({self.service.strategy.settings.dmi_period}봉) | "
            f"키움 일봉 DMI({self.service.strategy.settings.dmi_period}일)"
        )
        self._update_trade_buttons()
        holdings = list(snapshot.balance_summary.holdings) if snapshot.balance_summary else []
        self._replace_rows(self.holdings, self._format_holdings(holdings))
        self._replace_rows(
            self.orders,
            self._format_recent_order_activity(
                snapshot.account_trade_history,
                snapshot.trade_history,
            ),
        )
        self._replace_rows(self.logs, self._format_logs(snapshot.logs))
        self._replace_trade_history_rows(
            self._format_combined_trade_history(
                snapshot.account_trade_history,
                snapshot.trade_history,
            )
        )
        history_status = snapshot.account_trade_history_message
        if snapshot.account_trade_history_updated_at is not None:
            history_status += (
                " · 마지막 조회 "
                f"{snapshot.account_trade_history_updated_at.strftime('%Y-%m-%d %H:%M:%S')}"
            )
        self.account_trade_history_status_var.set(history_status)
        if self.service.storage.trade_history_file_error:
            self.trade_history_file_var.set(
                f"프로그램 주문요청 CSV 오류: {self.service.storage.trade_history_file_error}"
            )
        else:
            self.trade_history_file_var.set(
                f"프로그램 주문요청 CSV: {self.service.storage.trade_history_path}"
            )
        if self._watchlist_window is not None and self._watchlist_window.winfo_exists():
            self._render_watchlist_rows()
        if not self._compact_mode:
            self.after_idle(self._draw_main_dmi_chart)
        self._schedule_refresh_tick()
        self._schedule_chart_refresh()
        self._schedule_volume_rank_refresh()
        self._schedule_trade_value_rank_refresh()

    def _update_dmi_display(self, dmi: DmiPoint | None) -> None:
        if dmi is None:
            self.dmi_state_var.set("계산 전")
            self.dmi_plus_var.set("-")
            self.dmi_minus_var.set("-")
            self.adx_var.set("-")
            self.dmi_state_badge.configure(style="Neutral.Badge.TLabel")
            return
        self.dmi_plus_var.set(f"{dmi.plus_di:.2f}")
        self.dmi_minus_var.set(f"{dmi.minus_di:.2f}")
        self.adx_var.set("계산 중" if dmi.adx is None else f"{dmi.adx:.2f}")
        if dmi.pattern_state == "BULLISH":
            self.dmi_state_var.set("강세")
            self.dmi_state_badge.configure(style="Pink.Badge.TLabel")
        elif dmi.pattern_state == "BEARISH":
            self.dmi_state_var.set("약세")
            self.dmi_state_badge.configure(style="Blue.Badge.TLabel")
        else:
            self.dmi_state_var.set("중립")
            self.dmi_state_badge.configure(style="Neutral.Badge.TLabel")

    def _update_auto_trade_capability(self, snapshot) -> None:
        self.auto_trade_capability_var.set("자동주문 미사용")
        self.auto_trade_capability_badge.configure(style="Neutral.Badge.TLabel")
        self.auto_trade_detail_var.set(
            "자동매수·자동매도 기능은 제공하지 않습니다. 수동 주문만 사용할 수 있습니다."
        )

    def _update_holding_monitor(self, snapshot) -> None:
        monitor_balance = snapshot.balance_summary
        if snapshot.running and not self._holding_balance_fresh:
            monitor_balance = None
        active, detail = _holding_monitor_display(
            running=snapshot.running,
            symbol=snapshot.symbol,
            symbol_name=snapshot.symbol_name,
            balance_summary=monitor_balance,
        )
        self.holding_monitor_state_var.set("감시 ON" if active else "감시 OFF")
        color = UI_BLUE if active else UI_RED
        badge_style = "Blue.Badge.TLabel" if active else "Danger.Badge.TLabel"
        self.holding_monitor_badge.configure(style=badge_style)
        self.holding_monitor_detail_var.set(detail)
        self.holding_monitor_detail_label.configure(foreground=color)

        current_state = (active, detail)
        previous_state = self._last_holding_monitor_state
        self._last_holding_monitor_state = current_state
        if previous_state is None or previous_state == current_state:
            return
        if active:
            self.service.storage.log("INFO", "보유감시", f"감시 ON · {detail}")
        elif previous_state[0]:
            self.service.storage.log("WARN", "보유감시", f"감시 OFF · {detail}")

    def _mark_holding_balance_refresh_due(self) -> None:
        self._next_holding_balance_refresh_at = 0.0

    def _refresh_holding_balance_if_due(self, force: bool = False) -> bool:
        if not self.service.running:
            return False
        if not self._account_connection_confirmed(self.service.account_info):
            self._holding_balance_fresh = False
            return False
        if (
            self.service.account_info.connection_method != "REST API"
            and not self._password_session_ready()
        ):
            self._holding_balance_fresh = False
            return False

        account = clean_account_number(self._account_for_api())
        if not account:
            self._holding_balance_fresh = False
            return False
        now = monotonic()
        if not force and now < self._next_holding_balance_refresh_at:
            return False
        self._next_holding_balance_refresh_at = now + HOLDING_MONITOR_REFRESH_SECONDS
        balance = self.service.request_balance(
            account,
            password=self._account_password_for_order(),
            log_result=False,
        )
        if balance is None:
            self._holding_balance_fresh = False
            self._next_holding_balance_refresh_at = now + (HOLDING_MONITOR_REFRESH_SECONDS * 3)
            return False
        self._holding_balance_fresh = True
        return True

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
        if account.server_type == "실거래":
            parts.append(
                "장 상태 "
                f"{_market_session_text(snapshot.market_session_status, snapshot.regular_market_open, snapshot.real_time_registered)}"
            )
        return " | ".join(parts)

    def _auto_tick(self) -> None:
        self._refresh_after_id = None
        if self.service.account_info.connected and not self._ensure_live_connection():
            self._refresh()
            return
        if self.service.running:
            self._refresh_holding_balance_if_due()
        self._refresh()

    def _schedule_refresh_tick(self) -> None:
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
            self._update_current_price_display()
            if self._compact_mode:
                self._update_compact_monitor(self.service)
            self._update_watchlist_live_row()
            if self.service.chart_timeframe.endswith("s"):
                candles = self.service.chart_candles_for_display()
                label = timeframe_label(self.service.chart_timeframe)
                self.chart_caption_var.set(
                    f"{self.service.symbol} {self.service.symbol_name} | {label}봉 {len(candles)}개 | "
                    f"{self.service.chart_source} | 차트 DMI({self.service.strategy.settings.dmi_period}봉) | "
                    f"키움 일봉 DMI({self.service.strategy.settings.dmi_period}일)"
                )
                if not self._compact_mode:
                    self._draw_main_dmi_chart()
                    self._draw_popup_chart()
        self._schedule_chart_refresh()

    def _replace_rows(self, table: ttk.Treeview, rows: list[tuple]) -> None:
        for item in table.get_children():
            table.delete(item)
        for row in rows:
            table.insert("", "end", values=row)

    def _replace_trade_history_rows(self, rows: list[tuple]) -> None:
        for item in self.trade_history.get_children():
            self.trade_history.delete(item)
        for row in rows:
            side = row[1]
            result = row[7]
            tag = "FAILED" if result == "실패" else ("BUY" if side == "매수" else "SELL")
            self.trade_history.insert("", "end", values=row, tags=(tag,))

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

    def _format_trade_history(self, rows: list[tuple]) -> list[tuple]:
        formatted = []
        for (
            timestamp,
            side,
            symbol,
            symbol_name,
            quantity,
            price,
            total_amount,
            success,
            order_no,
            order_mode,
            message,
        ) in rows:
            formatted.append(
                (
                    str(timestamp).replace("T", " "),
                    SIDE_LABELS.get(side, side),
                    normalize_symbol(symbol),
                    symbol_name or "-",
                    f"{int(quantity):,}주",
                    f"{float(price):,.0f}원" if price else "-",
                    f"{float(total_amount):,.0f}원" if total_amount else "-",
                    "접수" if success else "실패",
                    order_no or "-",
                    order_mode or "-",
                    message,
                )
            )
        return formatted

    def _format_combined_trade_history(self, executions: list, local_rows: list[tuple]) -> list[tuple]:
        remote_rows = []
        remote_order_nos = set()
        for execution in executions:
            if execution.order_no:
                remote_order_nos.add(execution.order_no)
            remote_rows.append(
                (
                    str(execution.timestamp).replace("T", " "),
                    SIDE_LABELS.get(execution.side, execution.side),
                    normalize_symbol(execution.symbol),
                    execution.symbol_name or "-",
                    f"{int(execution.quantity):,}주",
                    f"{float(execution.price):,.0f}원" if execution.price else "-",
                    f"{float(execution.total_amount):,.0f}원" if execution.total_amount else "-",
                    execution.status or "체결",
                    execution.order_no or "-",
                    execution.order_mode or "키움 계좌",
                    execution.message,
                )
            )

        range_start = (
            datetime.now() - timedelta(days=ACCOUNT_TRADE_HISTORY_DAYS)
        ).strftime("%Y-%m-%d")
        recent_local_rows = [
            row
            for row in local_rows
            if str(row[0]).replace("T", " ")[:10] >= range_start
        ]
        local_formatted = self._format_trade_history(recent_local_rows)
        for row in local_formatted:
            order_no = row[8]
            if row[7] == "접수" and order_no not in ("", "-") and order_no in remote_order_nos:
                continue
            remote_rows.append(row)
        remote_rows.sort(key=lambda row: str(row[0]), reverse=True)
        return remote_rows

    def _format_recent_order_activity(
        self,
        executions: list,
        local_rows: list[tuple],
        limit: int = 10,
    ) -> list[tuple]:
        combined = self._format_combined_trade_history(executions, local_rows)
        combined.sort(key=lambda row: str(row[0]), reverse=True)
        recent_rows = []
        for row in combined[: max(1, int(limit))]:
            full_timestamp = str(row[0])
            compact_timestamp = (
                full_timestamp[5:19]
                if len(full_timestamp) >= 19 and full_timestamp[4] == "-"
                else full_timestamp
            )
            recent_rows.append(
                (
                    compact_timestamp,
                    row[2],
                    row[1],
                    row[4],
                    row[5],
                    row[7],
                    row[10],
                )
            )
        return recent_rows

    def _format_holdings(self, rows: list) -> list[tuple]:
        formatted = []
        for holding in rows:
            formatted.append(
                (
                    normalize_symbol(holding.symbol),
                    holding.name,
                    holding.quantity,
                    holding.sellable_quantity or holding.quantity,
                    f"{holding.average_price:,.0f}",
                    f"{holding.current_price:,.0f}",
                    f"{holding.purchase_amount or holding.average_price * holding.quantity:,.0f}",
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
        selected_label = self._privacy_account_label(selected)
        if not snapshot.account_info.connected:
            return (
                f"계좌 창: 미연결 | 선택 계좌 {selected_label} | "
                "OpenAPI+ 로그인 또는 REST API 연결 후 계좌번호 앞4자리+뒤4자리와 잔고가 표시됩니다."
            )
        connection_method = snapshot.account_info.connection_method or "OpenAPI+"
        if not self._account_connection_confirmed(snapshot.account_info):
            return (
                f"계좌 창: {connection_method} 로그인 완료 / 계좌 미연결 | 선택 계좌 {selected_label} | "
                "'계좌잔고 불러오기'를 눌러 계좌 접근을 확인해 주세요."
            )
        if not snapshot.balance_summary:
            balance_help = (
                "'계좌잔고 불러오기'를 누르면 잔고가 표시됩니다."
                if connection_method == "REST API"
                else "계좌 비밀번호 입력 후 '비밀번호 세팅'을 누르면 잔고가 표시됩니다."
            )
            return (
                f"계좌 창: {connection_method} 연결됨({snapshot.account_info.server_type}) | "
                f"선택 계좌 {selected_label} | {balance_help}"
            )
        balance = snapshot.balance_summary
        return (
            f"계좌 창: {connection_method} 연결됨({snapshot.account_info.server_type}) | "
            f"선택 계좌 {self._privacy_account_label(balance.account)} | "
            f"예수금 {balance.deposit:,.0f}원 | 주문가능 {balance.orderable_amount:,.0f}원 | "
            f"출금가능 {balance.withdrawable_amount:,.0f}원 | 보유 {len(balance.holdings)}종목 | "
            f"추정예탁자산 {balance.estimated_assets:,.0f}원 | "
            f"평가금액 {balance.total_evaluation:,.0f} | 평가손익 {balance.total_profit_loss:,.0f} | "
            f"당일 실현손익 {snapshot.daily_performance.realized_profit:+,.0f}원 | "
            f"수익률 {balance.total_profit_rate:.2f}%"
        )

    def _format_trade_ready(self, snapshot) -> str:
        account = clean_account_number(self._account_for_api())
        name = snapshot.symbol_name or self.symbol_name_var.get().strip()
        quantity_ok = self._order_quantity_valid()
        baseline_ready = self._selected_trading_baseline() is not None
        account_connected = self._account_connection_confirmed(snapshot.account_info)
        server_ready = (
            snapshot.account_info.server_type == "모의투자" or self._real_order_session_ready()
        )
        baseline = self._selected_trading_baseline()
        if (
            account_connected
            and account
            and name
            and quantity_ok
            and baseline is not None
            and server_ready
        ):
            order_mode = "모의주문" if snapshot.account_info.server_type == "모의투자" else "실거래 주문"
            return (
                f"거래 준비 완료: {snapshot.symbol} {name} | 계좌 {self._privacy_account_label(account)} | "
                f"고정 운용금액 {baseline.capital_limit:,.0f}원 | "
                f"{self._order_quantity()}주 수동 {self._order_price_label()} 매수/매도 {order_mode} 가능"
            )
        missing = []
        if not snapshot.account_info.connected:
            missing.append("키움 API 로그인")
        elif not account_connected:
            missing.append("계좌연결")
        if not account:
            missing.append("계좌번호")
        if not name or name == "키움 로그인 후 조회 필요":
            missing.append("회사명")
        if not quantity_ok:
            missing.append("주문수량")
        if not baseline_ready:
            missing.append("금액 세팅")
        if snapshot.account_info.server_type == "실거래" and not self.allow_real_order_var.get():
            missing.append("실거래 세션 승인")
        elif snapshot.account_info.server_type == "실거래" and not self._real_order_session_armed:
            missing.append("실거래 세션 승인 확인")
        return f"거래 준비: {', '.join(missing)} 확인이 필요합니다."

    def _trading_ready(self) -> bool:
        account = clean_account_number(self._account_for_api())
        name = self.symbol_name_var.get().strip()
        baseline_ready = self._selected_trading_baseline() is not None
        server_ready = (
            self.service.account_info.server_type == "모의투자" or self._real_order_session_ready()
        )
        return bool(
            self._account_connection_confirmed(self.service.account_info)
            and account
            and name
            and name != "키움 로그인 후 조회 필요"
            and self._order_quantity_valid()
            and baseline_ready
            and server_ready
        )

    def _order_quantity_valid(self) -> bool:
        return self._order_quantity() > 0

    def _order_quantity(self) -> int:
        return _parse_order_quantity(self.order_qty_var.get())

    def _change_dmi_period(self, delta: int) -> None:
        current = _clamp_dmi_period(self.dmi_period_var.get())
        period = _clamp_dmi_period(current + int(delta))
        self.dmi_period_var.set(period)
        self.dmi_period_display_var.set(f"{period}일")
        if period == current:
            return

        if self.service.running:
            self.service.stop()
        if self._real_order_session_ready():
            self._clear_real_order_authorization(
                "DMI 계산 기간 변경으로 실거래 세션 승인을 해제했습니다."
            )
        self.service.configure(
            self.symbol_var.get(),
            self._operating_capital(),
            StrategySettings(dmi_period=period),
        )
        self.service.storage.log(
            "INFO",
            "DMI",
            f"DMI 일봉 계산 기간을 {period}일로 변경했습니다. 자동주문 조건을 다시 설정해 주세요.",
        )
        self._update_dmi_display(None)
        self._refresh()

    def _on_trade_mode_changed(self) -> None:
        self.service.storage.log(
            "WARN",
            "주문모드",
            "자동매수·자동매도 기능이 제거되어 수동 주문만 사용할 수 있습니다.",
        )
        self._refresh()

    def _selected_order_style(self) -> str:
        return "MIDPOINT"

    def _order_price_label(self) -> str:
        return "중간가"

    def _on_order_price_mode_changed(self) -> None:
        self.order_price_mode_var.set("MIDPOINT")
        self.service.storage.set_app_setting("orders.price_mode", "MIDPOINT")
        self._apply_order_price_mode_labels()
        self.service.storage.log("INFO", "주문", "수동 주문은 중간가 지정가로 전송합니다.")
        self._refresh()

    def _apply_order_price_mode_labels(self) -> None:
        label = self._order_price_label()
        self.buy_button.configure(text=f"수동 {label} 매수")
        self.sell_button.configure(text=f"수동 {label} 매도")
        self.modify_button.configure(text=f"수동 {label} 정정")

    def _activate_quick_order(self, slot: int) -> None:
        index = max(0, min(9, int(slot) - 1))
        self.quick_slot_var.set(index + 1)
        quantity = self._quick_order_presets[index]
        self.order_qty_var.set(str(quantity))
        self.order_qty_display_var.set(f"{quantity}주")
        for button_index, button in enumerate(self.quick_order_buttons):
            button.configure(
                style="Accent.TButton" if button_index == index else "TButton"
            )
        self._refresh()

    def _save_selected_quick_order(self) -> None:
        quantity = self._order_quantity()
        if quantity <= 0:
            self._show_warning("퀵 주문 저장", "저장할 주문 수량은 1주 이상이어야 합니다.")
            return
        index = max(0, min(9, self.quick_slot_var.get() - 1))
        self._quick_order_presets[index] = quantity
        self.quick_order_buttons[index].configure(text=f"{index + 1}·{quantity}주")
        self.service.storage.set_app_setting(
            "orders.quick_quantities",
            json.dumps(self._quick_order_presets, ensure_ascii=False),
        )
        self.service.storage.log(
            "INFO",
            "퀵주문",
            f"퀵 주문 {index + 1}번을 {quantity}주로 저장했습니다.",
        )
        self._activate_quick_order(index + 1)

    def _change_order_quantity(self, delta: int) -> None:
        quantity = max(0, self._order_quantity() + int(delta))
        if quantity != self._order_quantity() and self._real_order_session_ready():
            if self.service.running:
                self.service.stop()
            self._clear_real_order_authorization(
                "주문 수량 변경으로 실거래 세션 승인을 해제했습니다."
            )
        self.order_qty_var.set(str(quantity))
        self.order_qty_display_var.set(f"{quantity}주")
        self._refresh()

    def _selected_symbol_ready(self) -> bool:
        symbol = normalize_symbol(self.symbol_var.get())
        name = self.symbol_name_var.get().strip()
        invalid_names = {"조회 실패", "키움 로그인 후 조회 필요"}
        return bool(symbol and symbol != "000000" and name and name not in invalid_names)

    def _selected_current_price(self) -> float:
        symbol = normalize_symbol(self.symbol_var.get())
        for quote in (self.service.real_time_quote, self.service.market_quote):
            if quote is None or normalize_symbol(quote.symbol) != symbol:
                continue
            if quote.current_price > 0:
                return float(quote.current_price)
        return 0.0

    def _update_current_price_display(self) -> None:
        current_price = self._selected_current_price()
        self.current_price_display_var.set(f"{current_price:,.0f}원" if current_price > 0 else "미조회")

    def _update_trade_buttons(self) -> None:
        state = "normal" if self._trading_ready() else "disabled"
        self.buy_button.configure(state=state)
        self.sell_button.configure(state=state)
        original_ready = bool(self.original_order_no_var.get().strip())
        correction_state = state if original_ready else "disabled"
        self.modify_button.configure(state=correction_state)
        self.cancel_button.configure(state=correction_state)
        chart_state = "normal" if self.service.account_info.connected else "disabled"
        self.chart_button.configure(state=chart_state)

    def _require_live_connection(self) -> bool:
        if self._ensure_live_connection():
            return True
        self._refresh()
        detail = self.service.account_info.message or (
            "현재 주문 네트워크 연결에 문제가 발생했습니다. 키움 API 연결 상태를 확인해 주세요."
        )
        self._show_warning("키움 API 연결 필요", detail)
        return False

    def _ensure_live_connection(self) -> bool:
        if self.service.sync_account_connection():
            return True
        self._clear_real_order_authorization()
        self._clear_account_password_session()
        self._selected_account_full = ""
        self._holding_balance_fresh = False
        self._set_account_display("")
        if self._account_info_window is not None and self._account_info_window.winfo_exists():
            self._account_info_window.destroy()
        self._account_info_window = None
        self._close_candle_chart()
        self._update_connection_badge(False)
        self._update_trade_buttons()
        return False

    def _sync_account_password_controls(self, account_info) -> None:
        connection_method = account_info.connection_method or "OpenAPI+"
        if not account_info.connected:
            self._clear_account_password_session()
            state = "disabled"
        elif connection_method == "REST API":
            self._clear_account_password_session(status="REST 불필요")
            state = "disabled"
        else:
            account = clean_account_number(self._account_for_api())
            if not account:
                self._clear_account_password_session(status="계좌 대기")
                state = "disabled"
            else:
                state = "normal"
            if self._account_access_verified and self._password_session_ready():
                self.account_password_status_var.set("확인됨")
            elif self.account_password_status_var.get() not in {
                "계좌 대기",
                "입력 중",
                "확인 중",
                "확인 실패",
            }:
                self.account_password_status_var.set("미확인")
        self.account_password_entry.configure(state=state)
        button_text = (
            "비밀번호 재설정"
            if connection_method != "REST API" and self._password_session_ready()
            else "비밀번호 세팅"
        )
        self.account_password_button.configure(state=state, text=button_text)

    def _update_connection_badge(self, connected: bool, connection_method: str = "") -> None:
        if connected:
            self.connection_state_var.set("ON 연결됨")
            self.connection_badge.configure(style="Success.Badge.TLabel")
            self.connection_light.itemconfigure(
                self._connection_light_id,
                fill="#28B463",
                outline=UI_GREEN,
            )
            self._update_transfer_status("good")
        else:
            self.connection_state_var.set("OFF 연결 안됨")
            self.connection_badge.configure(style="Neutral.Badge.TLabel")
            self.connection_light.itemconfigure(
                self._connection_light_id,
                fill="#b0b0b0",
                outline=UI_MUTED,
            )
            self._update_transfer_status("waiting")

    def _update_transfer_status(self, state: str) -> None:
        values = {
            "good": ("전송 상태 양호", "Success.Badge.TLabel", UI_GREEN, "#0F6D31"),
            "error": ("전송 상태 오류", "Danger.Badge.TLabel", UI_RED, "#C8384A"),
            "waiting": ("전송 상태 대기", "Neutral.Badge.TLabel", "#B8B8B8", UI_MUTED),
        }
        text_value, style_name, fill, outline = values.get(state, values["waiting"])
        self.transfer_state_var.set(text_value)
        self.transfer_badge.configure(style=style_name)
        self.transfer_light.itemconfigure(
            self._transfer_light_id,
            fill=fill,
            outline=outline,
        )

    def _account_connection_confirmed(self, account_info) -> bool:
        account = self._account_for_api()
        if not account and account_info.accounts:
            account = account_info.accounts[0]
        password_verified = self._account_access_verified and _account_password_session_ready(
            account_info.connection_method,
            account,
            self._session_password_account,
            self.account_password_var.get(),
        )
        return _account_access_confirmed(
            account_info.connected,
            account_info.connection_method,
            account,
            password_verified,
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
        window.configure(background=UI_BACKGROUND)
        window.transient(self)
        window.protocol("WM_DELETE_WINDOW", window.destroy)

        body = ttk.Frame(window, padding=18)
        body.grid(row=0, column=0, sticky="nsew")
        ttk.Label(body, text="계좌정보", font=(UI_DISPLAY_FONT, 15, "bold")).grid(
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
            ("API 확인 계좌", self._privacy_account_label(self._account_for_api() or self._selected_account_full)),
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
                else "메인 화면에서 계좌 비밀번호 입력 후 비밀번호 세팅을 눌러 주세요."
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
            return "계좌 / 현재가 / 0B 실시간 / 0s 장 상태 / 3분봉 / 잔고 / 실주문(세션 승인·정규장)"
        if account_info.connection_method == "REST API":
            return "계좌 / 현재가 / 0B 실시간 / 0s 장 상태 / 3분봉 / 잔고 / 모의주문"
        return "계좌 / 현재가 / 장 상태 / 실시간 시세 / 3분봉 / 잔고 / 주문"

    def _account_for_api(self) -> str:
        entered = clean_account_number(self.account_var.get())
        selected = clean_account_number(self._selected_account_full)
        if selected and entered == clean_account_number(mask_account_number(selected)):
            return selected
        return entered or selected

    def _account_history_display_values(self) -> tuple[str, ...]:
        return tuple(mask_account_except_last_two(account) for account in self._account_history)

    def _remember_account(self, account: str) -> None:
        normalized = clean_account_number(account)
        if not normalized:
            return
        self._account_history = normalize_account_history(
            [normalized, *self._account_history]
        )
        self.service.storage.set_app_setting(
            "account.history",
            json.dumps(self._account_history, ensure_ascii=False),
        )
        if hasattr(self, "account_history_combo"):
            self.account_history_combo.configure(
                values=self._account_history_display_values()
            )
            self.account_history_var.set(mask_account_except_last_two(normalized))

    def _on_account_history_selected(self, _event: tk.Event | None = None) -> None:
        display_value = self.account_history_var.get().strip()
        values = self._account_history_display_values()
        try:
            account = self._account_history[values.index(display_value)]
        except (ValueError, IndexError):
            return
        available = {
            clean_account_number(candidate)
            for candidate in self.service.account_info.accounts
        }
        if available and clean_account_number(account) not in available:
            self._show_warning(
                "계좌 선택 확인",
                "현재 API 로그인에서 확인되지 않은 과거 계좌입니다.",
            )
            return
        self._selected_account_full = account
        self._clear_account_password_session()
        self._set_account_display(account)
        self.service.storage.log(
            "INFO",
            "계좌",
            f"최근 계좌 {mask_account_except_last_two(account)}를 선택했습니다.",
        )
        self._refresh()

    def _set_account_display(self, account: str) -> None:
        digits = display_account_number(account)
        if self._session_password_account and clean_account_number(
            self._session_password_account
        ) != clean_account_number(account):
            self._clear_account_password_session()
        if self.account_mask_enabled and len(digits) >= 8:
            self.account_first_var.set("****")
            self.account_last_var.set(f"**{digits[-2:]}")
        else:
            self.account_first_var.set(digits[:4] if len(digits) >= 4 else "")
            self.account_last_var.set(digits[4:8] if len(digits) >= 8 else "")
        self.account_var.set(mask_account_number(digits) if digits else "")
        if digits:
            self._remember_account(digits)

    def _privacy_account_label(self, account: str) -> str:
        digits = display_account_number(account)
        if not digits:
            return "미선택"
        if self.account_mask_enabled:
            return mask_account_except_last_two(digits)
        return mask_account_number(digits)

    def _settings(self) -> StrategySettings:
        return StrategySettings(dmi_period=_clamp_dmi_period(self.dmi_period_var.get()))
