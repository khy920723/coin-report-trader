"""진짜 `vault/`(M1 명세 3.10)의 시험: `KeyringSecretStore`, 백엔드 고르기, 가져오기.

keyring을 실제로 부르지 않는다. 진짜 `SecretStore`에는 keyring의 클래스를 물려받지 않고 부른 것을
기록하는 시험 안의 백엔드를 넣고, 고르는 함수는 `platform`과 `get_backend`를 넘겨 시험한다.
"""

import copy
import pickle
import subprocess
import sys
import textwrap
from typing import Any

import keyring
import pytest
from keyring.errors import PasswordDeleteError

from coin_report_trader import vault
from coin_report_trader.core import AlertCategory, SecretStore, SecretStoreError
from coin_report_trader.core.texts import ERROR_VAULT, ERROR_VAULT_UNAVAILABLE
from coin_report_trader.vault import (
    KeyringSecretStore,
    UnavailableSecretStore,
    create_secret_store,
    select_windows_backend,
    webhook_secret_name,
)

SECRET = "value-secret-1"
NAME = "discord-webhook:error"


class Backend:
    """부른 것을 기록하는 백엔드(keyring의 클래스를 물려받지 않는다)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        self.calls.append(("get", service, username))
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.calls.append(("set", service, username))
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.calls.append(("delete", service, username))
        if (service, username) not in self.values:
            raise PasswordDeleteError("not found")
        del self.values[(service, username)]


class OddError(Exception):
    """keyring의 오류가 아닌 오류(Windows API의 오류처럼). 글에 값이 든다."""


class BrokenBackend:
    """모든 메서드가 `OddError`를 내고, 그 글에 받은 값을 담는다."""

    def get_password(self, service: str, username: str) -> str | None:
        raise OddError(f"read {service} {username}")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise OddError(f"write {password}")

    def delete_password(self, service: str, username: str) -> None:
        raise OddError(f"delete {username}")


# --- KeyringSecretStore --------------------------------------------------------------------


def test_it_is_a_secret_store_and_has_no_value_in_its_repr() -> None:
    store = KeyringSecretStore(Backend())
    assert isinstance(store, SecretStore)
    assert isinstance(UnavailableSecretStore(), SecretStore)
    store.set(NAME, SECRET)
    assert SECRET not in repr(store)


def test_names_for_the_webhook_of_each_category() -> None:
    assert vault.SERVICE_NAME == "coin-report-trader"
    assert webhook_secret_name(AlertCategory.ERROR) == "discord-webhook:error"
    assert [webhook_secret_name(c) for c in AlertCategory] == [
        f"discord-webhook:{c.value}" for c in AlertCategory
    ]


def test_set_get_delete_call_the_backend_with_the_service_and_the_name() -> None:
    backend = Backend()
    store = KeyringSecretStore(backend)
    store.set(NAME, SECRET)
    assert store.get(NAME) == SECRET
    store.set(NAME, "value-2")  # 같은 이름은 덮어쓴다
    assert store.get(NAME) == "value-2"
    store.delete(NAME)
    assert store.get(NAME) is None
    service = "coin-report-trader"
    assert backend.calls == [
        ("set", service, NAME),
        ("get", service, NAME),
        ("set", service, NAME),
        ("get", service, NAME),
        ("delete", service, NAME),
        ("get", service, NAME),
    ]
    assert backend.values == {}


def test_the_categories_are_kept_apart() -> None:
    store = KeyringSecretStore(Backend())
    store.set(webhook_secret_name(AlertCategory.ERROR), "value-error")
    store.set(webhook_secret_name(AlertCategory.TRADE), "value-trade")
    assert store.get(webhook_secret_name(AlertCategory.ERROR)) == "value-error"
    assert store.get(webhook_secret_name(AlertCategory.TRADE)) == "value-trade"
    assert store.get(webhook_secret_name(AlertCategory.OTHER)) is None


def test_a_missing_value_is_none_and_deleting_it_does_nothing() -> None:
    backend = Backend()
    store = KeyringSecretStore(backend)
    assert store.get(NAME) is None
    store.delete(NAME)  # 값이 없어서 백엔드가 낸 `PasswordDeleteError`는 실패가 아니다
    assert [call[0] for call in backend.calls] == ["get", "delete"]


def test_an_empty_stored_value_counts_as_nothing_saved() -> None:
    backend = Backend()
    backend.values[("coin-report-trader", NAME)] = ""
    assert KeyringSecretStore(backend).get(NAME) is None


def test_bad_arguments_are_value_errors_not_vault_failures() -> None:
    backend = Backend()
    store = KeyringSecretStore(backend)
    with pytest.raises(ValueError, match="이름"):
        store.get("")
    with pytest.raises(ValueError, match="이름"):
        store.set("", SECRET)
    with pytest.raises(ValueError, match="이름"):
        store.delete("")
    with pytest.raises(ValueError, match="값"):
        store.set(NAME, "")
    with pytest.raises(ValueError, match="값"):
        store.set(NAME, 3)  # type: ignore[arg-type]
    assert backend.calls == []


def test_any_exception_of_the_backend_becomes_a_vault_error_without_the_value() -> None:
    store = KeyringSecretStore(BrokenBackend())
    for call in (
        lambda: store.get(NAME),
        lambda: store.set(NAME, SECRET),
        lambda: store.delete(NAME),
    ):
        with pytest.raises(SecretStoreError) as caught:
            call()
        error = caught.value
        assert error.error_name == ERROR_VAULT == "보관소 오류"
        assert str(error) == ERROR_VAULT
        assert error.args == (ERROR_VAULT,)
        # 원래 오류(글에 값이 든다)를 잇지 않는다.
        assert error.__cause__ is None
        assert error.__context__ is None
        assert SECRET not in repr(error) + str(error.args)
        again = pickle.loads(pickle.dumps(error))
        assert again.error_name == ERROR_VAULT
        assert copy.copy(error).error_name == ERROR_VAULT


def test_keyring_errors_are_wrapped_too_except_deleting_a_missing_value() -> None:
    class Failing(Backend):
        def set_password(self, service: str, username: str, password: str) -> None:
            raise keyring.errors.PasswordSetError(f"cannot {password}")

        def get_password(self, service: str, username: str) -> str | None:
            raise keyring.errors.KeyringLocked("locked")

    store = KeyringSecretStore(Failing())
    for call in (lambda: store.set(NAME, SECRET), lambda: store.get(NAME)):
        with pytest.raises(SecretStoreError) as caught:
            call()
        assert caught.value.error_name == ERROR_VAULT
        assert SECRET not in str(caught.value)
    store.delete(NAME)  # 값이 없다: 아무 일도 하지 않는다


def test_a_value_that_is_not_text_is_a_vault_failure() -> None:
    class Odd(Backend):
        def get_password(self, service: str, username: str) -> Any:
            return 12345

    with pytest.raises(SecretStoreError) as caught:
        KeyringSecretStore(Odd()).get(NAME)
    assert caught.value.error_name == ERROR_VAULT


def test_only_the_backend_methods_are_called_not_the_keyring_functions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("keyring의 모듈 함수를 불렀다")

    for name in ("get_password", "set_password", "delete_password", "get_credential"):
        monkeypatch.setattr(keyring, name, refuse)
        monkeypatch.setattr(keyring.core, name, refuse)
    store = KeyringSecretStore(Backend())
    store.set(NAME, SECRET)
    assert store.get(NAME) == SECRET
    store.delete(NAME)


def test_base_exceptions_are_not_wrapped() -> None:
    class Interrupted(Backend):
        def get_password(self, service: str, username: str) -> str | None:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        KeyringSecretStore(Interrupted()).get(NAME)


# --- 쓸 수 없는 보관소 -----------------------------------------------------------------------


@pytest.mark.parametrize("error_name", [ERROR_VAULT_UNAVAILABLE, ERROR_VAULT])
def test_an_unavailable_store_raises_its_error_every_time(error_name: str) -> None:
    store = UnavailableSecretStore(error_name)
    calls = (lambda: store.get(NAME), lambda: store.set(NAME, SECRET), lambda: store.delete(NAME))
    for call in calls:
        with pytest.raises(SecretStoreError) as caught:
            call()
        assert caught.value.error_name == error_name
        assert SECRET not in str(caught.value)
    assert UnavailableSecretStore().get.__self__._error_name == ERROR_VAULT_UNAVAILABLE


# --- Windows 백엔드 고르기 -----------------------------------------------------------------------


def backend_of(module: str, name: str = "WinVaultKeyring") -> Any:
    kind = type(name, (), {"__module__": module})
    return kind()


def test_the_windows_backend_is_used_on_windows() -> None:
    backend = backend_of("keyring.backends.Windows")
    assert select_windows_backend(platform="win32", get_backend=lambda: backend) is backend
    store = create_secret_store(platform="win32", get_backend=lambda: backend)
    assert isinstance(store, KeyringSecretStore)


def test_another_backend_on_windows_is_unavailable() -> None:
    for module in ("keyring.backends.fail", "keyring.backends.chainer", "somewhere.else"):
        backend = backend_of(module, "Other")
        with pytest.raises(SecretStoreError) as caught:
            select_windows_backend(platform="win32", get_backend=lambda b=backend: b)
        assert caught.value.error_name == ERROR_VAULT_UNAVAILABLE == "보관소를 쓸 수 없음"
        store = create_secret_store(platform="win32", get_backend=lambda b=backend: b)
        assert isinstance(store, UnavailableSecretStore)
        with pytest.raises(SecretStoreError) as again:
            store.set(NAME, SECRET)
        assert again.value.error_name == ERROR_VAULT_UNAVAILABLE


@pytest.mark.parametrize("platform", ["linux", "darwin", "cygwin", ""])
def test_other_operating_systems_never_ask_keyring_and_are_unavailable(platform: str) -> None:
    def never() -> Any:
        raise AssertionError("Windows가 아니면 백엔드를 얻지 않는다")

    with pytest.raises(SecretStoreError) as caught:
        select_windows_backend(platform=platform, get_backend=never)
    assert caught.value.error_name == ERROR_VAULT_UNAVAILABLE
    store = create_secret_store(platform=platform, get_backend=never)
    assert isinstance(store, UnavailableSecretStore)


def test_even_a_windows_backend_is_not_used_on_another_operating_system() -> None:
    backend = backend_of("keyring.backends.Windows")
    with pytest.raises(SecretStoreError) as caught:
        select_windows_backend(platform="linux", get_backend=lambda: backend)
    assert caught.value.error_name == ERROR_VAULT_UNAVAILABLE


def test_a_failure_while_getting_the_backend_is_a_vault_error() -> None:
    def boom() -> Any:
        raise OddError("secret detail")

    with pytest.raises(SecretStoreError) as caught:
        select_windows_backend(platform="win32", get_backend=boom)
    assert caught.value.error_name == ERROR_VAULT
    assert caught.value.__context__ is None
    store = create_secret_store(platform="win32", get_backend=boom)
    assert isinstance(store, UnavailableSecretStore)
    with pytest.raises(SecretStoreError) as again:
        store.get(NAME)
    assert again.value.error_name == ERROR_VAULT


def test_the_defaults_read_sys_platform_and_keyring_get_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = backend_of("keyring.backends.Windows")
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    monkeypatch.setattr(sys, "platform", "win32")
    assert select_windows_backend() is backend
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(SecretStoreError):
        select_windows_backend()


def test_the_choice_agrees_with_the_self_test_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--self-test`의 `vault` 점검과 같은 기준(모듈 이름이 `keyring.backends.Windows`로 시작)."""
    from coin_report_trader.app import self_test

    monkeypatch.setattr(sys, "platform", "win32")
    for module, ok in (("keyring.backends.Windows", True), ("keyring.backends.fail", False)):
        backend = backend_of(module, "Some")
        monkeypatch.setattr(keyring, "get_keyring", lambda b=backend: b)
        try:
            self_test.check_vault()
            checked = True
        except self_test.CheckFailed:
            checked = False
        try:
            select_windows_backend()
            chosen = True
        except SecretStoreError:
            chosen = False
        assert checked == chosen == ok


# --- 가져오기 -------------------------------------------------------------------------------


def test_importing_the_vault_modules_does_not_ask_keyring_for_a_backend() -> None:
    """새 프로세스에서 `keyring.get_keyring`이 불리면 끝나게 해 놓고 `vault/`를 모두 import한다."""
    code = textwrap.dedent(
        """
        import importlib
        import os
        import pkgutil

        import keyring
        import keyring.core

        def stop(*args, **kwargs):
            os._exit(3)

        keyring.get_keyring = stop
        keyring.core.get_keyring = stop

        import coin_report_trader.vault as package

        names = [package.__name__]
        names += [m.name for m in pkgutil.walk_packages(package.__path__, package.__name__ + ".")]
        for name in names:
            importlib.import_module(name)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code], timeout=120, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")


def test_the_vault_package_exports_its_names() -> None:
    assert sorted(vault.__all__) == sorted(
        [
            "SERVICE_NAME",
            "WEBHOOK_NAME_PREFIX",
            "KeyringSecretStore",
            "UnavailableSecretStore",
            "create_secret_store",
            "select_windows_backend",
            "webhook_secret_name",
        ]
    )
    assert all(hasattr(vault, name) for name in vault.__all__)
