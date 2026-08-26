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

from .models import BalanceSummary, Holding, OrderBookLevel, OrderBookSnapshot
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


@dataclass(frozen=True)
class KbOrderableCash:
    symbol: str
    deposit: float
    orderable_cash: float
    orderable_substitute: float
    orderable_total: float
    max_orderable_amount: float
    withdrawable_cash: float
    raw: dict[str, Any]


@dataclass(frozen=True)
class KbOrderExecution:
    order_no: str
    symbol: str
    name: str
    side: str
    ordered_quantity: int
    executed_quantity: int
    unfilled_quantity: int
    order_price: float
    executed_price: float
    order_time: str
    status: str
    message: str
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


def _first_number(body: dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = body.get(key)
        if value not in (None, ""):
            return _number(value)
    return 0.0


def _first_text(body: Any, *keys: str) -> str:
    """Return the first non-empty text value from a nested KB response."""

    if isinstance(body, dict):
        for key in keys:
            value = body.get(key)
            if value not in (None, ""):
                return str(value).strip()
        for value in body.values():
            nested = _first_text(value, *keys)
            if nested:
                return nested
    elif isinstance(body, list):
        for item in body:
            nested = _first_text(item, *keys)
            if nested:
                return nested
    return ""


def _valid_order_no(value: Any) -> str:
    order_no = "".join(character for character in str(value or "").strip() if character.isdigit())
    if not order_no or set(order_no) == {"0"}:
        return ""
    return order_no


def _is_krx_route_retryable(message: str) -> bool:
    text = str(message or "")
    return any(fragment in text for fragment in ("NXT", "KRX", "거래할 수 없는 종목", "거래할 수 없는"))


def _split_account(account: str) -> tuple[str, str]:
    digits = "".join(character for character in str(account or "") if character.isdigit())
    if len(digits) > 2:
        return digits[:-2], digits[-2:]
    return digits, ""


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
                "appKey": app_key,
                "appSecret": app_secret,
                "grantType": "client_credentials",
                "dataHeader": {
                    "ipAddr": "",
                    "macAddr": "",
                },
                "dataBody": {
                    "appKey": app_key,
                    "appSecret": app_secret,
                    "grantType": "client_credentials",
                },
            },
            authorized=False,
        )
        body = response.body if isinstance(response.body, dict) else {}
        data_body = body.get("dataBody") if isinstance(body.get("dataBody"), dict) else {}
        token = str(
            data_body.get("access_token")
            or data_body.get("accessToken")
            or data_body.get("token")
            or body.get("access_token")
            or body.get("accessToken")
            or body.get("token")
            or ""
        ).strip()
        if not token:
            raise KbOpenApiError("KB Open API가 access_token을 반환하지 않았습니다.")
        expires_in = max(
            60.0,
            _number(
                data_body.get("expires_in")
                or data_body.get("expiresIn")
                or body.get("expires_in")
                or body.get("expiresIn")
                or 1800
            ),
        )
        self._token = token
        self._token_type = "bearer"
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
            "appKey": self._app_key,
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
        token_type = str(payload.get("token_type") or "bearer").strip() or "bearer"
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
        self._token_type = "bearer"
        self._token_expires_at = expires_at
        self._app_key = str(payload.get("appKey") or payload.get("app_key") or "").strip()
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

    def request_order_book(self, symbol: str) -> OrderBookSnapshot:
        """Return KB's regular-session order book using IVU10070."""

        symbol = normalize_symbol(symbol)
        if not symbol:
            raise KbOpenApiError("6자리 종목번호를 입력해 주세요.")
        body = self._api_post(
            "ivu10070",
            {
                "is_cd": symbol,
                "ovtm_mkt_clsf": "0",
            },
        )
        levels: list[OrderBookLevel] = []
        for level in range(1, 11):
            ask_price = _first_number(
                body,
                f"s{level}_aprc",
                f"s_askprc{level}_p4",
                f"s_askprc{level}",
                f"ask_price{level}",
            )
            bid_price = _first_number(
                body,
                f"b{level}_aprc",
                f"b_askprc{level}_p4",
                f"b_askprc{level}",
                f"bid_price{level}",
            )
            ask_quantity = _integer(
                body.get(f"s_pstn_s{level}_aprc_q")
                or body.get(f"s_askprc_q{level}")
                or body.get(f"s{level}_aprc_q")
            )
            bid_quantity = _integer(
                body.get(f"b_pstn_b{level}_aprc_q")
                or body.get(f"b_askprc_q{level}")
                or body.get(f"b{level}_aprc_q")
            )
            if ask_price > 0 or bid_price > 0:
                levels.append(
                    OrderBookLevel(
                        level=level,
                        ask_price=ask_price,
                        ask_quantity=ask_quantity,
                        bid_price=bid_price,
                        bid_quantity=bid_quantity,
                    )
                )
        return OrderBookSnapshot(
            symbol=symbol,
            levels=tuple(levels),
            timestamp=str(body.get("askprc_rcp_tm") or datetime.now().strftime("%H%M%S")),
            source="KB IVU10070",
        )

    def request_buy_orderable_cash(self, symbol: str) -> KbOrderableCash:
        """Return the official domestic buy-orderable cash for a symbol."""

        symbol = normalize_symbol(symbol)
        if not symbol:
            raise KbOpenApiError("6자리 종목번호를 입력해 주세요.")
        body = self._api_post(
            "ssqm1802",
            {
                "is_no": symbol,
                "bnd_mktio_ccd": "1",
            },
        )
        return KbOrderableCash(
            symbol=symbol,
            deposit=_first_number(body, "tfnd"),
            orderable_cash=_first_number(body, "ordr_psbl_csh"),
            orderable_substitute=_first_number(body, "ordr_psbl_sbt"),
            orderable_total=_first_number(body, "ordr_psbl_tl_amt", "pcnt100_ordr_psbl_amt"),
            max_orderable_amount=_first_number(body, "mx_ordr_psbl_amt", "pcnt100_ordr_psbl_amt"),
            withdrawable_cash=_first_number(body, "do_psbl_csh"),
            raw=dict(body),
        )

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
        _, account_suffix = _split_account(account)
        request_body_base = {
            "mkt_tm_clsf": "1",
            "ordr_jb_clsf": "2" if is_buy else "1",
            "s_clsf": "1",
            "is_cd": symbol,
            "ordr_q": str(int(quantity)),
            "ordr_uprc": str(int(price)),
            "ordr_ccd": "00",
            "crdt_typ_cd": "00",
            "ln_dt": "",
            "crct_clsf": "",
            "orgn_ordr_no": "",
            "gtc_ccd": "",
            "ordr_mng_no": "",
            "spclz_ordr_ccd": "",
            "acct_cd": account_suffix,
            "stpd_prc": "",
        }
        api_id = "ssam1802" if is_buy else "ssam1801"
        last_route_error = ""
        for route_code in ("K", "1"):
            request_body = dict(request_body_base)
            request_body["sor_ordr_ccd"] = route_code
            try:
                body = self._api_post(api_id, request_body)
            except KbOpenApiError as exc:
                last_route_error = str(exc)
                if route_code == "K" and _is_krx_route_retryable(last_route_error):
                    continue
                raise
            order_no = _valid_order_no(
                _first_text(
                    body,
                    "ordr_no",
                    "order_no",
                    "ord_no",
                    "ordNo",
                    "odno",
                    "ODNO",
                )
            )
            message = _first_text(
                body,
                "o_msg",
                "msg",
                "message",
                "resultMessage",
                "processMessage",
            )
            if order_no:
                self.last_order_no = order_no
                return order_no
            if route_code == "K" and _is_krx_route_retryable(message):
                last_route_error = message
                continue
            raise KbOpenApiError(message or "KB Open API 주문번호가 수신되지 않았습니다.")
        raise KbOpenApiError(last_route_error or "KB KRX 주문 전송에 실패했습니다.")

    def request_balance(self, account: str = "") -> BalanceSummary:
        """Return the domestic account cash and holdings from SSQM2932.

        SSQM2932 is the customer-account endpoint documented by KB for a
        comprehensive brokerage account.  SPQM2226 is a different balance
        evaluation service and does not provide the domestic account fields
        needed by the manual order screen.
        """
        account_digits = "".join(character for character in str(account or "") if character.isdigit())
        body = self._api_post(
            "ssqm2932",
            {
                "inq_clsf": "1",
                "scrts_ccd": "1",
                "bnd_val_wy_cd": "1",
                "excg_mktpr_ccd": "1",
            },
        )
        holdings = self._parse_balance_holdings(body)
        deposit = _first_number(body, "tfnd", "krw_tfnd")
        orderable_amount = _first_number(
            body,
            "ordr_psbl_csh",
            "pcnt100_ordr_psbl_amt",
            "ordr_psbl_amt_p2",
        )
        withdrawable_amount = _first_number(
            body,
            "o_amt_psbl_amt",
            "o_amt_psbl_amt_p2",
            "krw_o_amt_psbl_amt",
        )
        total_evaluation = _first_number(
            body,
            "val_amt_sum",
            "tl_krw_val_amt",
            "tl_scrts_exch_val_amt",
            "asts_val_amt",
        )
        total_profit_loss = _first_number(
            body,
            "pl_amt_sum",
            "tl_krw_val_pl_amt",
            "krw_exch_val_pl",
        )
        total_purchase = sum(holding.purchase_amount for holding in holdings)
        if total_purchase <= 0:
            total_purchase = sum(
                holding.average_price * holding.quantity for holding in holdings
            )
        total_profit_rate = (total_profit_loss / total_purchase * 100.0) if total_purchase else 0.0
        return BalanceSummary(
            account=account_digits,
            deposit=deposit,
            orderable_amount=orderable_amount or deposit,
            withdrawable_amount=withdrawable_amount or deposit,
            d2_estimated_deposit=_first_number(body, "tfnd", "krw_tfnd"),
            total_purchase=total_purchase,
            total_evaluation=total_evaluation,
            total_profit_loss=total_profit_loss,
            total_profit_rate=total_profit_rate,
            estimated_assets=deposit + total_evaluation,
            holdings=tuple(holdings),
            message=str(body.get("o_msg") or "KB 잔고 조회 완료").strip(),
        )

    def request_order_executions(
        self,
        symbol: str = "",
        order_no: str = "",
        date_text: str = "",
        status: str = "0",
    ) -> tuple[KbOrderExecution, ...]:
        """Return KB account order/execution rows using SSQM2341.

        status: 0 all, 1 executed, 2 unfilled.
        """

        symbol = normalize_symbol(symbol)
        clean_order_no = str(order_no or "").strip()
        clean_status = str(status or "0").strip()
        if clean_status not in {"0", "1", "2"}:
            clean_status = "0"
        clean_date = "".join(character for character in str(date_text or "") if character.isdigit())
        if len(clean_date) != 8:
            clean_date = datetime.now().strftime("%Y%m%d")
        body = self._api_post(
            "ssqm2341",
            {
                "inq_clsf": "1",
                "ccls_clsf": clean_status,
                "ordr_dt": clean_date,
                "is_cd": symbol,
                "ordr_no": clean_order_no,
                "mthr_ordr_no": "",
                "orgn_ordr_no": "",
                "s_ccls_amt": "",
                "b_ccls_amt": "",
                "s_ccls_q": "",
                "b_ccls_q": "",
                "ac_nm": "",
                "is_nm": "",
                "cn_clsf": "1",
                "nxt_key": "",
            },
        )
        return tuple(self._parse_order_execution_rows(body))

    @staticmethod
    def _parse_balance_holdings(body: dict[str, Any]) -> list[Holding]:
        rows: list[dict[str, Any]] = []
        for value in body.values():
            if isinstance(value, list):
                rows.extend(item for item in value if isinstance(item, dict))
        if not rows and body.get("is_cd"):
            rows.append(body)

        holdings: list[Holding] = []
        for row in rows:
            symbol = normalize_symbol(row.get("is_cd"))
            if not symbol:
                continue
            quantity = _integer(
                row.get("frgn_hld_q_p6")
                or row.get("blnc_q")
                or row.get("blnc_q_p6")
                or row.get("hld_q")
                or row.get("ordr_psbl_q")
            )
            sellable_quantity = _integer(
                row.get("frgn_ordr_psbl_q_p6")
                or row.get("frgn_ordr_psbl_q1_p6")
                or row.get("ordr_psbl_q_p6")
                or row.get("ordr_psbl_q")
            )
            current_price = _number(row.get("now_prc_p4") or row.get("now_prc"))
            average_price = _number(
                row.get("byng_avr_prc")
                or row.get("byng_avr_prc_p4")
                or row.get("avr_prc")
            )
            profit_loss = _number(
                row.get("pl_amt")
                or row.get("krw_exch_val_pl")
                or row.get("evltv_prft")
            )
            purchase_amount = _number(row.get("byng_amt"))
            if purchase_amount <= 0 and average_price > 0 and quantity > 0:
                purchase_amount = average_price * quantity
            holdings.append(
                Holding(
                    symbol=symbol,
                    name=str(row.get("is_nm") or "").strip(),
                    quantity=quantity,
                    average_price=average_price,
                    current_price=current_price,
                    profit_loss=profit_loss,
                    profit_rate=_number(row.get("yld")),
                    sellable_quantity=sellable_quantity or quantity,
                    purchase_amount=purchase_amount,
                )
            )
        return holdings

    @staticmethod
    def _parse_order_execution_rows(body: dict[str, Any]) -> list[KbOrderExecution]:
        rows: list[dict[str, Any]] = []
        for value in body.values():
            if isinstance(value, list):
                rows.extend(item for item in value if isinstance(item, dict))
        if not rows and any(key in body for key in ("ordr_no", "is_cd", "stnd_is_no")):
            rows.append(body)

        executions: list[KbOrderExecution] = []
        for row in rows:
            order_no = str(row.get("ordr_no") or "").strip()
            symbol = normalize_symbol(row.get("is_cd") or row.get("stnd_is_no"))
            if not order_no and not symbol:
                continue
            ordered_quantity = _integer(row.get("ordr_q"))
            executed_quantity = _integer(row.get("tl_ccls_q") or row.get("ccls_q"))
            unfilled_quantity = _integer(row.get("nccls_q"))
            side_name = str(
                row.get("trd_dl_ccd_nm")
                or row.get("ordr_jb_clsf_nm")
                or row.get("ordr_ccd")
                or ""
            ).strip()
            if "매도" in side_name or str(row.get("ordr_jb_clsf") or "").strip() == "1":
                side = "매도"
            elif "매수" in side_name or str(row.get("ordr_jb_clsf") or "").strip() == "2":
                side = "매수"
            else:
                side = side_name or "-"
            if executed_quantity > 0 and unfilled_quantity <= 0:
                status = "체결"
            elif executed_quantity > 0 and unfilled_quantity > 0:
                status = "부분체결"
            elif unfilled_quantity > 0:
                status = "미체결"
            else:
                status = "주문"
            message = str(row.get("rfsl_rsn_nm") or row.get("o_msg") or "").strip()
            order_time = str(row.get("ccls_ntc_tm") or row.get("ordr_tm") or "").strip()
            if len(order_time) == 6 and order_time.isdigit():
                order_time = f"{order_time[:2]}:{order_time[2:4]}:{order_time[4:]}"
            executions.append(
                KbOrderExecution(
                    order_no=order_no,
                    symbol=symbol,
                    name=str(row.get("hngl_shrt_nm") or row.get("is_nm") or "").strip(),
                    side=side,
                    ordered_quantity=ordered_quantity,
                    executed_quantity=executed_quantity,
                    unfilled_quantity=unfilled_quantity,
                    order_price=_number(row.get("ordr_uprc") or row.get("ordr_uprc2")),
                    executed_price=_number(row.get("ccls_uprc") or row.get("ccls_uprc2")),
                    order_time=order_time,
                    status=status,
                    message=message,
                    raw=dict(row),
                )
            )
        return executions

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
        if authorized and not self._app_key:
            raise KbOpenApiError("KB App Key가 없습니다. 토큰 로그인 창에서 키 2개로 다시 연결해 주세요.")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if authorized:
            headers["appKey"] = self._app_key
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
            headers={**headers, "Content-Type": headers.get("Content-Type", "application/json")},
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
