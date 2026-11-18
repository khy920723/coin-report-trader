"""자료 스레드(M1 명세 3.8): 동작 큐, 받기, 통계 계산, 알림 저장을 맡는다.

- 창을 띄운 뒤로는 SQLite 연결 하나를 이 스레드만 연다(연결은 스레드 안에서 연다).
- `UiActions`의 동작은 큐에 넣고 바로 돌아온다. 결과는 `sink`(다리의 `post`)로 간다.
  19번이 맡는 동작은 `refresh_markets`, `save_settings`, `retry_candles`이고, 알림센터의 동작
  (`mark_seen`, `delete_alert`, `clear_alerts`)과 주소의 동작(`save_webhook`, `delete_webhook`,
  `send_test`)은 27번의 것이어서 아무 일도 하지 않는다.
- 기다림: 할 일이 없으면 다음 할 때(2의 1의 (라), 2의 3의 `blocked_until`, 설정을 읽지 못했을 때의
  다시 읽기)까지의 초를 `Clock`의 단조 시각으로 셈하고 `Clock.wait(event, 초)`로 기다린다. 다음
  할 때가 없으면 `IDLE_SECONDS`를 기다리고, 깨어나도 할 일이 없으면 다시 셈해 기다린다. `event`는
  큐에 일이 들어올 때와 끝낼 때 선다. 큐의 시간 제한이나 `time.sleep`으로 기다리지 않는다.
- 끝낼 때(`stop`)는 하던 일이 끝나기를 기다리지 않는다. 스레드는 daemon이다.
"""

import logging
import math
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

from coin_report_trader.core import (
    SAVE_TARGET_SETTINGS,
    AlertCategory,
    CandleUnit,
    Clock,
    CoinView,
    LoadState,
    MarketsView,
    Mode,
    QuoteError,
    QuoteSource,
    SaveResult,
    SettingsView,
    StorageError,
    UiUpdate,
    redact,
)
from coin_report_trader.core.texts import (
    CONTENT_BLOCKED,
    CONTENT_BLOCKED_NOT_REQUESTED,
    CONTENT_MARKETS_FAILED,
    CONTENT_NOT_ENOUGH_CANDLES,
    ERROR_BLOCKED,
    ERROR_NOT_ENOUGH_CANDLES,
    ERROR_STORAGE,
    SETTINGS_COIN_NOT_LISTED,
    SETTINGS_SAVE_FAILED,
    SETTINGS_SAVED,
    STAGE_CANDLE_SAVE,
    STAGE_CANDLES,
    STAGE_MARKETS,
    STAGE_SETTINGS_SAVE,
    STAGE_STARTUP,
    STAGE_STATS,
    after_candles_blocked,
    after_candles_retry,
    content_candles_failed,
    kst_when_text,
)
from coin_report_trader.stats import CandleStats, compute_candle_stats
from coin_report_trader.storage import alerts as alert_store
from coin_report_trader.storage.candles import last_candle_start, read_candles, write_candles
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
from .alert_rules import AlertSpec, RepeatRules

log = logging.getLogger(__name__)

DATA_THREAD_NAME: Final = "crt-data"
"""자료 스레드의 이름. 시험이 SQLite를 쓰는 스레드와 `apply`를 부르는 스레드를 가린다."""
FETCH_INTERVAL: Final = 3600.0
"""2의 1의 (라): 앞 받기가 끝난 때부터 60분마다 받는다."""
SETTINGS_RETRY_SECONDS: Final = 3600.0
"""설정을 읽지 못했을 때 다시 읽는 간격(60분)."""
IDLE_SECONDS: Final = 3600.0
"""다음 할 때가 하나도 없을 때 한 번에 기다리는 초. 깨어나도 할 일이 없으면 다시 기다린다."""
BACKOFF_SECONDS: Final = (300.0, 600.0, 1200.0, 2400.0, 3600.0)
"""기다릴 시간을 주지 않은 일시 차단의 늘리는 차례(2의 3). 마지막 값 다음도 같은 값이다."""
UNITS: Final = (CandleUnit.DAY, CandleUnit.MINUTE_60)
"""한 코인은 일봉, 60분봉의 차례로 받는다."""
MAX_COINS: Final = 3


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


@dataclass(frozen=True)
class _SaveTask:
    coins: tuple[str, ...]
    retention_days: int
    first_setup: bool


type _Task = str | _SaveTask


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


class DataWorker:
    """자료 스레드와 그 동작 큐. `UiActions`를 지킨다(주 스레드에서 부른다)."""

    def __init__(
        self,
        *,
        database_file: Path,
        logs_dir: Path,
        clock: Clock,
        quote: QuoteSource | None,
        sink: Callable[[UiUpdate], None],
    ) -> None:
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
        self._conn = None
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
        self._last_stats: dict[str, CandleStats] = {}
        self._last_view: dict[str, CoinView] = {}

    # --- 주 스레드: 시작, 끝, 동작 ------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=DATA_THREAD_NAME, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """멈춤을 알린다. 하던 일이 끝나기를 기다리지 않는다."""
        self._stop.set()
        self._wake.set()

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
        self, coins: tuple[str, ...], retention_days: int, *, first_setup: bool
    ) -> None:
        self._put(_SaveTask(tuple(coins), retention_days, first_setup))

    def retry_candles(self) -> None:
        self._put("retry")

    def mark_seen(self, alert_ids: tuple[int, ...]) -> None:
        """27번의 동작. 아무 일도 하지 않는다."""

    def delete_alert(self, alert_id: int) -> None:
        """27번의 동작. 아무 일도 하지 않는다."""

    def clear_alerts(self, category: AlertCategory) -> None:
        """27번의 동작. 아무 일도 하지 않는다."""

    def save_webhook(self, category: AlertCategory, url: str) -> None:
        """27번의 동작. 아무 일도 하지 않는다."""

    def delete_webhook(self, category: AlertCategory) -> None:
        """27번의 동작. 아무 일도 하지 않는다."""

    def send_test(self, category: AlertCategory) -> None:
        """27번의 동작. 아무 일도 하지 않는다."""

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
            self._last_view[update.market] = update
        if self._stop.is_set():
            return
        try:
            self._sink(update)
        except Exception as exc:
            log.warning("화면에 보내지 못했습니다: %s", type(exc).__name__)

    # --- 켤 때 차례(3.8) ----------------------------------------------------------------------

    def _startup(self) -> None:
        try:
            self._conn = open_database(self._database_file)
        except StorageError as exc:
            log.error("자료 스레드가 DB를 열지 못했습니다: %s", exc)
            self._settings_unreadable()
            return
        try:  # 차례 3: `"startup"` 줄을 알림으로 옮긴다.
            startup_errors.move_to_alerts(self._conn, self._logs_dir)
        except Exception as exc:
            log.warning("startup-errors.jsonl을 옮기지 못했습니다: %s", type(exc).__name__)
        self._read_settings_and_start()  # 차례 5, 6

    def _read_settings_and_start(self) -> None:
        if self._conn is None:
            self._settings_unreadable()
            return
        try:
            view = read_settings(self._conn)
        except Exception as exc:  # 읽기의 실패와 그 밖의 예외 모두 "설정을 읽지 못함"이다
            self._emit_unexpected(STAGE_STARTUP, None, exc)
            self._settings_unreadable()
            return
        self._settings = view
        self._settings_failed = False
        self._settings_retry_mono = None
        self._post(view)
        for market in view.coins:
            self._send_initial_view(market)
        if view.first_setup_done and view.coins:
            self._guard(self._fetch_round)

    def _settings_unreadable(self) -> None:
        first = not self._settings_failed
        self._settings_failed = True
        self._settings_retry_mono = self._clock.monotonic() + SETTINGS_RETRY_SECONDS
        if first:
            self._post(SettingsView(state=LoadState.FAILED))

    def _send_initial_view(self, market: str) -> None:
        """켤 때 처음의 `CoinView`: 저장된 봉으로 셈한 값(없으면 `NO_DATA`)."""
        try:
            stats = self._read_stats(market)
        except Exception as exc:  # 읽기의 실패와 셈의 예외 모두 A-30으로 알리고 다음 코인으로 간다
            self._emit_unexpected(STAGE_STARTUP, market, exc)
            name = ERROR_STORAGE if isinstance(exc, StorageError) else type(exc).__name__
            self._post(self._build_view(market, LoadState.FAILED, None, error_name=name))
            return
        state = LoadState.READY if stats.has_values else LoadState.NO_DATA
        self._post(self._build_view(market, state, stats))

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
        self._read_settings_and_start()

    def _scheduled_round(self) -> None:
        """(라)의 때와 `blocked_until`의 저절로 받기."""
        settings = self._settings
        if settings is None or not settings.coins:
            self._next_round_mono = None
            self._blocked_mono = None
            return
        self._fetch_round()

    # --- 화면의 동작 ----------------------------------------------------------------------------

    def _retry_candles(self) -> None:
        settings = self._settings
        if settings is None or not settings.coins:
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
        done = True if task.first_setup else current.first_setup_done
        new = SettingsView(task.coins, task.retention_days, done)
        assert self._conn is not None
        try:
            write_settings(self._conn, new)
        except StorageError as exc:
            self._post(SaveResult(SAVE_TARGET_SETTINGS, False, SETTINGS_SAVE_FAILED))
            self._emit_save_failed(STAGE_SETTINGS_SAVE, None, exc)
            return
        except ValueError as exc:
            self._post(SaveResult(SAVE_TARGET_SETTINGS, False, SETTINGS_SAVE_FAILED))
            self._emit_unexpected(STAGE_SETTINGS_SAVE, None, exc)
            return
        self._settings = new
        self._rules.a29_succeeded(STAGE_SETTINGS_SAVE, None)
        self._post(SaveResult(SAVE_TARGET_SETTINGS, True, SETTINGS_SAVED))
        self._post(new)
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

    def _fetch_round(self) -> None:
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
        self._next_round_mono = self._clock.monotonic() + FETCH_INTERVAL
        if blocked and self._blocked_until is not None:
            after = after_candles_blocked(kst_when_text(self._blocked_until, ended))
        else:
            after = after_candles_retry(
                kst_when_text(ended + timedelta(seconds=FETCH_INTERVAL), ended)
            )
        for result in results:
            a1 = result.a1
            if a1 is not None and self._rules.want_a1(result.market, a1.error_name):
                spec = alert_rules.candles_failed(
                    result.market, a1.stage, a1.error_name, a1.content, after
                )
                self._insert(spec, ended)

    def _fetch_coin(self, market: str) -> _CoinResult:
        assert self._client is not None
        assert self._conn is not None
        result = _CoinResult(market)
        self._post(self._coin_view(market, LoadState.LOADING))
        requests: list[tuple[CandleUnit, str, BaseException | None]] = []
        db_failure: tuple[str, BaseException] | None = None  # (단계, 예외). 먼저 난 것
        for unit in UNITS:
            try:
                last = last_candle_start(self._conn, market, unit)
            except StorageError as exc:
                db_failure = db_failure or (STAGE_CANDLES, exc)
                continue
            try:
                candles = self._client.fetch_candles(market, unit, last_start=last)
            except QuoteError as exc:
                if exc.error_name == ERROR_BLOCKED:
                    self._set_block(exc.retry_after)
                    result.blocked = True
                    break
                requests.append((unit, exc.error_name, None))
                continue
            except Exception as exc:
                requests.append((unit, type(exc).__name__, exc))
                continue
            try:
                write_candles(self._conn, candles)
            except StorageError as exc:
                db_failure = db_failure or (STAGE_CANDLE_SAVE, exc)
        if result.blocked:
            self._post(
                self._coin_view(market, LoadState.FAILED, error_name=ERROR_BLOCKED, blocked=True)
            )
            result.a1 = _A1(ERROR_BLOCKED, STAGE_CANDLES, CONTENT_BLOCKED)
            return result
        if db_failure is None and not requests:
            self._finish_success(market, result)
            return result
        error_name = self._record_failure(market, result, requests, db_failure)
        self._post(self._coin_view(market, LoadState.FAILED, error_name=error_name))
        return result

    def _record_failure(
        self,
        market: str,
        result: _CoinResult,
        requests: list[tuple[CandleUnit, str, BaseException | None]],
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
        first_unit, name, error = requests[0]
        del first_unit
        if error is None:
            units = tuple(unit for unit, _, _ in requests)
            content = content_candles_failed(units)
        else:
            content = redact(str(error))
        result.a1 = _A1(name, STAGE_CANDLES, content)
        return name

    def _finish_success(self, market: str, result: _CoinResult) -> None:
        """두 단위 모두 요청과 저장이 끝났다. `fetched_at`을 쓰고 값을 셈한다."""
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
            stats = self._read_stats(market)
        except Exception as exc:
            # 값은 그 코인에 마지막으로 보낸 `CoinView`의 값이다(3.8). 새로 읽거나 셈하지 않는다.
            self._emit_unexpected(STAGE_STATS, market, exc)
            name = ERROR_STORAGE if isinstance(exc, StorageError) else type(exc).__name__
            self._post(self._view_with_last_values(market, LoadState.FAILED, name))
            return
        if stats.has_values:
            self._rules.a1_succeeded(market)
            self._post(self._build_view(market, LoadState.READY, stats))
            return
        self._post(self._build_view(market, LoadState.NO_DATA, stats))
        result.a1 = _A1(ERROR_NOT_ENOUGH_CANDLES, STAGE_STATS, CONTENT_NOT_ENOUGH_CANDLES)

    # --- 값과 `CoinView`, `MarketsView` ---------------------------------------------------------

    def _read_stats(self, market: str) -> CandleStats:
        assert self._conn is not None
        candles = []
        for unit in UNITS:
            candles.extend(read_candles(self._conn, market, unit))
        stats = compute_candle_stats(candles, self._clock.now())
        self._last_stats[market] = stats
        return stats

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
            stats: CandleStats | None = self._read_stats(market)
        except Exception:
            stats = self._last_stats.get(market)
        return self._build_view(market, state, stats, error_name=error_name, blocked=blocked)

    def _view_with_last_values(
        self, market: str, state: LoadState, error_name: str | None
    ) -> CoinView:
        """그 코인에 마지막으로 보낸 `CoinView`의 값(없으면 모두 `None`)을 담는다."""
        last = self._last_view.get(market)
        fetched_at, as_of = self._fetched_at(market), self._clock.now()
        if last is None:
            return CoinView(
                market, state, fetched_at=fetched_at, error_name=error_name, as_of=as_of
            )
        return CoinView(
            market, state, last.periods, last.indicator, last.last_incomplete, fetched_at,
            error_name, None, as_of,
        )  # fmt: skip

    def _build_view(
        self,
        market: str,
        state: LoadState,
        stats: CandleStats | None,
        *,
        error_name: str | None = None,
        blocked: bool = False,
    ) -> CoinView:
        blocked_until = self._blocked_until if blocked else None
        fetched_at = self._fetched_at(market)
        as_of = self._clock.now()
        if stats is None:
            return CoinView(
                market, state, fetched_at=fetched_at, error_name=error_name,
                blocked_until=blocked_until, as_of=as_of,
            )  # fmt: skip
        return CoinView(
            market, state, stats.periods, stats.indicator, stats.last_incomplete, fetched_at,
            error_name, blocked_until, as_of,
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

    # --- 알림 저장 ------------------------------------------------------------------------------

    def _insert(self, spec: AlertSpec, created_at: datetime | None = None) -> bool:
        """알림을 표에 쓴다. 쓰지 못하면 로그에만 남긴다(메모리 알림은 27번의 몫)."""
        if self._conn is None:
            return False
        try:
            alert_store.insert_alert(
                self._conn,
                created_at=created_at or self._clock.now(),
                mode=Mode.PAPER,
                category=spec.category,
                code=spec.code,
                coin=spec.coin,
                text=spec.text,
                detail=spec.detail,
                seen=False,
                discord_state=DiscordState.NO_ADDRESS,
            )
        except StorageError as exc:
            log.warning("알림 %s을 저장하지 못했습니다: %s", spec.code, exc)
            return False
        return True

    def _emit_save_failed(self, stage: str, coin: str | None, exc: BaseException) -> None:
        """A-29: 쓰기의 실패."""
        if self._rules.want_a29(stage, coin):
            self._insert(alert_rules.save_failed(stage, coin, redact(str(exc))))

    def _emit_unexpected(self, stage: str, coin: str | None, exc: BaseException) -> None:
        """A-30: 읽기의 실패와 그 밖의 예외."""
        name = ERROR_STORAGE if isinstance(exc, StorageError) else type(exc).__name__
        if self._rules.want_a30(stage, coin, type(exc).__name__, self._clock.monotonic()):
            self._insert(alert_rules.unexpected(stage, coin, name, redact(str(exc))))

    def _emit_markets_failed(self, error_name: str, content: str) -> None:
        """A-31."""
        if self._rules.want_a31(error_name):
            self._insert(alert_rules.markets_failed(error_name, content))
