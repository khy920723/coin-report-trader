"""M2 명세 6절의 진짜 실행기 시나리오: C16, C17(3.7.1~3.7.5).

이 두 시나리오만 진짜 `ClaudeCodeRunner`를 쓰고, 부르는 것은 가짜 실행 파일
`tests/fakes/claude_cli.py`다(`[sys.executable, 그 파일]`. 3.15). 이름이 `claude`인 실행 파일은
띄우지 않는다. 가짜 실행 파일은 받은 인자, 환경 변수의 이름, 작업 폴더의 파일을 그 폴더 밖의
기록 파일에 적는다. 값은 모두 만든 값이다.
"""

import json
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from acceptance.m2_support import AAA, Rig, make_rig, show_report
from fakes.ai import good_answers
from PySide6.QtWidgets import QApplication

from coin_report_trader.ai import claude_cli
from coin_report_trader.ai.agents import coin_session_prompt
from coin_report_trader.ai.claude_cli import ClaudeCodeRunner
from coin_report_trader.core import (
    AiChoice,
    AiSlot,
    Effort,
    ReportState,
    SessionOutcome,
    SessionRequest,
    TokenUsage,
)

FAKE = str(Path(__file__).resolve().parents[1] / "fakes" / "claude_cli.py")
CHOICES = (
    AiChoice(AiSlot.SEARCH, "haiku", Effort.LOW),
    AiChoice(AiSlot.CANDLE, "sonnet", Effort.MEDIUM),
    AiChoice(AiSlot.FINAL, "opus", Effort.HIGH),
    AiChoice(AiSlot.SESSION, "fable", Effort.XHIGH),
)
LIMIT_TEXT = "You've hit your session limit · resets 3:45pm"
# 시험이 넣어 두어도 실행기의 세 명령에 가지 않아야 하는 변수 스물넷(M2 명세 3.7.2의 목록을 글로
# 적는다. 실행기의 `REMOVED_VARIABLES`를 가져오지 않는다).
DROPPED = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_MANTLE",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
    "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_BEDROCK_MANTLE_BASE_URL",
    "ANTHROPIC_VERTEX_BASE_URL",
    "ANTHROPIC_FOUNDRY_BASE_URL",
    "ANTHROPIC_AWS_BASE_URL",
    "ANTHROPIC_GOOGLE_CLOUD_BASE_URL",
    "ANTHROPIC_AWS_API_KEY",
    "ANTHROPIC_FOUNDRY_API_KEY",
    "ANTHROPIC_FOUNDRY_AUTH_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
    "ANTHROPIC_PROFILE",
    "CLAUDE_CODE_EFFORT_LEVEL",
    "CLAUDE_CODE_SUBAGENT_MODEL_FORCE",
)
ANSWER_FIELDS = ("search_judgment", "search_rows", "candle_judgment", "candle_rows", "final_rows",
                 "final_judgment")  # fmt: skip
VALUE_OPTIONS = (
    "--output-format", "--json-schema", "--model", "--effort", "--agents", "--tools",
    "--allowedTools", "--permission-mode",
)  # fmt: skip


class Fake:
    """가짜 실행 파일의 설정과 기록."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.config: dict[str, Any] = {"record": str(folder / "record.jsonl")}

    def command(self) -> list[str]:
        path = self.folder / "config.json"
        path.write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
        return [sys.executable, FAKE, "--fake-config", str(path)]

    def records(self, mode: str | None = None) -> list[dict[str, Any]]:
        path = Path(self.config["record"])
        if not path.exists():
            return []
        found = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return [entry for entry in found if mode is None or entry["mode"] == mode]


def tool_use(name: str, call_id: str, parent: str | None = None, **given: Any) -> dict[str, Any]:
    return {
        "type": "assistant",
        "parent_tool_use_id": parent,
        "message": {"content": [{"type": "tool_use", "id": call_id, "name": name, "input": given}]},
    }


def usage(inputs: int, outputs: int, cache_read: int) -> dict[str, int]:
    return {
        "inputTokens": inputs,
        "outputTokens": outputs,
        "cacheReadInputTokens": cache_read,
        "cacheCreationInputTokens": 0,
    }


def good_lines(answers: dict[str, str]) -> list[dict[str, Any]]:
    """코인 세션의 `Agent` 셋, 서브 에이전트의 `Read`와 `WebSearch`, 모델 둘의 `modelUsage`."""
    return [
        {"type": "system", "subtype": "init"},
        tool_use("Agent", "a1", subagent_type="search_judge"),
        tool_use("Read", "r1", "a1"),
        tool_use("WebSearch", "w1", "a1"),  # 검색 판별은 Read 뒤 WebSearch
        tool_use("Agent", "a2", subagent_type="candle_judge"),
        tool_use("Read", "r2", "a2"),
        tool_use("Agent", "a3", subagent_type="final_predictor"),
        tool_use("Read", "r3", "a3"),
        {
            "type": "result",
            "is_error": False,
            "result": "끝",
            "structured_output": answers,
            "modelUsage": {"model-a": usage(1000, 200, 0), "model-b": usage(500, 100, 300)},
        },
    ]


def request(timeout: float = 30.0) -> SessionRequest:
    return SessionRequest("r1-KRW-AAA", AAA, {"search_input.csv": "market\r\nKRW-AAA\r\n"},
                          CHOICES, timeout)  # fmt: skip


def pairs(args: list[str]) -> dict[str, str]:
    return {a: args[i + 1] for i, a in enumerate(args[:-1]) if a in VALUE_OPTIONS}


def grows(path: Path) -> bool:
    """그 파일이 0.4초 사이에 커지는가(쓰는 프로세스가 살아 있는가)."""
    before = path.stat().st_size if path.exists() else 0
    time.sleep(0.4)
    after = path.stat().st_size if path.exists() else 0
    return after > before


@pytest.fixture
def rigs(tmp_path: Path) -> Iterator[list[Rig]]:
    made: list[Rig] = []
    yield made
    for rig in made:
        rig.stop()


# --- C16 --------------------------------------------------------------------------------------


@pytest.mark.scenario("C16")
def test_c16_the_time_limit_ends_the_fake_executable_and_its_child_and_removes_the_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claude_cli, "KILL_WAIT_SECONDS", 5.0)
    fake = Fake(tmp_path / "fake")
    beat = str(tmp_path / "beat")
    fake.config.update(sleep=60, child=True, heartbeat=beat, lines=[])
    sessions = tmp_path / "ai-sessions"
    runner = ClaudeCodeRunner(sessions, fake.command())
    started = time.monotonic()
    result = runner.run(request(timeout=2.0))
    elapsed = time.monotonic() - started
    assert result.outcome is SessionOutcome.TIMEOUT
    assert 1.9 <= elapsed < 10  # 2초 뒤(넉넉히 10초 안)
    assert Path(beat + ".self").exists()
    assert Path(beat + ".child").exists()  # 자식 프로세스도 떴었다
    assert not grows(Path(beat + ".self"))  # 가짜 실행 파일이 끝나 있다
    assert not grows(Path(beat + ".child"))  # 그 자식도 끝나 있다
    assert not (sessions / "r1-KRW-AAA").exists()  # 세션 폴더가 지워져 있다


# --- C17 --------------------------------------------------------------------------------------


@pytest.mark.scenario("C17")
def test_c17_the_real_runner_gives_the_spec_command_agents_env_tools_and_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp: QApplication, rigs: list[Rig]
) -> None:
    for name in DROPPED:
        monkeypatch.setenv(name, "시험이 넣은 값")
    root = tmp_path / "app"
    fake = Fake(tmp_path / "fake")
    fake.config.update(
        version="2.1.314 (Claude Code)", auth_exit=0, lines=good_lines(good_answers(AAA))
    )
    runner = ClaudeCodeRunner(root / "ai-sessions", fake.command())
    rig = make_rig(root, runner=runner, choices=CHOICES)  # type: ignore[arg-type]
    rigs.append(rig)
    rig.start()
    rig.round()
    (item,) = rig.reports(AAA)
    assert item.state is ReportState.OK
    (run,) = fake.records("run")
    args = run["args"]
    options = pairs(args)
    assert args[0] == "-p"  # 셸을 거치지 않고 인자 목록으로 부른다
    assert args[1] == coin_session_prompt()  # 코인 세션 지시
    assert options == {
        "--output-format": "stream-json",
        "--json-schema": options["--json-schema"],
        "--model": "fable",  # 코인 세션 칸의 모델
        "--effort": "xhigh",  # 코인 세션 칸의 노력 값
        "--agents": "agents.json",
        "--tools": "Agent,Read,WebSearch",
        "--allowedTools": "Agent,WebSearch",
        "--permission-mode": "dontAsk",
    }
    assert {
        "--verbose", "--restricted", "--no-session-persistence", "--strict-mcp-config",
        "--disable-slash-commands",
    } <= set(args)  # fmt: skip
    schema = json.loads(options["--json-schema"])
    assert schema["type"] == "object"
    assert list(schema["properties"]) == list(ANSWER_FIELDS)  # 3.7.5의 여섯 필드
    assert all(spec == {"type": "string"} for spec in schema["properties"].values())
    assert schema["required"] == list(ANSWER_FIELDS[:4])
    assert schema["additionalProperties"] is False
    assert "Read" not in options["--allowedTools"].split(",")
    joined = " ".join(args)
    assert "--bare" not in joined
    assert "WebFetch" not in joined
    agents = json.loads(run["files"]["agents.json"])
    assert {name: spec["tools"] for name, spec in agents.items()} == {
        "search_judge": "Read,WebSearch",
        "candle_judge": "Read",
        "final_predictor": "Read",
    }
    assert {name: (spec["model"], spec["effort"]) for name, spec in agents.items()} == {
        "search_judge": ("haiku", "low"),
        "candle_judge": ("sonnet", "medium"),
        "final_predictor": ("opus", "high"),
    }  # 칸마다 서로 다른 값이다. 코인 세션 칸의 값(fable, xhigh)은 위의 `--model`, `--effort`다.
    assert all(spec["omitClaudeMd"] is True for spec in agents.values())
    records = fake.records()
    assert {r["mode"] for r in records} == {"auth", "run", "version"}  # run()과 check()의 세 명령
    for record in records:
        assert not set(DROPPED) & set(record["env_names"]), record["mode"]
        assert record["background_tasks"] == "1"
    answer_files = [n for n in run["files"] if n not in ("agents.json",) and not n.endswith(".csv")]
    assert answer_files == []  # 세션 폴더에 답 파일이 생기지 않는다
    assert set(run["files"]) == {
        "agents.json", "search_input.csv", "candle_stats.csv", "final_now.csv",
        "final_last_judgment.csv", "final_last_report.csv", "final_trades.csv",
    }  # fmt: skip
    assert not (root / "ai-sessions" / "r1-KRW-AAA").exists()
    stored = rig.rows("SELECT tools_used FROM reports")
    assert [json.loads(text) for (text,) in stored] == [
        {
            "session": ["Agent"],
            "search": ["Read", "WebSearch"],
            "candle": ["Read"],
            "final": ["Read"],
        }
    ]
    view = rig.report(item.report_id)
    assert view.tokens == TokenUsage(1500, 300, 300, 0)  # 3.7.5의 예
    probe = show_report(rig, AAA, item.report_id)
    try:
        assert "세션 전체 토큰: 입력 1,500 · 출력 300 · 캐시 읽기 300 · 캐시 만들기 0" in (
            probe.lines("reportUsage")
        )
    finally:
        probe.close()


@pytest.mark.scenario("C17")
def test_c17_the_tools_of_the_runner_result_follow_the_spec_example(tmp_path: Path) -> None:
    fake = Fake(tmp_path / "fake")
    fake.config["lines"] = good_lines(good_answers(AAA))
    result = ClaudeCodeRunner(tmp_path / "ai-sessions", fake.command()).run(request())
    assert result.outcome is SessionOutcome.ANSWERED
    assert result.tools == {
        AiSlot.SESSION: ("Agent",),
        AiSlot.SEARCH: ("Read", "WebSearch"),
        AiSlot.CANDLE: ("Read",),
        AiSlot.FINAL: ("Read",),
    }
    assert result.tokens == TokenUsage(1500, 300, 300, 0)


@pytest.mark.scenario("C17")
def test_c17_a_limit_result_line_is_a_limit_without_the_claude_text_in_the_detail(
    tmp_path: Path,
) -> None:
    fake = Fake(tmp_path / "fake")
    fake.config["lines"] = [{"type": "result", "is_error": True, "result": LIMIT_TEXT}]
    result = ClaudeCodeRunner(tmp_path / "ai-sessions", fake.command()).run(request())
    assert result.outcome is SessionOutcome.LIMIT
    assert LIMIT_TEXT not in (result.detail or "")
    assert "session limit" not in (result.detail or "")
