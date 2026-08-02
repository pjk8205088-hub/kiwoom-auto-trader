import unittest

from kiwoom_auto_trader.models import KiwoomOrderRequest
from kiwoom_auto_trader.rest_api import (
    KIWOOM_REST_GUIDE,
    KIWOOM_REST_PORTAL,
    KiwoomRestApiClient,
    KiwoomRestApiError,
    KiwoomRestRateLimiter,
    REALTIME_TRADE_SESSION_TTL_SECONDS,
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


def response(body, headers=None):
    payload = {"return_code": 0, "return_msg": "정상"}
    payload.update(body)
    return RestResponse(200, headers or {}, payload)


class KiwoomRestApiClientTests(unittest.TestCase):
    def test_uses_official_registration_and_guide_pages(self):
        self.assertEqual(
            KIWOOM_REST_PORTAL,
            "https://openapi.kiwoom.com/mgmt/VOpenApiRegView?dummyVal=0",
        )
        self.assertEqual(
            KIWOOM_REST_GUIDE,
            "https://openapi.kiwoom.com/guide/index?dummyVal=0",
        )

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

    def test_explains_missing_registered_account_after_token_issue(self):
        requester = FakeRequester(
            [
                response(
                    {
                        "token": "test-token",
                        "token_type": "bearer",
                        "expires_dt": "20991231235959",
                    }
                ),
                response({"acctNo": ""}),
            ]
        )
        client = KiwoomRestApiClient(
            mock=False,
            requester=requester,
            rate_limiter=NoopLimiter(),
        )

        with self.assertRaisesRegex(KiwoomRestApiError, "계좌·IP 등록 페이지"):
            client.connect("app-key", "secret-key")
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
        self.assertEqual(client.last_order_no, "0000123")
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

    def test_parses_ten_level_book_unfilled_and_order_actions(self):
        requester = FakeRequester(
            [
                response({"token": "test-token", "expires_dt": "20991231235959"}),
                response({"acctNo": "1234567890"}),
                response(
                    {
                        "bid_req_base_tm": "101501",
                        "sel_fpr_bid": "+4090",
                        "sel_fpr_req": "120",
                        "buy_fpr_bid": "+4080",
                        "buy_fpr_req": "150",
                        "sel_2th_pre_bid": "+4095",
                        "sel_2th_pre_req": "80",
                        "buy_2th_pre_bid": "+4075",
                        "buy_2th_pre_req": "90",
                    }
                ),
                response(
                    {
                        "oso": [
                            {
                                "ord_no": "0000999",
                                "stk_cd": "A005930",
                                "stk_nm": "삼성전자",
                                "io_tp_nm": "+매수",
                                "ord_qty": "2",
                                "oso_qty": "1",
                                "ord_pric": "+4085",
                                "cur_prc": "+4080",
                                "tm": "101502",
                                "ord_stt": "접수",
                            }
                        ]
                    }
                ),
                response({"ord_no": "0001000"}),
                response({"ord_no": "0001001"}),
            ]
        )
        client = KiwoomRestApiClient(
            mock=True,
            requester=requester,
            rate_limiter=NoopLimiter(),
        )
        client.connect("app-key", "secret-key")

        book = client.request_order_book("005930")
        unfilled = client.request_unfilled_orders("1234567890", "005930")
        client.send_order(
            KiwoomOrderRequest(
                account="1234567890",
                symbol="005930",
                side="BUY",
                quantity=1,
                price=4085,
                hoga="00",
                action="MODIFY",
                original_order_no="0000999",
            )
        )
        client.send_order(
            KiwoomOrderRequest(
                account="1234567890",
                symbol="005930",
                side="BUY",
                quantity=1,
                action="CANCEL",
                original_order_no="0000999",
            )
        )

        self.assertEqual((book.best_ask, book.best_bid), (4090, 4080))
        self.assertEqual(book.levels[1].bid_price, 4075)
        self.assertEqual(unfilled[0].unfilled_quantity, 1)
        self.assertEqual(unfilled[0].side, "BUY")
        modify_call, cancel_call = requester.calls[-2:]
        self.assertEqual(modify_call[2]["api-id"], "kt10002")
        self.assertEqual(modify_call[3]["mdfy_uv"], "4085")
        self.assertEqual(cancel_call[2]["api-id"], "kt10003")
        self.assertEqual(cancel_call[3]["orig_ord_no"], "0000999")

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

    def test_requests_and_parses_official_daily_chart_for_dmi(self):
        requester = FakeRequester(
            [
                response({"token": "test-token", "expires_dt": "20991231235959"}),
                response({"acctNo": "1234567890"}),
                response(
                    {
                        "stk_dt_pole_chart_qry": [
                            {
                                "cur_prc": "-72000",
                                "trde_qty": "1000",
                                "dt": "20260725",
                                "open_pric": "-71000",
                                "high_pric": "-72500",
                                "low_pric": "-70500",
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

        candles = client.request_daily_candles("005930", count=99)

        self.assertEqual(candles[0].timestamp, "20260725")
        self.assertEqual(candles[0].close, 72_000)
        self.assertEqual(requester.calls[-1][2]["api-id"], "ka10081")
        self.assertEqual(requester.calls[-1][3]["stk_cd"], "005930")
        self.assertEqual(len(requester.calls[-1][3]["base_dt"]), 8)
        self.assertEqual(requester.calls[-1][3]["upd_stkpc_tp"], "1")

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
                                "mac": "9348679",
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
        self.assertEqual(quotes[0].trade_value, 8_880_000_000_000)
        self.assertEqual(quotes[0].market_cap, 934_867_900_000_000)
        self.assertEqual(quotes[0].ask_price, 72100)

    def test_requests_watchlist_previous_value_and_program_trend(self):
        requester = FakeRequester(
            [
                response({"token": "test-token", "expires_dt": "20991231235959"}),
                response({"acctNo": "1234567890"}),
                response({"pred_trde_prica": "11963"}),
                response(
                    {
                        "stk_tm_prm_trde_trnsn": [
                            {"tm": "101500", "prm_netprps_amt": "+245"}
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

        previous = client.request_watchlist_previous_trade_value("005930")
        program = client.request_watchlist_program_trading_trend("005930")

        self.assertEqual(previous, 11_963_000_000)
        self.assertEqual(program, 245_000_000)
        self.assertEqual(requester.calls[-2][2]["api-id"], "ka10007")
        self.assertEqual(requester.calls[-1][2]["api-id"], "ka90008")

    def test_loads_every_stock_detail_when_five_symbols_are_listed(self):
        symbols = ["005930", "000660", "035420", "051910", "005380"]
        requester = FakeRequester(
            [
                response({"token": "test-token", "expires_dt": "20991231235959"}),
                response({"acctNo": "1234567890"}),
                response(
                    {
                        "atn_stk_infr": [
                            {"stk_cd": "035420", "stk_nm": "NAVER", "mac": "3"},
                            {"stk_cd": "005930", "stk_nm": "삼성전자", "mac": "1"},
                            {"stk_cd": "000660", "stk_nm": "SK하이닉스", "mac": "2"},
                        ]
                    }
                ),
                response(
                    {
                        "atn_stk_infr": [
                            {"stk_cd": "005380", "stk_nm": "현대차", "mac": "5"},
                            {"stk_cd": "051910", "stk_nm": "LG화학", "mac": "4"},
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

        quotes = client.request_watchlist_quotes(symbols)

        detail_calls = [
            call for call in requester.calls if call[2]["api-id"] == "ka10095"
        ]
        self.assertEqual(len(detail_calls), 2)
        self.assertEqual(detail_calls[0][3]["stk_cd"], "005930|000660|035420")
        self.assertEqual(detail_calls[1][3]["stk_cd"], "051910|005380")
        self.assertTrue(
            all(len(call[3]["stk_cd"]) <= 20 for call in detail_calls)
        )
        self.assertEqual([quote.symbol for quote in quotes], symbols)
        self.assertEqual(
            [quote.market_cap for quote in quotes],
            [100_000_000, 200_000_000, 300_000_000, 400_000_000, 500_000_000],
        )

    def test_requests_and_parses_official_today_volume_top(self):
        requester = FakeRequester(
            [
                response({"token": "test-token", "expires_dt": "20991231235959"}),
                response({"acctNo": "1234567890"}),
                response(
                    {
                        "tdy_trde_qty_upper": [
                            {
                                "stk_cd": "005930",
                                "stk_nm": "삼성전자",
                                "cur_prc": "+72000",
                                "pred_pre": "+500",
                                "pred_pre_sig": "2",
                                "flu_rt": "+0.70",
                                "pred_rt": "+110.25",
                                "trde_qty": "12345678",
                                "trde_tern_rt": "1.25",
                                "trde_amt": "888",
                            },
                            {
                                "stk_cd": "000660",
                                "stk_nm": "SK하이닉스",
                                "cur_prc": "-180000",
                                "pred_pre": "-2500",
                                "pred_pre_sig": "5",
                                "flu_rt": "-1.37",
                                "trde_qty": "9876543",
                                "trde_tern_rt": "0.85",
                                "trde_amt": "777",
                            },
                        ]
                    }
                ),
                response(
                    {
                        "list": [
                            {
                                "code": "005930",
                                "name": "삼성전자",
                                "nxtEnable": "Y",
                            }
                        ]
                    }
                ),
                response(
                    {
                        "list": [
                            {
                                "code": "000660",
                                "name": "SK하이닉스",
                                "nxtEnable": "N",
                            }
                        ]
                    }
                ),
                response(
                    {
                        "atn_stk_infr": [
                            {
                                "stk_cd": "005930",
                                "stk_nm": "삼성전자",
                                "mac": "9348679",
                            },
                            {
                                "stk_cd": "000660",
                                "stk_nm": "SK하이닉스",
                                "mac": "1234567",
                            },
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

        quotes = client.request_volume_ranking(market="000", limit=15)

        ranking_call = next(
            call for call in requester.calls if call[2]["api-id"] == "ka10030"
        )
        self.assertTrue(ranking_call[1].endswith("/api/dostk/rkinfo"))
        self.assertEqual(
            ranking_call[3],
            {
                "mrkt_tp": "000",
                "sort_tp": "1",
                "mang_stk_incls": "0",
                "crd_tp": "0",
                "trde_qty_tp": "0",
                "pric_tp": "0",
                "trde_prica_tp": "0",
                "mrkt_open_tp": "0",
                "stex_tp": "3",
            },
        )
        self.assertEqual([quote.rank for quote in quotes], [1, 2])
        self.assertEqual(quotes[0].name, "삼성전자")
        self.assertEqual(quotes[0].current_price, 72_000)
        self.assertEqual(quotes[0].volume, 12_345_678)
        self.assertEqual(quotes[0].trade_value, 888_000_000)
        self.assertEqual(quotes[0].market_cap, 934_867_900_000_000)
        self.assertEqual(quotes[0].change_sign, "2")
        self.assertEqual(quotes[0].previous_ratio, 110.25)
        self.assertTrue(quotes[0].nxt_available)
        self.assertFalse(quotes[1].nxt_available)
        self.assertEqual(quotes[1].change_rate, -1.37)
        self.assertEqual(requester.calls[-1][2]["api-id"], "ka10095")
        self.assertEqual(requester.calls[-1][3]["stk_cd"], "005930|000660")

    def test_requests_and_parses_official_trade_value_top(self):
        requester = FakeRequester(
            [
                response({"token": "test-token", "expires_dt": "20991231235959"}),
                response({"acctNo": "1234567890"}),
                response(
                    {
                        "trde_prica_upper": [
                            {
                                "stk_cd": "005930",
                                "now_rank": "1",
                                "stk_nm": "삼성전자",
                                "cur_prc": "+72000",
                                "pred_pre_sig": "1",
                                "pred_pre": "+500",
                                "flu_rt": "+0.70",
                                "now_trde_qty": "12345678",
                                "trde_prica": "12345",
                            },
                            {
                                "stk_cd": "000660",
                                "now_rank": "2",
                                "stk_nm": "SK하이닉스",
                                "cur_prc": "-180000",
                                "pred_pre_sig": "5",
                                "pred_pre": "-2500",
                                "flu_rt": "-1.37",
                                "now_trde_qty": "9876543",
                                "trde_prica": "9876",
                            },
                        ]
                    }
                ),
                response(
                    {
                        "list": [
                            {
                                "code": "005930",
                                "name": "삼성전자",
                                "nxtEnable": "Y",
                            }
                        ]
                    }
                ),
                response(
                    {
                        "list": [
                            {
                                "code": "000660",
                                "name": "SK하이닉스",
                                "nxtEnable": "N",
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

        quotes = client.request_trade_value_ranking(market="001", limit=15)

        ranking_call = next(
            call for call in requester.calls if call[2]["api-id"] == "ka10032"
        )
        self.assertTrue(ranking_call[1].endswith("/api/dostk/rkinfo"))
        self.assertEqual(
            ranking_call[3],
            {"mrkt_tp": "001", "mang_stk_incls": "1", "stex_tp": "3"},
        )
        self.assertEqual([quote.rank for quote in quotes], [1, 2])
        self.assertEqual(quotes[0].trade_value, 12_345_000_000)
        self.assertEqual(quotes[0].change, 500)
        self.assertEqual(quotes[0].change_sign, "1")
        self.assertTrue(quotes[0].nxt_available)
        self.assertEqual(quotes[1].change_rate, -1.37)

    def test_requests_all_pages_of_today_filled_orders(self):
        requester = FakeRequester(
            [
                response({"token": "test-token", "expires_dt": "20991231235959"}),
                response({"acctNo": "1234567890"}),
                response(
                    {
                        "acnt_ord_cntr_prps_dtl": [
                            {
                                "ord_no": "0000101",
                                "stk_cd": "A012200",
                                "stk_nm": "계양전기",
                                "io_tp_nm": "+매수",
                                "cntr_qty": "2",
                                "cntr_uv": "+4080",
                                "ord_tm": "101501",
                            }
                        ]
                    },
                    {"cont-yn": "Y", "next-key": "next-page"},
                ),
                response(
                    {
                        "acnt_ord_cntr_prps_dtl": [
                            {
                                "ord_no": "0000102",
                                "stk_cd": "012200",
                                "stk_nm": "계양전기",
                                "io_tp_nm": "-매도",
                                "cntr_qty": "1",
                                "cntr_uv": "4100",
                                "cnfm_tm": "102030",
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

        history = client.request_today_trade_history(
            "1234567890",
            order_date="20260727",
        )

        self.assertEqual([entry.side for entry in history], ["SELL", "BUY"])
        self.assertEqual(history[0].timestamp, "2026-07-27T10:20:30")
        self.assertEqual(history[0].total_amount, 4100)
        self.assertEqual(history[1].quantity, 2)
        history_calls = [call for call in requester.calls if call[2]["api-id"] == "kt00007"]
        self.assertEqual(len(history_calls), 2)
        self.assertEqual(
            history_calls[0][3],
            {
                "qry_tp": "4",
                "stk_bond_tp": "1",
                "sell_tp": "0",
                "dmst_stex_tp": "%",
                "ord_dt": "20260727",
                "stk_cd": "",
                "fr_ord_no": "",
            },
        )
        self.assertEqual(history_calls[1][2]["cont-yn"], "Y")
        self.assertEqual(history_calls[1][2]["next-key"], "next-page")

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
        client._real_time_symbol = "005930"

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
        self.assertTrue(client.is_real_time_registered())
        self.assertTrue(client.is_regular_market_open())

    def test_uses_fresh_official_0b_market_division_as_regular_session_evidence(self):
        clock = FakeClock()
        client = KiwoomRestApiClient(
            mock=False,
            rate_limiter=NoopLimiter(),
            clock=clock,
        )
        client._real_time_symbol = "012200"
        client._handle_websocket_message({"trnm": "REG", "return_code": 0})

        client._handle_websocket_message(
            {
                "trnm": "REAL",
                "data": [
                    {
                        "type": "0B",
                        "name": "주식체결",
                        "item": "012200",
                        "values": {
                            "20": "132014",
                            "10": "+4165",
                            "12": "+1.22",
                            "15": "+12",
                            "290": "2",
                        },
                    }
                ],
            }
        )

        quote = client.latest_real_time_quote("012200")
        status = client.latest_market_session_status()
        self.assertEqual(quote.current_price, 4165)
        self.assertEqual(quote.market_session_code, "2")
        self.assertTrue(status.is_open)
        self.assertIn("0B", status.source)
        self.assertTrue(client.is_regular_market_open())

        clock.now += REALTIME_TRADE_SESSION_TTL_SECONDS + 1
        self.assertFalse(client.is_regular_market_open())

    def test_official_0b_after_hours_division_closes_regular_session(self):
        client = KiwoomRestApiClient(mock=False, rate_limiter=NoopLimiter())
        client._real_time_symbol = "012200"
        client._handle_websocket_message({"trnm": "REG", "return_code": 0})

        client._handle_websocket_message(
            {
                "trnm": "REAL",
                "data": [
                    {
                        "type": "0B",
                        "item": "012200",
                        "values": {"20": "153100", "10": "+4165", "290": "3"},
                    }
                ],
            }
        )

        status = client.latest_market_session_status()
        self.assertEqual(status.operation_code, "8")
        self.assertFalse(client.is_regular_market_open())

    def test_fresh_0b_regular_session_allows_official_buy_and_sell_requests(self):
        clock = FakeClock()
        requester = FakeRequester(
            [
                response({"token": "live-token", "expires_dt": "20991231235959"}),
                response({"acctNo": "1234567890"}),
                response({"ord_no": "9000001"}),
                response({"ord_no": "9000002"}),
            ]
        )
        client = KiwoomRestApiClient(
            mock=False,
            requester=requester,
            rate_limiter=NoopLimiter(),
            clock=clock,
        )
        client.connect("app-key", "secret-key")
        client._real_time_symbol = "012200"
        client._handle_websocket_message({"trnm": "REG", "return_code": 0})
        client._handle_websocket_message(
            {
                "trnm": "REAL",
                "data": [
                    {
                        "type": "0B",
                        "item": "012200",
                        "values": {"20": "132014", "10": "+4165", "290": "2"},
                    }
                ],
            }
        )

        common = {
            "account": "1234567890",
            "symbol": "012200",
            "quantity": 2,
            "allow_real_order": True,
            "require_mock_server": False,
        }
        buy_message = client.send_order(KiwoomOrderRequest(side="BUY", **common))
        sell_message = client.send_order(KiwoomOrderRequest(side="SELL", **common))

        self.assertIn("매수주문 접수 완료", buy_message)
        self.assertIn("매도주문 접수 완료", sell_message)
        self.assertEqual(requester.calls[-2][2]["api-id"], "kt10000")
        self.assertEqual(requester.calls[-1][2]["api-id"], "kt10001")

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

        client._real_time_symbol = "005930"
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
