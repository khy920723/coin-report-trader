"""표 `candles`의 읽고 쓰기(M1 명세 3.7). 가격과 수량은 `Decimal`의 글로 저장한다."""

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal, InvalidOperation

from coin_report_trader.core import Candle, CandleUnit, StorageError
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
