"""자료 스레드(M1 명세 3.8, 3.9): 켜는 차례, 받기, 알림, 일시 차단, 설정, 코인 목록.

Qt 없이 `DataWorker`를 직접 돌린다. 가짜 시세와 가짜 시계를 쓰고, 화면에 가는 값은 시험이 모은다.
시각은 가짜 시계의 `advance`로만 옮긴다(실제 시간은 멈추지 않게 하는 안전 시간으로만 쓴다).
"""

import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fakes.clock import DEFAULT_START, FakeClock
from fakes.quote import OK, PRICE, FakeQuoteSource, make_candles
from fakes.stage3 import stage3_trade, write_stage3

from coin_report_trader.app import data_thread
from coin_report_trader.app.data_thread import DATA_THREAD_NAME, DataWorker
from coin_report_trader.core import (
    AlertCategory,
    AlertsView,
    Candle,
    CandleUnit,
    CoinTradeView,
    CoinView,
    LoadState,
    Market,
    MarketsView,
    QuoteError,
    RoundNowView,
    SaveResult,
    SettingsView,
    StorageError,
    TodayView,
    UiUpdate,
    empty_bar_stats,
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
from coin_report_trader.storage.settings import (
    read_fetched_at,
    read_settings,
    write_fetched_at,
    write_settings,
)

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
    trade: list[UiUpdate] = field(default_factory=list)
    """3단계의 매매 값(`CoinTradeView`, `TodayView`, `RoundNowView`. M3 명세 3.17.2). 1·2단계의 값을
    보는 시험의 `items`에 섞이지 않게 따로 모은다."""
    threads: set[str] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __call__(self, update: UiUpdate) -> None:
        with self.lock:
            if isinstance(update, CoinTradeView | TodayView | RoundNowView):
                self.trade.append(update)
            else:
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
            self.trade.clear()


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


def fetch_requests(rig: "Rig") -> list[Any]:
    """1·2단계의 요청(봉, 코인 목록). 3단계의 켤 때와 1분마다의 손절 점검이 내는 현재가 요청은
    빼고 센다(M3 명세 3.9. 이 파일의 시험에는 AI 회차가 없어 현재가 요청은 모두 그것이다)."""
    return [r for r in rig.quote.requests if r.kind != PRICE]


def make_rig(
    tmp_path: Path,
    *,
    coins: tuple[str, ...] = (AAA,),
    done: bool = True,
    days: int | None = 30,
    unit: CandleUnit | None = HOUR,
    count: int | None = 100,
    quote: FakeQuoteSource | None = None,
    bars: bool = True,
    clock: FakeClock | None = None,
) -> Rig:
    db = tmp_path / "app.db"
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    conn = open_database(db)
    try:
        migrate(conn)
        view = SettingsView(coins, days, done, candle_unit=unit, candle_count=count)
        if done and unit is not None and count is not None:
            write_stage3(conn, view)  # 3단계로 마친 DB(M3 명세 3.11.3. 23번)
        else:
            write_settings(conn, view)
    finally:
        conn.close()
    clock = clock or FakeClock()
    source = quote or FakeQuoteSource(markets=markets(AAA, BBB, CCC), clock=clock)
    # 3단계의 켤 때와 1분마다의 손절 점검이 고른 코인의 현재가를 읽는다(M3 명세 3.9. 만든 값)
    source.set_prices(dict.fromkeys(coins, Decimal(1000)))
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


def chosen(coins: tuple[str, ...], days: int | None, done: bool) -> SettingsView:
    """고른 봉 단위(60분봉)와 봉 개수(100개)가 저장된 설정(`make_rig`의 기본). 마친 설정에는
    3단계 값(`stage3_trade`)이 있다(M3 명세 3.11.3)."""
    trade = stage3_trade(coins) if done else None
    return SettingsView(coins, days, done, candle_unit=HOUR, candle_count=100, trade=trade)


def next_in(rig: Rig) -> float:
    """다음 (라)의 때까지 남은 초(오래된 알림 지우기의 때는 따로 센다)."""
    due = rig.worker._next_round_mono
    assert due is not None
    return due - rig.clock.monotonic()


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
        write_candles(conn, make_candles(AAA, HOUR, 120, last_start=THIS_HOUR - timedelta(hours=1)))
    finally:
        conn.close()
    rig.start()
    view = rig.sink.coin(AAA)[0]
    assert view.state is LoadState.READY
    assert view.stats is not None
    assert (view.stats.unit, view.stats.requested_count, view.stats.bar_count) == (HOUR, 100, 100)
    assert view.stats.close_last is not None
    assert view.last_incomplete is False
    assert fetch_requests(rig) == []


def test_the_initial_view_is_no_data_with_the_count_when_fewer_candles_are_stored(
    build: Any,
) -> None:
    rig = build(coins=(AAA,), done=False)
    conn = open_database(rig.db)
    try:
        write_candles(conn, make_candles(AAA, HOUR, 40, last_start=THIS_HOUR - timedelta(hours=1)))
    finally:
        conn.close()
    rig.start()
    view = rig.sink.coin(AAA)[0]
    assert view.state is LoadState.NO_DATA
    assert view.stats is not None
    assert (view.stats.requested_count, view.stats.bar_count) == (100, 40)
    assert view.stats.close_last is None


def test_the_initial_view_with_nothing_stored_has_empty_stats(build: Any) -> None:
    rig = build(coins=(AAA,))
    rig.start()
    view = rig.sink.coin(AAA)[0]
    assert view.state is LoadState.NO_DATA
    assert view.stats is not None
    assert (view.stats.unit, view.stats.requested_count, view.stats.bar_count) == (HOUR, 100, 0)
    assert view.fetched_at is None


def test_no_coin_view_is_sent_while_no_unit_is_stored(build: Any) -> None:
    rig = build(coins=(AAA, BBB), unit=None, count=None, done=False)
    rig.start()
    assert rig.sink.coin(AAA) == []
    assert rig.sink.coin(BBB) == []
    rig.worker.retry_candles()
    rig.settle()
    assert rig.sink.of(CoinView) == []
    assert rig.quote.candle_requests() == []
    assert rig.worker._next_round_mono is None
    rig.quote.script(QuoteError(ERROR_BLOCKED), kind="markets")
    rig.worker.refresh_markets()  # 차단 중의 화면도 `CoinView`를 보내지 않는다
    rig.settle()
    assert rig.sink.of(CoinView) == []


def test_actions_that_come_before_the_start_are_not_dropped(build: Any) -> None:
    rig = build(coins=(AAA,), done=False)
    rig.worker.refresh_markets()  # 시작 전에 큐에 들어왔다
    rig.start()
    assert [type(u).__name__ for u in rig.sink.items][:6] == [
        "SettingsView", "CoinView", "AlertsView", "AiStatusView", "ReportListView", "MarketsView",
    ]  # fmt: skip
    assert len(rig.quote.market_requests()) == 1


def test_alert_actions_for_unknown_ids_change_nothing_and_only_resend_the_view(
    build: Any,
) -> None:
    rig = build()
    rig.start()
    rig.sink.clear()
    rig.worker.mark_seen((1, 2))
    rig.worker.delete_alert(1)
    rig.worker.clear_alerts(AlertCategory.ERROR)
    rig.settle()
    assert [type(u).__name__ for u in rig.sink.items] == [
        "AlertsView", "AlertsView", "SaveResult", "AlertsView",
    ]  # fmt: skip
    assert rig.sink.items[2] == SaveResult("alert_delete", True)  # 분류 지우기는 늘 성공이다
    assert all(not view.alerts for view in rig.sink.of(AlertsView))


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
        "AlertsView",
        "AiStatusView",
        "ReportListView",
        "MarketsView",
        "MarketsView",
    ]


# --- 받기: 범위, 값, 차례 ------------------------------------------------------------------------


def test_first_fetch_saves_the_candles_and_sends_loading_then_the_result(build: Any) -> None:
    rig = build()
    rig.quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 300, last_start=THIS_HOUR))
    rig.start()
    assert [r.count for r in rig.quote.candle_requests(AAA, HOUR)] == [200]  # 100개는 한 요청이다
    assert rig.quote.candle_requests(AAA, DAY) == []  # 고른 단위 하나만 받는다
    assert len(rig.stored(AAA, HOUR)) == 200
    assert rig.stored(AAA, DAY) == ()
    views = rig.sink.coin(AAA)
    assert states(views) == [LoadState.NO_DATA, LoadState.LOADING, LoadState.READY]
    last = views[-1]
    assert last.fetched_at == NOW
    assert last.as_of == NOW
    assert last.error_name is None
    assert last.last_incomplete is True  # 지금 걸친 60분봉이 저장되어 있다
    assert last.stats is not None
    assert (last.stats.unit, last.stats.requested_count, last.stats.bar_count) == (HOUR, 100, 100)
    assert last.stats.last_start_utc == THIS_HOUR - timedelta(hours=1)  # 완성된 마지막 봉
    assert rig.fetched_at(AAA) == NOW
    assert rig.alerts() == []


def test_a_first_fetch_goes_back_in_200s_until_enough_complete_candles(build: Any) -> None:
    rig = build(count=500)
    rig.quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 450, last_start=THIS_HOUR))
    rig.start()
    assert [r.count for r in rig.quote.candle_requests(AAA, HOUR)] == [200, 200, 200]
    assert len(rig.stored(AAA, HOUR)) == 450
    last = rig.sink.coin(AAA)[-1]
    assert last.state is LoadState.NO_DATA  # 거래소에 450개뿐이라 500개가 안 된다
    assert last.stats is not None
    assert (last.stats.requested_count, last.stats.bar_count) == (500, 449)


def test_the_next_fetch_gets_only_the_new_candles_and_overwrites_the_last(build: Any) -> None:
    rig = build()
    rig.start()
    old_last = rig.stored(AAA, HOUR)[-1]
    assert old_last.start == THIS_HOUR
    # 새 60분봉 둘: 마지막으로 저장된 봉(지금 시간)은 종가가 바뀐 채 다시 오고, 뒤로 두 시간이
    # 더해진다.
    new = make_candles(AAA, HOUR, 3, last_start=THIS_HOUR + timedelta(hours=2))
    assert new[0].start == THIS_HOUR
    assert new[0].close != old_last.close
    rig.quote.put_candles(*new)
    before = len(rig.quote.candle_requests(AAA, HOUR))
    rig.advance(2 * 3600)  # 저절로 받는 (라)의 때가 한 번 지난다
    assert len(rig.quote.candle_requests(AAA, HOUR)) - before == 1  # 요청은 한 번
    stored = rig.stored(AAA, HOUR)
    assert len(stored) == 202  # 200개 + 새 봉 둘
    assert [c.start for c in stored[-3:]] == [c.start for c in new]
    assert stored[-3].close == new[0].close  # 마지막으로 저장된 봉은 덮어썼다
    assert stored[-3].close != old_last.close
    assert stored[-1].close == new[2].close


def test_the_next_fetch_is_ten_seconds_after_the_end_of_the_last_stored_bar(build: Any) -> None:
    rig = build()
    rig.start()
    count = len(fetch_requests(rig))
    # 저장된 마지막 60분봉은 03:00에 시작해 04:00에 끝난다. 거기에 10초를 더한다.
    assert next_in(rig) == pytest.approx(3610.0)
    rig.advance(3609)
    assert len(fetch_requests(rig)) == count
    rig.advance(1)
    assert len(fetch_requests(rig)) > count
    assert states(rig.sink.coin(AAA))[-2:] == [LoadState.LOADING, LoadState.READY]
    # 새 봉이 오지 않았으므로 마지막 봉은 그대로이고, 다음은 그다음 봉의 끝에서 10초 뒤다(05:00:10).
    assert next_in(rig) == pytest.approx(3600.0)
    count = len(fetch_requests(rig))
    rig.advance(3599)
    assert len(fetch_requests(rig)) == count
    rig.advance(1)
    assert len(fetch_requests(rig)) > count


def test_with_nothing_stored_the_next_fetch_is_the_next_boundary_since_the_epoch(
    build: Any,
) -> None:
    rig = build(bars=False)  # 거래소에도 봉이 없다
    rig.start()
    assert rig.stored(AAA, HOUR) == ()
    assert next_in(rig) == pytest.approx(3610.0)  # 03:00 다음의 경계 04:00 + 10초
    day = build(bars=False, unit=DAY)
    day.start()
    assert next_in(day) == pytest.approx(21 * 3600 + 10.0)  # 다음 UTC 자정 + 10초


def test_coins_with_different_last_bars_share_one_next_fetch_time(
    build: Any, tmp_path: Path
) -> None:
    rig = build(coins=(AAA, BBB), bars=False)
    rig.quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 5, last_start=THIS_HOUR))
    rig.quote.set_candles(
        BBB, HOUR, make_candles(BBB, HOUR, 5, last_start=THIS_HOUR - timedelta(hours=3))
    )
    rig.start()
    assert next_in(rig) == pytest.approx(3610.0)  # 코인이 달라도 봉의 경계는 같아 때가 하나다


def test_the_next_fetch_waits_for_a_late_bar_in_whole_bar_lengths(build: Any) -> None:
    rig = build(bars=False)
    rig.quote.set_candles(
        AAA, HOUR, make_candles(AAA, HOUR, 5, last_start=THIS_HOUR - timedelta(hours=5))
    )
    rig.start()  # 마지막 봉이 22:00(전날)에 시작했다: 길이 × m이 지금보다 뒤인 가장 작은 m
    assert next_in(rig) == pytest.approx(3610.0)


def test_a_save_or_a_retry_fetch_does_not_move_the_next_fetch_time(build: Any) -> None:
    rig = build()
    rig.start()
    assert next_in(rig) == pytest.approx(3610.0)
    due = rig.worker._next_due
    rig.advance(100)
    rig.worker.retry_candles()
    rig.settle()
    assert rig.worker._next_due == due
    assert next_in(rig) == pytest.approx(3510.0)


def test_no_next_time_waits_an_hour_and_does_nothing_when_it_wakes(build: Any) -> None:
    rig = build(coins=(AAA,), done=False)  # 처음 설정 전: (라)가 없다
    rig.start()
    assert rig.clock.waits[-1] == data_thread.IDLE_SECONDS
    sent = len(rig.sink.items)
    rig.advance(data_thread.IDLE_SECONDS)
    rig.advance(data_thread.IDLE_SECONDS)
    assert fetch_requests(rig) == []
    assert len(rig.sink.items) == sent
    assert rig.clock.waits[-1] == data_thread.IDLE_SECONDS


def test_retry_does_nothing_without_coins_even_while_blocked(build: Any) -> None:
    rig = build(coins=(), done=False, days=None, bars=False)
    rig.start()
    rig.sink.clear()
    rig.worker.retry_candles()
    rig.settle()
    assert rig.sink.items == []
    assert fetch_requests(rig) == []
    assert rig.worker._next_round_mono is None  # (라)의 60분을 새로 세지 않는다
    rig.quote.script(QuoteError(ERROR_BLOCKED), kind="markets")  # 코인 목록이 차단을 정한다
    rig.worker.refresh_markets()
    rig.settle()
    until, mono, backoff = rig.worker._blocked_until, rig.worker._blocked_mono, rig.worker._backoff
    assert until is not None
    assert backoff == 1
    rig.sink.clear()
    rig.worker.retry_candles()  # 차단 중이어도 같다
    rig.settle()
    assert rig.sink.items == []
    assert len(fetch_requests(rig)) == 1  # 코인 목록 요청 하나뿐이다
    assert rig.worker._next_round_mono is None
    assert (rig.worker._blocked_until, rig.worker._blocked_mono, rig.worker._backoff) == (
        until, mono, backoff,
    )  # fmt: skip
    assert rig.alerts()[-1].code == "A-31"
    assert len([a for a in rig.alerts() if a.code == "A-1"]) == 0


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
    shown = [
        (type(u).__name__, u.state if isinstance(u, CoinView | MarketsView) else None)
        for u in rig.sink.items
    ]
    assert shown == [
        ("SettingsView", None),
        ("CoinView", LoadState.NO_DATA),
        ("AlertsView", None),
        ("AiStatusView", None),
        ("ReportListView", None),
        ("CoinView", LoadState.LOADING),  # 켤 때의 (가) 받기
        ("CoinView", LoadState.READY),
        ("CoinView", LoadState.LOADING),  # 받는 중에 온 "다시 시도"는 그 뒤에 처리했다
        ("CoinView", LoadState.READY),
        ("MarketsView", LoadState.LOADING),  # 그다음 코인 목록
        ("MarketsView", LoadState.READY),
    ]


# --- 미완성 봉과 "봉이 모자람" -----------------------------------------------------------


def one_complete_hour(rig: Rig, coin: str = AAA) -> None:
    rig.quote.set_candles(
        coin, HOUR, make_candles(coin, HOUR, 1, last_start=THIS_HOUR - timedelta(hours=1))
    )


def test_not_enough_candles_is_no_data_with_one_alert_and_it_is_not_repeated(build: Any) -> None:
    rig = build()
    one_complete_hour(rig)
    rig.start()
    last = rig.sink.coin(AAA)[-1]
    assert last.state is LoadState.NO_DATA
    assert last.fetched_at == NOW  # "봉이 모자람"이어도 받기는 성공이다
    assert last.stats is not None
    assert (last.stats.requested_count, last.stats.bar_count) == (100, 1)
    assert last.stats.close_last is None
    assert last.stats.change is None
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
    assert alert.detail.content == (
        "완성된 봉이 고른 개수(100개)보다 적어 봉 통계를 셈하지 못했습니다(완성된 봉 1개)."
    )
    assert alert.detail.coin == AAA
    assert alert.seen is False
    assert alert.discord_failed is False
    assert alert.created_at == NOW
    rig.advance(3610)
    rig.advance(3600)
    rig.worker.retry_candles()
    rig.settle()
    assert len(rig.alerts()) == 1  # B33: 세 번 더 받아도 하나다


def test_the_alert_after_text_names_the_next_fetch_time(build: Any) -> None:
    rig = build()
    one_complete_hour(rig)
    rig.start()
    detail = rig.alerts()[0].detail
    assert detail is not None
    assert detail.after == "다음 받는 때(13:00)에 다시 받습니다."  # 한국 시간 12:00 + 60분


def test_the_next_fetch_time_text_carries_the_date_when_it_is_tomorrow(build: Any) -> None:
    rig = build()
    one_complete_hour(rig)
    rig.clock.set_now(datetime(2026, 1, 5, 14, 30, tzinfo=UTC))  # 한국 23:30
    rig.start()
    detail = rig.alerts()[0].detail
    assert detail is not None
    assert detail.after == "다음 받는 때(2026-01-06 00:00)에 다시 받습니다."


def test_the_unfinished_bar_is_flagged_and_left_out(build: Any) -> None:
    rig = build()
    rig.start()
    view = rig.sink.coin(AAA)[-1]
    assert view.state is LoadState.READY
    assert view.last_incomplete is True
    assert rig.stored(AAA, HOUR)[-1].start == THIS_HOUR
    assert view.stats is not None
    assert view.stats.last_start_utc == THIS_HOUR - timedelta(hours=1)


# --- 요청의 실패와 한 받기의 알림 ---------------------------------------------------------------


def fail(
    rig: Rig, *errors: QuoteError | object, unit: CandleUnit | None = None, market: str | None = AAA
) -> None:
    rig.quote.script(*errors, kind="candles", market=market, unit=unit)  # type: ignore[arg-type]


def test_a_failed_request_saves_nothing_and_makes_one_alert_that_names_the_unit(
    build: Any,
) -> None:
    quote = FakeQuoteSource(markets=markets(AAA))
    rig = build(quote=quote)
    fail(rig, QuoteError(ERROR_TIMEOUT), unit=HOUR)
    rig.start()
    view = rig.sink.coin(AAA)[-1]
    assert (view.state, view.error_name, view.fetched_at) == (LoadState.FAILED, ERROR_TIMEOUT, None)
    assert rig.stored(AAA, HOUR) == ()
    assert rig.quote.candle_requests(AAA, DAY) == []  # 고른 단위가 아닌 것은 받지 않는다
    alert = rig.alerts()[0]
    assert alert.text == "봉 통계를 만들지 못했습니다(시간 초과)."
    assert alert.detail is not None
    assert (alert.detail.stage, alert.detail.content) == ("봉 받기", "실패한 봉: 60분봉")
    assert len(rig.alerts()) == 1
    assert rig.fetched_at(AAA) is None
    assert view.stats is not None
    assert view.stats.bar_count == 0


def test_the_same_error_is_alerted_again_only_after_a_success(build: Any) -> None:
    rig = build()
    rig.quote.script(
        QuoteError(ERROR_NETWORK), QuoteError(ERROR_NETWORK), OK, QuoteError(ERROR_NETWORK),
        kind="candles", market=AAA, unit=HOUR,
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
        QuoteError(ERROR_NETWORK), QuoteError(ERROR_TIMEOUT), kind="candles", market=AAA, unit=HOUR
    )
    rig.start()
    rig.worker.retry_candles()
    rig.settle()
    assert [a.text for a in rig.alerts()] == [
        "봉 통계를 만들지 못했습니다(네트워크 끊김).",
        "봉 통계를 만들지 못했습니다(시간 초과).",
    ]


def test_a_failed_first_fetch_keeps_nothing_and_the_next_fetch_starts_over(build: Any) -> None:
    rig = build(count=500)
    rig.quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 450, last_start=THIS_HOUR))
    rig.quote.script(OK, QuoteError(ERROR_NETWORK), kind="candles", market=AAA, unit=HOUR)
    rig.start()  # 처음 받기의 두 번째 요청이 실패한다
    assert rig.stored(AAA, HOUR) == ()  # 끝까지 성공했을 때만 저장한다
    view = rig.sink.coin(AAA)[-1]
    assert (view.state, view.error_name, view.fetched_at) == (LoadState.FAILED, ERROR_NETWORK, None)
    assert len(rig.alerts()) == 1
    before = len(rig.quote.candle_requests(AAA, HOUR))
    rig.worker.retry_candles()
    rig.settle()
    assert len(rig.quote.candle_requests(AAA, HOUR)) - before == 3
    assert len(rig.stored(AAA, HOUR)) == 450


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
    quote = RaisingQuote(ValueError("나쁜 값 /srv/data"), HOUR, markets=markets(AAA))
    rig = build(quote=quote)
    rig.start()
    view = rig.sink.coin(AAA)[-1]
    assert (view.state, view.error_name) == (LoadState.FAILED, "ValueError")
    (alert,) = rig.alerts()
    assert (alert.code, alert.category) == ("A-1", AlertCategory.ERROR)
    assert alert.text == "봉 통계를 만들지 못했습니다(ValueError)."
    assert alert.detail is not None
    assert alert.detail.stage == "봉 받기"
    assert "나쁜 값" in alert.detail.content
    assert rig.stored(AAA, HOUR) == ()


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


def test_failing_to_read_the_stored_candles_before_a_fetch_is_an_a30_of_the_candle_stage(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()

    def unreadable(conn: Any, market: str, unit: CandleUnit, now: datetime) -> int:
        raise StorageError("완성된 봉 세기에 실패했습니다: OperationalError")

    monkeypatch.setattr(data_thread, "count_complete_candles", unreadable)
    rig.start()
    assert rig.quote.candle_requests() == []  # 저장된 봉을 읽지 못하면 요청하지 않는다
    view = rig.sink.coin(AAA)[-1]
    assert (view.state, view.error_name) == (LoadState.FAILED, "DB 오류")
    (alert,) = rig.alerts()
    assert (alert.code, alert.category, alert.coin) == ("A-30", AlertCategory.OTHER, AAA)
    assert alert.text == "예상하지 못한 오류가 났습니다(DB 오류)."
    assert alert.detail is not None
    assert alert.detail.stage == "봉 받기"
    assert alert.detail.after == "그 일을 그만두고 다음 일을 계속합니다."
    rig.advance(3610)  # 같은 단계에 같은 예외 종류는 60분 안에 다시 만들지 않는다
    assert len(rig.alerts()) == 2  # 60분이 지났으므로 다시 만든다
    rig.worker.retry_candles()
    rig.settle()
    assert len(rig.alerts()) == 2  # 방금 만들었으므로 만들지 않는다


def test_a_failed_fetched_at_write_is_an_a29_keeps_the_old_time_and_the_steps(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    rig.start()  # 성공: fetched_at = NOW
    assert rig.sink.coin(AAA)[-1].fetched_at == NOW
    rig.quote.script(QuoteError(ERROR_BLOCKED), kind="candles", market=AAA, unit=HOUR)
    rig.worker.retry_candles()  # 시간을 주지 않은 차단: 늘리는 차례가 한 칸 간다
    rig.settle()
    assert rig.worker._backoff == 1
    assert len(rig.alerts()) == 1

    def refuse(conn: Any, market: str, moment: datetime) -> None:
        raise StorageError("받은 때 저장에 실패했습니다: OperationalError")

    monkeypatch.setattr(data_thread, "write_fetched_at", refuse)
    rig.advance(300)  # 차단이 끝나 저절로 받는다: 요청과 봉 저장은 되고 fetched_at 쓰기만 실패한다
    view = rig.sink.coin(AAA)[-1]
    assert (view.state, view.error_name) == (LoadState.FAILED, "DB 오류")
    assert view.fetched_at == NOW  # 앞의 값 그대로다(받기 전체가 성공하지 않았다)
    assert rig.fetched_at(AAA) == NOW
    assert view.stats is not None
    assert view.stats.bar_count == 100
    assert rig.worker._backoff == 1  # 늘리는 차례를 처음으로 돌리지 않는다
    alerts = rig.alerts()
    assert [a.code for a in alerts] == ["A-1", "A-29"]
    assert alerts[1].detail is not None
    assert alerts[1].detail.stage == "봉 저장"
    assert alerts[1].coin == AAA


def test_a_failure_in_the_statistics_is_an_a30_and_the_fetch_still_counts(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    real = data_thread.compute_bar_stats
    calls = {"n": 0}

    def sometimes_broken(candles: Any, now: datetime, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 3:  # 켤 때(1), 받기 시작(2)의 셈은 지나가고 받기 뒤의 셈(3)에서 한 번 난다
            raise RuntimeError("셈이 깨졌다")
        return real(candles, now, **kwargs)

    monkeypatch.setattr(data_thread, "compute_bar_stats", sometimes_broken)
    rig.start()
    views = rig.sink.coin(AAA)
    view = views[-1]
    assert (view.state, view.error_name) == (LoadState.FAILED, "RuntimeError")
    # 값은 그 코인에 마지막으로 보낸 `CoinView`(받기 시작 때의 `LOADING`)의 값이다.
    # 새로 저장된 봉으로 셈하지 않는다(그러면 봉 수가 100이 된다).
    assert views[-2].state is LoadState.LOADING
    assert (view.stats, view.last_incomplete) == (views[-2].stats, views[-2].last_incomplete)
    assert view.stats is not None
    assert view.stats.bar_count == 0
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
    fail(rig, QuoteError(ERROR_NETWORK), unit=HOUR)

    def refuse(*args: Any, **kwargs: Any) -> int:
        raise StorageError("알림 저장에 실패했습니다: OperationalError")

    monkeypatch.setattr(data_thread.alert_store, "insert_alert", refuse)
    rig.start()
    assert rig.sink.coin(AAA)[-1].state is LoadState.FAILED  # 끝을 알리는 CoinView는 온다
    before = len(fetch_requests(rig))
    rig.advance(3610)
    assert len(fetch_requests(rig)) > before  # 다음 받는 때에도 받는다
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
    assert afters == {"12:02까지 봉 받기를 멈췄습니다."}  # blocked_until = 한국 시간 12:00 + 120초
    assert all(a.text == "봉 통계를 만들지 못했습니다(일시 차단)." for a in alerts)


def test_while_blocked_nothing_is_requested_and_it_is_fetched_once_after(build: Any) -> None:
    rig = build(coins=(AAA, BBB))
    block(rig, 120.0)
    rig.start()
    sent = len(fetch_requests(rig))
    # 차단 중의 "다시 시도": 요청 없이 차단 묶음, A-1 없음
    rig.sink.clear()
    rig.worker.retry_candles()
    rig.settle()
    assert len(fetch_requests(rig)) == sent
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
    assert len(fetch_requests(rig)) == sent
    rig.advance(1)  # blocked_until: 저절로 한 번 받는다
    assert len(rig.quote.candle_requests(AAA)) > 0
    assert {r.market for r in rig.quote.candle_requests()[sent:]} == {AAA, BBB}
    after = len(fetch_requests(rig))
    rig.advance(3489)  # 그 받기(03:02) 뒤의 (라)는 저장된 마지막 봉(03:00)의 끝 + 10초(04:00:10)다
    assert len(fetch_requests(rig)) == after
    rig.advance(1)
    assert len(fetch_requests(rig)) > after


def test_a_fetch_time_inside_a_block_does_nothing_and_the_block_end_fetches_once(
    build: Any,
) -> None:
    rig = build()
    rig.start()  # 성공: fetched_at = NOW
    fetched = rig.sink.coin(AAA)[-1].fetched_at
    assert fetched == NOW
    rig.advance(1800)
    block(rig, 7200.0)
    rig.worker.retry_candles()  # 차단을 받는다. (라)는 저장된 마지막 봉의 끝 + 10초(04:00:10)다
    rig.settle()
    view = rig.sink.coin(AAA)[-1]
    assert (view.error_name, view.fetched_at) == (ERROR_BLOCKED, fetched)
    requests, views, alerts = (
        len(fetch_requests(rig)),
        len(rig.sink.of(CoinView)),
        len(rig.alerts()),
    )
    backoff = rig.worker._backoff
    rig.advance(3600)  # (라)의 때가 차단 기간(7200초) 안에서 온다
    rig.advance(3599)  # 차단이 끝나기 1초 전
    assert (len(fetch_requests(rig)), len(rig.sink.of(CoinView)), len(rig.alerts())) == (
        requests, views, alerts,
    )  # fmt: skip
    rig.worker.retry_candles()  # 요청하지 않은 받기도 fetched_at과 차례를 바꾸지 않는다
    rig.settle()
    assert len(fetch_requests(rig)) == requests
    assert rig.sink.coin(AAA)[-1].fetched_at == fetched
    assert rig.worker._backoff == backoff
    rig.advance(1)  # blocked_until: 저절로 한 번 받는다
    assert len(fetch_requests(rig)) == requests + 1  # 고른 단위 하나
    assert states(rig.sink.coin(AAA))[-2:] == [LoadState.LOADING, LoadState.READY]


def test_a_save_during_a_block_saves_then_sends_blocked_views_without_requests(build: Any) -> None:
    rig = build(coins=(AAA,))
    block(rig, 600.0)
    rig.start()
    rig.worker.refresh_markets()  # 차단 중이므로 목록은 못 받았다
    rig.settle()
    count = len(fetch_requests(rig))
    rig.sink.clear()
    rig.worker.save_settings((AAA,), 90, first_setup=False)  # 코인이 같으면 목록과 견주지 않는다
    rig.settle()
    kinds = [type(u).__name__ for u in rig.sink.items]
    assert kinds == ["SaveResult", "SettingsView", "AlertsView", "CoinView"]
    assert rig.sink.coin(AAA)[0].error_name == ERROR_BLOCKED
    assert len(fetch_requests(rig)) == count
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
        kind="candles", market=AAA, unit=HOUR,
    )  # fmt: skip
    rig.start()
    assert seconds_blocked(rig) == 300
    rig.advance(300)
    assert seconds_blocked(rig) == 600
    rig.advance(600)  # 성공(03:15): 처음으로 돌아간다
    assert rig.sink.coin(AAA)[-1].state is LoadState.READY
    rig.advance(2710)  # 다음 (라)는 04:00:10이다
    assert seconds_blocked(rig) == 300


def markets_block_seconds(rig: Rig) -> float:
    view = rig.sink.of(MarketsView)[-1]
    assert (view.state, view.error_name) == (LoadState.FAILED, ERROR_BLOCKED)
    assert view.blocked_until is not None
    return (view.blocked_until - rig.clock.now()).total_seconds()


def test_the_list_of_markets_shares_the_steps_with_the_candles(build: Any) -> None:
    rig = build(coins=(AAA,), done=True)
    rig.quote.script(
        QuoteError(ERROR_BLOCKED), QuoteError(ERROR_NETWORK), kind="candles", unit=HOUR
    )
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
    rig = build(days=None)  # 보관 기간을 고르지 않아 60분마다의 알림 지우기가 없다
    block(rig, 999_999_999.0)
    rig.start()
    # 다음 기다림은 1분 점검(M3 명세 3.9)까지다. 차단의 때는 줄이지 않는다
    assert rig.clock.waits[-1] == pytest.approx(60.0)
    assert seconds_blocked(rig) == pytest.approx(999_999_999.0)
    count = len(fetch_requests(rig))
    rig.worker.retry_candles()
    rig.settle()
    assert len(fetch_requests(rig)) == count


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
    assert rig.settings() == chosen((), None, False)
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
    assert rig.settings() == chosen((), None, False)


def test_a_first_setup_save_stores_the_level_and_starts_the_fetch(build: Any) -> None:
    rig = build(coins=(), done=False, days=None, bars=False, unit=None, count=None)
    for coin in (AAA, BBB):
        rig.quote.set_candles(coin, HOUR, make_candles(coin, HOUR, 150, last_start=THIS_HOUR))
        rig.quote.set_candles(coin, DAY, make_candles(coin, DAY, 40, last_start=TODAY_START))
    rig.start()
    listed(rig)
    rig.sink.clear()
    rig.worker.save_settings(
        (BBB, AAA), 7, first_setup=True, candle_unit=HOUR, candle_count=100,
        trade=stage3_trade((BBB, AAA)),
    )  # fmt: skip
    rig.settle()
    kinds = [type(u).__name__ for u in rig.sink.items]
    assert kinds[:2] == ["SaveResult", "SettingsView"]
    assert rig.sink.items[0] == SaveResult("settings", True, SETTINGS_SAVED)
    assert rig.sink.items[1] == chosen((BBB, AAA), 7, True)
    assert rig.settings() == chosen((BBB, AAA), 7, True)  # 단위와 개수가 있어 마친 것이다
    assert [r.market for r in rig.quote.candle_requests()][:2] == [BBB, AAA]  # 설정 목록의 차례
    assert {r.unit for r in rig.quote.candle_requests()} == {HOUR}  # 고른 단위 하나만 받는다
    assert states(rig.sink.coin(BBB))[-2:] == [LoadState.LOADING, LoadState.READY]
    assert states(rig.sink.coin(AAA))[-2:] == [LoadState.LOADING, LoadState.READY]
    assert rig.worker.wait_idle(SAFETY)
    count = len(fetch_requests(rig))
    rig.advance(3610)  # 이제 (라)가 돈다
    assert len(fetch_requests(rig)) > count


def test_a_first_setup_save_without_the_unit_and_count_is_not_done(build: Any) -> None:
    rig = build(coins=(AAA,), done=False, days=None, unit=None, count=None, bars=False)
    rig.start()
    rig.worker.save_settings((AAA,), 7, first_setup=True)
    rig.settle()
    assert rig.settings() == SettingsView((AAA,), 7, False)  # 처음 설정을 마친 것이 아니다
    assert rig.sink.of(SettingsView)[-1] == SettingsView((AAA,), 7, False)
    assert rig.sink.of(CoinView) == []  # 봉 단위가 저장되지 않은 동안은 보내지 않는다
    assert rig.quote.candle_requests() == []


def test_a_save_from_the_settings_screen_keeps_the_stored_first_setup_level(build: Any) -> None:
    rig = build(coins=(AAA,), done=True, days=30)
    rig.start()
    listed(rig)
    rig.worker.save_settings((AAA,), 90, first_setup=False)  # 보관 기간만 바꾼다
    # 코인도 바꾼다(3단계의 설정 화면은 매매 칸을 함께 넘긴다. 새 코인의 투자 비율이 든다)
    rig.worker.save_settings((BBB, AAA), 90, first_setup=False, trade=stage3_trade((BBB, AAA)))
    rig.settle()
    assert rig.settings() == chosen((BBB, AAA), 90, True)
    results = rig.sink.of(SaveResult)
    assert results == [SaveResult("settings", True, SETTINGS_SAVED)] * 2
    assert rig.sink.of(SettingsView)[-1] == chosen((BBB, AAA), 90, True)


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
    rig.quote.set_markets(markets(BBB, CCC))  # 저장된 코인(AAA)은 이제 목록에 없다
    listed(rig)
    real = data_thread.write_settings
    failing = {"on": True}

    def maybe(conn: Any, view: SettingsView, **kwargs: Any) -> None:
        if failing["on"]:
            raise StorageError("설정 저장에 실패했습니다: OperationalError")
        real(conn, view, **kwargs)

    monkeypatch.setattr(data_thread, "write_settings", maybe)
    count = len(fetch_requests(rig))
    rig.sink.clear()
    rig.worker.save_settings((BBB,), 7, first_setup=False)  # 코인을 바꾸는 저장이 실패한다
    rig.settle()
    kinds = [type(u).__name__ for u in rig.sink.items]
    assert kinds == ["SaveResult", "AlertsView"]  # 만든 A-29가 알림센터에 보인다
    assert rig.sink.items[0] == SaveResult("settings", False, SETTINGS_SAVE_FAILED)
    assert len(fetch_requests(rig)) == count
    assert rig.settings() == chosen((AAA,), 30, True)
    (alert,) = rig.alerts()
    assert (alert.code, alert.category, alert.coin) == ("A-29", AlertCategory.ERROR, None)
    assert alert.text == "저장하지 못했습니다(DB 오류)."
    assert alert.detail is not None
    assert alert.detail.stage == "설정 저장"
    assert alert.detail.after == "저장하지 못한 값은 화면에 남겨 두었습니다."
    rig.worker.save_settings((BBB,), 7, first_setup=False)  # 성공하기 전에는 다시 만들지 않는다
    rig.settle()
    assert len(rig.alerts()) == 1
    failing["on"] = False
    # 실패한 저장이 자료 스레드가 가진 설정을 바꿨다면 코인(AAA)이 달라져 목록과 견주고 거절된다.
    rig.worker.save_settings((AAA,), 90, first_setup=False)
    rig.settle()
    assert rig.settings() == chosen((AAA,), 90, True)
    assert rig.sink.of(SaveResult)[-1] == SaveResult("settings", True, SETTINGS_SAVED)


# --- 봉 단위와 봉 개수를 저장한다(3.5.4) ---------------------------------------------------------


def put_stored(
    rig: Rig, candles: tuple[Candle, ...], *, fetched_at: datetime | None = None
) -> None:
    conn = open_database(rig.db)
    try:
        write_candles(conn, candles)
        if fetched_at is not None:
            write_fetched_at(conn, AAA, fetched_at)
    finally:
        conn.close()


def test_changing_the_unit_deletes_the_other_units_and_starts_over_when_none_is_stored(
    build: Any,
) -> None:
    rig = build(coins=(AAA,))
    rig.start()
    assert len(rig.stored(AAA, HOUR)) == 200
    assert rig.fetched_at(AAA) == NOW
    rig.sink.clear()
    rig.worker.save_settings((AAA,), 30, first_setup=False, candle_unit=DAY, candle_count=100)
    rig.settle()
    first = rig.sink.coin(AAA)[0]  # 받기 앞의 값: 저장된 값이 없다
    assert (first.state, first.fetched_at) == (LoadState.NO_DATA, None)
    assert first.stats is not None
    assert (first.stats.unit, first.stats.requested_count, first.stats.bar_count) == (DAY, 100, 0)
    assert states(rig.sink.coin(AAA)) == [LoadState.NO_DATA, LoadState.LOADING, LoadState.NO_DATA]
    assert rig.stored(AAA, HOUR) == ()  # 다른 단위의 봉은 지웠다
    assert len(rig.stored(AAA, DAY)) == 40  # 그리고 (나)의 처음 받기를 했다
    assert rig.settings().candle_unit is DAY
    assert [r.unit for r in rig.quote.candle_requests(AAA)][-1] is DAY


def test_changing_the_unit_to_one_that_is_stored_keeps_the_candles_and_the_fetched_time(
    build: Any,
) -> None:
    rig = build(coins=(AAA,))
    rig.start()
    earlier = NOW - timedelta(days=1)
    put_stored(
        rig, make_candles(AAA, DAY, 120, last_start=TODAY_START - timedelta(days=1)),
        fetched_at=earlier,
    )  # fmt: skip
    rig.worker._fetched.clear()  # 시험이 DB에 직접 쓴 값을 자료 스레드가 읽게 한다
    rig.sink.clear()
    rig.worker.save_settings((AAA,), 30, first_setup=False, candle_unit=DAY, candle_count=100)
    rig.settle()
    first = rig.sink.coin(AAA)[0]  # 저장된 봉으로 셈한 값과 지우지 않은 fetched_at
    assert (first.state, first.fetched_at) == (LoadState.READY, earlier)
    assert first.stats is not None
    assert (first.stats.unit, first.stats.bar_count) == (DAY, 100)
    assert rig.stored(AAA, HOUR) == ()  # 다른 단위의 봉만 지웠다
    assert len(rig.stored(AAA, DAY)) >= 120
    assert states(rig.sink.coin(AAA))[:2] == [LoadState.READY, LoadState.LOADING]


def test_a_failed_unit_change_in_storage_keeps_the_candles_and_the_fetched_time(build: Any) -> None:
    rig = build(coins=(AAA,))
    rig.start()
    conn = open_database(rig.db)
    try:
        migrate(conn)
        conn.execute("DROP TRIGGER IF EXISTS never")
        conn.execute(
            "CREATE TRIGGER never BEFORE UPDATE ON settings WHEN NEW.key = 'candle_unit' "
            "BEGIN SELECT RAISE(ABORT, '쓸 수 없다'); END"
        )
        conn.commit()
    finally:
        conn.close()
    rig.sink.clear()
    rig.worker.save_settings((AAA,), 30, first_setup=False, candle_unit=DAY, candle_count=100)
    rig.settle()
    assert rig.sink.of(SaveResult)[-1] == SaveResult("settings", False, SETTINGS_SAVE_FAILED)
    assert len(rig.stored(AAA, HOUR)) == 200  # 같은 트랜잭션이라 지우기도 되돌려졌다
    assert rig.fetched_at(AAA) == NOW
    assert rig.settings().candle_unit is HOUR


def test_a_count_only_change_keeps_everything_and_fetches_back_the_missing_candles(
    build: Any,
) -> None:
    rig = build(coins=(AAA,))
    rig.quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 300, last_start=THIS_HOUR))
    rig.start()
    assert len(rig.stored(AAA, HOUR)) == 200
    before = len(rig.quote.candle_requests(AAA, HOUR))
    rig.sink.clear()
    rig.worker.save_settings((AAA,), 30, first_setup=False, candle_count=500)  # 단위는 그대로
    rig.settle()
    assert rig.settings().candle_count == 500
    assert rig.settings().candle_unit is HOUR
    assert rig.fetched_at(AAA) is not None
    assert len(rig.stored(AAA, HOUR)) == 300  # 거슬러 받아 가장 이른 봉까지 왔다
    assert len(rig.quote.candle_requests(AAA, HOUR)) - before == 2  # 그 뒤의 받기와 거슬러 받기
    assert (AAA, HOUR) in rig.worker._reached_oldest
    last = rig.sink.coin(AAA)[-1]
    assert last.state is LoadState.NO_DATA  # 300개뿐이라 500개가 안 된다
    assert last.stats is not None
    assert (last.stats.requested_count, last.stats.bar_count) == (500, 299)
    after = len(rig.quote.candle_requests(AAA, HOUR))
    rig.worker.retry_candles()  # 가장 이른 봉에 닿았다고 기억해 다시 거슬러 받지 않는다
    rig.settle()
    assert len(rig.quote.candle_requests(AAA, HOUR)) - after == 1


def test_a_restart_forgets_that_the_oldest_candle_was_reached(build: Any) -> None:
    first = build(coins=(AAA,), count=500)
    first.quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 300, last_start=THIS_HOUR))
    first.start()
    first.worker.retry_candles()
    first.settle()
    assert (AAA, HOUR) in first.worker._reached_oldest
    first.worker.stop()
    first.worker.join(SAFETY)
    second = build(coins=(AAA,), count=500, bars=False)
    second.quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 300, last_start=THIS_HOUR))
    second.start()
    kinds = [r.to is None for r in second.quote.candle_requests(AAA, HOUR)]
    assert len(kinds) == 2  # 그 뒤의 받기와, 기억이 없어 다시 하는 거슬러 받기


def test_a_unit_change_clears_what_was_remembered_about_the_oldest_candle(build: Any) -> None:
    rig = build(coins=(AAA,), count=500)
    rig.quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 300, last_start=THIS_HOUR))
    rig.start()
    assert (AAA, HOUR) in rig.worker._reached_oldest
    rig.worker.save_settings((AAA,), 30, first_setup=False, candle_unit=DAY, candle_count=500)
    rig.settle()
    assert (AAA, HOUR) not in rig.worker._reached_oldest


def test_an_omitted_unit_or_count_keeps_the_stored_value(build: Any) -> None:
    rig = build(coins=(AAA,))
    rig.start()
    rig.worker.save_settings((AAA,), 7, first_setup=False)
    rig.settle()
    assert rig.settings() == chosen((AAA,), 7, True)
    rig.worker.save_settings((AAA,), 7, first_setup=False, candle_unit=HOUR)
    rig.worker.save_settings((AAA,), 7, first_setup=False, candle_count=200)
    rig.settle()
    assert rig.settings() == SettingsView(
        (AAA,), 7, True, candle_unit=HOUR, candle_count=200, trade=stage3_trade((AAA,))
    )  # fmt: skip
    assert len(rig.stored(AAA, HOUR)) == 200  # 같은 단위라 봉을 지우지 않는다


def test_a_count_that_is_not_offered_is_not_saved_and_makes_no_alert(build: Any) -> None:
    rig = build(coins=(AAA,))
    rig.start()
    count = len(fetch_requests(rig))
    rig.sink.clear()
    rig.worker.save_settings((AAA,), 7, first_setup=False, candle_count=150)
    rig.settle()
    assert rig.sink.items == [SaveResult("settings", False, SETTINGS_SAVE_FAILED)]
    assert rig.settings() == chosen((AAA,), 30, True)
    assert rig.alerts() == []
    assert len(fetch_requests(rig)) == count


def test_a_record_of_the_first_stage_without_a_unit_is_not_done_and_sends_no_coin_view(
    build: Any,
) -> None:
    rig = build(coins=(AAA,), done=True, unit=None, count=None)  # 1단계 보완 전에 마친 기록
    rig.start()
    first = rig.sink.items[0]
    assert isinstance(first, SettingsView)
    assert first.first_setup_done is False
    assert rig.sink.of(CoinView) == []
    assert fetch_requests(rig) == []
    rig.advance(3 * data_thread.IDLE_SECONDS)
    assert fetch_requests(rig) == []  # (라)도 없다


def test_a_success_after_not_enough_candles_lets_the_next_shortage_alert_again(build: Any) -> None:
    rig = build(coins=(AAA,), count=500)  # 거래소에는 200개뿐이라 500개가 안 된다
    rig.start()
    assert rig.sink.coin(AAA)[-1].state is LoadState.NO_DATA
    assert [a.code for a in rig.alerts()] == ["A-1"]
    rig.worker.retry_candles()  # 같은 오류 이름은 다시 만들지 않는다
    rig.settle()
    assert len(rig.alerts()) == 1
    rig.worker.save_settings((AAA,), 30, first_setup=False, candle_count=100)
    rig.settle()
    assert rig.sink.coin(AAA)[-1].state is LoadState.READY  # 값이 나는 받기: 되풀이를 푼다
    assert len(rig.alerts()) == 1
    rig.worker.save_settings((AAA,), 30, first_setup=False, candle_count=500)  # 다시 모자라다
    rig.settle()
    assert rig.sink.coin(AAA)[-1].state is LoadState.NO_DATA
    alerts = rig.alerts()
    assert [a.code for a in alerts] == ["A-1", "A-1"]
    assert alerts[1].detail is not None
    assert alerts[1].detail.content == (
        "완성된 봉이 고른 개수(500개)보다 적어 봉 통계를 셈하지 못했습니다(완성된 봉 199개)."
    )


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
    assert rig.sink.items[0] == SettingsView(state=LoadState.FAILED)
    (alerts_view,) = rig.sink.of(AlertsView)  # 알림센터는 그대로 보낸다(만든 A-30이 보인다)
    assert [view.code for view in alerts_view.alerts] == ["A-30"]
    assert len(rig.sink.items) == 2
    assert fetch_requests(rig) == []
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
    assert [type(u).__name__ for u in rig.sink.items] == ["SettingsView", "AlertsView"]
    assert fetch_requests(rig) == []
    assert len(rig.alerts()) == 1


def test_unreadable_settings_are_read_again_every_sixty_minutes(build: Any) -> None:
    rig = build()
    break_settings(rig)
    rig.start()
    rig.advance(3600)  # 아직 깨져 있다: A-30은 60분이 지났으니 다시 만든다
    assert fetch_requests(rig) == []
    assert [a.code for a in rig.alerts()] == ["A-30", "A-30"]
    assert rig.sink.of(SettingsView) == [SettingsView(state=LoadState.FAILED)]
    repair_settings(rig)
    rig.advance(3600)
    assert rig.sink.of(SettingsView)[-1] == chosen((AAA,), 30, True)
    assert states(rig.sink.coin(AAA))[-3:] == [
        LoadState.NO_DATA,
        LoadState.LOADING,
        LoadState.READY,
    ]
    assert rig.quote.candle_requests(AAA)  # 처음 설정을 마쳤으므로 받기를 시작한다
    # 그 뒤는 보통과 같다: (라)로 받는다(저장된 마지막 봉의 끝 + 10초, 06:00:10)
    count = len(fetch_requests(rig))
    rig.advance(3609)
    assert len(fetch_requests(rig)) == count
    rig.advance(1)
    assert len(fetch_requests(rig)) > count


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


def test_the_a1_of_a_round_are_made_together_after_it_ends_with_one_time(build: Any) -> None:
    rig = build(coins=(AAA, BBB))
    fail(rig, QuoteError(ERROR_NETWORK), unit=HOUR, market=AAA)
    fail(rig, QuoteError(ERROR_TIMEOUT), unit=HOUR, market=BBB)
    rig.start()
    first, second = rig.alerts()
    assert (first.coin, second.coin) == (AAA, BBB)  # 코인의 차례
    assert first.created_at == second.created_at == NOW
    assert first.id < second.id
    assert first.detail is not None
    assert first.detail.after == "다음 받는 때(13:00)에 다시 받습니다."


def test_the_stored_alert_columns(build: Any) -> None:
    rig = build()
    fail(rig, QuoteError(ERROR_NETWORK), unit=HOUR)
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
            "실패한 봉: 60분봉", "다음 받는 때(13:00)에 다시 받습니다.", 0, "not_sent",
        )
    ]  # fmt: skip


def test_the_alert_texts_carry_no_secret_or_path(build: Any) -> None:
    url = "https://" + "discord.com/api/webhooks/" + "1" * 18 + "/" + "Tok_" * 16
    quote = RaisingQuote(RuntimeError(f"{url} /{'home'}/u1/x"), HOUR, markets=markets(AAA))
    rig = build(quote=quote)
    rig.start()
    (alert,) = rig.alerts()
    assert alert.detail is not None
    assert alert.detail.content == "(가려서 보임) ~/x"


# --- 검토 뒤 보완: 켤 때의 예외, 기다림의 틈, 요청 한도, 시계가 흐르는 받기 ----------------------


class TickingQuote(FakeQuoteSource):
    """요청마다 가짜 시계를 옮기는 `QuoteSource`(받기가 시간이 걸리는 것을 흉내 낸다)."""

    def __init__(self, clock: FakeClock, seconds: float, **kwargs: Any) -> None:
        super().__init__(clock=clock, **kwargs)
        self._ticker = (clock, seconds)

    def get_candles(self, market: str, unit: CandleUnit, *, to: datetime | None, count: int) -> Any:
        self._ticker[0].advance(self._ticker[1])
        return super().get_candles(market, unit, to=to, count=count)

    def list_markets(self) -> tuple[Market, ...]:
        self._ticker[0].advance(self._ticker[1])
        return super().list_markets()


def test_an_exception_in_the_first_calculation_does_not_end_the_thread(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build(coins=(AAA, BBB))
    real = data_thread.compute_bar_stats
    calls = {"n": 0}

    def broken_once(candles: Any, now: datetime, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:  # 켤 때 첫 코인의 처음 셈
            raise RuntimeError("처음 셈이 깨졌다")
        return real(candles, now, **kwargs)

    monkeypatch.setattr(data_thread, "compute_bar_stats", broken_once)
    rig.start()
    assert rig.worker.thread is not None
    assert rig.worker.thread.is_alive()
    first = rig.sink.coin(AAA)[0]
    assert (first.state, first.error_name, first.stats) == (
        LoadState.FAILED, "RuntimeError", empty_bar_stats(AAA, HOUR, 100),
    )  # fmt: skip
    assert rig.sink.coin(BBB)[0].state is LoadState.NO_DATA  # 남은 코인의 `CoinView`도 온다
    assert [r.market for r in rig.quote.candle_requests()] == [AAA, BBB]  # (가) 받기
    assert states(rig.sink.coin(AAA))[-2:] == [LoadState.LOADING, LoadState.READY]
    (alert,) = rig.alerts()
    assert (alert.code, alert.category, alert.coin) == ("A-30", AlertCategory.OTHER, AAA)
    assert alert.text == "예상하지 못한 오류가 났습니다(RuntimeError)."
    assert alert.detail is not None
    assert (alert.detail.stage, alert.detail.error_name) == ("켜기", "RuntimeError")
    assert alert.detail.content == "처음 셈이 깨졌다"
    count = len(fetch_requests(rig))
    rig.worker.retry_candles()  # 큐의 동작과 (라)가 그대로 돈다
    rig.settle()
    assert len(fetch_requests(rig)) > count
    rig.advance(3610)
    assert len(fetch_requests(rig)) > count + 2


def test_an_exception_in_the_first_fetch_round_does_not_end_the_thread(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    calls = {"n": 0}
    real = rig.worker._fetch_round

    def broken_once() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("받기가 깨졌다")
        real()

    monkeypatch.setattr(rig.worker, "_fetch_round", broken_once)
    rig.start()
    (alert,) = rig.alerts()
    assert (alert.code, alert.detail.stage if alert.detail else None) == ("A-30", "봉 받기")
    rig.worker.retry_candles()
    rig.settle()
    assert rig.sink.coin(AAA)[-1].state is LoadState.READY
    assert rig.worker.thread is not None
    assert rig.worker.thread.is_alive()


def test_a_stored_candle_that_cannot_be_read_at_the_start_is_an_a30_of_that_coin(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build(coins=(AAA, BBB))
    real = data_thread.read_latest_complete_candles
    calls = {"n": 0}

    def unreadable_once(conn: Any, market: str, unit: CandleUnit, now: datetime, count: int) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise StorageError("봉 읽기에 실패했습니다: OperationalError")
        return real(conn, market, unit, now, count)

    monkeypatch.setattr(data_thread, "read_latest_complete_candles", unreadable_once)
    rig.start()
    first = rig.sink.coin(AAA)[0]
    assert (first.state, first.error_name, first.fetched_at) == (LoadState.FAILED, "DB 오류", None)
    assert first.stats == empty_bar_stats(AAA, HOUR, 100)  # 값이 없어도 `stats`는 늘 있다
    assert first.last_incomplete is False
    assert rig.sink.coin(BBB)[0].state is LoadState.NO_DATA
    (alert,) = rig.alerts()
    assert (alert.code, alert.category, alert.coin) == ("A-30", AlertCategory.OTHER, AAA)
    assert alert.detail is not None
    assert (alert.detail.stage, alert.detail.error_name) == ("켜기", "DB 오류")
    assert alert.detail.coin == AAA
    assert len(rig.quote.candle_requests(AAA)) == 1  # (가) 받기는 그대로 나간다
    assert states(rig.sink.coin(AAA))[-2:] == [LoadState.LOADING, LoadState.READY]


def test_wait_idle_returns_only_once_the_clock_wait_is_on_so_a_later_advance_wakes_it(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    real = rig.worker._seconds_until_due

    def slow() -> float:
        value = real()
        threading.Event().wait(0.05)  # 남은 초를 셈한 뒤 시계의 기다림이 걸리기까지의 틈을 넓힌다
        return value

    monkeypatch.setattr(rig.worker, "_seconds_until_due", slow)
    rig.worker.start()
    assert rig.worker.wait_idle(SAFETY)
    count = len(fetch_requests(rig))
    rig.clock.advance(3610)  # 참을 돌려준 뒤의 `advance`는 틈과 상관없이 자료 스레드를 깨운다
    assert rig.worker.wait_idle(SAFETY)
    assert len(fetch_requests(rig)) > count
    for _ in range(3):
        count = len(fetch_requests(rig))
        rig.clock.advance(3600)
        assert rig.worker.wait_idle(SAFETY)
        assert len(fetch_requests(rig)) > count


def window_of(requests: list[Any], start: int) -> int:
    first = requests[start].monotonic
    return sum(1 for r in requests if first <= r.monotonic < first + 1.0)


def test_requests_through_the_worker_keep_the_limit_and_share_one_client(build: Any) -> None:
    rig = build(coins=(AAA, BBB))
    rig.start()  # 봉 요청 둘
    rig.worker.refresh_markets()  # 코인 목록 요청은 같은 `QuoteClient`의 한도에 센다
    rig.settle()
    for _ in range(2):
        rig.worker.retry_candles()
        rig.settle()
    requests = fetch_requests(rig)
    assert len(requests) == 7
    assert [r.kind for r in requests].count("markets") == 1
    assert all(r.monotonic is not None for r in requests)
    assert max(window_of(requests, i) for i in range(len(requests))) <= 5
    assert max(r.monotonic for r in requests) - min(r.monotonic for r in requests) >= 1.0
    assert rig.clock.sleeps  # 한도 장치가 시계로 기다렸다


def test_the_a1_and_the_next_fetch_time_count_from_the_end_of_the_round(build: Any) -> None:
    clock = FakeClock()
    quote = TickingQuote(clock, 30.0, markets=markets(AAA, BBB))
    rig = build(coins=(AAA, BBB), quote=quote, clock=clock)
    fail(rig, QuoteError(ERROR_NETWORK), unit=HOUR, market=AAA)
    fail(rig, QuoteError(ERROR_TIMEOUT), unit=HOUR, market=BBB)
    rig.start()
    ended = NOW + timedelta(seconds=60)  # 요청 둘, 요청마다 30초
    assert rig.clock.now() == ended
    first, second = rig.alerts()
    assert first.created_at == second.created_at == ended
    for alert in (first, second):
        assert alert.detail is not None
        # 저장된 봉이 없어 (라)는 다음 경계(04:00:10 UTC)다
        assert alert.detail.after == "다음 받는 때(13:00)에 다시 받습니다."
    count = len(fetch_requests(rig))
    rig.advance(3549)  # 04:00:10까지 3550초 남았다(끝난 때가 03:01:00이다)
    assert len(fetch_requests(rig)) == count
    rig.advance(1)
    assert len(fetch_requests(rig)) > count
    assert rig.sink.coin(AAA)[-1].as_of > ended


def test_the_a1_after_text_ignores_actions_queued_during_the_round(build: Any) -> None:
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
    quote.script(QuoteError(ERROR_NETWORK), kind="candles", market=AAA, unit=HOUR)
    rig.worker.start()
    assert inside.wait(SAFETY)
    rig.worker.retry_candles()  # 받는 중에 큐에 든 (다)
    rig.worker.save_settings((AAA,), 7, first_setup=False)  # 와 (나)
    rig.clock.advance(120)  # 받기가 길어졌다
    release.set()
    rig.settle()
    alert = rig.alerts()[0]
    assert alert.created_at == NOW + timedelta(seconds=120)
    assert alert.detail is not None
    # 큐의 일은 정해진 때가 아니다(저장된 봉이 없어 다음 경계 04:00:10 UTC)
    assert alert.detail.after == "다음 받는 때(13:00)에 다시 받습니다."
    assert len(rig.alerts()) == 1


def test_an_earlier_failed_coin_also_gets_the_block_time_when_a_later_coin_is_blocked(
    build: Any,
) -> None:
    rig = build(coins=(AAA, BBB))
    fail(rig, QuoteError(ERROR_TIMEOUT), unit=HOUR, market=AAA)
    block(rig, 120.0, market=BBB)
    rig.start()
    first, second = rig.alerts()
    assert (first.coin, second.coin) == (AAA, BBB)
    assert first.text == "봉 통계를 만들지 못했습니다(시간 초과)."
    assert first.detail is not None
    assert first.detail.content == "실패한 봉: 60분봉"
    assert first.detail.after == "12:02까지 봉 받기를 멈췄습니다."  # 차단이 아닌 앞 코인의 것도
    assert second.detail is not None
    assert second.detail.after == "12:02까지 봉 받기를 멈췄습니다."
    assert second.detail.content == "거래소가 공개 시세 요청을 잠시 막았습니다."
    assert rig.sink.coin(AAA)[-1].error_name == ERROR_TIMEOUT


def test_every_alert_code_is_stored_as_not_sent_without_a_send_thread(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build(coins=(AAA,), done=True)
    rig.start()
    rig.quote.script(QuoteError(ERROR_NETWORK), kind="markets")
    rig.worker.refresh_markets()  # A-31
    rig.settle()
    real_write = data_thread.write_settings

    def refuse(conn: Any, view: SettingsView, **kwargs: Any) -> None:
        raise StorageError("설정 저장에 실패했습니다: OperationalError")

    monkeypatch.setattr(data_thread, "write_settings", refuse)
    rig.worker.save_settings((AAA,), 7, first_setup=False)  # A-29
    rig.settle()
    monkeypatch.setattr(data_thread, "write_settings", real_write)

    def unreadable(conn: Any, market: str, unit: CandleUnit, now: datetime) -> Any:
        raise StorageError("완성된 봉 세기에 실패했습니다: OperationalError")

    monkeypatch.setattr(data_thread, "count_complete_candles", unreadable)
    rig.worker.retry_candles()  # A-30
    rig.settle()
    fail_unit = [a for a in rig.alerts() if a.code == "A-30"]
    assert len(fail_unit) == 1
    conn = open_database(rig.db)
    try:
        rows = conn.execute("SELECT code, discord_state FROM alerts ORDER BY id").fetchall()
    finally:
        conn.close()
    assert sorted(code for code, _ in rows) == ["A-29", "A-30", "A-31"]
    assert {state for _, state in rows} == {"not_sent"}  # 보내기 스레드가 없으니 결과도 없다


def test_a_coin_change_is_accepted_after_a_failed_or_blocked_list_but_a_reorder_needs_a_list(
    build: Any,
) -> None:
    rig = build(coins=(AAA,), done=True)
    rig.start()
    listed(rig)  # READY
    rig.quote.script(QuoteError(ERROR_NETWORK), kind="markets")
    listed(rig)  # 받지 못함: 앞서 받은 목록을 지우지 않는다
    assert rig.sink.of(MarketsView)[-1].state is LoadState.FAILED
    rig.worker.save_settings((BBB,), 30, first_setup=False)
    rig.settle()
    assert rig.settings().coins == (BBB,)
    rig.quote.script(QuoteError(ERROR_BLOCKED, 600.0), kind="markets")
    listed(rig)  # 일시 차단 중
    assert rig.sink.of(MarketsView)[-1].error_name == ERROR_BLOCKED
    rig.worker.save_settings((CCC, BBB), 30, first_setup=False)
    rig.settle()
    assert rig.settings().coins == (CCC, BBB)
    assert rig.sink.of(SaveResult)[-1] == SaveResult("settings", True, SETTINGS_SAVED)


def test_a_reorder_of_the_saved_coins_is_refused_without_a_received_list(build: Any) -> None:
    rig = build(coins=(AAA, BBB), done=True)
    rig.start()  # 코인 목록은 한 번도 받지 않았다
    rig.sink.clear()
    rig.worker.save_settings((BBB, AAA), 30, first_setup=False)
    rig.settle()
    assert rig.sink.items == [SaveResult("settings", False, SETTINGS_COIN_NOT_LISTED)]
    assert rig.settings().coins == (AAA, BBB)


def test_actions_queued_before_the_start_run_after_every_view_of_the_first_fetch(
    build: Any,
) -> None:
    rig = build(coins=(AAA, BBB), done=True)
    rig.worker.retry_candles()
    rig.worker.refresh_markets()
    rig.start()
    shown = [
        (type(u).__name__, getattr(u, "market", None), u.state if hasattr(u, "state") else None)
        for u in rig.sink.items
    ]
    load, ready = LoadState.LOADING, LoadState.READY
    assert shown == [
        ("SettingsView", None, LoadState.READY),
        ("CoinView", AAA, LoadState.NO_DATA),
        ("CoinView", BBB, LoadState.NO_DATA),
        ("AlertsView", None, LoadState.READY),
        ("AiStatusView", None, None),
        ("ReportListView", AAA, ready), ("ReportListView", BBB, ready),
        ("CoinView", AAA, load), ("CoinView", AAA, ready),  # (가) 받기
        ("CoinView", BBB, load), ("CoinView", BBB, ready),
        ("CoinView", AAA, load), ("CoinView", AAA, ready),  # 시작 전에 넣은 "다시 시도"
        ("CoinView", BBB, load), ("CoinView", BBB, ready),
        ("MarketsView", None, load), ("MarketsView", None, ready),  # 그다음 코인 목록
    ]  # fmt: skip


def test_a_restart_counts_the_repeats_from_the_start(build: Any, tmp_path: Path) -> None:
    first = build()
    fail(first, QuoteError(ERROR_NETWORK), unit=HOUR)
    first.start()
    first.worker.retry_candles()
    first.settle()
    assert len(first.alerts()) == 1  # 같은 오류 이름은 한 번이다
    first.worker.stop()
    first.worker.join(SAFETY)
    second = build()  # 같은 DB로 다시 켠다
    fail(second, QuoteError(ERROR_NETWORK), unit=HOUR)
    second.start()
    assert [a.code for a in second.alerts()] == ["A-1", "A-1"]  # 되풀이가 처음부터 세어진다


def test_an_exception_other_than_a_storage_error_while_reading_settings_is_unreadable_settings(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    real = data_thread.read_settings
    calls = {"n": 0}

    def broken_once(conn: Any) -> SettingsView:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("설정 글이 이상하다")
        return real(conn)

    monkeypatch.setattr(data_thread, "read_settings", broken_once)
    rig.start()
    assert [type(u).__name__ for u in rig.sink.items] == ["SettingsView", "AlertsView"]
    assert rig.sink.items[0] == SettingsView(state=LoadState.FAILED)
    assert fetch_requests(rig) == []
    (alert,) = rig.alerts()
    assert (alert.code, alert.coin) == ("A-30", None)
    assert alert.detail is not None
    assert (alert.detail.stage, alert.detail.error_name) == ("켜기", "ValueError")
    assert rig.worker.thread is not None
    assert rig.worker.thread.is_alive()
    rig.advance(3600)  # 60분 뒤 다시 읽으면 읽힌다
    assert rig.sink.of(SettingsView)[-1] == chosen((AAA,), 30, True)
    assert rig.quote.candle_requests(AAA)


# --- 예외로 끝난 받기 뒤에도 다음 (라)를 정한다 ---------------------------------------------------


def break_the_round(rig: Rig, monkeypatch: pytest.MonkeyPatch, *, on_call: int = 1) -> list[int]:
    """코인의 `try` 밖에 있는 `_expire_block`이 `on_call`번째에 한 번 예외를 낸다.

    돌려주는 목록의 길이가 받기가 불린 횟수다.
    """
    calls: list[int] = []
    real = rig.worker._expire_block

    def expire() -> None:
        calls.append(1)
        if len(calls) == on_call:
            raise RuntimeError("받기가 시작하자마자 깨졌다")
        real()

    monkeypatch.setattr(rig.worker, "_expire_block", expire)
    return calls


def test_a_first_round_that_ends_with_an_exception_is_tried_again_after_sixty_minutes(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    calls = break_the_round(rig, monkeypatch)
    rig.start()
    assert len(calls) == 1
    assert fetch_requests(rig) == []
    (alert,) = rig.alerts()
    assert alert.code == "A-30"
    rig.advance(3609)  # 저장된 봉이 없어 (라)는 다음 경계(04:00:10)다
    assert fetch_requests(rig) == []
    rig.advance(1)  # 화면의 동작 없이 (라)의 때에 받는다
    assert len(calls) == 2
    assert len(fetch_requests(rig)) == 1
    assert rig.sink.coin(AAA)[-1].state is LoadState.READY


def test_a_first_round_after_settings_were_read_again_that_ends_with_an_exception_is_retried(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    break_settings(rig)
    rig.start()
    repair_settings(rig)
    calls = break_the_round(rig, monkeypatch)
    rig.advance(3600)  # 설정을 다시 읽고 (가) 받기를 시작하지만 예외로 끝난다(04:00)
    assert len(calls) == 1
    assert fetch_requests(rig) == []
    assert rig.sink.of(SettingsView)[-1] == chosen((AAA,), 30, True)
    rig.advance(3610)  # 화면의 동작 없이 (라)의 때(05:00:10)에 받는다
    assert len(calls) == 2
    assert len(fetch_requests(rig)) == 1


def test_a_first_setup_fetch_that_ends_with_an_exception_is_retried_after_sixty_minutes(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build(coins=(AAA,), done=False, days=None)
    rig.start()
    calls = break_the_round(rig, monkeypatch)
    rig.worker.save_settings((AAA,), 30, first_setup=True)  # 저장된 코인과 같은 코인
    rig.settle()
    assert rig.sink.of(SaveResult)[-1] == SaveResult("settings", True, SETTINGS_SAVED)
    assert len(calls) == 1  # 단계가 "설정 저장"인 `_guard` 안에서 받기가 예외로 끝났다
    assert fetch_requests(rig) == []
    assert [a.detail.stage for a in rig.alerts() if a.detail] == ["설정 저장"]
    rig.advance(3610)
    assert len(calls) == 2
    assert len(fetch_requests(rig)) == 1


def test_a_scheduled_round_that_ends_with_an_exception_does_not_spin(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    rig.start()  # 성공: 다음 (라)는 04:00:10이다
    calls = break_the_round(rig, monkeypatch)
    count = len(fetch_requests(rig))
    rig.advance(3610)  # (라)의 받기가 예외로 끝난다
    assert len(calls) == 1
    assert rig.worker.wait_idle(SAFETY)  # 자료 스레드는 쉰다(같은 받기를 바퀴마다 부르지 않는다)
    rig.advance(3599)
    assert len(calls) == 1  # 다음 (라)(05:00:10)가 되기 전에는 받기가 다시 불리지 않는다
    assert len(fetch_requests(rig)) == count
    rig.advance(1)
    assert len(calls) == 2  # 그 때가 되면 한 번 받는다
    assert len(fetch_requests(rig)) == count + 1
    assert rig.worker.wait_idle(SAFETY)
    assert len(calls) == 2


def test_the_round_ended_by_stop_does_not_set_the_next_time(
    build: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    rig.start()
    rig.worker._next_round_mono = None
    rig.worker.stop()
    rig.worker.join(SAFETY)
    rig.worker._fetch_round()  # 멈춘 뒤의 받기는 다음 때를 정하지 않는다
    assert rig.worker._next_round_mono is None
