"""3단계 잇기 2: 기록 화면, CSV 내보내기, 모의 성적, DB 백업(M3 명세 3.13, 3.15, 3.16.1).

자료 스레드(`DataWorker`)가 상속한다. 이 모듈의 메서드는 모두 자료 스레드에서만 부른다. 셈은
`ai/last_result.py`(`compute_last_result`)와 `trading/paper/performance.py`의 부품이 하고, DB는
`storage/`의 함수로만 읽는다.
"""

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any, Final

from coin_report_trader.ai.csv_files import decimal_text, ratio_text, utc_text, write_csv
from coin_report_trader.ai.last_result import PastJudgment, compute_last_result
from coin_report_trader.core import (
    CANDLE_UNIT_LENGTHS,
    ORDER_KIND_TITLES,
    ORDER_REASON_TITLES,
    ORDER_SIDE_TITLES,
    ORDER_STATE_TITLES,
    SAVE_TARGET_EXPORT,
    CandleUnit,
    Direction,
    ErrorPauseCause,
    HistoryRow,
    HistoryTab,
    HistoryView,
    LastResult,
    LoadState,
    Mode,
    OrderKind,
    OrderReason,
    OrderSide,
    OrderState,
    PauseKind,
    PerformanceView,
    ReportState,
    RoundStage,
    SaveResult,
    StorageError,
    TradeSettings,
    Verdict,
    coin_label,
)
from coin_report_trader.core.clock import KST
from coin_report_trader.core.texts import (
    STAGE_BACKUP,
    kst_datetime_text,
    qty_text,
    unit_price_text,
)
from coin_report_trader.storage import reports as report_store
from coin_report_trader.storage import trading as trade_store
from coin_report_trader.storage.backups import (
    BACKUPS_FOLDER,
    DAY_START_HOUR,
    backup_day,
    make_daily_backup,
    prune_daily_backups,
)
from coin_report_trader.storage.candles import read_candles
from coin_report_trader.trading.paper.performance import compute_performance

log = logging.getLogger(__name__)

_MODULE: Final = __name__.removeprefix("coin_report_trader.")
BEFORE_NEXT: Final = "다음 회차 전"
"""다음 회차 때가 아직 오지 않은 판단의 실제 값, 차이, 맞음·틀림 칸(3.15.1)."""
STILL_PAUSED: Final = "멈춰 있음"
NO_VALUE: Final = "-"
EXPORT_OK: Final = "내보냈습니다."
EXPORT_FAILED: Final = "내보내지 못했습니다."
BOM: Final = chr(0xFEFF)
ROUND_RESULTS: Final = {"done": "끝남", "interrupted": "끊김", "discarded": "멈춤으로 버림"}
"""기록 화면의 회차 결과 글(3.5.3)."""
PAUSE_CAUSE_TEXTS: Final = {
    ErrorPauseCause.UNEXPECTED: "예상하지 못한 오류",
    ErrorPauseCause.SAVE: "저장 실패",
}
"""오류 멈춤의 {오류 종류}. `EXCHANGE`는 저장된 `detail`("일시 차단" 등)이다(3.10.2)."""
_REPORT_FAILURE_TEXTS: Final = {
    ReportState.INTERRUPTED: "끊김",
    ReportState.DISCARDED: "멈춤으로 버림",
    ReportState.RUNNING: "만드는 중",
}
_TITLES: Final = {
    HistoryTab.ROUNDS: ("번호", "시작 시각", "끝난 단계", "결과"),
    HistoryTab.JUDGMENTS: (
        "회차", "코인", "최종 판단", "예측 가격", "다음 회차 때의 실제 값", "차이(%)",
        "맞음·틀림", "매수 제안가", "매도 제안가", "받지 못한 까닭",
    ),
    HistoryTab.ORDERS: (
        "시각", "코인", "매수·매도", "지정가·시장가", "가격", "수량", "까닭", "상태",
    ),
    HistoryTab.FILLS: (
        "시각", "코인", "매수·매도", "체결 가격", "체결 수량", "수수료", "실현 손익",
    ),
    HistoryTab.PAUSES: ("시작 시각", "종류", "푼 시각"),
    HistoryTab.EXTERNAL: ("시각", "코인", "수량 변화"),
}  # fmt: skip
"""화면의 머리글(3.15.1. 외부 변동 탭은 M5 명세 3.11)."""
HISTORY_COLUMNS: Final = {
    HistoryTab.ROUNDS: ("number", "started_utc", "stage", "state", "close_utc", "ended_utc"),
    HistoryTab.JUDGMENTS: (
        "round_number", "market", "direction", "predicted_price", "actual_price", "diff", "hit",
        "buy_price", "sell_price", "state", "failure", "predicted_reason",
    ),
    HistoryTab.ORDERS: (
        "created_utc", "market", "side", "kind", "price", "qty", "reason", "state", "closed_utc",
        "uid",
    ),
    HistoryTab.FILLS: ("filled_utc", "market", "side", "price", "qty", "fee", "realized_pnl"),
    HistoryTab.PAUSES: (
        "started_utc", "kind", "cause", "detail", "sell_all", "released_utc",
    ),
    HistoryTab.EXTERNAL: ("utc", "market", "qty_delta"),
}  # fmt: skip
"""CSV의 머리줄(3.15.2)."""
_NUMBER: Final = re.compile(r"-?\d+(?:\.\d+)?")
_FAR: Final = timedelta(days=36500)
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=KST)
_ENUMS: Final[Mapping[str, frozenset[str]]] = {
    "direction": frozenset(d.value for d in Direction),
    "hit": frozenset(v.value for v in Verdict),
    "stage": frozenset(s.value for s in RoundStage),
    "side": frozenset(s.value for s in OrderSide),
    "kind": frozenset([k.value for k in OrderKind] + [k.value for k in PauseKind]),
    "reason": frozenset(r.value for r in OrderReason),
    "cause": frozenset(c.value for c in ErrorPauseCause),
    "state": frozenset(
        [s.value for s in OrderState]
        + [s.value for s in ReportState]
        + ["running", "done", "interrupted", "discarded"]
    ),
}
_NUMERIC_COLUMNS: Final = frozenset(
    {
        "number", "round_number", "predicted_price", "actual_price", "diff", "buy_price",
        "sell_price", "price", "qty", "fee", "realized_pnl", "sell_all", "qty_delta",
    }
)  # fmt: skip
_TIME_COLUMNS: Final = frozenset(
    {"started_utc", "close_utc", "ended_utc", "created_utc", "closed_utc", "filled_utc",
     "released_utc", "utc"}
)  # fmt: skip


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """기록의 하루(한국 09:00에 바뀐다. 3.15.1)의 처음과 끝."""
    start = datetime.combine(day, time(DAY_START_HOUR), tzinfo=KST)
    return start, start + timedelta(days=1)


def _signed_won(amount: Decimal) -> str:
    """손익의 글: 원 아래를 버리고 부호와 ▲▼를 붙인다(3.17의 값의 꼴)."""
    whole = int(amount.to_integral_value(rounding=ROUND_DOWN))
    if whole > 0:
        return f"▲ +{whole:,}원"
    if whole < 0:
        return f"▼ -{abs(whole):,}원"
    return "0원"


def _signed_percent(ratio: Decimal) -> str:
    """차이의 글: 백분율 소수 둘째 자리, 부호와 ▲▼(3.17의 값의 꼴)."""
    percent = (ratio * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    if percent > 0:
        return f"▲ +{percent}%"
    if percent < 0:
        return f"▼ -{abs(percent)}%"
    return "0.00%"


KRW_CODE: Final = "KRW"
KRW_TITLE: Final = "원화"
"""외부 변동 탭의 원화 줄의 코인 칸(M5 명세 3.10의 A-22와 같은 글)."""


def _signed_qty(delta: Decimal) -> str:
    """수량 변화의 글: 부호를 붙인다(늘면 +, 줄면 -)."""
    shown = format(delta.normalize(), "f")
    return shown if delta < 0 else f"+{shown}"


def _price(value: str | Decimal | None) -> str:
    if value is None or value == "":
        return NO_VALUE
    return unit_price_text(Decimal(str(value)))


@dataclass(frozen=True)
class _Judgment:
    row: trade_store.JudgmentRow
    result: LastResult | None
    """`OK`인 판단의 결과. 다음 회차 전이거나 봉이 없으면 실제 값 칸이 `None`이다."""
    before_next: bool


def _csv_cell(column: str, value: str) -> str:
    """수식 막기(3.15.2): 값이 `=`, `+`, `-`, `@`, 탭, CR로 시작하면 앞에 `'`를 붙인다. 정해진 값
    (열거형의 값)과 같은 칸, `Decimal`로 읽히는 수, 시각 글은 그대로 쓴다."""
    if not value or value[0] not in "=+-@\t\r":
        return value
    if column in _TIME_COLUMNS:
        return value
    allowed = _ENUMS.get(column)
    if allowed is not None and value in allowed:
        return value
    if column in _NUMERIC_COLUMNS and _NUMBER.fullmatch(value):
        return value
    return "'" + value


class RecordsMixin:
    """기록, CSV, 모의 성적, DB 백업. `DataWorker`의 일부다."""

    _conn: Any
    _clock: Any
    _logs_dir: Path
    _trade: Callable[[], TradeSettings | None]
    _trade_coins: Callable[[], tuple[str, ...]]
    _prices: dict[str, tuple[Decimal, datetime]]
    _post: Callable[..., None]
    _put: Callable[..., None]
    _rules: Any
    _emit_save_failed: Callable[..., None]
    _db_account: Callable[[], Any]
    _mode: Mode

    def _init_records(self) -> None:
        self._next_backup_mono: float | None = None
        self._history_mode: Mode | None = None
        """기록 화면이 마지막으로 고른 모드(`load_history`의 `mode`. 내보내기도 그 기록이다)."""

    def _records_mode(self) -> Mode:
        """기록 화면과 내보내기의 모드: 고른 것이 없으면 지금 모드(M5 명세 3.4, 3.11)."""
        chosen = self._history_mode
        return self._mode if chosen is None else chosen

    # --- 기록 화면(3.15.1) -------------------------------------------------------------------

    def _do_load_history(self, tab: HistoryTab, market: str | None, day: date) -> None:
        """`HistoryView`를 보낸다. 읽지 못하면 `FAILED`다(호출한 쪽이 A-30을 만든다)."""
        if tab not in _TITLES:
            self._post(HistoryView(tab, market, day, (), (), LoadState.NO_DATA))
            return
        start, end = day_bounds(day)
        rows = self._history_rows(tab, market, start, end)
        state = LoadState.READY if rows else LoadState.NO_DATA
        self._post(HistoryView(tab, market, day, _TITLES[tab], tuple(rows), state))

    def _post_history_failed(self, tab: HistoryTab, market: str | None, day: date) -> None:
        self._post(HistoryView(tab, market, day, (), (), LoadState.FAILED))

    def _history_rows(
        self, tab: HistoryTab, market: str | None, start: datetime, end: datetime
    ) -> list[HistoryRow]:
        if tab is HistoryTab.ROUNDS:
            return self._round_rows(start, end)
        if tab is HistoryTab.JUDGMENTS:
            return self._judgment_rows(market, start, end)
        if tab is HistoryTab.ORDERS:
            return self._order_rows(market, start, end)
        if tab is HistoryTab.FILLS:
            return self._fill_rows(market, start, end)
        if tab is HistoryTab.EXTERNAL:
            return self._external_rows(market, start, end)
        return self._pause_rows_view(start, end)

    def _external_rows(
        self, market: str | None, start: datetime, end: datetime
    ) -> list[HistoryRow]:
        """외부 변동 탭(M5 명세 3.11): 시각, 코인(원화는 "원화"), 수량 변화."""
        out: list[HistoryRow] = []
        for change in trade_store.list_external_changes(
            self._conn, self._records_mode(), start=start, end=end, market=market
        ):
            coin = KRW_TITLE if change.market == KRW_CODE else coin_label(change.market)
            cells = (kst_datetime_text(change.utc), coin, _signed_qty(change.qty_delta))
            out.append(HistoryRow(f"external-{change.change_id}", cells, (), None, change.market))
        return out

    def _round_rows(self, start: datetime, end: datetime) -> list[HistoryRow]:
        out: list[HistoryRow] = []
        for row in trade_store.list_round_rows(
            self._conn, self._records_mode(), start=start, end=end
        ):
            stage = NO_VALUE if row.stage is None else row.stage.value
            result = ROUND_RESULTS.get(row.state, "도는 중")
            when = kst_datetime_text(row.started_utc)
            cells = (str(row.number), when, stage, result)
            detail = (("번호", str(row.number)), ("시작", when), ("결과", result))
            out.append(HistoryRow(f"round-{row.round_id}", cells, detail, None))
        return out

    def _judgments(
        self,
        market: str | None,
        start: datetime | None,
        end: datetime | None,
        mode: Mode | None = None,
    ) -> list[_Judgment]:
        now = self._clock.now()
        rows = trade_store.list_judgment_rows(
            self._conn,
            self._records_mode() if mode is None else mode,
            start=start,
            end=end,
            market=market,
        )
        closes: dict[tuple[str, str], dict[datetime, Decimal]] = {}
        out: list[_Judgment] = []
        for row in rows:
            usable = (
                row.state is ReportState.OK
                and row.direction is not None
                and row.base_price is not None
                and row.predicted_price is not None
            )
            if not usable:
                out.append(_Judgment(row, None, False))
                continue
            unit = CandleUnit(row.unit)
            past = PastJudgment(
                row.round_number,
                row.close_utc,
                unit,
                Decimal(str(row.base_price)),
                Direction(str(row.direction)),
                Decimal(str(row.predicted_price)),
            )
            before = row.close_utc + CANDLE_UNIT_LENGTHS[unit] > now
            actual: Decimal | None = None
            if not before:
                key = (row.market, unit.value)
                if key not in closes:
                    closes[key] = {
                        c.start: c.close for c in read_candles(self._conn, row.market, unit)
                    }
                actual = closes[key].get(row.close_utc)
            out.append(_Judgment(row, compute_last_result(past, actual), before))
        return out

    def _judgment_rows(
        self, market: str | None, start: datetime, end: datetime
    ) -> list[HistoryRow]:
        out: list[HistoryRow] = []
        for item in self._judgments(market, start, end):
            row = item.row
            cells = self._judgment_cells(item)
            detail: tuple[tuple[str, str], ...] = ()
            if item.result is not None:
                reason = self._predicted_reason(row.report_id)
                detail = (("예측 까닭", reason or NO_VALUE),)
            out.append(
                HistoryRow(f"judgment-{row.report_id}", cells, detail, row.report_id, row.market)
            )
        return out

    def _judgment_cells(self, item: _Judgment) -> tuple[str, ...]:
        row = item.row
        base = (str(row.round_number), coin_label(row.market))
        result = item.result
        if result is None:
            reason = _REPORT_FAILURE_TEXTS.get(row.state, row.failure or "")
            dashes = (NO_VALUE,) * 7
            return (*base, *dashes, reason)
        if item.before_next:
            tail = (BEFORE_NEXT,) * 3
        elif result.actual_price is None:
            tail = (NO_VALUE,) * 3
        else:
            diff = NO_VALUE if result.diff is None else _signed_percent(result.diff)
            hit = NO_VALUE if result.hit is None else result.hit.value
            tail = (_price(result.actual_price), diff, hit)
        return (
            *base,
            str(row.direction),
            _price(row.predicted_price),
            *tail,
            _price(row.buy_price),
            _price(row.sell_price),
            "",
        )

    def _predicted_reason(self, report_id: int) -> str | None:
        view = report_store.read_report(self._conn, report_id)
        return None if view is None else view.predicted_reason

    def _order_rows(self, market: str | None, start: datetime, end: datetime) -> list[HistoryRow]:
        out: list[HistoryRow] = []
        for order in trade_store.list_orders(
            self._conn, self._records_mode(), start=start, end=end, market=market
        ):
            cells = (
                kst_datetime_text(order.created_utc),
                coin_label(order.market),
                ORDER_SIDE_TITLES[order.side],
                ORDER_KIND_TITLES[order.kind],
                _price(order.price),
                qty_text(order.qty),
                ORDER_REASON_TITLES[order.reason],
                ORDER_STATE_TITLES[order.state],
            )
            detail = (("식별값", order.uid), ("오류 이름", order.error_name or NO_VALUE))
            out.append(HistoryRow(f"order-{order.order_id}", cells, detail, None, order.market))
        return out

    def _fill_rows(self, market: str | None, start: datetime, end: datetime) -> list[HistoryRow]:
        out: list[HistoryRow] = []
        for fill in trade_store.list_fills(
            self._conn, self._records_mode(), start=start, end=end, market=market
        ):
            pnl = NO_VALUE if fill.realized_pnl is None else _signed_won(fill.realized_pnl)
            cells = (
                kst_datetime_text(fill.filled_utc),
                coin_label(fill.market),
                ORDER_SIDE_TITLES[fill.side],
                _price(fill.price),
                qty_text(fill.qty),
                _price(fill.fee),
                pnl,
            )
            out.append(HistoryRow(f"fill-{fill.fill_id}", cells, (), None, fill.market))
        return out

    def _pause_rows_view(self, start: datetime, end: datetime) -> list[HistoryRow]:
        out: list[HistoryRow] = []
        for row in trade_store.list_pauses(self._conn, self._records_mode(), start=start, end=end):
            if row.kind is PauseKind.USER:
                kind = "사용자"
            elif row.cause is ErrorPauseCause.EXCHANGE:
                kind = f"오류: {row.detail or '일시 차단'}"
            else:
                label = PAUSE_CAUSE_TEXTS.get(row.cause, NO_VALUE) if row.cause else NO_VALUE
                kind = f"오류: {label}"
            released = (
                STILL_PAUSED if row.released_utc is None else kst_datetime_text(row.released_utc)
            )
            cells = (kst_datetime_text(row.started_utc), kind, released)
            detail = (("전량 매도", "예" if row.sell_all else "아니오"),)
            out.append(HistoryRow(f"pause-{row.pause_id}", cells, detail, None))
        return out

    # --- CSV 내보내기(3.15.2) ----------------------------------------------------------------

    def _do_export_history(self, tab: HistoryTab, market: str | None, path: str) -> None:
        """그 탭, 그 코인의 모든 날짜의 줄을 오래된 것이 위로 쓴다. 쓰지 못하면(`OSError`) 실패의
        글이고 A-29도 오류 멈춤도 아니다."""
        if tab not in HISTORY_COLUMNS:
            self._post(SaveResult(SAVE_TARGET_EXPORT, False, EXPORT_FAILED))
            return
        rows = self._export_rows(tab, market)
        columns = HISTORY_COLUMNS[tab]
        guarded = [
            [_csv_cell(column, value) for column, value in zip(columns, row, strict=True)]
            for row in rows
        ]
        text = write_csv(columns, guarded)
        try:
            with Path(path).open("w", encoding="utf-8", newline="") as handle:
                handle.write(BOM + text)
        except (OSError, ValueError) as exc:
            log.warning("기록을 내보내지 못했습니다: %s", type(exc).__name__)
            self._post(SaveResult(SAVE_TARGET_EXPORT, False, EXPORT_FAILED))
            return
        self._post(SaveResult(SAVE_TARGET_EXPORT, True, EXPORT_OK))

    def _export_rows(self, tab: HistoryTab, market: str | None) -> list[list[str]]:
        start = _EPOCH
        end = self._clock.now() + _FAR
        if tab is HistoryTab.ROUNDS:
            rounds = reversed(
                trade_store.list_round_rows(self._conn, self._records_mode(), start=start, end=end)
            )
            return [
                [
                    str(row.number),
                    utc_text(row.started_utc),
                    "" if row.stage is None else row.stage.value,
                    row.state,
                    "" if row.close_utc is None else utc_text(row.close_utc),
                    "" if row.ended_utc is None else utc_text(row.ended_utc),
                ]
                for row in rounds
            ]
        if tab is HistoryTab.JUDGMENTS:
            return [
                self._judgment_csv(item) for item in reversed(self._judgments(market, None, None))
            ]
        if tab is HistoryTab.ORDERS:
            orders = reversed(
                trade_store.list_orders(
                    self._conn, self._records_mode(), start=start, end=end, market=market
                )
            )
            return [
                [
                    utc_text(order.created_utc),
                    order.market,
                    order.side.value,
                    order.kind.value,
                    "" if order.price is None else decimal_text(order.price),
                    decimal_text(order.qty),
                    order.reason.value,
                    order.state.value,
                    "" if order.closed_utc is None else utc_text(order.closed_utc),
                    order.uid,
                ]
                for order in orders
            ]
        if tab is HistoryTab.FILLS:
            fills = reversed(
                trade_store.list_fills(
                    self._conn, self._records_mode(), start=start, end=end, market=market
                )
            )
            return [
                [
                    utc_text(fill.filled_utc),
                    fill.market,
                    fill.side.value,
                    decimal_text(fill.price),
                    decimal_text(fill.qty),
                    decimal_text(fill.fee),
                    "" if fill.realized_pnl is None else decimal_text(fill.realized_pnl),
                ]
                for fill in fills
            ]
        if tab is HistoryTab.EXTERNAL:
            changes = reversed(
                trade_store.list_external_changes(
                    self._conn, self._records_mode(), start=start, end=end, market=market
                )
            )
            return [
                [utc_text(change.utc), change.market, decimal_text(change.qty_delta)]
                for change in changes
            ]
        pauses = reversed(
            trade_store.list_pauses(self._conn, self._records_mode(), start=start, end=end)
        )
        return [
            [
                utc_text(row.started_utc),
                row.kind.value,
                "" if row.cause is None else row.cause.value,
                row.detail or "",
                "1" if row.sell_all else "0",
                "" if row.released_utc is None else utc_text(row.released_utc),
            ]
            for row in pauses
        ]

    def _judgment_csv(self, item: _Judgment) -> list[str]:
        row = item.row
        result = item.result
        if result is None:  # `OK`가 아닌 줄의 판단 칸은 빈 칸이다
            return [
                str(row.round_number), row.market, "", "", "", "", "", "", "", row.state.value,
                row.failure or "", "",
            ]  # fmt: skip
        actual = "" if result.actual_price is None else decimal_text(result.actual_price)
        diff = "" if result.diff is None else ratio_text(result.diff)
        hit = "" if result.hit is None else result.hit.value
        return [
            str(row.round_number),
            row.market,
            str(row.direction),
            str(row.predicted_price),
            actual,
            diff,
            hit,
            "" if row.buy_price is None else str(row.buy_price),
            "" if row.sell_price is None else str(row.sell_price),
            row.state.value,
            row.failure or "",
            self._predicted_reason(row.report_id) or "",
        ]

    # --- 모의 성적(3.16.1) ---------------------------------------------------------------------

    def _post_performance(self) -> None:
        """코인마다 한 줄과 합계 한 줄. 켤 때와 체결·회차가 끝날 때마다 보낸다."""
        trade = self._trade()
        if trade is None or self._conn is None:
            return
        try:
            funds = trade_store.read_paper_funds(self._conn, Mode.PAPER)
            if funds is None:
                return
            coins = self._trade_coins()
            judgments: dict[str, list[LastResult]] = {market: [] for market in coins}
            for item in self._judgments(None, None, None, Mode.PAPER):
                if item.result is not None and item.row.market in judgments:
                    judgments[item.row.market].append(item.result)
            prices: dict[str, Decimal | None] = {
                market: (None if market not in self._prices else self._prices[market][0])
                for market in coins
            }
            for holding in self._db_account().holdings:  # 뺀 코인의 보유도 자산에 든다
                prices.setdefault(holding.market, self._prices.get(holding.market, (None,))[0])
            rows = compute_performance(
                coins,
                judgments=judgments,
                fills=trade_store.list_fills(self._conn, Mode.PAPER),
                account=self._db_account(),
                prices=prices,
                funds=funds,
            )
        except StorageError as exc:
            log.warning("모의 성적을 셈하지 못했습니다: %s", exc)
            return
        self._post(PerformanceView(rows, as_of=self._clock.now()))

    # --- DB 백업(3.13) -------------------------------------------------------------------------

    def _backup_check(self) -> None:
        """하루 한 번(켤 때와 하루가 바뀌는 한국 09:00): 그날의 백업이 없으면 만들고 날 수를 넘은
        것을 지운다. 멈춤과 오류 멈춤과 상관없이 한다. 실패는 A-29("DB 백업")만이다."""
        trade = self._trade()
        if trade is None or self._conn is None:
            self._next_backup_mono = None
            return
        now = self._clock.now()
        day = backup_day(now)
        folder = self._logs_dir.parent / BACKUPS_FOLDER
        failure: StorageError | None = None
        try:
            make_daily_backup(self._conn, folder, day)
        except StorageError as exc:
            failure = exc
        try:
            prune_daily_backups(folder, day, trade.backup_days)
        except StorageError as exc:
            failure = failure or exc
        next_day = datetime.combine(day + timedelta(days=1), time(DAY_START_HOUR), tzinfo=KST)
        self._next_backup_mono = self._clock.monotonic() + max(
            0.0, (next_day - now).total_seconds()
        )
        if failure is None:
            self._rules.a29_succeeded(STAGE_BACKUP, None)
        else:
            self._emit_save_failed(STAGE_BACKUP, None, failure, module=_MODULE)

    def _backup_due(self, now_mono: float) -> bool:
        due = self._next_backup_mono
        return due is not None and now_mono >= due
