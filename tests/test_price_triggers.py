import unittest

from kiwoom_auto_trader.price_triggers import OneShotPriceTrigger, OneShotPriceTriggerBook


class OneShotPriceTriggerTests(unittest.TestCase):
    def test_calculates_rising_buy_and_falling_sell_targets(self):
        buy = OneShotPriceTrigger.create("BUY", "005930", 10_000, 3, 1)
        sell = OneShotPriceTrigger.create("SELL", "005930", 10_000, 3, 1)

        self.assertEqual(buy.target_price, 10_300)
        self.assertEqual(sell.target_price, 9_700)
        self.assertEqual(buy.required_pattern, "BULLISH")
        self.assertEqual(sell.required_pattern, "BEARISH")
        self.assertTrue(buy.reached(10_300, "BULLISH"))
        self.assertFalse(buy.reached(10_300, "BEARISH"))
        self.assertFalse(buy.reached(10_299, "BULLISH"))
        self.assertTrue(sell.reached(9_700, "BEARISH"))
        self.assertFalse(sell.reached(9_700, "BULLISH"))
        self.assertFalse(sell.reached(9_701, "BEARISH"))

    def test_keeps_buy_and_sell_settings_independent_and_consumes_once(self):
        book = OneShotPriceTriggerBook()
        buy = book.arm("BUY", "005930", 10_000, 3, 2)
        sell = book.arm("SELL", "005930", 10_000, 3, 4)

        self.assertEqual(book.pop_triggered("005930", 10_300, "BULLISH"), (buy,))
        self.assertIsNone(book.get("BUY"))
        self.assertEqual(book.get("SELL"), sell)
        self.assertEqual(book.pop_triggered("005930", 9_700, "BEARISH"), (sell,))
        self.assertEqual(book.pop_triggered("005930", 9_700, "BEARISH"), ())

    def test_keeps_price_setting_until_the_required_dmi_state_matches(self):
        book = OneShotPriceTriggerBook()
        buy = book.arm("BUY", "005930", 10_000, 3, 2)

        self.assertEqual(book.pop_triggered("005930", 10_500, "BEARISH"), ())
        self.assertEqual(book.get("BUY"), buy)
        self.assertEqual(book.pop_triggered("005930", 10_500, "BULLISH"), (buy,))

    def test_does_not_consume_a_setting_for_another_symbol(self):
        book = OneShotPriceTriggerBook()
        book.arm("SELL", "005930", 10_000, 3, 1)

        self.assertEqual(book.pop_triggered("000660", 20_000, "BEARISH"), ())
        self.assertIsNotNone(book.get("SELL"))

    def test_keeps_the_account_selected_when_the_order_is_armed(self):
        trigger = OneShotPriceTrigger.create(
            "SELL",
            "005930",
            10_000,
            0.2,
            3,
            allow_real_order=True,
            account="1234-5678",
        )

        self.assertEqual(trigger.account, "12345678")
        self.assertEqual(trigger.target_price, 9_980)

    def test_rejects_placeholder_symbol_invalid_percent_and_zero_quantity(self):
        with self.assertRaisesRegex(ValueError, "종목번호"):
            OneShotPriceTrigger.create("BUY", "000000", 10_000, 3, 1)
        with self.assertRaisesRegex(ValueError, "등락률"):
            OneShotPriceTrigger.create("BUY", "005930", 10_000, 0, 1)
        with self.assertRaisesRegex(ValueError, "주문 수량"):
            OneShotPriceTrigger.create("BUY", "005930", 10_000, 3, 0)


if __name__ == "__main__":
    unittest.main()
