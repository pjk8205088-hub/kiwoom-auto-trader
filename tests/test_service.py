import tempfile
import unittest
from pathlib import Path

from kiwoom_auto_trader.kiwoom_api import KiwoomAccountInfo
from kiwoom_auto_trader.models import Candle, StrategySettings
from kiwoom_auto_trader.rest_api import KiwoomRestApiError
from kiwoom_auto_trader.service import AutoTradingService
from kiwoom_auto_trader.storage import Storage


class FakeAccountApi:
    def __init__(
        self,
        user_id: str,
        connected: bool = True,
        reported_account_count: int = 1,
    ) -> None:
        self.user_id = user_id
        self.connected = connected
        self.reported_account_count = reported_account_count

    def start_login(self) -> str:
        return "이미 키움 OpenAPI에 연결되어 있습니다."

    def get_account_info(self) -> KiwoomAccountInfo:
        if not self.connected:
            return KiwoomAccountInfo(False, [], message="키움 OpenAPI에 연결되지 않았습니다.")
        message = (
            "모의투자 계좌 1개를 불러왔습니다."
            if self.reported_account_count == 1
            else "키움 로그인 정보 수신 확인에 실패했습니다."
        )
        return KiwoomAccountInfo(
            True,
            ["1234567890"],
            user_id=self.user_id,
            user_name="테스트사용자",
            server_type="모의투자",
            message=message,
            reported_account_count=self.reported_account_count,
            login_event_code=0,
        )

    def login_status_message(self) -> str:
        return "키움 OpenAPI에 연결되지 않았습니다."

    def is_connected(self) -> bool:
        return self.connected


class FakeRestApi:
    def __init__(self) -> None:
        self.connected = False
        self.mock = True
        self.sell_failures_remaining = 0
        self.order_calls = 0
        self.info = KiwoomAccountInfo(
            False,
            [],
            server_type="모의투자",
            connection_method="REST API",
        )

    def connect(self, app_key: str, secret_key: str) -> KiwoomAccountInfo:
        if app_key != "app-key" or secret_key != "secret-key":
            raise AssertionError("테스트 키가 전달되지 않았습니다.")
        self.connected = True
        server_type = "모의투자" if self.mock else "실거래"
        self.info = KiwoomAccountInfo(
            True,
            ["1234567890"],
            user_id="REST API 토큰 인증",
            server_type=server_type,
            message=f"REST API {server_type} 연결 완료",
            reported_account_count=1,
            login_event_code=0,
            connection_method="REST API",
        )
        return self.info

    def get_account_info(self) -> KiwoomAccountInfo:
        return self.info

    def is_connected(self) -> bool:
        return self.connected

    def login_status_message(self) -> str:
        return self.info.message

    def clear_session(self) -> None:
        self.connected = False
        self.info = KiwoomAccountInfo(
            False,
            [],
            server_type="모의투자" if self.mock else "실거래",
            message="REST API 세션 종료",
            connection_method="REST API",
        )

    def send_order(self, request) -> str:
        self.order_calls += 1
        if request.side == "SELL" and self.sell_failures_remaining > 0:
            self.sell_failures_remaining -= 1
            raise KiwoomRestApiError("테스트용 모의 매도 요청 실패")
        return "REST 모의 주문 접수 완료"


class AutoTradingServiceTests(unittest.TestCase):
    def test_configure_preserves_strategy_state_when_settings_do_not_change(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        settings = StrategySettings(use_cci_filter=False)

        service.configure("005930", 1_000_000, settings)
        service.strategy.previous_pattern = "BULLISH"
        service.configure("005930", 1_000_000, settings)

        self.assertEqual(service.strategy.previous_pattern, "BULLISH")

    def test_requires_app_id_to_match_openapi_login_id(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_id_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        service.kiwoom_api = FakeAccountApi("other-user")

        service.start_account_connection("expected-user")
        info = service.refresh_account_connection()

        self.assertFalse(info.connected)
        self.assertEqual(info.accounts, [])
        self.assertIn("일치하지 않아", info.message)

    def test_live_disconnect_stops_trading_and_clears_account(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_disconnect_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        fake_api = FakeAccountApi("expected-user")
        service.kiwoom_api = fake_api
        service.start_account_connection("expected-user")
        self.assertTrue(service.refresh_account_connection().connected)
        service.start()
        self.assertFalse(service.order_manager.stop_requested)

        fake_api.connected = False
        connected = service.sync_account_connection()

        self.assertFalse(connected)
        self.assertFalse(service.account_info.connected)
        self.assertFalse(service.running)
        self.assertTrue(service.order_manager.stop_requested)
        self.assertIn("주문을 중지", service.account_info.message)

    def test_rejects_connected_session_with_incomplete_account_data(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_account_count_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        service.kiwoom_api = FakeAccountApi("expected-user", reported_account_count=2)

        service.start_account_connection("expected-user")
        info = service.refresh_account_connection()

        self.assertFalse(info.connected)
        self.assertEqual(info.accounts, [])
        self.assertIn("수신 확인", info.message)

    def test_connects_rest_mock_account_and_switches_active_mode(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_rest_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        service.rest_api = FakeRestApi()

        info = service.start_rest_connection("app-key", "secret-key")

        self.assertTrue(info.connected)
        self.assertEqual(service.connection_mode, "REST")
        self.assertEqual(info.connection_method, "REST API")
        self.assertEqual(info.accounts, ["1234567890"])
        self.assertTrue(service.sync_account_connection())

        service.rest_api.connected = False
        self.assertFalse(service.sync_account_connection())
        self.assertFalse(service.account_info.connected)

    def test_connects_rest_live_account_in_read_only_server_mode(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_rest_live_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        service.rest_api = FakeRestApi()

        info = service.start_rest_connection("app-key", "secret-key", mock=False)

        self.assertTrue(info.connected)
        self.assertFalse(service.rest_api.mock)
        self.assertEqual(info.server_type, "실거래")

    def test_market_strategy_uses_explicit_pattern_with_real_candles(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_pattern_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        service.configure("005930", 1_000_000, StrategySettings(use_cci_filter=False))
        candles = [Candle(high=101, low=99, close=100) for _ in range(20)]
        service.request_three_minute_candles = lambda _symbol=None: candles

        buy = service.evaluate_strategy_with_market_data("005930", "BULLISH")
        sell = service.evaluate_strategy_with_market_data("005930", "BEARISH")

        self.assertEqual(buy.action, "BUY")
        self.assertEqual(sell.action, "SELL")

    def test_retries_mock_sell_request_but_not_buy(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_sell_retry_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        fake_rest = FakeRestApi()
        service.rest_api = fake_rest
        service.start_rest_connection("app-key", "secret-key")
        fake_rest.sell_failures_remaining = 2

        message = service.send_kiwoom_order("1234567890", "SELL", 1)

        self.assertEqual(fake_rest.order_calls, 3)
        self.assertIn("접수 완료", message)
        fake_rest.order_calls = 0
        service.send_kiwoom_order("1234567890", "BUY", 1)
        self.assertEqual(fake_rest.order_calls, 1)

    def test_blocks_manual_buy_over_configured_capital_limit(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_cap_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        fake_rest = FakeRestApi()
        service.rest_api = fake_rest
        service.start_rest_connection("app-key", "secret-key")
        service.current_price = 100_000
        service.max_capital = 500_000

        message = service.send_kiwoom_order("1234567890", "BUY", 6)

        self.assertEqual(fake_rest.order_calls, 0)
        self.assertIn("최대 5주", message)


if __name__ == "__main__":
    unittest.main()
