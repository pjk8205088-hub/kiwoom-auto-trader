from __future__ import annotations

import math
from dataclasses import dataclass

from .models import OrderSide, PatternState
from .symbols import clean_account_number, normalize_symbol


MAX_TRIGGER_PERCENT = 100.0


@dataclass(frozen=True)
class OneShotPriceTrigger:
    side: OrderSide
    symbol: str
    base_price: float
    percent: float
    target_price: float
    quantity: int
    required_pattern: PatternState
    allow_real_order: bool = False
    account: str = ""

    @classmethod
    def create(
        cls,
        side: OrderSide,
        symbol: str,
        base_price: float,
        percent: float,
        quantity: int,
        allow_real_order: bool = False,
        account: str = "",
    ) -> OneShotPriceTrigger:
        if side not in ("BUY", "SELL"):
            raise ValueError("자동주문 구분은 BUY 또는 SELL이어야 합니다.")
        normalized = normalize_symbol(symbol)
        if not normalized or normalized == "000000":
            raise ValueError("먼저 실제 종목번호를 설정해 주세요.")
        if not math.isfinite(base_price) or base_price <= 0:
            raise ValueError("현재가를 먼저 불러와 주세요.")
        if not math.isfinite(percent) or not 0 < percent <= MAX_TRIGGER_PERCENT:
            raise ValueError("등락률은 0보다 크고 100 이하인 숫자로 입력해 주세요.")
        if quantity <= 0:
            raise ValueError("주문 수량은 1주 이상 선택해 주세요.")

        direction = 1.0 if side == "BUY" else -1.0
        target_price = base_price * (1.0 + direction * percent / 100.0)
        required_pattern: PatternState = "BULLISH" if side == "BUY" else "BEARISH"
        return cls(
            side=side,
            symbol=normalized,
            base_price=float(base_price),
            percent=float(percent),
            target_price=target_price,
            quantity=int(quantity),
            required_pattern=required_pattern,
            allow_real_order=bool(allow_real_order),
            account=clean_account_number(account),
        )

    def reached(self, current_price: float, pattern_state: PatternState) -> bool:
        if not math.isfinite(current_price) or current_price <= 0:
            return False
        if pattern_state != self.required_pattern:
            return False
        if self.side == "BUY":
            return current_price >= self.target_price
        return current_price <= self.target_price


class OneShotPriceTriggerBook:
    def __init__(self) -> None:
        self._triggers: dict[OrderSide, OneShotPriceTrigger] = {}

    def arm(
        self,
        side: OrderSide,
        symbol: str,
        base_price: float,
        percent: float,
        quantity: int,
        allow_real_order: bool = False,
        account: str = "",
    ) -> OneShotPriceTrigger:
        trigger = OneShotPriceTrigger.create(
            side,
            symbol,
            base_price,
            percent,
            quantity,
            allow_real_order,
            account,
        )
        self._triggers[side] = trigger
        return trigger

    def get(self, side: OrderSide) -> OneShotPriceTrigger | None:
        return self._triggers.get(side)

    def clear(self, side: OrderSide | None = None) -> None:
        if side is None:
            self._triggers.clear()
            return
        self._triggers.pop(side, None)

    def pop_triggered(
        self,
        symbol: str,
        current_price: float,
        pattern_state: PatternState,
    ) -> tuple[OneShotPriceTrigger, ...]:
        normalized = normalize_symbol(symbol)
        triggered: list[OneShotPriceTrigger] = []
        for side in ("BUY", "SELL"):
            trigger = self._triggers.get(side)
            if (
                trigger is None
                or trigger.symbol != normalized
                or not trigger.reached(current_price, pattern_state)
            ):
                continue
            triggered.append(self._triggers.pop(side))
        return tuple(triggered)
