"""`app/autostart.py`의 `WindowsAutostart`(M1 명세 3.16.2). 실제 레지스트리는 쓰지 않는다.

`winreg`를 흉내 낸 가짜 모듈을 `sys.modules`에 넣는다(메서드 안에서 import하므로). `sys.frozen`과
`sys.platform`은 시험이 정한다. 값은 모두 만든 값이다.
"""

import sys
from pathlib import Path
from types import ModuleType, TracebackType
from typing import Any

import pytest

from coin_report_trader.app import autostart
from coin_report_trader.app.autostart import RUN_KEY, VALUE_NAME, WindowsAutostart, current_command
from coin_report_trader.core import AutostartStore

COMMAND = '"C:\\made\\up\\coin-report-trader.exe"'
OTHER = "다른 앱 값"


class _Key:
    def __init__(self, module: "FakeWinreg", path: str, access: int) -> None:
        self.module, self.path, self.access = module, path, access

    def __enter__(self) -> "_Key":
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.module.closed += 1


class FakeWinreg(ModuleType):
    """`winreg`의 쓰는 부분만 흉내 낸다. 키 하나(`RUN_KEY`)의 값들을 사전으로 둔다."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self, values: dict[str, str] | None = None, key_exists: bool = True) -> None:
        super().__init__("winreg")
        self.values: dict[str, str] = {} if values is None else dict(values)
        self.key_exists = key_exists
        self.calls: list[tuple[Any, ...]] = []
        self.closed = 0

    def _open(self, root: str, path: str, access: int, *, create: bool) -> _Key:
        if root != self.HKEY_CURRENT_USER or path != RUN_KEY:
            raise AssertionError(f"뜻밖의 키: {root} {path}")
        if not self.key_exists:
            if not create:
                raise FileNotFoundError(path)
            self.key_exists = True
        return _Key(self, path, access)

    def OpenKey(self, root: str, path: str, reserved: int, access: int) -> _Key:
        self.calls.append(("OpenKey", root, path, reserved, access))
        return self._open(root, path, access, create=False)

    def CreateKeyEx(self, root: str, path: str, reserved: int, access: int) -> _Key:
        self.calls.append(("CreateKeyEx", root, path, reserved, access))
        return self._open(root, path, access, create=True)

    def QueryValueEx(self, key: _Key, name: str) -> tuple[Any, int]:
        self.calls.append(("QueryValueEx", name))
        assert key.access & self.KEY_READ
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key: _Key, name: str, reserved: int, kind: int, value: str) -> None:
        self.calls.append(("SetValueEx", name, reserved, kind, value))
        assert key.access & self.KEY_SET_VALUE
        self.values[name] = value

    def DeleteValue(self, key: _Key, name: str) -> None:
        self.calls.append(("DeleteValue", name))
        assert key.access & self.KEY_SET_VALUE
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


@pytest.fixture
def frozen_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")


def install(monkeypatch: pytest.MonkeyPatch, module: FakeWinreg) -> FakeWinreg:
    monkeypatch.setitem(sys.modules, "winreg", module)
    return module


def test_it_keeps_the_port_and_the_names_of_the_spec() -> None:
    assert isinstance(WindowsAutostart(), AutostartStore)
    assert RUN_KEY == r"Software\Microsoft\Windows\CurrentVersion\Run"
    assert VALUE_NAME == "coin-report-trader"


def test_the_command_is_the_quoted_absolute_path_of_the_exe_without_arguments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    exe = tmp_path / "coin-report-trader.exe"
    monkeypatch.setattr(sys, "executable", str(exe))
    assert current_command() == f'"{exe}"'


@pytest.mark.parametrize(
    ("frozen", "platform", "expected"),
    [
        (True, "win32", True),
        (False, "win32", False),
        (True, "linux", False),
        (False, "linux", False),
    ],
)
def test_it_is_supported_only_for_a_frozen_exe_on_windows(
    monkeypatch: pytest.MonkeyPatch, frozen: bool, platform: str, expected: bool
) -> None:
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    assert WindowsAutostart().supported() is expected


def test_an_unsupported_run_neither_reads_nor_writes_the_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = install(monkeypatch, FakeWinreg({VALUE_NAME: COMMAND}))
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    store = WindowsAutostart()
    assert store.read() is None
    store.write(COMMAND)
    store.delete()
    assert fake.calls == []
    assert fake.values == {VALUE_NAME: COMMAND}


def test_the_winreg_module_is_imported_inside_the_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Path(autostart.__file__).read_text(encoding="utf-8")
    head = source.split("class WindowsAutostart")[0]
    assert "import winreg" not in head  # 모듈을 import하는 때에는 winreg를 부르지 않는다


def test_read_returns_the_registered_text_and_none_when_missing(
    monkeypatch: pytest.MonkeyPatch, frozen_windows: None
) -> None:
    fake = install(monkeypatch, FakeWinreg({VALUE_NAME: COMMAND}))
    assert WindowsAutostart().read() == COMMAND
    assert fake.calls[0] == ("OpenKey", "HKCU", RUN_KEY, 0, FakeWinreg.KEY_READ)
    assert fake.calls[1] == ("QueryValueEx", VALUE_NAME)
    install(monkeypatch, FakeWinreg({"다른": OTHER}))
    assert WindowsAutostart().read() is None
    install(monkeypatch, FakeWinreg(key_exists=False))
    assert WindowsAutostart().read() is None  # 키가 없어도 없는 값이다


def test_write_sets_the_value_name_and_text_and_keeps_the_other_values(
    monkeypatch: pytest.MonkeyPatch, frozen_windows: None
) -> None:
    fake = install(monkeypatch, FakeWinreg({"다른": OTHER, VALUE_NAME: "옛 글"}))
    WindowsAutostart().write(COMMAND)
    assert fake.values == {"다른": OTHER, VALUE_NAME: COMMAND}
    assert ("SetValueEx", VALUE_NAME, 0, FakeWinreg.REG_SZ, COMMAND) in fake.calls
    assert {call[0] for call in fake.calls} == {"CreateKeyEx", "SetValueEx"}
    assert fake.closed == 1


def test_delete_removes_only_its_value_and_a_missing_value_or_key_is_a_success(
    monkeypatch: pytest.MonkeyPatch, frozen_windows: None
) -> None:
    fake = install(monkeypatch, FakeWinreg({"다른": OTHER, VALUE_NAME: COMMAND}))
    WindowsAutostart().delete()
    assert fake.values == {"다른": OTHER}
    WindowsAutostart().delete()  # 값이 없다
    install(monkeypatch, FakeWinreg(key_exists=False))
    WindowsAutostart().delete()  # 키가 없다


def test_it_never_touches_the_startup_approved_key(
    monkeypatch: pytest.MonkeyPatch, frozen_windows: None
) -> None:
    fake = install(monkeypatch, FakeWinreg({VALUE_NAME: COMMAND}))
    store = WindowsAutostart()
    store.read()
    store.write(COMMAND)
    store.delete()
    paths = {call[2] for call in fake.calls if call[0] in ("OpenKey", "CreateKeyEx")}
    assert paths == {RUN_KEY}
    assert (
        "StartupApproved"
        not in Path(autostart.__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    )  # 꺼짐 표시는 코드가 다루지 않는다(설명 글에만 있다)


def test_an_os_error_of_the_registry_goes_up_as_it_is(
    monkeypatch: pytest.MonkeyPatch, frozen_windows: None
) -> None:
    fake = install(monkeypatch, FakeWinreg())

    def deny(*args: Any) -> None:
        raise PermissionError("막힘")

    fake.SetValueEx = deny  # type: ignore[method-assign, assignment]
    with pytest.raises(OSError, match="막힘"):
        WindowsAutostart().write(COMMAND)
