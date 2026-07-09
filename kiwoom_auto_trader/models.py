from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Action = Literal["BUY", "SELL", "HOLD"]
PatternState = Literal["NONE", "BULLISH", "BEARISH"]


@dataclass(frozen=True)
class Candle:
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class StrategySettings:
    cci_period: int = 20
    cci_upper: float = 100.0
    cci_lower: float = -100.0
    use_cci_filter: bool = True


@dataclass(frozen=True)
class SymbolConfig:
    symbol: str
    max_capital: float
    settings: StrategySettings


@dataclass(frozen=True)
class TradeDecision:
    action: Action
    reason: str
    cci_value: float | None
    pattern_state: PatternState


@dataclass
class Position:
    symbol: str
    quantity: int = 0
    average_price: float = 0.0

    @property
    def market_value(self) -> float:
        return self.quantity * self.average_price


@dataclass(frozen=True)
class OrderResult:
    symbol: str
    side: Literal["BUY", "SELL"]
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
