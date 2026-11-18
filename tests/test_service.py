import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from kiwoom_auto_trader.kiwoom_api import KiwoomAccountInfo, KiwoomOpenApiError
from kiwoom_auto_trader.models import (
    BalanceSummary,
    Candle,
    Holding,
    RealTimeQuote,
    StrategySettings,
    TradeDecision,
    WatchlistQuote,
)
from kiwoom_auto_trader.rest_api import KiwoomRestApiError
from kiwoom_auto_trader.service import AutoTradingService
from kiwoom_auto_trader.storage import Storage
from kiwoom_auto_trader.strategy_profile_schema import new_profile


def dmi_buy_transition_candles() -> list[Candle]:
    return [
        Candle(high=10, low=8, close=9, timestamp="20260711090000"),
        Candle(high=9, low=7, close=8, timestamp="20260711090300"),
        Candle(high=8, low=6, close=7, timestamp="20260711090600"),
        Candle(high=7, low=5, close=6, timestamp="20260711090900"),
        Candle(high=8, low=6, close=7, timestamp="20260711091200"),
        Candle(high=9, low=7, close=8, timestamp="20260711091500"),
    ]


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
        self.order_requests = []
        self.balance_calls = []
        self.balance_summary = BalanceSummary(
            account="1234567890",
            deposit=2_000_000,
            orderable_amount=2_000_000,
        )
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
        self.order_requests.append(request)
        if request.side == "SELL" and self.sell_failures_remaining > 0:
            self.sell_failures_remaining -= 1
            raise KiwoomRestApiError("테스트용 모의 매도 요청 실패")
        mode = "실거래" if request.allow_real_order else "모의"
        return f"REST {mode} 주문 접수 완료"

    def request_balance(self, account: str, password: str = "") -> BalanceSummary:
        self.balance_calls.append((account, password))
        return self.balance_summary


class FakeOpenApiOrderApi:
    def __init__(self) -> None:
        self.balance_calls = []
        self.order_requests = []

    def request_balance(self, account: str, password: str = "") -> BalanceSummary:
        self.balance_calls.append((account, password))
        if password != "9876":
            raise KiwoomOpenApiError("계좌 비밀번호 확인 실패")
        return BalanceSummary(
            account=account,
            deposit=1_000_000,
            orderable_amount=1_000_000,
        )

    def send_order(self, request) -> str:
        self.order_requests.append(request)
        return "OpenAPI+ 모의 주문 접수 완료"


class FakeChartApi:
    def __init__(self) -> None:
        self.intervals = []

    def request_minute_candles(self, symbol, interval=3, count=120):
        self.intervals.append((symbol, interval, count))
        return [
            Candle(
                high=105,
                low=95,
                close=102,
                open=100,
                volume=10,
                timestamp="20260711100000",
            )
        ]


class FakeRealtimeApi:
    def __init__(self) -> None:
        self.quotes = [
            RealTimeQuote("005930", 100, volume=2, timestamp="20260711101530"),
            RealTimeQuote("005930", 103, volume=3, timestamp="20260711101530"),
        ]

    def pump_messages(self):
        return

    def drain_real_time_quotes(self, symbol):
        quotes = [quote for quote in self.quotes if quote.symbol == symbol]
        self.quotes = []
        return quotes

    def latest_real_time_quote(self, symbol):
        if self.quotes and self.quotes[-1].symbol == symbol:
            return self.quotes[-1]
        return RealTimeQuote(symbol, 103, volume=3, timestamp="20260711101530")


class FakeRestTickApi:
    def __init__(self) -> None:
        self.tick_requests = []
        self.live_quotes = [
            RealTimeQuote("005930", 106, volume=5, timestamp="20260711101532")
        ]
        self.latest_quote = self.live_quotes[-1]

    def request_tick_candles(self, symbol, count=3000):
        self.tick_requests.append((symbol, count))
        return [
            Candle(104, 104, 104, 104, 4, "20260711101531"),
            Candle(103, 103, 103, 103, 3, "20260711101530"),
            Candle(100, 100, 100, 100, 2, "20260711101530"),
        ]

    def pump_messages(self):
        return

    def is_real_time_registered(self):
        return True

    def drain_real_time_quotes(self, symbol):
        quotes = [quote for quote in self.live_quotes if quote.symbol == symbol]
        self.live_quotes = []
        return quotes

    def latest_real_time_quote(self, symbol):
        return self.latest_quote if self.latest_quote.symbol == symbol else None


class FakeWatchlistApi:
    def request_watchlist_quotes(self, symbols):
        return [
            WatchlistQuote(
                symbol=symbol,
                name="삼성전자" if symbol == "005930" else "삼성전기",
                current_price=72000,
                change=500,
                change_rate=0.7,
                volume=123456,
            )
            for symbol in symbols
        ]


class AutoTradingServiceTests(unittest.TestCase):
    def test_live_profile_tick_can_trigger_order_between_chart_polls(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(storage=Storage(Path(directory) / "live-tick.sqlite3"))
            service.running = True
            service.max_capital = service.total_capital_limit = 1000
            service.account_info = KiwoomAccountInfo(True, ["1234567890"], server_type="모의투자")
            service.balance_summary = BalanceSummary(
                account="1234567890", deposit=1000, orderable_amount=1000
            )
            today = datetime.now().strftime("%Y%m%d")
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
            service._watch_minute_history["005930"] = (1, [
                Candle(open=100, high=100, low=99, close=99, timestamp=today + "090000"),
                Candle(open=99, high=100, low=99, close=99, timestamp=today + "090100"),
            ])
            service._watch_daily_history["005930"] = (
                today, [Candle(high=99, low=97, close=98, timestamp=yesterday)]
            )
            service.watchlist_quotes["005930"] = WatchlistQuote(
                symbol="005930", name="삼성전자", current_price=99,
                open_price=100, low_price=99,
            )

            class Api:
                last_order_no = "TEST-2"
                orders = []

                def latest_real_time_quote(self, symbol):
                    return RealTimeQuote(symbol, current_price=101, timestamp=today + "090200")

                def request_balance(self, account, password=""):
                    return BalanceSummary(account=account, deposit=1000, orderable_amount=1000)

                def send_order(self, request):
                    self.orders.append(request)
                    return "모의 실시간 주문 접수"

            api = Api()
            service.kiwoom_api = api
            profile = new_profile(0)
            profile["pdf"].update(enabled=True, shortOnly=True, dailyMaEnabled=False,
                                  conditionCombine="OR")
            profile["bar"]["minute"].update(shortPeriod=2, longPeriod=3)
            settings = {"005930": StrategySettings(profile=profile)}

            first = service.execute_profile_tick_orders(settings, "1234567890", account_password="1234")
            second = service.execute_profile_tick_orders(settings, "1234567890", account_password="1234")

            self.assertIn("005930", first)
            self.assertEqual(second, {})
            self.assertEqual(len(api.orders), 1)
            self.assertEqual(service._watch_minute_history["005930"][1][-1].close, 101)

    def test_profile_order_flow_uses_configured_symbol_without_watchlist_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(storage=Storage(Path(directory) / "profile-flow.sqlite3"))
            service.running = True
            service.symbol = "035420"
            service.max_capital = service.total_capital_limit = 1000
            service.account_info = KiwoomAccountInfo(
                True, ["1234567890"], server_type="모의투자"
            )
            today = datetime.now().strftime("%Y%m%d")
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
            bars = [
                Candle(open=100, high=100, low=99, close=99, timestamp=today + "090000"),
                Candle(open=99, high=100, low=99, close=99, timestamp=today + "090100"),
                Candle(open=99, high=102, low=99, close=101, timestamp=today + "090200"),
            ]

            class Api:
                last_order_no = "TEST-1"
                orders = []

                def request_balance(self, account, password=""):
                    return BalanceSummary(account=account, deposit=1000, orderable_amount=1000)

                def request_watchlist_quotes(self, symbols):
                    return [WatchlistQuote(symbol="005930", name="삼성전자", current_price=101)]

                def request_minute_candles(self, symbol, interval=1, count=120):
                    return list(reversed(bars))

                def request_daily_candles(self, symbol, count=61):
                    return [Candle(high=99, low=97, close=98, timestamp=yesterday)]

                def send_order(self, request):
                    self.orders.append(request)
                    return "모의 주문 접수"

            api = Api()
            service.kiwoom_api = api
            profile = new_profile(0)
            profile["pdf"].update(enabled=True, shortOnly=True, dailyMaEnabled=False,
                                  conditionCombine="OR")
            profile["bar"]["minute"].update(shortPeriod=2, longPeriod=3)
            settings = {"005930": StrategySettings(profile=profile)}

            first = service.execute_profile_orders(settings, "1234567890", account_password="1234")
            second = service.execute_profile_orders(settings, "1234567890", account_password="1234")

            self.assertIn("005930", first)
            self.assertEqual(second, {})
            self.assertEqual(len(api.orders), 1)
            self.assertEqual(api.orders[0].symbol, "005930")
            self.assertEqual(service.symbol, "035420")
            self.assertTrue(service.storage.successful_order_on_day(
                "005930", "BUY", datetime.now().date().isoformat()
            ))

    def test_register_profile_realtime_does_not_replace_selected_symbol(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(storage=Storage(Path(directory) / "realtime-profiles.sqlite3"))
            service.symbol = "035420"
            service.kiwoom_api.register_real_time_prices = lambda symbols: f"{len(symbols)}종목 등록"

            message = service.register_profile_realtime(("005930", "000660"))

            self.assertIn("2종목", message)
            self.assertEqual(service.profile_real_time_symbols, ("005930", "000660"))
            self.assertEqual(service.symbol, "035420")

    def test_monitor_chart_does_not_replace_selected_trading_symbol(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(storage=Storage(Path(directory) / "monitor-chart.sqlite3"))
            service.symbol = "035420"

            class ChartApi:
                def request_minute_candles(self, symbol, interval=1, count=120):
                    self.requested = (symbol, interval, count)
                    return list(reversed(dmi_buy_transition_candles()))

            api = ChartApi()
            service.kiwoom_api = api
            candles = service.request_monitor_candles("005930", 1)

            self.assertEqual(api.requested, ("005930", 1, 120))
            self.assertEqual(candles[0].timestamp, "20260711090000")
            self.assertEqual(service.symbol, "035420")

    def test_monitor_daily_averages_include_latest_intraday_close(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(storage=Storage(Path(directory) / "monitor-daily.sqlite3"))
            service.symbol = "035420"

            class ChartApi:
                def request_daily_candles(self, symbol, count=61):
                    return [
                        Candle(high=12, low=10, close=11, timestamp="20261113"),
                        Candle(high=11, low=9, close=10, timestamp="20261112"),
                    ]

            service.kiwoom_api = ChartApi()
            minute = [Candle(high=14, low=12, close=13, timestamp="20261116090100")]
            averages = service.request_monitor_daily_averages("005930", minute, (2, 3))
            self.assertEqual(averages, {2: 12.0, 3: 34 / 3})
            self.assertEqual(service.symbol, "035420")

    def test_profile_orders_require_live_authorization_and_deduplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(storage=Storage(Path(directory) / "profile-orders.sqlite3"))
            service.running = True
            service.account_info = KiwoomAccountInfo(
                True, ["1234567890"], server_type="실거래"
            )
            profile = new_profile(0)
            profile["pdf"]["enabled"] = True
            settings = {"005930": StrategySettings(profile=profile)}
            today = datetime.now().strftime("%Y%m%d")
            decision = TradeDecision("BUY", "조건 충족", "NONE", candle_timestamp=today + "090000")
            quote = WatchlistQuote(symbol="005930", name="삼성전자", current_price=70000)

            def successful_order(**kwargs):
                service.last_order_success = True
                return "모의 주문 성공"

            with patch.object(service, "request_balance", return_value=BalanceSummary(account="1234567890")), \
                 patch.object(service, "refresh_watchlist_quotes", side_effect=lambda _symbols: service.watchlist_quotes.update({"005930": quote})), \
                 patch.object(service, "evaluate_watch_symbols", return_value={"005930": decision}), \
                 patch.object(service, "order_candidates", return_value={"005930": "BUY"}), \
                 patch.object(service, "plan_candidate_quantities", return_value={"005930": 2}), \
                 patch.object(service, "send_kiwoom_order", side_effect=successful_order) as send:
                self.assertEqual(service.execute_profile_orders(settings, "1234567890"), {})
                self.assertEqual(send.call_count, 0)
                first = service.execute_profile_orders(
                    settings, "1234567890", allow_real_order=True, regular_market_open=True
                )
                second = service.execute_profile_orders(
                    settings, "1234567890", allow_real_order=True, regular_market_open=True
                )
                self.assertIn("005930", first)
                self.assertEqual(second, {})
                self.assertEqual(send.call_count, 1)
                self.assertEqual(send.call_args.kwargs["symbol"], "005930")

    def test_targeted_order_keeps_selected_symbol_and_price(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(storage=Storage(Path(directory) / "target-order.sqlite3"))
            fake_rest = FakeRestApi()
            service.rest_api = fake_rest
            service.start_rest_connection("app-key", "secret-key")
            service.symbol = "005930"
            service.symbol_name = "삼성전자"
            service.current_price = 72_000
            service.max_capital = 1_000_000
            service.total_capital_limit = 1_000_000

            service.send_kiwoom_order(
                "1234567890", "BUY", 1, symbol="000660",
                reference_price=100_000, symbol_name="SK하이닉스",
            )

            self.assertTrue(service.last_order_success)
            self.assertEqual(fake_rest.order_requests[-1].symbol, "000660")
            self.assertEqual(service.symbol, "005930")
            self.assertEqual(service.current_price, 72_000)
            self.assertEqual(service.storage.recent_orders(1)[0][1], "000660")

    def test_configured_quote_refresh_does_not_register_symbol_as_watchlist(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(storage=Storage(Path(directory) / "configured-quote.sqlite3"))
            service.kiwoom_api = FakeWatchlistApi()

            rows = service.refresh_watchlist_quotes(("005930",))

            self.assertEqual(rows, [])
            self.assertEqual(service.watchlist_quotes["005930"].current_price, 72000)
            self.assertEqual(service.watchlist_items(), [])

    def test_failed_balance_refresh_discards_stale_holdings(self):
        class FailingBalanceApi:
            def request_balance(self, account, password=""):
                raise KiwoomOpenApiError("잔고 조회 실패")

        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(storage=Storage(Path(directory) / "balance-fail.sqlite3"))
            service.kiwoom_api = FailingBalanceApi()
            service.balance_summary = BalanceSummary(
                account="1234567890",
                holdings=(Holding("005930", "삼성전자", 3, 10, 8, -6, -20),),
            )

            self.assertIsNone(service.request_balance("1234567890", password="1234"))
            self.assertIsNone(service.balance_summary)

    def test_candidate_quantity_plan_respects_total_funds_and_holdings(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(storage=Storage(Path(directory) / "plan.sqlite3"))
            service.total_capital_limit = 1_000_000
            service.balance_summary = BalanceSummary(
                account="1234567890", orderable_amount=800_000,
                total_evaluation=600_000,
                holdings=(Holding("005930", "삼성전자", 4, 70000, 72000, 8000, 2.8),),
            )
            service.watchlist_quotes = {
                "000660": WatchlistQuote("000660", current_price=100_000),
                "035420": WatchlistQuote("035420", current_price=50_000),
            }
            plan = service.plan_candidate_quantities({
                "000660": "BUY", "035420": "BUY", "005930": "SELL"
            })
            self.assertEqual(plan, {"000660": 2, "035420": 4, "005930": 4})
            self.assertEqual(service.storage.recent_orders(10), [])

    def test_failed_quote_refresh_does_not_size_buy_from_old_price(self):
        class FailingQuoteApi:
            def request_watchlist_quotes(self, symbols):
                raise KiwoomOpenApiError("시세 조회 실패")

        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(storage=Storage(Path(directory) / "stale.sqlite3"))
            service.add_watchlist_symbol("005930")
            service.kiwoom_api = FailingQuoteApi()
            service.balance_summary = BalanceSummary(account="1234567890", orderable_amount=1_000_000)
            service.watchlist_quotes = {"005930": WatchlistQuote("005930", current_price=70_000)}

            service.refresh_watchlist_quotes()

            self.assertEqual(service.watchlist_quotes, {})
            self.assertEqual(service.plan_candidate_quantities({"005930": "BUY"}), {})

    def test_order_candidates_respect_holdings_and_profile_switches(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(storage=Storage(Path(directory) / "candidates.sqlite3"))
            buy = TradeDecision("BUY", "test", "BULLISH")
            sell = TradeDecision("SELL", "test", "BEARISH")
            settings = {
                "005930": StrategySettings(profile={"sellEnabled": True}),
                "000660": StrategySettings(profile={"buyEnabled": True}),
                "035420": StrategySettings(profile={"buyEnabled": False}),
            }
            decisions = {"005930": sell, "000660": buy, "035420": buy}
            self.assertEqual(service.order_candidates(decisions, settings), {})

            service.balance_summary = BalanceSummary(
                account="1234567890",
                holdings=(Holding("005930", "삼성전자", 3, 70000, 71000, 3000, 1.4),),
            )
            self.assertEqual(
                service.order_candidates(decisions, settings),
                {"005930": "SELL", "000660": "BUY"},
            )
            self.assertEqual(service.order_candidates({"005930": buy}, settings), {})
            self.assertEqual(service.storage.recent_orders(10), [])

    def test_multi_symbol_evaluation_keeps_selected_symbol_state(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(storage=Storage(Path(directory) / "multi-eval.sqlite3"))
            service.symbol = "005930"
            service.current_price = 72000

            class CandleApi:
                def request_minute_candles(self, symbol, interval=3, count=120):
                    if symbol == "000660":
                        raise KiwoomOpenApiError("시세 조회 실패")
                    return list(reversed(dmi_buy_transition_candles()))

            service.kiwoom_api = CandleApi()
            settings = StrategySettings(dmi_period=2)
            results = service.evaluate_watch_symbols({"005930": settings, "000660": settings})

            self.assertIn("005930", results)
            self.assertIsNone(results["000660"])
            self.assertEqual(set(service._watch_engines), {"005930", "000660"})
            self.assertEqual(service.symbol, "005930")
            self.assertEqual(service.current_price, 72000)
            self.assertEqual(service.storage.recent_orders(10), [])

    def test_profiled_watch_evaluation_reuses_daily_history(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(storage=Storage(Path(directory) / "daily-watch.sqlite3"))

            class ChartApi:
                daily_calls = 0

                def request_minute_candles(self, symbol, interval=3, count=120):
                    return list(reversed(dmi_buy_transition_candles()))

                def request_daily_candles(self, symbol, count=61):
                    self.daily_calls += 1
                    return [Candle(high=10, low=8, close=9, timestamp="20260710")]

            api = ChartApi()
            service.kiwoom_api = api
            settings = StrategySettings(dmi_period=2, profile={"bar": {"combine": "DAILY_ONLY"}})

            first = service.evaluate_watch_symbols({"005930": settings})
            second = service.evaluate_watch_symbols({"005930": settings})

            self.assertEqual(api.daily_calls, 1)
            self.assertEqual(first["005930"].action, "HOLD")
            self.assertEqual(second["005930"].action, "HOLD")
            self.assertEqual(service.storage.recent_orders(10), [])

    def test_pdf_watch_mode_fetches_selected_minute_interval_and_daily_history(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(storage=Storage(Path(directory) / "pdf-watch.sqlite3"))

            class ChartApi:
                intervals = []

                def request_minute_candles(self, symbol, interval=3, count=120):
                    self.intervals.append(interval)
                    return list(reversed(dmi_buy_transition_candles()))

                def request_daily_candles(self, symbol, count=61):
                    return [Candle(high=10, low=8, close=9, timestamp="20260710")]

            api = ChartApi()
            service.kiwoom_api = api
            profile = new_profile(0)
            profile["pdf"].update(enabled=True, minuteInterval=1)
            results = service.evaluate_watch_symbols({
                "005930": StrategySettings(dmi_period=99, profile=profile)
            })

            self.assertEqual(api.intervals, [1])
            self.assertIsNotNone(results["005930"])
            self.assertEqual(service.storage.recent_orders(10), [])

    def test_watch_strategy_uses_manual_holding_average_for_stop_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(storage=Storage(Path(directory) / "manual-sell.sqlite3"))

            class ChartApi:
                def request_minute_candles(self, symbol, interval=3, count=120):
                    return list(reversed(dmi_buy_transition_candles()))

                def request_daily_candles(self, symbol, count=61):
                    return []

            service.kiwoom_api = ChartApi()
            service.balance_summary = BalanceSummary(
                account="1234567890",
                holdings=(Holding("005930", "삼성전자", 3, 10, 8, -6, -20),),
            )
            settings = {"005930": StrategySettings(dmi_period=3, profile=new_profile(1))}

            decisions = service.evaluate_watch_symbols(settings)

            self.assertEqual(decisions["005930"].action, "SELL")
            self.assertEqual(service.order_candidates(decisions, settings), {"005930": "SELL"})
            self.assertEqual(service.storage.recent_orders(10), [])

    def test_minute_only_profile_loads_daily_close_for_overextension_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(storage=Storage(Path(directory) / "overextension.sqlite3"))

            class ChartApi:
                daily_calls = 0

                def request_minute_candles(self, symbol, interval=3, count=120):
                    return list(reversed(dmi_buy_transition_candles()))

                def request_daily_candles(self, symbol, count=61):
                    self.daily_calls += 1
                    return [Candle(high=10, low=8, close=9, timestamp="20260710")]

            api = ChartApi()
            service.kiwoom_api = api
            profile = {
                "bar": {"combine": "MINUTE_ONLY"},
                "buyBlock": {"X02_overExtended": {"enabled": True}},
            }

            service.evaluate_watch_symbols({"005930": StrategySettings(dmi_period=2, profile=profile)})

            self.assertEqual(api.daily_calls, 1)

    def test_bulk_watchlist_registration_skips_duplicates_and_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(storage=Storage(Path(directory) / "bulk-watch.sqlite3"))
            symbols = [f"{index:06d}" for index in range(1, 23)]

            self.assertEqual(service.add_watchlist_symbols(symbols + [symbols[0]]), (20, 3))
            self.assertEqual(len(service.watchlist_items()), 20)
            self.assertEqual(service.add_watchlist_symbols([symbols[0], symbols[20]]), (0, 2))

    def test_condition_search_does_not_register_or_order_symbols(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(storage=Storage(Path(directory) / "conditions.sqlite3"))
            with self.assertRaises(KiwoomOpenApiError):
                service.list_saved_conditions()

            class ConditionApi:
                def list_saved_conditions(self):
                    return [(0, "장초반")]

                def search_condition(self, index, name):
                    return ["005930", "000660"]

            service.kiwoom_api = ConditionApi()
            service.account_info = KiwoomAccountInfo(True, ["1234567890"])
            self.assertEqual(service.list_saved_conditions(), [(0, "장초반")])
            self.assertEqual(service.search_condition(0, "장초반"), ["005930", "000660"])
            self.assertEqual(service.watchlist_items(), [])
            self.assertEqual(service.storage.recent_orders(10), [])

    def test_market_symbols_are_read_only_and_require_openapi_login(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(storage=Storage(Path(directory) / "markets.sqlite3"))
            with self.assertRaises(KiwoomOpenApiError):
                service.list_market_symbols()

            class MarketApi:
                def list_market_symbols(self, market):
                    return ["005930"] if market == "KOSPI" else ["035420"]

            service.kiwoom_api = MarketApi()
            service.account_info = KiwoomAccountInfo(True, ["1234567890"])
            self.assertEqual(
                service.list_market_symbols(),
                {"KOSPI": ["005930"], "KOSDAQ": ["035420"]},
            )
            self.assertEqual(service.watchlist_items(), [])

    def test_start_records_time_once_and_snapshot_keeps_it_after_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(
                storage=Storage(Path(directory) / "start-time.sqlite3")
            )

            service.start()
            first_started_at = service.started_at
            service.start()
            service.stop()

            self.assertIsNotNone(first_started_at)
            self.assertEqual(service.started_at, first_started_at)
            self.assertEqual(service.snapshot().started_at, first_started_at)
            self.assertTrue(
                any(
                    "시작 시각" in row[3]
                    for row in service.storage.recent_logs(10)
                )
            )

    def test_persists_normalized_watchlist_symbol_and_refreshes_quotes(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_watchlist_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        service.add_watchlist_symbol("00915")
        service.add_watchlist_symbol("005930")

        reloaded = AutoTradingService(storage=Storage(db))
        reloaded.kiwoom_api = FakeWatchlistApi()
        rows = reloaded.refresh_watchlist_quotes()

        self.assertEqual([row.symbol for row in rows], ["009150", "005930"])
        self.assertEqual(rows[0].name, "삼성전기")
        self.assertEqual(rows[1].current_price, 72000)
        self.assertEqual(reloaded.watchlist_items()[1], ("005930", "삼성전자"))

    def test_builds_selected_second_chart_from_drained_realtime_trades(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_second_chart_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        service.kiwoom_api = FakeRealtimeApi()
        service.real_time_symbol = "005930"
        service.select_realtime_chart(1)

        service.refresh_real_time_quote()
        candles = service.chart_candles_for_display()

        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0].open, 100)
        self.assertEqual(candles[0].high, 103)
        self.assertEqual(candles[0].close, 103)
        self.assertEqual(candles[0].volume, 5)

    def test_backfills_rest_second_chart_from_official_one_tick_history(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_tick_history_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        api = FakeRestTickApi()
        service.connection_mode = "REST"
        service.rest_api = api
        service.symbol = "005930"
        service.real_time_candles.reset("005930")

        one_second = service.select_realtime_chart(1)
        five_second = service.select_realtime_chart(5)
        service.real_time_symbol = "005930"
        service.refresh_real_time_quote()
        combined = service.select_realtime_chart(1)
        log_messages = [row[3] for row in service.storage.recent_logs(20)]

        self.assertEqual(api.tick_requests, [("005930", 3000)])
        self.assertEqual(len(one_second), 2)
        self.assertEqual(one_second[0].open, 100)
        self.assertEqual(one_second[0].high, 103)
        self.assertEqual(one_second[0].close, 103)
        self.assertEqual(one_second[0].volume, 5)
        self.assertEqual(len(five_second), 1)
        self.assertEqual(five_second[0].close, 104)
        self.assertEqual(len(combined), 3)
        self.assertEqual(combined[-1].close, 106)
        self.assertEqual(combined[-1].volume, 5)
        self.assertTrue(
            any("REST WebSocket 등록 완료" in message for message in log_messages)
        )
        self.assertTrue(
            any("첫 실시간 체결 확인" in message for message in log_messages)
        )
        self.assertEqual(service.chart_source, "키움 ka10079 1틱 + 0B 실시간")

    def test_hour_chart_uses_sixty_minute_api_without_changing_strategy_candles(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_chart_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        chart_api = FakeChartApi()
        service.kiwoom_api = chart_api
        strategy_candles = dmi_buy_transition_candles()
        service.candles = list(strategy_candles)

        chart_candles = service.request_chart_candles(60, "005930")

        self.assertEqual(chart_api.intervals, [("005930", 60, 200)])
        self.assertEqual(service.chart_timeframe, "60m")
        self.assertEqual(chart_candles[0].close, 102)
        self.assertEqual(service.candles, strategy_candles)

    def test_configure_preserves_strategy_state_when_settings_do_not_change(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        settings = StrategySettings(dmi_period=3)

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

    def test_connects_rest_live_account_in_live_server_mode(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_rest_live_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        service.rest_api = FakeRestApi()

        info = service.start_rest_connection("app-key", "secret-key", mock=False)

        self.assertTrue(info.connected)
        self.assertFalse(service.rest_api.mock)
        self.assertEqual(info.server_type, "실거래")

    def test_live_rest_order_passes_explicit_real_order_flags(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_rest_order_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        fake_rest = FakeRestApi()
        service.rest_api = fake_rest
        service.start_rest_connection("app-key", "secret-key", mock=False)
        service.symbol = "005930"
        service.current_price = 72_000
        service.max_capital = 1_000_000

        message = service.send_kiwoom_order(
            "1234567890",
            "BUY",
            1,
            allow_real_order=True,
        )

        self.assertIn("실거래", message)
        self.assertEqual(fake_rest.balance_calls, [("1234567890", "")])
        self.assertTrue(fake_rest.order_requests[0].allow_real_order)
        self.assertFalse(fake_rest.order_requests[0].require_mock_server)

    def test_real_order_requires_verified_live_account_before_balance_or_order(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutoTradingService(storage=Storage(Path(directory) / "live-guard.sqlite3"))
            api = FakeRestApi()
            service.rest_api = api
            service.connection_mode = "REST"
            service.symbol = "005930"
            service.current_price = 72_000
            service.max_capital = 1_000_000
            service.account_info = KiwoomAccountInfo(
                True, ["1234567890"], server_type="모의투자", connection_method="REST API"
            )

            message = service.send_kiwoom_order(
                "1234567890", "BUY", 1, allow_real_order=True
            )

            self.assertIn("실거래 계좌 연결 확인 전", message)
            self.assertEqual(api.balance_calls, [])
            self.assertEqual(api.order_requests, [])

    def test_market_strategy_calculates_dmi_transitions_from_real_candles(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_pattern_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        service.configure("005930", 1_000_000, StrategySettings(dmi_period=3))
        buy_candles = dmi_buy_transition_candles()
        sell_candles = buy_candles + [
            Candle(high=8, low=6, close=7, timestamp="20260711091800")
        ]
        service.request_three_minute_candles = lambda _symbol=None: list(reversed(buy_candles))

        buy = service.evaluate_strategy_with_market_data("005930")
        service.request_three_minute_candles = lambda _symbol=None: list(reversed(sell_candles))
        sell = service.evaluate_strategy_with_market_data("005930")

        self.assertEqual(buy.action, "BUY")
        self.assertEqual(sell.action, "SELL")
        self.assertIsNotNone(service.latest_dmi)

    def test_dmi_buy_transition_sends_one_mock_order_per_candle(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_dmi_order_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        fake_rest = FakeRestApi()
        service.rest_api = fake_rest
        service.start_rest_connection("app-key", "secret-key")
        service.configure("005930", 1_000_000, StrategySettings(dmi_period=3))
        service.current_price = 100_000
        candles = dmi_buy_transition_candles()
        service.request_three_minute_candles = lambda _symbol=None: list(reversed(candles))

        first = service.evaluate_and_send_order_with_market_data("1234567890", quantity=3)
        duplicate = service.evaluate_and_send_order_with_market_data("1234567890", quantity=3)

        self.assertEqual(first.action, "BUY")
        self.assertEqual(duplicate.action, "HOLD")
        self.assertEqual(fake_rest.order_calls, 1)
        self.assertEqual(fake_rest.order_requests[0].quantity, 3)

    def test_retries_mock_sell_request_but_not_buy(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_sell_retry_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        fake_rest = FakeRestApi()
        service.rest_api = fake_rest
        service.start_rest_connection("app-key", "secret-key")
        service.symbol = "005930"
        service.current_price = 72_000
        fake_rest.balance_summary = BalanceSummary(
            account="1234567890",
            deposit=2_000_000,
            orderable_amount=2_000_000,
            holdings=(Holding("005930", "삼성전자", 10, 70_000, 72_000, 20_000, 2.8),),
        )
        fake_rest.sell_failures_remaining = 2

        message = service.send_kiwoom_order("1234567890", "SELL", 1)

        self.assertEqual(fake_rest.order_calls, 3)
        self.assertIn("접수 완료", message)
        fake_rest.order_calls = 0
        fake_rest.balance_summary = BalanceSummary(
            account="1234567890",
            deposit=2_000_000,
            orderable_amount=2_000_000,
        )
        service.send_kiwoom_order("1234567890", "BUY", 1)
        self.assertEqual(fake_rest.order_calls, 1)

    def test_openapi_order_rechecks_balance_with_session_password(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_password_order_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        fake_openapi = FakeOpenApiOrderApi()
        service.kiwoom_api = fake_openapi
        service.connection_mode = "ACTIVEX"
        service.account_info = KiwoomAccountInfo(
            True,
            ["1234567890"],
            user_id="test-user",
            server_type="모의투자",
            reported_account_count=1,
        )
        service.symbol = "005930"
        service.current_price = 100_000
        service.max_capital = 1_000_000

        message = service.send_kiwoom_order(
            "1234567890",
            "BUY",
            1,
            account_password="9876",
        )

        self.assertEqual(fake_openapi.balance_calls, [("1234567890", "9876")])
        self.assertEqual(len(fake_openapi.order_requests), 1)
        self.assertTrue(service.last_order_account_access_verified)
        self.assertIn("접수 완료", message)

        service.evaluate_strategy_with_market_data = lambda _symbol: TradeDecision(
            "BUY",
            "자동주문 테스트",
            "BULLISH",
        )
        decision = service.evaluate_and_send_order_with_market_data(
            "1234567890",
            quantity=1,
            account_password="9876",
        )

        self.assertEqual(decision.action, "BUY")
        self.assertEqual(
            fake_openapi.balance_calls,
            [("1234567890", "9876"), ("1234567890", "9876")],
        )
        self.assertEqual(len(fake_openapi.order_requests), 2)
        self.assertNotIn("9876", repr(service.storage.recent_logs(50)))

        blocked = service.send_kiwoom_order("1234567890", "BUY", 1)

        self.assertEqual(len(fake_openapi.order_requests), 2)
        self.assertFalse(service.last_order_account_access_verified)
        self.assertIn("비밀번호", blocked)

    def test_blocks_manual_buy_over_configured_capital_limit(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_cap_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        fake_rest = FakeRestApi()
        service.rest_api = fake_rest
        service.start_rest_connection("app-key", "secret-key")
        service.symbol = "005930"
        service.current_price = 100_000
        service.max_capital = 500_000

        message = service.send_kiwoom_order("1234567890", "BUY", 6)

        self.assertEqual(fake_rest.order_calls, 0)
        self.assertIn("최대 5주", message)

    def test_allows_additional_buy_within_remaining_capital_limit(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_add_buy_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        fake_rest = FakeRestApi()
        service.rest_api = fake_rest
        service.start_rest_connection("app-key", "secret-key")
        service.symbol = "005930"
        service.current_price = 100_000
        service.max_capital = 500_000
        fake_rest.balance_summary = BalanceSummary(
            account="1234567890",
            deposit=2_000_000,
            orderable_amount=2_000_000,
            holdings=(Holding("005930", "삼성전자", 1, 90_000, 100_000, 10_000, 11.1),),
        )

        message = service.send_kiwoom_order("1234567890", "BUY", 2)

        self.assertEqual(fake_rest.order_calls, 1)
        self.assertEqual(fake_rest.order_requests[0].quantity, 2)
        self.assertIn("접수 완료", message)

    def test_blocks_additional_buy_over_remaining_capital_limit(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_add_buy_cap_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        fake_rest = FakeRestApi()
        service.rest_api = fake_rest
        service.start_rest_connection("app-key", "secret-key")
        service.symbol = "005930"
        service.current_price = 100_000
        service.max_capital = 500_000
        fake_rest.balance_summary = BalanceSummary(
            account="1234567890",
            deposit=2_000_000,
            orderable_amount=2_000_000,
            holdings=(Holding("005930", "삼성전자", 1, 90_000, 100_000, 10_000, 11.1),),
        )

        message = service.send_kiwoom_order("1234567890", "BUY", 5)

        self.assertEqual(fake_rest.order_calls, 0)
        self.assertIn("최대 4주", message)

    def test_blocks_manual_buy_over_account_orderable_amount(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_funds_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        fake_rest = FakeRestApi()
        service.rest_api = fake_rest
        service.start_rest_connection("app-key", "secret-key")
        service.symbol = "005930"
        service.current_price = 100_000
        service.max_capital = 1_000_000
        service.balance_summary = BalanceSummary(
            account="1234567890",
            deposit=450_000,
            orderable_amount=450_000,
        )
        fake_rest.balance_summary = service.balance_summary

        message = service.send_kiwoom_order("1234567890", "BUY", 5)

        self.assertEqual(fake_rest.order_calls, 0)
        self.assertIn("주문가능금액", message)

    def test_blocks_zero_share_order_before_api_call(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_zero_qty_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        fake_rest = FakeRestApi()
        service.rest_api = fake_rest
        service.start_rest_connection("app-key", "secret-key")

        message = service.send_kiwoom_order("1234567890", "BUY", 0)

        self.assertEqual(fake_rest.order_calls, 0)
        self.assertIn("1주 이상", message)

    def test_blocks_placeholder_symbol_before_api_call(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_symbol_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        fake_rest = FakeRestApi()
        service.rest_api = fake_rest
        service.start_rest_connection("app-key", "secret-key")

        message = service.send_kiwoom_order("1234567890", "BUY", 1)

        self.assertEqual(fake_rest.order_calls, 0)
        self.assertIn("종목 세팅", message)

    def test_strategy_sell_uses_selected_share_quantity(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_sell_qty_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        fake_rest = FakeRestApi()
        service.rest_api = fake_rest
        service.start_rest_connection("app-key", "secret-key")
        service.symbol = "005930"
        service.balance_summary = BalanceSummary(
            account="1234567890",
            holdings=(Holding("005930", "삼성전자", 10, 70000, 72000, 20000, 2.8),),
        )
        fake_rest.balance_summary = service.balance_summary
        service.evaluate_strategy_with_market_data = lambda _symbol: TradeDecision(
            "SELL",
            "테스트 매도",
            "BEARISH",
        )

        decision = service.evaluate_and_send_order_with_market_data(
            "1234567890",
            quantity=3,
        )

        self.assertEqual(decision.action, "SELL")
        self.assertEqual(fake_rest.order_calls, 1)
        self.assertEqual(fake_rest.order_requests[0].quantity, 3)


if __name__ == "__main__":
    unittest.main()
