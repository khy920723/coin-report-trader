"""M2 명세 3.15의 `claude` 실행 장치(`tests/conftest.py`).

막는 것: `subprocess.Popen`(과 그것을 거치는 호출)이 띄우는 실행 파일의 이름이 `claude`,
`claude.exe`, `claude.cmd`인 것. 막지 않는 것: `[sys.executable, 가짜 실행 파일]`과 이름이 다른 실행
파일. 진짜 `claude`는 어디에서도 뜨지 않는다(프로세스를 만들기 전에 막는다).
"""

import asyncio
import contextlib
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"


def test_the_claude_guard_is_installed(claude_guard) -> None:
    assert claude_guard.is_installed()
    assert subprocess.Popen.__init__.__qualname__ == "Popen.__init__"
    assert subprocess.Popen.__init__.__module__.rpartition(".")[2] == "conftest"


# (이름, Popen에 넘기는 위치 인자, 키워드 인자)
BLOCKED: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = [
    ("list", (["claude", "--version"],), {}),
    ("list-path", ([os.path.join("bin", "claude"), "-p"],), {}),
    ("list-exe", ([os.path.join("tools", "claude.exe")],), {}),
    ("list-cmd-upper", (["CLAUDE.CMD", "auth", "status"],), {}),
    ("windows-backslash", ([r"C:\tools\Claude.exe"],), {}),
    ("string", ("claude --version",), {}),
    ("string-quoted", ('"C:\\Program Files\\x\\claude.exe" -p',), {}),
    ("bytes", (b"claude",), {}),
    ("pathlike", (Path("claude"),), {}),
    ("pathlike-in-list", ([Path("bin") / "claude.cmd"],), {}),
    ("shell-string", ("cd somewhere && claude -p",), {"shell": True}),
    ("shell-list", (["echo a; claude --version"],), {"shell": True}),
    ("executable", (["other-name", "--version"],), {"executable": "claude"}),
    ("executable-positional", (["other-name"], -1, os.path.join("x", "claude.exe")), {}),
]


@pytest.mark.parametrize(
    ("args", "kwargs"), [(a, k) for _n, a, k in BLOCKED], ids=[n for n, _a, _k in BLOCKED]
)
def test_popen_of_a_claude_program_is_blocked(claude_guard, args, kwargs) -> None:
    with claude_guard.expect_blocked() as caught, pytest.raises(claude_guard.Blocked):
        subprocess.Popen(*args, **kwargs)
    assert len(caught) == 1
    assert "claude" in caught[0]


@pytest.mark.parametrize(
    "call",
    [
        lambda: subprocess.run(["claude", "--version"], check=False),
        lambda: subprocess.call(["claude"]),
        lambda: subprocess.check_call(["claude"]),
        lambda: subprocess.check_output(["claude", "auth", "status"]),
        lambda: subprocess.getoutput("claude --version"),
        lambda: os.popen("claude --version"),
    ],
    ids=["run", "call", "check_call", "check_output", "getoutput", "os.popen"],
)
def test_the_calls_that_go_through_popen_are_blocked(claude_guard, call) -> None:
    with claude_guard.expect_blocked() as caught, pytest.raises(claude_guard.Blocked):
        call()
    assert len(caught) == 1


def test_an_asyncio_child_process_is_blocked(claude_guard) -> None:
    async def start() -> None:
        await asyncio.create_subprocess_exec("claude", "--version")

    with claude_guard.expect_blocked() as caught, pytest.raises(claude_guard.Blocked):
        asyncio.run(start())
    assert len(caught) == 1


def _program_that_leaves_a_mark(folder: Path, mark: Path) -> Path:
    """뜨면 `mark`를 만드는 실행 파일(이름이 claude)."""
    if sys.platform == "win32":
        program = folder / "claude.cmd"
        program.write_text(f'@echo off\r\necho ran> "{mark}"\r\n', encoding="ascii")
    else:
        program = folder / "claude"
        program.write_text(f"#!/bin/sh\necho ran > '{mark}'\n", encoding="ascii")
        program.chmod(0o755)
    return program


def test_the_blocked_program_never_starts(claude_guard, tmp_path: Path) -> None:
    mark = tmp_path / "ran.txt"
    program = _program_that_leaves_a_mark(tmp_path, mark)
    with claude_guard.expect_blocked(), pytest.raises(claude_guard.Blocked):
        subprocess.run([str(program)], check=False, timeout=30)
    assert not mark.exists()


def test_a_swallowed_call_is_still_recorded(claude_guard) -> None:
    # 넓은 except가 삼켜도 기록이 남는다(그대로 두면 정리 단계에서 이 시험이 실패한다).
    with claude_guard.expect_blocked() as caught, contextlib.suppress(Exception):
        subprocess.run(["claude"], check=False)  # 일부러 삼킨다
    assert len(caught) == 1


def test_the_fake_program_run_by_python_is_not_blocked(claude_guard, tmp_path: Path) -> None:
    fake = tmp_path / "claude"  # 가짜 실행 파일은 이름이 같아도 sys.executable로 띄운다(3.15)
    fake.write_text("print('fake claude 2.1.314')\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(fake)], capture_output=True, text=True, check=True, timeout=60
    )
    assert result.stdout.strip() == "fake claude 2.1.314"
    assert claude_guard.take_attempts() == []


@pytest.mark.parametrize(
    "args",
    [
        [sys.executable, "-c", "pass"],
        [sys.executable, "-c", "import sys; sys.argv", "claude"],  # 인자에 든 claude
        "echo claude",  # shell 없이 준 글의 실행 파일은 echo(나 글 전체)다
    ],
    ids=["python", "argument", "echo-string"],
)
def test_other_programs_are_not_blocked(claude_guard, args) -> None:
    with contextlib.suppress(OSError):  # "echo claude"는 POSIX에서 그런 파일이 없어 실패한다
        subprocess.run(args, capture_output=True, check=False, timeout=60)
    assert claude_guard.take_attempts() == []


@pytest.mark.parametrize("name", ["claude-helper", "myclaude", "claude.py", "claude.exe.bak"])
def test_names_that_only_contain_claude_are_not_blocked(claude_guard, name: str) -> None:
    with contextlib.suppress(OSError):  # 그런 실행 파일은 없다
        subprocess.run([os.path.join("no-such-folder", name)], check=False, timeout=60)
    assert claude_guard.take_attempts() == []


def test_wrong_popen_arguments_raise_the_usual_error(claude_guard) -> None:
    with pytest.raises(TypeError):
        subprocess.Popen()  # type: ignore[call-overload]
    assert claude_guard.take_attempts() == []


CHILD_TESTS = textwrap.dedent(
    """
    import contextlib
    import subprocess


    def test_swallows_a_claude_call():
        with contextlib.suppress(Exception):
            subprocess.run(["claude", "--version"], check=False)


    def test_sees_the_count(claude_guard):
        assert claude_guard.blocked_by_test == {"test_child.py::test_swallows_a_claude_call": 1}
    """
)


def test_a_swallowed_call_fails_its_test_at_teardown_and_is_counted(tmp_path: Path) -> None:
    # 이 conftest를 따로 된 pytest로 돌린다. 자식은 sys.executable이므로 장치에 걸리지 않는다.
    (tmp_path / "conftest.py").write_bytes(CONFTEST.read_bytes())
    (tmp_path / "test_child.py").write_text(CHILD_TESTS, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
    env["PYTHONUTF8"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
         "--rootdir", str(tmp_path), str(tmp_path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=300,
        check=False,
    )  # fmt: skip
    output = result.stdout.decode("utf-8", "replace") + result.stderr.decode("utf-8", "replace")
    assert result.returncode == 1, output
    assert "claude 실행을 1번 막았다" in output
    # 첫 시험은 본문을 지나고 정리 단계에서 실패한다. 둘째 시험은 센 값(1)을 보고 통과한다.
    assert "ERROR at teardown of test_swallows_a_claude_call" in output, output
    assert "2 passed, 1 error" in output, output
