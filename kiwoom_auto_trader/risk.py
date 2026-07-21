from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskCheck:
    approved: bool
    quantity: int
    reason: str


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
