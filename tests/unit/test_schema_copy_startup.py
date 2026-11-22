"""켤 때의 스키마를 바꾸기 전의 사본(M3 명세 3.13. `app/application.py`의 켜는 순서 1)."""

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fakes.clock import FakeClock
from fakes.discord import FakeDiscord
from fakes.quote import FakeQuoteSource
from fakes.vault import FakeVault
from PySide6.QtCore import QTimer

from coin_report_trader.app import application, run_gui
from coin_report_trader.app.services import Services
from coin_report_trader.config.paths import resolve_app_paths
from coin_report_trader.core import StorageError
from coin_report_trader.storage import database
from coin_report_trader.storage.database import open_database, schema_version


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request: pytest.FixtureRequest) -> Any:
    data_home = tmp_path / "share"
    data_home.mkdir()
    key = "LOCALAPPDATA" if sys.platform == "win32" else "XDG_DATA_HOME"
    monkeypatch.setenv(key, str(data_home))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return request.getfixturevalue("qapp")


def services() -> Services:
    return Services(
        quote=FakeQuoteSource(), webhook=FakeDiscord(), vault=FakeVault(), clock=FakeClock()
    )


def old_database(version: int) -> Path:
    paths = resolve_app_paths()
    paths.ensure()
    conn = open_database(paths.database_file)
    try:
        for statements in (database._SCHEMA_1, database._SCHEMA_2)[:version]:
            for statement in statements:
                conn.execute(statement)
        conn.execute("INSERT INTO settings VALUES ('coins', '[\"KRW-AAA\"]')")
        conn.execute(f"PRAGMA user_version={version}")
        conn.commit()
    finally:
        conn.close()
    return paths.root


def quit_soon(app: Any) -> QTimer:
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(app.quit)
    timer.start(0)
    return timer


def test_an_old_database_is_copied_into_backups_before_it_is_raised(env: Any) -> None:
    root = old_database(2)
    timer = quit_soon(env)
    assert run_gui([], services=services()) == 0
    timer.stop()
    copies = sorted((root / "backups").glob("before-schema-3-*.db"))
    assert len(copies) == 1
    assert copies[0].name.startswith("before-schema-3-2026")  # 가짜 시계의 한국 시각
    copy = open_database(copies[0])
    try:
        assert schema_version(copy) == 2
        assert copy.execute("SELECT value FROM settings").fetchall() == [('["KRW-AAA"]',)]
    finally:
        copy.close()
    conn = open_database(resolve_app_paths().database_file)
    try:
        assert schema_version(conn) == 3
    finally:
        conn.close()


def test_a_new_database_gets_no_copy(env: Any) -> None:
    timer = quit_soon(env)
    assert run_gui([], services=services()) == 0
    timer.stop()
    root = resolve_app_paths().root
    assert not (root / "backups").exists() or list((root / "backups").iterdir()) == []


def test_a_copy_that_cannot_be_made_stops_before_raising_with_a_startup_a29(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = old_database(1)

    def broken(*args: Any, **kwargs: Any) -> None:
        raise StorageError("DB 백업에 실패했습니다: OSError")

    monkeypatch.setattr(application, "copy_before_upgrade", broken)
    with pytest.raises(StorageError, match="DB 백업"):
        run_gui([], services=services())
    conn = open_database(resolve_app_paths().database_file)
    try:
        assert schema_version(conn) == 1  # 올리지 않았다
    finally:
        conn.close()
    lines = [
        json.loads(line)
        for line in (root / "logs" / "startup-errors.jsonl").read_text("utf-8").splitlines()
    ]
    assert [(x["code"], x["stage"], x["error_name"]) for x in lines] == [
        ("A-29", "켜기", "DB 오류")
    ]
