from __future__ import annotations

from dataclasses import dataclass, field
from random import uniform

from .broker import BrokerClient, MockBroker
from .kiwoom_api import KiwoomAccountInfo, KiwoomOpenApiClient, KiwoomOpenApiError
from .models import (
    BalanceSummary,
    Candle,
    KiwoomOrderRequest,
    MarketQuote,
    PatternState,
    RealTimeQuote,
    StrategySettings,
    TradeDecision,
)
from .order_manager import OrderManager
from .risk import RiskManager
from .storage import Storage
from .strategy import StrategyEngine


@dataclass
class ServiceSnapshot:
    connection: str
    running: bool
    symbol: str
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
        self.account_info = KiwoomAccountInfo(False, [], message="키움 계좌가 연결되지 않았습니다.")
        self.running = False
        self.symbol = "005930"
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
        self.symbol = symbol.strip() or "005930"
        self.max_capital = max_capital
        self.strategy = StrategyEngine(settings)
        self.storage.log("INFO", "설정", f"{self.symbol} 설정을 저장했습니다.")

    def start(self) -> None:
        self.running = True
        self.storage.log("INFO", "시스템", "자동 운용을 시작했습니다.")

    def stop(self) -> None:
        self.running = False
        self.order_manager.request_stop()
        self.storage.log("WARN", "시스템", "자동 운용을 중지했습니다.")

    def emergency_stop(self) -> None:
        self.stop()
        self.storage.log("ERROR", "시스템", "긴급 정지가 요청되었습니다.")

    def start_account_connection(self) -> str:
        try:
            message = self.kiwoom_api.start_login()
            self.storage.log("INFO", "계좌", message)
            return message
        except KiwoomOpenApiError as exc:
            message = str(exc)
            self.account_info = KiwoomAccountInfo(False, [], message=message)
            self.storage.log("ERROR", "계좌", message)
            return message

    def check_account_environment(self) -> str:
        status = self.kiwoom_api.check_environment()
        self.account_info = KiwoomAccountInfo(False, [], message=status.message)
        self.storage.log("INFO", "계좌", status.message)
        return status.message

    def refresh_account_connection(self) -> KiwoomAccountInfo:
        try:
            self.account_info = self.kiwoom_api.get_account_info()
        except KiwoomOpenApiError as exc:
            self.account_info = KiwoomAccountInfo(False, [], message=str(exc))
        self.storage.log("INFO", "계좌", self.account_info.message)
        return self.account_info

    def request_current_price(self, symbol: str | None = None) -> MarketQuote | None:
        target = (symbol or self.symbol).strip()
        try:
            self.market_quote = self.kiwoom_api.request_current_price(target)
            if self.market_quote.current_price > 0:
                self.current_price = self.market_quote.current_price
            self.last_api_message = self.market_quote.message
            self.storage.log("INFO", "시세", f"{target} 현재가 {self.current_price:,.0f}")
            return self.market_quote
        except KiwoomOpenApiError as exc:
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "시세", self.last_api_message)
            return None

    def request_three_minute_candles(self, symbol: str | None = None) -> list[Candle]:
        target = (symbol or self.symbol).strip()
        try:
            self.candles = self.kiwoom_api.request_minute_candles(target, interval=3, count=120)
            if self.candles:
                self.current_price = self.candles[0].close
            self.last_api_message = f"{target} 3분봉 {len(self.candles)}개 조회 완료"
            self.storage.log("INFO", "시세", self.last_api_message)
            return self.candles
        except KiwoomOpenApiError as exc:
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "시세", self.last_api_message)
            return []

    def evaluate_strategy_with_market_data(self, symbol: str | None = None) -> TradeDecision | None:
        candles = self.request_three_minute_candles(symbol)
        if len(candles) < 2:
            self.last_api_message = "전략 판단에 필요한 3분봉 데이터가 부족합니다."
            self.storage.log("WARN", "전략", self.last_api_message)
            return None
        latest = candles[0]
        previous = candles[1]
        pattern: PatternState = "BULLISH" if latest.close >= previous.close else "BEARISH"
        ordered_candles = list(reversed(candles))
        decision = self.strategy.evaluate(ordered_candles, pattern)
        self.pattern_state = pattern
        self.last_decision = decision
        self.last_api_message = f"실제 3분봉 기반 전략 판단: {decision.action} / {decision.reason}"
        self.storage.log("INFO", "전략", self.last_api_message)
        return decision

    def request_balance(self, account: str, password: str = "") -> BalanceSummary | None:
        try:
            self.balance_summary = self.kiwoom_api.request_balance(account, password=password)
            self.last_api_message = self.balance_summary.message
            self.storage.log(
                "INFO",
                "잔고",
                f"{account} 보유종목 {len(self.balance_summary.holdings)}개 조회 완료",
            )
            return self.balance_summary
        except KiwoomOpenApiError as exc:
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "잔고", self.last_api_message)
            return None

    def register_real_time_price(self, symbol: str | None = None) -> str:
        target = (symbol or self.symbol).strip()
        try:
            self.last_api_message = self.kiwoom_api.register_real_time_price(target)
            self.real_time_symbol = target
            self.storage.log("INFO", "실시간", self.last_api_message)
        except KiwoomOpenApiError as exc:
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "실시간", self.last_api_message)
        return self.last_api_message

    def refresh_real_time_quote(self) -> RealTimeQuote | None:
        if not self.real_time_symbol:
            return None
        self.kiwoom_api.pump_messages()
        self.real_time_quote = self.kiwoom_api.latest_real_time_quote(self.real_time_symbol)
        if self.real_time_quote and self.real_time_quote.current_price > 0:
            self.current_price = self.real_time_quote.current_price
        return self.real_time_quote

    def unregister_real_time(self) -> str:
        try:
            self.last_api_message = self.kiwoom_api.unregister_real_time()
            self.real_time_symbol = ""
            self.real_time_quote = None
            self.storage.log("INFO", "실시간", self.last_api_message)
        except KiwoomOpenApiError as exc:
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
        try:
            request = KiwoomOrderRequest(
                account=account,
                symbol=self.symbol,
                side="BUY" if side == "BUY" else "SELL",
                quantity=quantity,
                price=0,
                hoga="03",
                allow_real_order=allow_real_order,
                require_mock_server=not allow_real_order,
            )
            self.last_api_message = self.kiwoom_api.send_order(request)
            self.storage.log("WARN" if allow_real_order else "INFO", "주문", self.last_api_message)
        except KiwoomOpenApiError as exc:
            self.last_api_message = str(exc)
            self.storage.log("ERROR", "주문", self.last_api_message)
        return self.last_api_message

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
