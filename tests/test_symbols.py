import unittest

from kiwoom_auto_trader.symbols import mask_account_number, normalize_symbol, known_symbol_name


class SymbolHelperTests(unittest.TestCase):
    def test_normalizes_samsung_electro_mechanics_short_code(self):
        self.assertEqual(normalize_symbol("00915"), "009150")
        self.assertEqual(known_symbol_name("00915"), "삼성전기")

    def test_masks_account_with_first_four_and_last_four_digits(self):
        self.assertEqual(mask_account_number("1234567890"), "1234-7890")


if __name__ == "__main__":
    unittest.main()
