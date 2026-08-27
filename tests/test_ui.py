import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kiwoom_auto_trader.kiwoom_api import KiwoomAccountInfo
from kiwoom_auto_trader.models import (
    BalanceSummary,
    Candle,
    Holding,
    MarketQuote,
    MarketSessionStatus,
    RealTimeQuote,
    TradeExecution,
    TradingBaseline,
    VolumeRankQuote,
    WatchlistQuote,
)
from kiwoom_auto_trader.service import ServiceSnapshot
from kiwoom_auto_trader.ui import (
    KiwoomRestLoginDialog,
    KbManualTradeWindow,
    TraderApp,
    _balance_trade_capability,
    _balance_trade_capability_text,
    _account_access_confirmed,
    _account_password_input_allowed,
    _account_password_session_ready,
    _baseline_validation_message,
    _clamp_dmi_period,
    _clamp_window_opacity_percent,
    _compact_monitor_display,
    _format_hundred_eok_won,
    _holding_monitor_display,
    _parse_money_input,
    _parse_order_quantity,
    _percentage_input_allowed,
    _market_session_text,
    normalize_account_history,
    normalize_watchlist_layout,
    _regular_market_is_open,
)


class UiHelperTests(unittest.TestCase):
    def test_normalizes_recent_account_history_without_duplicates(self):
        self.assertEqual(
            normalize_account_history(
                ["6698-6208", "66986208", "1234-5678", "", "0000-1111"],
                limit=2,
            ),
            ["66986208", "12345678"],
        )

    def test_parses_chart_timestamps_for_actual_buy_sell_markers(self):
        parsed = TraderApp._chart_datetime("2026-07-21T14:35:53")

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.strftime("%Y%m%d%H%M%S"), "20260721143553")
        self.assertIsNone(TraderApp._chart_datetime("invalid"))

    def test_combines_actual_fills_with_local_requests_without_duplicates(self):
        app = SimpleNamespace()
        app._format_trade_history = lambda rows: TraderApp._format_trade_history(app, rows)
        app._format_combined_trade_history = (
            lambda executions, local_rows: TraderApp._format_combined_trade_history(
                app,
                executions,
                local_rows,
            )
        )
        executions = [
            TradeExecution(
                timestamp="2026-08-08T10:15:03",
                side="BUY",
                symbol="012200",
                symbol_name="계양전기",
                quantity=2,
                price=4_080,
                order_no="0000101",
                order_mode="키움 실거래",
            )
        ]
        local_rows = [
            (
                "2026-08-08T10:15:00",
                "BUY",
                "012200",
                "계양전기",
                2,
                4_080,
                8_160,
                1,
                "0000101",
                "REST API 실거래",
                "주문 접수",
            ),
            (
                "2026-08-08T10:16:00",
                "SELL",
                "012200",
                "계양전기",
                1,
                4_100,
                4_100,
                0,
                "",
                "REST API 실거래",
                "주문 실패",
            ),
        ]

        rows = TraderApp._format_combined_trade_history(app, executions, local_rows)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][7], "실패")
        self.assertEqual(rows[1][7], "체결")
        self.assertEqual(rows[1][8], "0000101")

        recent_rows = TraderApp._format_recent_order_activity(
            app,
            executions,
            local_rows,
        )
        self.assertEqual(recent_rows[0][0], "08-08 10:16:00")
        self.assertEqual(recent_rows[0][5], "실패")
        self.assertEqual(recent_rows[1][0], "08-08 10:15:03")
        self.assertEqual(recent_rows[1][5], "체결")

    def test_clamps_dmi_period_to_button_range(self):
        self.assertEqual(_clamp_dmi_period(-5), 1)
        self.assertEqual(_clamp_dmi_period(1), 1)
        self.assertEqual(_clamp_dmi_period(14), 14)
        self.assertEqual(_clamp_dmi_period(99), 99)
        self.assertEqual(_clamp_dmi_period(120), 99)
        self.assertEqual(_clamp_dmi_period("5D"), 14)

    def test_clamps_window_opacity_to_visible_range(self):
        self.assertEqual(_clamp_window_opacity_percent(0), 0)
        self.assertEqual(_clamp_window_opacity_percent(35), 35)
        self.assertEqual(_clamp_window_opacity_percent(72.6), 73)
        self.assertEqual(_clamp_window_opacity_percent(100), 100)
        self.assertEqual(_clamp_window_opacity_percent(150), 100)
        self.assertEqual(_clamp_window_opacity_percent("invalid"), 100)

    def test_kb_net_filled_quantities_ignore_unfilled_and_net_sells(self):
        rows = [
            {"side": "매수", "symbol": "288980", "qty": "1주", "status": "체결"},
            {"side": "매수", "symbol": "288980", "qty": "2주", "status": "미체결"},
            {"side": "매도", "symbol": "288980", "qty": "1주", "status": "체결"},
            {"side": "BUY", "symbol": "012200", "qty": "3주", "status": "부분체결"},
        ]

        self.assertEqual(
            KbManualTradeWindow._net_filled_quantities(rows),
            {"012200": 3},
        )

    def test_account_privacy_setting_masks_every_digit_except_last_two(self):
        masked_app = SimpleNamespace(account_mask_enabled=True)
        visible_app = SimpleNamespace(account_mask_enabled=False)

        self.assertEqual(
            TraderApp._privacy_account_label(masked_app, "6698-6208"),
            "****-**08",
        )
        self.assertEqual(
            TraderApp._privacy_account_label(visible_app, "6698-6208"),
            "6698-6208",
        )

    def test_formats_money_in_hundred_eok_won_units(self):
        self.assertEqual(_format_hundred_eok_won(10_000_000_000), "1.00")
        self.assertEqual(_format_hundred_eok_won(123_456_000_000), "12.35")
        self.assertEqual(_format_hundred_eok_won(0), "-")

    def test_applies_selected_opacity_only_to_the_main_window(self):
        display = MagicMock()
        app = SimpleNamespace(
            window_opacity_display_var=display,
            wm_attributes=MagicMock(),
        )

        TraderApp._apply_window_opacity(app, 72.6)

        display.set.assert_called_once_with("73%")
        app.wm_attributes.assert_called_once_with("-alpha", 0.73)

    def test_compact_monitor_prefers_matching_realtime_price_and_rise(self):
        snapshot = SimpleNamespace(
            symbol="012200",
            symbol_name="계양전기",
            running=True,
            price=4_080,
            market_quote=MarketQuote(
                symbol="012200",
                name="계양전기",
                current_price=4_090,
                change_rate=0.25,
            ),
            real_time_quote=RealTimeQuote(
                symbol="012200",
                current_price=4_100,
                change=20,
                change_rate=0.49,
            ),
        )

        stock, price, trend, direction = _compact_monitor_display(snapshot)

        self.assertEqual(stock, "감시중 · 계양전기 · 012200")
        self.assertEqual(price, "4,100원")
        self.assertEqual(trend, "▲ 상승 +0.49%")
        self.assertEqual(direction, "up")

    def test_compact_monitor_uses_market_quote_for_falling_stock(self):
        snapshot = SimpleNamespace(
            symbol="005930",
            symbol_name="삼성전자",
            running=False,
            price=72_000,
            market_quote=MarketQuote(
                symbol="005930",
                name="삼성전자",
                current_price=71_500,
                change=-500,
                change_rate=-0.69,
            ),
            real_time_quote=None,
        )

        stock, price, trend, direction = _compact_monitor_display(snapshot)

        self.assertEqual(stock, "삼성전자 · 005930")
        self.assertEqual(price, "71,500원")
        self.assertEqual(trend, "▼ 하락 -0.69%")
        self.assertEqual(direction, "down")

    def test_holding_monitor_turns_blue_only_for_running_owned_symbol(self):
        balance = BalanceSummary(
            account="12345678",
            holdings=(Holding("012200", "계양전기", 3, 4_000, 4_100, 300, 2.5),),
        )

        active, detail = _holding_monitor_display(
            running=True,
            symbol="012200",
            symbol_name="계양전기",
            balance_summary=balance,
        )

        self.assertTrue(active)
        self.assertEqual(detail, "계양전기 주식 · 소프트웨어에서 3주 감시중")

        stopped, stopped_detail = _holding_monitor_display(
            running=False,
            symbol="012200",
            symbol_name="계양전기",
            balance_summary=balance,
        )
        self.assertFalse(stopped)
        self.assertIn("실시간 감시 중지", stopped_detail)

    def test_holding_monitor_stays_off_when_selected_stock_is_not_owned(self):
        balance = BalanceSummary(
            account="12345678",
            holdings=(Holding("005930", "삼성전자", 2, 70_000, 72_000, 4_000, 2.8),),
        )

        active, detail = _holding_monitor_display(
            running=True,
            symbol="012200",
            symbol_name="계양전기",
            balance_summary=balance,
        )

        self.assertFalse(active)
        self.assertEqual(detail, "계양전기 주식 · 보유수량 0주로 감시 대상 없음")

    def test_trade_capability_uses_cash_and_holdings_independently(self):
        no_cash_with_sellable_holding = BalanceSummary(
            account="12345678",
            deposit=0,
            orderable_amount=0,
            holdings=(Holding("005930", "삼성전자", 2, 70_000, 72_000, 0, 2.8),),
        )
        cash_only = BalanceSummary(
            account="12345678",
            deposit=100_000,
            orderable_amount=100_000,
            holdings=(),
        )

        self.assertEqual(_balance_trade_capability(no_cash_with_sellable_holding), (False, True))
        self.assertEqual(_balance_trade_capability(cash_only), (True, False))
        self.assertEqual(_balance_trade_capability_text(no_cash_with_sellable_holding), "매도 가능합니다")
        self.assertEqual(_balance_trade_capability_text(cash_only), "매수 가능합니다")

    def test_running_monitor_refreshes_rest_balance_without_log_spam(self):
        request_balance = MagicMock(return_value=BalanceSummary(account="12345678"))
        app = SimpleNamespace(
            service=SimpleNamespace(
                running=True,
                account_info=SimpleNamespace(connection_method="REST API"),
                request_balance=request_balance,
            ),
            _next_holding_balance_refresh_at=0.0,
            _account_connection_confirmed=lambda _info: True,
            _password_session_ready=lambda: True,
            _account_for_api=lambda: "12345678",
            _account_password_for_order=lambda: "",
        )

        refreshed = TraderApp._refresh_holding_balance_if_due(app, force=True)

        self.assertTrue(refreshed)
        request_balance.assert_called_once_with(
            "12345678",
            password="",
            log_result=False,
        )

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
        request_trade_history = MagicMock(return_value=[])
        app = SimpleNamespace(
            _require_live_connection=lambda: True,
            service=SimpleNamespace(
                account_info=SimpleNamespace(connection_method="OpenAPI+"),
                request_balance=request_balance,
                request_recent_trade_history=request_trade_history,
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
            _account_password_for_order=lambda: app.account_password_var.get(),
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
        request_trade_history.assert_called_once_with(
            "12345678",
            password="1234",
            days=10,
        )
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

    def test_formats_custom_watchlist_fields_in_hundred_eok_units(self):
        quote = WatchlistQuote(
            symbol="005930",
            name="삼성전자",
            current_price=72_000,
            change=500,
            change_rate=0.7,
            volume=123_456,
            trade_value=250_000_000_000,
            previous_trade_value=200_000_000_000,
            market_cap=5_000_000_000_000,
            program_trading_trend=-30_000_000_000,
        )

        values = TraderApp._watchlist_row_values(quote)

        self.assertEqual(values[4], "25.00")
        self.assertEqual(values[5], "20.00")
        self.assertEqual(values[9], "500.00")
        self.assertEqual(values[10], "5.00%")
        self.assertEqual(values[11], "-3.00")

    def test_normalizes_visible_watchlist_fields_and_column_order(self):
        visible, order = normalize_watchlist_layout(
            ["trade_value", "symbol", "unknown"],
            ["trade_value", "symbol", "name"],
        )

        self.assertEqual(visible, ["trade_value", "symbol", "name"])
        self.assertEqual(order[:3], ["trade_value", "symbol", "name"])

    def test_announces_only_new_actual_trade_executions(self):
        app = SimpleNamespace(
            _known_execution_keys=None,
            voice_notifier=MagicMock(),
            service=SimpleNamespace(storage=MagicMock()),
        )
        app.voice_notifier.announce_execution.return_value = True
        app._execution_voice_key = TraderApp._execution_voice_key
        existing = TradeExecution(
            timestamp="2026-08-01T09:10:00",
            side="BUY",
            symbol="005930",
            symbol_name="삼성전자",
            quantity=1,
            price=72_000,
            order_no="1001",
        )
        newest = TradeExecution(
            timestamp="2026-08-01T09:11:00",
            side="SELL",
            symbol="005930",
            symbol_name="삼성전자",
            quantity=1,
            price=72_500,
            order_no="1002",
        )

        TraderApp._announce_new_trade_executions(app, [existing])
        TraderApp._announce_new_trade_executions(app, [newest, existing])
        TraderApp._announce_new_trade_executions(app, [newest, existing])

        app.voice_notifier.announce_execution.assert_called_once_with("SELL")

    def test_formats_volume_ranking_rows_and_direction(self):
        quote = VolumeRankQuote(
            rank=1,
            symbol="005930",
            name="삼성전자",
            current_price=72000,
            change_rate=-1.25,
            change=-900,
            change_sign="5",
            volume=12_345_678,
            trade_value=123_456_000_000,
            market_cap=789_010_000_000,
        )

        values = TraderApp._volume_rank_values(quote)

        self.assertEqual(
            values,
            (
                1,
                "005930",
                "삼성전자",
                "72,000",
                "▼ 1.25%",
                "12,345,678",
            ),
        )
        self.assertEqual(TraderApp._volume_rank_tag(quote), "down")
        self.assertEqual(
            TraderApp._trade_value_rank_values(quote),
            (
                1,
                "005930",
                "삼성전자",
                "72,000",
                "▼ 1.25%",
                "12.35",
                "-900",
            ),
        )

    def test_uses_official_price_change_markers_for_rankings(self):
        upper = VolumeRankQuote(rank=1, symbol="005930", change_sign="1")
        rising = VolumeRankQuote(rank=2, symbol="005930", change_sign="2")
        lower = VolumeRankQuote(rank=3, symbol="005930", change_sign="4")
        falling = VolumeRankQuote(rank=4, symbol="005930", change_sign="5")

        self.assertEqual(TraderApp._rank_change_marker(upper), "↑")
        self.assertEqual(TraderApp._rank_change_marker(rising), "▲")
        self.assertEqual(TraderApp._rank_change_marker(lower), "↓")
        self.assertEqual(TraderApp._rank_change_marker(falling), "▼")
        self.assertEqual(TraderApp._volume_rank_tag(upper), "up")
        self.assertEqual(TraderApp._volume_rank_tag(lower), "down")


if __name__ == "__main__":
    unittest.main()
