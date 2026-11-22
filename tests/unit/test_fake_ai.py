"""`tests/fakes/ai.py`의 `FakeAiRunner`(M2 명세 3.15). 값은 모두 만든 값이다."""

import threading
from decimal import Decimal

import pytest
from fakes.ai import (
    DEFAULT_TOOLS,
    FakeAiRunner,
    answered,
    failed,
    good_answers,
    limit,
    timeout,
    wrong_header_answers,
)

from coin_report_trader.ai.answers import JudgeContext, judge
from coin_report_trader.core import (
    AiChoice,
    AiRunner,
    AiSlot,
    ClaudeState,
    Effort,
    FailureKind,
    SessionOutcome,
    SessionRequest,
)

CHOICES = tuple(AiChoice(slot, "haiku", Effort.LOW) for slot in AiSlot)


def request(market: str = "KRW-AAA", key: str = "r1-AAA") -> SessionRequest:
    return SessionRequest(key, market, {"a.csv": "x\r\n"}, CHOICES, 900.0)


def context(market: str = "KRW-AAA") -> JudgeContext:
    return JudgeContext(market, Decimal("100"))


def test_it_is_an_ai_runner() -> None:
    assert isinstance(FakeAiRunner(), AiRunner)


def test_a_coin_without_a_plan_gets_its_own_good_answer() -> None:
    runner = FakeAiRunner()
    for market in ("KRW-AAA", "KRW-BBB"):
        result = runner.run(request(market))
        assert result.outcome is SessionOutcome.ANSWERED
        assert judge(result, context(market)).failure is None
        assert f"{market}," in result.answers["final_judgment"]
    assert result.tools == DEFAULT_TOOLS  # 마지막으로 낸 결과의 쓴 도구


def test_each_coin_gets_the_result_that_was_planned() -> None:
    runner = FakeAiRunner()
    runner.plan("KRW-AAA", timeout())
    runner.plan("KRW-BBB", limit())
    runner.plan("KRW-CCC", failed("끝 코드 1"))
    assert runner.run(request("KRW-AAA")).outcome is SessionOutcome.TIMEOUT
    assert runner.run(request("KRW-BBB")).outcome is SessionOutcome.LIMIT
    third = runner.run(request("KRW-CCC"))
    assert (third.outcome, third.detail) == (SessionOutcome.FAILED, "끝 코드 1")
    assert runner.run(request("KRW-DDD")).outcome is SessionOutcome.ANSWERED


def test_a_plan_may_be_a_function_of_the_request() -> None:
    runner = FakeAiRunner()
    runner.plan("KRW-AAA", lambda req: answered(good_answers(req.market, predicted="150")))
    result = runner.run(request())
    assert "150" in result.answers["final_judgment"]


def test_planned_tools_and_tokens_are_carried() -> None:
    runner = FakeAiRunner()
    tools = dict.fromkeys(AiSlot, ()) | {AiSlot.SEARCH: ("Read", "WebSearch", "WebFetch")}
    runner.plan("KRW-AAA", answered(good_answers("KRW-AAA"), tools=tools, tokens=None))
    result = runner.run(request())
    assert result.tools[AiSlot.SEARCH] == ("Read", "WebSearch", "WebFetch")
    assert result.tokens is None
    assert result.model_tokens == ()


def test_wrong_headers_and_wrong_values_are_judged_as_format_errors() -> None:
    runner = FakeAiRunner()
    runner.plan("KRW-AAA", answered(wrong_header_answers("KRW-AAA")))
    judged = judge(runner.run(request()), context())
    assert judged.failure is FailureKind.FORMAT
    assert "search_judgment" in (judged.reason or "")


def test_the_five_check_states_and_the_count() -> None:
    runner = FakeAiRunner()
    seen = []
    for state in ClaudeState:
        runner.set_check(state, "2.0.1" if state is ClaudeState.VERSION_OUT_OF_RANGE else None)
        seen.append(runner.check())
    assert [c.state for c in seen] == list(ClaudeState)
    assert seen[3].version == "2.0.1"
    assert runner.check_calls == 5
    assert FakeAiRunner().check().state is ClaudeState.READY


def test_run_raises_the_planned_exception_and_still_records_the_request() -> None:
    runner = FakeAiRunner()
    runner.plan("KRW-AAA", raises=RuntimeError("시험"))
    with pytest.raises(RuntimeError, match="시험"):
        runner.run(request())
    assert len(runner.requests) == 1


def test_requests_are_recorded_with_their_files() -> None:
    runner = FakeAiRunner()
    runner.run(request("KRW-AAA", "r1-AAA"))
    runner.run(request("KRW-BBB", "r1-BBB"))
    assert [(r.key, r.market, dict(r.files)) for r in runner.requests] == [
        ("r1-AAA", "KRW-AAA", {"a.csv": "x\r\n"}),
        ("r1-BBB", "KRW-BBB", {"a.csv": "x\r\n"}),
    ]


def test_a_late_session_waits_for_the_event_and_signals_that_it_started() -> None:
    runner = FakeAiRunner()
    release = threading.Event()
    started = runner.plan("KRW-AAA", wait=release)
    results = []
    thread = threading.Thread(target=lambda: results.append(runner.run(request())))
    thread.start()
    assert started.wait(10)  # 시작했다
    thread.join(0.2)
    assert thread.is_alive()
    assert results == []  # 이벤트가 서기 전에는 돌아오지 않는다
    release.set()
    thread.join(10)
    assert not thread.is_alive()
    assert results[0].outcome is SessionOutcome.ANSWERED


def test_stop_all_is_counted() -> None:
    runner = FakeAiRunner()
    runner.stop_all()
    runner.stop_all()
    assert runner.stop_calls == 2
