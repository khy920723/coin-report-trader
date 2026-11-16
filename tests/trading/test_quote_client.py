"""`QuoteClient`의 시험: 나눠 받기, 받는 범위, 요청 한도, 실패. 가짜 시세와 가짜 시계로 한다.

시험 자료는 만든 숫자다(`fakes.quote.make_candles`).
"""

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fakes.clock import FakeClock
from fakes.quote import OK, FakeQuoteSource, make_candles, with_close

from coin_report_trader.core import Candle, CandleUnit, Market, QuoteError
from coin_report_trader.core.texts import (
    ERROR_BAD_RESPONSE,
    ERROR_BLOCKED,
    ERROR_NETWORK,
    ERROR_RATE_LIMIT,
)
from coin_report_trader.trading.quote import MAX_CANDLES, QuoteClient

M = "KRW-AAA"
DAY = CandleUnit.DAY
HOUR = CandleUnit.MINUTE_60
START = datetime(2026, 1, 5, 3, 0, tzinfo=UTC)
"""가짜 시계의 처음 시각(한국 시간 12:00). 이날의 일봉은 00:00 UTC에 시작했다."""
TODAY = datetime(2026, 1, 5, tzinfo=UTC)
THIS_HOUR = datetime(2026, 1, 5, 3, tzinfo=UTC)


def setup(*candles: Candle) -> tuple[QuoteClient, FakeQuoteSource, FakeClock]:
    clock = FakeClock(START)
    source = FakeQuoteSource(candles=candles, clock=clock)
    return QuoteClient(source, clock), source, clock


def assert_ascending_without_overlap(candles: tuple[Candle, ...]) -> None:
    starts = [c.start for c in candles]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)


# --- 코인 목록 -------------------------------------------------------------------------------


def test_list_markets_is_one_request() -> None:
    client, source, _ = setup()
    source.set_markets([Market("KRW-AAA", "가가", False), Market("KRW-BBB", "나나", True)])
    assert client.list_markets() == (
        Market("KRW-AAA", "가가", False),
        Market("KRW-BBB", "나나", True),
    )
    assert [r.kind for r in source.requests] == ["markets"]


def test_list_markets_error_is_raised_as_is() -> None:
    client, source, _ = setup()
    source.script(QuoteError(ERROR_BLOCKED, 120.0), kind="markets")
    with pytest.raises(QuoteError) as caught:
        client.list_markets()
    assert (caught.value.error_name, caught.value.retry_after) == (ERROR_BLOCKED, 120.0)
    assert len(source.requests) == 1


# --- 처음 받기 ------------------------------------------------------------------------------


def test_first_day_fetch_takes_every_candle_in_three_requests_for_450() -> None:
    candles = make_candles(M, DAY, 450, last_start=TODAY)
    client, source, _ = setup(*candles)
    got = client.fetch_candles(M, DAY, last_start=None)
    assert got == candles
    requests = source.candle_requests(M, DAY)
    assert len(requests) == 3
    assert all(r.count == MAX_CANDLES for r in requests)
    # 가장 최근부터 거슬러 간다: 다음 요청의 `to`는 앞 응답에서 가장 오래된 봉의 시작 시각이다.
    assert [r.to for r in requests] == [None, candles[250].start, candles[50].start]


@pytest.mark.parametrize(("total", "requests"), [(0, 1), (1, 1), (199, 1), (200, 2), (400, 3)])
def test_first_day_fetch_stops_at_a_short_answer(total: int, requests: int) -> None:
    candles = make_candles(M, DAY, total, last_start=TODAY) if total else ()
    client, source, _ = setup(*candles)
    assert client.fetch_candles(M, DAY, last_start=None) == candles
    assert len(source.candle_requests()) == requests


def test_first_hour_fetch_is_one_request_for_the_latest_200() -> None:
    candles = make_candles(M, HOUR, 300, last_start=THIS_HOUR)
    client, source, _ = setup(*candles)
    got = client.fetch_candles(M, HOUR, last_start=None)
    assert got == candles[100:]
    requests = source.candle_requests()
    assert len(requests) == 1
    assert (requests[0].unit, requests[0].to, requests[0].count) == (HOUR, None, 200)


def test_first_fetch_keeps_the_incomplete_last_candle() -> None:
    # 만들어지는 중인 마지막 봉(오늘 일봉)도 응답에 오는 대로 돌려준다. 완성 판정은 `stats`가 한다.
    candles = make_candles(M, DAY, 3, last_start=TODAY)
    client, _, _ = setup(*candles)
    assert client.fetch_candles(M, DAY, last_start=None)[-1].start == TODAY


def test_markets_and_units_do_not_mix() -> None:
    a_day = make_candles(M, DAY, 5, last_start=TODAY)
    b_day = make_candles("KRW-BBB", DAY, 7, last_start=TODAY)
    a_hour = make_candles(M, HOUR, 9, last_start=THIS_HOUR)
    client, _, _ = setup(*a_day, *b_day, *a_hour)
    assert client.fetch_candles(M, DAY, last_start=None) == a_day
    assert client.fetch_candles("KRW-BBB", DAY, last_start=None) == b_day
    assert client.fetch_candles(M, HOUR, last_start=None) == a_hour


# --- 그 뒤의 받기 ---------------------------------------------------------------------------


def test_later_day_fetch_takes_only_from_the_last_stored_candle() -> None:
    candles = make_candles(M, DAY, 450, last_start=TODAY)
    client, source, clock = setup(*candles)
    stored = client.fetch_candles(M, DAY, last_start=None)
    last = stored[-1]

    # 이틀이 지나 새 일봉 둘이 생기고, 저장된 마지막 일봉의 종가가 바뀌었다.
    clock.advance(timedelta(days=2).total_seconds())
    changed = with_close(last, last.close + Decimal(3))
    newer = make_candles(M, DAY, 3, last_start=TODAY + timedelta(days=2))[1:]
    source.put_candles(changed, *newer)
    before = len(source.requests)

    got = client.fetch_candles(M, DAY, last_start=last.start)
    assert got == (changed, *newer)
    later = source.requests[before:]
    assert len(later) == 1
    assert (later[0].to, later[0].count) == (None, 3)


def test_later_fetch_splits_more_than_200_candles() -> None:
    candles = make_candles(M, HOUR, 500, last_start=THIS_HOUR)
    last_start = candles[49].start  # 지금까지 451개
    client, source, _ = setup(*candles)
    got = client.fetch_candles(M, HOUR, last_start=last_start)
    assert got == candles[49:]
    assert got[0].start == last_start
    assert_ascending_without_overlap(got)
    requests = source.candle_requests()
    assert [r.count for r in requests] == [200, 200, 51]
    assert [r.to for r in requests] == [None, candles[300].start, candles[100].start]
    assert all(r.count <= MAX_CANDLES for r in requests)


def test_later_fetch_with_gaps_in_the_candles() -> None:
    # 체결이 없던 구간에는 봉이 없다. 있는 봉만 받고, last_start에 닿을 때까지 거슬러 간다.
    full = make_candles(M, HOUR, 600, last_start=THIS_HOUR)
    kept = tuple(c for i, c in enumerate(full) if i % 3 != 1)
    last_start = full[30].start
    client, source, _ = setup(*kept)
    got = client.fetch_candles(M, HOUR, last_start=last_start)
    assert got == tuple(c for c in kept if c.start >= last_start)
    assert_ascending_without_overlap(got)
    assert all(r.count <= MAX_CANDLES for r in source.candle_requests())


def test_later_fetch_when_the_stored_candle_is_gone() -> None:
    # 저장된 마지막 봉 뒤의 봉만 있으면(그 앞이 비었으면) 짧은 응답에서 멈춘다.
    candles = make_candles(M, DAY, 4, last_start=TODAY)
    client, source, _ = setup(*candles)
    got = client.fetch_candles(M, DAY, last_start=TODAY - timedelta(days=10))
    assert got == candles
    assert len(source.candle_requests()) == 1


def test_later_fetch_with_nothing_new_still_asks_once() -> None:
    candles = make_candles(M, DAY, 5, last_start=TODAY)
    client, source, _ = setup(*candles)
    got = client.fetch_candles(M, DAY, last_start=TODAY)
    assert got == (candles[-1],)
    assert [(r.to, r.count) for r in source.candle_requests()] == [(None, 1)]


def test_later_fetch_when_the_clock_is_behind_the_stored_candle() -> None:
    # PC 시계가 뒤로 갔어도 요청은 하나 이상 내고 last_start의 봉을 다시 받는다.
    candles = make_candles(M, DAY, 5, last_start=TODAY)
    client, source, clock = setup(*candles)
    clock.set_now(TODAY - timedelta(days=3))
    got = client.fetch_candles(M, DAY, last_start=TODAY)
    assert got == (candles[-1],)
    assert len(source.candle_requests()) == 1


def test_last_start_needs_a_time_zone() -> None:
    client, source, _ = setup()
    with pytest.raises(ValueError, match="last_start"):
        client.fetch_candles(M, DAY, last_start=datetime(2026, 1, 5))
    assert source.requests == []


# --- 요청 한도 ------------------------------------------------------------------------------


def test_twelve_requests_keep_more_than_one_second_between_every_sixth() -> None:
    candles = make_candles(M, DAY, 2300, last_start=TODAY)  # 200개씩 11번과 100개 1번
    client, source, clock = setup(*candles)
    assert client.fetch_candles(M, DAY, last_start=None) == candles
    times = [r.monotonic for r in source.requests]
    assert len(times) == 12
    assert times == sorted(times)
    for i in range(len(times) - 5):
        assert times[i + 5] - times[i] > 1.0, i
    # 길이 1초인 어느 구간(양 끝 포함)에도 5번을 넘지 않는다.
    for t in times:
        assert sum(1 for u in times if t <= u <= t + 1.0) <= 5
    # 처음 다섯은 기다리지 않는다. 기다림은 시계의 sleep으로만 한다.
    assert times[:5] == [times[0]] * 5
    assert clock.sleeps
    assert all(0 < s <= 1.1 for s in clock.sleeps)


def test_the_limit_counts_markets_and_candles_together() -> None:
    client, source, _ = setup(*make_candles(M, DAY, 3, last_start=TODAY))
    for _ in range(3):
        client.list_markets()
    for _ in range(3):
        client.fetch_candles(M, DAY, last_start=None)
    times = [r.monotonic for r in source.requests]
    assert times[5] - times[0] > 1.0


def test_no_real_sleep_or_monotonic(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object) -> None:
        raise AssertionError("time 모듈의 시계를 직접 불렀다")

    client, _, _ = setup(*make_candles(M, DAY, 1300, last_start=TODAY))
    monkeypatch.setattr(time, "sleep", forbidden)
    monkeypatch.setattr(time, "monotonic", forbidden)
    assert len(client.fetch_candles(M, DAY, last_start=None)) == 1300


# --- 실패 -----------------------------------------------------------------------------------


def test_rate_limit_error_is_not_retried_on_the_spot() -> None:
    client, source, clock = setup(*make_candles(M, DAY, 3, last_start=TODAY))
    source.script(QuoteError(ERROR_RATE_LIMIT, 1.0))
    with pytest.raises(QuoteError) as caught:
        client.fetch_candles(M, DAY, last_start=None)
    assert caught.value.error_name == ERROR_RATE_LIMIT
    assert len(source.requests) == 1
    assert clock.sleeps == []


@pytest.mark.parametrize("unit", [DAY, HOUR])
def test_blocked_error_carries_retry_after(unit: CandleUnit) -> None:
    client, source, _ = setup()
    source.script(QuoteError(ERROR_BLOCKED, 120.0))
    with pytest.raises(QuoteError) as caught:
        client.fetch_candles(M, unit, last_start=None)
    assert (caught.value.error_name, caught.value.retry_after) == (ERROR_BLOCKED, 120.0)


def test_failure_in_the_middle_returns_no_candles() -> None:
    candles = make_candles(M, DAY, 450, last_start=TODAY)
    client, source, _ = setup(*candles)
    source.script(OK, QuoteError(ERROR_NETWORK), kind="candles", market=M, unit=DAY)
    with pytest.raises(QuoteError) as caught:
        client.fetch_candles(M, DAY, last_start=None)
    assert caught.value.error_name == ERROR_NETWORK
    assert len(source.requests) == 2  # 셋째 요청은 내지 않는다
    # 다음 받기는 다시 처음 받기로 모두 받는다.
    assert client.fetch_candles(M, DAY, last_start=None) == candles


def test_failure_in_the_middle_of_a_later_fetch() -> None:
    candles = make_candles(M, HOUR, 500, last_start=THIS_HOUR)
    client, source, _ = setup(*candles)
    source.script(OK, OK, QuoteError(ERROR_NETWORK), unit=HOUR)
    with pytest.raises(QuoteError):
        client.fetch_candles(M, HOUR, last_start=candles[49].start)
    assert len(source.requests) == 3


class BrokenSource:
    """약속을 어기는 시세: 늘 같은 봉 200개를 준다(`to`를 보지 않는다)."""

    def __init__(self, candles: tuple[Candle, ...]) -> None:
        self.candles = candles
        self.calls = 0

    def list_markets(self) -> tuple[Market, ...]:
        return ()

    def get_candles(
        self, market: str, unit: CandleUnit, *, to: datetime | None, count: int
    ) -> tuple[Candle, ...]:
        self.calls += 1
        return tuple(reversed(self.candles))[:count]


@pytest.mark.parametrize(
    "candles",
    [
        make_candles(M, DAY, 200, last_start=TODAY),  # 둘째 요청에서 `to` 뒤의 봉이 온다
        make_candles("KRW-BBB", DAY, 200, last_start=TODAY),  # 다른 마켓
        make_candles(M, HOUR, 200, last_start=THIS_HOUR),  # 다른 단위
    ],
)
def test_a_source_breaking_the_contract_gives_bad_response(candles: tuple[Candle, ...]) -> None:
    clock = FakeClock(START)
    source = BrokenSource(candles)
    client = QuoteClient(source, clock)
    with pytest.raises(QuoteError) as caught:
        client.fetch_candles(M, DAY, last_start=None)
    assert caught.value.error_name == ERROR_BAD_RESPONSE
    assert source.calls <= 2


def test_more_candles_than_asked_is_bad_response() -> None:
    class TooMany(BrokenSource):
        def get_candles(self, market, unit, *, to, count):  # type: ignore[override]
            return tuple(reversed(self.candles))

    client = QuoteClient(TooMany(make_candles(M, DAY, 5, last_start=TODAY)), FakeClock(START))
    with pytest.raises(QuoteError) as caught:
        client.fetch_candles(M, DAY, last_start=TODAY)  # count 1을 묻는다
    assert caught.value.error_name == ERROR_BAD_RESPONSE
