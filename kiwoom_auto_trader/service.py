from __future__ import annotations

from dataclasses import dataclass, field
from random import uniform

from .broker import BrokerClient, MockBroker
from .kiwoom_api import KiwoomAccountInfo, KiwoomOpenApiClient, KiwoomOpenApiError
from .models import Candle, PatternState, StrategySettings, TradeDecision
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

    def refresh_account_connection(self) -> KiwoomAccountInfo:
        try:
            self.account_info = self.kiwoom_api.get_account_info()
        except KiwoomOpenApiError as exc:
            self.account_info = KiwoomAccountInfo(False, [], message=str(exc))
        self.storage.log("INFO", "계좌", self.account_info.message)
        return self.account_info

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
