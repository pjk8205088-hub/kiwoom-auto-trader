from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime

from .models import OrderBookSnapshot, OrderSide, PerformanceSummary, TradeExecution


def krx_tick_size(price: float) -> int:
    value = max(0.0, float(price))
    if value < 2_000:
        return 1
    if value < 5_000:
        return 5
    if value < 20_000:
        return 10
    if value < 50_000:
        return 50
    if value < 200_000:
        return 100
    if value < 500_000:
        return 500
    return 1_000


def midpoint_limit_price(best_ask: float, best_bid: float, side: OrderSide) -> int:
    ask = float(best_ask)
    bid = float(best_bid)
    if ask <= 0 or bid <= 0:
        raise ValueError("매도1호가와 매수1호가가 모두 있어야 중간가를 계산할 수 있습니다.")
    if ask < bid:
        raise ValueError("매도1호가가 매수1호가보다 낮아 호가 정보를 다시 조회해야 합니다.")
    midpoint = (ask + bid) / 2.0
    tick = krx_tick_size(midpoint)
    units = midpoint / tick
    rounded = math.floor(units) if side == "BUY" else math.ceil(units)
    return max(tick, int(rounded * tick))


def automatic_limit_price(book: OrderBookSnapshot, side: OrderSide) -> tuple[int, str]:
    """Choose an empty quote level first, then fall back to the midpoint."""

    if not book.levels:
        raise ValueError("자동가를 계산하려면 10호가 정보가 필요합니다.")

    if side == "BUY":
        empty_prices = [
            float(level.bid_price)
            for level in book.levels
            if float(level.bid_price) > 0 and int(level.bid_quantity) <= 0
        ]
        if empty_prices:
            return int(min(empty_prices)), "빈 매수호가 최하단"
    else:
        empty_prices = [
            float(level.ask_price)
            for level in book.levels
            if float(level.ask_price) > 0 and int(level.ask_quantity) <= 0
        ]
        if empty_prices:
            return int(max(empty_prices)), "빈 매도호가 최상단"

    return midpoint_limit_price(book.best_ask, book.best_bid, side), "중간가 대체"


def daily_return_percent(estimated_assets: float, realized_profit: float) -> float:
    assets = float(estimated_assets)
    profit = float(realized_profit)
    principal = assets - profit
    if principal <= 0:
        return 0.0
    return profit / principal * 100.0


def performance_from_executions(
    executions: list[TradeExecution],
    period: str,
) -> PerformanceSummary:
    inventory: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))
    realized = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    winning = 0
    losing = 0
    sells = 0

    def sort_key(item: TradeExecution) -> tuple[datetime, str]:
        digits = "".join(character for character in item.timestamp if character.isdigit())
        try:
            moment = datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
        except (TypeError, ValueError):
            moment = datetime.min
        return moment, item.order_no

    for execution in sorted(executions, key=sort_key):
        symbol = execution.symbol
        quantity = max(0, int(execution.quantity))
        price = max(0.0, float(execution.price))
        if quantity <= 0 or price <= 0:
            continue
        held, average = inventory[symbol]
        if execution.side == "BUY":
            total_cost = held * average + quantity * price
            next_quantity = held + quantity
            inventory[symbol] = (next_quantity, total_cost / next_quantity)
            continue
        closed = min(held, quantity)
        if closed <= 0:
            continue
        profit = (price - average) * closed
        realized += profit
        sells += 1
        if profit > 0:
            winning += 1
            gross_profit += profit
        elif profit < 0:
            losing += 1
            gross_loss += abs(profit)
        inventory[symbol] = (held - closed, average if held > closed else 0.0)

    return PerformanceSummary(
        period=period,
        trade_count=sells,
        winning_trades=winning,
        losing_trades=losing,
        realized_profit=realized,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
    )
