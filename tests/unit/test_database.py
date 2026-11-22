"""DB 파일 열기 시험."""

import sqlite3
from pathlib import Path

import pytest

from coin_report_trader.core import StorageError
from coin_report_trader.storage import database
from coin_report_trader.storage.database import SCHEMA_VERSION, open_database, schema_version


def _pragmas(conn: sqlite3.Connection) -> tuple[str, int, int]:
    (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
    (synchronous,) = conn.execute("PRAGMA synchronous").fetchone()
    return str(mode), int(synchronous), schema_version(conn)


def test_open_creates_the_file_with_wal_and_full_sync_every_time(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    for _ in range(2):
        conn = open_database(path)
        try:
            assert path.is_file()
            assert _pragmas(conn) == ("wal", 2, 0)
        finally:
            conn.close()


def test_schema_version_starts_at_zero_and_reads_user_version(tmp_path: Path) -> None:
    assert SCHEMA_VERSION == 3
    conn = open_database(tmp_path / "app.db")
    try:
        conn.execute("PRAGMA user_version=4")
        assert schema_version(conn) == 4
        # M0에서는 표를 만들지 않는다.
        assert conn.execute("SELECT count(*) FROM sqlite_master").fetchone() == (0,)
    finally:
        conn.close()


def test_missing_folder_is_a_storage_error(tmp_path: Path) -> None:
    with pytest.raises(StorageError):
        open_database(tmp_path / "no-such-folder" / "app.db")
    assert not (tmp_path / "no-such-folder").exists()


class _Cursor:
    def __init__(self, value: object) -> None:
        self._value = value

    def fetchone(self) -> tuple[object]:
        return (self._value,)


class _FakeConnection:
    """PRAGMA를 읽을 때 정해 둔 값을 돌려주는 연결."""

    def __init__(self, answers: dict[str, object], error: Exception | None = None) -> None:
        self.answers = answers
        self.error = error
        self.closed = False

    def execute(self, sql: str) -> _Cursor:
        if self.error is not None:
            raise self.error
        return _Cursor(self.answers[sql.split("=")[0].replace("PRAGMA ", "").strip()])

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    "answers",
    [
        {"journal_mode": "delete", "synchronous": 2},
        {"journal_mode": "wal", "synchronous": 1},
    ],
)
def test_unexpected_settings_are_a_storage_error(
    monkeypatch: pytest.MonkeyPatch, answers: dict[str, object]
) -> None:
    fake = _FakeConnection(answers)
    monkeypatch.setattr(database.sqlite3, "connect", lambda _path: fake)
    with pytest.raises(StorageError, match="journal_mode"):
        open_database(Path("unused.db"))
    assert fake.closed


def test_sqlite_error_while_setting_is_a_storage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeConnection({}, error=sqlite3.OperationalError("disk I/O error"))
    monkeypatch.setattr(database.sqlite3, "connect", lambda _path: fake)
    with pytest.raises(StorageError):
        open_database(Path("unused.db"))
    assert fake.closed
