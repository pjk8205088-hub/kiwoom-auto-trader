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
            self.storage.log("INFO", "주문", f"매수 체결: {symbol} {quantity}주")
        else:
            self.storage.log("WARN", "주문", f"매수 실패: {result.message}")
        return result

    def execute_sell(self, symbol: str, quantity: int, price: float) -> OrderResult:
        result = self.broker.place_market_sell(symbol, quantity, price)
        self.storage.save_order_result(result)
        if result.success:
            self.storage.log("INFO", "주문", f"매도 체결: {symbol} {result.quantity}주")
            return result

        self.storage.log("WARN", "주문", f"매도 실패, 재시도합니다: {result.message}")
        return self.retry_sell_until_resolved(symbol, price)

    def retry_sell_until_resolved(self, symbol: str, price: float) -> OrderResult:
        last_result: OrderResult | None = None
        for attempt in range(1, self.max_sell_attempts + 1):
            if self.stop_requested:
                break
            position = self.broker.get_position(symbol)
            if position.quantity <= 0:
                self.storage.log("INFO", "주문", "보유 수량이 없어 매도 재시도를 종료했습니다.")
                return last_result if last_result else self._no_position_result(symbol, price)
            last_result = self.broker.place_market_sell(symbol, position.quantity, price)
            self.storage.save_order_result(last_result)
            if last_result.success:
                self.storage.log("INFO", "주문", f"{attempt}회차 매도 재시도 체결")
                return last_result
            self.storage.log("WARN", "주문", f"{attempt}회차 매도 재시도 실패")

        if last_result is None:
            return self._no_position_result(symbol, price)
        self.storage.log("ERROR", "주문", "매도 재시도 한도에 도달했습니다.")
        return last_result

    def _no_position_result(self, symbol: str, price: float) -> OrderResult:
        from datetime import datetime

        return OrderResult(symbol, "SELL", 0, price, True, "보유 수량 없음", datetime.now())
