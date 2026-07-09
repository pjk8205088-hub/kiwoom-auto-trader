from __future__ import annotations

from .broker import BrokerClient
from .models import OrderResult
from .storage import Storage


class OrderManager:
    def __init__(self, broker: BrokerClient, storage: Storage, max_sell_attempts: int = 5) -> None:
        self.broker = broker
        self.storage = storage
        self.max_sell_attempts = max_sell_attempts
        self.stop_requested = False

    def request_stop(self) -> None:
        self.stop_requested = True

    def execute_buy(self, symbol: str, quantity: int, price: float) -> OrderResult:
        result = self.broker.place_market_buy(symbol, quantity, price)
        self.storage.save_order_result(result)
        if result.success:
            self.storage.log("INFO", "ORDER", f"Buy filled: {symbol} x {quantity}")
        else:
            self.storage.log("WARN", "ORDER", f"Buy failed: {result.message}")
        return result

    def execute_sell(self, symbol: str, quantity: int, price: float) -> OrderResult:
        result = self.broker.place_market_sell(symbol, quantity, price)
        self.storage.save_order_result(result)
        if result.success:
            self.storage.log("INFO", "ORDER", f"Sell filled: {symbol} x {result.quantity}")
            return result

        self.storage.log("WARN", "ORDER", f"Sell failed, retrying: {result.message}")
        return self.retry_sell_until_resolved(symbol, price)

    def retry_sell_until_resolved(self, symbol: str, price: float) -> OrderResult:
        last_result: OrderResult | None = None
        for attempt in range(1, self.max_sell_attempts + 1):
            if self.stop_requested:
                break
            position = self.broker.get_position(symbol)
            if position.quantity <= 0:
                self.storage.log("INFO", "ORDER", "Sell retry stopped: no position remains.")
                return last_result if last_result else self._no_position_result(symbol, price)
            last_result = self.broker.place_market_sell(symbol, position.quantity, price)
            self.storage.save_order_result(last_result)
            if last_result.success:
                self.storage.log("INFO", "ORDER", f"Sell retry filled on attempt {attempt}.")
                return last_result
            self.storage.log("WARN", "ORDER", f"Sell retry attempt {attempt} failed.")

        if last_result is None:
            return self._no_position_result(symbol, price)
        self.storage.log("ERROR", "ORDER", "Sell retry limit reached.")
        return last_result

    def _no_position_result(self, symbol: str, price: float) -> OrderResult:
        from datetime import datetime

        return OrderResult(symbol, "SELL", 0, price, True, "No position remains.", datetime.now())
