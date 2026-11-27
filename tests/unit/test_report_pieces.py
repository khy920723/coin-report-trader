"""보고서 알림의 글, 주소를 읽지 못함, 알림 글 만들기, 조립의 잇기(M2 명세 3.11, 3.12.7)."""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fakes.ai import FakeAiRunner
from fakes.clock import FakeClock
from fakes.discord import FakeDiscord
from fakes.quote import FakeQuoteSource
from fakes.vault import FakeVault
from PySide6.QtCore import QTimer

from coin_report_trader.app import alert_rules, run_gui
from coin_report_trader.app.send_thread import SendWorker
from coin_report_trader.app.send_types import AddressState, DeliveryResult, WebhookResult
from coin_report_trader.app.services import Services
from coin_report_trader.config.paths import resolve_app_paths
from coin_report_trader.core import (
    AiChoice,
    AiSlot,
    AlertCategory,
    CandleUnit,
    Direction,
    Effort,
    Mode,
    SettingsView,
    UiUpdate,
    WebhookView,
)
from coin_report_trader.core.texts import (
    AI_MARK_TEXT,
    CODE_CLAUDE_OFF,
    CODE_LOGGED_OUT,
    CODE_PRICE_FAILED,
    CODE_REPORT_DONE,
    CODE_REPORT_FAILED,
    ERROR_CLAUDE_NOT_FOUND,
    ERROR_VERSION_UNREADABLE,
    error_version_out_of_range,
)
from coin_report_trader.notify import alert_message
from coin_report_trader.storage.database import migrate, open_database
from coin_report_trader.storage.settings import write_settings
from coin_report_trader.vault import webhook_secret_name

SAFETY = 10.0
FOUR = tuple(AiChoice(slot, "haiku", Effort.LOW) for slot in AiSlot)


# --- 디스코드 글 ---------------------------------------------------------------------------------


def test_a_report_alert_has_the_ai_mark_as_its_third_line() -> None:
    shown = alert_message(
        mode=Mode.PAPER,
        category=AlertCategory.REPORT,
        coin="KRW-AAA",
        text="회차 3의 보고서를 받았습니다. 최종 판단: 오를 것.",
    )
    assert shown.split("\n") == [
        "[모의매매] 보고서 알림",
        "AAA: 회차 3의 보고서를 받았습니다. 최종 판단: 오를 것.",
        AI_MARK_TEXT,
    ]
    assert AI_MARK_TEXT == "AI가 쓴 글입니다. 틀릴 수 있습니다."


def test_the_other_categories_do_not_get_the_ai_mark() -> None:
    for category in (AlertCategory.TRADE, AlertCategory.LIMIT, AlertCategory.HOLDING):
        shown = alert_message(mode=Mode.PAPER, category=category, coin=None, text="글")
        assert AI_MARK_TEXT not in shown


# --- 알림 글(3.11) --------------------------------------------------------------------------------


def test_the_alert_texts_follow_the_table() -> None:
    price = alert_rules.price_failed("KRW-AAA", "네트워크 끊김", "10:00")
    assert (price.code, price.category, price.coin) == (
        CODE_PRICE_FAILED, AlertCategory.ERROR, "KRW-AAA",
    )  # fmt: skip
    assert price.text == "현재가를 읽지 못해 이번 회차를 쉬었습니다."
    assert price.stage == "현재가 읽기"
    assert price.content == "AI 보고서를 만들기 전에 현재가를 읽지 못했습니다."
    assert price.after == (
        "이번 회차에 이 코인의 AI를 부르지 않았습니다. 다음 회차(10:00)에 다시 합니다."
    )
    failed = alert_rules.report_failed("KRW-AAA", "시간 초과", "까닭 글", "10:00")
    assert failed.code == CODE_REPORT_FAILED
    assert failed.text == "AI 보고서를 받지 못해 이번 회차에 주문하지 않았습니다(시간 초과)."
    assert (failed.stage, failed.error_name, failed.content) == (
        "AI 보고서",
        "시간 초과",
        "까닭 글",
    )
    assert failed.after == (
        "이번 회차에 이 코인의 보고서를 만들지 않았습니다. 다음 회차(10:00)에 다시 부릅니다."
    )
    out = alert_rules.logged_out()
    assert (out.code, out.coin, out.error_name) == (CODE_LOGGED_OUT, None, "로그인 만료")
    assert out.text == "Claude 로그인이 만료되어 AI 보고서를 받지 못했습니다. 다시 로그인하세요."
    assert out.content == "claude auth status가 로그인되지 않았다고 알렸습니다."
    assert out.after == (
        "이번 회차에 AI 보고서를 만들지 않았습니다. Claude Code에 다시 로그인하면 다음 회차부터"
        " 다시 부릅니다."
    )


def test_the_claude_off_alert_has_two_texts_and_a_range_line() -> None:
    off = alert_rules.claude_off(error_version_out_of_range("2.2.5"))
    assert (off.code, off.coin, off.stage) == (CODE_CLAUDE_OFF, None, "AI 보고서")
    assert off.text == "Claude Code 버전이 시험한 범위 밖이라 AI를 껐습니다."
    assert off.content == "시험한 범위: 2.1.314 이상 2.2.0 미만"
    assert off.after == "AI를 껐습니다. 범위 안의 버전이 되면 다음 회차부터 다시 돕니다."
    for name in (ERROR_CLAUDE_NOT_FOUND, ERROR_VERSION_UNREADABLE):
        missing = alert_rules.claude_off(name)
        assert missing.text == "Claude Code를 찾지 못했거나 버전을 읽지 못해 AI를 껐습니다."
        assert missing.content == "Claude Code를 설치하고 로그인했는지 확인하세요."
        assert missing.error_name == name


def test_the_report_alert_has_no_detail_and_the_other_alerts_have_one() -> None:
    done = alert_rules.report_done("KRW-AAA", 4, Direction.DOWN)
    assert (done.code, done.category, done.detail) == (CODE_REPORT_DONE, AlertCategory.REPORT, None)
    assert done.text == "회차 4의 보고서를 받았습니다. 최종 판단: 내릴 것."
    assert alert_rules.logged_out().detail is not None


def test_the_repeat_rules_of_the_report_alerts() -> None:
    rules = alert_rules.RepeatRules()
    assert rules.want_a2("A", "x")
    assert not rules.want_a2("A", "x")
    assert rules.want_a2("B", "x")
    rules.a2_succeeded("A")
    assert rules.want_a2("A", "x")
    assert rules.want_a17("A", "시간 초과")
    assert not rules.want_a17("A", "시간 초과")
    assert rules.want_a17("A", "형식 오류")
    assert rules.want_a18()
    assert not rules.want_a18()
    rules.report_ok("B")  # 어느 코인이든 `OK`가 되면 A-18의 되풀이가 풀린다
    assert rules.want_a18()
    assert not rules.want_a17("A", "시간 초과")  # 다른 코인의 `OK`는 A-17을 풀지 않는다
    rules.report_ok("A")
    assert rules.want_a17("A", "시간 초과")
    assert rules.want_a19()
    assert not rules.want_a19()
    rules.ai_on()
    assert rules.want_a19()


# --- 주소를 읽지 못함(3.12.7) ---------------------------------------------------------


@dataclass
class Data:
    def address_states(self, states: dict[AlertCategory, AddressState]) -> None:
        pass

    def delivery_result(self, result: DeliveryResult) -> None:
        pass

    def webhook_result(self, result: WebhookResult) -> None:
        pass


@dataclass
class Views:
    items: list[UiUpdate] = field(default_factory=list)

    def __call__(self, update: UiUpdate) -> None:
        self.items.append(update)

    def webhooks(self, category: AlertCategory) -> list[WebhookView]:
        return [u for u in self.items if isinstance(u, WebhookView) and u.category == category]


def _address() -> str:
    return "https://" + "discord.com/api/webhooks/" + "1" * 18 + "/" + "a" * 64


def test_the_webhook_view_carries_unreadable_until_a_save_or_delete_succeeds() -> None:
    vault = FakeVault()
    vault.fail_get()  # 켤 때 보관소에서 읽지 못한다
    views = Views()
    worker = SendWorker(webhook=FakeDiscord(), vault=vault, clock=FakeClock(), sink=views)
    worker.attach(Data())  # type: ignore[arg-type]
    worker.start()
    try:
        assert worker.wait_idle(SAFETY)
        error = AlertCategory.ERROR
        assert {v.unreadable for v in views.webhooks(error)} == {True}
        assert views.webhooks(error)[0].unreadable is True
        vault.heal()
        worker.send_test(error)  # 다른 보내기(시험 글)는 그 값을 바꾸지 않는다
        assert worker.wait_idle(SAFETY)
        assert views.webhooks(error)[-1].unreadable is True
        vault.fail_set()
        worker.save_webhook(error, _address())  # 저장이 실패하면 그대로다
        assert worker.wait_idle(SAFETY)
        assert views.webhooks(error)[-1].unreadable is True
        vault.heal()
        worker.save_webhook(error, _address())
        assert worker.wait_idle(SAFETY)
        assert views.webhooks(error)[-1].unreadable is False
        other = AlertCategory.TRADE
        assert views.webhooks(other)[-1].unreadable is True  # 다른 분류는 그대로다
        worker.delete_webhook(other)
        assert worker.wait_idle(SAFETY)
        assert views.webhooks(other)[-1].unreadable is False
    finally:
        worker.stop()
        worker.join(SAFETY)
    assert webhook_secret_name(error) in vault.values


def test_the_webhook_view_is_not_unreadable_when_the_vault_can_be_read() -> None:
    views = Views()
    worker = SendWorker(webhook=FakeDiscord(), vault=FakeVault(), clock=FakeClock(), sink=views)
    worker.attach(Data())  # type: ignore[arg-type]
    worker.start()
    try:
        assert worker.wait_idle(SAFETY)
    finally:
        worker.stop()
        worker.join(SAFETY)
    assert {v.unreadable for v in views.items if isinstance(v, WebhookView)} == {False}
    assert WebhookView(AlertCategory.ERROR, False).unreadable is False


# --- 조립의 잇기 ---------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request: pytest.FixtureRequest) -> Any:
    data_home = tmp_path / "share"
    data_home.mkdir()
    key = "LOCALAPPDATA" if sys.platform == "win32" else "XDG_DATA_HOME"
    monkeypatch.setenv(key, str(data_home))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return request.getfixturevalue("qapp")


def test_services_ai_reaches_the_worker_checks_at_start_cleans_the_session_folder_and_stops(
    env: Any,
) -> None:
    paths = resolve_app_paths()
    paths.ensure()
    conn = open_database(paths.database_file)
    try:
        migrate(conn)
        view = SettingsView(
            ("KRW-AAA",), 30, True, candle_unit=CandleUnit.MINUTE_60, candle_count=100,
            ai_choices=FOUR,
        )  # fmt: skip
        write_settings(conn, view)
    finally:
        conn.close()
    leftover = paths.root / "ai-sessions" / "r1-KRW-AAA"  # 앞 실행이 남긴 폴더
    leftover.mkdir(parents=True)
    runner = FakeAiRunner()
    services = Services(
        quote=FakeQuoteSource(),
        webhook=FakeDiscord(),
        vault=FakeVault(),
        clock=FakeClock(),
        ai=runner,
    )
    timer = QTimer()
    elapsed = 0

    def tick() -> None:
        nonlocal elapsed
        elapsed += 20
        if (runner.check_calls and not leftover.exists()) or elapsed >= 10_000:
            timer.stop()
            env.quit()

    timer.timeout.connect(tick)
    timer.start(20)
    assert run_gui([], services=services) == 0
    timer.stop()
    assert runner.check_calls >= 1  # 켤 때 네 칸이 저장되어 있으면 버전을 확인한다
    assert not leftover.exists()
    assert runner.stop_calls == 1  # 끝낼 때 도는 세션을 끝낸다
