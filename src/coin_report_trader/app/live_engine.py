"""5단계 잇기 2: 실거래의 거래소 경계 부르기(M5 명세 3.6, 3.5.5. 15번).

자료 스레드(`DataWorker`)가 상속한다. 이 모듈의 메서드는 모두 자료 스레드에서만 부른다. 실거래에서
3단계의 잇기(`trade_round`, `pause_flow`)가 엔진을 부르던 자리(회차의 주문, 1분 점검의 체결 판정과
손절, 멈춤과 전량 매도의 취소와 매도, 대조, 코인 빼기)는 여기의 메서드로 `ExchangePort`를 부른다.
계산(대조, 외부 변동, A-6·A-7, 체결과 손익, 원화 잔고)은 `trading/live/`의 부품을 부르기만 하고,
DB는 `storage/`의 함수로만 만진다.

- 주문(3.6.2): 식별값과 주문 값을 `orders`에 `OPEN`, `sent` 0으로 쓰고(매도면 그때의 거래소 평균
  매수가를 `avg_price_at_send`에) → `place` → `sent` 1과 상태. 결과를 모르면(`PlaceResult.unknown`)
  다시 보내지 않고 `UNCONFIRMED`다(3.6.3). 거절이면 그 주문만 `FAILED`와 오류 이름이다(3.5.5).
- 조회(3.6.3, 3.6.4): 걸린 주문과 확인 안 됨 주문마다 `query`. "없음"이 600초 동안 이어지면
  `NOT_SENT`. 아직 쓰지 않은 체결(`trade_key`)마다 `fills`에 한 줄, 체결 수량은 `executed_qty`.
- 취소(3.6.4): 취소하기 전에 조회해 체결을 쓰고, 끝났으면 취소하지 않는다. 취소의 결과가 아직
  걸림이면 취소가 확인되지 않은 주문이다(`_cancel_pending`).
- 매매 엔진의 계정 읽기(3.6.1, 3.6.6): `read_account` 뒤에 외부 변동(`external_changes`, A-22)과
  직접 건 주문에 묶인 몫(A-6)을 셈하고 `live_snapshots`를 바꾼다. 계정 화면의 읽기는 여기에 들지
  않는다(3.15.2).
- 대조(3.6.5): 고른 코인마다 체결 대기 주문 목록 → 저장한 걸림·확인 안 됨 주문마다 조회 → 계정 읽기.
  목록에만 있는 주문은 사용자가 직접 건 주문이고 취소하지 않는다. 앞 목록의 직접 건 주문이 사라졌고
  그 사이 그 코인의 프로그램 주문이 반대편으로 체결되었으면 A-7이다.
- 오류(3.5.5): 거래를 계속 막는 오류는 오류 멈춤과 A-23(일시 차단은 `_set_block`), 목록에 없는
  오류는 정해진 알림이 없는 일이면 A-24, 잠깐의 문제는 그 일만 실패다.

실거래의 체결 쓰기: 체결은 `_live_fill_store`(`LiveFillStore`)로 쓴다. 실제 프로그램의 것은
`StoredLiveFills`이고 `storage/trading.py`의 `record_live_fill`(체결 한 줄만. 가상 계정과 주문의
상태는 쓰지 않는다)과 `fill_trade_keys`(그 주문의 쓴 `trade_key`)를 부른다. 쓰지 못하면
(`StorageError`) A-29("주문 저장")와 오류 멈춤이다(3.6.4, M3 명세 3.10.2).
"""

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Final, Protocol

from coin_report_trader.core import (
    ORDER_SIDE_TITLES,
    ErrorPauseCause,
    FillRecord,
    Mode,
    OrderPlan,
    OrderRecord,
    OrderSide,
    OrderState,
    PaperAccount,
    StorageError,
    coin_label,
)
from coin_report_trader.core.errors import ExchangeError
from coin_report_trader.core.exchange import ExchangeAccount, ExchangeRules, OrderSnapshot
from coin_report_trader.core.texts import (
    ERROR_BLOCKED,
    ERROR_STORAGE,
    STAGE_ORDER,
    STAGE_ORDER_SAVE,
    STAGE_RECONCILE,
    qty_text,
    trade_balance_line,
)
from coin_report_trader.storage import trading as trade_store
from coin_report_trader.trading.live import (
    KRW_MARKET,
    LiveSnapshot,
    canceled_direct_orders,
    direct_lock_started,
    direct_orders,
    executed_qty,
    find_external_changes,
    live_account_for_limits,
    live_realized_pnl,
    new_trades,
    queried_state,
    snapshot_rows,
)
from coin_report_trader.trading.orders import new_uid
from coin_report_trader.trading.paper import holding_of
from coin_report_trader.trading.paper.fills import first_judged_until
from coin_report_trader.trading.upbit import ERROR_KEY_INVALID, ErrorBranch
from coin_report_trader.trading.upbit.errors import branch_of

from . import alert_rules

log = logging.getLogger(__name__)

_MODULE: Final = __name__.removeprefix("coin_report_trader.")
_ZERO: Final = Decimal(0)
_FAR: Final = timedelta(days=36500)


class LiveFillStore(Protocol):
    """실거래 체결의 쓰기와 읽기(모듈 설명). 실제 프로그램은 `StoredLiveFills`다."""

    def write(self, conn: sqlite3.Connection, fill: FillRecord, trade_key: str) -> FillRecord:
        """체결 한 줄만 쓴다(주문의 상태와 계정은 쓰지 않는다). 실패는 `StorageError`다."""
        ...

    def trade_keys(self, conn: sqlite3.Connection, order_id: int) -> frozenset[str]:
        """그 주문에 이미 쓴 체결의 `trade_key`."""
        ...


class StoredLiveFills:
    """`LiveFillStore`를 `storage/trading.py`의 함수로 갖춘 것(모듈 설명)."""

    def write(self, conn: sqlite3.Connection, fill: FillRecord, trade_key: str) -> FillRecord:
        return trade_store.record_live_fill(conn, fill, trade_key)

    def trade_keys(self, conn: sqlite3.Connection, order_id: int) -> frozenset[str]:
        return trade_store.fill_trade_keys(conn, order_id)

    def __repr__(self) -> str:
        return "StoredLiveFills()"


class LiveEngineMixin:
    """실거래의 거래소 경계 부르기. `DataWorker`의 일부다."""

    _conn: Any
    _clock: Any
    _rules: Any
    _pause: Any
    _mode: Mode
    _exchange: Any
    _stage_now: str
    _reconciling: bool
    _cancel_pending: set[int]
    _post: Callable[..., None]
    _emit_alert: Callable[..., bool]
    _emit_save_failed: Callable[..., None]
    _enter_error: Callable[..., bool]
    _post_trade_status: Callable[[], None]
    _set_block: Callable[..., None]
    _trade_coins: Callable[[], tuple[str, ...]]

    def _init_live_engine(self) -> None:
        self._live_account: ExchangeAccount | None = None
        """매매 엔진이 마지막으로 읽은 실제 계정(실거래)."""
        self._live_rules: dict[str, ExchangeRules] = {}
        """코인마다 마지막으로 읽은 주문 가능 정보(3.6.1)."""
        self._missing_since: dict[int, datetime] = {}
        """조회가 처음 "없음"이었던 때(주문 번호마다. 3.6.3의 600초)."""
        self._snap_mark: int | None = None
        """마지막 계정 읽기 때까지 쓴 실거래 체결의 가장 큰 번호(외부 변동의 프로그램 몫. 3.6.6)."""
        self._direct_seen: dict[str, tuple[OrderSnapshot, ...]] = {}
        """코인마다 앞의 체결 대기 주문 목록의 직접 건 주문(A-7)."""
        self._direct_mark: int | None = None
        """앞의 목록을 읽을 때까지 쓴 실거래 체결의 가장 큰 번호(A-7)."""
        self._live_block_pending: tuple[float | None] | None = None
        """계정 화면이 받은 일시 차단(장치에 남아 매매의 다음 요청에 걸린다. 3.5.3)."""
        self._live_fill_store: LiveFillStore = StoredLiveFills()
        """실거래 체결을 쓰는 곳(모듈 설명)."""

    # --- 묻기 ---------------------------------------------------------------------------------

    def _live(self) -> bool:
        return self._mode is Mode.LIVE

    def _live_ready(self) -> bool:
        """매매 엔진이 거래소에 요청해도 되는지. 계정 화면이 받은 일시 차단이 남아 있으면 그 차단을
        지금 걸고(오류 멈춤과 A-23) 거짓이다(3.5.3, 3.15.5). 모의매매의 경계(`PaperExchange`)는
        실거래가 아니므로 거짓이다."""
        exchange = self._exchange
        if exchange is None or exchange.mode is not Mode.LIVE:
            return False
        if self._pause.error and not self._reconciling:
            return False
        pending = self._live_block_pending
        if pending is not None:
            self._live_block_pending = None
            self._set_block(pending[0])
            return False
        return True

    def _live_limits_account(self) -> PaperAccount:
        """한도와 판단에 넘길 꼴의 실제 계정(마지막 엔진 읽기). 읽은 적이 없으면 `StorageError`."""
        account = self._live_account
        if account is None:
            raise StorageError("실제 계정을 아직 읽지 않았습니다")
        return live_account_for_limits(account)

    # --- 오류(3.5.5) --------------------------------------------------------------------------

    def _exchange_failed(
        self, error_name: str, stage: str, coin: str | None, *, unlisted_alert: bool = True
    ) -> ErrorBranch:
        """거래소 오류의 갈래대로 한다. 거래를 계속 막는 오류는 오류 멈춤과 A-23이다."""
        branch = branch_of(error_name)
        if branch is ErrorBranch.BLOCKING:
            self._stage_now = stage
            if error_name == ERROR_BLOCKED:
                self._live_block(None)
            else:
                self._live_key_error(error_name, stage)
        elif branch is ErrorBranch.UNLISTED and unlisted_alert:
            wanted = self._rules.want_a24(coin, error_name, self._clock.monotonic())
            spec = alert_rules.unknown_exchange(coin, stage, error_name)
            self._emit_alert(spec, wanted, module=_MODULE)
        return branch

    def _exchange_exc(
        self, exc: ExchangeError, stage: str, coin: str | None, *, unlisted_alert: bool = True
    ) -> ErrorBranch:
        if exc.error_name == ERROR_BLOCKED:
            self._stage_now = stage
            self._live_block(exc.retry_after)
            return ErrorBranch.BLOCKING
        return self._exchange_failed(exc.error_name, stage, coin, unlisted_alert=unlisted_alert)

    def _live_block(self, retry_after: float | None) -> None:
        """업비트의 418: 차단이 풀리는 때를 담고 오류 멈춤 "일시 차단"과 A-23(3.5.3)."""
        self._set_block(retry_after)

    def _live_key_error(self, kind: str, stage: str) -> None:
        """키 오류 넷: 오류 멈춤(원인 `EXCHANGE`, {오류 종류}는 그 이름)과 A-23(3.5.5)."""
        if self._enter_error(ErrorPauseCause.EXCHANGE, kind):
            self._emit_alert(alert_rules.exchange_error_pause(stage, kind), True, module=_MODULE)
        else:
            self._post_trade_status()

    # --- 매매 엔진의 계정 읽기(3.6.1, 3.6.6) -----------------------------------------------------

    def _engine_read(self, stage: str, coin: str | None = None) -> ExchangeAccount | str:
        """실제 계정을 읽고 외부 변동·A-6과 `live_snapshots`를 쓴다. 읽지 못하면 오류 이름."""
        if not self._live_ready():
            return ERROR_BLOCKED if self._pause.error else ERROR_KEY_INVALID
        failed: ExchangeError | None = None
        account: ExchangeAccount | None = None
        try:
            account = self._exchange.read_account()
        except ExchangeError as exc:
            failed = exc
        if failed is not None or account is None:
            assert failed is not None
            self._exchange_exc(failed, stage, coin, unlisted_alert=False)
            return failed.error_name
        self._live_account = account
        try:
            self._after_engine_read(account)
        except StorageError as exc:
            self._emit_save_failed(STAGE_ORDER_SAVE, None, exc, module=_MODULE)
        return account

    def _after_engine_read(self, account: ExchangeAccount) -> None:
        coins = self._trade_coins()
        stored = trade_store.read_live_snapshots(self._conn)
        before = tuple(
            LiveSnapshot(row.market, row.balance, row.locked, row.direct_locked, row.read_utc)
            for row in stored
        )
        fills = self._live_fills_after(self._snap_mark, before)
        now = self._clock.now()
        for change in find_external_changes(before, account, coins, fills):
            trade_store.insert_external_change(
                self._conn, mode=Mode.LIVE, market=change.market, qty_delta=change.qty_delta, at=now
            )
            coin = None if change.market == KRW_MARKET else change.market
            self._emit_alert(alert_rules.external_change(coin), True, module=_MODULE)
        rows = snapshot_rows(account, coins, self._program_open_snapshots())
        previous = {row.market: row.direct_locked for row in before}
        for row in rows:
            if row.market == KRW_MARKET:
                continue
            if direct_lock_started(previous.get(row.market, _ZERO), row.direct_locked):
                spec = alert_rules.direct_locked(row.market, qty_text(row.direct_locked))
                self._emit_alert(spec, True, module=_MODULE)
        trade_store.replace_live_snapshots(
            self._conn,
            [
                trade_store.LiveSnapshotRow(
                    row.market, row.balance, row.locked, row.direct_locked, row.read_utc
                )
                for row in rows
            ],
        )
        self._snap_mark = self._max_live_fill_id()

    def _write_start_snapshots(self, account: ExchangeAccount) -> None:
        """실거래 켜기의 외부 변동 시작점(3.7.2의 3). `StorageError`는 부르는 쪽이 다룬다."""
        rows = snapshot_rows(account, self._trade_coins(), self._program_open_snapshots())
        trade_store.replace_live_snapshots(
            self._conn,
            [
                trade_store.LiveSnapshotRow(
                    row.market, row.balance, row.locked, row.direct_locked, row.read_utc
                )
                for row in rows
            ],
        )
        self._live_account = account
        self._snap_mark = self._max_live_fill_id()

    def _live_fills_after(
        self, mark: int | None, before: tuple[LiveSnapshot, ...]
    ) -> tuple[FillRecord, ...]:
        """앞의 읽기 뒤에 쓴 실거래 체결. 이 실행에서 읽은 적이 없으면(다시 켬) 앞의 읽은 때 뒤의
        체결이다."""
        fills = trade_store.list_fills(self._conn, Mode.LIVE)
        if mark is not None:
            return tuple(fill for fill in fills if fill.fill_id > mark)
        if not before:
            return ()
        since = min(row.read_utc for row in before)
        return tuple(fill for fill in fills if fill.filled_utc > since)

    def _max_live_fill_id(self) -> int:
        fills = trade_store.list_fills(self._conn, Mode.LIVE)
        return max((fill.fill_id for fill in fills), default=0)

    def _program_open_snapshots(self) -> tuple[OrderSnapshot, ...]:
        """저장한 걸린 실거래 주문(남은 수량은 수량 − 체결 수량. A-6의 프로그램 몫)."""
        result: list[OrderSnapshot] = []
        for order in trade_store.list_open_orders(self._conn, Mode.LIVE):
            stored = trade_store.read_order_exec(self._conn, order.order_id)
            done = _ZERO if stored is None or stored[0] is None else stored[0]
            result.append(
                OrderSnapshot(
                    uid=order.uid,
                    market=order.market,
                    side=order.side,
                    kind=order.kind,
                    price=order.price,
                    qty=order.qty,
                    executed_qty=done,
                    state=order.state,
                    trades=(),
                    program=True,
                )
            )
        return tuple(result)

    def _live_rules_for(self, market: str) -> ExchangeRules | str:
        """그 코인의 주문 가능 정보를 읽는다(회차마다 주문 전에 한 번. 3.6.1). 실패면 오류 이름."""
        if not self._live_ready():
            return ERROR_BLOCKED if self._pause.error else ERROR_KEY_INVALID
        failed: ExchangeError | None = None
        rules: ExchangeRules | None = None
        try:
            rules = self._exchange.read_rules(market)
        except ExchangeError as exc:
            failed = exc
        if failed is not None or rules is None:
            assert failed is not None
            self._exchange_exc(failed, STAGE_ORDER, market, unlisted_alert=False)
            return failed.error_name
        self._live_rules[market] = rules
        return rules

    # --- 주문 내기(3.6.2, 3.6.3) ---------------------------------------------------------------

    def _live_place(self, plan: OrderPlan, round_id: int | None) -> tuple[int | None, str | None]:
        """(주문 번호, 오류 이름). 주문을 쓰지 못했거나 보내지 못했으면 번호가 `None`이다. 거절이면
        번호와 오류 이름이 둘 다 있다(그 주문은 `FAILED`)."""
        if not self._live_ready():
            return None, ERROR_BLOCKED if self._pause.error else ERROR_KEY_INVALID
        now = self._clock.now()
        try:
            order = trade_store.insert_order(
                self._conn,
                plan,
                mode=Mode.LIVE,
                uid=new_uid(),
                round_id=round_id,
                created_at=now,
                judged_until=first_judged_until(now),
            )
            if plan.side is OrderSide.SELL:
                avg = self._live_avg(plan.market)
                if avg is not None:
                    trade_store.update_order_exec(self._conn, order.order_id, avg_price_at_send=avg)
        except StorageError as exc:
            self._emit_save_failed(STAGE_ORDER_SAVE, plan.market, exc, module=_MODULE)
            return None, ERROR_STORAGE
        self._rules.a29_succeeded(STAGE_ORDER_SAVE, plan.market)
        failed: ExchangeError | None = None
        result = None
        try:
            result = self._exchange.place(plan, order.uid)
        except ExchangeError as exc:  # 받은 주문의 꼴을 읽지 못함: 결과를 모르는 것으로 둔다
            failed = exc
        try:
            if result is not None and result.snapshot is not None:
                trade_store.update_order(
                    self._conn, order.order_id, state=OrderState.OPEN, sent=True
                )
                self._live_order_alert(order, plan.amount)
                self._apply_live_snapshot(order, result.snapshot)
                return order.order_id, None
            if failed is not None or (result is not None and result.unknown):
                name = failed.error_name if failed is not None else result.error_name  # type: ignore[union-attr]
                trade_store.update_order(
                    self._conn,
                    order.order_id,
                    state=OrderState.UNCONFIRMED,
                    error_name=name,
                    sent=True,
                )
                self._missing_since[order.order_id] = now
                return order.order_id, None
            assert result is not None
            name = result.error_name or ERROR_KEY_INVALID
            trade_store.update_order(
                self._conn,
                order.order_id,
                state=OrderState.FAILED,
                closed_at=now,
                error_name=name,
                sent=True,
            )
        except StorageError as exc:
            self._emit_save_failed(STAGE_ORDER_SAVE, plan.market, exc, module=_MODULE)
            return order.order_id, ERROR_STORAGE
        if branch_of(name) is ErrorBranch.BLOCKING:
            self._exchange_failed(name, self._stage_now, plan.market)
        return order.order_id, name

    def _live_avg(self, market: str) -> Decimal | None:
        account = self._live_account
        if account is None:
            return None
        for holding in account.holdings:
            if holding.market == market:
                return holding.avg_buy_price
        return None

    def _live_order_alert(self, order: OrderRecord, amount: Decimal) -> None:
        from .trade_round import _summary

        spec = alert_rules.order_placed(
            order.market, _summary(order), self._live_line(order, amount)
        )
        self._emit_alert(spec, True, module=_MODULE)

    def _live_line(self, order: OrderRecord, amount: Decimal) -> str:
        """디스코드 매매 알림의 셋째 줄: 실거래는 실제 잔고(마지막 엔진 읽기. 3.10)."""
        account = self._live_account
        krw = _ZERO if account is None else account.krw
        qty = _ZERO
        if account is not None:
            limits = live_account_for_limits(account)
            holding = holding_of(limits, order.market)
            qty = _ZERO if holding is None else holding.qty
        return trade_balance_line(amount, krw, coin_label(order.market), qty)

    # --- 조회와 체결 쓰기(3.6.3, 3.6.4) --------------------------------------------------------

    def _apply_live_snapshot(self, order: OrderRecord, snapshot: OrderSnapshot) -> bool:
        """조회·취소의 결과를 쓴다: 아직 쓰지 않은 체결, 체결 수량, 상태. 체결을 쓰지 못하면 A-29
        ("주문 저장")와 오류 멈춤이고 거짓이다.

        주문 생성과 취소의 응답에는 체결 목록이 없다. 체결 수량만큼의 체결 목록이 없으면 한 번
        조회해 받고, 그래도 없으면 체결을 다 쓰기 전에는 주문을 닫지 않는다(다음 조회에서 쓴다)."""
        if executed_qty(snapshot.trades) < snapshot.executed_qty:
            snapshot = self._full_snapshot(order, snapshot)
        written = self._live_trade_keys(order.order_id)
        fresh = new_trades(snapshot, written)
        stored = trade_store.read_order_exec(self._conn, order.order_id)
        avg_at_send = None if stored is None else stored[1]
        rules = self._live_rules.get(order.market)
        if rules is None and fresh and order.side is OrderSide.SELL:
            read = self._live_rules_for(order.market)  # 손익의 bid_fee(3.6.4)
            rules = None if isinstance(read, str) else read
        side = ORDER_SIDE_TITLES[order.side]
        for trade in fresh:
            pnl: Decimal | None = None
            if (
                order.side is OrderSide.SELL
                and avg_at_send is not None
                and avg_at_send > 0
                and rules is not None
            ):
                pnl = live_realized_pnl(trade, avg_at_send, rules.bid_fee)
            record = FillRecord(
                fill_id=0,
                order_id=order.order_id,
                mode=Mode.LIVE,
                market=order.market,
                side=order.side,
                price=trade.price,
                qty=trade.qty,
                fee=trade.fee,
                realized_pnl=pnl,
                filled_utc=trade.filled_utc,
            )
            try:
                self._write_live_fill(record, trade.trade_key)
            except StorageError as exc:
                self._emit_save_failed(STAGE_ORDER_SAVE, order.market, exc, module=_MODULE)
                return False
            self._rules.a29_succeeded(STAGE_ORDER_SAVE, order.market)
            line = self._live_line(order, trade.price * trade.qty)
            spec = alert_rules.filled(order.market, side, trade.qty, trade.price, line)
            self._emit_alert(spec, True, module=_MODULE)
        done = executed_qty(snapshot.trades)
        closing = snapshot.state in (OrderState.FILLED, OrderState.CANCELED)
        try:
            trade_store.update_order_exec(self._conn, order.order_id, executed_qty=done)
            if snapshot.state is not order.state or closing:
                trade_store.update_order(
                    self._conn,
                    order.order_id,
                    state=snapshot.state,
                    closed_at=self._clock.now() if closing else None,
                    sent=True,
                )
        except StorageError as exc:
            self._emit_save_failed(STAGE_ORDER_SAVE, order.market, exc, module=_MODULE)
            return False
        if closing:
            self._cancel_pending.discard(order.order_id)
        return True

    def _full_snapshot(self, order: OrderRecord, snapshot: OrderSnapshot) -> OrderSnapshot:
        failed: ExchangeError | None = None
        found: OrderSnapshot | None = None
        try:
            found = self._exchange.query(order.uid)
        except ExchangeError as exc:
            failed = exc
        if failed is not None:
            self._exchange_exc(failed, STAGE_RECONCILE, order.market, unlisted_alert=False)
        if found is not None:
            snapshot = found
        if executed_qty(snapshot.trades) < snapshot.executed_qty:
            snapshot = replace(snapshot, state=OrderState.OPEN)
        return snapshot

    def _write_live_fill(self, record: FillRecord, trade_key: str) -> FillRecord:
        return self._live_fill_store.write(self._conn, record, trade_key)

    def _live_trade_keys(self, order_id: int) -> frozenset[str]:
        return self._live_fill_store.trade_keys(self._conn, order_id)

    def _live_judge(self, order: OrderRecord) -> bool | None:
        """걸린·확인 안 됨 주문 하나를 조회한다. 끝났으면(체결, 취소, 보내지 않음) 참, 걸린 그대로면
        거짓, 알 수 없으면(조회 실패, "없음", 쓰기 실패) `None`."""
        if not self._live_ready():
            return None
        failed: ExchangeError | None = None
        snapshot: OrderSnapshot | None = None
        try:
            snapshot = self._exchange.query(order.uid)
        except ExchangeError as exc:
            failed = exc
        if failed is not None:
            self._exchange_exc(failed, STAGE_RECONCILE, order.market)
            return None
        now = self._clock.now()
        if snapshot is None:
            since = self._missing_since.get(order.order_id)
            state = queried_state(order, None, missing_since=since, now=now)
            if state is OrderState.NOT_SENT:
                trade_store.update_order(
                    self._conn, order.order_id, state=OrderState.NOT_SENT, closed_at=now
                )
                self._missing_since.pop(order.order_id, None)
                self._cancel_pending.discard(order.order_id)
                return True
            if since is None:
                self._missing_since[order.order_id] = now
            if order.state is not OrderState.UNCONFIRMED:
                trade_store.update_order(
                    self._conn, order.order_id, state=OrderState.UNCONFIRMED, sent=True
                )
            return None
        self._missing_since.pop(order.order_id, None)
        if not self._apply_live_snapshot(order, snapshot):
            return None
        return snapshot.state is not OrderState.OPEN

    def _live_unconfirmed(self, market: str | None = None) -> tuple[OrderRecord, ...]:
        """저장한 실거래의 확인 안 됨 주문."""
        end = self._clock.now() + _FAR
        start = end - 2 * _FAR
        return tuple(
            order
            for order in trade_store.list_orders(
                self._conn, Mode.LIVE, start=start, end=end, market=market
            )
            if order.state is OrderState.UNCONFIRMED
        )

    def _live_judge_unconfirmed(self) -> None:
        for order in self._live_unconfirmed():
            self._live_judge(order)

    def _live_close_unsent(self) -> None:
        """켤 때 `sent` 0인 실거래 줄: 바로 닫지 않고 조회한다(3.6.3)."""
        for order in trade_store.list_unsent_orders(self._conn, Mode.LIVE):
            self._live_judge(order)

    # --- 취소(3.6.4) ----------------------------------------------------------------------------

    def _live_cancel(self, order: OrderRecord, *, by_round: bool = False) -> bool:
        """취소 전 조회 뒤 취소한다. 취소했으면 참(모의매매의 `_cancel_order`와 같은 뜻)."""
        judged = self._live_judge(order)
        if judged is None:
            fresh = trade_store.read_order(self._conn, order.order_id)
            if fresh is not None and fresh.state is OrderState.OPEN:
                self._cancel_pending.add(order.order_id)
            return False
        if judged:
            self._cancel_pending.discard(order.order_id)
            return False
        failed: ExchangeError | None = None
        snapshot: OrderSnapshot | None = None
        try:
            snapshot = self._exchange.cancel(order.uid)
        except ExchangeError as exc:
            failed = exc
        if failed is not None:
            self._exchange_exc(failed, self._stage_now, order.market)
            self._cancel_pending.add(order.order_id)
            return False
        now = self._clock.now()
        if snapshot is None:
            trade_store.update_order(
                self._conn, order.order_id, state=OrderState.NOT_SENT, closed_at=now
            )
            self._cancel_pending.discard(order.order_id)
            return False
        fresh = trade_store.read_order(self._conn, order.order_id) or order
        if not self._apply_live_snapshot(fresh, snapshot):
            self._cancel_pending.add(order.order_id)
            return False
        if snapshot.state is OrderState.CANCELED:
            if by_round:
                trade_store.update_order(
                    self._conn,
                    order.order_id,
                    state=OrderState.CANCELED,
                    closed_at=now,
                    canceled_by_round=True,
                )
            self._cancel_pending.discard(order.order_id)
            return True
        if snapshot.state is OrderState.OPEN:
            self._cancel_pending.add(order.order_id)
        return False

    # --- 대조(3.6.5)와 A-7 -----------------------------------------------------------------------

    def _live_lists(self) -> bool:
        """고른 코인마다 체결 대기 주문 목록을 읽어 직접 건 주문을 가르고 A-7을 본다. 다 읽었으면
        참."""
        if not self._live_ready():
            return False
        coins = self._trade_coins()
        fills = self._live_fills_after(self._direct_mark, ())
        mark = self._max_live_fill_id()
        ok = True
        for market in coins:
            failed: ExchangeError | None = None
            listed: tuple[OrderSnapshot, ...] = ()
            try:
                listed = self._exchange.open_orders(market)
            except ExchangeError as exc:
                failed = exc
            if failed is not None:
                self._exchange_exc(failed, STAGE_RECONCILE, market)
                ok = False
                continue
            ours = [
                order.uid
                for order in listed
                if trade_store.find_order(self._conn, order.uid) is not None
            ]
            direct = direct_orders(listed, ours)
            before = self._direct_seen.get(market)
            if before is not None and self._direct_mark is not None:
                for _ in canceled_direct_orders(before, direct, fills):
                    self._emit_alert(alert_rules.direct_canceled(market), True, module=_MODULE)
            self._direct_seen[market] = direct
        if ok:
            self._direct_mark = mark
        return ok

    def _live_reconcile(self) -> bool:
        """대조(3.6.5): 목록 → 저장한 걸림·확인 안 됨 주문마다 조회 → 계정 읽기(외부 변동)."""
        if not self._live_lists():
            return False
        ok = True
        for order in trade_store.list_open_orders(self._conn, Mode.LIVE):
            if self._live_judge(order) is None:
                ok = False
        for order in self._live_unconfirmed():
            self._live_judge(order)
        account = self._engine_read(STAGE_RECONCILE)
        return ok and not isinstance(account, str)
