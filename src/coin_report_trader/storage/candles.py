"""표 `candles`의 읽고 쓰기(M1 명세 3.7). 가격과 수량은 `Decimal`의 글로 저장한다."""

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal, InvalidOperation

from coin_report_trader.core import CANDLE_UNIT_LENGTHS, Candle, CandleUnit, StorageError
from coin_report_trader.storage.database import transaction, translate_errors
from coin_report_trader.storage.times import format_utc, parse_utc

_COLUMNS = "market, unit, start_utc, open, high, low, close, volume, value"


def write_candles(conn: sqlite3.Connection, candles: Iterable[Candle]) -> int:
    """한 번 받은 봉 전부를 하나의 트랜잭션으로 쓰고, 쓴 봉의 수를 돌려준다.

    같은 (마켓, 단위, 시작 시각)은 덮어쓴다. 중간에 실패하면 되돌려 이번 호출의 봉은 하나도
    남지 않고(앞서 저장된 봉은 그대로), 열린 트랜잭션도 남기지 않는다. DB 오류는 `StorageError`다.
    봉이 없으면 아무것도 하지 않는다.
    """
    rows = [_to_row(candle) for candle in candles]
    if not rows:
        return 0
    with translate_errors("봉 저장"), transaction(conn):
        conn.executemany(
            f"INSERT OR REPLACE INTO candles ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def read_candles(conn: sqlite3.Connection, market: str, unit: CandleUnit) -> tuple[Candle, ...]:
    """저장된 그 코인·단위의 봉을 시작 시각 순(오래된 것이 앞)으로 읽는다."""
    with translate_errors("봉 읽기"):
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM candles WHERE market = ? AND unit = ? ORDER BY start_utc",
            (market, CandleUnit(unit).value),
        ).fetchall()
    return tuple(_from_row(row) for row in rows)


def last_candle_start(conn: sqlite3.Connection, market: str, unit: CandleUnit) -> datetime | None:
    """저장된 그 코인·단위의 마지막 봉의 시작 시각(`last_start`로 쓴다). 없으면 `None`."""
    with translate_errors("마지막 봉 읽기"):
        (text,) = conn.execute(
            "SELECT max(start_utc) FROM candles WHERE market = ? AND unit = ?",
            (market, CandleUnit(unit).value),
        ).fetchone()
    return None if text is None else parse_utc(text)


def first_candle_start(conn: sqlite3.Connection, market: str, unit: CandleUnit) -> datetime | None:
    """저장된 그 코인·단위의 가장 이른 봉의 시작 시각(`first_start`, 3.5.3). 없으면 `None`."""
    with translate_errors("가장 이른 봉 읽기"):
        (text,) = conn.execute(
            "SELECT min(start_utc) FROM candles WHERE market = ? AND unit = ?",
            (market, CandleUnit(unit).value),
        ).fetchone()
    return None if text is None else parse_utc(text)


def _complete_cutoff(unit: CandleUnit, now: datetime) -> str:
    """이 시각 글보다 늦지 않게 시작한 봉이 완성된 봉이다(시작 + 단위의 길이 <= `now`)."""
    return format_utc(now - CANDLE_UNIT_LENGTHS[CandleUnit(unit)])


def count_complete_candles(
    conn: sqlite3.Connection, market: str, unit: CandleUnit, now: datetime
) -> int:
    """저장된 그 코인·단위의 완성된 봉의 수(`missing`을 셈하는 데 쓴다. 3.5.3)."""
    with translate_errors("완성된 봉 세기"):
        (count,) = conn.execute(
            "SELECT count(*) FROM candles WHERE market = ? AND unit = ? AND start_utc <= ?",
            (market, CandleUnit(unit).value, _complete_cutoff(unit, now)),
        ).fetchone()
    return int(count)


def read_latest_complete_candles(
    conn: sqlite3.Connection, market: str, unit: CandleUnit, now: datetime, count: int
) -> tuple[Candle, ...]:
    """저장된 그 코인·단위의 완성된 봉 가운데 시작 시각이 가장 늦은 `count`개를 시작 시각
    순(오래된 것이 앞)으로 읽는다. 완성된 봉이 `count`개보다 적으면 있는 만큼이다."""
    if count <= 0:
        return ()
    with translate_errors("봉 읽기"):
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM candles WHERE market = ? AND unit = ? AND start_utc <= ?"
            " ORDER BY start_utc DESC LIMIT ?",
            (market, CandleUnit(unit).value, _complete_cutoff(unit, now), count),
        ).fetchall()
    return tuple(_from_row(row) for row in reversed(rows))


def has_candles(conn: sqlite3.Connection, unit: CandleUnit) -> bool:
    """어느 코인이든 그 단위의 봉이 하나라도 저장되어 있는지(3.5.4)."""
    with translate_errors("봉 있는지 읽기"):
        row = conn.execute(
            "SELECT 1 FROM candles WHERE unit = ? LIMIT 1", (CandleUnit(unit).value,)
        ).fetchone()
    return row is not None


def delete_candles_except(conn: sqlite3.Connection, unit: CandleUnit) -> int:
    """그 단위가 아닌 봉을 모두 지우고 지운 수를 돌려준다(봉 단위를 저장할 때. 3.5.4)."""
    with translate_errors("다른 단위의 봉 지우기"), transaction(conn):
        return conn.execute(
            "DELETE FROM candles WHERE unit <> ?", (CandleUnit(unit).value,)
        ).rowcount


def _to_row(candle: Candle) -> tuple[object, ...]:
    return (
        candle.market,
        CandleUnit(candle.unit).value,
        format_utc(candle.start),
        str(candle.open),
        str(candle.high),
        str(candle.low),
        str(candle.close),
        str(candle.volume),
        str(candle.value),
    )


def _from_row(row: tuple[str, ...]) -> Candle:
    market, unit, start, *numbers = row
    try:
        open_, high, low, close, volume, value = (Decimal(number) for number in numbers)
        start_at = parse_utc(start)
        return Candle(market, CandleUnit(unit), start_at, open_, high, low, close, volume, value)
    except (InvalidOperation, ValueError) as exc:
        raise StorageError("저장된 봉의 값이 맞지 않습니다") from exc
