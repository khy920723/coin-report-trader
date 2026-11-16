"""DB 파일 열기와 스키마 올리기(M1 명세 3.7).

연결마다 `journal_mode=WAL`, `synchronous=FULL`을 걸고 다시 읽어 확인한다. 스키마 번호는
`PRAGMA user_version`이다(M0는 0, M1은 1). `open_database`는 열기만 하고, 표는 `migrate`가
만든다.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from coin_report_trader.core import StorageError
from coin_report_trader.storage.states import DiscordState

_DISCORD_STATES = ", ".join(repr(state.value) for state in DiscordState)

SCHEMA_VERSION = 1
"""이 앱이 아는 스키마 번호."""

_SCHEMA_1 = (
    """
    CREATE TABLE settings (
        key TEXT NOT NULL PRIMARY KEY,
        value TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE candles (
        market TEXT NOT NULL,
        unit TEXT NOT NULL,
        start_utc TEXT NOT NULL,
        open TEXT NOT NULL,
        high TEXT NOT NULL,
        low TEXT NOT NULL,
        close TEXT NOT NULL,
        volume TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (market, unit, start_utc)
    ) STRICT
    """,
    f"""
    CREATE TABLE alerts (
        id INTEGER PRIMARY KEY,
        created_utc TEXT NOT NULL,
        mode TEXT NOT NULL,
        category TEXT NOT NULL,
        code TEXT NOT NULL,
        coin TEXT,
        text TEXT NOT NULL,
        stage TEXT,
        error_name TEXT,
        content TEXT,
        "after" TEXT,
        seen INTEGER NOT NULL CHECK (seen IN (0, 1)),
        discord_state TEXT NOT NULL
            CHECK (discord_state IN ({_DISCORD_STATES}))
    ) STRICT
    """,
)

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


@contextmanager
def translate_errors(action: str) -> Iterator[None]:
    """`sqlite3.Error`를 `StorageError`로 바꾼다. 글에는 오류 종류만 쓴다(경로, 값 제외)."""
    try:
        yield
    except sqlite3.Error as exc:
        raise StorageError(f"{action}에 실패했습니다: {type(exc).__name__}") from exc


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """`BEGIN IMMEDIATE`로 트랜잭션을 손으로 열고, 끝나면 커밋한다.

    블록에서 무엇이든 나면(커밋 실패 포함) 되돌리고 그 예외를 그대로 낸다. 어느 쪽이든 끝난 뒤에는
    열린 트랜잭션이 남지 않는다. Python의 `sqlite3`는 `CREATE TABLE`과 `PRAGMA` 앞에서 트랜잭션을
    열지 않으므로 이 함수가 연다.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise


def migrate(conn: sqlite3.Connection) -> int:
    """스키마를 이 앱이 아는 번호(`SCHEMA_VERSION`)로 올리고, 올린 뒤의 번호를 돌려준다.

    `user_version` 0이면 표 셋을 만들고 1로 올린다. 이미 1이면 그대로 둔다. 이 앱이 아는 것보다
    큰 번호면 `StorageError`를 낸다. 표 만들기와 번호 쓰기는 하나의 트랜잭션이라, 중간에 실패하면
    번호도 표도 남지 않는다(`StorageError`). WAL과 synchronous 설정은 건드리지 않는다.
    """
    with translate_errors("DB 스키마 올리기"):
        version = schema_version(conn)
        _check_known(version)
        if version == SCHEMA_VERSION:
            return SCHEMA_VERSION
        with transaction(conn):
            version = schema_version(conn)  # 잠근 뒤에 다시 읽는다
            _check_known(version)
            if version < SCHEMA_VERSION:
                for statement in _SCHEMA_1:
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    return SCHEMA_VERSION


def _check_known(version: int) -> None:
    if version > SCHEMA_VERSION:
        raise StorageError(
            f"DB가 이 앱보다 새 것입니다: 스키마 번호 {version}(이 앱은 {SCHEMA_VERSION})"
        )
    if version < 0:
        raise StorageError(f"DB의 스키마 번호가 맞지 않습니다: {version}")
