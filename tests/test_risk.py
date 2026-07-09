import unittest

from kiwoom_auto_trader.risk import RiskManager


class RiskManagerTests(unittest.TestCase):
    def test_calculates_quantity_from_capital(self):
        manager = RiskManager()

        self.assertEqual(manager.calculate_quantity(1_000_000, 72_000), 13)

    def test_rejects_duplicate_position(self):
        manager = RiskManager()

        check = manager.approve_buy(1_000_000, 72_000, current_quantity=1)

        self.assertFalse(check.approved)
        self.assertEqual(check.quantity, 0)


if __name__ == "__main__":
    unittest.main()
