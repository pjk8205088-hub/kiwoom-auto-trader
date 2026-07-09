import unittest

from kiwoom_auto_trader.kiwoom_api import KiwoomOpenApiClient


class FakeKiwoomApi:
    def __init__(self):
        self.connected = 0
        self.login_called = False

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
        }
        return values.get(tag, "")


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

    def test_environment_check_uses_fake_dispatch(self):
        fake = FakeKiwoomApi()
        client = KiwoomOpenApiClient(dispatch_factory=lambda: fake)

        status = client.check_environment()

        self.assertTrue(status.active_x_available)
        self.assertIn("준비", status.setup_guide)


if __name__ == "__main__":
    unittest.main()
