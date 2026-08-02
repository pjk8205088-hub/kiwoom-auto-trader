from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Action = Literal["BUY", "SELL", "HOLD"]
PatternState = Literal["NONE", "BULLISH", "BEARISH"]
OrderSide = Literal["BUY", "SELL"]
OrderAction = Literal["NEW", "MODIFY", "CANCEL"]

MIN_DMI_PERIOD = 1
MAX_DMI_PERIOD = 99


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

    def __post_init__(self) -> None:
        if not MIN_DMI_PERIOD <= self.dmi_period <= MAX_DMI_PERIOD:
            raise ValueError(
                f"DMI 계산 기간은 {MIN_DMI_PERIOD}일부터 {MAX_DMI_PERIOD}일까지 선택해 주세요."
            )


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
class TradingBaseline:
    symbol: str
    capital_limit: float
    reference_price: float
    set_at: str


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
class OrderBookLevel:
    level: int
    ask_price: float = 0.0
    ask_quantity: int = 0
    bid_price: float = 0.0
    bid_quantity: int = 0


@dataclass(frozen=True)
class OrderBookSnapshot:
    symbol: str
    levels: tuple[OrderBookLevel, ...] = ()
    timestamp: str = ""
    source: str = ""

    @property
    def best_ask(self) -> float:
        return self.levels[0].ask_price if self.levels else 0.0

    @property
    def best_bid(self) -> float:
        return self.levels[0].bid_price if self.levels else 0.0


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
    previous_trade_value: float = 0.0
    market_cap: float = 0.0
    program_trading_trend: float = 0.0
    open_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
    ask_price: float = 0.0
    bid_price: float = 0.0
    timestamp: str = ""


@dataclass(frozen=True)
class VolumeRankQuote:
    rank: int
    symbol: str
    name: str = ""
    current_price: float = 0.0
    change: float = 0.0
    change_rate: float = 0.0
    volume: int = 0
    turnover_rate: float = 0.0
    trade_value: float = 0.0
    market_cap: float = 0.0
    change_sign: str = ""
    previous_ratio: float = 0.0
    nxt_available: bool = False
    timestamp: str = ""


@dataclass(frozen=True)
class AccountCash:
    account: str
    deposit: float = 0.0
    orderable_amount: float = 0.0
    withdrawable_amount: float = 0.0
    d2_estimated_deposit: float = 0.0
    message: str = ""


@dataclass(frozen=True)
class Holding:
    symbol: str
    name: str
    quantity: int
    average_price: float
    current_price: float
    profit_loss: float
    profit_rate: float
    sellable_quantity: int = 0
    purchase_amount: float = 0.0


@dataclass(frozen=True)
class BalanceSummary:
    account: str
    deposit: float = 0.0
    orderable_amount: float = 0.0
    withdrawable_amount: float = 0.0
    d2_estimated_deposit: float = 0.0
    total_purchase: float = 0.0
    total_evaluation: float = 0.0
    total_profit_loss: float = 0.0
    total_profit_rate: float = 0.0
    estimated_assets: float = 0.0
    realized_profit_today: float = 0.0
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
    market_session_code: str = ""


@dataclass(frozen=True)
class MarketSessionStatus:
    operation_code: str
    event_time: str = ""
    expected_remaining_seconds: int = 0
    received_at: str = ""
    source: str = ""

    @property
    def is_open(self) -> bool:
        return self.operation_code == "3"


@dataclass(frozen=True)
class KiwoomOrderRequest:
    account: str
    symbol: str
    side: OrderSide
    quantity: int
    price: int = 0
    hoga: str = "03"
    original_order_no: str = ""
    action: OrderAction = "NEW"
    allow_real_order: bool = False
    require_mock_server: bool = True


@dataclass(frozen=True)
class UnfilledOrder:
    order_no: str
    symbol: str
    symbol_name: str
    side: OrderSide
    order_quantity: int
    unfilled_quantity: int
    order_price: float
    current_price: float = 0.0
    timestamp: str = ""
    status: str = "미체결"


@dataclass(frozen=True)
class QuickOrderPreset:
    slot: int
    quantity: int = 1
    label: str = ""

    def __post_init__(self) -> None:
        if not 1 <= self.slot <= 10:
            raise ValueError("퀵 주문 번호는 1번부터 10번까지입니다.")
        if self.quantity <= 0:
            raise ValueError("퀵 주문 수량은 1주 이상이어야 합니다.")


@dataclass(frozen=True)
class PerformanceSummary:
    period: str
    trade_count: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    realized_profit: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0

    @property
    def win_rate(self) -> float:
        closed = self.winning_trades + self.losing_trades
        return (self.winning_trades / closed * 100.0) if closed else 0.0

    @property
    def profit_loss_ratio(self) -> float:
        if self.gross_loss <= 0:
            return self.gross_profit if self.gross_profit > 0 else 0.0
        return self.gross_profit / self.gross_loss


@dataclass(frozen=True)
class TradeExecution:
    timestamp: str
    side: OrderSide
    symbol: str
    symbol_name: str
    quantity: int
    price: float
    order_no: str = ""
    order_mode: str = ""
    status: str = "체결"
    message: str = "키움 계좌 실제 체결"

    @property
    def total_amount(self) -> float:
        return self.quantity * self.price


@dataclass(frozen=True)
class OrderResult:
    symbol: str
    side: OrderSide
    quantity: int
    price: float
    success: bool
    message: str
    timestamp: datetime
    symbol_name: str = ""
    total_amount: float = 0.0
    order_no: str = ""
    order_mode: str = ""


@dataclass(frozen=True)
class SystemLog:
    level: Literal["INFO", "WARN", "ERROR"]
    category: str
    message: str
    timestamp: datetime
