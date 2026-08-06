from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from random import uniform

from .broker import BrokerClient, MockBroker
from .charting import (
    SUPPORTED_MINUTE_INTERVALS,
    SUPPORTED_SECOND_INTERVALS,
    RealTimeCandleAggregator,
    aggregate_minute_candles,
    timeframe_label,
)
from .kiwoom_api import (
    KiwoomAccountInfo,
    KiwoomOpenApiClient,
    KiwoomOpenApiError,
    is_valid_account_password,
)
from .models import (
    BalanceSummary,
    Candle,
    DmiPoint,
    Holding,
    KiwoomOrderRequest,
    MarketQuote,
    MarketSessionStatus,
    OrderBookSnapshot,
    OrderResult,
    PatternState,
    PerformanceSummary,
    RealTimeQuote,
    StrategySettings,
    TradeExecution,
    TradeDecision,
    UnfilledOrder,
    VolumeRankQuote,
    WatchlistQuote,
)
from .order_manager import OrderManager
from .order_pricing import (
    automatic_limit_price,
    midpoint_limit_price,
    performance_from_executions,
)
from .rest_api import KiwoomRestApiClient, KiwoomRestApiError
from .risk import DailyLossCircuitBreaker, DailyRiskStatus, RiskManager
from .storage import Storage
from .strategy import StrategyEngine
from .symbols import clean_account_number, known_symbol_name, mask_account_number, normalize_symbol


API_ERRORS = (KiwoomOpenApiError, KiwoomRestApiError)
ACCOUNT_TRADE_HISTORY_DAYS = 10
MINUTE_TRADE_VALUE_GATE = 4_000_000_000.0


@dataclass
class ServiceSnapshot:
    connection: str
    running: bool
    symbol: str
    symbol_name: str
    pattern: PatternState
    price: float
    quantity: int
    average_price: float
    decision: TradeDecision | None
    account_info: KiwoomAccountInfo
    started_at: datetime | None = None
    dmi: DmiPoint | None = None
    market_session_status: MarketSessionStatus | None = None
    real_time_registered: bool = False
    regular_market_open: bool = False
    market_quote: MarketQuote | None = None
    balance_summary: BalanceSummary | None = None
    real_time_quote: RealTimeQuote | None = None
    chart_candles: list[Candle] = field(default_factory=list)
    chart_timeframe: str = "3m"
    chart_source: str = ""
    last_api_message: str = ""
    orders: list[tuple] = field(default_factory=list)
    trade_history: list[tuple] = field(default_factory=list)
    account_trade_history: list[TradeExecution] = field(default_factory=list)
    account_trade_history_message: str = ""
    account_trade_history_updated_at: datetime | None = None
    order_book: OrderBookSnapshot | None = None
    unfilled_orders: list[UnfilledOrder] = field(default_factory=list)
    daily_performance: PerformanceSummary | None = None
    monthly_performance: PerformanceSummary | None = None
    daily_risk_status: DailyRiskStatus | None = None
    latest_minute_trade_value: float = 0.0
    logs: list[tuple] = field(default_factory=list)


class AutoTradingService:
    def __init__(
        self,
        broker: BrokerClient | None = None,
        storage: Storage | None = None,
    ) -> None:
        self.broker = broker or MockBroker()
        self.storage = storage or Storage()
        self.risk = RiskManager()
        self.daily_loss_breaker = DailyLossCircuitBreaker(
            float(self.storage.get_app_setting("risk.daily_loss_limit_percent", "5") or 5)
        )
        self.daily_loss_breaker.restore_lock(
            self.storage.get_app_setting("risk.locked_date", "")
        )
        self.strategy = StrategyEngine()
        self.order_manager = OrderManager(self.broker, self.storage)
        self.kiwoom_api = KiwoomOpenApiClient()
        self.rest_api = KiwoomRestApiClient(mock=True)
        self.connection_mode = "ACTIVEX"
        self.account_info = KiwoomAccountInfo(False, [], message="키움 계좌가 연결되지 않았습니다.")
        self.expected_user_id = ""
        self.running = False
        self.symbol = "000000"
        self.symbol_name = ""
        self.max_capital = 1_000_000.0
        self.current_price = 0.0
        self.pattern_state: PatternState = "NONE"
        self.candles: list[Candle] = []
        self.dmi_candles: list[Candle] = []
        self.chart_candles: list[Candle] = []
        self.chart_timeframe = "3m"
        self.chart_source = "키움 분봉 API"
        self.real_time_candles = RealTimeCandleAggregator()
        self._last_aggregated_quote_key: tuple | None = None
        self._tick_history_symbol = ""
        self.latest_dmi: DmiPoint | None = None
        self.last_decision: TradeDecision | None = None
        self.market_quote: MarketQuote | None = None
        self.balance_summary: BalanceSummary | None = None
        self.account_trade_history: list[TradeExecution] = []
        self.account_trade_history_message = (
            f"키움 계좌 연결 후 최근 {ACCOUNT_TRADE_HISTORY_DAYS}일 전체 체결내역을 조회합니다."
        )
        self.account_trade_history_updated_at: datetime | None = None
        self.order_book: OrderBookSnapshot | None = None
        self.unfilled_orders: list[UnfilledOrder] = []
        self.daily_risk_status: DailyRiskStatus | None = None
        self.real_time_symbol = ""
        self.real_time_quote: RealTimeQuote | None = None
        self._real_time_registration_logged = False
        self._first_real_time_quote_logged_symbol = ""
        self.watchlist_quotes: dict[str, WatchlistQuote] = {}
        self.volume_ranking: list[VolumeRankQuote] = []
        self.volume_ranking_message = "REST API 연결 후 실시간 거래량 순위를 불러옵니다."
        self.trade_value_ranking: list[VolumeRankQuote] = []
        self.trade_value_ranking_message = (
            "REST API 연결 후 거래대금 상위 순위를 불러옵니다."
        )
        self.last_api_message = ""
        self.last_order_account_access_verified: bool | None = None
        self.started_at: datetime | None = None
        self.latest_minute_trade_value = 0.0
        self.latest_minute_trade_value_timestamp = ""

    def configure(
        self,
        symbol: str,
        max_capital: float,
        settings: StrategySettings,
    ) -> None:
        previous_symbol = self.symbol
        next_symbol = normalize_symbol(symbol) or "000000"
        if next_symbol != previous_symbol or self.strategy.settings != settings:
            self.strategy = StrategyEngine(settings)
            self.dmi_candles = []
            self.latest_dmi = None
            self.pattern_state = "NONE"
        if next_symbol != previous_symbol:
            self.chart_candles = []
            self.real_time_candles.reset(next_symbol)
            self._last_aggregated_quote_key = None
            self._tick_history_symbol = ""
            self.market_quote = None
            self.real_time_quote = None
            self._real_time_registration_logged = False
            self._first_real_time_quote_logged_symbol = ""
        self.symbol = next_symbol
        fallback_name = known_symbol_name(self.symbol)
        if fallback_name:
            self.symbol_name = fallback_name
        elif next_symbol != previous_symbol:
            self.symbol_name = ""
        self.max_capital = max_capital
        self.storage.log("INFO", "설정", f"{self.symbol} 설정을 저장했습니다.")

    def start(self) -> None:
        self.order_manager.resume()
        if self.running:
            return
        self.running = True
        self.started_at = datetime.now()
        self.storage.log(
            "INFO",
            "시스템",
            f"자동 운용을 시작했습니다. 시작 시각 {self.started_at.strftime('%Y-%m-%d %H:%M:%S')}",
        )

    def stop(self) -> None:
        self.running = False
        self.order_manager.request_stop()
        self.storage.log("WARN", "시스템", "자동 운용을 중지했습니다.")

    def emergency_stop(self) -> None:
        self.stop()
        self.storage.log("ERROR", "시스템", "긴급 정지가 요청되었습니다.")

    def start_account_connection(self, expected_user_id: str = "") -> str:
        self.connection_mode = "ACTIVEX"
        self.rest_api.clear_session()
        self.expected_user_id = expected_user_id.strip()
        self.account_info = KiwoomAccountInfo(
            False,
            [],
            message="키움 OpenAPI+ 로그인과 사용자 ID 확인을 기다리고 있습니다.",
        )
        self._clear_live_trading_state()
        try:
            message = self.kiwoom_api.start_login()
            self.storage.log("INFO", "계좌", message)
            return message
        except KiwoomOpenApiError as exc:
            message = str(exc)
            self.account_info = KiwoomAccountInfo(False, [], message=message)
            self.storage.log("ERROR", "계좌", message)
            return message

    def start_rest_connection(
        self,
        app_key: str,
        secret_key: str,
        mock: bool = True,
    ) -> KiwoomAccountInfo:
        self.connection_mode = "REST"
        self.expected_user_id = ""
        if isinstance(self.rest_api, KiwoomRestApiClient) and self.rest_api.mock != mock:
            self.rest_api.clear_session()
            self.rest_api = KiwoomRestApiClient(mock=mock)
        elif hasattr(self.rest_api, "mock"):
            self.rest_api.mock = mock
        server_type = "모의투자" if mock else "실거래"
        self.account_info = KiwoomAccountInfo(
            False,
            [],
            server_type=server_type,
            message=f"키움 REST API {server_type} 토큰과 계좌를 확인하고 있습니다.",
            connection_method="REST API",
        )
        self._clear_live_trading_state()
        try:
            self.account_info = self.rest_api.connect(app_key, secret_key)
            self.storage.log("INFO", "계좌", self.account_info.message)
        except KiwoomRestApiError as exc:
            message = str(exc)
            self.account_info = KiwoomAccountInfo(
                False,
                [],
                server_type=server_type,
                message=message,
                connection_method="REST API",
            )
            self.storage.log("ERROR", "계좌", message)
        return self.account_info

    def check_account_environment(self) -> str:
        if self.connection_mode == "REST":
            self.account_info = self.rest_api.get_account_info()
            self.storage.log("INFO", "계좌", self.account_info.message)
            return self.account_info.message
        status = self.kiwoom_api.check_environment()
        message = status.message
        if status.active_x_available:
            try:
                self.account_info = self._verify_account_info(self.kiwoom_api.get_account_info())
                message = self.account_info.message
            except KiwoomOpenApiError as exc:
                self.account_info = KiwoomAccountInfo(False, [], message=str(exc))
                message = str(exc)
        else:
            self.account_info = KiwoomAccountInfo(False, [], message=message)
        self.storage.log("INFO", "계좌", message)
        return message

    def refresh_account_connection(self) -> KiwoomAccountInfo:
        if self.connection_mode == "REST":
            self.account_info = self.rest_api.get_account_info()
            return self.account_info
        previous_message = self.account_info.message
        try:
            raw_info = self.kiwoom_api.get_account_info()
            self.account_info = self._verify_account_info(raw_info)
            if not self.account_info.connected:
                if raw_info.connected:
                    self._clear_live_trading_state()
                else:
                    self.account_info = KiwoomAccountInfo(
                        False,
                        [],
                        message=self.kiwoom_api.login_status_message(),
                    )
        except KiwoomOpenApiError as exc:
            self.account_info = KiwoomAccountInfo(False, [], message=str(exc))
        if self.account_info.message != previous_message:
            self.storage.log("INFO", "계좌", self.account_info.message)
        return self.account_info

    def sync_account_connection(self) -> bool:
        """Keep the UI/order gate aligned with the live OpenAPI connection."""
        if not self.account_info.connected:
            return False
        api = self._active_api()
        method = self.account_info.connection_method or "OpenAPI+"
        try:
            if api.is_connected():
                return True
            message = f"키움 {method} 연결이 종료되어 자동운용과 주문을 중지했습니다."
        except API_ERRORS as exc:
            message = f"키움 {method} 연결 확인 실패로 자동운용과 주문을 중지했습니다: {exc}"

        if self.connection_mode == "REST":
            self.rest_api.clear_session()
        self.account_info = KiwoomAccountInfo(
            False,
            [],
            message=message,
            connection_method=method,
        )
        self._clear_live_trading_state()
        self.last_api_message = message
        self.storage.log("ERROR", "계좌", message)
        return False

    def _verify_account_info(self, info: KiwoomAccountInfo) -> KiwoomAccountInfo:
        if not info.connected:
            return info
        if not info.login_data_received:
            return KiwoomAccountInfo(
                False,
                [],
                user_id=info.user_id,
                user_name=info.user_name,
                server_type=info.server_type,
                message=info.message or "키움 로그인 정보 수신 확인에 실패했습니다.",
                reported_account_count=info.reported_account_count,
                login_event_code=info.login_event_code,
                connection_method=info.connection_method,
            )
        expected = self.expected_user_id.casefold()
        actual = info.user_id.strip().casefold()
        if not expected:
            return KiwoomAccountInfo(
                False,
                [],
                user_id=info.user_id,
                user_name=info.user_name,
                server_type=info.server_type,
                message="앱에서 확인할 키움 ID를 입력한 뒤 OpenAPI+ 로그인을 시작해 주세요.",
                reported_account_count=info.reported_account_count,
                login_event_code=info.login_event_code,
                connection_method=info.connection_method,
            )
        if not actual or actual != expected:
            return KiwoomAccountInfo(
                False,
                [],
                user_id=info.user_id,
                user_name=info.user_name,
                server_type=info.server_type,
                message="OpenAPI+ 로그인 ID가 앱에서 입력한 ID와 일치하지 않아 연결을 차단했습니다.",
                reported_account_count=info.reported_account_count,
                login_event_code=info.login_event_code,
                connection_method=info.connection_method,
            )
        return info

    def _clear_live_trading_state(self) -> None:
        self.running = False
        self.order_manager.request_stop()
        self.market_quote = None
        self.balance_summary = None
        self.account_trade_history = []
        self.account_trade_history_message = (
            f"키움 계좌 연결 후 최근 {ACCOUNT_TRADE_HISTORY_DAYS}일 전체 체결내역을 조회합니다."
        )
        self.account_trade_history_updated_at = None
        self.real_time_symbol = ""
        self.real_time_quote = None
        self._real_time_registration_logged = False
        self._first_real_time_quote_logged_symbol = ""
        self.watchlist_quotes = {}
        self.volume_ranking = []
        self.volume_ranking_message = "REST API 연결 후 실시간 거래량 순위를 불러옵니다."
        self.trade_value_ranking = []
        self.trade_value_ranking_message = (
            "REST API 연결 후 거래대금 상위 순위를 불러옵니다."
        )
        self.candles = []
        self.chart_candles = []
        self.chart_timeframe = "3m"
        self.chart_source = "키움 분봉 API"
        self.real_time_candles.reset(self.symbol)
        self._last_aggregated_quote_key = None
        self._tick_history_symbol = ""
        self.latest_dmi = None
        self.pattern_state = "NONE"
        self.strategy.reset()
        self.last_decision = None
        self.last_order_account_access_verified = None

    def account_login_status(self) -> str:
        try:
            return self._active_api().login_status_message()
        except API_ERRORS as exc:
            return str(exc)

    def _active_api(self):
        return self.rest_api if self.connection_mode == "REST" else self.kiwoom_api

    def latest_market_session_status(self) -> MarketSessionStatus | None:
        getter = getattr(self._active_api(), "latest_market_session_status", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except API_ERRORS as exc:
            self.last_api_message = str(exc)
            return None

    def is_regular_market_open(self) -> bool:
        api = self._active_api()
        checker = getattr(api, "is_regular_market_open", None)
        if callable(checker):
            try:
                return bool(checker())
            except API_ERRORS as exc:
                self.last_api_message = str(exc)
                return False
        status = self.latest_market_session_status()
        return bool(status and status.is_open)

    def is_real_time_registered(self) -> bool:
        api = self._active_api()
        checker = getattr(api, "is_real_time_registered", None)
        if callable(checker):
            try:
                return bool(checker())
            except API_ERRORS as exc:
                self.last_api_message = str(exc)
                return False
        return bool(self.real_time_symbol and self.account_info.connected)

    def lookup_symbol_name(self, symbol: str | None = None) -> str:
        target = normalize_symbol(symbol or self.symbol)
        if not target:
            self.symbol_name = ""
            self.last_api_message = "종목번호를 입력해 주세요."
            return ""

        self.symbol = target
        fallback_name = known_symbol_name(target)
        if fallback_name:
            self.symbol_name = fallback_name

        api = self._active_api()
        try:
            api_name = api.lookup_symbol_name(target)
            if api_name:
                self.symbol_name = api_name
                self.last_api_message = f"{target} 종목명: {api_name}"
                self.storage.log("INFO", "종목", self.last_api_message)
                return api_name
        except API_ERRORS as exc:
            if not fallback_name:
                self.last_api_message = str(exc)
                self.storage.log("WARN", "종목", self.last_api_message)
                return ""

        if fallback_name:
            self.last_api_message = f"{target} 종목명: {fallback_name}"
            self.storage.log("INFO", "종목", self.last_api_message)
        return self.symbol_name

    def search_symbols(self, query: str, limit: int = 20) -> list[tuple[str, str]]:
        needle = str(query or "").strip()
        if not needle:
            return []
        api = self._active_api()
        search = getattr(api, "search_symbols", None)
        if callable(search):
            try:
                return list(search(needle, limit=limit))
            except API_ERRORS as exc:
                self.last_api_message = str(exc)
                self.storage.log("ERROR", "종목검색", self.last_api_message)
        local: dict[str, str] = {
            symbol: name for symbol, name in self.storage.watchlist_symbols()
        }
        local.update({quote.symbol: quote.name for quote in self.volume_ranking})
        local.update({quote.symbol: quote.name for quote in self.trade_value_ranking})
        if self.balance_summary is not None:
            local.update(
                {holding.symbol: holding.name for holding in self.balance_summary.holdings}
            )
        folded = needle.casefold()
        return [
            item
            for item in local.items()
            if folded in item[0].casefold() or folded in item[1].casefold()
        ][: max(1, int(limit))]

    def request_current_price(self, symbol: str | None = None) -> MarketQuote | None:
        target = normalize_symbol(symbol or self.symbol)
        api = self._active_api()
        try:
            self.market_quote = api.request_current_price(target)
            self.symbol = self.market_quote.symbol or target
            if self.market_quote.name:
                self.symbol_name = self.market_quote.name
            if self.market_quote.current_price > 0:
                self.current_price = self.market_quote.current_price
            existing_watch = self.watchlist_quotes.get(self.symbol)
            if existing_watch is not None:
                self.watchlist_quotes[self.symbol] = replace(
                    existing_watch,
                    name=self.market_quote.name or existing_watch.name,
                    current_price=self.market_quote.current_price,
                    change=self.market_quote.change,
                    change_rate=self.market_quote.change_rate,
                    timestamp=self.market_quote.timestamp,
                )
            self.last_api_message = self.market_quote.message
            self.storage.log("INFO", "시세", f"{target} 현재가 {self.current_price:,.0f}")
            return self.market_quote
        except API_ERRORS as exc:
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "시세", self.last_api_message)
            return None

    def request_order_book(self, symbol: str | None = None) -> OrderBookSnapshot | None:
        target = normalize_symbol(symbol or self.symbol)
        if not target:
            self.last_api_message = "호가를 조회할 종목코드를 입력해 주세요."
            return None
        try:
            self.order_book = self._active_api().request_order_book(target)
            self.last_api_message = (
                f"{target} 10호가 조회 완료 · 매도1 {self.order_book.best_ask:,.0f}원 / "
                f"매수1 {self.order_book.best_bid:,.0f}원"
            )
            self.storage.log("INFO", "호가", self.last_api_message)
            return self.order_book
        except API_ERRORS as exc:
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "호가", self.last_api_message)
            return None

    def midpoint_price(self, side: str, symbol: str | None = None) -> int:
        normalized_side = "BUY" if side == "BUY" else "SELL"
        book = self.request_order_book(symbol)
        if book is None:
            raise KiwoomOpenApiError(self.last_api_message or "호가 조회에 실패했습니다.")
        try:
            return midpoint_limit_price(book.best_ask, book.best_bid, normalized_side)
        except ValueError as exc:
            raise KiwoomOpenApiError(str(exc)) from exc

    def automatic_price(self, side: str, symbol: str | None = None) -> tuple[int, str]:
        normalized_side = "BUY" if side == "BUY" else "SELL"
        book = self.request_order_book(symbol)
        if book is None:
            raise KiwoomOpenApiError(self.last_api_message or "호가 조회에 실패했습니다.")
        try:
            return automatic_limit_price(book, normalized_side)
        except ValueError as exc:
            raise KiwoomOpenApiError(str(exc)) from exc

    def request_latest_minute_trade_value(self, symbol: str | None = None) -> float | None:
        """Read the newest 1-minute candle and expose its value in won.

        The filter is applied only when the connected Kiwoom API supplies minute
        candles. Mock adapters and older adapters without this endpoint keep the
        previous behavior so offline UI tests remain usable.
        """
        target = normalize_symbol(symbol or self.symbol)
        api = self._active_api()
        request_candles = getattr(api, "request_minute_candles", None)
        if not target or not callable(request_candles):
            return None
        try:
            candles = list(request_candles(target, interval=1, count=1) or [])
        except API_ERRORS as exc:
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "거래대금", self.last_api_message)
            return None
        if not candles:
            self.latest_minute_trade_value = 0.0
            self.latest_minute_trade_value_timestamp = ""
            return 0.0
        candle = candles[0]
        self.latest_minute_trade_value = max(0.0, float(candle.close)) * max(0, int(candle.volume))
        self.latest_minute_trade_value_timestamp = str(candle.timestamp or "")
        return self.latest_minute_trade_value

    def request_unfilled_orders(
        self,
        account: str,
        symbol: str = "",
    ) -> list[UnfilledOrder]:
        account = clean_account_number(account)
        if not account and self.account_info.accounts:
            account = clean_account_number(self.account_info.accounts[0])
        try:
            self.unfilled_orders = self._active_api().request_unfilled_orders(
                account,
                symbol=symbol,
            )
            self.last_api_message = f"미체결 {len(self.unfilled_orders)}건 조회 완료"
            if self.unfilled_orders:
                self.storage.log("WARN", "미체결", self.last_api_message)
            return list(self.unfilled_orders)
        except API_ERRORS as exc:
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "미체결", self.last_api_message)
            return list(self.unfilled_orders)

    def add_watchlist_symbol(self, symbol: str) -> str:
        normalized = normalize_symbol(symbol)
        if not normalized:
            self.last_api_message = "등록할 관심종목 코드를 입력해 주세요."
            return ""
        existing = {value for value, _name in self.storage.watchlist_symbols()}
        if normalized not in existing and len(existing) >= 20:
            self.last_api_message = "관심종목은 최대 20개까지 등록할 수 있습니다."
            self.storage.log("WARN", "관심종목", self.last_api_message)
            return ""
        name = known_symbol_name(normalized)
        if self.account_info.connected:
            try:
                name = self._active_api().lookup_symbol_name(normalized) or name
            except API_ERRORS:
                pass
        self.storage.add_watchlist_symbol(normalized, name)
        self.last_api_message = f"관심종목 {normalized} {name} 등록 완료".strip()
        self.storage.log("INFO", "관심종목", self.last_api_message)
        return normalized

    def remove_watchlist_symbol(self, symbol: str) -> None:
        normalized = normalize_symbol(symbol)
        if not normalized:
            return
        self.storage.remove_watchlist_symbol(normalized)
        self.watchlist_quotes.pop(normalized, None)
        self.last_api_message = f"관심종목 {normalized} 삭제 완료"
        self.storage.log("INFO", "관심종목", self.last_api_message)

    def watchlist_items(self) -> list[tuple[str, str]]:
        return self.storage.watchlist_symbols()

    def watchlist_rows(self) -> list[WatchlistQuote]:
        return [
            self.watchlist_quotes.get(symbol, WatchlistQuote(symbol=symbol, name=name))
            for symbol, name in self.watchlist_items()
        ]

    def refresh_watchlist_quotes(
        self,
        supplemental_fields: set[str] | None = None,
    ) -> list[WatchlistQuote]:
        items = self.watchlist_items()
        symbols = [symbol for symbol, _name in items]
        if not symbols:
            self.watchlist_quotes = {}
            self.last_api_message = "등록된 관심종목이 없습니다."
            return []
        api = self._active_api()
        try:
            quotes = api.request_watchlist_quotes(symbols)
            supplements = set(supplemental_fields or ())
            enriched_quotes: list[WatchlistQuote] = []
            for quote in quotes:
                updates: dict[str, float] = {}
                if "previous_trade_value" in supplements:
                    request_previous = getattr(
                        api,
                        "request_watchlist_previous_trade_value",
                        None,
                    )
                    if callable(request_previous):
                        try:
                            updates["previous_trade_value"] = float(
                                request_previous(quote.symbol)
                            )
                        except API_ERRORS as exc:
                            self.storage.log(
                                "WARN",
                                "관심종목",
                                f"{quote.symbol} 전일 거래대금 조회 실패: {exc}",
                            )
                if "program_trading_trend" in supplements:
                    request_program = getattr(
                        api,
                        "request_watchlist_program_trading_trend",
                        None,
                    )
                    if callable(request_program):
                        try:
                            updates["program_trading_trend"] = float(
                                request_program(quote.symbol)
                            )
                        except API_ERRORS as exc:
                            self.storage.log(
                                "WARN",
                                "관심종목",
                                f"{quote.symbol} 프로그램 매매 추이 조회 실패: {exc}",
                            )
                enriched_quotes.append(replace(quote, **updates) if updates else quote)
            quotes = enriched_quotes
            self.watchlist_quotes = {quote.symbol: quote for quote in quotes if quote.symbol}
            for quote in quotes:
                if quote.symbol and quote.name:
                    self.storage.add_watchlist_symbol(quote.symbol, quote.name)
            selected = self.watchlist_quotes.get(self.symbol)
            if selected is not None:
                self.symbol_name = selected.name or self.symbol_name
                if selected.current_price > 0:
                    self.current_price = selected.current_price
            self.last_api_message = f"관심종목 {len(quotes)}개 시세 조회 완료"
            self.storage.log("INFO", "관심종목", self.last_api_message)
            return self.watchlist_rows()
        except API_ERRORS as exc:
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "관심종목", self.last_api_message)
            return self.watchlist_rows()

    def refresh_volume_ranking(
        self,
        market: str = "000",
        limit: int = 15,
    ) -> list[VolumeRankQuote]:
        if self.connection_mode != "REST" or not self.account_info.connected:
            self.volume_ranking = []
            self.volume_ranking_message = "키움 REST API 연결 후 거래량 순위를 불러올 수 있습니다."
            return []
        try:
            rows = self.rest_api.request_volume_ranking(market=market, limit=limit)
            self.volume_ranking = list(rows[: max(1, int(limit))])
            now = datetime.now().strftime("%H:%M:%S")
            self.volume_ranking_message = (
                f"키움 ka10030 · {len(self.volume_ranking)}개 · {now} 갱신"
            )
            return list(self.volume_ranking)
        except (KiwoomRestApiError, AttributeError) as exc:
            self.volume_ranking_message = str(exc)
            self.last_api_message = str(exc)
            return list(self.volume_ranking)

    def refresh_trade_value_ranking(
        self,
        market: str = "000",
        limit: int = 15,
    ) -> list[VolumeRankQuote]:
        if self.connection_mode != "REST" or not self.account_info.connected:
            self.trade_value_ranking = []
            self.trade_value_ranking_message = (
                "키움 REST API 연결 후 거래대금 상위 순위를 불러올 수 있습니다."
            )
            return []
        try:
            rows = self.rest_api.request_trade_value_ranking(
                market=market,
                limit=limit,
            )
            self.trade_value_ranking = list(rows[: max(1, int(limit))])
            now = datetime.now().strftime("%H:%M:%S")
            self.trade_value_ranking_message = (
                f"키움 ka10032 · {len(self.trade_value_ranking)}개 · {now} 갱신"
            )
            return list(self.trade_value_ranking)
        except (KiwoomRestApiError, AttributeError) as exc:
            self.trade_value_ranking_message = str(exc)
            self.last_api_message = str(exc)
            return list(self.trade_value_ranking)

    def request_three_minute_candles(self, symbol: str | None = None) -> list[Candle]:
        target = normalize_symbol(symbol or self.symbol)
        api = self._active_api()
        try:
            self.candles = api.request_minute_candles(target, interval=3, count=120)
            self.symbol = target
            if self.candles:
                self.current_price = self.candles[0].close
            if self.chart_timeframe == "3m":
                self.chart_candles = list(self.candles)
                self.chart_source = "키움 ka10080/opt10080"
            self.last_api_message = f"{target} 3분봉 {len(self.candles)}개 조회 완료"
            self.storage.log("INFO", "시세", self.last_api_message)
            return self.candles
        except API_ERRORS as exc:
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "시세", self.last_api_message)
            return []

    def request_daily_dmi_candles(
        self,
        symbol: str | None = None,
        *,
        log_result: bool = True,
    ) -> list[Candle]:
        target = normalize_symbol(symbol or self.symbol)
        api = self._active_api()
        period = self.strategy.settings.dmi_period
        try:
            self.dmi_candles = api.request_daily_candles(
                target,
                count=max(200, (period * 2) + 1),
            )
            self.symbol = target
            self.latest_dmi = None
            self.pattern_state = "NONE"
            if self.dmi_candles:
                ordered_candles = list(reversed(self.dmi_candles))
                self.latest_dmi = self.strategy.latest_dmi(ordered_candles)
                if self.latest_dmi is not None:
                    self.pattern_state = self.latest_dmi.pattern_state
            self.last_api_message = (
                f"{target} 일봉 {len(self.dmi_candles)}개로 "
                f"DMI({period}일) 계산 완료"
            )
            if log_result:
                self.storage.log("INFO", "DMI", self.last_api_message)
            return self.dmi_candles
        except API_ERRORS as exc:
            self.dmi_candles = []
            self.latest_dmi = None
            self.pattern_state = "NONE"
            self.last_api_message = str(exc)
            if log_result:
                self.storage.log("ERROR", "DMI", self.last_api_message)
            return []

    def request_chart_candles(
        self,
        interval_minutes: int,
        symbol: str | None = None,
    ) -> list[Candle]:
        interval_minutes = int(interval_minutes)
        if interval_minutes not in SUPPORTED_MINUTE_INTERVALS:
            supported = ", ".join(str(value) for value in SUPPORTED_MINUTE_INTERVALS)
            self.last_api_message = f"분봉 간격은 {supported}분만 지원합니다."
            self.storage.log("WARN", "차트", self.last_api_message)
            return []

        self.chart_timeframe = f"{interval_minutes}m"
        if interval_minutes == 3:
            return self.request_three_minute_candles(symbol)

        target = normalize_symbol(symbol or self.symbol)
        api = self._active_api()
        try:
            native_interval = 60 if interval_minutes in {120, 240} else interval_minutes
            native_count = 200 * max(1, interval_minutes // native_interval)
            native_candles = api.request_minute_candles(
                target,
                interval=native_interval,
                count=native_count,
            )
            self.chart_candles = (
                aggregate_minute_candles(native_candles, native_interval, interval_minutes)
                if interval_minutes != native_interval
                else native_candles
            )
            self.symbol = target
            self.chart_source = (
                f"키움 60분봉 기반 {interval_minutes}분 집계"
                if interval_minutes in {120, 240}
                else "키움 ka10080/opt10080"
            )
            if self.chart_candles:
                self.current_price = self.chart_candles[0].close
            label = timeframe_label(self.chart_timeframe)
            self.last_api_message = f"{target} {label}봉 {len(self.chart_candles)}개 조회 완료"
            self.storage.log("INFO", "차트", self.last_api_message)
            return self.chart_candles
        except API_ERRORS as exc:
            self.chart_candles = []
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "차트", self.last_api_message)
            return []

    def select_realtime_chart(self, interval_seconds: int) -> list[Candle]:
        interval_seconds = int(interval_seconds)
        if interval_seconds not in SUPPORTED_SECOND_INTERVALS:
            supported = ", ".join(str(value) for value in SUPPORTED_SECOND_INTERVALS)
            self.last_api_message = f"초봉 간격은 {supported}초만 지원합니다."
            self.storage.log("WARN", "차트", self.last_api_message)
            return []
        self.chart_timeframe = f"{interval_seconds}s"
        target = normalize_symbol(self.symbol)
        if self.connection_mode == "REST" and self._tick_history_symbol != target:
            self._load_rest_tick_history(target)
        if self.connection_mode == "REST" and self._tick_history_symbol == target:
            self.chart_source = "키움 ka10079 1틱 + 0B 실시간"
        else:
            self.chart_source = "키움 실시간 체결 0B/주식체결"
        return self.real_time_candles.candles(interval_seconds)

    def _load_rest_tick_history(self, symbol: str) -> None:
        request_ticks = getattr(self.rest_api, "request_tick_candles", None)
        if not callable(request_ticks) or not symbol or symbol == "000000":
            return
        try:
            ticks = request_ticks(symbol, count=3000)
        except API_ERRORS as exc:
            self.last_api_message = f"REST 1틱 이력 조회 실패: {exc}"
            self.storage.log("ERROR", "초봉", self.last_api_message)
            return

        if self.real_time_candles.symbol != symbol:
            self.real_time_candles.reset(symbol)
        for tick in reversed(ticks):
            self.real_time_candles.add_tick_candle(symbol, tick)
        self._tick_history_symbol = symbol
        second_count = len(self.real_time_candles.candles(1))
        self.last_api_message = (
            f"{symbol} ka10079 1틱 {len(ticks)}개를 실제 체결시간 기준 "
            f"1초봉 {second_count}개로 집계했습니다."
        )
        self.storage.log("INFO", "초봉", self.last_api_message)

    def chart_candles_for_display(self) -> list[Candle]:
        if self.chart_timeframe.endswith("s"):
            return self.real_time_candles.candles(int(self.chart_timeframe[:-1]))
        return list(self.chart_candles)

    def evaluate_strategy_with_market_data(
        self,
        symbol: str | None = None,
    ) -> TradeDecision | None:
        candles = self.request_daily_dmi_candles(symbol)
        required = self.strategy.settings.dmi_period + 1
        if len(candles) < required:
            self.last_api_message = (
                f"DMI 전략 판단에 필요한 일봉이 부족합니다({len(candles)}/{required})."
            )
            self.storage.log("WARN", "전략", self.last_api_message)
            return None
        ordered_candles = list(reversed(candles))
        decision = self.strategy.evaluate(ordered_candles)
        self.latest_dmi = self.strategy.last_dmi
        self.pattern_state = decision.pattern_state
        self.last_decision = decision
        adx_text = "계산 중" if decision.adx is None else f"{decision.adx:.2f}"
        self.last_api_message = (
            f"일봉 DMI({self.strategy.settings.dmi_period}일) 전략 판단: {decision.action} / "
            f"+DI {decision.dmi_plus or 0.0:.2f}, -DI {decision.dmi_minus or 0.0:.2f}, "
            f"ADX {adx_text} / {decision.reason}"
        )
        self.storage.log("INFO", "전략", self.last_api_message)
        return decision

    def request_balance(
        self,
        account: str,
        password: str = "",
        log_result: bool = True,
    ) -> BalanceSummary | None:
        account = clean_account_number(account)
        if not account and self.account_info.accounts:
            account = clean_account_number(self.account_info.accounts[0])
        api = self._active_api()
        try:
            self.balance_summary = api.request_balance(account, password=password)
            self.last_api_message = self.balance_summary.message
            self.refresh_daily_risk()
            if log_result:
                self.storage.log(
                    "INFO",
                    "잔고",
                    f"{mask_account_number(account)} 보유종목 {len(self.balance_summary.holdings)}개 조회 완료",
                )
            return self.balance_summary
        except API_ERRORS as exc:
            self.last_api_message = str(exc)
            if log_result:
                self.storage.log("ERROR", "잔고", self.last_api_message)
            return None

    def request_today_trade_history(
        self,
        account: str,
        password: str = "",
        log_result: bool = True,
    ) -> list[TradeExecution] | None:
        return self.request_recent_trade_history(
            account,
            password=password,
            days=1,
            log_result=log_result,
        )

    def request_recent_trade_history(
        self,
        account: str,
        password: str = "",
        days: int = ACCOUNT_TRADE_HISTORY_DAYS,
        end_date: str | None = None,
        log_result: bool = True,
    ) -> list[TradeExecution] | None:
        account = clean_account_number(account)
        if not account and self.account_info.accounts:
            account = clean_account_number(self.account_info.accounts[0])
        api = self._active_api()
        try:
            history_days = int(days)
            if not 1 <= history_days <= 31:
                raise ValueError("체결내역 조회 기간은 1~31일로 지정해 주세요.")
            if end_date:
                end = datetime.strptime(str(end_date).strip(), "%Y%m%d")
            else:
                end = datetime.now()
            query_dates = [
                (end - timedelta(days=offset)).strftime("%Y%m%d")
                for offset in range(history_days)
            ]
            history: list[TradeExecution] = []
            for query_date in query_dates:
                history.extend(
                    api.request_today_trade_history(
                        account,
                        password=password,
                        order_date=query_date,
                    )
                )
            self.account_trade_history = sorted(
                list(history),
                key=lambda item: item.timestamp,
                reverse=True,
            )
            self.account_trade_history_updated_at = datetime.now()
            self.refresh_daily_risk()
            start_label = datetime.strptime(query_dates[-1], "%Y%m%d").strftime("%Y-%m-%d")
            end_label = datetime.strptime(query_dates[0], "%Y%m%d").strftime("%Y-%m-%d")
            self.account_trade_history_message = (
                f"키움 최근 {history_days}일 계좌 전체 체결 "
                f"{len(self.account_trade_history)}건 조회 완료 "
                f"({start_label}~{end_label}, 초 단위)"
            )
            self.last_api_message = self.account_trade_history_message
            if log_result:
                self.storage.log(
                    "INFO",
                    "체결내역",
                    f"{mask_account_number(account)} {self.account_trade_history_message}",
                )
            return list(self.account_trade_history)
        except (KiwoomOpenApiError, KiwoomRestApiError, ValueError) as exc:
            self.account_trade_history_message = f"키움 최근 체결내역 조회 실패: {exc}"
            self.last_api_message = self.account_trade_history_message
            if log_result:
                self.storage.log("ERROR", "체결내역", self.account_trade_history_message)
            return None

    def performance_summary(self, period: str = "daily") -> PerformanceSummary:
        now = datetime.now()
        normalized = period.strip().lower()
        if normalized == "monthly":
            prefix = now.strftime("%Y-%m")
            label = now.strftime("%Y년 %m월")
        else:
            prefix = now.strftime("%Y-%m-%d")
            label = now.strftime("%Y-%m-%d")
        executions = [
            execution
            for execution in self.account_trade_history
            if str(execution.timestamp).startswith(prefix)
        ]
        return performance_from_executions(executions, label)

    def configure_daily_loss_limit(self, limit_percent: float) -> None:
        self.daily_loss_breaker.configure(limit_percent)
        self.storage.set_app_setting(
            "risk.daily_loss_limit_percent",
            self.daily_loss_breaker.limit_percent,
        )
        self.refresh_daily_risk()

    def refresh_daily_risk(self) -> DailyRiskStatus | None:
        balance = self.balance_summary
        if balance is None:
            self.daily_risk_status = None
            return None
        daily = self.performance_summary("daily")
        starting_assets = (
            balance.estimated_assets
            - balance.total_profit_loss
            - daily.realized_profit
        )
        if starting_assets <= 0:
            starting_assets = balance.estimated_assets or balance.deposit
        self.daily_risk_status = self.daily_loss_breaker.evaluate(
            starting_assets=starting_assets,
            realized_profit=daily.realized_profit,
            unrealized_profit=balance.total_profit_loss,
        )
        self.storage.set_app_setting(
            "risk.locked_date",
            self.daily_loss_breaker.locked_date,
        )
        if self.daily_risk_status.locked:
            self.running = False
            self.order_manager.request_stop()
            self.storage.log("ERROR", "위험관리", self.daily_risk_status.reason)
        return self.daily_risk_status

    def daily_trade_locked(self, trade_date: date | None = None) -> bool:
        return not self.daily_loss_breaker.can_trade(trade_date)

    def register_real_time_price(self, symbol: str | None = None) -> str:
        target = normalize_symbol(symbol or self.symbol)
        api = self._active_api()
        try:
            if target != self.real_time_symbol:
                self.real_time_candles.reset(target)
                self._last_aggregated_quote_key = None
                self._tick_history_symbol = ""
            self._real_time_registration_logged = False
            self._first_real_time_quote_logged_symbol = ""
            self.last_api_message = api.register_real_time_price(target)
            self.symbol = target
            self.real_time_symbol = target
            self.storage.log("INFO", "실시간", self.last_api_message)
        except API_ERRORS as exc:
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "실시간", self.last_api_message)
        return self.last_api_message

    def refresh_real_time_quote(self) -> RealTimeQuote | None:
        if not self.real_time_symbol:
            return None
        api = self._active_api()
        try:
            api.pump_messages()
            registered = self.is_real_time_registered()
            if (
                self.connection_mode == "REST"
                and registered
                and not self._real_time_registration_logged
            ):
                self.storage.log(
                    "INFO",
                    "실시간",
                    f"{self.real_time_symbol} REST WebSocket 등록 완료 · 다음 체결 대기",
                )
                self._real_time_registration_logged = True
            drain = getattr(api, "drain_real_time_quotes", None)
            quotes = list(drain(self.real_time_symbol)) if callable(drain) else []
            self.real_time_quote = api.latest_real_time_quote(self.real_time_symbol)
        except API_ERRORS as exc:
            message = str(exc)
            if message != self.last_api_message:
                self.storage.log("ERROR", "실시간", message)
            self.last_api_message = message
            return None
        if not quotes and self.real_time_quote is not None:
            quote_key = self._real_time_quote_key(self.real_time_quote)
            if quote_key != self._last_aggregated_quote_key:
                quotes = [self.real_time_quote]
        for quote in quotes:
            self.real_time_candles.add(quote)
            self._last_aggregated_quote_key = self._real_time_quote_key(quote)
        if self.real_time_quote and self.real_time_quote.current_price > 0:
            self.current_price = self.real_time_quote.current_price
            if self._first_real_time_quote_logged_symbol != self.real_time_quote.symbol:
                session_code = self.real_time_quote.market_session_code or "미수신"
                self.storage.log(
                    "INFO",
                    "실시간",
                    f"{self.real_time_quote.symbol} 첫 실시간 체결 확인: "
                    f"{self.real_time_quote.current_price:,.0f}원 / 장구분 {session_code}",
                )
                self._first_real_time_quote_logged_symbol = self.real_time_quote.symbol
            existing_watch = self.watchlist_quotes.get(self.real_time_quote.symbol)
            if existing_watch is not None:
                self.watchlist_quotes[self.real_time_quote.symbol] = replace(
                    existing_watch,
                    current_price=self.real_time_quote.current_price,
                    change=self.real_time_quote.change,
                    change_rate=self.real_time_quote.change_rate,
                    timestamp=self.real_time_quote.timestamp,
                )
        return self.real_time_quote

    def unregister_real_time(self) -> str:
        api = self._active_api()
        try:
            self.last_api_message = api.unregister_real_time()
            self.real_time_symbol = ""
            self.real_time_quote = None
            self._real_time_registration_logged = False
            self._first_real_time_quote_logged_symbol = ""
            self.storage.log("INFO", "실시간", self.last_api_message)
        except API_ERRORS as exc:
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "실시간", self.last_api_message)
        return self.last_api_message

    def send_kiwoom_order(
        self,
        account: str,
        side: str,
        quantity: int,
        allow_real_order: bool = False,
        account_password: str = "",
        order_style: str = "MARKET",
        action: str = "NEW",
        original_order_no: str = "",
        use_margin: bool = False,
    ) -> str:
        account = clean_account_number(account)
        self.symbol = normalize_symbol(self.symbol) or self.symbol
        result_side = "BUY" if side == "BUY" else "SELL"
        normalized_action = action.strip().upper()
        if normalized_action not in {"NEW", "MODIFY", "CANCEL"}:
            normalized_action = "NEW"
        normalized_style = order_style.strip().upper()
        api = self._active_api()
        order_price = float(self.current_price)
        hoga = "03"
        order_total = max(0, int(quantity)) * max(0.0, order_price)
        order_symbol_name = self.symbol_name or known_symbol_name(self.symbol)
        order_mode = self.account_info.server_type or ("실거래" if allow_real_order else "모의투자")
        order_no = ""
        self.last_order_account_access_verified = None
        try:
            if not account:
                raise KiwoomOpenApiError("주문할 계좌번호를 입력해 주세요.")
            if not self.symbol:
                raise KiwoomOpenApiError("주문할 종목번호를 입력해 주세요.")
            if normalized_action != "CANCEL" and quantity <= 0:
                raise KiwoomOpenApiError("주문 수량은 1주 이상 선택해 주세요.")
            if self.symbol == "000000":
                raise KiwoomOpenApiError("종목 세팅을 먼저 완료해 주세요.")
            if normalized_action in {"MODIFY", "CANCEL"} and not original_order_no.strip():
                raise KiwoomOpenApiError("정정 또는 취소할 원주문번호를 선택해 주세요.")
            if normalized_action in {"NEW", "MODIFY"} and normalized_style in {"MIDPOINT", "AUTOMATIC", "AUTO"}:
                if normalized_style in {"AUTOMATIC", "AUTO"}:
                    order_price, price_reason = self.automatic_price(result_side, self.symbol)
                    self.storage.log(
                        "INFO",
                        "호가",
                        f"{self.symbol} {result_side} 자동가 {order_price:,.0f}원 ({price_reason})",
                    )
                else:
                    order_price = float(self.midpoint_price(result_side, self.symbol))
                hoga = "00"
                order_total = max(0, int(quantity)) * order_price
            elif normalized_action == "MODIFY":
                raise KiwoomOpenApiError("정정 주문은 최신 자동가 또는 중간가를 다시 조회해 전송합니다.")
            elif normalized_action == "CANCEL":
                order_price = 0.0
                order_total = 0.0

            if normalized_action == "NEW" and result_side == "BUY" and self.daily_trade_locked():
                raise KiwoomOpenApiError(
                    "일일 최대 손실 한도에 도달해 오늘 신규 매수 주문이 잠겨 있습니다."
                )

            if self.account_info.connection_method != "REST API":
                if not is_valid_account_password(account_password):
                    self.last_order_account_access_verified = False
                    raise KiwoomOpenApiError(
                        "자동주문에 사용할 계좌 비밀번호를 숫자 4~8자리로 세팅해 주세요."
                    )
                try:
                    self.balance_summary = api.request_balance(
                        account,
                        password=account_password,
                    )
                except API_ERRORS:
                    self.last_order_account_access_verified = False
                    raise
                self.last_order_account_access_verified = True
                self.storage.log(
                    "INFO",
                    "주문",
                    f"{mask_account_number(account)} 주문 전 계좌 접근과 최신 잔고를 재확인했습니다.",
                )
            else:
                self.balance_summary = api.request_balance(account)
                self.last_order_account_access_verified = True
                self.storage.log(
                    "INFO",
                    "주문",
                    f"{mask_account_number(account)} REST 주문 전 최신 잔고와 주문가능금액을 재확인했습니다.",
                )

            holding_quantity = self._holding_quantity(self.symbol)
            if normalized_action == "NEW" and result_side == "BUY":
                risk_check = self.risk.approve_buy(
                    self.max_capital,
                    order_price,
                    holding_quantity,
                )
                if not risk_check.approved:
                    raise KiwoomOpenApiError(risk_check.reason)
                if quantity > risk_check.quantity:
                    raise KiwoomOpenApiError(
                        f"주문 수량 {quantity}주는 운용 한도를 초과합니다. "
                        f"현재가 기준 최대 {risk_check.quantity}주까지 가능합니다."
                    )
                if self.balance_summary is not None:
                    available_funds = (
                        self.balance_summary.orderable_amount
                        if self.balance_summary.orderable_amount > 0
                        else self.balance_summary.deposit
                    )
                    estimated_cost = quantity * order_price
                    if use_margin:
                        available_funds *= 2.0
                    if estimated_cost > available_funds:
                        raise KiwoomOpenApiError(
                            f"예상 매수금액 {estimated_cost:,.0f}원이 계좌 주문가능금액 "
                            f"{available_funds:,.0f}원을 초과합니다."
                        )
            elif (
                normalized_action == "NEW"
                and self.balance_summary is not None
                and quantity > holding_quantity
            ):
                raise KiwoomOpenApiError(
                    f"매도 수량 {quantity}주가 조회된 보유 수량 {holding_quantity}주를 초과합니다."
                )
            request = KiwoomOrderRequest(
                account=account,
                symbol=self.symbol,
                side=result_side,
                quantity=quantity,
                price=int(order_price),
                hoga=hoga,
                original_order_no=original_order_no.strip(),
                action=normalized_action,
                allow_real_order=allow_real_order,
                require_mock_server=not allow_real_order,
            )
            max_attempts = (
                3
                if normalized_action == "NEW"
                and result_side == "SELL"
                and self.account_info.server_type == "모의투자"
                and not allow_real_order
                else 1
            )
            for attempt in range(1, max_attempts + 1):
                try:
                    self.last_api_message = api.send_order(request)
                    order_no = str(getattr(api, "last_order_no", "") or "").strip()
                    break
                except API_ERRORS as exc:
                    if attempt >= max_attempts:
                        raise
                    self.storage.log(
                        "WARN",
                        "주문",
                        f"모의 매도 요청 실패({attempt}/{max_attempts}): {exc} / 재시도합니다.",
                    )
            self.storage.save_order_result(
                OrderResult(
                    self.symbol,
                    result_side,
                    quantity,
                    order_price,
                    True,
                    self.last_api_message,
                    datetime.now(),
                    symbol_name=order_symbol_name,
                    total_amount=order_total,
                    order_no=order_no,
                    order_mode=order_mode,
                )
            )
            self.storage.log("WARN" if allow_real_order else "INFO", "주문", self.last_api_message)
        except API_ERRORS as exc:
            self.last_api_message = str(exc)
            self.storage.save_order_result(
                OrderResult(
                    self.symbol,
                    result_side,
                    quantity,
                    order_price,
                    False,
                    self.last_api_message,
                    datetime.now(),
                    symbol_name=order_symbol_name,
                    total_amount=order_total,
                    order_no=order_no,
                    order_mode=order_mode,
                )
            )
            self.storage.log("ERROR", "주문", self.last_api_message)
        return self.last_api_message

    def evaluate_and_send_order_with_market_data(
        self,
        account: str,
        quantity: int,
        allow_real_order: bool = False,
        account_password: str = "",
        order_style: str = "MIDPOINT",
        require_trade_value_filter: bool = False,
    ) -> TradeDecision | None:
        if quantity <= 0:
            self.last_api_message = "주문 수량은 1주 이상 선택해 주세요."
            self.storage.log("WARN", "주문", self.last_api_message)
            return None
        decision = self.evaluate_strategy_with_market_data(self.symbol)
        if decision is None:
            return None

        if decision.action == "HOLD":
            self.storage.log("INFO", "자동주문", "전략 판단 결과 대기라 주문하지 않았습니다.")
            return decision

        if require_trade_value_filter:
            minute_value = self.request_latest_minute_trade_value(self.symbol)
            if minute_value is not None and minute_value < MINUTE_TRADE_VALUE_GATE:
                reason = (
                    f"1분봉 거래대금 {minute_value / 100_000_000:,.1f}억원이 기준 "
                    f"{MINUTE_TRADE_VALUE_GATE / 100_000_000:,.0f}억원보다 작아 자동주문을 대기합니다."
                )
                self.last_api_message = reason
                self.storage.log("INFO", "자동주문", reason)
                return replace(decision, action="HOLD", reason=reason)

        if decision.action == "BUY":
            existing_quantity = self._holding_quantity(self.symbol)
            check = self.risk.approve_buy(self.max_capital, self.current_price, existing_quantity)
            if not check.approved:
                self.last_api_message = check.reason
                self.storage.log("WARN", "위험관리", check.reason)
                return decision
            order_quantity = quantity
        else:
            order_quantity = quantity

        self.send_kiwoom_order(
            account=account,
            side=decision.action,
            quantity=order_quantity,
            allow_real_order=allow_real_order,
            account_password=account_password,
            order_style=order_style,
        )
        return decision

    def cancel_all_unfilled_orders(
        self,
        account: str,
        allow_real_order: bool,
        account_password: str = "",
    ) -> list[str]:
        messages: list[str] = []
        orders = self.request_unfilled_orders(account)
        original_symbol = self.symbol
        try:
            for order in orders:
                if order.unfilled_quantity <= 0:
                    continue
                self.symbol = order.symbol
                messages.append(
                    self.send_kiwoom_order(
                        account=account,
                        side=order.side,
                        quantity=order.unfilled_quantity,
                        allow_real_order=allow_real_order,
                        account_password=account_password,
                        action="CANCEL",
                        original_order_no=order.order_no,
                    )
                )
        finally:
            self.symbol = original_symbol
        return messages

    def emergency_liquidate(
        self,
        account: str,
        allow_real_order: bool,
        account_password: str = "",
    ) -> list[str]:
        self.emergency_stop()
        balance = self.request_balance(account, password=account_password)
        if balance is None:
            return [self.last_api_message]
        messages: list[str] = []
        original_symbol = self.symbol
        try:
            for holding in balance.holdings:
                quantity = holding.sellable_quantity or holding.quantity
                if quantity <= 0:
                    continue
                self.symbol = holding.symbol
                self.symbol_name = holding.name
                self.current_price = holding.current_price
                messages.append(
                    self.send_kiwoom_order(
                        account=account,
                        side="SELL",
                        quantity=quantity,
                        allow_real_order=allow_real_order,
                        account_password=account_password,
                        order_style="MARKET",
                    )
                )
        finally:
            self.symbol = original_symbol
        self.storage.log(
            "ERROR",
            "긴급청산",
            f"보유 종목 {len(messages)}개 전량 시장가 매도 요청을 전송했습니다.",
        )
        return messages

    def step(self, price: float | None = None) -> TradeDecision:
        if price is not None and price > 0:
            self.current_price = price
        self._append_mock_candle(self.current_price)

        decision = self.strategy.evaluate(self.candles)
        self.latest_dmi = self.strategy.last_dmi
        self.pattern_state = decision.pattern_state
        self.last_decision = decision
        self.storage.log("INFO", "전략", f"{decision.action}: {decision.reason}")

        if self.running:
            self._execute_decision(decision)
        return decision

    def snapshot(self) -> ServiceSnapshot:
        position = self.broker.get_position(self.symbol)
        account_holding = self._account_holding(self.symbol)
        if self.balance_summary is not None:
            position_quantity = account_holding.quantity if account_holding is not None else 0
            average_price = account_holding.average_price if account_holding is not None else 0.0
        else:
            position_quantity = position.quantity
            average_price = position.average_price
        return ServiceSnapshot(
            connection=self.broker.connection_status(),
            running=self.running,
            symbol=self.symbol,
            symbol_name=self.symbol_name,
            pattern=self.pattern_state,
            price=self.current_price,
            quantity=position_quantity,
            average_price=average_price,
            decision=self.last_decision,
            account_info=self.account_info,
            started_at=self.started_at,
            dmi=self.latest_dmi,
            market_session_status=self.latest_market_session_status(),
            real_time_registered=self.is_real_time_registered(),
            regular_market_open=self.is_regular_market_open(),
            market_quote=self.market_quote,
            balance_summary=self.balance_summary,
            real_time_quote=self.refresh_real_time_quote(),
            chart_candles=self.chart_candles_for_display(),
            chart_timeframe=self.chart_timeframe,
            chart_source=self.chart_source,
            last_api_message=self.last_api_message,
            orders=self.storage.recent_orders(10),
            trade_history=self.storage.recent_trade_history(200),
            account_trade_history=list(self.account_trade_history),
            account_trade_history_message=self.account_trade_history_message,
            account_trade_history_updated_at=self.account_trade_history_updated_at,
            order_book=self.order_book,
            unfilled_orders=list(self.unfilled_orders),
            daily_performance=self.performance_summary("daily"),
            monthly_performance=self.performance_summary("monthly"),
            daily_risk_status=self.daily_risk_status,
            latest_minute_trade_value=self.latest_minute_trade_value,
            logs=self.storage.recent_logs(10),
        )

    @staticmethod
    def _real_time_quote_key(quote: RealTimeQuote) -> tuple:
        return (
            quote.symbol,
            quote.timestamp,
            quote.current_price,
            quote.volume,
        )

    def _execute_decision(self, decision: TradeDecision) -> None:
        position = self.broker.get_position(self.symbol)
        if decision.action == "BUY":
            check = self.risk.approve_buy(self.max_capital, self.current_price, position.quantity)
            if not check.approved:
                self.storage.log("WARN", "위험관리", check.reason)
                return
            self.order_manager.execute_buy(self.symbol, check.quantity, self.current_price)
        elif decision.action == "SELL" and position.quantity > 0:
            self.order_manager.execute_sell(self.symbol, position.quantity, self.current_price)

    def _append_mock_candle(self, price: float) -> None:
        spread = max(price * 0.004, 1.0)
        high = price + uniform(0, spread)
        low = max(1.0, price - uniform(0, spread))
        close = price
        self.candles.append(
            Candle(
                high=high,
                low=low,
                close=close,
                open=self.candles[-1].close if self.candles else close,
                timestamp=datetime.now().strftime("%Y%m%d%H%M%S"),
            )
        )
        if len(self.candles) > 200:
            self.candles = self.candles[-200:]

    def _account_holding(self, symbol: str) -> Holding | None:
        target = normalize_symbol(symbol)
        if self.balance_summary is not None:
            for holding in self.balance_summary.holdings:
                if normalize_symbol(holding.symbol) == target:
                    return holding
        return None

    def _holding_quantity(self, symbol: str) -> int:
        holding = self._account_holding(symbol)
        if holding is not None:
            return holding.quantity
        if self.balance_summary is not None:
            return 0
        target = normalize_symbol(symbol)
        return self.broker.get_position(target).quantity
