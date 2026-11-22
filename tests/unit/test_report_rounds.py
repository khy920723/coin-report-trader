"""회차와 AI 보고서 잇기(M2 명세 3.5, 3.9~3.11): 자료 스레드, 가짜 실행기, 가짜 시세, 가짜 시계.

진짜 `claude`와 거래소는 부르지 않는다. 값은 모두 만든 값이다. 시각은 가짜 시계로만 옮긴다.
세션 결과는 AI 스레드가 큐에 넣으므로, 조건이 될 때까지 짧게 끊어 보며 안전 시간까지 기다린다.
"""

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fakes.ai import (
    FakeAiRunner,
    answered,
    failed,
    good_answers,
    limit,
    timeout,
    wrong_header_answers,
)
from fakes.clock import FakeClock
from fakes.quote import FakeQuoteSource, make_candles, with_close

from coin_report_trader.ai import inputs as ai_inputs
from coin_report_trader.app import report_round
from coin_report_trader.app.data_thread import DataWorker
from coin_report_trader.core import (
    AiChoice,
    AiSlot,
    AiStatus,
    AiStatusView,
    AlertCategory,
    CandleUnit,
    ClaudeState,
    CoinView,
    Effort,
    LoadState,
    Market,
    Mode,
    QuoteError,
    ReportListView,
    ReportState,
    ReportView,
    SaveResult,
    SettingsView,
    StorageError,
    UiUpdate,
)
from coin_report_trader.core.texts import (
    CODE_CLAUDE_OFF,
    CODE_LOGGED_OUT,
    CODE_PRICE_FAILED,
    CODE_REPORT_DONE,
    CODE_REPORT_FAILED,
    CODE_SAVE_FAILED,
    CODE_UNEXPECTED,
    ERROR_BLOCKED,
    ERROR_NETWORK,
    REPORT_UNREADABLE,
)
from coin_report_trader.storage import reports as report_store
from coin_report_trader.storage.alerts import list_alerts
from coin_report_trader.storage.database import migrate, open_database
from coin_report_trader.storage.settings import read_settings, write_settings

AAA, BBB = "KRW-AAA", "KRW-BBB"
HOUR = CandleUnit.MINUTE_60
SAFETY = 10.0
THIS_HOUR = datetime(2026, 1, 5, 3, tzinfo=UTC)
FIRST_CLOSE = datetime(2026, 1, 5, 4, tzinfo=UTC)  # 첫 (라)의 봉 마감 시각 T(04:00:10에 받는다)
HOUR_SECONDS = 3600
FOUR = tuple(AiChoice(slot, "haiku", Effort.LOW) for slot in AiSlot)
PARTS = [("검색 판별", 1), ("봉 판별", 1), ("최종 예측", 1)]


@dataclass
class Sink:
    items: list[UiUpdate] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __call__(self, update: UiUpdate) -> None:
        with self.lock:
            self.items.append(update)

    def of(self, kind: type) -> list[Any]:
        with self.lock:
            return [item for item in self.items if isinstance(item, kind)]


@dataclass
class Rig:
    worker: DataWorker
    sink: Sink
    clock: FakeClock
    quote: FakeQuoteSource
    runner: FakeAiRunner
    db: Path
    sessions: Path

    def start(self) -> None:
        self.worker.start()
        self.settle()

    def settle(self) -> None:
        assert self.worker.wait_idle(SAFETY), "자료 스레드가 쉬지 않는다"

    def round(self) -> None:
        """다음 (라)의 때까지 시계를 옮기고 세션이 모두 끝나 처리될 때까지 기다린다."""
        due = self.worker._next_due
        assert due is not None
        self.clock.advance((due - self.clock.now()).total_seconds())
        self.settle()
        self.drain()

    def drain(self) -> None:
        deadline = time.monotonic() + SAFETY
        while self.worker._pending and time.monotonic() < deadline:
            time.sleep(0.005)
        assert not self.worker._pending, "세션이 끝나지 않았다"
        self.settle()

    def conn(self) -> Any:
        return open_database(self.db)

    def alerts(self) -> list[Any]:
        conn = self.conn()
        try:
            return list(reversed(list_alerts(conn)))
        finally:
            conn.close()

    def codes(self) -> list[str]:
        return [alert.code for alert in self.alerts()]

    def reports(self, market: str = AAA) -> tuple[Any, ...]:
        conn = self.conn()
        try:
            return report_store.list_round_items(conn, market, Mode.PAPER)
        finally:
            conn.close()

    def report(self, report_id: int) -> ReportView:
        conn = self.conn()
        try:
            view = report_store.read_report(conn, report_id)
        finally:
            conn.close()
        assert view is not None
        return view

    def rows(self, sql: str, *args: Any) -> list[tuple[Any, ...]]:
        conn = self.conn()
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def statuses(self) -> list[AiStatus]:
        return [view.status for view in self.sink.of(AiStatusView)]


def make_rig(
    tmp_path: Path,
    *,
    coins: tuple[str, ...] = (AAA,),
    choices: tuple[AiChoice, ...] = FOUR,
    runner: FakeAiRunner | None = None,
    prices: bool = True,
    seed: Callable[[Any], None] | None = None,
) -> Rig:
    db = tmp_path / "app.db"
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    conn = open_database(db)
    try:
        migrate(conn)
        view = SettingsView(coins, 30, True, candle_unit=HOUR, candle_count=100, ai_choices=choices)
        write_settings(conn, view)
        if seed is not None:
            seed(conn)
    finally:
        conn.close()
    clock = FakeClock()
    quote = FakeQuoteSource(
        markets=[Market(c, f"이름 {c}", False) for c in (AAA, BBB)], clock=clock
    )
    for coin in coins:
        quote.set_candles(coin, HOUR, make_candles(coin, HOUR, 200, last_start=THIS_HOUR))
        if prices:
            quote.set_prices({coin: Decimal("100")})
    fake = runner or FakeAiRunner()
    sink = Sink()
    sessions = tmp_path / "ai-sessions"
    worker = DataWorker(
        database_file=db,
        logs_dir=logs,
        clock=clock,
        quote=quote,
        sink=sink,
        ai=fake,
        sessions_root=sessions,
    )
    return Rig(worker, sink, clock, quote, fake, db, sessions)


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Callable[..., Rig]]:
    rigs: list[Rig] = []

    def factory(**kwargs: Any) -> Rig:
        rig = make_rig(tmp_path / f"r{len(rigs)}", **kwargs)
        rigs.append(rig)
        return rig

    (tmp_path).mkdir(exist_ok=True)
    yield factory
    for rig in rigs:
        rig.worker.stop()
        rig.worker.join(SAFETY)


@pytest.fixture(autouse=True)
def _mkdirs(tmp_path: Path) -> None:
    for index in range(4):
        (tmp_path / f"r{index}").mkdir(exist_ok=True)


# --- 켤 때 -------------------------------------------------------------------------------------


def test_with_fewer_than_four_slots_the_status_is_not_chosen_and_nothing_is_called(
    build: Callable[..., Rig],
) -> None:
    rig = build(choices=FOUR[:3])
    rig.start()
    assert rig.statuses() == [AiStatus.NOT_CHOSEN]
    assert rig.runner.check_calls == 0
    rig.round()
    assert (rig.runner.check_calls, rig.runner.requests) == (0, [])
    assert rig.reports() == ()
    assert rig.rows("SELECT count(*) FROM rounds") == [(0,)]  # 회차를 시작하지 않는다(번호도 없다)


def test_at_start_with_four_slots_the_version_is_checked_and_the_status_is_on(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    assert rig.runner.check_calls == 1
    (view,) = rig.sink.of(AiStatusView)
    assert (view.status, view.version) == (AiStatus.ON, "2.1.314")
    lists = rig.sink.of(ReportListView)
    assert [(v.market, v.items, v.state) for v in lists] == [(AAA, (), LoadState.READY)]
    kinds = [type(u).__name__ for u in rig.sink.items]
    assert kinds.index("AiStatusView") < kinds.index("ReportListView")


@pytest.mark.parametrize(
    ("state", "version", "status", "text"),
    [
        (ClaudeState.NOT_FOUND, None, AiStatus.NOT_FOUND, "Claude Code를 찾지 못함"),
        (ClaudeState.VERSION_UNREADABLE, None, AiStatus.NOT_FOUND, "버전을 읽지 못함"),
        (ClaudeState.VERSION_OUT_OF_RANGE, "2.2.5", AiStatus.VERSION_OFF, "버전 범위 밖(2.2.5)"),
    ],
)
def test_a_check_that_cannot_use_claude_turns_the_ai_off_with_one_a19(
    build: Callable[..., Rig], state: ClaudeState, version: str | None, status: AiStatus, text: str
) -> None:
    rig = build(runner=FakeAiRunner(state=state, version=version))
    rig.start()
    assert rig.statuses() == [status]
    (alert,) = rig.alerts()
    assert (alert.code, alert.category, alert.coin) == (CODE_CLAUDE_OFF, AlertCategory.ERROR, None)
    assert alert.detail is not None
    assert alert.detail.error_name == text
    rig.round()  # 다음 (라)에 다시 보지만 A-19는 `ON`으로 돌아오기 전에는 다시 만들지 않는다
    assert rig.codes() == [CODE_CLAUDE_OFF]
    assert rig.runner.requests == []
    assert rig.rows("SELECT count(*) FROM rounds") == [(0,)]


def test_the_ai_comes_back_on_when_the_check_passes_at_a_later_round(
    build: Callable[..., Rig],
) -> None:
    runner = FakeAiRunner(state=ClaudeState.VERSION_OUT_OF_RANGE, version="2.2.5")
    rig = build(runner=runner)
    rig.start()
    runner.set_check(ClaudeState.READY, "2.1.400")
    rig.round()
    assert rig.statuses() == [AiStatus.VERSION_OFF, AiStatus.ON]
    assert [item.state for item in rig.reports()] == [ReportState.OK]
    runner.set_check(ClaudeState.VERSION_OUT_OF_RANGE, "2.2.5")
    rig.round()
    rig.runner.set_check(ClaudeState.READY, "2.1.400")
    assert rig.codes().count(CODE_CLAUDE_OFF) == 2  # 돌아온 뒤에는 다시 만든다


def test_an_interrupted_round_and_the_session_folders_are_cleaned_at_start(
    tmp_path: Path, build: Callable[..., Rig]
) -> None:
    def seed(conn: Any) -> None:
        round_id = report_store.insert_round(
            conn, mode=Mode.PAPER, number=1, trigger=report_store.RoundTrigger.BAR_CLOSE,
            unit=HOUR, close_at=THIS_HOUR, next_at=FIRST_CLOSE, started_at=THIS_HOUR,
        )  # fmt: skip
        report_store.insert_report(
            conn, round_id=round_id, mode=Mode.PAPER, market=AAA, created_at=THIS_HOUR
        )

    rig = build(seed=seed)
    leftover = rig.sessions / "r1-KRW-AAA"
    leftover.mkdir(parents=True)
    (leftover / "search_input.csv").write_text("남은 것", encoding="utf-8")
    rig.start()
    assert rig.rows("SELECT state FROM rounds") == [("interrupted",)]
    assert rig.rows("SELECT state FROM reports") == [("interrupted",)]
    assert list(rig.sessions.iterdir()) == []
    items = rig.sink.of(ReportListView)[0].items
    assert [item.state for item in items] == [ReportState.INTERRUPTED]
    rig.round()  # 끊긴 회차는 다시 돌리지 않고 다음 회차의 번호는 이어진다
    assert rig.rows("SELECT number, state FROM rounds ORDER BY number") == [
        (1, "interrupted"),
        (2, "done"),
    ]


# --- 회차 -------------------------------------------------------------------------------------


def test_a_good_round_writes_the_report_one_a26_and_ends_the_round(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    assert rig.runner.requests == []  # 켤 때의 받기는 회차가 아니다
    rig.round()
    (request,) = rig.runner.requests
    assert request.market == AAA
    assert set(request.files) == {
        ai_inputs.FILE_SEARCH_INPUT,
        ai_inputs.FILE_CANDLE_STATS,
        ai_inputs.FILE_FINAL_NOW,
        ai_inputs.FILE_FINAL_LAST_JUDGMENT,
        ai_inputs.FILE_FINAL_LAST_REPORT,
        ai_inputs.FILE_FINAL_TRADES,
    }
    assert request.choices == FOUR
    assert request.timeout_seconds == 900.0
    assert "KRW-AAA" in request.files[ai_inputs.FILE_FINAL_NOW]
    assert request.files[ai_inputs.FILE_FINAL_NOW].splitlines()[1].split(",")[1] == "100"
    (item,) = rig.reports()
    assert (item.round_number, item.state, item.direction.value) == (1, ReportState.OK, "오를 것")
    assert item.close_utc == FIRST_CLOSE
    view = rig.report(item.report_id)
    assert (view.base_price, view.predicted_price, view.buy_price) == (
        Decimal("100"), Decimal("102"), Decimal("99"),
    )  # fmt: skip
    assert view.tokens is not None
    assert view.tokens.input_tokens == 1000
    assert view.elapsed_report_seconds == 10  # T(04:00:00)에서 04:00:10까지
    assert {(e.part, e.seq) for e in view.evidence} == {
        (AiSlot.SEARCH, 1), (AiSlot.CANDLE, 1), (AiSlot.FINAL, 1),
    }  # fmt: skip
    assert rig.rows("SELECT tools_used FROM reports") == [
        ('{"session": ["Agent"], "search": ["Read", "WebSearch"], "candle": ["Read"],'
         ' "final": ["Read"]}',)
    ]  # fmt: skip
    assert rig.rows("SELECT state, number, trigger, unit FROM rounds") == [
        ("done", 1, "bar_close", "minute60")
    ]
    (alert,) = rig.alerts()
    assert (alert.code, alert.category, alert.coin, alert.report_id) == (
        CODE_REPORT_DONE, AlertCategory.REPORT, AAA, item.report_id,
    )  # fmt: skip
    assert alert.text == "회차 1의 보고서를 받았습니다. 최종 판단: 오를 것."
    assert alert.detail is None
    shown = rig.sink.of(ReportListView)[-1]
    assert [i.state for i in shown.items] == [ReportState.OK]


def test_the_candle_stats_input_is_the_same_bar_stats_the_screen_got(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    rig.round()
    view = [v for v in rig.sink.of(CoinView) if v.market == AAA and v.state is LoadState.READY][-1]
    (request,) = rig.runner.requests
    assert request.files[ai_inputs.FILE_CANDLE_STATS] == ai_inputs.candle_stats(view.stats)


def test_two_coins_run_together_and_a_coin_whose_stats_failed_is_skipped_without_a_new_alert(
    build: Callable[..., Rig],
) -> None:
    rig = build(coins=(AAA, BBB))
    rig.start()
    rig.quote.script(QuoteError(ERROR_NETWORK), kind="candles", market=BBB)
    rig.round()
    states = {m: [i.state for i in rig.reports(m)] for m in (AAA, BBB)}
    assert states == {AAA: [ReportState.OK], BBB: [ReportState.SKIPPED]}
    skipped = rig.reports(BBB)[0]
    assert skipped.failure == "봉 통계를 만들지 못함"
    assert sorted(rig.codes()) == ["A-1", CODE_REPORT_DONE]  # 새 알림은 그 받기의 A-1뿐이다
    assert [r.market for r in rig.runner.requests] == [AAA]
    assert rig.rows("SELECT state FROM rounds") == [("done",)]


def test_a_failed_price_read_skips_the_coin_with_one_a2_and_the_repeat_waits_for_a_success(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    rig.quote.script(*[QuoteError(ERROR_NETWORK)] * 20, kind="price", market=AAA)
    rig.round()
    assert [(i.state, i.failure) for i in rig.reports()] == [
        (ReportState.SKIPPED, "현재가를 읽지 못함")
    ]
    (alert,) = rig.alerts()
    assert (alert.code, alert.coin) == (CODE_PRICE_FAILED, AAA)
    assert alert.text == "현재가를 읽지 못해 이번 회차를 쉬었습니다."
    assert alert.detail is not None
    assert alert.detail.stage == "현재가 읽기"
    assert alert.detail.error_name == ERROR_NETWORK
    assert (
        alert.detail.after
        == ("이번 회차에 이 코인의 AI를 부르지 않았습니다. 다음 회차(05:00)에 다시 합니다.")
        or "다음 회차(" in alert.detail.after
    )
    assert rig.runner.requests == []
    rig.round()
    assert rig.codes() == [CODE_PRICE_FAILED]  # 같은 오류 이름은 다시 만들지 않는다
    rig.quote.clear_script()
    rig.round()
    rig.quote.script(*[QuoteError(ERROR_NETWORK)] * 20, kind="price", market=AAA)
    rig.round()
    assert rig.codes().count(CODE_PRICE_FAILED) == 2  # 한 번 성공한 뒤에는 다시 만든다


def test_a_blocked_price_read_sets_the_block_and_the_next_coin_is_not_requested(
    build: Callable[..., Rig],
) -> None:
    rig = build(coins=(AAA, BBB))
    rig.start()
    rig.quote.script(QuoteError(ERROR_BLOCKED, retry_after=600), kind="price", market=AAA)
    before = len(rig.quote.price_requests())
    rig.round()
    assert len(rig.quote.price_requests()) - before == 1  # 두 번째 코인은 요청하지 않았다
    assert [i.failure for i in rig.reports(AAA)] == ["현재가를 읽지 못함"]
    assert [i.failure for i in rig.reports(BBB)] == ["현재가를 읽지 못함"]
    alerts = [a for a in rig.alerts() if a.code == CODE_PRICE_FAILED]
    assert {a.coin: a.detail.error_name for a in alerts} == {AAA: ERROR_BLOCKED, BBB: ERROR_BLOCKED}
    assert rig.runner.requests == []


def test_logged_out_writes_failed_reports_and_one_a18_without_sessions(
    build: Callable[..., Rig],
) -> None:
    runner = FakeAiRunner(state=ClaudeState.LOGGED_OUT)
    rig = build(coins=(AAA, BBB), runner=runner)
    rig.start()
    assert rig.statuses() == [AiStatus.ON]  # 켤 때 `LOGGED_OUT`도 `ON`이다
    rig.round()
    for market in (AAA, BBB):
        assert [(i.state, i.failure) for i in rig.reports(market)] == [
            (ReportState.FAILED, "로그인 만료")
        ]
    (alert,) = rig.alerts()
    assert (alert.code, alert.coin, alert.category) == (CODE_LOGGED_OUT, None, AlertCategory.ERROR)
    assert runner.requests == []
    rig.round()
    assert rig.codes() == [CODE_LOGGED_OUT]  # 보고서가 한 번 `OK`가 되기 전에는 다시 만들지 않는다
    runner.set_check(ClaudeState.READY)
    rig.round()
    runner.set_check(ClaudeState.LOGGED_OUT)
    rig.round()
    assert rig.codes().count(CODE_LOGGED_OUT) == 2


@pytest.mark.parametrize(
    ("result", "kind", "reason"),
    [
        (timeout(), "시간 초과", "시간 제한(900초)을 넘겼습니다."),
        (limit(), "한도 초과", "Claude 구독 사용 한도에 닿았습니다. 앱의 문제가 아닙니다."),
        (failed(), "형식 오류", "세션이 정상으로 끝나지 않았습니다."),
    ],
)
def test_a_failed_session_gives_a_failed_report_and_one_a17_with_the_fixed_reason(
    build: Callable[..., Rig], result: Any, kind: str, reason: str
) -> None:
    runner = FakeAiRunner()
    runner.plan(AAA, result)
    rig = build(runner=runner)
    rig.start()
    checks = runner.check_calls
    rig.round()
    assert [(i.state, i.failure) for i in rig.reports()] == [(ReportState.FAILED, kind)]
    (alert,) = rig.alerts()
    assert (alert.code, alert.coin, alert.text) == (
        CODE_REPORT_FAILED, AAA, f"AI 보고서를 받지 못했습니다({kind}).",
    )  # fmt: skip
    assert alert.detail is not None
    assert (alert.detail.stage, alert.detail.error_name, alert.detail.content) == (
        "AI 보고서", kind, reason,
    )  # fmt: skip
    expected_checks = (
        checks + 1 + (1 if kind == "형식 오류" else 0)
    )  # 회차 앞 한 번, 실패면 한 번 더
    assert runner.check_calls == expected_checks
    rig.round()
    assert rig.codes() == [CODE_REPORT_FAILED]  # 같은 코인, 같은 종류는 `OK`가 되기 전에 다시 없다


def test_a_failed_session_whose_check_says_logged_out_is_an_a18_not_an_a17(
    build: Callable[..., Rig],
) -> None:
    runner = FakeAiRunner()

    def lose_login(request: Any) -> Any:
        runner.set_check(ClaudeState.LOGGED_OUT)
        return failed()

    runner.plan(AAA, lose_login)
    rig = build(runner=runner)
    rig.start()
    rig.round()
    assert [(i.state, i.failure) for i in rig.reports()] == [(ReportState.FAILED, "로그인 만료")]
    assert rig.codes() == [CODE_LOGGED_OUT]


def test_a_wrong_header_answer_is_a_format_error_with_the_fixed_reason_only(
    build: Callable[..., Rig],
) -> None:
    runner = FakeAiRunner()
    runner.plan(AAA, answered(wrong_header_answers(AAA)))
    rig = build(runner=runner)
    rig.start()
    rig.round()
    assert [(i.state, i.failure) for i in rig.reports()] == [(ReportState.FAILED, "형식 오류")]
    (alert,) = rig.alerts()
    assert alert.detail is not None
    assert alert.detail.content == "search_judgment의 머리줄이 다릅니다."
    assert alert.detail.error_name == "형식 오류"
    saved = rig.rows("SELECT answer_part, kind FROM report_rows ORDER BY answer_part, kind, seq")
    assert ("search", "근거") in saved  # 머리줄이 맞는 표만 저장해 둔다
    assert rig.rows("SELECT search_judgment FROM reports") == [(None,)]  # 머리줄이 틀린 표는 아니다


def test_a_report_that_becomes_ok_lets_the_same_failure_alert_again(
    build: Callable[..., Rig],
) -> None:
    runner = FakeAiRunner()
    runner.plan(AAA, timeout())
    rig = build(runner=runner)
    rig.start()
    rig.round()
    runner.plan(AAA, None)
    rig.round()
    runner.plan(AAA, timeout())
    rig.round()
    assert rig.codes() == [CODE_REPORT_FAILED, CODE_REPORT_DONE, CODE_REPORT_FAILED]


def test_the_next_round_gets_the_last_judgment_and_its_rows_and_stores_the_comparison(
    build: Callable[..., Rig],
) -> None:
    runner = FakeAiRunner()
    rig = build(runner=runner)
    rig.start()
    rig.round()  # 회차 1: 정상
    first = rig.reports()[0]
    runner.plan(
        AAA, lambda req: answered(good_answers(AAA, result_text="지난 결과 글", compared=PARTS))
    )
    rig.round()
    second = runner.requests[1]
    last_judgment = second.files[ai_inputs.FILE_FINAL_LAST_JUDGMENT].splitlines()
    assert len(last_judgment) == 2  # 머리줄과 지난 판단 한 줄
    assert "오를 것" in last_judgment[1]
    assert len(runner.requests[0].files[ai_inputs.FILE_FINAL_LAST_JUDGMENT].splitlines()) == 1
    last_report = second.files[ai_inputs.FILE_FINAL_LAST_REPORT].splitlines()
    assert len(last_report) == 1 + 5  # 머리줄과 지난 판단의 줄 표 전부(검색 둘, 봉 하나, 최종 둘)
    new = rig.reports()[0]
    assert (new.round_number, new.state) == (2, ReportState.OK)
    view = rig.report(new.report_id)
    assert [(c.part, c.seq) for c in view.compared] == [
        (AiSlot.SEARCH, 1), (AiSlot.CANDLE, 1), (AiSlot.FINAL, 1),
    ]  # fmt: skip
    assert view.last_result is not None
    assert view.last_result.round_number == 1
    assert view.last_result.predicted_price == Decimal("102")
    assert view.last_result.actual_price is None  # 그 봉이 저장되어 있지 않으면 `None`이다
    assert rig.rows("SELECT last_report_id FROM reports WHERE id = ?", new.report_id) == [
        (first.report_id,)
    ]


def test_the_last_result_reads_the_stored_candle_that_starts_at_the_last_close(
    build: Callable[..., Rig],
) -> None:
    runner = FakeAiRunner()
    rig = build(runner=runner)
    rig.start()
    rig.round()  # 회차 1: T 04:00, 예측 102, 현재가 100, 오를 것
    candle = with_close(make_candles(AAA, HOUR, 1, last_start=FIRST_CLOSE)[0], Decimal("101"))
    rig.quote.put_candles(candle)  # 그 예측이 가리킨 때(05:00)에 끝나는 봉
    runner.plan(
        AAA, lambda req: answered(good_answers(AAA, result_text="지난 결과", compared=PARTS))
    )
    rig.round()
    view = rig.report(rig.reports()[0].report_id)
    assert view.last_result is not None
    assert (view.last_result.actual_price, view.last_result.diff) == (
        Decimal("101"), Decimal("-0.0098"),
    )  # fmt: skip
    assert view.last_result.hit is not None
    assert view.last_result.hit.value == "맞음"
    row = runner.requests[1].files[ai_inputs.FILE_FINAL_LAST_JUDGMENT].splitlines()[1]
    assert ",101,-0.0098,맞음" in row


def test_the_fetch_after_a_block_ends_is_not_a_round(build: Callable[..., Rig]) -> None:
    rig = build()
    rig.start()
    rig.quote.script(QuoteError(ERROR_BLOCKED, retry_after=600), kind="price", market=AAA)
    rig.round()  # 현재가 읽기에서 차단을 받았다
    assert rig.rows("SELECT count(*) FROM rounds") == [(1,)]
    rig.clock.advance(600)  # 차단이 풀려 저절로 하는 받기: 봉 통계만 새로 한다
    rig.settle()
    assert rig.rows("SELECT count(*) FROM rounds") == [(1,)]
    assert rig.runner.requests == []


def test_a_missing_comparison_makes_the_second_report_a_format_error(
    build: Callable[..., Rig],
) -> None:
    runner = FakeAiRunner()
    rig = build(runner=runner)
    rig.start()
    rig.round()
    runner.plan(AAA, answered(good_answers(AAA)))  # 지난 판단이 있는데 견준 결과와 결과 글이 없다
    rig.round()
    assert [i.state for i in rig.reports()] == [ReportState.FAILED, ReportState.OK]


def test_a_round_that_would_start_while_the_last_one_is_running_is_skipped(
    build: Callable[..., Rig],
) -> None:
    runner = FakeAiRunner()
    hold = threading.Event()
    started = runner.plan(AAA, wait=hold)
    rig = build(runner=runner)
    rig.start()
    rig.clock.advance((rig.worker._next_due - rig.clock.now()).total_seconds())
    rig.settle()
    assert started.wait(SAFETY)
    rig.clock.advance(HOUR_SECONDS)
    rig.settle()
    assert rig.rows("SELECT count(*) FROM rounds") == [
        (1,)
    ]  # 앞 회차가 끝나지 않아 번호를 주지 않았다
    assert len(runner.requests) == 1
    assert [i.state for i in rig.reports()] == [ReportState.RUNNING]
    hold.set()
    rig.drain()
    assert [i.state for i in rig.reports()] == [ReportState.OK]
    assert rig.rows("SELECT state FROM rounds") == [("done",)]


# --- AI 끄기(3.5.4) -----------------------------------------------------------------------------


def test_an_exception_of_the_runner_turns_the_ai_off_with_one_a30_and_an_error_report(
    build: Callable[..., Rig],
) -> None:
    runner = FakeAiRunner()
    runner.plan(AAA, raises=RuntimeError("실행기의 글"))
    rig = build(coins=(AAA, BBB), runner=runner)
    rig.start()
    rig.round()
    assert rig.statuses() == [AiStatus.ON, AiStatus.ERROR_OFF]
    assert [(i.state, i.failure) for i in rig.reports(AAA)] == [(ReportState.ERROR, "RuntimeError")]
    assert [i.state for i in rig.reports(BBB)] == [ReportState.OK]  # 이미 도는 세션은 끝나게 둔다
    other = [a for a in rig.alerts() if a.code == CODE_UNEXPECTED]
    assert len(other) == 1
    assert other[0].category is AlertCategory.OTHER
    assert other[0].detail is not None
    assert other[0].detail.stage == "AI 보고서"
    assert other[0].detail.after == (
        "AI를 껐습니다. 봉 받기는 계속하고, 프로그램을 다시 켜면 AI가 다시 돕니다."
    )
    before = len(runner.requests)
    rig.round()  # 끈 뒤에는 새 세션을 띄우지 않지만 봉 받기는 계속한다
    assert len(runner.requests) == before
    assert rig.quote.candle_requests()
    assert rig.rows("SELECT count(*) FROM rounds") == [(1,)]


def test_the_error_off_is_kept_until_the_program_is_started_again(
    tmp_path: Path, build: Callable[..., Rig]
) -> None:
    runner = FakeAiRunner()
    runner.plan(AAA, raises=RuntimeError("x"))
    rig = build(runner=runner)
    rig.start()
    rig.round()
    checks = runner.check_calls
    rig.worker.save_settings(
        (AAA,), 30, first_setup=False, candle_unit=HOUR, candle_count=100, ai_choices=FOUR
    )
    rig.settle()
    assert runner.check_calls == checks  # `ERROR_OFF`이면 부르지 않는다
    assert rig.statuses()[-1] is AiStatus.ERROR_OFF


def test_a_write_failure_turns_the_ai_off_with_an_a29_and_the_alert_is_not_repeated(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    rig.start()

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise StorageError("쓰기 실패")

    monkeypatch.setattr(report_round.report_store, "finish_report", broken)
    rig.round()
    assert rig.statuses()[-1] is AiStatus.ERROR_OFF
    a29 = [a for a in rig.alerts() if a.code == CODE_SAVE_FAILED]
    assert len(a29) == 1
    assert a29[0].coin == AAA
    assert a29[0].detail is not None
    assert (a29[0].detail.stage, a29[0].detail.error_name) == ("보고서 저장", "DB 오류")
    assert a29[0].detail.after == (
        "AI를 껐습니다. 봉 받기는 계속하고, 프로그램을 다시 켜면 AI가 다시 돕니다."
    )
    assert CODE_REPORT_DONE not in rig.codes()


def test_an_exception_while_handling_the_answer_hides_the_exception_text(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    rig.start()

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("답에서 온 글")

    monkeypatch.setattr(report_round.ai_answers, "judge", broken)
    rig.round()
    (alert,) = [a for a in rig.alerts() if a.code == CODE_UNEXPECTED]
    assert alert.detail is not None
    assert "답에서 온 글" not in alert.detail.content
    assert alert.detail.content.startswith("ValueError")
    assert "답에서 온 글" not in alert.text
    assert [(i.state, i.failure) for i in rig.reports()] == [(ReportState.ERROR, "ValueError")]
    assert rig.statuses()[-1] is AiStatus.ERROR_OFF


def test_a_price_read_exception_that_is_not_a_quote_error_turns_the_ai_off(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    rig.start()

    def broken(market: str) -> Decimal:
        raise RuntimeError("뜻밖")

    monkeypatch.setattr(rig.quote, "get_price", broken)
    rig.round()
    assert rig.statuses()[-1] is AiStatus.ERROR_OFF
    assert CODE_PRICE_FAILED not in rig.codes()
    assert CODE_UNEXPECTED in rig.codes()
    assert [i.state for i in rig.reports()] == [ReportState.ERROR]


# --- 네 칸을 저장한 때 ---------------------------------------------------------------


def test_saving_the_four_slots_checks_claude_and_changes_the_status(
    build: Callable[..., Rig],
) -> None:
    rig = build(choices=())
    rig.start()
    assert rig.statuses() == [AiStatus.NOT_CHOSEN]
    rig.worker.save_settings((AAA,), 30, first_setup=False, candle_unit=HOUR, candle_count=100)
    rig.settle()
    assert rig.runner.check_calls == 0  # 네 칸이 아니면 부르지 않는다
    rig.worker.save_settings(
        (AAA,), 30, first_setup=False, candle_unit=HOUR, candle_count=100, ai_choices=FOUR[:3]
    )
    rig.settle()
    assert rig.runner.check_calls == 0
    assert rig.sink.of(SettingsView)[-1].ai_choices == ()
    rig.worker.save_settings(
        (AAA,), 30, first_setup=False, candle_unit=HOUR, candle_count=100, ai_choices=FOUR
    )
    rig.settle()
    assert rig.runner.check_calls == 1
    assert rig.statuses() == [AiStatus.NOT_CHOSEN, AiStatus.ON]
    conn = rig.conn()
    try:
        assert read_settings(conn).ai_choices == FOUR
    finally:
        conn.close()
    assert rig.sink.of(SettingsView)[-1].ai_choices == FOUR
    rig.worker.save_settings((AAA,), 7, first_setup=False)  # 다른 값만 저장해도 네 칸은 그대로다
    rig.settle()
    assert rig.sink.of(SettingsView)[-1].ai_choices == FOUR
    assert rig.runner.check_calls == 1


def test_saving_the_four_slots_with_a_claude_that_cannot_be_used_makes_an_a19(
    build: Callable[..., Rig],
) -> None:
    runner = FakeAiRunner(state=ClaudeState.NOT_FOUND, version=None)
    rig = build(choices=(), runner=runner)
    rig.start()
    rig.worker.save_settings(
        (AAA,), 30, first_setup=False, candle_unit=HOUR, candle_count=100, ai_choices=FOUR
    )
    rig.settle()
    assert rig.statuses() == [AiStatus.NOT_CHOSEN, AiStatus.NOT_FOUND]
    assert rig.codes() == [CODE_CLAUDE_OFF]


# --- 읽기 동작과 끝낼 때 -------------------------------------------------------------


def test_load_reports_and_load_report_send_the_views_and_an_unknown_report_is_unreadable(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    rig.round()
    item = rig.reports()[0]
    rig.sink.items.clear()
    rig.worker.load_reports(AAA)
    rig.worker.load_report(item.report_id)
    rig.worker.load_report(9999)
    rig.settle()
    (listed,) = rig.sink.of(ReportListView)
    assert (listed.market, listed.state, [i.report_id for i in listed.items]) == (
        AAA, LoadState.READY, [item.report_id],
    )  # fmt: skip
    (view,) = rig.sink.of(ReportView)
    assert view.report_id == item.report_id
    (missing,) = rig.sink.of(SaveResult)
    assert (missing.target, missing.ok, missing.message) == ("report", False, REPORT_UNREADABLE)


def test_stopping_the_worker_stops_the_running_sessions(build: Callable[..., Rig]) -> None:
    rig = build()
    rig.start()
    rig.worker.stop()
    assert rig.runner.stop_calls == 1


def test_the_default_runner_is_the_real_one_and_is_not_called_while_slots_are_not_chosen(
    tmp_path: Path,
) -> None:
    from coin_report_trader.ai.claude_cli import ClaudeCodeRunner
    from coin_report_trader.core import SystemClock

    worker = DataWorker(
        database_file=tmp_path / "app.db",
        logs_dir=tmp_path / "logs",
        clock=SystemClock(),
        quote=None,
        sink=lambda update: None,
    )
    assert isinstance(worker._runner, ClaudeCodeRunner)
    assert worker._sessions_root == tmp_path / report_round.SESSIONS_FOLDER
