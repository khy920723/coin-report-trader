"""`QuoteClient.fetch_recent`(1단계 보완의 받기, M1 명세 3.5.3)의 시험.

처음 받기, 그 뒤의 받기, 거슬러 받기와 `reached_oldest`, 네 단위, 요청 한도, 실패를 본다.
가짜 시세와 가짜 시계로 하고, 시험 자료는 만든 숫자다(`fakes.quote.make_candles`).
"""

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fakes.clock import FakeClock
from fakes.quote import CANDLES, OK, FakeQuoteSource, make_candles, with_close

from coin_report_trader.core import CANDLE_UNIT_LENGTHS, Candle, CandleUnit, Market, QuoteError
from coin_report_trader.core.texts import (
    ERROR_BAD_RESPONSE,
    ERROR_BLOCKED,
    ERROR_NETWORK,
    ERROR_RATE_LIMIT,
)
from coin_report_trader.trading.quote import MAX_CANDLES, QuoteClient, RecentCandles

M = "KRW-AAA"
HOUR = CandleUnit.MINUTE_60
NOW = datetime(2026, 1, 5, 3, 20, tzinfo=UTC)
"""가짜 시계의 처음 시각.

60분봉 03:00, 30분봉 03:00, 240분봉 00:00, 일봉 00:00에 시작한 봉이 미완성이다.
"""

CURRENT_START = {
    CandleUnit.MINUTE_30: datetime(2026, 1, 5, 3, 0, tzinfo=UTC),
    CandleUnit.MINUTE_60: datetime(2026, 1, 5, 3, 0, tzinfo=UTC),
    CandleUnit.MINUTE_240: datetime(2026, 1, 5, 0, 0, tzinfo=UTC),
    CandleUnit.DAY: datetime(2026, 1, 5, 0, 0, tzinfo=UTC),
}
"""단위마다 `NOW`에 만들어지는 중인(미완성) 봉의 시작 시각."""


def setup(*candles: Candle) -> tuple[QuoteClient, FakeQuoteSource, FakeClock]:
    clock = FakeClock(NOW)
    source = FakeQuoteSource(candles=candles, clock=clock)
    return QuoteClient(source, clock), source, clock


def recent(client: QuoteClient, unit: CandleUnit = HOUR, **kwargs: object) -> RecentCandles:
    args: dict[str, object] = {
        "count": 200,
        "last_start": None,
        "first_start": None,
        "missing": 0,
        **kwargs,
    }
    return client.fetch_recent(M, unit, **args)  # type: ignore[arg-type]


def with_current(unit: CandleUnit, total: int) -> tuple[Candle, ...]:
    """가장 최근 봉이 미완성인 이어진 봉 `total`개."""
    return make_candles(M, unit, total, last_start=CURRENT_START[unit])


def assert_ascending_without_overlap(candles: tuple[Candle, ...]) -> None:
    starts = [c.start for c in candles]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)


def test_recent_candles_is_frozen() -> None:
    result = RecentCandles(candles=(), reached_oldest=False)
    with pytest.raises(AttributeError):
        result.reached_oldest = True  # type: ignore[misc]


# --- 처음 받기 ------------------------------------------------------------------------------


@pytest.mark.parametrize(("count", "requests"), [(100, 1), (200, 2), (500, 3)])
def test_first_fetch_requests_per_count(count: int, requests: int) -> None:
    # 응답에 미완성 봉 하나가 함께 오므로 N 100은 1번, 200은 2번, 500은 3번이다(3.5.3).
    candles = with_current(HOUR, 600)
    client, source, _ = setup(*candles)
    result = recent(client, count=count)
    sent = source.candle_requests()
    assert len(sent) == requests
    assert all(r.count == MAX_CANDLES and r.unit is HOUR for r in sent)
    assert result.reached_oldest is False
    assert result.candles == candles[600 - 200 * requests :]
    assert_ascending_without_overlap(result.candles)


def test_first_fetch_of_600_hours_for_500_is_three_requests() -> None:
    # B8의 꼴: 60분봉 600개(가장 최근 봉은 미완성), N 500.
    candles = with_current(HOUR, 600)
    client, source, _ = setup(*candles)
    result = recent(client, count=500)
    assert len(source.requests) == 3
    assert [r.to for r in source.requests] == [None, candles[400].start, candles[200].start]
    assert result.candles == candles
    assert {r.unit for r in source.requests} == {HOUR}


def test_first_fetch_stops_when_the_exchange_gives_fewer() -> None:
    # B31의 꼴: 30분봉 150개(가장 최근 봉은 미완성), N 200.
    candles = with_current(CandleUnit.MINUTE_30, 150)
    client, source, _ = setup(*candles)
    result = recent(client, CandleUnit.MINUTE_30, count=200)
    assert len(source.requests) == 1
    assert result.reached_oldest is True
    assert result.candles == candles


def test_first_fetch_counts_only_completed_candles() -> None:
    # 200개가 모두 완성된 봉이면 N 200은 한 번에 모인다.
    completed = make_candles(M, HOUR, 400, last_start=CURRENT_START[HOUR] - timedelta(hours=1))
    client, source, _ = setup(*completed)
    result = recent(client, count=200)
    assert len(source.requests) == 1
    assert result.reached_oldest is False
    assert result.candles == completed[200:]


def test_first_fetch_of_exactly_200_with_an_incomplete_one_asks_again() -> None:
    # 받은 200개 가운데 완성된 봉이 199개라 다시 거슬러 묻고, 빈 응답이라 멈춘다.
    candles = with_current(HOUR, 200)
    client, source, _ = setup(*candles)
    result = recent(client, count=200)
    assert [r.to for r in source.requests] == [None, candles[0].start]
    assert result.reached_oldest is True
    assert result.candles == candles


def test_first_fetch_with_no_candles() -> None:
    client, source, _ = setup()
    result = recent(client, count=100)
    assert result == RecentCandles(candles=(), reached_oldest=True)
    assert len(source.requests) == 1


def test_completion_follows_the_clock() -> None:
    # 시각이 단위의 길이만큼 흐르면 미완성이던 봉도 완성된 봉으로 센다.
    candles = with_current(HOUR, 400)
    client, source, clock = setup(*candles)
    clock.advance(CANDLE_UNIT_LENGTHS[HOUR].total_seconds())
    recent(client, count=200)
    assert len(source.requests) == 1


@pytest.mark.parametrize("unit", list(CandleUnit))
def test_a_candle_ending_exactly_now_is_completed(unit: CandleUnit) -> None:
    # 3.5의 완성: 시작 시각 + 단위의 길이가 지금보다 뒤일 때만 미완성이다. 꼭 지금이면 완성이다.
    candles = with_current(unit, 400)
    client, source, clock = setup(*candles)
    end = CURRENT_START[unit] + CANDLE_UNIT_LENGTHS[unit]
    clock.advance((end - NOW).total_seconds())
    assert clock.now() == end
    result = recent(client, unit, count=200)
    assert len(source.requests) == 1  # 받은 200개가 모두 완성된 봉이다
    assert result.candles == candles[200:]
    assert result.reached_oldest is False


def test_a_candle_ending_just_after_now_is_not_completed() -> None:
    # 끝나는 때보다 조금 앞이면 아직 미완성이라 N 200에 한 번 더 묻는다.
    candles = with_current(HOUR, 400)
    client, source, clock = setup(*candles)
    end = CURRENT_START[HOUR] + CANDLE_UNIT_LENGTHS[HOUR]
    clock.advance((end - NOW).total_seconds() - 0.001)
    recent(client, count=200)
    assert len(source.requests) == 2


@pytest.mark.parametrize("unit", list(CandleUnit))
def test_first_fetch_for_each_of_the_four_units(unit: CandleUnit) -> None:
    candles = with_current(unit, 250)
    others = [with_current(u, 250) for u in CandleUnit if u is not unit]
    client, source, _ = setup(*candles, *[c for group in others for c in group])
    result = recent(client, unit, count=200)
    assert result.candles == candles
    assert result.reached_oldest is True  # 250개라 둘째 요청이 50개다
    assert {r.unit for r in source.requests} == {unit}
    assert all(c.unit is unit for c in result.candles)


def test_first_fetch_ignores_missing_and_first_start() -> None:
    candles = with_current(HOUR, 300)
    client, source, _ = setup(*candles)
    result = recent(client, count=100, missing=50, first_start=candles[0].start)
    assert len(source.requests) == 1
    assert result.candles == candles[100:]


# --- 그 뒤의 받기 ---------------------------------------------------------------------------


def test_later_fetch_takes_new_candles_and_the_last_stored_one() -> None:
    # B9의 꼴: 새 60분봉 둘이 생기고 마지막으로 저장된 봉의 종가가 바뀐다. 요청은 한 번.
    candles = with_current(HOUR, 600)
    client, source, clock = setup(*candles)
    stored = recent(client, count=500).candles
    last = stored[-1]
    clock.advance(timedelta(hours=2).total_seconds())
    changed = with_close(last, last.close + Decimal(7))
    newer = make_candles(M, HOUR, 3, last_start=last.start + timedelta(hours=2))[1:]
    source.put_candles(changed, *newer)
    before = len(source.requests)

    result = recent(client, count=500, last_start=last.start, first_start=stored[0].start)
    assert result == RecentCandles(candles=(changed, *newer), reached_oldest=False)
    later = source.requests[before:]
    assert [(r.to, r.count) for r in later] == [(None, 3)]


def test_later_fetch_splits_more_than_200_candles() -> None:
    candles = with_current(CandleUnit.MINUTE_30, 500)
    client, source, _ = setup(*candles)
    result = recent(
        client,
        CandleUnit.MINUTE_30,
        last_start=candles[49].start,
        first_start=candles[0].start,
    )
    assert result.candles == candles[49:]
    assert [r.count for r in source.requests] == [200, 200, 51]
    assert result.reached_oldest is False


def test_later_fetch_with_missing_zero_never_reaches_back() -> None:
    # B31의 꼴: `reached_oldest`가 참인 코인에는 자료 스레드가 `missing` 0을 넘긴다.
    candles = with_current(CandleUnit.MINUTE_30, 150)
    client, source, _ = setup(*candles)
    result = recent(
        client,
        CandleUnit.MINUTE_30,
        last_start=candles[-1].start,
        first_start=candles[0].start,
        missing=0,
    )
    assert len(source.requests) == 1
    assert source.requests[0].to is None
    assert result == RecentCandles(candles=(candles[-1],), reached_oldest=False)


# --- 거슬러 받기 ----------------------------------------------------------------------------


def test_reaching_back_fills_what_is_missing() -> None:
    # B44의 꼴: 저장된 완성된 봉 120개(그리고 미완성 하나), 그 앞에 400개, 봉 개수 500.
    candles = with_current(HOUR, 521)
    stored = candles[400:]
    client, source, _ = setup(*candles)
    result = recent(
        client,
        count=500,
        last_start=stored[-1].start,
        first_start=stored[0].start,
        missing=380,
    )
    sent = [(r.to, r.count) for r in source.requests]
    assert sent == [(None, 1), (stored[0].start, 200), (candles[200].start, 180)]
    assert result.candles == (*candles[20:400], candles[-1])
    assert result.reached_oldest is False
    assert_ascending_without_overlap(result.candles)
    assert all(r.count <= MAX_CANDLES for r in source.requests)


def test_reaching_back_stops_where_the_exchange_gives_fewer() -> None:
    candles = with_current(HOUR, 371)
    stored = candles[250:]
    client, source, _ = setup(*candles)
    result = recent(
        client,
        count=500,
        last_start=stored[-1].start,
        first_start=stored[0].start,
        missing=380,
    )
    assert [r.count for r in source.requests] == [1, 200, 180]
    assert result.reached_oldest is True
    assert result.candles == (*candles[:250], candles[-1])


def test_reaching_back_exactly_200_is_one_request() -> None:
    candles = with_current(HOUR, 500)
    client, source, _ = setup(*candles)
    result = recent(
        client,
        count=500,
        last_start=candles[-1].start,
        first_start=candles[300].start,
        missing=200,
    )
    assert [r.count for r in source.requests] == [1, 200]
    assert result.reached_oldest is False
    assert result.candles == (*candles[100:300], candles[-1])


def test_reaching_back_with_nothing_older() -> None:
    candles = with_current(HOUR, 50)
    client, source, _ = setup(*candles)
    result = recent(
        client,
        last_start=candles[-1].start,
        first_start=candles[0].start,
        missing=151,
    )
    assert [(r.to, r.count) for r in source.requests] == [(None, 1), (candles[0].start, 151)]
    assert result == RecentCandles(candles=(candles[-1],), reached_oldest=True)


# --- 인자 -----------------------------------------------------------------------------------


def test_reaching_back_needs_first_start() -> None:
    client, source, _ = setup(*with_current(HOUR, 5))
    with pytest.raises(ValueError, match="first_start"):
        recent(client, last_start=CURRENT_START[HOUR], first_start=None, missing=10)
    assert source.requests == []


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"count": 0}, ValueError),
        ({"count": True}, TypeError),
        ({"count": 100.0}, TypeError),
        ({"missing": -1}, ValueError),
        ({"missing": False}, TypeError),
        ({"last_start": datetime(2026, 1, 5)}, ValueError),
        (
            {"last_start": CURRENT_START[HOUR], "first_start": datetime(2026, 1, 1), "missing": 1},
            ValueError,
        ),
    ],
)
def test_bad_arguments_send_nothing(kwargs: dict[str, object], error: type) -> None:
    client, source, _ = setup(*with_current(HOUR, 5))
    with pytest.raises(error):
        recent(client, **kwargs)
    assert source.requests == []


def test_bad_unit_sends_nothing() -> None:
    client, source, _ = setup()
    with pytest.raises(ValueError, match="week"):
        recent(client, "week")  # type: ignore[arg-type]
    assert source.requests == []


# --- 요청 한도와 실패 -----------------------------------------------------------------------


def test_twelve_requests_keep_more_than_one_second_between_every_sixth() -> None:
    candles = with_current(HOUR, 2400)
    client, source, clock = setup(*candles)
    result = recent(
        client,
        count=500,
        last_start=candles[-1].start,
        first_start=candles[2199].start,
        missing=2199,  # 그 뒤의 받기 1번과 거슬러 받기 11번(200개씩 10번과 199개 1번)
    )
    times = [r.monotonic for r in source.requests]
    assert len(times) == 12
    for i in range(len(times) - 5):
        assert times[i + 5] - times[i] > 1.0, i
    assert clock.sleeps
    assert result.candles == (*candles[:2199], candles[-1])
    assert result.reached_oldest is False


def test_rate_limit_error_is_not_retried_on_the_spot() -> None:
    client, source, clock = setup(*with_current(HOUR, 300))
    source.script(QuoteError(ERROR_RATE_LIMIT, 1.0))
    with pytest.raises(QuoteError) as caught:
        recent(client, count=200)
    assert caught.value.error_name == ERROR_RATE_LIMIT
    assert len(source.requests) == 1
    assert clock.sleeps == []


def test_failure_in_the_second_request_of_a_first_fetch() -> None:
    # B35의 꼴: 60분봉 처음 받기(N 500)의 둘째 요청에서 "네트워크 끊김". 봉을 돌려주지 않는다.
    candles = with_current(HOUR, 600)
    client, source, _ = setup(*candles)
    source.script(OK, QuoteError(ERROR_NETWORK), kind=CANDLES, market=M, unit=HOUR)
    with pytest.raises(QuoteError) as caught:
        recent(client, count=500)
    assert caught.value.error_name == ERROR_NETWORK
    assert len(source.requests) == 2
    # 다음 받기는 다시 처음 받기로 받는다.
    assert recent(client, count=500).candles == candles


def test_failure_while_reaching_back_returns_nothing() -> None:
    candles = with_current(HOUR, 521)
    client, source, _ = setup(*candles)
    source.script(OK, OK, QuoteError(ERROR_NETWORK))
    with pytest.raises(QuoteError):
        recent(
            client,
            count=500,
            last_start=candles[-1].start,
            first_start=candles[400].start,
            missing=380,
        )
    assert len(source.requests) == 3


def test_old_fetch_is_gone() -> None:
    # 1단계 보완 전의 받기(`fetch_candles`, 일봉의 가장 오래된 봉까지의 처음 받기)는 43번이 지웠다.
    assert not hasattr(QuoteClient, "fetch_candles")
    assert not hasattr(QuoteClient, "_fetch_all")


def test_the_limit_counts_markets_and_candles_together() -> None:
    client, source, _ = setup(*with_current(HOUR, 3))
    for _ in range(3):
        client.list_markets()
    for _ in range(3):
        recent(client, count=100)
    times = [r.monotonic for r in source.requests]
    assert len(times) == 6
    assert times[5] - times[0] > 1.0


def test_no_real_sleep_or_monotonic(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object) -> None:
        raise AssertionError("time 모듈의 시계를 직접 불렀다")

    candles = with_current(HOUR, 1300)
    client, _, clock = setup(*candles)
    monkeypatch.setattr(time, "sleep", forbidden)
    monkeypatch.setattr(time, "monotonic", forbidden)
    result = recent(
        client,
        count=500,
        last_start=candles[-1].start,
        first_start=candles[1200].start,
        missing=1199,
    )
    assert result.candles == (*candles[1:1200], candles[-1])
    assert clock.sleeps


@pytest.mark.parametrize("unit", list(CandleUnit))
def test_blocked_error_carries_retry_after(unit: CandleUnit) -> None:
    client, source, _ = setup()
    source.script(QuoteError(ERROR_BLOCKED, 120.0))
    with pytest.raises(QuoteError) as caught:
        recent(client, unit, count=200)
    assert (caught.value.error_name, caught.value.retry_after) == (ERROR_BLOCKED, 120.0)


def test_later_fetch_with_gaps_in_the_candles() -> None:
    # 체결이 없던 구간에는 봉이 없다. 있는 봉만 받고, last_start에 닿을 때까지 거슬러 간다.
    full = with_current(HOUR, 600)
    kept = tuple(c for i, c in enumerate(full) if i % 3 != 1)
    last_start = full[30].start
    client, source, _ = setup(*kept)
    result = recent(client, last_start=last_start, first_start=kept[0].start)
    assert result.candles == tuple(c for c in kept if c.start >= last_start)
    assert_ascending_without_overlap(result.candles)
    assert all(r.count <= MAX_CANDLES for r in source.candle_requests())


def test_later_fetch_when_the_clock_is_behind_the_stored_candle() -> None:
    # PC 시계가 뒤로 갔어도 요청은 하나 이상 내고 last_start의 봉을 다시 받는다.
    candles = with_current(HOUR, 5)
    client, source, clock = setup(*candles)
    clock.set_now(CURRENT_START[HOUR] - timedelta(days=3))
    result = recent(client, last_start=candles[-1].start, first_start=candles[0].start)
    assert result.candles == (candles[-1],)
    assert len(source.candle_requests()) == 1


class BrokenSource:
    """약속을 어기는 시세: 늘 같은 봉들을 준다(`to`를 보지 않는다)."""

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
        with_current(HOUR, 200),  # 둘째 요청에서 `to` 뒤의 봉이 온다
        make_candles("KRW-BBB", HOUR, 200, last_start=CURRENT_START[HOUR]),  # 다른 마켓
        with_current(CandleUnit.DAY, 200),  # 다른 단위
    ],
)
def test_a_source_breaking_the_contract_gives_bad_response(candles: tuple[Candle, ...]) -> None:
    source = BrokenSource(candles)
    client = QuoteClient(source, FakeClock(NOW))
    with pytest.raises(QuoteError) as caught:
        recent(client, count=500)
    assert caught.value.error_name == ERROR_BAD_RESPONSE
    assert source.calls <= 2


def test_more_candles_than_asked_is_bad_response() -> None:
    class TooMany(BrokenSource):
        def get_candles(self, market, unit, *, to, count):  # type: ignore[override]
            return tuple(reversed(self.candles))

    candles = with_current(HOUR, 5)
    client = QuoteClient(TooMany(candles), FakeClock(NOW))
    with pytest.raises(QuoteError) as caught:
        recent(client, last_start=candles[-1].start, first_start=candles[0].start)  # count 1
    assert caught.value.error_name == ERROR_BAD_RESPONSE
