"""DB 백업과 스키마를 바꾸기 전의 사본(M3 명세 3.13). 값은 모두 만든 값이다."""

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from coin_report_trader.core import StorageError
from coin_report_trader.storage.backups import (
    BACKUPS_FOLDER,
    backup_day,
    copy_before_upgrade,
    daily_backup_name,
    make_daily_backup,
    prune_daily_backups,
    schema_copy_name,
)
from coin_report_trader.storage.database import migrate, open_database, schema_version


def db_with_row(path: Path, version: int | None = None) -> sqlite3.Connection:
    conn = open_database(path)
    if version is None:
        migrate(conn)
        conn.execute("INSERT INTO settings VALUES ('coins', '[\"KRW-AAA\"]')")
        conn.commit()
    else:
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT) STRICT")
        conn.execute("INSERT INTO settings VALUES ('coins', '[\"KRW-AAA\"]')")
        conn.execute(f"PRAGMA user_version={version}")
        conn.commit()
    return conn


def read_coins(path: Path) -> list[tuple[str]]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT value FROM settings WHERE key = 'coins'").fetchall()
    finally:
        conn.close()


def test_the_backup_day_changes_at_nine_in_the_korean_morning() -> None:
    assert backup_day(datetime(2026, 12, 5, 0, 0, tzinfo=UTC)) == date(2026, 12, 5)  # 한국 09:00
    assert backup_day(datetime(2026, 12, 4, 23, 59, 59, tzinfo=UTC)) == date(2026, 12, 4)
    assert backup_day(datetime(2026, 12, 5, 14, 59, tzinfo=UTC)) == date(2026, 12, 5)  # 23:59
    assert backup_day(datetime(2026, 12, 5, 15, 0, tzinfo=UTC)) == date(2026, 12, 5)  # 다음날 0시
    assert daily_backup_name(date(2026, 12, 5)) == "app-2026-12-05.db"
    assert BACKUPS_FOLDER == "backups"


def test_the_daily_backup_is_made_once_with_the_contents_including_the_wal(tmp_path: Path) -> None:
    conn = db_with_row(tmp_path / "app.db")
    folder = tmp_path / "backups"
    try:
        made = make_daily_backup(conn, folder, date(2026, 12, 5))
        assert made == folder / "app-2026-12-05.db"
        assert read_coins(made) == [('["KRW-AAA"]',)]  # WAL에만 있던 줄도 든다
        assert make_daily_backup(conn, folder, date(2026, 12, 5)) is None  # 그날 것은 한 번
        assert sorted(p.name for p in folder.iterdir()) == ["app-2026-12-05.db"]
    finally:
        conn.close()


def test_prune_keeps_the_last_n_days_and_touches_nothing_else(tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    folder.mkdir()
    names = [
        "app-2026-12-01.db", "app-2026-12-02.db", "app-2026-12-03.db", "app-2026-12-04.db",
        "app-2026-12-05.db", "before-schema-3-20261201-090000.db", "app-2026-12-1.db", "note.txt",
        "app-2026-02-30.db", "app-2026-12-01.db.bak", "x-app-2026-12-01.db",
        "before-schema-3-app-2026-12-01.db",
    ]  # fmt: skip
    for name in names:
        (folder / name).write_bytes(b"x")
    removed = prune_daily_backups(folder, date(2026, 12, 5), 3)
    assert removed == ("app-2026-12-01.db", "app-2026-12-02.db")
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        [
            "app-2026-12-03.db",
            "app-2026-12-04.db",
            "app-2026-12-05.db",
            "before-schema-3-20261201-090000.db",
            "app-2026-12-1.db",
            "note.txt",
            "app-2026-02-30.db",
            "app-2026-12-01.db.bak",
            "x-app-2026-12-01.db",
            "before-schema-3-app-2026-12-01.db",
        ]  # 지울 날짜가 든 다른 이름도 건드리지 않는다
    )
    assert prune_daily_backups(tmp_path / "none", date(2026, 12, 5), 3) == ()
    with pytest.raises(ValueError, match="1 이상"):
        prune_daily_backups(folder, date(2026, 12, 5), 0)


def test_a_failure_to_make_or_prune_is_a_storage_error(tmp_path: Path) -> None:
    conn = db_with_row(tmp_path / "app.db")
    blocker = tmp_path / "file"
    blocker.write_text("폴더가 아니다", encoding="utf-8")
    try:
        with pytest.raises(StorageError, match="DB 백업"):
            make_daily_backup(conn, blocker, date(2026, 12, 5))
    finally:
        conn.close()
    assert not (tmp_path / "file" / "app-2026-12-05.db.part").exists()
    folder = tmp_path / "backups"
    folder.mkdir()
    (folder / "app-2026-11-01.db").mkdir()  # 지울 수 없는 것(폴더)
    with pytest.raises(StorageError, match="DB 백업 지우기"):
        prune_daily_backups(folder, date(2026, 12, 5), 3)


def test_the_schema_copy_name_has_the_new_number_and_the_korean_time() -> None:
    moment = datetime(2026, 12, 4, 15, 30, 5, tzinfo=UTC)  # 한국 2026-12-05 00:30:05
    assert schema_copy_name(moment) == "before-schema-3-20261205-003005.db"


@pytest.mark.parametrize("version", [1, 2])
def test_an_old_database_is_copied_before_it_is_raised(tmp_path: Path, version: int) -> None:
    conn = db_with_row(tmp_path / "app.db", version)
    moment = datetime(2026, 12, 4, 15, 30, 5, tzinfo=UTC)
    try:
        copy = copy_before_upgrade(conn, tmp_path / "backups", moment)
    finally:
        conn.close()
    assert copy == tmp_path / "backups" / "before-schema-3-20261205-003005.db"
    assert read_coins(copy) == [('["KRW-AAA"]',)]
    check = sqlite3.connect(copy)
    try:
        assert schema_version(check) == version  # 올리기 전의 것이다
    finally:
        check.close()


def test_a_new_or_current_database_gets_no_copy(tmp_path: Path) -> None:
    new = open_database(tmp_path / "new.db")
    current = db_with_row(tmp_path / "current.db")
    moment = datetime(2026, 12, 5, tzinfo=UTC)
    try:
        assert copy_before_upgrade(new, tmp_path / "backups", moment) is None
        assert copy_before_upgrade(current, tmp_path / "backups", moment) is None
    finally:
        new.close()
        current.close()
    assert not (tmp_path / "backups").exists()
