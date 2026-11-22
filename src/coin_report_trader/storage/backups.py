"""DB 백업과 스키마를 바꾸기 전의 사본(M3 명세 3.13, REQ-DATA-009, REQ-MAINT-006).

자리는 자료 폴더 `root` 아래 `backups`다. 만드는 것은 모두 `sqlite3`의 `Connection.backup`이다(파일
복사가 아니다. WAL의 내용도 든다). 실패는 `StorageError`이고, 부르는 쪽이 알림을 정한다(하루 백업은
A-29 "DB 백업", 사본은 창을 띄우지 못한 오류). `--self-test`는 이 폴더와 파일을 만들지 않는다.
"""

import contextlib
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final

from coin_report_trader.core import KST, StorageError
from coin_report_trader.storage.database import SCHEMA_VERSION, schema_version, translate_errors

BACKUPS_FOLDER: Final = "backups"
DAY_START_HOUR: Final = 9
"""하루는 한국 09:00에 바뀐다(2절)."""
_DAILY: Final = re.compile(r"app-(\d{4})-(\d{2})-(\d{2})\.db")


def backup_day(now: datetime) -> date:
    """그 때가 속한 하루의 한국 날짜(한국 09:00에 바뀐다)."""
    return (now.astimezone(KST) - timedelta(hours=DAY_START_HOUR)).date()


def daily_backup_name(day: date) -> str:
    return f"app-{day.isoformat()}.db"


def _copy(conn: sqlite3.Connection, target: Path) -> None:
    """`conn`의 DB를 `target`에 백업한다. 다 쓰지 못하면 남은 파일을 지우고 `StorageError`다."""
    partial = target.with_name(target.name + ".part")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with translate_errors("DB 백업"):
            dest = sqlite3.connect(partial)
            try:
                conn.backup(dest)
            finally:
                dest.close()
        partial.replace(target)
    except OSError as exc:
        raise StorageError(f"DB 백업에 실패했습니다: {type(exc).__name__}") from exc
    finally:
        with contextlib.suppress(OSError):
            partial.unlink(missing_ok=True)


def make_daily_backup(conn: sqlite3.Connection, folder: Path, day: date) -> Path | None:
    """그날의 `app-YYYY-MM-DD.db`가 없으면 만들고 그 자리를 돌려준다. 이미 있으면 `None`."""
    target = Path(folder) / daily_backup_name(day)
    if target.exists():
        return None
    _copy(conn, target)
    return target


def prune_daily_backups(folder: Path, today: date, keep_days: int) -> tuple[str, ...]:
    """`app-YYYY-MM-DD.db` 가운데 날짜가 `today`보다 `keep_days`일 이상 앞인 파일을 지우고 지운
    이름을 돌려준다. 그 밖의 이름은 건드리지 않는다. 실패는 `StorageError`다."""
    if keep_days < 1:
        raise ValueError("남기는 날 수는 1 이상이다")
    root = Path(folder)
    try:
        names = sorted(item.name for item in root.iterdir()) if root.is_dir() else []
    except OSError as exc:
        raise StorageError(f"DB 백업 지우기에 실패했습니다: {type(exc).__name__}") from exc
    removed: list[str] = []
    for name in names:
        found = _DAILY.fullmatch(name)
        if found is None:
            continue
        try:
            day = date(int(found.group(1)), int(found.group(2)), int(found.group(3)))
        except ValueError:
            continue
        if (today - day).days < keep_days:
            continue
        try:
            (root / name).unlink()
        except OSError as exc:
            raise StorageError(f"DB 백업 지우기에 실패했습니다: {type(exc).__name__}") from exc
        removed.append(name)
    return tuple(removed)


def schema_copy_name(now: datetime) -> str:
    """`before-schema-{새 번호}-YYYYMMDD-HHMMSS.db`(한국 시각)."""
    local = now.astimezone(KST)
    return f"before-schema-{SCHEMA_VERSION}-{local.strftime('%Y%m%d-%H%M%S')}.db"


def copy_before_upgrade(conn: sqlite3.Connection, folder: Path, now: datetime) -> Path | None:
    """`migrate`가 번호를 올려야 하고 그 DB의 번호가 1 이상이면 올리기 전의 사본을 만든다. 새
    DB(번호 0)와 이미 올린 DB에는 만들지 않는다(`None`). 사본은 날 수로 지우지 않는다."""
    with translate_errors("DB 번호 읽기"):
        version = schema_version(conn)
    if version < 1 or version >= SCHEMA_VERSION:
        return None
    target = Path(folder) / schema_copy_name(now)
    _copy(conn, target)
    return target
