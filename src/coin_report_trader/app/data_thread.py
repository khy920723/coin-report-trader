"""자료 스레드(M1 명세 3.8): 동작 큐, 받기, 통계 계산, 알림 저장을 맡는다.

- 창을 띄운 뒤로는 SQLite 연결 하나를 이 스레드만 연다(연결은 스레드 안에서 연다).
- `UiActions`의 동작은 큐에 넣고 바로 돌아온다. 결과는 `sink`(다리의 `post`)로 간다.
  이 스레드가 맡는 동작은 `refresh_markets`, `save_settings`, `retry_candles`, `mark_seen`,
  `delete_alert`, `clear_alerts`다. 주소의 동작(`save_webhook`, `delete_webhook`, `send_test`)은
  보내기 스레드(`send_thread`)의 큐에 바로 들어가므로 여기에 없다.
- 알림: 새 알림은 `"not_sent"`로 쓰고 알림의 번호, 분류, 가린 글만 보내기 스레드에 넘긴다(`send`).
  보낸 결과는 큐로 돌아와 이 스레드가 `discord_state`를 쓴다. 표에 쓰지 못한 알림(A-29 "알림
  저장")은 메모리에 두고 `logs/startup-errors.jsonl`에도 `"memory"` 줄로 쓴다(3.9).
- 기다림: 할 일이 없으면 다음 할 때(2의 1의 (라), 2의 3의 `blocked_until`, 설정을 읽지 못했을 때의
  다시 읽기)까지의 초를 `Clock`의 단조 시각으로 셈하고 `Clock.wait(event, 초)`로 기다린다. 다음
  할 때가 없으면 `IDLE_SECONDS`를 기다리고, 깨어나도 할 일이 없으면 다시 셈해 기다린다. `event`는
  큐에 일이 들어올 때와 끝낼 때 선다. 큐의 시간 제한이나 `time.sleep`으로 기다리지 않는다.
- 끝낼 때(`stop`)는 하던 일이 끝나기를 기다리지 않는다. 스레드는 daemon이다.
"""

import logging
import math
import sqlite3
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Final

from coin_report_trader import __version__
from coin_report_trader.core import (
    CANDLE_COUNTS,
    CANDLE_UNIT_LENGTHS,
    SAVE_TARGET_ALERT_DELETE,
    SAVE_TARGET_SETTINGS,
    AiChoice,
    AiRunner,
    AiSlot,
    AlertCategory,
    AlertsView,
    AlertView,
    AutostartStore,
    BarStats,
    CandleUnit,
    Clock,
    CoinView,
    HistoryTab,
    LiveSettings,
    LoadState,
    MarketsView,
    Mode,
    QuoteError,
    QuoteSource,
    SaveResult,
    SettingsView,
    StorageError,
    TradeSettings,
    UiUpdate,
    empty_bar_stats,
    redact,
)
from coin_report_trader.core.texts import (
    AUTOSTART_REGISTER_FAILED,
    AUTOSTART_REMOVE_FAILED,
    CONTENT_BLOCKED,
    CONTENT_BLOCKED_NOT_REQUESTED,
    CONTENT_MARKETS_FAILED,
    ERROR_BLOCKED,
    ERROR_NOT_ENOUGH_CANDLES,
    ERROR_STORAGE,
    ERROR_VAULT,
    SETTINGS_COIN_NOT_LISTED,
    SETTINGS_SAVE_FAILED,
    SETTINGS_SAVED,
    STAGE_ALERT_DELETE,
    STAGE_ALERT_SAVE,
    STAGE_CANDLE_SAVE,
    STAGE_CANDLES,
    STAGE_MARKETS,
    STAGE_REPORT,
    STAGE_SETTINGS_SAVE,
    STAGE_STARTUP,
    STAGE_STATS,
    after_candles_blocked,
    after_candles_retry,
    content_candles_failed,
    content_not_enough_candles,
    kst_when_text,
    text_save_failed,
)
from coin_report_trader.notify import alert_message
from coin_report_trader.stats import compute_bar_stats
from coin_report_trader.storage import alerts as alert_store
from coin_report_trader.storage.candles import (
    count_complete_candles,
    first_candle_start,
    has_candles,
    last_candle_start,
    read_latest_complete_candles,
    write_candles,
)
from coin_report_trader.storage.database import open_database
from coin_report_trader.storage.settings import (
    read_fetched_at,
    read_settings,
    write_fetched_at,
    write_settings,
)
from coin_report_trader.storage.states import DiscordState
from coin_report_trader.trading.quote import HttpQuoteSource, QuoteClient

from . import alert_rules, startup_errors
from .alert_rules import (
    ALERT_WRITE_INSERT,
    ALERT_WRITE_SEEN,
    ALERT_WRITE_STATE,
    AlertSpec,
    RepeatRules,
)
from .autostart import WindowsAutostart, current_command
from .memory_alerts import MemoryAlert, MemoryAlerts
from .problem_report import (
    ProblemReport,
    ProblemRow,
    kst_text,
    problem_fingerprint,
    problem_message,
    problem_source,
)
from .report_round import SESSIONS_FOLDER, LoadReport, LoadReports, ReportRoundMixin, SessionDone
from .send_types import (
    AddressState,
    DeliveryResult,
    SendJob,
    SendPort,
    WebhookResult,
)

log = logging.getLogger(__name__)

_MODULE: Final = __name__.removeprefix("coin_report_trader.")
_PROBLEM_CATEGORIES: Final = MappingProxyType(
    {AlertCategory.ERROR: "오류", AlertCategory.OTHER: "기타"}
)

DATA_THREAD_NAME: Final = "crt-data"
"""자료 스레드의 이름. 시험이 SQLite를 쓰는 스레드와 `apply`를 부르는 스레드를 가린다."""
FETCH_MARGIN: Final = timedelta(seconds=10)
"""2의 1의 (라): 봉이 끝난 때에서 이만큼 뒤에 받는다(3.5.3)."""
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
"""저장된 봉이 없을 때 봉의 경계를 세는 기준(UTC 1970-01-01 00:00)."""
SETTINGS_RETRY_SECONDS: Final = 3600.0
"""설정을 읽지 못했을 때 다시 읽는 간격(60분)."""
IDLE_SECONDS: Final = 3600.0
"""다음 할 때가 하나도 없을 때 한 번에 기다리는 초. 깨어나도 할 일이 없으면 다시 기다린다."""
BACKOFF_SECONDS: Final = (300.0, 600.0, 1200.0, 2400.0, 3600.0)
"""기다릴 시간을 주지 않은 일시 차단의 늘리는 차례(2의 3). 마지막 값 다음도 같은 값이다."""
MAX_COINS: Final = 3
CLEANUP_INTERVAL: Final = 3600.0
"""오래된 알림 지우기(3.9): 창이 뜬 직후, 보관 기간을 저장한 직후, 그 뒤로 60분마다."""
STATES_WAIT_SECONDS: Final = 30.0
"""켤 때 차례 1에서 보내기 스레드의 주소 상태를 기다리는 가장 긴 실제 시간. 지나면 모두 읽지
못한 것으로 보고 이어간다(보내기 스레드가 보관소에서 막혀도 받기가 시작되도록)."""


def _four_choices(choices: tuple[AiChoice, ...] | None) -> tuple[AiChoice, ...] | None:
    """네 칸(`AiSlot` 넷이 하나씩)이 다 있으면 `AiSlot`의 차례로 고른 묶음, 아니면 `None`."""
    if not choices or len(choices) != len(AiSlot) or {c.slot for c in choices} != set(AiSlot):
        return None
    by_slot = {choice.slot: choice for choice in choices}
    return tuple(by_slot[slot] for slot in AiSlot)


def _has_values(stats: BarStats) -> bool:
    """완성된 봉이 고른 개수 이상이라 값이 났는가(`READY`의 기준. 3.4)."""
    return stats.bar_count >= stats.requested_count


@dataclass
class _A1:
    """그 받기가 끝난 뒤 만들 A-1 하나."""

    error_name: str
    stage: str
    content: str


@dataclass
class _CoinResult:
    market: str
    a1: _A1 | None = None
    blocked: bool = False
    stats_ok: bool = False
    """봉을 새로 받고 봉 통계에 값이 났다(회차가 세션을 띄울 수 있다. M2 명세 3.5.2의 4)."""


@dataclass(frozen=True)
class _SaveTask:
    coins: tuple[str, ...]
    retention_days: int
    first_setup: bool
    candle_unit: CandleUnit | None = None
    candle_count: int | None = None
    autostart: bool | None = None
    ai_choices: tuple[AiChoice, ...] | None = None


@dataclass(frozen=True)
class _Seen:
    ids: tuple[int, ...]


@dataclass(frozen=True)
class _Delete:
    alert_id: int


@dataclass(frozen=True)
class _Clear:
    category: AlertCategory


@dataclass(frozen=True)
class _Delivery:
    result: DeliveryResult


@dataclass(frozen=True)
class _Webhook:
    result: WebhookResult


type _Task = (
    str
    | _SaveTask
    | _Seen
    | _Delete
    | _Clear
    | _Delivery
    | _Webhook
    | SessionDone
    | LoadReports
    | LoadReport
)


class _WakeEvent(threading.Event):
    """자료 스레드를 깨우는 `event`. `Clock.wait`가 처음 기다리기 시작한 때를 `waiting`에 남긴다.

    `Clock.wait`는 마감을 정한 뒤에 `event`를 기다리므로, `waiting`이 선 뒤에 시계를 옮기면 그
    기다림이 깬다. `DataWorker.wait_idle`이 이것을 본다.
    """

    def __init__(self) -> None:
        super().__init__()
        self.waiting = threading.Event()

    def wait(self, timeout: float | None = None) -> bool:
        self.waiting.set()
        return super().wait(timeout)


class DataWorker(ReportRoundMixin):
    """자료 스레드와 그 동작 큐. `UiActions`를 지킨다(주 스레드에서 부른다)."""

    def __init__(
        self,
        *,
        database_file: Path,
        logs_dir: Path,
        clock: Clock,
        quote: QuoteSource | None,
        sink: Callable[[UiUpdate], None],
        send: SendPort | None = None,
        autostart: AutostartStore | None = None,
        ai: AiRunner | None = None,
        sessions_root: Path | None = None,
    ) -> None:
        self._send = send
        self._problems = ProblemReport(logs_dir.parent)
        self._alert_round: int | None = None
        """회차를 쓴 뒤 그 회차 안에서 만드는 알림의 문제 보고 `round_number`."""
        root = logs_dir.parent / SESSIONS_FOLDER if sessions_root is None else sessions_root
        self._init_reports(ai, root)
        self._autostart: AutostartStore = WindowsAutostart() if autostart is None else autostart
        self._database_file = database_file
        self._logs_dir = logs_dir
        self._clock = clock
        self._quote = quote
        self._sink = sink
        self._lock = threading.Lock()
        self._queue: deque[_Task] = deque()
        self._wake = _WakeEvent()
        self._stop = threading.Event()
        self._idle = threading.Event()
        self._thread: threading.Thread | None = None
        # 아래는 자료 스레드만 만진다.
        self._conn: sqlite3.Connection | None = None
        self._client: QuoteClient | None = None
        self._owned_source: HttpQuoteSource | None = None
        self._rules = RepeatRules()
        self._settings: SettingsView | None = None
        self._settings_failed = False
        self._settings_retry_mono: float | None = None
        self._markets_listed: frozenset[str] | None = None
        self._next_round_mono: float | None = None
        self._blocked_mono: float | None = None
        self._blocked_until: datetime | None = None
        self._backoff = 0
        self._fetched: dict[str, datetime | None] = {}
        self._last_stats: dict[str, tuple[BarStats, bool]] = {}
        self._reached_oldest: set[tuple[str, CandleUnit]] = set()
        """거슬러 받기가 가장 이른 봉에 닿은 (코인, 단위). 단위가 바뀌면 비우고 다시 켜면 잃는다."""
        self._next_due: datetime | None = None
        self._last_view: dict[str, CoinView] = {}
        self._memory = MemoryAlerts()
        self._alerts_open = False
        self._alerts_posting = False
        self._steps_done = False
        self._next_cleanup_mono: float | None = None
        self._states_ready = threading.Event()
        self._states_late = False
        self._address_states: dict[AlertCategory, AddressState] | None = None

    # --- 주 스레드: 시작, 끝, 동작 ------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=DATA_THREAD_NAME, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """멈춤을 알린다. 하던 일이 끝나기를 기다리지 않는다."""
        self._stop.set()
        self._wake.set()
        self._states_ready.set()
        self.stop_ai()  # 도는 세션을 끝낸다. 기다리지 않는다(M2 명세 3.5.2)

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def thread(self) -> threading.Thread | None:
        return self._thread

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """큐가 비고 지금 할 일이 없어 시계의 기다림(`Clock.wait`)에 걸릴 때까지 기다린다.

        참을 돌려준 뒤에 가짜 시계를 옮기면(`advance`) 자료 스레드가 틈 없이 깬다(시험의 길,
        기다리는 것은 실제 시간이다).
        """
        done = threading.Event()
        for _ in range(max(1, int(timeout / 0.002))):
            if self._idle.is_set() and self._wake.waiting.is_set() and not self._work_pending():
                return True
            done.wait(0.002)
        return False

    def refresh_markets(self) -> None:
        self._put("refresh")

    def save_settings(
        self,
        coins: tuple[str, ...],
        retention_days: int,
        *,
        first_setup: bool,
        candle_unit: CandleUnit | None = None,
        candle_count: int | None = None,
        ai_choices: tuple[AiChoice, ...] | None = None,
        autostart: bool | None = None,
        trade: TradeSettings | None = None,
        accept_below_stop: tuple[str, ...] = (),
        live: LiveSettings | None = None,
    ) -> None:
        """`candle_unit`, `candle_count`가 `None`이면 저장된 값을 그대로 둔다(3.5.4).

        `ai_choices`가 `None`이나 빈 튜플이면 저장된 칸을 그대로 둔다(M2 명세 3.10).

        `autostart`가 `None`이면 저장된 값을 그대로 둔다(M1 명세 3.16.3).

        `trade`와 `accept_below_stop`은 받기만 하고 쓰지 않는다(M3 명세 4절 1번. 23번까지
        자료 스레드는 아무것도 하지 않는다). `live`도 받기만 한다(M5 명세 4절 3번. 15번까지).
        """
        del trade, accept_below_stop, live
        self._put(
            _SaveTask(
                tuple(coins),
                retention_days,
                first_setup,
                candle_unit,
                candle_count,
                autostart,
                None if ai_choices is None else tuple(ai_choices),
            )
        )

    def pause(self) -> None:
        """받기만 하고 하지 않는다(M3 명세 4절 1번. 23번이 멈춤을 만든다)."""

    def pause_and_sell_all(self) -> None:
        """받기만 하고 하지 않는다(M3 명세 4절 1번)."""

    def resume_user(self) -> None:
        """받기만 하고 하지 않는다(M3 명세 4절 1번)."""

    def resume_error(self) -> None:
        """받기만 하고 하지 않는다(M3 명세 4절 1번)."""

    def load_history(
        self, tab: HistoryTab, market: str | None, day: date, mode: Mode | None = None
    ) -> None:
        """받기만 하고 하지 않는다(M3 명세 4절 1번. 25번이 `HistoryView`를 보낸다)."""
        del tab, market, day, mode

    def export_history(self, tab: HistoryTab, market: str | None, path: str) -> None:
        """받기만 하고 하지 않는다(M3 명세 4절 1번. 25번이 쓴다)."""
        del tab, market, path

    def save_upbit_key(self, access: str, secret: str, created: date) -> None:
        """받기만 하고 하지 않는다(M5 명세 4절 3번. 15번이 보관소에 쓴다). 값은 어디에도 남기지
        않는다."""
        del access, secret, created

    def delete_upbit_key(self) -> None:
        """받기만 하고 하지 않는다(M5 명세 4절 3번)."""

    def prepare_live_on(self) -> None:
        """받기만 하고 하지 않는다(M5 명세 4절 3번)."""

    def turn_live_on(self) -> None:
        """받기만 하고 하지 않는다(M5 명세 4절 3번)."""

    def turn_live_off(self) -> None:
        """받기만 하고 하지 않는다(M5 명세 4절 3번)."""

    def account_visible(self, visible: bool) -> None:
        """받기만 하고 하지 않는다(M5 명세 4절 3번. 15번이 읽는다)."""
        del visible

    def refresh_account(self) -> None:
        """받기만 하고 하지 않는다(M5 명세 4절 3번)."""

    def retry_candles(self) -> None:
        self._put("retry")

    def mark_seen(self, alert_ids: tuple[int, ...]) -> None:
        self._put(_Seen(tuple(alert_ids)))

    def delete_alert(self, alert_id: int) -> None:
        self._put(_Delete(alert_id))

    def clear_alerts(self, category: AlertCategory) -> None:
        self._put(_Clear(AlertCategory(category)))

    # --- 보내기 스레드가 부르는 길(`DataPort`). 어느 스레드에서나 부르고 바로 돌아온다 ---------

    def address_states(self, states: dict[AlertCategory, AddressState]) -> None:
        """켤 때 분류마다의 주소 상태(켤 때 차례 1). 상태만 받고 주소는 받지 않는다."""
        if self._states_late:
            return
        self._address_states = dict(states)
        self._states_ready.set()

    def delivery_result(self, result: DeliveryResult) -> None:
        self._put(_Delivery(result))

    def webhook_result(self, result: WebhookResult) -> None:
        self._put(_Webhook(result))

    def _put(self, task: _Task) -> None:
        with self._lock:
            self._queue.append(task)
            self._idle.clear()
        self._wake.set()

    # --- 자료 스레드 --------------------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._open()
            self._guard(self._startup, STAGE_STARTUP)
            self._loop()
        except Exception as exc:
            log.error("자료 스레드가 멈췄습니다: %s: %s", type(exc).__name__, exc)
        finally:
            self._close()

    def _open(self) -> None:
        quote = self._quote
        if quote is None:
            self._owned_source = HttpQuoteSource()
            quote = self._owned_source
        self._client = QuoteClient(quote, self._clock)

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception as exc:
                log.warning("DB를 닫지 못했습니다: %s", type(exc).__name__)
        if self._owned_source is not None:
            try:
                self._owned_source.close()
            except Exception as exc:
                log.warning("공개 시세 연결을 닫지 못했습니다: %s", type(exc).__name__)

    def _post(self, update: UiUpdate) -> None:
        if isinstance(update, CoinView):
            if self._chosen() is None:
                return  # 봉 단위가 저장되지 않은 동안은 `CoinView`를 보내지 않는다(3.5.4).
            self._last_view[update.market] = update
        if self._stop.is_set():
            return
        try:
            self._sink(update)
        except Exception as exc:
            log.warning("화면에 보내지 못했습니다: %s", type(exc).__name__)

    # --- 켤 때 차례(3.8) ----------------------------------------------------------------------

    def _startup(self) -> None:
        self._await_address_states()  # 차례 1
        self._problems.prune(self._clock.now())  # 남기는 파일(M2 명세 3.11.1. 차례 3의 앞)
        try:
            self._conn = open_database(self._database_file)
        except StorageError as exc:
            log.error("자료 스레드가 DB를 열지 못했습니다: %s", exc)
            self._settings_unreadable()
            self._publish_alerts_first()
            return
        self._guard(self._clean_interrupted, STAGE_REPORT)  # 끊긴 회차(M2 명세 3.5.3)
        self._alert_steps()  # 차례 2, 3
        self._read_settings_and_start()  # 차례 4, 5, 6

    def _await_address_states(self) -> None:
        """차례 1: 보내기 스레드가 넘긴 분류마다의 주소 상태를 받는다. 보내기 스레드가 없으면
        기다리지 않는다. 너무 오래 오지 않으면 모두 읽지 못한 것으로 보고 이어간다."""
        if self._send is None:
            return
        if not self._states_ready.wait(STATES_WAIT_SECONDS) and not self._stop.is_set():
            log.warning("보내기 스레드의 주소 상태가 오지 않아 모두 읽지 못한 것으로 봅니다")
            self._states_late = True
            self._address_states = dict.fromkeys(AlertCategory, AddressState.UNREADABLE)

    def _settled_state(self, category: AlertCategory) -> DiscordState:
        """앞 실행에서 남은 `"not_sent"`를 바꿀 값: 주소가 저장되어 있거나 보관소를 읽지
        못했으면 `"failed"`, 없으면 `"no_address"`."""
        states = self._address_states
        if states is not None and states.get(category) in (
            AddressState.SAVED,
            AddressState.UNREADABLE,
        ):
            return DiscordState.FAILED
        return DiscordState.NO_ADDRESS

    def _alert_steps(self) -> None:
        """차례 2: 남은 `"not_sent"`를 바꾼다(보내지 않는다). 차례 3: `startup-errors.jsonl`을
        알림으로 옮긴다."""
        assert self._conn is not None
        self._steps_done = True
        failure: StorageError | None = None
        if self._send is not None:
            for category in AlertCategory:
                try:
                    alert_store.settle_not_sent(
                        self._conn, category, self._settled_state(category)
                    )
                except StorageError as exc:
                    log.warning("남은 알림의 디스코드 결과를 바꾸지 못했습니다: %s", exc)
                    failure = failure or exc
        try:
            startup_errors.move_to_alerts(
                self._conn, self._logs_dir, self._settled_state, self._on_moved_alert
            )
        except Exception as exc:
            log.warning("startup-errors.jsonl을 옮기지 못했습니다: %s", type(exc).__name__)
        # 차례 2의 실패의 A-29는 차례 3 뒤에 한 번 만든다(첫 `AlertsView` 앞이다). 앞 실행에서
        # 남은 알림만 바꾸므로 이 실행에서 만든 알림은 바꾸지 않고, 표에 쓰지 못해 메모리에
        # 두며 쓴 `"memory"` 줄은 같은 켤 때의 차례 3이 옮기지 않는다(다음에 켤 때 옮긴다).
        if self._send is not None:
            if failure is None:
                self._rules.alert_save_succeeded(ALERT_WRITE_STATE)
            else:
                self._emit_alert_save_failed(ALERT_WRITE_STATE, failure)

    def _publish_alerts_first(self) -> None:
        """차례 5의 `AlertsView`. 차례 2~4가 끝난 뒤에 처음 보내고, 그 뒤로는 알림이 바뀔 때
        보낸다."""
        if not self._alerts_open:
            self._alerts_open = True
            self._post_alerts(STAGE_STARTUP)

    def _read_settings_and_start(self) -> None:
        if self._conn is None:
            self._settings_unreadable()
            self._publish_alerts_first()
            return
        try:
            view = read_settings(self._conn)
        except Exception as exc:  # 읽기의 실패와 그 밖의 예외 모두 "설정을 읽지 못함"이다
            self._emit_unexpected(STAGE_STARTUP, None, exc)
            self._settings_unreadable()
            self._publish_alerts_first()
            return
        view = self._match_autostart_at_start(view)
        self._settings = view
        self._settings_failed = False
        self._settings_retry_mono = None
        self._cleanup_old()  # 차례 4
        self._post(view)
        for market in view.coins:
            self._send_initial_view(market)
        self._publish_alerts_first()
        self._guard(self._start_ai_status, STAGE_REPORT)
        self._guard(self._post_start_reports, STAGE_REPORT)
        if view.first_setup_done and view.coins:
            self._guard(self._fetch_round)

    def _supported(self) -> bool:
        """`supported()`가 `OSError`를 내도 지원하지 않음으로 본다."""
        try:
            return bool(self._autostart.supported())
        except OSError as exc:
            log.error("시작 앱 지원 확인이 실패했습니다: %s", redact(str(exc)))
            return False

    def _register_autostart(self) -> str | None:
        """등록된 값이 지금 exe의 명령과 다르면(없거나 옛 판의 폴더) 쓴다. 실패 글이나 `None`."""
        try:
            command = current_command()
            if self._autostart.read() != command:
                self._autostart.write(command)
        except OSError as exc:
            log.error("시작 앱 등록이 실패했습니다: %s", redact(str(exc)))
            return AUTOSTART_REGISTER_FAILED
        return None

    def _remove_autostart(self) -> str | None:
        try:
            self._autostart.delete()
        except OSError as exc:
            log.error("시작 앱 지우기가 실패했습니다: %s", redact(str(exc)))
            return AUTOSTART_REMOVE_FAILED
        return None

    def _match_autostart_at_start(self, view: SettingsView) -> SettingsView:
        """켤 때 맞추기(3.16.2): 저장된 값이 켜짐이고 지원하면 다를 때만 다시 쓴다. 꺼짐이면
        레지스트리를 건드리지 않는다. `supported`와 마지막 실패 글을 `SettingsView`에 담는다."""
        supported = self._supported()
        error = self._register_autostart() if view.autostart and supported else None
        return replace(view, autostart_supported=supported, autostart_error=error)

    def _settings_unreadable(self) -> None:
        first = not self._settings_failed
        self._settings_failed = True
        self._settings_retry_mono = self._clock.monotonic() + SETTINGS_RETRY_SECONDS
        if first:
            self._post(SettingsView(state=LoadState.FAILED))

    def _send_initial_view(self, market: str) -> None:
        """켤 때 처음의 `CoinView`: 저장된 봉으로 셈한 값(없으면 `NO_DATA`)."""
        if self._chosen() is None:
            return
        try:
            stats, incomplete = self._read_stats(market)
        except Exception as exc:  # 읽기의 실패와 셈의 예외 모두 A-30으로 알리고 다음 코인으로 간다
            self._emit_unexpected(STAGE_STARTUP, market, exc)
            name = ERROR_STORAGE if isinstance(exc, StorageError) else type(exc).__name__
            self._post(self._build_view(market, LoadState.FAILED, None, error_name=name))
            return
        state = LoadState.READY if _has_values(stats) else LoadState.NO_DATA
        self._post(self._build_view(market, state, (stats, incomplete)))

    # --- 반복 ---------------------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            self._wake.waiting.clear()
            self._idle.clear()  # 일하는 동안은 쉬는 중이 아니다(`wait_idle`이 끝나기를 기다린다)
            with self._lock:
                task = self._queue.popleft() if self._queue else None
            if task is not None:
                self._run_task(task)
                continue
            action = self._due_action()
            if action is not None:
                self._guard(action)
                continue
            with self._lock:
                if not self._queue:
                    self._idle.set()
            self._clock.wait(self._wake, self._seconds_until_due())

    def _guard(self, action: Callable[[], None], stage: str = STAGE_CANDLES) -> None:
        try:
            action()
        except Exception as exc:
            log.error("자료 스레드의 일이 실패했습니다: %s: %s", type(exc).__name__, exc)
            self._emit_unexpected(stage, None, exc)

    def _run_task(self, task: _Task) -> None:
        if isinstance(task, _Seen):
            self._guard(lambda: self._mark_seen(task), STAGE_ALERT_SAVE)
            return
        if isinstance(task, _Delete | _Clear):
            self._guard(lambda: self._delete_alerts(task), STAGE_ALERT_DELETE)
            return
        if isinstance(task, _Delivery):
            self._guard(lambda: self._on_delivery(task.result), STAGE_ALERT_SAVE)
            return
        if isinstance(task, _Webhook):
            self._guard(lambda: self._on_webhook(task.result), STAGE_ALERT_SAVE)
            return
        if isinstance(task, SessionDone):
            self._guard(lambda: self._on_session_done(task), STAGE_REPORT)
            return
        if isinstance(task, LoadReports):
            self._guard(lambda: self._post_report_list(task.market), STAGE_REPORT)
            return
        if isinstance(task, LoadReport):
            self._guard(lambda: self._do_load_report(task), STAGE_REPORT)
            return
        if self._settings_failed or self._settings is None:
            return  # 설정을 읽지 못한 동안은 아무것도 하지 않는다.
        if isinstance(task, _SaveTask):
            self._guard(lambda: self._save_settings(task), STAGE_SETTINGS_SAVE)
        elif task == "refresh":
            self._guard(self._refresh_markets, STAGE_MARKETS)
        elif task == "retry":
            self._guard(self._retry_candles)

    def _work_pending(self) -> bool:
        with self._lock:
            if self._queue:
                return True
        return self._due_action() is not None

    def _due_action(self) -> Callable[[], None] | None:
        now = self._clock.monotonic()
        if self._settings_failed:
            due = self._settings_retry_mono
            return self._retry_settings if due is not None and now >= due else None
        if self._settings is None:
            return None
        cleanup = self._next_cleanup_mono
        if cleanup is not None and now >= cleanup:
            return self._cleanup_old
        if self._blocked_mono is not None:
            return self._scheduled_round if now >= self._blocked_mono else None
        due = self._next_round_mono
        return self._scheduled_round if due is not None and now >= due else None

    def _seconds_until_due(self) -> float:
        now = self._clock.monotonic()
        if self._settings_failed:
            due = self._settings_retry_mono
        elif self._blocked_mono is not None:
            due = self._blocked_mono
        else:
            due = self._next_round_mono
        cleanup = self._next_cleanup_mono
        if cleanup is not None and not self._settings_failed:
            due = cleanup if due is None else min(due, cleanup)
        if due is None:
            return IDLE_SECONDS
        seconds = due - now
        return seconds if math.isfinite(seconds) and seconds > 0 else 0.0

    def _retry_settings(self) -> None:
        """설정을 읽지 못했을 때 60분마다 다시 읽는다."""
        if self._conn is None:
            try:
                self._conn = open_database(self._database_file)
            except StorageError as exc:
                log.error("자료 스레드가 DB를 열지 못했습니다: %s", exc)
                self._settings_retry_mono = self._clock.monotonic() + SETTINGS_RETRY_SECONDS
                return
        self._settings_retry_mono = self._clock.monotonic() + SETTINGS_RETRY_SECONDS
        if not self._steps_done:  # DB를 처음 열었다: 켤 때 차례 2, 3을 지금 한다
            self._alert_steps()
            if self._alerts_open:
                self._post_alerts(STAGE_STARTUP)
        self._read_settings_and_start()

    def _scheduled_round(self) -> None:
        """(라)의 때와 `blocked_until`의 저절로 받기."""
        settings = self._settings
        if settings is None or not settings.coins or self._chosen() is None:
            self._next_round_mono = None
            self._blocked_mono = None
            return
        # 일시 차단이 풀려 저절로 하는 받기는 회차가 아니다(M2 명세 3.5.1). (라)의 받기만 회차다.
        due = self._next_due
        is_round = self._blocked_mono is None and due is not None
        self._fetch_round(due - FETCH_MARGIN if is_round and due is not None else None)

    # --- 화면의 동작 ----------------------------------------------------------------------------

    def _retry_candles(self) -> None:
        settings = self._settings
        if settings is None or not settings.coins or self._chosen() is None:
            return
        if self._is_blocked():
            self._send_blocked_views()
            return
        self._fetch_round()

    def _refresh_markets(self) -> None:
        if self._is_blocked():
            self._post(self._markets_view(LoadState.FAILED, error_name=ERROR_BLOCKED, blocked=True))
            return
        self._post(self._markets_view(LoadState.LOADING))
        assert self._client is not None
        try:
            markets = self._client.list_markets()
        except QuoteError as exc:
            if exc.error_name == ERROR_BLOCKED:
                self._set_block(exc.retry_after)
                self._post(
                    self._markets_view(LoadState.FAILED, error_name=ERROR_BLOCKED, blocked=True)
                )
                self._emit_markets_failed(ERROR_BLOCKED, CONTENT_BLOCKED)
                self._send_blocked_views()
            else:
                self._post(self._markets_view(LoadState.FAILED, error_name=exc.error_name))
                self._emit_markets_failed(exc.error_name, CONTENT_MARKETS_FAILED)
        except Exception as exc:
            name = type(exc).__name__
            self._post(self._markets_view(LoadState.FAILED, error_name=name))
            self._emit_markets_failed(name, redact(str(exc)))
        else:
            self._markets_listed = frozenset(market.market for market in markets)
            self._rules.a31_succeeded()
            self._post(self._markets_view(LoadState.READY, markets=tuple(markets)))

    def _save_settings(self, task: _SaveTask) -> None:
        current = self._settings
        assert current is not None
        if task.coins != current.coins and not self._coins_listed(task.coins):
            self._post(SaveResult(SAVE_TARGET_SETTINGS, False, SETTINGS_COIN_NOT_LISTED))
            return
        if task.candle_count is not None and task.candle_count not in CANDLE_COUNTS:
            self._post(SaveResult(SAVE_TARGET_SETTINGS, False, SETTINGS_SAVE_FAILED))
            return
        unit = current.candle_unit if task.candle_unit is None else CandleUnit(task.candle_unit)
        count = current.candle_count if task.candle_count is None else task.candle_count
        assert self._conn is not None
        unit_changed = unit is not None and unit != current.candle_unit
        fresh = False  # 고른 단위의 봉이 어느 코인에도 없다(3.5.4)
        if unit is not None and unit_changed:
            try:
                fresh = not has_candles(self._conn, unit)
            except StorageError as exc:
                self._post(SaveResult(SAVE_TARGET_SETTINGS, False, SETTINGS_SAVE_FAILED))
                self._emit_save_failed(STAGE_SETTINGS_SAVE, None, exc)
                return
        done = True if task.first_setup else current.first_setup_done
        autostart = current.autostart if task.autostart is None else task.autostart
        four = _four_choices(task.ai_choices)
        stored_choices = current.ai_choices if four is None else four
        new = SettingsView(
            task.coins,
            task.retention_days,
            done,
            candle_unit=unit,
            candle_count=count,
            autostart=autostart,
            autostart_supported=current.autostart_supported,
            autostart_error=current.autostart_error,
            ai_choices=four or (),  # 빈 묶음은 저장된 칸을 그대로 둔다(M2 명세 3.10)
        )
        try:
            write_settings(
                self._conn,
                new,
                drop_other_unit_candles=unit_changed,
                clear_fetched_at=fresh,
                autostart=task.autostart,
            )
        except StorageError as exc:
            self._post(SaveResult(SAVE_TARGET_SETTINGS, False, SETTINGS_SAVE_FAILED))
            self._emit_save_failed(STAGE_SETTINGS_SAVE, None, exc)
            return
        except ValueError as exc:
            self._post(SaveResult(SAVE_TARGET_SETTINGS, False, SETTINGS_SAVE_FAILED))
            self._emit_unexpected(STAGE_SETTINGS_SAVE, None, exc)
            return
        # 처음 설정을 마쳤다는 것은 단위와 개수가 모두 저장된 것이다(3.7).
        new = replace(
            new,
            first_setup_done=done and unit is not None and count is not None,
            ai_choices=stored_choices,
        )
        if task.autostart is not None and new.autostart_supported:
            # 저장 때 맞추기(3.16.2): 설정 값의 저장은 이미 됐고, 레지스트리의 실패는 글로만 남는다.
            error = self._register_autostart() if task.autostart else self._remove_autostart()
            new = replace(new, autostart_error=error)
        self._settings = new
        if unit_changed:
            self._reached_oldest.clear()
            self._last_stats.clear()
            if fresh:
                self._fetched.clear()
        self._rules.a29_succeeded(STAGE_SETTINGS_SAVE, None)
        self._post(SaveResult(SAVE_TARGET_SETTINGS, True, SETTINGS_SAVED))
        self._post(new)
        if four is not None:  # 네 칸을 저장한 때(저장이 성공한 뒤) `check()`(M2 명세 3.5.4)
            self._guard(self._after_ai_choices_saved, STAGE_REPORT)
        self._cleanup_old(always_post=True)  # 보관 기간을 저장한 직후(3.9)
        if unit_changed:
            for market in new.coins:  # 받기 앞의 값: 고른 단위의 저장된 봉으로 셈한 것
                self._send_initial_view(market)
        if new.coins:
            if self._is_blocked():
                self._send_blocked_views()
            else:
                self._fetch_round()

    def _coins_listed(self, coins: tuple[str, ...]) -> bool:
        listed = self._markets_listed
        if listed is None or not 1 <= len(coins) <= MAX_COINS or len(set(coins)) != len(coins):
            return False
        return all(coin in listed for coin in coins)

    # --- 일시 차단(2의 3) ------------------------------------------------------------------------

    def _is_blocked(self) -> bool:
        return self._blocked_mono is not None and self._clock.monotonic() < self._blocked_mono

    def _expire_block(self) -> None:
        if self._blocked_mono is not None and self._clock.monotonic() >= self._blocked_mono:
            self._blocked_mono = None
            self._blocked_until = None

    def _set_block(self, retry_after: float | None) -> None:
        if retry_after is not None and math.isfinite(retry_after) and retry_after > 0:
            seconds = float(retry_after)  # 응답이 준 시간은 차례를 바꾸지 않는다.
        else:
            seconds = BACKOFF_SECONDS[min(self._backoff, len(BACKOFF_SECONDS) - 1)]
            self._backoff += 1
        self._blocked_mono = self._clock.monotonic() + seconds
        self._blocked_until = self._clock.now() + timedelta(seconds=seconds)

    def _send_blocked_views(self) -> None:
        settings = self._settings
        if settings is None:
            return
        for market in settings.coins:
            self._send_blocked_view(market)

    def _send_blocked_view(self, market: str) -> None:
        self._post(
            self._coin_view(market, LoadState.FAILED, error_name=ERROR_BLOCKED, blocked=True)
        )

    # --- 받기(2의 1, 3.8의 "받기가 끝나는 모습") --------------------------------------------------

    def _fetch_round(self, round_close: datetime | None = None) -> None:
        """한 번의 받기. `round_close`가 있으면 (라)의 받기이고 끝난 뒤 그 봉 마감 시각 T의
        회차를 시작한다(M2 명세 3.5.2). 어떻게 끝나든(예외 포함) 다음 (라)의 때를 정한다(3.5.3).

        고른 봉 단위가 없으면 받지 않는다. 멈추려고 끝난 받기는 정하지 않는다. 요청하지 않는
        일(차단 중의 `retry_candles` 등)은 이 함수를 부르지 않으므로 (라)를 새로 세지 않는다.
        `_backoff`, `blocked_*`는 건드리지 않는다.
        """
        if self._chosen() is None:
            return
        try:
            self._fetch_round_body(round_close)
        finally:
            if not self._stop.is_set():
                self._schedule_next()

    def _compute_next_due(self, unit: CandleUnit) -> datetime:
        """다음 (라)의 때(3.5.3): 고른 코인들의 저장된 마지막 봉의 시작 시각 가운데 가장 늦은 것에
        봉의 길이 × m(m은 결과가 지금보다 뒤가 되는 1 이상의 가장 작은 정수)을 더한 때에 10초를
        더한다. 저장된 봉이 하나도 없으면 UTC 1970-01-01 00:00부터 단위의 길이로 센 다음 경계다.
        (가)(나)(다)의 받기는 이 때를 바꾸지 않으므로 한 받기가 끝난 뒤에 다시 셈해도 같다."""
        length = CANDLE_UNIT_LENGTHS[unit]
        now = self._clock.now()
        latest: datetime | None = None
        settings = self._settings
        if self._conn is not None and settings is not None:
            for market in settings.coins:
                try:
                    start = last_candle_start(self._conn, market, unit)
                except StorageError:
                    continue
                if start is not None and (latest is None or start > latest):
                    latest = start
        if latest is None:
            end = _EPOCH + length * ((now - _EPOCH) // length + 1)
        else:
            end = latest + length * max(1, (now - latest) // length + 1)
        return end + FETCH_MARGIN

    def _schedule_next(self) -> datetime | None:
        """(라)의 때를 셈해 자료 스레드의 기다림("다음 할 때")에 넣는다."""
        chosen = self._chosen()
        if chosen is None:
            return None
        due = self._compute_next_due(chosen[0])
        self._next_due = due
        delay = (due - self._clock.now()).total_seconds()
        self._next_round_mono = self._clock.monotonic() + max(0.0, delay)
        return due

    def _fetch_round_body(self, round_close: datetime | None = None) -> None:
        settings = self._settings
        assert settings is not None
        self._expire_block()
        results: list[_CoinResult] = []
        blocked = False
        for market in settings.coins:
            if self._stop.is_set():
                return
            if blocked:  # 그 받기에서 아직 요청하지 않은 코인
                self._send_blocked_view(market)
                a1 = _A1(ERROR_BLOCKED, STAGE_CANDLES, CONTENT_BLOCKED_NOT_REQUESTED)
                results.append(_CoinResult(market, a1, blocked=True))
                continue
            try:
                result = self._fetch_coin(market)
            except Exception as exc:
                log.error("%s의 받기가 실패했습니다: %s: %s", market, type(exc).__name__, exc)
                self._emit_unexpected(STAGE_CANDLES, market, exc)
                self._post(self._coin_view(market, LoadState.FAILED, error_name=type(exc).__name__))
                result = _CoinResult(market)
            results.append(result)
            blocked = result.blocked
        if self._stop.is_set():
            return
        ended = self._clock.now()
        due = self._schedule_next()
        if blocked and self._blocked_until is not None:
            after = after_candles_blocked(kst_when_text(self._blocked_until, ended))
        else:
            assert due is not None
            after = after_candles_retry(kst_when_text(due, ended))
        for result in results:
            a1 = result.a1
            if a1 is not None:
                wanted = self._rules.want_a1(result.market, a1.error_name)
                spec = alert_rules.candles_failed(
                    result.market, a1.stage, a1.error_name, a1.content, after
                )
                self._emit_alert(spec, wanted, ended)
        if round_close is not None:
            self._start_round(round_close, results)

    def _fetch_coin(self, market: str) -> _CoinResult:
        assert self._client is not None
        assert self._conn is not None
        chosen = self._chosen()
        assert chosen is not None
        unit, count = chosen
        result = _CoinResult(market)
        self._post(self._coin_view(market, LoadState.LOADING))
        request: tuple[str, BaseException | None] | None = None
        db_failure: tuple[str, BaseException] | None = None  # (단계, 예외)
        try:
            last = last_candle_start(self._conn, market, unit)
            first = first_candle_start(self._conn, market, unit)
            have = count_complete_candles(self._conn, market, unit, self._clock.now())
        except StorageError as exc:
            db_failure = (STAGE_CANDLES, exc)
        else:
            missing = 0 if (market, unit) in self._reached_oldest else max(0, count - have)
            try:
                recent = self._client.fetch_recent(
                    market, unit, count=count, last_start=last, first_start=first, missing=missing
                )
            except QuoteError as exc:
                if exc.error_name == ERROR_BLOCKED:
                    self._set_block(exc.retry_after)
                    result.blocked = True
                else:
                    request = (exc.error_name, None)
            except Exception as exc:
                request = (type(exc).__name__, exc)
            else:
                try:
                    write_candles(self._conn, recent.candles)
                except StorageError as exc:
                    db_failure = (STAGE_CANDLE_SAVE, exc)
                else:
                    if recent.reached_oldest:
                        self._reached_oldest.add((market, unit))
        if result.blocked:
            self._post(
                self._coin_view(market, LoadState.FAILED, error_name=ERROR_BLOCKED, blocked=True)
            )
            result.a1 = _A1(ERROR_BLOCKED, STAGE_CANDLES, CONTENT_BLOCKED)
            return result
        if db_failure is None and request is None:
            self._finish_success(market, result)
            return result
        error_name = self._record_failure(market, unit, result, request, db_failure)
        self._post(self._coin_view(market, LoadState.FAILED, error_name=error_name))
        return result

    def _record_failure(
        self,
        market: str,
        unit: CandleUnit,
        result: _CoinResult,
        request: tuple[str, BaseException | None] | None,
        db_failure: tuple[str, BaseException] | None,
    ) -> str:
        """한 받기의 알림의 차례(DB, 요청)로 알림 하나를 정하고 오류 이름을 돌려준다."""
        if db_failure is not None:
            stage, exc = db_failure
            if stage == STAGE_CANDLE_SAVE:
                self._emit_save_failed(STAGE_CANDLE_SAVE, market, exc)
            else:
                self._emit_unexpected(stage, market, exc)
            return ERROR_STORAGE
        assert request is not None
        name, error = request
        content = content_candles_failed(unit) if error is None else redact(str(error))
        result.a1 = _A1(name, STAGE_CANDLES, content)
        return name

    def _finish_success(self, market: str, result: _CoinResult) -> None:
        """요청과 저장이 끝났다. `fetched_at`을 쓰고 값을 셈한다."""
        assert self._conn is not None
        moment = self._clock.now()
        try:
            write_fetched_at(self._conn, market, moment)
        except StorageError as exc:
            self._emit_save_failed(STAGE_CANDLE_SAVE, market, exc)
            self._post(self._coin_view(market, LoadState.FAILED, error_name=ERROR_STORAGE))
            return
        self._fetched[market] = moment
        self._rules.a29_succeeded(STAGE_CANDLE_SAVE, market)
        self._backoff = 0
        try:
            stats, _ = self._read_stats(market)
        except Exception as exc:
            # 값은 그 코인에 마지막으로 보낸 `CoinView`의 값이다(3.8). 새로 읽거나 셈하지 않는다.
            self._emit_unexpected(STAGE_STATS, market, exc)
            name = ERROR_STORAGE if isinstance(exc, StorageError) else type(exc).__name__
            self._post(self._view_with_last_values(market, LoadState.FAILED, name))
            return
        if _has_values(stats):
            self._rules.a1_succeeded(market)
            result.stats_ok = True
            self._post(self._build_view(market, LoadState.READY, self._last_stats[market]))
            return
        self._post(self._build_view(market, LoadState.NO_DATA, self._last_stats[market]))
        content = content_not_enough_candles(stats.bar_count, stats.requested_count)
        result.a1 = _A1(ERROR_NOT_ENOUGH_CANDLES, STAGE_STATS, content)

    # --- 값과 `CoinView`, `MarketsView` ---------------------------------------------------------

    def _chosen(self) -> tuple[CandleUnit, int] | None:
        """저장된 봉 단위와 봉 개수. 둘 중 하나라도 없으면 `None`(받지도 `CoinView`를 보내지도
        않는다. 3.5.4)."""
        settings = self._settings
        if settings is None or settings.candle_unit is None or settings.candle_count is None:
            return None
        return CandleUnit(settings.candle_unit), settings.candle_count

    def _read_stats(self, market: str) -> tuple[BarStats, bool]:
        """저장된 봉으로 봉 통계와 `last_incomplete`를 셈한다."""
        assert self._conn is not None
        chosen = self._chosen()
        assert chosen is not None
        unit, count = chosen
        now = self._clock.now()
        candles = read_latest_complete_candles(self._conn, market, unit, now, count)
        last = last_candle_start(self._conn, market, unit)
        stats, _ = compute_bar_stats(candles, now, market=market, unit=unit, count=count)
        incomplete = last is not None and last + CANDLE_UNIT_LENGTHS[unit] > now
        self._last_stats[market] = (stats, incomplete)
        return stats, incomplete

    def _fetched_at(self, market: str) -> datetime | None:
        if market not in self._fetched:
            if self._conn is None:
                return None
            try:
                self._fetched[market] = read_fetched_at(self._conn, market)
            except StorageError:
                return None
        return self._fetched[market]

    def _coin_view(
        self,
        market: str,
        state: LoadState,
        *,
        error_name: str | None = None,
        blocked: bool = False,
    ) -> CoinView:
        """저장된 봉으로 값을 다시 셈해 담는다. 읽지 못하면 마지막으로 셈한 값을 담는다."""
        try:
            computed: tuple[BarStats, bool] | None = self._read_stats(market)
        except Exception:
            computed = self._last_stats.get(market)
        return self._build_view(market, state, computed, error_name=error_name, blocked=blocked)

    def _view_with_last_values(
        self, market: str, state: LoadState, error_name: str | None
    ) -> CoinView:
        """그 코인에 마지막으로 보낸 `CoinView`의 값(없으면 `stats`가 `None`)을 담는다."""
        last = self._last_view.get(market)
        fetched_at, as_of = self._fetched_at(market), self._clock.now()
        if last is None:
            return self._build_view(market, state, None, error_name=error_name)
        return replace(
            last,
            state=state,
            fetched_at=fetched_at,
            error_name=error_name,
            blocked_until=None,
            as_of=as_of,
        )

    def _build_view(
        self,
        market: str,
        state: LoadState,
        computed: tuple[BarStats, bool] | None,
        *,
        error_name: str | None = None,
        blocked: bool = False,
    ) -> CoinView:
        blocked_until = self._blocked_until if blocked else None
        fetched_at = self._fetched_at(market)
        as_of = self._clock.now()
        chosen = self._chosen()
        if computed is None and chosen is not None:
            # 셈한 값이 없어도 `stats`는 늘 담는다(3.4). 봉이 하나도 없는 값이다.
            computed = (empty_bar_stats(market, chosen[0], chosen[1]), False)
        if computed is None:
            return CoinView(
                market, state, fetched_at=fetched_at, error_name=error_name,
                blocked_until=blocked_until, as_of=as_of,
            )  # fmt: skip
        stats, last_incomplete = computed
        return CoinView(
            market, state, last_incomplete=last_incomplete, fetched_at=fetched_at,
            error_name=error_name, blocked_until=blocked_until, as_of=as_of, stats=stats,
        )  # fmt: skip

    def _markets_view(
        self,
        state: LoadState,
        *,
        markets: tuple = (),
        error_name: str | None = None,
        blocked: bool = False,
    ) -> MarketsView:
        return MarketsView(
            state, markets, error_name, self._blocked_until if blocked else None, self._clock.now()
        )

    # --- 알림 저장, 보내기, 보았다는 표시, 지우기 -----------------------------------------------

    def _emit_alert(
        self,
        spec: AlertSpec,
        wanted: bool,
        created_at: datetime | None = None,
        *,
        report_id: int | None = None,
        exc: BaseException | None = None,
        module: str | None = None,
    ) -> bool:
        """되풀이가 허락한 알림은 표에 쓰고, 막은 알림은 문제 보고 표에만 한 줄 쓴다(3.11.1)."""
        if wanted:
            return self._insert(spec, created_at, report_id=report_id, exc=exc, module=module)
        self._record_problem(
            spec, None, created_at or self._clock.now(), problem_source(exc, module or _MODULE)
        )
        return False

    def _record_problem(
        self, spec: AlertSpec, alert_id: int | None, when: datetime, source: str
    ) -> None:
        """오류 알림과 기타 알림이면 문제 보고 표에 한 줄을 덧붙인다. 실패해도 막지 않는다."""
        detail = spec.detail
        self._write_problem(
            alert_id=alert_id,
            when=when,
            category=spec.category,
            code=spec.code,
            stage="" if detail is None else detail.stage,
            error_name="" if detail is None else detail.error_name,
            coin=spec.coin,
            text=spec.text,
            content=None if detail is None else detail.content,
            source=source,
        )

    def _write_problem(
        self,
        *,
        alert_id: int | None,
        when: datetime,
        category: AlertCategory,
        code: str,
        stage: str,
        error_name: str,
        coin: str | None,
        text: str,
        content: str | None,
        source: str,
        mode: Mode = Mode.PAPER,
    ) -> None:
        title = _PROBLEM_CATEGORIES.get(AlertCategory(category))
        if title is None:
            return  # 다른 분류(보고서 알림의 A-26 등)는 쓰지 않는다
        try:
            fingerprint = problem_fingerprint(code, stage, error_name, source)
            row = ProblemRow(
                alert_id=alert_id,
                occurred_kst=kst_text(when),
                app_version=f"v{__version__}",
                mode=Mode(mode).value,
                category=title,
                code=code,
                stage=stage,
                error_name=error_name,
                source=source,
                coin=coin or "",
                round_number=self._alert_round,
                repeat_24h=self._problems.count_recent(fingerprint, when) + 1,
                fingerprint=fingerprint,
                message=problem_message(text, content),
            )
            self._problems.append(row)
        except Exception as exc:  # 알림을 만들지 않고 회차와 다른 일을 막지 않는다
            log.warning("문제 보고 표에 쓰지 못했습니다: %s", type(exc).__name__)

    def _on_moved_alert(self, alert_id: int, parsed: "startup_errors._Parsed") -> None:
        detail = parsed.detail
        self._write_problem(
            alert_id=alert_id,
            when=parsed.created_at,
            category=parsed.category,
            code=parsed.code,
            stage="" if detail is None else detail.stage,
            error_name="" if detail is None else detail.error_name,
            coin=parsed.coin,
            text=parsed.text,
            content=None if detail is None else detail.content,
            source=parsed.source,
            mode=parsed.mode,
        )

    def _insert(
        self,
        spec: AlertSpec,
        created_at: datetime | None = None,
        *,
        report_id: int | None = None,
        exc: BaseException | None = None,
        module: str | None = None,
    ) -> bool:
        """알림을 `"not_sent"`로 표에 쓰고 보내기 스레드에 넘긴다. 되풀이가 막은 알림은 여기
        오기 전에 걸러진다(만들지 않은 알림은 보내지도 않는다). 표에 쓰지 못하면 그 알림은 로그에만
        남기고, 알림 저장 실패(A-29)를 메모리 알림으로 둔다(3.9)."""
        if self._conn is None:
            return False
        when = created_at or self._clock.now()
        try:
            alert_id = alert_store.insert_alert(
                self._conn,
                created_at=when,
                mode=Mode.PAPER,
                category=spec.category,
                code=spec.code,
                coin=spec.coin,
                text=spec.text,
                detail=spec.detail,
                seen=False,
                discord_state=DiscordState.NOT_SENT,
                report_id=report_id,
            )
        except StorageError as store_exc:
            log.warning("알림 %s을 저장하지 못했습니다: %s", spec.code, store_exc)
            self._keep_in_memory(store_exc)
            return False
        self._rules.alert_save_succeeded(ALERT_WRITE_INSERT)
        self._record_problem(spec, alert_id, when, problem_source(exc, module or _MODULE))
        self._submit(alert_id, spec)
        if self._alerts_open:
            self._post_alerts(STAGE_ALERT_SAVE)
        return True

    def _keep_in_memory(self, exc: BaseException) -> None:
        """알림 저장 실패의 A-29를 메모리에 둔다. 화면에 보이고 디스코드로도 보내고,
        `startup-errors.jsonl`에 `"memory"` 줄도 쓴다(쓰지 못하면 이 실행 동안만 보인다).
        표에 쓰지 못한 원래의 알림은 두지 않는다(로그에만 남는다)."""
        wanted = self._rules.want_alert_save(ALERT_WRITE_INSERT)
        spec = alert_rules.save_failed(STAGE_ALERT_SAVE, None, redact(str(exc)))
        when = self._clock.now()
        source = problem_source(exc, _MODULE)
        if not wanted:
            self._record_problem(spec, None, when, source)
            return
        alert = self._memory.add(
            created_at=when,
            mode=Mode.PAPER,
            category=spec.category,
            code=spec.code,
            coin=spec.coin,
            text=spec.text,
            detail=spec.detail,
            source=source,
        )
        self._record_problem(spec, alert.id, when, source)
        startup_errors.append_line(self._logs_dir, startup_errors.memory_line(alert))
        self._submit(alert.id, spec)
        if self._alerts_open:
            self._post_alerts(STAGE_ALERT_SAVE)

    def _submit(self, alert_id: int, spec: AlertSpec) -> None:
        """보내기 스레드에 알림의 번호, 분류, 가린 글만 넘긴다."""
        if self._send is None or self._stop.is_set():
            return
        message = alert_message(
            mode=Mode.PAPER,
            category=spec.category,
            coin=spec.coin,
            text=spec.text,
            detail=spec.detail,
        )
        try:
            self._send.submit(SendJob(alert_id, spec.category, redact(message)))
        except Exception as exc:
            log.warning("보내기 스레드에 넘기지 못했습니다: %s", type(exc).__name__)

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StorageError("DB가 열려 있지 않습니다")
        return self._conn

    def _post_alerts(self, stage: str) -> None:
        """`AlertsView`를 보낸다. 읽지 못하면 `FAILED`(줄 없음)이고 A-30(`stage`)을 만든다."""
        if self._alerts_posting:
            return
        self._alerts_posting = True
        try:
            self._post(self._alerts_view(stage))
        finally:
            self._alerts_posting = False

    def _alerts_view(self, stage: str) -> AlertsView:
        try:
            conn = self._db()
            rows = alert_store.list_alerts(conn)
            unseen = dict(alert_store.count_unseen(conn))
        except Exception as exc:
            self._emit_unexpected(stage, None, exc)
            return AlertsView((), state=LoadState.FAILED)
        views: list[AlertView] = [*rows, *(item.view() for item in self._memory.all())]
        views.sort(key=lambda view: (view.created_at, view.id), reverse=True)
        for category, count in self._memory.unseen().items():
            unseen[category] = unseen.get(category, 0) + count
        return AlertsView(tuple(views), MappingProxyType(unseen))

    def _mark_seen(self, task: _Seen) -> None:
        """줄이 이미 지워졌으면 아무것도 바꾸지 않고 `AlertsView`만 다시 보낸다."""
        stored: list[int] = []
        for alert_id in dict.fromkeys(task.ids):
            if alert_id >= 0:
                stored.append(alert_id)
                continue
            item = self._memory.get(alert_id)
            if item is not None and not item.seen:
                item.seen = True
                self._update_memory_line(item)
        if stored:
            try:
                conn = self._db()
                for alert_id in stored:
                    alert_store.mark_seen(conn, alert_id)
            except StorageError as exc:
                self._emit_alert_save_failed(ALERT_WRITE_SEEN, exc)
            else:
                self._rules.alert_save_succeeded(ALERT_WRITE_SEEN)
        self._post_alerts(STAGE_ALERT_SAVE)

    def _delete_alerts(self, task: _Delete | _Clear) -> None:
        """손으로 지우기(3.9): 그 줄이나 그 분류의 모든 줄을 지우고 `AlertsView`를 보낸다."""
        try:
            if isinstance(task, _Delete):
                removed = self._delete_one(task.alert_id)
            else:
                removed = self._clear_category(task.category)
        except StorageError as exc:
            text = text_save_failed(STAGE_ALERT_DELETE, ERROR_STORAGE)
            self._post(SaveResult(SAVE_TARGET_ALERT_DELETE, False, text))
            self._emit_save_failed(STAGE_ALERT_DELETE, None, exc)
        else:
            self._rules.a29_succeeded(STAGE_ALERT_DELETE, None)
            if removed:
                self._post(SaveResult(SAVE_TARGET_ALERT_DELETE, True))
        self._post_alerts(STAGE_ALERT_DELETE)

    def _delete_one(self, alert_id: int) -> bool:
        """지운 줄이 있었으면 참. 메모리 알림은 메모리에서 지우고 그 줄도 뺀다."""
        if alert_id < 0:
            item = self._memory.remove(alert_id)
            if item is None:
                return False
            startup_errors.remove_memory_line(self._logs_dir, item)
            return True
        return alert_store.delete_alert(self._db(), alert_id)

    def _clear_category(self, category: AlertCategory) -> bool:
        alert_store.delete_category(self._db(), category)
        for item in self._memory.of_category(category):
            self._memory.remove(item.id)
            startup_errors.remove_memory_line(self._logs_dir, item)
        return True

    def _update_memory_line(self, item: MemoryAlert) -> None:
        startup_errors.update_memory_line(self._logs_dir, item)

    def _on_delivery(self, result: DeliveryResult) -> None:
        """보낸 결과를 쓴다. 그 사이에 알림이 지워졌으면 로그에만 남긴다."""
        if result.alert_id < 0:
            item = self._memory.get(result.alert_id)
            if item is None:
                log.info("지운 메모리 알림 %s의 보낸 결과(%s)", result.alert_id, result.state.value)
                return
            item.discord_state = result.state
            self._update_memory_line(item)
        else:
            try:
                found = alert_store.set_discord_state(self._db(), result.alert_id, result.state)
            except StorageError as exc:
                log.warning("보낸 결과를 쓰지 못했습니다: %s", exc)
                self._emit_alert_save_failed(ALERT_WRITE_STATE, exc)
                return
            self._rules.alert_save_succeeded(ALERT_WRITE_STATE)
            if not found:
                log.info("지운 알림 %s의 보낸 결과(%s)", result.alert_id, result.state.value)
                return
        if self._alerts_open:
            self._post_alerts(STAGE_ALERT_SAVE)

    def _on_webhook(self, result: WebhookResult) -> None:
        """주소 저장·지우기의 결과. 실패하면 A-29(분류마다 되풀이를 센다), 성공하면 푼다."""
        if result.ok:
            self._rules.a29_webhook_succeeded(result.stage, result.category)
            return
        wanted = self._rules.want_a29_webhook(result.stage, result.category)
        name = redact(result.error_name or ERROR_VAULT)
        self._emit_alert(alert_rules.vault_failed(result.stage, name, name), wanted)

    def _cleanup_old(self, *, always_post: bool = False) -> None:
        """오래된 알림 지우기(3.9). 보관 기간을 고르기 전과 설정을 읽지 못하는 동안은 하지
        않는다. 한 뒤에는 60분 뒤로 다시 잡는다."""
        settings = self._settings
        if self._settings_failed or settings is None or settings.retention_days is None:
            self._next_cleanup_mono = None
            return
        self._next_cleanup_mono = self._clock.monotonic() + CLEANUP_INTERVAL
        try:
            deleted = alert_store.delete_expired(
                self._db(), retention_days=settings.retention_days, now=self._clock.now()
            )
        except StorageError as exc:
            self._emit_save_failed(STAGE_ALERT_DELETE, None, exc)
            return
        self._rules.a29_succeeded(STAGE_ALERT_DELETE, None)
        if (deleted or always_post) and self._alerts_open:
            self._post_alerts(STAGE_ALERT_DELETE)

    def _emit_save_failed(
        self, stage: str, coin: str | None, exc: BaseException, *, module: str | None = None
    ) -> None:
        """A-29: 쓰기의 실패."""
        wanted = self._rules.want_a29(stage, coin)
        spec = alert_rules.save_failed(stage, coin, redact(str(exc)))
        self._emit_alert(spec, wanted, exc=exc, module=module)

    def _emit_alert_save_failed(self, kind: str, exc: BaseException) -> None:
        """A-29("알림 저장"): 알림 표의 쓰기(표시, 보낸 결과, 남은 `"not_sent"` 바꾸기)의 실패.
        쓰기의 종류마다 따로 센다. 이 A-29를 표에 쓰지 못하면 `_insert`가 메모리에 둔다."""
        wanted = self._rules.want_alert_save(kind)
        spec = alert_rules.save_failed(STAGE_ALERT_SAVE, None, redact(str(exc)))
        self._emit_alert(spec, wanted, exc=exc)

    def _emit_unexpected(
        self,
        stage: str,
        coin: str | None,
        exc: BaseException,
        *,
        after: str | None = None,
        content: str | None = None,
        module: str | None = None,
    ) -> None:
        """A-30: 읽기의 실패와 그 밖의 예외. `after`와 `content`는 AI 보고서의 일이 정한다."""
        name = ERROR_STORAGE if isinstance(exc, StorageError) else type(exc).__name__
        wanted = self._rules.want_a30(stage, coin, type(exc).__name__, self._clock.monotonic())
        text = redact(str(exc)) if content is None else content
        if after is None:
            spec = alert_rules.unexpected(stage, coin, name, text)
        else:
            spec = alert_rules.unexpected(stage, coin, name, text, after)
        self._emit_alert(spec, wanted, exc=exc, module=module)

    def _emit_markets_failed(self, error_name: str, content: str) -> None:
        """A-31."""
        wanted = self._rules.want_a31(error_name)
        self._emit_alert(alert_rules.markets_failed(error_name, content), wanted)
