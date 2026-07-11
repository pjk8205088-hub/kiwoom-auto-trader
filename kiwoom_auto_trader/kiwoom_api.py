from __future__ import annotations

import os
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Any, Callable

from .charting import SUPPORTED_MINUTE_INTERVALS
from .models import (
    AccountCash,
    BalanceSummary,
    Candle,
    Holding,
    KiwoomOrderRequest,
    MarketQuote,
    RealTimeQuote,
    WatchlistQuote,
)
from .symbols import normalize_symbol

try:
    import pythoncom
    import win32com.client
except ImportError:  # pragma: no cover - exercised only on systems without pywin32.
    pythoncom = None
    win32com = None


class KiwoomOpenApiError(RuntimeError):
    pass


KIWOOM_HOME_PAGE = "https://www1.kiwoom.com/h/main"
KIWOOM_SETUP_GUIDE = (
    "준비 순서: 1) 키움 OpenAPI+ 서비스 사용 등록, "
    "2) OpenAPI+ 모듈 설치, 3) 공동인증서/HTS ID 준비, "
    "4) 32비트 실행 파일로 다시 실행"
)
DEFAULT_TR_TIMEOUT_SECONDS = 10.0
DEFAULT_COM_RETRY_SECONDS = 8.0
COM_CALL_REJECTED = -2147418113
KIWOOM_LOGIN_ERRORS = {
    0: "로그인에 성공했습니다.",
    -100: "사용자 정보 교환에 실패했습니다.",
    -101: "키움 서버에 연결할 수 없습니다.",
    -102: "버전 정보가 맞지 않습니다. OpenAPI+ 모듈을 업데이트해 주세요.",
}
LOGIN_WINDOW_TITLE_NEEDLES = (
    "open api login",
    "openapi login",
    "open api 로그인",
    "키움증권 open api",
    "khopenapi",
)
LOGIN_WINDOW_FOCUS_ATTEMPTS = 12


def is_valid_account_password(password: str) -> bool:
    value = str(password or "").strip()
    return value.isdigit() and 4 <= len(value) <= 8


class KiwoomRequestLimiter:
    """키움 공식 TR 제한을 넘기기 전에 호출을 잠시 대기시킨다."""

    limits = ((1.0, 5), (60.0, 100), (3600.0, 1000))

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleeper
        self._requests: deque[float] = deque()

    def acquire(self) -> None:
        while True:
            now = self._clock()
            while self._requests and now - self._requests[0] >= 3600.0:
                self._requests.popleft()

            delay = 0.0
            request_times = tuple(self._requests)
            for window, maximum in self.limits:
                recent = [requested_at for requested_at in request_times if now - requested_at < window]
                if len(recent) >= maximum:
                    delay = max(delay, window - (now - recent[0]) + 0.001)

            if delay <= 0:
                self._requests.append(now)
                return
            self._sleep(delay)


def _to_number(value: Any) -> float:
    text = str(value or "").strip().replace(",", "").replace("+", "")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _to_price(value: Any) -> float:
    return abs(_to_number(value))


def _to_int(value: Any) -> int:
    return int(_to_price(value))


class _KiwoomEventSink:
    def OnReceiveTrData(
        self,
        screen_no: str,
        rqname: str,
        trcode: str,
        record_name: str,
        prev_next: str,
        data_len: int,
        error_code: str,
        message: str,
        splm_msg: str,
    ) -> None:
        client = getattr(self, "_client", None)
        if client is not None:
            client._handle_tr_data(screen_no, rqname, trcode, record_name, prev_next)

    def OnReceiveRealData(self, code: str, real_type: str, real_data: str) -> None:
        client = getattr(self, "_client", None)
        if client is not None:
            client._handle_real_data(code, real_type, real_data)

    def OnEventConnect(self, error_code: int) -> None:
        client = getattr(self, "_client", None)
        if client is not None:
            client._last_login_error = int(error_code)


@dataclass(frozen=True)
class KiwoomEnvironmentStatus:
    process_bits: int
    is_32bit_process: bool
    pywin32_available: bool
    active_x_available: bool
    message: str
    setup_guide: str = KIWOOM_SETUP_GUIDE


@dataclass(frozen=True)
class KiwoomAccountInfo:
    connected: bool
    accounts: list[str]
    user_id: str = ""
    user_name: str = ""
    server_type: str = ""
    message: str = ""
    reported_account_count: int = 0
    login_event_code: int | None = None
    connection_method: str = "OpenAPI+"

    @property
    def account_count(self) -> int:
        return len(self.accounts)

    @property
    def login_data_received(self) -> bool:
        return bool(
            self.connected
            and self.user_id.strip()
            and self.accounts
            and self.reported_account_count == len(self.accounts)
        )


class KiwoomOpenApiClient:
    control_id = "KHOPENAPI.KHOpenAPICtrl.1"

    def __init__(
        self,
        dispatch_factory: Callable[[], Any] | None = None,
        request_limiter: KiwoomRequestLimiter | None = None,
        login_window_nudger: Callable[[], None] | None = None,
    ) -> None:
        self._dispatch_factory = dispatch_factory
        self._api: Any | None = None
        self._screen_no = 1000
        self._tr_results: dict[str, Any] = {}
        self._tr_parsers: dict[str, Callable[[str, str, str], Any]] = {}
        self._real_quotes: dict[str, RealTimeQuote] = {}
        self._real_quote_events: deque[RealTimeQuote] = deque(maxlen=5000)
        self._last_login_error: int | None = None
        self._last_comm_connect_result: int | None = None
        self._login_window_status = ""
        self._login_window_nudger = login_window_nudger or self._nudge_login_window_to_front
        self._request_limiter = request_limiter or KiwoomRequestLimiter()

    def check_environment(self) -> KiwoomEnvironmentStatus:
        process_bits = struct.calcsize("P") * 8
        is_32bit = process_bits == 32
        pywin32_available = self._dispatch_factory is not None or (
            pythoncom is not None and win32com is not None
        )
        active_x_available = False

        if pywin32_available and (is_32bit or self._dispatch_factory is not None):
            try:
                api = self._ensure_api()
                active_x_available = api is not None
            except KiwoomOpenApiError:
                active_x_available = False

        if not is_32bit and self._dispatch_factory is None:
            message = (
                f"현재 프로그램이 {process_bits}비트로 실행 중입니다. "
                "키움 OpenAPI+는 32비트 ActiveX이므로 KiwoomAutoTrader-32bit.exe로 실행해야 합니다. "
                f"{KIWOOM_SETUP_GUIDE}"
            )
        elif not pywin32_available:
            message = (
                "pywin32가 설치되어 있지 않아 키움 OpenAPI+를 호출할 수 없습니다. "
                f"{KIWOOM_SETUP_GUIDE}"
            )
        elif not active_x_available:
            message = (
                "키움 OpenAPI+ ActiveX를 찾을 수 없습니다. "
                "키움 OpenAPI+ 모듈 설치와 서비스 사용 등록을 확인해 주세요. "
                f"{KIWOOM_SETUP_GUIDE}"
            )
        else:
            message = "키움 OpenAPI+ 연결 환경이 준비되어 있습니다."

        return KiwoomEnvironmentStatus(
            process_bits,
            is_32bit,
            pywin32_available,
            active_x_available,
            message,
        )

    def start_login(self) -> str:
        status = self.check_environment()
        if not status.active_x_available:
            raise KiwoomOpenApiError(status.message)

        api = self._ensure_api()
        if self.is_connected():
            return "이미 키움 OpenAPI에 연결되어 있습니다."

        self._last_login_error = None
        self._last_comm_connect_result = None
        self._login_window_status = "키움 OpenAPI+ 로그인 창을 찾는 중입니다."
        try:
            result = self._call_api(lambda: api.CommConnect())
        except Exception as exc:
            raise KiwoomOpenApiError(f"CommConnect 호출 중 오류가 발생했습니다: {exc}") from exc
        try:
            self._last_comm_connect_result = 0 if result is None else int(result)
        except (TypeError, ValueError) as exc:
            raise KiwoomOpenApiError(f"CommConnect 반환코드를 해석할 수 없습니다: {result}") from exc
        if self._last_comm_connect_result != 0:
            raise KiwoomOpenApiError(
                f"CommConnect 호출 실패 (반환코드 {self._last_comm_connect_result}). "
                "키움 OpenAPI+ 서비스 사용신청, 모듈 설치와 버전을 확인해 주세요."
            )
        try:
            self._login_window_nudger()
        except Exception:
            self._login_window_status = "로그인 창 전면 표시 보조 기능을 시작하지 못했습니다."
        return (
            "키움 로그인 창 요청을 완료했습니다 "
            f"(CommConnect 반환코드 {self._last_comm_connect_result}). "
            "공식 로그인 창에서 인증을 완료해 주세요."
        )

    def is_connected(self) -> bool:
        api = self._ensure_api()
        try:
            return int(self._call_api(lambda: api.GetConnectState())) == 1
        except Exception as exc:  # pragma: no cover - depends on COM runtime.
            raise KiwoomOpenApiError(f"연결 상태 확인 실패: {exc}") from exc

    def get_account_info(self) -> KiwoomAccountInfo:
        api = self._ensure_api()
        if not self.is_connected():
            return KiwoomAccountInfo(False, [], message="키움 OpenAPI에 아직 연결되지 않았습니다.")

        raw_accounts = str(self._call_api(lambda: api.GetLoginInfo("ACCNO")) or "")
        if not raw_accounts:
            raw_accounts = str(self._call_api(lambda: api.GetLoginInfo("ACCLIST")) or "")

        accounts = [account.strip() for account in raw_accounts.split(";") if account.strip()]
        raw_account_count = str(self._call_api(lambda: api.GetLoginInfo("ACCOUNT_CNT")) or "").strip()
        try:
            reported_account_count = max(0, int(raw_account_count))
        except ValueError:
            reported_account_count = 0
        user_id = str(self._call_api(lambda: api.GetLoginInfo("USER_ID")) or "").strip()
        user_name = str(self._call_api(lambda: api.GetLoginInfo("USER_NAME")) or "").strip()
        server_type = self.get_server_name()
        if not user_id:
            message = "OpenAPI+ 연결은 되었지만 USER_ID 정보가 수신되지 않았습니다."
        elif not accounts:
            message = f"{server_type} 로그인은 완료되었지만 ACCNO 계좌 정보가 수신되지 않았습니다."
        elif reported_account_count != len(accounts):
            message = (
                "키움 계좌 개수 정보가 일치하지 않습니다. "
                f"ACCOUNT_CNT {reported_account_count}개 / ACCNO {len(accounts)}개"
            )
        else:
            message = f"키움 로그인 정보 수신 완료: {server_type}, 계좌 {len(accounts)}개"
        return KiwoomAccountInfo(
            connected=True,
            accounts=accounts,
            user_id=user_id,
            user_name=user_name,
            server_type=server_type,
            message=message,
            reported_account_count=reported_account_count,
            login_event_code=self._last_login_error,
        )

    def get_server_gubun(self) -> str:
        api = self._ensure_api()
        value = str(self._call_api(lambda: api.GetLoginInfo("GetServerGubun")) or "").strip()
        return value

    def is_mock_server(self) -> bool:
        return self.get_server_gubun() == "1"

    def get_server_name(self) -> str:
        return "모의투자" if self.is_mock_server() else "실거래"

    @property
    def last_login_error(self) -> int | None:
        return self._last_login_error

    @property
    def last_comm_connect_result(self) -> int | None:
        return self._last_comm_connect_result

    @property
    def login_window_status(self) -> str:
        return self._login_window_status

    def login_status_message(self) -> str:
        self.pump_messages()
        if self.is_connected():
            return f"키움 로그인 완료 ({self.get_server_name()} 서버)"
        if self._last_login_error is None:
            details = ["키움 로그인 진행 중입니다. 공식 로그인 창에서 인증을 완료해 주세요."]
            if self._last_comm_connect_result is not None:
                details.append(f"CommConnect 반환코드 {self._last_comm_connect_result}")
            if self._login_window_status:
                details.append(self._login_window_status)
            return " | ".join(details)
        detail = KIWOOM_LOGIN_ERRORS.get(
            self._last_login_error,
            "알 수 없는 로그인 오류가 발생했습니다.",
        )
        return f"키움 로그인 실패: {detail} (오류코드 {self._last_login_error})"

    def request_current_price(self, symbol: str) -> MarketQuote:
        symbol = normalize_symbol(symbol)
        if not symbol:
            raise KiwoomOpenApiError("종목코드를 입력해 주세요.")
        return self._request_tr(
            "현재가조회",
            "opt10001",
            {"종목코드": symbol},
            self._parse_current_price,
        )

    def request_watchlist_quotes(self, symbols: list[str]) -> list[WatchlistQuote]:
        normalized = list(
            dict.fromkeys(
                symbol
                for symbol in (normalize_symbol(value) for value in symbols)
                if symbol
            )
        )
        quotes: list[WatchlistQuote] = []
        for symbol in normalized[:20]:
            quote = self.request_current_price(symbol)
            quotes.append(
                WatchlistQuote(
                    symbol=normalize_symbol(quote.symbol) or symbol,
                    name=quote.name,
                    current_price=quote.current_price,
                    change=quote.change,
                    change_rate=quote.change_rate,
                    volume=quote.volume,
                    timestamp=quote.timestamp,
                )
            )
        return quotes

    def request_minute_candles(self, symbol: str, interval: int = 3, count: int = 60) -> list[Candle]:
        symbol = normalize_symbol(symbol)
        if not symbol:
            raise KiwoomOpenApiError("종목코드를 입력해 주세요.")
        interval = max(1, int(interval))
        if interval not in SUPPORTED_MINUTE_INTERVALS:
            supported = ", ".join(str(value) for value in SUPPORTED_MINUTE_INTERVALS)
            raise KiwoomOpenApiError(f"분봉 간격은 {supported}분만 지원합니다.")
        count = max(1, int(count))
        candles = self._request_tr(
            "분봉조회",
            "opt10080",
            {
                "종목코드": symbol,
                "틱범위": str(interval),
                "수정주가구분": "1",
            },
            self._parse_minute_candles,
        )
        return candles[:count]

    def request_balance(
        self,
        account: str,
        password: str = "",
        password_media_type: str = "00",
        query_type: str = "2",
    ) -> BalanceSummary:
        account = account.strip()
        if not account:
            raise KiwoomOpenApiError("잔고 조회 계좌번호를 입력해 주세요.")
        password = password.strip()
        if not is_valid_account_password(password):
            raise KiwoomOpenApiError("계좌 비밀번호는 숫자 4~8자리로 입력해 주세요.")
        inputs = {
            "계좌번호": account,
            "비밀번호": password,
            "비밀번호입력매체구분": password_media_type,
            "조회구분": query_type,
        }
        cash = self._request_tr(
            "예수금상세현황조회",
            "opw00001",
            inputs,
            lambda trcode, rqname, record_name: self._parse_account_cash(
                trcode, rqname, record_name, account
            ),
        )
        balance = self._request_tr(
            "계좌잔고조회",
            "opw00018",
            inputs,
            lambda trcode, rqname, record_name: self._parse_balance(
                trcode, rqname, record_name, account
            ),
        )
        return replace(
            balance,
            deposit=cash.deposit,
            orderable_amount=cash.orderable_amount,
            withdrawable_amount=cash.withdrawable_amount,
            d2_estimated_deposit=cash.d2_estimated_deposit,
            message="계좌 예수금 및 잔고 조회 완료",
        )

    def register_real_time_price(self, symbol: str, screen_no: str = "9001") -> str:
        api = self._ensure_api()
        symbol = normalize_symbol(symbol)
        if not symbol:
            raise KiwoomOpenApiError("실시간 등록 종목코드를 입력해 주세요.")
        self._real_quotes.pop(symbol, None)
        self._real_quote_events.clear()
        result = api.SetRealReg(screen_no, symbol, "10;11;12;13;15;20", "0")
        if result not in (0, None):
            raise KiwoomOpenApiError(f"실시간 시세 등록 실패: {result}")
        return f"{symbol} 실시간 시세를 등록했습니다."

    def unregister_real_time(self, screen_no: str = "9001") -> str:
        api = self._ensure_api()
        api.SetRealRemove(screen_no, "ALL")
        return "실시간 시세 등록을 해제했습니다."

    def latest_real_time_quote(self, symbol: str) -> RealTimeQuote | None:
        return self._real_quotes.get(normalize_symbol(symbol))

    def drain_real_time_quotes(self, symbol: str) -> list[RealTimeQuote]:
        target = normalize_symbol(symbol)
        quotes: list[RealTimeQuote] = []
        while self._real_quote_events:
            quote = self._real_quote_events.popleft()
            if quote.symbol == target:
                quotes.append(quote)
        return quotes

    def lookup_symbol_name(self, symbol: str) -> str:
        api = self._ensure_api()
        normalized = normalize_symbol(symbol)
        if not normalized:
            raise KiwoomOpenApiError("종목코드를 입력해 주세요.")
        try:
            return str(api.GetMasterCodeName(normalized) or "").strip()
        except Exception as exc:  # pragma: no cover - depends on COM runtime.
            raise KiwoomOpenApiError(f"종목명 조회 실패: {exc}") from exc

    def send_order(self, request: KiwoomOrderRequest) -> str:
        api = self._ensure_api()
        if not self.is_connected():
            raise KiwoomOpenApiError("키움 OpenAPI 로그인 후 주문할 수 있습니다.")
        if request.require_mock_server and not self.is_mock_server():
            raise KiwoomOpenApiError(
                "현재 접속 서버가 모의투자 서버가 아닙니다. 모의주문은 모의투자 접속에서만 허용됩니다."
            )
        if not request.allow_real_order and not request.require_mock_server:
            raise KiwoomOpenApiError("실거래 주문 잠금이 켜져 있어 주문을 차단했습니다.")
        if request.quantity <= 0:
            raise KiwoomOpenApiError("주문 수량은 1주 이상이어야 합니다.")

        order_type = 1 if request.side == "BUY" else 2
        rqname = "모의매수주문" if request.side == "BUY" else "모의매도주문"
        symbol = normalize_symbol(request.symbol)
        self._request_limiter.acquire()
        result = api.SendOrder(
            rqname,
            self._next_screen_no(),
            request.account,
            order_type,
            symbol,
            int(request.quantity),
            int(request.price),
            request.hoga,
            request.original_order_no,
        )
        if result != 0:
            raise KiwoomOpenApiError(f"SendOrder 호출 실패: {result}")
        return f"{rqname} 요청을 전송했습니다."

    def _nudge_login_window_to_front(self) -> None:
        thread = threading.Thread(
            target=self._focus_login_window_worker,
            name="KiwoomLoginWindowFocus",
            daemon=True,
        )
        thread.start()

    def _focus_login_window_worker(self) -> None:
        try:
            import win32api
            import win32con
            import win32gui
            import win32process
        except Exception:
            self._login_window_status = "로그인 창 전면 표시 기능을 사용할 수 없습니다."
            return

        current_pid = os.getpid()
        found = False

        def _show_matching_window(hwnd: int, _: Any) -> bool:
            nonlocal found
            try:
                _, process_id = win32process.GetWindowThreadProcessId(hwnd)
                title = str(win32gui.GetWindowText(hwnd) or "").strip()
            except Exception:
                return True
            if process_id != current_pid:
                return True
            lowered_title = title.casefold()
            title_matches = any(
                needle in lowered_title for needle in LOGIN_WINDOW_TITLE_NEEDLES
            ) or lowered_title in {"로그인", "키움 로그인"}
            if not title_matches:
                return True

            found = True
            try:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
                work_left, work_top, work_right, work_bottom = win32api.GetMonitorInfo(monitor)[
                    "Work"
                ]
                width = max(320, right - left)
                height = max(240, bottom - top)
                is_offscreen = (
                    right <= work_left
                    or left >= work_right
                    or bottom <= work_top
                    or top >= work_bottom
                )
                if is_offscreen:
                    left = work_left + max(0, (work_right - work_left - width) // 2)
                    top = work_top + max(0, (work_bottom - work_top - height) // 2)
                    win32gui.SetWindowPos(
                        hwnd,
                        win32con.HWND_TOP,
                        left,
                        top,
                        width,
                        height,
                        win32con.SWP_SHOWWINDOW,
                    )

                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW
                win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
                win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
                win32gui.BringWindowToTop(hwnd)
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
            return True

        for _ in range(LOGIN_WINDOW_FOCUS_ATTEMPTS):
            found = False
            try:
                win32gui.EnumWindows(_show_matching_window, None)
            except Exception:
                self._login_window_status = "로그인 창 탐색 중 Windows 오류가 발생했습니다."
                return
            if found:
                self._login_window_status = "키움 OpenAPI+ 로그인 창을 화면 앞으로 표시했습니다."
                return
            time.sleep(0.25)

        self._login_window_status = (
            "CommConnect는 성공했지만 OpenAPI+ 로그인 창을 찾지 못했습니다. "
            "작업 표시줄과 다른 모니터를 확인해 주세요."
        )

    def pump_messages(self) -> None:
        if pythoncom is not None:
            try:
                pythoncom.PumpWaitingMessages()
            except Exception:
                pass

    def _call_api(self, call: Callable[[], Any], timeout: float = DEFAULT_COM_RETRY_SECONDS) -> Any:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while True:
            self.pump_messages()
            try:
                return call()
            except Exception as exc:  # pragma: no cover - depends on COM runtime.
                last_error = exc
                if not self._is_retryable_com_error(exc) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)
        if last_error is not None:
            raise last_error

    def _is_retryable_com_error(self, exc: Exception) -> bool:
        if getattr(exc, "hresult", None) == COM_CALL_REJECTED:
            return True
        if exc.args and exc.args[0] == COM_CALL_REJECTED:
            return True
        return str(COM_CALL_REJECTED) in str(exc)

    def _request_tr(
        self,
        rqname: str,
        trcode: str,
        inputs: dict[str, str],
        parser: Callable[[str, str, str], Any],
        timeout_seconds: float = DEFAULT_TR_TIMEOUT_SECONDS,
    ) -> Any:
        api = self._ensure_api()
        if not self.is_connected():
            raise KiwoomOpenApiError("키움 OpenAPI 로그인 후 조회할 수 있습니다.")

        self._request_limiter.acquire()
        request_id = f"{rqname}-{int(time.time() * 1000)}"
        self._tr_results[request_id] = None
        self._tr_parsers[request_id] = parser
        for key, value in inputs.items():
            api.SetInputValue(key, value)
        result = api.CommRqData(request_id, trcode, 0, self._next_screen_no())
        if result != 0:
            self._tr_results.pop(request_id, None)
            self._tr_parsers.pop(request_id, None)
            raise KiwoomOpenApiError(f"{rqname} 요청 실패: {result}")

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            self.pump_messages()
            data = self._tr_results.get(request_id)
            if data is not None:
                self._tr_results.pop(request_id, None)
                self._tr_parsers.pop(request_id, None)
                if isinstance(data, Exception):
                    raise KiwoomOpenApiError(str(data))
                return data
            time.sleep(0.05)

        self._tr_results.pop(request_id, None)
        self._tr_parsers.pop(request_id, None)
        raise KiwoomOpenApiError(f"{rqname} 응답 대기 시간이 초과되었습니다.")

    def _handle_tr_data(
        self,
        screen_no: str,
        rqname: str,
        trcode: str,
        record_name: str,
        prev_next: str,
    ) -> None:
        parser = self._tr_parsers.get(rqname)
        if parser is None:
            return
        try:
            self._tr_results[rqname] = parser(trcode, rqname, record_name)
        except Exception as exc:
            self._tr_results[rqname] = KiwoomOpenApiError(str(exc))

    def _handle_real_data(self, code: str, real_type: str, real_data: str) -> None:
        if "주식" not in real_type and "체결" not in real_type:
            return
        current = _to_price(self._get_real_data(code, 10))
        change = _to_number(self._get_real_data(code, 11))
        change_rate = _to_number(self._get_real_data(code, 12))
        volume = _to_int(self._get_real_data(code, 15))
        timestamp = str(self._get_real_data(code, 20) or "").strip()
        symbol = normalize_symbol(code)
        quote = RealTimeQuote(
            symbol, current, change, change_rate, volume, timestamp
        )
        self._real_quotes[symbol] = quote
        self._real_quote_events.append(quote)

    def _parse_current_price(self, trcode: str, rqname: str, record_name: str) -> MarketQuote:
        return MarketQuote(
            symbol=str(self._get_comm_data(trcode, record_name, 0, "종목코드")).strip(),
            name=str(self._get_comm_data(trcode, record_name, 0, "종목명")).strip(),
            current_price=_to_price(self._get_comm_data(trcode, record_name, 0, "현재가")),
            change=_to_number(self._get_comm_data(trcode, record_name, 0, "전일대비")),
            change_rate=_to_number(
                self._get_comm_data(trcode, record_name, 0, "등락율")
                or self._get_comm_data(trcode, record_name, 0, "등락률")
            ),
            volume=_to_int(self._get_comm_data(trcode, record_name, 0, "거래량")),
            message="현재가 조회 완료",
        )

    def _parse_minute_candles(self, trcode: str, rqname: str, record_name: str) -> list[Candle]:
        repeat_count = self._get_repeat_count(trcode, rqname)
        candles: list[Candle] = []
        for index in range(repeat_count):
            candles.append(
                Candle(
                    high=_to_price(self._get_comm_data(trcode, record_name, index, "고가")),
                    low=_to_price(self._get_comm_data(trcode, record_name, index, "저가")),
                    close=_to_price(self._get_comm_data(trcode, record_name, index, "현재가")),
                    open=_to_price(self._get_comm_data(trcode, record_name, index, "시가")),
                    volume=_to_int(self._get_comm_data(trcode, record_name, index, "거래량")),
                    timestamp=str(self._get_comm_data(trcode, record_name, index, "체결시간")).strip(),
                )
            )
        return candles

    def _parse_balance(
        self,
        trcode: str,
        rqname: str,
        record_name: str,
        account: str,
    ) -> BalanceSummary:
        repeat_count = self._get_repeat_count(trcode, rqname)
        holdings: list[Holding] = []
        for index in range(repeat_count):
            raw_symbol = str(self._get_comm_data(trcode, record_name, index, "종목번호")).strip()
            symbol = raw_symbol.lstrip("A")
            holdings.append(
                Holding(
                    symbol=symbol,
                    name=str(self._get_comm_data(trcode, record_name, index, "종목명")).strip(),
                    quantity=_to_int(self._get_comm_data(trcode, record_name, index, "보유수량")),
                    average_price=_to_price(self._get_comm_data(trcode, record_name, index, "매입가")),
                    current_price=_to_price(self._get_comm_data(trcode, record_name, index, "현재가")),
                    profit_loss=_to_number(self._get_comm_data(trcode, record_name, index, "평가손익")),
                    profit_rate=_to_number(self._get_comm_data(trcode, record_name, index, "수익률(%)")),
                )
            )
        return BalanceSummary(
            account=account,
            total_purchase=_to_price(self._get_comm_data(trcode, record_name, 0, "총매입금액")),
            total_evaluation=_to_price(self._get_comm_data(trcode, record_name, 0, "총평가금액")),
            total_profit_loss=_to_number(self._get_comm_data(trcode, record_name, 0, "총평가손익금액")),
            total_profit_rate=_to_number(self._get_comm_data(trcode, record_name, 0, "총수익률(%)")),
            estimated_assets=_to_price(self._get_comm_data(trcode, record_name, 0, "추정예탁자산")),
            holdings=tuple(holdings),
            message="계좌 잔고 조회 완료",
        )

    def _parse_account_cash(
        self,
        trcode: str,
        rqname: str,
        record_name: str,
        account: str,
    ) -> AccountCash:
        del rqname

        def amount(*labels: str) -> float:
            for label in labels:
                raw_value = self._get_comm_data(trcode, record_name, 0, label)
                if raw_value:
                    return _to_number(raw_value)
            return 0.0

        return AccountCash(
            account=account,
            deposit=amount("예수금"),
            orderable_amount=amount("주문가능금액"),
            withdrawable_amount=amount("출금가능금액"),
            d2_estimated_deposit=amount("d+2추정예수금", "D+2추정예수금"),
            message="계좌 예수금 조회 완료",
        )

    def _get_comm_data(self, trcode: str, record_name: str, index: int, item: str) -> str:
        api = self._ensure_api()
        try:
            return str(api.GetCommData(trcode, record_name, index, item) or "").strip()
        except Exception:
            return ""

    def _get_repeat_count(self, trcode: str, rqname: str) -> int:
        api = self._ensure_api()
        try:
            return int(api.GetRepeatCnt(trcode, rqname) or 0)
        except Exception:
            return 0

    def _get_real_data(self, code: str, fid: int) -> str:
        api = self._ensure_api()
        try:
            return str(api.GetCommRealData(code, fid) or "").strip()
        except Exception:
            return ""

    def _next_screen_no(self) -> str:
        self._screen_no += 1
        if self._screen_no > 8999:
            self._screen_no = 1000
        return str(self._screen_no)

    def _ensure_api(self) -> Any:
        if self._api is None:
            if self._dispatch_factory is not None:
                self._api = self._dispatch_factory()
            else:
                if pythoncom is None or win32com is None:
                    raise KiwoomOpenApiError(
                        "pywin32가 설치되어 있지 않아 키움 OpenAPI를 호출할 수 없습니다."
                    )
                if struct.calcsize("P") * 8 != 32:
                    raise KiwoomOpenApiError(
                        "현재 프로그램이 64비트입니다. "
                        "키움 OpenAPI+는 32비트 ActiveX라서 32비트 EXE로 실행해야 합니다."
                    )
                pythoncom.CoInitialize()
                try:
                    self._api = win32com.client.DispatchWithEvents(
                        self.control_id,
                        _KiwoomEventSink,
                    )
                    self._api._client = self
                except Exception as exc:  # pragma: no cover - depends on installed OCX.
                    raise KiwoomOpenApiError(
                        "키움 OpenAPI+ ActiveX를 찾을 수 없습니다. "
                        "영웅문/키움 OpenAPI+ 설치와 실행 파일 비트 수를 확인해 주세요."
                    ) from exc
        return self._api
