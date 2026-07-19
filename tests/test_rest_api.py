import unittest

from kiwoom_auto_trader.models import KiwoomOrderRequest
from kiwoom_auto_trader.rest_api import (
    KiwoomRestApiClient,
    KiwoomRestApiError,
    KiwoomRestRateLimiter,
    RestResponse,
    _decode_websocket_message,
    _encode_websocket_message,
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
                        "entr": "300000",
                        "ord_alow_amt": "250000",
                        "pymn_alow_amt": "200000",
                        "d2_entra": "280000",
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
        self.assertEqual(balance.deposit, 300000)
        self.assertEqual(balance.orderable_amount, 250000)
        self.assertEqual(balance.withdrawable_amount, 200000)
        self.assertEqual(balance.d2_estimated_deposit, 280000)
        self.assertEqual(balance.holdings[0].quantity, 10)
        self.assertIn("0000123", order_message)
        cash_call = next(call for call in requester.calls if call[2]["api-id"] == "kt00001")
        self.assertEqual(cash_call[3], {"qry_tp": "3"})
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

    def test_requests_official_sixty_minute_chart_scope(self):
        requester = FakeRequester(
            [
                response({"token": "test-token", "expires_dt": "20991231235959"}),
                response({"acctNo": "1234567890"}),
                response({"stk_min_pole_chart_qry": []}),
            ]
        )
        client = KiwoomRestApiClient(
            mock=True,
            requester=requester,
            rate_limiter=NoopLimiter(),
        )
        client.connect("app-key", "secret-key")

        client.request_minute_candles("005930", interval=60)

        self.assertEqual(requester.calls[-1][2]["api-id"], "ka10080")
        self.assertEqual(requester.calls[-1][3]["tic_scope"], "60")

    def test_requests_and_parses_official_one_tick_chart(self):
        requester = FakeRequester(
            [
                response({"token": "test-token", "expires_dt": "20991231235959"}),
                response({"acctNo": "1234567890"}),
                response(
                    {
                        "stk_tic_chart_qry": [
                            {
                                "cur_prc": "+4385",
                                "trde_qty": "+12",
                                "cntr_tm": "20260714153500",
                                "open_pric": "+4385",
                                "high_pric": "+4385",
                                "low_pric": "+4385",
                            }
                        ]
                    }
                ),
            ]
        )
        client = KiwoomRestApiClient(
            mock=False,
            requester=requester,
            rate_limiter=NoopLimiter(),
        )
        client.connect("app-key", "secret-key")

        candles = client.request_tick_candles("012200")

        self.assertEqual(requester.calls[-1][2]["api-id"], "ka10079")
        self.assertEqual(requester.calls[-1][3]["tic_scope"], "1")
        self.assertEqual(candles[0].close, 4385)
        self.assertEqual(candles[0].volume, 12)
        self.assertEqual(candles[0].timestamp, "20260714153500")

    def test_requests_and_parses_official_watchlist_information(self):
        requester = FakeRequester(
            [
                response({"token": "test-token", "expires_dt": "20991231235959"}),
                response({"acctNo": "1234567890"}),
                response(
                    {
                        "atn_stk_infr": [
                            {
                                "stk_cd": "005930",
                                "stk_nm": "삼성전자",
                                "cur_prc": "+72000",
                                "pred_pre": "+500",
                                "flu_rt": "+0.70",
                                "trde_qty": "123456",
                                "trde_prica": "8880000",
                                "open_pric": "+71500",
                                "high_pric": "+72500",
                                "low_pric": "+71000",
                                "sel_bid": "+72100",
                                "buy_bid": "+72000",
                                "cntr_tm": "101530",
                            }
                        ]
                    }
                ),
            ]
        )
        client = KiwoomRestApiClient(
            mock=True,
            requester=requester,
            rate_limiter=NoopLimiter(),
        )
        client.connect("app-key", "secret-key")

        quotes = client.request_watchlist_quotes(["005930", "000660"])

        self.assertEqual(requester.calls[-1][2]["api-id"], "ka10095")
        self.assertEqual(requester.calls[-1][3]["stk_cd"], "005930|000660")
        self.assertEqual(quotes[0].name, "삼성전자")
        self.assertEqual(quotes[0].current_price, 72000)
        self.assertEqual(quotes[0].change, 500)
        self.assertEqual(quotes[0].change_rate, 0.7)
        self.assertEqual(quotes[0].volume, 123456)
        self.assertEqual(quotes[0].ask_price, 72100)

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
                            "15": "12",
                        },
                    }
                ],
            }
        )

        quote = client.latest_real_time_quote("005930")
        self.assertEqual(registration["data"][0]["item"], ["005930"])
        self.assertEqual(registration["data"][0]["type"], ["0B"])
        self.assertEqual(registration["data"][1], {"item": [""], "type": ["0s"]})
        self.assertIsNotNone(quote)
        self.assertEqual(quote.current_price, 72000)
        self.assertEqual(quote.change_rate, 1.25)
        self.assertEqual(quote.volume, 12)
        events = client.drain_real_time_quotes("005930")
        self.assertEqual(events, [quote])
        self.assertEqual(client.drain_real_time_quotes("005930"), [])

    def test_echoes_plain_and_json_websocket_ping_messages(self):
        client = KiwoomRestApiClient(mock=False, rate_limiter=NoopLimiter())

        plain_ping = _decode_websocket_message("PING")
        json_ping = _decode_websocket_message(b'{"trnm":"PING"}')

        self.assertEqual(plain_ping, "PING")
        self.assertEqual(_encode_websocket_message(plain_ping), "PING")
        self.assertEqual(client._handle_websocket_message(plain_ping), "PING")
        self.assertEqual(json_ping, {"trnm": "PING"})
        self.assertEqual(_encode_websocket_message(json_ping), '{"trnm": "PING"}')
        self.assertEqual(client._handle_websocket_message(json_ping), json_ping)

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

    def test_parses_official_market_open_message(self):
        client = KiwoomRestApiClient(mock=False, rate_limiter=NoopLimiter())

        client._handle_websocket_message({"trnm": "REG", "return_code": 0})
        client._handle_websocket_message(
            {
                "trnm": "REAL",
                "data": [
                    {
                        "type": "0s",
                        "name": "장시작시간",
                        "item": "",
                        "values": {"20": "090000", "214": "0", "215": "3"},
                    }
                ],
            }
        )

        status = client.latest_market_session_status()
        self.assertIsNotNone(status)
        self.assertTrue(status.is_open)
        self.assertEqual(status.event_time, "090000")
        self.assertTrue(client.is_regular_market_open())

    def test_live_client_requires_explicit_session_and_market_open_signal(self):
        requester = FakeRequester(
            [
                response({"token": "live-token", "expires_dt": "20991231235959"}),
                response({"acctNo": "1234567890"}),
                response({"ord_no": "9000001"}),
            ]
        )
        client = KiwoomRestApiClient(
            mock=False,
            requester=requester,
            rate_limiter=NoopLimiter(),
        )

        info = client.connect("app-key", "secret-key")

        self.assertEqual(info.server_type, "실거래")
        self.assertTrue(requester.calls[0][1].startswith("https://api.kiwoom.com/"))
        with self.assertRaisesRegex(KiwoomRestApiError, "세션 승인"):
            client.send_order(
                KiwoomOrderRequest(
                    account="1234567890",
                    symbol="005930",
                    side="BUY",
                    quantity=1,
                )
            )
        approved_request = KiwoomOrderRequest(
            account="1234567890",
            symbol="005930",
            side="BUY",
            quantity=1,
            allow_real_order=True,
            require_mock_server=False,
        )
        with self.assertRaisesRegex(KiwoomRestApiError, "장중 신호"):
            client.send_order(approved_request)

        client._handle_websocket_message({"trnm": "REG", "return_code": 0})
        client._handle_websocket_message(
            {
                "trnm": "REAL",
                "data": [
                    {
                        "type": "0s",
                        "item": "",
                        "values": {"20": "090000", "214": "0", "215": "3"},
                    }
                ],
            }
        )
        message = client.send_order(approved_request)

        self.assertIn("REST 실거래 매수주문", message)
        self.assertEqual(requester.calls[-1][2]["api-id"], "kt10000")
        self.assertEqual(requester.calls[-1][3]["trde_tp"], "3")

    def test_explains_registered_ip_mismatch(self):
        requester = FakeRequester(
            [
                RestResponse(
                    200,
                    {},
                    {
                        "return_code": 8010,
                        "return_msg": "토큰 발급 IP와 요청 IP가 동일하지 않습니다.",
                    },
                )
            ]
        )
        client = KiwoomRestApiClient(
            mock=False,
            requester=requester,
            rate_limiter=NoopLimiter(),
        )

        with self.assertRaisesRegex(KiwoomRestApiError, "등록 현황.*공인 IP"):
            client.connect("app-key", "secret-key")


if __name__ == "__main__":
    unittest.main()
