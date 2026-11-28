"""3단계 잇기 1: 설정의 매매 칸, 회차의 주문, 1분 점검(M3 명세 3.5, 3.7~3.9, 3.11, 3.14).

자료 스레드(`DataWorker`)가 상속한다. 이 모듈의 메서드는 모두 자료 스레드에서만 부른다. 계산과
판정은 `trading/`의 부품을 부르기만 하고(`decide`, `check_buy`, `check_cancel_cap`, 모의 체결,
가상 계정의 셈, 손절 판정, 설정의 받지 않는 값), DB는 `storage/`의 함수로만 만진다(3.2).

- 회차의 주문(3.5.1의 4, 6, 7): 정리(걸린 주문의 취소 전 판정과 취소, 유의·주의를 위한 코인 목록)
  → 코인마다 세션이 끝난 차례로 계정 읽기(가상 계정, 새 현재가, 호가 단위) → `decide` →
  `check_cancel_cap`, 손절 뒤라 새로 사지 않음, `check_buy` → 최우선 호가를 읽고 식별값을 먼저
  저장한 뒤 거래소 경계에 냄(바로 체결이면 경계가 체결을 쓴다) → 보고서의 `order_id`·
  `no_order_reason`·`elapsed_order_s`.
- 1분 점검(3.9): 코인마다 모의 체결 판정 → 현재가 읽기 → 손절 판정 → 필요하면 손절 매도. 켤 때도
  같은 점검을 한 번 하고(소급 판정. 3.7.4), 걸린 주문의 판정이 끝나기 전에는 회차를 돌리지 않는다.
- 거래소 경계(M5 명세 4절 15번): 모의매매의 주문, 체결 판정, 취소는 `select_exchange`가 고른
  `PaperExchange`(`ExchangePort`)로 한다. 가상 계정·주문·체결은 `SqlitePaperStore`로 읽고 쓴다.
  3단계의 동작과 기록은 그대로다: 최우선 호가는 주문 줄을 쓰기 전에 한 번 읽어 `place`에 넘기고
  (읽지 못하면 줄이 없다), 공개 시세를 요청하기 전의 검사(호가 소스가 없음, 일시 차단)와 그 실패의
  처리, 체결을 쓰지 못한 때의 A-29는 이 모듈이 한다(`_PaperQuotes`, `_PaperStore`).
- 멈춤과 오류 멈춤, 전량 매도, 상태 띠의 켜는 중 값은 `pause_flow`(25번)다. 이 모듈은 그 상태를 보고
  멈추기만 한다(오류 멈춤이면 1분 점검을 하지 않고, 사용자 멈춤이면 '손절 중지'일 때 하지 않는다).

`DataWorker`가 갖춰야 하는 것: `_conn`, `_clock`, `_settings`, `_client`, `_rules`, `_stop`,
`_post`,
`_emit_alert`, `_emit_save_failed`, `_emit_unexpected`, `_emit_markets_failed`, `_is_blocked`,
`_set_block`, `_active_round`, `_next_due`, `_markets_listed`.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any, Final

from coin_report_trader.ai.csv_files import decimal_text, utc_text, write_csv
from coin_report_trader.ai.inputs import FINAL_TRADES_COLUMNS
from coin_report_trader.core import (
    MIN_ORDER_FALLBACK,
    ORDER_KIND_TITLES,
    ORDER_SIDE_TITLES,
    PAPER_FEE_RATE,
    SAVE_TARGET_SETTINGS,
    BelowStopView,
    CoinTradeView,
    Direction,
    FillRecord,
    Mode,
    NoOrderReason,
    OrderKind,
    OrderPlan,
    OrderReason,
    OrderRecord,
    OrderRules,
    OrderSide,
    OrderState,
    PaperAccount,
    PaperHolding,
    QuoteError,
    ReportState,
    RoundNowView,
    RoundStage,
    SaveResult,
    SettingsView,
    StorageError,
    TodayView,
    TradeMethod,
    TradeSettings,
    coin_label,
)
from coin_report_trader.core.clock import KST
from coin_report_trader.core.errors import ExchangeError
from coin_report_trader.core.exchange import OrderSnapshot
from coin_report_trader.core.texts import (
    BELOW_STOP_PRICE_UNREAD,
    CODE_NO_AVG,
    CODE_PRICE_GUARD,
    CODE_TOO_SMALL,
    ERROR_BLOCKED,
    ERROR_STORAGE,
    STAGE_ORDER,
    STAGE_ORDER_SAVE,
    STAGE_RECONCILE,
    STAGE_STOP_LOSS,
    kst_when_text,
    order_summary_text,
    trade_balance_line,
)
from coin_report_trader.storage import reports as report_store
from coin_report_trader.storage import trading as trade_store
from coin_report_trader.storage.backups import DAY_START_HOUR, backup_day
from coin_report_trader.storage.paper_store import SqlitePaperStore
from coin_report_trader.trading.live import live_krw_enough, select_exchange
from coin_report_trader.trading.orders import (
    BuyContext,
    check_buy,
    check_cancel_cap,
    check_trade_settings,
    coin_ratio_key,
    decide,
    error_branch,
    new_uid,
    widen_price,
)
from coin_report_trader.trading.paper import (
    holding_of,
    is_dust,
    sellable_qty,
    unlock_order,
)
from coin_report_trader.trading.risk.pause import may_run_round, may_stop_check
from coin_report_trader.trading.risk.stop_loss import (
    StopCheck,
    stop_line,
    stop_loss_check,
    stop_loss_plan,
)
from coin_report_trader.trading.upbit.errors import ErrorBranch, branch_of

from . import alert_rules

log = logging.getLogger(__name__)

_MODULE: Final = __name__.removeprefix("coin_report_trader.")
STOP_CHECK_INTERVAL: Final = 60.0
"""1분 점검의 간격(초. `STOP_CHECK_SECONDS`)."""
_ZERO: Final = Decimal(0)
_HUNDRED: Final = Decimal(100)
LIVE_NO_KRW_REASON: Final = "원화 잔고가 모자람"
"""실거래에서 원화 잔고가 모자라 사지 않은 까닭의 글(M5 명세 3.6.1)."""
_BUY_BLOCK_REASONS: Final = (
    NoOrderReason.DAILY_LOSS_LIMIT,
    NoOrderReason.CAUTION,
    NoOrderReason.SETTINGS_CHECK,
)
"""코인 칸의 "새 매수 막힘"에 보이는, 회차와 상관없이 지금 걸려 있는 까닭(3.17.2)."""


@dataclass
class _OrderTarget:
    """보고서 하나의 주문을 하는 데 쓰는 값(회차의 6)."""

    report_id: int
    market: str
    round_close: datetime
    next_close: datetime
    elapsed_report_s: int | None


def _day_bounds(now: datetime) -> tuple[datetime, datetime]:
    """그날(한국 09:00 경계. 2절)의 처음과 끝(UTC가 붙은 때)."""
    day = backup_day(now)
    start = datetime.combine(day, time(DAY_START_HOUR), tzinfo=KST)
    return start, start + timedelta(days=1)


class _QuotesHeld(Exception):
    """모의매매의 거래소 경계가 공개 시세를 요청하려 할 때 3단계 그대로 이 모듈이 멈춘 것: 요청하기
    전의 검사에 걸렸거나(`error`가 `None`: 호가 소스가 없음, 일시 차단) 요청이 실패했다(`error`).
    `PaperExchange`는 쓰기 전에 공개 시세를 읽으므로, 이 예외가 지나갈 때 경계는 아무것도 쓰지
    않았다. 이 모듈이 받아 3단계의 차례로 다룬다(실패면 `_quote_failed`)."""

    def __init__(self, error: QuoteError | None) -> None:
        super().__init__("공개 시세를 요청하지 않음" if error is None else error.error_name)
        self.error = error


class _FillNotWritten(StorageError):
    """모의 체결 한 줄(체결, 주문의 `FILLED`, 가상 계정)을 쓰지 못했다(M3 명세 3.7.2: A-29 "주문
    저장"과 오류 멈춤). 처음 난 `StorageError`는 `__cause__`다. 다른 쓰기와 읽기의 실패는 3단계처럼
    그대로 `StorageError`다."""


class _PaperQuotes:
    """`PaperExchange`에 넘기는 공개 시세(최우선 호가와 1분봉). 3단계의 요청 앞 검사를 그대로 한다:
    호가 소스가 없거나 공개 시세를 요청하면 안 되면 요청하지 않고, 요청이 실패하면 그 오류를
    `_QuotesHeld`에 담아 이 모듈로 올린다."""

    def __init__(self, worker: "TradeMixin") -> None:
        self._worker = worker

    def _client(self) -> Any:
        worker = self._worker
        if not worker._has_book or worker._is_blocked():
            raise _QuotesHeld(None)
        return worker._client

    def read_best_prices(self, market: str) -> tuple[Decimal, Decimal]:
        client = self._client()
        try:
            ask, bid = client.read_best_prices(market)
        except QuoteError as exc:
            raise _QuotesHeld(exc) from exc
        return Decimal(ask), Decimal(bid)

    def fetch_minutes(self, market: str, *, since: datetime, until: datetime) -> Any:
        client = self._client()
        try:
            return client.fetch_minutes(market, since=since, until=until)
        except QuoteError as exc:
            raise _QuotesHeld(exc) from exc

    def __repr__(self) -> str:
        return "_PaperQuotes()"


class _PaperStore(SqlitePaperStore):
    """모의매매의 `PaperStore`. 체결 쓰기의 실패만 `_FillNotWritten`으로 가른다(3단계의 A-29)."""

    def __init__(self, conn: Any) -> None:
        super().__init__(conn)
        self.conn = conn

    def apply_fill(self, fill: FillRecord, account: PaperAccount) -> FillRecord:
        try:
            return super().apply_fill(fill, account)
        except StorageError as exc:
            raise _FillNotWritten(str(exc)) from exc


def _summary(order: OrderRecord | OrderPlan) -> str:
    return order_summary_text(
        ORDER_KIND_TITLES[OrderKind(order.kind)],
        ORDER_SIDE_TITLES[OrderSide(order.side)],
        order.price,
        order.qty,
    )


class TradeMixin:
    """설정의 매매 칸, 회차의 주문, 1분 점검. `DataWorker`의 일부다."""

    _conn: Any
    _clock: Any
    _settings: Any
    _client: Any
    _rules: Any
    _stop: Any
    _active_round: Any
    _next_due: Any
    _markets_listed: Any
    _post: Callable[..., None]
    _emit_alert: Callable[..., bool]
    _emit_save_failed: Callable[..., None]
    _emit_unexpected: Callable[..., None]
    _emit_markets_failed: Callable[..., None]
    _is_blocked: Callable[[], bool]
    _set_block: Callable[..., None]
    # `PauseMixin`(pause_flow)이 채우는 이름
    _pause: Any
    _sell_all_text: Callable[[str], str | None]
    _retry_sell_all: Callable[[], None]
    _restore_pauses: Callable[[], None]
    _start_checks: Callable[..., None]
    _post_pause_views: Callable[[], None]
    _post_performance: Callable[[], None]
    # 5단계(`live_flow`, `live_engine`)가 채우는 이름
    _mode: Mode
    _live_account: Any
    _live_rules: dict[str, Any]
    _live_limits_account: Callable[[], PaperAccount]
    _engine_read: Callable[..., Any]
    _live_rules_for: Callable[[str], Any]
    _live_place: Callable[..., tuple[int | None, str | None]]
    _live_judge: Callable[[OrderRecord], bool | None]
    _live_cancel: Callable[..., bool]
    _live_unconfirmed: Callable[..., tuple[OrderRecord, ...]]
    _live_judge_unconfirmed: Callable[[], None]
    _live_close_unsent: Callable[[], None]
    _live_lists: Callable[[], bool]
    _live_startup: Callable[[], None]
    _exchange: Any

    def _init_trade(self, min_order: Decimal | None) -> None:
        self._min_order: Decimal = MIN_ORDER_FALLBACK if min_order is None else min_order
        self._has_book = False
        """공개 시세 소스가 `BookSource`를 갖춘다(`_open`이 정한다)."""
        self._ticks: dict[str, Decimal] = {}
        """코인마다 한 회차에 한 번 읽은 호가 단위(3.6). 1분 점검도 있으면 쓴다."""
        self._cautions: dict[str, bool] = {}
        """이 실행에서 마지막으로 받은 유의·주의 표시(3.8.2)."""
        self._trade_ready = False
        """켤 때 걸린 주문의 소급 판정과 손절 점검을 마쳤다(그 전에는 회차를 돌리지 않는다)."""
        self._last_quote_error = ""
        """현재가를 마지막으로 읽지 못한 까닭의 오류 이름(A-25)."""
        self._stop_check_mono: float | None = None
        self._sold_canceled: set[str] = set()
        """이번 회차의 정리에서 매도 주문을 취소한 코인(손절만 남음. 3.14.1)."""
        self._round_stopped: set[str] = set()
        """이번 회차 도중 손절이 난 코인(그 회차의 판단을 버린다. 3.5.1)."""
        self._round_canceled: dict[str, list[tuple[Decimal, datetime]]] = {}
        """이번 회차의 정리에서 취소한 판단 주문의 가격과 취소한 때(`final_trades.csv`. 3.5.4)."""
        self._cancel_pending: set[int] = set()
        """판정하지 못해 취소가 확인되지 않은 주문(`OPEN` 그대로. 3.7.2)."""
        self._prices: dict[str, tuple[Decimal, datetime]] = {}
        self._directions: dict[str, tuple[Direction, int]] = {}
        self._settings_blocked = False
        """저장된 설정이 지금의 최소 주문 금액으로 검사에 걸린다(3.11.2의 마지막 줄, A-15)."""
        self._round_stage: RoundStage | None = None
        self._round_number: int | None = None
        self._tick_error_name = NoOrderReason.NO_PRICE_OR_TICK.value
        """호가 단위를 마지막으로 읽지 못한 까닭의 오류 이름(주문 때의 A-2)."""
        self._paper_made: tuple[Any, _PaperStore, Any] | None = None
        """(연결, 저장 어댑터, `PaperExchange`). 연결마다 하나를 만든다(`_paper_engine`)."""

    # --- 묻기 ---------------------------------------------------------------------------------

    def _trade(self) -> TradeSettings | None:
        """3단계로 처음 설정을 마친 저장된 매매 칸. 아니면 `None`(주문과 손절이 없다).

        실거래에서는 실거래 설정으로 바꾼 값이다(`LiveSettings.apply`. M5 명세 3.6.1, 3.7.5).
        """
        settings = self._settings
        if settings is None or not settings.first_setup_done:
            return None
        trade: TradeSettings | None = settings.trade
        live = settings.live
        if (
            trade is not None
            and self._mode is Mode.LIVE
            and live is not None
            and live.budget is not None
            and live.daily_loss_limit is not None
        ):
            return live.apply(trade)
        return trade

    def _trade_coins(self) -> tuple[str, ...]:
        settings = self._settings
        return () if settings is None else tuple(settings.coins)

    def _db_account(self) -> PaperAccount:
        account = trade_store.read_paper_account(self._conn, Mode.PAPER)
        if account is None:
            raise StorageError("가상 계정이 없습니다")
        return account

    def _trade_account(self) -> PaperAccount:
        """판단, 한도, 손절에 쓰는 계정: 모의매매는 가상 계정, 실거래는 매매 엔진이 마지막으로 읽은
        실제 계정(M5 명세 3.6.1)."""
        if self._mode is Mode.LIVE:
            return self._live_limits_account()
        return self._db_account()

    def _paper_engine(self) -> tuple[_PaperStore, Any]:
        """모의매매의 저장 어댑터와 거래소 경계(`select_exchange`가 고른 `PaperExchange`. M5 명세
        3.4, 15번). 연결마다 하나다. 모의매매이면 `_exchange`도 그것이다."""
        made = self._paper_made
        if made is None or made[0] is not self._conn:
            store = _PaperStore(self._conn)
            exchange = select_exchange(
                Mode.PAPER,
                paper_store=store,
                client=None,
                allowed=False,
                quotes=_PaperQuotes(self),  # type: ignore[arg-type]
                clock=self._clock,
                min_order=self._min_order,
            )
            made = self._paper_made = (self._conn, store, exchange)
        if self._mode is Mode.PAPER:
            self._exchange = made[2]
        return made[1], made[2]

    def _coin_min(self, market: str) -> Decimal:
        """그 코인의 최소 주문 금액(실거래는 그 코인의 주문 가능 정보. M5 명세 3.6.1)."""
        if self._mode is Mode.LIVE:
            rules = self._live_rules.get(market)
            if rules is not None:
                return Decimal(rules.min_total)
        return self._order_min()

    def _order_min(self) -> Decimal:
        """최소 주문 금액: 실거래는 주문 가능 정보의 값(읽은 코인 가운데 가장 큰 것), 모의매매와
        읽지 못했을 때는 지금 값(M5 명세 3.1)."""
        if self._mode is Mode.LIVE and self._live_rules:
            return max(rules.min_total for rules in self._live_rules.values())
        return self._min_order

    # --- 설정 저장(3.11) ------------------------------------------------------------------------

    def _check_trade_save(
        self,
        current: SettingsView,
        coins: tuple[str, ...],
        trade: TradeSettings | None,
        accept_below_stop: tuple[str, ...],
    ) -> bool:
        """저장하기 전의 매매 칸 검사. 저장해도 되면 참. 아니면 결과를 이미 보냈다.

        - `check_trade_settings`에 걸리면 `SaveResult.field_errors`(3.11.2).
        - 새로 고른 코인에 손절선 아래인 가상 보유가 있고 `accept_below_stop`에 없으면
          `BelowStopView`(현재가를 그때 읽는다. 읽지 못하면 그 코인 칸 아래의 글. 3.11.4).
        """
        if trade is None:
            return True
        errors = check_trade_settings(trade, coins, self._min_order)
        if errors:
            self._post(SaveResult(SAVE_TARGET_SETTINGS, False, None, errors))
            return False
        added = [coin for coin in coins if coin not in current.coins]
        if not added or not current.first_setup_done:
            return True
        account = trade_store.read_paper_account(self._conn, Mode.PAPER)
        if account is None:
            return True
        for market in added:
            if market in accept_below_stop:
                continue
            holding = holding_of(account, market)
            if holding is None or holding.qty <= 0:
                continue
            line = stop_line(holding.avg_price, trade.stop_ratio)
            if line is None or holding.avg_price is None:
                continue
            price = self._read_quote_price(market)
            if price is None:
                field = {coin_ratio_key(market): BELOW_STOP_PRICE_UNREAD}
                self._post(SaveResult(SAVE_TARGET_SETTINGS, False, None, field))
                return False
            if price <= line:
                self._post(BelowStopView(market, holding.avg_price, price, line))
                return False
        return True

    def _after_trade_save(self, before: SettingsView, after: SettingsView, first: bool) -> None:
        """저장이 성공한 뒤의 매매 쪽 일: 설정 바뀜(3.16.2), 뺀 코인의 주문 취소와 A-16(3.11.4),
        저장된 설정의 검사(A-15), 화면의 값."""
        trade = self._trade()
        if trade is None:
            return
        now = self._clock.now()
        if first:
            for market in after.coins:
                self._setting_event(trade_store.SETTING_COIN_ADDED, market, None, now)
                self._setting_event(trade_store.SETTING_METHOD_CHANGED, market, trade.method, now)
        else:
            for market in after.coins:
                if market not in before.coins:
                    self._setting_event(trade_store.SETTING_COIN_ADDED, market, None, now)
            for market in before.coins:
                if market not in after.coins:
                    self._setting_event(trade_store.SETTING_COIN_REMOVED, market, None, now)
                    self._remove_coin(market)
            old = before.trade
            if old is not None and old.method != trade.method:
                for market in after.coins:
                    self._setting_event(
                        trade_store.SETTING_METHOD_CHANGED, market, trade.method, now
                    )
            if before.candle_count is not None and before.candle_count != after.candle_count:
                for market in after.coins:
                    self._setting_event(
                        trade_store.SETTING_CANDLE_COUNT_CHANGED,
                        market,
                        str(after.candle_count),
                        now,
                    )
        self._check_saved_trade(post_result=False)
        self._post_trade_views()

    def _setting_event(self, kind: str, market: str | None, value: str | None, now: datetime):
        try:
            trade_store.insert_setting_event(
                self._conn, mode=self._mode, kind=kind, market=market, value=value, at=now
            )
        except StorageError as exc:
            log.warning("설정 바뀜을 쓰지 못했습니다: %s", exc)

    def _remove_coin(self, market: str) -> None:
        """보유 중인 코인을 뺌: 그 코인의 프로그램 주문을 취소하고 A-16(3.11.4). 보유는 남는다."""
        for order in trade_store.list_open_orders(self._conn, self._mode, market):
            self._cancel_order(order)
        if self._mode is Mode.LIVE:
            self._engine_read(STAGE_ORDER, market)
            account: PaperAccount | None = (
                None if self._live_account is None else self._live_limits_account()
            )
        else:
            account = trade_store.read_paper_account(self._conn, Mode.PAPER)
        holding = None if account is None else holding_of(account, market)
        if holding is not None and holding.qty > 0:
            self._emit_alert(alert_rules.coin_removed(market), True, module=_MODULE)

    def _check_saved_trade(self, *, post_result: bool) -> None:
        """저장된 설정이 지금의 최소 주문 금액으로 검사에 걸리는지(3.11.2의 마지막 줄). 걸리면 새
        매수를 막고 A-15(다시 통과하기 전에는 한 번). `post_result`가 참이면(켤 때) 걸린 칸의 글을
        담은 `SaveResult`를 보낸다."""
        trade = self._trade()
        if trade is None:
            return
        errors = check_trade_settings(trade, self._trade_coins(), self._order_min())
        self._settings_blocked = bool(errors)
        if not errors:
            self._rules.a15_cleared()
            return
        if post_result:
            self._post(SaveResult(SAVE_TARGET_SETTINGS, False, None, errors))
        self._emit_alert(alert_rules.min_order_changed(), self._rules.want_a15(), module=_MODULE)

    # --- 켤 때(2의 3, 3.7.4, 3.8.3) -------------------------------------------------------------

    def _trade_startup(self) -> None:
        """끊긴 회차 정리 뒤: 멈춤 이어 가기 → `sent` 0인 주문 닫기 → 소급 판정과 손절 점검(켜는
        중의 두 칸) → 저장된 설정의 검사 → 화면의 값(켤 때 차례 5)."""
        self._trade_ready = False
        if self._trade() is None:
            self._stop_check_mono = None
            return
        self._restore_pauses()
        self._live_startup()  # 모드 이어 가기와 키(M5 명세 3.7.2의 끝)
        self._close_unsent()
        self._check_saved_trade(post_result=True)
        if self._pause.error:  # 오류 멈춤은 풀 때까지 대조도 손절 점검도 없다(3.10.2)
            self._stop_check_mono = self._clock.monotonic() + STOP_CHECK_INTERVAL
            self._post_pause_views()
            self._post_trade_views()
            return
        self._start_checks()

    def _close_unsent(self) -> None:
        """켤 때 `sent` 0인 줄(보내기 전에 꺼짐)을 `NOT_SENT`로 닫고 묶음을 푼다(3.8.3)."""
        if self._mode is Mode.LIVE:
            self._live_close_unsent()  # 실거래는 바로 닫지 않고 조회한다(M5 명세 3.6.3)
        now = self._clock.now()
        for order in trade_store.list_unsent_orders(self._conn, Mode.PAPER):
            account = trade_store.read_paper_account(self._conn, Mode.PAPER)
            if account is not None and order.kind is OrderKind.LIMIT and _locked(account, order):
                trade_store.write_paper_account(
                    self._conn, Mode.PAPER, unlock_order(account, order)
                )
            trade_store.update_order(
                self._conn, order.order_id, state=OrderState.NOT_SENT, closed_at=now
            )

    # --- 1분 점검(3.9) ----------------------------------------------------------------------------

    def _minute_due(self, now_mono: float) -> bool:
        due = self._stop_check_mono
        return due is not None and now_mono >= due

    def _minute_check(self) -> None:
        """코인마다(설정 목록의 차례) 모의 체결 판정 → 현재가 읽기 → 손절 판정 → 손절 매도. 끝에
        하루 손실 한도를 보고 화면의 값을 보낸다. 판정이 모두 끝났으면 회차를 돌릴 수 있다.

        오류 멈춤이면 아무것도 하지 않고(3.10.2), 사용자 멈춤에서 '손절 중지'이면 점검은 하지 않고
        남은 취소와 전량 매도의 다시 팔기만 한다(3.9, 3.10.3).
        """
        self._stop_check_mono = self._clock.monotonic() + STOP_CHECK_INTERVAL
        trade = self._trade()
        if trade is None or self._conn is None:
            return
        if self._pause.error:
            self._post_trade_views()
            return
        if not may_stop_check(self._pause, trade.pause_stop_loss):
            self._retry_pending_cancels()
            self._retry_sell_all()
            self._post_trade_views()
            return
        judged = True
        coins = self._trade_coins()
        if self._mode is Mode.LIVE:
            judged = self._live_minute(coins, trade)
            if judged is None:
                return
        for market in coins if self._mode is Mode.PAPER else ():
            if self._stop.is_set():
                return
            judged = self._judge_market(market) and judged
            price = self._read_quote_price(market)
            if price is None:
                continue
            self._stop_check(market, price, trade)
        for order in trade_store.list_open_orders(self._conn, self._mode):
            if order.market not in coins:  # 뺀 코인의 걸린 주문(생기지 않지만 판정은 한다)
                judged = self._judge_orders((order,)) and judged
        self._check_daily_loss(trade)
        self._retry_sell_all()
        self._trade_ready = self._trade_ready or judged
        self._post_trade_views()

    def _live_minute(self, coins: tuple[str, ...], trade: TradeSettings) -> bool | None:
        """실거래의 1분 점검: 코인마다 걸린 주문의 조회(체결 판정) → 확인 안 됨 주문의 조회 → 매매
        엔진의 계정 읽기(외부 변동) → 코인마다 현재가 읽기와 손절 판정(M5 명세 3.6.4, 3.6.6). 모두
        판정했으면 참, 멈춰야 하면 `None`."""
        judged = True
        for market in coins:
            if self._stop.is_set():
                return None
            judged = self._judge_market(market) and judged
        self._live_judge_unconfirmed()
        account = self._engine_read(STAGE_STOP_LOSS)
        if isinstance(account, str):
            return False
        for market in coins:
            if self._stop.is_set() or self._pause.error:
                return None
            price = self._read_quote_price(market)
            if price is None:
                continue
            self._stop_check(market, price, trade)
        return judged

    def _retry_pending_cancels(self) -> None:
        """멈춘 동안에도 취소가 확인되지 않은 주문은 다시 취소한다(3.7.2)."""
        for order_id in tuple(self._cancel_pending):
            fresh = trade_store.read_order(self._conn, order_id)
            if fresh is not None and fresh.state is OrderState.OPEN:
                self._cancel_order(fresh)
            else:
                self._cancel_pending.discard(order_id)

    def _judge_market(self, market: str) -> bool:
        orders = trade_store.list_open_orders(self._conn, self._mode, market)
        done = self._judge_orders(orders)
        for order in orders:  # 남은 취소(3.7.2): 판정과 함께 다시 한다
            if order.order_id in self._cancel_pending:
                fresh = trade_store.read_order(self._conn, order.order_id)
                if fresh is not None and fresh.state is OrderState.OPEN:
                    self._cancel_order(fresh)
                else:
                    self._cancel_pending.discard(order.order_id)
        return done

    def _judge_orders(self, orders: tuple[OrderRecord, ...]) -> bool:
        """걸린 지정가 주문마다 1분봉으로 판정한다(3.7.2). 모두 판정했으면 참. 1분봉을 받지
        못하면 그 주문은 다음 1분 점검에 다시 한다."""
        ok = True
        for order in orders:
            if order.state is not OrderState.OPEN:
                continue
            if order.mode is Mode.LIVE:  # 실거래는 시장가도 조회한다(M5 명세 3.6.4)
                if self._live_judge(order) is None:
                    ok = False
                continue
            if order.kind is not OrderKind.LIMIT:
                continue
            if self._judge_one(order) is None:
                ok = False
        return ok

    def _judge_one(self, order: OrderRecord) -> bool | None:
        """주문 하나를 지금 바로 앞의 완성된 1분봉까지 판정한다(거래소 경계의 `query`). 체결이면 참,
        걸린 그대로면 거짓, 1분봉을 받지 못하면 `None`. 실거래는 거래소에 조회한다(M5 명세
        3.6.4)."""
        if order.mode is Mode.LIVE:
            return self._live_judge(order)
        _, exchange = self._paper_engine()
        try:
            snapshot: OrderSnapshot | None = exchange.query(order.uid)
        except _QuotesHeld as held:
            if held.error is not None:
                self._quote_failed(order.market, STAGE_RECONCILE, held.error)
            return None
        except ExchangeError as exc:
            self._quote_failed(order.market, STAGE_RECONCILE, exc)
            return None
        except _FillNotWritten as exc:
            self._paper_fill_failed(order, exc)
            return True
        if snapshot is None:
            return None
        if snapshot.state is OrderState.FILLED:
            self._paper_filled(order, snapshot)
            return True
        return False

    def _quote_failed(
        self, market: str | None, stage: str, exc: QuoteError | ExchangeError
    ) -> None:
        """정해진 알림이 없는 일의 공개 시세 실패(3.8.4): 일시 차단은 차단을 걸고, 목록에 없는
        오류는 A-24(같은 코인, 같은 오류 이름은 60분에 한 번), 잠깐의 문제는 알림이 없다."""
        branch = error_branch(exc.error_name)
        if branch is ErrorBranch.BLOCKING:
            self._set_block(exc.retry_after)
        elif branch is ErrorBranch.UNLISTED:
            wanted = self._rules.want_a24(market, exc.error_name, self._clock.monotonic())
            spec = alert_rules.unknown_exchange(market, stage, exc.error_name)
            self._emit_alert(spec, wanted, exc=exc, module=_MODULE)

    def _read_quote_price(self, market: str) -> Decimal | None:
        """현재가를 읽는다(1분 점검과 설정 저장). 읽지 못하면 `None`(알림 없음. 3.9)."""
        if self._is_blocked():
            self._last_quote_error = ERROR_BLOCKED
            return None
        try:
            price: Decimal = self._client.read_price(market)
        except QuoteError as exc:
            self._last_quote_error = str(exc.error_name)
            if exc.error_name == ERROR_BLOCKED:
                self._set_block(exc.retry_after)
            return None
        self._prices[market] = (price, self._clock.now())
        return price

    def _stop_check(self, market: str, price: Decimal, trade: TradeSettings) -> None:
        account = self._trade_account()
        holding = holding_of(account, market)
        check = stop_loss_check(holding, price, trade)
        if check is StopCheck.NO_HOLDING:
            self._rules.a3_cleared(market)
            self._rules.a8_cleared(market)
            self._rules.a9_cleared(market)
            return
        if check is StopCheck.NO_AVG:
            spec = alert_rules.no_avg_price(market, STAGE_STOP_LOSS)
            self._emit_alert(spec, self._rules.want_a9(market), module=_MODULE)
            return
        self._rules.a9_cleared(market)
        if check is StopCheck.REACHED:
            self._stop_loss(market, price, trade)

    def _stop_loss(self, market: str, price: Decimal, trade: TradeSettings) -> None:
        """손절 매도의 차례(5.4절): 걸린 주문마다 취소 전 판정과 취소 → 계정을 다시 읽음 →
        `stop_loss_plan` → 식별값을 먼저 저장하고 시장가로 냄(3.8.3)."""
        for order in trade_store.list_open_orders(self._conn, self._mode, market):
            self._cancel_order(order)
        if self._mode is Mode.LIVE:  # 취소한 뒤 계정을 다시 읽는다(M5 명세 3.6.1)
            read = self._engine_read(STAGE_STOP_LOSS, market)
            if isinstance(read, str):
                wanted = self._rules.want_a8(market, self._clock.monotonic())
                self._emit_alert(alert_rules.stop_failed(market, read), wanted, module=_MODULE)
                return
        holding = holding_of(self._trade_account(), market)
        if holding is None or holding.qty <= 0:
            self._rules.a8_cleared(market)
            return
        tick = self._tick_for(market)
        rules = OrderRules(tick if tick is not None else _ZERO, self._coin_min(market))
        decision = stop_loss_plan(holding, price, trade, rules)
        if decision.plan is None:
            if decision.alert == CODE_TOO_SMALL:
                self._emit_alert(
                    alert_rules.too_small(market), self._rules.want_a3(market), module=_MODULE
                )
            return
        self._rules.a3_cleared(market)
        result = self._place(decision.plan, None)
        if isinstance(result, str):
            wanted = self._rules.want_a8(market, self._clock.monotonic())
            self._emit_alert(alert_rules.stop_failed(market, result), wanted, module=_MODULE)
            return
        self._rules.a8_cleared(market)
        if self._active_round is not None and self._round_stage is not RoundStage.RECORD:
            self._round_stopped.add(market)

    def _check_daily_loss(self, trade: TradeSettings) -> bool:
        """하루 손실 한도(5.5절): 닿았으면 프로그램이 건 매수 주문을 취소하고 A-12(하루에 한 번)."""
        now = self._clock.now()
        start, end = _day_bounds(now)
        pnl = trade_store.realized_pnl_between(self._conn, self._mode, start=start, end=end)
        if pnl > -trade.daily_loss_limit:
            return False
        for order in trade_store.list_open_orders(self._conn, self._mode):
            if order.side is OrderSide.BUY:
                self._cancel_order(order)
        self._emit_alert(
            alert_rules.daily_loss(), self._rules.want_a12(start.date()), module=_MODULE
        )
        return True

    # --- 주문 하나의 동작 ------------------------------------------------------------------------

    def _tick_for(self, market: str) -> Decimal | None:
        """이 회차에 읽은 호가 단위. 없으면 읽는다. 읽지 못하면 `None`."""
        tick = self._ticks.get(market)
        if tick is not None:
            return tick
        if not self._has_book or self._is_blocked():
            return None
        try:
            tick = self._client.read_tick_size(market)
        except QuoteError as exc:
            self._tick_error_name = str(exc.error_name)
            if exc.error_name == ERROR_BLOCKED:
                self._set_block(exc.retry_after)
            return None
        self._ticks[market] = tick
        return tick

    def _cancel_order(self, order: OrderRecord, *, by_round: bool = False) -> bool:
        """취소 전 판정(3.7.2) 뒤 취소한다. 취소했으면 참. 체결로 판정되면 취소하지 않고 체결로
        쓴다. 판정하지 못하면 취소가 확인되지 않은 것으로 둔다(`OPEN` 그대로). 실거래는 거래소에
        조회한 뒤 취소한다(M5 명세 3.6.4)."""
        if order.mode is Mode.LIVE:
            return self._live_cancel(order, by_round=by_round)
        if order.kind is OrderKind.LIMIT:
            judged = self._judge_one(order)
            if judged is None:
                self._cancel_pending.add(order.order_id)
                return False
            if judged:
                self._cancel_pending.discard(order.order_id)
                return False
        account = self._db_account()
        if order.kind is OrderKind.LIMIT and _locked(account, order):
            return self._paper_cancel(order, by_round)
        # 묶인 것이 없는 주문(시장가, 넣을 때의 체결을 쓰지 못한 지정가): 풀 것 없이 닫는다(3단계)
        trade_store.update_order(
            self._conn,
            order.order_id,
            state=OrderState.CANCELED,
            closed_at=self._clock.now(),
            canceled_by_round=True if by_round else None,
        )
        self._cancel_pending.discard(order.order_id)
        return True

    def _paper_cancel(self, order: OrderRecord, by_round: bool) -> bool:
        """판정한 뒤의 모의 취소를 거래소 경계로 한다(묶음을 풀고 `CANCELED`). 취소했으면 참."""
        _, exchange = self._paper_engine()
        try:
            snapshot: OrderSnapshot | None = exchange.cancel(order.uid)
        except _QuotesHeld as held:  # 판정한 뒤 분이 바뀌어 다시 판정하려다 멈춤
            if held.error is not None:
                self._quote_failed(order.market, STAGE_RECONCILE, held.error)
            self._cancel_pending.add(order.order_id)
            return False
        except _FillNotWritten as exc:
            self._paper_fill_failed(order, exc)
            self._cancel_pending.discard(order.order_id)
            return False
        if snapshot is None:
            return False
        if snapshot.state is OrderState.FILLED:
            self._paper_filled(order, snapshot)
            return False
        if snapshot.state is not OrderState.CANCELED:  # 판정하지 못해 걸린 그대로
            self._cancel_pending.add(order.order_id)
            return False
        if by_round:
            closed = trade_store.read_order(self._conn, order.order_id)
            trade_store.update_order(
                self._conn,
                order.order_id,
                state=OrderState.CANCELED,
                closed_at=None if closed is None else closed.closed_utc,
                canceled_by_round=True,
            )
        self._cancel_pending.discard(order.order_id)
        return True

    def _place(self, plan: OrderPlan, round_id: int | None) -> int | str:
        """최우선 호가를 읽고, 식별값과 주문 값을 먼저 쓰고(3.8.3) 거래소 경계(`PaperExchange`)에
        낸 뒤 `sent` 1과 상태를 쓴다. 낸 주문의 번호, 내지 못하면 그 오류 이름(최우선 호가를 읽지
        못함)을 돌려준다. 주문을 쓰지 못하면 A-29("주문 저장")와 `StorageError`의 이름이다. 실거래는
        `UpbitExchange`에 낸다(M5 명세 3.6.2)."""
        if self._mode is Mode.LIVE:
            order_id, error = self._live_place(plan, round_id)
            if error is not None or order_id is None:
                return error or ERROR_STORAGE
            return order_id
        best = self._read_book(plan.market)  # 주문 줄을 쓰기 전에 읽는다(읽지 못하면 줄이 없다)
        if isinstance(best, str):
            return best
        store, exchange = self._paper_engine()
        try:
            order = store.insert_order(
                plan,
                mode=Mode.PAPER,
                uid=new_uid(),
                round_id=round_id,
                created_utc=self._clock.now(),
            )
        except StorageError as exc:
            self._emit_save_failed(STAGE_ORDER_SAVE, plan.market, exc, module=_MODULE)
            return ERROR_STORAGE
        self._rules.a29_succeeded(STAGE_ORDER_SAVE, plan.market)
        account = self._db_account()  # A-27의 잔고 줄(묶음은 원화와 보유 수량을 바꾸지 않는다)
        try:
            result = exchange.place(plan, order.uid, best=best)
        except _FillNotWritten as exc:  # 넣을 때의 체결을 쓰지 못함: 낸 주문은 걸린 그대로다
            store.update_order(order.order_id, state=OrderState.OPEN, sent=True)
            self._order_alert(order, plan.amount, account)
            self._paper_fill_failed(order, exc)
            return order.order_id
        snapshot = result.snapshot
        assert snapshot is not None  # 최우선 호가를 넘겼으므로 모의매매는 늘 낸다
        filled = snapshot.state is OrderState.FILLED
        store.update_order(
            order.order_id,
            state=snapshot.state,
            closed_utc=snapshot.trades[0].filled_utc if filled else None,
            sent=True,
        )
        self._order_alert(order, plan.amount, account)
        if filled:
            self._paper_filled(order, snapshot)
        return order.order_id

    def _read_book(self, market: str) -> tuple[Decimal, Decimal] | str:
        if not self._has_book:
            return NoOrderReason.NO_PRICE_OR_TICK.value
        if self._is_blocked():
            return ERROR_BLOCKED
        try:
            ask, bid = self._client.read_best_prices(market)
        except QuoteError as exc:
            if exc.error_name == ERROR_BLOCKED:
                self._set_block(exc.retry_after)
            return str(exc.error_name)
        return Decimal(ask), Decimal(bid)

    def _paper_filled(self, order: OrderRecord, snapshot: OrderSnapshot) -> None:
        """거래소 경계가 쓴 모의 체결(체결 줄, 주문의 `FILLED`, 계정을 한 트랜잭션. M3 명세 3.7.2)의
        뒤: A-29의 되풀이를 풀고 A-28(셋째 줄은 체결 뒤의 가상 계정)."""
        trade = snapshot.trades[0]
        self._rules.a29_succeeded(STAGE_ORDER_SAVE, order.market)
        self._cancel_pending.discard(order.order_id)
        line = self._balance_line(order.market, trade.price * trade.qty, self._db_account())
        side = ORDER_SIDE_TITLES[order.side]
        spec = alert_rules.filled(order.market, side, trade.qty, trade.price, line)
        self._emit_alert(spec, True, module=_MODULE)

    def _paper_fill_failed(self, order: OrderRecord, exc: _FillNotWritten) -> None:
        """모의 체결을 쓰지 못함: 아무것도 바뀌지 않았고 A-29("주문 저장")와 오류 멈춤이다."""
        cause = exc.__cause__ if isinstance(exc.__cause__, StorageError) else exc
        self._emit_save_failed(STAGE_ORDER_SAVE, order.market, cause, module=_MODULE)

    def _order_alert(self, order: OrderRecord, amount: Decimal, account: PaperAccount) -> None:
        line = self._balance_line(order.market, amount, account)
        spec = alert_rules.order_placed(order.market, _summary(order), line)
        self._emit_alert(spec, True, module=_MODULE)

    def _balance_line(self, market: str, amount: Decimal, account: PaperAccount) -> str:
        holding = holding_of(account, market)
        qty = _ZERO if holding is None else holding.qty
        return trade_balance_line(amount, account.krw, coin_label(market), qty)

    # --- 회차(3.5.1) --------------------------------------------------------------------------

    def _round_may_trade(self) -> bool:
        """회차를 시작해도 되는지(1의 더함): 3단계 처음 설정을 마치지 않았으면 회차가 없고(2의 5),
        켤 때의 소급 판정과 손절 점검이 끝나기 전에도 시작하지 않는다."""
        return self._trade() is not None and self._trade_ready and may_run_round(self._pause)

    def _set_stage(self, round_id: int, stage: RoundStage, number: int | None = None) -> None:
        if number is not None:
            self._round_number = number
        if self._round_stage is stage:
            return
        self._round_stage = stage
        try:
            trade_store.set_round_stage(self._conn, round_id, stage)
        except StorageError as exc:
            log.warning("회차의 단계를 쓰지 못했습니다: %s", exc)
        self._post_round_now()

    def _round_cleanup(self, round_id: int, number: int) -> None:
        """정리(3절의 2): 코인 목록을 한 번 받아 유의·주의를 보고, 코인마다 프로그램이 낸 걸린
        주문을 취소한다(취소 전 판정). 매도 주문을 취소한 코인을 기억한다."""
        self._ticks.clear()
        self._sold_canceled.clear()
        self._round_stopped.clear()
        self._round_canceled = {}
        self._set_stage(round_id, RoundStage.CLEANUP, number)
        self._read_cautions()
        if self._mode is Mode.LIVE:
            self._live_lists()  # 체결 대기 주문 목록과 A-7(M5 명세 3.5.2의 표)
        for order in trade_store.list_open_orders(self._conn, self._mode):
            if self._cancel_order(order, by_round=True):
                if order.side is OrderSide.SELL:
                    self._sold_canceled.add(order.market)
                if order.reason is OrderReason.JUDGMENT and order.price is not None:
                    kept = self._round_canceled.setdefault(order.market, [])
                    kept.append((order.price, self._clock.now()))

    def _read_cautions(self) -> None:
        if self._is_blocked():
            return
        try:
            markets = self._client.list_markets()
        except QuoteError as exc:
            if exc.error_name == ERROR_BLOCKED:
                self._set_block(exc.retry_after)
            self._emit_markets_failed(exc.error_name, "코인 목록 요청이 실패했습니다.")
            return
        self._markets_listed = frozenset(market.market for market in markets)
        self._rules.a31_succeeded()
        self._cautions = {market.market: bool(market.caution) for market in markets}

    def _final_trades(self, market: str) -> str:
        """`final_trades.csv`(3.5.4): 최근 체결 가격, 지금 보유 중이면 평균 매수가, 이번 회차의
        정리에서 취소한 판단 주문의 가격."""
        rows: list[list[str]] = []
        fills = trade_store.list_fills(self._conn, self._mode, market=market)
        if fills:
            latest = fills[0]
            rows.append(
                [market, "최근 체결 가격", decimal_text(latest.price), utc_text(latest.filled_utc)]
            )
        if self._mode is Mode.LIVE:
            account = None if self._live_account is None else self._live_limits_account()
        else:
            account = trade_store.read_paper_account(self._conn, Mode.PAPER)
        holding = None if account is None else holding_of(account, market)
        if holding is not None and holding.qty > 0 and holding.avg_price is not None:
            rows.append(
                [
                    market,
                    "평균 매수가",
                    decimal_text(holding.avg_price),
                    utc_text(self._clock.now()),
                ]
            )
        for price, when in self._round_canceled.get(market, ()):
            rows.append(
                [market, "체결되지 않고 끝난 주문 가격", decimal_text(price), utc_text(when)]
            )
        return write_csv(FINAL_TRADES_COLUMNS, rows)

    def _mark_method(self, report_id: int) -> None:
        trade = self._trade()
        if trade is not None:
            trade_store.set_report_method(self._conn, report_id, trade.method)

    def _no_order(self, target: _OrderTarget, reason: NoOrderReason) -> None:
        """주문하지 않은 까닭과 주문까지 걸린 시간(주문이 없으면 보고서까지와 같다)."""
        if self._trade() is None:
            return
        elapsed = target.elapsed_report_s
        if elapsed is None:
            elapsed = int((self._clock.now() - target.round_close).total_seconds())
        text = reason.value
        if reason is NoOrderReason.NO_KRW and self._mode is Mode.LIVE:
            text = LIVE_NO_KRW_REASON
        trade_store.set_report_order(
            self._conn,
            target.report_id,
            order_id=None,
            no_order_reason=text,
            elapsed_order_s=elapsed,
        )

    def _order_for_report(self, target: _OrderTarget, round_id: int) -> None:
        """`OK` 보고서 하나의 주문(3.5.1의 6). 보고서를 쓴 뒤 그 코인만 한다."""
        trade = self._trade()
        if trade is None:
            return
        market = target.market
        view = report_store.read_report(self._conn, target.report_id)
        if view is None or view.state is not ReportState.OK or view.direction is None:
            self._no_order(target, NoOrderReason.NO_JUDGMENT)
            return
        self._directions[market] = (view.direction, view.round_number)
        if market in self._round_stopped:
            self._no_order(target, NoOrderReason.DROPPED_BY_STOP_LOSS)
            return
        self._set_stage(round_id, RoundStage.ACCOUNT)
        if self._mode is Mode.LIVE:
            live = self._live_round_account(market, target, trade)
            if live is None:
                self._no_order(target, NoOrderReason.NO_PRICE_OR_TICK)
                return
            account, min_order, fee_rate = live
        else:
            account, min_order, fee_rate = self._db_account(), self._min_order, PAPER_FEE_RATE
        price = self._read_order_price(market, target)
        tick = None if price is None else self._tick_for(market)
        if price is None or tick is None:
            if price is not None:
                self._order_read_failed(market, target, self._last_tick_error(market))
            self._no_order(target, NoOrderReason.NO_PRICE_OR_TICK)
            return
        self._set_stage(round_id, RoundStage.ORDER)
        rules = OrderRules(tick, min_order)
        holding = holding_of(account, market)
        decision = decide(
            trade.method, view.direction, holding, price, view, trade, fee_rate, rules
        )
        stop_only = market in self._sold_canceled
        if decision.plan is None:
            assert decision.reason is not None
            self._decision_alert(market, decision.alert, trade, view, price, rules, stop_only)
            self._no_order(target, decision.reason)
            return
        plan = decision.plan
        reason = self._buy_blocked(plan, account, trade, rules, market)
        if reason is not None:
            self._limit_alert(market, reason)
            self._no_order(target, reason)
            return
        if self._mode is Mode.LIVE:
            self._live_round_place(plan, round_id, target)
            return
        result = self._place(plan, round_id)
        if isinstance(result, str):
            self._order_read_failed(market, target, result)
            self._no_order(target, NoOrderReason.NO_PRICE_OR_TICK)
            return
        elapsed = int((self._clock.now() - target.round_close).total_seconds())
        trade_store.set_report_order(
            self._conn,
            target.report_id,
            order_id=result,
            no_order_reason=None,
            elapsed_order_s=elapsed,
        )

    def _live_round_account(
        self, market: str, target: _OrderTarget, trade: TradeSettings
    ) -> tuple[PaperAccount, Decimal, Decimal] | None:
        """실거래 회차의 계정 읽기(M5 명세 3.6.1): `read_account()`와 그 코인의
        `read_rules(market)`. 돌려주는 것은 (한도의 꼴의 계정, 최소 주문 금액, 수수료율)이다.
        읽지 못하면 A-2(거래를 계속 막는 오류면 오류 멈춤)이고 `None`이다. 저장된 설정이 실거래의
        최소 주문 금액으로 검사에 걸리면 A-15와 새 매수 막음이다."""
        account = self._engine_read(STAGE_ORDER, market)
        rules = account if isinstance(account, str) else self._live_rules_for(market)
        if isinstance(rules, str):
            if not self._pause.error:
                self._order_read_failed(market, target, rules)
            return None
        errors = check_trade_settings(trade, self._trade_coins(), rules.min_total)
        self._settings_blocked = bool(errors)
        if errors:
            spec = alert_rules.min_order_changed()
            self._emit_alert(spec, self._rules.want_a15(), module=_MODULE)
        else:
            self._rules.a15_cleared()
        # 판단표는 수수료율 하나를 받는다: 평균회귀의 매도 조건에 쓰는 매도 수수료율을 넘긴다.
        return self._live_limits_account(), Decimal(rules.min_total), Decimal(rules.ask_fee)

    def _live_round_place(self, plan: OrderPlan, round_id: int, target: _OrderTarget) -> None:
        """실거래 회차의 주문(M5 명세 3.6.2): 낸 주문과 거절된 주문(`FAILED`)은 보고서의
        `order_id`다. 쓰지 못했거나 보내지 못한 주문은 A-2다. 목록에 없는 오류의 거절은 A-24다."""
        order_id, error = self._live_place(plan, round_id)
        if order_id is None:
            if not self._pause.error:
                self._order_read_failed(plan.market, target, error or ERROR_STORAGE)
            self._no_order(target, NoOrderReason.NO_PRICE_OR_TICK)
            return
        if error is not None and branch_of(error) is ErrorBranch.UNLISTED:
            wanted = self._rules.want_a24(plan.market, error, self._clock.monotonic())
            spec = alert_rules.unknown_exchange(plan.market, STAGE_ORDER, error)
            self._emit_alert(spec, wanted, module=_MODULE)
        elapsed = int((self._clock.now() - target.round_close).total_seconds())
        trade_store.set_report_order(
            self._conn,
            target.report_id,
            order_id=order_id,
            no_order_reason=None,
            elapsed_order_s=elapsed,
        )

    def _read_order_price(self, market: str, target: _OrderTarget) -> Decimal | None:
        """계정 읽기의 새 현재가(5.1절). 읽지 못하면 주문 때의 A-2."""
        if self._is_blocked():
            self._order_read_failed(market, target, ERROR_BLOCKED)
            return None
        try:
            price: Decimal = self._client.read_price(market)
        except QuoteError as exc:
            if exc.error_name == ERROR_BLOCKED:
                self._set_block(exc.retry_after)
            self._order_read_failed(market, target, exc.error_name)
            return None
        self._prices[market] = (price, self._clock.now())
        return price

    def _last_tick_error(self, market: str) -> str:
        """호가 단위를 읽지 못한 까닭의 이름. 다시 요청하지 않는다(차단이면 "일시 차단")."""
        del market
        if not self._has_book:
            return NoOrderReason.NO_PRICE_OR_TICK.value
        return ERROR_BLOCKED if self._is_blocked() else self._tick_error_name

    def _order_read_failed(self, market: str, target: _OrderTarget, error_name: str) -> None:
        wanted = self._rules.want_a2(market, error_name)
        next_text = kst_when_text(target.next_close, self._clock.now())
        spec = alert_rules.order_price_failed(market, error_name, next_text)
        self._emit_alert(spec, wanted, module=_MODULE)

    def _buy_blocked(
        self,
        plan: OrderPlan,
        account: PaperAccount,
        trade: TradeSettings,
        rules: OrderRules,
        market: str,
    ) -> NoOrderReason | None:
        """판단의 주문의 한도(3.8.2): `check_cancel_cap`, 매수이면 손절 뒤라 새로 사지 않음과
        `check_buy`."""
        now = self._clock.now()
        start, end = _day_bounds(now)
        mode = self._mode
        cancels = trade_store.count_canceled_judgment_orders(
            self._conn, mode, market, start=start, end=end
        )
        capped = check_cancel_cap(cancels)
        if capped is not None or plan.side is not OrderSide.BUY:
            return capped
        if self._after_stop_loss(market):
            return NoOrderReason.NO_BUY_AFTER_STOP_LOSS
        opens = trade_store.list_open_orders(self._conn, mode)
        unconfirmed = any(
            order.market == market and order.order_id in self._cancel_pending for order in opens
        )
        if mode is Mode.LIVE and self._live_unconfirmed(market):
            unconfirmed = True  # 확인 안 됨 주문(M5 명세 3.6.3)
        context = BuyContext(
            account=account,
            open_orders=tuple(order for order in opens if order.market in trade.coin_ratios),
            day_realized_pnl=trade_store.realized_pnl_between(
                self._conn, mode, start=start, end=end
            ),
            caution=self._cautions.get(market, False),
            settings_check_failed=self._settings_blocked,
            last_order_utc=trade_store.last_order_time(self._conn, mode, market),
            unconfirmed=unconfirmed,
        )
        reason = check_buy(plan, context, trade, rules, now)
        if mode is Mode.LIVE:
            reason = self._live_krw_check(plan, reason, unconfirmed)
        return reason

    def _live_krw_check(
        self, plan: OrderPlan, reason: NoOrderReason | None, unconfirmed: bool
    ) -> NoOrderReason | None:
        """실거래의 원화 잔고(M5 명세 3.6.1): `krw` < 가격 × 수량 × (1 + bid_fee)이면 사지 않는다.
        `check_buy`의 원화 검사(가상 원화의 수수료율)를 주문 가능 정보의 수수료율로 다시 본다."""
        if reason not in (None, NoOrderReason.NO_KRW):
            return reason
        account = self._live_account
        rules = self._live_rules.get(plan.market)
        if account is None or rules is None:
            return reason
        if not live_krw_enough(plan, account, rules):
            return NoOrderReason.NO_KRW
        if reason is NoOrderReason.NO_KRW:
            return NoOrderReason.UNCONFIRMED_ORDER if unconfirmed else None
        return reason

    def _after_stop_loss(self, market: str) -> bool:
        """회차 밖의 손절(3.5.1): 마지막 손절 체결 F에 도는 회차가 없었으면, `started_utc`가 F 뒤인
        가장 이른 회차에서는 새로 사지 않는다. 저장된 기록으로 센다."""
        fill = trade_store.last_stop_loss_fill(self._conn, self._mode, market)
        active = self._active_round
        if fill is None or active is None:
            return False
        moment = fill.filled_utc
        first = trade_store.first_round_after(self._conn, self._mode, moment)
        if first != active.round_id:
            return False
        return not _ran_at(self._conn, moment, active.round_id, self._mode)

    def _decision_alert(
        self,
        market: str,
        code: str | None,
        trade: TradeSettings,
        view: Any,
        price: Decimal,
        rules: OrderRules,
        stop_only: bool,
    ) -> None:
        """`decide`가 정한 알림(A-3, A-4, A-9)."""
        if code == CODE_PRICE_GUARD:
            predicted = trade.method is TradeMethod.MEAN_REVERSION
            base = view.predicted_price if predicted else None
            if base is None:
                base = view.sell_price if view.direction is Direction.DOWN else view.buy_price
            shown = self._widened(base, view, trade, rules, predicted)
            spec = alert_rules.price_guard(
                market,
                predicted=predicted,
                price=_price_text(shown),
                current=_price_text(price),
                stop_only=stop_only,
            )
            self._emit_alert(spec, True, module=_MODULE)
        elif code == CODE_NO_AVG:
            spec = alert_rules.no_avg_price(market, STAGE_ORDER)
            self._emit_alert(spec, self._rules.want_a9(market), module=_MODULE)
        elif code == CODE_TOO_SMALL:
            spec = alert_rules.too_small(market)
            self._emit_alert(spec, self._rules.want_a3(market), module=_MODULE)

    def _widened(
        self, base: Decimal | None, view: Any, trade: TradeSettings, rules: OrderRules, mr: bool
    ) -> Decimal | None:
        """A-4의 {가격}: 허용 범위로 넓혀 맞춘 가격(3.8.5)."""
        if base is None:
            return None
        if mr:
            side = OrderSide.BUY if view.direction is Direction.DOWN else OrderSide.SELL
        else:
            side = OrderSide.BUY if view.direction is Direction.UP else OrderSide.SELL
        widened: Decimal = widen_price(base, side, trade.tolerance, rules.tick_size)
        return widened

    def _limit_alert(self, market: str, reason: NoOrderReason) -> None:
        """한도에 걸린 까닭의 알림(3.8.2의 표)."""
        now = self._clock.now()
        day = _day_bounds(now)[0].date()
        if reason is NoOrderReason.DAILY_LOSS_LIMIT:
            self._emit_alert(alert_rules.daily_loss(), self._rules.want_a12(day), module=_MODULE)
        elif reason is NoOrderReason.CAUTION:
            spec = alert_rules.caution(market)
            self._emit_alert(spec, self._rules.want_a13(market, day), module=_MODULE)
        elif reason is NoOrderReason.SETTINGS_CHECK:
            spec = alert_rules.min_order_changed()
            self._emit_alert(spec, self._rules.want_a15(), module=_MODULE)
        elif reason in (NoOrderReason.COIN_BUDGET, NoOrderReason.BUDGET):
            self._emit_alert(alert_rules.over_budget(market), True, module=_MODULE)
        elif reason is NoOrderReason.NO_KRW:
            live = self._mode is Mode.LIVE
            spec = alert_rules.live_no_krw(market) if live else alert_rules.no_krw(market)
            self._emit_alert(spec, True, module=_MODULE)

    def _round_finished(self) -> None:
        self._round_stage = None
        self._round_number = None
        self._sold_canceled.clear()
        self._round_stopped.clear()
        self._post_round_now()

    # --- 화면에 보내는 값(3.17.2) -------------------------------------------------------------

    def _post_trade_views(self) -> None:
        """코인마다 `CoinTradeView`, `TodayView`, `RoundNowView`(켤 때 차례 5와 바뀔 때마다)."""
        trade = self._trade()
        if trade is None or self._conn is None:
            return
        try:
            account = self._trade_account()
        except StorageError as exc:
            log.warning("계정을 읽지 못했습니다: %s", exc)
            if self._mode is Mode.LIVE:
                self._post_round_now()
            return
        now = self._clock.now()
        opens = trade_store.list_open_orders(self._conn, self._mode)
        start, end = _day_bounds(now)
        pnl = trade_store.realized_pnl_between(self._conn, self._mode, start=start, end=end)
        reached = pnl <= -trade.daily_loss_limit
        for market in self._trade_coins():
            self._post(self._coin_trade_view(market, account, opens, trade, reached, now))
        used = _ZERO
        for holding in account.holdings:
            if holding.market in trade.coin_ratios and holding.avg_price is not None:
                used += holding.avg_price * holding.qty
        for order in opens:
            buying = order.side is OrderSide.BUY and order.price is not None
            if buying and order.market in trade.coin_ratios:
                assert order.price is not None
                used += order.price * order.qty
        self._post(
            TodayView(
                realized_pnl=pnl,
                daily_loss_limit=trade.daily_loss_limit,
                used=used,
                budget=trade.budget,
                paper_krw=account.krw,
                limit_reached=reached,
                as_of=now,
            )
        )
        self._post_round_now()
        self._post_performance()

    def _coin_trade_view(
        self,
        market: str,
        account: PaperAccount,
        opens: tuple[OrderRecord, ...],
        trade: TradeSettings,
        reached: bool,
        now: datetime,
    ) -> CoinTradeView:
        holding = holding_of(account, market)
        if holding is not None and holding.qty <= 0:
            holding = None
        known = self._prices.get(market)
        price = None if known is None else known[0]
        blocked: list[str] = []
        caution = self._cautions.get(market, False)
        flags = {
            NoOrderReason.DAILY_LOSS_LIMIT: reached,
            NoOrderReason.CAUTION: caution,
            NoOrderReason.SETTINGS_CHECK: self._settings_blocked,
        }
        for reason in _BUY_BLOCK_REASONS:
            if flags[reason]:
                blocked.append(reason.value)
        last = self._directions.get(market)
        return CoinTradeView(
            market=market,
            price=price,
            price_read_utc=None if known is None else known[1],
            holding=holding,
            sellable_qty=sellable_qty(holding),
            value=None if holding is None or price is None else holding.qty * price,
            stop_line=None if holding is None else stop_line(holding.avg_price, trade.stop_ratio),
            dust=price is not None and is_dust(holding, price, self._coin_min(market)),
            last_direction=None if last is None else last[0],
            last_round=None if last is None else last[1],
            open_orders=tuple(order for order in opens if order.market == market),
            buy_blocked=tuple(blocked),
            caution=caution,
            sell_all=self._sell_all_text(market),
            as_of=now,
        )

    def _post_round_now(self) -> None:
        if self._trade() is None:
            return
        running = self._active_round is not None and self._round_stage is not None
        self._post(
            RoundNowView(
                round_number=self._round_number if running else None,
                stage=self._round_stage if running else None,
                next_round_utc=self._next_due,
                paused=not may_run_round(self._pause),
                as_of=self._clock.now(),
            )
        )

    def _load_directions(self) -> None:
        """켤 때: 코인마다 지난 판단의 방향과 회차(코인 칸의 "지난 판단")."""
        far = self._clock.now() + timedelta(days=36500)
        for market in self._trade_coins():
            last = report_store.read_last_judgment(
                self._conn, market=market, mode=self._mode, before_close=far
            )
            if last is not None:
                self._directions[market] = (last.direction, last.round_number)


def _locked(account: PaperAccount, order: OrderRecord) -> bool:
    """그 지정가 주문의 묶음이 계정에 남아 있는지(켤 때 `sent` 0인 줄과 취소에서 푼다)."""
    if order.price is None:
        return False
    if order.side is OrderSide.BUY:
        return account.locked_krw >= order.price * order.qty * (1 + PAPER_FEE_RATE)
    holding: PaperHolding | None = holding_of(account, order.market)
    return holding is not None and holding.locked_qty >= order.qty


def _ran_at(conn: Any, moment: datetime, round_id: int, mode: Mode = Mode.PAPER) -> bool:
    """그때 이 회차 말고 도는 회차가 있었는지(회차 밖의 손절인지 가른다)."""
    del round_id
    return bool(trade_store.round_running_at(conn, mode, moment))


def _price_text(value: Decimal | None) -> str:
    return "-" if value is None else format(value.normalize(), ",f")
