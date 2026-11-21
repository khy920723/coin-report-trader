"""`--self-test` 점검과 보고서 시험. 실제 사용자 폴더를 쓰지 않는다."""

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QMainWindow

import coin_report_trader
from coin_report_trader import ui
from coin_report_trader.app import self_test as st
from coin_report_trader.core import UiContext

BOM = b"\xef\xbb\xbf"
KEYS = ["import", "qt", "sqlite", "paths", "notice", "tls", "vault"]
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
    # 명세 3.8, 3.11: 머리 여섯 줄 + 항목 일곱 + 결과 한 줄. 빈 줄은 없다.
    assert len(lines) == 6 + 7 + 1
    assert "" not in lines
    items = lines[6:13]
    assert [line.split(":")[0] for line in items] == [f"[통과] {key}" for key in KEYS]
    assert lines[-1] == "결과: 통과 (7/7)"
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
        "[통과] tls",
        "[통과] vault",
    ]
    assert f"RuntimeError: boom in {ROOT_TOKEN}" in text
    assert str(env.data_home) not in text
    assert text.splitlines()[-1] == "결과: 실패 (6/7)"


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
    assert lines[-3:] == ["[통과] a: x", "[실패] b: y", "결과: 실패 (1/2)"]
    assert len(lines) == 6 + 2 + 1


def test_format_report_puts_each_item_on_one_line() -> None:
    results = [st.CheckResult("a", False, "first\nsecond\r\nthird"), st.CheckResult("b", True, "")]
    lines = st.format_report(results).splitlines()
    assert lines[6] == "[실패] a: first second third"
    assert lines[7] == "[통과] b: "
    assert lines[-1] == "결과: 실패 (1/2)"


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
    assert "스키마 번호 1" in detail
    assert "표 3개" in detail
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


# --- 오류 문구에서 경로가 새지 않는다 ---------------------------------------------------


def _forms(path: Path) -> list[str]:
    """보고서에 남으면 안 되는 경로의 꼴."""
    text = str(path)
    return [text, path.as_posix(), text.replace("\\", "\\\\"), repr(text)[1:-1]]


def test_permission_error_under_the_data_folder_leaves_no_path(
    env: SimpleNamespace, offscreen_app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = env.data_home / "coin-report-trader" / "data" / "app.db"

    def denied() -> str:
        raise PermissionError(13, "Permission denied", str(secret))

    monkeypatch.setattr(st, "check_sqlite", denied)
    assert st.run_self_test() == 1
    text = _report(env)
    for form in _forms(env.data_home) + _forms(secret):
        assert form not in text
    assert f"[실패] sqlite: PermissionError: Permission denied ({ROOT_TOKEN}" in text
    assert text.splitlines()[-1] == "결과: 실패 (6/7)"


def test_oserror_while_importing_leaves_no_path(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = env.data_home / "coin-report-trader" / "x.py"
    real = st.importlib.import_module

    def fake(name: str, package: str | None = None) -> object:
        if name == "coin_report_trader.core.screens":
            raise PermissionError(13, "Permission denied", str(secret))
        return real(name, package)

    monkeypatch.setattr(st.importlib, "import_module", fake)
    with pytest.raises(st.CheckFailed) as caught:
        st.check_imports()
    for form in _forms(secret) + _forms(env.data_home):
        assert form not in str(caught.value)
    assert "coin_report_trader.core.screens (PermissionError: Permission denied (" in str(
        caught.value
    )


def test_a_failure_while_describing_does_not_stop_the_next_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(_exc: BaseException) -> str:
        raise RuntimeError("cannot describe")

    def fails() -> str:
        raise ValueError("x")

    monkeypatch.setattr(st, "describe_error", broken)
    results = st.run_checks([st.Check("a", fails), st.Check("b", lambda: "ok")])
    assert [(r.key, r.ok) for r in results] == [("a", False), ("b", True)]
    assert results[0].detail == "ValueError"


def test_an_unknown_home_folder_does_not_stop_the_checks(
    env: SimpleNamespace, offscreen_app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(cls: type[Path]) -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(refuse))
    monkeypatch.setattr(st, "check_notice", lambda: 1 / 0)
    results = st.run_checks(st.default_checks())
    assert [r.key for r in results] == KEYS
    assert [r.ok for r in results] == [True, True, True, True, False, True, True]
    assert results[3].detail.startswith(ROOT_TOKEN)


# --- QApplication이 없는 프로세스(exe가 CI에서 타는 경로) ------------------------------------


def test_self_test_in_a_fresh_process_without_qt_platform(tmp_path: Path) -> None:
    data_home = tmp_path / "share"
    data_home.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    child_env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    child_env["LOCALAPPDATA" if sys.platform == "win32" else "XDG_DATA_HOME"] = str(data_home)
    result = subprocess.run(
        [sys.executable, "-m", "coin_report_trader", "--self-test"],
        cwd=cwd,
        env=child_env,
        timeout=120,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout.decode("utf-8", "replace")
    raw = (cwd / st.REPORT_FILE_NAME).read_bytes()
    assert raw.startswith(BOM)
    lines = raw.decode("utf-8-sig").splitlines()
    assert any(line.startswith("[통과] qt: ") and "offscreen" in line for line in lines)
    assert lines[-1] == "결과: 통과 (7/7)"
    assert str(tmp_path) not in "\n".join(lines)


def test_the_real_pyside6_plugin_folders_hold_the_offscreen_plugin(
    offscreen_app: QApplication,
) -> None:
    found = st.find_offscreen_plugin(st._qt_plugin_dirs(QCoreApplication.libraryPaths()))
    assert found is not None
    assert "offscreen" in found.name
    assert found.is_file()


# --- 일곱 항목: tls, vault, import 목록, 오류 설명의 가리기(M1 명세 3.10, 3.11) -----------------


def test_import_lists_cover_the_new_folders_and_modules() -> None:
    assert {"trading", "stats", "vault", "notify"} <= set(st.SUBPACKAGES)
    assert {"httpx", "certifi", "keyring"} <= set(st.THIRD_PARTY_MODULES)
    names = set(st._package_module_names())
    assert "coin_report_trader.trading.quote.client" in names
    assert "coin_report_trader.stats.bar_stats" in names
    assert "coin_report_trader.stats.compute" not in names  # 옛 통계는 51번이 지웠다
    assert "coin_report_trader.app.data_thread" in names
    assert "coin_report_trader.app.send_thread" in names
    assert "coin_report_trader.vault.store" in names
    assert "coin_report_trader.notify.sender" in names


def test_tls_check_names_only_the_bundle_file() -> None:
    detail = st.check_tls()
    assert detail.startswith("cacert.pem, CA 인증서 ")
    import certifi

    assert str(Path(certifi.where()).parent) not in detail


def test_tls_check_fails_without_the_bundle_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import certifi

    monkeypatch.setattr(certifi, "where", lambda: str(tmp_path / "missing.pem"))
    with pytest.raises(st.CheckFailed, match=r"missing\.pem") as caught:
        st.check_tls()
    assert str(tmp_path) not in str(caught.value)


def test_tls_check_fails_when_the_bundle_has_no_ca_certificate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import certifi

    import coin_report_trader.core as core

    bundle = tmp_path / "empty.pem"
    bundle.write_text("", encoding="utf-8")
    monkeypatch.setattr(certifi, "where", lambda: str(bundle))

    class Context:
        def cert_store_stats(self) -> dict[str, int]:
            return {"x509_ca": 0}

    monkeypatch.setattr(st, "make_tls_context", lambda cafile: Context())
    assert st.make_tls_context is not core.make_tls_context
    with pytest.raises(st.CheckFailed, match=r"CA 인증서가 없습니다: empty\.pem"):
        st.check_tls()


def test_tls_check_in_an_exe_wants_the_bundle_inside_the_bundle_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import certifi

    inside = tmp_path / "_internal"
    inside.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    real = Path(certifi.where())
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "coin-report-trader.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(inside), raising=False)
    monkeypatch.setattr(certifi, "where", lambda: str(real))
    with pytest.raises(st.CheckFailed, match=r"번들 안에 없습니다: cacert\.pem"):
        st.check_tls()
    copy = inside / "cacert.pem"
    copy.write_bytes(real.read_bytes())
    monkeypatch.setattr(certifi, "where", lambda: str(copy))
    assert st.check_tls().startswith("cacert.pem, CA 인증서 ")
    assert outside.is_dir()


def test_vault_check_does_not_judge_other_operating_systems(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import keyring

    def never() -> object:
        raise AssertionError("Windows가 아니면 get_keyring을 부르지 않는다")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(keyring, "get_keyring", never)
    assert "판정하지 않음" in st.check_vault()


def test_vault_check_wants_the_windows_backend_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import keyring

    class WinVaultKeyring:
        __module__ = "keyring.backends.Windows"

    class Other:
        __module__ = "keyring.backends.fail"

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(keyring, "get_keyring", lambda: WinVaultKeyring())
    assert st.check_vault() == "Windows 자격 증명 보관소(WinVaultKeyring)"
    monkeypatch.setattr(keyring, "get_keyring", lambda: Other())
    with pytest.raises(st.CheckFailed, match="Windows 자격 증명 보관소가 아닙니다: Other"):
        st.check_vault()


def test_failure_descriptions_are_redacted(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://" + "discord.com/api/webhooks/" + "1" * 18 + "/" + "T" * 64

    def leaky() -> str:
        raise RuntimeError(f"보냄 {url}")

    def failed() -> str:
        raise st.CheckFailed(f"설명 {url}")

    results = st.run_checks([st.Check("a", leaky), st.Check("b", failed)])
    assert [r.ok for r in results] == [False, False]
    for result in results:
        assert "T" * 20 not in result.detail
        assert "(가려서 보임)" in result.detail


def test_services_are_accepted_and_do_not_change_the_result(
    env: SimpleNamespace, offscreen_app: QApplication
) -> None:
    class Never:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"점검이 services.{name}을 읽었다")

    assert st.run_self_test(services=Never()) == 0
    first = _report(env).splitlines()
    assert st.run_self_test() == 0
    second = _report(env).splitlines()
    assert [line for line in first if not line.startswith("점검 시각")] == [
        line for line in second if not line.startswith("점검 시각")
    ]


def test_run_self_test_takes_services_typed_as_the_services_class() -> None:
    import inspect

    from coin_report_trader.app.services import Services

    parameter = inspect.signature(st.run_self_test).parameters["services"]
    assert parameter.annotation == (Services | None)
    assert parameter.default is None
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
