"""`run_gui`와 `main([])` 시험. 창이 뜬 직후 이벤트 루프를 끝낸다."""

import logging
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel

from coin_report_trader import __main__ as entry
from coin_report_trader import ui
from coin_report_trader.app import application, run_gui
from coin_report_trader.config.paths import resolve_app_paths
from coin_report_trader.core import StorageError
from coin_report_trader.storage.database import open_database

ROOT_TOKEN = "%LOCALAPPDATA%" if sys.platform == "win32" else "$XDG_DATA_HOME"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request: pytest.FixtureRequest) -> Any:
    """자료 폴더를 `tmp_path` 아래로 바꾸고 offscreen QApplication을 얻는다."""
    data_home = tmp_path / "share"
    data_home.mkdir()
    key = "LOCALAPPDATA" if sys.platform == "win32" else "XDG_DATA_HOME"
    monkeypatch.setenv(key, str(data_home))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qapp = request.getfixturevalue("qapp")
    return SimpleNamespace(data_home=data_home, root=data_home / "coin-report-trader", app=qapp)


def _quit_after_look(app: QApplication, look: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """이벤트 루프가 돌기 시작하면 한 번 보고 끝낸다. 보지 못해도 10초 뒤에는 끝낸다."""
    seen: dict[str, Any] = {}
    safety = QTimer()
    safety.setSingleShot(True)
    safety.timeout.connect(app.quit)
    safety.start(10_000)
    # 이벤트 루프가 끝난 뒤에도 타이머가 남지 않게 seen에 붙잡아 둔다.
    seen["_safety"] = safety

    def tick() -> None:
        try:
            seen.update(look())
        except Exception as exc:  # 슬롯의 예외는 Qt가 삼키므로 시험이 읽게 남긴다.
            seen["error"] = repr(exc)
        finally:
            app.quit()

    QTimer.singleShot(0, tick)
    return seen


def _look_at_window() -> dict[str, Any]:
    windows = [w for w in QApplication.topLevelWidgets() if w.objectName() == "mainWindow"]
    assert len(windows) == 1
    window = windows[0]
    status = window.findChild(QLabel, "statusLine")
    assert status is not None
    return {"visible": window.isVisible(), "title": window.windowTitle(), "status": status.text()}


def _finish(seen: dict[str, Any]) -> dict[str, Any]:
    seen.pop("_safety").stop()
    assert "error" not in seen, seen["error"]
    return seen


def _log_text(env: Any) -> str:
    return (env.root / "logs" / "app.log").read_text(encoding="utf-8")


def test_run_gui_shows_the_window_and_logs_start_and_stop(env: Any) -> None:
    seen = _quit_after_look(env.app, _look_at_window)
    assert run_gui([]) == 0
    seen = _finish(seen)
    assert seen["visible"] is True
    assert seen["title"] == "Coin Report Trader"
    assert seen["status"].startswith("바탕 버전 0.0.1 · 매매 기능 없음 · 자료 폴더 ")
    assert seen["status"].endswith(
        f"{ROOT_TOKEN}{'\\' if sys.platform == 'win32' else '/'}coin-report-trader"
    )
    assert str(env.data_home) not in seen["status"]
    for folder in (env.root, env.root / "data", env.root / "logs"):
        assert folder.is_dir()
    assert (env.root / "data" / "app.db").is_file()
    log = _log_text(env)
    assert "시작: 버전 0.0.1" in log
    assert "종료: 코드 0" in log
    assert log.index("시작") < log.index("종료")
    assert str(env.data_home) not in log


def test_run_gui_uses_the_existing_application_and_cleans_up(env: Any) -> None:
    seen = _quit_after_look(env.app, lambda: {"same": QApplication.instance() is env.app})
    assert run_gui([]) == 0
    assert _finish(seen)["same"] is True
    assert QApplication.instance() is env.app
    assert logging.getLogger("coin_report_trader").handlers == []
    # 로그 파일이 닫혔으므로 지울 수 있다(Windows).
    (env.root / "logs" / "app.log").unlink()


def test_main_without_options_runs_the_window(env: Any) -> None:
    seen = _quit_after_look(env.app, _look_at_window)
    assert entry.main([]) == 0
    assert _finish(seen)["visible"] is True
    assert (env.root / "data" / "app.db").is_file()
    assert "종료: 코드 0" in _log_text(env)


def test_run_twice_reuses_the_database_and_appends_to_the_log(env: Any) -> None:
    for _ in range(2):
        seen = _quit_after_look(env.app, _look_at_window)
        assert run_gui([]) == 0
        _finish(seen)
    assert _log_text(env).count("종료: 코드 0") == 2


def _make_newer_database(env: Any) -> None:
    paths = resolve_app_paths()
    paths.ensure()
    conn = open_database(paths.database_file)
    try:
        conn.execute("PRAGMA user_version=1")
    finally:
        conn.close()


def test_a_newer_database_stops_before_the_window(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_window(_context: object) -> object:
        raise AssertionError("창을 만들면 안 된다")

    monkeypatch.setattr(ui, "create_main_window", no_window)
    _make_newer_database(env)
    with pytest.raises(StorageError, match="스키마 번호"):
        run_gui([])
    log = _log_text(env)
    assert "오류: StorageError" in log
    assert "종료: 코드 1" in log
    assert logging.getLogger("coin_report_trader").handlers == []


def test_main_turns_a_startup_error_into_exit_code_one(
    env: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    _make_newer_database(env)
    assert entry.main([]) == 1
    err = capsys.readouterr().err
    assert "StorageError" in err
    assert str(env.data_home) not in err


def test_database_open_failure_is_logged_with_shortened_paths(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(path: Path) -> object:
        raise StorageError(f"cannot open {path}")

    monkeypatch.setattr(application, "open_database", refuse)
    with pytest.raises(StorageError):
        run_gui([])
    log = _log_text(env)
    assert f"오류: StorageError: cannot open {ROOT_TOKEN}" in log
    assert str(env.data_home) not in log


def test_an_unusable_data_location_fails_before_any_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    key = "LOCALAPPDATA" if sys.platform == "win32" else "XDG_DATA_HOME"
    monkeypatch.setenv(key, str(blocker))
    with pytest.raises(OSError):  # noqa: PT011
        run_gui([])
    assert entry.main([]) == 1


def test_closing_the_window_ends_the_program(env: Any) -> None:
    """명세 2절 6번: 창을 닫으면 프로그램이 끝난다. `quit()`을 부르지 않는다."""
    fired = {"safety": False}
    safety = QTimer()
    safety.setSingleShot(True)
    safety.timeout.connect(lambda: (fired.update(safety=True), env.app.quit()))
    safety.start(10_000)
    seen: dict[str, Any] = {}

    def close_the_window() -> None:
        try:
            windows = [w for w in QApplication.topLevelWidgets() if w.objectName() == "mainWindow"]
            visible = [w for w in windows if w.isVisible()]
            seen["visible"] = len(visible)
            for window in visible:
                window.close()
        except Exception as exc:
            seen["error"] = repr(exc)
            env.app.quit()

    QTimer.singleShot(0, close_the_window)
    code = run_gui([])
    safety.stop()
    assert "error" not in seen, seen["error"]
    assert seen["visible"] == 1
    assert fired["safety"] is False, "창을 닫았는데 프로그램이 끝나지 않아 안전 타이머가 울렸다"
    assert code == 0
    assert "종료: 코드 0" in _log_text(env)


def test_run_gui_makes_the_last_closed_window_quit_the_program(env: Any) -> None:
    env.app.setQuitOnLastWindowClosed(False)
    seen = _quit_after_look(env.app, lambda: {"quit": QApplication.quitOnLastWindowClosed()})
    assert run_gui([]) == 0
    assert _finish(seen)["quit"] is True


def test_an_oserror_is_logged_without_the_path(env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = env.root / "data" / "app.db"

    def denied(path: Path) -> object:
        raise PermissionError(13, "Permission denied", str(secret))

    monkeypatch.setattr(application, "open_database", denied)
    with pytest.raises(PermissionError):
        run_gui([])
    log = _log_text(env)
    assert f"오류: PermissionError: Permission denied ({ROOT_TOKEN}" in log
    for form in (str(env.data_home), env.data_home.as_posix(), repr(str(secret))[1:-1]):
        assert form not in log
