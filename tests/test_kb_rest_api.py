import json
import tempfile
import unittest
from pathlib import Path

from kiwoom_auto_trader.kb_rest_api import KbOpenApiClient, KbOpenApiError
from kiwoom_auto_trader.rest_api import RestResponse


class KbOpenApiClientTests(unittest.TestCase):
    def test_connect_and_quote_use_official_envelope(self) -> None:
        calls = []

        def requester(method, url, headers, body, timeout):
            calls.append((method, url, headers, body, timeout))
            if url.endswith("/oauth2/token"):
                return RestResponse(200, {}, {"access_token": "kb-token", "expires_in": 1800})
            return RestResponse(
                200,
                {},
                {
                    "dataHeader": {"resultCode": "200"},
                    "dataBody": {
                        "is_nm": "삼성전자",
                        "now_prc": "72000",
                        "dy2_bdy_cmpr": "1000",
                        "dy2_bdy_cmpr_ccd": "1",
                        "up_dwn_r_p2": "1.41",
                        "acml_vlm": "123456",
                    },
                },
            )

        client = KbOpenApiClient(requester=requester, clock=lambda: 1000.0)
        client.connect("app", "secret")
        quote = client.request_current_price("005930")

        self.assertTrue(client.is_connected)
        self.assertEqual(quote.name, "삼성전자")
        self.assertEqual(quote.current_price, 72000)
        self.assertEqual(quote.volume, 123456)
        self.assertEqual(calls[0][3]["appKey"], "app")
        self.assertEqual(calls[0][3]["appSecret"], "secret")
        self.assertEqual(calls[1][1], "https://developer.kbsec.com:32484/api/v1/ivu10140")
        self.assertEqual(calls[1][2]["Authorization"], "bearer kb-token")
        self.assertEqual(calls[1][3]["dataBody"]["shrt_cd"], "005930")

    def test_sell_order_maps_to_ssam1801_and_returns_order_number(self) -> None:
        calls = []

        def requester(method, url, headers, body, timeout):
            calls.append((url, body))
            if url.endswith("/oauth2/token"):
                return RestResponse(200, {}, {"access_token": "kb-token", "expires_in": 1800})
            return RestResponse(
                200,
                {},
                {"dataHeader": {"resultCode": "200"}, "dataBody": {"ordr_no": "1234567890"}},
            )

        client = KbOpenApiClient(requester=requester, clock=lambda: 1000.0)
        client.connect("app", "secret")
        order_no = client.place_cash_order("123456789", "005930", "SELL", 2, 72000, "1234")

        self.assertEqual(order_no, "1234567890")
        self.assertTrue(calls[-1][0].endswith("/api/v1/ssam1801"))
        data = calls[-1][1]["dataBody"]
        self.assertEqual(data["ordr_jb_clsf"], "1")
        self.assertEqual(data["acct_cd"], "89")
        self.assertEqual(data["ordr_ccd"], "00")
        self.assertEqual(data["crdt_typ_cd"], "00")
        self.assertEqual(data["sor_ordr_ccd"], "S")
        self.assertEqual(data["ordr_q"], "2")

    def test_balance_uses_spqm2226_and_parses_cash_fields(self) -> None:
        calls = []

        def requester(method, url, headers, body, timeout):
            calls.append((url, body))
            if url.endswith("/oauth2/token"):
                return RestResponse(200, {}, {"access_token": "kb-token", "expires_in": 1800})
            return RestResponse(
                200,
                {},
                {
                    "dataHeader": {"resultCode": "200"},
                    "dataBody": {
                        "krw_tfnd": "10000",
                        "ordr_psbl_amt_p2": "10000",
                        "tl_asts_exch_val_amt": "10000",
                        "tl_krw_val_amt": "0",
                        "tl_krw_val_pl_amt": "0",
                    },
                },
            )

        client = KbOpenApiClient(requester=requester, clock=lambda: 1000.0)
        client.connect("app", "secret")
        balance = client.request_balance("39400537301")

        self.assertTrue(calls[-1][0].endswith("/api/v1/spqm2226"))
        self.assertNotIn("acct_cd", calls[-1][1]["dataBody"])
        self.assertEqual(balance.deposit, 10000)
        self.assertEqual(balance.orderable_amount, 10000)
        self.assertEqual(balance.estimated_assets, 10000)

    def test_buy_orderable_cash_uses_ssqm1802(self) -> None:
        calls = []

        def requester(method, url, headers, body, timeout):
            calls.append((url, body))
            if url.endswith("/oauth2/token"):
                return RestResponse(200, {}, {"access_token": "kb-token", "expires_in": 1800})
            return RestResponse(
                200,
                {},
                {
                    "dataHeader": {"resultCode": "200"},
                    "dataBody": {
                        "tfnd": "10000",
                        "ordr_psbl_csh": "10000",
                        "ordr_psbl_tl_amt": "10000",
                        "mx_ordr_psbl_amt": "10000",
                    },
                },
            )

        client = KbOpenApiClient(requester=requester, clock=lambda: 1000.0)
        client.connect("app", "secret")
        orderable = client.request_buy_orderable_cash("005930")

        self.assertTrue(calls[-1][0].endswith("/api/v1/ssqm1802"))
        self.assertEqual(calls[-1][1]["dataBody"]["is_no"], "005930")
        self.assertEqual(orderable.deposit, 10000)
        self.assertEqual(orderable.orderable_total, 10000)

    def test_order_execution_list_uses_ssqm2341(self) -> None:
        calls = []

        def requester(method, url, headers, body, timeout):
            calls.append((url, body))
            if url.endswith("/oauth2/token"):
                return RestResponse(200, {}, {"access_token": "kb-token", "expires_in": 1800})
            return RestResponse(
                200,
                {},
                {
                    "dataHeader": {"resultCode": "200"},
                    "dataBody": {
                        "grid_cnt1": "1",
                        "grid1": [
                            {
                                "ordr_no": "0001234567",
                                "is_cd": "005930",
                                "hngl_shrt_nm": "삼성전자",
                                "trd_dl_ccd_nm": "매수",
                                "ordr_q": "2",
                                "tl_ccls_q": "2",
                                "nccls_q": "0",
                                "ordr_uprc": "72000",
                                "ccls_uprc": "72000",
                                "ordr_tm": "093015",
                            }
                        ],
                    },
                },
            )

        client = KbOpenApiClient(requester=requester, clock=lambda: 1000.0)
        client.connect("app", "secret")
        executions = client.request_order_executions(symbol="005930", date_text="20260825")

        self.assertTrue(calls[-1][0].endswith("/api/v1/ssqm2341"))
        self.assertEqual(calls[-1][1]["dataBody"]["ccls_clsf"], "0")
        self.assertEqual(calls[-1][1]["dataBody"]["ordr_dt"], "20260825")
        self.assertEqual(calls[-1][1]["dataBody"]["is_cd"], "005930")
        self.assertEqual(len(executions), 1)
        self.assertEqual(executions[0].order_no, "0001234567")
        self.assertEqual(executions[0].side, "매수")
        self.assertEqual(executions[0].status, "체결")
        self.assertEqual(executions[0].order_time, "09:30:15")

    def test_api_error_is_reported(self) -> None:
        def requester(method, url, headers, body, timeout):
            return RestResponse(200, {}, {"dataHeader": {"resultCode": "401", "resultMessage": "denied"}})

        client = KbOpenApiClient(requester=requester, clock=lambda: 1000.0)
        with self.assertRaises(KbOpenApiError):
            client.connect("app", "secret")

    def test_token_file_round_trip_excludes_app_credentials(self) -> None:
        calls = []

        def requester(method, url, headers, body, timeout):
            calls.append((url, headers))
            if url.endswith("/oauth2/token"):
                return RestResponse(
                    200,
                    {},
                    {"access_token": "saved-token", "token_type": "Bearer", "expires_in": 1800},
                )
            return RestResponse(
                200,
                {},
                {
                    "dataHeader": {"resultCode": "200"},
                    "dataBody": {"is_nm": "삼성전자", "now_prc": "72000"},
                },
            )

        source = KbOpenApiClient(requester=requester, clock=lambda: 1000.0)
        source.connect("app-key", "app-secret")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kb_openapi_token_test.json"
            source.save_token_file(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["access_token"], "saved-token")
            self.assertEqual(payload["appKey"], "app-key")
            self.assertNotIn("appSecret", payload)
            self.assertNotIn("app-secret", path.read_text(encoding="utf-8"))

            loaded = KbOpenApiClient(requester=requester, clock=lambda: 1100.0)
            message = loaded.load_token_file(path)
            quote = loaded.request_current_price("005930")

        self.assertEqual(message, "KB 토큰 파일 로그인 완료")
        self.assertTrue(loaded.is_connected)
        self.assertEqual(quote.current_price, 72000)
        self.assertEqual(calls[-1][1]["Authorization"], "bearer saved-token")

    def test_expired_token_file_is_rejected(self) -> None:
        client = KbOpenApiClient(clock=lambda: 2000.0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kb_openapi_token_expired.json"
            path.write_text(
                json.dumps(
                    {
                        "format": "kb-openapi-access-token",
                        "version": 1,
                        "access_token": "expired",
                        "token_type": "Bearer",
                        "expires_at_epoch": 1999.0,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(KbOpenApiError, "만료"):
                client.load_token_file(path)


if __name__ == "__main__":
    unittest.main()
