"""3단계 잇기 2: 멈춤, 오류 멈춤, 전량 매도, 상태 띠의 값(M3 명세 3.10, 3.14.1).

자료 스레드(`DataWorker`)가 상속한다. 이 모듈의 메서드는 모두 자료 스레드에서만 부른다. 상태는
`trading/risk/`의 `PauseState`와 그것을 바꾸는 함수가 정하고(여기서 셈하지 않는다), 이 모듈은 바뀐
상태를 표 `pauses`에 쓰고, 켤 때 읽어 이어 가며, 취소와 판정과 손절 점검을 차례로 부른다.

- 멈춤('멈춤'): 확인 창 없이 바로. 프로그램이 건 주문을 모두 취소하고 도는 회차를 버린다.
- 오류 멈춤: 거래를 계속 막는 오류(일시 차단. A-23), 예상하지 못한 오류(A-30), 매매 DB 쓰기의
  실패(A-29)가 날 때마다 든다(알림의 되풀이와 따로). 들어갈 때 주문은 취소하지 않는다.
- 풀기: 대조(소급 판정) → 손절 점검 → 다음 예정 시각부터 회차. 멈춘 동안 지나간 시각은 돌리지
  않는다.
- 전량 매도: 멈춘 뒤 고른 코인마다 손절 매도의 차례로 팔 수 있는 수량을 시장가로 판다. 실패한
  코인은 1분마다 다시 판다.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from coin_report_trader.core import (
    ErrorPauseCause,
    Mode,
    NoOrderReason,
    OrderReason,
    OrderRules,
    PauseKind,
    StorageError,
    TradeSettings,
    TradeStatusView,
    coin_label,
)
from coin_report_trader.core.texts import (
    ERROR_BLOCKED,
    STAGE_CANDLES,
    STAGE_ORDER_SAVE,
    STAGE_PAUSE_SAVE,
    STAGE_REPORT_SAVE,
    STAGE_SELL_ALL,
    STARTING_RECONCILE,
    STARTING_STOP_CHECK,
    kst_when_text,
    qty_text,
)
from coin_report_trader.storage import reports as report_store
from coin_report_trader.storage import trading as trade_store
from coin_report_trader.trading.paper import holding_of, sellable_qty
from coin_report_trader.trading.risk.pause import (
    PauseState,
    enter_error,
    may_fetch_quotes,
    may_sell_all,
    may_stop_check,
    pause_user,
    release_error,
    release_user,
    stop_loss_off,
)
from coin_report_trader.trading.risk.sell_all import SellAllResult, sell_all_text
from coin_report_trader.trading.risk.stop_loss import stop_loss_plan

from . import alert_rules
from .trade_round import _summary

log = logging.getLogger(__name__)

_MODULE: Final = __name__.removeprefix("coin_report_trader.")
TRADE_WRITE_STAGES: Final = frozenset({STAGE_ORDER_SAVE, STAGE_PAUSE_SAVE, STAGE_REPORT_SAVE})
"""오류 멈춤에 드는 저장 실패의 단계: 주문·체결·가상 계정("주문 저장"), 회차와 보고서("보고서
저장"), 멈춤("멈춤 저장")의 쓰기다(3.10.2). 설정, 설정 바뀜, 봉, 알림, 보관소, 백업은 안 든다."""
TOO_SMALL_NAME: Final = "최소 주문 금액보다 작음"
"""A-25의 오류 이름: 평가 금액이 최소 주문 금액보다 작아 팔지 못함(3.14.1)."""
_ZERO: Final = Decimal(0)


class PauseMixin:
    """멈춤, 오류 멈춤, 전량 매도. `DataWorker`의 일부다."""

    _conn: Any
    _clock: Any
    _settings: Any
    _stop: Any
    _blocked_until: Any
    _blocked_mono: Any
    _next_due: Any
    _active_round: Any
    _pending: Any
    _trade_ready: bool
    _tick_error_name: str
    _last_quote_error: str
    _post: Callable[..., None]
    _emit_alert: Callable[..., bool]
    _emit_save_failed: Callable[..., None]
    _trade: Callable[[], Any]
    _trade_coins: Callable[[], Any]
    _db_account: Callable[[], Any]
    _cancel_order: Callable[..., bool]
    _cancel_pending: Any
    _judge_orders: Callable[[Any], bool]
    _minute_check: Callable[[], None]
    _read_quote_price: Callable[[str], Decimal | None]
    _tick_for: Callable[[str], Decimal | None]
    _place: Callable[..., Any]
    _post_trade_views: Callable[[], None]
    _post_round_now: Callable[[], None]
    _round_finished: Callable[[], None]
    _post_report_list: Callable[[str], None]
    _schedule_next: Callable[[], Any]
    _expire_block: Callable[[], None]
    stop_ai: Callable[[], None]
    _rules: Any
    _min_order: Decimal
    _mode: Mode
    _trade_account: Callable[[], Any]
    _coin_min: Callable[[str], Decimal]
    _engine_read: Callable[..., Any]
    _live_reconcile: Callable[[], bool]
    _post_live_views: Callable[[], None]

    def _init_pause(self) -> None:
        self._pause = PauseState()
        self._pause_rows: dict[str, int] = {}
        """풀리지 않은 멈춤의 `pauses.id`("user", "error"). 쓰지 못한 멈춤은 메모리에만 있다."""
        self._starting_step: str | None = None
        self._sell_all_state: dict[str, tuple[SellAllResult, Decimal | None]] = {}
        self._sell_all_alerted: set[str] = set()
        self._stage_now = STAGE_CANDLES
        """지금 하는 일의 단계(A-23의 단계. `_guard`가 정한다)."""
        self._reconciling = False
        """대조 중이다(오류 멈춤을 풀려고 걸린 주문을 판정하는 동안은 공개 시세를 부른다)."""
        self._memory_error_cause: ErrorPauseCause | None = None
        """앞 실행의 `"memory"` 줄에 A-29나 A-30이 있었다(켤 때 오류 멈춤으로 다시 든다)."""

    # --- 묻기 ---------------------------------------------------------------------------------

    def _trade_active(self) -> bool:
        """3단계로 처음 설정을 마쳤다(오류 멈춤과 멈춤은 그 뒤부터다. 3.10.2)."""
        return bool(self._trade() is not None)

    def _quotes_allowed(self) -> bool:
        """공개 시세를 요청해도 되는지: 원인이 일시 차단인 오류 멈춤 동안은 하나도 내지 않는다.
        풀려고 대조하는 동안은 부른다(`PauseState`의 `may_fetch_quotes`)."""
        return self._reconciling or may_fetch_quotes(self._pause)

    # --- 오류 멈춤에 들어감(3.10.2) -----------------------------------------------------------

    def _enter_error(self, cause: ErrorPauseCause, detail: str | None) -> bool:
        """오류 멈춤에 새로 들어갔으면 참. 이미 오류 멈춤이거나 처음 설정을 마치지 않았으면 거짓.
        표에 쓰지 못해도 메모리에 둔 채 오류 멈춤이다. 어떤 일이 있어도 예외를 내지 않는다."""
        try:
            if not self._trade_active() or self._pause.error:
                return False
            self._pause = enter_error(self._pause, cause, detail)
            self._starting_step = None
            for market, (result, _) in tuple(self._sell_all_state.items()):
                if result is not SellAllResult.DONE:
                    self._sell_all_state[market] = (SellAllResult.STOPPED, None)
            self._write_pause(PauseKind.ERROR, cause, detail, False)
            self._discard_running_round()
            self._post_pause_views()
            self._post_trade_views()
            return True
        except Exception as exc:
            log.warning("오류 멈춤에 들어가는 중 실패했습니다: %s", type(exc).__name__)
            return True

    def _block_pause(self) -> None:
        """일시 차단이 걸렸다(`_set_block` 뒤): 오류 멈춤 `EXCHANGE`와 A-23(들어갈 때마다)."""
        if not self._trade_active():
            return
        if self._enter_error(ErrorPauseCause.EXCHANGE, ERROR_BLOCKED):
            now = self._clock.now()
            until = self._blocked_until
            when = None if until is None else kst_when_text(until, now)
            spec = alert_rules.error_pause(self._stage_now, ERROR_BLOCKED, when)
            self._emit_alert(spec, True, module=_MODULE)
        else:
            self._post_trade_status()

    # --- 표 `pauses` ---------------------------------------------------------------------------

    def _write_pause(
        self, kind: PauseKind, cause: ErrorPauseCause | None, detail: str | None, sell_all: bool
    ) -> None:
        try:
            pause_id = trade_store.insert_pause(
                self._conn,
                mode=self._mode,
                kind=kind,
                cause=cause,
                detail=detail,
                sell_all=sell_all,
                started_at=self._clock.now(),
            )
        except StorageError as exc:
            self._emit_save_failed(STAGE_PAUSE_SAVE, None, exc, module=_MODULE)
            return
        self._rules.a29_succeeded(STAGE_PAUSE_SAVE, None)
        self._pause_rows["error" if kind is PauseKind.ERROR else "user"] = pause_id

    def _release_pause(self, key: str) -> None:
        pause_id = self._pause_rows.pop(key, None)
        if pause_id is None:
            return
        try:
            trade_store.release_pause(self._conn, pause_id, released_at=self._clock.now())
        except StorageError as exc:
            self._emit_save_failed(STAGE_PAUSE_SAVE, None, exc, module=_MODULE)

    def _restore_pauses(self) -> None:
        """켤 때: 풀리지 않은 멈춤을 읽어 이어 가고, 앞 실행에서 표에 쓰지 못한 A-29·A-30이 있었으면
        오류 멈춤으로 다시 든다(3.10.2의 '메모리의 오류 멈춤')."""
        try:  # 모드를 바꿔도 멈춤은 이어진다: 두 모드의 풀리지 않은 멈춤을 모두 읽는다
            rows = (
                *trade_store.current_pauses(self._conn, Mode.PAPER),
                *trade_store.current_pauses(self._conn, Mode.LIVE),
            )
        except StorageError as exc:
            log.warning("멈춤을 읽지 못했습니다: %s", exc)
            rows = ()
        state = self._pause
        for row in rows:
            if row.kind is PauseKind.USER:
                state = pause_user(state)
                self._pause_rows["user"] = row.pause_id
            elif row.cause is not None:
                state = enter_error(state, row.cause, row.detail)
                self._pause_rows["error"] = row.pause_id
        self._pause = state
        cause = self._memory_error_cause
        self._memory_error_cause = None
        if cause is not None and not self._pause.error:
            self._enter_error(cause, None)

    # --- 멈춤 동작(3.10.1) ---------------------------------------------------------------------

    def _do_pause(self, *, sell_all: bool) -> None:
        """'멈춤' 또는 '멈추고 전량 매도'."""
        if not self._trade_active() or self._conn is None:
            return
        if sell_all and not may_sell_all(self._pause):
            return
        if not self._pause.user:
            self._pause = pause_user(self._pause)
            self._write_pause(PauseKind.USER, None, None, sell_all)
            self._discard_running_round()
            self._cancel_all_orders()
        elif sell_all:
            pause_id = self._pause_rows.get("user")
            if pause_id is not None:
                try:
                    trade_store.mark_sell_all(self._conn, pause_id)
                except StorageError as exc:
                    self._emit_save_failed(STAGE_PAUSE_SAVE, None, exc, module=_MODULE)
        if sell_all:
            self._sell_all_begin()
        self._post_pause_views()
        self._post_trade_views()

    def _cancel_all_orders(self) -> None:
        for order in trade_store.list_open_orders(self._conn, self._mode):
            self._cancel_order(order)

    def _discard_running_round(self) -> None:
        """도는 회차를 버린다(3.5.3): 회차 `discarded`, `RUNNING` 보고서 `DISCARDED`(주문하지 않은
        까닭 "멈춤으로 버림"), 도는 세션은 끝내고 그 결과는 버린다."""
        current = self._active_round
        if current is None or self._conn is None:
            return
        try:
            if not report_store.has_running_report(self._conn, current.round_id):
                return
        except StorageError as exc:
            log.warning("도는 회차를 읽지 못했습니다: %s", exc)
            return
        now = self._clock.now()
        dropped = [
            item for item in self._pending.values() if item.round.round_id == current.round_id
        ]
        for item in dropped:
            self._pending.pop(item.report_id, None)
        self.stop_ai()
        try:
            trade_store.discard_round(self._conn, current.round_id, ended_at=now)
            elapsed = int((now - current.close).total_seconds())
            for item in dropped:
                trade_store.set_report_order(
                    self._conn,
                    item.report_id,
                    order_id=None,
                    no_order_reason=NoOrderReason.DROPPED_BY_PAUSE.value,
                    elapsed_order_s=elapsed,
                )
        except StorageError as exc:
            log.warning("도는 회차를 버리지 못했습니다: %s", exc)
        self._active_round = None
        self._round_finished()
        for item in dropped:
            self._post_report_list(item.market)

    # --- 풀기 -----------------------------------------------------------------------------------

    def _do_resume_user(self) -> None:
        """'멈춤 풀기': 대조 → 손절 점검 → 다음 예정 시각부터 회차."""
        if not self._pause.user or self._conn is None or not self._trade_active():
            return
        self._pause = release_user(self._pause)
        self._release_pause("user")
        self._sell_all_state.clear()
        self._sell_all_alerted.clear()
        self._start_checks()

    def _do_resume_error(self) -> None:
        """'오류 멈춤 풀기': 먼저 대조한다. 대조가 끝나면 풀리고, 실패하면 오류 멈춤 그대로다.
        원인이 일시 차단이면 차단이 풀리기 전에는 풀지 않는다(단추가 흐리다)."""
        state = self._pause
        if not state.error or self._conn is None or not self._trade_active():
            return
        until = self._blocked_until
        exchange = state.error_cause is ErrorPauseCause.EXCHANGE
        if exchange and until is not None and self._clock.now() < until:
            return
        self._set_starting(STARTING_RECONCILE)
        if not self._reconcile():
            self._set_starting(None)
            return
        self._pause = release_error(self._pause)
        self._release_pause("error")
        self._expire_block()
        self._start_checks(reconciled=True)

    def _reconcile(self) -> bool:
        """걸린 주문을 모두 1분봉으로 소급 판정한다(3.7.4). 모두 판정했으면 참. 실거래는 거래소와
        맞춰 본다(M5 명세 3.6.5)."""
        self._reconciling = True
        try:
            if self._mode is Mode.LIVE:
                return bool(self._live_reconcile())
            return bool(self._judge_orders(trade_store.list_open_orders(self._conn, Mode.PAPER)))
        finally:
            self._reconciling = False

    def _start_checks(self, *, reconciled: bool = False) -> None:
        """대조(소급 판정)와 손절 점검을 하고 다음 예정 시각부터 회차를 돌린다."""
        self._trade_ready = False
        if not reconciled:
            self._set_starting(STARTING_RECONCILE)
            self._reconcile()
        self._set_starting(STARTING_STOP_CHECK)
        try:
            self._minute_check()
        finally:
            self._starting_step = None
        self._schedule_next()
        self._post_pause_views()
        self._post_trade_views()

    def _set_starting(self, step: str | None) -> None:
        self._starting_step = step
        self._post_trade_status()

    # --- 전량 매도(3.10.3) --------------------------------------------------------------------

    def _sell_all_begin(self) -> None:
        self._sell_all_state = {}
        self._sell_all_alerted = set()
        if self._mode is Mode.LIVE:
            self._engine_read(STAGE_SELL_ALL)  # 취소한 뒤의 실제 계정(M5 명세 3.6.1)
        for market in self._trade_coins():
            self._sell_all_coin(market)

    def _retry_sell_all(self) -> None:
        """실패한 코인은 '멈춤' 중 손절 설정과 상관없이 1분마다 다시 판다(3.10.3)."""
        if self._pause.error or not self._pause.user:
            return
        for market, (result, _) in tuple(self._sell_all_state.items()):
            if result in (SellAllResult.RETRYING, SellAllResult.LEFT):
                self._sell_all_coin(market)

    def _sell_all_coin(self, market: str) -> None:
        trade: TradeSettings | None = self._trade()
        if trade is None:
            return
        try:
            account = self._trade_account()
        except StorageError:
            self._sell_all_failed(market, SellAllResult.RETRYING, _ZERO, "")
            return
        holding = holding_of(account, market)
        if holding is None or holding.qty <= 0:
            self._sell_all_state[market] = (SellAllResult.DONE, None)
            return
        qty = sellable_qty(holding)
        if qty <= 0:  # 취소가 확인되지 않은 주문이 묶고 있다: 확인될 때까지 다시 한다
            self._sell_all_failed(market, SellAllResult.RETRYING, qty, "")
            return
        price = self._read_quote_price(market)
        if price is None:
            error = self._last_quote_error or NoOrderReason.NO_PRICE_OR_TICK.value
            self._sell_all_failed(market, SellAllResult.RETRYING, qty, error)
            return
        tick = self._tick_for(market)
        if tick is None:
            self._sell_all_failed(market, SellAllResult.RETRYING, qty, self._tick_error_name)
            return
        decision = stop_loss_plan(
            holding,
            price,
            trade,
            OrderRules(tick, self._coin_min(market)),
            reason=OrderReason.SELL_ALL,
        )
        if decision.plan is None:
            self._sell_all_failed(market, SellAllResult.LEFT, qty, TOO_SMALL_NAME)
            return
        result = self._place(decision.plan, None)
        if isinstance(result, str):
            self._sell_all_failed(market, SellAllResult.RETRYING, qty, result)
            return
        if self._mode is Mode.LIVE:
            self._engine_read(STAGE_SELL_ALL, market)
        left = holding_of(self._trade_account(), market)
        if left is None or left.qty <= 0:
            self._sell_all_state[market] = (SellAllResult.DONE, None)
        else:
            self._sell_all_failed(market, SellAllResult.RETRYING, sellable_qty(left), "")

    def _sell_all_failed(
        self, market: str, result: SellAllResult, qty: Decimal, error_name: str
    ) -> None:
        """코인 하나의 팔지 못함: 결과 글을 남기고 A-25를 누름마다 코인마다 한 번 만든다."""
        self._sell_all_state[market] = (result, qty if result is SellAllResult.LEFT else None)
        if market in self._sell_all_alerted or not error_name:
            return
        self._sell_all_alerted.add(market)
        spec = alert_rules.sell_all_left(market, qty_text(qty), error_name)
        self._emit_alert(spec, True, module=_MODULE)

    def _sell_all_text(self, market: str) -> str | None:
        state = self._sell_all_state.get(market)
        if state is None:
            return None
        result, left = state
        return sell_all_text(result, left)

    # --- 화면의 값 -------------------------------------------------------------------------------

    def _uncanceled_orders(self) -> tuple[str, ...]:
        """오류 멈춤 동안 취소하지 못한 주문의 요약("{코인} {주문 요약}")."""
        items: list[str] = []
        try:
            orders = trade_store.list_open_orders(self._conn, self._mode)
        except StorageError:
            return ()
        for order in orders:
            items.append(f"{coin_label(order.market)} {_summary(order)}")
        return tuple(items)

    def _post_pause_views(self) -> None:
        self._post_trade_status()
        self._post_round_now()
        try:  # 켜는 조건과 키를 고칠 수 있는지가 멈춤 상태를 본다(M5 명세 3.7.2, 3.8)
            self._post_live_views()
        except Exception as exc:
            log.warning("실거래 상태를 보내지 못했습니다: %s", type(exc).__name__)

    def _post_trade_status(self) -> None:
        trade: TradeSettings | None = self._trade()
        if trade is None or self._conn is None:
            return
        state = self._pause
        exchange = state.error and state.error_cause is ErrorPauseCause.EXCHANGE
        now: datetime = self._clock.now()
        self._post(
            TradeStatusView(
                user_paused=state.user,
                error_paused=state.error,
                error_cause=state.error_cause,
                error_detail=state.error_detail,
                stop_loss_off=stop_loss_off(state, trade.pause_stop_loss),
                starting_step=self._starting_step,
                next_round_utc=self._next_due,
                blocked_until=self._blocked_until if exchange else None,
                uncanceled=self._uncanceled_orders() if state.error else (),
                as_of=now,
                mode=self._mode,
            )
        )

    def _stop_check_allowed(self) -> bool:
        trade: TradeSettings | None = self._trade()
        if trade is None:
            return False
        return may_stop_check(self._pause, trade.pause_stop_loss)
