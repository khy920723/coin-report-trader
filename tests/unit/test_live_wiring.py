"""5단계 잇기: 조립이 보관소와 업비트 운반을 자료 스레드에 넘긴다(M5 명세 3.4의 `Services`,
4절 15번). 창은 띄우지 않는다(자료 스레드를 만드는 자리에서 멈춘다)."""

import sys
from pathlib import Path
from typing import Any

import pytest
from fakes.clock import FakeClock
from fakes.discord import FakeDiscord
from fakes.quote import FakeQuoteSource
from fakes.upbit import FakeUpbitTransport
from fakes.vault import FakeVault

from coin_report_trader.app import data_thread
from coin_report_trader.app.application import run_gui
from coin_report_trader.app.services import Services


class _Stop(Exception):
    pass


def test_the_exchange_transport_and_the_vault_reach_the_data_thread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    key = "LOCALAPPDATA" if sys.platform == "win32" else "XDG_DATA_HOME"
    monkeypatch.setenv(key, str(tmp_path))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    request.getfixturevalue("qapp")
    seen: dict[str, Any] = {}

    def record(**kwargs: Any) -> object:
        seen.update(kwargs)
        raise _Stop

    monkeypatch.setattr(data_thread, "DataWorker", record)
    transport = FakeUpbitTransport()
    vault = FakeVault()
    services = Services(
        quote=FakeQuoteSource(),
        webhook=FakeDiscord(),
        vault=vault,
        clock=FakeClock(),
        exchange_transport=transport,
    )
    with pytest.raises(_Stop):
        run_gui([], services=services)
    assert seen["exchange_transport"] is transport
    assert seen["vault"] is vault
    assert seen["real_vault"] is True  # 보관소를 넣지 않으면 진짜 보관소를 쓴다


def test_a_data_thread_without_a_vault_never_reads_one(tmp_path: Path) -> None:
    worker = data_thread.DataWorker(
        database_file=tmp_path / "app.db",
        logs_dir=tmp_path,
        clock=FakeClock(),
        quote=None,
        sink=lambda update: None,
    )
    assert worker._secret_store() is None  # 시험의 자료 스레드는 진짜 보관소를 만들지 않는다
