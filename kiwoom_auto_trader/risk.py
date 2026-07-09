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
        if current_quantity > 0:
            return RiskCheck(False, 0, "Position already exists.")
        quantity = self.calculate_quantity(max_capital, market_price)
        if quantity <= 0:
            return RiskCheck(False, 0, "Max capital is below the current market price.")
        return RiskCheck(True, quantity, "Approved.")
