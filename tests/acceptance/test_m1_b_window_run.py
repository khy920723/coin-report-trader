"""M1 명세 6절의 창을 띄우는 시나리오: B18, B19, B21, B26, B38, B41(3.7, 3.8, 3.8.1, 3.9, 3.11).

`run_gui`를 그대로 돌리고 `services`에 가짜(시세, 디스코드, 보관소, 시계)를 넣는다(6절의 아래 글).
`services`에는 `FakeAiRunner`(`check()`는 `READY`, 정상 답)도 넣는다(M2 명세 3.15. 15번 뒤의 회차가
진짜 실행기를 부르지 않게), 가짜 시세를 `BookSource`로도 넣는다(M3 명세 3.4. 23번 뒤의 주문이
진짜 시세를 부르지 않게). "저장하고 시작"(3단계는 "모의매매 시작". 단추 이름은 그대로)을 누르기
전에 2단계의 여덟 칸(M2 명세 3.12.2. 13번부터 있다)이 있으면 모두 빈 항목이 아닌 값을 고르고,
3단계의 매매 칸과 확인란(M3 명세 3.17.5. 17번부터 있다)이 있으면 만든 값으로 채운다.
자료 폴더는 `tests/conftest.py`가 시험마다 `tmp_path` 아래로 바꾼다. 사람이 하는 일은 창의 단추나
화면이 부르는 동작(`UiActions`)으로 한다. B41의 두 번째 실행은 다른 프로세스로 돌린다.
"""

import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from acceptance.m1_support import AAA, HOUR, NOW, THIS_HOUR, markets, prepare_db, webhook_url
from acceptance.m3_support import fill_trade_fields
from fakes.ai import FakeAiRunner
from fakes.clock import FakeClock
from fakes.discord import FakeDiscord
from fakes.quote import FakeQuoteSource, make_candles
from fakes.vault import FakeVault
from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QLabel,
    QListWidget,
    QWidget,
)

from coin_report_trader import ui
from coin_report_trader.__main__ import main
from coin_report_trader.app import data_thread, run_gui, single_instance, startup_errors
from coin_report_trader.app.data_thread import DATA_THREAD_NAME
from coin_report_trader.app.services import Services
from coin_report_trader.config.paths import resolve_app_paths
from coin_report_trader.core import (
    AiSlot,
    AlertCategory,
    CandleUnit,
    CoinView,
    Mode,
    QuoteError,
    ScreenId,
    SettingsView,
    StorageError,
)
from coin_report_trader.core.texts import ERROR_NETWORK, ERROR_STORAGE, STAGE_STARTUP
from coin_report_trader.storage import alerts as alert_store
from coin_report_trader.storage.database import open_database
from coin_report_trader.storage.states import DiscordState
from coin_report_trader.vault import webhook_secret_name

LIMIT = 20.0
"""한 `run_gui`가 넘지 않을 실제 시간(안전 타이머). 넘으면 남은 차례가 있어 실패로 끝난다."""
STARTUP_KEYS = {"kind", "created_utc", "mode", "category", "code", "coin", "text", "stage",
                "error_name", "content", "after", "source"}  # fmt: skip


def services(clock: FakeClock | None = None, **kwargs: Any) -> Services:
    clock = clock or FakeClock()
    quote = kwargs.pop("quote", None) or FakeQuoteSource(markets=markets(AAA), clock=clock)
    quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 200, last_start=THIS_HOUR))
    return Services(
        quote=quote,
        webhook=kwargs.pop("webhook", None) or FakeDiscord(),
        vault=kwargs.pop("vault", None) or FakeVault(),
        clock=clock,
        ai=kwargs.pop("ai", None) or FakeAiRunner(),
        book=quote,  # 23번 뒤의 주문이 진짜 시세를 부르지 않게(M3 명세 3.4)
    )


def choose_ai_slots(window: Any) -> int:
    """처음 설정의 여덟 칸(M2 명세 3.12.2. 13번부터 있다)이 있으면 모두 빈 항목이 아닌 첫 값을
    고른다. 고른 칸의 수(0이나 8)를 돌려준다. 일부만 있으면 13번이 덜 된 것이라 실패로 본다.
    """
    names = [
        f"firstSetupAi{kind}_{slot.value}" for slot in AiSlot for kind in ("Model", "Effort")
    ]
    combos = [combo for name in names if (combo := window.findChild(QComboBox, name)) is not None]
    assert len(combos) in (0, len(names)), f"처음 설정의 여덟 칸 가운데 {len(combos)}칸만 있다"
    for combo in combos:
        index = next((i for i in range(combo.count()) if combo.itemText(i)), None)
        assert index is not None, f"{combo.objectName()}에 고를 값이 없다"
        combo.setCurrentIndex(index)
    return len(combos)


class App:
    """한 번의 `run_gui`가 만든 창, 동작, 자료 스레드, 창이 받은 값."""

    def __init__(self) -> None:
        self.window: Any = None
        self.actions: Any = None
        self.workers: list[Any] = []
        self.applied: list[Any] = []
        self.apply_threads: set[str] = set()

    def idle(self) -> bool:
        return bool(self.workers) and self.workers[0].wait_idle(0.002)


def run_app(
    monkeypatch: pytest.MonkeyPatch,
    given: Services,
    steps: list[Callable[[App], bool]],
) -> tuple[int, App]:
    """`run_gui`를 돌리며 차례를 하나씩 밟는다. 차례는 참을 돌려줄 때 끝난다. 다 끝나면 닫는다."""
    app = App()
    real_create = ui.create_main_window

    def create(context: Any) -> Any:
        window = real_create(context)
        real_apply = window.apply

        def apply(update: Any) -> None:
            app.applied.append(update)
            app.apply_threads.add(threading.current_thread().name)
            real_apply(update)

        window.apply = apply
        app.window, app.actions = window, context.actions
        return window

    class SeenWorker(data_thread.DataWorker):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            app.workers.append(self)

    monkeypatch.setattr(ui, "create_main_window", create)
    monkeypatch.setattr(data_thread, "DataWorker", SeenWorker)
    pending = list(steps)
    failure: list[BaseException] = []
    poll = QTimer()
    poll.setInterval(10)

    def tick() -> None:
        if not pending:
            poll.stop()
            QApplication.quit()
            return
        if app.window is None:
            return
        try:
            if pending[0](app):
                pending.pop(0)
        except BaseException as exc:  # 슬롯의 예외는 Qt가 삼키므로 시험이 다시 낸다
            failure.append(exc)
            poll.stop()
            QApplication.quit()

    poll.timeout.connect(tick)
    safety = QTimer()
    safety.setSingleShot(True)
    safety.timeout.connect(QApplication.quit)
    armed = {"on": True}

    def begin() -> None:
        if armed["on"]:
            poll.start()
            safety.start(int(LIMIT * 1000))

    QTimer.singleShot(0, begin)
    try:
        code = run_gui([], services=given)
    finally:
        armed["on"] = False
        poll.stop()
        safety.stop()
        monkeypatch.setattr(ui, "create_main_window", real_create)
    if failure:
        raise failure[0]
    assert not pending, f"끝내지 못한 차례가 {len(pending)}개 남았다"
    return code, app


def root() -> Path:
    return resolve_app_paths().root


def db_file() -> Path:
    return root() / "data" / "app.db"


def read(sql: str) -> list[tuple[Any, ...]]:
    conn = open_database(db_file())
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def insert(category: AlertCategory, minutes: int = 5) -> int:
    conn = open_database(db_file())
    try:
        return alert_store.insert_alert(
            conn, created_at=NOW - timedelta(minutes=minutes), mode=Mode.PAPER, category=category,
            code="A-30", coin=None, text=f"{category.value} 알림", detail=None, seen=False,
            discord_state=DiscordState.NO_ADDRESS,
        )  # fmt: skip
    finally:
        conn.close()


def ready_db() -> None:
    prepare_db(
        db_file(), settings=SettingsView((AAA,), 30, True, candle_unit=HOUR, candle_count=100)
    )


def find(window: QWidget, kind: type, name: str) -> Any:
    found = window.findChild(kind, name)
    assert found is not None, name
    return found


# --- B18: 본 알림이 다시 켜도 남는다, 스레드 ------------------------------------------------------


@pytest.mark.scenario("B18")
def test_b18_seen_alerts_stay_seen_after_a_restart_and_threads_keep_to_their_work(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready_db()
    ids = [insert(AlertCategory.OTHER, minutes=m) for m in (3, 2, 1)]
    sqlite_threads: set[str] = set()
    real_connect = sqlite3.connect
    watching = {"on": False}

    def connect(*args: Any, **kwargs: Any) -> Any:
        if watching["on"]:
            sqlite_threads.add(threading.current_thread().name)
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    real_create = ui.create_main_window

    def create(context: Any) -> Any:
        watching["on"] = True  # 창을 만든 뒤의 SQLite 사용만 센다(켜는 순서 1은 주 스레드다)
        return real_create(context)

    monkeypatch.setattr(ui, "create_main_window", create)

    def mark_two(app: App) -> bool:
        if not app.idle():
            return False
        app.actions.mark_seen(tuple(ids[:2]))
        return True

    def seen_in_db(app: App) -> bool:
        if not app.idle():
            return False
        watching["on"] = False  # 시험이 읽는 것은 세지 않는다
        try:
            return read("SELECT id FROM alerts WHERE seen = 1") == [(i,) for i in ids[:2]]
        finally:
            watching["on"] = True

    code, first = run_app(monkeypatch, services(), [mark_two, seen_in_db])
    watching["on"] = False
    assert code == 0
    assert first.apply_threads == {"MainThread"}  # `apply`는 주 스레드에서만 불린다
    assert sqlite_threads == {DATA_THREAD_NAME}  # 자료 스레드만 SQLite를 쓴다
    shown: dict[str, str] = {}

    def look(app: App) -> bool:
        if not app.idle():
            return False
        nav = find(app.window, QListWidget, "screenNav")
        shown["status"] = nav.item(list(ScreenId).index(ScreenId.STATUS)).text()
        # 자료 스레드가 쉬어도 `AlertsView`가 주 스레드에 닿기 전일 수 있어 보일 때까지 본다.
        return shown["status"] != "현황"

    code, _ = run_app(monkeypatch, services(), [look])
    assert code == 0
    assert read("SELECT id FROM alerts WHERE seen = 1") == [(i,) for i in ids[:2]]
    assert shown["status"].endswith(" (1)")


# --- B19: 창을 띄우기 전의 오류 ------------------------------------------------------------------


@pytest.mark.scenario("B19")
def test_b19_an_error_before_the_window_is_shown_next_time_and_never_sent(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file().mkdir(parents=True)  # DB 자리에 폴더가 있어 열지 못한다
    with pytest.raises(StorageError):  # 켜는 순서 1의 오류는 다시 낸다
        run_app(monkeypatch, services(), [])
    text = (root() / "logs" / startup_errors.FILE_NAME).read_text("utf-8")
    lines = [json.loads(raw) for raw in text.splitlines()]
    (line,) = lines
    assert line["kind"] == "startup"
    assert set(line) == STARTUP_KEYS
    db_file().rmdir()
    url = webhook_url("b")
    discord = FakeDiscord()
    vault = FakeVault({webhook_secret_name(AlertCategory.ERROR): url})
    seen: dict[str, Any] = {}

    def look(app: App) -> bool:
        if not app.idle():
            return False
        rows = read("SELECT id, code, stage, error_name, discord_state FROM alerts")
        (row,) = rows
        seen["row"] = row
        nav = find(app.window, QListWidget, "screenNav")
        nav.setCurrentRow(list(ScreenId).index(ScreenId.STATUS))  # 현황 화면의 알림센터
        find(app.window, QAbstractButton, "alertTab_error").click()
        return True

    def line_is_shown(app: App) -> bool:
        line_label = app.window.findChild(QLabel, f"alertLine_{seen['row'][0]}")
        if line_label is None or not line_label.isVisibleTo(app.window):
            return False
        seen["line"] = line_label.text()
        failed = app.window.findChild(QWidget, f"alertDiscordFailed_{seen['row'][0]}")
        seen["failed_mark"] = failed is not None and failed.isVisibleTo(app.window)
        return True

    code, _ = run_app(monkeypatch, services(webhook=discord, vault=vault), [look, line_is_shown])
    assert code == 0
    assert seen["row"][1:] == ("A-29", STAGE_STARTUP, ERROR_STORAGE, "local_only")
    assert ERROR_STORAGE in seen["line"]  # 알림센터에 그 오류가 보인다
    assert seen["failed_mark"] is False  # "보내지 못함" 표시가 없다
    assert discord.sent == []  # 디스코드로 보내지 않는다


# --- B21: M0의 DB를 올린다 ------------------------------------------------------------------------


@pytest.mark.scenario("B21")
def test_b21_an_m0_database_is_raised_to_schema_one_and_the_self_test_passes(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_file().parent.mkdir(parents=True)
    conn = open_database(db_file())  # M0이 만든 DB(스키마 0)
    try:
        assert conn.execute("PRAGMA user_version").fetchone() == (0,)
    finally:
        conn.close()
    code, _ = run_app(monkeypatch, services(), [App.idle])
    assert code == 0
    conn = open_database(db_file())
    try:
        assert conn.execute("PRAGMA user_version").fetchone() == (3,)
        names = sorted(
            name
            for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            if not name.startswith("sqlite_")
        )
        assert names == [
            "alerts", "candles", "fills", "orders", "paper_accounts", "paper_holdings", "pauses",
            "report_rows", "reports", "rounds", "setting_events", "settings",
        ]  # fmt: skip
        (sql,) = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'alerts'").fetchone()
        assert "AUTOINCREMENT" in sql.upper()
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert conn.execute("PRAGMA synchronous").fetchone() == (2,)
    finally:
        conn.close()
    monkeypatch.chdir(tmp_path)
    for given in (None, services()):
        assert main(["--self-test"], services=given) == 0
        report = (tmp_path / "self-test-report.txt").read_bytes().decode("utf-8-sig")
        assert report.splitlines()[-1] == "결과: 통과 (7/7)"


# --- B26: 알림 지우기는 다시 켜도 남는다 ---


@pytest.mark.scenario("B26")
def test_b26_deleted_alerts_stay_gone_and_numbers_are_not_reused(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready_db()
    stored = make_candles(AAA, HOUR, 50, last_start=THIS_HOUR - timedelta(hours=1))
    conn = open_database(db_file())
    try:
        from coin_report_trader.storage.candles import write_candles

        write_candles(conn, stored)
    finally:
        conn.close()
    errors = [insert(AlertCategory.ERROR, minutes=m) for m in (9, 8)]
    trades = [insert(AlertCategory.TRADE, minutes=m) for m in (7, 6)]
    largest = trades[-1]
    given = services()
    assert isinstance(given.quote, FakeQuoteSource)
    quote = given.quote

    def delete(app: App) -> bool:
        if not app.idle():
            return False
        app.actions.delete_alert(largest)  # 가장 큰 번호의 한 줄
        app.actions.clear_alerts(AlertCategory.ERROR)  # 한 탭의 모두
        return True

    def new_alert(app: App) -> bool:
        if not app.idle():
            return False
        quote.script(QuoteError(ERROR_NETWORK), kind="candles", market=AAA, unit=HOUR)
        app.actions.retry_candles()
        return True

    def made(app: App) -> bool:
        return app.idle() and any(code == "A-1" for (code,) in read("SELECT code FROM alerts"))

    code, _ = run_app(monkeypatch, given, [delete, new_alert, made])
    assert code == 0
    remaining = [row[0] for row in read("SELECT id FROM alerts ORDER BY id")]
    assert remaining[0] == trades[0]
    assert all(i not in remaining for i in (*errors, largest))
    assert remaining[-1] > largest  # 지운 번호를 다시 주지 않는다
    code, _ = run_app(monkeypatch, services(), [App.idle])
    assert code == 0
    again = [row[0] for row in read("SELECT id FROM alerts ORDER BY id")]
    assert again[: len(remaining)] == remaining  # 지운 줄은 돌아오지 않고 남은 탭의 알림은 그대로다
    conn = open_database(db_file())
    try:
        from coin_report_trader.storage.candles import read_candles

        starts = {c.start for c in read_candles(conn, AAA, HOUR)}
        assert {c.start for c in stored} <= starts  # 지우기가 봉을 지우지 않는다
    finally:
        conn.close()


# --- B38: 1단계 보완 전에 마친 기록 ---------------------------------------------------------------


@pytest.mark.scenario("B38")
def test_b38_an_old_record_stays_on_first_setup_until_a_unit_is_chosen(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare_db(db_file(), settings=SettingsView((AAA,), 30, True))  # "1", 코인과 보관 기간만
    assert read("SELECT value FROM settings WHERE key = 'first_setup_level'") == [("1",)]
    given = services()
    assert isinstance(given.quote, FakeQuoteSource)
    quote = given.quote
    seen: dict[str, Any] = {}

    def look(app: App) -> bool:
        if not app.idle():
            return False
        window = app.window
        notice = window.findChild(QLabel, "firstSetupLegacyNotice")
        box = window.findChild(QWidget, f"firstSetupCoin_{AAA}")
        if notice is None or not notice.isVisibleTo(window) or box is None:
            return False
        seen["settings"] = next(u for u in app.applied if isinstance(u, SettingsView))
        seen["requests"] = len(quote.candle_requests())
        seen["coin_views"] = [u for u in app.applied if isinstance(u, CoinView)]
        seen["coin_checked"] = box.isChecked()
        seen["retention"] = find(window, QAbstractButton, "firstSetupRetention30").isChecked()
        seen["unit_chosen"] = [
            u.value for u in CandleUnit
            if find(window, QAbstractButton, f"firstSetupUnit_{u.value}").isChecked()
        ]  # fmt: skip
        seen["count_200"] = find(window, QAbstractButton, "firstSetupCount_200").isChecked()
        seen["coins_locked"] = not find(window, QWidget, "firstSetupCoinSearch").isEnabled()
        # 보관 기간 묶음은 'ⓘ'가 들어 열려 있고(M1 명세 3.14.7), 그 안의 고르기 단추가 잠긴다.
        seen["retention_locked"] = not find(window, QWidget, "firstSetupRetention30").isEnabled()
        find(window, QAbstractButton, f"firstSetupUnit_{CandleUnit.MINUTE_60.value}").click()
        choose_ai_slots(window)  # 예전 기록에서도 여덟 칸은 잠그지 않는다(M2 명세 3.12.2)
        fill_trade_fields(window)  # 17번부터 매매 칸과 확인란이 있다(M3 명세 3.17.5)
        find(window, QAbstractButton, "firstSetupStart").click()
        return True

    def fetched(app: App) -> bool:
        return app.idle() and len(quote.candle_requests(AAA, HOUR)) > 0

    code, _ = run_app(monkeypatch, given, [look, fetched])
    assert code == 0
    first = seen["settings"]
    assert (first.first_setup_done, first.coins, first.retention_days) == (False, (AAA,), 30)
    assert (first.candle_unit, first.candle_count) == (None, None)
    assert seen["requests"] == 0  # 봉 요청이 없다
    assert seen["coin_views"] == []  # `CoinView`는 오지 않는다
    assert (seen["coin_checked"], seen["retention"]) == (True, True)
    assert (seen["unit_chosen"], seen["count_200"]) == ([], True)
    assert (seen["coins_locked"], seen["retention_locked"]) == (True, True)
    stored = dict(read("SELECT key, value FROM settings WHERE key NOT LIKE 'fetched%'"))
    # 17번 앞에는 "2", 17번과 5번 뒤에는 "3"이다(M3 명세 3.11.1).
    assert stored["first_setup_level"] in ("2", "3")
    assert (stored["candle_unit"], stored["candle_count"]) == ("minute60", "200")


# --- B41: 한 번 더 켜기 ---

CHILD = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, sys.argv[1])
    from fakes.clock import FakeClock
    from fakes.discord import FakeDiscord
    from fakes.quote import FakeQuoteSource
    from fakes.vault import FakeVault
    from coin_report_trader.__main__ import main
    from coin_report_trader.app.services import Services
    fakes = Services(quote=FakeQuoteSource(), webhook=FakeDiscord(), vault=FakeVault(),
                     clock=FakeClock())
    sys.exit(main(sys.argv[2:], services=fakes))
    """
)
# 첫 붙기가 실패한 뒤의 첫 기다림에서 표시 한 줄을 찍는다(`notify`의 다시 붙기가 돈다는 표시).
RETRY_MARK = "B41-RETRY-WAIT"
LATE_CHILD = (
    textwrap.dedent(
        f"""
    import sys
    import time
    import types
    from coin_report_trader.app import single_instance
    marked = []

    def sleep(seconds):
        if not marked:
            marked.append(True)
            print("{RETRY_MARK}", flush=True)
        time.sleep(seconds)

    single_instance.time = types.SimpleNamespace(monotonic=time.monotonic, sleep=sleep)
    """
    )
    + CHILD
)
TESTS_DIR = Path(__file__).resolve().parent.parent
SAFE_JOIN = 10.0


def spawn(*argv: str, cwd: Path | None = None, code: str = CHILD) -> subprocess.Popen[str]:
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    return subprocess.Popen(
        [sys.executable, "-c", code, str(TESTS_DIR), *argv],
        env=env,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def pump_until(condition: Callable[[], bool], seconds: float = 30.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def written_by_the_app() -> dict[str, int]:
    """로그, DB, `startup-errors.jsonl`의 크기(두 번째 실행이 쓰지 않았는지 견준다)."""
    logs = root() / "logs"
    return {
        name: (path.stat().st_size if path.exists() else -1)
        for name, path in {
            "log": logs / "app.log",
            "jsonl": logs / startup_errors.FILE_NAME,
            "db": db_file(),
        }.items()
    }


@pytest.mark.scenario("B41")
def test_b41_a_second_start_brings_the_first_window_forward_and_ends(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready_db()
    fronts: list[str] = []
    real_front = single_instance.Started.bring_to_front

    def counted(self: Any) -> None:
        fronts.append("front")
        real_front(self)

    monkeypatch.setattr(single_instance.Started, "bring_to_front", counted)
    seen: dict[str, Any] = {}

    def second_start(app: App) -> bool:
        if not app.idle():
            return False
        seen["before"] = written_by_the_app()
        seen["child"] = spawn()
        return True

    def second_ended(app: App) -> bool:
        child = seen["child"]
        if child.poll() is None:
            return False
        seen["code"] = child.returncode
        seen["output"] = child.stdout.read() if child.stdout else ""
        return bool(fronts)

    code, _ = run_app(monkeypatch, services(), [second_start, second_ended])
    assert code == 0
    assert seen["code"] == 0, seen["output"]  # 창을 만들지 않고 0으로 끝난다
    assert fronts == ["front"]  # 먼저 켠 쪽이 `show`를 받아 앞으로 내는 함수가 한 번 불린다
    after = written_by_the_app()
    assert after["jsonl"] == seen["before"]["jsonl"] == -1
    log = (root() / "logs" / "app.log").read_text(encoding="utf-8")
    assert log.count("시작: 버전") == 1  # 두 번째 실행은 로그에 쓰지 않는다


@pytest.mark.scenario("B41")
def test_b41_a_second_start_waits_for_a_server_that_opens_late(qapp: QApplication) -> None:
    from PySide6.QtCore import QLockFile

    root().mkdir(parents=True)
    holder = QLockFile(str(single_instance.lock_path(root())))
    assert holder.tryLock(0)  # 첫 앱이 잠금은 잡았지만 서버는 아직 열지 않았다
    server = QLocalServer()
    lines: list[bytes] = []
    output: list[str] = []
    try:
        child = spawn(code=LATE_CHILD)
        reader = threading.Thread(
            target=lambda: output.extend(child.stdout or ()), name="b41-child-output", daemon=True
        )
        reader.start()
        # 서버가 없어 첫 붙기가 실패하고 다시 붙기를 기다리기 시작한 뒤에 서버를 연다.
        assert pump_until(lambda: any(RETRY_MARK in line for line in output)), (
            "두 번째 실행이 다시 붙기를 기다리지 않았다: " + "".join(output)
        )
        QLocalServer.removeServer(single_instance.server_name(root()))
        assert server.listen(single_instance.server_name(root()))
        opened = time.monotonic()
        received: list[float] = []

        def read_lines() -> bool:
            while server.hasPendingConnections():
                socket = server.nextPendingConnection()
                socket.waitForReadyRead(1000)
                lines.append(bytes(socket.readAll().data()).strip())
                received.append(time.monotonic())
            return bool(lines) and child.poll() is not None

        assert pump_until(read_lines), "두 번째 실행이 늦게 열린 서버에 붙지 못했다"
        assert received[0] - opened < 3.0  # 서버가 열린 뒤 3초 안에 다시 붙어 보냈다
        reader.join(SAFE_JOIN)
        assert child.returncode == 0, "".join(output)
        assert lines == [b"show"]
        assert not (root() / "logs").exists()  # 로그, DB를 만들지 않는다
        assert not db_file().exists()
    finally:
        server.close()
        holder.unlock()


@pytest.mark.scenario("B41")
def test_b41_the_self_test_does_not_touch_the_lock_or_the_server(
    qapp: QApplication, tmp_path: Path
) -> None:
    from PySide6.QtCore import QLockFile

    root().mkdir(parents=True)
    holder = QLockFile(str(single_instance.lock_path(root())))
    assert holder.tryLock(0)
    server = QLocalServer()
    QLocalServer.removeServer(single_instance.server_name(root()))
    assert server.listen(single_instance.server_name(root()))
    try:
        child = spawn("--self-test", cwd=tmp_path)
        assert pump_until(lambda: child.poll() is not None), "--self-test가 끝나지 않았다"
        output = child.stdout.read() if child.stdout else ""
        assert child.returncode == 0, output
        QCoreApplication.processEvents()
        assert not server.hasPendingConnections()  # 서버에 붙지 않는다
        assert holder.isLocked()  # 잠금은 그대로다
    finally:
        server.close()
        holder.unlock()
