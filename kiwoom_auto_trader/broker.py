from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import OrderResult, Position


class BrokerClient(Protocol):
    def connection_status(self) -> str:
        ...

    def get_position(self, symbol: str) -> Position:
        ...

    def place_market_buy(self, symbol: str, quantity: int, price: float) -> OrderResult:
        ...

    def place_market_sell(self, symbol: str, quantity: int, price: float) -> OrderResult:
        ...


class MockBroker:
    def __init__(self) -> None:
        self.positions: dict[str, Position] = {}
        self.fail_next_buy = False
        self.fail_next_sell = False

    def connection_status(self) -> str:
        return "MOCK_CONNECTED"

    def get_position(self, symbol: str) -> Position:
        return self.positions.get(symbol, Position(symbol=symbol))

    def place_market_buy(self, symbol: str, quantity: int, price: float) -> OrderResult:
        if self.fail_next_buy:
            self.fail_next_buy = False
            return self._result(symbol, "BUY", quantity, price, False, "Mock buy failure.")

        current = self.get_position(symbol)
        new_quantity = current.quantity + quantity
        if new_quantity <= 0:
            average_price = price
        else:
            previous_value = current.quantity * current.average_price
            average_price = (previous_value + quantity * price) / new_quantity
        self.positions[symbol] = Position(symbol, new_quantity, average_price)
        return self._result(symbol, "BUY", quantity, price, True, "Mock buy filled.")

    def place_market_sell(self, symbol: str, quantity: int, price: float) -> OrderResult:
        if self.fail_next_sell:
            self.fail_next_sell = False
            return self._result(symbol, "SELL", quantity, price, False, "Mock sell failure.")

        current = self.get_position(symbol)
        sell_quantity = min(quantity, current.quantity)
        remaining = current.quantity - sell_quantity
        if remaining > 0:
            self.positions[symbol] = Position(symbol, remaining, current.average_price)
        else:
            self.positions.pop(symbol, None)
        return self._result(symbol, "SELL", sell_quantity, price, True, "Mock sell filled.")

    def _result(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        success: bool,
        message: str,
    ) -> OrderResult:
        return OrderResult(
            symbol=symbol,
            side=side,  # type: ignore[arg-type]
            quantity=quantity,
            price=price,
            success=success,
            message=message,
            timestamp=datetime.now(),
        )


class KiwoomBrokerPlaceholder:
    def connection_status(self) -> str:
        return "LIVE_ADAPTER_NOT_IMPLEMENTED"

    def get_position(self, symbol: str) -> Position:
        raise NotImplementedError("Live Kiwoom adapter is not implemented.")

    def place_market_buy(self, symbol: str, quantity: int, price: float) -> OrderResult:
        raise NotImplementedError("Live Kiwoom adapter is not implemented.")

    def place_market_sell(self, symbol: str, quantity: int, price: float) -> OrderResult:
        raise NotImplementedError("Live Kiwoom adapter is not implemented.")
