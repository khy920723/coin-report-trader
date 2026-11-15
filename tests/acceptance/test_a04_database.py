"""명세 7절 A4: DB 파일 열기."""

import contextlib
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
