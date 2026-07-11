from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from random import uniform

from .broker import BrokerClient, MockBroker
from .kiwoom_api import KiwoomAccountInfo, KiwoomOpenApiClient, KiwoomOpenApiError
from .models import (
    BalanceSummary,
    Candle,
    KiwoomOrderRequest,
    MarketQuote,
    OrderResult,
    PatternState,
    RealTimeQuote,
    StrategySettings,
    TradeDecision,
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
    market_quote: MarketQuote | None = None
    balance_summary: BalanceSummary | None = None
    real_time_quote: RealTimeQuote | None = None
    last_api_message: str = ""
    orders: list[tuple] = field(default_factory=list)
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
        self.symbol = "005930"
        self.symbol_name = known_symbol_name(self.symbol)
        self.max_capital = 1_000_000.0
        self.current_price = 72_000.0
        self.pattern_state: PatternState = "NONE"
        self.candles: list[Candle] = []
        self.last_decision: TradeDecision | None = None
        self.market_quote: MarketQuote | None = None
        self.balance_summary: BalanceSummary | None = None
        self.real_time_symbol = ""
        self.real_time_quote: RealTimeQuote | None = None
        self.last_api_message = ""

    def configure(
        self,
        symbol: str,
        max_capital: float,
        settings: StrategySettings,
    ) -> None:
        previous_symbol = self.symbol
        next_symbol = normalize_symbol(symbol) or "005930"
        if next_symbol != previous_symbol or self.strategy.settings != settings:
            self.strategy = StrategyEngine(settings)
        self.symbol = next_symbol
        fallback_name = known_symbol_name(self.symbol)
        if fallback_name:
            self.symbol_name = fallback_name
        self.max_capital = max_capital
        self.storage.log("INFO", "설정", f"{self.symbol} 설정을 저장했습니다.")

    def start(self) -> None:
        self.order_manager.resume()
        self.running = True
        self.storage.log("INFO", "시스템", "자동 운용을 시작했습니다.")

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
        self.real_time_quote = None
        self.candles = []
        self.last_decision = None

    def account_login_status(self) -> str:
        try:
            return self._active_api().login_status_message()
        except API_ERRORS as exc:
            return str(exc)

    def _active_api(self):
        return self.rest_api if self.connection_mode == "REST" else self.kiwoom_api

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
            self.last_api_message = self.market_quote.message
            self.storage.log("INFO", "시세", f"{target} 현재가 {self.current_price:,.0f}")
            return self.market_quote
        except API_ERRORS as exc:
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "시세", self.last_api_message)
            return None

    def request_three_minute_candles(self, symbol: str | None = None) -> list[Candle]:
        target = normalize_symbol(symbol or self.symbol)
        api = self._active_api()
        try:
            self.candles = api.request_minute_candles(target, interval=3, count=120)
            self.symbol = target
            if self.candles:
                self.current_price = self.candles[0].close
            self.last_api_message = f"{target} 3분봉 {len(self.candles)}개 조회 완료"
            self.storage.log("INFO", "시세", self.last_api_message)
            return self.candles
        except API_ERRORS as exc:
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "시세", self.last_api_message)
            return []

    def evaluate_strategy_with_market_data(
        self,
        symbol: str | None = None,
        pattern_state: PatternState | None = None,
    ) -> TradeDecision | None:
        candles = self.request_three_minute_candles(symbol)
        if len(candles) < 2:
            self.last_api_message = "전략 판단에 필요한 3분봉 데이터가 부족합니다."
            self.storage.log("WARN", "전략", self.last_api_message)
            return None
        pattern = pattern_state or self.pattern_state
        ordered_candles = list(reversed(candles))
        decision = self.strategy.evaluate(ordered_candles, pattern)
        self.pattern_state = pattern
        self.last_decision = decision
        self.last_api_message = (
            f"강세/약세 입력과 실제 3분봉 CCI 기반 전략 판단: "
            f"{decision.action} / {decision.reason}"
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
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "잔고", self.last_api_message)
            return None

    def register_real_time_price(self, symbol: str | None = None) -> str:
        target = normalize_symbol(symbol or self.symbol)
        api = self._active_api()
        try:
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
            self.real_time_quote = api.latest_real_time_quote(self.real_time_symbol)
        except API_ERRORS as exc:
            message = str(exc)
            if message != self.last_api_message:
                self.storage.log("ERROR", "실시간", message)
            self.last_api_message = message
            return None
        if self.real_time_quote and self.real_time_quote.current_price > 0:
            self.current_price = self.real_time_quote.current_price
        return self.real_time_quote

    def unregister_real_time(self) -> str:
        api = self._active_api()
        try:
            self.last_api_message = api.unregister_real_time()
            self.real_time_symbol = ""
            self.real_time_quote = None
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
    ) -> str:
        account = clean_account_number(account)
        self.symbol = normalize_symbol(self.symbol) or self.symbol
        result_side = "BUY" if side == "BUY" else "SELL"
        api = self._active_api()
        try:
            if not account:
                raise KiwoomOpenApiError("주문할 계좌번호를 입력해 주세요.")
            if not self.symbol:
                raise KiwoomOpenApiError("주문할 종목번호를 입력해 주세요.")
            holding_quantity = self._holding_quantity(self.symbol)
            if result_side == "BUY":
                risk_check = self.risk.approve_buy(
                    self.max_capital,
                    self.current_price,
                    holding_quantity,
                )
                if not risk_check.approved:
                    raise KiwoomOpenApiError(risk_check.reason)
                if quantity > risk_check.quantity:
                    raise KiwoomOpenApiError(
                        f"주문 수량 {quantity}주는 운용 한도를 초과합니다. "
                        f"현재가 기준 최대 {risk_check.quantity}주까지 가능합니다."
                    )
            elif self.balance_summary is not None and quantity > holding_quantity:
                raise KiwoomOpenApiError(
                    f"매도 수량 {quantity}주가 조회된 보유 수량 {holding_quantity}주를 초과합니다."
                )
            request = KiwoomOrderRequest(
                account=account,
                symbol=self.symbol,
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
                    self.current_price,
                    True,
                    self.last_api_message,
                    datetime.now(),
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
                    self.current_price,
                    False,
                    self.last_api_message,
                    datetime.now(),
                )
            )
            self.storage.log("ERROR", "주문", self.last_api_message)
        return self.last_api_message

    def evaluate_and_send_order_with_market_data(
        self,
        account: str,
        quantity: int,
        allow_real_order: bool = False,
        pattern_state: PatternState | None = None,
    ) -> TradeDecision | None:
        decision = self.evaluate_strategy_with_market_data(self.symbol, pattern_state)
        if decision is None:
            return None

        if decision.action == "HOLD":
            self.storage.log("INFO", "자동주문", "전략 판단 결과 대기라 주문하지 않았습니다.")
            return decision

        if decision.action == "BUY":
            existing_quantity = self._holding_quantity(self.symbol)
            check = self.risk.approve_buy(self.max_capital, self.current_price, existing_quantity)
            if not check.approved:
                self.last_api_message = check.reason
                self.storage.log("WARN", "위험관리", check.reason)
                return decision
            order_quantity = min(max(1, quantity), check.quantity)
        else:
            order_quantity = self._holding_quantity(self.symbol) or max(1, quantity)

        self.send_kiwoom_order(
            account=account,
            side=decision.action,
            quantity=order_quantity,
            allow_real_order=allow_real_order,
        )
        return decision

    def step(self, pattern_state: PatternState, price: float | None = None) -> TradeDecision:
        if price is not None and price > 0:
            self.current_price = price
        self.pattern_state = pattern_state
        self._append_mock_candle(self.current_price)

        decision = self.strategy.evaluate(self.candles, pattern_state)
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
            market_quote=self.market_quote,
            balance_summary=self.balance_summary,
            real_time_quote=self.refresh_real_time_quote(),
            last_api_message=self.last_api_message,
            orders=self.storage.recent_orders(10),
            logs=self.storage.recent_logs(10),
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
        self.candles.append(Candle(high=high, low=low, close=close))
        if len(self.candles) > 200:
            self.candles = self.candles[-200:]

    def _holding_quantity(self, symbol: str) -> int:
        target = normalize_symbol(symbol)
        if self.balance_summary:
            for holding in self.balance_summary.holdings:
                if normalize_symbol(holding.symbol) == target:
                    return holding.quantity
        return self.broker.get_position(target).quantity
