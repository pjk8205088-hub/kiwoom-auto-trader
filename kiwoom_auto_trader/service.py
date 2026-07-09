from __future__ import annotations

from dataclasses import dataclass, field
from random import uniform

from .broker import BrokerClient, MockBroker
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
        self.storage.log("INFO", "CONFIG", f"Configured {self.symbol}.")

    def start(self) -> None:
        self.running = True
        self.storage.log("INFO", "SYSTEM", "Auto trading started.")

    def stop(self) -> None:
        self.running = False
        self.order_manager.request_stop()
        self.storage.log("WARN", "SYSTEM", "Auto trading stopped.")

    def emergency_stop(self) -> None:
        self.stop()
        self.storage.log("ERROR", "SYSTEM", "Emergency stop requested.")

    def step(self, pattern_state: PatternState, price: float | None = None) -> TradeDecision:
        if price is not None and price > 0:
            self.current_price = price
        self.pattern_state = pattern_state
        self._append_mock_candle(self.current_price)

        decision = self.strategy.evaluate(self.candles, pattern_state)
        self.last_decision = decision
        self.storage.log("INFO", "STRATEGY", f"{decision.action}: {decision.reason}")

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
            orders=self.storage.recent_orders(10),
            logs=self.storage.recent_logs(10),
        )

    def _execute_decision(self, decision: TradeDecision) -> None:
        position = self.broker.get_position(self.symbol)
        if decision.action == "BUY":
            check = self.risk.approve_buy(self.max_capital, self.current_price, position.quantity)
            if not check.approved:
                self.storage.log("WARN", "RISK", check.reason)
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
