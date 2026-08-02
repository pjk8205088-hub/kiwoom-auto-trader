from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .charting import NATIVE_MINUTE_INTERVALS
from .kiwoom_api import KiwoomAccountInfo
from .models import (
    AccountCash,
    BalanceSummary,
    Candle,
    Holding,
    KiwoomOrderRequest,
    MarketQuote,
    MarketSessionStatus,
    OrderBookLevel,
    OrderBookSnapshot,
    RealTimeQuote,
    TradeExecution,
    UnfilledOrder,
    VolumeRankQuote,
    WatchlistQuote,
)
from .symbols import clean_account_number, normalize_symbol


KIWOOM_REST_PORTAL = "https://openapi.kiwoom.com/mgmt/VOpenApiRegView?dummyVal=0"
KIWOOM_REST_GUIDE = "https://openapi.kiwoom.com/guide/index?dummyVal=0"
KIWOOM_REST_LIVE_URL = "https://api.kiwoom.com"
KIWOOM_REST_MOCK_URL = "https://mockapi.kiwoom.com"
KIWOOM_REST_LIVE_SOCKET_URL = "wss://api.kiwoom.com:10000/api/dostk/websocket"
KIWOOM_REST_MOCK_SOCKET_URL = "wss://mockapi.kiwoom.com:10000/api/dostk/websocket"
DEFAULT_REST_TIMEOUT_SECONDS = 10.0
MAX_TRADE_HISTORY_PAGES_PER_DAY = 100
REALTIME_TRADE_SESSION_TTL_SECONDS = 30.0
KIWOOM_TRADE_VALUE_UNIT_WON = 1_000_000.0
KIWOOM_MARKET_CAP_UNIT_WON = 100_000_000.0
KIWOOM_WATCHLIST_CODE_FIELD_MAX_LENGTH = 20


class KiwoomRestApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class RestResponse:
    status_code: int
    headers: dict[str, str]
    body: dict[str, Any]


RestRequester = Callable[
    [str, str, dict[str, str], dict[str, Any], float],
    RestResponse,
]


class KiwoomRestRateLimiter:
    """Apply Kiwoom's domestic REST and mock-TR request limits."""

    def __init__(
        self,
        mock: bool,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.mock = mock
        self._clock = clock
        self._sleep = sleeper
        self._requests: deque[float] = deque()
        self._last_by_api_id: dict[str, float] = {}

    def acquire(self, api_id: str) -> None:
        while True:
            now = self._clock()
            while self._requests and now - self._requests[0] >= 1.0:
                self._requests.popleft()

            delay = 0.0
            if len(self._requests) >= 5:
                delay = max(delay, 1.0 - (now - self._requests[0]) + 0.001)
            if self.mock and api_id in self._last_by_api_id:
                elapsed = now - self._last_by_api_id[api_id]
                if elapsed < 1.0:
                    delay = max(delay, 1.0 - elapsed + 0.001)

            if delay <= 0:
                self._requests.append(now)
                self._last_by_api_id[api_id] = now
                return
            self._sleep(delay)


def _number(value: Any) -> float:
    text = str(value or "").strip().replace(",", "").replace("+", "")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _price(value: Any) -> float:
    return abs(_number(value))


def _integer(value: Any) -> int:
    return int(abs(_number(value)))


def _pipe_separated_batches(
    values: list[str],
    max_length: int = KIWOOM_WATCHLIST_CODE_FIELD_MAX_LENGTH,
) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    current_length = 0
    for value in values:
        separator_length = 1 if current else 0
        candidate_length = current_length + separator_length + len(value)
        if current and candidate_length > max_length:
            batches.append(current)
            current = [value]
            current_length = len(value)
            continue
        current.append(value)
        current_length = candidate_length
    if current:
        batches.append(current)
    return batches


def _decode_websocket_message(raw_message: Any) -> Any:
    if isinstance(raw_message, bytes):
        raw_message = raw_message.decode("utf-8")
    if not isinstance(raw_message, str):
        return raw_message
    stripped = raw_message.strip()
    if not stripped:
        return ""
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return stripped


def _encode_websocket_message(message: Any) -> str:
    if isinstance(message, str):
        return message
    return json.dumps(message, ensure_ascii=False)


class KiwoomRestApiClient:
    def __init__(
        self,
        mock: bool = True,
        requester: RestRequester | None = None,
        rate_limiter: KiwoomRestRateLimiter | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.mock = mock
        self.base_url = KIWOOM_REST_MOCK_URL if mock else KIWOOM_REST_LIVE_URL
        self._requester = requester or self._default_requester
        self._rate_limiter = rate_limiter or KiwoomRestRateLimiter(mock)
        self._clock = clock
        self._token = ""
        self._token_expires_at = 0.0
        self._account = ""
        self._account_info = KiwoomAccountInfo(
            False,
            [],
            server_type=self.get_server_name(),
            message="키움 REST API가 연결되지 않았습니다.",
            connection_method="REST API",
        )
        self._real_time_symbol = ""
        self._real_time_quote: RealTimeQuote | None = None
        self._real_quote_events: deque[RealTimeQuote] = deque(maxlen=5000)
        self._market_session_status: MarketSessionStatus | None = None
        self._market_session_confirmed_at = 0.0
        self._market_session_from_trade = False
        self._websocket_thread: threading.Thread | None = None
        self._websocket = None
        self._websocket_stop = threading.Event()
        self._websocket_ready = threading.Event()
        self._websocket_lock = threading.Lock()
        self._websocket_error = ""
        self.last_order_no = ""
        self._symbol_directory: list[tuple[str, str]] = []
        self._symbol_directory_loaded = False
        self._nxt_symbols: set[str] = set()

    def connect(self, app_key: str, secret_key: str) -> KiwoomAccountInfo:
        app_key = app_key.strip()
        secret_key = secret_key.strip()
        if not app_key or not secret_key:
            raise KiwoomRestApiError("REST API AppKey와 SecretKey를 입력해 주세요.")

        self.clear_session()
        token_response = self._post(
            "au10001",
            "/oauth2/token",
            {
                "grant_type": "client_credentials",
                "appkey": app_key,
                "secretkey": secret_key,
            },
            authorized=False,
        )
        token = str(token_response.get("token") or "").strip()
        if not token:
            raise KiwoomRestApiError("키움 REST API가 접근토큰을 반환하지 않았습니다.")

        self._token = token
        self._token_expires_at = self._parse_expiration(token_response.get("expires_dt"))
        try:
            account_response = self._post("ka00001", "/api/dostk/acnt", {})
            account = clean_account_number(account_response.get("acctNo") or account_response.get("acct_no"))
            if not account:
                raise KiwoomRestApiError(
                    "REST API 토큰에서 계좌번호를 확인하지 못했습니다. "
                    "키움 계좌·IP 등록 페이지에서 사용할 계좌가 등록되어 있는지 확인해 주세요."
                )
            self._account = account
            self._account_info = KiwoomAccountInfo(
                True,
                [account],
                user_id="REST API 토큰 인증",
                server_type=self.get_server_name(),
                message=f"키움 REST API {self.get_server_name()} 연결 및 계좌 확인 완료",
                reported_account_count=1,
                login_event_code=0,
                connection_method="REST API",
            )
            return self._account_info
        except Exception:
            self.clear_session()
            raise

    def clear_session(self) -> None:
        self._stop_websocket()
        self._token = ""
        self._token_expires_at = 0.0
        self._account = ""
        self._real_time_symbol = ""
        with self._websocket_lock:
            self._real_time_quote = None
            self._real_quote_events.clear()
            self._market_session_status = None
            self._market_session_confirmed_at = 0.0
            self._market_session_from_trade = False
        self._websocket_error = ""
        self.last_order_no = ""
        self._websocket_ready.clear()
        self._account_info = KiwoomAccountInfo(
            False,
            [],
            server_type=self.get_server_name(),
            message="키움 REST API가 연결되지 않았습니다.",
            connection_method="REST API",
        )

    def is_connected(self) -> bool:
        return bool(self._account and self._has_valid_token())

    def get_account_info(self) -> KiwoomAccountInfo:
        if not self.is_connected():
            return KiwoomAccountInfo(
                False,
                [],
                server_type=self.get_server_name(),
                message="REST API 토큰이 없거나 만료되었습니다.",
                connection_method="REST API",
            )
        return self._account_info

    def login_status_message(self) -> str:
        return self._account_info.message

    def get_server_name(self) -> str:
        return "모의투자" if self.mock else "실거래"

    def lookup_symbol_name(self, symbol: str) -> str:
        return self.request_current_price(symbol).name

    def search_symbols(self, query: str, limit: int = 20) -> list[tuple[str, str]]:
        needle = str(query or "").strip().casefold()
        if not needle:
            return []
        self._ensure_symbol_directory()
        matches = [
            item
            for item in self._symbol_directory
            if needle in item[0].casefold() or needle in item[1].casefold()
        ]
        matches.sort(
            key=lambda item: (
                not item[1].casefold().startswith(needle),
                not item[0].startswith(needle),
                item[1],
            )
        )
        return matches[: max(1, int(limit))]

    def _ensure_symbol_directory(self) -> None:
        if self._symbol_directory_loaded:
            return
        directory: dict[str, str] = {}
        nxt_symbols: set[str] = set()
        for market in ("0", "10"):
            continuation_headers: dict[str, str] | None = None
            for _page in range(10):
                response = self._post_response(
                    "ka10099",
                    "/api/dostk/stkinfo",
                    {"mrkt_tp": market},
                    extra_headers=continuation_headers,
                )
                rows = response.body.get("list") or []
                if isinstance(rows, list):
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        code = normalize_symbol(row.get("code"))
                        name = str(row.get("name") or "").strip()
                        if code and name:
                            directory[code] = name
                        nxt_enabled = str(
                            row.get("nxtEnable")
                            or row.get("nxt_enable")
                            or row.get("nxt_yn")
                            or ""
                        ).strip().upper()
                        if code and nxt_enabled == "Y":
                            nxt_symbols.add(code)
                cont_yn = self._response_header(response.headers, "cont-yn").upper()
                next_key = self._response_header(response.headers, "next-key")
                if cont_yn != "Y" or not next_key:
                    break
                continuation_headers = {"cont-yn": "Y", "next-key": next_key}
        self._symbol_directory = sorted(directory.items(), key=lambda item: item[1])
        self._nxt_symbols = nxt_symbols
        self._symbol_directory_loaded = True

    def request_current_price(self, symbol: str) -> MarketQuote:
        symbol = normalize_symbol(symbol)
        if not symbol:
            raise KiwoomRestApiError("종목코드를 입력해 주세요.")
        body = self._post("ka10001", "/api/dostk/stkinfo", {"stk_cd": symbol})
        return MarketQuote(
            symbol=normalize_symbol(body.get("stk_cd")) or symbol,
            name=str(body.get("stk_nm") or "").strip(),
            current_price=_price(body.get("cur_prc")),
            change=_number(body.get("pred_pre")),
            change_rate=_number(body.get("flu_rt")),
            volume=_integer(body.get("trde_qty")),
            timestamp=datetime.now().strftime("%Y%m%d%H%M%S"),
            message="REST API 현재가 조회 완료",
        )

    def request_order_book(self, symbol: str) -> OrderBookSnapshot:
        symbol = normalize_symbol(symbol)
        if not symbol:
            raise KiwoomRestApiError("호가를 조회할 종목코드를 입력해 주세요.")
        body = self._post("ka10004", "/api/dostk/mrkcond", {"stk_cd": symbol})
        levels: list[OrderBookLevel] = []
        for level in range(1, 11):
            ask_price_key = "sel_fpr_bid" if level == 1 else f"sel_{level}th_pre_bid"
            ask_quantity_key = "sel_fpr_req" if level == 1 else f"sel_{level}th_pre_req"
            bid_price_key = "buy_fpr_bid" if level == 1 else f"buy_{level}th_pre_bid"
            bid_quantity_key = "buy_fpr_req" if level == 1 else f"buy_{level}th_pre_req"
            levels.append(
                OrderBookLevel(
                    level=level,
                    ask_price=_price(body.get(ask_price_key)),
                    ask_quantity=_integer(body.get(ask_quantity_key)),
                    bid_price=_price(body.get(bid_price_key)),
                    bid_quantity=_integer(body.get(bid_quantity_key)),
                )
            )
        return OrderBookSnapshot(
            symbol=symbol,
            levels=tuple(levels),
            timestamp=str(body.get("bid_req_base_tm") or datetime.now().strftime("%H%M%S")),
            source="키움 REST ka10004",
        )

    def request_watchlist_quotes(self, symbols: list[str]) -> list[WatchlistQuote]:
        normalized = list(
            dict.fromkeys(
                symbol
                for symbol in (normalize_symbol(value) for value in symbols)
                if symbol
            )
        )
        if not normalized:
            raise KiwoomRestApiError("관심종목 코드를 한 개 이상 등록해 주세요.")
        quotes_by_symbol: dict[str, WatchlistQuote] = {}
        for batch in _pipe_separated_batches(normalized[:100]):
            body = self._post(
                "ka10095",
                "/api/dostk/stkinfo",
                {"stk_cd": "|".join(batch)},
            )
            rows = body.get("atn_stk_infr") or []
            if not isinstance(rows, list):
                raise KiwoomRestApiError("REST API 관심종목 응답 형식이 올바르지 않습니다.")

            for row in rows:
                if not isinstance(row, dict):
                    continue
                raw_symbol = str(row.get("stk_cd") or "").strip()
                symbol = normalize_symbol(raw_symbol)
                if not symbol:
                    continue
                market = (
                    "NXT"
                    if "_NX" in raw_symbol
                    else "SOR"
                    if "_AL" in raw_symbol
                    else "KRX"
                )
                quotes_by_symbol[symbol] = WatchlistQuote(
                    symbol=symbol,
                    name=str(row.get("stk_nm") or "").strip(),
                    market=market,
                    current_price=_price(row.get("cur_prc")),
                    change=_number(row.get("pred_pre")),
                    change_rate=_number(row.get("flu_rt")),
                    volume=_integer(row.get("trde_qty")),
                    trade_value=(
                        _number(row.get("trde_prica")) * KIWOOM_TRADE_VALUE_UNIT_WON
                    ),
                    market_cap=(
                        _number(row.get("mac")) * KIWOOM_MARKET_CAP_UNIT_WON
                    ),
                    open_price=_price(row.get("open_pric")),
                    high_price=_price(row.get("high_pric")),
                    low_price=_price(row.get("low_pric")),
                    ask_price=_price(row.get("sel_bid")),
                    bid_price=_price(row.get("buy_bid")),
                    timestamp=str(
                        row.get("cntr_tm") or row.get("bid_tm") or ""
                    ).strip(),
                )
        return [
            quotes_by_symbol[symbol]
            for symbol in normalized
            if symbol in quotes_by_symbol
        ]

    def request_watchlist_previous_trade_value(self, symbol: str) -> float:
        """Return yesterday's trading value in won using official ka10007."""
        normalized = normalize_symbol(symbol)
        if not normalized:
            raise KiwoomRestApiError("종목코드를 입력해 주세요.")
        body = self._post(
            "ka10007",
            "/api/dostk/mrkcond",
            {"stk_cd": normalized},
        )
        return _number(body.get("pred_trde_prica")) * KIWOOM_TRADE_VALUE_UNIT_WON

    def request_watchlist_program_trading_trend(self, symbol: str) -> float:
        """Return the latest program net-buy amount in won using official ka90008."""
        normalized = normalize_symbol(symbol)
        if not normalized:
            raise KiwoomRestApiError("종목코드를 입력해 주세요.")
        body = self._post(
            "ka90008",
            "/api/dostk/mrkcond",
            {
                "amt_qty_tp": "1",
                "stk_cd": normalized,
                "date": datetime.now().strftime("%Y%m%d"),
            },
        )
        rows = body.get("stk_tm_prm_trde_trnsn") or []
        if not isinstance(rows, list):
            raise KiwoomRestApiError("프로그램 매매 추이 응답 형식이 올바르지 않습니다.")
        latest = next((row for row in rows if isinstance(row, dict)), None)
        if latest is None:
            return 0.0
        return _number(latest.get("prm_netprps_amt")) * KIWOOM_TRADE_VALUE_UNIT_WON

    def request_volume_ranking(
        self,
        market: str = "000",
        limit: int = 15,
    ) -> list[VolumeRankQuote]:
        market = str(market or "000").strip()
        if market not in {"000", "001", "101"}:
            raise KiwoomRestApiError("거래량 순위 시장은 전체, 코스피, 코스닥만 선택할 수 있습니다.")
        limit = max(1, min(100, int(limit)))
        body = self._post(
            "ka10030",
            "/api/dostk/rkinfo",
            {
                "mrkt_tp": market,
                "sort_tp": "1",
                "mang_stk_incls": "0",
                "crd_tp": "0",
                "trde_qty_tp": "0",
                "pric_tp": "0",
                "trde_prica_tp": "0",
                "mrkt_open_tp": "0",
                "stex_tp": "1" if self.mock else "3",
            },
        )
        rows = body.get("tdy_trde_qty_upper") or []
        if not isinstance(rows, list):
            raise KiwoomRestApiError("REST API 당일 거래량 순위 응답 형식이 올바르지 않습니다.")

        try:
            self._ensure_symbol_directory()
        except KiwoomRestApiError:
            pass

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        quotes: list[VolumeRankQuote] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = normalize_symbol(row.get("stk_cd"))
            if not symbol:
                continue
            quotes.append(
                VolumeRankQuote(
                    rank=len(quotes) + 1,
                    symbol=symbol,
                    name=str(row.get("stk_nm") or "").strip(),
                    current_price=_price(row.get("cur_prc")),
                    change=_number(row.get("pred_pre")),
                    change_rate=_number(row.get("flu_rt")),
                    volume=_integer(row.get("trde_qty")),
                    turnover_rate=_number(row.get("trde_tern_rt")),
                    trade_value=(
                        _number(row.get("trde_amt")) * KIWOOM_TRADE_VALUE_UNIT_WON
                    ),
                    change_sign=str(row.get("pred_pre_sig") or "").strip(),
                    previous_ratio=_number(row.get("pred_rt")),
                    nxt_available=(
                        symbol in self._nxt_symbols
                        or "_NX" in str(row.get("stk_cd") or "").upper()
                    ),
                    timestamp=timestamp,
                )
            )
            if len(quotes) >= limit:
                break

        if quotes:
            try:
                details = self.request_watchlist_quotes(
                    [quote.symbol for quote in quotes]
                )
            except KiwoomRestApiError:
                details = []
            details_by_symbol = {quote.symbol: quote for quote in details}
            quotes = [
                replace(
                    quote,
                    market_cap=details_by_symbol.get(
                        quote.symbol,
                        WatchlistQuote(symbol=quote.symbol),
                    ).market_cap,
                )
                for quote in quotes
            ]
        return quotes

    def request_trade_value_ranking(
        self,
        market: str = "000",
        limit: int = 15,
    ) -> list[VolumeRankQuote]:
        """Return the official ka10032 transaction-value ranking."""
        market = str(market or "000").strip()
        if market not in {"000", "001", "101"}:
            raise KiwoomRestApiError(
                "거래대금 순위 시장은 전체, 코스피, 코스닥만 선택할 수 있습니다."
            )
        limit = max(1, min(100, int(limit)))
        body = self._post(
            "ka10032",
            "/api/dostk/rkinfo",
            {
                "mrkt_tp": market,
                "mang_stk_incls": "1",
                "stex_tp": "1" if self.mock else "3",
            },
        )
        rows = body.get("trde_prica_upper") or []
        if not isinstance(rows, list):
            raise KiwoomRestApiError(
                "REST API 거래대금 순위 응답 형식이 올바르지 않습니다."
            )

        try:
            self._ensure_symbol_directory()
        except KiwoomRestApiError:
            pass

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        quotes: list[VolumeRankQuote] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_symbol = str(row.get("stk_cd") or "")
            symbol = normalize_symbol(raw_symbol)
            if not symbol:
                continue
            rank = _integer(row.get("now_rank")) or len(quotes) + 1
            quotes.append(
                VolumeRankQuote(
                    rank=rank,
                    symbol=symbol,
                    name=str(row.get("stk_nm") or "").strip(),
                    current_price=_price(row.get("cur_prc")),
                    change=_number(row.get("pred_pre")),
                    change_rate=_number(row.get("flu_rt")),
                    volume=_integer(row.get("now_trde_qty")),
                    trade_value=(
                        _number(row.get("trde_prica"))
                        * KIWOOM_TRADE_VALUE_UNIT_WON
                    ),
                    change_sign=str(row.get("pred_pre_sig") or "").strip(),
                    nxt_available=(
                        symbol in self._nxt_symbols or "_NX" in raw_symbol.upper()
                    ),
                    timestamp=timestamp,
                )
            )
            if len(quotes) >= limit:
                break
        return quotes

    def request_minute_candles(
        self,
        symbol: str,
        interval: int = 3,
        count: int = 120,
    ) -> list[Candle]:
        symbol = normalize_symbol(symbol)
        if not symbol:
            raise KiwoomRestApiError("종목코드를 입력해 주세요.")
        interval = int(interval)
        if interval not in NATIVE_MINUTE_INTERVALS:
            supported = ", ".join(str(value) for value in NATIVE_MINUTE_INTERVALS)
            raise KiwoomRestApiError(f"분봉 간격은 {supported}분만 지원합니다.")
        body = self._post(
            "ka10080",
            "/api/dostk/chart",
            {
                "stk_cd": symbol,
                "tic_scope": str(interval),
                "upd_stkpc_tp": "1",
            },
        )
        rows = body.get("stk_min_pole_chart_qry") or []
        if not isinstance(rows, list):
            raise KiwoomRestApiError(f"REST API {interval}분봉 응답 형식이 올바르지 않습니다.")
        candles = [
            Candle(
                high=_price(row.get("high_pric")),
                low=_price(row.get("low_pric")),
                close=_price(row.get("cur_prc")),
                open=_price(row.get("open_pric")),
                volume=_integer(row.get("trde_qty")),
                timestamp=str(row.get("cntr_tm") or "").strip(),
            )
            for row in rows
            if isinstance(row, dict)
        ]
        return candles[:count]

    def request_daily_candles(self, symbol: str, count: int = 200) -> list[Candle]:
        symbol = normalize_symbol(symbol)
        if not symbol:
            raise KiwoomRestApiError("종목코드를 입력해 주세요.")
        count = max(1, int(count))
        body = self._post(
            "ka10081",
            "/api/dostk/chart",
            {
                "stk_cd": symbol,
                "base_dt": datetime.now().strftime("%Y%m%d"),
                "upd_stkpc_tp": "1",
            },
        )
        rows = body.get("stk_dt_pole_chart_qry") or []
        if not isinstance(rows, list):
            raise KiwoomRestApiError("REST API 일봉 응답 형식이 올바르지 않습니다.")
        candles = [
            Candle(
                high=_price(row.get("high_pric")),
                low=_price(row.get("low_pric")),
                close=_price(row.get("cur_prc")),
                open=_price(row.get("open_pric")),
                volume=_integer(row.get("trde_qty")),
                timestamp=str(row.get("dt") or "").strip(),
            )
            for row in rows
            if isinstance(row, dict) and _price(row.get("cur_prc")) > 0
        ]
        return candles[:count]

    def request_tick_candles(self, symbol: str, count: int = 3000) -> list[Candle]:
        symbol = normalize_symbol(symbol)
        if not symbol:
            raise KiwoomRestApiError("종목코드를 입력해 주세요.")
        count = max(1, int(count))
        body = self._post(
            "ka10079",
            "/api/dostk/chart",
            {
                "stk_cd": symbol,
                "tic_scope": "1",
                "upd_stkpc_tp": "1",
            },
        )
        rows = body.get("stk_tic_chart_qry") or []
        if not isinstance(rows, list):
            raise KiwoomRestApiError("REST API 1틱 차트 응답 형식이 올바르지 않습니다.")
        candles = [
            Candle(
                high=_price(row.get("high_pric")),
                low=_price(row.get("low_pric")),
                close=_price(row.get("cur_prc")),
                open=_price(row.get("open_pric")),
                volume=_integer(row.get("trde_qty")),
                timestamp=str(row.get("cntr_tm") or "").strip(),
            )
            for row in rows
            if isinstance(row, dict) and _price(row.get("cur_prc")) > 0
        ]
        return candles[:count]

    def request_balance(self, account: str, password: str = "") -> BalanceSummary:
        del password
        account = clean_account_number(account) or self._account
        if not account or account != self._account:
            raise KiwoomRestApiError("REST API 토큰의 계좌번호와 선택 계좌가 일치하지 않습니다.")
        cash = self.request_account_cash(account)
        body = self._post(
            "kt00018",
            "/api/dostk/acnt",
            {"qry_tp": "1", "dmst_stex_tp": "KRX"},
        )
        raw_holdings = body.get("acnt_evlt_remn_indv_tot") or []
        holdings: list[Holding] = []
        if isinstance(raw_holdings, list):
            for row in raw_holdings:
                if not isinstance(row, dict):
                    continue
                holdings.append(
                    Holding(
                        symbol=normalize_symbol(str(row.get("stk_cd") or "").lstrip("AJQ")),
                        name=str(row.get("stk_nm") or "").strip(),
                        quantity=_integer(row.get("rmnd_qty")),
                        average_price=_price(row.get("pur_pric")),
                        current_price=_price(row.get("cur_prc")),
                        profit_loss=_number(row.get("evltv_prft")),
                        profit_rate=_number(row.get("prft_rt")),
                    )
                )
        return BalanceSummary(
            account=account,
            deposit=cash.deposit,
            orderable_amount=cash.orderable_amount,
            withdrawable_amount=cash.withdrawable_amount,
            d2_estimated_deposit=cash.d2_estimated_deposit,
            total_purchase=_number(body.get("tot_pur_amt")),
            total_evaluation=_number(body.get("tot_evlt_amt")),
            total_profit_loss=_number(body.get("tot_evlt_pl")),
            total_profit_rate=_number(body.get("tot_prft_rt")),
            estimated_assets=_number(body.get("prsm_dpst_aset_amt")),
            holdings=tuple(holdings),
            message="REST API 예수금 및 계좌평가잔고 조회 완료",
        )

    def request_today_trade_history(
        self,
        account: str,
        password: str = "",
        order_date: str | None = None,
    ) -> list[TradeExecution]:
        del password
        account = clean_account_number(account) or self._account
        if not account or account != self._account:
            raise KiwoomRestApiError("REST API 토큰의 계좌번호와 선택 계좌가 일치하지 않습니다.")

        query_date = str(order_date or datetime.now().strftime("%Y%m%d")).strip()
        if len(query_date) != 8 or not query_date.isdigit():
            raise KiwoomRestApiError("주문 조회일자는 YYYYMMDD 8자리로 입력해 주세요.")

        request_body = {
            "qry_tp": "4",
            "stk_bond_tp": "1",
            "sell_tp": "0",
            "dmst_stex_tp": "%",
            "ord_dt": query_date,
            "stk_cd": "",
            "fr_ord_no": "",
        }
        rows: list[dict[str, Any]] = []
        continuation_headers: dict[str, str] | None = None
        continuation_keys: set[str] = set()
        for _page in range(MAX_TRADE_HISTORY_PAGES_PER_DAY):
            response = self._post_response(
                "kt00007",
                "/api/dostk/acnt",
                request_body,
                extra_headers=continuation_headers,
            )
            records = response.body.get("acnt_ord_cntr_prps_dtl") or []
            if isinstance(records, list):
                rows.extend(record for record in records if isinstance(record, dict))

            cont_yn = self._response_header(response.headers, "cont-yn").upper()
            next_key = self._response_header(response.headers, "next-key")
            if cont_yn != "Y" or not next_key:
                break
            if next_key in continuation_keys:
                raise KiwoomRestApiError(
                    f"{query_date} 체결내역 연속조회 키가 반복되어 조회를 중단했습니다."
                )
            continuation_keys.add(next_key)
            continuation_headers = {"cont-yn": "Y", "next-key": next_key}
        else:
            raise KiwoomRestApiError(
                f"{query_date} 체결내역이 {MAX_TRADE_HISTORY_PAGES_PER_DAY}페이지를 초과했습니다."
            )

        executions: list[TradeExecution] = []
        seen: set[tuple[str, str, str, int, float, str]] = set()
        for row in rows:
            side = self._trade_side(row.get("io_tp_nm"), row.get("trde_tp"))
            quantity = _integer(row.get("cntr_qty"))
            if side is None or quantity <= 0:
                continue
            price = _price(row.get("cntr_uv") or row.get("ord_uv"))
            order_no = str(row.get("ord_no") or "").strip()
            symbol = normalize_symbol(str(row.get("stk_cd") or "").lstrip("AJQ"))
            timestamp = self._trade_timestamp(
                query_date,
                row.get("cnfm_tm") or row.get("ord_tm"),
            )
            key = (order_no, side, symbol, quantity, price, timestamp)
            if key in seen:
                continue
            seen.add(key)
            executions.append(
                TradeExecution(
                    timestamp=timestamp,
                    side=side,
                    symbol=symbol,
                    symbol_name=str(row.get("stk_nm") or "").strip(),
                    quantity=quantity,
                    price=price,
                    order_no=order_no,
                    order_mode=f"키움 {self.get_server_name()}",
                    message=(
                        "키움 kt00007 계좌 실제 체결 · "
                        f"{query_date[0:4]}-{query_date[4:6]}-{query_date[6:8]}"
                    ),
                )
            )
        return sorted(executions, key=lambda item: item.timestamp, reverse=True)

    def request_account_cash(self, account: str) -> AccountCash:
        account = clean_account_number(account) or self._account
        if not account or account != self._account:
            raise KiwoomRestApiError("REST API 토큰의 계좌번호와 선택 계좌가 일치하지 않습니다.")
        body = self._post(
            "kt00001",
            "/api/dostk/acnt",
            {"qry_tp": "3"},
        )
        return AccountCash(
            account=account,
            deposit=_number(body.get("entr")),
            orderable_amount=_number(body.get("ord_alow_amt")),
            withdrawable_amount=_number(body.get("pymn_alow_amt")),
            d2_estimated_deposit=_number(body.get("d2_entra")),
            message="REST API 예수금 조회 완료",
        )

    def request_unfilled_orders(
        self,
        account: str,
        symbol: str = "",
    ) -> list[UnfilledOrder]:
        account = clean_account_number(account) or self._account
        if not account or account != self._account:
            raise KiwoomRestApiError("REST API 토큰의 계좌번호와 선택 계좌가 일치하지 않습니다.")
        normalized = normalize_symbol(symbol)
        body = self._post(
            "ka10075",
            "/api/dostk/acnt",
            {
                "all_stk_tp": "1" if normalized else "0",
                "trde_tp": "0",
                "stex_tp": "0",
                **({"stk_cd": normalized} if normalized else {}),
            },
        )
        rows = body.get("oso") or []
        orders: list[UnfilledOrder] = []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                side = self._trade_side(row.get("io_tp_nm"), row.get("trde_tp"))
                if side is None:
                    continue
                orders.append(
                    UnfilledOrder(
                        order_no=str(row.get("ord_no") or "").strip(),
                        symbol=normalize_symbol(str(row.get("stk_cd") or "").lstrip("AJQ")),
                        symbol_name=str(row.get("stk_nm") or "").strip(),
                        side=side,
                        order_quantity=_integer(row.get("ord_qty")),
                        unfilled_quantity=_integer(row.get("oso_qty")),
                        order_price=_price(row.get("ord_pric")),
                        current_price=_price(row.get("cur_prc")),
                        timestamp=str(row.get("tm") or "").strip(),
                        status=str(row.get("ord_stt") or "미체결").strip(),
                    )
                )
        return orders

    def register_real_time_price(self, symbol: str, screen_no: str = "") -> str:
        del screen_no
        if not self.is_connected():
            raise KiwoomRestApiError("REST API 토큰 연결 후 실시간 시세를 등록할 수 있습니다.")
        symbol = normalize_symbol(symbol)
        if not symbol:
            raise KiwoomRestApiError("실시간 조회 종목코드를 입력해 주세요.")
        self._stop_websocket()
        self._real_time_symbol = symbol
        with self._websocket_lock:
            self._real_time_quote = None
            self._real_quote_events.clear()
            self._market_session_status = None
            self._market_session_confirmed_at = 0.0
            self._market_session_from_trade = False
        self._websocket_error = ""
        self._websocket_ready.clear()
        stop_event = threading.Event()
        self._websocket_stop = stop_event
        token = self._token
        self._websocket_thread = threading.Thread(
            target=self._run_websocket,
            args=(stop_event, token, symbol),
            name="KiwoomRestRealtime",
            daemon=True,
        )
        self._websocket_thread.start()
        return f"{symbol} REST WebSocket 주식체결(0B) 등록을 시작했습니다."

    def unregister_real_time(self, screen_no: str = "") -> str:
        del screen_no
        symbol = self._real_time_symbol
        self._stop_websocket()
        self._real_time_symbol = ""
        with self._websocket_lock:
            self._real_time_quote = None
            self._real_quote_events.clear()
            self._market_session_status = None
            self._market_session_confirmed_at = 0.0
            self._market_session_from_trade = False
        self._websocket_error = ""
        self._websocket_ready.clear()
        return f"{symbol or 'REST'} WebSocket 실시간 시세를 중지했습니다."

    def latest_real_time_quote(self, symbol: str) -> RealTimeQuote | None:
        symbol = normalize_symbol(symbol)
        if not self._real_time_symbol or symbol != self._real_time_symbol:
            return None
        if self._websocket_error:
            raise KiwoomRestApiError(self._websocket_error)
        with self._websocket_lock:
            return self._real_time_quote

    def drain_real_time_quotes(self, symbol: str) -> list[RealTimeQuote]:
        target = normalize_symbol(symbol)
        with self._websocket_lock:
            quotes = [quote for quote in self._real_quote_events if quote.symbol == target]
            self._real_quote_events.clear()
        return quotes

    def latest_market_session_status(self) -> MarketSessionStatus | None:
        with self._websocket_lock:
            return self._market_session_status

    def is_real_time_registered(self) -> bool:
        return bool(
            self._real_time_symbol
            and self._websocket_ready.is_set()
            and not self._websocket_error
        )

    def is_regular_market_open(self) -> bool:
        with self._websocket_lock:
            status = self._market_session_status
            confirmed_at = self._market_session_confirmed_at
            from_trade = self._market_session_from_trade
        if not (
            status
            and status.is_open
            and self.is_real_time_registered()
        ):
            return False
        if from_trade:
            age = max(0.0, self._clock() - confirmed_at)
            return age <= REALTIME_TRADE_SESSION_TTL_SECONDS
        return True

    def send_order(self, request: KiwoomOrderRequest) -> str:
        self.last_order_no = ""
        if not self.is_connected():
            raise KiwoomRestApiError("REST API 토큰 연결 후 주문할 수 있습니다.")
        if self.mock:
            if request.allow_real_order or not request.require_mock_server:
                raise KiwoomRestApiError("모의투자 서버에서는 실거래 주문 요청을 사용할 수 없습니다.")
            order_mode = "모의"
        else:
            if not request.allow_real_order or request.require_mock_server:
                raise KiwoomRestApiError(
                    "REST API 실거래 주문은 앱의 실거래 세션 승인이 있어야 합니다."
                )
            if not self.is_regular_market_open():
                raise KiwoomRestApiError(
                    "키움 실시간 장 상태(0s 또는 0B 장구분)에서 정규장 장중 신호를 확인하지 못해 "
                    "실거래 주문을 차단했습니다."
                )
            order_mode = "실거래"
        if request.action != "CANCEL" and request.quantity <= 0:
            raise KiwoomRestApiError("주문 수량은 1주 이상이어야 합니다.")
        if request.side not in {"BUY", "SELL"}:
            raise KiwoomRestApiError("주문 구분은 매수 또는 매도여야 합니다.")
        if clean_account_number(request.account) != self._account:
            raise KiwoomRestApiError("REST API 토큰의 계좌번호와 주문 계좌가 일치하지 않습니다.")

        if request.action in {"MODIFY", "CANCEL"} and not request.original_order_no.strip():
            raise KiwoomRestApiError("정정 또는 취소할 원주문번호를 입력해 주세요.")

        if request.action == "MODIFY":
            if request.price <= 0:
                raise KiwoomRestApiError("정정 주문의 중간가를 다시 조회해 주세요.")
            api_id = "kt10002"
            request_body = {
                "dmst_stex_tp": "KRX",
                "orig_ord_no": request.original_order_no.strip(),
                "stk_cd": normalize_symbol(request.symbol),
                "mdfy_qty": str(request.quantity),
                "mdfy_uv": str(int(request.price)),
                "mdfy_cond_uv": "",
            }
        elif request.action == "CANCEL":
            api_id = "kt10003"
            request_body = {
                "dmst_stex_tp": "KRX",
                "orig_ord_no": request.original_order_no.strip(),
                "stk_cd": normalize_symbol(request.symbol),
                "cncl_qty": str(max(0, int(request.quantity))),
            }
        else:
            api_id = "kt10000" if request.side == "BUY" else "kt10001"
            trade_type = str(request.hoga or "3").lstrip("0") or "0"
            request_body = {
                "dmst_stex_tp": "KRX",
                "stk_cd": normalize_symbol(request.symbol),
                "ord_qty": str(request.quantity),
                "ord_uv": str(int(request.price)) if request.price > 0 else "",
                "trde_tp": trade_type,
                "cond_uv": "",
            }
        body = self._post(api_id, "/api/dostk/ordr", request_body)
        order_no = str(body.get("ord_no") or "").strip()
        if not order_no:
            raise KiwoomRestApiError("REST API 주문번호가 수신되지 않았습니다.")
        self.last_order_no = order_no
        side_name = "매수" if request.side == "BUY" else "매도"
        action_name = {"NEW": "", "MODIFY": "정정 ", "CANCEL": "취소 "}[request.action]
        return f"REST {order_mode} {side_name}{action_name}주문 접수 완료 (주문번호 {order_no})"

    def pump_messages(self) -> None:
        return

    def _run_websocket(self, stop_event: threading.Event, token: str, symbol: str) -> None:
        websocket = None
        try:
            from websockets.sync.client import connect

            socket_url = (
                KIWOOM_REST_MOCK_SOCKET_URL if self.mock else KIWOOM_REST_LIVE_SOCKET_URL
            )
            with connect(
                socket_url,
                open_timeout=DEFAULT_REST_TIMEOUT_SECONDS,
                close_timeout=2,
                ping_interval=None,
            ) as websocket:
                if stop_event.is_set():
                    return
                self._websocket = websocket
                websocket.send(_encode_websocket_message({"trnm": "LOGIN", "token": token}))
                while not stop_event.is_set():
                    try:
                        raw_message = websocket.recv(timeout=0.5)
                    except TimeoutError:
                        continue
                    if raw_message is None:
                        break
                    response = _decode_websocket_message(raw_message)
                    reply = self._handle_websocket_message(response)
                    if reply is not None:
                        websocket.send(_encode_websocket_message(reply))
                if symbol:
                    try:
                        websocket.send(_encode_websocket_message(self._remove_real_time_message(symbol)))
                    except Exception:
                        pass
        except Exception as exc:
            if not stop_event.is_set():
                self._websocket_error = f"REST WebSocket 실시간 시세 연결 실패: {exc}"
                self._websocket_ready.clear()
        finally:
            if self._websocket is websocket:
                self._websocket = None
            self._websocket_ready.clear()

    def _handle_websocket_message(self, response: Any) -> dict[str, Any] | str | None:
        if isinstance(response, str):
            return response if response.strip().upper() == "PING" else None
        if not isinstance(response, dict):
            return None
        tr_name = str(response.get("trnm") or "").upper()
        if tr_name == "LOGIN":
            return_code = int(response.get("return_code") or 0)
            if return_code != 0:
                message = str(response.get("return_msg") or "WebSocket 토큰 로그인이 실패했습니다.")
                raise KiwoomRestApiError(f"REST WebSocket 로그인 오류 {return_code}: {message}")
            self._websocket_error = ""
            return self._register_real_time_message()
        if tr_name == "PING":
            return response
        if tr_name == "REG":
            if int(response.get("return_code") or 0) != 0:
                message = str(response.get("return_msg") or "실시간 시세 등록이 실패했습니다.")
                raise KiwoomRestApiError(f"REST WebSocket 등록 오류: {message}")
            self._websocket_ready.set()
            self._websocket_error = ""
            return None
        if tr_name == "SYSTEM" and int(response.get("return_code") or 0) != 0:
            message = str(response.get("return_msg") or "WebSocket 시스템 오류가 발생했습니다.")
            raise KiwoomRestApiError(f"REST WebSocket 시스템 오류: {message}")
        if tr_name != "REAL":
            return None

        rows = response.get("data") or []
        if not isinstance(rows, list):
            return None
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_type = str(row.get("type") or "").upper()
            values = row.get("values") or {}
            if not isinstance(values, dict):
                continue
            if row_type == "0S":
                status = MarketSessionStatus(
                    operation_code=str(values.get("215") or "").strip(),
                    event_time=str(values.get("20") or "").strip(),
                    expected_remaining_seconds=_integer(values.get("214")),
                    received_at=datetime.now().strftime("%Y%m%d%H%M%S"),
                    source="키움 REST 장시작시간(0s)",
                )
                with self._websocket_lock:
                    self._market_session_status = status
                    self._market_session_confirmed_at = self._clock()
                    self._market_session_from_trade = False
                continue
            if row_type != "0B":
                continue
            symbol = normalize_symbol(row.get("item"))
            if symbol != self._real_time_symbol or not isinstance(values, dict):
                continue
            trade_time = str(values.get("20") or "").strip()
            time_digits = "".join(character for character in trade_time if character.isdigit())
            if len(time_digits) >= 14:
                timestamp = time_digits[:14]
            elif len(time_digits) >= 6:
                timestamp = f"{datetime.now():%Y%m%d}{time_digits[-6:]}"
            else:
                timestamp = f"{datetime.now():%Y%m%d%H%M%S}"
            quote = RealTimeQuote(
                symbol=symbol,
                current_price=_price(values.get("10")),
                change_rate=_number(values.get("12")),
                volume=_integer(values.get("15")),
                timestamp=timestamp,
                market_session_code=str(values.get("290") or "").strip(),
            )
            trade_session_code = quote.market_session_code
            operation_code = {"1": "0", "2": "3", "3": "8"}.get(trade_session_code)
            trade_status = None
            if operation_code is not None:
                trade_status = MarketSessionStatus(
                    operation_code=operation_code,
                    event_time=trade_time,
                    received_at=datetime.now().strftime("%Y%m%d%H%M%S"),
                    source=f"키움 REST 주식체결(0B) 장구분 {trade_session_code}",
                )
            with self._websocket_lock:
                self._real_time_quote = quote
                self._real_quote_events.append(quote)
                if trade_status is not None:
                    self._market_session_status = trade_status
                    self._market_session_confirmed_at = self._clock()
                    self._market_session_from_trade = True
        return None

    def _register_real_time_message(self, symbol: str = "") -> dict[str, Any]:
        target = normalize_symbol(symbol) or self._real_time_symbol
        return {
            "trnm": "REG",
            "grp_no": "1",
            "refresh": "1",
            "data": [
                {"item": [target], "type": ["0B"]},
                {"item": [""], "type": ["0s"]},
            ],
        }

    def _remove_real_time_message(self, symbol: str = "") -> dict[str, Any]:
        target = normalize_symbol(symbol) or self._real_time_symbol
        return {
            "trnm": "REMOVE",
            "grp_no": "1",
            "data": [
                {"item": [target], "type": ["0B"]},
                {"item": [""], "type": ["0s"]},
            ],
        }

    def _stop_websocket(self) -> None:
        self._websocket_stop.set()
        self._websocket_ready.clear()
        websocket = self._websocket
        if websocket is not None:
            try:
                websocket.close()
            except Exception:
                pass
        thread = self._websocket_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._websocket_thread = None

    def _post(
        self,
        api_id: str,
        path: str,
        body: dict[str, Any],
        authorized: bool = True,
    ) -> dict[str, Any]:
        return self._post_response(api_id, path, body, authorized=authorized).body

    def _post_response(
        self,
        api_id: str,
        path: str,
        body: dict[str, Any],
        authorized: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> RestResponse:
        if authorized and not self._has_valid_token():
            raise KiwoomRestApiError("REST API 토큰이 없거나 만료되었습니다.")
        self._rate_limiter.acquire(api_id)
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "api-id": api_id,
        }
        if authorized:
            headers["authorization"] = f"Bearer {self._token}"
        if extra_headers:
            headers.update(extra_headers)
        response = self._requester(
            "POST",
            f"{self.base_url}{path}",
            headers,
            body,
            DEFAULT_REST_TIMEOUT_SECONDS,
        )
        response_body = response.body if isinstance(response.body, dict) else {}
        return_code = response_body.get("return_code", 0 if response.status_code < 400 else response.status_code)
        try:
            normalized_code = int(return_code)
        except (TypeError, ValueError):
            normalized_code = response.status_code
        if response.status_code >= 400 or normalized_code != 0:
            message = str(response_body.get("return_msg") or "키움 REST API 요청이 실패했습니다.")
            if normalized_code in (8030, 8031):
                key_type = "모의투자용" if self.mock else "실전투자용"
                message = (
                    f"{message} 현재 선택한 서버에 맞는 {key_type} AppKey와 "
                    "SecretKey를 사용해 주세요."
                )
            elif normalized_code == 8010:
                message = (
                    f"{message} 키움 계좌·IP 등록 페이지의 IP 등록 현황과 "
                    "현재 인터넷 공인 IP가 같은지 확인해 주세요."
                )
            raise KiwoomRestApiError(f"{api_id} 오류 {normalized_code}: {message}")
        return RestResponse(response.status_code, response.headers, response_body)

    @staticmethod
    def _response_header(headers: dict[str, str], name: str) -> str:
        target = name.casefold()
        for key, value in headers.items():
            if str(key).casefold() == target:
                return str(value or "").strip()
        return ""

    @staticmethod
    def _trade_side(label: Any, code: Any) -> str | None:
        text = str(label or "").strip()
        if "매수" in text:
            return "BUY"
        if "매도" in text:
            return "SELL"
        normalized_code = str(code or "").strip()
        if normalized_code == "2":
            return "BUY"
        if normalized_code == "1":
            return "SELL"
        return None

    @staticmethod
    def _trade_timestamp(order_date: str, raw_time: Any) -> str:
        digits = "".join(character for character in str(raw_time or "") if character.isdigit())
        if len(digits) >= 14:
            value = digits[:14]
        elif len(digits) >= 6:
            value = f"{order_date}{digits[-6:]}"
        else:
            value = f"{order_date}000000"
        return (
            f"{value[0:4]}-{value[4:6]}-{value[6:8]}T"
            f"{value[8:10]}:{value[10:12]}:{value[12:14]}"
        )

    def _has_valid_token(self) -> bool:
        return bool(self._token and self._clock() < self._token_expires_at - 30.0)

    def _parse_expiration(self, value: Any) -> float:
        text = str(value or "").strip()
        try:
            return datetime.strptime(text, "%Y%m%d%H%M%S").timestamp()
        except ValueError:
            return self._clock() + 23 * 60 * 60

    @staticmethod
    def _default_requester(
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        timeout: float,
    ) -> RestResponse:
        request = Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8-sig")
                parsed = json.loads(raw) if raw else {}
                return RestResponse(response.status, dict(response.headers.items()), parsed)
        except HTTPError as exc:
            raw = exc.read().decode("utf-8-sig", errors="replace")
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = {"return_msg": "키움 REST API HTTP 오류가 발생했습니다."}
            return RestResponse(exc.code, dict(exc.headers.items()), parsed)
        except URLError as exc:
            raise KiwoomRestApiError("키움 REST API 서버에 연결할 수 없습니다.") from exc
