import unittest

from kiwoom_auto_trader.kiwoom_api import KiwoomOpenApiClient, KiwoomRequestLimiter


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
        self.inputs = {}
        self.order_calls = []
        self.real_reg_calls = []

    def CommConnect(self):
        self.login_called = True
        return 0

    def GetConnectState(self):
        return self.connected

    def GetLoginInfo(self, tag):
        values = {
            "ACCNO": "1234567890;0987654321;",
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
        }
        return values.get(item, "")

    def GetRepeatCnt(self, trcode, rqname):
        return 1

    def SetRealReg(self, screen_no, code_list, fid_list, real_type):
        self.real_reg_calls.append((screen_no, code_list, fid_list, real_type))
        return 0

    def SetRealRemove(self, screen_no, code):
        return 0

    def SendOrder(self, rqname, screen_no, account, order_type, code, quantity, price, hoga, original):
        self.order_calls.append((rqname, account, order_type, code, quantity, price, hoga, original))
        return 0


class KiwoomOpenApiClientTests(unittest.TestCase):
    def test_starts_login_with_comm_connect(self):
        fake = FakeKiwoomApi()
        client = KiwoomOpenApiClient(dispatch_factory=lambda: fake)

        message = client.start_login()

        self.assertTrue(fake.login_called)
        self.assertIn("로그인 창", message)

    def test_reads_account_info_after_connection(self):
        fake = FakeKiwoomApi()
        fake.connected = 1
        client = KiwoomOpenApiClient(dispatch_factory=lambda: fake)

        info = client.get_account_info()

        self.assertTrue(info.connected)
        self.assertEqual(info.user_name, "테스트사용자")
        self.assertEqual(info.accounts, ["1234567890", "0987654321"])
        self.assertEqual(info.server_type, "모의투자")

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

    def test_registers_real_time_price(self):
        fake = FakeKiwoomApi()
        fake.connected = 1
        client = KiwoomOpenApiClient(dispatch_factory=lambda: fake)

        message = client.register_real_time_price("005930")

        self.assertIn("등록", message)
        self.assertEqual(fake.real_reg_calls[0][1], "005930")

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
