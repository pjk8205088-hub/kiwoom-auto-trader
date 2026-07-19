import unittest

from kiwoom_auto_trader.models import AccountCash, BalanceSummary, MarketQuote
from kiwoom_auto_trader.kiwoom_api import (
    KiwoomOpenApiClient,
    KiwoomOpenApiError,
    KiwoomRequestLimiter,
    is_valid_account_password,
)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeKiwoomApi:
    def __init__(self):
        self.connected = 0
        self.login_called = False
        self.comm_connect_result = 0
        self.inputs = {}
        self.order_calls = []
        self.real_reg_calls = []

    def CommConnect(self):
        self.login_called = True
        return self.comm_connect_result

    def GetConnectState(self):
        return self.connected

    def GetLoginInfo(self, tag):
        values = {
            "ACCNO": "1234567890;0987654321;",
            "ACCOUNT_CNT": "2",
            "USER_ID": "test-user",
            "USER_NAME": "테스트사용자",
            "GetServerGubun": "1",
        }
        return values.get(tag, "")

    def GetMasterCodeName(self, code):
        values = {
            "005930": "삼성전자",
            "009150": "삼성전기",
        }
        return values.get(code, "")

    def SetInputValue(self, key, value):
        self.inputs[key] = value

    def CommRqData(self, rqname, trcode, prev_next, screen_no):
        return 0

    def GetCommData(self, trcode, record_name, index, item):
        values = {
            "종목코드": "005930",
            "종목명": "삼성전자",
            "현재가": "-72000",
            "등락율": "1.23",
            "거래량": "123456",
            "고가": "73000",
            "저가": "71000",
            "시가": "71500",
            "체결시간": "20260709103000",
            "종목번호": "A005930",
            "보유수량": "10",
            "매입가": "70000",
            "평가손익": "20000",
            "수익률(%)": "2.8",
            "총매입금액": "700000",
            "총평가금액": "720000",
            "총평가손익금액": "20000",
            "총수익률(%)": "2.8",
            "추정예탁자산": "1000000",
            "예수금": "300000",
            "주문가능금액": "250000",
            "출금가능금액": "200000",
            "d+2추정예수금": "280000",
        }
        return values.get(item, "")

    def GetRepeatCnt(self, trcode, rqname):
        return 1

    def SetRealReg(self, screen_no, code_list, fid_list, real_type):
        self.real_reg_calls.append((screen_no, code_list, fid_list, real_type))
        return 0

    def SetRealRemove(self, screen_no, code):
        return 0

    def GetCommRealData(self, code, fid):
        values = {
            10: "+72000",
            11: "+500",
            12: "+0.70",
            13: "123456",
            15: "12",
            20: "101530",
            214: "0",
            215: "3",
        }
        return values.get(fid, "")

    def SendOrder(self, rqname, screen_no, account, order_type, code, quantity, price, hoga, original):
        self.order_calls.append((rqname, account, order_type, code, quantity, price, hoga, original))
        return 0


class KiwoomOpenApiClientTests(unittest.TestCase):
    def test_starts_login_with_comm_connect(self):
        fake = FakeKiwoomApi()
        nudged = []
        client = KiwoomOpenApiClient(
            dispatch_factory=lambda: fake,
            login_window_nudger=lambda: nudged.append(True),
        )

        message = client.start_login()

        self.assertTrue(fake.login_called)
        self.assertEqual(client.last_comm_connect_result, 0)
        self.assertEqual(nudged, [True])
        self.assertIn("반환코드 0", message)

    def test_reports_comm_connect_failure_code(self):
        fake = FakeKiwoomApi()
        fake.comm_connect_result = -101
        client = KiwoomOpenApiClient(
            dispatch_factory=lambda: fake,
            login_window_nudger=lambda: None,
        )

        with self.assertRaises(KiwoomOpenApiError) as caught:
            client.start_login()

        self.assertIn("반환코드 -101", str(caught.exception))

    def test_reads_account_info_after_connection(self):
        fake = FakeKiwoomApi()
        fake.connected = 1
        client = KiwoomOpenApiClient(dispatch_factory=lambda: fake)

        info = client.get_account_info()

        self.assertTrue(info.connected)
        self.assertEqual(info.user_name, "테스트사용자")
        self.assertEqual(info.accounts, ["1234567890", "0987654321"])
        self.assertEqual(info.server_type, "모의투자")
        self.assertEqual(info.reported_account_count, 2)
        self.assertTrue(info.login_data_received)

    def test_detects_incomplete_login_account_data(self):
        fake = FakeKiwoomApi()
        fake.connected = 1
        original_get_login_info = fake.GetLoginInfo

        def mismatched_account_count(tag):
            if tag == "ACCOUNT_CNT":
                return "3"
            return original_get_login_info(tag)

        fake.GetLoginInfo = mismatched_account_count
        client = KiwoomOpenApiClient(dispatch_factory=lambda: fake)

        info = client.get_account_info()

        self.assertTrue(info.connected)
        self.assertFalse(info.login_data_received)
        self.assertIn("일치하지 않습니다", info.message)

    def test_retries_rejected_com_connection_state(self):
        fake = FakeKiwoomApi()
        fake.connected = 1
        calls = {"count": 0}

        def busy_once():
            calls["count"] += 1
            if calls["count"] == 1:
                raise Exception(-2147418113, "오류입니다.")
            return fake.connected

        fake.GetConnectState = busy_once
        client = KiwoomOpenApiClient(dispatch_factory=lambda: fake)

        self.assertTrue(client.is_connected())
        self.assertEqual(calls["count"], 2)

    def test_environment_check_uses_fake_dispatch(self):
        fake = FakeKiwoomApi()
        client = KiwoomOpenApiClient(dispatch_factory=lambda: fake)

        status = client.check_environment()

        self.assertTrue(status.active_x_available)
        self.assertIn("준비", status.setup_guide)

    def test_parses_current_price_tr(self):
        fake = FakeKiwoomApi()
        fake.connected = 1
        client = KiwoomOpenApiClient(dispatch_factory=lambda: fake)

        quote = client._parse_current_price("opt10001", "현재가조회", "")

        self.assertEqual(quote.symbol, "005930")
        self.assertEqual(quote.name, "삼성전자")
        self.assertEqual(quote.current_price, 72000)

    def test_parses_balance_tr(self):
        fake = FakeKiwoomApi()
        fake.connected = 1
        client = KiwoomOpenApiClient(dispatch_factory=lambda: fake)

        balance = client._parse_balance("opw00018", "계좌잔고조회", "", "1234567890")

        self.assertEqual(balance.account, "1234567890")
        self.assertEqual(len(balance.holdings), 1)
        self.assertEqual(balance.holdings[0].symbol, "005930")

    def test_parses_openapi_deposit_fields(self):
        fake = FakeKiwoomApi()
        fake.connected = 1
        client = KiwoomOpenApiClient(dispatch_factory=lambda: fake)

        cash = client._parse_account_cash("opw00001", "예수금상세현황조회", "", "1234567890")

        self.assertEqual(cash.deposit, 300000)
        self.assertEqual(cash.orderable_amount, 250000)
        self.assertEqual(cash.withdrawable_amount, 200000)
        self.assertEqual(cash.d2_estimated_deposit, 280000)

    def test_accepts_only_four_to_eight_digit_account_passwords(self):
        self.assertFalse(is_valid_account_password("123"))
        self.assertTrue(is_valid_account_password("1234"))
        self.assertTrue(is_valid_account_password("12345678"))
        self.assertFalse(is_valid_account_password("123456789"))
        self.assertFalse(is_valid_account_password("12ab"))

    def test_combines_openapi_deposit_and_holding_balance(self):
        client = KiwoomOpenApiClient(dispatch_factory=lambda: FakeKiwoomApi())
        calls = []

        def request_tr(rqname, trcode, inputs, parser):
            del parser
            calls.append((rqname, trcode, inputs.copy()))
            if trcode == "opw00001":
                return AccountCash(
                    account="1234567890",
                    deposit=300000,
                    orderable_amount=250000,
                    withdrawable_amount=200000,
                    d2_estimated_deposit=280000,
                )
            return BalanceSummary(account="1234567890", estimated_assets=1000000)

        client._request_tr = request_tr

        balance = client.request_balance("1234567890", password="1234")

        self.assertEqual([call[1] for call in calls], ["opw00001", "opw00018"])
        self.assertEqual(calls[0][2]["비밀번호"], "1234")
        self.assertEqual(balance.deposit, 300000)
        self.assertEqual(balance.estimated_assets, 1000000)
        self.assertEqual(balance.message, "계좌 예수금 및 잔고 조회 완료")

    def test_registers_real_time_price(self):
        fake = FakeKiwoomApi()
        fake.connected = 1
        client = KiwoomOpenApiClient(dispatch_factory=lambda: fake)

        message = client.register_real_time_price("005930")

        self.assertIn("등록", message)
        self.assertEqual(fake.real_reg_calls[0][1], "005930")
        self.assertEqual(fake.real_reg_calls[1], ("9002", "", "20;214;215", "0"))

    def test_parses_openapi_market_open_status(self):
        fake = FakeKiwoomApi()
        client = KiwoomOpenApiClient(dispatch_factory=lambda: fake)

        client._handle_real_data("", "장시작시간", "")

        status = client.latest_market_session_status()
        self.assertIsNotNone(status)
        self.assertTrue(status.is_open)
        self.assertEqual(status.event_time, "101530")

    def test_buffers_openapi_realtime_trades_for_second_candles(self):
        fake = FakeKiwoomApi()
        client = KiwoomOpenApiClient(dispatch_factory=lambda: fake)

        client._handle_real_data("005930", "주식체결", "")

        quote = client.latest_real_time_quote("005930")
        events = client.drain_real_time_quotes("005930")
        self.assertIsNotNone(quote)
        self.assertEqual(quote.current_price, 72000)
        self.assertEqual(events, [quote])
        self.assertEqual(client.drain_real_time_quotes("005930"), [])

    def test_builds_watchlist_rows_from_rate_limited_openapi_quotes(self):
        client = KiwoomOpenApiClient(dispatch_factory=lambda: FakeKiwoomApi())
        client.request_current_price = lambda symbol: MarketQuote(
            symbol=symbol,
            name="삼성전자" if symbol == "005930" else "SK하이닉스",
            current_price=72000,
            change=500,
            change_rate=0.7,
            volume=123,
        )

        quotes = client.request_watchlist_quotes(["005930", "000660"])

        self.assertEqual([quote.symbol for quote in quotes], ["005930", "000660"])
        self.assertEqual(quotes[0].change, 500)

    def test_looks_up_symbol_name(self):
        fake = FakeKiwoomApi()
        fake.connected = 1
        client = KiwoomOpenApiClient(dispatch_factory=lambda: fake)

        name = client.lookup_symbol_name("00915")

        self.assertEqual(name, "삼성전기")

    def test_sends_mock_market_order(self):
        fake = FakeKiwoomApi()
        fake.connected = 1
        client = KiwoomOpenApiClient(dispatch_factory=lambda: fake)

        from kiwoom_auto_trader.models import KiwoomOrderRequest

        message = client.send_order(
            KiwoomOrderRequest(
                account="1234567890",
                symbol="005930",
                side="BUY",
                quantity=1,
            )
        )

        self.assertIn("전송", message)
        self.assertEqual(fake.order_calls[0][2], 1)
        self.assertEqual(fake.order_calls[0][4], 1)

    def test_reports_login_error_from_event(self):
        fake = FakeKiwoomApi()
        client = KiwoomOpenApiClient(dispatch_factory=lambda: fake)
        client._last_login_error = -101

        message = client.login_status_message()

        self.assertIn("서버에 연결할 수 없습니다", message)
        self.assertIn("-101", message)

    def test_rate_limiter_waits_after_five_requests_per_second(self):
        clock = FakeClock()
        limiter = KiwoomRequestLimiter(clock=clock, sleeper=clock.sleep)

        for _ in range(6):
            limiter.acquire()

        self.assertEqual(len(clock.sleeps), 1)
        self.assertGreaterEqual(clock.now, 1.0)


if __name__ == "__main__":
    unittest.main()
