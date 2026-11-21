"""봉 통계의 불변식(M1 명세 3.5, 3.6): `compute_candle_stats`.

1단계 보완에서 기간과 지표의 항목이 바뀔 수 있으므로 값 자체는 굳히지 않는다. 항목이 바뀌어도 남는
성질(봉의 차례와 중복, 미완성 봉, 가격의 배수, 비율의 범위)만 본다.
"""

import dataclasses
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from coin_report_trader.core import Candle, CandleUnit
from coin_report_trader.stats import compute_candle_stats

MARKET = "KRW-AAA"
NOW = datetime(2026, 1, 5, 3, 0, tzinfo=UTC)
STEP = {CandleUnit.DAY: timedelta(days=1), CandleUnit.MINUTE_60: timedelta(hours=1)}
PRICE_FIELDS = ("open_price", "close_price", "high", "low")
"""`PeriodStats`에서 가격을 그대로 담는 칸. 나머지 칸은 가격의 비율이거나 수다."""

prices = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1000000"), places=2)


@st.composite
def candle(draw: st.DrawFn, unit: CandleUnit, slot: int) -> Candle:
    """`slot`은 `NOW` 직전에 완성된 봉에서 거슬러 오른 칸 수다(0이 가장 최근에 완성된 봉)."""
    low = draw(prices)
    high = low + draw(st.decimals(min_value=0, max_value=10000, places=2))
    return Candle(
        market=MARKET,
        unit=unit,
        start=NOW - STEP[unit] * (slot + 1),
        open=draw(st.sampled_from([low, high, (low + high) / 2])),
        high=high,
        low=low,
        close=draw(st.sampled_from([low, high, (low + high) / 2])),
        volume=draw(prices),
        value=draw(prices),
    )


@st.composite
def candles(draw: st.DrawFn) -> list[Candle]:
    """일봉과 60분봉. 단위마다 시작 시각은 서로 다르고 모두 완성된 봉이다."""
    found: list[Candle] = []
    for unit, most in ((CandleUnit.DAY, 400), (CandleUnit.MINUTE_60, 200)):
        slots = draw(st.lists(st.integers(0, most), max_size=40, unique=True))
        found.extend(draw(candle(unit, slot)) for slot in slots)
    return found


def scaled(c: Candle, factor: Decimal) -> Candle:
    return dataclasses.replace(
        c, open=c.open * factor, high=c.high * factor, low=c.low * factor, close=c.close * factor
    )


@given(candles(), st.randoms(use_true_random=False))
def test_the_order_of_the_candles_does_not_change_the_result(found, rnd: random.Random):
    """봉을 넘기는 차례가 달라도 결과가 같다."""
    shuffled = list(found)
    rnd.shuffle(shuffled)
    assert compute_candle_stats(shuffled, NOW) == compute_candle_stats(found, NOW)


@given(candles(), st.data())
def test_a_candle_received_twice_counts_once(found, data):
    """같은 봉이 두 번 들어와도(같은 값) 한 번으로 센다."""
    again = data.draw(st.lists(st.sampled_from(found), max_size=10)) if found else []
    assert compute_candle_stats([*again, *found], NOW) == compute_candle_stats(found, NOW)


@given(candles(), st.data())
def test_a_later_copy_of_the_same_start_wins(found, data):
    """같은 단위에서 시작 시각이 같은 봉은 나중에 온 것을 쓴다."""
    if not found:
        return
    target = data.draw(st.sampled_from(found))
    replaced = dataclasses.replace(target, close=target.close + 1, high=target.high + 1)
    later = [c if c is not target else replaced for c in found]
    assert compute_candle_stats([*found, replaced], NOW) == compute_candle_stats(later, NOW)


@given(candles(), st.data())
def test_candles_that_are_not_complete_yet_are_not_used(found, data):
    """`now`에 아직 완성되지 않은 봉은 기간과 지표에 들지 않는다. 미완성 일봉이 있으면 표시한다."""
    unit = data.draw(st.sampled_from([CandleUnit.DAY, CandleUnit.MINUTE_60]))  # 옛 함수는 둘만 안다
    ahead = data.draw(st.integers(0, 3))
    building = data.draw(candle(unit, 0))
    building = dataclasses.replace(building, start=NOW - STEP[unit] + timedelta(minutes=1)
                                   + STEP[unit] * ahead)  # fmt: skip
    base = compute_candle_stats(found, NOW)
    more = compute_candle_stats([*found, building], NOW)
    assert more.periods == base.periods
    assert more.indicator == base.indicator
    if unit is CandleUnit.DAY:
        latest = max((c.start for c in found if c.unit is CandleUnit.DAY), default=None)
        assert more.last_incomplete is (latest is None or building.start > latest)


@given(candles(), st.integers(-3, 4))
def test_multiplying_every_price_by_the_same_power_of_ten_keeps_every_ratio(found, power):
    """모든 가격을 같은 배수(10의 거듭제곱)로 바꾸면 비율과 수는 그대로, 가격만 그 배수다."""
    factor = Decimal(10) ** power
    base = compute_candle_stats(found, NOW)
    moved = compute_candle_stats([scaled(c, factor) for c in found], NOW)
    assert moved.indicator == base.indicator
    assert moved.last_incomplete == base.last_incomplete
    for before, after in zip(base.periods, moved.periods, strict=True):
        for name in (f.name for f in dataclasses.fields(before)):
            old, new = getattr(before, name), getattr(after, name)
            if name in PRICE_FIELDS and old is not None:
                assert new == old * factor, name
            else:
                assert new == old, name


@given(candles())
def test_ratios_stay_in_their_ranges(found):
    """값이 맞는 봉(저가 ≤ 시가·종가 ≤ 고가, 가격 > 0)에서 비율은 제 범위 안이다."""
    stats = compute_candle_stats(found, NOW)
    for period in stats.periods:
        if period.change is None:
            continue
        assert period.low <= period.high
        assert period.from_high <= 0
        assert -1 <= period.max_drawdown <= 0
        assert period.volatility >= 0
        assert 0 <= period.up_ratio <= 1
        assert period.count >= 2
        assert period.first_start <= period.last_start


@given(candles())
def test_a_window_with_fewer_than_two_complete_candles_has_only_its_count(found):
    """완성된 봉이 2개보다 적은 기간은 개수(와 이름, 단위)만 있고 나머지 값은 모두 없다."""
    for period in compute_candle_stats(found, NOW).periods:
        if period.count >= 2:
            continue
        for field in dataclasses.fields(period):
            if field.name not in ("period", "unit", "count"):
                assert getattr(period, field.name) is None, field.name


FULL = [
    *(
        Candle(MARKET, unit, NOW - STEP[unit] * (slot + 1), Decimal(10), Decimal(11), Decimal(9),
               Decimal(10), Decimal(1), Decimal(1))
        for unit, most in ((CandleUnit.DAY, 400), (CandleUnit.MINUTE_60, 200))
        for slot in range(most)
    )
]  # fmt: skip
"""봉이 넉넉한 기준(일봉 400개, 60분봉 200개)."""


@given(candles())
def test_a_period_without_enough_candles_keeps_the_name_and_unit_it_has_with_many(found):
    """봉이 모자란 기간도 봉이 넉넉할 때와 같은 차례, 이름, 단위다.

    기간의 목록 자체(몇 개, 어떤 이름)는 굳히지 않는다(1단계 보완에서 바뀐다).
    """
    names = [(p.period, p.unit) for p in compute_candle_stats(FULL, NOW).periods]
    assert [(p.period, p.unit) for p in compute_candle_stats(found, NOW).periods] == names
