from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Action = Literal["BUY", "SELL", "HOLD"]
PatternState = Literal["NONE", "BULLISH", "BEARISH"]
OrderSide = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class Candle:
    high: float
    low: float
    close: float
    open: float = 0.0
    volume: int = 0
    timestamp: str = ""


@dataclass(frozen=True)
class StrategySettings:
    dmi_period: int = 14


@dataclass(frozen=True)
class DmiPoint:
    index: int
    timestamp: str
    plus_di: float
    minus_di: float
    adx: float | None
    pattern_state: PatternState


@dataclass(frozen=True)
class SymbolConfig:
    symbol: str
    max_capital: float
    settings: StrategySettings


@dataclass(frozen=True)
class TradeDecision:
    action: Action
    reason: str
    pattern_state: PatternState
    dmi_plus: float | None = None
    dmi_minus: float | None = None
    adx: float | None = None
    candle_timestamp: str = ""


@dataclass
class Position:
    symbol: str
    quantity: int = 0
    average_price: float = 0.0

    @property
    def market_value(self) -> float:
        return self.quantity * self.average_price


@dataclass(frozen=True)
class MarketQuote:
    symbol: str
    name: str = ""
    current_price: float = 0.0
    change: float = 0.0
    change_rate: float = 0.0
    volume: int = 0
    timestamp: str = ""
    message: str = ""


@dataclass(frozen=True)
class WatchlistQuote:
    symbol: str
    name: str = ""
    market: str = "KRX"
    current_price: float = 0.0
    change: float = 0.0
    change_rate: float = 0.0
    volume: int = 0
    trade_value: float = 0.0
    open_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
    ask_price: float = 0.0
    bid_price: float = 0.0
    timestamp: str = ""


@dataclass(frozen=True)
class Holding:
    symbol: str
    name: str
    quantity: int
    average_price: float
    current_price: float
    profit_loss: float
    profit_rate: float


@dataclass(frozen=True)
class BalanceSummary:
    account: str
    total_purchase: float = 0.0
    total_evaluation: float = 0.0
    total_profit_loss: float = 0.0
    total_profit_rate: float = 0.0
    estimated_assets: float = 0.0
    holdings: tuple[Holding, ...] = ()
    message: str = ""


@dataclass(frozen=True)
class RealTimeQuote:
    symbol: str
    current_price: float = 0.0
    change: float = 0.0
    change_rate: float = 0.0
    volume: int = 0
    timestamp: str = ""


@dataclass(frozen=True)
class KiwoomOrderRequest:
    account: str
    symbol: str
    side: OrderSide
    quantity: int
    price: int = 0
    hoga: str = "03"
    original_order_no: str = ""
    allow_real_order: bool = False
    require_mock_server: bool = True


@dataclass(frozen=True)
class OrderResult:
    symbol: str
    side: OrderSide
    quantity: int
    price: float
    success: bool
    message: str
    timestamp: datetime


@dataclass(frozen=True)
class SystemLog:
    level: Literal["INFO", "WARN", "ERROR"]
    category: str
    message: str
    timestamp: datetime
