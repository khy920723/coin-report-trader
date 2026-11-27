"""M1 명세 6절의 자료 스레드 시나리오: B11, B12, B17, B27, B29, B33, B34, B37.

따른 절은 2의 3, 3.5.4, 3.8, 3.9다. 자료 스레드와 보내기 스레드를 가짜 시세, 가짜 시계로 돌린다
(`acceptance.m1_support`). 화면에 보이는 글은 창을 만들어 본다(띄우지 않는다). 저장 실패와 예외는
그 함수만 잠깐 바꿔 낸다. 숫자는 만든 값이다.
"""

from datetime import timedelta

import pytest
from acceptance.m1_support import AAA, BBB, DAY, HOUR, MIN30, NOW, THIS_HOUR, prepare_db, utc
from fakes.quote import OK, make_candles
from PySide6.QtWidgets import QLabel, QWidget

from coin_report_trader.app import data_thread
from coin_report_trader.core import (
    AlertCategory,
    CoinView,
    LoadState,
    MarketsView,
    Mode,
    QuoteError,
    SettingsView,
    StorageError,
    UiContext,
)
from coin_report_trader.core.texts import (
    ERROR_BLOCKED,
    ERROR_NETWORK,
    ERROR_NOT_ENOUGH_CANDLES,
    ERROR_STORAGE,
    MARKETS_FAILED,
    STAGE_CANDLE_SAVE,
    STAGE_STARTUP,
    STAGE_STATS,
    blocked_markets_text,
    kst_when_text,
)
from coin_report_trader.storage import alerts as alert_store
from coin_report_trader.storage.database import open_database
from coin_report_trader.storage.states import DiscordState
from coin_report_trader.ui import create_main_window


def seconds_to_next_round(rig) -> float:
    due = rig.data._next_round_mono
    assert due is not None
    return due - rig.clock.monotonic()


def shown_texts(window: QWidget) -> list[str]:
    return [label.text() for label in window.findChildren(QLabel) if label.isVisibleTo(window)]


def blocked(*, retry: float | None) -> QuoteError:
    return QuoteError(ERROR_BLOCKED, retry)


def codes(rig) -> list[tuple[str, str | None, str | None]]:
    """알림의 (번호, 코인, 오류 이름). 23번 뒤에 생기는 매매 알림(A-27, A-28)은 세지 않는다."""
    return [
        (a.code, a.coin, a.detail.error_name if a.detail else None)
        for a in rig.alerts()
        if a.code not in ("A-27", "A-28")
    ]


# --- B11: 일시 차단 ----------------------------------------------------------------------------


@pytest.mark.scenario("B11")
def test_b11_a_block_stops_every_request_until_it_ends_then_fetches_once(rigs) -> None:
    rig = rigs(coins=(AAA, BBB))
    for coin in (AAA, BBB):
        rig.quote.set_candles(coin, HOUR, make_candles(coin, HOUR, 200, last_start=THIS_HOUR))
    rig.quote.script(blocked(retry=120.0), kind="candles", market=AAA, unit=HOUR)
    rig.start()
    until = NOW + timedelta(seconds=120)
    for coin in (AAA, BBB):
        last = rig.sink.coin(coin)[-1]
        assert (last.state, last.error_name, last.blocked_until) == (
            LoadState.FAILED, ERROR_BLOCKED, until,
        )  # fmt: skip
    # 둘째 코인에는 `LOADING`이 오지 않는다(요청하기 전에 받기가 멈췄다).
    assert [v.state for v in rig.sink.coin(BBB)][-1:] == [LoadState.FAILED]
    assert LoadState.LOADING not in [v.state for v in rig.sink.coin(BBB)[1:]]
    # 3단계부터 일시 차단은 오류 멈춤이다: 들어갈 때 A-23 하나(M3 명세 3.10.2)
    assert codes(rig) == [
        ("A-23", None, ERROR_BLOCKED),
        ("A-1", AAA, ERROR_BLOCKED),
        ("A-1", BBB, ERROR_BLOCKED),
    ]
    requests = len(rig.quote.requests)
    # 차단 중: "다시 시도"와 코인 목록 받기는 요청을 내지 않고, 차단 묶음의 `CoinView`만 다시 온다.
    rig.sink.clear()
    rig.data.retry_candles()
    rig.data.refresh_markets()
    rig.settle()
    assert len(rig.quote.requests) == requests
    for coin in (AAA, BBB):
        (view,) = rig.sink.coin(coin)
        assert (view.state, view.blocked_until) == (LoadState.FAILED, until)
    assert len(codes(rig)) == 3  # A-1은 더 생기지 않는다
    # 120초가 지나도 오류 멈춤이라 저절로 받지 않는다. 사용자가 풀면 대조 뒤 다시 받을 수 있다.
    rig.advance(119)
    assert len(rig.quote.requests) == requests
    rig.advance(1)
    rig.advance(3600)
    assert len(rig.quote.requests) == requests
    rig.data.resume_error()
    rig.settle()
    rig.data.retry_candles()
    rig.settle()
    after = rig.quote.candle_requests()[len(rig.quote.candle_requests()) - 2 :]
    assert sorted(r.market for r in after) == [AAA, BBB]
    assert rig.sink.coin(AAA)[-1].state in (LoadState.READY, LoadState.NO_DATA)


@pytest.mark.scenario("B11")
def test_b11_a_block_until_the_next_day_shows_the_date_and_time(qapp) -> None:
    window = create_main_window(UiContext("0.0.1", "X", None))
    try:
        window.apply(SettingsView((AAA,), 30, True, candle_unit=HOUR, candle_count=100))
        as_of = utc(2026, 1, 1, 14, 50)  # 한국 23:50
        until = utc(2026, 1, 1, 15, 20)  # 한국 다음 날 00:20
        view = CoinView(AAA, LoadState.FAILED, error_name=ERROR_BLOCKED, blocked_until=until,
                        as_of=as_of)  # fmt: skip
        window.apply(view)
        assert any("2026-01-02 00:20" in text for text in shown_texts(window))
    finally:
        window.deleteLater()


@pytest.mark.scenario("B11")
def test_b11_blocks_without_a_wait_grow_five_minutes_then_ten(rigs) -> None:
    rig = rigs()
    rig.quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 200, last_start=THIS_HOUR))
    rig.quote.script(
        blocked(retry=None), blocked(retry=120.0), blocked(retry=None),
        kind="candles", market=AAA, unit=HOUR,
    )  # fmt: skip
    rig.start()
    waits = []
    for _ in range(3):
        view = rig.sink.coin(AAA)[-1]
        assert view.blocked_until is not None
        wait = (view.blocked_until - rig.clock.now()).total_seconds()
        waits.append(wait)
        rig.advance(wait)  # 일시 차단은 오류 멈춤이라 풀고 다시 받아야 다음 차단을 받는다
        rig.data.resume_error()
        rig.settle()
        rig.data.retry_candles()
        rig.settle()
    assert waits == [300.0, 120.0, 600.0]  # 5분, 120초, 10분


# --- B12: 되풀이 ------------------------------------------------------------------------------


@pytest.mark.scenario("B12")
def test_b12_a1_comes_only_for_the_first_failure_and_after_a_success(rigs) -> None:
    rig = rigs()
    rig.quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 200, last_start=THIS_HOUR))
    failure = QuoteError(ERROR_NETWORK)
    rig.quote.script(failure, failure, OK, failure, kind="candles", market=AAA, unit=HOUR)
    rig.start()
    for _ in range(3):
        rig.advance(seconds_to_next_round(rig))
    states = [v.state for v in rig.sink.coin(AAA) if v.state is not LoadState.LOADING]
    assert states[-4:] == [LoadState.FAILED, LoadState.FAILED, LoadState.READY, LoadState.FAILED]
    assert codes(rig) == [("A-1", AAA, ERROR_NETWORK), ("A-1", AAA, ERROR_NETWORK)]


# --- B17: 오래된 알림 지우기 -----------------------------------------------------------------


@pytest.mark.scenario("B17")
def test_b17_old_alerts_are_kept_until_a_retention_is_chosen(rigs) -> None:
    stored = make_candles(AAA, HOUR, 30, last_start=THIS_HOUR - timedelta(hours=1))
    rig = rigs(days=None, done=False, candles=stored)
    conn = open_database(rig.db)
    try:
        ids = [
            alert_store.insert_alert(
                conn, created_at=NOW - timedelta(days=age), mode=Mode.PAPER,
                category=AlertCategory.OTHER, code="A-30", coin=None, text=f"{age}일 전",
                detail=None, seen=False, discord_state=DiscordState.NO_ADDRESS,
            )
            for age in (8, 6)
        ]  # fmt: skip
    finally:
        conn.close()
    rig.start()
    rig.advance(3600)  # 고르기 전에는 60분마다의 지우기도 지우지 않는다
    assert [a.id for a in rig.alerts()] == ids
    rig.data.save_settings((AAA,), 7, first_setup=True, candle_unit=HOUR, candle_count=100)
    rig.settle()
    # 8일 전은 지워지고 6일 전은 남는다(저장한 뒤의 받기가 만든 새 알림은 세지 않는다).
    assert [a.id for a in rig.alerts() if a.id in ids] == [ids[1]]
    assert rig.stored(AAA, HOUR)[: len(stored)] == stored  # 봉은 그대로다


# --- B27: 저장 실패, 예외, 설정을 읽지 못함 ------------------------------------------------


@pytest.mark.scenario("B27")
def test_b27_a_save_failure_and_an_exception_each_make_one_alert_and_fetching_goes_on(
    rigs, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = rigs()
    rig.quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 200, last_start=THIS_HOUR))
    real_write, real_compute = data_thread.write_candles, data_thread.compute_bar_stats
    calls = {"write": 0, "compute": 0}

    def write_once_broken(conn, candles):
        calls["write"] += 1
        if calls["write"] == 1:
            raise StorageError("봉 저장에 실패했습니다: OperationalError")
        return real_write(conn, candles)

    def compute_broken_in_the_second_round(candles, now, **kwargs):
        calls["compute"] += 1
        if calls["write"] == 2 and not calls.get("raised"):
            calls["raised"] = 1
            raise RuntimeError("셈이 깨졌다")
        return real_compute(candles, now, **kwargs)

    monkeypatch.setattr(data_thread, "write_candles", write_once_broken)
    monkeypatch.setattr(data_thread, "compute_bar_stats", compute_broken_in_the_second_round)
    rig.start()  # 첫 받기: 저장 실패
    rig.advance(seconds_to_next_round(rig))  # 둘째 받기: 셈의 예외
    before = len(rig.quote.candle_requests())
    rig.advance(seconds_to_next_round(rig))  # 셋째 받기: 다음 받는 때에 봉 요청이 나간다
    assert len(rig.quote.candle_requests()) == before + 1
    found = [(a.code, a.detail.stage, a.detail.error_name) for a in rig.alerts() if a.detail]
    assert found == [
        ("A-29", STAGE_CANDLE_SAVE, ERROR_STORAGE),
        ("A-30", STAGE_STATS, "RuntimeError"),
    ]


@pytest.mark.scenario("B27")
def test_b27_settings_that_cannot_be_read_stop_fetching_until_they_can(
    rigs, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = rigs()
    rig.quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 200, last_start=THIS_HOUR))
    real = data_thread.read_settings
    broken = {"on": True}

    def read(conn):
        if broken["on"]:
            raise StorageError("설정 읽기에 실패했습니다: OperationalError")
        return real(conn)

    monkeypatch.setattr(data_thread, "read_settings", read)
    rig.start()
    (first,) = rig.sink.of(SettingsView)
    assert first.state is LoadState.FAILED
    assert rig.quote.candle_requests() == []
    found = [(a.code, a.detail.stage, a.detail.error_name) for a in rig.alerts() if a.detail]
    assert found == [("A-30", STAGE_STARTUP, ERROR_STORAGE)]
    rig.data.retry_candles()  # 읽지 못한 동안 넣은 "다시 시도"도 아무것도 하지 않는다
    rig.settle()
    assert rig.quote.candle_requests() == []
    assert rig.sink.of(CoinView) == []
    broken["on"] = False
    rig.advance(3600)  # 60분 뒤 다시 읽는다
    last = rig.sink.of(SettingsView)[-1]
    assert (last.state, last.first_setup_done) == (LoadState.READY, True)
    assert rig.quote.candle_requests(AAA, HOUR) != []


# --- B29: 코인 목록을 받지 못함 ---------------------------------------------------------------


@pytest.mark.scenario("B29")
def test_b29_two_failed_market_lists_make_one_a31_and_show_the_failed_line(rigs, qapp) -> None:
    rig = rigs()
    rig.quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 200, last_start=THIS_HOUR))
    rig.start()
    rig.sink.clear()
    failure = QuoteError(ERROR_NETWORK)
    rig.quote.script(failure, failure, kind="markets")
    for _ in range(2):
        rig.data.refresh_markets()
        rig.settle()
    views = rig.sink.of(MarketsView)
    assert [v.state for v in views] == [LoadState.LOADING, LoadState.FAILED] * 2
    for view in views[1::2]:
        assert (view.markets, view.error_name, view.blocked_until) == ((), ERROR_NETWORK, None)
    assert [a.code for a in rig.alerts() if a.code == "A-31"] == ["A-31"]
    window = create_main_window(UiContext("0.0.1", "X", None))
    try:
        window.apply(SettingsView((), None, False))
        window.apply(views[-1])
        assert MARKETS_FAILED in shown_texts(window)
    finally:
        window.deleteLater()


# --- B33: 봉이 모자람의 되풀이 ----------------------------------------------------------------


@pytest.mark.scenario("B33")
def test_b33_not_enough_bars_three_times_makes_one_a1(rigs) -> None:
    rig = rigs()
    rig.quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 2, last_start=THIS_HOUR))
    rig.start()
    for _ in range(2):
        rig.advance(seconds_to_next_round(rig))
    assert len(rig.quote.candle_requests(AAA, HOUR)) >= 3
    assert codes(rig) == [("A-1", AAA, ERROR_NOT_ENOUGH_CANDLES)]


# --- B34: 코인 목록의 일시 차단 ----------------------------------------------------------------


@pytest.mark.scenario("B34")
def test_b34_a_block_of_the_market_list_stops_every_request_for_its_wait(rigs, qapp) -> None:
    rig = rigs(coins=(AAA, BBB))
    for coin in (AAA, BBB):
        rig.quote.set_candles(coin, HOUR, make_candles(coin, HOUR, 200, last_start=THIS_HOUR))
    rig.start()
    alerts_before = len(codes(rig))
    rig.sink.clear()
    rig.quote.script(blocked(retry=120.0), kind="markets")
    rig.data.refresh_markets()
    rig.settle()
    until = rig.clock.now() + timedelta(seconds=120)
    markets_view = rig.sink.of(MarketsView)[-1]
    assert (markets_view.state, markets_view.markets, markets_view.error_name) == (
        LoadState.FAILED, (), ERROR_BLOCKED,
    )  # fmt: skip
    assert markets_view.blocked_until == until
    for coin in (AAA, BBB):
        view = rig.sink.coin(coin)[-1]
        assert (view.state, view.error_name, view.blocked_until) == (
            LoadState.FAILED, ERROR_BLOCKED, until,
        )  # fmt: skip
    new = codes(rig)[alerts_before:]  # 센 기준과 자르는 기준이 같다(A-27, A-28을 뺀 목록)
    assert [(code, name) for code, _coin, name in new] == [
        ("A-23", ERROR_BLOCKED),  # 일시 차단은 오류 멈춤이다(M3 명세 3.10.2)
        ("A-31", ERROR_BLOCKED),
    ]  # 고른 코인의 A-1은 생기지 않는다
    requests = len(rig.quote.requests)
    rig.sink.clear()
    rig.data.retry_candles()
    rig.data.refresh_markets()
    rig.settle()
    assert len(rig.quote.requests) == requests  # 차단 중에는 봉과 코인 목록 요청이 없다
    assert rig.sink.of(MarketsView)[-1] == markets_view.__class__(
        **{**markets_view.__dict__, "as_of": rig.sink.of(MarketsView)[-1].as_of}
    )
    assert all(rig.sink.coin(c)[-1].blocked_until == until for c in (AAA, BBB))
    assert len(codes(rig)) == alerts_before + 2  # A-23과 A-31 뒤로 A-31도 A-1도 더 생기지 않는다
    window = create_main_window(UiContext("0.0.1", "X", None))
    try:
        window.apply(SettingsView((), None, False))
        window.apply(markets_view)
        expected = blocked_markets_text(kst_when_text(until, markets_view.as_of))
        assert expected in shown_texts(window)  # 2의 3의 코인 목록 글
    finally:
        window.deleteLater()
    markets_before = len(rig.quote.market_requests())
    rig.advance(
        120
    )  # 오류 멈춤이라 저절로 받지 않는다. 풀면 받을 수 있고 코인 목록은 다시 부를 때 받는다
    assert len(rig.quote.requests) == requests
    rig.data.resume_error()
    rig.settle()
    rig.data.retry_candles()
    rig.settle()
    assert len(rig.quote.candle_requests()) > 0
    assert len(rig.quote.requests) > requests
    assert len(rig.quote.market_requests()) == markets_before
    rig.data.refresh_markets()
    rig.settle()
    assert len(rig.quote.market_requests()) == markets_before + 1


# --- B37: 1단계 보완 전의 DB에서 봉 단위를 고름 ------------------------------------------------


def old_db(tmp_path) -> None:
    day_start = NOW.replace(hour=0)
    candles = [
        *make_candles(AAA, DAY, 30, last_start=day_start),
        *make_candles(BBB, DAY, 30, last_start=day_start),
        *make_candles(AAA, HOUR, 150, last_start=THIS_HOUR - timedelta(hours=1)),
        *make_candles(BBB, HOUR, 150, last_start=THIS_HOUR - timedelta(hours=1)),
    ]
    earlier = NOW - timedelta(hours=1)
    prepare_db(
        tmp_path / "data" / "app.db",
        settings=SettingsView((AAA, BBB), 30, True),  # 1단계 보완 전에 마침("1"), 단위 없음
        candles=candles,
        fetched={AAA: earlier, BBB: earlier},
    )


@pytest.mark.parametrize(
    ("unit", "kept", "fresh"),
    [(MIN30, None, True), (HOUR, HOUR, False), (DAY, DAY, False)],
    ids=["30분봉", "60분봉", "일봉"],
)
@pytest.mark.scenario("B37")
def test_b37_choosing_a_unit_on_an_old_db_keeps_only_that_unit(
    rigs, tmp_path, unit, kept, fresh
) -> None:
    old_db(tmp_path)
    rig = rigs(fresh=False)
    # 가장 최근 봉(미완성)의 시작: 일봉은 UTC 00:00(한국 09:00)이 경계다(M1 명세의 봉 단위).
    latest = NOW.replace(hour=0) if unit is DAY else THIS_HOUR
    for coin in (AAA, BBB):
        fake = make_candles(coin, unit, 150, last_start=latest)
        assert unit is not DAY or all((c.start.hour, c.start.minute) == (0, 0) for c in fake)
        rig.quote.set_candles(coin, unit, fake)
    rig.start()
    assert rig.sink.of(CoinView) == []  # 봉 단위가 없는 동안 `CoinView`는 오지 않는다
    assert rig.quote.candle_requests() == []
    before = {u: len(rig.stored(AAA, u)) for u in (DAY, HOUR)}
    rig.data.save_settings((AAA, BBB), 30, first_setup=True, candle_unit=unit, candle_count=100)
    rig.settle()
    for other in (DAY, HOUR, MIN30):
        if other is not unit:
            assert rig.stored(AAA, other) == ()
            assert rig.stored(BBB, other) == ()
            assert rig.quote.candle_requests(unit=other) == []
    for coin in (AAA, BBB):
        views = rig.sink.coin(coin)
        first = views[0]
        if fresh:
            assert (first.state, first.fetched_at) == (LoadState.NO_DATA, None)
            assert first.stats is not None
            assert first.stats.bar_count == 0
            first_request = rig.quote.candle_requests(coin, unit)[0]
            assert (first_request.to, first_request.count) == (None, 200)  # 처음 받기
        else:
            assert first.fetched_at is not None  # 받은 때가 남아 있다
            assert first.stats is not None
            assert first.stats.bar_count >= min(100, before[kept] - 1)
            # 그 뒤의 받기: 저장된 마지막 봉부터 지금까지만 받는다(첫 요청은 200개보다 적다). 저장된
            # 완성된 봉이 고른 개수보다 적으면(일봉 30개) 이어서 거슬러 받는다.
            later = rig.quote.candle_requests(coin, unit)
            assert (later[0].to, later[0].count < 200) == (None, True)
            assert all(r.to is not None for r in later[1:])
    if not fresh:
        assert len(rig.stored(AAA, kept)) >= before[kept]
