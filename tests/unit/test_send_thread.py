"""보내기 스레드(M1 명세 3.8, 3.9, 3.10)만 따로: 결과 이름, 진짜 보내는 객체를 만드는 곳, 끝낼 때.

가짜 디스코드, 가짜 보관소, 가짜 시계, 자료 스레드 대신 결과를 적는 `Data`를 쓴다(네트워크,
keyring 없음). 주소처럼 생긴 글은 시험 안에서 이어 붙여 만든다.
"""

import subprocess
import sys
import textwrap
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from fakes.clock import FakeClock
from fakes.discord import FakeDiscord
from fakes.vault import FakeVault

from coin_report_trader.app import send_thread
from coin_report_trader.app.send_thread import SEND_THREAD_NAME, SendWorker, _SaveJob
from coin_report_trader.app.send_types import (
    AddressState,
    DeliveryResult,
    SendJob,
    WebhookResult,
)
from coin_report_trader.core import AlertCategory, UiUpdate, WebhookError, WebhookView
from coin_report_trader.core.texts import (
    ERROR_DISCORD_TIMEOUT,
    ERROR_VAULT,
    ERROR_VAULT_UNAVAILABLE,
    TEST_SENT,
)
from coin_report_trader.storage.states import DiscordState
from coin_report_trader.vault import UnavailableSecretStore, webhook_secret_name

SAFETY = 10.0
ERROR, TRADE = AlertCategory.ERROR, AlertCategory.TRADE


def webhook_url(tag: str = "a") -> str:
    return "https://" + "discord.com/api/webhooks/" + "1" * 18 + "/" + (tag * 64)[:64]


@dataclass
class Data:
    """자료 스레드의 자리: 받은 것을 적는다."""

    states: list[dict[AlertCategory, AddressState]] = field(default_factory=list)
    deliveries: list[DeliveryResult] = field(default_factory=list)
    webhooks: list[WebhookResult] = field(default_factory=list)

    def address_states(self, states: dict[AlertCategory, AddressState]) -> None:
        self.states.append(states)

    def delivery_result(self, result: DeliveryResult) -> None:
        self.deliveries.append(result)

    def webhook_result(self, result: WebhookResult) -> None:
        self.webhooks.append(result)


@dataclass
class Views:
    items: list[UiUpdate] = field(default_factory=list)

    def __call__(self, update: UiUpdate) -> None:
        self.items.append(update)


@dataclass
class Rig:
    worker: SendWorker
    data: Data
    views: Views
    discord: FakeDiscord
    vault: FakeVault
    clock: FakeClock

    def start(self) -> None:
        self.worker.start()
        assert self.worker.wait_idle(SAFETY)

    def settle(self) -> None:
        assert self.worker.wait_idle(SAFETY)


@pytest.fixture
def make() -> Iterator[Any]:
    made: list[Rig] = []

    def factory(vault: FakeVault | None = None, discord: FakeDiscord | None = None) -> Rig:
        discord = discord or FakeDiscord()
        vault = vault if vault is not None else FakeVault()
        clock, data, views = FakeClock(), Data(), Views()
        worker = SendWorker(webhook=discord, vault=vault, clock=clock, sink=views)
        worker.attach(data)
        rig = Rig(worker, data, views, discord, vault, clock)
        made.append(rig)
        return rig

    yield factory
    for rig in made:
        rig.discord.release()
        rig.worker.stop()
        rig.worker.join(SAFETY)
        assert rig.worker.thread is None or not rig.worker.thread.is_alive()


def job(category: AlertCategory = ERROR, alert_id: int = 7) -> SendJob:
    return SendJob(alert_id, category, f"[모의매매] {category.value} 알림\n글")


def test_the_states_of_every_category_are_given_to_the_data_thread_without_the_address(
    make: Any,
) -> None:
    secret = webhook_url("s")
    vault = FakeVault({webhook_secret_name(ERROR): secret})
    rig = make(vault)
    rig.start()
    (states,) = rig.data.states
    assert states == {c: AddressState.SAVED if c is ERROR else AddressState.NONE for c in states}
    assert set(states) == set(AlertCategory)
    assert secret not in repr(rig.data)
    assert secret not in repr(rig.views.items)


def test_an_unreadable_vault_gives_unreadable_states_and_views_that_are_not_saved(
    make: Any,
) -> None:
    vault = FakeVault({webhook_secret_name(ERROR): webhook_url("t")})
    vault.fail_get(ERROR_VAULT)
    rig = make(vault)
    rig.start()
    assert set(rig.data.states[0].values()) == {AddressState.UNREADABLE}
    assert all(
        isinstance(v, WebhookView) and not v.saved and v.save_message is None
        for v in rig.views.items
    )


def test_an_unavailable_store_object_is_the_same_as_an_unreadable_vault(make: Any) -> None:
    rig = make(UnavailableSecretStore(ERROR_VAULT_UNAVAILABLE))
    rig.start()
    assert set(rig.data.states[0].values()) == {AddressState.UNREADABLE}


def test_a_job_gives_sent_failed_or_no_address_and_reads_the_address_just_before(
    make: Any,
) -> None:
    url = webhook_url("u")
    vault = FakeVault({webhook_secret_name(ERROR): url})
    rig = make(vault)
    rig.discord.script(WebhookError(ERROR_DISCORD_TIMEOUT))
    rig.start()
    reads = len(vault.calls)
    rig.worker.submit(job(ERROR, 1))  # 시간 초과: 한 번만 시도한다
    rig.worker.submit(job(ERROR, 2))  # 성공
    rig.worker.submit(job(TRADE, 3))  # 주소 없음: 보내지 않는다
    rig.settle()
    assert [(r.alert_id, r.state) for r in rig.data.deliveries] == [
        (1, DiscordState.FAILED),
        (2, DiscordState.SENT),
        (3, DiscordState.NO_ADDRESS),
    ]
    assert [m.url for m in rig.discord.sent] == [url, url]
    assert len(vault.calls) == reads + 3  # 보낼 때마다 보관소에서 읽는다
    assert rig.discord.sent[0].text == job(ERROR, 1).message


def test_a_vault_read_failure_or_a_strange_error_is_failed_and_the_thread_goes_on(
    make: Any, caplog: pytest.LogCaptureFixture
) -> None:
    url = webhook_url("v")
    vault = FakeVault({webhook_secret_name(ERROR): url})
    rig = make(vault)
    rig.start()
    vault.fail_get(ERROR_VAULT, times=1)
    rig.worker.submit(job(ERROR, 1))
    rig.settle()
    vault.heal()

    def boom(target: str, text: str) -> None:
        raise RuntimeError(f"{target} 이상한 오류")

    rig.discord.send = boom  # type: ignore[method-assign]
    rig.worker.submit(job(ERROR, 2))
    rig.settle()
    assert [r.state for r in rig.data.deliveries] == [DiscordState.FAILED, DiscordState.FAILED]
    assert "RuntimeError" in caplog.text
    assert url.rsplit("/", 1)[1] not in caplog.text
    assert rig.worker.thread is not None
    assert rig.worker.thread.is_alive()


def test_the_results_of_a_save_and_a_delete_go_to_the_data_thread_by_category(make: Any) -> None:
    rig = make()
    rig.start()
    rig.worker.save_webhook(ERROR, webhook_url("w"))
    rig.worker.delete_webhook(ERROR)
    rig.settle()
    rig.vault.fail_set(ERROR_VAULT_UNAVAILABLE)
    rig.worker.save_webhook(TRADE, webhook_url("x"))
    rig.settle()
    assert rig.data.webhooks == [
        WebhookResult(ERROR, "주소 저장", True, None),
        WebhookResult(ERROR, "주소 지우기", True, None),
        WebhookResult(TRADE, "주소 저장", False, ERROR_VAULT_UNAVAILABLE),
    ]


def test_a_job_that_carries_an_address_hides_it_in_its_repr() -> None:
    url = webhook_url("y")
    assert url.rsplit("/", 1)[1] not in repr(_SaveJob(ERROR, url))
    assert "https" not in repr(_SaveJob(ERROR, url))


def test_every_result_is_a_new_view_with_both_texts(make: Any) -> None:
    rig = make(FakeVault({webhook_secret_name(TRADE): webhook_url("z")}))
    rig.start()
    rig.worker.send_test(TRADE)
    rig.settle()
    views = [v for v in rig.views.items if isinstance(v, WebhookView) and v.category is TRADE]
    assert views[-1].test_message == TEST_SENT
    assert len({id(v) for v in views}) == len(views)


def test_the_thread_is_a_named_daemon_and_stops_without_waiting_for_a_held_send(
    make: Any,
) -> None:
    rig = make(FakeVault({webhook_secret_name(ERROR): webhook_url("b")}))
    rig.discord.hold()
    rig.start()
    thread = rig.worker.thread
    assert thread is not None
    assert (thread.name, thread.daemon) == (SEND_THREAD_NAME, True)
    rig.worker.submit(job(ERROR, 1))
    assert rig.discord.entered.wait(SAFETY)
    rig.worker.stop()
    rig.discord.release()
    rig.worker.join(SAFETY)
    assert not thread.is_alive()
    assert rig.data.deliveries == []  # 멈춘 뒤의 결과는 버린다


def test_without_services_the_real_objects_are_made_in_the_thread_used_only_there_and_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made: dict[str, Any] = {}

    class FakeHttp(FakeDiscord):
        def __init__(self) -> None:
            super().__init__()
            made["sender_thread"] = threading.current_thread().name
            made["sender"] = self
            self.closed = False

        def close(self) -> None:
            self.closed = True

    def fake_store() -> FakeVault:
        made["store_thread"] = threading.current_thread().name
        return FakeVault({webhook_secret_name(ERROR): webhook_url("c")})

    monkeypatch.setattr(send_thread, "HttpWebhookSender", FakeHttp)
    monkeypatch.setattr(send_thread, "create_secret_store", fake_store)
    data = Data()
    worker = SendWorker(webhook=None, vault=None, clock=FakeClock(), sink=Views())
    worker.attach(data)
    assert made == {}  # 만들 때는 아무것도 만들지 않는다
    worker.start()
    assert worker.wait_idle(SAFETY)
    worker.submit(job(ERROR, 1))
    assert worker.wait_idle(SAFETY)
    worker.stop()
    worker.join(SAFETY)
    assert made["sender_thread"] == made["store_thread"] == SEND_THREAD_NAME
    assert made["sender"].closed
    assert [r.state for r in data.deliveries] == [DiscordState.SENT]


def test_importing_the_new_app_modules_creates_no_client_and_asks_no_vault() -> None:
    code = textwrap.dedent(
        """
        import importlib
        import os

        import httpx
        import keyring
        import keyring.core

        def stop(*args, **kwargs):
            os._exit(3)

        keyring.get_keyring = stop
        keyring.core.get_keyring = stop
        httpx.Client.__init__ = stop
        httpx.HTTPTransport.__init__ = stop

        for name in (
            "send_thread", "send_types", "memory_alerts", "actions", "data_thread",
            "startup_errors", "alert_rules",
        ):
            importlib.import_module("coin_report_trader.app." + name)
        importlib.import_module("coin_report_trader.app.application")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code], timeout=120, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
