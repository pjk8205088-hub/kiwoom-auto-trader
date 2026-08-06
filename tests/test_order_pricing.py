import unittest

from kiwoom_auto_trader.models import OrderBookLevel, OrderBookSnapshot, TradeExecution
from kiwoom_auto_trader.order_pricing import (
    automatic_limit_price,
    daily_return_percent,
    midpoint_limit_price,
    performance_from_executions,
)


class OrderPricingTests(unittest.TestCase):
    def test_automatic_price_prefers_empty_quote_level(self):
        book = OrderBookSnapshot(
            "005930",
            levels=(
                OrderBookLevel(1, 4_135, 100, 4_130, 100),
                OrderBookLevel(2, 4_140, 50, 4_125, 0),
                OrderBookLevel(3, 4_145, 0, 4_120, 80),
            ),
        )
        self.assertEqual(automatic_limit_price(book, "BUY"), (4_125, "빈 매수호가 최하단"))
        self.assertEqual(automatic_limit_price(book, "SELL"), (4_145, "빈 매도호가 최상단"))

    def test_automatic_price_falls_back_to_midpoint_without_empty_level(self):
        book = OrderBookSnapshot(
            "005930",
            levels=(OrderBookLevel(1, 4_135, 100, 4_130, 100),),
        )
        self.assertEqual(automatic_limit_price(book, "BUY"), (4_130, "중간가 대체"))

    def test_midpoint_uses_side_aware_valid_tick(self):
        self.assertEqual(midpoint_limit_price(4_135, 4_130, "BUY"), 4_130)
        self.assertEqual(midpoint_limit_price(4_135, 4_130, "SELL"), 4_135)

    def test_midpoint_rejects_missing_or_crossed_quote(self):
        with self.assertRaises(ValueError):
            midpoint_limit_price(0, 4_000, "BUY")
        with self.assertRaises(ValueError):
            midpoint_limit_price(3_990, 4_000, "SELL")

    def test_daily_return_uses_assets_minus_profit_as_principal(self):
        self.assertAlmostEqual(daily_return_percent(1_050_000, 50_000), 5.0)

    def test_performance_uses_average_cost_for_closed_sales(self):
        rows = [
            TradeExecution("2026-08-01T09:00:00", "BUY", "005930", "삼성전자", 2, 100),
            TradeExecution("2026-08-01T09:01:00", "BUY", "005930", "삼성전자", 2, 120),
            TradeExecution("2026-08-01T09:02:00", "SELL", "005930", "삼성전자", 2, 130),
            TradeExecution("2026-08-01T09:03:00", "SELL", "005930", "삼성전자", 2, 90),
        ]
        summary = performance_from_executions(rows, "test")
        self.assertEqual(summary.trade_count, 2)
        self.assertEqual(summary.winning_trades, 1)
        self.assertEqual(summary.losing_trades, 1)
        self.assertEqual(summary.realized_profit, 0)
        self.assertEqual(summary.gross_profit, 40)
        self.assertEqual(summary.gross_loss, 40)
        self.assertEqual(summary.profit_loss_ratio, 1)


if __name__ == "__main__":
    unittest.main()
