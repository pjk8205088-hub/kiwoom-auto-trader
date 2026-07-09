from __future__ import annotations

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

    def start_login(self) -> str:
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

    def _ensure_api(self) -> Any:
        if self._api is None:
            if self._dispatch_factory is not None:
                self._api = self._dispatch_factory()
            else:
                if pythoncom is None or win32com is None:
                    raise KiwoomOpenApiError(
                        "pywin32가 설치되어 있지 않아 키움 OpenAPI를 호출할 수 없습니다."
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
