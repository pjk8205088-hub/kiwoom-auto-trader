"""Small client for the official KB Securities B2C Open API.

The application uses this client only from the separate KB manual-trading
window.  It never runs from the old Kiwoom strategy loop and it never submits
an order without a user-enabled checkbox and a final confirmation dialog.
"""

from __future__ import annotations

import json
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .rest_api import RestResponse
from .symbols import normalize_symbol


KB_OPENAPI_PORTAL = "https://openapi.kbsec.com/intro"
KB_OPENAPI_GUIDE = "https://openapi.kbsec.com/guide_b2c"
KB_OPENAPI_DOCS = "https://openapi.kbsec.com/apidoc_b2c"
KB_OPENAPI_LIVE_URL = "https://developer.kbsec.com:32484"
KB_OPENAPI_TIMEOUT_SECONDS = 10.0


class KbOpenApiError(RuntimeError):
    """Raised when KB authentication or an API request fails."""


@dataclass(frozen=True)
class KbQuote:
    symbol: str
    name: str
    current_price: float
    change: float
    change_rate: float
    volume: int
    timestamp: str
    raw: dict[str, Any]


KbRequester = Callable[
    [str, str, dict[str, str], dict[str, Any], float],
    RestResponse,
]


def _number(value: Any) -> float:
    text = str(value or "").strip().replace(",", "").replace("+", "")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _signed_number(value: Any, direction: Any) -> float:
    number = abs(_number(value))
    code = str(direction or "").strip().upper()
    if code in {"2", "4", "D", "DOWN", "-"}:
        return -number
    return number


def _integer(value: Any) -> int:
    return int(abs(_number(value)))


def _local_network_identity() -> tuple[str, str]:
    """Return the identifiers required by KB's dataHeader envelope."""

    try:
        ip_address = socket.gethostbyname(socket.gethostname())
    except OSError:
        ip_address = ""
    try:
        node = uuid.getnode()
        mac_address = ":".join(f"{(node >> shift) & 0xFF:02x}" for shift in range(40, -1, -8))
    except (OSError, ValueError):
        mac_address = ""
    return ip_address, mac_address


class KbOpenApiClient:
    """Authenticated client for the KB Securities production B2C API."""

    def __init__(
        self,
        requester: KbRequester | None = None,
        clock: Callable[[], float] = time.time,
        base_url: str = KB_OPENAPI_LIVE_URL,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._requester = requester or self._default_requester
        self._clock = clock
        self._token = ""
        self._token_expires_at = 0.0
        self._app_key = ""
        self._last_quote: KbQuote | None = None
        self._ip_address, self._mac_address = _local_network_identity()
        self.last_order_no = ""

    @property
    def is_connected(self) -> bool:
        return bool(self._token and self._clock() < self._token_expires_at)

    @property
    def last_quote(self) -> KbQuote | None:
        return self._last_quote

    def connect(self, app_key: str, app_secret: str) -> str:
        app_key = str(app_key or "").strip()
        app_secret = str(app_secret or "").strip()
        if not app_key or not app_secret:
            raise KbOpenApiError("KB Open API App Key와 App Secret을 입력해 주세요.")
        self.disconnect()
        response = self._request(
            "POST",
            "/oauth2/token",
            {
                "grant_type": "client_credentials",
                "appKey": app_key,
                "appSecret": app_secret,
            },
            authorized=False,
        )
        body = response.body
        token = str(
            body.get("access_token")
            or body.get("accessToken")
            or body.get("token")
            or ""
        ).strip()
        if not token:
            raise KbOpenApiError("KB Open API가 access_token을 반환하지 않았습니다.")
        expires_in = max(60.0, _number(body.get("expires_in") or body.get("expiresIn") or 1800))
        self._token = token
        self._token_expires_at = self._clock() + expires_in - 30.0
        self._app_key = app_key
        return "KB Open API 토큰 연결 완료"

    def disconnect(self) -> None:
        self._token = ""
        self._token_expires_at = 0.0
        self._app_key = ""
        self._last_quote = None
        self.last_order_no = ""

    def request_current_price(self, symbol: str) -> KbQuote:
        symbol = normalize_symbol(symbol)
        if not symbol:
            raise KbOpenApiError("6자리 종목번호를 입력해 주세요.")
        body = self._api_post(
            "ivu10140",
            {
                "excg_clsf": "0",
                "shrt_cd": symbol,
            },
        )
        quote = KbQuote(
            symbol=symbol,
            name=str(body.get("is_nm") or "").strip(),
            current_price=_number(body.get("now_prc") or body.get("sprc")),
            change=_signed_number(body.get("dy2_bdy_cmpr"), body.get("dy2_bdy_cmpr_ccd")),
            change_rate=_number(body.get("up_dwn_r_p2")),
            volume=_integer(body.get("acml_vlm") or body.get("bdy_vlm")),
            timestamp=datetime.now().strftime("%Y%m%d%H%M%S"),
            raw=dict(body),
        )
        self._last_quote = quote
        return quote

    def place_cash_order(
        self,
        account: str,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        account_password: str = "",
    ) -> str:
        account = "".join(character for character in str(account or "") if character.isdigit())
        symbol = normalize_symbol(symbol)
        normalized_side = str(side or "").upper()
        if not account:
            raise KbOpenApiError("KB 주문 계좌번호를 입력해 주세요.")
        if not symbol:
            raise KbOpenApiError("6자리 종목번호를 입력해 주세요.")
        if normalized_side not in {"BUY", "SELL"}:
            raise KbOpenApiError("주문 구분은 매수 또는 매도여야 합니다.")
        if int(quantity) <= 0:
            raise KbOpenApiError("주문 수량은 1주 이상이어야 합니다.")
        if float(price) <= 0:
            raise KbOpenApiError("주문 가격은 0보다 커야 합니다.")
        if not str(account_password or "").isdigit() or not 4 <= len(str(account_password)) <= 8:
            raise KbOpenApiError("계좌 비밀번호는 4~8자리 숫자여야 합니다.")

        is_buy = normalized_side == "BUY"
        request_body = {
            "gds_no1": "01" if is_buy else "",
            "ordr_uprc": str(int(price)),
            "gnl_ac_no1": account,
            "ordr_jb_clsf": "2" if is_buy else "1",
            "ordr_mng_no": "",
            "ln_dt": "",
            "acct_cd": "",
            "crct_clsf": "",
            "hts_pwd": str(account_password or "").strip(),
            "gtc_ccd": "",
            "spclz_ordr_ccd": "",
            "is_cd": symbol,
            "s_clsf": "" if is_buy else "2",
            "orgn_ordr_no": "",
            "sor_ordr_ccd": "N" if is_buy else "",
            "stpd_prc": "",
            "ordr_ccd": "00",
            "mkt_tm_clsf": "1",
            "ordr_q": str(int(quantity)),
            "crdt_typ_cd": "00",
        }
        body = self._api_post("ssam1802" if is_buy else "ssam1801", request_body)
        order_no = str(body.get("ordr_no") or "").strip()
        if not order_no:
            message = str(body.get("o_msg") or "KB Open API 주문번호가 수신되지 않았습니다.").strip()
            raise KbOpenApiError(message)
        self.last_order_no = order_no
        return order_no

    def _api_post(self, api_id: str, data_body: dict[str, Any]) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"/api/v1/{api_id}",
            {
                "dataHeader": {
                    "ipAddr": self._ip_address,
                    "macAddr": self._mac_address,
                },
                "dataBody": data_body,
            },
            authorized=True,
        )
        envelope = response.body
        data_body_response = envelope.get("dataBody")
        return data_body_response if isinstance(data_body_response, dict) else {}

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any],
        authorized: bool,
    ) -> RestResponse:
        if authorized and not self.is_connected:
            raise KbOpenApiError("KB Open API가 연결되지 않았거나 토큰이 만료되었습니다.")
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json",
        }
        if authorized:
            headers["Authorization"] = f"Bearer {self._token}"
        response = self._requester(
            method,
            f"{self.base_url}{path}",
            headers,
            body,
            KB_OPENAPI_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            raise KbOpenApiError(f"KB Open API HTTP 오류 {response.status_code}")
        response_body = response.body if isinstance(response.body, dict) else {}
        header = response_body.get("dataHeader")
        if isinstance(header, dict):
            result_code = str(header.get("resultCode") or "200").strip()
            if result_code not in {"", "200", "0"}:
                message = str(
                    header.get("resultMessage")
                    or header.get("processMessage")
                    or response_body.get("message")
                    or "KB Open API 요청이 실패했습니다."
                ).strip()
                raise KbOpenApiError(f"KB Open API 오류 {result_code}: {message}")
        return RestResponse(response.status_code, response.headers, response_body)

    @staticmethod
    def _default_requester(
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        timeout: float,
    ) -> RestResponse:
        request = Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8-sig")
                parsed = json.loads(raw) if raw else {}
                return RestResponse(response.status, dict(response.headers.items()), parsed)
        except HTTPError as exc:
            raise KbOpenApiError(f"KB Open API HTTP 오류 {exc.code}") from exc
        except URLError as exc:
            raise KbOpenApiError("KB Open API 서버에 연결할 수 없습니다.") from exc
