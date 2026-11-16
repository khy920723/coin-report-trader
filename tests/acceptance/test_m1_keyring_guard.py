"""M1 명세 3.13의 keyring 장치(`tests/conftest.py`).

막는 범위: keyring 패키지의 모듈 함수 `get_password`, `set_password`, `delete_password`,
`get_credential`과, keyring 패키지에 정의된 백엔드 클래스(그 하위 클래스 포함)의 같은 네 메서드.
막지 않는 것: `keyring.get_keyring()`, keyring의 클래스를 물려받지 않은 시험 안의 객체.
"""

import contextlib
from collections.abc import Callable
from typing import Any

import keyring
import keyring.backend
import keyring.backends.fail
import keyring.core
import pytest
from keyring.compat import properties

FUNCTIONS = ("get_password", "set_password", "delete_password", "get_credential")
ARGS: dict[str, tuple[Any, ...]] = {
    "get_password": ("svc", "user"),
    "set_password": ("svc", "user", "value"),
    "delete_password": ("svc", "user"),
    "get_credential": ("svc", "user"),
}


def test_m1_keyring_guard_is_installed(keyring_guard) -> None:
    assert keyring_guard.is_installed()


@pytest.mark.parametrize("name", FUNCTIONS)
@pytest.mark.parametrize("module", [keyring, keyring.core], ids=["keyring", "keyring.core"])
def test_m1_module_functions_are_blocked(keyring_guard, module: Any, name: str) -> None:
    function: Callable[..., Any] = getattr(module, name)
    with keyring_guard.expect_blocked() as caught, pytest.raises(keyring_guard.Blocked):
        function(*ARGS[name])
    # 백엔드까지 가기 전에 그 모듈 함수에서 막혔다.
    assert len(caught) == 1
    assert f"{module.__name__}.{name}을" in caught[0]


@pytest.mark.parametrize("name", FUNCTIONS)
def test_m1_methods_of_a_keyring_backend_are_blocked(keyring_guard, name: str) -> None:
    backend = keyring.backends.fail.Keyring()
    with keyring_guard.expect_blocked() as caught, pytest.raises(keyring_guard.Blocked):
        getattr(backend, name)(*ARGS[name])
    assert len(caught) == 1


class _LateBackend(keyring.backend.KeyringBackend):
    """장치를 건 뒤(이 시험 파일을 읽을 때) 만든 하위 클래스. 값은 메모리에 둔다.

    keyring은 하위 클래스를 모두 백엔드 후보로 적는다. `priority`가 오류를 내면 쓸 수 없는
    백엔드로 보아 고르지 않으므로, 뒤의 시험의 `get_keyring()`이 이 클래스를 고르지 않는다.
    """

    @properties.classproperty
    def priority(cls) -> float:
        raise RuntimeError("시험용 백엔드라 고르지 않는다")

    def __init__(self) -> None:
        super().__init__()
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


@pytest.mark.parametrize("name", ["get_password", "set_password", "delete_password"])
def test_m1_methods_of_a_subclass_made_later_are_blocked(keyring_guard, name: str) -> None:
    backend = _LateBackend()
    assert _LateBackend in keyring_guard.guarded_classes
    with keyring_guard.expect_blocked() as caught, pytest.raises(keyring_guard.Blocked):
        getattr(backend, name)(*ARGS[name])
    assert len(caught) == 1
    assert backend.values == {}


def test_m1_a_swallowed_call_is_still_recorded(keyring_guard) -> None:
    # 넓은 except가 삼켜도 기록이 남는다(그대로 두면 정리 단계에서 이 시험이 실패한다).
    with keyring_guard.expect_blocked() as caught, contextlib.suppress(Exception):
        keyring.set_password(*ARGS["set_password"])  # 일부러 삼킨다
    assert len(caught) == 1


def test_m1_get_keyring_is_not_blocked(keyring_guard, monkeypatch: pytest.MonkeyPatch) -> None:
    assert not _LateBackend.viable  # 백엔드 후보로 골라지지 않는다
    backend = _LateBackend()
    monkeypatch.setattr(keyring.core, "_keyring_backend", backend)
    assert keyring.get_keyring() is backend
    assert keyring_guard.take_attempts() == []


def test_m1_objects_outside_keyring_are_not_blocked(keyring_guard) -> None:
    class MemoryStore:  # keyring의 클래스를 물려받지 않은 시험 안의 객체
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        def get_password(self, service: str, username: str) -> str | None:
            return self.values.get(f"{service}/{username}")

        def set_password(self, service: str, username: str, password: str) -> None:
            self.values[f"{service}/{username}"] = password

    store = MemoryStore()
    store.set_password("svc", "user", "value")
    assert store.get_password("svc", "user") == "value"
    assert keyring_guard.take_attempts() == []
