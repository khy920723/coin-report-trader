"""자료 스레드(M1 명세 3.8, 3.9): 켜는 차례, 받기, 알림, 일시 차단, 설정, 코인 목록.

Qt 없이 `DataWorker`를 직접 돌린다. 가짜 시세와 가짜 시계를 쓰고, 화면에 가는 값은 시험이 모은다.
시각은 가짜 시계의 `advance`로만 옮긴다(실제 시간은 멈추지 않게 하는 안전 시간으로만 쓴다).
"""

import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fakes.clock import DEFAULT_START, FakeClock
from fakes.quote import OK, FakeQuoteSource, make_candles

from coin_report_trader.app import data_thread
from coin_report_trader.app.data_thread import DATA_THREAD_NAME, DataWorker
from coin_report_trader.core import (
    AlertCategory,
    Candle,
    CandleUnit,
    CoinView,
    LoadState,
    Market,
    MarketsView,
    QuoteError,
    SaveResult,
    SettingsView,
    StorageError,
    UiUpdate,
)
from coin_report_trader.core.texts import (
    ERROR_BLOCKED,
    ERROR_NETWORK,
    ERROR_TIMEOUT,
    SETTINGS_COIN_NOT_LISTED,
    SETTINGS_SAVE_FAILED,
    SETTINGS_SAVED,
)
from coin_report_trader.storage.alerts import list_alerts
from coin_report_trader.storage.candles import read_candles, write_candles
from coin_report_trader.storage.database import migrate, open_database
from coin_report_trader.storage.settings import read_fetched_at, read_settings, write_settings

AAA, BBB, CCC = "KRW-AAA", "KRW-BBB", "KRW-CCC"
DAY, HOUR = CandleUnit.DAY, CandleUnit.MINUTE_60
SAFETY = 10.0
NOW = DEFAULT_START  # 2026-01-05 03:00 UTC(한국 12:00)
TODAY_START = datetime(2026, 1, 5, tzinfo=UTC)  # 지금 걸쳐 있는(미완성) 일봉의 시작
THIS_HOUR = datetime(2026, 1, 5, 3, tzinfo=UTC)


def markets(*codes: str) -> list[Market]:
    return [Market(code, f"이름 {code}", False) for code in codes]


@dataclass
class Sink:
    """화면에 가는 값을 모은다."""

    items: list[UiUpdate] = field(default_factory=list)
    threads: set[str] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __call__(self, update: UiUpdate) -> None:
        with self.lock:
            self.items.append(update)
            self.threads.add(threading.current_thread().name)

    def of(self, kind: type) -> list[Any]:
        with self.lock:
            return [u for u in self.items if isinstance(u, kind)]

    def coin(self, market: str) -> list[CoinView]:
        return [v for v in self.of(CoinView) if v.market == market]

    def clear(self) -> None:
        with self.lock:
            self.items.clear()


@dataclass
class Rig:
    worker: DataWorker
    sink: Sink
    clock: FakeClock
    quote: FakeQuoteSource
    db: Path
    logs: Path

    def start(self) -> None:
        self.worker.start()
        self.settle()

    def settle(self) -> None:
        assert self.worker.wait_idle(SAFETY), "자료 스레드가 쉬지 않는다"

    def advance(self, seconds: float) -> None:
        self.clock.advance(seconds)
        self.settle()

    def alerts(self) -> list[Any]:
        conn = open_database(self.db)
        try:
            return list(reversed(list_alerts(conn)))  # 오래된 것이 앞
        finally:
            conn.close()

    def stored(self, market: str, unit: CandleUnit) -> tuple[Candle, ...]:
        conn = open_database(self.db)
        try:
            return read_candles(conn, market, unit)
        finally:
            conn.close()

    def fetched_at(self, market: str) -> datetime | None:
        conn = open_database(self.db)
        try:
            return read_fetched_at(conn, market)
        finally:
            conn.close()

    def settings(self) -> SettingsView:
        conn = open_database(self.db)
        try:
            return read_settings(conn)
        finally:
            conn.close()


def make_rig(
    tmp_path: Path,
    *,
    coins: tuple[str, ...] = (AAA,),
    done: bool = True,
    days: int | None = 30,
    quote: FakeQuoteSource | None = None,
    bars: bool = True,
) -> Rig:
    db = tmp_path / "app.db"
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    conn = open_database(db)
    try:
        migrate(conn)
        write_settings(conn, SettingsView(coins, days, done))
    finally:
        conn.close()
    clock = FakeClock()
    source = quote or FakeQuoteSource(markets=markets(AAA, BBB, CCC), clock=clock)
    if bars:
        for coin in coins:
            source.set_candles(coin, DAY, make_candles(coin, DAY, 40, last_start=TODAY_START))
            source.set_candles(coin, HOUR, make_candles(coin, HOUR, 200, last_start=THIS_HOUR))
    sink = Sink()
    worker = DataWorker(database_file=db, logs_dir=logs, clock=clock, quote=source, sink=sink)
    return Rig(worker, sink, clock, source, db, logs)


@pytest.fixture
def rigs() -> Iterator[list[Rig]]:
    made: list[Rig] = []
    yield made
    for rig in made:
        rig.worker.stop()
        rig.worker.join(SAFETY)
        assert rig.worker.thread is None or not rig.worker.thread.is_alive()


@pytest.fixture
def build(tmp_path: Path, rigs: list[Rig]) -> Any:
    def factory(**kwargs: Any) -> Rig:
        rig = make_rig(tmp_path, **kwargs)
        rigs.append(rig)
        return rig

    return factory


def states(views: list[CoinView]) -> list[LoadState]:
    return [v.state for v in views]


# --- 켜는 차례, 스레드, 다리 ---------------------------------------------------------------------


def test_the_thread_is_a_named_daemon_and_sends_settings_before_the_coin_views(build: Any) -> None:
    rig = build(coins=(AAA, BBB))
    rig.start()
    thread = rig.worker.thread
    assert thread is not None
    assert thread.name == DATA_THREAD_NAME
    assert thread.daemon
    assert rig.sink.threads == {DATA_THREAD_NAME}
    kinds = [type(u).__name__ for u in rig.sink.items]
    assert kinds[:3] == ["SettingsView", "CoinView", "CoinView"]
    first = rig.sink.items[0]
    assert isinstance(first, SettingsView)
    assert (first.coins, first.retention_days, first.first_setup_done, first.state) == (
        (AAA, BBB), 30, True, LoadState.READY,
    )  # fmt: skip
    initial = rig.sink.items[1:3]
    assert [(v.market, v.state) for v in initial] == [
        (AAA, LoadState.NO_DATA),
        (BBB, LoadState.NO_DATA),
    ]
    assert all(v.fetched_at is None and v.as_of == NOW for v in initial)


def test_the_initial_view_uses_the_stored_candles(build: Any, tmp_path: Path) -> None:
    rig = build(coins=(AAA,), done=False)  # 처음 설정 전이라 받기는 하지 않는다
    conn = open_database(rig.db)
    try:
        write_candles(conn, make_candles(AAA, DAY, 40, last_start=TODAY_START - timedelta(days=1)))
    finally:
        conn.close()
    rig.start()
    view = rig.sink.coin(AAA)[0]
    assert view.state is LoadState.READY
    assert view.periods[3].count == 30
    assert view.indicator is not None
    assert rig.quote.requests == []


def test_actions_that_come_before_the_start_are_not_dropped(build: Any) -> None:
    rig = build(coins=(AAA,), done=False)
    rig.worker.refresh_markets()  # 시작 전에 큐에 들어왔다
    rig.start()
    assert [type(u).__name__ for u in rig.sink.items][:3] == [
        "SettingsView", "CoinView", "MarketsView",
    ]  # fmt: skip
    assert len(rig.quote.market_requests()) == 1


def test_the_other_actions_do_nothing_in_the_first_number(build: Any) -> None:
    rig = build()
    rig.start()
    rig.sink.clear()
    rig.worker.mark_seen((1, 2))
    rig.worker.delete_alert(1)
    rig.worker.clear_alerts(AlertCategory.ERROR)
    rig.worker.save_webhook(AlertCategory.ERROR, "x")
    rig.worker.delete_webhook(AlertCategory.ERROR)
    rig.worker.send_test(AlertCategory.ERROR)
    rig.settle()
    assert rig.sink.items == []


def test_stop_ends_the_thread_without_waiting_for_the_next_time(build: Any) -> None:
    rig = build()
    rig.start()
    thread = rig.worker.thread
    rig.worker.stop()
    rig.worker.join(SAFETY)
    assert thread is not None
    assert not thread.is_alive()


def test_updates_after_stop_are_dropped(build: Any) -> None:
    rig = build()
    rig.start()
    rig.sink.clear()
    rig.worker.stop()
    rig.worker.join(SAFETY)
    rig.worker.refresh_markets()
    assert rig.sink.items == []


def test_a_sink_that_fails_does_not_stop_the_thread(
    build: Any, caplog: pytest.LogCaptureFixture
) -> None:
    rig = build(done=False)
    calls: list[UiUpdate] = []

    def broken(update: UiUpdate) -> None:
        calls.append(update)
        raise RuntimeError("화면이 닫혔다")

    rig.worker._sink = broken
    rig.start()
    rig.worker.refresh_markets()
    rig.settle()
    assert [type(c).__name__ for c in calls] == [
        "SettingsView",
        "CoinView",
        "MarketsView",
        "MarketsView",
    ]


# --- 받기: 범위, 값, 차례 ------------------------------------------------------------------------


def test_first_fetch_saves_the_candles_and_sends_loading_then_the_result(build: Any) -> None:
    rig = build()
    rig.quote.set_candles(AAA, DAY, make_candles(AAA, DAY, 450, last_start=TODAY_START))
    rig.quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 300, last_start=THIS_HOUR))
    rig.start()
    day_requests = rig.quote.candle_requests(AAA, DAY)
    hour_requests = rig.quote.candle_requests(AAA, HOUR)
    assert [r.count for r in day_requests] == [
        200,
        200,
        200,
    ]  # 450개: 요청 셋(한 요청에 200개 이하)
    assert len(hour_requests) == 1
    assert len(rig.stored(AAA, DAY)) == 450
    assert len(rig.stored(AAA, HOUR)) == 200
    views = rig.sink.coin(AAA)
    assert states(views) == [LoadState.NO_DATA, LoadState.LOADING, LoadState.READY]
    last = views[-1]
    assert last.fetched_at == NOW
    assert last.as_of == NOW
    assert last.error_name is None
    assert last.last_incomplete is True  # 지금 걸친 일봉이 저장되어 있다
    assert last.periods[0].count == 449  # 전체: 완성된 일봉
    assert last.indicator is not None
    assert rig.fetched_at(AAA) == NOW
    assert rig.alerts() == []


def test_the_next_fetch_gets_only_the_new_candles_and_overwrites_the_last(build: Any) -> None:
    rig = build()
    rig.start()
    saved_close = rig.stored(AAA, DAY)[-1].close
    rig.advance(24 * 3600)  # 이튿날 한국 시간 12시
    rig.quote.put_candles(*make_candles(AAA, DAY, 2, last_start=TODAY_START + timedelta(days=1)))
    before = len(rig.quote.candle_requests(AAA, DAY))
    rig.worker.retry_candles()
    rig.settle()
    new_requests = rig.quote.candle_requests(AAA, DAY)[before:]
    assert len(new_requests) == 1
    stored = rig.stored(AAA, DAY)
    assert stored[-1].start == TODAY_START + timedelta(days=1)
    assert len(stored) == 41  # 40개 + 새 하루. 겹친 하루는 덮어썼다
    assert stored[-2].close != saved_close or stored[-2].start == TODAY_START


def test_the_fetch_every_sixty_minutes(build: Any) -> None:
    rig = build()
    rig.start()
    count = len(rig.quote.requests)
    assert rig.clock.waits[-1] == data_thread.FETCH_INTERVAL
    rig.advance(3599)
    assert len(rig.quote.requests) == count
    rig.advance(1)
    assert len(rig.quote.requests) > count
    assert states(rig.sink.coin(AAA))[-2:] == [LoadState.LOADING, LoadState.READY]
    # 앞 받기가 끝난 때부터 다시 60분이다
    count = len(rig.quote.requests)
    rig.advance(3599)
    assert len(rig.quote.requests) == count
    rig.advance(1)
    assert len(rig.quote.requests) > count


def test_no_next_time_waits_an_hour_and_does_nothing_when_it_wakes(build: Any) -> None:
    rig = build(coins=(AAA,), done=False)  # 처음 설정 전: (라)가 없다
    rig.start()
    assert rig.clock.waits[-1] == data_thread.IDLE_SECONDS
    sent = len(rig.sink.items)
    rig.advance(data_thread.IDLE_SECONDS)
    rig.advance(data_thread.IDLE_SECONDS)
    assert rig.quote.requests == []
    assert len(rig.sink.items) == sent
    assert rig.clock.waits[-1] == data_thread.IDLE_SECONDS


def test_retry_does_nothing_without_coins_even_while_blocked(build: Any) -> None:
    rig = build(coins=(), done=False)
    rig.start()
    rig.sink.clear()
    rig.worker.retry_candles()
    rig.settle()
    assert rig.sink.items == []
    assert rig.quote.requests == []


def test_actions_that_arrive_during_a_fetch_run_after_it(build: Any) -> None:
    release = threading.Event()
    inside = threading.Event()
    quote = FakeQuoteSource(markets=markets(AAA, BBB))
    real = quote.get_candles

    def gated(market: str, unit: CandleUnit, *, to: datetime | None, count: int) -> Any:
        inside.set()
        assert release.wait(SAFETY)
        return real(market, unit, to=to, count=count)

    quote.get_candles = gated  # type: ignore[method-assign]
    rig = build(coins=(AAA,), quote=quote)
    rig.worker.start()
    assert inside.wait(SAFETY)
    rig.worker.retry_candles()
    rig.worker.refresh_markets()
    assert rig.quote.market_requests() == []  # 받는 중이라 아직 처리하지 않았다
    release.set()
    rig.settle()
    kinds = [type(u).__name__ for u in rig.sink.items]
    assert kinds.index("MarketsView") > max(i for i, k in enumerate(kinds) if k == "CoinView") - 3
    assert len(rig.quote.market_requests()) == 1
    assert states(rig.sink.coin(AAA)).count(LoadState.LOADING) == 2  # 처음 받기와 "다시 시도"


# --- 미완성 봉과 "봉이 모자람" -----------------------------------------------------------


def one_complete_day(rig: Rig, coin: str = AAA) -> None:
    rig.quote.set_candles(
        coin, DAY, make_candles(coin, DAY, 1, last_start=TODAY_START - timedelta(days=1))
    )
    rig.quote.set_candles(coin, HOUR, ())


def test_not_enough_candles_is_no_data_with_one_alert_and_it_is_not_repeated(build: Any) -> None:
    rig = build()
    one_complete_day(rig)
    rig.start()
    last = rig.sink.coin(AAA)[-1]
    assert last.state is LoadState.NO_DATA
    assert last.fetched_at == NOW  # "봉이 모자람"이어도 받기는 성공이다
    assert all(p.change is None for p in last.periods)
    alerts = rig.alerts()
    assert len(alerts) == 1
    alert = alerts[0]
    assert (alert.code, alert.category, alert.coin, alert.mode.value) == (
        "A-1",
        AlertCategory.ERROR,
        AAA,
        "paper",
    )
    assert alert.text == "봉 통계를 만들지 못했습니다(봉이 모자람)."
    assert alert.detail is not None
    assert (alert.detail.stage, alert.detail.error_name) == ("봉 통계 계산", "봉이 모자람")
    assert alert.detail.content == "완성된 봉이 모자라 다섯 기간 모두 통계를 셈하지 못했습니다."
    assert alert.detail.coin == AAA
    assert alert.seen is False
    assert alert.discord_failed is False
    assert alert.created_at == NOW
    rig.advance(3600)
    rig.advance(3600)
    rig.worker.retry_candles()
    rig.settle()
    assert len(rig.alerts()) == 1  # B33: 세 번 더 받아도 하나다


def test_the_alert_after_text_names_the_next_fetch_time(build: Any) -> None:
    rig = build()
    one_complete_day(rig)
    rig.start()
    detail = rig.alerts()[0].detail
    assert detail is not None
    assert detail.after == "다음 받는 때(13:00)에 다시 받습니다."  # 한국 시간 12:00 + 60분


def test_the_next_fetch_time_text_carries_the_date_when_it_is_tomorrow(build: Any) -> None:
    rig = build()
    one_complete_day(rig)
    rig.clock.set_now(datetime(2026, 1, 5, 14, 30, tzinfo=UTC))  # 한국 23:30
    rig.start()
    detail = rig.alerts()[0].detail
    assert detail is not None
    assert detail.after == "다음 받는 때(2026-01-06 00:30)에 다시 받습니다."


def test_the_unfinished_day_is_flagged_and_left_out(build: Any) -> None:
    rig = build()
    rig.start()
    view = rig.sink.coin(AAA)[-1]
    assert view.state is LoadState.READY
    assert view.last_incomplete is True
    assert rig.stored(AAA, DAY)[-1].start == TODAY_START
    assert view.periods[0].last_start == TODAY_START - timedelta(days=1)


# --- 요청의 실패와 한 받기의 알림 ---------------------------------------------------------------


def fail(
    rig: Rig, *errors: QuoteError | object, unit: CandleUnit | None = None, market: str | None = AAA
) -> None:
    rig.quote.script(*errors, kind="candles", market=market, unit=unit)  # type: ignore[arg-type]


def test_one_unit_fails_the_other_is_kept(build: Any) -> None:
    quote = FakeQuoteSource(markets=markets(AAA))
    rig = build(quote=quote)
    fail(rig, QuoteError(ERROR_TIMEOUT), unit=DAY)
    rig.start()
    view = rig.sink.coin(AAA)[-1]
    assert (view.state, view.error_name, view.fetched_at) == (LoadState.FAILED, ERROR_TIMEOUT, None)
    assert rig.stored(AAA, DAY) == ()
    assert len(rig.stored(AAA, HOUR)) == 200
    alert = rig.alerts()[0]
    assert alert.text == "봉 통계를 만들지 못했습니다(시간 초과)."
    assert alert.detail is not None
    assert (alert.detail.stage, alert.detail.content) == ("봉 받기", "실패한 봉: 일봉")
    assert len(rig.alerts()) == 1
    assert rig.fetched_at(AAA) is None
    assert view.periods[4].count > 0  # 저장된 60분봉의 값은 담는다


def test_both_units_failing_make_one_alert_with_the_first_error_name(build: Any) -> None:
    rig = build()
    fail(rig, QuoteError(ERROR_NETWORK), unit=DAY)
    fail(rig, QuoteError(ERROR_TIMEOUT), unit=HOUR)
    rig.start()
    view = rig.sink.coin(AAA)[-1]
    assert view.error_name == ERROR_NETWORK
    (alert,) = rig.alerts()
    assert alert.detail is not None
    assert alert.detail.error_name == ERROR_NETWORK
    assert alert.detail.content == "실패한 봉: 일봉, 60분봉"


def test_the_same_error_is_alerted_again_only_after_a_success(build: Any) -> None:
    rig = build()
    rig.quote.script(
        QuoteError(ERROR_NETWORK), QuoteError(ERROR_NETWORK), OK, QuoteError(ERROR_NETWORK),
        kind="candles", market=AAA, unit=DAY,
    )  # fmt: skip
    rig.start()  # 실패
    assert len(rig.alerts()) == 1
    rig.worker.retry_candles()  # 같은 이름의 실패
    rig.settle()
    assert len(rig.alerts()) == 1
    rig.worker.retry_candles()  # 성공
    rig.settle()
    assert rig.sink.coin(AAA)[-1].state is LoadState.READY
    rig.worker.retry_candles()  # 성공 뒤의 실패
    rig.settle()
    assert len(rig.alerts()) == 2


def test_a_different_error_name_is_a_new_alert(build: Any) -> None:
    rig = build()
    rig.quote.script(
        QuoteError(ERROR_NETWORK), QuoteError(ERROR_TIMEOUT), kind="candles", market=AAA, unit=DAY
    )
    rig.start()
    rig.worker.retry_candles()
    rig.settle()
    assert [a.text for a in rig.alerts()] == [
        "봉 통계를 만들지 못했습니다(네트워크 끊김).",
        "봉 통계를 만들지 못했습니다(시간 초과).",
    ]


def test_a_failed_first_day_fetch_keeps_nothing_and_the_next_fetch_starts_over(build: Any) -> None:
    rig = build()
    rig.quote.set_candles(AAA, DAY, make_candles(AAA, DAY, 450, last_start=TODAY_START))
    rig.quote.script(OK, QuoteError(ERROR_NETWORK), kind="candles", market=AAA, unit=DAY)
    rig.start()
    assert rig.stored(AAA, DAY) == ()
    view = rig.sink.coin(AAA)[-1]
    assert (view.state, view.error_name, view.fetched_at) == (LoadState.FAILED, ERROR_NETWORK, None)
    assert len(rig.stored(AAA, HOUR)) == 200
    assert len(rig.alerts()) == 1
    before = len(rig.quote.candle_requests(AAA, DAY))
    rig.worker.retry_candles()
    rig.settle()
    assert len(rig.quote.candle_requests(AAA, DAY)) - before == 3
    assert len(rig.stored(AAA, DAY)) == 450


class RaisingQuote(FakeQuoteSource):
    """요청에서 `QuoteError`가 아닌 예외를 내는 `QuoteSource`."""

    def __init__(self, error: Exception, unit: CandleUnit | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.error = error
        self.only = unit

    def get_candles(self, market: str, unit: CandleUnit, *, to: datetime | None, count: int) -> Any:
        if self.only is None or self.only == unit:
            raise self.error
        return super().get_candles(market, unit, to=to, count=count)

    def list_markets(self) -> tuple[Market, ...]:
        raise self.error


def test_an_exception_from_a_request_is_a_request_failure_not_an_unexpected_one(build: Any) -> None:
    quote = RaisingQuote(ValueError("나쁜 값 /srv/data"), DAY, markets=markets(AAA))
    rig = build(quote=quote)
    quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 5, last_start=THIS_HOUR))
    rig.start()
    view = rig.sink.coin(AAA)[-1]
    assert (view.state, view.error_name) == (LoadState.FAILED, "ValueError")
    (alert,) = rig.alerts()
    assert (alert.code, alert.category) == ("A-1", AlertCategory.ERROR)
    assert alert.text == "봉 통계를 만들지 못했습니다(ValueError)."
    assert alert.detail is not None
    assert alert.detail.stage == "봉 받기"
    assert "나쁜 값" in alert.detail.content
    assert len(rig.stored(AAA, HOUR)) == 5  # 다른 단위는 계속 받는다


# --- DB의 실패(A-29, A-30) ----------------------------------------------------------------------


def test_a_candle_save_failure_is_one_a29_with_the_coin_and_no_a1(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    real = data_thread.write_candles
    failing = {"on": True}

    def maybe_fail(conn: Any, candles: Any) -> int:
        if failing["on"]:
            raise StorageError("봉 저장에 실패했습니다: OperationalError")
        return real(conn, candles)

    monkeypatch.setattr(data_thread, "write_candles", maybe_fail)
    rig.start()
    view = rig.sink.coin(AAA)[-1]
    assert (view.state, view.error_name, view.fetched_at) == (LoadState.FAILED, "DB 오류", None)
    (alert,) = rig.alerts()
    assert (alert.code, alert.category, alert.coin) == ("A-29", AlertCategory.ERROR, AAA)
    assert alert.text == "저장하지 못했습니다(DB 오류)."
    assert alert.detail is not None
    assert alert.detail.stage == "봉 저장"
    assert alert.detail.after == "다음 받는 때에 다시 받아 저장합니다."
    assert alert.detail.error_name == "DB 오류"
    rig.worker.retry_candles()  # 저장이 한 번 성공하기 전에는 다시 만들지 않는다
    rig.settle()
    assert len(rig.alerts()) == 1
    failing["on"] = False
    rig.worker.retry_candles()
    rig.settle()
    assert rig.sink.coin(AAA)[-1].state is LoadState.READY
    failing["on"] = True
    rig.worker.retry_candles()
    rig.settle()
    assert [a.code for a in rig.alerts()] == ["A-29", "A-29"]  # 성공한 뒤의 실패는 다시 만든다


def test_failing_to_read_the_last_candle_is_an_a30_of_the_candle_stage(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()

    def unreadable(conn: Any, market: str, unit: CandleUnit) -> None:
        raise StorageError("마지막 봉 읽기에 실패했습니다: OperationalError")

    monkeypatch.setattr(data_thread, "last_candle_start", unreadable)
    rig.start()
    assert rig.quote.candle_requests() == []  # 그 단위의 요청은 건너뛴다
    view = rig.sink.coin(AAA)[-1]
    assert (view.state, view.error_name) == (LoadState.FAILED, "DB 오류")
    (alert,) = rig.alerts()
    assert (alert.code, alert.category, alert.coin) == ("A-30", AlertCategory.OTHER, AAA)
    assert alert.text == "예상하지 못한 오류가 났습니다(DB 오류)."
    assert alert.detail is not None
    assert alert.detail.stage == "봉 받기"
    assert alert.detail.after == "그 일을 그만두고 다음 일을 계속합니다."
    rig.advance(3600)  # 같은 단계에 같은 예외 종류는 60분 안에 다시 만들지 않는다
    assert len(rig.alerts()) == 2  # 60분이 지났으므로 다시 만든다
    rig.worker.retry_candles()
    rig.settle()
    assert len(rig.alerts()) == 2  # 방금 만들었으므로 만들지 않는다


def test_a_failed_fetched_at_write_is_an_a29_and_the_values_use_the_new_candles(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()

    def refuse(conn: Any, market: str, moment: datetime) -> None:
        raise StorageError("받은 때 저장에 실패했습니다: OperationalError")

    monkeypatch.setattr(data_thread, "write_fetched_at", refuse)
    rig.start()
    view = rig.sink.coin(AAA)[-1]
    assert (view.state, view.error_name, view.fetched_at) == (LoadState.FAILED, "DB 오류", None)
    assert view.periods[3].count == 30  # 새로 저장된 봉으로 셈한 값이다
    (alert,) = rig.alerts()
    assert alert.code == "A-29"
    assert alert.detail is not None
    assert alert.detail.stage == "봉 저장"
    assert len(rig.stored(AAA, DAY)) == 40
    assert rig.worker._backoff == 0


def test_a_failure_in_the_statistics_is_an_a30_and_the_fetch_still_counts(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    real = data_thread.compute_candle_stats
    calls = {"n": 0}

    def sometimes_broken(candles: Any, now: datetime) -> Any:
        calls["n"] += 1
        if calls["n"] > 1:  # 켤 때 처음의 셈은 지나가고, 받기 뒤의 셈에서 난다
            raise RuntimeError("셈이 깨졌다")
        return real(candles, now)

    monkeypatch.setattr(data_thread, "compute_candle_stats", sometimes_broken)
    rig.start()
    view = rig.sink.coin(AAA)[-1]
    assert (view.state, view.error_name) == (LoadState.FAILED, "RuntimeError")
    assert view.fetched_at == NOW  # 요청과 저장은 끝났다
    assert rig.fetched_at(AAA) == NOW
    (alert,) = rig.alerts()
    assert (alert.code, alert.category, alert.coin) == ("A-30", AlertCategory.OTHER, AAA)
    assert alert.detail is not None
    assert (alert.detail.stage, alert.detail.error_name) == ("봉 통계 계산", "RuntimeError")
    assert alert.text == "예상하지 못한 오류가 났습니다(RuntimeError)."


def test_an_alert_that_cannot_be_saved_does_not_stop_the_fetching(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    fail(rig, QuoteError(ERROR_NETWORK), unit=DAY)

    def refuse(*args: Any, **kwargs: Any) -> int:
        raise StorageError("알림 저장에 실패했습니다: OperationalError")

    monkeypatch.setattr(data_thread.alert_store, "insert_alert", refuse)
    rig.start()
    assert rig.sink.coin(AAA)[-1].state is LoadState.FAILED  # 끝을 알리는 CoinView는 온다
    before = len(rig.quote.requests)
    rig.advance(3600)
    assert len(rig.quote.requests) > before  # 다음 받는 때에도 받는다
    assert rig.alerts() == []


def test_an_unexpected_exception_in_a_task_is_an_a30_and_the_thread_goes_on(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build(done=False)
    rig.start()

    def broken() -> None:
        raise RuntimeError("뜻밖")

    monkeypatch.setattr(rig.worker, "_refresh_markets", broken)
    rig.worker.refresh_markets()
    rig.worker.refresh_markets()
    rig.settle()
    (alert,) = rig.alerts()  # 같은 단계에 같은 예외 종류는 60분 안에 한 번
    assert (alert.code, alert.coin) == ("A-30", None)
    assert alert.detail is not None
    assert (alert.detail.stage, alert.detail.error_name) == ("코인 목록 받기", "RuntimeError")
    assert rig.worker.thread is not None
    assert rig.worker.thread.is_alive()


# --- 일시 차단(2의 3) -------------------------------------------------------------------------


def block(
    rig: Rig, retry_after: float | None, *, kind: str = "candles", market: str | None = AAA
) -> None:
    rig.quote.script(QuoteError(ERROR_BLOCKED, retry_after), kind=kind, market=market)


def blocked_views(rig: Rig, market: str) -> list[CoinView]:
    return [v for v in rig.sink.coin(market) if v.error_name == ERROR_BLOCKED]


def test_a_block_stops_the_round_and_marks_every_unrequested_coin(build: Any) -> None:
    rig = build(coins=(AAA, BBB))
    block(rig, 120.0)
    rig.start()
    until = NOW + timedelta(seconds=120)
    first, second = rig.sink.coin(AAA), rig.sink.coin(BBB)
    assert states(first)[-2:] == [LoadState.LOADING, LoadState.FAILED]
    assert states(second) == [LoadState.NO_DATA, LoadState.FAILED]  # 둘째 코인은 LOADING이 없다
    for view in (first[-1], second[-1]):
        assert (view.error_name, view.blocked_until, view.as_of) == (ERROR_BLOCKED, until, NOW)
    assert [r.market for r in rig.quote.candle_requests()] == [AAA]  # 앞 코인의 요청만 나갔다
    alerts = rig.alerts()
    assert [(a.code, a.coin) for a in alerts] == [("A-1", AAA), ("A-1", BBB)]
    contents = [a.detail.content for a in alerts if a.detail]
    assert contents == [
        "거래소가 공개 시세 요청을 잠시 막았습니다.",
        "앞 코인에서 일시 차단을 받아 이 코인은 요청하지 않았습니다.",
    ]
    afters = {a.detail.after for a in alerts if a.detail}
    assert afters == {"14:00까지 봉 받기를 멈췄습니다."} or afters == {
        "12:02까지 봉 받기를 멈췄습니다."
    }
    assert all(a.text == "봉 통계를 만들지 못했습니다(일시 차단)." for a in alerts)


def test_while_blocked_nothing_is_requested_and_it_is_fetched_once_after(build: Any) -> None:
    rig = build(coins=(AAA, BBB))
    block(rig, 120.0)
    rig.start()
    sent = len(rig.quote.requests)
    # 차단 중의 "다시 시도": 요청 없이 차단 묶음, A-1 없음
    rig.sink.clear()
    rig.worker.retry_candles()
    rig.settle()
    assert len(rig.quote.requests) == sent
    assert [(v.market, v.state, v.error_name) for v in rig.sink.of(CoinView)] == [
        (AAA, LoadState.FAILED, ERROR_BLOCKED),
        (BBB, LoadState.FAILED, ERROR_BLOCKED),
    ]
    assert len(rig.alerts()) == 2
    # 차단 중의 코인 목록: 요청 없이 "일시 차단 중", A-31 없음
    rig.worker.refresh_markets()
    rig.settle()
    assert rig.quote.market_requests() == []
    view = rig.sink.of(MarketsView)[-1]
    assert (view.state, view.error_name, view.blocked_until) == (
        LoadState.FAILED, ERROR_BLOCKED, NOW + timedelta(seconds=120),
    )  # fmt: skip
    assert len(rig.alerts()) == 2
    rig.advance(119)
    assert len(rig.quote.requests) == sent
    rig.advance(1)  # blocked_until: 저절로 한 번 받는다
    assert len(rig.quote.candle_requests(AAA)) > 0
    assert {r.market for r in rig.quote.candle_requests()[sent:]} == {AAA, BBB}
    after = len(rig.quote.requests)
    rig.advance(3599)  # 그 받기 뒤 60분이다
    assert len(rig.quote.requests) == after
    rig.advance(1)
    assert len(rig.quote.requests) > after


def test_blocked_requests_do_not_move_the_sixty_minute_timer_or_the_fetched_at(build: Any) -> None:
    rig = build()
    rig.start()  # 성공: fetched_at = NOW, 다음 (라)는 NOW + 60분
    fetched = rig.sink.coin(AAA)[-1].fetched_at
    assert fetched == NOW
    rig.advance(1800)
    block(rig, 7200.0)
    rig.worker.retry_candles()  # 차단을 받는다
    rig.settle()
    count = len(rig.quote.requests)
    view = rig.sink.coin(AAA)[-1]
    assert (view.error_name, view.fetched_at) == (ERROR_BLOCKED, fetched)
    rig.advance(1800)  # 처음 (라)의 때: 차단 중이라 요청하지 않는다
    assert len(rig.quote.requests) == count
    rig.worker.retry_candles()  # 요청하지 않은 받기도 fetched_at을 바꾸지 않는다
    rig.settle()
    assert rig.sink.coin(AAA)[-1].fetched_at == fetched
    assert len(rig.quote.requests) == count


def test_a_save_during_a_block_saves_then_sends_blocked_views_without_requests(build: Any) -> None:
    rig = build(coins=(AAA,))
    block(rig, 600.0)
    rig.start()
    rig.worker.refresh_markets()  # 차단 중이므로 목록은 못 받았다
    rig.settle()
    count = len(rig.quote.requests)
    rig.sink.clear()
    rig.worker.save_settings((AAA,), 90, first_setup=False)  # 코인이 같으면 목록과 견주지 않는다
    rig.settle()
    kinds = [type(u).__name__ for u in rig.sink.items]
    assert kinds == ["SaveResult", "SettingsView", "CoinView"]
    assert rig.sink.coin(AAA)[0].error_name == ERROR_BLOCKED
    assert len(rig.quote.requests) == count
    assert len(rig.alerts()) == 1  # A-1은 처음 받기의 것 하나뿐이다


def seconds_blocked(rig: Rig) -> float:
    view = rig.sink.coin(AAA)[-1]
    assert view.blocked_until is not None
    return (view.blocked_until - rig.clock.now()).total_seconds()


def test_the_backoff_steps_when_the_exchange_gives_no_time(build: Any) -> None:
    rig = build()
    rig.quote.script(*[QuoteError(ERROR_BLOCKED)] * 7, kind="candles", market=AAA)
    rig.start()
    seen = [seconds_blocked(rig)]
    for _ in range(6):
        rig.advance(seen[-1])  # 차단이 끝나면 저절로 받고 또 차단을 받는다
        seen.append(seconds_blocked(rig))
    assert seen == [300, 600, 1200, 2400, 3600, 3600, 3600]


def test_a_time_given_by_the_exchange_does_not_move_the_steps(build: Any) -> None:
    rig = build()
    rig.quote.script(
        QuoteError(ERROR_BLOCKED), QuoteError(ERROR_BLOCKED, 120.0), QuoteError(ERROR_BLOCKED),
        kind="candles", market=AAA,
    )  # fmt: skip
    rig.start()
    seen = [seconds_blocked(rig)]
    for _ in range(2):
        rig.advance(seen[-1])
        seen.append(seconds_blocked(rig))
    assert seen == [300, 120, 600]


def test_a_success_returns_the_steps_to_the_start(build: Any) -> None:
    rig = build()
    rig.quote.script(
        QuoteError(ERROR_BLOCKED), QuoteError(ERROR_BLOCKED), OK, QuoteError(ERROR_BLOCKED),
        kind="candles", market=AAA, unit=DAY,
    )  # fmt: skip
    rig.start()
    assert seconds_blocked(rig) == 300
    rig.advance(300)
    assert seconds_blocked(rig) == 600
    rig.advance(600)  # 성공: 처음으로 돌아간다
    assert rig.sink.coin(AAA)[-1].state is LoadState.READY
    rig.advance(3600)
    assert seconds_blocked(rig) == 300


def markets_block_seconds(rig: Rig) -> float:
    view = rig.sink.of(MarketsView)[-1]
    assert (view.state, view.error_name) == (LoadState.FAILED, ERROR_BLOCKED)
    assert view.blocked_until is not None
    return (view.blocked_until - rig.clock.now()).total_seconds()


def test_the_list_of_markets_shares_the_steps_with_the_candles(build: Any) -> None:
    rig = build(coins=(AAA,), done=True)
    rig.quote.script(QuoteError(ERROR_BLOCKED), QuoteError(ERROR_NETWORK), kind="candles", unit=DAY)
    rig.quote.script(QuoteError(ERROR_BLOCKED), kind="markets")
    rig.start()  # 봉이 차단: 5분
    assert seconds_blocked(rig) == 300
    rig.advance(300)  # 저절로 받는 봉은 차단이 아닌 실패다(차례를 처음으로 돌리지 않는다)
    assert rig.sink.coin(AAA)[-1].error_name == ERROR_NETWORK
    rig.worker.refresh_markets()
    rig.settle()
    assert markets_block_seconds(rig) == 600  # 같은 차례의 다음 칸


def test_a_success_of_the_list_does_not_return_the_steps_to_the_start(build: Any) -> None:
    rig = build(coins=(), done=False, days=None, bars=False)
    rig.quote.script(QuoteError(ERROR_BLOCKED), OK, QuoteError(ERROR_BLOCKED), kind="markets")
    rig.start()
    rig.worker.refresh_markets()
    rig.settle()
    assert markets_block_seconds(rig) == 300
    rig.advance(300)
    rig.worker.refresh_markets()  # 성공
    rig.settle()
    assert rig.sink.of(MarketsView)[-1].state is LoadState.READY
    rig.worker.refresh_markets()
    rig.settle()
    assert markets_block_seconds(rig) == 600


def test_the_blocked_until_wait_is_not_capped_and_huge_waits_are_accepted(build: Any) -> None:
    rig = build()
    block(rig, 999_999_999.0)
    rig.start()
    assert rig.clock.waits[-1] == pytest.approx(999_999_999.0)
    assert seconds_blocked(rig) == pytest.approx(999_999_999.0)
    count = len(rig.quote.requests)
    rig.worker.retry_candles()
    rig.settle()
    assert len(rig.quote.requests) == count


# --- 코인 목록 ---------------------------------------------------------------------------------


def test_refresh_markets_sends_loading_then_the_list(build: Any) -> None:
    rig = build(done=False)
    rig.start()
    rig.sink.clear()
    rig.worker.refresh_markets()
    rig.settle()
    loading, ready = rig.sink.of(MarketsView)
    assert (loading.state, loading.markets, loading.error_name, loading.blocked_until) == (
        LoadState.LOADING, (), None, None,
    )  # fmt: skip
    assert (ready.state, ready.error_name, ready.blocked_until) == (LoadState.READY, None, None)
    assert [m.market for m in ready.markets] == [AAA, BBB, CCC]
    assert ready.as_of == loading.as_of == NOW
    assert rig.alerts() == []


def test_an_empty_list_is_still_ready(build: Any) -> None:
    rig = build(done=False, quote=FakeQuoteSource())
    rig.start()
    rig.worker.refresh_markets()
    rig.settle()
    last = rig.sink.of(MarketsView)[-1]
    assert (last.state, last.markets) == (LoadState.READY, ())


def test_a_failed_list_is_one_a31_until_a_success(build: Any) -> None:
    rig = build(done=False)
    rig.quote.script(
        QuoteError(ERROR_NETWORK),
        QuoteError(ERROR_NETWORK),
        OK,
        QuoteError(ERROR_NETWORK),
        kind="markets",
    )
    rig.start()
    for _ in range(2):
        rig.worker.refresh_markets()
        rig.settle()
    failed = rig.sink.of(MarketsView)[-1]
    assert (failed.state, failed.markets, failed.error_name, failed.blocked_until) == (
        LoadState.FAILED, (), ERROR_NETWORK, None,
    )  # fmt: skip
    (alert,) = rig.alerts()
    assert (alert.code, alert.category, alert.coin) == ("A-31", AlertCategory.ERROR, None)
    assert alert.text == "코인 목록을 받지 못했습니다(네트워크 끊김)."
    assert alert.detail is not None
    assert (alert.detail.stage, alert.detail.content) == (
        "코인 목록 받기",
        "코인 목록 요청이 실패했습니다.",
    )
    assert alert.detail.after == "처음 설정이나 설정 화면에서 다시 시도할 수 있습니다."
    for _ in range(2):  # 성공 뒤의 실패는 다시 만든다
        rig.worker.refresh_markets()
        rig.settle()
    assert [a.code for a in rig.alerts()] == ["A-31", "A-31"]


def test_a_block_on_the_list_is_an_a31_and_blocks_the_coins(build: Any) -> None:
    rig = build(coins=(AAA, BBB))
    rig.start()
    rig.sink.clear()
    rig.quote.script(QuoteError(ERROR_BLOCKED, 90.0), kind="markets")
    rig.worker.refresh_markets()
    rig.settle()
    view = rig.sink.of(MarketsView)[-1]
    assert (view.state, view.error_name, view.blocked_until) == (
        LoadState.FAILED, ERROR_BLOCKED, NOW + timedelta(seconds=90),
    )  # fmt: skip
    (alert,) = rig.alerts()
    assert alert.code == "A-31"
    assert alert.detail is not None
    assert alert.detail.content == "거래소가 공개 시세 요청을 잠시 막았습니다."
    blocked = [v for v in rig.sink.of(CoinView) if v.error_name == ERROR_BLOCKED]
    assert [v.market for v in blocked] == [AAA, BBB]  # 봉은 요청하지 않았으므로 A-1은 없다
    assert all(v.blocked_until == NOW + timedelta(seconds=90) for v in blocked)


def test_an_exception_from_the_list_request_is_an_a31_not_an_a30(build: Any) -> None:
    quote = RaisingQuote(RuntimeError("목록이 깨졌다"), markets=markets(AAA))
    rig = build(done=False, quote=quote)
    rig.start()
    rig.worker.refresh_markets()
    rig.settle()
    view = rig.sink.of(MarketsView)[-1]
    assert (view.state, view.error_name) == (LoadState.FAILED, "RuntimeError")
    (alert,) = rig.alerts()
    assert alert.code == "A-31"
    assert alert.detail is not None
    assert (alert.detail.error_name, alert.detail.content) == ("RuntimeError", "목록이 깨졌다")


# --- 설정 저장 ---------------------------------------------------------------------------------


def listed(rig: Rig) -> None:
    rig.worker.refresh_markets()
    rig.settle()


def test_saving_new_coins_needs_the_last_listed_markets(build: Any) -> None:
    rig = build(coins=(), done=False, days=None, bars=False)
    rig.start()
    rig.sink.clear()
    rig.worker.save_settings((AAA,), 30, first_setup=True)  # 한 번도 받지 못했다
    rig.settle()
    (result,) = rig.sink.items
    assert result == SaveResult("settings", False, SETTINGS_COIN_NOT_LISTED)
    assert rig.settings() == SettingsView((), None, False)
    assert rig.quote.candle_requests() == []
    assert rig.alerts() == []  # 거절은 저장 실패가 아니다
    listed(rig)
    rig.sink.clear()
    rig.worker.save_settings((AAA, "KRW-ZZZ"), 30, first_setup=True)  # 목록에 없다
    rig.settle()
    assert rig.sink.items == [SaveResult("settings", False, SETTINGS_COIN_NOT_LISTED)]
    rig.worker.save_settings((AAA, BBB, CCC, "KRW-DDD"), 30, first_setup=True)  # 넷
    rig.worker.save_settings((AAA, AAA), 30, first_setup=True)  # 겹침
    rig.settle()
    assert rig.settings() == SettingsView((), None, False)


def test_a_first_setup_save_stores_the_level_and_starts_the_fetch(build: Any) -> None:
    rig = build(coins=(), done=False, days=None, bars=False)
    for coin in (AAA, BBB):
        rig.quote.set_candles(coin, DAY, make_candles(coin, DAY, 40, last_start=TODAY_START))
        rig.quote.set_candles(coin, HOUR, make_candles(coin, HOUR, 50, last_start=THIS_HOUR))
    rig.start()
    listed(rig)
    rig.sink.clear()
    rig.worker.save_settings((BBB, AAA), 7, first_setup=True)
    rig.settle()
    kinds = [type(u).__name__ for u in rig.sink.items]
    assert kinds[:2] == ["SaveResult", "SettingsView"]
    assert rig.sink.items[0] == SaveResult("settings", True, SETTINGS_SAVED)
    assert rig.sink.items[1] == SettingsView((BBB, AAA), 7, True)
    assert rig.settings() == SettingsView((BBB, AAA), 7, True)
    assert [r.market for r in rig.quote.candle_requests()][:2] == [BBB, BBB]  # 설정 목록의 차례
    assert states(rig.sink.coin(BBB))[-2:] == [LoadState.LOADING, LoadState.READY]
    assert states(rig.sink.coin(AAA))[-2:] == [LoadState.LOADING, LoadState.READY]
    assert rig.worker.wait_idle(SAFETY)
    count = len(rig.quote.requests)
    rig.advance(3600)  # 이제 (라)가 돈다
    assert len(rig.quote.requests) > count


def test_a_save_from_the_settings_screen_keeps_the_stored_first_setup_level(build: Any) -> None:
    rig = build(coins=(AAA,), done=True, days=30)
    rig.start()
    listed(rig)
    rig.worker.save_settings((AAA,), 90, first_setup=False)  # 보관 기간만 바꾼다
    rig.worker.save_settings((BBB, AAA), 90, first_setup=False)  # 코인도 바꾼다
    rig.settle()
    assert rig.settings() == SettingsView((BBB, AAA), 90, True)
    results = rig.sink.of(SaveResult)
    assert results == [SaveResult("settings", True, SETTINGS_SAVED)] * 2
    assert rig.sink.of(SettingsView)[-1] == SettingsView((BBB, AAA), 90, True)


def test_changing_only_the_retention_saves_and_starts_the_fetch_without_the_market_list(
    build: Any,
) -> None:
    rig = build(coins=(AAA,), done=True, days=30)
    rig.start()  # 코인 목록은 한 번도 받지 않았다
    before = len(rig.quote.candle_requests())
    rig.sink.clear()
    rig.worker.save_settings((AAA,), 7, first_setup=False)
    rig.settle()
    assert [type(u).__name__ for u in rig.sink.items][:2] == ["SaveResult", "SettingsView"]
    assert rig.sink.items[0] == SaveResult("settings", True, SETTINGS_SAVED)
    assert len(rig.quote.candle_requests()) > before  # (나)의 받기
    assert rig.settings().retention_days == 7


def test_a_failed_write_is_an_a29_and_sends_nothing_else(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build(coins=(AAA,), done=True, days=30)
    rig.start()
    real = data_thread.write_settings
    failing = {"on": True}

    def maybe(conn: Any, view: SettingsView) -> None:
        if failing["on"]:
            raise StorageError("설정 저장에 실패했습니다: OperationalError")
        real(conn, view)

    monkeypatch.setattr(data_thread, "write_settings", maybe)
    count = len(rig.quote.requests)
    rig.sink.clear()
    rig.worker.save_settings((AAA,), 7, first_setup=False)
    rig.settle()
    assert rig.sink.items == [SaveResult("settings", False, SETTINGS_SAVE_FAILED)]
    assert len(rig.quote.requests) == count
    assert rig.settings().retention_days == 30
    (alert,) = rig.alerts()
    assert (alert.code, alert.category, alert.coin) == ("A-29", AlertCategory.ERROR, None)
    assert alert.text == "저장하지 못했습니다(DB 오류)."
    assert alert.detail is not None
    assert alert.detail.stage == "설정 저장"
    assert alert.detail.after == "저장하지 못한 값은 화면에 남겨 두었습니다."
    rig.worker.save_settings((AAA,), 7, first_setup=False)  # 성공하기 전에는 다시 만들지 않는다
    rig.settle()
    assert len(rig.alerts()) == 1
    failing["on"] = False
    rig.worker.save_settings((AAA,), 7, first_setup=False)
    rig.settle()
    assert rig.settings().retention_days == 7  # 자료 스레드가 가진 설정은 실패 때 바뀌지 않았다


# --- 설정을 읽지 못했을 때 ------------------------------------------------------------------------


def break_settings(rig: Rig) -> None:
    conn = open_database(rig.db)
    try:
        conn.execute("UPDATE settings SET value = '깨짐' WHERE key = 'coins'")
        conn.commit()
    finally:
        conn.close()


def repair_settings(rig: Rig) -> None:
    conn = open_database(rig.db)
    try:
        conn.execute("UPDATE settings SET value = '[\"KRW-AAA\"]' WHERE key = 'coins'")
        conn.commit()
    finally:
        conn.close()


def test_unreadable_settings_send_failed_settings_and_make_no_requests(build: Any) -> None:
    rig = build()
    break_settings(rig)
    rig.start()
    assert rig.sink.items == [SettingsView(state=LoadState.FAILED)]
    assert rig.quote.requests == []
    (alert,) = rig.alerts()
    assert (alert.code, alert.category, alert.coin) == ("A-30", AlertCategory.OTHER, None)
    assert alert.detail is not None
    assert (alert.detail.stage, alert.detail.error_name) == ("켜기", "DB 오류")
    assert rig.clock.waits[-1] == data_thread.SETTINGS_RETRY_SECONDS
    # 읽지 못한 동안의 세 동작은 아무것도 하지 않는다(큐에 남은 것도 같다)
    rig.worker.retry_candles()
    rig.worker.refresh_markets()
    rig.worker.save_settings((AAA,), 7, first_setup=True)
    rig.settle()
    assert rig.sink.items == [SettingsView(state=LoadState.FAILED)]
    assert rig.quote.requests == []
    assert len(rig.alerts()) == 1


def test_unreadable_settings_are_read_again_every_sixty_minutes(build: Any) -> None:
    rig = build()
    break_settings(rig)
    rig.start()
    rig.advance(3600)  # 아직 깨져 있다: A-30은 60분이 지났으니 다시 만든다
    assert rig.quote.requests == []
    assert [a.code for a in rig.alerts()] == ["A-30", "A-30"]
    assert rig.sink.of(SettingsView) == [SettingsView(state=LoadState.FAILED)]
    repair_settings(rig)
    rig.advance(3600)
    assert rig.sink.of(SettingsView)[-1] == SettingsView((AAA,), 30, True)
    assert states(rig.sink.coin(AAA))[-3:] == [
        LoadState.NO_DATA,
        LoadState.LOADING,
        LoadState.READY,
    ]
    assert rig.quote.candle_requests(AAA)  # 처음 설정을 마쳤으므로 받기를 시작한다
    # 그 뒤는 보통과 같다: (라)로 60분마다 받는다
    count = len(rig.quote.requests)
    rig.advance(3600)
    assert len(rig.quote.requests) > count


def test_an_unreadable_settings_retry_does_not_repeat_the_a30_inside_sixty_minutes(
    build: Any,
) -> None:
    rig = build()
    break_settings(rig)
    rig.start()
    rig.advance(3599)
    assert len(rig.alerts()) == 1


# --- 창을 띄우기 전의 오류를 옮긴다 ------------------------------------------------------------


def test_startup_lines_become_local_only_alerts_with_their_own_time(build: Any) -> None:
    from coin_report_trader.app import startup_errors

    rig = build(coins=(AAA,), done=False)
    line = startup_errors.startup_line(
        StorageError("DB 열기에 실패했습니다: OperationalError"), NOW - timedelta(days=2)
    )
    assert startup_errors.append_line(rig.logs, line)
    rig.start()
    (alert,) = rig.alerts()
    assert (alert.code, alert.category, alert.coin) == ("A-29", AlertCategory.ERROR, None)
    assert alert.created_at == NOW - timedelta(days=2)
    assert alert.detail is not None
    assert (alert.detail.stage, alert.detail.error_name) == ("켜기", "DB 오류")
    conn = open_database(rig.db)
    try:
        states_ = conn.execute("SELECT discord_state, seen FROM alerts").fetchall()
    finally:
        conn.close()
    assert states_ == [("local_only", 0)]
    assert (rig.logs / startup_errors.FILE_NAME).read_text(encoding="utf-8") == ""


# --- 한 받기의 알림의 차례(3.8) --------------------------------------------------------------


def test_a_block_after_another_failure_leaves_only_the_block_alert(build: Any) -> None:
    rig = build()
    fail(rig, QuoteError(ERROR_TIMEOUT), unit=DAY)
    fail(rig, QuoteError(ERROR_BLOCKED, 90.0), unit=HOUR)
    rig.start()
    view = rig.sink.coin(AAA)[-1]
    assert (view.state, view.error_name) == (LoadState.FAILED, ERROR_BLOCKED)
    (alert,) = rig.alerts()
    assert alert.text == "봉 통계를 만들지 못했습니다(일시 차단)."
    assert alert.detail is not None
    assert alert.detail.content == "거래소가 공개 시세 요청을 잠시 막았습니다."


def test_a_block_after_a_save_failure_leaves_only_the_block_alert(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()

    def refuse(conn: Any, candles: Any) -> int:
        raise StorageError("봉 저장에 실패했습니다: OperationalError")

    monkeypatch.setattr(data_thread, "write_candles", refuse)
    fail(rig, QuoteError(ERROR_BLOCKED, 90.0), unit=HOUR)
    rig.start()
    (alert,) = rig.alerts()
    assert alert.code == "A-1"
    assert rig.sink.coin(AAA)[-1].error_name == ERROR_BLOCKED


def test_a_db_failure_comes_before_a_request_failure(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    real = data_thread.last_candle_start

    def unreadable_for_days(conn: Any, market: str, unit: CandleUnit) -> Any:
        if unit is DAY:
            raise StorageError("마지막 봉 읽기에 실패했습니다: OperationalError")
        return real(conn, market, unit)

    monkeypatch.setattr(data_thread, "last_candle_start", unreadable_for_days)
    fail(rig, QuoteError(ERROR_NETWORK), unit=HOUR)
    rig.start()
    view = rig.sink.coin(AAA)[-1]
    assert view.error_name == "DB 오류"
    (alert,) = rig.alerts()
    assert (alert.code, alert.detail.stage if alert.detail else None) == ("A-30", "봉 받기")


def test_two_units_with_the_same_error_name_make_one_alert(build: Any) -> None:
    rig = build()
    fail(rig, QuoteError(ERROR_NETWORK), unit=DAY)
    fail(rig, QuoteError(ERROR_NETWORK), unit=HOUR)
    rig.start()
    (alert,) = rig.alerts()
    assert alert.detail is not None
    assert (alert.detail.error_name, alert.detail.content) == (
        ERROR_NETWORK, "실패한 봉: 일봉, 60분봉",
    )  # fmt: skip


def test_the_a1_of_a_round_are_made_together_after_it_ends_with_one_time(build: Any) -> None:
    rig = build(coins=(AAA, BBB))
    fail(rig, QuoteError(ERROR_NETWORK), unit=DAY, market=AAA)
    fail(rig, QuoteError(ERROR_TIMEOUT), unit=DAY, market=BBB)
    rig.start()
    first, second = rig.alerts()
    assert (first.coin, second.coin) == (AAA, BBB)  # 코인의 차례
    assert first.created_at == second.created_at == NOW
    assert first.id < second.id
    assert first.detail is not None
    assert first.detail.after == "다음 받는 때(13:00)에 다시 받습니다."


def test_the_stored_alert_columns(build: Any) -> None:
    rig = build()
    fail(rig, QuoteError(ERROR_NETWORK), unit=DAY)
    rig.start()
    conn = open_database(rig.db)
    try:
        row = conn.execute(
            "SELECT created_utc, mode, category, code, coin, text, stage, error_name, content,"
            ' "after", seen, discord_state FROM alerts'
        ).fetchall()
    finally:
        conn.close()
    assert row == [
        (
            "2026-01-05T03:00:00Z", "paper", "error", "A-1", AAA,
            "봉 통계를 만들지 못했습니다(네트워크 끊김).", "봉 받기", "네트워크 끊김",
            "실패한 봉: 일봉", "다음 받는 때(13:00)에 다시 받습니다.", 0, "no_address",
        )
    ]  # fmt: skip


def test_the_alert_texts_carry_no_secret_or_path(build: Any) -> None:
    url = "https://" + "discord.com/api/webhooks/" + "1" * 18 + "/" + "Tok_" * 16
    quote = RaisingQuote(RuntimeError(f"{url} /{'home'}/u1/x"), DAY, markets=markets(AAA))
    rig = build(quote=quote)
    rig.start()
    (alert,) = rig.alerts()
    assert alert.detail is not None
    assert alert.detail.content == "(가려서 보임) ~/x"
