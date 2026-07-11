import unittest

from kiwoom_auto_trader.models import KiwoomOrderRequest
from kiwoom_auto_trader.rest_api import (
    KiwoomRestApiClient,
    KiwoomRestApiError,
    KiwoomRestRateLimiter,
    RestResponse,
)


class NoopLimiter:
    def acquire(self, api_id):
        return


class FakeRequester:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body, timeout))
        return self.responses.pop(0)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def response(body):
    payload = {"return_code": 0, "return_msg": "정상"}
    payload.update(body)
    return RestResponse(200, {}, payload)


class KiwoomRestApiClientTests(unittest.TestCase):
    def test_connects_with_token_and_account_without_storing_secret(self):
        requester = FakeRequester(
            [
                response(
                    {
                        "token": "test-token",
                        "token_type": "bearer",
                        "expires_dt": "20991231235959",
                    }
                ),
                response({"acctNo": "1234567890"}),
            ]
        )
        client = KiwoomRestApiClient(
            mock=True,
            requester=requester,
            rate_limiter=NoopLimiter(),
        )

        info = client.connect("app-key", "secret-key")

        self.assertTrue(info.connected)
        self.assertEqual(info.accounts, ["1234567890"])
        self.assertEqual(info.connection_method, "REST API")
        self.assertFalse(hasattr(client, "secret_key"))
        self.assertEqual(requester.calls[0][2]["api-id"], "au10001")
        self.assertNotIn("authorization", requester.calls[0][2])
        self.assertEqual(requester.calls[1][2]["authorization"], "Bearer test-token")

    def test_parses_market_chart_balance_and_mock_order(self):
        requester = FakeRequester(
            [
                response({"token": "test-token", "expires_dt": "20991231235959"}),
                response({"acctNo": "1234567890"}),
                response(
                    {
                        "stk_cd": "005930",
                        "stk_nm": "삼성전자",
                        "cur_prc": "-72000",
                        "flu_rt": "1.25",
                        "trde_qty": "1000",
                    }
                ),
                response(
                    {
                        "stk_min_pole_chart_qry": [
                            {
                                "cur_prc": "-72000",
                                "trde_qty": "500",
                                "cntr_tm": "20260711120000",
                                "open_pric": "-71500",
                                "high_pric": "-72500",
                                "low_pric": "-71000",
                            }
                        ]
                    }
                ),
                response(
                    {
                        "tot_pur_amt": "700000",
                        "tot_evlt_amt": "720000",
                        "tot_evlt_pl": "20000",
                        "tot_prft_rt": "2.85",
                        "prsm_dpst_aset_amt": "1000000",
                        "acnt_evlt_remn_indv_tot": [
                            {
                                "stk_cd": "A005930",
                                "stk_nm": "삼성전자",
                                "rmnd_qty": "10",
                                "pur_pric": "70000",
                                "cur_prc": "72000",
                                "evltv_prft": "20000",
                                "prft_rt": "2.85",
                            }
                        ],
                    }
                ),
                response({"ord_no": "0000123"}),
            ]
        )
        client = KiwoomRestApiClient(
            mock=True,
            requester=requester,
            rate_limiter=NoopLimiter(),
        )
        client.connect("app-key", "secret-key")

        quote = client.request_current_price("005930")
        candles = client.request_minute_candles("005930", interval=3)
        balance = client.request_balance("1234567890")
        order_message = client.send_order(
            KiwoomOrderRequest(
                account="1234567890",
                symbol="005930",
                side="BUY",
                quantity=1,
            )
        )

        self.assertEqual(quote.name, "삼성전자")
        self.assertEqual(quote.current_price, 72000)
        self.assertEqual(candles[0].timestamp, "20260711120000")
        self.assertEqual(balance.holdings[0].quantity, 10)
        self.assertIn("0000123", order_message)
        self.assertEqual(requester.calls[-1][2]["api-id"], "kt10000")

    def test_blocks_real_order_flags(self):
        requester = FakeRequester(
            [
                response({"token": "test-token", "expires_dt": "20991231235959"}),
                response({"acctNo": "1234567890"}),
            ]
        )
        client = KiwoomRestApiClient(
            mock=True,
            requester=requester,
            rate_limiter=NoopLimiter(),
        )
        client.connect("app-key", "secret-key")

        with self.assertRaises(KiwoomRestApiError):
            client.send_order(
                KiwoomOrderRequest(
                    account="1234567890",
                    symbol="005930",
                    side="BUY",
                    quantity=1,
                    allow_real_order=True,
                    require_mock_server=False,
                )
            )

    def test_mock_limiter_waits_for_same_api_id(self):
        clock = FakeClock()
        limiter = KiwoomRestRateLimiter(mock=True, clock=clock, sleeper=clock.sleep)

        limiter.acquire("ka10001")
        limiter.acquire("ka10001")

        self.assertEqual(len(clock.sleeps), 1)
        self.assertGreaterEqual(clock.now, 1.0)

    def test_parses_official_0b_websocket_trade_message(self):
        client = KiwoomRestApiClient(mock=True, rate_limiter=NoopLimiter())
        client._real_time_symbol = "005930"

        registration = client._handle_websocket_message(
            {"trnm": "LOGIN", "return_code": 0, "return_msg": ""}
        )
        client._handle_websocket_message(
            {
                "trnm": "REAL",
                "data": [
                    {
                        "type": "0B",
                        "name": "주식체결",
                        "item": "005930",
                        "values": {
                            "20": "101530",
                            "10": "+72000",
                            "12": "+1.25",
                            "13": "123456",
                        },
                    }
                ],
            }
        )

        quote = client.latest_real_time_quote("005930")
        self.assertEqual(registration["data"][0]["item"], ["005930"])
        self.assertEqual(registration["data"][0]["type"], ["0B"])
        self.assertIsNotNone(quote)
        self.assertEqual(quote.current_price, 72000)
        self.assertEqual(quote.change_rate, 1.25)
        self.assertEqual(quote.volume, 123456)

    def test_explains_live_key_used_against_mock_server(self):
        requester = FakeRequester(
            [
                RestResponse(
                    200,
                    {},
                    {
                        "return_code": 8030,
                        "return_msg": "투자구분이 달라 AppKey를 사용할 수 없습니다.",
                    },
                )
            ]
        )
        client = KiwoomRestApiClient(
            mock=True,
            requester=requester,
            rate_limiter=NoopLimiter(),
        )

        with self.assertRaisesRegex(KiwoomRestApiError, "모의투자용 AppKey"):
            client.connect("app-key", "secret-key")


if __name__ == "__main__":
    unittest.main()
