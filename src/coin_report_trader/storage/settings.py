"""표 `settings`의 읽고 쓰기(M1 명세 3.7). 키는 이 파일 안에서만 안다.

키: `coins`(마켓 코드의 JSON 배열), `retention_days`(`"7"`, `"30"`, `"90"`),
`first_setup_level`(M1이 마치면 `"1"`), `fetched_at:{마켓 코드}`(시각 글).
"""

import json
import sqlite3
from datetime import datetime
from typing import Final

from coin_report_trader.core import SettingsView, StorageError
from coin_report_trader.storage.database import transaction, translate_errors
from coin_report_trader.storage.times import format_utc, parse_utc

KEY_COINS: Final = "coins"
KEY_RETENTION_DAYS: Final = "retention_days"
KEY_FIRST_SETUP_LEVEL: Final = "first_setup_level"
KEY_FETCHED_AT_PREFIX: Final = "fetched_at:"

RETENTION_CHOICES: Final = (7, 30, 90)
FIRST_SETUP_LEVEL_M1: Final = "1"

_UPSERT = (
    "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = ?"
)


def _put(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(_UPSERT, (key, value, value))


def read_settings(conn: sqlite3.Connection) -> SettingsView:
    """저장된 `coins`, `retention_days`, `first_setup_level`을 `SettingsView`로 읽는다.

    값이 없는 키는 기본값(코인 없음, 보관 기간 `None`, 처음 설정 안 마침)이다. 저장된 글이 맞지
    않으면 `StorageError`다.
    """
    with translate_errors("설정 읽기"):
        rows = dict(
            conn.execute(
                "SELECT key, value FROM settings WHERE key IN (?, ?, ?)",
                (KEY_COINS, KEY_RETENTION_DAYS, KEY_FIRST_SETUP_LEVEL),
            ).fetchall()
        )
    return SettingsView(
        coins=_decode_coins(rows.get(KEY_COINS)),
        retention_days=_decode_retention(rows.get(KEY_RETENTION_DAYS)),
        first_setup_done=_decode_level(rows.get(KEY_FIRST_SETUP_LEVEL)),
    )


def write_settings(conn: sqlite3.Connection, settings: SettingsView) -> None:
    """`SettingsView`의 세 값을 한 트랜잭션으로 쓴다.

    `retention_days`가 `None`이면 그 키를 지우고, `first_setup_done`이 거짓이면
    `first_setup_level`을 지운다. 보관 기간이 7, 30, 90이 아니면 `ValueError`다.
    """
    if settings.retention_days is not None and settings.retention_days not in RETENTION_CHOICES:
        raise ValueError(f"보관 기간은 {RETENTION_CHOICES} 가운데 하나여야 합니다")
    coins = json.dumps(list(settings.coins), ensure_ascii=False)
    with translate_errors("설정 저장"), transaction(conn):
        _put(conn, KEY_COINS, coins)
        if settings.retention_days is None:
            conn.execute("DELETE FROM settings WHERE key = ?", (KEY_RETENTION_DAYS,))
        else:
            _put(conn, KEY_RETENTION_DAYS, str(settings.retention_days))
        if settings.first_setup_done:
            _put(conn, KEY_FIRST_SETUP_LEVEL, FIRST_SETUP_LEVEL_M1)
        else:
            conn.execute("DELETE FROM settings WHERE key = ?", (KEY_FIRST_SETUP_LEVEL,))


def read_fetched_at(conn: sqlite3.Connection, market: str) -> datetime | None:
    """그 코인의 마지막으로 성공한 받기가 끝난 때. 없으면 `None`."""
    with translate_errors("받은 때 읽기"):
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (KEY_FETCHED_AT_PREFIX + market,)
        ).fetchone()
    return None if row is None else parse_utc(row[0])


def write_fetched_at(conn: sqlite3.Connection, market: str, moment: datetime) -> None:
    """그 코인의 `fetched_at`을 쓴다(같은 코인은 덮어쓴다)."""
    text = format_utc(moment)
    with translate_errors("받은 때 저장"), transaction(conn):
        _put(conn, KEY_FETCHED_AT_PREFIX + market, text)


def _decode_coins(text: str | None) -> tuple[str, ...]:
    if text is None:
        return ()
    try:
        loaded = json.loads(text)
    except ValueError as exc:
        raise StorageError("저장된 코인 목록을 읽지 못했습니다") from exc
    if not isinstance(loaded, list) or not all(isinstance(item, str) for item in loaded):
        raise StorageError("저장된 코인 목록의 꼴이 맞지 않습니다")
    return tuple(loaded)


def _decode_retention(text: str | None) -> int | None:
    if text is None:
        return None
    if text not in {str(days) for days in RETENTION_CHOICES}:
        raise StorageError("저장된 보관 기간이 맞지 않습니다")
    return int(text)


def _decode_level(text: str | None) -> bool:
    if text is None:
        return False
    if not text.isascii() or not text.isdigit():
        raise StorageError("저장된 처음 설정 단계가 맞지 않습니다")
    return int(text) >= 1
