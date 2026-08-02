from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class RiskCheck:
    approved: bool
    quantity: int
    reason: str


@dataclass(frozen=True)
class DailyRiskStatus:
    trade_date: str
    loss_rate: float
    limit_percent: float
    locked: bool
    reason: str


class DailyLossCircuitBreaker:
    def __init__(self, limit_percent: float = 5.0) -> None:
        self.limit_percent = self._validate_limit(limit_percent)
        self.locked_date = ""

    @staticmethod
    def _validate_limit(value: float) -> float:
        limit = float(value)
        if not 0 < limit <= 100:
            raise ValueError("일일 최대 손실 한도는 0% 초과 100% 이하로 입력해 주세요.")
        return limit

    def configure(self, limit_percent: float) -> None:
        self.limit_percent = self._validate_limit(limit_percent)

    def restore_lock(self, locked_date: str) -> None:
        self.locked_date = str(locked_date or "").strip()

    def reset_for_new_day(self, trade_date: date | None = None) -> None:
        current = (trade_date or date.today()).isoformat()
        if self.locked_date and self.locked_date != current:
            self.locked_date = ""

    def evaluate(
        self,
        starting_assets: float,
        realized_profit: float,
        unrealized_profit: float,
        trade_date: date | None = None,
    ) -> DailyRiskStatus:
        current = (trade_date or date.today()).isoformat()
        self.reset_for_new_day(trade_date)
        base = max(0.0, float(starting_assets))
        total_profit = float(realized_profit) + float(unrealized_profit)
        loss_rate = (total_profit / base * 100.0) if base > 0 else 0.0
        if base > 0 and loss_rate <= -self.limit_percent:
            self.locked_date = current
        locked = self.locked_date == current
        reason = (
            f"일일 손실 {loss_rate:.2f}%가 한도 -{self.limit_percent:.2f}%에 도달해 당일 주문을 잠갔습니다."
            if locked
            else f"일일 손실 한도 정상 · 현재 {loss_rate:.2f}% / 한도 -{self.limit_percent:.2f}%"
        )
        return DailyRiskStatus(
            trade_date=current,
            loss_rate=loss_rate,
            limit_percent=self.limit_percent,
            locked=locked,
            reason=reason,
        )

    def can_trade(self, trade_date: date | None = None) -> bool:
        current = (trade_date or date.today()).isoformat()
        self.reset_for_new_day(trade_date)
        return self.locked_date != current


class RiskManager:
    def calculate_quantity(self, max_capital: float, market_price: float) -> int:
        if max_capital <= 0:
            return 0
        if market_price <= 0:
            return 0
        return int(max_capital // market_price)

    def approve_buy(
        self,
        max_capital: float,
        market_price: float,
        current_quantity: int,
    ) -> RiskCheck:
        maximum_quantity = self.calculate_quantity(max_capital, market_price)
        remaining_quantity = maximum_quantity - max(0, int(current_quantity))
        if remaining_quantity <= 0:
            return RiskCheck(
                False,
                0,
                "기존 보유 수량을 포함하면 종목별 운용 한도를 초과해 추가 매수를 막았습니다.",
            )
        return RiskCheck(True, remaining_quantity, "잔여 운용 한도 내 추가 매수 가능")
