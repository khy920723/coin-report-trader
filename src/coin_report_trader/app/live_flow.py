"""5단계 잇기 1: 모드, 업비트 키, 실거래 켜기·끄기, 실거래 설정(M5 명세 3.7, 3.7.5, 3.8. 15번).

자료 스레드(`DataWorker`)가 상속한다. `save_upbit_key` 등 동작은 주 스레드에서 큐에 넣고, 나머지
메서드는 모두 자료 스레드에서만 부른다. 켜는 조건과 자산 검사는 `trading/live/unlock.py`가 셈하고
(여기서는 저장된 기록을 모아 넘긴다), 실거래 연결은 `trading/live/select.py`의 `select_exchange`로만
만든다(`CLAUDE.md`의 구조 원칙, 3.13).

- 모드: 켤 때 `trade_mode`를 읽는다. `live`인데 실거래 허용 값이 거짓이면 `paper`로 쓰고 A-20이다.
  `live`면 키를 읽고(없거나 읽지 못하면 A-23 "키가 맞지 않음"과 오류 멈춤) `select_exchange`로
  `ExchangePort`를 고른다. 대조와 손절 점검은 3단계 켤 때의 차례 그대로다.
- 키(3.8): 보관소의 두 이름(`upbit_access_key`, `upbit_secret_key`)과 설정 키 `upbit_key_created`.
  넣기와 지우기는 모의매매의 멈춤 상태에서만 된다. 값은 받은 즉시 보관소에 넘기고 어디에도 남기지
  않는다. 키 한 벌에 `UpbitClient` 하나를 만들어 매매 엔진과 계정 화면이 함께 쓴다(3.5.3).
- 실거래 켜기(3.7.2): 앞 차례(`prepare_live_on`)와 뒤 차례(`turn_live_on`). 끄기(3.7.3).
- 실거래 설정(3.7.5): 저장 전의 검사와 칸 이름 옮기기, 실거래 중의 막음(3.7.4).
- 화면의 값: `LiveStatusView`(켤 때와 바뀔 때마다), `UpbitKeyView`.
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Final

from coin_report_trader.core import (
    SAVE_TARGET_SETTINGS,
    ErrorPauseCause,
    Mode,
    ReportState,
    SaveResult,
    SecretStore,
    SecretStoreError,
    SettingsView,
    StorageError,
    TradeSettings,
    coin_label,
)
from coin_report_trader.core.clock import KST
from coin_report_trader.core.errors import ExchangeError, QuoteError
from coin_report_trader.core.exchange import (
    KEY_EXPIRY_WARN_DAYS,
    LIVE_CONFIRM_SECONDS,
    UPBIT_KEY_VALID_DAYS,
    ExchangeAccount,
    LiveBlock,
    LiveConfirmView,
    LiveSettings,
    live_block_budget_too_big,
    live_block_not_enough_rounds,
)
from coin_report_trader.core.ports import UpbitTransport
from coin_report_trader.core.texts import (
    ERROR_STORAGE,
    ERROR_TIMEOUT,
    ERROR_VAULT,
    STAGE_KEY_SAVE,
    STAGE_LIVE_ON,
    STAGE_STARTUP,
    text_save_failed,
)
from coin_report_trader.core.views import BelowStopView, LiveStatusView, UpbitKeyView
from coin_report_trader.storage import trading as trade_store
from coin_report_trader.storage.settings import (
    delete_key_created,
    read_key_created,
    read_live_rounds_n,
    read_trade_mode,
    write_key_created,
    write_trade_mode,
)
from coin_report_trader.trading.live import (
    LIVE_TRADING_ALLOWED,
    RoundReport,
    SettingEvent,
    count_paper_rounds,
    live_account_for_limits,
    live_blocks,
    live_budget_fits,
    live_settings_ok,
    select_exchange,
)
from coin_report_trader.trading.orders import check_trade_settings
from coin_report_trader.trading.orders.settings_check import (
    KEY_ADD_RATIO,
    KEY_BUDGET,
    KEY_COIN_RATIOS,
    KEY_DAILY_LOSS_LIMIT,
    KEY_FIRST_RATIO,
    KEY_MR_FIRST_RATIO,
    TEXT_POSITIVE,
    text_ratio_sum,
)
from coin_report_trader.trading.paper import holding_of
from coin_report_trader.trading.risk.stop_loss import stop_line
from coin_report_trader.trading.upbit import (
    ERROR_KEY_INVALID,
    HttpxUpbitTransport,
    UpbitClient,
    UpbitKeys,
)
from coin_report_trader.vault import (
    UPBIT_ACCESS_KEY_NAME,
    UPBIT_SECRET_KEY_NAME,
    create_secret_store,
)

from . import alert_rules
from .trade_round import _summary

log = logging.getLogger(__name__)

_MODULE: Final = __name__.removeprefix("coin_report_trader.")
_HUNDRED: Final = Decimal(100)
_ZERO: Final = Decimal(0)

SAVE_TARGET_UPBIT_KEY: Final = "upbit_key"
"""키 넣기와 지우기의 결과(`SaveResult.target`. 3.4)."""
SAVE_TARGET_LIVE_ON: Final = "live_on"
"""실거래 켜기의 결과(`SaveResult.target`. 3.4). 모드 칸 아래 글이라 끄기의 결과도 이 이름이다."""
TEXT_CONNECT_FAILED: Final = "업비트에 연결하지 못했습니다({}). 키, 허용 IP, 권한을 확인하세요."
TEXT_PRICE_UNREAD: Final = "현재가를 읽지 못해 확인하지 못했습니다. 다시 누르세요."
TEXT_OFF_FAILED: Final = "취소하지 못한 주문이 있어 끄지 못했습니다."
TEXT_NEW_COIN_LIVE: Final = (
    "실거래 중에는 새 코인을 고를 수 없습니다. 모의매매로 돌아가 모의 회차를 채우세요."
)
TEXT_PAPER_ONLY: Final = "모의매매에서만 바꿀 수 있습니다."
TEXT_KEY_LOCKED: Final = "멈춤 상태에서만 키를 고칠 수 있습니다."
TEXT_KEY_DELETE_FAILED: Final = "키를 지우지 못했습니다."
LIVE_KEY_BUDGET: Final = "live_budget"
LIVE_KEY_DAILY_LOSS: Final = "live_daily_loss_limit"
LIVE_KEY_RATIOS: Final = "live_coin_ratios"
LIVE_KEY_RATIO_PREFIX: Final = "live_coin_ratio:"


@dataclass(frozen=True)
class KeyTask:
    """`save_upbit_key`의 값. `repr`에 값이 들지 않는다(3.5.1)."""

    access: str = field(repr=False)
    secret: str = field(repr=False)
    created: date


@dataclass(frozen=True)
class LiveTask:
    """키 지우기와 실거래 켜기·끄기의 동작 이름."""

    kind: str


LIVE_DELETE_KEY: Final = "delete_key"
LIVE_PREPARE: Final = "prepare"
LIVE_TURN_ON: Final = "turn_on"
LIVE_TURN_OFF: Final = "turn_off"


def live_ratio_key(market: str) -> str:
    """실거래 투자 비율 칸의 이름(설정 키): `live_coin_ratio:{마켓 코드}`(3.7.5)."""
    return f"{LIVE_KEY_RATIO_PREFIX}{market}"


class LiveMixin:
    """모드, 키, 실거래 켜기·끄기, 실거래 설정. `DataWorker`의 일부다."""

    _conn: Any
    _clock: Any
    _settings: Any
    _client: Any
    _rules: Any
    _pause: Any
    _min_order: Decimal
    _stage_now: str
    _live_account: ExchangeAccount | None
    _live_rules: dict[str, Any]
    _cancel_pending: set[int]
    _put: Callable[..., None]
    _post: Callable[..., None]
    _emit_alert: Callable[..., bool]
    _enter_error: Callable[..., bool]
    _trade: Callable[[], TradeSettings | None]
    _trade_coins: Callable[[], tuple[str, ...]]
    _trade_active: Callable[[], bool]
    _cancel_order: Callable[..., bool]
    _post_pause_views: Callable[[], None]
    _post_trade_views: Callable[[], None]
    _post_trade_status: Callable[[], None]
    _live_reconcile: Callable[[], bool]
    _write_start_snapshots: Callable[[ExchangeAccount], None]
    _is_blocked: Callable[[], bool]
    _on_key_changed: Callable[[], None]

    def _init_live(
        self,
        vault: SecretStore | None,
        real_vault: bool,
        transport: UpbitTransport | None,
    ) -> None:
        self._mode = Mode.PAPER
        self._exchange: Any = None
        """지금 모드의 `ExchangePort`(실거래만. 모의매매는 3단계 엔진을 부른다)."""
        self._vault: SecretStore | None = vault
        self._real_vault = real_vault and vault is None
        self._transport: UpbitTransport | None = transport
        self._owned_transport: HttpxUpbitTransport | None = None
        self._upbit: UpbitClient | None = None
        """키 한 벌의 `UpbitClient`(매매 엔진과 계정 화면이 함께 쓴다. 3.5.3)."""
        self._key_present = False
        self._key_read = False
        self._key_created: date | None = None
        self._live_below_shown: set[str] = set()
        """실거래 켜기에서 `BelowStopView`로 보인 코인(다시 부르면 확인한 것으로 본다. 3.7.2)."""

    # --- 주 스레드: 동작 -----------------------------------------------------------------------

    def save_upbit_key(self, access: str, secret: str, created: date) -> None:
        """값은 자료 스레드에서 바로 보관소에 넘기고 어디에도 남기지 않는다(3.4)."""
        self._put(KeyTask(str(access), str(secret), created))

    def delete_upbit_key(self) -> None:
        self._put(LiveTask(LIVE_DELETE_KEY))

    def prepare_live_on(self) -> None:
        self._put(LiveTask(LIVE_PREPARE))

    def turn_live_on(self) -> None:
        self._put(LiveTask(LIVE_TURN_ON))

    def turn_live_off(self) -> None:
        self._put(LiveTask(LIVE_TURN_OFF))

    # --- 자료 스레드: 동작의 차례 -----------------------------------------------------------

    def _run_live_task(self, task: KeyTask | LiveTask) -> None:
        if isinstance(task, KeyTask):
            self._do_save_key(task)
        elif task.kind == LIVE_DELETE_KEY:
            self._do_delete_key()
        elif task.kind == LIVE_PREPARE:
            self._do_prepare_live_on()
        elif task.kind == LIVE_TURN_ON:
            self._do_turn_live_on()
        elif task.kind == LIVE_TURN_OFF:
            self._do_turn_live_off()

    # --- 보관소와 키(3.8) -----------------------------------------------------------------------

    def _secret_store(self) -> SecretStore | None:
        if self._vault is None and self._real_vault:
            self._vault = create_secret_store()
        return self._vault

    def _load_key(self) -> None:
        """보관소에서 키가 있는지와 설정의 만든 날을 읽는다. 값은 `UpbitClient`에만 둔다."""
        self._key_read = True
        self._key_created = None
        self._drop_client()
        keys = self._read_keys()
        self._key_present = keys is not None
        if keys is not None:
            self._upbit = UpbitClient(keys, self._upbit_transport(), self._clock)
        try:
            self._key_created = read_key_created(self._conn)
        except StorageError as exc:
            log.warning("키를 만든 날을 읽지 못했습니다: %s", exc)

    def _read_keys(self) -> UpbitKeys | None:
        store = self._secret_store()
        if store is None:
            return None
        try:
            access = store.get(UPBIT_ACCESS_KEY_NAME)
            secret = store.get(UPBIT_SECRET_KEY_NAME)
        except SecretStoreError as exc:
            log.warning("업비트 키를 읽지 못했습니다: %s", exc.error_name)
            return None
        if not access or not secret:
            return None
        try:
            return UpbitKeys(access, secret)
        except (TypeError, ValueError):
            return None

    def _upbit_transport(self) -> UpbitTransport:
        if self._transport is not None:
            return self._transport
        if self._owned_transport is None:
            self._owned_transport = HttpxUpbitTransport()
        return self._owned_transport

    def _key_client(self) -> UpbitClient | None:
        """키 한 벌의 `UpbitClient`. 아직 읽지 않았으면 보관소에서 읽는다."""
        if not self._key_read:
            self._load_key()
        return self._upbit

    def _drop_client(self) -> None:
        """들고 있던 키의 값을 버린다(키를 지우거나 바꿀 때. 3.15.4)."""
        self._upbit = None

    def _close_live(self) -> None:
        transport = self._owned_transport
        if transport is not None:
            try:
                transport.close()
            except Exception as exc:
                log.warning("업비트 연결을 닫지 못했습니다: %s", type(exc).__name__)

    def _key_editable(self) -> bool:
        """키를 넣고 지울 수 있는지: 모의매매의 멈춤 상태(사용자 멈춤이나 오류 멈춤. 3.7.4, 3.8)."""
        state = self._pause
        return self._trade_active() and self._mode is Mode.PAPER and (state.user or state.error)

    def _key_view(self, message: str | None = None) -> UpbitKeyView:
        created = self._key_created if self._key_present else None
        expires = None if created is None else created + timedelta(days=UPBIT_KEY_VALID_DAYS)
        return UpbitKeyView(
            present=self._key_present,
            created=created,
            expires=expires,
            can_edit=self._key_editable(),
            message=message,
        )

    def _post_key_view(self, message: str | None = None) -> None:
        if self._trade_active():
            self._post(self._key_view(message))

    def _do_save_key(self, task: KeyTask) -> None:
        if not self._key_editable():
            self._post(SaveResult(SAVE_TARGET_UPBIT_KEY, False, TEXT_KEY_LOCKED))
            self._post_key_view()
            return
        store = self._secret_store()
        error_name: str | None = None
        if store is None:
            error_name = ERROR_VAULT
        else:
            try:
                store.set(UPBIT_ACCESS_KEY_NAME, task.access)
                store.set(UPBIT_SECRET_KEY_NAME, task.secret)
            except SecretStoreError as exc:
                error_name = exc.error_name
            except ValueError:
                error_name = ERROR_VAULT
        if error_name is None:
            try:
                write_key_created(self._conn, task.created)
            except StorageError:
                error_name = ERROR_STORAGE
        if error_name is not None:
            self._key_save_failed(error_name)
            self._load_key()
            self._post(
                SaveResult(
                    SAVE_TARGET_UPBIT_KEY, False, text_save_failed(STAGE_KEY_SAVE, error_name)
                )
            )
            self._post_key_view()
            return
        self._rules.a29_succeeded(STAGE_KEY_SAVE, None)
        self._load_key()
        self._post(SaveResult(SAVE_TARGET_UPBIT_KEY, True))
        self._post_key_view()
        self._post_live_status()
        self._on_key_changed()

    def _do_delete_key(self) -> None:
        if not self._key_editable():
            self._post(SaveResult(SAVE_TARGET_UPBIT_KEY, False, TEXT_KEY_LOCKED))
            self._post_key_view()
            return
        store = self._secret_store()
        error_name: str | None = None
        if store is None:
            error_name = ERROR_VAULT
        else:
            try:
                store.delete(UPBIT_ACCESS_KEY_NAME)
                store.delete(UPBIT_SECRET_KEY_NAME)
            except SecretStoreError as exc:
                error_name = exc.error_name
        if error_name is None:
            try:
                delete_key_created(self._conn)
            except StorageError:
                error_name = ERROR_STORAGE
        if error_name is not None:
            self._key_save_failed(error_name)
            self._load_key()
            self._post_key_view(TEXT_KEY_DELETE_FAILED)
            self._on_key_changed()
            return
        self._rules.a29_succeeded(STAGE_KEY_SAVE, None)
        self._load_key()
        self._post(SaveResult(SAVE_TARGET_UPBIT_KEY, True))
        self._post_key_view()
        self._post_live_status()
        self._on_key_changed()

    def _key_save_failed(self, error_name: str) -> None:
        """A-29(단계 "업비트 키 저장"). 오류 멈춤으로 들지 않는다(3.8)."""
        wanted = self._rules.want_a29(STAGE_KEY_SAVE, None)
        spec = alert_rules.key_save_failed(STAGE_KEY_SAVE, error_name)
        self._emit_alert(spec, wanted, module=_MODULE)

    def _key_expiry_alert(self) -> None:
        """켤 때 한 번: 만료일까지 30일 이하이거나 지났으면 A-32(3.8)."""
        created = self._key_created
        if not self._key_present or created is None:
            return
        expires = created + timedelta(days=UPBIT_KEY_VALID_DAYS)
        today = self._clock.now().astimezone(KST).date()
        left = (expires - today).days
        if left > KEY_EXPIRY_WARN_DAYS:
            return
        spec = alert_rules.key_expiry(
            expires.isoformat(), expired=left < 0, live=self._mode is Mode.LIVE
        )
        self._emit_alert(spec, True, module=_MODULE)

    # --- 켤 때의 모드(3.7.2의 끝) ---------------------------------------------------------------

    def _live_startup(self) -> None:
        """켤 때: 모드를 읽고, 실거래면 키를 읽어 거래소 경계를 고른다. 대조와 손절 점검은 3단계
        켤 때의 차례가 한다."""
        self._exchange = None
        self._mode = read_trade_mode(self._conn)
        self._load_key()
        if self._mode is Mode.LIVE and not _allowed():
            self._mode = Mode.PAPER
            try:
                write_trade_mode(self._conn, Mode.PAPER)
            except StorageError as exc:
                self._live_write_failed(exc)
            self._emit_alert(alert_rules.live_off(), True, module=_MODULE)
        elif self._mode is Mode.LIVE:
            client = self._upbit
            if client is None:
                self._stage_now = STAGE_STARTUP
                if self._enter_error(ErrorPauseCause.EXCHANGE, ERROR_KEY_INVALID):
                    spec = alert_rules.exchange_error_pause(STAGE_STARTUP, ERROR_KEY_INVALID)
                    self._emit_alert(spec, True, module=_MODULE)
            else:
                self._exchange = select_exchange(
                    Mode.LIVE,
                    paper_store=None,
                    client=client,
                    allowed=_allowed(),
                    clock=self._clock,
                )
        self._key_expiry_alert()

    def _post_live_views(self) -> None:
        """켤 때 차례 5와 바뀔 때마다: `LiveStatusView`와 `UpbitKeyView`."""
        if not self._trade_active():
            return
        self._post_live_status()
        self._post_key_view()

    # --- 켜는 조건(3.7.2의 1~7)과 `LiveStatusView` ------------------------------------------------

    def _live_counts(
        self, trade: TradeSettings, coins: tuple[str, ...]
    ) -> tuple[dict[str, int], int]:
        rows = trade_store.list_judgment_rows(self._conn, Mode.PAPER)
        reports = [
            RoundReport(
                market=row.market,
                mode=Mode.PAPER,
                ok=row.state is ReportState.OK,
                method=row.method,
                utc=row.close_utc,
            )
            for row in rows
        ]
        events = [
            SettingEvent(kind=event.kind, market=event.market, utc=event.utc)
            for event in trade_store.list_setting_events(self._conn, Mode.PAPER)
        ]
        counts = {coin: count_paper_rounds(coin, trade.method, reports, events) for coin in coins}
        return counts, read_live_rounds_n(self._conn)

    def _live_block_texts(self) -> tuple[tuple[str, ...], dict[str, int], int, bool]:
        """(막힌 까닭의 글, 코인마다 센 수, N, 전량 매도 기록)."""
        settings: SettingsView = self._settings
        trade: TradeSettings | None = None if settings is None else settings.trade
        coins = () if settings is None else tuple(settings.coins)
        if trade is None:
            return (LiveBlock.NO_SETTINGS.value,), {}, 0, False
        counts, rounds_n = self._live_counts(trade, coins)
        sold = trade_store.has_sold_all(self._conn, Mode.PAPER)
        ok = live_settings_ok(settings.live, trade, coins, self._min_order)
        blocks = live_blocks(
            allowed=_allowed(),
            key_present=self._key_present,
            pause=self._pause,
            coins=coins,
            counts=counts,
            rounds_n=rounds_n,
            sell_all_done=sold,
            settings_ok=ok,
        )
        texts: list[str] = []
        for block in blocks:
            if block is LiveBlock.NOT_ENOUGH_ROUNDS:
                texts += [
                    live_block_not_enough_rounds(coin, counts.get(coin, 0), rounds_n)
                    for coin in coins
                    if counts.get(coin, 0) < rounds_n
                ]
            else:
                texts.append(block.value)
        return tuple(texts), counts, rounds_n, sold

    def _post_live_status(self, extra: tuple[str, ...] = ()) -> None:
        if not self._trade_active() or self._conn is None:
            return
        texts, counts, rounds_n, sold = self._live_block_texts()
        coins = self._trade_coins()
        self._post(
            LiveStatusView(
                mode=self._mode,
                allowed=_allowed(),
                blocks=(*texts, *extra),
                counts=tuple((coin, counts.get(coin, 0), rounds_n) for coin in coins),
                sell_all_done=sold,
                as_of=self._clock.now(),
            )
        )

    # --- 실거래 켜기(3.7.2) ----------------------------------------------------------------------

    def _read_live_on(
        self,
    ) -> tuple[ExchangeAccount, dict[str, Decimal | None]] | str:
        """키로 계정과 고른 코인의 주문 가능 정보를 한 번씩, 고른 코인의 현재가를 읽는다
        (`LIVE_CONFIRM_SECONDS` 안). 실패면 모드 칸 아래의 글이다. 오류 멈춤으로 들지 않는다."""
        client = self._key_client()
        if client is None:
            return TEXT_CONNECT_FAILED.format(ERROR_KEY_INVALID)
        started = self._clock.monotonic()
        exchange = select_exchange(
            Mode.LIVE, paper_store=None, client=client, allowed=_allowed(), clock=self._clock
        )
        coins = self._trade_coins()
        failed: ExchangeError | None = None
        account: ExchangeAccount | None = None
        try:
            account = exchange.read_account()
            for coin in coins:
                self._live_rules[coin] = exchange.read_rules(coin)
        except ExchangeError as exc:
            failed = exc
        if failed is not None or account is None:
            name = ERROR_KEY_INVALID if failed is None else failed.error_name
            log.warning("실거래 켜기의 업비트 읽기가 실패했습니다: %s", name)
            return TEXT_CONNECT_FAILED.format(name)
        prices: dict[str, Decimal | None] = {}
        for coin in coins:
            prices[coin] = self._live_on_price(coin)
        if self._clock.monotonic() - started > LIVE_CONFIRM_SECONDS:
            return TEXT_CONNECT_FAILED.format(ERROR_TIMEOUT)
        return account, prices

    def _live_on_price(self, market: str) -> Decimal | None:
        if self._is_blocked():
            return None
        try:
            price: Decimal = self._client.read_price(market)
        except (QuoteError, TypeError):
            return None
        return price

    def _live_confirm(
        self, live: LiveSettings, account: ExchangeAccount, prices: Mapping[str, Decimal | None]
    ) -> LiveConfirmView | str | None:
        """조건 8. 통과면 `LiveConfirmView`, 현재가를 읽지 못했으면 그 글, 걸리면 `None`(까닭은
        `LiveStatusView`로 보냈다)."""
        assert live.budget is not None
        fits, total = live_budget_fits(live.budget, account, prices)
        if total is None:
            return TEXT_PRICE_UNREAD
        if not fits:
            self._post_live_status((live_block_budget_too_big(live.budget, total),))
            return None
        coins = self._trade_coins()
        held = {holding.market: holding for holding in account.holdings}
        holdings: list[tuple[str, Decimal, Decimal | None]] = []
        for coin in coins:
            holding = held.get(coin)
            qty = _ZERO if holding is None else holding.balance + holding.locked
            price = prices.get(coin)
            holdings.append((coin, qty, None if price is None else qty * price))
        return LiveConfirmView(
            budget=live.budget,
            coins=tuple(
                (coin, live.coin_ratios[coin], live.budget * live.coin_ratios[coin] / _HUNDRED)
                for coin in coins
            ),
            krw_available=account.krw,
            holdings=tuple(holdings),
            total=total,
            as_of=account.read_utc,
        )

    def _live_on_checks(
        self,
    ) -> tuple[LiveSettings, ExchangeAccount, dict[str, Decimal | None], LiveConfirmView] | None:
        """앞 차례와 뒤 차례가 함께 보는 것: 조건 1~7, 키와 계정 읽기, 조건 8."""
        if self._mode is Mode.LIVE:
            self._post_live_status()
            return None
        texts, _, _, _ = self._live_block_texts()
        if texts:
            self._post_live_status()
            return None
        live: LiveSettings = self._settings.live
        read = self._read_live_on()
        if isinstance(read, str):
            self._post(SaveResult(SAVE_TARGET_LIVE_ON, False, read))
            return None
        account, prices = read
        view = self._live_confirm(live, account, prices)
        if view is None:
            return None
        if isinstance(view, str):
            self._post(SaveResult(SAVE_TARGET_LIVE_ON, False, view))
            return None
        return live, account, prices, view

    def _do_prepare_live_on(self) -> None:
        """앞 차례: 통과하면 `LiveConfirmView`, 아니면 `LiveStatusView`나 모드 칸 아래 글."""
        self._live_below_shown = set()
        checked = self._live_on_checks()
        if checked is not None:
            self._post(checked[3])

    def _do_turn_live_on(self) -> None:
        """뒤 차례: 다시 보기 → 손절선 아래 보유(`BelowStopView`) → 모드 쓰기 → 대조."""
        checked = self._live_on_checks()
        if checked is None:
            return
        live, account, prices, _ = checked
        trade = self._settings.trade
        limits = live_account_for_limits(account)
        for coin in self._trade_coins():
            holding = holding_of(limits, coin)
            if holding is None or holding.qty <= 0 or holding.avg_price is None:
                continue
            line = stop_line(holding.avg_price, trade.stop_ratio)
            price = prices.get(coin)
            if line is None or price is None or price > line or coin in self._live_below_shown:
                continue
            self._live_below_shown.add(coin)
            self._post(BelowStopView(coin, holding.avg_price, price, line))
            return
        client = self._key_client()
        assert client is not None
        try:
            write_trade_mode(self._conn, Mode.LIVE)
            self._write_start_snapshots(account)
        except StorageError as exc:
            try:
                write_trade_mode(self._conn, Mode.PAPER)
            except StorageError:
                log.warning("모드를 모의매매로 되돌려 쓰지 못했습니다")
            self._live_write_failed(exc)
            self._post(
                SaveResult(
                    SAVE_TARGET_LIVE_ON, False, text_save_failed(STAGE_LIVE_ON, ERROR_STORAGE)
                )
            )
            self._post_live_status()
            return
        self._rules.a29_succeeded(STAGE_LIVE_ON, None)
        self._exchange = select_exchange(
            Mode.LIVE, paper_store=None, client=client, allowed=_allowed(), clock=self._clock
        )
        self._mode = Mode.LIVE
        self._live_below_shown = set()
        self._post(SaveResult(SAVE_TARGET_LIVE_ON, True))
        self._post_live_status()
        self._post_pause_views()
        self._post_key_view()
        self._stage_now = STAGE_LIVE_ON
        self._live_reconcile()
        self._post_trade_views()
        del live

    def _live_write_failed(self, exc: StorageError) -> None:
        """모드나 실제 계정을 쓰지 못함: A-29("실거래 켜기")와 오류 멈춤(매매 DB 쓰기. 3.9)."""
        entered = self._enter_error(ErrorPauseCause.SAVE, None)
        wanted = self._rules.want_a29(STAGE_LIVE_ON, None) or entered
        spec = alert_rules.live_on_save_failed(STAGE_LIVE_ON, str(exc), error_pause=entered)
        self._emit_alert(spec, wanted, module=_MODULE)

    # --- 실거래 끄기(3.7.3) ---------------------------------------------------------------------

    def _do_turn_live_off(self) -> None:
        state = self._pause
        if self._mode is not Mode.LIVE or not (state.user or state.error):
            self._post_live_status()
            return
        leftover: tuple[str, ...] = ()
        if not state.error:
            for order in trade_store.list_open_orders(self._conn, Mode.LIVE):
                self._cancel_order(order)
            if trade_store.list_open_orders(self._conn, Mode.LIVE):
                self._post(SaveResult(SAVE_TARGET_LIVE_ON, False, TEXT_OFF_FAILED))
                self._post_live_status()
                return
        else:
            leftover = tuple(
                f"{coin_label(order.market)} {_summary(order)}"
                for order in trade_store.list_open_orders(self._conn, Mode.LIVE)
            )
        try:
            write_trade_mode(self._conn, Mode.PAPER)
        except StorageError as exc:
            self._live_write_failed(exc)
            self._post(
                SaveResult(
                    SAVE_TARGET_LIVE_ON, False, text_save_failed(STAGE_LIVE_ON, ERROR_STORAGE)
                )
            )
            return
        self._mode = Mode.PAPER
        self._exchange = None
        self._live_account = None
        self._cancel_pending.clear()
        self._emit_alert(alert_rules.live_off(leftover), True, module=_MODULE)
        self._post_live_status()
        self._post_pause_views()
        self._post_key_view()
        self._post_trade_views()

    # --- 실거래 설정(3.7.5)과 실거래 중의 막음(3.7.4) -------------------------------------------

    def _check_live_save(
        self,
        current: SettingsView,
        coins: tuple[str, ...],
        trade: TradeSettings | None,
        candle_count: int | None,
        live: LiveSettings | None,
    ) -> tuple[bool, LiveSettings | None]:
        """(저장해도 되는지, 저장할 실거래 설정). 아니면 결과를 이미 보냈다."""
        if self._mode is Mode.LIVE:
            if any(coin not in current.coins for coin in coins):
                self._post(SaveResult(SAVE_TARGET_SETTINGS, False, TEXT_NEW_COIN_LIVE))
                return False, None
            old = current.trade
            method_changed = trade is not None and old is not None and trade.method != old.method
            count_changed = candle_count is not None and candle_count != current.candle_count
            if method_changed or count_changed:
                self._post(SaveResult(SAVE_TARGET_SETTINGS, False, TEXT_PAPER_ONLY))
                return False, None
            return True, None  # 실거래 중에는 실거래 설정을 저장하지 않는다
        if live is None:
            return True, None
        base = trade if trade is not None else current.trade
        errors = self._live_field_errors(live, base, coins)
        if errors:
            self._post(SaveResult(SAVE_TARGET_SETTINGS, False, None, errors))
            return False, None
        return True, live

    def _live_field_errors(
        self, live: LiveSettings, trade: TradeSettings | None, coins: tuple[str, ...]
    ) -> dict[str, str]:
        """받지 않는 값(채운 칸에만. 3.7.5). 칸 이름은 실거래 설정 키다."""
        errors: dict[str, str] = {}
        if live.budget is not None and live.budget <= 0:
            errors[LIVE_KEY_BUDGET] = TEXT_POSITIVE
        if live.daily_loss_limit is not None and live.daily_loss_limit <= 0:
            errors[LIVE_KEY_DAILY_LOSS] = TEXT_POSITIVE
        filled: list[Decimal] = []
        for coin in coins:
            ratio = live.coin_ratios.get(coin)
            if ratio is None:
                continue
            filled.append(ratio)
            if ratio <= 0:
                errors[live_ratio_key(coin)] = TEXT_POSITIVE
        if coins and len(filled) == len(coins):
            total = sum(filled, _ZERO)
            if total != _HUNDRED:
                errors[LIVE_KEY_RATIOS] = text_ratio_sum(total)
        if errors or trade is None or not live.complete(coins):
            return errors
        checked = check_trade_settings(live.apply(trade), coins, self._min_order)
        return _rename_live_errors(checked, coins)


def _rename_live_errors(checked: Mapping[str, str], coins: tuple[str, ...]) -> dict[str, str]:
    """`check_trade_settings`의 칸 이름을 실거래 설정 키로 옮긴다(3.7.5). 실거래 설정에 닿지 않는
    칸(모의매매의 칸)은 버린다. 비율 칸의 하한 글은 그 글의 코인의 비율 칸으로 간다."""
    renamed: dict[str, list[str]] = {}

    def add(key: str, text: str) -> None:
        texts = renamed.setdefault(key, [])
        if text not in texts:  # 첫 매수 비율 칸 둘의 같은 글은 한 번이다
            texts.append(text)

    simple = {
        KEY_BUDGET: LIVE_KEY_BUDGET,
        KEY_DAILY_LOSS_LIMIT: LIVE_KEY_DAILY_LOSS,
        KEY_COIN_RATIOS: LIVE_KEY_RATIOS,
    }
    for key, text in checked.items():
        if key in simple:
            add(simple[key], text)
            continue
        if key.startswith("coin_ratio:"):
            add(live_ratio_key(key.removeprefix("coin_ratio:")), text)
            continue
        if key in (KEY_FIRST_RATIO, KEY_MR_FIRST_RATIO, KEY_ADD_RATIO):
            for line in text.split("\n"):
                for coin in coins:
                    if line.startswith(f"{coin_label(coin)}:"):
                        add(live_ratio_key(coin), line)
                        break
    return {key: "\n".join(texts) for key, texts in renamed.items()}


def _allowed() -> bool:
    """실거래 허용 값(3.7.1). 시험이 이 모듈의 이름을 바꿔 거짓의 판을 흉내 낸다(F20)."""
    return bool(LIVE_TRADING_ALLOWED)
