"""진입점 `main` 시험. 어떤 경우에도 예외가 밖으로 나오지 않는다."""

import sys
from pathlib import Path

import pytest

import coin_report_trader
from coin_report_trader import __main__ as entry

BOM = b"\xef\xbb\xbf"


def test_version_prints_and_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert entry.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"coin-report-trader {coin_report_trader.__version__}"


@pytest.mark.parametrize("argv", [["--없는옵션"], ["--unknown"], ["extra"], ["--self"], ["--help"]])
def test_unknown_options_return_two(argv: list[str]) -> None:
    assert entry.main(argv) == 2


def test_without_streams_nothing_is_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    # 창 모드 exe에서는 stdout, stderr가 None이다.
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    assert entry.main(["--없는옵션"]) == 2
    assert entry.main(["--version"]) == 0


def test_argv_defaults_to_the_process_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["prog", "--version"])
    assert entry.main() == 0


def test_window_mode_calls_run_gui(monkeypatch: pytest.MonkeyPatch) -> None:
    import coin_report_trader.app as app_package

    seen: list[list[str]] = []
    monkeypatch.setattr(
        app_package, "run_gui", lambda argv, *, services=None: seen.append(list(argv)) or 7
    )
    assert entry.main([]) == 7
    assert seen == [[]]


def test_main_passes_services_on_as_it_is(monkeypatch: pytest.MonkeyPatch) -> None:
    import coin_report_trader.app as app_package
    from coin_report_trader.app import self_test

    given = object()
    gui: list[object] = []
    checks: list[object] = []
    monkeypatch.setattr(
        app_package, "run_gui", lambda argv, *, services=None: gui.append(services) or 0
    )
    monkeypatch.setattr(
        self_test, "run_self_test", lambda *, services=None: checks.append(services) or 0
    )
    assert entry.main([], services=given) == 0
    assert entry.main(["--self-test"], services=given) == 0
    assert entry.main([]) == 0
    assert gui == [given, None]
    assert checks == [given]


def test_window_mode_failure_returns_one_and_names_only_the_error_type(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    import coin_report_trader.app as app_package

    def boom(_argv: list[str], *, services: object = None) -> int:
        raise RuntimeError(f"cannot open {tmp_path}")

    monkeypatch.setattr(app_package, "run_gui", boom)
    assert entry.main([]) == 1
    err = capsys.readouterr().err
    assert "RuntimeError" in err
    assert str(tmp_path) not in err


def test_window_mode_failure_without_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    import coin_report_trader.app as app_package

    def boom(_argv: list[str], *, services: object = None) -> int:
        raise RuntimeError("x")

    monkeypatch.setattr(app_package, "run_gui", boom)
    monkeypatch.setattr(sys, "stderr", None)
    assert entry.main([]) == 1


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (KeyboardInterrupt(), 1),
        (SystemExit(5), 5),
        (SystemExit(None), 0),
        (SystemExit("x"), 1),
        (ValueError(), 1),
    ],
)
def test_nothing_escapes_main(
    monkeypatch: pytest.MonkeyPatch, error: BaseException, expected: int
) -> None:
    def boom(_argv: list[str], _services: object = None) -> int:
        raise error

    monkeypatch.setattr(entry, "_gui", boom)
    assert entry.main([]) == expected


def test_self_test_returns_the_exit_code_of_run_self_test(monkeypatch: pytest.MonkeyPatch) -> None:
    from coin_report_trader.app import self_test

    for code in (0, 1):
        monkeypatch.setattr(
            self_test, "run_self_test", lambda *, services=None, code=code: code
        )
        assert entry.main(["--self-test"]) == code


def test_self_test_import_failure_writes_a_fallback_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "coin_report_trader.app.self_test", None)
    assert entry.main(["--self-test"]) == 1
    raw = (tmp_path / entry.FALLBACK_REPORT_NAME).read_bytes()
    assert raw.startswith(BOM)
    text = raw.decode("utf-8-sig")
    assert text.splitlines()[-1] == "결과: 실패"
    assert "ImportError" in text or "ModuleNotFoundError" in text
    assert str(tmp_path) not in text


def test_self_test_crash_writes_a_fallback_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from coin_report_trader.app import self_test

    def boom(*, services: object = None) -> int:
        raise RuntimeError(f"broken in {tmp_path}")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(self_test, "run_self_test", boom)
    assert entry.main(["--self-test"]) == 1
    text = (tmp_path / entry.FALLBACK_REPORT_NAME).read_text(encoding="utf-8-sig")
    assert text.splitlines()[-1] == "결과: 실패"
    assert "RuntimeError" in text
    assert str(tmp_path) not in text


def test_fallback_report_name_matches_the_self_test_module() -> None:
    from coin_report_trader.app import self_test

    assert entry.FALLBACK_REPORT_NAME == self_test.REPORT_FILE_NAME


def test_fallback_report_failure_is_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(entry, "FALLBACK_REPORT_NAME", "no-such-folder/report.txt")
    monkeypatch.setitem(sys.modules, "coin_report_trader.app.self_test", None)
    assert entry.main(["--self-test"]) == 1
