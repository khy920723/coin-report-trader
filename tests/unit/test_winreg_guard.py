"""M1 명세 3.16.4의 `winreg` 쓰기 막기 장치(`tests/conftest.py`).

막는 것: 진짜 `winreg`의 쓰기 함수 `SetValue`, `SetValueEx`, `DeleteValue`, `DeleteKey`,
`CreateKey`, `CreateKeyEx`(부르면 그 시험이 실패한다). 막지 않는 것: 읽기 함수, 시험이
`sys.modules`에 넣은 가짜 `winreg` 모듈. Windows가 아니면 `winreg`가 없어 할 일이 없다.

장치의 동작은 운영체제와 상관없이 보려고, 장치의 새 사본을 흉내 낸 모듈(`types.ModuleType`)에 건다.
진짜 `winreg`를 보는 시험은 Windows에서만 돈다. 그 시험은 잘못된 핸들(`object()`)을 넘기므로
장치가 풀려 있어도 레지스트리에 쓰지 못하고 `TypeError`로 끝난다(그래서 그 시험이 실패한다).
"""

import contextlib
import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path
from typing import Any

import pytest

CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"
SPEC_FUNCTIONS = ("SetValue", "SetValueEx", "DeleteValue", "DeleteKey", "CreateKey", "CreateKeyEx")
READ_FUNCTIONS = ("OpenKey", "QueryValueEx", "EnumValue", "CloseKey")


def _conftest(winreg_guard: Any) -> types.ModuleType:
    return sys.modules[type(winreg_guard).__module__]


def _fake_winreg(calls: list[str]) -> types.ModuleType:
    """쓰기·읽기 함수가 부른 이름을 `calls`에 적는 흉내 낸 `winreg` 모듈."""
    module = types.ModuleType("winreg")
    for name in (*SPEC_FUNCTIONS, *READ_FUNCTIONS):

        def function(*args: Any, _name: str = name, **kwargs: Any) -> str:
            calls.append(_name)
            return _name

        setattr(module, name, function)
    module.HKEY_CURRENT_USER = 1  # type: ignore[attr-defined]
    module.REG_SZ = 1  # type: ignore[attr-defined]
    return module


@pytest.fixture
def guarded(winreg_guard: Any) -> Any:
    """흉내 낸 모듈에 건 장치의 새 사본. (장치, 모듈, 부른 이름) 꼴이다."""
    calls: list[str] = []
    module = _fake_winreg(calls)
    guard = type(winreg_guard)()
    patch = pytest.MonkeyPatch()
    guard.install(patch, module=module)
    yield guard, module, calls
    patch.undo()


def test_the_write_functions_are_the_six_of_the_spec(winreg_guard) -> None:
    assert _conftest(winreg_guard).WINREG_WRITE_FUNCTIONS == SPEC_FUNCTIONS


def test_the_session_guard_is_installed(winreg_guard) -> None:
    assert winreg_guard.is_installed()
    if sys.platform != "win32":
        assert winreg_guard.module is None  # Windows가 아니면 winreg가 없어 할 일이 없다
        return
    import winreg

    assert winreg_guard.module is winreg
    for name in SPEC_FUNCTIONS:
        function = getattr(winreg, name)
        assert function.__qualname__ == f"winreg.{name}"
        assert function.__module__.rpartition(".")[2] == "conftest"


@pytest.mark.parametrize("name", SPEC_FUNCTIONS)
def test_each_write_function_is_blocked_before_the_original_runs(guarded, name: str) -> None:
    guard, module, calls = guarded
    with guard.expect_blocked() as caught, pytest.raises(guard.Blocked):
        getattr(module, name)(module.HKEY_CURRENT_USER, "Software", 0, module.REG_SZ, "x")
    assert caught == [f"winreg.{name}을 막았다(실제 레지스트리를 쓰지 않는다)"]
    assert calls == []  # 원래 함수는 불리지 않았다
    assert guard.take_attempts() == []


def test_read_functions_are_not_blocked(guarded) -> None:
    guard, module, calls = guarded
    for name in READ_FUNCTIONS:
        assert getattr(module, name)(module.HKEY_CURRENT_USER, "Software") == name
    assert calls == list(READ_FUNCTIONS)
    assert guard.take_attempts() == []


def test_a_swallowed_call_is_still_recorded_and_counted_by_calls(guarded) -> None:
    guard, module, calls = guarded
    with contextlib.suppress(Exception):
        module.SetValueEx(module.HKEY_CURRENT_USER, "coin-report-trader", 0, module.REG_SZ, "x")
    with contextlib.suppress(Exception):
        module.DeleteValue(module.HKEY_CURRENT_USER, "coin-report-trader")
    assert guard.take_attempts() == [
        "winreg.SetValueEx을 막았다(실제 레지스트리를 쓰지 않는다)",
        "winreg.DeleteValue을 막았다(실제 레지스트리를 쓰지 않는다)",
    ]
    assert calls == []


def test_expect_blocked_without_a_blocked_call_is_a_failure(guarded) -> None:
    guard, _module, _calls = guarded
    with pytest.raises(AssertionError, match="막힌 winreg 쓰기가 없었다"), guard.expect_blocked():
        pass


def test_the_report_counts_the_calls(guarded) -> None:
    guard, module, _calls = guarded
    for _ in range(12):
        with contextlib.suppress(Exception):
            module.CreateKey(module.HKEY_CURRENT_USER, "Software")
    report = guard.report(guard.take_attempts())
    assert report.startswith("winreg 쓰기를 12번 막았다")
    assert "winreg_guard.expect_blocked()" in report
    assert report.endswith("- 외 2건")


def test_undo_restores_the_functions_and_the_guard_is_no_longer_installed(winreg_guard) -> None:
    calls: list[str] = []
    module = _fake_winreg(calls)
    originals = {name: getattr(module, name) for name in SPEC_FUNCTIONS}
    guard = type(winreg_guard)()
    patch = pytest.MonkeyPatch()
    guard.install(patch, module=module)
    assert guard.is_installed()
    assert all(getattr(module, name) is not originals[name] for name in SPEC_FUNCTIONS)
    patch.undo()
    assert not guard.is_installed()
    assert all(getattr(module, name) is originals[name] for name in SPEC_FUNCTIONS)


def test_a_guard_that_was_never_installed_is_not_installed(winreg_guard) -> None:
    assert not type(winreg_guard)().is_installed()


def test_a_fake_module_in_sys_modules_is_not_blocked(winreg_guard, monkeypatch) -> None:
    calls: list[str] = []
    fake = _fake_winreg(calls)
    monkeypatch.setitem(sys.modules, "winreg", fake)
    import winreg

    assert winreg is fake
    winreg.SetValueEx(winreg.HKEY_CURRENT_USER, "coin-report-trader", 0, winreg.REG_SZ, "x")
    winreg.DeleteValue(winreg.HKEY_CURRENT_USER, "coin-report-trader")
    assert calls == ["SetValueEx", "DeleteValue"]
    assert winreg_guard.take_attempts() == []


@pytest.mark.skipif(sys.platform != "win32", reason="진짜 winreg는 Windows에만 있다")
@pytest.mark.parametrize("name", SPEC_FUNCTIONS)
def test_the_real_winreg_write_functions_are_blocked(winreg_guard, name: str) -> None:
    import winreg

    # 잘못된 핸들이라 장치가 풀려 있어도 레지스트리에 쓰지 못하고 TypeError로 끝난다.
    with winreg_guard.expect_blocked() as caught, pytest.raises(winreg_guard.Blocked):
        getattr(winreg, name)(object(), "coin-report-trader-test")
    assert len(caught) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="진짜 winreg는 Windows에만 있다")
def test_the_real_winreg_read_functions_are_left_alone(winreg_guard) -> None:
    import winreg

    for name in READ_FUNCTIONS:
        assert getattr(winreg, name).__module__ != type(winreg_guard).__module__


FAKE_WINREG = textwrap.dedent(
    """
    # 자식 pytest가 Windows가 아닌 곳에서 장치를 걸 모듈. Windows에서는 내장 winreg가 앞선다.
    HKEY_CURRENT_USER = 1


    def _reached(*args, **kwargs):
        raise RuntimeError("원래의 쓰기 함수에 닿았다")


    SetValue = SetValueEx = DeleteValue = DeleteKey = CreateKey = CreateKeyEx = _reached
    """
)

CHILD_TESTS = textwrap.dedent(
    """
    import contextlib

    import winreg


    def test_swallows_a_winreg_write():
        with contextlib.suppress(Exception):
            winreg.DeleteValue(object(), "coin-report-trader-test")  # 잘못된 핸들


    def test_sees_the_count(winreg_guard):
        assert winreg_guard.blocked_by_test == {"test_child.py::test_swallows_a_winreg_write": 1}
    """
)


def test_a_swallowed_call_fails_its_test_at_teardown_and_is_counted(tmp_path: Path) -> None:
    # 이 conftest를 따로 된 pytest로 돌린다. Windows가 아니면 흉내 낸 winreg를 PYTHONPATH로 준다.
    child = tmp_path / "child"
    child.mkdir()
    (child / "conftest.py").write_bytes(CONFTEST.read_bytes())
    (child / "test_child.py").write_text(CHILD_TESTS, encoding="utf-8")
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "winreg.py").write_text(FAKE_WINREG, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = str(shim)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
         "--rootdir", str(child), str(child)],
        cwd=child,
        env=env,
        capture_output=True,
        timeout=300,
        check=False,
    )  # fmt: skip
    output = result.stdout.decode("utf-8", "replace") + result.stderr.decode("utf-8", "replace")
    assert result.returncode == 1, output
    assert "winreg 쓰기를 1번 막았다" in output, output
    # 첫 시험은 본문을 지나고 정리 단계에서 실패한다. 둘째 시험은 센 값(1)을 보고 통과한다.
    assert "ERROR at teardown of test_swallows_a_winreg_write" in output, output
    assert "2 passed, 1 error" in output, output
