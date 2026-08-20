"""Small client for the official KB Securities B2C Open API.

The application uses this client only from the separate KB manual-trading
window.  It never runs from the old Kiwoom strategy loop and it never submits
an order without a user-enabled checkbox and a final confirmation dialog.
"""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
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
KB_TOKEN_FILE_FORMAT = "kb-openapi-access-token"
KB_TOKEN_FILE_VERSION = 1


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

    ip_address = ""
    for public_ip_url in (
        "https://api.ipify.org?format=text",
        "https://ifconfig.me/ip",
    ):
        try:
            with urlopen(public_ip_url, timeout=3.0) as response:
                candidate = response.read().decode("utf-8", errors="ignore").strip()
        except Exception:
            candidate = ""
        if candidate:
            ip_address = candidate
            break
    if not ip_address:
        try:
            ip_address = socket.gethostbyname(socket.gethostname())
        except OSError:
            ip_address = ""
    try:
        node = uuid.getnode()
        mac_address = "-".join(f"{(node >> shift) & 0xFF:02X}" for shift in range(40, -1, -8))
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
        self._token_type = "Bearer"
        self._token_expires_at = 0.0
        self._app_key = ""
        self._last_quote: KbQuote | None = None
        self._ip_address: str | None = None
        self._mac_address: str | None = None
        self.last_order_no = ""

    @property
    def is_connected(self) -> bool:
        return bool(self._token and self._clock() < self._token_expires_at)

    @property
    def last_quote(self) -> KbQuote | None:
        return self._last_quote

    @property
    def token_expires_at(self) -> float:
        return self._token_expires_at

    @property
    def token_seconds_remaining(self) -> int:
        return max(0, int(self._token_expires_at - self._clock()))

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
        self._token_type = str(body.get("token_type") or body.get("tokenType") or "Bearer").strip() or "Bearer"
        self._token_expires_at = self._clock() + expires_in - 30.0
        self._app_key = app_key
        return "KB Open API 토큰 연결 완료"

    def save_token_file(self, path: str | Path) -> Path:
        """Save the active bearer token without persisting the app secret."""

        if not self.is_connected:
            raise KbOpenApiError("저장할 KB Access Token이 없거나 이미 만료되었습니다.")
        target = Path(path).expanduser()
        if not target.name:
            raise KbOpenApiError("토큰 파일 저장 경로를 확인해 주세요.")
        target.parent.mkdir(parents=True, exist_ok=True)
        expires_at = datetime.fromtimestamp(self._token_expires_at, tz=timezone.utc)
        payload = {
            "format": KB_TOKEN_FILE_FORMAT,
            "version": KB_TOKEN_FILE_VERSION,
            "access_token": self._token,
            "token_type": self._token_type,
            "expires_at": expires_at.isoformat(),
            "expires_at_epoch": self._token_expires_at,
            "base_url": self.base_url,
        }
        temporary = target.with_name(f".{target.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, target)
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise KbOpenApiError("KB 토큰 파일을 저장할 수 없습니다.") from exc
        return target

    def load_token_file(self, path: str | Path) -> str:
        """Load a locally saved bearer token after validating its expiry."""

        source = Path(path).expanduser()
        try:
            payload = json.loads(source.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KbOpenApiError("KB 토큰 파일을 읽을 수 없습니다.") from exc
        if not isinstance(payload, dict) or payload.get("format") != KB_TOKEN_FILE_FORMAT:
            raise KbOpenApiError("이 프로그램에서 저장한 KB 토큰 파일이 아닙니다.")
        token = str(payload.get("access_token") or "").strip()
        token_type = str(payload.get("token_type") or "Bearer").strip() or "Bearer"
        try:
            expires_at = float(payload.get("expires_at_epoch") or 0)
        except (TypeError, ValueError):
            expires_at = 0.0
        if not expires_at:
            try:
                expires_at = datetime.fromisoformat(
                    str(payload.get("expires_at") or "").replace("Z", "+00:00")
                ).timestamp()
            except (TypeError, ValueError):
                expires_at = 0.0
        if not token:
            raise KbOpenApiError("토큰 파일에 Access Token이 없습니다.")
        if token_type.lower() != "bearer" or any(character.isspace() for character in token):
            raise KbOpenApiError("KB 토큰 파일의 인증 형식이 올바르지 않습니다.")
        if expires_at <= self._clock():
            raise KbOpenApiError("KB Access Token이 만료되었습니다. App Key로 다시 발급해 주세요.")
        self.disconnect()
        self._token = token
        self._token_type = "Bearer"
        self._token_expires_at = expires_at
        return "KB 토큰 파일 로그인 완료"

    def disconnect(self) -> None:
        self._token = ""
        self._token_type = "Bearer"
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
        ip_address, mac_address = self._network_identity()
        response = self._request(
            "POST",
            f"/api/v1/{api_id}",
            {
                "dataHeader": {
                    "ipAddr": ip_address,
                    "macAddr": mac_address,
                },
                "dataBody": data_body,
            },
            authorized=True,
        )
        envelope = response.body
        data_body_response = envelope.get("dataBody")
        return data_body_response if isinstance(data_body_response, dict) else {}

    def _network_identity(self) -> tuple[str, str]:
        if not self._ip_address or not self._mac_address:
            self._ip_address, self._mac_address = _local_network_identity()
        return self._ip_address or "", self._mac_address or ""

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
            headers["Authorization"] = f"{self._token_type} {self._token}"
        try:
            response = self._requester(
                method,
                f"{self.base_url}{path}",
                headers,
                body,
                KB_OPENAPI_TIMEOUT_SECONDS,
            )
        except HTTPError as exc:
            response = RestResponse(
                exc.code,
                dict(exc.headers.items()) if exc.headers is not None else {},
                self._decode_error_body(exc),
            )
        except URLError as exc:
            raise KbOpenApiError(f"KB Open API 서버에 연결할 수 없습니다: {exc.reason}") from exc
        if response.status_code >= 400:
            error_message = self._extract_error_message(response.body)
            if error_message:
                raise KbOpenApiError(f"KB Open API HTTP 오류 {response.status_code} ({error_message})")
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
    def _decode_error_body(error: HTTPError) -> dict[str, Any] | str:
        try:
            raw = error.read()
        except Exception:
            return {}
        for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
            try:
                text = raw.decode(encoding)
                break
            except Exception:
                text = ""
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text.strip()
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _extract_error_message(body: Any) -> str:
        if not isinstance(body, dict):
            return str(body or "").strip()
        header = body.get("dataHeader")
        if isinstance(header, dict):
            code = str(header.get("processCode") or header.get("resultCode") or "").strip()
            message = str(
                header.get("processMessage")
                or header.get("resultMessage")
                or body.get("message")
                or ""
            ).strip()
            if code and message:
                return f"{code}: {message}"
            if code:
                return code
            if message:
                return message
        message = str(body.get("message") or "").strip()
        return message

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
                parsed = KbOpenApiClient._decode_json_bytes(response.read())
                return RestResponse(response.status, dict(response.headers.items()), parsed)
        except HTTPError as exc:
            return RestResponse(
                exc.code,
                dict(exc.headers.items()) if exc.headers is not None else {},
                KbOpenApiClient._decode_json_bytes(exc.read()),
            )
        except URLError as exc:
            raise KbOpenApiError("KB Open API 서버에 연결할 수 없습니다.") from exc

    @staticmethod
    def _decode_json_bytes(raw: bytes) -> dict[str, Any] | str:
        if not raw:
            return {}
        for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
            try:
                text = raw.decode(encoding)
                break
            except Exception:
                text = ""
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text.strip()
        return parsed if isinstance(parsed, dict) else {}
