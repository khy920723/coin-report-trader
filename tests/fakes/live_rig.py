"""5단계 잇기의 시험이 함께 쓰는 자료 스레드의 틀(M5 명세 15번).

`trade_rig`의 틀(가짜 실행기, 가짜 시세, 가짜 시계, 3단계로 처음 설정을 마친 DB)에 가짜 업비트
(`FakeUpbitTransport`), 가짜 보관소(`FakeVault`), 실거래 설정, 켜는 조건(모의 회차, 전량 매도
기록)을
더한다. 값은 모두 만든 값이다(코인 `KRW-AAA`, `KRW-BBB`, 가격 1,000 근처의 정수, 호가 단위 1,
키는 꼴만 맞춘 글).

- 기본(M5 명세 6절): 처음 설정을 3단계로 마침, 코인 `KRW-AAA`, 60분봉, 키가 보관소에 있음,
  `KRW-AAA`의 모의 회차 N(30)회, 모의매매의 전량 매도 기록, 가짜 업비트의 수수료율 0.0005와 최소
  주문 금액 5,000원, 실거래 설정은 예산 100,000원·`KRW-AAA` 100%·하루 손실 한도 3,000원, 가짜
  업비트의 계정은 원화 200,000원·보유 없음. 사용자 멈춤 상태로 켠다(`paused`).
- `SqlLiveFills`: 실거래 체결의 쓰기와 읽기(`DataWorker._live_fill_store`에 넣는다). 진짜 저장
  함수(`storage/trading.py`의 `record_live_fill`, `fill_trade_keys`)를 부른다. `fills=False`이면
  넣지 않고 프로그램의 것(`StoredLiveFills`)을 그대로 쓴다.
"""

import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from coin_report_trader.app.data_thread import DataWorker
from coin_report_trader.core import (
    Direction,
    FillRecord,
    LiveSettings,
    Market,
    Mode,
    PauseKind,
    ReportState,
    RoundTrigger,
    SettingsView,
    TradeMethod,
)
from coin_report_trader.storage import reports as report_store
from coin_report_trader.storage import trading as trade_store
from coin_report_trader.storage.database import migrate, open_database
from coin_report_trader.storage.settings import write_key_created, write_trade_mode
from coin_report_trader.vault import UPBIT_ACCESS_KEY_NAME, UPBIT_SECRET_KEY_NAME
from fakes.ai import FakeAiRunner
from fakes.clock import FakeClock
from fakes.quote import FakeQuoteSource, make_candles
from fakes.stage3 import stage3_trade, write_stage3
from fakes.trade_rig import FOUR, HOUR, SAFETY, THIS_HOUR, Rig, Sink
from fakes.upbit import TEST_ACCESS_KEY, TEST_SECRET_KEY, FakeUpbitTransport
from fakes.vault import FakeVault

AAA, BBB = "KRW-AAA", "KRW-BBB"
PRICE = Decimal(1000)
KEYS = {UPBIT_ACCESS_KEY_NAME: TEST_ACCESS_KEY, UPBIT_SECRET_KEY_NAME: TEST_SECRET_KEY}
LIVE = LiveSettings(
    budget=Decimal(100_000),
    coin_ratios={AAA: Decimal(100)},
    daily_loss_limit=Decimal(3000),
)
PAST = datetime(2025, 12, 1, tzinfo=UTC)
"""모의 회차를 심는 때(시계의 처음보다 앞)."""
TODAY = date(2026, 1, 5)


class SqlLiveFills:
    """실거래 체결 한 줄을 쓰고 읽는 것(`app/live_engine.py`의 `LiveFillStore`). 진짜 저장 함수를
    부른다."""

    def write(self, conn: sqlite3.Connection, fill: FillRecord, trade_key: str) -> FillRecord:
        return trade_store.record_live_fill(conn, fill, trade_key)

    def trade_keys(self, conn: sqlite3.Connection, order_id: int) -> frozenset[str]:
        return trade_store.fill_trade_keys(conn, order_id)


@dataclass
class LiveRig(Rig):
    upbit: FakeUpbitTransport = None  # type: ignore[assignment]
    vault: FakeVault = None  # type: ignore[assignment]

    def live_orders(self) -> list[tuple[Any, ...]]:
        return self.query(
            "SELECT market, side, kind, state, sent FROM orders WHERE mode = 'live' ORDER BY id"
        )

    def upbit_calls(self, method: str | None = None, path: str | None = None) -> list[Any]:
        return [
            r
            for r in self.upbit.requests
            if (method is None or r.method == method) and (path is None or r.path == path)
        ]

    def mode(self) -> str:
        rows = self.query("SELECT value FROM settings WHERE key = 'trade_mode'")
        return "paper" if not rows else str(rows[0][0])


def seed_rounds(conn: sqlite3.Connection, market: str, count: int, method: TradeMethod) -> None:
    """그 코인의 `OK`인 모의 회차를 `count`개 심는다(지금 매매 방식)."""
    start = report_store.max_round_number(conn, Mode.PAPER)
    for index in range(count):
        close = PAST + timedelta(hours=index)
        round_id = report_store.insert_round(
            conn,
            mode=Mode.PAPER,
            number=start + index + 1,
            trigger=RoundTrigger.BAR_CLOSE,
            unit=HOUR,
            close_at=close,
            next_at=close + timedelta(hours=1),
            started_at=close + timedelta(seconds=10),
        )
        report_id = report_store.insert_report(
            conn, round_id=round_id, mode=Mode.PAPER, market=market, created_at=close
        )
        report_store.finish_report(
            conn,
            report_id,
            report_store.ReportRecord(
                state=ReportState.OK,
                base_price=PRICE,
                direction=Direction.UP.value,
                predicted_price=PRICE + 10,
                predicted_reason="만든 까닭",
                finished_at=close,
            ),
        )
        trade_store.set_report_method(conn, report_id, method)
        report_store.finish_round(
            conn, round_id, state="done", ended_at=close + timedelta(minutes=1)
        )


def seed_sold_all(conn: sqlite3.Connection) -> None:
    pause_id = trade_store.insert_pause(
        conn,
        mode=Mode.PAPER,
        kind=PauseKind.USER,
        cause=None,
        detail=None,
        sell_all=True,
        started_at=PAST,
    )
    trade_store.release_pause(conn, pause_id, released_at=PAST + timedelta(minutes=5))


def make_live_rig(
    tmp_path: Path,
    *,
    coins: tuple[str, ...] = (AAA,),
    live: LiveSettings | None = LIVE,
    rounds: int = 30,
    sold: bool = True,
    key: bool = True,
    key_created: date | None = date(2025, 12, 1),
    paused: bool = True,
    mode: Mode = Mode.PAPER,
    krw: Decimal = Decimal(200_000),
    holdings: dict[str, tuple[Decimal, Decimal]] | None = None,
    method: TradeMethod = TradeMethod.TREND,
    seed: Callable[[sqlite3.Connection], None] | None = None,
    fills: bool = True,
    clock: FakeClock | None = None,
    trade_changes: dict[str, Any] | None = None,
) -> LiveRig:
    db = tmp_path / "app.db"
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    conn = open_database(db)
    try:
        migrate(conn)
        view = SettingsView(coins, 30, True, candle_unit=HOUR, candle_count=100, ai_choices=FOUR)
        trade = replace(stage3_trade(coins, method=method), **(trade_changes or {}))
        write_stage3(conn, replace(view, trade=trade, live=live))
        for market in coins:
            seed_rounds(conn, market, rounds, method)
        if sold:
            seed_sold_all(conn)
        if paused:
            trade_store.insert_pause(
                conn,
                mode=Mode.PAPER,
                kind=PauseKind.USER,
                cause=None,
                detail=None,
                sell_all=False,
                started_at=PAST + timedelta(days=1),
            )
        if key and key_created is not None:
            write_key_created(conn, key_created)
        if mode is Mode.LIVE:
            write_trade_mode(conn, Mode.LIVE)
        if seed is not None:
            seed(conn)
    finally:
        conn.close()
    clock = clock or FakeClock()
    quote = FakeQuoteSource(
        markets=[Market(c, f"이름 {c}", False) for c in (AAA, BBB)], clock=clock
    )
    for coin in (AAA, BBB):
        quote.set_candles(coin, HOUR, make_candles(coin, HOUR, 200, last_start=THIS_HOUR))
        quote.set_book(coin, PRICE + 1, PRICE)
    quote.set_prices({AAA: PRICE, BBB: PRICE})
    upbit = FakeUpbitTransport(clock=clock)
    upbit.set_account(krw=krw, coins=holdings or {})
    for coin in (AAA, BBB):
        upbit.set_chance(coin)
        upbit.set_market_price(coin, PRICE)
    vault = FakeVault(KEYS if key else None)
    runner = FakeAiRunner()
    sink = Sink()
    worker = DataWorker(
        database_file=db,
        logs_dir=logs,
        clock=clock,
        quote=quote,
        sink=sink,
        ai=runner,
        sessions_root=tmp_path / "ai-sessions",
        vault=vault,
        exchange_transport=upbit,
    )
    if fills:
        worker._live_fill_store = SqlLiveFills()
    return LiveRig(worker, sink, clock, quote, runner, db, upbit=upbit, vault=vault)


def live_rig_builder(tmp_path: Path) -> Iterator[Callable[..., LiveRig]]:
    """`build` 픽스처의 몸(시험 파일마다 `@pytest.fixture`로 감싼다)."""
    rigs: list[LiveRig] = []

    def factory(**kwargs: Any) -> LiveRig:
        root = tmp_path / f"r{len(rigs)}"
        root.mkdir()
        rig = make_live_rig(root, **kwargs)
        rigs.append(rig)
        return rig

    yield factory
    for rig in rigs:
        rig.worker.stop()
        rig.worker.join(SAFETY)
