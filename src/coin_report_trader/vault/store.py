"""진짜 `SecretStore`와 Windows 백엔드 고르기(M1 명세 3.4, 3.10).

- 서비스 이름은 `coin-report-trader`(`SERVICE_NAME`), 사용자 이름(`name`)은
  `discord-webhook:{분류 값}`이다(`webhook_secret_name`). `KeyringSecretStore`는 `name`을 그대로
  keyring의 사용자 이름으로 쓴다(앞을 붙이지 않는다).
- `KeyringSecretStore`는 생성자로 받은 백엔드 객체의 `get_password`, `set_password`,
  `delete_password`만 부른다. keyring의 모듈 함수는 부르지 않는다.
- 백엔드 메서드가 낸 `Exception`은 모두 `SecretStoreError("보관소 오류")`로 바꾼다. 원래 오류는 잇지
  않는다(그 글에 값이 들어 있을 수 있다). 예외는 하나다: 값이 없는 이름의 `delete`는 실패가
  아니다(keyring의 `PasswordDeleteError`는 아무 일도 하지 않은 것으로 본다).
- Windows 백엔드가 아니면 `SecretStoreError("보관소를 쓸 수 없음")`이다.
- 값(웹후크 주소)을 예외 글, `args`, `repr`에 두지 않는다.
"""

import sys
from collections.abc import Callable
from typing import Any, Final

import keyring
from keyring.errors import PasswordDeleteError

from coin_report_trader.core import APP_ID, AlertCategory, SecretStoreError
from coin_report_trader.core.texts import ERROR_VAULT, ERROR_VAULT_UNAVAILABLE

SERVICE_NAME: Final = APP_ID
"""keyring의 서비스 이름(`coin-report-trader`)."""
WEBHOOK_NAME_PREFIX: Final = "discord-webhook:"
UPBIT_ACCESS_KEY_NAME: Final = "upbit_access_key"
UPBIT_SECRET_KEY_NAME: Final = "upbit_secret_key"
"""업비트 키 둘의 보관소 이름(M5 명세 3.8). 값은 Windows 자격 증명 관리자에만 둔다."""
_WINDOWS_BACKEND_MODULE: Final = "keyring.backends.Windows"
"""Windows 백엔드 클래스의 모듈. `--self-test`의 `vault` 점검과 같은 기준이다."""


def webhook_secret_name(category: AlertCategory) -> str:
    """그 분류의 웹후크 주소의 `name`: `discord-webhook:{분류 값}`(예: `discord-webhook:error`)."""
    return WEBHOOK_NAME_PREFIX + AlertCategory(category).value


def select_windows_backend(
    *,
    platform: str | None = None,
    get_backend: Callable[[], Any] | None = None,
) -> Any:
    """keyring이 고른 백엔드가 Windows 백엔드일 때만 그것을 돌려준다.

    Windows가 아닌 운영체제에서는 `get_backend`(기본 `keyring.get_keyring`)도 부르지 않고,
    Windows 백엔드가 아닌 백엔드가 골라져도 쓰지 않는다. 두 경우 모두
    `SecretStoreError("보관소를 쓸 수 없음")`이다. 백엔드를 얻다 난 오류는 "보관소 오류"다.
    `platform`(기본 `sys.platform`)과 `get_backend`는 시험이 넣는 길이다.
    """
    if (sys.platform if platform is None else platform) != "win32":
        raise SecretStoreError(ERROR_VAULT_UNAVAILABLE)
    failed = False
    backend: Any = None
    try:
        backend = (keyring.get_keyring if get_backend is None else get_backend)()
    except Exception:
        failed = True
    if failed:
        raise SecretStoreError(ERROR_VAULT)
    if not type(backend).__module__.startswith(_WINDOWS_BACKEND_MODULE):
        raise SecretStoreError(ERROR_VAULT_UNAVAILABLE)
    return backend


class KeyringSecretStore:
    """keyring 백엔드 객체를 받는 진짜 `SecretStore`."""

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def __repr__(self) -> str:
        return "KeyringSecretStore()"

    def get(self, name: str) -> str | None:
        """저장된 값. 없으면 `None`(빈 글도 저장된 값이 없는 것으로 본다)."""
        _check_name(name)
        value = self._call(lambda: self._backend.get_password(SERVICE_NAME, name))
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise SecretStoreError(ERROR_VAULT)
        return value

    def set(self, name: str, value: str) -> None:
        """값을 저장한다(같은 이름은 덮어쓴다). 빈 글이나 글이 아닌 값은 `ValueError`다."""
        _check_name(name)
        if not isinstance(value, str) or not value:
            raise ValueError("저장할 값은 비어 있지 않은 글이어야 합니다")
        self._call(lambda: self._backend.set_password(SERVICE_NAME, name, value))

    def delete(self, name: str) -> None:
        """값을 지운다. 저장된 값이 없는 이름은 아무 일도 하지 않는다."""
        _check_name(name)
        failed = False
        try:
            self._backend.delete_password(SERVICE_NAME, name)
        except PasswordDeleteError:
            return  # 값이 없어서 지울 것이 없다
        except Exception:
            failed = True
        if failed:
            raise SecretStoreError(ERROR_VAULT)

    def _call(self, action: Callable[[], Any]) -> Any:
        """백엔드의 오류를 "보관소 오류"로 바꾼다. `except` 밖에서 내므로 원래 오류가 안 달린다."""
        failed = False
        result: Any = None
        try:
            result = action()
        except Exception:
            failed = True
        if failed:
            raise SecretStoreError(ERROR_VAULT)
        return result


class UnavailableSecretStore:
    """쓸 수 없는 보관소. 부를 때마다 `SecretStoreError(error_name)`을 낸다."""

    def __init__(self, error_name: str = ERROR_VAULT_UNAVAILABLE) -> None:
        self._error_name = error_name

    def __repr__(self) -> str:
        return f"UnavailableSecretStore({self._error_name!r})"

    def get(self, name: str) -> str | None:
        raise SecretStoreError(self._error_name)

    def set(self, name: str, value: str) -> None:
        raise SecretStoreError(self._error_name)

    def delete(self, name: str) -> None:
        raise SecretStoreError(self._error_name)


def create_secret_store(
    *,
    platform: str | None = None,
    get_backend: Callable[[], Any] | None = None,
) -> KeyringSecretStore | UnavailableSecretStore:
    """Windows 백엔드로 `KeyringSecretStore`를 만든다. 쓸 수 없으면 그 오류를 내는 보관소다.

    조립(27번)은 늘 이것을 받아 쓰고, 저장이나 지우기가 부를 때마다 `SecretStoreError`를 받아
    알림과 칸 아래 글을 만든다. `platform`, `get_backend`는 `select_windows_backend`와 같다.
    """
    try:
        backend = select_windows_backend(platform=platform, get_backend=get_backend)
    except SecretStoreError as exc:
        return UnavailableSecretStore(exc.error_name)
    return KeyringSecretStore(backend)


def _check_name(name: str) -> None:
    if not isinstance(name, str) or not name:
        raise ValueError("이름은 비어 있지 않은 글이어야 합니다")
