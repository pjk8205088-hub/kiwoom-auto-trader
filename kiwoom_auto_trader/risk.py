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
            return RiskCheck(False, 0, "이미 보유 포지션이 있어 추가 매수를 막았습니다.")
        quantity = self.calculate_quantity(max_capital, market_price)
        if quantity <= 0:
            return RiskCheck(False, 0, "운용 한도가 현재가보다 낮아 주문할 수 없습니다.")
        return RiskCheck(True, quantity, "주문 가능")
