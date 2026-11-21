"""봉 저장과 합치기의 불변식(M1 명세 3.7): `write_candles`와 `read_candles`.

DB는 시험마다 메모리 안의 SQLite에 `migrate`로 표를 만든다(파일을 남기지 않는다). 봉의 값은 만든
숫자다.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from coin_report_trader.core import Candle, CandleUnit
from coin_report_trader.storage.candles import last_candle_start, read_candles, write_candles
from coin_report_trader.storage.database import migrate

MARKET = "KRW-AAA"
BASE = datetime(2026, 1, 1, tzinfo=UTC)
STEP = {CandleUnit.DAY: timedelta(days=1), CandleUnit.MINUTE_60: timedelta(hours=1)}

prices = st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("100000000"), places=4)


def fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    migrate(conn)
    return conn


@st.composite
def candle_at(draw: st.DrawFn, unit: CandleUnit, slot: int) -> Candle:
    low = draw(prices)
    high = low + draw(st.decimals(min_value=0, max_value=1000, places=4))
    return Candle(
        market=MARKET,
        unit=unit,
        start=BASE + STEP[unit] * slot,
        open=draw(st.sampled_from([low, high])),
        high=high,
        low=low,
        close=draw(st.sampled_from([low, high])),
        volume=draw(prices),
        value=draw(prices),
    )


@st.composite
def candle_series(draw: st.DrawFn) -> tuple[CandleUnit, list[Candle]]:
    """한 단위의 봉들. 시작 시각은 서로 다르고 차례는 섞여 있다."""
    unit = draw(st.sampled_from(list(CandleUnit)))
    slots = draw(st.lists(st.integers(0, 500), min_size=1, max_size=30, unique=True))
    return unit, [draw(candle_at(unit, slot)) for slot in slots]


@given(candle_series(), st.data())
def test_the_same_candles_received_many_times_are_stored_once_in_start_order(series, data):
    """같은 봉을 여러 번(나눠서, 겹쳐서) 받아도 봉마다 한 줄이고 시작 시각 순서로 읽힌다."""
    unit, candles = series
    conn = fresh_db()
    batches = data.draw(
        st.lists(st.lists(st.sampled_from(candles), max_size=15), min_size=1, max_size=5)
    )
    for batch in [*batches, candles]:
        write_candles(conn, batch)
    stored = read_candles(conn, MARKET, unit)
    assert list(stored) == sorted(candles, key=lambda c: c.start)
    assert len({c.start for c in stored}) == len(stored)
    assert last_candle_start(conn, MARKET, unit) == max(c.start for c in candles)


@given(candle_series(), st.data())
def test_the_last_candle_being_built_is_overwritten_by_the_next_receipt(series, data):
    """만들어지는 중인 마지막 봉을 다시 받으면 덮어쓴다(줄 수는 그대로, 값은 새 것)."""
    unit, candles = series
    conn = fresh_db()
    write_candles(conn, candles)
    last = max(candles, key=lambda c: c.start)
    newer = data.draw(candle_at(unit, (last.start - BASE) // STEP[unit]))
    write_candles(conn, [newer])
    stored = read_candles(conn, MARKET, unit)
    assert len(stored) == len(candles)
    assert stored[-1] == newer
    assert list(stored[:-1]) == sorted(candles, key=lambda c: c.start)[:-1]


@given(candle_series(), st.data())
def test_the_order_of_the_receipts_does_not_change_what_is_stored(series, data):
    """같은 봉들을 받는 차례(묶음의 차례와 묶음 안의 차례)가 달라도 저장된 결과가 같다."""
    unit, candles = series
    split = data.draw(st.lists(st.integers(0, 3), min_size=len(candles), max_size=len(candles)))
    batches = [[c for c, b in zip(candles, split, strict=True) if b == k] for k in range(4)]
    first, second = fresh_db(), fresh_db()
    for batch in batches:
        write_candles(first, batch)
    for batch in data.draw(st.permutations(batches)):
        write_candles(second, data.draw(st.permutations(batch)))
    assert read_candles(first, MARKET, unit) == read_candles(second, MARKET, unit)


@given(candle_series())
def test_the_other_unit_and_market_are_not_touched(series):
    """한 단위의 봉을 써도 다른 단위와 다른 코인의 봉은 그대로다."""
    unit, candles = series
    other_unit = next(u for u in CandleUnit if u is not unit)
    conn = fresh_db()
    kept = [
        Candle("KRW-BBB", unit, BASE, Decimal(1), Decimal(2), Decimal(1), Decimal(2), Decimal(1),
               Decimal(1)),
        Candle(MARKET, other_unit, BASE, Decimal(3), Decimal(4), Decimal(3), Decimal(4), Decimal(1),
               Decimal(1)),
    ]  # fmt: skip
    write_candles(conn, kept)
    write_candles(conn, candles)
    assert read_candles(conn, "KRW-BBB", unit) == (kept[0],)
    assert read_candles(conn, MARKET, other_unit) == (kept[1],)
