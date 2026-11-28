"""회차와 AI 보고서 잇기(M2 명세 3.5, 3.9, 3.10, 3.11). 자료 스레드(`DataWorker`)가 상속한다.

회차의 차례를 자료 스레드가 맡고, 코인 세션은 AI 스레드(`ai_threads`)가 기다린다. 이 모듈의
메서드는 모두 자료 스레드에서만 부른다(`start_session`의 스레드는 결과를 큐에 넣을 뿐이다).
DB는 `storage/`의 함수로만 만지고, 답의 검사와 지난 판단의 셈은 `ai/`의 부품이 한다.

`DataWorker`가 갖춰야 하는 것: `_conn`, `_clock`, `_settings`, `_client`, `_rules`, `_last_stats`,
`_stop`, `_post`, `_put`, `_insert`, `_chosen`, `_is_blocked`, `_set_block`, `_emit_save_failed`,
`_emit_unexpected`, `_sink`.
"""

import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from coin_report_trader.ai import answers as ai_answers
from coin_report_trader.ai import inputs as ai_inputs
from coin_report_trader.ai.claude_cli import ClaudeCodeRunner
from coin_report_trader.ai.csv_files import Row
from coin_report_trader.ai.last_result import (
    PastJudgment,
    compute_last_result,
    target_close_utc,
)
from coin_report_trader.core import (
    CANDLE_UNIT_LENGTHS,
    SAVE_TARGET_REPORT,
    SESSION_TIMEOUT_SECONDS,
    AiRunner,
    AiSlot,
    AiStatus,
    AiStatusView,
    CandleUnit,
    ClaudeCheck,
    ClaudeState,
    Direction,
    FailureKind,
    LastResult,
    LoadState,
    Mode,
    NoOrderReason,
    QuoteError,
    ReportListView,
    ReportState,
    RoundStage,
    RoundTrigger,
    SaveResult,
    SessionOutcome,
    SessionRequest,
    SessionResult,
    SkipReason,
    StorageError,
    redact,
)
from coin_report_trader.core.texts import (
    ERROR_BLOCKED,
    ERROR_CLAUDE_NOT_FOUND,
    ERROR_VERSION_UNREADABLE,
    REPORT_UNREADABLE,
    STAGE_ORDER,
    STAGE_REPORT,
    STAGE_REPORT_SAVE,
    error_version_out_of_range,
    kst_when_text,
)
from coin_report_trader.storage import reports as report_store
from coin_report_trader.storage import trading as trade_store
from coin_report_trader.storage.candles import read_candles

from . import alert_rules
from .ai_threads import start_session
from .problem_report import problem_source
from .trade_round import _OrderTarget

if TYPE_CHECKING:
    from coin_report_trader.app.data_thread import _CoinResult

log = logging.getLogger(__name__)

SESSIONS_FOLDER: Final = "ai-sessions"
"""자료 폴더 아래의 세션 폴더를 담는 폴더(3.7.2)."""
LOGGED_OUT_REASON: Final = "claude auth status가 로그인되지 않았다고 알렸습니다."
_MODULE: Final = __name__.removeprefix("coin_report_trader.")


@dataclass(frozen=True)
class SessionDone:
    """AI 스레드가 자료 스레드의 큐에 넣는 결과. `outcome`은 `SessionResult`나 실행기의 예외."""

    report_id: int
    outcome: "SessionResult | Exception"


@dataclass(frozen=True)
class LoadReports:
    market: str
    mode: Mode | None = None


@dataclass(frozen=True)
class LoadReport:
    report_id: int


@dataclass
class _Round:
    round_id: int
    number: int
    close: datetime
    next_close: datetime
    unit: CandleUnit


@dataclass
class _Pending:
    """띄운 세션 하나가 끝날 때까지 메모리에 두는 값."""

    report_id: int
    round: _Round
    market: str
    base_price: Decimal
    price_read_at: datetime
    last_report_id: int | None
    last_result: LastResult | None
    last_evidence: tuple[tuple[AiSlot, int], ...] | None


class ReportRoundMixin:
    """회차, AI 스레드, 보고서 쓰기, AI 끄기. `DataWorker`의 일부다."""

    # `DataWorker`가 채우는 이름(위 설명). 형식 검사는 하지 않는다.
    _conn: Any
    _clock: Any
    _settings: Any
    _client: Any
    _rules: Any
    _last_stats: Any
    _stop: Any
    _post: Callable[..., None]
    _put: Callable[..., None]
    _insert: Callable[..., bool]
    _emit_alert: Callable[..., bool]
    _chosen: Callable[[], Any]
    _is_blocked: Callable[[], bool]
    _set_block: Callable[..., None]
    _emit_save_failed: Callable[..., None]
    _emit_unexpected: Callable[..., None]
    # `TradeMixin`(trade_round)이 채우는 이름
    _sold_canceled: Any
    _round_may_trade: Callable[[], bool]
    _round_cleanup: Callable[..., None]
    _set_stage: Callable[..., None]
    _mark_method: Callable[[int], None]
    _no_order: Callable[..., None]
    _order_for_report: Callable[..., None]
    _final_trades: Callable[[str], str]
    _trade: Callable[[], Any]
    _round_finished: Callable[[], None]
    _post_trade_views: Callable[[], None]
    _mode: Mode

    def _init_reports(self, runner: AiRunner | None, sessions_root: Path) -> None:
        self._sessions_root = sessions_root
        self._runner: AiRunner = (
            ClaudeCodeRunner(sessions_root) if runner is None else runner
        )  # 만들기만 한다(실행 파일을 찾지 않는다)
        self._ai_status: AiStatus | None = None
        self._ai_version: str | None = None
        self._pending: dict[int, _Pending] = {}
        self._active_round: _Round | None = None

    # --- 주 스레드: 동작 -----------------------------------------------------------------------

    def load_reports(self, market: str, mode: Mode | None = None) -> None:
        """`mode`가 `None`이면 지금 모드의 보고서다(M5 명세 3.4)."""
        self._put(LoadReports(market, None if mode is None else Mode(mode)))

    def load_report(self, report_id: int) -> None:
        self._put(LoadReport(report_id))

    def stop_ai(self) -> None:
        """끝낼 때: 도는 세션의 프로세스를 모두 끝낸다. 기다리지 않는다(3.5.2)."""
        try:
            self._runner.stop_all()
        except Exception as exc:
            log.warning("세션을 끝내지 못했습니다: %s", type(exc).__name__)

    # --- AI 상태(3.5.4) ------------------------------------------------------------------------

    def _ai_choices_saved(self) -> bool:
        settings = self._settings
        if settings is None:
            return False
        return {choice.slot for choice in settings.ai_choices} == set(AiSlot)

    def _set_ai_status(self, status: AiStatus, version: str | None) -> None:
        changed = (status, version) != (self._ai_status, self._ai_version)
        self._ai_status = status
        self._ai_version = version
        if status is AiStatus.ON:
            self._rules.ai_on()
        if changed:
            self._post(AiStatusView(status, version, self._clock.now()))

    def _call_check(self) -> ClaudeCheck | None:
        """`check()`를 부른다. 예외가 나면 AI를 끄고 `None`이다."""
        try:
            return self._runner.check()
        except Exception as exc:
            log.error("Claude Code 확인이 실패했습니다: %s: %s", type(exc).__name__, exc)
            self._ai_off(None, exc)
            return None

    def _apply_check(self, check: ClaudeCheck) -> bool:
        """`check()`의 결과를 `AiStatus`에 담는다. 쓸 수 있으면(`READY`, `LOGGED_OUT`) 참이다.
        쓸 수 없으면 AI를 끄고 A-19를 만든다(되풀이는 `ON`으로 돌아오기 전까지 한 번)."""
        state = check.state
        if state in (ClaudeState.READY, ClaudeState.LOGGED_OUT):
            self._set_ai_status(AiStatus.ON, check.version)
            return True
        if state is ClaudeState.VERSION_OUT_OF_RANGE:
            status = AiStatus.VERSION_OFF
            name = error_version_out_of_range(check.version or "")
        elif state is ClaudeState.VERSION_UNREADABLE:
            status, name = AiStatus.NOT_FOUND, ERROR_VERSION_UNREADABLE
        else:
            status, name = AiStatus.NOT_FOUND, ERROR_CLAUDE_NOT_FOUND
        self._set_ai_status(status, check.version)
        wanted = self._rules.want_a19()
        self._emit_alert(alert_rules.claude_off(name), wanted, module=_MODULE)
        return False

    def _start_ai_status(self) -> None:
        """켤 때 차례 5: 처음 `AiStatusView`를 보낸다. 네 칸이 다 저장되어 있으면 `check()`를
        부른다(시작할 때의 버전 확인)."""
        if not self._ai_choices_saved():
            self._set_ai_status(AiStatus.NOT_CHOSEN, None)
            return
        check = self._call_check()
        if check is not None:
            self._apply_check(check)

    def _after_ai_choices_saved(self) -> None:
        """네 칸을 저장한 때(저장이 성공한 뒤): `check()`를 불러 상태를 바꾼다."""
        check = self._call_check()
        if check is not None:
            self._apply_check(check)

    def _ai_off(self, coin: str | None, exc: BaseException, *, hide_text: bool = False) -> None:
        """AI 보고서의 일에서 난 오류(M3 명세 3.10.2). 3단계부터는 AI를 끄지 않고(`ERROR_OFF`로 끄는
        길을 없앴다) 오류 멈춤에 든다. 쓰기 실패면 A-29("보고서 저장"), 그 밖은 A-30이다. 두 알림이
        오류 멈춤에 들게 한다. `hide_text`가 참이면(답을 다루다 난 예외) A-30의 내용에 예외의 글을
        쓰지 않는다."""
        if isinstance(exc, StorageError):
            self._emit_save_failed(STAGE_REPORT_SAVE, coin, exc, module=_MODULE)
            return
        content = redact(str(exc))
        if hide_text:
            content = f"{type(exc).__name__} · {problem_source(exc, _MODULE)}"
        self._emit_unexpected(
            STAGE_REPORT,
            coin,
            exc,
            after=alert_rules.AFTER_AI_OFF_ERROR,
            content=content,
            module=_MODULE,
        )

    # --- 켤 때 --------------------------------------------------------------------------------

    def _clean_interrupted(self) -> None:
        """끊긴 회차와 세션 폴더를 정리한다(3.5.3). DB를 연 직후에 한다."""
        try:
            report_store.interrupt_unfinished(self._conn, now=self._clock.now())
        except StorageError as exc:
            log.warning("끊긴 회차를 정리하지 못했습니다: %s", exc)
            self._ai_off(None, exc)
        root = self._sessions_root
        try:
            children = list(root.iterdir()) if root.is_dir() else []
        except OSError as exc:
            log.warning("세션 폴더를 읽지 못했습니다: %s", type(exc).__name__)
            return
        for child in children:
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except OSError as exc:
                log.warning("세션 폴더를 지우지 못했습니다: %s", type(exc).__name__)

    def _post_start_reports(self) -> None:
        """켤 때 차례 5: 코인마다 `ReportListView`를 보낸다(처음 `AiStatusView` 뒤)."""
        settings = self._settings
        if settings is None:
            return
        for market in settings.coins:
            self._post_report_list(market)

    # --- 읽기 동작 ----------------------------------------------------------------------------

    def _post_report_list(self, market: str, mode: Mode | None = None) -> None:
        try:
            chosen = self._mode if mode is None else mode
            items = report_store.list_round_items(self._conn, market, chosen)
        except Exception as exc:
            self._emit_unexpected(STAGE_REPORT, market, exc)
            self._post(ReportListView(market, (), LoadState.FAILED))
            return
        self._post(ReportListView(market, items, LoadState.READY))

    def _do_load_report(self, task: LoadReport) -> None:
        try:
            view = report_store.read_report(self._conn, task.report_id)
        except Exception as exc:
            self._emit_unexpected(STAGE_REPORT, None, exc)
            view = None
        if view is None:
            self._post(SaveResult(SAVE_TARGET_REPORT, False, REPORT_UNREADABLE))
            return
        self._post(view)

    # --- 회차(3.5.2) --------------------------------------------------------------------------

    def _start_round(self, close: datetime, results: "list[_CoinResult]") -> None:
        """(라)의 받기가 끝난 뒤 회차를 시작한다. 시작하지 않는 때는 로그에만 남긴다."""
        settings = self._settings
        if settings is None or not settings.first_setup_done or self._chosen() is None:
            return
        if not self._ai_choices_saved():
            return
        if self._conn is None or self._stop.is_set():
            return
        if not self._round_may_trade():
            log.info("켤 때의 소급 판정과 손절 점검이 끝나지 않아 이번 회차를 건너뜁니다")
            return
        try:
            if self._active_round is not None and report_store.has_running_report(
                self._conn, self._active_round.round_id
            ):
                log.info("앞 회차가 끝나지 않아 이번 회차를 건너뜁니다")
                return
        except StorageError as exc:
            self._ai_off(None, exc)
            return
        check = self._call_check()
        if check is None or not self._apply_check(check):
            return
        try:
            self._run_round(close, results, logged_out=check.state is ClaudeState.LOGGED_OUT)
        except StorageError as exc:
            self._ai_off(None, exc)
        except Exception as exc:
            self._ai_off(None, exc)
        finally:
            self._alert_round = None

    def _run_round(
        self, close: datetime, results: "list[_CoinResult]", *, logged_out: bool
    ) -> None:
        settings = self._settings
        unit, _ = self._chosen()
        now = self._clock.now()
        number = report_store.max_round_number(self._conn, self._mode) + 1
        next_close = close + CANDLE_UNIT_LENGTHS[unit]
        round_id = report_store.insert_round(
            self._conn,
            mode=self._mode,
            number=number,
            trigger=RoundTrigger.BAR_CLOSE,
            unit=unit,
            close_at=close,
            next_at=next_close,
            started_at=now,
        )
        current = _Round(round_id, number, close, next_close, unit)
        self._active_round = current
        self._alert_round = number  # 이 회차 안에서 만드는 알림의 문제 보고 `round_number`
        self._round_cleanup(round_id, number)  # 정리(M3 명세 3.5.1의 4)
        self._set_stage(round_id, RoundStage.STATS)
        by_market = {result.market: result for result in results}
        if logged_out:
            self._write_logged_out(current, settings.coins)
            self._maybe_finish_round(current)
            return
        for market in settings.coins:
            if self._stop.is_set() or not self._round_may_trade():
                break
            result = by_market.get(market)
            self._start_coin(current, market, result is not None and result.stats_ok)
        self._maybe_finish_round(current)

    def _write_logged_out(self, current: _Round, coins: tuple[str, ...]) -> None:
        for market in coins:
            report_id = report_store.insert_report(
                self._conn,
                round_id=current.round_id,
                mode=self._mode,
                market=market,
                created_at=self._clock.now(),
            )
            record = report_store.ReportRecord(
                state=ReportState.FAILED,
                failure=FailureKind.LOGGED_OUT.value,
                finished_at=self._clock.now(),
            )
            report_store.finish_report(self._conn, report_id, record)
            self._mark_method(report_id)
            self._no_order(_target(report_id, market, current, None), NoOrderReason.NO_JUDGMENT)
            self._post_report_list(market)
        spec = alert_rules.logged_out(tuple(sorted(self._sold_canceled)))
        self._emit_alert(spec, self._rules.want_a18(), module=_MODULE)

    def _start_coin(self, current: _Round, market: str, stats_ok: bool) -> None:
        """코인 하나의 보고서를 쓰고 세션을 띄운다(3.5.2의 4)."""
        report_id = report_store.insert_report(
            self._conn,
            round_id=current.round_id,
            mode=self._mode,
            market=market,
            created_at=self._clock.now(),
        )
        self._mark_method(report_id)
        try:
            self._start_coin_session(current, market, report_id, stats_ok)
        except StorageError as exc:
            self._write_error_report(report_id, "DB 오류")
            self._ai_off(market, exc)
        except Exception as exc:
            self._write_error_report(report_id, type(exc).__name__)
            self._ai_off(market, exc, hide_text=True)
        self._post_report_list(market)

    def _start_coin_session(
        self, current: _Round, market: str, report_id: int, stats_ok: bool
    ) -> None:
        if not stats_ok:
            self._skip(report_id, SkipReason.NO_STATS)
            self._no_order(_target(report_id, market, current, None), NoOrderReason.NO_JUDGMENT)
            return
        price = self._read_price(market, current)
        if price is None:
            self._skip(report_id, SkipReason.NO_PRICE)
            self._no_order(_target(report_id, market, current, None), NoOrderReason.NO_JUDGMENT)
            return
        read_at = self._clock.now()
        last = self._last_judgment(market, current)
        stats = self._last_stats[market][0]
        files = self._input_files(market, current, price, read_at, last)
        pending = _Pending(
            report_id=report_id,
            round=current,
            market=market,
            base_price=price,
            price_read_at=read_at,
            last_report_id=None if last is None else last[0].report_id,
            last_result=None if last is None else last[1],
            last_evidence=None if last is None else _evidence_keys(last[0]),
        )
        files[ai_inputs.FILE_CANDLE_STATS] = ai_inputs.candle_stats(stats)
        request = SessionRequest(
            key=f"r{current.round_id}-{market}",
            market=market,
            files=files,
            choices=tuple(self._settings.ai_choices),
            timeout_seconds=float(SESSION_TIMEOUT_SECONDS),
        )
        self._pending[report_id] = pending
        self._set_stage(current.round_id, RoundStage.AI)
        try:
            start_session(
                self._runner,
                request,
                lambda outcome: self._put(SessionDone(report_id, outcome)),
            )
        except Exception:
            self._pending.pop(report_id, None)
            raise

    def _skip(self, report_id: int, reason: SkipReason) -> None:
        record = report_store.ReportRecord(
            state=ReportState.SKIPPED, failure=reason.value, finished_at=self._clock.now()
        )
        report_store.finish_report(self._conn, report_id, record)

    def _write_error_report(self, report_id: int, name: str) -> None:
        """AI를 끄게 한 오류의 보고서를 `ERROR`로 쓴다. 쓸 수 없으면 로그에만 남긴다."""
        try:
            record = report_store.ReportRecord(
                state=ReportState.ERROR, failure=name, finished_at=self._clock.now()
            )
            report_store.finish_report(self._conn, report_id, record)
            if self._trade() is not None:
                trade_store.set_report_order(
                    self._conn,
                    report_id,
                    order_id=None,
                    no_order_reason=NoOrderReason.NO_JUDGMENT.value,
                    elapsed_order_s=0,
                )
        except Exception as exc:
            log.warning("오류 보고서를 쓰지 못했습니다: %s", type(exc).__name__)

    def _read_price(self, market: str, current: _Round) -> Decimal | None:
        """현재가를 읽는다. 읽지 못하면(`QuoteError`) A-2를 만들고 `None`이다. 일시 차단 중이면
        요청하지 않는다. `QuoteError`가 아닌 예외는 그대로 낸다."""
        name: str
        if self._is_blocked():
            name = ERROR_BLOCKED
        else:
            try:
                price = self._client.read_price(market)
            except QuoteError as exc:
                name = exc.error_name
                if name == ERROR_BLOCKED:
                    self._set_block(exc.retry_after)
            else:
                self._rules.a2_succeeded(market)
                return price
        wanted = self._rules.want_a2(market, name)
        next_text = kst_when_text(current.next_close, self._clock.now())
        self._emit_alert(alert_rules.price_failed(market, name, next_text), wanted, module=_MODULE)
        return None

    def _last_judgment(
        self, market: str, current: _Round
    ) -> "tuple[report_store.LastJudgment, LastResult] | None":
        """지난 판단과 그 결과(3.9). 실제 값은 저장된 완성된 봉에서만 읽는다."""
        last = report_store.read_last_judgment(
            self._conn, market=market, mode=self._mode, before_close=current.close
        )
        if last is None:
            return None
        length = CANDLE_UNIT_LENGTHS[last.unit]
        now = self._clock.now()
        actual: Decimal | None = None
        for candle in read_candles(self._conn, market, last.unit):
            if candle.start == last.close_utc and candle.start + length <= now:
                actual = candle.close
        past = PastJudgment(
            last.round_number,
            last.close_utc,
            last.unit,
            last.base_price,
            last.direction,
            last.predicted_price,
        )
        return last, compute_last_result(past, actual)

    def _input_files(
        self,
        market: str,
        current: _Round,
        price: Decimal,
        read_at: datetime,
        last: "tuple[report_store.LastJudgment, LastResult] | None",
    ) -> dict[str, str]:
        unit = current.unit
        if last is None:
            last_input = None
            last_rows: list[tuple[AiSlot, Row]] = []
        else:
            judgment, result = last
            past = PastJudgment(
                judgment.round_number,
                judgment.close_utc,
                judgment.unit,
                judgment.base_price,
                judgment.direction,
                judgment.predicted_price,
            )
            last_input = ai_inputs.LastJudgmentInput(
                round_number=judgment.round_number,
                round_close=judgment.close_utc,
                base_price=judgment.base_price,
                direction=judgment.direction,
                predicted_price=judgment.predicted_price,
                predicted_reason=judgment.predicted_reason,
                result_text=judgment.result_text or "",
                target_close=target_close_utc(past),
                actual_price=result.actual_price,
                diff=result.diff,
                hit=result.hit,
            )
            last_rows = [
                (
                    AiSlot(row.answer_part),
                    Row(
                        market,
                        row.kind,
                        row.seq,
                        row.text,
                        part=row.part,
                        ref_seq=row.ref_seq,
                        url=row.url,
                        verdict=row.verdict,
                        belief=row.belief,
                        compared=row.compared,
                        hits=row.hits,
                    ),
                )
                for row in judgment.rows
            ]
        return {
            ai_inputs.FILE_SEARCH_INPUT: ai_inputs.search_input(
                market=market, unit=unit, round_close=current.close, next_round=current.next_close
            ),
            ai_inputs.FILE_FINAL_NOW: ai_inputs.final_now(
                market=market,
                current_price=price,
                price_read_at=read_at,
                unit=unit,
                round_close=current.close,
                next_round=current.next_close,
            ),
            ai_inputs.FILE_FINAL_LAST_JUDGMENT: ai_inputs.final_last_judgment(last_input),
            ai_inputs.FILE_FINAL_LAST_REPORT: ai_inputs.final_last_report(last_rows),
            ai_inputs.FILE_FINAL_TRADES: (
                ai_inputs.final_trades() if self._trade() is None else self._final_trades(market)
            ),
        }

    def _maybe_finish_round(self, current: _Round) -> None:
        """그 회차의 보고서가 모두 `RUNNING`이 아니게 되면 회차를 `done`으로 쓴다. 멈춤이나 오류
        멈춤으로 버린 회차(`_active_round`가 비었다)는 다시 쓰지 않는다(M3 명세 3.5.3)."""
        if self._active_round is not current:
            return
        try:
            if report_store.has_running_report(self._conn, current.round_id):
                return
            if self._trade() is not None:
                self._set_stage(current.round_id, RoundStage.RECORD)
            report_store.finish_round(
                self._conn,
                current.round_id,
                state=report_store.ROUND_DONE,
                ended_at=self._clock.now(),
            )
        except StorageError as exc:
            self._ai_off(None, exc)
            return
        self._round_finished()
        self._post_trade_views()

    # --- 세션 결과(3.5.2의 5) -------------------------------------------------------------------

    def _on_session_done(self, task: SessionDone) -> None:
        pending = self._pending.pop(task.report_id, None)
        if pending is None:
            return
        self._alert_round = pending.round.number
        try:
            if isinstance(task.outcome, Exception):
                self._write_error_report(pending.report_id, type(task.outcome).__name__)
                self._ai_off(pending.market, task.outcome)
            else:
                self._finish_session(pending, task.outcome)
        except StorageError as exc:
            self._write_error_report(pending.report_id, "DB 오류")
            self._ai_off(pending.market, exc)
        except Exception as exc:
            self._write_error_report(pending.report_id, type(exc).__name__)
            self._ai_off(pending.market, exc, hide_text=True)
        try:
            self._post_report_list(pending.market)
            # 회차를 `done`으로 쓰다 난 알림도 그 회차 안이다
            self._maybe_finish_round(pending.round)
        finally:
            self._alert_round = None

    def _finish_session(self, pending: _Pending, result: SessionResult) -> None:
        judged = self._judge(pending, result)
        now = self._clock.now()
        last = pending.last_result
        failure = judged.failure
        record = report_store.ReportRecord(
            state=ReportState.OK if failure is None else ReportState.FAILED,
            failure=None if failure is None else failure.value,
            base_price=pending.base_price,
            price_read_at=pending.price_read_at,
            search_judgment=judged.search_judgment,
            search_status=judged.search_status,
            candle_judgment=judged.candle_judgment,
            direction=judged.direction,
            predicted_price=judged.predicted_price,
            predicted_reason=judged.predicted_reason,
            buy_price=judged.buy_price,
            sell_price=judged.sell_price,
            result_text=judged.result_text,
            last_report_id=pending.last_report_id,
            last_predicted_price=None if last is None else last.predicted_price,
            last_actual_price=None if last is None else last.actual_price,
            last_diff=None if last is None else last.diff,
            last_hit=None if last is None or last.hit is None else last.hit.value,
            tokens=result.tokens,
            model_tokens=result.model_tokens,
            tools=result.tools,
            elapsed_report_s=int((now - pending.round.close).total_seconds()),
            finished_at=now,
        )
        rows = [
            report_store.ReportRow(
                answer_part=row.answer_part.value,
                kind=str(row.kind),
                seq=row.seq,
                text=row.text,
                part=row.part,
                ref_seq=row.ref_seq,
                url=row.url,
                verdict=row.verdict,
                belief=row.belief,
                compared=row.compared,
                hits=row.hits,
            )
            for row in judged.rows
        ]
        report_store.finish_report(self._conn, pending.report_id, record, rows)
        self._rules.a29_succeeded(STAGE_REPORT_SAVE, pending.market)
        self._alert_for(pending, failure, judged.reason, judged.direction)
        target = _target(pending.report_id, pending.market, pending.round, record.elapsed_report_s)
        try:  # 주문의 실패는 보고서를 고쳐 쓰지 않는다(그 보고서는 `OK` 그대로)
            if failure is not None:
                self._no_order(target, NoOrderReason.NO_JUDGMENT)
            else:
                self._order_for_report(target, pending.round.round_id)
        except StorageError as exc:
            self._emit_unexpected(STAGE_ORDER, pending.market, exc, module=_MODULE)
        except Exception as exc:
            log.error("주문이 실패했습니다: %s: %s", type(exc).__name__, exc)
            self._emit_unexpected(STAGE_ORDER, pending.market, exc, module=_MODULE)

    def _judge(self, pending: _Pending, result: SessionResult) -> ai_answers.Judged:
        """실패한 세션은 `check()`를 한 번 더 불러 로그인 만료를 가른다(`TIMEOUT`, `LIMIT`은
        부르지 않는다). 그 밖에는 `judge`가 가린다."""
        if result.outcome is SessionOutcome.FAILED:
            check = self._runner.check()
            if check.state is ClaudeState.LOGGED_OUT:
                return ai_answers.Judged(FailureKind.LOGGED_OUT, LOGGED_OUT_REASON)
        context = ai_answers.JudgeContext(
            market=pending.market, price=pending.base_price, last_evidence=pending.last_evidence
        )
        return ai_answers.judge(result, context)

    def _alert_for(
        self,
        pending: _Pending,
        failure: FailureKind | None,
        reason: str | None,
        direction: str | None,
    ) -> None:
        market = pending.market
        if failure is None:
            self._rules.report_ok(market)
            self._insert(
                alert_rules.report_done(market, pending.round.number, Direction(str(direction))),
                report_id=pending.report_id,
                module=_MODULE,
            )
            return
        if failure is FailureKind.LOGGED_OUT:
            spec = alert_rules.logged_out(tuple(sorted(self._sold_canceled)))
            self._emit_alert(spec, self._rules.want_a18(), module=_MODULE)
            return
        wanted = self._rules.want_a17(market, failure.value)
        next_text = kst_when_text(pending.round.next_close, self._clock.now())
        spec = alert_rules.report_failed(
            market,
            failure.value,
            reason or "",
            next_text,
            stop_only=market in self._sold_canceled,
        )
        self._emit_alert(spec, wanted, module=_MODULE)


def _target(
    report_id: int, market: str, current: _Round, elapsed_report_s: int | None
) -> _OrderTarget:
    return _OrderTarget(report_id, market, current.close, current.next_close, elapsed_report_s)


def _evidence_keys(last: "report_store.LastJudgment") -> tuple[tuple[AiSlot, int], ...]:
    """지난 판단의 `근거` 줄마다의 (`answer_part`, `seq`)(3.8.5)."""
    return tuple((AiSlot(row.answer_part), row.seq) for row in last.rows if row.kind == "근거")
