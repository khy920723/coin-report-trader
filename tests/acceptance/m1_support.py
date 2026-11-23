"""M1 1단계 보완 수용 시험(명세 6절의 B1~B44)이 함께 쓰는 도구.

- 자료 스레드(`DataWorker`)와 보내기 스레드(`SendWorker`)를 Qt 없이 함께 돌리는 `Rig`.
- 바깥은 모두 `tests/fakes/`의 가짜(시세, 디스코드, 보관소, 시계)다. 네트워크, keyring, 진짜
  웹후크를 쓰지 않는다. 시각은 가짜 시계의 `advance`로만 옮긴다(실제 시간은 안전 시간으로만 쓴다).
- 주소처럼 생긴 글은 실행 중에 이어 붙여 만든다(명세 3.13). 숫자는 만든 값이다.

이 파일은 시험을 담지 않는다(`test_`로 시작하지 않아 모으지 않는다).
"""

import json
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from acceptance.m3_support import plant_prices, planted_trade
from fakes.clock import DEFAULT_START, FakeClock
from fakes.discord import FakeDiscord
from fakes.quote import FakeQuoteSource
from fakes.vault import FakeVault

from coin_report_trader.app import startup_errors
from coin_report_trader.app.data_thread import DataWorker
from coin_report_trader.app.send_thread import SendWorker
from coin_report_trader.core import (
    AlertCategory,
    AlertView,
    Candle,
    CandleUnit,
    CoinView,
    Market,
    SettingsView,
    UiUpdate,
)
from coin_report_trader.storage import alerts as alert_store
from coin_report_trader.storage.candles import read_candles, write_candles
from coin_report_trader.storage.database import migrate, open_database
from coin_report_trader.storage.settings import (
    read_fetched_at,
    read_settings,
    write_fetched_at,
    write_settings,
)
from coin_report_trader.vault import webhook_secret_name

AAA, BBB = "KRW-AAA", "KRW-BBB"
MIN30, HOUR, MIN240, DAY = (
    CandleUnit.MINUTE_30,
    CandleUnit.MINUTE_60,
    CandleUnit.MINUTE_240,
    CandleUnit.DAY,
)
SAFETY = 10.0
NOW = DEFAULT_START
"""가짜 시계의 처음 시각: 2026-01-05 03:00 UTC(한국 12:00)."""
THIS_HOUR = NOW.replace(minute=0)
"""`NOW`에 걸쳐 있는(미완성) 60분봉의 시작."""

# 명세 3.5.2의 다섯 60분봉(시가, 고가, 저가, 종가).
SPEC_BARS = ((100, 110, 100, 110), (110, 110, 99, 99), (99, 121, 99, 121), (121, 121, 110, 110),
             (110, 132, 110, 132))  # fmt: skip


def bar(
    start: datetime, o: int, h: int, low: int, c: int, *, market: str = AAA, unit: CandleUnit = HOUR
) -> Candle:
    d = Decimal
    return Candle(market, unit, start, d(o), d(h), d(low), d(c), d(1), d(1))


def spec_bars(first: datetime, *, market: str = AAA) -> list[Candle]:
    """`first`부터 한 시간씩의 명세 3.5.2의 다섯 60분봉."""
    return [
        bar(first + timedelta(hours=i), *values, market=market)
        for i, values in enumerate(SPEC_BARS)
    ]


def markets(*codes: str) -> list[Market]:
    return [Market(code, f"이름 {code}", False) for code in codes]


def webhook_url(tag: str = "a") -> str:
    """주소의 꼴을 지킨 시험용 글. 실행 중에 이어 붙인다(저장소에 주소의 꼴을 두지 않는다)."""
    return "https://" + "discord.com/api/webhooks/" + "7" * 18 + "/" + (tag * 64)[:64]


def token_of(url: str) -> str:
    return url.rsplit("/", 1)[1]


@dataclass
class Sink:
    """화면에 가는 값을 모은다. 보낸 스레드의 이름도 적는다."""

    items: list[UiUpdate] = field(default_factory=list)
    threads: dict[str, set[str]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __call__(self, update: UiUpdate) -> None:
        with self.lock:
            self.items.append(update)
            name = type(update).__name__
            self.threads.setdefault(name, set()).add(threading.current_thread().name)

    def of(self, kind: type) -> list[Any]:
        with self.lock:
            return [u for u in self.items if isinstance(u, kind)]

    def coin(self, market: str) -> list[CoinView]:
        return [v for v in self.of(CoinView) if v.market == market]

    def clear(self) -> None:
        with self.lock:
            self.items.clear()


@dataclass
class Rig:
    data: DataWorker
    send: SendWorker
    sink: Sink
    clock: FakeClock
    quote: FakeQuoteSource
    discord: FakeDiscord
    vault: FakeVault
    db: Path
    logs: Path

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

    def advance(self, seconds: float) -> None:
        self.clock.advance(seconds)
        self.settle()

    def stop(self) -> None:
        self.discord.release()
        self.data.stop()
        self.send.stop()
        self.data.join(SAFETY)
        self.send.join(SAFETY)
        for worker in (self.data, self.send):
            assert worker.thread is None or not worker.thread.is_alive()

    def _read(self, read: Callable[[Any], Any]) -> Any:
        conn = open_database(self.db)
        try:
            return read(conn)
        finally:
            conn.close()

    def alerts(self) -> list[AlertView]:
        """오래된 것이 앞."""
        return list(reversed(self._read(alert_store.list_alerts)))

    def rows(self, sql: str) -> list[tuple[Any, ...]]:
        return self._read(lambda conn: conn.execute(sql).fetchall())

    def stored(self, market: str, unit: CandleUnit) -> tuple[Candle, ...]:
        return self._read(lambda conn: read_candles(conn, market, unit))

    def fetched_at(self, market: str) -> datetime | None:
        return self._read(lambda conn: read_fetched_at(conn, market))

    def settings(self) -> SettingsView:
        return self._read(read_settings)

    def jsonl(self) -> list[dict[str, Any]]:
        path = self.logs / startup_errors.FILE_NAME
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def prepare_db(
    db: Path,
    *,
    settings: SettingsView | None,
    candles: Iterable[Candle] = (),
    fetched: dict[str, datetime] | None = None,
    raw: Iterable[tuple[str, str]] = (),
) -> None:
    """DB를 스키마 1로 만들고 설정, 봉, 받은 때, 날것의 설정 키를 쓴다."""
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = open_database(db)
    try:
        migrate(conn)
        if settings is not None:
            write_settings(conn, settings)
        stored = list(candles)
        if stored:
            write_candles(conn, stored)
        for market, moment in (fetched or {}).items():
            write_fetched_at(conn, market, moment)
        for key, value in raw:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()


def make_rig(
    tmp_path: Path,
    *,
    coins: tuple[str, ...] = (AAA,),
    days: int | None = 30,
    done: bool = True,
    unit: CandleUnit | None = HOUR,
    count: int | None = 100,
    fresh: bool = True,
    candles: Iterable[Candle] = (),
    quote: FakeQuoteSource | None = None,
    discord: FakeDiscord | None = None,
    vault: FakeVault | None = None,
    saved: dict[AlertCategory, str] | None = None,
    clock: FakeClock | None = None,
) -> Rig:
    """`fresh`이면 DB를 새로 만들어 설정과 `candles`를 쓴다. 아니면 있는 DB를 그대로 쓴다."""
    db = tmp_path / "data" / "app.db"
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    if fresh:
        # 1단계 보완 전의 기록("1")은 봉 단위나 개수가 없는 설정이다. 그 밖의 마친 DB는 3단계로
        # 심는다: `first_setup_level` "3", 3단계 키, 가상 계정(M3 명세 4절 31번).
        stage3 = done and unit is not None and count is not None
        settings = SettingsView(
            coins,
            days,
            done,
            candle_unit=unit,
            candle_count=count,
            trade=planted_trade(coins) if stage3 else None,
        )
        prepare_db(db, settings=settings, candles=candles)
    clock = clock or FakeClock()
    quote = quote or FakeQuoteSource(markets=markets(AAA, BBB), clock=clock)
    plant_prices(quote, coins)  # 23번 뒤의 손절 점검이 고른 코인의 현재가를 읽는다(M3 명세 3.9)
    discord = discord or FakeDiscord()
    if vault is None:
        vault = FakeVault({webhook_secret_name(c): url for c, url in (saved or {}).items()})
    sink = Sink()
    send = SendWorker(webhook=discord, vault=vault, clock=clock, sink=sink)
    data = DataWorker(
        database_file=db, logs_dir=logs, clock=clock, quote=quote, sink=sink, send=send
    )
    send.attach(data)
    return Rig(data, send, sink, clock, quote, discord, vault, db, logs)


def restart(rig: Rig, tmp_path: Path) -> Rig:
    """끄고 다시 켠다: 같은 DB, 로그 폴더, 시계, 시세, 디스코드, 보관소로 새 두 스레드를 만든다."""
    rig.stop()
    return make_rig(
        tmp_path,
        fresh=False,
        quote=rig.quote,
        discord=rig.discord,
        vault=rig.vault,
        clock=rig.clock,
    )


def until(condition: Callable[[], bool], limit: float = SAFETY) -> bool:
    waiter = threading.Event()
    for _ in range(int(limit / 0.002)):
        if condition():
            return True
        waiter.wait(0.002)
    return condition()


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)  # type: ignore[misc]
