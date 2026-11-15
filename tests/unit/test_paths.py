"""자료 폴더 위치와 줄인 경로 시험. 실제 사용자 폴더를 쓰지 않는다."""

import dataclasses
import sys
from pathlib import Path, PureWindowsPath

import pytest

from coin_report_trader.config.paths import (
    AppPathError,
    AppPaths,
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
    assert display_text("a/b/c", {"HOME": "/"}, "linux") == "a/b/c"


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
