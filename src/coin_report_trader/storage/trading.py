"""3단계 표의 쓰기와 읽기(M3 명세 3.12): 가상 계정, 주문, 체결, 멈춤, 설정 바뀜, 회차와 보고서의
더한 열. 가격·수량·금액은 `Decimal`의 글, 시각은 UTC 글이다(M1 명세 3.7).

쓰기는 모두 하나의 트랜잭션이고 DB 오류는 `StorageError`다. 체결 하나는 `fills`의 줄, 그 주문의
`FILLED`와 닫은 때, 가상 계정을 한 트랜잭션에서 쓴다(3.7.2). 주문, 체결, 멈춤, 설정 바뀜을 지우는
함수는 없다(REQ-DATA-008). 이름은 명세가 정하지 않아 이 파일이 정했다.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Final

from coin_report_trader.core import (
    ErrorPauseCause,
    FillRecord,
    Mode,
    OrderKind,
    OrderPlan,
    OrderReason,
    OrderRecord,
    OrderSide,
    OrderState,
    PaperAccount,
    PaperHolding,
    PauseKind,
    ReportState,
    RoundStage,
    StorageError,
    TradeMethod,
)
from coin_report_trader.storage.database import transaction, translate_errors
from coin_report_trader.storage.times import format_utc, parse_utc

SETTING_COIN_ADDED: Final = "coin_added"
SETTING_COIN_REMOVED: Final = "coin_removed"
SETTING_METHOD_CHANGED: Final = "method_changed"
SETTING_CANDLE_COUNT_CHANGED: Final = "candle_count_changed"
SETTING_N_CHANGED: Final = "n_changed"
SETTING_EVENT_KINDS: Final = (
    SETTING_COIN_ADDED,
    SETTING_COIN_REMOVED,
    SETTING_METHOD_CHANGED,
    SETTING_CANDLE_COUNT_CHANGED,
    SETTING_N_CHANGED,
)
"""`setting_events.kind`의 값(3.16.2)."""
PAPER_TRADE_KEY: Final = "paper"
"""모의매매 체결의 `fills.trade_key`(M5 명세 3.9)."""
ROUND_DISCARDED: Final = "discarded"
"""멈춤으로 버린 회차의 `rounds.state`(3.5.3)."""

_ZERO: Final = Decimal(0)
_ORDER_COLUMNS: Final = (
    "id, uid, mode, round_id, market, side, kind, reason, price, qty, state, created_utc,"
    " closed_utc, error_name"
)
_FILL_COLUMNS: Final = "id, order_id, mode, market, side, price, qty, fee, realized_pnl, filled_utc"

_FILL_COLUMNS_JOINED: Final = ", ".join("fills." + c.strip() for c in _FILL_COLUMNS.split(","))


@dataclass(frozen=True)
class PauseRow:
    """표 `pauses`의 한 줄. `released_utc`가 `None`이면 지금의 멈춤이다."""

    pause_id: int
    mode: Mode
    kind: PauseKind
    cause: ErrorPauseCause | None
    detail: str | None
    sell_all: bool
    started_utc: datetime
    released_utc: datetime | None


@dataclass(frozen=True)
class LiveSnapshotRow:
    """표 `live_snapshots`의 한 줄: 마지막으로 읽은 실제 계정(M5 명세 3.9). `market`은 원화면
    `KRW`다."""

    market: str
    balance: Decimal
    locked: Decimal
    direct_locked: Decimal
    read_utc: datetime


@dataclass(frozen=True)
class ExternalChange:
    """표 `external_changes`의 한 줄: 외부 변동(M5 명세 3.9, 3.6.6)."""

    change_id: int
    mode: Mode
    market: str
    qty_delta: Decimal
    utc: datetime


@dataclass(frozen=True)
class SettingEvent:
    """표 `setting_events`의 한 줄(3.16.2)."""

    event_id: int
    mode: Mode
    kind: str
    market: str | None
    value: str | None
    utc: datetime


@dataclass(frozen=True)
class RoundRow:
    """기록 화면 회차 탭의 바탕(3.15.1)."""

    round_id: int
    number: int
    started_utc: datetime
    stage: RoundStage | None
    state: str
    close_utc: datetime | None = None
    ended_utc: datetime | None = None


@dataclass(frozen=True)
class JudgmentRow:
    """기록 화면 판단 탭과 모의 성적의 바탕(3.15.1, 3.16.1). 글은 저장된 그대로다(`OK`가 아닌
    보고서의 판단 칸에는 "보류" 같은 값이 있을 수 있어, 화면 값으로 바꾸는 것은 부르는 쪽이다)."""

    report_id: int
    round_number: int
    market: str
    close_utc: datetime
    unit: str
    state: ReportState
    failure: str | None
    base_price: str | None
    direction: str | None
    predicted_price: str | None
    buy_price: str | None
    sell_price: str | None
    method: TradeMethod | None
    order_id: int | None
    no_order_reason: str | None


# --- 안쪽 --------------------------------------------------------------------------------------


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise StorageError("저장된 수가 맞지 않습니다") from exc


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else _decimal(value)


def _optional_time(value: object) -> datetime | None:
    return None if value is None else parse_utc(str(value))


def _order(row: Sequence[object]) -> OrderRecord:
    (order_id, uid, mode, round_id, market, side, kind, reason, price, qty, state, created,
     closed, error_name) = row  # fmt: skip
    try:
        return OrderRecord(
            order_id=int(order_id),  # type: ignore[call-overload]
            uid=str(uid),
            mode=Mode(mode),
            round_id=None if round_id is None else int(round_id),  # type: ignore[call-overload]
            market=str(market),
            side=OrderSide(side),
            kind=OrderKind(kind),
            reason=OrderReason(reason),
            price=_optional_decimal(price),
            qty=_decimal(qty),
            state=OrderState(state),
            created_utc=parse_utc(str(created)),
            closed_utc=_optional_time(closed),
            error_name=None if error_name is None else str(error_name),
        )
    except ValueError as exc:
        raise StorageError("저장된 주문의 값이 맞지 않습니다") from exc


def _fill(row: Sequence[object]) -> FillRecord:
    fill_id, order_id, mode, market, side, price, qty, fee, pnl, filled = row
    try:
        return FillRecord(
            fill_id=int(fill_id),  # type: ignore[call-overload]
            order_id=int(order_id),  # type: ignore[call-overload]
            mode=Mode(mode),
            market=str(market),
            side=OrderSide(side),
            price=_decimal(price),
            qty=_decimal(qty),
            fee=_decimal(fee),
            realized_pnl=_optional_decimal(pnl),
            filled_utc=parse_utc(str(filled)),
        )
    except ValueError as exc:
        raise StorageError("저장된 체결의 값이 맞지 않습니다") from exc


def _holding(row: Sequence[object]) -> PaperHolding:
    market, qty, locked, amount_sum, qty_sum = row
    held = _decimal(qty)
    buy_amount, buy_qty = _decimal(amount_sum), _decimal(qty_sum)
    average = buy_amount / buy_qty if held > 0 and buy_qty > 0 else None
    return PaperHolding(
        market=str(market),
        qty=held,
        locked_qty=_decimal(locked),
        avg_price=average,
        buy_amount_sum=buy_amount,
        buy_qty_sum=buy_qty,
    )


def _write_account(conn: sqlite3.Connection, mode: Mode, account: PaperAccount) -> None:
    cursor = conn.execute(
        "UPDATE paper_accounts SET krw = ?, locked_krw = ? WHERE mode = ?",
        (str(account.krw), str(account.locked_krw), Mode(mode).value),
    )
    if cursor.rowcount == 0:
        raise StorageError("가상 계정이 없습니다")
    for holding in account.holdings:
        values = (
            str(holding.qty),
            str(holding.locked_qty),
            str(holding.buy_amount_sum),
            str(holding.buy_qty_sum),
        )
        conn.execute(
            "INSERT INTO paper_holdings (mode, market, qty, locked_qty, buy_amount_sum,"
            " buy_qty_sum) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (mode, market) DO UPDATE SET"
            " qty = excluded.qty, locked_qty = excluded.locked_qty,"
            " buy_amount_sum = excluded.buy_amount_sum, buy_qty_sum = excluded.buy_qty_sum",
            (Mode(mode).value, holding.market, *values),
        )


# --- 가상 계정 ----------------------------------------------------------------------------------


def create_paper_account(
    conn: sqlite3.Connection, mode: Mode, funds: Decimal, *, created_at: datetime
) -> bool:
    """가상 계정을 만든다(가상 자금 = 가상 원화, 보유 없음. 3.7.1). 이미 있으면 그대로 두고 거짓."""
    with translate_errors("가상 계정 저장"), transaction(conn):
        cursor = conn.execute(
            "INSERT INTO paper_accounts (mode, funds, krw, locked_krw, created_utc)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT (mode) DO NOTHING",
            (Mode(mode).value, str(funds), str(funds), "0", format_utc(created_at)),
        )
        return cursor.rowcount > 0


def read_paper_funds(conn: sqlite3.Connection, mode: Mode) -> Decimal | None:
    """처음 가상 자금. 계정이 없으면 `None`."""
    with translate_errors("가상 계정 읽기"):
        row = conn.execute(
            "SELECT funds FROM paper_accounts WHERE mode = ?", (Mode(mode).value,)
        ).fetchone()
    return None if row is None else _decimal(row[0])


def read_paper_account(conn: sqlite3.Connection, mode: Mode) -> PaperAccount | None:
    """가상 계정과 보유(줄이 있는 코인 모두. 보유가 0이어도 줄은 남는다). 없으면 `None`.
    평균 매수가는 보유가 0이 된 뒤의 매수 합으로 셈한다(`buy_amount_sum ÷ buy_qty_sum`)."""
    with translate_errors("가상 계정 읽기"):
        row = conn.execute(
            "SELECT krw, locked_krw FROM paper_accounts WHERE mode = ?", (Mode(mode).value,)
        ).fetchone()
        if row is None:
            return None
        holdings = conn.execute(
            "SELECT market, qty, locked_qty, buy_amount_sum, buy_qty_sum FROM paper_holdings"
            " WHERE mode = ? ORDER BY market",
            (Mode(mode).value,),
        ).fetchall()
    return PaperAccount(
        krw=_decimal(row[0]),
        locked_krw=_decimal(row[1]),
        holdings=tuple(_holding(item) for item in holdings),
    )


def write_paper_account(conn: sqlite3.Connection, mode: Mode, account: PaperAccount) -> None:
    """가상 원화, 묶인 원화, 보유(수량, 묶인 수량, 매수 합)를 쓴다. 보유가 0이어도 줄을 지우지
    않는다(3.12). 계정이 없으면 `StorageError`."""
    with translate_errors("가상 계정 저장"), transaction(conn):
        _write_account(conn, mode, account)


# --- 주문 ---------------------------------------------------------------------------------------


def insert_order(
    conn: sqlite3.Connection,
    plan: OrderPlan,
    *,
    mode: Mode,
    uid: str,
    round_id: int | None,
    created_at: datetime,
    judged_until: datetime,
) -> OrderRecord:
    """주문을 `OPEN`, `sent` 0으로 쓰고 그 줄을 돌려준다(3.8.3의 1). 식별값이 겹치면
    `StorageError`다."""
    values = (
        uid,
        Mode(mode).value,
        round_id,
        plan.market,
        OrderSide(plan.side).value,
        OrderKind(plan.kind).value,
        OrderReason(plan.reason).value,
        _text(plan.price),
        str(plan.qty),
        OrderState.OPEN.value,
        format_utc(created_at),
        format_utc(judged_until),
    )
    with translate_errors("주문 저장"), transaction(conn):
        cursor = conn.execute(
            "INSERT INTO orders (uid, mode, round_id, market, side, kind, reason, price, qty,"
            " state, sent, created_utc, judged_until_utc)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
            values,
        )
        order_id = cursor.lastrowid
    if order_id is None:
        raise StorageError("주문 번호를 받지 못했습니다")
    return OrderRecord(
        order_id=order_id,
        uid=uid,
        mode=Mode(mode),
        round_id=round_id,
        market=plan.market,
        side=OrderSide(plan.side),
        kind=OrderKind(plan.kind),
        reason=OrderReason(plan.reason),
        price=plan.price,
        qty=plan.qty,
        state=OrderState.OPEN,
        created_utc=created_at,
        closed_utc=None,
        error_name=None,
    )


def update_order(
    conn: sqlite3.Connection,
    order_id: int,
    *,
    state: OrderState,
    closed_at: datetime | None = None,
    error_name: str | None = None,
    sent: bool | None = None,
    canceled_by_round: bool | None = None,
) -> bool:
    """주문의 상태와 닫은 때, 오류 이름을 고쳐 쓴다. `sent`와 `canceled_by_round`는 `None`이면
    그대로 둔다. 그 줄이 있었으면 참이다."""
    sets = ["state = ?", "closed_utc = ?", "error_name = ?"]
    values: list[object] = [
        OrderState(state).value,
        None if closed_at is None else format_utc(closed_at),
        error_name,
    ]
    if sent is not None:
        sets.append("sent = ?")
        values.append(1 if sent else 0)
    if canceled_by_round is not None:
        sets.append("canceled_by_round = ?")
        values.append(1 if canceled_by_round else 0)
    with translate_errors("주문 저장"), transaction(conn):
        cursor = conn.execute(
            f"UPDATE orders SET {', '.join(sets)} WHERE id = ?", (*values, order_id)
        )
        return cursor.rowcount > 0


def update_order_exec(
    conn: sqlite3.Connection,
    order_id: int,
    *,
    executed_qty: Decimal | None = None,
    avg_price_at_send: Decimal | None = None,
) -> bool:
    """실거래의 체결 수량과 매도 주문을 낼 때의 거래소 평균 매수가(M5 명세 3.9, 3.6.4)를 쓴다.
    `None`인 값은 그대로 둔다. 둘 다 `None`이면 아무것도 쓰지 않고 줄이 있는지만 본다."""
    sets: list[str] = []
    values: list[object] = []
    if executed_qty is not None:
        sets.append("executed_qty = ?")
        values.append(str(executed_qty))
    if avg_price_at_send is not None:
        sets.append("avg_price_at_send = ?")
        values.append(str(avg_price_at_send))
    with translate_errors("주문 저장"), transaction(conn):
        if not sets:
            row = conn.execute("SELECT 1 FROM orders WHERE id = ?", (order_id,)).fetchone()
            return row is not None
        cursor = conn.execute(
            f"UPDATE orders SET {', '.join(sets)} WHERE id = ?", (*values, order_id)
        )
        return cursor.rowcount > 0


def read_order_exec(
    conn: sqlite3.Connection, order_id: int
) -> tuple[Decimal | None, Decimal | None] | None:
    """(`executed_qty`, `avg_price_at_send`). 그 주문이 없으면 `None`."""
    with translate_errors("주문 읽기"):
        row = conn.execute(
            "SELECT executed_qty, avg_price_at_send FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
    if row is None:
        return None
    return _optional_decimal(row[0]), _optional_decimal(row[1])


def set_judged_until(conn: sqlite3.Connection, order_id: int, until: datetime) -> bool:
    """모의 체결 판정에서 마지막으로 본 1분봉의 끝(3.7.2)."""
    with translate_errors("주문 저장"), transaction(conn):
        cursor = conn.execute(
            "UPDATE orders SET judged_until_utc = ? WHERE id = ?", (format_utc(until), order_id)
        )
        return cursor.rowcount > 0


def read_judged_until(conn: sqlite3.Connection, order_id: int) -> datetime | None:
    with translate_errors("주문 읽기"):
        row = conn.execute(
            "SELECT judged_until_utc FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
    return None if row is None else parse_utc(str(row[0]))


def read_order(conn: sqlite3.Connection, order_id: int) -> OrderRecord | None:
    with translate_errors("주문 읽기"):
        row = conn.execute(f"SELECT {_ORDER_COLUMNS} FROM orders WHERE id = ?", (order_id,))
        found = row.fetchone()
    return None if found is None else _order(found)


def find_order(conn: sqlite3.Connection, uid: str) -> OrderRecord | None:
    """식별값으로 찾는다."""
    with translate_errors("주문 읽기"):
        found = conn.execute(
            f"SELECT {_ORDER_COLUMNS} FROM orders WHERE uid = ?", (uid,)
        ).fetchone()
    return None if found is None else _order(found)


def list_open_orders(
    conn: sqlite3.Connection, mode: Mode, market: str | None = None
) -> tuple[OrderRecord, ...]:
    """걸린(`OPEN`) 주문. 오래된 것이 앞이다. `market`이 없으면 모든 코인."""
    query = f"SELECT {_ORDER_COLUMNS} FROM orders WHERE mode = ? AND state = ?"
    values: list[object] = [Mode(mode).value, OrderState.OPEN.value]
    if market is not None:
        query += " AND market = ?"
        values.append(market)
    with translate_errors("주문 읽기"):
        rows = conn.execute(query + " ORDER BY id", values).fetchall()
    return tuple(_order(row) for row in rows)


def list_unsent_orders(conn: sqlite3.Connection, mode: Mode) -> tuple[OrderRecord, ...]:
    """`sent` 0이고 닫히지 않은 주문(켤 때 `NOT_SENT`로 닫는다. 3.8.3)."""
    with translate_errors("주문 읽기"):
        rows = conn.execute(
            f"SELECT {_ORDER_COLUMNS} FROM orders WHERE mode = ? AND sent = 0 AND state IN (?, ?)"
            " ORDER BY id",
            (Mode(mode).value, OrderState.OPEN.value, OrderState.UNCONFIRMED.value),
        ).fetchall()
    return tuple(_order(row) for row in rows)


def list_orders(
    conn: sqlite3.Connection,
    mode: Mode,
    *,
    start: datetime,
    end: datetime,
    market: str | None = None,
) -> tuple[OrderRecord, ...]:
    """`created_utc`가 `start` 이상 `end` 미만인 주문. 새것이 앞이다(기록 화면. 3.15.1)."""
    query = (
        f"SELECT {_ORDER_COLUMNS} FROM orders WHERE mode = ? AND created_utc >= ?"
        " AND created_utc < ?"
    )
    values: list[object] = [Mode(mode).value, format_utc(start), format_utc(end)]
    if market is not None:
        query += " AND market = ?"
        values.append(market)
    with translate_errors("주문 읽기"):
        rows = conn.execute(query + " ORDER BY created_utc DESC, id DESC", values).fetchall()
    return tuple(_order(row) for row in rows)


def count_canceled_judgment_orders(
    conn: sqlite3.Connection, mode: Mode, market: str, *, start: datetime, end: datetime
) -> int:
    """그날(`start` 이상 `end` 미만에 만든) 그 코인의 판단 주문 가운데 `CANCELED`의 수(3.8.2의
    걸었다 지우기. 취소한 날이 아니라 만든 날로 센다)."""
    with translate_errors("주문 읽기"):
        (count,) = conn.execute(
            "SELECT count(*) FROM orders WHERE mode = ? AND market = ? AND reason = ?"
            " AND state = ? AND created_utc >= ? AND created_utc < ?",
            (
                Mode(mode).value,
                market,
                OrderReason.JUDGMENT.value,
                OrderState.CANCELED.value,
                format_utc(start),
                format_utc(end),
            ),
        ).fetchone()
    return int(count)


def last_order_time(conn: sqlite3.Connection, mode: Mode, market: str) -> datetime | None:
    """그 코인의 마지막 프로그램 주문을 저장한 때(날짜와 상관없이 가장 늦은 것. 3.8.2)."""
    with translate_errors("주문 읽기"):
        (latest,) = conn.execute(
            "SELECT max(created_utc) FROM orders WHERE mode = ? AND market = ?",
            (Mode(mode).value, market),
        ).fetchone()
    return _optional_time(latest)


# --- 체결 ---------------------------------------------------------------------------------------


def record_fill(
    conn: sqlite3.Connection,
    fill: FillRecord,
    account: PaperAccount,
    *,
    trade_key: str = PAPER_TRADE_KEY,
) -> FillRecord:
    """체결 한 줄, 그 주문의 `FILLED`와 닫은 때(체결 때), 가상 계정을 한 트랜잭션에서 쓴다(3.7.2).
    `fill.fill_id`는 보지 않고 새 번호가 든 줄을 돌려준다. 같은 주문의 같은 체결(`trade_key`)을
    두 번
    쓰면 `StorageError`다(`UNIQUE (order_id, trade_key)`. 모의매매는 `"paper"` 하나라 주문마다
    한 번이다. 실거래는 거래소의 체결 식별값이 `trade_key`다. M5 명세 3.9)."""
    values = (
        fill.order_id,
        Mode(fill.mode).value,
        fill.market,
        OrderSide(fill.side).value,
        str(fill.price),
        str(fill.qty),
        str(fill.fee),
        _text(fill.realized_pnl),
        format_utc(fill.filled_utc),
        trade_key,
    )
    with translate_errors("주문 저장"), transaction(conn):
        cursor = conn.execute(
            "INSERT INTO fills (order_id, mode, market, side, price, qty, fee, realized_pnl,"
            " filled_utc, trade_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        fill_id = cursor.lastrowid
        updated = conn.execute(
            "UPDATE orders SET state = ?, closed_utc = ? WHERE id = ?",
            (OrderState.FILLED.value, format_utc(fill.filled_utc), fill.order_id),
        )
        if updated.rowcount == 0:
            raise StorageError("체결의 주문이 없습니다")
        _write_account(conn, fill.mode, account)
    if fill_id is None:
        raise StorageError("체결 번호를 받지 못했습니다")
    return FillRecord(
        fill_id=fill_id,
        order_id=fill.order_id,
        mode=fill.mode,
        market=fill.market,
        side=fill.side,
        price=fill.price,
        qty=fill.qty,
        fee=fill.fee,
        realized_pnl=fill.realized_pnl,
        filled_utc=fill.filled_utc,
    )


def fills_of(conn: sqlite3.Connection, order_id: int) -> tuple[FillRecord, ...]:
    with translate_errors("체결 읽기"):
        rows = conn.execute(
            f"SELECT {_FILL_COLUMNS} FROM fills WHERE order_id = ? ORDER BY id", (order_id,)
        ).fetchall()
    return tuple(_fill(row) for row in rows)


def list_fills(
    conn: sqlite3.Connection,
    mode: Mode,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    market: str | None = None,
) -> tuple[FillRecord, ...]:
    """체결. `start` 이상 `end` 미만(없으면 끝이 없다). 새것이 앞이다(기록 화면과 성적. 3.15,
    3.16)."""
    query = f"SELECT {_FILL_COLUMNS} FROM fills WHERE mode = ?"
    values: list[object] = [Mode(mode).value]
    if start is not None:
        query += " AND filled_utc >= ?"
        values.append(format_utc(start))
    if end is not None:
        query += " AND filled_utc < ?"
        values.append(format_utc(end))
    if market is not None:
        query += " AND market = ?"
        values.append(market)
    with translate_errors("체결 읽기"):
        rows = conn.execute(query + " ORDER BY filled_utc DESC, id DESC", values).fetchall()
    return tuple(_fill(row) for row in rows)


def realized_pnl_between(
    conn: sqlite3.Connection, mode: Mode, *, start: datetime, end: datetime
) -> Decimal:
    """그날(`start` 이상 `end` 미만) 프로그램이 판 체결의 실현 손익 합(3.8.2의 하루 손실 한도)."""
    total = _ZERO
    for fill in list_fills(conn, mode, start=start, end=end):
        if fill.side is OrderSide.SELL and fill.realized_pnl is not None:
            total += fill.realized_pnl
    return total


def last_stop_loss_fill(conn: sqlite3.Connection, mode: Mode, market: str) -> FillRecord | None:
    """그 코인의 마지막 손절 체결(그 주문의 `reason`이 `stop_loss`. 3.5.1)."""
    with translate_errors("체결 읽기"):
        row = conn.execute(
            f"SELECT {_FILL_COLUMNS_JOINED} FROM fills JOIN orders ON orders.id = fills.order_id"
            " WHERE fills.mode = ? AND fills.market = ? AND orders.reason = ?"
            " ORDER BY fills.filled_utc DESC, fills.id DESC LIMIT 1",
            (Mode(mode).value, market, OrderReason.STOP_LOSS.value),
        ).fetchone()
    return None if row is None else _fill(row)


# --- 회차와 보고서의 더한 열 ----------------------------------------------------------------------


def set_round_stage(conn: sqlite3.Connection, round_id: int, stage: RoundStage) -> bool:
    """`rounds.stage`(3.5.1)."""
    with translate_errors("회차 저장"), transaction(conn):
        cursor = conn.execute(
            "UPDATE rounds SET stage = ? WHERE id = ?", (RoundStage(stage).value, round_id)
        )
        return cursor.rowcount > 0


def discard_round(conn: sqlite3.Connection, round_id: int, *, ended_at: datetime) -> int:
    """멈춤으로 버린 회차(3.5.3): 회차를 `discarded`로, 그 회차의 `RUNNING` 보고서를 `DISCARDED`로
    고쳐 쓴다. 고친 보고서의 수를 돌려준다."""
    moment = format_utc(ended_at)
    with translate_errors("회차 저장"), transaction(conn):
        reports = conn.execute(
            "UPDATE reports SET state = ?, finished_utc = ? WHERE round_id = ? AND state = ?",
            (ReportState.DISCARDED.value, moment, round_id, ReportState.RUNNING.value),
        ).rowcount
        conn.execute(
            "UPDATE rounds SET state = ?, ended_utc = ? WHERE id = ?",
            (ROUND_DISCARDED, moment, round_id),
        )
    return reports


def round_running_at(conn: sqlite3.Connection, mode: Mode, moment: datetime) -> bool:
    """그때 도는 회차가 있었는지(어느 회차의 `started_utc` 이상 `ended_utc` 미만. 끝나지 않은 회차는
    끝이 없다. 3.5.1의 회차 밖의 손절)."""
    text = format_utc(moment)
    with translate_errors("회차 읽기"):
        row = conn.execute(
            "SELECT 1 FROM rounds WHERE mode = ? AND started_utc <= ?"
            " AND (ended_utc IS NULL OR ended_utc > ?) LIMIT 1",
            (Mode(mode).value, text, text),
        ).fetchone()
    return row is not None


def first_round_after(conn: sqlite3.Connection, mode: Mode, moment: datetime) -> int | None:
    """`started_utc`가 그때 뒤인 가장 이른 회차의 `id`(3.5.1)."""
    with translate_errors("회차 읽기"):
        row = conn.execute(
            "SELECT id FROM rounds WHERE mode = ? AND started_utc > ?"
            " ORDER BY started_utc, id LIMIT 1",
            (Mode(mode).value, format_utc(moment)),
        ).fetchone()
    return None if row is None else int(row[0])


def set_report_method(conn: sqlite3.Connection, report_id: int, method: TradeMethod) -> bool:
    """그 회차의 매매 방식(`reports.method`. 3.16.2)."""
    with translate_errors("보고서 저장"), transaction(conn):
        cursor = conn.execute(
            "UPDATE reports SET method = ? WHERE id = ?", (TradeMethod(method).value, report_id)
        )
        return cursor.rowcount > 0


def set_report_order(
    conn: sqlite3.Connection,
    report_id: int,
    *,
    order_id: int | None,
    no_order_reason: str | None,
    elapsed_order_s: int,
) -> bool:
    """낸 주문이나 주문하지 않은 까닭과 주문까지 걸린 시간(3.5.1의 6의 3)."""
    with translate_errors("보고서 저장"), transaction(conn):
        cursor = conn.execute(
            "UPDATE reports SET order_id = ?, no_order_reason = ?, elapsed_order_s = ?"
            " WHERE id = ?",
            (order_id, no_order_reason, elapsed_order_s, report_id),
        )
        return cursor.rowcount > 0


def list_round_rows(
    conn: sqlite3.Connection, mode: Mode, *, start: datetime, end: datetime
) -> tuple[RoundRow, ...]:
    """`started_utc`가 `start` 이상 `end` 미만인 회차. 새것이 앞이다(3.15.1의 회차 탭)."""
    with translate_errors("회차 읽기"):
        rows = conn.execute(
            "SELECT id, number, started_utc, stage, state, close_utc, ended_utc FROM rounds"
            " WHERE mode = ? AND started_utc >= ? AND started_utc < ?"
            " ORDER BY started_utc DESC, id DESC",
            (Mode(mode).value, format_utc(start), format_utc(end)),
        ).fetchall()
    result: list[RoundRow] = []
    for round_id, number, started, stage, state, close, ended in rows:
        try:
            parsed_stage = None if stage is None else RoundStage(stage)
        except ValueError:
            parsed_stage = None
        result.append(
            RoundRow(
                int(round_id),
                int(number),
                parse_utc(str(started)),
                parsed_stage,
                str(state),
                None if close is None else parse_utc(str(close)),
                None if ended is None else parse_utc(str(ended)),
            )
        )
    return tuple(result)


def list_judgment_rows(
    conn: sqlite3.Connection,
    mode: Mode,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    market: str | None = None,
) -> tuple[JudgmentRow, ...]:
    """보고서마다의 판단 줄. 회차의 `started_utc`가 `start` 이상 `end` 미만(없으면 끝이 없다).
    새것이 앞이다(3.15.1의 판단 탭, 3.16의 성적)."""
    query = (
        "SELECT reports.id, rounds.number, reports.market, rounds.close_utc, rounds.unit,"
        " reports.state, reports.failure, reports.base_price, reports.direction,"
        " reports.predicted_price, reports.buy_price, reports.sell_price, reports.method,"
        " reports.order_id, reports.no_order_reason FROM reports"
        " JOIN rounds ON rounds.id = reports.round_id WHERE reports.mode = ?"
    )
    values: list[object] = [Mode(mode).value]
    if start is not None:
        query += " AND rounds.started_utc >= ?"
        values.append(format_utc(start))
    if end is not None:
        query += " AND rounds.started_utc < ?"
        values.append(format_utc(end))
    if market is not None:
        query += " AND reports.market = ?"
        values.append(market)
    with translate_errors("보고서 읽기"):
        rows = conn.execute(
            query + " ORDER BY rounds.started_utc DESC, reports.id DESC", values
        ).fetchall()
    result: list[JudgmentRow] = []
    for row in rows:
        (report_id, number, coin, close, unit, state, failure, base, direction, predicted, buy,
         sell, method, order_id, reason) = row  # fmt: skip
        try:
            parsed_state = ReportState(state)
            parsed_method = None if method is None else TradeMethod(method)
        except ValueError as exc:
            raise StorageError("저장된 보고서의 값이 맞지 않습니다") from exc
        result.append(
            JudgmentRow(
                report_id=int(report_id),
                round_number=int(number),
                market=str(coin),
                close_utc=parse_utc(str(close)),
                unit=str(unit),
                state=parsed_state,
                failure=None if failure is None else str(failure),
                base_price=None if base is None else str(base),
                direction=None if direction is None else str(direction),
                predicted_price=None if predicted is None else str(predicted),
                buy_price=None if buy is None else str(buy),
                sell_price=None if sell is None else str(sell),
                method=parsed_method,
                order_id=None if order_id is None else int(order_id),
                no_order_reason=None if reason is None else str(reason),
            )
        )
    return tuple(result)


# --- 멈춤 ---------------------------------------------------------------------------------------


def insert_pause(
    conn: sqlite3.Connection,
    *,
    mode: Mode,
    kind: PauseKind,
    cause: ErrorPauseCause | None,
    detail: str | None,
    sell_all: bool,
    started_at: datetime,
) -> int:
    """멈춤 한 줄을 풀리지 않은 채로 쓰고 새 `id`를 돌려준다(3.10)."""
    with translate_errors("멈춤 저장"), transaction(conn):
        cursor = conn.execute(
            "INSERT INTO pauses (mode, kind, cause, detail, sell_all, started_utc)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                Mode(mode).value,
                PauseKind(kind).value,
                None if cause is None else ErrorPauseCause(cause).value,
                detail,
                1 if sell_all else 0,
                format_utc(started_at),
            ),
        )
        pause_id = cursor.lastrowid
    if pause_id is None:
        raise StorageError("멈춤 번호를 받지 못했습니다")
    return pause_id


def release_pause(conn: sqlite3.Connection, pause_id: int, *, released_at: datetime) -> bool:
    """멈춤을 푼다. 이미 풀렸거나 없으면 거짓."""
    with translate_errors("멈춤 저장"), transaction(conn):
        cursor = conn.execute(
            "UPDATE pauses SET released_utc = ? WHERE id = ? AND released_utc IS NULL",
            (format_utc(released_at), pause_id),
        )
        return cursor.rowcount > 0


def mark_sell_all(conn: sqlite3.Connection, pause_id: int) -> bool:
    """풀리지 않은 멈춤에 '멈추고 전량 매도'를 눌렀다고 남긴다(`sell_all` 1. 3.10.3)."""
    with translate_errors("멈춤 저장"), transaction(conn):
        cursor = conn.execute(
            "UPDATE pauses SET sell_all = 1 WHERE id = ? AND released_utc IS NULL", (pause_id,)
        )
        return cursor.rowcount > 0


def _pause(row: Sequence[object]) -> PauseRow:
    pause_id, mode, kind, cause, detail, sell_all, started, released = row
    try:
        return PauseRow(
            pause_id=int(pause_id),  # type: ignore[call-overload]
            mode=Mode(mode),
            kind=PauseKind(kind),
            cause=None if cause is None else ErrorPauseCause(cause),
            detail=None if detail is None else str(detail),
            sell_all=bool(sell_all),
            started_utc=parse_utc(str(started)),
            released_utc=_optional_time(released),
        )
    except ValueError as exc:
        raise StorageError("저장된 멈춤의 값이 맞지 않습니다") from exc


_PAUSE_COLUMNS: Final = "id, mode, kind, cause, detail, sell_all, started_utc, released_utc"


def current_pauses(conn: sqlite3.Connection, mode: Mode) -> tuple[PauseRow, ...]:
    """풀리지 않은 멈춤(지금의 멈춤). 오래된 것이 앞이다."""
    with translate_errors("멈춤 읽기"):
        rows = conn.execute(
            f"SELECT {_PAUSE_COLUMNS} FROM pauses WHERE mode = ? AND released_utc IS NULL"
            " ORDER BY id",
            (Mode(mode).value,),
        ).fetchall()
    return tuple(_pause(row) for row in rows)


def list_pauses(
    conn: sqlite3.Connection, mode: Mode, *, start: datetime, end: datetime
) -> tuple[PauseRow, ...]:
    """`started_utc`가 `start` 이상 `end` 미만인 멈춤. 새것이 앞이다(3.15.1의 멈춤 탭)."""
    with translate_errors("멈춤 읽기"):
        rows = conn.execute(
            f"SELECT {_PAUSE_COLUMNS} FROM pauses WHERE mode = ? AND started_utc >= ?"
            " AND started_utc < ? ORDER BY started_utc DESC, id DESC",
            (Mode(mode).value, format_utc(start), format_utc(end)),
        ).fetchall()
    return tuple(_pause(row) for row in rows)


def has_sold_all(conn: sqlite3.Connection, mode: Mode) -> bool:
    """그 모드에서 '멈추고 전량 매도'를 한 번이라도 했는지(`sell_all` 1. REQ-LIVE-014)."""
    with translate_errors("멈춤 읽기"):
        row = conn.execute(
            "SELECT 1 FROM pauses WHERE mode = ? AND sell_all = 1 LIMIT 1", (Mode(mode).value,)
        ).fetchone()
    return row is not None


# --- 실제 계정의 마지막 읽기와 외부 변동(M5 명세 3.9) --------------------------------------------


def replace_live_snapshots(conn: sqlite3.Connection, rows: Sequence[LiveSnapshotRow]) -> None:
    """마지막으로 읽은 실제 계정으로 `live_snapshots` 전체를 바꾼다(한 트랜잭션)."""
    with translate_errors("계정 저장"), transaction(conn):
        conn.execute("DELETE FROM live_snapshots")
        for row in rows:
            conn.execute(
                "INSERT INTO live_snapshots (market, balance, locked, direct_locked, read_utc)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    row.market,
                    str(row.balance),
                    str(row.locked),
                    str(row.direct_locked),
                    format_utc(row.read_utc),
                ),
            )


def read_live_snapshots(conn: sqlite3.Connection) -> tuple[LiveSnapshotRow, ...]:
    """마지막으로 읽은 실제 계정. 마켓 차례다."""
    with translate_errors("계정 읽기"):
        rows = conn.execute(
            "SELECT market, balance, locked, direct_locked, read_utc FROM live_snapshots"
            " ORDER BY market"
        ).fetchall()
    return tuple(
        LiveSnapshotRow(
            str(market), _decimal(balance), _decimal(locked), _decimal(direct), parse_utc(str(read))
        )
        for market, balance, locked, direct, read in rows
    )


def insert_external_change(
    conn: sqlite3.Connection, *, mode: Mode, market: str, qty_delta: Decimal, at: datetime
) -> int:
    """외부 변동 한 줄을 쓰고 새 `id`를 돌려준다. 지우는 함수는 없다."""
    with translate_errors("외부 변동 저장"), transaction(conn):
        cursor = conn.execute(
            "INSERT INTO external_changes (mode, market, qty_delta, utc) VALUES (?, ?, ?, ?)",
            (Mode(mode).value, market, str(qty_delta), format_utc(at)),
        )
        change_id = cursor.lastrowid
    if change_id is None:
        raise StorageError("외부 변동 번호를 받지 못했습니다")
    return change_id


def list_external_changes(
    conn: sqlite3.Connection,
    mode: Mode,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    market: str | None = None,
) -> tuple[ExternalChange, ...]:
    """`utc`가 `start` 이상 `end` 미만(없으면 끝이 없다)인 외부 변동. 새것이 앞이다(기록 화면의
    외부 변동 탭)."""
    query = "SELECT id, mode, market, qty_delta, utc FROM external_changes WHERE mode = ?"
    values: list[object] = [Mode(mode).value]
    if start is not None:
        query += " AND utc >= ?"
        values.append(format_utc(start))
    if end is not None:
        query += " AND utc < ?"
        values.append(format_utc(end))
    if market is not None:
        query += " AND market = ?"
        values.append(market)
    with translate_errors("외부 변동 읽기"):
        rows = conn.execute(query + " ORDER BY utc DESC, id DESC", values).fetchall()
    return tuple(
        ExternalChange(int(i), Mode(m), str(mk), _decimal(delta), parse_utc(str(utc)))
        for i, m, mk, delta, utc in rows
    )


# --- 설정 바뀜 -----------------------------------------------------------------------------------


def insert_setting_event(
    conn: sqlite3.Connection,
    *,
    mode: Mode,
    kind: str,
    market: str | None,
    value: str | None,
    at: datetime,
) -> int:
    """설정 바뀜 한 줄(3.16.2). `kind`는 `SETTING_EVENT_KINDS`의 하나다(아니면 `ValueError`)."""
    if kind not in SETTING_EVENT_KINDS:
        raise ValueError(f"설정 바뀜의 종류가 아니다: {kind}")
    with translate_errors("설정 저장"), transaction(conn):
        cursor = conn.execute(
            "INSERT INTO setting_events (mode, kind, market, value, utc) VALUES (?, ?, ?, ?, ?)",
            (Mode(mode).value, kind, market, value, format_utc(at)),
        )
        event_id = cursor.lastrowid
    if event_id is None:
        raise StorageError("설정 바뀜 번호를 받지 못했습니다")
    return event_id


def list_setting_events(conn: sqlite3.Connection, mode: Mode) -> tuple[SettingEvent, ...]:
    """그 모드의 설정 바뀜. 오래된 것이 앞이다."""
    with translate_errors("설정 읽기"):
        rows = conn.execute(
            "SELECT id, mode, kind, market, value, utc FROM setting_events WHERE mode = ?"
            " ORDER BY utc, id",
            (Mode(mode).value,),
        ).fetchall()
    return tuple(
        SettingEvent(
            int(event_id),
            Mode(row_mode),
            str(kind),
            None if market is None else str(market),
            None if value is None else str(value),
            parse_utc(str(utc)),
        )
        for event_id, row_mode, kind, market, value, utc in rows
    )
