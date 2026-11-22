"""잇기 2: 알림센터(M1 명세 3.8, 3.9, 11절). 자료 스레드와 보내기 스레드를 함께 돌린다.

Qt 없이 `DataWorker`와 `SendWorker`를 직접 돌린다. 가짜 시세, 가짜 디스코드, 가짜 보관소, 가짜
시계만 쓴다(네트워크, keyring, 진짜 웹후크 없음). 주소처럼 생긴 글은 시험 안에서 이어 붙여 만든다.
시각은 가짜 시계의 `advance`로만 옮긴다(실제 시간은 멈추지 않게 하는 안전 시간으로만 쓴다).
"""

import json
import logging
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fakes.clock import DEFAULT_START, FakeClock
from fakes.discord import OK, FakeDiscord
from fakes.quote import FakeQuoteSource, make_candles
from fakes.vault import FakeVault

from coin_report_trader.app import data_thread, startup_errors
from coin_report_trader.app.data_thread import DATA_THREAD_NAME, DataWorker
from coin_report_trader.app.memory_alerts import MemoryAlerts
from coin_report_trader.app.send_thread import SEND_THREAD_NAME, SendWorker
from coin_report_trader.core import (
    AlertCategory,
    AlertsView,
    CandleUnit,
    CoinView,
    LoadState,
    Market,
    Mode,
    QuoteError,
    SaveResult,
    SettingsView,
    StorageError,
    UiUpdate,
    WebhookError,
    WebhookView,
)
from coin_report_trader.core.texts import (
    ERROR_DISCORD_RATE_LIMIT,
    ERROR_DISCORD_TIMEOUT,
    ERROR_MAINTENANCE,
    ERROR_NETWORK,
    ERROR_SERVER,
    ERROR_TIMEOUT,
    ERROR_VAULT,
    ERROR_VAULT_UNAVAILABLE,
    TEST_FAILED,
    TEST_SENDING,
    TEST_SENT,
    WEBHOOK_BAD_FORMAT,
    WEBHOOK_DELETE_FAILED,
    WEBHOOK_SAVE_FAILED,
)
from coin_report_trader.storage import alerts as alert_store
from coin_report_trader.storage.database import migrate, open_database
from coin_report_trader.storage.settings import write_settings
from coin_report_trader.storage.states import DiscordState
from coin_report_trader.vault import webhook_secret_name

AAA, BBB = "KRW-AAA", "KRW-BBB"
DAY, HOUR = CandleUnit.DAY, CandleUnit.MINUTE_60
SAFETY = 10.0
NOW = DEFAULT_START
TODAY_START = NOW.replace(hour=0, minute=0)
THIS_HOUR = NOW.replace(minute=0)
ERROR, TRADE, OTHER = AlertCategory.ERROR, AlertCategory.TRADE, AlertCategory.OTHER


def webhook_url(tag: str = "a") -> str:
    """주소의 꼴을 지킨 시험용 글. 시험 안에서 이어 붙인다(저장소에 주소의 꼴을 두지 않는다)."""
    return "https://" + "discord.com/api/webhooks/" + "1" * 18 + "/" + (tag * 64)[:64]


def token_of(url: str) -> str:
    return url.rsplit("/", 1)[1]


def secret(category: AlertCategory) -> str:
    return webhook_secret_name(category)


@dataclass
class Sink:
    items: list[UiUpdate] = field(default_factory=list)
    threads: dict[str, set[str]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __call__(self, update: UiUpdate) -> None:
        with self.lock:
            self.items.append(update)
            self.threads.setdefault(type(update).__name__, set()).add(
                threading.current_thread().name
            )

    def of(self, kind: type) -> list[Any]:
        with self.lock:
            return [u for u in self.items if isinstance(u, kind)]

    def webhooks(self, category: AlertCategory) -> list[WebhookView]:
        return [v for v in self.of(WebhookView) if v.category is category]

    def clear(self) -> None:
        with self.lock:
            self.items.clear()


class RecordingVault(FakeVault):
    """부른 스레드의 이름을 적는 가짜 보관소."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.threads: set[str] = set()

    def get(self, name: str) -> str | None:
        self.threads.add(threading.current_thread().name)
        return super().get(name)

    def set(self, name: str, value: str) -> None:
        self.threads.add(threading.current_thread().name)
        super().set(name, value)

    def delete(self, name: str) -> None:
        self.threads.add(threading.current_thread().name)
        super().delete(name)


@dataclass
class Rig:
    data: DataWorker
    send: SendWorker
    sink: Sink
    clock: FakeClock
    quote: FakeQuoteSource
    discord: FakeDiscord
    vault: RecordingVault
    db: Path
    logs: Path

    def start(self) -> None:
        self.send.start()
        self.data.start()
        self.settle()

    def settle(self) -> None:
        """두 스레드가 모두 쉴 때까지. 자료 → 보내기 → 자료로 오가는 일이 끝나야 한다."""
        for _ in range(100):
            assert self.data.wait_idle(SAFETY), "자료 스레드가 쉬지 않는다"
            assert self.send.wait_idle(SAFETY), "보내기 스레드가 쉬지 않는다"
            if self.data.wait_idle(SAFETY) and self.send.wait_idle(0.05):
                return
        raise AssertionError("두 스레드가 끝내 쉬지 않는다")

    def advance(self, seconds: float) -> None:
        self.clock.advance(seconds)
        self.settle()

    def stop(self) -> None:
        self.data.stop()
        self.send.stop()
        self.data.join(SAFETY)
        self.send.join(SAFETY)

    def rows(self, sql: str = "SELECT id, category, code, discord_state, seen FROM alerts") -> Any:
        conn = open_database(self.db)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def alerts(self) -> list[Any]:
        conn = open_database(self.db)
        try:
            return list(reversed(alert_store.list_alerts(conn)))
        finally:
            conn.close()

    def state_of(self, alert_id: int) -> str:
        (row,) = self.rows(f"SELECT discord_state FROM alerts WHERE id = {int(alert_id)}")
        return row[0]

    def last_alerts_view(self) -> AlertsView:
        return self.sink.of(AlertsView)[-1]

    def jsonl(self) -> list[dict[str, Any]]:
        path = self.logs / startup_errors.FILE_NAME
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line]


def markets(*codes: str) -> list[Market]:
    return [Market(code, f"이름 {code}", False) for code in codes]


def make_rig(
    tmp_path: Path,
    *,
    coins: tuple[str, ...] = (AAA,),
    days: int | None = 30,
    saved: dict[AlertCategory, str] | None = None,
    discord: FakeDiscord | None = None,
    vault: RecordingVault | None = None,
    clock: FakeClock | None = None,
    fresh: bool = True,
) -> Rig:
    db = tmp_path / "app.db"
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    if fresh:
        conn = open_database(db)
        try:
            migrate(conn)
            write_settings(
                conn, SettingsView(coins, days, True, candle_unit=HOUR, candle_count=100)
            )
        finally:
            conn.close()
    clock = clock or FakeClock()
    quote = FakeQuoteSource(markets=markets(AAA, BBB), clock=clock)
    for coin in coins:
        quote.set_candles(coin, DAY, make_candles(coin, DAY, 40, last_start=TODAY_START))
        quote.set_candles(coin, HOUR, make_candles(coin, HOUR, 200, last_start=THIS_HOUR))
    discord = discord or FakeDiscord()
    if vault is None:
        vault = RecordingVault({secret(c): url for c, url in (saved or {}).items()})
    sink = Sink()
    send = SendWorker(webhook=discord, vault=vault, clock=clock, sink=sink)
    data = DataWorker(
        database_file=db, logs_dir=logs, clock=clock, quote=quote, sink=sink, send=send
    )
    send.attach(data)
    return Rig(data, send, sink, clock, quote, discord, vault, db, logs)


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Callable[..., Rig]]:
    made: list[Rig] = []

    def factory(**kwargs: Any) -> Rig:
        rig = make_rig(tmp_path, **kwargs)
        made.append(rig)
        return rig

    yield factory
    for rig in made:
        rig.discord.release()
        rig.stop()
        for worker in (rig.data, rig.send):
            assert worker.thread is None or not worker.thread.is_alive()


def candles_fail(rig: Rig, *errors: Any) -> None:
    rig.quote.script(*errors, kind="candles", market=AAA, unit=HOUR)


def make_stored_alert(
    rig: Rig,
    *,
    category: AlertCategory = ERROR,
    state: DiscordState = DiscordState.NOT_SENT,
    age: timedelta = timedelta(0),
    seen: bool = False,
    code: str = "A-1",
) -> int:
    conn = open_database(rig.db)
    try:
        return alert_store.insert_alert(
            conn,
            created_at=NOW - age,
            mode=Mode.PAPER,
            category=category,
            code=code,
            coin=None,
            text="시험 알림입니다.",
            detail=None,
            seen=seen,
            discord_state=state,
        )
    finally:
        conn.close()


def until(condition: Callable[[], bool], limit: float = SAFETY) -> bool:
    waiter = threading.Event()
    for _ in range(int(limit / 0.002)):
        if condition():
            return True
        waiter.wait(0.002)
    return False


# --- 켤 때 차례(3.8): 주소 상태, 남은 "not_sent", 줄을 알림으로 옮기기 --------


def test_the_first_webhook_views_come_from_the_send_thread_per_category(
    build: Callable[..., Rig],
) -> None:
    rig = build(saved={ERROR: webhook_url()})
    rig.start()
    views = rig.sink.of(WebhookView)
    assert [v.category for v in views[:6]] == list(AlertCategory)  # 분류마다 하나, 탭의 차례
    assert {v.category: v.saved for v in views[:6]} == {c: c is ERROR for c in AlertCategory}
    assert all(v.save_message is None and v.test_message is None for v in views)
    assert rig.sink.threads["WebhookView"] == {SEND_THREAD_NAME}
    assert rig.sink.threads["AlertsView"] == {DATA_THREAD_NAME}
    assert rig.sink.threads["SettingsView"] == {DATA_THREAD_NAME}


def test_leftover_not_sent_alerts_become_failed_or_no_address_and_are_not_sent(
    build: Callable[..., Rig],
) -> None:  # B30
    rig = build(saved={ERROR: webhook_url()})
    with_address = make_stored_alert(rig, category=ERROR)
    without = make_stored_alert(rig, category=TRADE)
    rig.start()
    assert rig.state_of(with_address) == "failed"
    assert rig.state_of(without) == "no_address"
    assert rig.discord.sent == []
    shown = {v.id: v.discord_failed for v in rig.sink.of(AlertsView)[0].alerts}
    assert shown == {with_address: True, without: False}


def test_the_data_thread_waits_for_the_address_states_before_its_first_views(
    build: Callable[..., Rig],
) -> None:
    rig = build(saved={ERROR: webhook_url("l")})
    leftover = make_stored_alert(rig, category=ERROR)
    rig.data.start()  # 보내기 스레드는 아직 시작하지 않았다
    threading.Event().wait(0.1)
    assert rig.sink.items == []
    assert rig.state_of(leftover) == "not_sent"
    rig.send.start()
    rig.settle()
    assert rig.state_of(leftover) == "failed"  # 주소가 저장된 분류
    others = [type(u).__name__ for u in rig.sink.items if not isinstance(u, WebhookView)]
    assert others[:2] == ["SettingsView", "CoinView"]


def test_a_state_that_never_comes_is_taken_as_unreadable_and_the_start_goes_on(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(data_thread, "STATES_WAIT_SECONDS", 0.05)
    rig = build()
    leftover = make_stored_alert(rig, category=TRADE)
    rig.data.start()  # 보내기 스레드가 오지 않는다
    assert until(lambda: rig.sink.of(AlertsView) != [])
    assert rig.state_of(leftover) == "failed"  # 주소 상태를 모르면 읽지 못한 것으로 본다
    assert rig.sink.of(SettingsView)


def test_leftover_not_sent_alerts_are_failed_when_the_vault_cannot_be_read(
    build: Callable[..., Rig],
) -> None:
    vault = RecordingVault()
    vault.fail_get(ERROR_VAULT)
    rig = build(vault=vault)
    first = make_stored_alert(rig, category=ERROR)
    second = make_stored_alert(rig, category=TRADE)
    rig.start()
    assert (rig.state_of(first), rig.state_of(second)) == ("failed", "failed")
    assert [v.saved for v in rig.sink.of(WebhookView)[:6]] == [False] * 6
    assert rig.discord.sent == []


def test_the_first_alerts_view_comes_after_the_leftovers_and_the_lines_are_settled(
    build: Callable[..., Rig],
) -> None:
    rig = build(saved={ERROR: webhook_url()})
    leftover = make_stored_alert(rig, category=ERROR)
    line = startup_errors.startup_line(
        StorageError("DB 열기에 실패했습니다: OperationalError"), NOW
    )
    startup_errors.append_line(rig.logs, line)
    rig.start()
    kinds = [type(u).__name__ for u in rig.sink.items if type(u).__name__ != "WebhookView"]
    assert kinds[:3] == ["SettingsView", "CoinView", "AlertsView"]
    first = rig.sink.of(AlertsView)[0]
    assert {v.id: v.discord_failed for v in first.alerts}[leftover] is True
    assert len(first.alerts) == 2  # 옮긴 줄도 이미 들어 있다
    assert rig.sink.of(AlertsView)[0].unseen[ERROR] == 2


def test_startup_lines_are_local_only_and_never_sent(build: Callable[..., Rig]) -> None:
    rig = build(saved={ERROR: webhook_url()})  # B19
    line = startup_errors.startup_line(
        StorageError("DB 열기에 실패했습니다: OperationalError"), NOW
    )
    startup_errors.append_line(rig.logs, line)
    rig.start()
    ((_, category, code, state, seen),) = rig.rows()
    assert (category, code, state, seen) == ("error", "A-29", "local_only", 0)
    assert rig.discord.sent == []
    assert rig.jsonl() == []
    assert rig.sink.of(AlertsView)[0].alerts[0].discord_failed is False


def make_memory_line(
    created: str,
    *,
    identifier: int,
    category: str = "error",
    seen: bool = False,
    state: str | None = None,
) -> dict[str, Any]:
    line = startup_errors.startup_line(StorageError("알림 저장에 실패했습니다"), NOW)
    line.update(
        kind="memory", created_utc=created, id=identifier, seen=seen, discord_state=state,
        category=category, stage="알림 저장", after="이 알림은 이번 실행 동안 보이고, 다음에 켤 때 "
        "알림센터에 옮깁니다.",
    )  # fmt: skip
    return line


def test_memory_lines_move_with_their_marks_and_are_not_sent_again(
    build: Callable[..., Rig],
) -> None:  # B36
    rig = build(saved={ERROR: webhook_url()})
    startup_errors.append_line(
        rig.logs, make_memory_line("2026-01-03T01:02:03Z", identifier=-1, seen=True, state="sent")
    )
    startup_errors.append_line(
        rig.logs, make_memory_line("2026-01-04T01:02:03Z", identifier=-2, state="no_address")
    )
    startup_errors.append_line(  # 결과가 적혀 있지 않다: 남은 "not_sent"처럼 그 분류의 주소 상태로
        rig.logs, make_memory_line("2026-01-04T02:02:03Z", identifier=-3, state=None)
    )
    startup_errors.append_line(
        rig.logs,
        make_memory_line("2026-01-04T03:02:03Z", identifier=-4, category="trade", state=None),
    )
    rig.start()
    conn = open_database(rig.db)
    try:
        rows = conn.execute(
            "SELECT created_utc, category, seen, discord_state FROM alerts ORDER BY created_utc"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [
        ("2026-01-03T01:02:03Z", "error", 1, "sent"),
        ("2026-01-04T01:02:03Z", "error", 0, "no_address"),
        ("2026-01-04T02:02:03Z", "error", 0, "failed"),  # 주소가 저장된 분류
        ("2026-01-04T03:02:03Z", "trade", 0, "no_address"),  # 주소가 없는 분류
    ]
    assert rig.discord.sent == []
    assert rig.jsonl() == []
    assert rig.last_alerts_view().unseen[ERROR] == 2


def test_actions_queued_before_the_start_run_after_the_first_alerts_view(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    alert_id = make_stored_alert(rig, category=TRADE, state=DiscordState.NO_ADDRESS)
    rig.data.mark_seen((alert_id,))
    rig.data.delete_alert(alert_id)
    rig.start()
    views = rig.sink.of(AlertsView)
    assert [len(v.alerts) for v in views] == [1, 1, 0]  # 처음 → 본 표시 → 지움
    assert views[0].unseen[TRADE] == 1
    assert views[1].unseen[TRADE] == 0
    assert rig.rows() == []


# --- 보내기: 주소는 분류마다, 한 번만, 자료 스레드를 늦추지 않는다 --------


def test_an_error_alert_goes_to_the_error_address_with_a_first_line_and_no_content(
    build: Callable[..., Rig],
) -> None:  # B14
    url = webhook_url("t")
    rig = build()
    rig.start()
    rig.send.save_webhook(ERROR, f"  {url}  ")  # 앞뒤 빈칸은 지운다
    rig.settle()
    assert rig.vault.values == {secret(ERROR): url}
    candles_fail(rig, QuoteError(ERROR_NETWORK))
    rig.data.retry_candles()
    rig.settle()
    (sent,) = rig.discord.alerts
    assert sent.url == url
    lines = sent.text.split("\n")
    assert lines[0] == "[모의매매] 오류 알림"
    assert len(lines) == 3
    assert "실패한 봉" not in sent.text  # 내용 전체는 보내지 않는다
    (row,) = rig.rows()
    assert row[3] == "sent"
    # 주소의 토큰은 DB, 로그, 알림, 화면에 보낸 값, 자료 스레드가 다룬 값 어디에도 없다.
    token = token_of(url)
    rig.stop()  # 연결을 닫은 뒤에 파일을 읽는다(Windows는 열려 있는 파일을 막을 수 있다)
    for path in rig.db.parent.glob("app.db*"):  # DB와 그 옆의 WAL 파일
        assert token.encode() not in path.read_bytes()
    assert not any(token in repr(u) for u in rig.sink.items)
    for path in rig.logs.glob("*"):
        assert token not in path.read_text(encoding="utf-8")
    assert token not in repr(rig.data.__dict__)
    assert token not in repr(rig.send)


def test_the_token_is_not_in_the_log_or_the_alert_when_the_send_fails(
    build: Callable[..., Rig], caplog: pytest.LogCaptureFixture
) -> None:
    url = webhook_url("u")
    rig = build(saved={ERROR: url})
    rig.discord.fail(ERROR_DISCORD_TIMEOUT)
    candles_fail(rig, QuoteError(ERROR_NETWORK))
    with caplog.at_level(logging.DEBUG):
        rig.start()
    assert token_of(url) not in caplog.text
    assert token_of(url) not in repr(rig.sink.items)
    assert ERROR_DISCORD_TIMEOUT in caplog.text  # 로그에는 오류 이름만


def test_an_alert_is_not_sent_to_another_category_address(build: Callable[..., Rig]) -> None:
    rig = build(saved={TRADE: webhook_url("c")})  # B15
    candles_fail(rig, QuoteError(ERROR_NETWORK))
    rig.start()
    (row,) = rig.rows()
    assert row[3] == "no_address"
    assert rig.discord.sent == []
    assert rig.last_alerts_view().alerts[0].discord_failed is False  # 표시가 붙지 않는다
    rig.send.save_webhook(ERROR, webhook_url("d"))  # 뒤에 주소를 넣어도 보내지 않는다
    rig.settle()
    assert rig.discord.sent == []
    assert rig.state_of(row[0]) == "no_address"


def test_a_failed_send_is_tried_once_marked_and_never_sent_again(
    build: Callable[..., Rig], tmp_path: Path
) -> None:  # B16
    url = webhook_url("e")
    rig = build(saved={ERROR: url})
    rig.discord.script(WebhookError(ERROR_DISCORD_TIMEOUT))
    candles_fail(rig, QuoteError(ERROR_NETWORK))
    rig.start()
    assert len(rig.discord.sent) == 1  # 한 번만 시도한다
    (row,) = rig.rows()
    assert row[3] == "failed"
    assert rig.last_alerts_view().alerts[0].discord_failed is True
    rig.advance(3600)  # 다음 받기는 성공이다. 같은 오류가 아니므로 새 알림도 없다
    assert len(rig.discord.sent) == 1
    rig.stop()
    second = build(saved={ERROR: url}, discord=FakeDiscord(), fresh=False)  # 끄고 다시 켠다
    second.start()
    assert second.discord.sent == []
    assert second.rows()[0][3] == "failed"  # 표시가 남는다
    assert second.sink.of(AlertsView)[0].alerts[0].discord_failed is True


def test_a_held_send_does_not_slow_the_fetch_of_the_data_thread(
    build: Callable[..., Rig],
) -> None:  # B16: 보내기가 막혀도 받기와 저장이 늦어지지 않는다
    rig = build(saved={ERROR: webhook_url("f")})
    rig.discord.hold()
    candles_fail(rig, QuoteError(ERROR_NETWORK))
    rig.data.start()  # 자료 스레드는 보내기 스레드의 주소 상태를 기다린다
    rig.send.start()
    assert rig.discord.entered.wait(SAFETY)  # 보내기 스레드가 디스코드 안에서 멈췄다
    assert rig.data.wait_idle(SAFETY)
    requests = len(rig.quote.candle_requests(AAA))
    rig.sink.clear()
    rig.clock.advance(3610)  # (라)의 때(04:00:10): 보내기가 막힌 채로 받는다
    assert rig.data.wait_idle(SAFETY)
    assert len(rig.quote.candle_requests(AAA)) > requests
    assert [v.state for v in rig.sink.of(CoinView)][-1] is LoadState.READY
    assert rig.send.wait_idle(0.05) is False  # 보내기는 아직 막혀 있다
    assert rig.rows()[0][3] == "not_sent"
    rig.discord.release()
    rig.settle()
    assert rig.rows()[0][3] == "sent"


def test_a_suppressed_repeat_alert_is_not_made_and_not_sent(build: Callable[..., Rig]) -> None:
    rig = build(saved={ERROR: webhook_url("g")})
    candles_fail(rig, QuoteError(ERROR_NETWORK), QuoteError(ERROR_NETWORK))
    rig.start()
    rig.advance(3600)  # 같은 코인에 같은 오류 이름이다. 받기가 성공하기 전이라 다시 만들지 않는다
    assert len(rig.rows()) == 1
    assert len(rig.discord.alerts) == 1


def test_the_result_of_a_deleted_alert_is_only_logged(
    build: Callable[..., Rig], caplog: pytest.LogCaptureFixture
) -> None:
    rig = build(saved={ERROR: webhook_url("h")})
    rig.discord.hold()
    candles_fail(rig, QuoteError(ERROR_NETWORK))
    rig.send.start()
    rig.data.start()
    assert rig.discord.entered.wait(SAFETY)
    assert rig.data.wait_idle(SAFETY)
    (first,) = rig.rows()
    rig.data.delete_alert(first[0])  # 보내기 스레드에 넘긴 뒤 손으로 지운다
    assert rig.data.wait_idle(SAFETY)
    with caplog.at_level(logging.INFO):
        rig.discord.release()
        rig.settle()
    assert len(rig.discord.alerts) == 1  # 그 글은 보낸다
    assert rig.rows() == []  # 표에 쓰지 않았다
    assert f"지운 알림 {first[0]}의 보낸 결과(sent)" in caplog.text
    candles_fail(rig, QuoteError(ERROR_NETWORK))  # 새 알림은 지운 번호를 받지 않는다
    rig.data.retry_candles()
    rig.settle()
    # 같은 오류 이름은 되풀이가 막는다: 코인을 바꿔 새 알림을 만든다
    assert all(row[0] != first[0] for row in rig.rows())


def test_a_rate_limit_is_retried_once_after_the_wait_without_moving_the_data_clock(
    build: Callable[..., Rig],
) -> None:
    rig = build(saved={ERROR: webhook_url("i")})
    rig.discord.fail(ERROR_DISCORD_RATE_LIMIT, retry_after=2.0, times=1)
    candles_fail(rig, QuoteError(ERROR_NETWORK))
    rig.send.start()
    rig.data.start()
    assert until(lambda: 2.0 in rig.clock.waits)  # 보내기 스레드가 시계로 기다린다
    moment, mono = rig.clock.now(), rig.clock.monotonic()
    assert (moment, mono) == (NOW, rig.clock.monotonic())  # 보내기 스레드가 시각을 옮기지 않는다
    assert rig.clock.sleeps == []
    assert len(rig.discord.sent) == 1
    rig.clock.advance(2.0)
    rig.settle()
    assert [m.error_name for m in rig.discord.sent] == [ERROR_DISCORD_RATE_LIMIT, None]
    assert rig.rows()[0][3] == "sent"


def test_the_idle_send_thread_never_uses_the_clock(build: Callable[..., Rig]) -> None:
    rig = build()
    rig.start()
    assert rig.send.thread is not None
    assert rig.send.thread.name == SEND_THREAD_NAME
    assert rig.send.thread.daemon
    assert rig.clock.sleeps == []
    waits_before = list(rig.clock.waits)
    rig.send.send_test(ERROR)  # 주소가 없는 분류: 보내지 않고 끝난다
    rig.settle()
    assert rig.clock.waits == waits_before


def test_only_the_data_thread_uses_sqlite_and_only_the_send_thread_uses_the_vault_and_discord(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: set[str] = set()
    real_open = data_thread.open_database

    def recording_open(path: Path) -> Any:
        opened.add(threading.current_thread().name)
        return real_open(path)

    monkeypatch.setattr(data_thread, "open_database", recording_open)
    sender_threads: set[str] = set()
    rig = build(saved={ERROR: webhook_url("j")})
    real_send = rig.discord.send

    def recording_send(url: str, text: str) -> None:
        sender_threads.add(threading.current_thread().name)
        real_send(url, text)

    rig.discord.send = recording_send  # type: ignore[method-assign]
    candles_fail(rig, QuoteError(ERROR_NETWORK))
    rig.start()
    rig.send.save_webhook(TRADE, webhook_url("k"))
    rig.send.send_test(TRADE)
    rig.settle()
    assert opened == {DATA_THREAD_NAME}
    assert rig.vault.threads == {SEND_THREAD_NAME}
    assert sender_threads == {SEND_THREAD_NAME}
    assert rig.discord.tests  # 확인용 글도 보냈다


# --- 보았다는 표시와 지우기 --------


def test_seen_marks_stay_after_a_restart_and_the_unseen_count_follows(
    build: Callable[..., Rig],
) -> None:  # B18
    rig = build()
    ids = [make_stored_alert(rig, category=OTHER, state=DiscordState.NO_ADDRESS) for _ in range(3)]
    rig.start()
    rig.data.mark_seen((ids[0], ids[1]))
    rig.settle()
    assert sum(rig.last_alerts_view().unseen.values()) == 1
    rig.stop()
    again = build(fresh=False)
    again.start()
    view = again.sink.of(AlertsView)[0]
    assert {v.id: v.seen for v in view.alerts} == {ids[0]: True, ids[1]: True, ids[2]: False}
    assert sum(view.unseen.values()) == 1


def test_a_seen_mark_for_a_deleted_row_changes_nothing_else(build: Callable[..., Rig]) -> None:
    rig = build()
    keep = make_stored_alert(rig, category=OTHER, state=DiscordState.NO_ADDRESS)
    gone = make_stored_alert(rig, category=OTHER, state=DiscordState.NO_ADDRESS)
    rig.start()
    rig.data.delete_alert(gone)
    rig.data.mark_seen((gone,))  # 화면이 지우기 전의 `AlertsView`로 부른 것
    rig.settle()
    view = rig.last_alerts_view()
    assert [(v.id, v.seen) for v in view.alerts] == [(keep, False)]


def test_deleted_rows_do_not_come_back_and_new_alerts_do_not_reuse_the_numbers(
    build: Callable[..., Rig],
) -> None:  # B26
    rig = build()
    one = make_stored_alert(rig, category=OTHER, state=DiscordState.NO_ADDRESS)
    two = make_stored_alert(rig, category=OTHER, state=DiscordState.NO_ADDRESS)
    biggest = make_stored_alert(rig, category=TRADE, state=DiscordState.NO_ADDRESS)
    rig.start()
    rig.data.delete_alert(biggest)  # 가장 큰 번호를 지운다
    rig.data.clear_alerts(OTHER)
    rig.settle()
    results = rig.sink.of(SaveResult)
    assert [(r.target, r.ok) for r in results] == [("alert_delete", True)] * 2
    assert rig.last_alerts_view().alerts == ()
    rig.stop()
    again = build(fresh=False)
    candles_fail(again, QuoteError(ERROR_NETWORK))
    again.start()
    assert again.last_alerts_view().alerts[0].id > biggest  # 지운 번호를 받지 않는다
    assert {row[0] for row in again.rows()}.isdisjoint({one, two, biggest})
    assert len(again.rows()) == 1


def test_clearing_one_category_keeps_the_others_and_the_candles(build: Callable[..., Rig]) -> None:
    rig = build()
    make_stored_alert(rig, category=OTHER, state=DiscordState.NO_ADDRESS)
    keep = make_stored_alert(rig, category=TRADE, state=DiscordState.NO_ADDRESS)
    rig.start()
    stored = len(rig.quote.candle_requests(AAA))
    rig.data.clear_alerts(OTHER)
    rig.settle()
    assert [v.id for v in rig.last_alerts_view().alerts] == [keep]
    assert len(rig.quote.candle_requests(AAA)) == stored


def test_a_failed_delete_shows_a_message_makes_one_a29_and_keeps_the_rows(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    keep = make_stored_alert(rig, category=TRADE, state=DiscordState.NO_ADDRESS)
    rig.start()
    real = data_thread.alert_store.delete_alert
    failing = {"on": True}

    def maybe(conn: Any, alert_id: int) -> bool:
        if failing["on"]:
            raise StorageError("알림 지우기에 실패했습니다: OperationalError")
        return real(conn, alert_id)

    monkeypatch.setattr(data_thread.alert_store, "delete_alert", maybe)
    rig.sink.clear()
    rig.data.delete_alert(keep)
    rig.settle()
    result = rig.sink.of(SaveResult)[0]
    assert result == SaveResult("alert_delete", False, "지우지 못했습니다(DB 오류).")
    made = [a for a in rig.alerts() if a.code == "A-29"]
    assert len(made) == 1
    assert made[0].detail is not None
    assert made[0].detail.stage == "알림 지우기"
    assert made[0].text == "지우지 못했습니다(DB 오류)."
    assert made[0].detail.after == "지우지 못한 알림은 남겨 두었습니다."
    assert keep in {v.id for v in rig.last_alerts_view().alerts}
    rig.data.delete_alert(keep)  # 성공하기 전에는 다시 만들지 않는다
    rig.settle()
    assert len([a for a in rig.alerts() if a.code == "A-29"]) == 1
    failing["on"] = False
    rig.data.delete_alert(keep)
    rig.settle()
    assert keep not in {v.id for v in rig.last_alerts_view().alerts}
    failing["on"] = True
    other = make_stored_alert(rig, category=TRADE, state=DiscordState.NO_ADDRESS)
    rig.data.delete_alert(other)  # 한 번 성공했으니 새 실패는 다시 만든다
    rig.settle()
    assert len([a for a in rig.alerts() if a.code == "A-29"]) == 2


def test_a_failed_clear_is_the_same_failure(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    keep = make_stored_alert(rig, category=TRADE, state=DiscordState.NO_ADDRESS)
    rig.start()

    def refuse(conn: Any, category: AlertCategory) -> int:
        raise StorageError("알림 지우기에 실패했습니다: OperationalError")

    monkeypatch.setattr(data_thread.alert_store, "delete_category", refuse)
    rig.sink.clear()
    rig.data.clear_alerts(TRADE)
    rig.settle()
    assert rig.sink.of(SaveResult)[0].ok is False
    assert keep in {v.id for v in rig.last_alerts_view().alerts}
    assert [a.code for a in rig.alerts() if a.code == "A-29"] == ["A-29"]


def test_a_failed_alert_read_gives_a_failed_view_and_an_a30_for_the_work_being_done(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    make_stored_alert(rig, category=TRADE, state=DiscordState.NO_ADDRESS)

    def unreadable(conn: Any) -> Any:
        raise StorageError("알림 읽기에 실패했습니다: OperationalError")

    monkeypatch.setattr(data_thread.alert_store, "list_alerts", unreadable)
    rig.start()
    first = rig.sink.of(AlertsView)[0]
    assert (first.state, first.alerts) == (LoadState.FAILED, ())
    sql = "SELECT stage, error_name FROM alerts WHERE code = 'A-30' ORDER BY id"
    # 만든 A-30의 보낸 결과를 쓴 뒤의 읽기도 실패한다: 그때 하던 일("알림 저장")의 단계다
    assert rig.rows(sql) == [("켜기", "DB 오류"), ("알림 저장", "DB 오류")]
    rig.sink.clear()
    rig.data.mark_seen((1,))  # 같은 단계의 같은 예외 종류는 60분 안에 다시 만들지 않는다
    rig.settle()
    assert rig.sink.of(AlertsView)[0].state is LoadState.FAILED
    assert len(rig.rows(sql)) == 2
    rig.data.delete_alert(1)  # 다른 단계는 따로 센다
    rig.settle()
    assert [stage for stage, _ in rig.rows(sql)][-1] == "알림 지우기"


# --- 오래된 알림 지우기(3.9, B17) --------


def test_old_alerts_are_deleted_when_the_retention_is_saved_not_before(
    build: Callable[..., Rig],
) -> None:  # B17
    rig = build(days=None)
    ages = {"old": 8, "recent": 6, "ancient": 400}
    ids = {
        name: make_stored_alert(
            rig, category=TRADE, state=DiscordState.NO_ADDRESS, age=timedelta(days=days)
        )
        for name, days in ages.items()
    }
    old, recent = ids["old"], ids["recent"]
    rig.start()
    everything = set(ids.values())
    assert {row[0] for row in rig.rows()} == everything  # 보관 기간을 고르기 전에는 지우지 않는다
    rig.advance(3600)
    assert {row[0] for row in rig.rows()} == everything
    stored = rig.rows("SELECT COUNT(*) FROM candles")[0][0]
    assert stored > 0
    rig.data.save_settings((AAA,), 7, first_setup=False)
    rig.settle()
    assert {row[0] for row in rig.rows()} == {recent}  # 고르기 전에 생긴 알림도 생긴 때부터 잰다
    assert old not in {v.id for v in rig.last_alerts_view().alerts}
    assert [v.id for v in rig.last_alerts_view().alerts] == [recent]
    assert rig.rows("SELECT COUNT(*) FROM candles")[0][0] >= stored  # 봉은 그대로다


def test_old_alerts_are_deleted_at_window_up_and_every_sixty_minutes(
    build: Callable[..., Rig],
) -> None:
    rig = build(days=7)
    old = make_stored_alert(
        rig, category=TRADE, state=DiscordState.NO_ADDRESS, age=timedelta(days=8)
    )
    soon = make_stored_alert(
        rig,
        category=TRADE,
        state=DiscordState.NO_ADDRESS,
        age=timedelta(days=7) - timedelta(minutes=30),
    )
    rig.start()
    assert {row[0] for row in rig.rows()} == {soon}  # 창이 뜬 직후
    first = rig.sink.of(AlertsView)[0]  # 차례 4(지우기)는 첫 `AlertsView`보다 앞이다
    assert [v.id for v in first.alerts] == [soon]
    assert old not in {v.id for v in first.alerts}
    rig.advance(3600)  # 60분 뒤
    assert rig.rows() == []
    assert rig.last_alerts_view().alerts == ()
    assert old not in {v.id for v in rig.last_alerts_view().alerts}


def test_old_alerts_are_not_deleted_while_the_settings_are_unreadable(
    build: Callable[..., Rig],
) -> None:
    rig = build(days=7)
    make_stored_alert(rig, category=TRADE, state=DiscordState.NO_ADDRESS, age=timedelta(days=30))
    conn = open_database(rig.db)
    try:
        conn.execute("UPDATE settings SET value = '깨짐' WHERE key = 'coins'")
        conn.commit()
    finally:
        conn.close()
    rig.start()
    assert rig.sink.of(SettingsView)[0].state is LoadState.FAILED
    assert len(rig.rows()) == 2  # 낡은 알림과 A-30
    rig.advance(3600)
    assert len([r for r in rig.rows() if r[2] != "A-30"]) == 1  # 보관 기간을 모르니 지우지 않는다


def test_a_failed_old_alert_deletion_is_an_a29_of_the_delete_stage(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build(days=7)

    def refuse(conn: Any, **kwargs: Any) -> int:
        raise StorageError("오래된 알림 지우기에 실패했습니다: OperationalError")

    monkeypatch.setattr(data_thread.alert_store, "delete_expired", refuse)
    rig.start()
    (alert,) = rig.alerts()
    assert alert.code == "A-29"
    assert alert.detail is not None
    assert alert.detail.stage == "알림 지우기"
    rig.advance(3600)  # 성공하기 전에는 다시 만들지 않는다
    assert len(rig.rows()) == 1


# --- 메모리 알림(3.9, B36) --------


@pytest.fixture
def refuse_a1(monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    """A-1의 줄을 표에 쓰지 못하게 한다(`failing["on"]`을 거짓으로 하면 푼다)."""
    real = data_thread.alert_store.insert_alert
    failing = {"on": True}

    def maybe(conn: Any, **kwargs: Any) -> int:
        if failing["on"]:
            raise StorageError("알림 저장에 실패했습니다: OperationalError")
        return real(conn, **kwargs)

    monkeypatch.setattr(data_thread.alert_store, "insert_alert", maybe)
    return failing


def test_an_alert_that_cannot_be_saved_becomes_a_negative_memory_alert_that_is_sent_and_written(
    build: Callable[..., Rig], refuse_a1: dict[str, bool]
) -> None:
    rig = build(saved={ERROR: webhook_url("m")})
    candles_fail(rig, QuoteError(ERROR_NETWORK))
    rig.start()
    view = rig.last_alerts_view()
    (shown,) = view.alerts
    assert shown.id == -1
    assert (shown.code, shown.category, shown.coin) == ("A-29", ERROR, None)
    assert shown.text == "저장하지 못했습니다(DB 오류)."
    assert shown.detail is not None
    assert shown.detail.stage == "알림 저장"
    assert (
        shown.detail.after == "이 알림은 이번 실행 동안 보이고, 다음에 켤 때 알림센터에 옮깁니다."
    )
    assert view.unseen[ERROR] == 1
    assert rig.rows() == []  # 표에는 없다
    (sent,) = rig.discord.alerts  # 디스코드로도 보낸다
    assert sent.text.split("\n")[0] == "[모의매매] 오류 알림"
    (line,) = rig.jsonl()
    assert line["kind"] == "memory"
    assert (line["id"], line["seen"], line["discord_state"]) == (-1, False, "sent")
    assert (line["code"], line["category"], line["stage"]) == ("A-29", "error", "알림 저장")
    assert line["created_utc"] == "2026-01-05T03:00:00Z"
    assert len(line) == 15  # 열한 키와 `id`, `seen`, `discord_state`, `source`


def test_the_a29_of_an_alert_save_is_made_once_until_a_save_succeeds_and_numbers_are_not_reused(
    build: Callable[..., Rig], refuse_a1: dict[str, bool]
) -> None:
    rig = build()
    candles_fail(rig, QuoteError(ERROR_NETWORK))
    rig.start()
    rig.data.mark_seen((-1,))
    rig.data.delete_alert(-1)
    rig.settle()
    assert rig.last_alerts_view().alerts == ()
    assert rig.jsonl() == []  # 지운 것은 파일에서도 빠진다
    rig.quote.script(QuoteError(ERROR_TIMEOUT), kind="candles", market=AAA, unit=HOUR)
    rig.data.retry_candles()  # 저장이 계속 실패한다: 성공하기 전에는 다시 만들지 않는다
    rig.settle()
    assert rig.last_alerts_view().alerts == ()
    refuse_a1["on"] = False  # 저장이 한 번 성공하면 푼다
    rig.quote.script(QuoteError(ERROR_SERVER), kind="candles", market=AAA, unit=HOUR)
    rig.data.retry_candles()
    rig.settle()
    assert len(rig.rows()) == 1
    refuse_a1["on"] = True
    rig.quote.script(QuoteError(ERROR_MAINTENANCE), kind="candles", market=AAA, unit=HOUR)
    rig.data.retry_candles()
    rig.settle()
    ids = [v.id for v in rig.last_alerts_view().alerts]
    assert -2 in ids
    assert -1 not in ids  # 지운 번호는 다시 주지 않는다


def test_marks_and_deletes_of_memory_alerts_are_kept_in_the_file_and_the_rest_move_next_time(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:  # B36
    url = webhook_url("n")
    rig = build(saved={ERROR: url})
    failing = {"on": True}
    real = data_thread.alert_store.insert_alert

    def maybe(conn: Any, **kwargs: Any) -> int:
        if failing["on"]:
            raise StorageError("알림 저장에 실패했습니다: OperationalError")
        return real(conn, **kwargs)

    monkeypatch.setattr(data_thread.alert_store, "insert_alert", maybe)
    candles_fail(rig, QuoteError(ERROR_NETWORK))
    rig.start()
    # 두 번째 메모리 알림: 한 번 성공한 뒤 다시 실패한다
    failing["on"] = False
    rig.data.refresh_markets()  # 성공한 저장으로 되풀이를 푼다
    rig.quote.script(QuoteError(ERROR_NETWORK), kind="markets")
    rig.settle()
    failing["on"] = True
    rig.quote.script(QuoteError("시간 초과"), kind="markets")
    rig.data.refresh_markets()
    rig.settle()
    ids = sorted(v.id for v in rig.last_alerts_view().alerts if v.id < 0)
    assert ids[:2] == [-2, -1] or ids == [-1]
    rig.data.mark_seen((-1,))
    rig.settle()
    lines = {line["id"]: line for line in rig.jsonl()}
    assert lines[-1]["seen"] is True
    if -2 in lines:
        rig.data.delete_alert(-2)
        rig.settle()
        assert -2 not in {line["id"] for line in rig.jsonl()}
    rig.stop()
    failing["on"] = False
    again = build(saved={ERROR: url}, fresh=False)
    again.start()
    assert again.discord.sent == []  # 다시 보내지 않는다
    assert again.jsonl() == []
    moved = list(again.rows("SELECT code, seen, discord_state, created_utc FROM alerts"))
    assert ("A-29", 1, "sent", "2026-01-05T03:00:00Z") in moved
    assert all(r[2] != "not_sent" for r in moved)
    assert all(v.id > 0 for v in again.sink.of(AlertsView)[0].alerts)


def test_a_memory_alert_is_still_shown_when_the_file_cannot_be_written(
    build: Callable[..., Rig], refuse_a1: dict[str, bool], caplog: pytest.LogCaptureFixture
) -> None:
    rig = build()
    (rig.logs / startup_errors.FILE_NAME).mkdir()  # 파일을 쓸 수 없다
    candles_fail(rig, QuoteError(ERROR_NETWORK))
    with caplog.at_level(logging.WARNING):
        rig.start()
    assert [v.id for v in rig.last_alerts_view().alerts] == [-1]
    rig.data.mark_seen((-1,))
    rig.settle()
    assert rig.last_alerts_view().alerts[0].seen is True  # 이 실행 동안은 메모리에서 센다


def test_clearing_a_category_also_clears_its_memory_alerts_and_their_lines(
    build: Callable[..., Rig], refuse_a1: dict[str, bool]
) -> None:
    rig = build()
    candles_fail(rig, QuoteError(ERROR_NETWORK))
    rig.start()
    assert [v.id for v in rig.last_alerts_view().alerts] == [-1]
    assert len(rig.jsonl()) == 1
    rig.data.clear_alerts(TRADE)  # 다른 분류의 지우기는 건드리지 않는다
    rig.settle()
    assert [v.id for v in rig.last_alerts_view().alerts] == [-1]
    rig.data.clear_alerts(ERROR)
    rig.settle()
    assert rig.last_alerts_view().alerts == ()
    assert rig.jsonl() == []


def test_the_result_of_a_deleted_memory_alert_is_only_logged(
    build: Callable[..., Rig], refuse_a1: dict[str, bool], caplog: pytest.LogCaptureFixture
) -> None:
    rig = build(saved={ERROR: webhook_url("o")})
    rig.discord.hold()
    candles_fail(rig, QuoteError(ERROR_NETWORK))
    rig.send.start()
    rig.data.start()
    assert rig.discord.entered.wait(SAFETY)
    assert rig.data.wait_idle(SAFETY)
    rig.data.delete_alert(-1)
    assert rig.data.wait_idle(SAFETY)
    with caplog.at_level(logging.INFO):
        rig.discord.release()
        rig.settle()
    assert rig.last_alerts_view().alerts == ()
    assert rig.jsonl() == []  # 줄이 되살아나지 않는다
    assert "지운 메모리 알림 -1의 보낸 결과(sent)" in caplog.text


def test_memory_alert_numbers_start_at_minus_one_and_count_down() -> None:
    memory = MemoryAlerts()
    made = [
        memory.add(
            created_at=NOW, mode=Mode.PAPER, category=ERROR, code="A-29", coin=None, text="글",
            detail=None,
        )
        for _ in range(3)
    ]  # fmt: skip
    assert [item.id for item in made] == [-1, -2, -3]
    memory.remove(-3)
    again = memory.add(
        created_at=NOW, mode=Mode.PAPER, category=ERROR, code="A-29", coin=None, text="글",
        detail=None,
    )  # fmt: skip
    assert again.id == -4


# --- 주소 저장, 지우기, 확인용 글 --------


def test_a_saved_address_goes_to_the_vault_and_every_result_is_a_new_view(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    url = webhook_url("p")
    before = rig.sink.webhooks(ERROR)[-1]
    rig.send.save_webhook(ERROR, url)
    rig.settle()
    saved = rig.sink.webhooks(ERROR)[-1]
    assert saved is not before
    assert (saved.saved, saved.save_message, saved.test_message) == (True, None, None)
    assert rig.vault.values == {secret(ERROR): url}
    rig.send.delete_webhook(ERROR)
    rig.settle()
    deleted = rig.sink.webhooks(ERROR)[-1]
    assert deleted is not saved
    assert (deleted.saved, deleted.save_message) == (False, None)
    assert rig.vault.values == {}
    assert rig.rows() == []  # 성공은 알림을 만들지 않는다


def test_a_bad_format_is_shown_without_touching_the_vault(build: Callable[..., Rig]) -> None:
    rig = build()
    rig.start()
    calls = len(rig.vault.calls)
    rig.send.save_webhook(ERROR, "https://example.test/x")
    rig.settle()
    view = rig.sink.webhooks(ERROR)[-1]
    assert (view.saved, view.save_message) == (False, WEBHOOK_BAD_FORMAT)
    assert len(rig.vault.calls) == calls
    assert rig.rows() == []


def test_a_failed_address_save_shows_the_message_and_makes_one_a29_per_category(
    build: Callable[..., Rig],
) -> None:  # B23
    rig = build()
    rig.start()
    rig.vault.fail_set(ERROR_VAULT_UNAVAILABLE)
    first, second = webhook_url("q"), webhook_url("r")
    rig.send.save_webhook(ERROR, first)
    rig.settle()
    view = rig.sink.webhooks(ERROR)[-1]
    assert (view.saved, view.save_message) == (False, WEBHOOK_SAVE_FAILED)
    (alert,) = rig.alerts()
    assert (alert.code, alert.category, alert.coin) == ("A-29", ERROR, None)
    assert alert.text == "저장하지 못했습니다(보관소를 쓸 수 없음)."
    assert alert.detail is not None
    assert (alert.detail.stage, alert.detail.error_name) == ("주소 저장", ERROR_VAULT_UNAVAILABLE)
    assert alert.detail.after == "주소는 저장되지 않았습니다. 다시 넣어 저장하세요."
    rig.send.save_webhook(ERROR, second)  # 성공하기 전에는 다시 만들지 않는다
    rig.send.save_webhook(TRADE, second)  # 다른 분류는 따로 센다
    rig.settle()
    assert [a.code for a in rig.alerts()] == ["A-29", "A-29"]
    rig.vault.heal()
    rig.send.save_webhook(ERROR, first)
    rig.settle()
    assert rig.vault.values == {secret(ERROR): first}
    rig.vault.fail_set(ERROR_VAULT)
    rig.send.save_webhook(ERROR, second)  # 한 번 성공했으니 새 실패는 다시 만든다
    rig.settle()
    assert len(rig.alerts()) == 3
    rig.stop()
    for token in (token_of(first), token_of(second)):
        assert token.encode() not in rig.db.read_bytes()
        assert all(token not in a.text for a in rig.alerts())
        assert all(token not in (a.detail.content if a.detail else "") for a in rig.alerts())
        assert not any(token in repr(u) for u in rig.sink.items)


def test_a_failed_address_delete_keeps_the_address_and_makes_an_a29(
    build: Callable[..., Rig],
) -> None:  # B24
    url = webhook_url("s")
    rig = build(saved={ERROR: url})
    rig.start()
    rig.vault.fail_delete(ERROR_VAULT)
    rig.send.delete_webhook(ERROR)
    rig.settle()
    view = rig.sink.webhooks(ERROR)[-1]
    assert (view.saved, view.save_message) == (True, WEBHOOK_DELETE_FAILED)
    assert rig.vault.values == {secret(ERROR): url}
    (alert,) = rig.alerts()
    assert alert.text == "지우지 못했습니다(보관소 오류)."
    assert alert.detail is not None
    assert (alert.detail.stage, alert.detail.after) == (
        "주소 지우기", "저장된 주소는 그대로 남아 있습니다.",
    )  # fmt: skip
    assert token_of(url) not in alert.detail.content


def test_the_a29_of_an_address_alert_goes_to_the_error_address_once_it_is_stored(
    build: Callable[..., Rig],
) -> None:
    rig = build(saved={ERROR: webhook_url("v")})
    rig.start()
    rig.vault.fail_delete(ERROR_VAULT)
    rig.send.delete_webhook(TRADE)  # 주소가 없는 분류의 지우기가 실패했다
    rig.settle()
    (sent,) = rig.discord.alerts
    third = sent.text.split("\n")[2]
    assert third == (
        "단계: 주소 지우기 · 오류: 보관소 오류 · 그 뒤에 한 일: 저장된 주소는 그대로 남아 있습니다."
    )


def test_save_message_and_test_message_are_kept_apart_and_sent_together(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    rig.vault.fail_set(ERROR_VAULT)
    rig.send.save_webhook(ERROR, webhook_url("w"))
    rig.settle()
    rig.vault.heal()
    rig.send.save_webhook(ERROR, webhook_url("x"))
    rig.send.send_test(ERROR)
    rig.settle()
    views = rig.sink.webhooks(ERROR)[-3:]
    assert [(v.saved, v.save_message, v.test_message) for v in views] == [
        (True, None, None),  # 저장 성공
        (True, None, TEST_SENDING),  # 확인용 글을 보내는 중
        (True, None, TEST_SENT),
    ]
    rig.vault.fail_delete(ERROR_VAULT)
    rig.send.delete_webhook(ERROR)
    rig.settle()
    last = rig.sink.webhooks(ERROR)[-1]
    assert (last.save_message, last.test_message) == (WEBHOOK_DELETE_FAILED, TEST_SENT)
    rig.send.send_test(ERROR)
    rig.settle()
    last = rig.sink.webhooks(ERROR)[-1]
    assert (last.save_message, last.test_message) == (WEBHOOK_DELETE_FAILED, TEST_SENT)


def test_a_new_address_clears_the_old_test_result_and_a_delete_clears_it_too(
    build: Callable[..., Rig],
) -> None:
    rig = build(saved={TRADE: webhook_url("1")})
    rig.start()
    rig.send.send_test(TRADE)
    rig.settle()
    assert rig.sink.webhooks(TRADE)[-1].test_message == TEST_SENT
    rig.send.save_webhook(TRADE, webhook_url("2"))
    rig.settle()
    assert rig.sink.webhooks(TRADE)[-1].test_message is None
    rig.send.send_test(TRADE)
    rig.settle()
    rig.send.delete_webhook(TRADE)
    rig.settle()
    assert rig.sink.webhooks(TRADE)[-1].test_message is None


def test_two_confirmation_messages_one_sent_and_one_failed_and_no_alert_rows(
    build: Callable[..., Rig],
) -> None:  # B25
    url = webhook_url("y")
    rig = build(saved={TRADE: url})
    rig.discord.script(OK, WebhookError(ERROR_DISCORD_TIMEOUT))
    rig.start()
    count = len(rig.rows())
    rig.send.send_test(TRADE)
    rig.settle()
    rig.send.send_test(TRADE)
    rig.settle()
    assert [(m.url, m.error_name) for m in rig.discord.tests] == [
        (url, None), (url, ERROR_DISCORD_TIMEOUT),
    ]  # fmt: skip
    texts = [v.test_message for v in rig.sink.webhooks(TRADE)[1:]]
    assert texts == [TEST_SENDING, TEST_SENT, TEST_SENDING, TEST_FAILED]
    assert all(ERROR_DISCORD_TIMEOUT not in (t or "") for t in texts)  # 오류 이름은 글에 없다
    assert len(rig.rows()) == count == 0  # `alerts` 표의 줄 수는 바뀌지 않고 알림도 없다
    assert rig.discord.alerts == []
    assert "[Coin Report Trader] 확인용 글입니다." in rig.discord.tests[0].text


def test_a_confirmation_message_without_a_readable_address_fails_without_an_alert(
    build: Callable[..., Rig],
) -> None:
    rig = build(saved={TRADE: webhook_url("z")})
    rig.start()
    rig.vault.fail_get(ERROR_VAULT)
    rig.send.send_test(TRADE)
    rig.settle()
    assert rig.sink.webhooks(TRADE)[-1].test_message == TEST_FAILED
    assert rig.discord.sent == []
    assert rig.rows() == []


def test_the_send_thread_survives_a_sender_that_raises_something_else(
    build: Callable[..., Rig],
) -> None:
    rig = build(saved={ERROR: webhook_url("b")})

    def boom(url: str, text: str) -> None:
        raise RuntimeError(f"{url} 이상한 오류")

    rig.discord.send = boom  # type: ignore[method-assign]
    candles_fail(rig, QuoteError(ERROR_NETWORK))
    rig.start()
    assert rig.rows()[0][3] == "failed"
    rig.send.send_test(ERROR)
    rig.settle()
    assert rig.sink.webhooks(ERROR)[-1].test_message == TEST_FAILED
    assert rig.send.thread is not None
    assert rig.send.thread.is_alive()


# --- 끝낼 때 --------


def test_stopping_does_not_wait_for_a_held_send_and_a_stopped_thread_posts_nothing(
    build: Callable[..., Rig],
) -> None:
    rig = build(saved={ERROR: webhook_url("c")})
    rig.discord.hold()
    candles_fail(rig, QuoteError(ERROR_NETWORK))
    rig.send.start()
    rig.data.start()
    assert rig.discord.entered.wait(SAFETY)
    rig.data.stop()
    rig.send.stop()
    rig.data.join(SAFETY)
    count = len(rig.sink.items)
    rig.discord.release()  # 막힌 보내기가 풀린 뒤에도 결과를 쓰지도 보내지도 않는다
    rig.send.join(SAFETY)
    assert rig.send.thread is not None
    assert not rig.send.thread.is_alive()
    assert len(rig.sink.items) == count
    assert rig.rows()[0][3] == "not_sent"


def test_a_rate_limit_wait_ends_at_once_when_the_thread_is_stopped(
    build: Callable[..., Rig],
) -> None:
    rig = build(saved={ERROR: webhook_url("d")})
    rig.discord.fail(ERROR_DISCORD_RATE_LIMIT, retry_after=30.0)
    candles_fail(rig, QuoteError(ERROR_NETWORK))
    rig.send.start()
    rig.data.start()
    assert until(lambda: 30.0 in rig.clock.waits)
    rig.send.stop()
    rig.send.join(SAFETY)
    assert rig.send.thread is not None
    assert not rig.send.thread.is_alive()
    assert len(rig.discord.sent) == 1  # 멈춘 뒤에는 다시 보내지 않는다


def test_the_settings_view_and_coin_views_still_come_first(build: Callable[..., Rig]) -> None:
    rig = build(saved={ERROR: webhook_url("e")})
    rig.start()
    others = [type(u).__name__ for u in rig.sink.items if not isinstance(u, WebhookView)]
    assert others[:3] == ["SettingsView", "CoinView", "AlertsView"]


# --- 알림 표의 쓰기 실패는 쓰기의 종류마다 A-29 하나("알림 저장", 3.9의 되풀이) ------------------


def alert_save_a29(rig: Rig) -> list[Any]:
    return rig.rows(
        "SELECT id, discord_state FROM alerts WHERE code = 'A-29' AND stage = '알림 저장'"
    )


def test_a_failing_seen_mark_makes_one_a29_until_a_seen_mark_succeeds(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    target = make_stored_alert(rig, category=TRADE, state=DiscordState.NO_ADDRESS)
    rig.start()
    real = data_thread.alert_store.mark_seen
    failing = {"on": True}

    def maybe(conn: Any, alert_id: int) -> bool:
        if failing["on"]:
            raise StorageError("보았다는 표시 쓰기에 실패했습니다: OperationalError")
        return real(conn, alert_id)

    monkeypatch.setattr(data_thread.alert_store, "mark_seen", maybe)
    rig.data.mark_seen((target,))
    rig.settle()
    rig.data.mark_seen((target,))  # 그 A-29를 표에 쓴 것은 되풀이를 풀지 않는다
    rig.settle()
    assert len(alert_save_a29(rig)) == 1
    failing["on"] = False
    rig.data.mark_seen((target,))  # 같은 쓰기가 성공하면 푼다
    rig.settle()
    failing["on"] = True
    rig.data.mark_seen((target,))
    rig.settle()
    assert len(alert_save_a29(rig)) == 2


def test_a_seen_mark_that_fails_together_with_the_alert_write_is_kept_in_memory_once(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build(saved={ERROR: webhook_url("q")})
    target = make_stored_alert(rig, category=TRADE, state=DiscordState.NO_ADDRESS)
    rig.start()

    def refuse_mark(conn: Any, alert_id: int) -> bool:
        raise StorageError("보았다는 표시 쓰기에 실패했습니다: OperationalError")

    def refuse_insert(conn: Any, **kwargs: Any) -> int:
        raise StorageError("알림 저장에 실패했습니다: OperationalError")

    monkeypatch.setattr(data_thread.alert_store, "mark_seen", refuse_mark)
    monkeypatch.setattr(data_thread.alert_store, "insert_alert", refuse_insert)
    rig.data.mark_seen((target,))
    rig.settle()
    shown = [v for v in rig.last_alerts_view().alerts if v.id < 0]
    assert [(v.code, v.detail.stage if v.detail else None) for v in shown] == [
        ("A-29", "알림 저장")
    ]
    assert [line["kind"] for line in rig.jsonl()] == ["memory"]
    assert alert_save_a29(rig) == []  # 표와 메모리를 합쳐 A-29("알림 저장")는 하나다
    rig.data.mark_seen((target,))
    rig.settle()
    assert len([v for v in rig.last_alerts_view().alerts if v.id < 0]) == 1
    assert len(rig.discord.alerts) == 1  # 디스코드로도 보낸다


def test_a_discord_state_that_cannot_be_written_is_one_a29_and_its_own_result_adds_none(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build(saved={ERROR: webhook_url("r")})

    def refuse(conn: Any, alert_id: int, state: Any) -> bool:
        raise StorageError("디스코드 결과 저장에 실패했습니다: OperationalError")

    monkeypatch.setattr(data_thread.alert_store, "set_discord_state", refuse)
    candles_fail(rig, QuoteError(ERROR_NETWORK))
    rig.start()
    assert len(alert_save_a29(rig)) == 1
    assert len(rig.discord.alerts) == 2  # A-1과 그 A-29를 보냈다. 결과를 쓰지 못해도 더는 안 는다


def test_leftover_not_sent_that_cannot_be_settled_is_an_a29_in_the_first_alerts_view(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build(saved={ERROR: webhook_url("s")})
    make_stored_alert(rig, category=ERROR)

    def refuse(conn: Any, category: AlertCategory, state: Any) -> int:
        raise StorageError("디스코드 결과 정리에 실패했습니다: OperationalError")

    monkeypatch.setattr(data_thread.alert_store, "settle_not_sent", refuse)
    rig.start()
    first = rig.sink.of(AlertsView)[0]
    made = [v for v in first.alerts if v.code == "A-29"]
    assert [v.detail.stage if v.detail else None for v in made] == [
        "알림 저장"
    ]  # 분류마다 늘지 않는다
    assert len(alert_save_a29(rig)) == 1


def test_the_a29_of_a_failed_step_two_is_not_settled_by_the_same_step_and_is_sent(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build(saved={ERROR: webhook_url("t")})
    make_stored_alert(rig, category=TRADE)
    real = data_thread.alert_store.settle_not_sent

    def only_trade_fails(conn: Any, category: AlertCategory, state: Any) -> int:
        if category is TRADE:
            raise StorageError("디스코드 결과 정리에 실패했습니다: OperationalError")
        return real(conn, category, state)

    monkeypatch.setattr(data_thread.alert_store, "settle_not_sent", only_trade_fails)
    rig.discord.hold()  # 보낸 결과가 오기 전의 모습을 본다
    rig.send.start()
    rig.data.start()
    assert rig.discord.entered.wait(SAFETY)
    assert rig.data.wait_idle(SAFETY)
    (made,) = [v for v in rig.sink.of(AlertsView)[0].alerts if v.code == "A-29"]
    assert made.discord_failed is False  # 이 실행에서 만든 알림은 "보내지 못함"이 아니다
    (row,) = [r for r in rig.rows() if r[2] == "A-29"]
    assert row[3] == "not_sent"
    rig.discord.release()
    rig.settle()
    assert rig.state_of(row[0]) == "sent"  # 보낸 결과가 오면 그대로 바뀐다


def test_several_failed_categories_of_step_two_make_one_a29_even_with_successes_between(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    make_stored_alert(rig, category=TRADE)
    real = data_thread.alert_store.settle_not_sent

    def trade_and_other_fail(conn: Any, category: AlertCategory, state: Any) -> int:
        if category in (TRADE, OTHER):
            raise StorageError("디스코드 결과 정리에 실패했습니다: OperationalError")
        return real(conn, category, state)

    monkeypatch.setattr(data_thread.alert_store, "settle_not_sent", trade_and_other_fail)
    rig.start()
    first = rig.sink.of(AlertsView)[0]
    assert [v.code for v in first.alerts if v.code == "A-29"] == ["A-29"]
    assert len(alert_save_a29(rig)) == 1


def test_a_successful_seen_mark_does_not_free_the_repeat_of_an_alert_write(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    target = make_stored_alert(rig, category=TRADE, state=DiscordState.NO_ADDRESS)
    rig.start()

    def refuse_insert(conn: Any, **kwargs: Any) -> int:
        raise StorageError("알림 저장에 실패했습니다: OperationalError")

    monkeypatch.setattr(data_thread.alert_store, "insert_alert", refuse_insert)
    candles_fail(rig, QuoteError(ERROR_NETWORK))
    rig.data.retry_candles()
    rig.settle()
    assert len([v for v in rig.last_alerts_view().alerts if v.id < 0]) == 1
    rig.data.mark_seen((target,))  # 표시 쓰기의 성공은 알림 쓰기의 되풀이를 풀지 않는다
    rig.settle()
    candles_fail(rig, QuoteError(ERROR_SERVER))
    rig.data.retry_candles()
    rig.settle()
    assert len([v for v in rig.last_alerts_view().alerts if v.id < 0]) == 1


def test_a_successful_seen_mark_does_not_free_the_repeat_of_a_discord_state_write(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build(saved={ERROR: webhook_url("u")})
    target = make_stored_alert(rig, category=TRADE, state=DiscordState.NO_ADDRESS)

    def refuse(conn: Any, alert_id: int, state: Any) -> bool:
        raise StorageError("디스코드 결과 저장에 실패했습니다: OperationalError")

    monkeypatch.setattr(data_thread.alert_store, "set_discord_state", refuse)
    candles_fail(rig, QuoteError(ERROR_NETWORK))
    rig.start()
    assert len(alert_save_a29(rig)) == 1
    rig.data.mark_seen((target,))  # 표시 쓰기의 성공은 `discord_state` 쓰기의 되풀이를 풀지 않는다
    rig.settle()
    candles_fail(rig, QuoteError(ERROR_SERVER))
    rig.data.retry_candles()
    rig.settle()
    assert len(alert_save_a29(rig)) == 1


def test_the_memory_a29_of_a_failed_step_two_is_not_moved_to_the_table_in_the_same_start(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build(saved={ERROR: webhook_url("v")})
    make_stored_alert(rig, category=TRADE)

    def refuse_settle(conn: Any, category: AlertCategory, state: Any) -> int:
        raise StorageError("디스코드 결과 정리에 실패했습니다: OperationalError")

    def refuse_insert(conn: Any, **kwargs: Any) -> int:
        raise StorageError("알림 저장에 실패했습니다: OperationalError")

    monkeypatch.setattr(data_thread.alert_store, "settle_not_sent", refuse_settle)
    monkeypatch.setattr(
        data_thread.alert_store, "insert_alert", refuse_insert
    )  # 자료 스레드의 쓰기만
    rig.start()
    first = rig.sink.of(AlertsView)[0]
    made = [v for v in first.alerts if v.code == "A-29"]
    assert [(v.id < 0, v.detail.stage if v.detail else None) for v in made] == [(True, "알림 저장")]
    assert alert_save_a29(rig) == []  # 표에는 없다
    assert [line["kind"] for line in rig.jsonl()] == ["memory"]  # 다음에 켤 때 옮긴다
