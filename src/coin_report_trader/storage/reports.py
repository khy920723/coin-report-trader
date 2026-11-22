"""표 `rounds`, `reports`, `report_rows`의 쓰기와 읽기(M2 명세 3.10). 가격은 `Decimal`의 글이다.

쓰기는 모두 하나의 트랜잭션이다(한 보고서의 판단 칸과 줄 표도 한 트랜잭션). DB 오류는
`StorageError`다. 읽기는 화면 값(`RoundItem`, `ReportView`, `LastResult`)을 만든다. `OK`가 아닌
보고서의 판단 칸(`search_judgment`, `candle_judgment`, `direction`, 가격, 까닭, `result_text`),
지난 판단의 결과, 줄 표는 화면 값에 담지 않는다(`None`, 빈 튜플). 실패한 답에는 "보류" 같은 값이
저장될 수 있어(3.8.5), 열거형으로 바꾸다 실패하면 목록이 깨지기 때문이다. 저장된 값은 그대로 둔다.

이름은 명세가 정하지 않아 이 파일이 정했다. 보고서, 회차, 줄을 지우는 함수는 없다(11절).
"""

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Final

from coin_report_trader.core import (
    AI_PARTS,
    AI_SLOT_TITLES,
    REPORT_LIST_LIMIT,
    AiSlot,
    Belief,
    CandleJudgment,
    CandleUnit,
    ChecklistItem,
    ComparedItem,
    Direction,
    EvidenceItem,
    LastResult,
    Mode,
    ModelTokens,
    ReportState,
    ReportView,
    RoundItem,
    RoundTrigger,
    RowKind,
    SearchJudgment,
    SourceItem,
    StorageError,
    TokenUsage,
    Verdict,
)
from coin_report_trader.storage.database import transaction, translate_errors
from coin_report_trader.storage.times import format_utc, parse_utc

ROUND_RUNNING: Final = "running"
ROUND_DONE: Final = "done"
ROUND_INTERRUPTED: Final = "interrupted"
"""`rounds.state`의 값(3.10). 3단계가 값을 더하므로 표에 CHECK를 걸지 않는다."""

_TITLE_TO_SLOT: Final = {title: slot for slot, title in AI_SLOT_TITLES.items()}
_SLOT_ORDER: Final = {slot.value: index for index, slot in enumerate(AiSlot)}


@dataclass(frozen=True)
class ReportRecord:
    """한 보고서를 끝낼 때 쓰는 값(`finish_report`). 실패한 답의 값도 그대로 쓴다(3.8.5)."""

    state: ReportState
    failure: str | None = None
    base_price: Decimal | None = None
    price_read_at: datetime | None = None
    search_judgment: str | None = None
    search_status: str | None = None
    candle_judgment: str | None = None
    direction: str | None = None
    predicted_price: Decimal | str | None = None
    predicted_reason: str | None = None
    buy_price: Decimal | str | None = None
    sell_price: Decimal | str | None = None
    result_text: str | None = None
    last_report_id: int | None = None
    last_predicted_price: Decimal | None = None
    last_actual_price: Decimal | None = None
    last_diff: Decimal | None = None
    last_hit: str | None = None
    tokens: TokenUsage | None = None
    model_tokens: tuple[ModelTokens, ...] = ()
    tools: Mapping[AiSlot, Sequence[str]] | None = None
    """칸마다 쓴 도구의 이름. 세션을 띄우지 않은 보고서는 `None`(열이 NULL)이다."""
    elapsed_report_s: int | None = None
    elapsed_order_s: int | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True)
class ReportRow:
    """줄 표(`report_rows`)의 한 줄. 열은 3.8.1의 줄 표와 같다."""

    answer_part: str
    """`AiSlot`의 세 부분 값."""
    kind: str
    """`RowKind`의 값."""
    seq: int
    text: str
    part: str | None = None
    ref_seq: int | None = None
    url: str | None = None
    verdict: str | None = None
    belief: str | None = None
    compared: int | None = None
    hits: int | None = None


@dataclass(frozen=True)
class LastJudgment:
    """지난 판단(3.9): 그 코인, 그 모드에서 앞선 `OK` 보고서 가운데 T가 가장 늦은 것."""

    report_id: int
    round_number: int
    close_utc: datetime
    unit: CandleUnit
    base_price: Decimal
    direction: Direction
    predicted_price: Decimal
    predicted_reason: str
    result_text: str | None
    rows: tuple[ReportRow, ...]
    """그 보고서의 줄 표 전부(`final_last_report.csv`의 바탕)."""


# --- 쓰기 -------------------------------------------------------------------------------------


def insert_round(
    conn: sqlite3.Connection,
    *,
    mode: Mode,
    number: int,
    trigger: RoundTrigger,
    unit: CandleUnit,
    close_at: datetime,
    next_at: datetime,
    started_at: datetime,
) -> int:
    """회차 한 줄을 `running`으로 쓰고 새 `id`를 돌려준다.

    (`mode`, `number`)가 겹치면 `StorageError`다.
    """
    row = (
        Mode(mode).value,
        number,
        RoundTrigger(trigger).value,
        CandleUnit(unit).value,
        format_utc(close_at),
        format_utc(next_at),
        format_utc(started_at),
        ROUND_RUNNING,
    )
    with translate_errors("회차 저장"), transaction(conn):
        cursor = conn.execute(
            'INSERT INTO rounds (mode, number, "trigger", unit, close_utc, next_utc, started_utc,'
            " state) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )
        round_id = cursor.lastrowid
    if round_id is None:
        raise StorageError("회차 번호를 받지 못했습니다")
    return round_id


def finish_round(
    conn: sqlite3.Connection, round_id: int, *, state: str, ended_at: datetime
) -> bool:
    """회차의 상태(`done`, `interrupted`)와 끝난 때를 고쳐 쓴다. 그 줄이 있었으면 참이다."""
    with translate_errors("회차 저장"), transaction(conn):
        cursor = conn.execute(
            "UPDATE rounds SET state = ?, ended_utc = ? WHERE id = ?",
            (state, format_utc(ended_at), round_id),
        )
        return cursor.rowcount > 0


def insert_report(
    conn: sqlite3.Connection, *, round_id: int, mode: Mode, market: str, created_at: datetime
) -> int:
    """보고서 한 줄을 `RUNNING`(만드는 중)으로 쓰고 새 `id`를 돌려준다.

    (`round_id`, `market`)이 겹치면 `StorageError`다.
    """
    with translate_errors("보고서 저장"), transaction(conn):
        cursor = conn.execute(
            "INSERT INTO reports (round_id, mode, market, state, created_utc)"
            " VALUES (?, ?, ?, ?, ?)",
            (round_id, Mode(mode).value, market, ReportState.RUNNING.value, format_utc(created_at)),
        )
        report_id = cursor.lastrowid
    if report_id is None:
        raise StorageError("보고서 번호를 받지 못했습니다")
    return report_id


def finish_report(
    conn: sqlite3.Connection,
    report_id: int,
    record: ReportRecord,
    rows: Sequence[ReportRow] = (),
) -> bool:
    """보고서를 끝낸 상태와 값으로 고쳐 쓰고 줄 표를 쓴다. 모두 하나의 트랜잭션이다.

    그 보고서의 앞의 줄은 지우고 `rows`를 쓴다. (`answer_part`, `kind`, `seq`)가 앞 줄과 겹치는
    뒤 줄은 버린다(3.8.5. 키에 걸려 `StorageError`가 나면 A-29로 AI가 꺼지기 때문이다). 그
    보고서가 있었으면 참이다(없으면 줄도 쓰지 않는다).
    """
    values = (
        ReportState(record.state).value,
        record.failure,
        _text(record.base_price),
        None if record.price_read_at is None else format_utc(record.price_read_at),
        record.search_judgment,
        record.search_status,
        record.candle_judgment,
        record.direction,
        _text(record.predicted_price),
        record.predicted_reason,
        _text(record.buy_price),
        _text(record.sell_price),
        record.result_text,
        record.last_report_id,
        _text(record.last_predicted_price),
        _text(record.last_actual_price),
        _text(record.last_diff),
        record.last_hit,
        *_token_values(record.tokens),
        _model_tokens_json(record.model_tokens),
        _tools_json(record.tools),
        record.elapsed_report_s,
        record.elapsed_order_s,
        None if record.finished_at is None else format_utc(record.finished_at),
        report_id,
    )
    seen: set[tuple[str, str, int]] = set()
    kept: list[tuple[object, ...]] = []
    for row in rows:
        key = (str(row.answer_part), str(row.kind), row.seq)
        if key in seen:
            continue
        seen.add(key)
        kept.append(
            (
                report_id,
                *key[:2],
                row.seq,
                row.part,
                row.ref_seq,
                row.text,
                row.url,
                row.verdict,
                row.belief,
                row.compared,
                row.hits,
            )
        )
    with translate_errors("보고서 저장"), transaction(conn):
        cursor = conn.execute(
            "UPDATE reports SET state = ?, failure = ?, base_price = ?, price_read_utc = ?,"
            " search_judgment = ?, search_status = ?, candle_judgment = ?, direction = ?,"
            " predicted_price = ?, predicted_reason = ?, buy_price = ?, sell_price = ?,"
            " result_text = ?, last_report_id = ?, last_predicted_price = ?,"
            " last_actual_price = ?, last_diff = ?, last_hit = ?, input_tokens = ?,"
            " output_tokens = ?, cache_read_tokens = ?, cache_creation_tokens = ?,"
            " model_tokens = ?, tools_used = ?, elapsed_report_s = ?, elapsed_order_s = ?,"
            " finished_utc = ? WHERE id = ?",
            values,
        )
        if cursor.rowcount == 0:
            return False
        conn.execute("DELETE FROM report_rows WHERE report_id = ?", (report_id,))
        conn.executemany(
            "INSERT INTO report_rows (report_id, answer_part, kind, seq, part, ref_seq, text, url,"
            " verdict, belief, compared, hits) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            kept,
        )
    return True


def interrupt_unfinished(conn: sqlite3.Connection, *, now: datetime) -> tuple[int, int]:
    """끊긴 회차를 정리한다: `running`인 회차를 `interrupted`로, `RUNNING`인 보고서를
    `INTERRUPTED`로 바꾼다(끝난 때는 `now`). (바꾼 회차의 수, 바꾼 보고서의 수)를 돌려준다."""
    moment = format_utc(now)
    with translate_errors("끊긴 회차 정리"), transaction(conn):
        reports = conn.execute(
            "UPDATE reports SET state = ?, finished_utc = ? WHERE state = ?",
            (ReportState.INTERRUPTED.value, moment, ReportState.RUNNING.value),
        ).rowcount
        rounds = conn.execute(
            "UPDATE rounds SET state = ?, ended_utc = ? WHERE state = ?",
            (ROUND_INTERRUPTED, moment, ROUND_RUNNING),
        ).rowcount
    return rounds, reports


# --- 읽기 -------------------------------------------------------------------------------------


def max_round_number(conn: sqlite3.Connection, mode: Mode) -> int:
    """그 모드의 가장 큰 회차 번호. 회차가 없으면 0이다."""
    with translate_errors("회차 읽기"):
        (number,) = conn.execute(
            "SELECT max(number) FROM rounds WHERE mode = ?", (Mode(mode).value,)
        ).fetchone()
    return 0 if number is None else int(number)


def has_running_report(conn: sqlite3.Connection, round_id: int) -> bool:
    """그 회차에 `RUNNING`인 보고서가 있는지."""
    with translate_errors("보고서 읽기"):
        row = conn.execute(
            "SELECT 1 FROM reports WHERE round_id = ? AND state = ? LIMIT 1",
            (round_id, ReportState.RUNNING.value),
        ).fetchone()
    return row is not None


def list_round_items(
    conn: sqlite3.Connection, market: str, mode: Mode, limit: int = REPORT_LIST_LIMIT
) -> tuple[RoundItem, ...]:
    """그 코인, 그 모드의 보고서 목록을 새것부터 `limit`개 읽는다(`ReportListView`의 줄)."""
    with translate_errors("보고서 읽기"):
        rows = conn.execute(
            "SELECT reports.id, rounds.number, rounds.close_utc, reports.state, reports.direction,"
            " reports.failure FROM reports JOIN rounds ON rounds.id = reports.round_id"
            " WHERE reports.market = ? AND reports.mode = ?"
            " ORDER BY rounds.number DESC, reports.id DESC LIMIT ?",
            (market, Mode(mode).value, limit),
        ).fetchall()
    return tuple(_round_item(row) for row in rows)


def read_report(conn: sqlite3.Connection, report_id: int) -> ReportView | None:
    """보고서 하나와 그 줄 표를 `ReportView`로 읽는다. 없으면 `None`이다."""
    with translate_errors("보고서 읽기"):
        row = conn.execute(
            f"SELECT {_REPORT_COLUMNS} FROM reports JOIN rounds ON rounds.id = reports.round_id"
            " WHERE reports.id = ?",
            (report_id,),
        ).fetchone()
        if row is None:
            return None
        rows = _read_rows(conn, report_id)
        last_round = None
        if row[_LAST_REPORT_ID] is not None:
            last_round = conn.execute(
                "SELECT rounds.number FROM reports JOIN rounds ON rounds.id = reports.round_id"
                " WHERE reports.id = ?",
                (row[_LAST_REPORT_ID],),
            ).fetchone()
    return _report_view(row, rows, None if last_round is None else int(last_round[0]))


def read_last_judgment(
    conn: sqlite3.Connection, *, market: str, mode: Mode, before_close: datetime
) -> LastJudgment | None:
    """지난 판단을 읽는다(3.9): 그 코인, 그 모드에서 T가 `before_close`보다 앞선 `OK` 보고서
    가운데 T가 가장 늦은 것과 그 줄 표. 없으면 `None`이다."""
    with translate_errors("지난 판단 읽기"):
        row = conn.execute(
            "SELECT reports.id, rounds.number, rounds.close_utc, rounds.unit, reports.base_price,"
            " reports.direction, reports.predicted_price, reports.predicted_reason,"
            " reports.result_text FROM reports JOIN rounds ON rounds.id = reports.round_id"
            " WHERE reports.market = ? AND reports.mode = ? AND reports.state = ?"
            " AND rounds.close_utc < ? ORDER BY rounds.close_utc DESC, reports.id DESC LIMIT 1",
            (market, Mode(mode).value, ReportState.OK.value, format_utc(before_close)),
        ).fetchone()
        if row is None:
            return None
        stored = _read_rows(conn, int(row[0]))
    report_id, number, close, unit, base, direction, price, reason, result_text = row
    try:
        return LastJudgment(
            report_id=int(report_id),
            round_number=int(number),
            close_utc=parse_utc(str(close)),
            unit=CandleUnit(unit),
            base_price=Decimal(str(base)),
            direction=Direction(direction),
            predicted_price=Decimal(str(price)),
            predicted_reason=str(reason),
            result_text=None if result_text is None else str(result_text),
            rows=tuple(_stored_row(item) for item in stored),
        )
    except (ValueError, InvalidOperation, TypeError) as exc:
        raise StorageError("저장된 지난 판단의 값이 맞지 않습니다") from exc


# --- 안쪽 -------------------------------------------------------------------------------------

_REPORT_COLUMNS: Final = (
    "reports.id, reports.market, rounds.number, rounds.close_utc, reports.state, reports.failure,"
    " reports.base_price, reports.search_judgment, reports.candle_judgment, reports.direction,"
    " reports.predicted_price, reports.predicted_reason, reports.buy_price, reports.sell_price,"
    " reports.result_text, reports.last_report_id, reports.last_predicted_price,"
    " reports.last_actual_price, reports.last_diff, reports.last_hit, reports.input_tokens,"
    " reports.output_tokens, reports.cache_read_tokens, reports.cache_creation_tokens,"
    " reports.elapsed_report_s"
)
_LAST_REPORT_ID: Final = 15
_ROW_COLUMNS: Final = (
    "answer_part, kind, seq, part, ref_seq, text, url, verdict, belief, compared, hits"
)


def _text(value: Decimal | str | None) -> str | None:
    return None if value is None else str(value)


def _token_values(tokens: TokenUsage | None) -> tuple[int | None, ...]:
    if tokens is None:
        return (None, None, None, None)
    return (
        tokens.input_tokens,
        tokens.output_tokens,
        tokens.cache_read_tokens,
        tokens.cache_creation_tokens,
    )


def _model_tokens_json(items: tuple[ModelTokens, ...]) -> str | None:
    if not items:
        return None
    return json.dumps(
        [
            {
                "model": item.model,
                "input_tokens": item.usage.input_tokens,
                "output_tokens": item.usage.output_tokens,
                "cache_read_tokens": item.usage.cache_read_tokens,
                "cache_creation_tokens": item.usage.cache_creation_tokens,
            }
            for item in items
        ],
        ensure_ascii=False,
    )


def _tools_json(tools: Mapping[AiSlot, Sequence[str]] | None) -> str | None:
    if tools is None:
        return None
    return json.dumps(
        {AiSlot(slot).value: list(names) for slot, names in tools.items()}, ensure_ascii=False
    )


def _read_rows(conn: sqlite3.Connection, report_id: int) -> list[tuple[object, ...]]:
    return conn.execute(
        f"SELECT {_ROW_COLUMNS} FROM report_rows WHERE report_id = ?"
        " ORDER BY answer_part, kind, seq",
        (report_id,),
    ).fetchall()


def _stored_row(row: tuple[object, ...]) -> ReportRow:
    answer_part, kind, seq, part, ref_seq, text, url, verdict, belief, compared, hits = row
    return ReportRow(
        answer_part=str(answer_part),
        kind=str(kind),
        seq=int(seq),  # type: ignore[call-overload]
        text=str(text),
        part=None if part is None else str(part),
        ref_seq=None if ref_seq is None else int(ref_seq),  # type: ignore[call-overload]
        url=None if url is None else str(url),
        verdict=None if verdict is None else str(verdict),
        belief=None if belief is None else str(belief),
        compared=None if compared is None else int(compared),  # type: ignore[call-overload]
        hits=None if hits is None else int(hits),  # type: ignore[call-overload]
    )


def _round_item(row: tuple[object, ...]) -> RoundItem:
    report_id, number, close, state_text, direction, failure = row
    try:
        state = ReportState(state_text)
        return RoundItem(
            report_id=int(report_id),  # type: ignore[call-overload]
            round_number=int(number),  # type: ignore[call-overload]
            close_utc=parse_utc(str(close)),
            state=state,
            direction=Direction(direction) if state is ReportState.OK and direction else None,
            failure=None if failure is None else str(failure),
        )
    except ValueError as exc:
        raise StorageError("저장된 보고서의 값이 맞지 않습니다") from exc


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _report_view(
    row: tuple[object, ...], stored: list[tuple[object, ...]], last_round: int | None
) -> ReportView:
    (report_id, market, number, close, state_text, failure, base, search, candle, direction,
     predicted, reason, buy, sell, result_text, last_report_id, last_predicted, last_actual,
     last_diff, last_hit, in_tokens, out_tokens, cache_read, cache_creation,
     elapsed) = row  # fmt: skip
    try:
        state = ReportState(state_text)
        tokens = (
            None
            if in_tokens is None
            else TokenUsage(int(in_tokens), int(out_tokens), int(cache_read), int(cache_creation))  # type: ignore[call-overload,arg-type]
        )
        common = {
            "report_id": int(report_id),  # type: ignore[call-overload]
            "market": str(market),
            "round_number": int(number),  # type: ignore[call-overload]
            "close_utc": parse_utc(str(close)),
            "state": state,
            "failure": None if failure is None else str(failure),
            "base_price": _decimal(base),
            "tokens": tokens,
            "elapsed_report_seconds": None if elapsed is None else int(elapsed),  # type: ignore[call-overload]
        }
        if state is not ReportState.OK:
            return ReportView(
                **common,  # type: ignore[arg-type]
                search_judgment=None,
                candle_judgment=None,
                direction=None,
                predicted_price=None,
                predicted_reason=None,
                buy_price=None,
                sell_price=None,
                result_text=None,
                last_result=None,
                compared=(),
                checklist=(),
                evidence=(),
                sources=(),
            )
        last_result = None
        if last_report_id is not None and last_round is not None and last_predicted is not None:
            last_result = LastResult(
                round_number=last_round,
                predicted_price=Decimal(str(last_predicted)),
                actual_price=_decimal(last_actual),
                diff=_decimal(last_diff),
                hit=None if last_hit is None else Verdict(last_hit),
            )
        compared, checklist, evidence, sources = _view_rows(stored)
        return ReportView(
            **common,  # type: ignore[arg-type]
            search_judgment=None if search is None else SearchJudgment(search),
            candle_judgment=None if candle is None else CandleJudgment(candle),
            direction=None if direction is None else Direction(direction),
            predicted_price=_decimal(predicted),
            predicted_reason=None if reason is None else str(reason),
            buy_price=_decimal(buy),
            sell_price=_decimal(sell),
            result_text=None if result_text is None else str(result_text),
            last_result=last_result,
            compared=compared,
            checklist=checklist,
            evidence=evidence,
            sources=sources,
        )
    except (ValueError, InvalidOperation, TypeError, KeyError) as exc:
        raise StorageError("저장된 보고서의 값이 맞지 않습니다") from exc


def _view_rows(
    stored: list[tuple[object, ...]],
) -> tuple[
    tuple[ComparedItem, ...],
    tuple[ChecklistItem, ...],
    tuple[EvidenceItem, ...],
    tuple[SourceItem, ...],
]:
    rows = [_stored_row(item) for item in stored]
    compared: list[ComparedItem] = []
    checklist: list[ChecklistItem] = []
    evidence: list[EvidenceItem] = []
    sources: list[SourceItem] = []
    for row in sorted(rows, key=lambda r: (_SLOT_ORDER.get(r.answer_part, 99), r.seq)):
        kind = RowKind(row.kind)
        if kind is RowKind.EVIDENCE:
            part = AiSlot(row.answer_part)
            if part not in AI_PARTS:
                raise ValueError("근거의 부분이 세 부분이 아니다")
            evidence.append(EvidenceItem(part, row.seq, row.text))
        elif kind is RowKind.SOURCE:
            sources.append(SourceItem(row.seq, row.text, row.url or ""))
        elif kind is RowKind.COMPARED:
            if row.part is None or row.ref_seq is None or row.verdict is None:
                raise ValueError("견준 결과의 줄이 모자란다")
            compared.append(
                ComparedItem(_TITLE_TO_SLOT[row.part], row.ref_seq, row.text, Verdict(row.verdict))
            )
        else:
            if row.belief is None or row.compared is None or row.hits is None:
                raise ValueError("검증 목록의 줄이 모자란다")
            checklist.append(
                ChecklistItem(row.seq, row.text, Belief(row.belief), row.compared, row.hits)
            )
    return tuple(compared), tuple(checklist), tuple(evidence), tuple(sources)
