"""M2 2단계 수용 시험(명세 6절의 C1~C33)이 함께 쓰는 도구.

- 자료 스레드(`DataWorker`), 보내기 스레드(`SendWorker`), 가짜 실행기(`FakeAiRunner`)를 Qt 없이
  함께 돌리는 `Rig`.
- 바깥은 모두 `tests/fakes/`의 가짜(시세, 실행기, 디스코드, 보관소, 시계)다. 거래소, 진짜 `claude`,
  네트워크, keyring을 쓰지 않는다. 시각은 가짜 시계로만 옮긴다(실제 시간은 안전 시간으로만 쓴다).
- 봉 단위는 60분봉, 코인은 `KRW-AAA`(와 `KRW-BBB`)이고, 네 칸은 모두 골라 저장한다(명세 6절의 첫
  글. 따로 적은 시나리오 말고). 숫자는 만든 값이다. 주소처럼 생긴 글은 실행 중에 이어 붙여 만든다.

이 파일은 시험을 담지 않는다(`test_`로 시작하지 않아 모으지 않는다).
"""

import csv
import io
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fakes.ai import FakeAiRunner
from fakes.clock import FakeClock
from fakes.discord import FakeDiscord
from fakes.quote import FakeQuoteSource, make_candles
from fakes.vault import FakeVault

from coin_report_trader.app.data_thread import DataWorker
from coin_report_trader.app.problem_report import PROBLEMS_FOLDER
from coin_report_trader.app.send_thread import SendWorker
from coin_report_trader.core import (
    AiChoice,
    AiSlot,
    AiStatus,
    AiStatusView,
    AlertCategory,
    AlertView,
    CandleUnit,
    Effort,
    Market,
    Mode,
    ReportView,
    RoundItem,
    SettingsView,
    UiUpdate,
)
from coin_report_trader.storage import reports as report_store
from coin_report_trader.storage.alerts import list_alerts
from coin_report_trader.storage.database import migrate, open_database
from coin_report_trader.storage.settings import write_settings
from coin_report_trader.vault import webhook_secret_name

AAA, BBB = "KRW-AAA", "KRW-BBB"
HOUR = CandleUnit.MINUTE_60
SAFETY = 10.0
THIS_HOUR = datetime(2026, 1, 5, 3, tzinfo=UTC)
"""가짜 시계의 처음 시각(03:00 UTC)에 걸친 60분봉의 시작."""
FIRST_CLOSE = datetime(2026, 1, 5, 4, tzinfo=UTC)
"""첫 (라)의 봉 마감 시각 T(04:00:10에 받는다)."""
FOUR = tuple(AiChoice(slot, "haiku", Effort.LOW) for slot in AiSlot)
PRICE = Decimal("100")


def webhook_url(tag: str = "c") -> str:
    """주소의 꼴을 지킨 시험용 글. 실행 중에 이어 붙인다(저장소에 주소의 꼴을 두지 않는다)."""
    return "https://" + "discord.com/api/webhooks/" + "5" * 18 + "/" + (tag * 64)[:64]


def token_of(url: str) -> str:
    return url.rsplit("/", 1)[1]


@dataclass
class Sink:
    """화면에 가는 값을 모은다. 보낸 스레드의 이름도 적는다."""

    items: list[UiUpdate] = field(default_factory=list)
    threads: set[str] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __call__(self, update: UiUpdate) -> None:
        with self.lock:
            self.items.append(update)
            self.threads.add(threading.current_thread().name)

    def of(self, kind: type) -> list[Any]:
        with self.lock:
            return [item for item in self.items if isinstance(item, kind)]


@dataclass
class Rig:
    data: DataWorker
    send: SendWorker
    sink: Sink
    clock: FakeClock
    quote: FakeQuoteSource
    runner: FakeAiRunner
    discord: FakeDiscord
    vault: FakeVault
    root: Path

    @property
    def db(self) -> Path:
        return self.root / "data" / "app.db"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def sessions(self) -> Path:
        return self.root / "ai-sessions"

    @property
    def problems(self) -> Path:
        return self.root / PROBLEMS_FOLDER

    def start(self) -> None:
        self.send.start()
        self.data.start()
        self.settle()

    def settle(self) -> None:
        """두 스레드가 모두 쉴 때까지(자료 → 보내기 → 자료로 오가는 일이 끝나야 한다)."""
        for _ in range(100):
            assert self.data.wait_idle(SAFETY), "자료 스레드가 쉬지 않는다"
            assert self.send.wait_idle(SAFETY), "보내기 스레드가 쉬지 않는다"
            if self.data.wait_idle(SAFETY) and self.send.wait_idle(0.05):
                return
        raise AssertionError("두 스레드가 끝내 쉬지 않는다")

    def drain(self) -> None:
        """띄운 세션이 모두 끝나 자료 스레드가 처리할 때까지(안전 시간까지 짧게 끊어 본다)."""
        deadline = time.monotonic() + SAFETY
        while self.data._pending and time.monotonic() < deadline:
            time.sleep(0.005)
        assert not self.data._pending, "세션이 끝나지 않았다"
        self.settle()

    def to_next_due(self, extra: float = 0.0) -> None:
        """다음 (라)의 때(+ `extra`초)로 시계를 옮긴다. 세션은 기다리지 않는다."""
        due = self.data._next_due
        assert due is not None, "다음 받기의 때가 없다"
        self.clock.advance((due - self.clock.now()).total_seconds() + extra)
        self.settle()

    def round(self) -> None:
        """다음 (라)의 때로 시계를 옮기고 세션이 모두 끝나 처리될 때까지 기다린다."""
        self.to_next_due()
        self.drain()

    def stop(self) -> None:
        self.discord.release()
        self.data.stop()
        self.send.stop()
        self.data.join(SAFETY)
        self.send.join(SAFETY)

    # --- 읽기 --------------------------------------------------------------------------------

    def _read(self, read: Callable[[Any], Any]) -> Any:
        conn = open_database(self.db)
        try:
            return read(conn)
        finally:
            conn.close()

    def rows(self, sql: str, *args: Any) -> list[tuple[Any, ...]]:
        return self._read(lambda conn: conn.execute(sql, args).fetchall())

    def alerts(self) -> list[AlertView]:
        """오래된 것이 앞."""
        return list(reversed(self._read(list_alerts)))

    def codes(self) -> list[str]:
        return [alert.code for alert in self.alerts()]

    def reports(self, market: str = AAA) -> tuple[RoundItem, ...]:
        """그 코인의 회차 줄(새것이 앞)."""
        return self._read(lambda conn: report_store.list_round_items(conn, market, Mode.PAPER))

    def report(self, report_id: int) -> ReportView:
        view = self._read(lambda conn: report_store.read_report(conn, report_id))
        assert view is not None
        return view

    def statuses(self) -> list[AiStatus]:
        return [view.status for view in self.sink.of(AiStatusView)]

    def problem_rows(self) -> list[dict[str, str]]:
        """문제 보고 표의 줄(달 파일의 이름 차례)."""
        found: list[dict[str, str]] = []
        for path in sorted(self.problems.glob("problems-*.csv")):
            text = path.read_text(encoding="utf-8").removeprefix(chr(0xFEFF))
            found += list(csv.DictReader(io.StringIO(text)))
        return found


def seed_db(
    root: Path,
    *,
    coins: tuple[str, ...] = (AAA,),
    choices: tuple[AiChoice, ...] = FOUR,
    seed: Callable[[Any], None] | None = None,
) -> Path:
    """`root/data/app.db`를 만들어 설정을 쓴다.

    `choices`가 빈 튜플이면 네 칸이 없다(2단계 전의 기록).
    """
    db = root / "data" / "app.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = open_database(db)
    try:
        migrate(conn)
        view = SettingsView(coins, 30, True, candle_unit=HOUR, candle_count=100, ai_choices=choices)
        write_settings(conn, view)
        if seed is not None:
            seed(conn)
    finally:
        conn.close()
    return db


def fake_quote(clock: FakeClock, coins: tuple[str, ...] = (AAA, BBB)) -> FakeQuoteSource:
    quote = FakeQuoteSource(
        markets=[Market(code, f"이름 {code}", False) for code in (AAA, BBB)], clock=clock
    )
    for coin in coins:
        quote.set_candles(coin, HOUR, make_candles(coin, HOUR, 200, last_start=THIS_HOUR))
    quote.set_prices(dict.fromkeys(coins, PRICE))
    return quote


def make_rig(
    root: Path,
    *,
    coins: tuple[str, ...] = (AAA,),
    choices: tuple[AiChoice, ...] = FOUR,
    runner: FakeAiRunner | None = None,
    seed: Callable[[Any], None] | None = None,
    fresh: bool = True,
    clock: FakeClock | None = None,
    quote: FakeQuoteSource | None = None,
    discord: FakeDiscord | None = None,
    vault: FakeVault | None = None,
    saved: dict[AlertCategory, str] | None = None,
) -> Rig:
    """`fresh`이면 `root` 아래에 DB를 새로 만들어 설정을 쓴다. 아니면 있는 DB를 그대로 쓴다."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(exist_ok=True)
    if fresh:
        seed_db(root, coins=coins, choices=choices, seed=seed)
    clock = clock or FakeClock()
    quote = quote or fake_quote(clock, coins)
    runner = runner or FakeAiRunner()
    discord = discord or FakeDiscord()
    if vault is None:
        vault = FakeVault({webhook_secret_name(c): url for c, url in (saved or {}).items()})
    sink = Sink()
    send = SendWorker(webhook=discord, vault=vault, clock=clock, sink=sink)
    data = DataWorker(
        database_file=root / "data" / "app.db",
        logs_dir=root / "logs",
        clock=clock,
        quote=quote,
        sink=sink,
        send=send,
        ai=runner,
    )
    send.attach(data)
    return Rig(data, send, sink, clock, quote, runner, discord, vault, root)


def restart(rig: Rig, *, runner: FakeAiRunner | None = None) -> Rig:
    """끄고 다시 켠다: 같은 자료 폴더, 시계, 시세, 디스코드, 보관소로 새 스레드를 만든다."""
    rig.stop()
    return make_rig(
        rig.root,
        fresh=False,
        clock=rig.clock,
        quote=rig.quote,
        runner=runner or rig.runner,
        discord=rig.discord,
        vault=rig.vault,
    )


# --- 보고서 화면 -------------------------------------------------------------------------------


class _NoActions:
    """창이 부르는 동작을 받기만 한다(화면이 부른 `load_reports`·`load_report`를 적는다)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def __getattr__(self, name: str) -> Callable[..., None]:
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args))

        return call


@dataclass
class ReportScreenProbe:
    """자료 스레드가 보낸 보고서 값을 창의 `apply`에 그대로 넣고, 보고서 화면의 글을 읽는다."""

    window: Any
    screen: Any

    def text(self, name: str) -> str | None:
        from PySide6.QtWidgets import QLabel

        label = self.screen.findChild(QLabel, name)
        assert label is not None, name
        return None if label.isHidden() else label.text()

    def lines(self, section: str) -> list[str]:
        from PySide6.QtWidgets import QLabel, QWidget

        widget = self.screen.findChild(QWidget, section)
        assert widget is not None, section
        if widget.isHidden():
            return []
        return [label.text() for label in widget.findChildren(QLabel) if label.text()]

    def all_text(self) -> str:
        from PySide6.QtWidgets import QLabel

        return "\n".join(
            label.text()
            for label in self.screen.findChildren(QLabel)
            if label.text() and not label.isHidden()
        )

    def rounds(self) -> list[str]:
        from PySide6.QtWidgets import QListWidget

        box = self.screen.findChild(QListWidget, "reportRounds")
        assert box is not None
        return [box.item(i).text() for i in range(box.count())]

    def close(self) -> None:
        self.window.close()
        self.window.deleteLater()


def show_report(rig: Rig, market: str, report_id: int | None = None) -> ReportScreenProbe:
    """`rig`의 자료 스레드에 `load_reports`(와 `load_report`)를 부르고, 받은 화면 값을 창에 넣는다.

    주 스레드에서 부른다(`qapp` 픽스처가 있는 시험). 창은 `close()`로 닫는다.
    """
    from PySide6.QtWidgets import QApplication, QListWidget

    from coin_report_trader.core import (
        ReportListView,
        ScreenId,
        UiContext,
    )
    from coin_report_trader.ui import create_main_window, screen_widget, select_screen

    rig.data.load_reports(market)
    rig.settle()
    listing = [v for v in rig.sink.of(ReportListView) if v.market == market][-1]
    if report_id is None and listing.items:
        report_id = listing.items[0].report_id
    view = None
    if report_id is not None:
        rig.data.load_report(report_id)
        rig.settle()
        view = [v for v in rig.sink.of(ReportView) if v.report_id == report_id][-1]
    settings = rig._read(_read_settings)
    statuses = rig.sink.of(AiStatusView)
    window = create_main_window(UiContext("0.0.1", "X", _NoActions()))
    window.resize(1100, 800)
    window.show()
    window.apply(settings)
    if statuses:
        window.apply(statuses[-1])
    screen = screen_widget(window, ScreenId.REPORTS)
    select_screen(window, ScreenId.REPORTS)
    screen.choose_market(market)
    window.apply(listing)
    ids = [item.report_id for item in listing.items]
    if report_id in ids:
        box = screen.findChild(QListWidget, "reportRounds")
        box.setCurrentRow(ids.index(report_id))
    if view is not None:
        window.apply(view)
    QApplication.processEvents()
    return ReportScreenProbe(window, screen)


def _read_settings(conn: Any) -> SettingsView:
    from coin_report_trader.storage.settings import read_settings

    return read_settings(conn)
