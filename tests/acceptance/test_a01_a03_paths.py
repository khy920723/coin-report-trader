"""명세 7절 A1~A3: 자료 폴더 위치."""

from pathlib import Path, PureWindowsPath

import pytest

from coin_report_trader.config.paths import resolve_app_paths

# 만든 값이다(명세 3.5절). Ubuntu에서도 같은 값으로 Windows 규칙을 본다.
WIN_LOCALAPPDATA = "D:\\AppData\\Local"


@pytest.mark.scenario("A1")
def test_a1_windows_paths_follow_localappdata() -> None:
    paths = resolve_app_paths({"LOCALAPPDATA": WIN_LOCALAPPDATA}, "win32")
    root = PureWindowsPath(str(paths.root))
    assert root == PureWindowsPath(WIN_LOCALAPPDATA) / "coin-report-trader"
    assert PureWindowsPath(str(paths.database_file)) == root / "data" / "app.db"
    assert PureWindowsPath(str(paths.log_file)) == root / "logs" / "app.log"


@pytest.mark.scenario("A2")
def test_a2_linux_without_xdg_data_home_uses_home(tmp_path: Path) -> None:
    paths = resolve_app_paths({"HOME": str(tmp_path)}, "linux")
    assert paths.root == tmp_path / ".local" / "share" / "coin-report-trader"


@pytest.mark.scenario("A3")
def test_a3_windows_without_localappdata_is_an_error(tmp_path: Path) -> None:
    # 대신 쓸 만한 값(HOME, XDG_DATA_HOME)을 줘도 그쪽으로 가지 않는다.
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    home.mkdir()
    xdg.mkdir()
    env = {"HOME": str(home), "USERPROFILE": str(home), "XDG_DATA_HOME": str(xdg)}
    with pytest.raises(Exception):  # noqa: B017, PT011 - 명세는 오류의 종류를 정하지 않는다
        resolve_app_paths(env, "win32")
    assert list(home.iterdir()) == []
    assert list(xdg.iterdir()) == []
