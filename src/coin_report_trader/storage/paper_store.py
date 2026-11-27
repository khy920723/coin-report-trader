"""`PaperStore`를 갖춘 `storage`의 어댑터(M5 명세 3.4, 3.9).

`PaperExchange`(거래)가 쓰는 가상 계정, 걸린 주문, 체결의 읽고 쓰기를 `storage/trading.py`의 함수로
잇는다. 연결은 만든 쪽(자료 스레드)이 넘기고, 이 어댑터는 그 연결만 쓴다. 쓰기의 실패는
`StorageError`다. 새 주문의 `judged_until_utc`는 건 때를 올림한 분이다(M3 명세 3.7.2).
"""

import sqlite3
from datetime import datetime, timedelta

from coin_report_trader.core import (
    FillRecord,
    Mode,
    OrderPlan,
    OrderRecord,
    OrderState,
    PaperAccount,
)
from coin_report_trader.storage import trading as trade_store


def _ceil_minute(moment: datetime) -> datetime:
    floor = moment.replace(second=0, microsecond=0)
    return floor if floor == moment else floor + timedelta(minutes=1)


class SqlitePaperStore:
    """`core.ports.PaperStore`를 지킨다."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def read_account(self, mode: Mode) -> PaperAccount | None:
        return trade_store.read_paper_account(self._conn, mode)

    def write_account(self, mode: Mode, account: PaperAccount) -> None:
        trade_store.write_paper_account(self._conn, mode, account)

    def insert_order(
        self, plan: OrderPlan, *, mode: Mode, uid: str, round_id: int | None, created_utc: datetime
    ) -> OrderRecord:
        return trade_store.insert_order(
            self._conn,
            plan,
            mode=mode,
            uid=uid,
            round_id=round_id,
            created_at=created_utc,
            judged_until=_ceil_minute(created_utc),
        )

    def update_order(
        self,
        order_id: int,
        *,
        state: OrderState,
        closed_utc: datetime | None = None,
        error_name: str | None = None,
        sent: bool | None = None,
    ) -> None:
        trade_store.update_order(
            self._conn,
            order_id,
            state=state,
            closed_at=closed_utc,
            error_name=error_name,
            sent=sent,
        )

    def find_order(self, uid: str) -> OrderRecord | None:
        return trade_store.find_order(self._conn, uid)

    def open_orders(self, mode: Mode, market: str | None = None) -> tuple[OrderRecord, ...]:
        return trade_store.list_open_orders(self._conn, mode, market)

    def apply_fill(self, fill: FillRecord, account: PaperAccount) -> FillRecord:
        return trade_store.record_fill(self._conn, fill, account)

    def fills_of(self, order_id: int) -> tuple[FillRecord, ...]:
        return trade_store.fills_of(self._conn, order_id)

    def __repr__(self) -> str:
        return "SqlitePaperStore()"
