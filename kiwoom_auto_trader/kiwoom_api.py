from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Callable

try:
    import pythoncom
    import win32com.client
except ImportError:  # pragma: no cover - exercised only on systems without pywin32.
    pythoncom = None
    win32com = None


class KiwoomOpenApiError(RuntimeError):
    pass


KIWOOM_OPENAPI_PAGE = "https://www.kiwoom.com/h/customer/download/VOpenApiInfoView"
KIWOOM_SETUP_GUIDE = (
    "준비 순서: 1) 키움 OpenAPI+ 서비스 사용 등록, "
    "2) OpenAPI+ 모듈 설치, 3) 공동인증서/HTS ID 준비, "
    "4) 32비트 실행 파일로 다시 실행"
)


@dataclass(frozen=True)
class KiwoomEnvironmentStatus:
    process_bits: int
    is_32bit_process: bool
    pywin32_available: bool
    active_x_available: bool
    message: str
    setup_guide: str = KIWOOM_SETUP_GUIDE


@dataclass(frozen=True)
class KiwoomAccountInfo:
    connected: bool
    accounts: list[str]
    user_id: str = ""
    user_name: str = ""
    message: str = ""

    @property
    def account_count(self) -> int:
        return len(self.accounts)


class KiwoomOpenApiClient:
    control_id = "KHOPENAPI.KHOpenAPICtrl.1"

    def __init__(self, dispatch_factory: Callable[[], Any] | None = None) -> None:
        self._dispatch_factory = dispatch_factory
        self._api: Any | None = None

    def check_environment(self) -> KiwoomEnvironmentStatus:
        process_bits = struct.calcsize("P") * 8
        is_32bit = process_bits == 32
        pywin32_available = self._dispatch_factory is not None or (
            pythoncom is not None and win32com is not None
        )
        active_x_available = False

        if pywin32_available and (is_32bit or self._dispatch_factory is not None):
            try:
                api = self._ensure_api()
                active_x_available = api is not None
            except KiwoomOpenApiError:
                active_x_available = False

        if not is_32bit and self._dispatch_factory is None:
            message = (
                f"현재 프로그램이 {process_bits}비트로 실행 중입니다. "
                "키움 OpenAPI+는 32비트 ActiveX이므로 KiwoomAutoTrader-32bit.exe로 실행해야 합니다. "
                f"{KIWOOM_SETUP_GUIDE}"
            )
        elif not pywin32_available:
            message = (
                "pywin32가 설치되어 있지 않아 키움 OpenAPI+를 호출할 수 없습니다. "
                f"{KIWOOM_SETUP_GUIDE}"
            )
        elif not active_x_available:
            message = (
                "키움 OpenAPI+ ActiveX를 찾을 수 없습니다. "
                "키움 OpenAPI+ 모듈 설치와 서비스 사용 등록을 확인해 주세요. "
                f"{KIWOOM_SETUP_GUIDE}"
            )
        else:
            message = "키움 OpenAPI+ 연결 환경이 준비되어 있습니다."

        return KiwoomEnvironmentStatus(
            process_bits,
            is_32bit,
            pywin32_available,
            active_x_available,
            message,
        )

    def start_login(self) -> str:
        status = self.check_environment()
        if not status.active_x_available:
            raise KiwoomOpenApiError(status.message)

        api = self._ensure_api()
        if self.is_connected():
            return "이미 키움 OpenAPI에 연결되어 있습니다."

        result = api.CommConnect()
        if result not in (0, None):
            raise KiwoomOpenApiError(f"CommConnect 호출 실패: {result}")
        return "키움 로그인 창을 열었습니다. 로그인 완료 후 계좌를 조회합니다."

    def is_connected(self) -> bool:
        api = self._ensure_api()
        try:
            self.pump_messages()
            return int(api.GetConnectState()) == 1
        except Exception as exc:  # pragma: no cover - depends on COM runtime.
            raise KiwoomOpenApiError(f"연결 상태 확인 실패: {exc}") from exc

    def get_account_info(self) -> KiwoomAccountInfo:
        api = self._ensure_api()
        if not self.is_connected():
            return KiwoomAccountInfo(False, [], message="키움 OpenAPI에 아직 연결되지 않았습니다.")

        raw_accounts = str(api.GetLoginInfo("ACCNO") or "")
        if not raw_accounts:
            raw_accounts = str(api.GetLoginInfo("ACCLIST") or "")

        accounts = [account.strip() for account in raw_accounts.split(";") if account.strip()]
        return KiwoomAccountInfo(
            connected=True,
            accounts=accounts,
            user_id=str(api.GetLoginInfo("USER_ID") or ""),
            user_name=str(api.GetLoginInfo("USER_NAME") or ""),
            message="계좌 연결이 완료되었습니다.",
        )

    def pump_messages(self) -> None:
        if pythoncom is not None:
            try:
                pythoncom.PumpWaitingMessages()
            except Exception:
                pass

    def _ensure_api(self) -> Any:
        if self._api is None:
            if self._dispatch_factory is not None:
                self._api = self._dispatch_factory()
            else:
                if pythoncom is None or win32com is None:
                    raise KiwoomOpenApiError(
                        "pywin32가 설치되어 있지 않아 키움 OpenAPI를 호출할 수 없습니다."
                    )
                if struct.calcsize("P") * 8 != 32:
                    raise KiwoomOpenApiError(
                        "현재 프로그램이 64비트입니다. "
                        "키움 OpenAPI+는 32비트 ActiveX라서 32비트 EXE로 실행해야 합니다."
                    )
                pythoncom.CoInitialize()
                try:
                    self._api = win32com.client.Dispatch(self.control_id)
                except Exception as exc:  # pragma: no cover - depends on installed OCX.
                    raise KiwoomOpenApiError(
                        "키움 OpenAPI+ ActiveX를 찾을 수 없습니다. "
                        "영웅문/키움 OpenAPI+ 설치와 실행 파일 비트 수를 확인해 주세요."
                    ) from exc
        return self._api
