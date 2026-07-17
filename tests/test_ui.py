import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from kiwoom_auto_trader.kiwoom_api import KiwoomAccountInfo
from kiwoom_auto_trader.models import BalanceSummary, Candle, WatchlistQuote
from kiwoom_auto_trader.service import ServiceSnapshot
from kiwoom_auto_trader.ui import (
    KiwoomRestLoginDialog,
    TraderApp,
    _account_access_confirmed,
    _account_password_input_allowed,
    _baseline_validation_message,
    _parse_money_input,
    _parse_order_quantity,
    _percentage_input_allowed,
)


class UiHelperTests(unittest.TestCase):
    def test_marks_openapi_account_connected_only_after_password_verification(self):
        self.assertFalse(_account_access_confirmed(True, "OpenAPI+", "12345678", False))
        self.assertTrue(_account_access_confirmed(True, "OpenAPI+", "12345678", True))
        self.assertTrue(_account_access_confirmed(True, "REST API", "12345678", False))
        self.assertFalse(_account_access_confirmed(False, "REST API", "12345678", True))

    def test_accepts_numeric_percentage_during_entry(self):
        self.assertTrue(_percentage_input_allowed(""))
        self.assertTrue(_percentage_input_allowed("3"))
        self.assertTrue(_percentage_input_allowed("3.5"))
        self.assertTrue(_percentage_input_allowed("0."))
        self.assertFalse(_percentage_input_allowed("3%"))
        self.assertFalse(_percentage_input_allowed("1.2.3"))

    def test_parses_non_negative_integer_order_quantity(self):
        self.assertEqual(_parse_order_quantity(""), 0)
        self.assertEqual(_parse_order_quantity("0"), 0)
        self.assertEqual(_parse_order_quantity("25"), 25)
        self.assertEqual(_parse_order_quantity("2.5"), 0)
        self.assertEqual(_parse_order_quantity("-1"), 0)

    def test_accepts_only_up_to_eight_password_digits_during_entry(self):
        self.assertTrue(_account_password_input_allowed(""))
        self.assertTrue(_account_password_input_allowed("1234"))
        self.assertTrue(_account_password_input_allowed("12345678"))
        self.assertFalse(_account_password_input_allowed("123456789"))
        self.assertFalse(_account_password_input_allowed("12ab"))

    def test_password_setting_verifies_balance_then_clears_plaintext(self):
        class Variable:
            def __init__(self, value=""):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        balance = BalanceSummary(account="12345678", deposit=1_000_000)
        request_balance = MagicMock(return_value=balance)
        app = SimpleNamespace(
            _require_live_connection=lambda: True,
            service=SimpleNamespace(
                account_info=SimpleNamespace(connection_method="OpenAPI+"),
                request_balance=request_balance,
            ),
            account_password_var=Variable("1234"),
            account_password_status_var=Variable("미확인"),
            account_password_entry=MagicMock(),
            account_password_button=MagicMock(),
            _account_access_verified=False,
            _update_connection_badge=MagicMock(),
            status_text=Variable(),
            update_idletasks=MagicMock(),
            _account_for_api=lambda: "12345678",
            _refresh=MagicMock(),
            _show_account_info_window=MagicMock(),
        )

        TraderApp._set_account_password(app)

        request_balance.assert_called_once_with("12345678", "1234")
        self.assertEqual(app.account_password_var.get(), "")
        self.assertEqual(app.account_password_status_var.get(), "확인됨")
        self.assertTrue(app._account_access_verified)

    def test_parses_comma_formatted_operating_capital(self):
        self.assertEqual(_parse_money_input("1,000,000"), 1_000_000)
        self.assertEqual(_parse_money_input("잘못된 금액"), 0)

    def test_validates_fixed_capital_against_price_and_account_funds(self):
        self.assertEqual(_baseline_validation_message(500_000, 72_000, 1_000_000), "")
        self.assertIn(
            "주문가능금액보다 작아야",
            _baseline_validation_message(1_000_000, 72_000, 1_000_000),
        )
        self.assertIn("1주 가격", _baseline_validation_message(70_000, 72_000, 1_000_000))
        self.assertIn("현재가", _baseline_validation_message(500_000, 0, 1_000_000))

    def test_reads_single_line_key_file_without_persisting_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "account_appkey.txt"
            path.write_text("test-key-value\n", encoding="utf-8")

            value = KiwoomRestLoginDialog._read_key_file(path)

        self.assertEqual(value, "test-key-value")

    def test_rejects_multiline_key_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "account_secretkey.txt"
            path.write_text("first\nsecond\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "형식"):
                KiwoomRestLoginDialog._read_key_file(path)

    def test_reads_matching_key_pair_from_download_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            downloads = Path(directory)
            (downloads / "account_appkey.txt").write_text("test-app-key\n", encoding="utf-8")
            (downloads / "account_secretkey.txt").write_text(
                "test-secret-key\n",
                encoding="utf-8",
            )

            key_pair = KiwoomRestLoginDialog._read_latest_key_pair(downloads)

        self.assertEqual(key_pair, ("test-app-key", "test-secret-key"))

    def test_does_not_pair_keys_with_different_prefixes(self):
        with tempfile.TemporaryDirectory() as directory:
            downloads = Path(directory)
            (downloads / "first_appkey.txt").write_text("test-app-key\n", encoding="utf-8")
            (downloads / "second_secretkey.txt").write_text(
                "test-secret-key\n",
                encoding="utf-8",
            )

            key_pair = KiwoomRestLoginDialog._read_latest_key_pair(downloads)

        self.assertIsNone(key_pair)

    def test_labels_live_rest_account_as_order_locked(self):
        info = KiwoomAccountInfo(
            True,
            ["1234567890"],
            server_type="실거래",
            connection_method="REST API",
        )

        label = TraderApp._account_capability_label(info)

        self.assertIn("주문 잠금", label)

    def test_main_status_hides_internal_mock_state_and_account_details(self):
        snapshot = ServiceSnapshot(
            connection="MOCK_CONNECTED",
            running=False,
            symbol="002880",
            symbol_name="디와이아이",
            pattern="NONE",
            price=1_234,
            quantity=0,
            average_price=0,
            decision=None,
            account_info=KiwoomAccountInfo(
                True,
                ["1234567890"],
                server_type="실거래",
                message="연결 완료 1234-5678",
                connection_method="REST API",
            ),
            last_api_message="계좌 1234-5678 조회 완료",
        )

        status = TraderApp._format_main_status(snapshot)

        self.assertIn("REST API 연결됨(실거래)", status)
        self.assertIn("종목 002880 디와이아이", status)
        self.assertNotIn("MOCK_CONNECTED", status)
        self.assertNotIn("내부 테스트", status)
        self.assertNotIn("1234-5678", status)

    def test_orders_api_candles_chronologically_for_the_main_chart(self):
        candles = [
            Candle(110, 90, 100, 98, timestamp="20260711120600"),
            Candle(108, 88, 96, 95, timestamp="20260711120000"),
            Candle(109, 89, 98, 96, timestamp="20260711120300"),
        ]

        ordered = TraderApp._chronological_candles(candles)

        self.assertEqual(
            [candle.timestamp for candle in ordered],
            ["20260711120000", "20260711120300", "20260711120600"],
        )

    def test_formats_watchlist_price_fields_and_direction(self):
        quote = WatchlistQuote(
            symbol="005930",
            name="삼성전자",
            current_price=72000,
            change=500,
            change_rate=0.7,
            volume=123456,
        )

        values = TraderApp._watchlist_values(quote)

        self.assertEqual(values[3:], ("72,000", "+500", "+0.70%", "123,456"))
        self.assertEqual(TraderApp._watchlist_tag(quote), "up")


if __name__ == "__main__":
    unittest.main()
