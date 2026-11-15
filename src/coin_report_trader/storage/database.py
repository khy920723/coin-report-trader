"""DB 파일 열기. 표는 만들지 않는다(M0).

연결마다 `journal_mode=WAL`, `synchronous=FULL`을 걸고 다시 읽어 확인한다. 스키마 번호는
`PRAGMA user_version`이고 M0에서는 0이다.
"""

import sqlite3
from pathlib import Path

from coin_report_trader.core import StorageError

SCHEMA_VERSION = 0
"""이 앱이 아는 스키마 번호."""

_SYNCHRONOUS_FULL = 2


def open_database(path: Path) -> sqlite3.Connection:
    """폴더가 있다고 보고 DB 파일을 연다(없으면 만든다). 설정이 걸리지 않으면 `StorageError`."""
    try:
        conn = sqlite3.connect(path)
    except sqlite3.Error as exc:
        raise StorageError(f"DB 파일을 열지 못했습니다: {type(exc).__name__}") from exc
    try:
        conn.execute("PRAGMA journal_mode=WAL").fetchone()
        conn.execute("PRAGMA synchronous=FULL").fetchone()
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]
    except sqlite3.Error as exc:
        conn.close()
        raise StorageError(f"DB 설정을 걸지 못했습니다: {type(exc).__name__}") from exc
    if str(journal_mode).lower() != "wal" or synchronous != _SYNCHRONOUS_FULL:
        conn.close()
        raise StorageError(
            f"DB 설정이 맞지 않습니다: journal_mode={journal_mode}, synchronous={synchronous}"
        )
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    """`PRAGMA user_version`."""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])
