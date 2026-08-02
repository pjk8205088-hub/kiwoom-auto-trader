import unittest
from datetime import date

from kiwoom_auto_trader.risk import DailyLossCircuitBreaker, RiskManager


class RiskManagerTests(unittest.TestCase):
    def test_calculates_quantity_from_capital(self):
        manager = RiskManager()

        self.assertEqual(manager.calculate_quantity(1_000_000, 72_000), 13)

    def test_allows_additional_buy_within_remaining_capital_limit(self):
        manager = RiskManager()

        check = manager.approve_buy(1_000_000, 72_000, current_quantity=1)

        self.assertTrue(check.approved)
        self.assertEqual(check.quantity, 12)

    def test_rejects_additional_buy_when_holdings_use_capital_limit(self):
        manager = RiskManager()

        check = manager.approve_buy(1_000_000, 72_000, current_quantity=13)

        self.assertFalse(check.approved)
        self.assertEqual(check.quantity, 0)
        self.assertIn("운용 한도", check.reason)

    def test_daily_loss_breaker_locks_only_current_date(self):
        breaker = DailyLossCircuitBreaker(5)

        status = breaker.evaluate(1_000_000, -30_000, -25_000, date(2026, 8, 1))

        self.assertTrue(status.locked)
        self.assertFalse(breaker.can_trade(date(2026, 8, 1)))
        self.assertTrue(breaker.can_trade(date(2026, 8, 2)))


if __name__ == "__main__":
    unittest.main()
