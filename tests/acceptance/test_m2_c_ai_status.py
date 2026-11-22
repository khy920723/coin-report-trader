"""M2 명세 6절의 AI 끄기와 다시 켜기 시나리오: C18, C19, C23, C24(3.5.4, 3.11).

가짜 실행기의 `check()`와 `run`, 쓰기가 실패하는 DB(SQLite 트리거)로 일으킨다. 값은 모두 만든
값이다.
"""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from acceptance.m2_support import AAA, BBB, Rig, make_rig, restart
from fakes.ai import FakeAiRunner

from coin_report_trader.core import AiStatus, ClaudeState, ReportState


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Callable[..., Rig]]:
    made: list[Rig] = []

    def factory(**kwargs: Any) -> Rig:
        rig = make_rig(tmp_path / f"r{len(made)}", **kwargs)
        made.append(rig)
        return rig

    def track(rig: Rig) -> Rig:
        made.append(rig)
        return rig

    factory.track = track  # type: ignore[attr-defined]
    yield factory
    for rig in made:
        rig.stop()


def rounds(rig: Rig) -> int:
    return rig.rows("SELECT COUNT(*) FROM rounds")[0][0]


# --- C18 --------------------------------------------------------------------------------------


@pytest.mark.scenario("C18")
@pytest.mark.parametrize(
    ("state", "version", "status"),
    [
        (ClaudeState.VERSION_OUT_OF_RANGE, "2.0.1", AiStatus.VERSION_OFF),
        (ClaudeState.VERSION_UNREADABLE, None, AiStatus.NOT_FOUND),
        (ClaudeState.NOT_FOUND, None, AiStatus.NOT_FOUND),
    ],
    ids=["out-of-range", "unreadable", "not-found"],
)
def test_c18_a_claude_that_cannot_be_used_turns_the_ai_off_until_the_check_passes(
    build: Callable[..., Rig], state: ClaudeState, version: str | None, status: AiStatus
) -> None:
    runner = FakeAiRunner(state=state, version=version)
    rig = build(runner=runner)
    rig.start()
    assert rig.statuses()[-1] is status
    rig.round()
    rig.round()
    assert rig.codes().count("A-19") == 1  # 하나씩이다
    assert runner.requests == []  # 세션이 뜨지 않는다
    assert rounds(rig) == 0  # 회차가 생기지 않는다
    runner.set_check(ClaudeState.READY)
    rig.round()  # 다음 회차의 때
    assert rig.statuses()[-1] is AiStatus.ON
    assert rounds(rig) == 1
    assert [i.state for i in rig.reports(AAA)] == [ReportState.OK]


# --- C19 --------------------------------------------------------------------------------------


@pytest.mark.scenario("C19")
def test_c19_logged_out_fails_each_coin_without_sessions_and_one_a18_until_a_good_report(
    build: Callable[..., Rig],
) -> None:
    runner = FakeAiRunner(state=ClaudeState.LOGGED_OUT)
    rig = build(coins=(AAA, BBB), runner=runner)
    rig.start()
    rig.round()
    rig.round()
    for market in (AAA, BBB):
        assert [(i.state, i.failure) for i in rig.reports(market)] == [
            (ReportState.FAILED, "로그인 만료")
        ] * 2
    assert runner.requests == []  # 세션이 뜨지 않는다
    logged_out = [a for a in rig.alerts() if a.code == "A-18"]
    assert [a.coin for a in logged_out] == [None]  # 하나(코인 없음)
    assert "A-17" not in rig.codes()
    runner.set_check(ClaudeState.READY)
    rig.round()
    assert rig.reports(AAA)[0].state is ReportState.OK
    runner.set_check(ClaudeState.LOGGED_OUT)
    rig.round()
    assert rig.codes().count("A-18") == 2  # 다시 로그아웃하면 다시 생긴다


# --- C23 --------------------------------------------------------------------------------------


@pytest.mark.scenario("C23")
def test_c23_an_exception_of_the_runner_turns_the_ai_off_until_the_program_starts_again(
    build: Callable[..., Rig],
) -> None:
    runner = FakeAiRunner()
    runner.plan(AAA, raises=RuntimeError("만든 실패"))
    rig = build(runner=runner)
    rig.start()
    rig.round()
    unexpected = [a for a in rig.alerts() if a.code == "A-30"]
    assert len(unexpected) == 1
    assert unexpected[0].detail is not None
    assert unexpected[0].detail.stage == "AI 보고서"
    assert rig.statuses()[-1] is AiStatus.ERROR_OFF
    fetches = len(rig.quote.candle_requests(market=AAA))
    sessions = len(runner.requests)
    rig.round()  # 다음 봉의 때
    assert len(rig.quote.candle_requests(market=AAA)) > fetches  # 봉 받기는 나간다
    assert rounds(rig) == 1  # 회차와 세션은 없다
    assert len(runner.requests) == sessions
    runner.plan(AAA, None)
    again = build.track(restart(rig))  # type: ignore[attr-defined]
    again.start()
    assert again.statuses()[-1] is AiStatus.ON  # 다시 켜면 ON이다
    again.round()
    assert rounds(again) == 2
    assert again.reports(AAA)[0].state is ReportState.OK


# --- C24 --------------------------------------------------------------------------------------


def failing_report_writes(conn: Any) -> None:
    """보고서 표의 쓰기가 실패하는 DB: `reports`에 줄을 넣으면 SQLite가 거절한다."""
    conn.execute(
        "CREATE TRIGGER no_reports BEFORE INSERT ON reports "
        "BEGIN SELECT RAISE(ABORT, '만든 쓰기 실패'); END"
    )
    conn.commit()


@pytest.mark.scenario("C24")
def test_c24_a_report_write_failure_turns_the_ai_off_with_an_a29_and_the_fetch_goes_on(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=failing_report_writes)
    rig.start()
    rig.round()
    saved = [a for a in rig.alerts() if a.code == "A-29"]
    assert len(saved) == 1
    assert saved[0].detail is not None
    assert saved[0].detail.stage == "보고서 저장"
    assert rig.statuses()[-1] is AiStatus.ERROR_OFF
    fetches = len(rig.quote.candle_requests(market=AAA))
    rig.round()
    assert len(rig.quote.candle_requests(market=AAA)) > fetches  # 봉 받기는 계속된다
    assert rig.runner.requests == []
