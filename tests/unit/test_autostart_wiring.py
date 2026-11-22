"""`Services.autostart`가 자료 스레드에 닿는다(M1 명세 3.16.3). 창을 띄우고 바로 끝낸다."""

import sys
from pathlib import Path
from typing import Any

import pytest
from fakes.autostart import FakeAutostart
from fakes.clock import FakeClock
from fakes.discord import FakeDiscord
from fakes.quote import FakeQuoteSource
from fakes.vault import FakeVault
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from coin_report_trader.app import run_gui
from coin_report_trader.app.services import Services
from coin_report_trader.config.paths import resolve_app_paths
from coin_report_trader.core import CandleUnit, SettingsView
from coin_report_trader.storage.database import migrate, open_database
from coin_report_trader.storage.settings import write_settings


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request: pytest.FixtureRequest) -> Any:
    data_home = tmp_path / "share"
    data_home.mkdir()
    key = "LOCALAPPDATA" if sys.platform == "win32" else "XDG_DATA_HOME"
    monkeypatch.setenv(key, str(data_home))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return request.getfixturevalue("qapp")


def _quit_when(app: QApplication, ready: Any) -> QTimer:
    """맞춘 기록이 보이면(못 보아도 10초 뒤에) 이벤트 루프를 끝낸다."""
    timer = QTimer()
    elapsed = 0

    def tick() -> None:
        nonlocal elapsed
        elapsed += 20
        if ready() or elapsed >= 10_000:
            timer.stop()
            app.quit()

    timer.timeout.connect(tick)
    timer.start(20)
    return timer


def test_the_given_autostart_store_reaches_the_data_thread_and_is_matched_at_start(
    env: Any,
) -> None:
    paths = resolve_app_paths()
    paths.ensure()
    conn = open_database(paths.database_file)
    try:
        migrate(conn)
        view = SettingsView(
            ("KRW-AAA",), 30, True, candle_unit=CandleUnit.MINUTE_60, candle_count=100
        )
        write_settings(conn, view, autostart=True)
    finally:
        conn.close()
    store = FakeAutostart()
    services = Services(
        quote=FakeQuoteSource(),
        webhook=FakeDiscord(),
        vault=FakeVault(),
        clock=FakeClock(),
        autostart=store,
    )
    timer = _quit_when(env, lambda: bool(store.writes))
    assert run_gui([], services=services) == 0
    timer.stop()
    assert len(store.writes) == 1  # 켤 때 맞추기: 넘긴 가짜가 쓰였다
