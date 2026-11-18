"""가짜 보관소(`tests/fakes/vault.py`)의 시험(M1 명세 3.13).

27번의 조립과 29번의 수용 시험이 이 가짜를 고치지 않고 쓰므로, 약속(메모리, 정해 둔 실패, 기록)을
여기서 확인한다.
"""

import copy
import pickle
import threading

import pytest
from fakes.vault import DELETE, GET, SET, FakeVault, VaultCall

from coin_report_trader.core import SecretStore, SecretStoreError
from coin_report_trader.core.texts import ERROR_VAULT, ERROR_VAULT_UNAVAILABLE

NAME = "discord-webhook:error"
OTHER = "discord-webhook:trade"
VALUE = "value-1"


def test_it_is_a_secret_store() -> None:
    assert isinstance(FakeVault(), SecretStore)


def test_memory_get_set_delete() -> None:
    vault = FakeVault()
    assert vault.get(NAME) is None
    vault.set(NAME, VALUE)
    vault.set(OTHER, "value-2")
    assert vault.get(NAME) == VALUE
    assert vault.values == {NAME: VALUE, OTHER: "value-2"}
    vault.set(NAME, "value-3")  # 같은 이름은 덮어쓴다
    assert vault.get(NAME) == "value-3"
    vault.delete(NAME)
    assert vault.get(NAME) is None
    assert vault.get(OTHER) == "value-2"
    vault.delete(NAME)  # 저장된 값이 없는 이름은 아무 일도 하지 않는다
    assert vault.values == {OTHER: "value-2"}


def test_initial_values_and_a_copy_of_the_values() -> None:
    vault = FakeVault({NAME: VALUE})
    assert vault.get(NAME) == VALUE
    shown = vault.values
    shown[OTHER] = "x"
    assert vault.values == {NAME: VALUE}
    with pytest.raises(ValueError, match="값"):
        FakeVault({NAME: ""})


@pytest.mark.parametrize("name", ["", None, 3])
def test_a_bad_name_is_a_value_error_and_is_not_recorded(name: object) -> None:
    vault = FakeVault()
    for call in (
        lambda: vault.get(name),  # type: ignore[arg-type]
        lambda: vault.set(name, VALUE),  # type: ignore[arg-type]
        lambda: vault.delete(name),  # type: ignore[arg-type]
    ):
        with pytest.raises(ValueError, match="이름"):
            call()
    assert vault.calls == []


@pytest.mark.parametrize("value", ["", None, 3])
def test_a_bad_value_is_a_value_error_and_is_not_stored(value: object) -> None:
    vault = FakeVault()
    with pytest.raises(ValueError, match="값"):
        vault.set(NAME, value)  # type: ignore[arg-type]
    assert vault.values == {}
    assert vault.calls == []


def test_a_failed_save_keeps_the_old_value_and_does_not_store_the_new_one() -> None:
    vault = FakeVault({NAME: VALUE})
    vault.fail_set()
    with pytest.raises(SecretStoreError) as caught:
        vault.set(NAME, "value-new")
    assert caught.value.error_name == ERROR_VAULT
    assert str(caught.value) == ERROR_VAULT
    assert vault.values == {NAME: VALUE}  # B23: 넘긴 값은 저장되지 않는다
    with pytest.raises(SecretStoreError):
        vault.set(OTHER, "value-2")
    assert vault.values == {NAME: VALUE}
    assert vault.get(NAME) == VALUE  # 읽기와 지우기는 그대로다
    vault.delete(OTHER)


def test_a_failed_delete_leaves_the_stored_value() -> None:
    vault = FakeVault({NAME: VALUE})
    vault.fail_delete(ERROR_VAULT_UNAVAILABLE)
    with pytest.raises(SecretStoreError) as caught:
        vault.delete(NAME)
    assert caught.value.error_name == ERROR_VAULT_UNAVAILABLE
    assert vault.values == {NAME: VALUE}  # B24: 저장된 값은 그대로 남는다
    vault.set(NAME, "value-2")  # 저장은 된다
    assert vault.get(NAME) == "value-2"


def test_a_failed_read() -> None:
    vault = FakeVault({NAME: VALUE})
    vault.fail_get()
    with pytest.raises(SecretStoreError) as caught:
        vault.get(NAME)
    assert caught.value.error_name == ERROR_VAULT
    vault.set(OTHER, "value-2")
    vault.delete(OTHER)
    vault.heal()
    assert vault.get(NAME) == VALUE


def test_failures_last_until_healed_or_for_a_set_number_of_calls() -> None:
    vault = FakeVault()
    vault.fail_set()
    for _ in range(3):
        with pytest.raises(SecretStoreError):
            vault.set(NAME, VALUE)
    vault.heal()
    vault.set(NAME, VALUE)
    assert vault.values == {NAME: VALUE}
    vault.fail_set(times=2)
    for _ in range(2):
        with pytest.raises(SecretStoreError):
            vault.set(OTHER, "value-2")
    vault.set(OTHER, "value-2")  # 정한 횟수가 지나면 다시 정상이다
    assert vault.values == {NAME: VALUE, OTHER: "value-2"}


def test_a_later_failure_replaces_the_earlier_one_of_the_same_kind() -> None:
    vault = FakeVault()
    vault.fail_set("보관소 오류")
    vault.fail_set(ERROR_VAULT_UNAVAILABLE, times=1)
    with pytest.raises(SecretStoreError) as caught:
        vault.set(NAME, VALUE)
    assert caught.value.error_name == ERROR_VAULT_UNAVAILABLE
    vault.set(NAME, VALUE)  # 마지막으로 정한 한 번이 끝났다


@pytest.mark.parametrize("times", [0, -1, True])
def test_bad_times_are_refused(times: int) -> None:
    with pytest.raises(ValueError, match="times"):
        FakeVault().fail_set(times=times)


def test_calls_are_recorded_in_order_without_values() -> None:
    vault = FakeVault()
    vault.set(NAME, VALUE)
    vault.get(NAME)
    vault.fail_delete()
    with pytest.raises(SecretStoreError):
        vault.delete(NAME)
    vault.heal()
    vault.delete(NAME)
    assert vault.calls == [
        VaultCall(SET, NAME, False),
        VaultCall(GET, NAME, False),
        VaultCall(DELETE, NAME, True),  # 실패한 부름도 남긴다
        VaultCall(DELETE, NAME, False),
    ]
    assert VALUE not in repr(vault.calls)


def test_the_value_is_not_in_the_repr_or_the_errors() -> None:
    vault = FakeVault({NAME: VALUE})
    vault.fail_set()
    with pytest.raises(SecretStoreError) as caught:
        vault.set(OTHER, "value-secret")
    assert VALUE not in repr(vault)
    shown = " ".join([repr(vault), str(caught.value), repr(caught.value.args)])
    assert "value-secret" not in shown
    assert VALUE not in shown
    again = pickle.loads(pickle.dumps(caught.value))
    assert again.error_name == ERROR_VAULT
    assert copy.copy(caught.value).error_name == ERROR_VAULT


def test_several_threads_can_use_it_together() -> None:
    vault = FakeVault()

    def work(index: int) -> None:
        for step in range(50):
            vault.set(f"name-{index}", f"value-{step}")
            vault.get(f"name-{index}")

    threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert vault.values == {f"name-{i}": "value-49" for i in range(4)}
    assert len(vault.calls) == 400
