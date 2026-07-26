import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kiwoom_auto_trader.kiwoom_api import KiwoomAccountInfo
from kiwoom_auto_trader.models import (
    BalanceSummary,
    Candle,
    MarketSessionStatus,
    TradingBaseline,
    WatchlistQuote,
)
from kiwoom_auto_trader.price_triggers import OneShotPriceTriggerBook
from kiwoom_auto_trader.service import ServiceSnapshot
from kiwoom_auto_trader.ui import (
    KiwoomRestLoginDialog,
    TraderApp,
    _account_access_confirmed,
    _account_password_input_allowed,
    _account_password_session_ready,
    _automatic_trade_readiness,
    _baseline_validation_message,
    _clamp_dmi_period,
    _parse_money_input,
    _parse_order_quantity,
    _percentage_input_allowed,
    _market_session_text,
    _regular_market_is_open,
)


class UiHelperTests(unittest.TestCase):
    def test_clamps_dmi_period_to_button_range(self):
        self.assertEqual(_clamp_dmi_period(-5), 1)
        self.assertEqual(_clamp_dmi_period(1), 1)
        self.assertEqual(_clamp_dmi_period(14), 14)
        self.assertEqual(_clamp_dmi_period(99), 99)
        self.assertEqual(_clamp_dmi_period(120), 99)
        self.assertEqual(_clamp_dmi_period("5D"), 14)

    def test_automatic_trade_readiness_is_independent_of_market_hours(self):
        ready, missing = _automatic_trade_readiness(
            account_ready=True,
            symbol_ready=True,
            quantity_ready=True,
            baseline_ready=True,
            authorization_ready=True,
            automation_configured=True,
        )

        self.assertTrue(ready)
        self.assertEqual(missing, ())

        ready, missing = _automatic_trade_readiness(
            account_ready=True,
            symbol_ready=False,
            quantity_ready=False,
            baseline_ready=True,
            authorization_ready=True,
            automation_configured=True,
        )

        self.assertFalse(ready)
        self.assertEqual(missing, ("종목 세팅", "주문 수량"))

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

    def test_password_session_requires_matching_openapi_account(self):
        self.assertTrue(
            _account_password_session_ready("OpenAPI+", "12345678", "12345678", "1234")
        )
        self.assertFalse(
            _account_password_session_ready("OpenAPI+", "12345678", "87654321", "1234")
        )
        self.assertFalse(
            _account_password_session_ready("OpenAPI+", "12345678", "12345678", "12ab")
        )
        self.assertTrue(_account_password_session_ready("REST API", "12345678", "", ""))

    def test_password_setting_keeps_verified_value_in_process_session(self):
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
            _session_password_account="",
            _suppress_password_trace=False,
            _update_connection_badge=MagicMock(),
            status_text=Variable(),
            update_idletasks=MagicMock(),
            _account_for_api=lambda: "12345678",
            _refresh=MagicMock(),
            _show_account_info_window=MagicMock(),
        )

        def clear_session(status="미확인", clear_entry=True):
            app._session_password_account = ""
            app._account_access_verified = False
            if clear_entry:
                app.account_password_var.set("")
            app.account_password_status_var.set(status)

        app._clear_account_password_session = clear_session

        TraderApp._set_account_password(app)

        request_balance.assert_called_once_with("12345678", "1234")
        self.assertEqual(app.account_password_var.get(), "1234")
        self.assertEqual(app._session_password_account, "12345678")
        self.assertEqual(app.account_password_status_var.get(), "확인됨")
        self.assertTrue(app._account_access_verified)

    def test_password_controls_activate_only_after_account_number_loads(self):
        class Variable:
            def __init__(self, value=""):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        selected_account = {"value": ""}
        entry = MagicMock()
        button = MagicMock()
        app = SimpleNamespace(
            account_password_var=Variable(""),
            account_password_status_var=Variable("미확인"),
            account_password_entry=entry,
            account_password_button=button,
            _account_access_verified=False,
            _session_password_account="",
            _account_for_api=lambda: selected_account["value"],
            _password_session_ready=lambda: False,
        )

        def clear_session(status="미확인", clear_entry=True):
            app._account_access_verified = False
            app._session_password_account = ""
            if clear_entry:
                app.account_password_var.set("")
            app.account_password_status_var.set(status)

        app._clear_account_password_session = clear_session
        info = SimpleNamespace(connected=True, connection_method="OpenAPI+")

        TraderApp._sync_account_password_controls(app, info)

        entry.configure.assert_called_with(state="disabled")
        button.configure.assert_called_with(state="disabled", text="비밀번호 세팅")
        self.assertEqual(app.account_password_status_var.get(), "계좌 대기")

        entry.reset_mock()
        button.reset_mock()
        selected_account["value"] = "12345678"
        TraderApp._sync_account_password_controls(app, info)

        entry.configure.assert_called_with(state="normal")
        button.configure.assert_called_with(state="normal", text="비밀번호 세팅")

    def test_editing_verified_password_immediately_locks_orders(self):
        class Variable:
            def __init__(self, value=""):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        app = SimpleNamespace(
            _suppress_password_trace=False,
            account_password_var=Variable("5678"),
            account_password_status_var=Variable("확인됨"),
            _session_password_account="12345678",
            _account_access_verified=True,
            _update_connection_badge=MagicMock(),
            _update_trade_buttons=MagicMock(),
        )

        TraderApp._on_account_password_changed(app)

        self.assertEqual(app._session_password_account, "")
        self.assertFalse(app._account_access_verified)
        self.assertEqual(app.account_password_status_var.get(), "입력 중")
        app._update_connection_badge.assert_called_once_with(False)
        app._update_trade_buttons.assert_called_once_with()

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

    def test_labels_live_rest_account_as_session_approved_order_capable(self):
        info = KiwoomAccountInfo(
            True,
            ["1234567890"],
            server_type="실거래",
            connection_method="REST API",
        )

        label = TraderApp._account_capability_label(info)

        self.assertIn("실주문", label)
        self.assertIn("세션 승인", label)
        self.assertNotIn("주문 잠금", label)

    def test_recognizes_only_official_regular_market_open_code(self):
        opened = MarketSessionStatus("3", event_time="090000")
        waiting = MarketSessionStatus("0", event_time="085900")

        self.assertTrue(_regular_market_is_open(opened))
        self.assertFalse(_regular_market_is_open(waiting))
        self.assertFalse(_regular_market_is_open(None))

    def test_market_status_identifies_0b_trade_confirmation_and_stale_wait(self):
        status = MarketSessionStatus(
            "3",
            event_time="132014",
            source="키움 REST 주식체결(0B) 장구분 2",
        )

        self.assertEqual(
            _market_session_text(status, True),
            "정규장 장중(실시간 체결 확인)",
        )
        self.assertEqual(
            _market_session_text(status, False),
            "장중 실시간 체결 갱신 대기",
        )
        self.assertEqual(
            _market_session_text(None, False, True),
            "실시간 등록 완료·다음 체결 대기",
        )

    def test_live_rest_price_setting_arms_session_without_mock_connection(self):
        class Variable:
            def __init__(self, value=""):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        account_info = SimpleNamespace(connection_method="REST API", server_type="실거래")
        service = SimpleNamespace(
            account_info=account_info,
            real_time_symbol="005930",
            storage=SimpleNamespace(log=MagicMock()),
            configure=MagicMock(),
            request_daily_dmi_candles=MagicMock(),
            latest_dmi=SimpleNamespace(pattern_state="BEARISH"),
            pattern_state="BEARISH",
            strategy=SimpleNamespace(settings=SimpleNamespace(dmi_period=14)),
        )
        app = SimpleNamespace(
            service=service,
            symbol_var=Variable("005930"),
            buy_percent_var=Variable(""),
            sell_percent_var=Variable("0.2"),
            allow_real_order_var=Variable(False),
            price_triggers=OneShotPriceTriggerBook(),
            _real_order_session_armed=False,
            _require_live_connection=lambda: True,
            _selected_symbol_ready=lambda: True,
            _account_connection_confirmed=lambda _info: True,
            _password_session_ready=lambda: True,
            _selected_trading_baseline=lambda: TradingBaseline(
                "005930",
                1_000_000,
                70_000,
                "2026-07-19",
            ),
            _order_quantity=lambda: 2,
            _account_for_api=lambda: "12345678",
            _operating_capital=lambda: 1_000_000,
            _settings=lambda: None,
            _real_order_session_ready=lambda: False,
            _update_price_trigger_status=MagicMock(),
            _refresh=MagicMock(),
            _clear_real_order_authorization=MagicMock(),
        )

        with patch("kiwoom_auto_trader.ui.messagebox.askyesno", return_value=True):
            TraderApp._arm_price_trigger(app, "SELL")

        trigger = app.price_triggers.get("SELL")
        self.assertIsNotNone(trigger)
        self.assertTrue(trigger.allow_real_order)
        self.assertEqual(trigger.account, "12345678")
        self.assertEqual(trigger.target_price, 69_860)
        self.assertTrue(app._real_order_session_armed)
        self.assertTrue(app.allow_real_order_var.get())
        service.storage.log.assert_any_call(
            "WARN",
            "주문",
            "1234-5678 실거래 자동주문 세션을 가격 조건 설정과 함께 승인했습니다.",
        )

    def test_live_price_setting_waits_without_being_consumed_before_market_open(self):
        triggers = OneShotPriceTriggerBook()
        triggers.arm(
            "BUY",
            "005930",
            70_000,
            0.2,
            2,
            allow_real_order=True,
            account="12345678",
        )
        app = SimpleNamespace(
            _processing_price_triggers=False,
            _real_trading_account=lambda: True,
            _regular_market_open=lambda: False,
            price_triggers=triggers,
        )

        processed = TraderApp._process_one_shot_price_triggers(app)

        self.assertFalse(processed)
        self.assertIsNotNone(triggers.get("BUY"))

    def test_open_market_crossed_targets_send_automatic_buy_and_sell(self):
        class Variable:
            def __init__(self, value=""):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        for side, current_price in (("BUY", 4_165), ("SELL", 4_125)):
            with self.subTest(side=side):
                triggers = OneShotPriceTriggerBook()
                triggers.arm(
                    side,
                    "012200",
                    4_130,
                    0.02 if side == "BUY" else 0.1,
                    2,
                    allow_real_order=True,
                    account="12345678",
                )
                service = SimpleNamespace(
                    account_info=SimpleNamespace(
                        connection_method="REST API",
                        server_type="실거래",
                    ),
                    storage=SimpleNamespace(log=MagicMock()),
                    pattern_state="BULLISH" if side == "BUY" else "BEARISH",
                    configure=MagicMock(),
                    current_price=0,
                    send_kiwoom_order=MagicMock(return_value="주문 접수 완료"),
                )
                app = SimpleNamespace(
                    service=service,
                    _processing_price_triggers=False,
                    _real_trading_account=lambda: True,
                    _regular_market_open=lambda: True,
                    _selected_current_price=lambda: current_price,
                    price_triggers=triggers,
                    symbol_var=Variable("012200"),
                    buy_percent_var=Variable("0.02"),
                    sell_percent_var=Variable("0.1"),
                    _update_price_trigger_status=MagicMock(),
                    _account_connection_confirmed=lambda _info: True,
                    _account_for_api=lambda: "12345678",
                    _real_order_session_ready=lambda: True,
                    _operating_capital=lambda: 70_000,
                    _settings=lambda: None,
                    _account_password_for_order=lambda: "",
                    _handle_order_account_verification=MagicMock(),
                )

                processed = TraderApp._process_one_shot_price_triggers(app)

                self.assertTrue(processed)
                self.assertIsNone(triggers.get(side))
                service.send_kiwoom_order.assert_called_once_with(
                    account="12345678",
                    side=side,
                    quantity=2,
                    allow_real_order=True,
                    account_password="",
                )

    def test_crossed_price_waits_until_dmi_direction_matches(self):
        class Variable:
            def __init__(self, value=""):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        triggers = OneShotPriceTriggerBook()
        triggers.arm(
            "BUY",
            "012200",
            4_130,
            0.02,
            2,
            allow_real_order=True,
            account="12345678",
        )
        service = SimpleNamespace(
            account_info=SimpleNamespace(connection_method="REST API", server_type="실거래"),
            pattern_state="BEARISH",
            storage=SimpleNamespace(log=MagicMock()),
            send_kiwoom_order=MagicMock(),
        )
        app = SimpleNamespace(
            service=service,
            _processing_price_triggers=False,
            _real_trading_account=lambda: True,
            _regular_market_open=lambda: True,
            _selected_current_price=lambda: 4_165,
            price_triggers=triggers,
            symbol_var=Variable("012200"),
        )

        processed = TraderApp._process_one_shot_price_triggers(app)

        self.assertFalse(processed)
        self.assertIsNotNone(triggers.get("BUY"))
        service.send_kiwoom_order.assert_not_called()

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
