"""명세 7절 A6~A9: `--self-test`와 명령줄."""

import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from coin_report_trader.__main__ import main
from coin_report_trader.app import self_test

REPORT = "self-test-report.txt"
BOM = b"\xef\xbb\xbf"
KEYS = ["import", "qt", "sqlite", "paths", "notice", "tls", "vault"]


def _report(folder: Path) -> list[str]:
    raw = (folder / REPORT).read_bytes()
    assert raw.startswith(BOM)
    return raw.decode("utf-8-sig").splitlines()


def _items(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith(("[통과]", "[실패]"))]


@pytest.fixture
def in_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp: QApplication) -> Path:
    """현재 폴더를 `tmp_path`로 둔다. 자료 폴더는 conftest가 `tmp_path`로 바꿔 둔다.

    `qt` 점검이 새 QApplication을 만들지 않게 offscreen `qapp`을 먼저 만든다.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.scenario("A6")
def test_a6_self_test_passes_and_writes_the_report(in_tmp_path: Path) -> None:
    assert main(["--self-test"]) == 0
    lines = _report(in_tmp_path)
    assert [line.split(":")[0] for line in _items(lines)] == [f"[통과] {key}" for key in KEYS]
    assert lines[-1] == "결과: 통과 (7/7)"
    text = "\n".join(lines)
    assert str(in_tmp_path) not in text
    assert in_tmp_path.as_posix() not in text


@pytest.mark.scenario("A7")
def test_a7_one_failing_check_fails_only_that_item(
    in_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> str:
        raise RuntimeError("점검 하나가 실패하게 바꿈")

    monkeypatch.setattr(self_test, "check_sqlite", boom)
    assert main(["--self-test"]) == 1  # 예외가 밖으로 나오지 않는다
    lines = _report(in_tmp_path)
    expected = [f"[{'실패' if key == 'sqlite' else '통과'}] {key}" for key in KEYS]
    assert [line.split(":")[0] for line in _items(lines)] == expected
    assert lines[-1] == "결과: 실패 (6/7)"


@pytest.mark.scenario("A8")
def test_a8_import_failure_still_writes_a_report(
    in_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "coin_report_trader.app.self_test", None)
    assert main(["--self-test"]) == 1
    lines = _report(in_tmp_path)
    assert lines[-1].startswith("결과: 실패")


@pytest.mark.scenario("A9")
def test_a9_without_stdout_and_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    assert main(["--없는옵션"]) == 2
    assert main(["--version"]) == 0
