"""봉 통계의 불변식(M1 명세 3.5, 3.5.1): `compute_bar_stats`.

값 자체는 3.5.2의 예(`tests/unit/test_stats.py`)가 굳힌다. 여기서는 어떤 봉을 넣어도 남는 성질을
본다: 네 단위, 고른 개수보다 적으면 셈하지 않음, 미완성 봉을 쓰지 않음, 가장 늦은 N개만 씀, 다른
코인·단위의 봉, 봉의 차례와 중복, 가격의 배수, 비율의 범위.

봉 개수(N)는 셈을 빠르게 하려고 작은 수(2~30)를 쓴다(3.5.2의 "작은 N은 함수의 시험에서만 쓴다").
`CANDLE_COUNTS`의 셋은 `test_the_offered_counts_need_that_many_complete_candles`가 본다.
"""

import dataclasses
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from coin_report_trader.core import CANDLE_COUNTS, CANDLE_UNIT_LENGTHS, Candle, CandleUnit
from coin_report_trader.stats import compute_bar_stats

MARKET = "KRW-AAA"
OTHER_MARKET = "KRW-BBB"
NOW = datetime(2026, 1, 5, 3, 0, tzinfo=UTC)
UNITS = list(CandleUnit)

VALUE_FIELDS = (
    "first_start_utc", "last_start_utc", "close_last", "change", "slope", "vs_mean", "up_ratio",
    "volatility", "max_drawdown", "from_high", "high", "low",
)  # fmt: skip
"""셀 수 없을 때 `None`인 열(3.5.1의 `first_start_utc`부터 `low`까지)."""
PRICE_FIELDS = ("close_last", "high", "low")
"""가격을 그대로 담는 열. 나머지 값 열은 비율이거나 시각이다."""

prices = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1000000"), places=2)
counts = st.integers(2, 30)


def start_of(unit: CandleUnit, slot: int) -> datetime:
    """`slot`은 `NOW`에 막 끝난 봉에서 거슬러 오른 칸 수다(0이 가장 최근에 완성된 봉)."""
    return NOW - CANDLE_UNIT_LENGTHS[unit] * (slot + 1)


@st.composite
def candle(draw: st.DrawFn, unit: CandleUnit, start: datetime, market: str = MARKET) -> Candle:
    """값이 맞는 봉(0 < 저가 ≤ 시가·종가 ≤ 고가)."""
    low = draw(prices)
    high = low + draw(st.decimals(min_value=0, max_value=10000, places=2))
    return Candle(
        market=market,
        unit=unit,
        start=start,
        open=draw(st.sampled_from([low, high, (low + high) / 2])),
        high=high,
        low=low,
        close=draw(st.sampled_from([low, high, (low + high) / 2])),
        volume=draw(prices),
        value=draw(prices),
    )


@st.composite
def complete_candles(draw: st.DrawFn, unit: CandleUnit, most: int = 60) -> list[Candle]:
    """`unit`의 완성된 봉들. 시작 시각은 서로 다르고 사이가 빌 수 있다(채우지 않는다)."""
    slots = draw(st.lists(st.integers(0, most), max_size=40, unique=True))
    return [draw(candle(unit, start_of(unit, slot))) for slot in slots]


def compute(found: list[Candle], unit: CandleUnit, count: int, now: datetime = NOW):
    return compute_bar_stats(found, now, market=MARKET, unit=unit, count=count)


def latest_starts(found: list[Candle], count: int) -> list[datetime]:
    return sorted(c.start for c in found)[-count:]


def scaled(c: Candle, factor: Decimal) -> Candle:
    return dataclasses.replace(
        c, open=c.open * factor, high=c.high * factor, low=c.low * factor, close=c.close * factor
    )


# --- 고른 개수, 쓰는 봉 ---------------------------------------------------------------------


@pytest.mark.parametrize("unit", UNITS)
@given(data=st.data(), count=counts)
def test_fewer_complete_candles_than_the_count_are_not_computed(unit, data, count):
    """완성된 봉이 N개보다 적으면 셈하지 않는다: `bar_count`는 있는 수, 값 열은 모두 `None`.

    N개 이상이면 `bar_count`는 N이고 값 열은 모두 있다(가격이 0보다 크면 나누는 값이 0이 아니다).
    """
    found = data.draw(complete_candles(unit))
    stats, last_incomplete = compute(found, unit, count)
    assert (stats.market, stats.unit, stats.requested_count) == (MARKET, unit, count)
    assert last_incomplete is False
    if len(found) < count:
        assert stats.bar_count == len(found)
        assert all(getattr(stats, name) is None for name in VALUE_FIELDS)
    else:
        assert stats.bar_count == count
        assert all(getattr(stats, name) is not None for name in VALUE_FIELDS)


@pytest.mark.parametrize("unit", UNITS)
@given(data=st.data(), count=counts)
def test_only_the_latest_count_complete_candles_are_used(unit, data, count):
    """완성된 봉이 N개 이상이면 시작 시각이 가장 늦은 N개만 쓴다. 그보다 앞의 봉은 값을 바꾸지
    않고, 첫 봉과 마지막 봉의 시작은 그 N개의 처음과 끝이다."""
    found = data.draw(complete_candles(unit))
    if len(found) < count:
        return
    keep = set(latest_starts(found, count))
    latest = [c for c in found if c.start in keep]
    stats, _ = compute(found, unit, count)
    assert compute(latest, unit, count) == (stats, False)
    assert stats.first_start_utc == min(keep)
    assert stats.last_start_utc == max(keep)
    last = max(latest, key=lambda c: c.start)
    assert stats.close_last == last.close
    assert stats.high == max(c.high for c in latest)
    assert stats.low == min(c.low for c in latest)


@pytest.mark.parametrize("unit", UNITS)
@given(data=st.data(), count=counts)
def test_candles_that_are_not_complete_yet_are_not_used(unit, data, count):
    """`now`에 아직 끝나지 않은 봉(시작 + 단위의 길이 > `now`)은 셈에 들지 않는다. 그런 봉이
    저장된 마지막 봉이면 `last_incomplete`가 참이다."""
    found = data.draw(complete_candles(unit))
    length = CANDLE_UNIT_LENGTHS[unit]
    ahead = data.draw(st.integers(0, 3))
    early = data.draw(st.integers(1, int(length.total_seconds())))  # 끝나기까지 남은 초
    start = NOW - length + timedelta(seconds=early) + length * ahead
    building = data.draw(candle(unit, start))
    base, _ = compute(found, unit, count)
    assert compute([*found, building], unit, count) == (base, True)


@pytest.mark.parametrize("unit", UNITS)
@given(data=st.data(), count=counts)
def test_a_candle_that_ends_exactly_now_is_complete(unit, data, count):
    """시작 + 단위의 길이가 `now`와 꼭 같은 봉은 완성된 봉이다(3.5). 그 시각에 셈하면 그 봉이
    마지막 봉이고, 1초 앞에 셈하면 미완성이라 빠진다."""
    ending = data.draw(candle(unit, start_of(unit, 0)))  # 시작 + 길이 = NOW
    found = [c for c in data.draw(complete_candles(unit)) if c.start != ending.start]
    stats, last_incomplete = compute([*found, ending], unit, count)
    assert last_incomplete is False
    assert stats.bar_count == min(count, len(found) + 1)
    if stats.bar_count == count:
        assert stats.last_start_utc == ending.start
        assert stats.close_last == ending.close
    before, flagged = compute([*found, ending], unit, count, NOW - timedelta(seconds=1))
    assert flagged is True
    assert before.bar_count == min(count, len(found))
    assert before.last_start_utc != ending.start


@pytest.mark.parametrize("unit", UNITS)
@given(data=st.data(), count=counts)
def test_candles_of_other_coins_and_units_change_nothing(unit, data, count):
    """다른 코인의 봉과 다른 단위의 봉(시작 시각이 겹쳐도)은 결과를 바꾸지 않는다."""
    found = data.draw(complete_candles(unit))
    other_unit = data.draw(st.sampled_from([u for u in UNITS if u is not unit]))
    noise = [dataclasses.replace(c, market=OTHER_MARKET) for c in data.draw(complete_candles(unit))]
    noise += [dataclasses.replace(c, unit=other_unit) for c in data.draw(complete_candles(unit))]
    mixed = [*found, *noise]
    random.Random(len(mixed)).shuffle(mixed)
    assert compute(mixed, unit, count) == compute(found, unit, count)


@pytest.mark.parametrize("count", CANDLE_COUNTS)
@given(data=st.data())
def test_the_offered_counts_need_that_many_complete_candles(count, data):
    """고르는 봉 개수(100, 200, 500)에서도 완성된 봉이 N개여야 셈한다. N − 1개면 셈하지 않는다."""
    unit = data.draw(st.sampled_from(UNITS))
    closes = data.draw(st.lists(prices, min_size=1, max_size=8))
    have = count + data.draw(st.integers(-2, 2))
    found = [
        Candle(MARKET, unit, start_of(unit, slot), close, close, close, close, close, close)
        for slot, close in zip(range(have), closes * (have // len(closes) + 1), strict=False)
    ]
    stats, _ = compute(found, unit, count)
    assert stats.bar_count == min(have, count)
    assert (stats.close_last is None) is (have < count)


# --- 봉의 차례, 중복, 배수, 범위 ------------------------------------------------------------


@pytest.mark.parametrize("unit", UNITS)
@given(data=st.data(), count=counts, rnd=st.randoms(use_true_random=False))
def test_the_order_of_the_candles_does_not_change_the_result(unit, data, count, rnd):
    """봉을 넘기는 차례가 달라도 결과가 같다."""
    found = data.draw(complete_candles(unit))
    shuffled = list(found)
    rnd.shuffle(shuffled)
    assert compute(shuffled, unit, count) == compute(found, unit, count)


@pytest.mark.parametrize("unit", UNITS)
@given(data=st.data(), count=counts)
def test_a_candle_received_twice_counts_once(unit, data, count):
    """같은 봉이 두 번 들어와도(같은 값) 한 번으로 센다."""
    found = data.draw(complete_candles(unit))
    again = data.draw(st.lists(st.sampled_from(found), max_size=10)) if found else []
    assert compute([*again, *found], unit, count) == compute(found, unit, count)


@pytest.mark.parametrize("unit", UNITS)
@given(data=st.data(), count=counts)
def test_a_later_copy_of_the_same_start_wins(unit, data, count):
    """시작 시각이 같은 봉은 나중에 온 것을 쓴다."""
    found = data.draw(complete_candles(unit))
    if not found:
        return
    target = data.draw(st.sampled_from(found))
    replaced = dataclasses.replace(target, close=target.close + 1, high=target.high + 1)
    later = [c if c is not target else replaced for c in found]
    assert compute([*found, replaced], unit, count) == compute(later, unit, count)


@pytest.mark.parametrize("unit", UNITS)
@given(data=st.data(), count=counts, power=st.integers(-3, 4))
def test_multiplying_every_price_by_a_power_of_ten_keeps_every_ratio(unit, data, count, power):
    """모든 가격을 같은 배수(10의 거듭제곱)로 바꾸면 비율과 수와 시각은 그대로, 가격만 그 배수다."""
    found = data.draw(complete_candles(unit))
    factor = Decimal(10) ** power
    base, _ = compute(found, unit, count)
    moved, _ = compute([scaled(c, factor) for c in found], unit, count)
    for field in dataclasses.fields(base):
        old, new = getattr(base, field.name), getattr(moved, field.name)
        if field.name in PRICE_FIELDS and old is not None:
            assert new == old * factor, field.name
        else:
            assert new == old, field.name


@pytest.mark.parametrize("unit", UNITS)
@given(data=st.data(), count=counts)
def test_ratios_stay_in_their_ranges(unit, data, count):
    """값이 맞는 봉(0 < 저가 ≤ 시가·종가 ≤ 고가)에서 비율과 가격은 제 범위 안이다."""
    found = data.draw(complete_candles(unit))
    stats, _ = compute(found, unit, count)
    if stats.close_last is None:
        return
    assert stats.low <= stats.close_last <= stats.high
    assert stats.first_start_utc <= stats.last_start_utc
    assert stats.change >= -1
    assert stats.vs_mean >= -1
    assert -1 <= stats.from_high <= 0
    assert -1 <= stats.max_drawdown <= 0
    assert stats.volatility >= 0
    assert 0 <= stats.up_ratio <= 1
    for name in (
        "change",
        "slope",
        "vs_mean",
        "up_ratio",
        "volatility",
        "max_drawdown",
        "from_high",
    ):
        assert getattr(stats, name).as_tuple().exponent == -4, name  # 0.0001 단위로 맞춘 값
