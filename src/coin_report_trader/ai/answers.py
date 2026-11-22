"""세션 결과를 가리는 `judge`(M2 명세 3.8.5). 계산만 한다.

자료 스레드가 세션 결과를 받으면 `judge(result, context)`로 가린다. 차례대로 보고 처음 걸린 것이
실패 종류다.

1. `TIMEOUT`: 시간 초과. `LIMIT`: 한도 초과. `FAILED`: 형식 오류(로그인 만료인지는 `check()`를 아는
   조립이 3.5.2의 5대로 바꾼다).
2. 검색 판별: 먼저 검색 실패(`search_status`가 `실패`인 줄, 또는 `출처`가 0줄)를 보고, 그다음에 형식
   오류를 본다.
3. 봉 판별: 같은 꼴로 형식 오류.
4. 2나 3이 실패이면 최종 예측의 답은 있어도 보지 않고 버린다(REQ-AI-013).
5. 최종 예측: 형식 오류.

까닭의 글(`Judged.reason`)은 정한 글로만 만든다. 표·열·규칙의 이름과 정한 말이고, 답의 값과 글,
Claude Code의 글은 넣지 않는다(3.11.1의 "바깥 글").

실패한 답은 여섯 표 가운데 머리줄이 맞는 표만 저장해 둔다. 머리줄이 다른 표와 필드가 없는 표는
저장하지 않고, 머리줄이 맞는 줄 표에서도 정수 칸이 정수가 아니거나 `text`가 빈 줄은 저장하지 않으며,
(`answer_part`, `kind`, `seq`)가 앞 줄과 겹치는 뒤 줄은 버린다. 2나 3이 실패이면 최종 예측의 표는
저장하지 않는다.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Final

from coin_report_trader.ai.csv_files import ROWS_COLUMNS, CsvError, read_csv
from coin_report_trader.core import (
    AI_SLOT_TITLES,
    SESSION_TIMEOUT_SECONDS,
    AiSlot,
    Belief,
    CandleJudgment,
    Direction,
    FailureKind,
    RowKind,
    SearchJudgment,
    SessionOutcome,
    SessionResult,
    Verdict,
)

FIELD_SEARCH_JUDGMENT: Final = "search_judgment"
FIELD_SEARCH_ROWS: Final = "search_rows"
FIELD_CANDLE_JUDGMENT: Final = "candle_judgment"
FIELD_CANDLE_ROWS: Final = "candle_rows"
FIELD_FINAL_ROWS: Final = "final_rows"
FIELD_FINAL_JUDGMENT: Final = "final_judgment"

SEARCH_JUDGMENT_COLUMNS: Final = ("market", "judgment", "search_status")
CANDLE_JUDGMENT_COLUMNS: Final = ("market", "judgment")
FINAL_JUDGMENT_COLUMNS: Final = (
    "market",
    "result_text",
    "direction",
    "predicted_price",
    "predicted_reason",
    "buy_price",
    "sell_price",
)

SEARCH_STATUS_OK: Final = "성공"
SEARCH_STATUS_FAILED: Final = "실패"

REASON_TIMEOUT: Final = f"시간 제한({SESSION_TIMEOUT_SECONDS}초)을 넘겼습니다."
REASON_LIMIT: Final = "Claude 구독 사용 한도에 닿았습니다. 앱의 문제가 아닙니다."
REASON_SESSION_FAILED: Final = "세션이 정상으로 끝나지 않았습니다."
REASON_SEARCH_FAILED: Final = "검색 판별이 검색에 실패했다고 알렸습니다."

_EVIDENCE_MAX: Final = 300
_SOURCE_MAX: Final = 200
_URL_MAX: Final = 500
_CHECKLIST_MAX: Final = 100
_REASON_MAX: Final = 200
_RESULT_TEXT_MAX: Final = 1000
_INTEGER: Final = re.compile(r"[0-9]+")
_PRICE: Final = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_SLOT_BY_TITLE: Final = {title: slot for slot, title in AI_SLOT_TITLES.items()}
_PART_TITLES: Final = (
    AI_SLOT_TITLES[AiSlot.SEARCH],
    AI_SLOT_TITLES[AiSlot.CANDLE],
    AI_SLOT_TITLES[AiSlot.FINAL],
)


@dataclass(frozen=True)
class JudgeContext:
    """`judge`가 답을 견주는 바탕: 마켓 코드, 최종 예측에 준 현재가, 지난 판단의 근거 줄."""

    market: str
    price: Decimal
    last_evidence: tuple[tuple[AiSlot, int], ...] | None = None
    """지난 판단이 있으면 그 `근거` 줄마다의 (`answer_part`, `seq`)(`final_last_report.csv`의 것).
    지난 판단이 없으면 `None`이다."""


@dataclass(frozen=True)
class SavedRow:
    """저장할 줄 표의 한 줄(글은 CSV의 글 그대로)."""

    answer_part: AiSlot
    kind: str
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
class Judged:
    """`judge`의 결과. `failure`가 `None`이면 정상(`OK`)이다.

    판단 칸과 줄은 저장할 값이다. 정상이면 모두 3.8의 값이고, 실패이면 머리줄이 맞는 표의 값
    그대로다(빈 칸은 `None`. "보류" 같은 값도 그대로 든다).
    """

    failure: FailureKind | None = None
    reason: str | None = None
    search_judgment: str | None = None
    search_status: str | None = None
    candle_judgment: str | None = None
    direction: str | None = None
    predicted_price: str | None = None
    predicted_reason: str | None = None
    buy_price: str | None = None
    sell_price: str | None = None
    result_text: str | None = None
    rows: tuple[SavedRow, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class _Table:
    """읽은 표: 머리줄이 맞는지, 맞으면 칸 이름에서 글로의 줄들(칸 수가 맞는 줄만)."""

    header_ok: bool
    rows: tuple[dict[str, str], ...]
    broken: int
    """머리줄이 맞는데 칸 수가 맞지 않은 줄의 수."""
    broken_kinds: tuple[str, ...]
    """그 줄들의 둘째 칸(`kind`)의 글. 칸이 모자라 없으면 빈 글이다."""
    multiline: bool
    """어느 칸에 줄바꿈이 있다."""


def judge(result: SessionResult, context: JudgeContext) -> Judged:
    """세션 결과를 가린다(모듈 설명의 차례)."""
    if result.outcome is SessionOutcome.TIMEOUT:
        return Judged(FailureKind.TIMEOUT, REASON_TIMEOUT)
    if result.outcome is SessionOutcome.LIMIT:
        return Judged(FailureKind.LIMIT, REASON_LIMIT)
    if result.outcome is not SessionOutcome.ANSWERED:
        return Judged(FailureKind.FORMAT, REASON_SESSION_FAILED)
    answers = result.answers
    tables = {
        FIELD_SEARCH_JUDGMENT: _load(answers, FIELD_SEARCH_JUDGMENT, SEARCH_JUDGMENT_COLUMNS),
        FIELD_SEARCH_ROWS: _load(answers, FIELD_SEARCH_ROWS, ROWS_COLUMNS),
        FIELD_CANDLE_JUDGMENT: _load(answers, FIELD_CANDLE_JUDGMENT, CANDLE_JUDGMENT_COLUMNS),
        FIELD_CANDLE_ROWS: _load(answers, FIELD_CANDLE_ROWS, ROWS_COLUMNS),
        FIELD_FINAL_ROWS: _load(answers, FIELD_FINAL_ROWS, ROWS_COLUMNS),
        FIELD_FINAL_JUDGMENT: _load(answers, FIELD_FINAL_JUDGMENT, FINAL_JUDGMENT_COLUMNS),
    }
    early = _early_failure(tables, context)
    final_failure = None
    if early is None:
        final_failure = _check_final(tables, context)
    failure = early or final_failure
    saved = _saved(tables, include_final=early is None)
    if failure is None:
        return Judged(None, None, **saved)
    return Judged(failure[0], failure[1], **saved)


# --- 읽기 -------------------------------------------------------------------------------------


def _load(answers: Mapping[str, str], name: str, columns: Sequence[str]) -> _Table | None:
    """그 필드의 표를 읽는다. 필드가 없으면 `None`이다. 읽지 못하는 글은 머리줄이 맞지 않은 표다."""
    text = answers.get(name)
    if text is None:
        return None
    try:
        header, raw_rows = read_csv(text)
    except CsvError:
        return _Table(False, (), 0, (), False)
    if header != tuple(columns):
        return _Table(False, (), 0, (), False)
    rows: list[dict[str, str]] = []
    broken_kinds: list[str] = []
    multiline = False
    for raw in raw_rows:
        if len(raw) != len(columns):
            broken_kinds.append(raw[1] if len(raw) > 1 else "")
            continue
        if any("\n" in cell or "\r" in cell for cell in raw):
            multiline = True
        rows.append(dict(zip(columns, raw, strict=True)))
    return _Table(True, tuple(rows), len(broken_kinds), tuple(broken_kinds), multiline)


# --- 검사 -------------------------------------------------------------------------------------

Failure = tuple[FailureKind, str]


def _format(reason: str) -> Failure:
    return FailureKind.FORMAT, reason


def _early_failure(tables: Mapping[str, _Table | None], context: JudgeContext) -> Failure | None:
    """2(검색 판별)와 3(봉 판별)."""
    search_judgment = tables[FIELD_SEARCH_JUDGMENT]
    search_rows = tables[FIELD_SEARCH_ROWS]
    # 검색 실패가 다른 검사보다 먼저다(8절): 다른 칸이나 다른 부분의 답이 틀려도 앞선다.
    if (
        search_judgment is not None
        and search_judgment.header_ok
        and any(row["search_status"] == SEARCH_STATUS_FAILED for row in search_judgment.rows)
    ):
        return FailureKind.SEARCH, REASON_SEARCH_FAILED
    if (
        search_rows is not None
        and search_rows.header_ok
        and not any(row["kind"] == RowKind.SOURCE.value for row in search_rows.rows)
        and RowKind.SOURCE.value not in search_rows.broken_kinds
    ):
        return FailureKind.SEARCH, REASON_SEARCH_FAILED
    for reason in (
        _check_judgment(
            FIELD_SEARCH_JUDGMENT,
            search_judgment,
            context,
            {
                "judgment": tuple(j.value for j in SearchJudgment),
                "search_status": (SEARCH_STATUS_OK,),
            },
        ),
        _check_rows(
            FIELD_SEARCH_ROWS,
            search_rows,
            context,
            {RowKind.EVIDENCE: (1, 5), RowKind.SOURCE: (1, 10)},
        ),
        _check_judgment(
            FIELD_CANDLE_JUDGMENT,
            tables[FIELD_CANDLE_JUDGMENT],
            context,
            {"judgment": tuple(j.value for j in CandleJudgment)},
        ),
        _check_rows(
            FIELD_CANDLE_ROWS, tables[FIELD_CANDLE_ROWS], context, {RowKind.EVIDENCE: (1, 5)}
        ),
    ):
        if reason is not None:
            return _format(reason)
    return None


def _check_final(tables: Mapping[str, _Table | None], context: JudgeContext) -> Failure | None:
    """5(최종 예측)."""
    name = FIELD_FINAL_JUDGMENT
    reason = _final_judgment_reason(tables[name], context)
    if reason is None:
        reason = _check_rows(
            FIELD_FINAL_ROWS,
            tables[FIELD_FINAL_ROWS],
            context,
            {RowKind.COMPARED: (0, 10_000), RowKind.CHECKLIST: (1, 20), RowKind.EVIDENCE: (1, 5)},
        )
    if reason is None:
        reason = _compared_reason(tables[FIELD_FINAL_ROWS], context)
    return None if reason is None else _format(reason)


def _present(name: str, table: _Table | None) -> str | None:
    """필드가 있고 머리줄이 맞고 칸 수가 맞는 줄만 있는지."""
    if table is None:
        return f"{name}가 없습니다."
    if not table.header_ok:
        return f"{name}의 머리줄이 다릅니다."
    if table.broken or table.multiline:
        return f"{name}의 줄이 정한 꼴이 아닙니다."
    return None


def _check_judgment(
    name: str,
    table: _Table | None,
    context: JudgeContext,
    allowed: Mapping[str, tuple[str, ...]],
) -> str | None:
    """판단 표(한 줄): 코인 코드와 정한 값."""
    reason = _present(name, table)
    if reason is not None:
        return reason
    assert table is not None
    if len(table.rows) != 1:
        return f"{name}의 줄 수가 맞지 않습니다."
    row = table.rows[0]
    if row["market"] != context.market:
        return f"{name}의 market이 이 코인과 다릅니다."
    for column, values in allowed.items():
        if row[column] not in values:
            return f"{name}의 {column}이 정한 값이 아닙니다."
    return None


def _final_judgment_reason(table: _Table | None, context: JudgeContext) -> str | None:
    name = FIELD_FINAL_JUDGMENT
    reason = _present(name, table)
    if reason is not None:
        return reason
    assert table is not None
    if len(table.rows) != 1:
        return f"{name}의 줄 수가 맞지 않습니다."
    row = table.rows[0]
    if row["market"] != context.market:
        return f"{name}의 market이 이 코인과 다릅니다."
    if row["direction"] not in tuple(d.value for d in Direction):
        return f"{name}의 direction이 정한 값이 아닙니다."
    if not row["predicted_price"]:
        return f"{name}의 predicted_price가 비었습니다."
    if not row["predicted_reason"]:
        return f"{name}의 predicted_reason이 비었습니다."
    prices: dict[str, Decimal] = {}
    for column in ("predicted_price", "buy_price", "sell_price"):
        text = row[column]
        if not text and column != "predicted_price":
            continue
        price = _price(text)
        if price is None:
            return f"{name}의 {column}이 0보다 큰 수가 아닙니다."
        prices[column] = price
    predicted = prices["predicted_price"]
    if row["direction"] == Direction.UP.value and predicted < context.price:
        return f"{name}의 predicted_price가 direction과 반대쪽입니다."
    if row["direction"] == Direction.DOWN.value and predicted > context.price:
        return f"{name}의 predicted_price가 direction과 반대쪽입니다."
    if row["direction"] == Direction.UP.value and "buy_price" not in prices:
        return f"{name}의 buy_price가 없습니다."
    if row["direction"] == Direction.DOWN.value and "sell_price" not in prices:
        return f"{name}의 sell_price가 없습니다."
    if len(row["predicted_reason"]) > _REASON_MAX:
        return f"{name}의 predicted_reason이 너무 깁니다."
    if len(row["result_text"]) > _RESULT_TEXT_MAX:
        return f"{name}의 result_text가 너무 깁니다."
    if context.last_evidence is not None and not row["result_text"]:
        return f"{name}의 result_text가 비었습니다."
    return None


def _price(text: str) -> Decimal | None:
    if _PRICE.fullmatch(text) is None:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value > 0 else None


def _check_rows(
    name: str,
    table: _Table | None,
    context: JudgeContext,
    kinds: Mapping[RowKind, tuple[int, int]],
) -> str | None:
    """줄 표: 정한 종류만, 종류마다 줄 수와 번호, 칸마다 정한 꼴."""
    reason = _present(name, table)
    if reason is not None:
        return reason
    assert table is not None
    by_kind: dict[str, list[dict[str, str]]] = {kind.value: [] for kind in kinds}
    for row in table.rows:
        if row["market"] != context.market:
            return f"{name}의 market이 이 코인과 다릅니다."
        if row["kind"] not in by_kind:
            return f"{name}의 kind가 정한 값이 아닙니다."
        by_kind[row["kind"]].append(row)
    for kind, (low, high) in kinds.items():
        rows = by_kind[kind.value]
        if not low <= len(rows) <= high:
            return f"{name}의 {kind.value} 줄 수가 맞지 않습니다."
        seqs = []
        for row in rows:
            if _INTEGER.fullmatch(row["seq"]) is None:
                return f"{name}의 seq가 정수가 아닙니다."
            seqs.append(int(row["seq"]))
        if sorted(seqs) != list(range(1, len(rows) + 1)):
            return f"{name}의 {kind.value} seq가 1부터 빠짐없지 않습니다."
        for row in rows:
            reason = _row_reason(name, kind, row)
            if reason is not None:
                return reason
    return None


def _blank(name: str, row: Mapping[str, str], columns: Sequence[str]) -> str | None:
    for column in columns:
        if row[column]:
            return f"{name}의 {column}은 이 종류에서 비어 있어야 합니다."
    return None


def _row_reason(name: str, kind: RowKind, row: Mapping[str, str]) -> str | None:
    text = row["text"]
    if not text:
        return f"{name}의 text가 비었습니다."
    if kind is RowKind.EVIDENCE:
        if len(text) > _EVIDENCE_MAX:
            return f"{name}의 text가 너무 깁니다."
        return _blank(
            name, row, ("part", "ref_seq", "url", "verdict", "belief", "compared", "hits")
        )
    if kind is RowKind.SOURCE:
        if len(text) > _SOURCE_MAX:
            return f"{name}의 text가 너무 깁니다."
        url = row["url"]
        if not url.startswith(("http://", "https://")) or len(url) > _URL_MAX:
            return f"{name}의 url이 정한 꼴이 아닙니다."
        return _blank(name, row, ("part", "ref_seq", "verdict", "belief", "compared", "hits"))
    if kind is RowKind.COMPARED:
        if row["part"] not in _PART_TITLES:
            return f"{name}의 part가 정한 값이 아닙니다."
        if _INTEGER.fullmatch(row["ref_seq"]) is None:
            return f"{name}의 ref_seq가 정수가 아닙니다."
        if row["verdict"] not in tuple(v.value for v in Verdict):
            return f"{name}의 verdict가 정한 값이 아닙니다."
        return _blank(name, row, ("url", "belief", "compared", "hits"))
    # 검증 목록
    if len(text) > _CHECKLIST_MAX:
        return f"{name}의 text가 너무 깁니다."
    if row["belief"] not in tuple(b.value for b in Belief):
        return f"{name}의 belief가 정한 값이 아닙니다."
    if _INTEGER.fullmatch(row["compared"]) is None or _INTEGER.fullmatch(row["hits"]) is None:
        return f"{name}의 compared나 hits가 정수가 아닙니다."
    if int(row["hits"]) > int(row["compared"]):
        return f"{name}의 hits가 compared보다 큽니다."
    return _blank(name, row, ("part", "ref_seq", "url", "verdict"))


def _compared_reason(table: _Table | None, context: JudgeContext) -> str | None:
    """`견준 결과`는 지난 판단의 `근거` 줄과 하나씩 맞아야 한다(REQ-AI-014). 없으면 0줄이다."""
    assert table is not None
    given = [
        (_SLOT_BY_TITLE[row["part"]], int(row["ref_seq"]))
        for row in table.rows
        if row["kind"] == RowKind.COMPARED.value
    ]
    wanted = [] if context.last_evidence is None else list(context.last_evidence)
    if len(given) != len(set(given)) or sorted(given) != sorted(wanted):
        return f"{FIELD_FINAL_ROWS}의 견준 결과가 지난 판단의 근거 줄과 하나씩 맞지 않습니다."
    return None


# --- 저장할 값 -----------------------------------------------------------------------------------


def _first(table: _Table | None) -> Mapping[str, str] | None:
    if table is None or not table.header_ok or not table.rows:
        return None
    return table.rows[0]


def _text_or_none(row: Mapping[str, str] | None, column: str) -> str | None:
    if row is None:
        return None
    return row[column] or None


def _saved(tables: Mapping[str, _Table | None], *, include_final: bool) -> dict[str, object]:
    search = _first(tables[FIELD_SEARCH_JUDGMENT])
    candle = _first(tables[FIELD_CANDLE_JUDGMENT])
    final = _first(tables[FIELD_FINAL_JUDGMENT]) if include_final else None
    rows: list[SavedRow] = []
    seen: set[tuple[AiSlot, str, int]] = set()
    sources = [
        (AiSlot.SEARCH, tables[FIELD_SEARCH_ROWS]),
        (AiSlot.CANDLE, tables[FIELD_CANDLE_ROWS]),
    ]
    if include_final:
        sources.append((AiSlot.FINAL, tables[FIELD_FINAL_ROWS]))
    for part, table in sources:
        if table is None or not table.header_ok:
            continue
        for raw in table.rows:
            row = _saved_row(part, raw)
            if row is None:
                continue
            key = (row.answer_part, row.kind, row.seq)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return {
        "search_judgment": _text_or_none(search, "judgment"),
        "search_status": _text_or_none(search, "search_status"),
        "candle_judgment": _text_or_none(candle, "judgment"),
        "direction": _text_or_none(final, "direction"),
        "predicted_price": _text_or_none(final, "predicted_price"),
        "predicted_reason": _text_or_none(final, "predicted_reason"),
        "buy_price": _text_or_none(final, "buy_price"),
        "sell_price": _text_or_none(final, "sell_price"),
        "result_text": _text_or_none(final, "result_text"),
        "rows": tuple(rows),
    }


def _saved_row(part: AiSlot, raw: Mapping[str, str]) -> SavedRow | None:
    """저장할 수 있는 줄만: 정수 칸이 정수이고 `text`가 비지 않은 줄."""
    if not raw["text"] or _INTEGER.fullmatch(raw["seq"]) is None:
        return None
    ints: dict[str, int | None] = {}
    for column in ("ref_seq", "compared", "hits"):
        text = raw[column]
        if text and _INTEGER.fullmatch(text) is None:
            return None
        ints[column] = int(text) if text else None
    return SavedRow(
        answer_part=part,
        kind=raw["kind"],
        seq=int(raw["seq"]),
        text=raw["text"],
        part=raw["part"] or None,
        ref_seq=ints["ref_seq"],
        url=raw["url"] or None,
        verdict=raw["verdict"] or None,
        belief=raw["belief"] or None,
        compared=ints["compared"],
        hits=ints["hits"],
    )
