"""Claude Code 실행기 `ClaudeCodeRunner`(M2 명세 3.7.1~3.7.5). `AiRunner`를 지킨다.

앱은 사용자가 설치하고 로그인한 그대로의 `claude` 실행 파일만 부른다(REQ-AI-034). 로그인 화면이
없고, 자격 정보나 세션 토큰을 읽거나 저장하거나 옮기지 않는다. API 키를 넣지 않는다.

- 찾기: `command`가 주어지면 그것을 쓰고 찾지 않는다(시험). `None`이면 `PATH`의 절대 경로 항목만
  차례로 보아 `claude.exe`(Windows)나 `claude`를 처음 찾은 것을 쓴다. `shutil.which`를 쓰지 않고
  지금 폴더와 상대 경로는 보지 않으며 `.cmd`와 `.bat`은 부르지 않는다.
- 띄우는 법(`check()`와 `run()`이 같다): 셸을 거치지 않고 인자 목록으로, 표준 입력을 닫고, 3.7.2의
  환경(`claude_env`)으로, Windows에서는 `CREATE_NO_WINDOW`와 작업 개체(닫히면 그 안의 프로세스를
  모두 끝냄), 그 밖에서는 새 프로세스 묶음으로 띄운다.
- 시간 제한은 실제 흐른 시각(`time.monotonic()`)으로 잰다. 넘으면 묶음을 끝내고 5초까지 기다린 뒤
  `TIMEOUT`을 돌려준다. 재시도는 하지 않는다.
- 출력은 stream-json이다. 읽는 것은 마지막 `result` 줄의 몇 키와 `assistant` 줄의 `tool_use`
  블록의 이름뿐이다. 도구의 입력 값과 검색어, 도구의 결과, 글은 저장하지 않는다. `result`의 글은
  한도 초과를 가리는 데만 읽고 저장하거나 로그에 쓰지 않는다.
"""

import contextlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from coin_report_trader.ai import agents
from coin_report_trader.ai.claude_env import build_env
from coin_report_trader.core import (
    CLAUDE_VERSION_LIMIT,
    CLAUDE_VERSION_MIN,
    AiSlot,
    ClaudeCheck,
    ClaudeState,
    ModelTokens,
    SessionOutcome,
    SessionRequest,
    SessionResult,
    TokenUsage,
    redact,
)

log = logging.getLogger(__name__)

CHECK_TIMEOUT_SECONDS: Final = 30.0
"""`check()`의 두 명령의 시간 제한."""
KILL_WAIT_SECONDS: Final = 5.0
"""끝낸 뒤 프로세스가 끝나기를 기다리는 가장 긴 시간."""
READER_JOIN_SECONDS: Final = 5.0
STDERR_CHARS: Final = 2000
"""`detail`에 쓰는 표준 오류의 앞부분의 글자 수."""
LIMIT_MARK: Final = "You've hit your "
"""구독 사용 한도에 닿았다는 Claude Code의 글의 앞부분(errors 문서의 'Usage limits')."""
REMOVE_RETRIES: Final = 5

_VERSION: Final = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_CREATE_NO_WINDOW: Final = 0x08000000

_COMMAND_ARGS: Final = (
    "--output-format",
    "stream-json",
    "--verbose",
    "--json-schema",
)


# --- 찾기 -------------------------------------------------------------------------------------


def find_executable() -> str | None:
    """`PATH`의 절대 경로 항목에서 `claude.exe`(Windows)나 `claude`를 찾는다. 없으면 `None`."""
    name = "claude.exe" if sys.platform == "win32" else "claude"
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry or not os.path.isabs(entry):
            continue
        candidate = Path(entry) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


# --- 프로세스 묶음 ------------------------------------------------------------------------------


class _Group:
    """띄운 프로세스와 그 자식을 한꺼번에 끝내는 묶음."""

    def __init__(self, proc: subprocess.Popen[bytes], job: int | None) -> None:
        self.proc = proc
        self._job = job

    @classmethod
    def start(cls, args: Sequence[str], cwd: Path, env: Mapping[str, str]) -> "_Group":
        kwargs: dict[str, Any] = {
            "cwd": cwd,
            "env": dict(env),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = _CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(list(args), **kwargs)
        job = _assign_to_job(proc) if sys.platform == "win32" else None
        return cls(proc, job)

    def kill(self) -> None:
        """묶음 안의 프로세스를 모두 끝낸다. 이미 끝났어도 된다."""
        if sys.platform == "win32":
            if self._job is not None:
                _terminate_job(self._job)
            with contextlib.suppress(OSError):
                self.proc.kill()
            return
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(self.proc.pid, signal.SIGKILL)
        with contextlib.suppress(OSError):
            self.proc.kill()

    def close(self) -> None:
        for stream in (self.proc.stdout, self.proc.stderr):
            if stream is not None:
                with contextlib.suppress(OSError, ValueError):
                    stream.close()
        if self._job is not None:
            _close_handle(self._job)
            self._job = None


def _assign_to_job(proc: subprocess.Popen[bytes]) -> int | None:
    """Windows: 닫히면 그 안의 프로세스를 모두 끝내는 작업 개체를 만들어 프로세스를 넣는다.

    만들거나 넣지 못하면 로그에 남기고 `None`이다(그때는 프로세스 하나만 끝낸다).
    """
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = (
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        )

    class IoCounters(ctypes.Structure):
        _fields_ = tuple((name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        ))  # fmt: skip

    class ExtendedLimits(ctypes.Structure):
        _fields_ = (
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        )

    kill_on_close = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    extended_limit_information = 9  # JobObjectExtendedLimitInformation
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    )  # fmt: skip
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        log.warning("작업 개체를 만들지 못했습니다: %s", ctypes.get_last_error())
        return None
    info = ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = kill_on_close
    ok = kernel32.SetInformationJobObject(
        job, extended_limit_information, ctypes.byref(info), ctypes.sizeof(info)
    )
    if ok:
        ok = kernel32.AssignProcessToJobObject(job, int(proc._handle))  # type: ignore[attr-defined]
    if not ok:
        log.warning("작업 개체에 넣지 못했습니다: %s", ctypes.get_last_error())
        kernel32.CloseHandle(job)
        return None
    return int(job)


def _terminate_job(job: int) -> None:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.TerminateJobObject(ctypes.c_void_p(job), 1)


def _close_handle(job: int) -> None:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.CloseHandle(ctypes.c_void_p(job))


# --- 한 번의 실행 ------------------------------------------------------------------------------


@dataclass
class _Outcome:
    code: int | None
    """끝 코드. 시간 제한으로 끝냈으면 `None`."""
    timed_out: bool
    lines: list[bytes] = field(default_factory=list)
    stderr: str = ""


def _drain(stream: Any, sink: list[bytes], limit: int | None) -> None:
    """파이프를 끝까지 읽는다(막히지 않게). `limit`이 있으면 앞의 그만큼만 모은다."""
    kept = 0
    try:
        for chunk in iter(stream.readline, b""):
            if limit is None:
                sink.append(chunk)
            elif kept < limit:
                sink.append(chunk[: limit - kept])
                kept += len(chunk)
    except (OSError, ValueError):
        return


class ClaudeCodeRunner:
    """`AiRunner`의 진짜. `sessions_root`는 세션 폴더를 담는 폴더(자료 폴더 아래 `ai-sessions`)다.

    `command`는 실행 명령의 앞부분이다. `None`이면 `check()`가 찾은 실행 파일 하나다. 시험은
    `[sys.executable, 가짜 실행 파일]`을 넘긴다.
    """

    def __init__(self, sessions_root: Path, command: Sequence[str] | None = None) -> None:
        self._sessions_root = Path(sessions_root)
        self._command = None if command is None else list(command)
        self._lock = threading.Lock()
        self._active: set[_Group] = set()

    # --- 확인 ---------------------------------------------------------------------------------

    def _prefix(self) -> list[str] | None:
        if self._command is not None:
            return list(self._command)
        found = find_executable()
        return None if found is None else [found]

    def check(self) -> ClaudeCheck:
        """실행 파일, 버전, 로그인을 차례로 본다(앞이 안 되면 뒤를 하지 않는다)."""
        prefix = self._prefix()
        if prefix is None:
            return ClaudeCheck(ClaudeState.NOT_FOUND, None)
        outcome = self._execute(
            [*prefix, "--version"], self._neutral_folder(), CHECK_TIMEOUT_SECONDS
        )
        text = b"".join(outcome.lines).decode("utf-8", errors="replace")
        match = _VERSION.search(text)
        if outcome.timed_out or outcome.code != 0 or match is None:
            return ClaudeCheck(ClaudeState.VERSION_UNREADABLE, None)
        parts = tuple(int(group) for group in match.groups())
        version = ".".join(match.groups())
        if not CLAUDE_VERSION_MIN <= parts < CLAUDE_VERSION_LIMIT:
            return ClaudeCheck(ClaudeState.VERSION_OUT_OF_RANGE, version)
        # 로그인: 끝 코드만 본다. 표준 출력과 표준 오류는 읽어 버린다(계정의 글이 들 수 있다).
        status = self._execute(
            [*prefix, "auth", "status"], self._neutral_folder(), CHECK_TIMEOUT_SECONDS
        )
        if not status.timed_out and status.code == 0:
            return ClaudeCheck(ClaudeState.READY, version)
        return ClaudeCheck(ClaudeState.LOGGED_OUT, version)

    def _neutral_folder(self) -> Path:
        self._sessions_root.mkdir(parents=True, exist_ok=True)
        return self._sessions_root

    # --- 세션 ---------------------------------------------------------------------------------

    def run(self, request: SessionRequest) -> SessionResult:
        """세션 하나를 끝까지 돌린다. 시간 제한을 넘기면 스스로 끝내고 돌아온다. 세션 폴더는 어떻게
        끝나든 지운다."""
        choices = {AiSlot(choice.slot): choice for choice in request.choices}
        session = choices.get(AiSlot.SESSION)
        if session is None:
            raise ValueError("코인 세션 칸의 모델과 노력이 없다")
        prefix = self._prefix()
        if prefix is None:
            return _failed("Claude Code를 찾지 못했습니다.")
        folder = self._sessions_root / request.key
        self._prepare_folder(folder, request)
        try:
            args = [
                *prefix,
                "-p",
                agents.coin_session_prompt(),
                *_COMMAND_ARGS,
                json.dumps(agents.ANSWER_SCHEMA, ensure_ascii=False),
                "--model",
                session.model,
                "--effort",
                str(session.effort.value),
                "--agents",
                agents.AGENTS_FILE,
                "--tools",
                "Agent,Read,WebSearch",
                "--allowedTools",
                "Agent,WebSearch",
                "--permission-mode",
                "dontAsk",
                "--restricted",
                "--no-session-persistence",
                "--strict-mcp-config",
                "--disable-slash-commands",
            ]
            outcome = self._execute(args, folder, request.timeout_seconds)
            return _result(outcome)
        finally:
            _remove_folder(folder)

    def stop_all(self) -> None:
        """도는 세션의 프로세스를 모두 끝낸다(끝낼 때)."""
        with self._lock:
            groups = list(self._active)
        for group in groups:
            group.kill()

    # --- 안쪽 ---------------------------------------------------------------------------------

    def _prepare_folder(self, folder: Path, request: SessionRequest) -> None:
        """빈 세션 폴더를 만들고(남은 것이 있으면 지운다) 입력 파일과 `agents.json`만 쓴다."""
        _remove_folder(folder)
        folder.mkdir(parents=True)
        try:
            for name, text in request.files.items():
                if Path(name).name != name or name in ("", ".", ".."):
                    raise ValueError("입력 파일의 이름이 폴더 이름을 담고 있다")
                (folder / name).write_bytes(text.encode("utf-8-sig"))
            (folder / agents.AGENTS_FILE).write_bytes(
                agents.agents_json(request.choices).encode("utf-8")
            )
        except BaseException:
            _remove_folder(folder)
            raise

    def _execute(self, args: Sequence[str], cwd: Path, timeout: float) -> _Outcome:
        env = build_env(os.environ)
        group = _Group.start(args, cwd, env)
        with self._lock:
            self._active.add(group)
        out: list[bytes] = []
        err: list[bytes] = []
        readers = [
            threading.Thread(target=_drain, args=(group.proc.stdout, out, None), daemon=True),
            threading.Thread(target=_drain, args=(group.proc.stderr, err, 8192), daemon=True),
        ]
        for reader in readers:
            reader.start()
        timed_out = False
        code: int | None
        try:
            try:
                code = group.proc.wait(timeout=max(timeout, 0.0))
            except subprocess.TimeoutExpired:
                timed_out = True
                code = None
                group.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    group.proc.wait(timeout=KILL_WAIT_SECONDS)
            group.kill()  # 남은 자식이 없게 한다(파이프를 쥐고 있으면 읽기가 끝나지 않는다)
            for reader in readers:
                reader.join(READER_JOIN_SECONDS)
        finally:
            with self._lock:
                self._active.discard(group)
            group.close()
        stderr = b"".join(err).decode("utf-8", errors="replace")[:STDERR_CHARS]
        return _Outcome(code, timed_out, out, stderr)


# --- 결과 읽기 ---------------------------------------------------------------------------------


def _failed(detail: str) -> SessionResult:
    return SessionResult(SessionOutcome.FAILED, {}, None, (), detail, _empty_tools())


def _empty_tools() -> dict[AiSlot, tuple[str, ...]]:
    return dict.fromkeys(AiSlot, ())


def _json_lines(lines: Sequence[bytes]) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for raw in lines:
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            objects.append(parsed)
    return objects


def _used_tools(objects: Sequence[dict[str, Any]]) -> dict[AiSlot, tuple[str, ...]]:
    """칸마다 쓴 도구의 이름(3.7.5). 처음 쓴 차례로, 겹치지 않게 이름만 담는다."""
    calls: list[tuple[str, str, str | None]] = []  # (id, name, 부른 `Agent` 호출의 id)
    agent_slots: dict[str, AiSlot] = {}
    for obj in objects:
        if obj.get("type") != "assistant":
            continue
        message = obj.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        parent = obj.get("parent_tool_use_id")
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name, call_id = block.get("name"), block.get("id")
            if not isinstance(name, str):
                continue
            call_id = call_id if isinstance(call_id, str) else ""
            calls.append((call_id, name, parent if isinstance(parent, str) else None))
            if name == "Agent":
                tool_input = block.get("input")
                kind = tool_input.get("subagent_type") if isinstance(tool_input, dict) else None
                if isinstance(kind, str) and kind in agents.SLOT_BY_AGENT and call_id:
                    agent_slots[call_id] = agents.SLOT_BY_AGENT[kind]
    used: dict[AiSlot, list[str]] = {slot: [] for slot in AiSlot}
    for _, name, parent in calls:
        slot = AiSlot.SESSION if parent is None else agent_slots.get(parent, AiSlot.SESSION)
        if name not in used[slot]:
            used[slot].append(name)
    return {slot: tuple(names) for slot, names in used.items()}


def _tokens(
    usage: Any,
) -> tuple[TokenUsage | None, tuple[ModelTokens, ...]]:
    """`modelUsage`(모델 이름에서 사용량으로)의 모델마다의 값과 그 합."""
    if not isinstance(usage, dict):
        return None, ()
    per_model: list[ModelTokens] = []
    for model, item in usage.items():
        if not isinstance(item, dict):
            continue
        values = [
            _count(item.get(key))
            for key in (
                "inputTokens",
                "outputTokens",
                "cacheReadInputTokens",
                "cacheCreationInputTokens",
            )
        ]
        per_model.append(ModelTokens(str(model), TokenUsage(*values)))
    total = TokenUsage(
        sum(item.usage.input_tokens for item in per_model),
        sum(item.usage.output_tokens for item in per_model),
        sum(item.usage.cache_read_tokens for item in per_model),
        sum(item.usage.cache_creation_tokens for item in per_model),
    )
    return total, tuple(per_model)


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _result(outcome: _Outcome) -> SessionResult:
    """`SessionResult`를 만든다(3.7.5)."""
    objects = _json_lines(outcome.lines)
    results = [obj for obj in objects if obj.get("type") == "result"]
    last = results[-1] if results else None
    tools = _used_tools(objects)
    tokens, model_tokens = _tokens(last.get("modelUsage")) if last is not None else (None, ())
    if outcome.timed_out:
        return SessionResult(SessionOutcome.TIMEOUT, {}, tokens, model_tokens, None, tools)
    is_error = None if last is None else bool(last.get("is_error"))
    structured = None if last is None else last.get("structured_output")
    if (
        outcome.code == 0
        and last is not None
        and is_error is False
        and isinstance(structured, dict)
    ):
        answers = {
            name: value
            for name, value in structured.items()
            if name in agents.ANSWER_FIELDS and isinstance(value, str)
        }
        return SessionResult(SessionOutcome.ANSWERED, answers, tokens, model_tokens, None, tools)
    text = last.get("result") if last is not None else None
    if is_error and isinstance(text, str) and LIMIT_MARK in text:
        return SessionResult(SessionOutcome.LIMIT, {}, tokens, model_tokens, None, tools)
    detail = f"끝 코드 {outcome.code}, is_error {is_error}, 표준 오류: {redact(outcome.stderr)}"
    return SessionResult(SessionOutcome.FAILED, {}, tokens, model_tokens, detail, tools)


def _remove_folder(folder: Path) -> None:
    """세션 폴더를 지운다. 지우지 못하면 로그에만 남기고(다음에 켤 때 지운다) 넘어간다."""
    if not folder.exists():
        return
    for attempt in range(REMOVE_RETRIES):
        try:
            shutil.rmtree(folder)
        except OSError as exc:
            if attempt + 1 == REMOVE_RETRIES:
                log.warning("세션 폴더를 지우지 못했습니다: %s", type(exc).__name__)
                return
            time.sleep(0.2)  # Windows: 방금 끝난 프로세스가 아직 파일을 쥐고 있을 수 있다
        else:
            return
