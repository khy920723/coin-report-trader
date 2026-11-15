"""명세 7절 A4: DB 파일 열기."""

import contextlib
import sqlite3
from pathlib import Path

import pytest

from coin_report_trader.storage.database import open_database


@pytest.mark.scenario("A4")
def test_a4_open_twice_with_wal_full_sync_and_schema_zero(tmp_path: Path) -> None:
    assert list(tmp_path.iterdir()) == []
    path = tmp_path / "app.db"
    for _ in range(2):  # 닫고 다시 연다
        with contextlib.closing(open_database(path)) as conn:
            assert path.is_file()
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 0


@pytest.mark.scenario("A4")
def test_a4_open_database_sets_the_pragmas_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SQLite의 기본값이나 파일에 남은 WAL에 기대지 않고 `open_database`가 연결마다 건다.

    새 연결마다 `synchronous=OFF`를 먼저 걸어 두고, 두 번 여는 사이에 파일을 `journal_mode=DELETE`로
    바꿔 둔다. 그래도 두 번 모두 `wal`과 2가 나와야 한다.
    """
    path = tmp_path / "app.db"
    real_connect = sqlite3.connect

    def connect_with_sync_off(*args: object, **kwargs: object) -> sqlite3.Connection:
        conn = real_connect(*args, **kwargs)
        conn.execute("PRAGMA synchronous=OFF")
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect_with_sync_off)
    for attempt in range(2):
        if attempt == 1:
            with contextlib.closing(real_connect(path)) as plain:
                assert plain.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
        with contextlib.closing(open_database(path)) as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
