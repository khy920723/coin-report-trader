"""가짜 비밀 값 보관소(M1 명세 3.13). `core.SecretStore`(3.4)의 약속을 진짜와 같게 지킨다.

- 값은 메모리에 둔다. `name`은 진짜와 같은 뜻이다: 사용자 이름 전체(예: `discord-webhook:error`)를
  그대로 받는다(`vault.webhook_secret_name(category)`로 만든다).
  - `get(name)`: 저장된 값, 없으면 `None`. `set(name, value)`: 저장한다(같은 이름은 덮어쓴다).
    `delete(name)`: 지운다(저장된 값이 없는 이름은 아무 일도 하지 않는다).
  - 이름이 비었거나 값이 비어 있으면(또는 글이 아니면) 진짜처럼 `ValueError`다(기록하지 않는다).
- 시험이 정하는 것: `FakeVault({name: value})`로 처음 값을 둔다. `values`는 저장된 모든 값의
  복사본이다.
- 실패 정하기(`SecretStoreError`, 오류 이름은 시험이 정하고 기본은 "보관소 오류"):
  - `fail_set(error_name=…, times=None)`: 저장이 실패한다. 넘긴 값은 저장되지 않고 앞에 저장된
    값은 그대로다. B23(저장 실패의 칸 아래 글과 A-29)에 쓴다.
  - `fail_delete(error_name=…, times=None)`: 지우기가 실패한다. 저장된 값은 그대로 남는다.
    B24에 쓴다.
  - `fail_get(error_name=…, times=None)`: 읽기가 실패한다(27번이 켤 때 "보관소를 읽지 못함"을
    다룬다).
  - `times`가 `None`이면 `heal()`을 부를 때까지, 정수면 그 횟수만큼 실패하고 다시 정상이다.
    `heal()`은 정해 둔 실패를 모두 푼다. 여러 번 정하면 마지막 것이 앞의 것을 바꾼다.
  - 오류 이름은 `core.texts`의 `ERROR_VAULT`("보관소 오류"), `ERROR_VAULT_UNAVAILABLE`("보관소를
    쓸 수 없음")이다.
- 기록: `calls`는 부른 것마다 `VaultCall(kind, name, failed)`를 차례로 남긴다(`kind`는 `"get"`,
  `"set"`, `"delete"`). 값은 기록하지 않는다. 실패한 부름도 남긴다. 저장된 값은 `values`로 본다
  (B14: 저장 뒤에 보관소에 주소가 있다).
- 값(웹후크 주소)은 `repr`, 예외 글, `calls`에 두지 않는다.
- 여러 스레드가 함께 써도 된다(잠금 하나. 보관소를 쓰는 것은 보내기 스레드다).
- 이 파일은 시험만 import한다. `src/`는 이 폴더를 import하지 않는다.
"""

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from coin_report_trader.core import SecretStoreError
from coin_report_trader.core.texts import ERROR_VAULT

GET: Final = "get"
SET: Final = "set"
DELETE: Final = "delete"


@dataclass(frozen=True)
class VaultCall:
    """부른 것 하나의 기록(값은 담지 않는다)."""

    kind: str
    name: str
    failed: bool


@dataclass
class _Failure:
    error_name: str
    remaining: int | None  # `None`이면 `heal()`까지


class FakeVault:
    """`SecretStore`의 가짜(모듈 설명)."""

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, str] = {}
        self._failures: dict[str, _Failure] = {}
        self.calls: list[VaultCall] = []
        """부른 것(차례대로)."""
        for name, value in (values or {}).items():
            _check(name, value)
            self._values[name] = value

    def __repr__(self) -> str:
        return f"FakeVault(이름 {len(self._values)}개)"

    # --- 시험이 정하고 보는 것 -----------------------------------------------------------------

    @property
    def values(self) -> dict[str, str]:
        """저장된 모든 값의 복사본."""
        with self._lock:
            return dict(self._values)

    def fail_set(self, error_name: str = ERROR_VAULT, *, times: int | None = None) -> None:
        self._fail(SET, error_name, times)

    def fail_delete(self, error_name: str = ERROR_VAULT, *, times: int | None = None) -> None:
        self._fail(DELETE, error_name, times)

    def fail_get(self, error_name: str = ERROR_VAULT, *, times: int | None = None) -> None:
        self._fail(GET, error_name, times)

    def heal(self) -> None:
        """정해 둔 실패를 모두 푼다."""
        with self._lock:
            self._failures.clear()

    def _fail(self, kind: str, error_name: str, times: int | None) -> None:
        if times is not None and (isinstance(times, bool) or times < 1):
            raise ValueError("times는 1 이상의 정수이거나 None이다")
        with self._lock:
            self._failures[kind] = _Failure(error_name, times)

    # --- SecretStore ------------------------------------------------------------------------

    def get(self, name: str) -> str | None:
        _check_name(name)
        with self._lock:
            self._maybe_fail(GET, name)
            self.calls.append(VaultCall(GET, name, False))
            return self._values.get(name)

    def set(self, name: str, value: str) -> None:
        _check(name, value)
        with self._lock:
            self._maybe_fail(SET, name)
            self._values[name] = value
            self.calls.append(VaultCall(SET, name, False))

    def delete(self, name: str) -> None:
        _check_name(name)
        with self._lock:
            self._maybe_fail(DELETE, name)
            self._values.pop(name, None)
            self.calls.append(VaultCall(DELETE, name, False))

    def _maybe_fail(self, kind: str, name: str) -> None:
        """잠금 안에서 부른다. 정해 둔 실패가 있으면 기록하고 낸다."""
        failure = self._failures.get(kind)
        if failure is None:
            return
        if failure.remaining is not None:
            failure.remaining -= 1
            if failure.remaining == 0:
                del self._failures[kind]
        self.calls.append(VaultCall(kind, name, True))
        raise SecretStoreError(failure.error_name)


def _check_name(name: str) -> None:
    if not isinstance(name, str) or not name:
        raise ValueError("이름은 비어 있지 않은 글이어야 한다")


def _check(name: str, value: str) -> None:
    _check_name(name)
    if not isinstance(value, str) or not value:
        raise ValueError("값은 비어 있지 않은 글이어야 한다")
