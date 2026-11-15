"""자료 폴더 위치와 줄인 경로 시험. 실제 사용자 폴더를 쓰지 않는다."""

import dataclasses
import sys
from pathlib import Path, PureWindowsPath

import pytest

from coin_report_trader.config.paths import (
    AppPathError,
    AppPaths,
    describe_error,
    display_path,
    display_text,
    resolve_app_paths,
)

APP = "coin-report-trader"
WIN_BASE = "D:\\AppData\\Local"


def native_env(base: Path) -> dict[str, str]:
    """지금 OS에서 자료 폴더를 `base` 아래로 보내는 환경 변수."""
    key = "LOCALAPPDATA" if sys.platform == "win32" else "XDG_DATA_HOME"
    return {key: str(base)}


def test_windows_root_uses_localappdata() -> None:
    paths = resolve_app_paths({"LOCALAPPDATA": WIN_BASE}, "win32")
    assert PureWindowsPath(str(paths.root)) == PureWindowsPath(f"{WIN_BASE}\\{APP}")
    assert PureWindowsPath(str(paths.data_dir)) == PureWindowsPath(f"{WIN_BASE}\\{APP}\\data")
    assert PureWindowsPath(str(paths.logs_dir)) == PureWindowsPath(f"{WIN_BASE}\\{APP}\\logs")
    assert PureWindowsPath(str(paths.database_file)) == PureWindowsPath(
        f"{WIN_BASE}\\{APP}\\data\\app.db"
    )
    assert PureWindowsPath(str(paths.log_file)) == PureWindowsPath(
        f"{WIN_BASE}\\{APP}\\logs\\app.log"
    )


def test_linux_without_xdg_uses_home(tmp_path: Path) -> None:
    paths = resolve_app_paths({"HOME": str(tmp_path)}, "linux")
    assert paths.root == tmp_path / ".local" / "share" / APP
    assert paths.database_file == paths.root / "data" / "app.db"
    assert paths.log_file == paths.root / "logs" / "app.log"


def test_linux_uses_absolute_xdg_data_home() -> None:
    paths = resolve_app_paths({"XDG_DATA_HOME": "/srv/u", "HOME": "/srv/h"}, "linux")
    assert paths.root == Path("/srv/u") / APP


@pytest.mark.parametrize("xdg", ["", "relative/dir", "D:\\AppData\\Local"])
def test_linux_ignores_empty_or_non_absolute_xdg(xdg: str) -> None:
    paths = resolve_app_paths({"XDG_DATA_HOME": xdg, "HOME": "/srv/h"}, "linux")
    assert paths.root == Path("/srv/h") / ".local" / "share" / APP


def test_home_falls_back_to_path_home_only_when_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert resolve_app_paths({}, "linux").root == tmp_path / ".local" / "share" / APP
    assert resolve_app_paths({"HOME": "/srv/h"}, "linux").root == (
        Path("/srv/h") / ".local" / "share" / APP
    )


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"LOCALAPPDATA": ""},
        {"LOCALAPPDATA": "AppData\\Local"},
        # 절대 경로인지는 실행 중인 OS가 아니라 platform의 규칙으로 본다.
        {"LOCALAPPDATA": "/srv/u"},
        # 다른 폴더로 대신하지 않는다.
        {"XDG_DATA_HOME": "/srv/u", "HOME": "/srv/h"},
    ],
)
def test_windows_without_absolute_localappdata_is_an_error(env: dict[str, str]) -> None:
    with pytest.raises(AppPathError):
        resolve_app_paths(env, "win32")


def test_defaults_use_the_current_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key, value in native_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    assert resolve_app_paths().root == tmp_path / APP


def test_ensure_creates_folders_and_can_be_repeated(tmp_path: Path) -> None:
    paths = resolve_app_paths(native_env(tmp_path))
    assert not paths.root.exists()
    paths.ensure()
    paths.ensure()
    assert paths.root == tmp_path / APP
    assert paths.data_dir.is_dir()
    assert paths.logs_dir.is_dir()
    assert not paths.database_file.exists()
    assert not paths.log_file.exists()


def test_app_paths_is_frozen(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        paths.root = tmp_path / "x"  # type: ignore[misc]


# --- 줄인 경로 ----------------------------------------------------------------------


def test_display_windows_shortens_localappdata() -> None:
    env = {"LOCALAPPDATA": WIN_BASE, "HOME": "D:\\Profiles\\p"}
    assert display_path(f"{WIN_BASE}\\{APP}", env, "win32") == f"%LOCALAPPDATA%\\{APP}"
    assert display_path(f"{WIN_BASE.lower()}\\{APP}\\data", env, "win32") == (
        f"%LOCALAPPDATA%\\{APP}\\data"
    )
    assert display_path(WIN_BASE, env, "win32") == "%LOCALAPPDATA%"


def test_display_windows_shortens_home_then_last_name() -> None:
    env = {"LOCALAPPDATA": WIN_BASE, "HOME": "D:\\Profiles\\p"}
    assert display_path("D:\\Profiles\\p\\work\\x", env, "win32") == "~\\work\\x"
    assert display_path(f"E:\\elsewhere\\{APP}", env, "win32") == APP


def test_display_linux_shortens_xdg_then_home_then_last_name() -> None:
    env = {"XDG_DATA_HOME": "/srv/u", "HOME": "/srv/h"}
    assert display_path(f"/srv/u/{APP}", env, "linux") == f"$XDG_DATA_HOME/{APP}"
    assert display_path("/srv/u", env, "linux") == "$XDG_DATA_HOME"
    assert display_path(f"/srv/h/.local/share/{APP}", env, "linux") == f"~/.local/share/{APP}"
    assert display_path(f"/opt/other/{APP}", env, "linux") == APP


def test_display_linux_home_default_root() -> None:
    root = resolve_app_paths({"HOME": "/srv/h"}, "linux").root
    # Path를 그대로 넘긴다. Windows에서 str()은 역슬래시가 되어 linux 규칙의 경로가 아니다.
    assert display_path(root, {"HOME": "/srv/h"}, "linux") == f"~/.local/share/{APP}"


def test_display_does_not_match_a_longer_folder_name() -> None:
    env = {"XDG_DATA_HOME": "/srv/u", "HOME": "/srv/h"}
    assert display_path("/srv/u2/x", env, "linux") == "x"
    assert display_path("/srv/h2/x", env, "linux") == "x"


def test_display_ignores_a_non_absolute_xdg() -> None:
    env = {"XDG_DATA_HOME": "relative", "HOME": "/srv/h"}
    assert display_path("/srv/h/a", env, "linux") == "~/a"


def test_display_defaults_use_the_current_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for key, value in native_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    shown = display_path(tmp_path / APP)
    assert shown in (f"$XDG_DATA_HOME/{APP}", f"%LOCALAPPDATA%\\{APP}")
    assert str(tmp_path) not in shown


def test_display_text_shortens_paths_inside_messages() -> None:
    env = {"XDG_DATA_HOME": "/srv/u", "HOME": "/srv/h"}
    text = "denied: '/srv/u/coin-report-trader/data' and '/srv/h/x' ('/srv/h2')"
    assert display_text(text, env, "linux") == (
        "denied: '$XDG_DATA_HOME/coin-report-trader/data' and '~/x' ('/srv/h2')"
    )


def test_display_text_leaves_a_root_home_alone() -> None:
    # 루트 폴더(`/`)가 집 폴더여도 글 속의 모든 `/`를 `~`로 바꾸지 않는다.
    text = "denied: '/' and x / y"
    assert display_text(text, {"HOME": "/"}, "linux") == text
    assert display_text(text, {"HOME": "/", "XDG_DATA_HOME": "/"}, "linux") == text
    assert display_text("C:\\x D:\\y", {"HOME": "C:\\", "LOCALAPPDATA": "D:\\"}, "win32") == (
        "C:\\x D:\\y"
    )


def test_display_ignores_a_relative_home() -> None:
    assert display_text("a/b x", {"HOME": "a"}, "linux") == "a/b x"
    assert display_path("a/b", {"HOME": "a"}, "linux") == "b"


def test_display_follows_the_platform_rules_not_the_running_os() -> None:
    # Windows에서 linux 규칙의 경로를 Path로 만들면 PureWindowsPath처럼 다뤄진다.
    env = {"XDG_DATA_HOME": "/srv/u", "HOME": "/srv/h"}
    assert display_path(PureWindowsPath("/srv/h/a"), env, "linux") == "~/a"
    assert display_path(PureWindowsPath(f"/srv/u/{APP}"), env, "linux") == f"$XDG_DATA_HOME/{APP}"
    # 반대로 Linux에서 만든 win32 규칙의 경로(`D:\...` 뒤에 `/`가 섞인 글자)도 같은 결과다.
    root = resolve_app_paths({"LOCALAPPDATA": WIN_BASE}, "win32").root
    assert display_path(root, {"LOCALAPPDATA": WIN_BASE}, "win32") == f"%LOCALAPPDATA%\\{APP}"


def test_home_text_is_used_as_given_whatever_the_running_os() -> None:
    env = {"HOME": "/srv/h"}
    assert display_text("x /srv/h/a y", env, "linux") == "x ~/a y"
    assert display_path("/srv/h/.local/share/coin-report-trader", env, "linux") == (
        "~/.local/share/coin-report-trader"
    )


# --- Windows의 오류 문구(Linux에서도 돈다) ------------------------------------------------

WIN_ENV = {"LOCALAPPDATA": WIN_BASE, "HOME": "E:\\Profiles\\p"}


def _leftovers(text: str, *names: str) -> list[str]:
    """줄인 결과에 남은 원래 접두어의 이름 조각(대신 쓴 `%LOCALAPPDATA%` 글자는 뺀다)."""
    lowered = text.replace("%LOCALAPPDATA%", "").lower()
    return [name for name in names if name.lower() in lowered]


def test_windows_oserror_text_with_doubled_backslashes_is_shortened() -> None:
    exc = PermissionError(13, "Permission denied", f"{WIN_BASE}\\{APP}\\data")
    assert "\\\\" in str(exc)  # str(OSError)는 파일 이름을 역슬래시가 두 번 든 꼴로 쓴다.
    shown = display_text(str(exc), WIN_ENV, "win32")
    assert shown.startswith("[Errno 13] Permission denied: '%LOCALAPPDATA%")
    assert shown.endswith(f"{APP}\\\\data'")
    assert _leftovers(shown, "D:\\", "D:/", "AppData", "Local\\") == []


@pytest.mark.parametrize(
    "written",
    [
        "d:\\appdata\\local\\x",  # 대소문자만 다르다
        "D:\\APPDATA\\LOCAL\\x",
        "D:/AppData/Local/x",  # /가 섞였다
        "D:\\AppData/Local\\x",
        "D:\\\\AppData\\\\Local\\\\x",  # 역슬래시 둘
        "D://AppData//Local//x",
    ],
)
def test_windows_prefix_forms_are_all_shortened(written: str) -> None:
    shown = display_text(f"cannot open '{written}'", WIN_ENV, "win32")
    assert shown.startswith("cannot open '%LOCALAPPDATA%")
    assert shown.endswith("x'")
    assert _leftovers(shown, "D:\\", "D:/", "AppData") == []


@pytest.mark.parametrize("base", [f"{WIN_BASE}\\", f"{WIN_BASE}/", f"{WIN_BASE}\\\\"])
def test_windows_prefix_ending_with_a_separator_is_shortened(base: str) -> None:
    env = {"LOCALAPPDATA": base}
    for written in (f"{WIN_BASE}\\x", f"{WIN_BASE}\\\\x", f"{WIN_BASE.lower()}/x"):
        shown = display_text(f"'{written}'", env, "win32")
        assert shown.startswith("'%LOCALAPPDATA%")
        assert _leftovers(shown, "D:\\", "D:/", "AppData") == []


def test_windows_home_forms_are_shortened_and_the_longer_prefix_wins() -> None:
    env = {"LOCALAPPDATA": "E:\\Profiles\\p\\AppData\\Local", "HOME": "E:\\Profiles\\p"}
    shown = display_text("a e:/profiles/p/AppData/Local/x b E:\\\\Profiles\\\\p\\\\y", env, "win32")
    assert shown == "a %LOCALAPPDATA%/x b ~\\\\y"


def test_windows_text_keeps_other_folders_and_longer_names() -> None:
    text = "D:\\AppData\\LocalLow\\x and E:\\Other"
    assert display_text(text, WIN_ENV, "win32") == text


def test_non_windows_text_is_case_sensitive_and_ignores_a_trailing_slash() -> None:
    env = {"XDG_DATA_HOME": "/srv/u/", "HOME": "/srv/h/"}
    assert display_text("/srv/u/a /SRV/U/b /srv/h//c", env, "linux") == (
        "$XDG_DATA_HOME/a /SRV/U/b ~//c"
    )


def test_describe_error_for_oserror_uses_strerror_and_shortened_names() -> None:
    exc = PermissionError(13, "Permission denied", f"{WIN_BASE}\\{APP}\\data")
    assert describe_error(exc, WIN_ENV, "win32") == (
        f"PermissionError: Permission denied (%LOCALAPPDATA%\\{APP}\\data)"
    )


def test_describe_error_lists_both_file_names() -> None:
    exc = OSError(1, "Operation not permitted", "/srv/u/a", None, "/srv/h/b")
    env = {"XDG_DATA_HOME": "/srv/u", "HOME": "/srv/h"}
    assert describe_error(exc, env, "linux") == (
        "PermissionError: Operation not permitted ($XDG_DATA_HOME/a, ~/b)"
    )


def test_describe_error_without_a_file_name_or_strerror() -> None:
    env = {"HOME": "/srv/h"}
    assert describe_error(OSError(5, "Input/output error"), env, "linux") == (
        "OSError: Input/output error"
    )
    assert describe_error(OSError("opening /srv/h/x failed"), env, "linux") == (
        "OSError: opening ~/x failed"
    )
    assert describe_error(ValueError("bad /srv/h/x"), env, "linux") == "ValueError: bad ~/x"


def test_describe_error_accepts_bytes_names() -> None:
    exc = FileNotFoundError(2, "No such file or directory", b"/srv/h/x")
    assert describe_error(exc, {"HOME": "/srv/h"}, "linux") == (
        "FileNotFoundError: No such file or directory (~/x)"
    )


# --- 집 폴더를 정할 수 없는 환경 ---------------------------------------------------------


@pytest.fixture
def no_home(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(cls: type[Path]) -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(refuse))


def test_unknown_home_does_not_break_display(no_home: None) -> None:
    env = {"XDG_DATA_HOME": "/srv/u"}
    assert display_path("/srv/u/x", env, "linux") == "$XDG_DATA_HOME/x"
    assert display_path("/srv/h/x", env, "linux") == "x"
    assert display_text("/srv/u/x /srv/h/y", env, "linux") == "$XDG_DATA_HOME/x /srv/h/y"
    assert display_path("x", {}, "linux") == "x"
    assert describe_error(OSError(13, "Permission denied", "/srv/h/z"), {}, "linux") == (
        "PermissionError: Permission denied (z)"
    )


def test_unknown_home_is_a_path_error_only_when_it_is_needed(no_home: None) -> None:
    assert resolve_app_paths({"XDG_DATA_HOME": "/srv/u"}, "linux").root == Path("/srv/u") / APP
    assert resolve_app_paths({"LOCALAPPDATA": WIN_BASE}, "win32").root.name == APP
    with pytest.raises(AppPathError, match="집 폴더"):
        resolve_app_paths({}, "linux")
    with pytest.raises(AppPathError):
        resolve_app_paths({"XDG_DATA_HOME": "relative"}, "linux")
