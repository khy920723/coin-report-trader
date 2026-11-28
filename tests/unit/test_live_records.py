"""5단계 잇기: 기록·보고서의 모드, 외부 변동 탭과 CSV, 알림의 모드(M5 명세 3.4, 3.10, 3.11, 4절
15번). 값은 모두 만든 값이다."""

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fakes.live_rig import AAA, LiveRig, live_rig_builder

from coin_report_trader.core import (
    HistoryTab,
    HistoryView,
    LoadState,
    Mode,
    OrderKind,
    OrderPlan,
    OrderReason,
    OrderSide,
    OrderState,
    ReportListView,
)
from coin_report_trader.storage import trading as trade_store
from coin_report_trader.storage.database import open_database

DAY = date(2026, 1, 5)
AT = datetime(2026, 1, 5, 2, tzinfo=UTC)


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Callable[..., LiveRig]]:
    yield from live_rig_builder(tmp_path)


def seed_changes(conn: object) -> None:
    trade_store.insert_external_change(
        conn,
        mode=Mode.LIVE,
        market=AAA,
        qty_delta=Decimal("2.5"),
        at=AT,  # type: ignore[arg-type]
    )
    trade_store.insert_external_change(
        conn,
        mode=Mode.LIVE,
        market="KRW",
        qty_delta=Decimal(-5000),
        at=AT,  # type: ignore[arg-type]
    )
    trade_store.insert_external_change(
        conn,
        mode=Mode.PAPER,
        market=AAA,
        qty_delta=Decimal(9),
        at=AT,  # type: ignore[arg-type]
    )


def history(rig: LiveRig, tab: HistoryTab, mode: Mode | None = None) -> HistoryView:
    rig.worker.load_history(tab, None, DAY, mode)
    rig.settle()
    return rig.sink.of(HistoryView)[-1]


def test_the_external_tab_shows_the_live_changes(build: Callable[..., LiveRig]) -> None:
    rig = build(seed=seed_changes)
    rig.start()
    view = history(rig, HistoryTab.EXTERNAL, Mode.LIVE)
    assert view.state is LoadState.READY
    assert view.columns == ("시각", "코인", "수량 변화")
    assert sorted(row.cells[1:] for row in view.rows) == [("AAA", "+2.5"), ("원화", "-5000")]
    assert history(rig, HistoryTab.EXTERNAL, Mode.PAPER).rows[0].cells[1:] == ("AAA", "+9")


def test_the_external_csv_has_its_header_and_the_chosen_mode(
    build: Callable[..., LiveRig], tmp_path: Path
) -> None:
    rig = build(seed=seed_changes)
    rig.start()
    history(rig, HistoryTab.EXTERNAL, Mode.LIVE)
    path = tmp_path / "external.csv"
    rig.worker.export_history(HistoryTab.EXTERNAL, None, str(path))
    rig.settle()
    text = path.read_text(encoding="utf-8").lstrip(chr(0xFEFF))
    lines = text.strip().splitlines()
    assert lines[0] == "utc,market,qty_delta"
    assert sorted(lines[1:]) == [
        "2026-01-05T02:00:00Z,KRW,-5000",
        "2026-01-05T02:00:00Z,KRW-AAA,2.5",
    ]


def seed_orders(conn: object) -> None:
    for mode, qty in ((Mode.PAPER, "1"), (Mode.LIVE, "2"), (Mode.LIVE, "3")):
        plan = OrderPlan(
            AAA, OrderSide.BUY, OrderKind.LIMIT, OrderReason.JUDGMENT, Decimal(990), Decimal(qty),
            Decimal(990) * Decimal(qty),
        )  # fmt: skip
        order = trade_store.insert_order(
            conn,  # type: ignore[arg-type]
            plan,
            mode=mode,
            uid=f"seed-{mode.value}-{qty}",
            round_id=None,
            created_at=AT,
            judged_until=AT,
        )
        trade_store.update_order(
            conn,  # type: ignore[arg-type]
            order.order_id,
            state=OrderState.CANCELED,
            closed_at=AT,
            sent=True,
        )


def test_the_history_default_is_the_current_mode(build: Callable[..., LiveRig]) -> None:
    rig = build(mode=Mode.LIVE, seed=seed_orders)
    rig.start()
    assert len(history(rig, HistoryTab.ORDERS).rows) == 2  # 지금 모드(실거래)의 기록
    assert len(history(rig, HistoryTab.ORDERS, Mode.PAPER).rows) == 1


def test_load_reports_takes_the_mode(build: Callable[..., LiveRig]) -> None:
    rig = build(mode=Mode.LIVE)
    rig.start()
    rig.worker.load_reports(AAA, Mode.PAPER)
    rig.settle()
    assert len(rig.sink.of(ReportListView)[-1].items) > 0
    rig.worker.load_reports(AAA)
    rig.settle()
    assert rig.sink.of(ReportListView)[-1].items == ()


def test_alerts_carry_the_mode_of_the_moment(build: Callable[..., LiveRig]) -> None:
    rig = build(mode=Mode.LIVE, key=False)
    rig.start()
    conn = open_database(rig.db)
    try:
        rows = conn.execute("SELECT code, mode FROM alerts").fetchall()
    finally:
        conn.close()
    assert ("A-23", "live") in rows
    pauses = rig.query("SELECT mode, kind FROM pauses WHERE released_utc IS NULL ORDER BY id")
    assert pauses == [("paper", "user"), ("live", "error")]
