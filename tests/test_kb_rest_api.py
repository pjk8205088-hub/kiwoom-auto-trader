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
        self.assertEqual(calls[1][2]["Authorization"], "Bearer kb-token")
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
        self.assertEqual(data["gnl_ac_no1"], "123456789")
        self.assertEqual(data["ordr_jb_clsf"], "1")
        self.assertEqual(data["ordr_q"], "2")
        self.assertEqual(data["hts_pwd"], "1234")

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
            self.assertNotIn("appKey", payload)
            self.assertNotIn("appSecret", payload)
            self.assertNotIn("app-key", path.read_text(encoding="utf-8"))
            self.assertNotIn("app-secret", path.read_text(encoding="utf-8"))

            loaded = KbOpenApiClient(requester=requester, clock=lambda: 1100.0)
            message = loaded.load_token_file(path)
            quote = loaded.request_current_price("005930")

        self.assertEqual(message, "KB 토큰 파일 로그인 완료")
        self.assertTrue(loaded.is_connected)
        self.assertEqual(quote.current_price, 72000)
        self.assertEqual(calls[-1][1]["Authorization"], "Bearer saved-token")

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
