from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from random import uniform

from .broker import BrokerClient, MockBroker
from .charting import (
    SUPPORTED_MINUTE_INTERVALS,
    SUPPORTED_SECOND_INTERVALS,
    RealTimeCandleAggregator,
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
    KiwoomOrderRequest,
    MarketQuote,
    MarketSessionStatus,
    OrderResult,
    PatternState,
    RealTimeQuote,
    StrategySettings,
    TradeDecision,
    WatchlistQuote,
)
from .order_manager import OrderManager
from .rest_api import KiwoomRestApiClient, KiwoomRestApiError
from .risk import RiskManager
from .storage import Storage
from .strategy import StrategyEngine
from .symbols import clean_account_number, known_symbol_name, mask_account_number, normalize_symbol


API_ERRORS = (KiwoomOpenApiError, KiwoomRestApiError)


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
        self.total_capital_limit = 1_000_000.0
        self.current_price = 0.0
        self.pattern_state: PatternState = "NONE"
        self.candles: list[Candle] = []
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
        self.real_time_symbol = ""
        self.profile_real_time_symbols: tuple[str, ...] = ()
        self.real_time_quote: RealTimeQuote | None = None
        self._real_time_registration_logged = False
        self._first_real_time_quote_logged_symbol = ""
        self.watchlist_quotes: dict[str, WatchlistQuote] = {}
        self._watch_engines: dict[str, StrategyEngine] = {}
        self._watch_daily_history: dict[str, tuple[str, list[Candle]]] = {}
        self._watch_minute_history: dict[str, tuple[int, list[Candle]]] = {}
        self._watch_realtime_keys: dict[str, tuple[str, float, int]] = {}
        self._auto_order_keys: set[tuple[str, str, str]] = set()
        self.last_api_message = ""
        self.last_order_success = False
        self.last_order_account_access_verified: bool | None = None
        self.started_at: datetime | None = None

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
        self.total_capital_limit = max(0.0, float(max_capital))
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
        self.real_time_symbol = ""
        self.profile_real_time_symbols = ()
        self.real_time_quote = None
        self._real_time_registration_logged = False
        self._first_real_time_quote_logged_symbol = ""
        self.watchlist_quotes = {}
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
        return bool((self.real_time_symbol or self.profile_real_time_symbols) and self.account_info.connected)

    def register_profile_realtime(self, symbols: tuple[str, ...]) -> str:
        normalized = tuple(dict.fromkeys(normalize_symbol(item) for item in symbols))
        normalized = tuple(item for item in normalized if item and item != "000000")
        if not normalized:
            raise ValueError("실시간 등록할 설정 종목이 없습니다.")
        api = self._active_api()
        try:
            if self.connection_mode == "ACTIVEX":
                self.last_api_message = api.register_real_time_prices(list(normalized))
            else:
                self.last_api_message = api.register_real_time_prices(list(normalized))
            self.profile_real_time_symbols = normalized
            self.storage.log("INFO", "다중 실시간", self.last_api_message)
        except API_ERRORS as exc:
            self.profile_real_time_symbols = ()
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "다중 실시간", self.last_api_message)
        return self.last_api_message

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
                    volume=self.market_quote.volume,
                    trade_value=self.market_quote.trade_value,
                    open_price=self.market_quote.open_price,
                    high_price=self.market_quote.high_price,
                    low_price=self.market_quote.low_price,
                    timestamp=self.market_quote.timestamp,
                )
            self.last_api_message = self.market_quote.message
            self.storage.log("INFO", "시세", f"{target} 현재가 {self.current_price:,.0f}")
            return self.market_quote
        except API_ERRORS as exc:
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "시세", self.last_api_message)
            return None

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

    def add_watchlist_symbols(self, symbols: list[str]) -> tuple[int, int]:
        existing = {symbol for symbol, _name in self.watchlist_items()}
        added = 0
        skipped = 0
        for symbol in symbols:
            normalized = normalize_symbol(symbol)
            if not normalized or normalized in existing or len(existing) >= 20:
                skipped += 1
                continue
            if self.add_watchlist_symbol(normalized):
                existing.add(normalized)
                added += 1
            else:
                skipped += 1
        self.last_api_message = f"관심종목 {added}개 등록, {skipped}개 건너뜀 (최대 20개)"
        return added, skipped

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

    def list_market_symbols(self) -> dict[str, list[str]]:
        if self.connection_mode != "ACTIVEX" or not self.account_info.connected:
            raise KiwoomOpenApiError("전 종목 목록은 키움 OpenAPI+ 로그인 후 조회할 수 있습니다.")
        result = {
            market: self.kiwoom_api.list_market_symbols(market)
            for market in ("KOSPI", "KOSDAQ")
        }
        self.last_api_message = f"시장별 종목 {sum(map(len, result.values())):,}개 조회 완료"
        self.storage.log("INFO", "종목", self.last_api_message)
        return result

    def list_saved_conditions(self) -> list[tuple[int, str]]:
        if self.connection_mode != "ACTIVEX" or not self.account_info.connected:
            raise KiwoomOpenApiError("조건검색은 키움 OpenAPI+ 로그인 후 사용할 수 있습니다.")
        return self.kiwoom_api.list_saved_conditions()

    def search_condition(self, index: int, name: str) -> list[str]:
        if self.connection_mode != "ACTIVEX" or not self.account_info.connected:
            raise KiwoomOpenApiError("조건검색은 키움 OpenAPI+ 로그인 후 사용할 수 있습니다.")
        symbols = self.kiwoom_api.search_condition(index, name)
        self.last_api_message = f"{name} 조건검색 {len(symbols)}종목 조회 완료"
        self.storage.log("INFO", "조건검색", self.last_api_message)
        return symbols

    def watchlist_rows(self) -> list[WatchlistQuote]:
        return [
            self.watchlist_quotes.get(symbol, WatchlistQuote(symbol=symbol, name=name))
            for symbol, name in self.watchlist_items()
        ]

    def evaluate_watch_symbols(
        self, settings_by_symbol: dict[str, StrategySettings]
    ) -> dict[str, TradeDecision | None]:
        """Evaluate configured symbols independently without submitting orders."""
        results: dict[str, TradeDecision | None] = {}
        api = self._active_api()
        holdings = {
            normalize_symbol(item.symbol): item.average_price
            for item in self.balance_summary.holdings
        } if self.balance_summary else {}
        for raw_symbol, settings in settings_by_symbol.items():
            symbol = normalize_symbol(raw_symbol)
            if not symbol or symbol == "000000":
                continue
            engine = self._watch_engines.get(symbol)
            if engine is None or engine.settings != settings:
                engine = StrategyEngine(settings)
                self._watch_engines[symbol] = engine
            profile = settings.profile or {}
            try:
                interval = int(profile.get("pdf", {}).get("minuteInterval", 1)) if profile.get("pdf", {}).get("enabled") else 3
                candles = api.request_minute_candles(symbol, interval=interval, count=120)
                daily_candles = None
                needs_daily = profile and (
                    profile.get("pdf", {}).get("enabled")
                    or
                    profile.get("bar", {}).get("combine", "BOTH") in {"BOTH", "DAILY_ONLY"}
                    or profile.get("buyBlock", {}).get("X02_overExtended", {}).get("enabled")
                )
                if needs_daily:
                    today = datetime.now().strftime("%Y%m%d")
                    cached = self._watch_daily_history.get(symbol)
                    if cached is None or cached[0] != today:
                        try:
                            history = api.request_daily_candles(symbol, count=61)
                        except API_ERRORS:
                            if not profile.get("pdf", {}).get("enabled") or holdings.get(symbol, 0) <= 0:
                                raise
                            history = []
                        self._watch_daily_history[symbol] = (today, history)
                    daily_candles = self._watch_daily_history[symbol][1]
            except API_ERRORS as exc:
                engine.last_pdf_state = None
                self.storage.log("ERROR", "다중 감시", f"{symbol} 차트 조회 실패: {exc}")
                results[symbol] = None
                continue
            required = 1 if profile.get("pdf", {}).get("enabled") else settings.dmi_period + 1
            ordered = list(reversed(candles))
            self._watch_minute_history[symbol] = (interval, ordered)
            if len(candles) < required:
                engine.last_pdf_state = None
            results[symbol] = (
                engine.evaluate(
                    ordered, daily_candles,
                    entry_price=holdings.get(symbol, 0.0),
                    day_open=self.watchlist_quotes.get(symbol).open_price if symbol in self.watchlist_quotes else 0.0,
                    day_low=self.watchlist_quotes.get(symbol).low_price if symbol in self.watchlist_quotes else 0.0,
                )
                if len(candles) >= required else None
            )
        return results

    def request_monitor_candles(self, symbol: str, interval: int = 1) -> list[Candle]:
        target = normalize_symbol(symbol)
        if not target or interval not in {1, 3}:
            return []
        try:
            return list(reversed(self._active_api().request_minute_candles(
                target, interval=interval, count=120
            )))
        except API_ERRORS as exc:
            self.storage.log("ERROR", "모니터 차트", f"{target}: {exc}")
            return []

    def request_monitor_daily_averages(
        self, symbol: str, intraday: list[Candle], periods: tuple[int, ...]
    ) -> dict[int, float]:
        target = normalize_symbol(symbol)
        if not target or not intraday or not periods or any(not 2 <= item <= 60 for item in periods):
            return {}
        try:
            history = self._active_api().request_daily_candles(target, count=61)
        except API_ERRORS as exc:
            self.storage.log("ERROR", "모니터 차트", f"{target} 일봉 조회 실패: {exc}")
            return {}
        closes = StrategyEngine.daily_closes_with_intraday(history, intraday)
        return {
            period: sum(closes[-period:]) / period
            for period in periods if len(closes) >= period
        }

    def order_candidates(
        self,
        decisions: dict[str, TradeDecision | None],
        settings_by_symbol: dict[str, StrategySettings],
    ) -> dict[str, str]:
        """Return eligible signal sides; never submit an order."""
        if not self.balance_summary:
            return {}
        holdings = {normalize_symbol(item.symbol): item.quantity for item in self.balance_summary.holdings}
        candidates: dict[str, str] = {}
        for raw_symbol, decision in decisions.items():
            symbol = normalize_symbol(raw_symbol)
            settings = settings_by_symbol.get(symbol)
            if not symbol or decision is None or settings is None:
                continue
            profile = settings.profile or {}
            held = holdings.get(symbol, 0)
            if decision.action == "BUY" and held <= 0 and profile.get("buyEnabled", True):
                candidates[symbol] = "BUY"
            elif decision.action == "SELL" and held > 0 and profile.get("sellEnabled", True):
                candidates[symbol] = "SELL"
        return candidates

    def pdf_indicator_state(self, symbol: str) -> tuple[bool, bool, bool] | None:
        engine = self._watch_engines.get(normalize_symbol(symbol))
        return engine.last_pdf_state if engine is not None else None

    def plan_candidate_quantities(self, candidates: dict[str, str]) -> dict[str, int]:
        """Size candidate orders against current account data without submitting them."""
        balance = self.balance_summary
        if balance is None:
            return {}
        holdings = {normalize_symbol(item.symbol): item.quantity for item in balance.holdings}
        buys = [symbol for symbol, side in candidates.items() if side == "BUY"]
        available = max(0.0, min(
            balance.orderable_amount,
            self.total_capital_limit - max(0.0, balance.total_evaluation),
        ))
        per_buy_budget = available / len(buys) if buys else 0.0
        plan: dict[str, int] = {}
        for symbol, side in candidates.items():
            if side == "SELL" and holdings.get(symbol, 0) > 0:
                plan[symbol] = holdings[symbol]
            elif side == "BUY":
                quote = self.watchlist_quotes.get(symbol)
                price = quote.current_price if quote else 0.0
                quantity = self.risk.calculate_quantity(per_buy_budget, price)
                if quantity > 0:
                    plan[symbol] = quantity
        return plan

    def execute_profile_orders(
        self,
        settings_by_symbol: dict[str, StrategySettings],
        account: str,
        account_password: str = "",
        allow_real_order: bool = False,
        regular_market_open: bool = False,
    ) -> dict[str, str]:
        """Run one guarded scan of explicitly enabled per-symbol PDF strategies."""
        if not self.running or not self.account_info.connected:
            return {}
        real_account = self.account_info.server_type == "실거래"
        if real_account and (not allow_real_order or not regular_market_open):
            self.storage.log("WARN", "다중 자동주문", "실거래 승인 또는 정규장 확인이 없어 주문을 차단했습니다.")
            return {}
        if self.account_info.server_type not in {"실거래", "모의투자"}:
            return {}
        active = {
            normalize_symbol(symbol): settings
            for symbol, settings in settings_by_symbol.items()
            if normalize_symbol(symbol) not in {"", "000000"}
            and (settings.profile or {}).get("pdf", {}).get("enabled")
        }
        if not active or self.request_balance(account, password=account_password) is None:
            return {}
        self.refresh_watchlist_quotes(tuple(active))
        fresh_quotes = {
            symbol: quote for symbol, quote in self.watchlist_quotes.items()
            if symbol in active and quote.current_price > 0
        }
        decisions = self.evaluate_watch_symbols(active)
        return self._submit_profile_decisions(
            active, decisions, fresh_quotes, account, account_password, allow_real_order
        )

    def execute_profile_tick_orders(
        self,
        settings_by_symbol: dict[str, StrategySettings],
        account: str,
        account_password: str = "",
        allow_real_order: bool = False,
        regular_market_open: bool = False,
    ) -> dict[str, str]:
        """Evaluate new live trades against the last verified minute/daily snapshot."""
        if not self.running or not self.account_info.connected or self.balance_summary is None:
            return {}
        if self.account_info.server_type == "실거래" and (not allow_real_order or not regular_market_open):
            return {}
        if self.account_info.server_type not in {"실거래", "모의투자"}:
            return {}
        api = self._active_api()
        today = datetime.now().strftime("%Y%m%d")
        active: dict[str, StrategySettings] = {}
        decisions: dict[str, TradeDecision | None] = {}
        fresh_quotes: dict[str, WatchlistQuote] = {}
        holdings = {
            normalize_symbol(item.symbol): item.average_price
            for item in self.balance_summary.holdings
        }
        for raw_symbol, settings in settings_by_symbol.items():
            symbol = normalize_symbol(raw_symbol)
            profile = settings.profile or {}
            history = self._watch_minute_history.get(symbol)
            daily = self._watch_daily_history.get(symbol)
            quote_snapshot = self.watchlist_quotes.get(symbol)
            if (not profile.get("pdf", {}).get("enabled") or history is None
                    or daily is None or daily[0] != today or quote_snapshot is None):
                continue
            try:
                live = api.latest_real_time_quote(symbol)
            except API_ERRORS:
                continue
            if live is None or live.current_price <= 0:
                continue
            traded_at = RealTimeCandleAggregator._quote_datetime(live.timestamp, None)
            if traded_at.strftime("%Y%m%d") != today:
                continue
            key = (live.timestamp, live.current_price, live.volume)
            if self._watch_realtime_keys.get(symbol) == key:
                continue
            interval, bars = history
            if interval != int(profile["pdf"].get("minuteInterval", 1)) or not bars:
                continue
            minute = (traded_at.minute // interval) * interval
            bucket = traded_at.replace(minute=minute, second=0, microsecond=0).strftime("%Y%m%d%H%M%S")
            previous_digits = "".join(char for char in bars[-1].timestamp if char.isdigit())
            if len(previous_digits) < 12:
                continue
            previous_bucket = previous_digits[:12] + "00"
            if bucket < previous_bucket:
                continue
            if bucket == previous_bucket:
                last = bars[-1]
                bars[-1] = Candle(
                    open=last.open, high=max(last.high, live.current_price),
                    low=min(last.low, live.current_price), close=live.current_price,
                    volume=last.volume + abs(live.volume), timestamp=bucket,
                )
            else:
                bars.append(Candle(
                    open=live.current_price, high=live.current_price,
                    low=live.current_price, close=live.current_price,
                    volume=abs(live.volume), timestamp=bucket,
                ))
                del bars[:-120]
            self._watch_realtime_keys[symbol] = key
            fresh = replace(
                quote_snapshot, current_price=live.current_price,
                change=live.change if live.change else quote_snapshot.change,
                change_rate=live.change_rate if live.change_rate else quote_snapshot.change_rate,
                high_price=max(quote_snapshot.high_price, live.current_price),
                low_price=min(quote_snapshot.low_price, live.current_price)
                if quote_snapshot.low_price > 0 else live.current_price,
                timestamp=live.timestamp,
            )
            self.watchlist_quotes[symbol] = fresh
            engine = self._watch_engines.get(symbol)
            if engine is None or engine.settings != settings:
                engine = StrategyEngine(settings)
                self._watch_engines[symbol] = engine
            decisions[symbol] = engine.evaluate(
                bars, daily[1], entry_price=holdings.get(symbol, 0.0),
                day_open=fresh.open_price, day_low=fresh.low_price,
            )
            active[symbol] = settings
            fresh_quotes[symbol] = fresh
        return self._submit_profile_decisions(
            active, decisions, fresh_quotes, account, account_password, allow_real_order
        )

    def _submit_profile_decisions(
        self,
        active: dict[str, StrategySettings],
        decisions: dict[str, TradeDecision | None],
        fresh_quotes: dict[str, WatchlistQuote],
        account: str,
        account_password: str,
        allow_real_order: bool,
    ) -> dict[str, str]:
        candidates = self.order_candidates(decisions, active)
        plan = self.plan_candidate_quantities(candidates)
        results: dict[str, str] = {}
        today = datetime.now().strftime("%Y%m%d")
        for symbol, side in candidates.items():
            if not self.running:
                break
            quantity = plan.get(symbol, 0)
            quote = fresh_quotes.get(symbol)
            decision = decisions.get(symbol)
            key = (symbol, side, today)
            if (not quantity or quote is None or quote.current_price <= 0
                    or key in self._auto_order_keys
                    or self.storage.successful_order_on_day(symbol, side, datetime.now().date().isoformat())):
                continue
            if decision is None or not decision.candle_timestamp.startswith(today):
                self.storage.log("WARN", "다중 자동주문", f"{symbol} 오늘 분봉이 없어 주문하지 않았습니다.")
                continue
            results[symbol] = self.send_kiwoom_order(
                account=account,
                side=side,
                quantity=quantity,
                allow_real_order=allow_real_order,
                account_password=account_password,
                symbol=symbol,
                reference_price=quote.current_price,
                symbol_name=quote.name,
            )
            if self.last_order_success:
                self._auto_order_keys.add(key)
        return results

    def refresh_watchlist_quotes(self, extra_symbols: tuple[str, ...] = ()) -> list[WatchlistQuote]:
        items = self.watchlist_items()
        watchlist_symbols = {symbol for symbol, _name in items}
        symbols = [symbol for symbol, _name in items]
        symbols.extend(
            symbol for symbol in (normalize_symbol(raw) for raw in extra_symbols)
            if symbol and symbol not in watchlist_symbols
        )
        symbols = list(dict.fromkeys(symbols))
        if not symbols:
            self.watchlist_quotes = {}
            self.last_api_message = "등록된 관심종목이 없습니다."
            return []
        api = self._active_api()
        try:
            quotes = api.request_watchlist_quotes(symbols)
            self.watchlist_quotes = {quote.symbol: quote for quote in quotes if quote.symbol}
            for quote in quotes:
                if quote.symbol in watchlist_symbols and quote.name:
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
            self.watchlist_quotes = {}
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "관심종목", self.last_api_message)
            return self.watchlist_rows()

    def request_three_minute_candles(self, symbol: str | None = None) -> list[Candle]:
        target = normalize_symbol(symbol or self.symbol)
        api = self._active_api()
        try:
            self.candles = api.request_minute_candles(target, interval=3, count=120)
            self.symbol = target
            self.latest_dmi = None
            self.pattern_state = "NONE"
            if self.candles:
                self.current_price = self.candles[0].close
                ordered_candles = list(reversed(self.candles))
                self.latest_dmi = self.strategy.latest_dmi(ordered_candles)
                if self.latest_dmi is not None:
                    self.pattern_state = self.latest_dmi.pattern_state
            if self.chart_timeframe == "3m":
                self.chart_candles = list(self.candles)
                self.chart_source = "키움 ka10080/opt10080"
            self.last_api_message = f"{target} 3분봉 {len(self.candles)}개 조회 완료"
            self.storage.log("INFO", "시세", self.last_api_message)
            return self.candles
        except API_ERRORS as exc:
            self.latest_dmi = None
            self.pattern_state = "NONE"
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "시세", self.last_api_message)
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
            self.chart_candles = api.request_minute_candles(
                target,
                interval=interval_minutes,
                count=200,
            )
            self.symbol = target
            self.chart_source = "키움 ka10080/opt10080"
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
        profile = self.strategy.settings.profile or {}
        pdf_mode = bool(profile.get("pdf", {}).get("enabled"))
        if pdf_mode:
            fresh_quote = self.request_current_price(symbol)
            if fresh_quote is None or fresh_quote.current_price <= 0:
                return None
            interval = int(profile["pdf"].get("minuteInterval", 1))
            try:
                candles = self._active_api().request_minute_candles(
                    normalize_symbol(symbol or self.symbol), interval=interval, count=120
                )
            except API_ERRORS as exc:
                self.last_api_message = f"{interval}분봉 조회 실패: {exc}"
                self.storage.log("ERROR", "전략", self.last_api_message)
                return None
        else:
            interval = 3
            candles = self.request_three_minute_candles(symbol)
        required = 1 if pdf_mode else self.strategy.settings.dmi_period + 1
        if len(candles) < required:
            self.last_api_message = f"전략 판단에 필요한 {interval}분봉이 부족합니다({len(candles)}/{required})."
            self.storage.log("WARN", "전략", self.last_api_message)
            return None
        ordered_candles = list(reversed(candles))
        daily_candles = None
        entry_price = 0.0
        if pdf_mode:
            try:
                daily_candles = self._active_api().request_daily_candles(
                    normalize_symbol(symbol or self.symbol), count=61
                )
            except API_ERRORS as exc:
                if not self.balance_summary or not any(
                    normalize_symbol(item.symbol) == normalize_symbol(symbol or self.symbol)
                    and item.quantity > 0 for item in self.balance_summary.holdings
                ):
                    self.last_api_message = f"전일 종가 조회 실패: {exc}"
                    self.storage.log("ERROR", "전략", self.last_api_message)
                    return None
                daily_candles = []
                self.storage.log("WARN", "전략", "일봉 조회 실패: 보유 종목의 손절/익절만 판단합니다.")
            if self.balance_summary:
                entry_price = next((
                    item.average_price for item in self.balance_summary.holdings
                    if normalize_symbol(item.symbol) == normalize_symbol(symbol or self.symbol)
                ), 0.0)
        quote = self.market_quote if self.market_quote and normalize_symbol(self.market_quote.symbol) == normalize_symbol(symbol or self.symbol) else None
        decision = self.strategy.evaluate(
            ordered_candles, daily_candles, entry_price,
            day_open=quote.open_price if quote else 0.0,
            day_low=quote.low_price if quote else 0.0,
        )
        self.latest_dmi = self.strategy.last_dmi
        self.pattern_state = decision.pattern_state
        self.last_decision = decision
        adx_text = "계산 중" if decision.adx is None else f"{decision.adx:.2f}"
        self.last_api_message = (
            f"{interval}분봉 기획서 전략 판단: {decision.action} / {decision.reason}" if pdf_mode else
            f"3분봉 DMI({self.strategy.settings.dmi_period}) 전략 판단: {decision.action} / "
            f"+DI {decision.dmi_plus or 0.0:.2f}, -DI {decision.dmi_minus or 0.0:.2f}, "
            f"ADX {adx_text} / {decision.reason}"
        )
        self.storage.log("INFO", "전략", self.last_api_message)
        return decision

    def request_balance(self, account: str, password: str = "") -> BalanceSummary | None:
        account = clean_account_number(account)
        if not account and self.account_info.accounts:
            account = clean_account_number(self.account_info.accounts[0])
        api = self._active_api()
        try:
            self.balance_summary = api.request_balance(account, password=password)
            self.last_api_message = self.balance_summary.message
            self.storage.log(
                "INFO",
                "잔고",
                f"{mask_account_number(account)} 보유종목 {len(self.balance_summary.holdings)}개 조회 완료",
            )
            return self.balance_summary
        except API_ERRORS as exc:
            self.balance_summary = None
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "잔고", self.last_api_message)
            return None

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
            self.profile_real_time_symbols = ()
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
        symbol: str | None = None,
        reference_price: float | None = None,
        symbol_name: str | None = None,
    ) -> str:
        account = clean_account_number(account)
        target_symbol = normalize_symbol(symbol if symbol is not None else self.symbol)
        result_side = str(side).upper()
        if result_side not in {"BUY", "SELL"}:
            raise ValueError("주문 방향은 BUY 또는 SELL이어야 합니다.")
        api = self._active_api()
        order_price = float(self.current_price if reference_price is None else reference_price)
        order_total = max(0, int(quantity)) * max(0.0, order_price)
        order_symbol_name = (
            symbol_name or known_symbol_name(target_symbol)
            or (self.symbol_name if target_symbol == normalize_symbol(self.symbol) else "")
        )
        order_mode = self.account_info.server_type or ("실거래" if allow_real_order else "모의투자")
        order_no = ""
        self.last_order_success = False
        self.last_order_account_access_verified = None
        try:
            if allow_real_order and (
                not self.account_info.connected or self.account_info.server_type != "실거래"
            ):
                raise KiwoomOpenApiError("실거래 계좌 연결 확인 전에는 실제 주문을 보낼 수 없습니다.")
            if not account:
                raise KiwoomOpenApiError("주문할 계좌번호를 입력해 주세요.")
            if not target_symbol:
                raise KiwoomOpenApiError("주문할 종목번호를 입력해 주세요.")
            if quantity <= 0:
                raise KiwoomOpenApiError("주문 수량은 1주 이상 선택해 주세요.")
            if target_symbol == "000000":
                raise KiwoomOpenApiError("종목 세팅을 먼저 완료해 주세요.")

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

            holding_quantity = self._holding_quantity(target_symbol)
            if result_side == "BUY":
                if self.balance_summary is not None and self.total_capital_limit > 0:
                    current_evaluation = max(0.0, float(self.balance_summary.total_evaluation))
                    estimated_cost = quantity * order_price
                    if current_evaluation + estimated_cost > self.total_capital_limit:
                        raise KiwoomOpenApiError(
                            f"계좌 총 운용 한도 {self.total_capital_limit:,.0f}원을 초과합니다. "
                            f"현재 평가금액 {current_evaluation:,.0f}원, "
                            f"예상 매수금액 {estimated_cost:,.0f}원입니다."
                        )
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
                    if estimated_cost > available_funds:
                        raise KiwoomOpenApiError(
                            f"예상 매수금액 {estimated_cost:,.0f}원이 계좌 주문가능금액 "
                            f"{available_funds:,.0f}원을 초과합니다."
                        )
            elif self.balance_summary is not None and quantity > holding_quantity:
                raise KiwoomOpenApiError(
                    f"매도 수량 {quantity}주가 조회된 보유 수량 {holding_quantity}주를 초과합니다."
                )
            request = KiwoomOrderRequest(
                account=account,
                symbol=target_symbol,
                side=result_side,
                quantity=quantity,
                price=0,
                hoga="03",
                allow_real_order=allow_real_order,
                require_mock_server=not allow_real_order,
            )
            max_attempts = (
                3
                if result_side == "SELL"
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
                    target_symbol,
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
            self.last_order_success = True
            self.storage.log("WARN" if allow_real_order else "INFO", "주문", self.last_api_message)
        except API_ERRORS as exc:
            self.last_api_message = str(exc)
            self.storage.save_order_result(
                OrderResult(
                    target_symbol,
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
        auto: bool = False,
    ) -> TradeDecision | None:
        if quantity <= 0:
            self.last_api_message = "주문 수량은 1주 이상 선택해 주세요."
            self.storage.log("WARN", "주문", self.last_api_message)
            return None
        if (self.strategy.settings.profile or {}).get("pdf", {}).get("enabled"):
            if self.request_balance(account, password=account_password) is None:
                return None
        decision = self.evaluate_strategy_with_market_data(self.symbol)
        if decision is None:
            return None

        if decision.action == "HOLD":
            self.storage.log("INFO", "자동주문", "전략 판단 결과 대기라 주문하지 않았습니다.")
            return decision

        if auto and self.storage.successful_order_on_day(
            normalize_symbol(self.symbol), decision.action, datetime.now().date().isoformat()
        ):
            self.storage.log("INFO", "자동주문", "오늘 접수된 같은 종목·방향 주문이 있어 중복 주문을 막았습니다.")
            return decision

        if decision.action == "BUY":
            existing_quantity = self._holding_quantity(self.symbol)
            check = self.risk.approve_buy(self.max_capital, self.current_price, existing_quantity)
            if not check.approved:
                self.last_api_message = check.reason
                self.storage.log("WARN", "위험관리", check.reason)
                return decision
            order_quantity = quantity
        else:
            order_quantity = (
                self._holding_quantity(self.symbol)
                if (self.strategy.settings.profile or {}).get("pdf", {}).get("enabled")
                else quantity
            )
            if order_quantity <= 0:
                self.storage.log("WARN", "자동주문", "보유 수량을 확인할 수 없어 매도하지 않았습니다.")
                return decision

        self.send_kiwoom_order(
            account=account,
            side=decision.action,
            quantity=order_quantity,
            allow_real_order=allow_real_order,
            account_password=account_password,
        )
        return decision

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
        return ServiceSnapshot(
            connection=self.broker.connection_status(),
            running=self.running,
            symbol=self.symbol,
            symbol_name=self.symbol_name,
            pattern=self.pattern_state,
            price=self.current_price,
            quantity=position.quantity,
            average_price=position.average_price,
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

    def _holding_quantity(self, symbol: str) -> int:
        target = normalize_symbol(symbol)
        if self.balance_summary:
            for holding in self.balance_summary.holdings:
                if normalize_symbol(holding.symbol) == target:
                    return holding.quantity
        return self.broker.get_position(target).quantity
