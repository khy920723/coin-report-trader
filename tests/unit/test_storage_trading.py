"""3단계 표의 쓰기와 읽기, 설정의 매매 칸(M3 명세 3.11.1, 3.12). 값은 모두 만든 값이다."""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from coin_report_trader.core import (
    CandleUnit,
    ErrorPauseCause,
    FillRecord,
    Mode,
    OrderKind,
    OrderPlan,
    OrderReason,
    OrderSide,
    OrderState,
    PaperAccount,
    PaperHolding,
    PauseKind,
    PauseStopLoss,
    ReportState,
    RoundStage,
    RoundTrigger,
    SettingsView,
    StorageError,
    TradeMethod,
    TradeSettings,
)
from coin_report_trader.storage import reports as report_store
from coin_report_trader.storage import trading
from coin_report_trader.storage.database import migrate, open_database
from coin_report_trader.storage.settings import read_settings, write_settings

AAA, BBB = "KRW-AAA", "KRW-BBB"
PAPER = Mode.PAPER
T0 = datetime(2026, 1, 5, 3, 0, tzinfo=UTC)
D = Decimal


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_database(tmp_path / "app.db")
    migrate(connection)
    yield connection
    connection.close()


def plan(market: str = AAA, side: OrderSide = OrderSide.BUY, **changes: Any) -> OrderPlan:
    base: dict[str, Any] = {
        "market": market,
        "side": side,
        "kind": OrderKind.LIMIT,
        "reason": OrderReason.JUDGMENT,
        "price": D("1000"),
        "qty": D("2"),
        "amount": D("2000"),
    }
    base.update(changes)
    return OrderPlan(**base)


def trade(**changes: Any) -> TradeSettings:
    base: dict[str, Any] = {
        "budget": D("1000000"),
        "coin_ratios": {AAA: D("60"), BBB: D("40")},
        "first_ratio": D("30"),
        "mr_first_ratio": None,
        "add_ratio": D("10"),
        "tolerance": D("1.25"),
        "stop_ratio": D("5"),
        "daily_loss_limit": D("3000"),
        "order_interval_s": 60,
        "method": TradeMethod.TREND,
        "pause_stop_loss": PauseStopLoss.KEEP,
        "mr_sell_unknown_fee": False,
        "mr_tolerable_loss": D("5"),
        "paper_funds": D("100000"),
        "backup_days": 7,
    }
    base.update(changes)
    return TradeSettings(**base)


def view(coins: tuple[str, ...] = (AAA, BBB), **changes: Any) -> SettingsView:
    return SettingsView(
        coins, 30, True, candle_unit=CandleUnit.MINUTE_60, candle_count=100, **changes
    )


def level(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = 'first_setup_level'").fetchone()
    return None if row is None else row[0]


def keys(conn: sqlite3.Connection) -> dict[str, str]:
    return dict(conn.execute("SELECT key, value FROM settings").fetchall())


# --- 설정의 매매 칸(3.11.1) ----------------------------------------------------------------------


def test_the_trade_keys_and_value_texts_follow_the_spec(conn: sqlite3.Connection) -> None:
    write_settings(conn, view(trade=trade()), created_at=T0)
    stored = keys(conn)
    assert {k: stored[k] for k in stored if k not in ("coins", "retention_days")} == {
        "candle_unit": "minute60",
        "candle_count": "100",
        "first_setup_level": "3",
        "budget": "1000000",
        "coin_ratio:KRW-AAA": "60",
        "coin_ratio:KRW-BBB": "40",
        "first_ratio": "30",
        "add_ratio": "10",
        "tolerance": "1.25",
        "stop_ratio": "5",
        "daily_loss_limit": "3000",
        "order_interval_s": "60",
        "trade_method": "trend",
        "pause_stop_loss": "keep",
        "mr_sell_unknown_fee": "no",
        "mr_tolerable_loss": "5",
        "paper_funds": "100000",
        "backup_days": "7",
    }
    assert read_settings(conn).trade == trade()


def test_the_trade_is_read_only_when_every_required_key_is_stored(
    conn: sqlite3.Connection,
) -> None:
    write_settings(conn, view(trade=trade()), created_at=T0)
    assert read_settings(conn).trade is not None
    for key in ("budget", "coin_ratio:KRW-BBB", "tolerance", "backup_days", "first_ratio"):
        saved = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()[0]
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        assert read_settings(conn).trade is None, key
        conn.execute("INSERT INTO settings VALUES (?, ?)", (key, saved))
    conn.execute("UPDATE settings SET value = 'maybe' WHERE key = 'mr_sell_unknown_fee'")
    assert read_settings(conn).trade is None  # 글이 틀린 칸이 있으면 없음이다
    conn.execute("UPDATE settings SET value = 'yes' WHERE key = 'mr_sell_unknown_fee'")
    assert read_settings(conn).trade == trade(mr_sell_unknown_fee=True)
    conn.execute("UPDATE settings SET value = '열' WHERE key = 'budget'")
    assert read_settings(conn).trade is None  # 매매 칸 때문에 설정 전체를 읽지 못하지 않는다
    assert read_settings(conn).coins == (AAA, BBB)


def test_the_chosen_method_needs_its_first_ratio_and_the_other_can_be_empty(
    conn: sqlite3.Connection,
) -> None:
    mean = trade(method=TradeMethod.MEAN_REVERSION, first_ratio=None, mr_first_ratio=D("20"))
    write_settings(conn, view(trade=mean), created_at=T0)
    assert read_settings(conn).trade == mean
    assert "first_ratio" not in keys(conn)  # 없음이면 그 키를 지운다
    conn.execute("UPDATE settings SET value = 'trend' WHERE key = 'trade_method'")
    assert read_settings(conn).trade is None  # 추세추종의 첫 매수 비율이 없다


def test_a_save_without_trade_leaves_the_trade_keys_and_does_not_lower_the_level(
    conn: sqlite3.Connection,
) -> None:
    write_settings(conn, view(trade=trade()), created_at=T0)
    write_settings(conn, view())  # `trade=None`
    assert level(conn) == "3"
    assert read_settings(conn).trade == trade()


def test_the_level_is_three_when_the_stage3_keys_are_stored_even_without_trade(
    conn: sqlite3.Connection,
) -> None:
    write_settings(conn, view())
    assert level(conn) == "2"  # 1단계 보완에서 마침
    for key, value in {
        "budget": "1000", "coin_ratio:KRW-AAA": "60", "coin_ratio:KRW-BBB": "40",
        "first_ratio": "30", "add_ratio": "10", "tolerance": "0", "stop_ratio": "5",
        "daily_loss_limit": "3000", "order_interval_s": "60", "trade_method": "trend",
        "pause_stop_loss": "stop", "mr_sell_unknown_fee": "no", "mr_tolerable_loss": "5",
        "paper_funds": "5000", "backup_days": "3",
    }.items():  # fmt: skip
        conn.execute("INSERT INTO settings VALUES (?, ?)", (key, value))
    conn.commit()
    write_settings(conn, view(), created_at=T0)
    assert level(conn) == "3"
    write_settings(conn, view(coins=(AAA,)))  # 한 코인만 남아도(그 코인의 비율은 있다)
    assert level(conn) == "3"


def test_the_first_three_creates_the_paper_account_once_and_funds_are_then_kept(
    conn: sqlite3.Connection,
) -> None:
    write_settings(conn, view(trade=trade()), created_at=T0)
    assert trading.read_paper_funds(conn, PAPER) == D("100000")
    account = trading.read_paper_account(conn, PAPER)
    assert account == PaperAccount(D("100000"), D("0"), ())
    assert conn.execute("SELECT created_utc FROM paper_accounts").fetchone() == (
        "2026-01-05T03:00:00Z",
    )
    write_settings(conn, view(trade=trade(paper_funds=D("999"))), created_at=T0 + timedelta(1))
    assert keys(conn)["paper_funds"] == "100000"  # 모의매매를 시작한 뒤에는 고쳐 쓰지 않는다
    assert trading.read_paper_funds(conn, PAPER) == D("100000")
    assert conn.execute("SELECT count(*) FROM paper_accounts").fetchone() == (1,)


def test_the_level_three_and_the_account_are_one_transaction(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TRIGGER stop BEFORE INSERT ON paper_accounts BEGIN SELECT RAISE(ABORT, 'x'); END"
    )
    with pytest.raises(StorageError):
        write_settings(conn, view(trade=trade()), created_at=T0)
    assert level(conn) is None
    assert "budget" not in keys(conn)


def test_ratios_of_coins_not_in_the_list_are_removed_on_every_save(
    conn: sqlite3.Connection,
) -> None:
    write_settings(conn, view(trade=trade()), created_at=T0)
    write_settings(conn, view(coins=(AAA,)))
    assert [k for k in keys(conn) if k.startswith("coin_ratio:")] == ["coin_ratio:KRW-AAA"]


def test_a_save_that_is_not_done_still_deletes_the_level(conn: sqlite3.Connection) -> None:
    write_settings(conn, SettingsView((AAA,), 30, False))
    assert level(conn) is None
    assert read_settings(conn).trade is None


# --- 가상 계정 ----------------------------------------------------------------------------------


def test_the_paper_account_is_made_once_and_holdings_keep_their_rows(
    conn: sqlite3.Connection,
) -> None:
    assert trading.read_paper_account(conn, PAPER) is None
    assert trading.create_paper_account(conn, PAPER, D("50000"), created_at=T0)
    assert not trading.create_paper_account(conn, PAPER, D("1"), created_at=T0)
    held = PaperHolding(AAA, D("15"), D("5"), None, D("1550"), D("15"))
    trading.write_paper_account(conn, PAPER, PaperAccount(D("40000"), D("1000"), (held,)))
    account = trading.read_paper_account(conn, PAPER)
    assert account is not None
    assert (account.krw, account.locked_krw) == (D("40000"), D("1000"))
    (row,) = account.holdings
    assert row.avg_price == D("1550") / D("15")  # 평균 매수가는 매수 합으로 셈한다
    gone = PaperHolding(AAA, D("0"), D("0"), None, D("0"), D("0"))
    trading.write_paper_account(conn, PAPER, PaperAccount(D("1"), D("0"), (gone,)))
    account = trading.read_paper_account(conn, PAPER)
    assert account is not None
    assert account.holdings[0].qty == 0  # 보유가 0이어도 줄은 남는다
    assert account.holdings[0].avg_price is None
    left = PaperHolding(AAA, D("0"), D("0"), None, D("1000"), D("10"))
    trading.write_paper_account(conn, PAPER, PaperAccount(D("1"), D("0"), (left,)))
    account = trading.read_paper_account(conn, PAPER)
    assert account is not None
    assert account.holdings[0].avg_price is None  # 보유가 0이면 매수 합이 남아도 없음이다
    with pytest.raises(StorageError):
        trading.write_paper_account(conn, Mode.LIVE, PaperAccount(D("1"), D("0"), ()))


# --- 주문과 체결 ---------------------------------------------------------------------------------


def test_an_order_is_written_open_and_unsent_then_updated(conn: sqlite3.Connection) -> None:
    record = trading.insert_order(
        conn, plan(), mode=PAPER, uid="u-1", round_id=None, created_at=T0, judged_until=T0
    )
    assert (record.state, record.uid, record.price) == (OrderState.OPEN, "u-1", D("1000"))
    assert conn.execute("SELECT sent FROM orders").fetchone() == (0,)
    assert trading.list_unsent_orders(conn, PAPER) == (record,)
    assert trading.find_order(conn, "u-1") == record
    assert trading.read_order(conn, record.order_id) == record
    assert trading.list_open_orders(conn, PAPER, AAA) == (record,)
    assert trading.list_open_orders(conn, PAPER, BBB) == ()
    assert trading.update_order(conn, record.order_id, state=OrderState.OPEN, sent=True)
    assert trading.list_unsent_orders(conn, PAPER) == ()
    later = T0 + timedelta(minutes=5)
    assert trading.set_judged_until(conn, record.order_id, later)
    assert trading.read_judged_until(conn, record.order_id) == later
    trading.update_order(
        conn, record.order_id, state=OrderState.CANCELED, closed_at=later, canceled_by_round=True
    )
    closed = trading.read_order(conn, record.order_id)
    assert closed is not None
    assert (closed.state, closed.closed_utc) == (OrderState.CANCELED, later)
    assert conn.execute("SELECT canceled_by_round FROM orders").fetchone() == (1,)
    assert trading.list_open_orders(conn, PAPER) == ()
    with pytest.raises(StorageError):
        trading.insert_order(
            conn, plan(), mode=PAPER, uid="u-1", round_id=None, created_at=T0, judged_until=T0
        )
    assert not trading.update_order(conn, 999, state=OrderState.FAILED)


def test_a_market_order_has_no_price(conn: sqlite3.Connection) -> None:
    record = trading.insert_order(
        conn,
        plan(side=OrderSide.SELL, kind=OrderKind.MARKET, reason=OrderReason.STOP_LOSS, price=None),
        mode=PAPER,
        uid="u-2",
        round_id=None,
        created_at=T0,
        judged_until=T0,
    )
    assert trading.read_order(conn, record.order_id) == record
    assert record.price is None


def test_canceled_judgment_orders_are_counted_by_the_day_they_were_made(
    conn: sqlite3.Connection,
) -> None:
    def put(uid: str, at: datetime, state: OrderState, reason: OrderReason) -> None:
        record = trading.insert_order(
            conn, plan(reason=reason), mode=PAPER, uid=uid, round_id=None, created_at=at,
            judged_until=at,
        )  # fmt: skip
        trading.update_order(conn, record.order_id, state=state, closed_at=at + timedelta(1))

    start, end = T0, T0 + timedelta(days=1)
    put("a", T0 + timedelta(hours=1), OrderState.CANCELED, OrderReason.JUDGMENT)
    put("b", T0 + timedelta(hours=2), OrderState.CANCELED, OrderReason.JUDGMENT)
    put("c", T0 + timedelta(hours=3), OrderState.FILLED, OrderReason.JUDGMENT)
    put("d", T0 + timedelta(hours=4), OrderState.CANCELED, OrderReason.STOP_LOSS)
    put("e", T0 - timedelta(hours=1), OrderState.CANCELED, OrderReason.JUDGMENT)  # 앞날
    assert trading.count_canceled_judgment_orders(conn, PAPER, AAA, start=start, end=end) == 2
    assert trading.count_canceled_judgment_orders(conn, PAPER, BBB, start=start, end=end) == 0
    assert trading.last_order_time(conn, PAPER, AAA) == T0 + timedelta(hours=4)
    assert trading.last_order_time(conn, PAPER, BBB) is None
    day = trading.list_orders(conn, PAPER, start=start, end=end)
    assert [o.uid for o in day] == ["d", "c", "b", "a"]  # 새것이 앞


def fill_of(order_id: int, side: OrderSide, pnl: str | None, at: datetime) -> FillRecord:
    return FillRecord(
        0, order_id, PAPER, AAA, side, D("1000"), D("2"), D("1"),
        None if pnl is None else D(pnl), at,
    )  # fmt: skip


def test_a_fill_writes_the_fill_the_order_and_the_account_in_one_transaction(
    conn: sqlite3.Connection,
) -> None:
    trading.create_paper_account(conn, PAPER, D("10000"), created_at=T0)
    order = trading.insert_order(
        conn, plan(), mode=PAPER, uid="u", round_id=None, created_at=T0, judged_until=T0
    )
    after = PaperAccount(D("7999"), D("0"), (PaperHolding(AAA, D("2"), D("0"), None, D("2000"),
                                                         D("2")),))  # fmt: skip
    written = trading.record_fill(conn, fill_of(order.order_id, OrderSide.BUY, None, T0), after)
    assert written.fill_id == 1
    assert trading.fills_of(conn, order.order_id) == (written,)
    closed = trading.read_order(conn, order.order_id)
    assert closed is not None
    assert (closed.state, closed.closed_utc) == (OrderState.FILLED, T0)
    account = trading.read_paper_account(conn, PAPER)
    assert account is not None
    assert account.krw == D("7999")
    with pytest.raises(StorageError):  # 같은 주문의 체결을 두 번 쓰지 않는다
        trading.record_fill(conn, fill_of(order.order_id, OrderSide.BUY, None, T0), after)
    second = trading.insert_order(
        conn, plan(), mode=PAPER, uid="v", round_id=None, created_at=T0, judged_until=T0
    )
    conn.execute(
        "CREATE TRIGGER stop BEFORE UPDATE ON paper_accounts BEGIN SELECT RAISE(ABORT, 'x'); END"
    )
    with pytest.raises(StorageError):
        trading.record_fill(conn, fill_of(second.order_id, OrderSide.BUY, None, T0), after)
    assert trading.fills_of(conn, second.order_id) == ()  # 아무것도 바뀌지 않았다
    still = trading.read_order(conn, second.order_id)
    assert still is not None
    assert still.state is OrderState.OPEN


def test_the_day_pnl_counts_only_sells_and_the_last_stop_loss_fill_is_found(
    conn: sqlite3.Connection,
) -> None:
    trading.create_paper_account(conn, PAPER, D("10000"), created_at=T0)
    account = PaperAccount(D("10000"), D("0"), ())

    def sold(uid: str, reason: OrderReason, pnl: str, at: datetime) -> None:
        order = trading.insert_order(
            conn, plan(side=OrderSide.SELL, reason=reason), mode=PAPER, uid=uid, round_id=None,
            created_at=at, judged_until=at,
        )  # fmt: skip
        trading.record_fill(conn, fill_of(order.order_id, OrderSide.SELL, pnl, at), account)

    sold("a", OrderReason.JUDGMENT, "-1200", T0 + timedelta(hours=1))
    sold("b", OrderReason.STOP_LOSS, "-1900", T0 + timedelta(hours=2))
    sold("c", OrderReason.JUDGMENT, "500", T0 - timedelta(hours=1))  # 앞날
    bought = trading.insert_order(
        conn, plan(), mode=PAPER, uid="d", round_id=None, created_at=T0, judged_until=T0
    )
    odd = fill_of(bought.order_id, OrderSide.BUY, "7", T0 + timedelta(hours=3))
    trading.record_fill(conn, odd, account)  # 산 체결은 손익 합에 들지 않는다
    day = {"start": T0, "end": T0 + timedelta(days=1)}
    assert trading.realized_pnl_between(conn, PAPER, **day) == D("-3100")  # 3.8.2의 예
    stop = trading.last_stop_loss_fill(conn, PAPER, AAA)
    assert stop is not None
    assert stop.filled_utc == T0 + timedelta(hours=2)
    assert trading.last_stop_loss_fill(conn, PAPER, BBB) is None
    assert [f.realized_pnl for f in trading.list_fills(conn, PAPER, **day)] == [
        D("7"),
        D("-1900"),
        D("-1200"),
    ]
    assert len(trading.list_fills(conn, PAPER)) == 4
    assert trading.list_fills(conn, PAPER, market=BBB) == ()


# --- 회차, 보고서, 멈춤, 설정 바뀜 ----------------------------------------------------------


def a_round(conn: sqlite3.Connection, number: int, started: datetime) -> int:
    return report_store.insert_round(
        conn, mode=PAPER, number=number, trigger=RoundTrigger.BAR_CLOSE,
        unit=CandleUnit.MINUTE_60, close_at=started, next_at=started + timedelta(hours=1),
        started_at=started,
    )  # fmt: skip


def test_round_stage_discard_and_the_rounds_around_a_moment(conn: sqlite3.Connection) -> None:
    first = a_round(conn, 1, T0)
    report = report_store.insert_report(conn, round_id=first, mode=PAPER, market=AAA, created_at=T0)
    assert trading.set_round_stage(conn, first, RoundStage.AI)
    assert trading.round_running_at(conn, PAPER, T0 + timedelta(minutes=5))  # 끝나지 않은 회차
    assert trading.discard_round(conn, first, ended_at=T0 + timedelta(minutes=10)) == 1
    assert conn.execute("SELECT state, stage FROM rounds").fetchone() == ("discarded", "AI 호출")
    item = report_store.list_round_items(conn, AAA, PAPER)[0]
    assert item.state is ReportState.DISCARDED
    assert item.report_id == report
    assert trading.round_running_at(conn, PAPER, T0 + timedelta(minutes=5))
    assert not trading.round_running_at(conn, PAPER, T0 + timedelta(minutes=10))
    second = a_round(conn, 2, T0 + timedelta(hours=1))
    assert trading.first_round_after(conn, PAPER, T0 + timedelta(minutes=30)) == second
    assert trading.first_round_after(conn, PAPER, T0 + timedelta(hours=2)) is None
    rows = trading.list_round_rows(conn, PAPER, start=T0, end=T0 + timedelta(days=1))
    assert [(r.number, r.stage, r.state) for r in rows] == [
        (2, None, "running"),
        (1, RoundStage.AI, "discarded"),
    ]


def test_the_report_method_order_and_judgment_rows(conn: sqlite3.Connection) -> None:
    round_id = a_round(conn, 1, T0)
    report = report_store.insert_report(
        conn, round_id=round_id, mode=PAPER, market=AAA, created_at=T0
    )
    assert trading.set_report_method(conn, report, TradeMethod.TREND)
    assert trading.set_report_order(
        conn, report, order_id=None, no_order_reason="아무것도 하지 않음", elapsed_order_s=12
    )
    (row,) = trading.list_judgment_rows(conn, PAPER)
    assert (row.report_id, row.round_number, row.market, row.state) == (
        report, 1, AAA, ReportState.RUNNING,
    )  # fmt: skip
    assert (row.method, row.order_id, row.no_order_reason) == (
        TradeMethod.TREND, None, "아무것도 하지 않음",
    )  # fmt: skip
    assert conn.execute("SELECT elapsed_order_s FROM reports").fetchone() == (12,)
    assert trading.list_judgment_rows(conn, PAPER, market=BBB) == ()
    assert trading.list_judgment_rows(conn, PAPER, start=T0 + timedelta(1)) == ()


def test_pauses_are_written_released_and_read(conn: sqlite3.Connection) -> None:
    user = trading.insert_pause(
        conn, mode=PAPER, kind=PauseKind.USER, cause=None, detail=None, sell_all=True,
        started_at=T0,
    )  # fmt: skip
    error = trading.insert_pause(
        conn, mode=PAPER, kind=PauseKind.ERROR, cause=ErrorPauseCause.EXCHANGE,
        detail="일시 차단", sell_all=False, started_at=T0 + timedelta(minutes=1),
    )  # fmt: skip
    assert [p.pause_id for p in trading.current_pauses(conn, PAPER)] == [user, error]
    assert trading.has_sold_all(conn, PAPER)
    assert not trading.has_sold_all(conn, Mode.LIVE)
    assert trading.release_pause(conn, user, released_at=T0 + timedelta(minutes=2))
    assert not trading.release_pause(conn, user, released_at=T0 + timedelta(minutes=3))
    (left,) = trading.current_pauses(conn, PAPER)
    assert (left.kind, left.cause, left.detail) == (
        PauseKind.ERROR, ErrorPauseCause.EXCHANGE, "일시 차단",
    )  # fmt: skip
    day = trading.list_pauses(conn, PAPER, start=T0, end=T0 + timedelta(days=1))
    assert [p.pause_id for p in day] == [error, user]
    assert day[1].released_utc == T0 + timedelta(minutes=2)


def test_setting_events_take_only_the_four_kinds(conn: sqlite3.Connection) -> None:
    assert trading.SETTING_EVENT_KINDS == (
        "coin_added", "coin_removed", "method_changed", "candle_count_changed",
    )  # fmt: skip
    trading.insert_setting_event(conn, mode=PAPER, kind="coin_added", market=AAA, value=None, at=T0)
    trading.insert_setting_event(
        conn, mode=PAPER, kind="method_changed", market=None, value="trend", at=T0
    )
    with pytest.raises(ValueError, match="종류"):
        trading.insert_setting_event(conn, mode=PAPER, kind="unit", market=None, value=None, at=T0)
    events = trading.list_setting_events(conn, PAPER)
    assert [(e.kind, e.market, e.value) for e in events] == [
        ("coin_added", AAA, None),
        ("method_changed", None, "trend"),
    ]
