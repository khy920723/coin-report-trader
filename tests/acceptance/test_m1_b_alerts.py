"""M1 명세 6절의 알림·디스코드 시나리오: B14, B15, B16, B23, B24, B25, B30, B36(3.8, 3.9, 3.10).

자료 스레드와 보내기 스레드를 가짜 시세, 가짜 디스코드, 가짜 보관소, 가짜 시계로 돌린다
(`acceptance.m1_support`). 주소처럼 생긴 글은 실행 중에 이어 붙여 만든다. 알림 표에 쓰지 못하는
일은 그 함수만 잠깐 바꿔 낸다.
"""

import threading
from datetime import timedelta
from pathlib import Path

import pytest
from acceptance.m1_support import (
    AAA,
    BBB,
    HOUR,
    NOW,
    THIS_HOUR,
    restart,
    token_of,
    until,
    webhook_url,
)
from fakes.discord import OK
from fakes.quote import make_candles

from coin_report_trader.app import data_thread
from coin_report_trader.app.data_thread import DATA_THREAD_NAME
from coin_report_trader.core import (
    AlertCategory,
    AlertsView,
    LoadState,
    Mode,
    QuoteError,
    StorageError,
    WebhookError,
    WebhookView,
)
from coin_report_trader.core.texts import (
    ERROR_DISCORD_TIMEOUT,
    ERROR_NETWORK,
    ERROR_TIMEOUT,
    ERROR_VAULT,
    STAGE_WEBHOOK_DELETE,
    STAGE_WEBHOOK_SAVE,
    TEST_FAILED,
    TEST_SENT,
    WEBHOOK_DELETE_FAILED,
    WEBHOOK_SAVE_FAILED,
)
from coin_report_trader.storage import alerts as alert_store
from coin_report_trader.storage.database import open_database
from coin_report_trader.storage.states import DiscordState
from coin_report_trader.vault import webhook_secret_name

ERROR, TRADE = AlertCategory.ERROR, AlertCategory.TRADE


def with_bars(rig, *coins: str) -> None:
    for coin in coins:
        rig.quote.set_candles(coin, HOUR, make_candles(coin, HOUR, 200, last_start=THIS_HOUR))


def fail_candles(rig, market: str = AAA, *errors: QuoteError) -> None:
    rig.quote.script(*(errors or (QuoteError(ERROR_NETWORK),)), kind="candles", market=market,
                     unit=HOUR)  # fmt: skip


def everything_written(rig) -> bytes:
    """DB(WAL 포함)와 로그 폴더의 모든 파일의 바이트."""
    found = b""
    for folder in (rig.db.parent, rig.logs):
        for path in sorted(Path(folder).rglob("*")):
            if path.is_file():
                found += path.read_bytes()
    return found


def discord_state(rig, alert_id: int) -> str:
    (row,) = rig.rows(f"SELECT discord_state FROM alerts WHERE id = {int(alert_id)}")
    return row[0]


def stored_alert(rig, category: AlertCategory) -> int:
    conn = open_database(rig.db)
    try:
        return alert_store.insert_alert(
            conn, created_at=NOW - timedelta(minutes=5), mode=Mode.PAPER, category=category,
            code="A-30", coin=None, text="시험 알림입니다.", detail=None, seen=False,
            discord_state=DiscordState.NOT_SENT,
        )  # fmt: skip
    finally:
        conn.close()


# --- B14: 주소의 비밀 ----------------------------------------------------------------------------


@pytest.mark.scenario("B14")
def test_b14_the_address_is_only_in_the_vault_and_the_message_is_short(
    rigs, monkeypatch: pytest.MonkeyPatch
) -> None:
    sqlite_threads: set[str] = set()
    real_open = data_thread.open_database

    def recording_open(path):
        sqlite_threads.add(threading.current_thread().name)
        return real_open(path)

    monkeypatch.setattr(data_thread, "open_database", recording_open)
    rig = rigs()
    with_bars(rig, AAA)
    rig.start()
    url = webhook_url("s")
    rig.send.save_webhook(ERROR, url)
    rig.settle()
    assert rig.vault.values[webhook_secret_name(ERROR)] == url
    fail_candles(rig)
    rig.data.retry_candles()
    rig.settle()
    (alert,) = rig.alerts()
    assert (alert.code, alert.category) == ("A-1", ERROR)
    (sent,) = rig.discord.alerts
    assert sent.url == url
    assert sent.text.split("\n")[0] == "[모의매매] 오류 알림"
    assert alert.detail is not None
    assert alert.detail.content not in sent.text  # 내용 전체는 보내지 않는다
    token = token_of(url)
    assert token.encode() not in everything_written(rig)  # app.db, 로그
    assert token not in alert.text
    assert token not in repr(rig.sink.items)  # 자료 스레드와 보내기 스레드가 화면에 보낸 값
    assert sqlite_threads == {DATA_THREAD_NAME}  # 자료 스레드만 SQLite를 쓴다


# --- B15: 주소가 없는 분류 -------------------------------------------------------------------


@pytest.mark.scenario("B15")
def test_b15_an_alert_of_a_category_without_an_address_is_not_sent(rigs) -> None:
    rig = rigs(saved={TRADE: webhook_url("t")})
    with_bars(rig, AAA)
    fail_candles(rig)
    rig.start()
    (alert,) = rig.alerts()
    assert alert.category is ERROR
    assert discord_state(rig, alert.id) == "no_address"
    assert rig.discord.sent == []  # 다른 분류의 주소로도 보내지 않는다


# --- B16: 디스코드 시간 초과 -----------------------------------------------------------------


@pytest.mark.scenario("B16")
def test_b16_a_timed_out_send_is_tried_once_marked_and_never_sent_again(rigs, tmp_path) -> None:
    rig = rigs(coins=(AAA, BBB), saved={ERROR: webhook_url("u")})
    with_bars(rig, AAA, BBB)
    rig.discord.script(WebhookError(ERROR_DISCORD_TIMEOUT))
    rig.discord.hold()  # 보내기가 오래 걸리는 동안
    fail_candles(rig, AAA)
    rig.send.start()
    rig.data.start()
    assert rig.discord.entered.wait(10), "보내기가 시작되지 않았다"
    # 그동안 다른 코인의 받기가 늦어지지 않는다(자료 스레드는 보내기를 기다리지 않는다).
    assert until(lambda: any(v.state is LoadState.READY for v in rig.sink.coin(BBB)))
    rig.discord.release()
    rig.settle()
    (alert,) = rig.alerts()
    assert len(rig.discord.sent) == 1  # 한 번만 시도한다
    assert discord_state(rig, alert.id) == "failed"
    assert rig.sink.of(AlertsView)[-1].alerts[0].discord_failed is True
    again = restart(rig, tmp_path)
    rigs.track(again)
    again.start()
    assert len(again.discord.sent) == 1  # 다시 켜도 보내지 않는다
    (shown,) = again.sink.of(AlertsView)[0].alerts
    assert shown.discord_failed is True  # 표시가 남는다


# --- B23·B24: 보관소의 실패 ------------------------------------------------------------------


@pytest.mark.scenario("B23")
def test_b23_a_vault_that_cannot_save_shows_the_line_and_makes_one_a29(rigs) -> None:
    rig = rigs()
    with_bars(rig, AAA)
    rig.start()
    rig.vault.fail_set(ERROR_VAULT)
    url = webhook_url("v")
    rig.send.save_webhook(ERROR, url)
    rig.settle()
    view = [v for v in rig.sink.of(WebhookView) if v.category is ERROR][-1]
    assert (view.saved, view.save_message) == (False, WEBHOOK_SAVE_FAILED)
    found = [(a.code, a.detail.stage) for a in rig.alerts() if a.detail]
    assert found == [("A-29", STAGE_WEBHOOK_SAVE)]
    token = token_of(url)
    assert token.encode() not in everything_written(rig)
    assert all(token not in a.text and token not in repr(a.detail) for a in rig.alerts())


@pytest.mark.scenario("B24")
def test_b24_a_vault_that_cannot_delete_keeps_the_address_and_makes_one_a29(rigs) -> None:
    url = webhook_url("w")
    rig = rigs(saved={ERROR: url})
    with_bars(rig, AAA)
    rig.start()
    rig.vault.fail_delete(ERROR_VAULT)
    rig.send.delete_webhook(ERROR)
    rig.settle()
    view = [v for v in rig.sink.of(WebhookView) if v.category is ERROR][-1]
    assert (view.saved, view.save_message) == (True, WEBHOOK_DELETE_FAILED)
    found = [(a.code, a.detail.stage) for a in rig.alerts() if a.detail]
    assert found == [("A-29", STAGE_WEBHOOK_DELETE)]
    assert rig.vault.values[webhook_secret_name(ERROR)] == url  # 저장된 주소는 그대로다


# --- B25: 확인용 글 -----------------------------------------------------------------------------


@pytest.mark.scenario("B25")
def test_b25_two_test_messages_show_sent_then_failed_and_make_no_alert(rigs) -> None:
    rig = rigs(saved={ERROR: webhook_url("x")})
    with_bars(rig, AAA)
    rig.start()
    rows = rig.rows("SELECT count(*) FROM alerts")
    rig.discord.script(OK, WebhookError(ERROR_DISCORD_TIMEOUT))
    messages = []
    for _ in range(2):
        rig.send.send_test(ERROR)
        rig.settle()
        messages.append([v for v in rig.sink.of(WebhookView) if v.category is ERROR][-1])
    assert len(rig.discord.tests) == 2
    assert [m.test_message for m in messages] == [TEST_SENT, TEST_FAILED]
    assert rig.rows("SELECT count(*) FROM alerts") == rows  # 알림 표의 줄 수는 바뀌지 않는다


# --- B30: 켤 때 남은 "not_sent" ----------------------------------------------------------------


@pytest.mark.scenario("B30")
def test_b30_leftover_unsent_alerts_are_settled_without_sending(rigs) -> None:
    rig = rigs(saved={ERROR: webhook_url("y")})
    with_bars(rig, AAA)
    with_address = stored_alert(rig, ERROR)
    without = stored_alert(rig, TRADE)
    rig.start()
    assert rig.discord.sent == []  # 둘 다 보내지 않는다
    assert discord_state(rig, with_address) == "failed"
    assert discord_state(rig, without) == "no_address"
    shown = {v.id: v.discord_failed for v in rig.sink.of(AlertsView)[0].alerts}
    assert shown == {with_address: True, without: False}


# --- B36: 메모리 알림 ----------------------------------------------------------------------------


@pytest.mark.scenario("B36")
def test_b36_memory_alerts_have_negative_ids_and_move_with_their_marks_next_time(
    rigs, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = rigs(saved={ERROR: webhook_url("z")})
    with_bars(rig, AAA)
    failing = {"on": True}
    real_insert = data_thread.alert_store.insert_alert

    def maybe(conn, **kwargs):
        if failing["on"]:
            raise StorageError("알림 저장에 실패했습니다: OperationalError")
        return real_insert(conn, **kwargs)

    monkeypatch.setattr(data_thread.alert_store, "insert_alert", maybe)
    fail_candles(rig)
    rig.start()  # 첫 메모리 알림(-1)
    failing["on"] = False
    rig.quote.script(QuoteError(ERROR_NETWORK), kind="markets")
    rig.data.refresh_markets()  # 저장이 한 번 성공해 되풀이가 풀린다
    rig.settle()
    failing["on"] = True
    rig.quote.script(QuoteError(ERROR_TIMEOUT), kind="markets")
    rig.data.refresh_markets()  # 둘째 메모리 알림(-2)
    rig.settle()
    memory = sorted(v.id for v in rig.sink.of(AlertsView)[-1].alerts if v.id < 0)
    assert memory == [-2, -1]
    rig.data.mark_seen((-2,))
    rig.data.delete_alert(-1)
    rig.settle()
    lines = {line["id"]: line for line in rig.jsonl()}
    assert set(lines) == {-2}  # 지운 것은 `startup-errors.jsonl`에서도 빠진다
    assert lines[-2]["seen"] is True
    sent_before = len(rig.discord.sent)
    failing["on"] = False
    again = restart(rig, tmp_path)
    rigs.track(again)
    again.start()
    assert len(again.discord.sent) == sent_before  # 다시 보내지 않는다
    assert again.jsonl() == []
    moved = again.rows("SELECT code, seen, discord_state FROM alerts WHERE code = 'A-29'")
    assert moved == [("A-29", 1, lines[-2]["discord_state"])]  # 적힌 표시대로 옮겨졌다
    assert all(v.id > 0 for v in again.sink.of(AlertsView)[0].alerts)
