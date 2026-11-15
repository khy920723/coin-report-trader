"""`--self-test` 점검과 보고서 시험. 실제 사용자 폴더를 쓰지 않는다."""

import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QMainWindow

import coin_report_trader
from coin_report_trader import ui
from coin_report_trader.app import self_test as st
from coin_report_trader.core import UiContext

BOM = b"\xef\xbb\xbf"
KEYS = ["import", "qt", "sqlite", "paths", "notice"]
ROOT_TOKEN = "%LOCALAPPDATA%" if sys.platform == "win32" else "$XDG_DATA_HOME"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    """자료 폴더와 현재 폴더를 `tmp_path` 아래로 바꾼다."""
    data_home = tmp_path / "share"
    data_home.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    key = "LOCALAPPDATA" if sys.platform == "win32" else "XDG_DATA_HOME"
    monkeypatch.setenv(key, str(data_home))
    monkeypatch.chdir(cwd)
    return SimpleNamespace(data_home=data_home, cwd=cwd)


@pytest.fixture
def offscreen_app(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> QApplication:
    """한 프로세스에 하나뿐인 QApplication을 offscreen으로 얻는다."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return request.getfixturevalue("qapp")


def _report(env: SimpleNamespace) -> str:
    raw = (env.cwd / st.REPORT_FILE_NAME).read_bytes()
    assert raw.startswith(BOM)
    return raw.decode("utf-8-sig")


def test_full_run_passes_and_writes_the_report(
    env: SimpleNamespace, offscreen_app: QApplication
) -> None:
    assert st.run_self_test() == 0
    text = _report(env)
    lines = text.splitlines()
    assert lines[0] == st.REPORT_TITLE
    assert lines[1] == f"버전: {coin_report_trader.__version__}"
    assert lines[2] == "실행 형태: 소스"
    assert lines[3].startswith("Python: 3.13.")
    assert lines[4].startswith("OS: ")
    assert lines[5].startswith("점검 시각(UTC): ")
    items = [line for line in lines if line.startswith("[")]
    assert [line.split(":")[0] for line in items] == [f"[통과] {key}" for key in KEYS]
    assert lines[-1] == "결과: 통과 (5/5)"
    # 경로는 줄인 형태로만 쓴다.
    assert str(env.data_home) not in text
    assert str(env.cwd) not in text
    assert str(env.data_home.parent) not in text
    assert f"{ROOT_TOKEN}" in text
    # 자료 폴더가 만들어졌다.
    assert (env.data_home / "coin-report-trader" / "data").is_dir()
    assert (env.data_home / "coin-report-trader" / "logs").is_dir()
    assert not (env.data_home / "coin-report-trader" / "data" / "app.db").exists()


def test_one_failing_check_is_reported_and_the_others_still_run(
    env: SimpleNamespace, offscreen_app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> str:
        raise RuntimeError(f"boom in {env.data_home}/x")

    monkeypatch.setattr(st, "check_sqlite", boom)
    assert st.run_self_test() == 1
    text = _report(env)
    lines = [line for line in text.splitlines() if line.startswith("[")]
    assert [line.split(":")[0] for line in lines] == [
        "[통과] import",
        "[통과] qt",
        "[실패] sqlite",
        "[통과] paths",
        "[통과] notice",
    ]
    assert f"RuntimeError: boom in {ROOT_TOKEN}" in text
    assert str(env.data_home) not in text
    assert text.splitlines()[-1] == "결과: 실패 (4/5)"


def test_system_exit_inside_a_check_is_a_failure(env: SimpleNamespace) -> None:
    def bail() -> str:
        raise SystemExit(3)

    results = st.run_checks([st.Check("a", bail), st.Check("b", lambda: "ok")])
    assert [(r.key, r.ok) for r in results] == [("a", False), ("b", True)]
    assert results[0].detail.startswith("SystemExit")


def test_a_failed_report_write_is_an_exit_code_one(env: SimpleNamespace) -> None:
    checks = [st.Check("a", lambda: "ok")]
    assert st.run_self_test(report_path=env.cwd / "no-such-folder" / "r.txt", checks=checks) == 1
    assert st.run_self_test(report_path=env.cwd / "r.txt", checks=checks) == 0
    assert (env.cwd / "r.txt").read_bytes().startswith(BOM)


def test_report_goes_to_stdout_when_there_is_one(
    env: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    assert st.run_self_test(checks=[st.Check("a", lambda: "ok")]) == 0
    assert "결과: 통과 (1/1)" in capsys.readouterr().out


def test_report_without_stdout(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    assert st.run_self_test(checks=[st.Check("a", lambda: "ok")]) == 0


def test_format_report_counts_and_orders_items() -> None:
    results = [st.CheckResult("a", True, "x"), st.CheckResult("b", False, "y")]
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    lines = st.format_report(results, now).splitlines()
    assert "점검 시각(UTC): 2026-01-02T03:04:05Z" in lines
    assert lines[-4:] == ["[통과] a: x", "[실패] b: y", "", "결과: 실패 (1/2)"]


def test_default_checks_follow_the_spec_order() -> None:
    assert [check.key for check in st.default_checks()] == KEYS


# --- import ---------------------------------------------------------------------------


def test_package_module_names_cover_every_subpackage_and_the_entry_point() -> None:
    names = set(st._package_module_names())
    assert {
        "coin_report_trader",
        "coin_report_trader.__main__",
        "coin_report_trader.core.screens",
        "coin_report_trader.config.paths",
        "coin_report_trader.storage.database",
        "coin_report_trader.app.self_test",
        "coin_report_trader.ui.main_window",
    } <= names


def test_import_check_passes() -> None:
    assert "PySide6" in st.check_imports()


def test_import_check_names_the_module_that_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    real = st.importlib.import_module

    def fake(name: str, package: str | None = None) -> object:
        if name == "coin_report_trader.core.screens":
            raise ImportError("missing piece")
        return real(name, package)

    monkeypatch.setattr(st.importlib, "import_module", fake)
    with pytest.raises(st.CheckFailed, match=r"coin_report_trader\.core\.screens.*missing piece"):
        st.check_imports()


# --- qt -------------------------------------------------------------------------------


def test_qt_check_passes_and_leaves_no_visible_window(offscreen_app: QApplication) -> None:
    assert "offscreen" in st.check_qt()
    assert not [w for w in QApplication.topLevelWidgets() if w.isVisible()]


def test_qt_check_fails_when_the_window_is_shown(
    offscreen_app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = ui.create_main_window

    def shown(context: UiContext) -> QMainWindow:
        window = real(context)
        window.show()
        return window

    monkeypatch.setattr(ui, "create_main_window", shown)
    with pytest.raises(st.CheckFailed, match="표시"):
        st.check_qt()
    assert not [w for w in QApplication.topLevelWidgets() if w.isVisible()]


def test_qt_check_fails_on_a_wrong_title(
    offscreen_app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = ui.create_main_window

    def retitled(context: UiContext) -> QMainWindow:
        window = real(context)
        window.setWindowTitle("Other")
        return window

    monkeypatch.setattr(ui, "create_main_window", retitled)
    with pytest.raises(st.CheckFailed, match="제목"):
        st.check_qt()


def test_find_offscreen_plugin(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    platforms = tmp_path / "plugins" / "platforms"
    platforms.mkdir(parents=True)
    (platforms / "libqxcb.so").write_bytes(b"")
    assert st.find_offscreen_plugin([str(empty), str(tmp_path / "plugins")]) is None
    (platforms / "libqoffscreen.so").write_bytes(b"")
    assert st.find_offscreen_plugin([str(empty), str(tmp_path / "plugins")]) == (
        platforms / "libqoffscreen.so"
    )


# --- sqlite, paths, notice --------------------------------------------------------------


def test_sqlite_check_uses_a_temporary_database_only(env: SimpleNamespace) -> None:
    detail = st.check_sqlite()
    assert "wal" in detail
    assert "synchronous 2" in detail
    assert "스키마 번호 0" in detail
    assert not (env.data_home / "coin-report-trader").exists()


def test_paths_check_creates_and_probes_the_folders(env: SimpleNamespace) -> None:
    detail = st.check_paths()
    assert detail.startswith(f"{ROOT_TOKEN}")
    assert str(env.data_home) not in detail
    root = env.data_home / "coin-report-trader"
    assert (root / "data").is_dir()
    assert (root / "logs").is_dir()
    assert not any(p.name.startswith(".self-test-") for p in root.rglob("*"))


def test_paths_check_fails_when_the_folder_cannot_be_made(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    key = "LOCALAPPDATA" if sys.platform == "win32" else "XDG_DATA_HOME"
    monkeypatch.setenv(key, str(blocker))
    with pytest.raises(st.CheckFailed) as caught:
        st.check_paths()
    assert str(tmp_path) not in str(caught.value)


def test_paths_check_fails_when_the_location_cannot_be_decided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_args: object) -> object:
        raise st.AppPathError("LOCALAPPDATA 환경 변수가 없거나 절대 경로가 아닙니다")

    monkeypatch.setattr(st, "resolve_app_paths", refuse)
    with pytest.raises(st.CheckFailed, match="LOCALAPPDATA"):
        st.check_paths()


def test_notice_check_is_not_judged_from_source() -> None:
    assert "판정하지 않음" in st.check_notice()


def _fake_exe(monkeypatch: pytest.MonkeyPatch, folder: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(folder / "coin-report-trader.exe"))


def test_notice_check_passes_when_both_are_next_to_the_exe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "NOTICE.txt").write_text("x")
    (tmp_path / "_internal" / "licenses").mkdir(parents=True)
    _fake_exe(monkeypatch, tmp_path)
    assert "있음" in st.check_notice()


@pytest.mark.parametrize(
    ("notice", "licenses", "missing"),
    [(False, True, "NOTICE.txt"), (True, False, "licenses"), (False, False, "NOTICE.txt")],
)
def test_notice_check_fails_when_something_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, notice: bool, licenses: bool, missing: str
) -> None:
    if notice:
        (tmp_path / "NOTICE.txt").write_text("x")
    if licenses:
        (tmp_path / "_internal" / "licenses").mkdir(parents=True)
    _fake_exe(monkeypatch, tmp_path)
    with pytest.raises(st.CheckFailed, match=missing) as caught:
        st.check_notice()
    assert str(tmp_path) not in str(caught.value)


def test_frozen_report_says_exe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert "실행 형태: exe" in st.format_report([st.CheckResult("a", True, "x")]).splitlines()
