import unittest

from kiwoom_auto_trader.kb_hts import (
    KbHtsStatus,
    build_kb_handoff_text,
    normalize_kb_symbol,
)


class KbHtsHelperTests(unittest.TestCase):
    def test_normalizes_only_six_digit_stock_symbols(self):
        self.assertEqual(normalize_kb_symbol("005930"), "005930")
        self.assertEqual(normalize_kb_symbol(" 005930 "), "005930")
        self.assertEqual(normalize_kb_symbol("000000"), "")
        self.assertEqual(normalize_kb_symbol("00593"), "")
        self.assertEqual(normalize_kb_symbol("삼성전자"), "")

    def test_builds_manual_handoff_payload_without_credentials(self):
        payload = build_kb_handoff_text(
            "005930",
            "삼성전자",
            72000,
            "BUY",
            2,
        )

        self.assertIn("종목코드\t005930", payload)
        self.assertIn("종목명\t삼성전자", payload)
        self.assertIn("현재가\t72,000원", payload)
        self.assertIn("구분\t매수", payload)
        self.assertIn("수량\t2주", payload)
        self.assertNotIn("계좌", payload)
        self.assertNotIn("비밀번호", payload)

    def test_status_is_connected_when_process_or_window_is_found(self):
        self.assertTrue(KbHtsStatus(process_found=True).connected)
        self.assertTrue(KbHtsStatus(window_found=True).connected)
        self.assertFalse(KbHtsStatus().connected)


if __name__ == "__main__":
    unittest.main()
