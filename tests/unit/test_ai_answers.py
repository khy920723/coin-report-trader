"""`ai/answers.py`의 `judge`(M2 명세 3.8.5, 3.8.2~3.8.4, 수용 시나리오 C7~C15). 값은 만든 값이다."""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

import pytest

from coin_report_trader.ai.answers import (
    FINAL_JUDGMENT_COLUMNS,
    REASON_LIMIT,
    REASON_TIMEOUT,
    JudgeContext,
    judge,
)
from coin_report_trader.ai.csv_files import ROWS_COLUMNS, write_csv
from coin_report_trader.core import (
    AiSlot,
    FailureKind,
    SessionOutcome,
    SessionResult,
)

AAA = "KRW-AAA"
P = Decimal("100")
BOM = chr(0xFEFF)


def rows_text(rows: Sequence[Sequence[str]]) -> str:
    return write_csv(ROWS_COLUMNS, rows)


def line(kind: str, seq: int, text: str = "글", **cells: str) -> list[str]:
    row = dict.fromkeys(ROWS_COLUMNS, "")
    row.update(market=AAA, kind=kind, seq=str(seq), text=text, **cells)
    return [row[c] for c in ROWS_COLUMNS]


def evidence(count: int) -> list[list[str]]:
    return [line("근거", i, f"근거 {i}") for i in range(1, count + 1)]


def source(count: int = 1) -> list[list[str]]:
    return [
        line("출처", i, f"제목 {i}", url=f"https://example.invalid/{i}")
        for i in range(1, count + 1)
    ]


def checklist(count: int = 1) -> list[list[str]]:
    return [
        line("검증 목록", i, f"항목 {i}", belief="높음", compared="2", hits="1")
        for i in range(1, count + 1)
    ]


def final_judgment(**overrides: str) -> str:
    row = {
        "market": AAA,
        "result_text": "",
        "direction": "오를 것",
        "predicted_price": "102",
        "predicted_reason": "까닭",
        "buy_price": "99",
        "sell_price": "",
    }
    row.update(overrides)
    return write_csv(FINAL_JUDGMENT_COLUMNS, [[row[c] for c in FINAL_JUDGMENT_COLUMNS]])


def answers(**overrides: str | None) -> dict[str, str]:
    base: dict[str, str | None] = {
        "search_judgment": write_csv(
            ("market", "judgment", "search_status"), [[AAA, "긍정", "성공"]]
        ),
        "search_rows": rows_text(evidence(2) + source(2)),
        "candle_judgment": write_csv(("market", "judgment"), [[AAA, "오름"]]),
        "candle_rows": rows_text(evidence(1)),
        "final_rows": rows_text(checklist(2) + evidence(1)),
        "final_judgment": final_judgment(),
    }
    base.update(overrides)
    return {key: value for key, value in base.items() if value is not None}


def result(
    given: Mapping[str, str], outcome: SessionOutcome = SessionOutcome.ANSWERED
) -> SessionResult:
    return SessionResult(outcome, dict(given), None, (), None, {})


def run(given: Mapping[str, str], context: JudgeContext | None = None, **kwargs: Any):
    return judge(result(given, **kwargs), context or JudgeContext(AAA, P))


def check_fail(given: Mapping[str, str], kind: FailureKind, context: JudgeContext | None = None):
    judged = run(given, context)
    assert judged.failure is kind, judged.reason
    assert judged.reason
    return judged


# --- 1. 세션의 결과 ------------------------------------------------------------------------


def test_a_good_answer_is_ok_and_carries_every_value_to_save() -> None:
    judged = run(answers())
    assert judged.failure is None
    assert judged.reason is None
    assert (judged.search_judgment, judged.search_status, judged.candle_judgment) == (
        "긍정", "성공", "오름",
    )  # fmt: skip
    assert (judged.direction, judged.predicted_price, judged.predicted_reason) == (
        "오를 것", "102", "까닭",
    )  # fmt: skip
    assert (judged.buy_price, judged.sell_price, judged.result_text) == ("99", None, None)
    parts = {(row.answer_part, row.kind) for row in judged.rows}
    assert parts == {
        (AiSlot.SEARCH, "근거"),
        (AiSlot.SEARCH, "출처"),
        (AiSlot.CANDLE, "근거"),
        (AiSlot.FINAL, "검증 목록"),
        (AiSlot.FINAL, "근거"),
    }
    assert len(judged.rows) == 2 + 2 + 1 + 2 + 1


def test_timeout_and_limit_and_a_failed_session_are_failures_without_answers() -> None:
    timeout = judge(result({}, SessionOutcome.TIMEOUT), JudgeContext(AAA, P))
    assert timeout.failure is FailureKind.TIMEOUT
    assert timeout.reason == REASON_TIMEOUT == "시간 제한(900초)을 넘겼습니다."
    limit = judge(result({}, SessionOutcome.LIMIT), JudgeContext(AAA, P))
    assert limit.failure is FailureKind.LIMIT
    assert (
        limit.reason == REASON_LIMIT == "Claude 구독 사용 한도에 닿았습니다. 앱의 문제가 아닙니다."
    )
    failed = judge(result({}, SessionOutcome.FAILED), JudgeContext(AAA, P))
    assert (
        failed.failure is FailureKind.FORMAT
    )  # 로그인 만료로 바꾸는 것은 `check()`를 아는 조립이다
    for judged in (timeout, limit, failed):
        assert judged.rows == ()
        assert judged.direction is None


def test_the_timeout_ignores_answers_that_came_with_it() -> None:
    judged = judge(result(answers(), SessionOutcome.TIMEOUT), JudgeContext(AAA, P))
    assert judged.failure is FailureKind.TIMEOUT
    assert judged.rows == ()


# --- 2~3. 두 판별의 형식 -------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing", ["search_judgment", "search_rows", "candle_judgment", "candle_rows"]
)
def test_a_missing_judge_field_is_a_format_error_and_the_final_answer_is_not_kept(
    missing: str,
) -> None:
    judged = check_fail(answers(**{missing: None}), FailureKind.FORMAT)
    assert missing in judged.reason
    assert judged.direction is None  # 최종 예측의 답은 있어도 보지 않고 버린다
    assert all(row.answer_part is not AiSlot.FINAL for row in judged.rows)


def test_a_neutral_search_judgment_and_a_flat_candle_judgment_fail_and_drop_the_final_answer() -> (
    None
):  # C11
    searched = check_fail(
        answers(
            search_judgment=write_csv(
                ("market", "judgment", "search_status"), [[AAA, "중립", "성공"]]
            )
        ),
        FailureKind.FORMAT,
    )
    assert searched.search_judgment == "중립"  # 저장된 값은 그대로다
    assert searched.direction is None
    assert not any(row.answer_part is AiSlot.FINAL for row in searched.rows)
    candled = check_fail(
        answers(candle_judgment=write_csv(("market", "judgment"), [[AAA, "보합"]])),
        FailureKind.FORMAT,
    )
    assert candled.candle_judgment == "보합"
    assert candled.predicted_price is None


def test_a_search_failure_comes_before_every_other_check_c12() -> None:
    failed_status = write_csv(("market", "judgment", "search_status"), [[AAA, "긍정", "실패"]])
    check_fail(answers(search_judgment=failed_status), FailureKind.SEARCH)
    check_fail(answers(search_rows=rows_text(evidence(2))), FailureKind.SEARCH)  # 출처가 0줄
    third = check_fail(
        answers(
            search_judgment=write_csv(
                ("market", "judgment", "search_status"), [[AAA, "중립", "실패"]]
            ),
            candle_judgment=write_csv(("market", "상태"), [[AAA, "오름"]]),  # 머리줄도 다름
        ),
        FailureKind.SEARCH,
    )
    assert third.reason == "검색 판별이 검색에 실패했다고 알렸습니다."
    assert third.candle_judgment is None  # 머리줄이 다른 표는 저장하지 않는다
    assert third.search_judgment == "중립"


def test_a_search_failure_also_beats_a_wrong_final_answer_and_timeouts_beat_it() -> None:
    failed_status = write_csv(("market", "judgment", "search_status"), [[AAA, "긍정", "실패"]])
    judged = check_fail(
        answers(search_judgment=failed_status, final_judgment="엉뚱한 글"), FailureKind.SEARCH
    )
    assert judged.direction is None
    timeout = judge(
        result(answers(search_judgment=failed_status), SessionOutcome.TIMEOUT), JudgeContext(AAA, P)
    )
    assert timeout.failure is FailureKind.TIMEOUT


def test_a_search_judgment_with_a_wrong_header_is_not_a_search_failure() -> None:
    wrong = write_csv(("market", "judgment", "status"), [[AAA, "긍정", "실패"]])
    check_fail(answers(search_judgment=wrong), FailureKind.FORMAT)


def test_the_search_failure_needs_a_readable_rows_header() -> None:
    wrong = write_csv(("market", "kind"), [[AAA, "근거"]])
    check_fail(
        answers(search_rows=wrong), FailureKind.FORMAT
    )  # 출처 0줄이어도 머리줄이 다르면 형식 오류


@pytest.mark.parametrize(
    "bad",
    [
        write_csv(
            ("market", "judgment", "search_status"), [[AAA, "긍정", "성공"], [AAA, "부정", "성공"]]
        ),
        write_csv(("market", "judgment", "search_status"), []),
        write_csv(("market", "judgment", "search_status"), [["KRW-BBB", "긍정", "성공"]]),
        write_csv(("market", "judgment", "search_status"), [[AAA, "긍정", "보류"]]),
        write_csv(("market", "judgment", "search_status"), [[AAA, "긍정"]]),
        write_csv(("market", "search_status", "judgment"), [[AAA, "성공", "긍정"]]),
        write_csv(("market", "judgment", "search_status"), [["", "긍정", "성공"]]),
        "",
        "아무 글",
    ],
)
def test_a_bad_search_judgment_table_is_a_format_error(bad: str) -> None:
    check_fail(answers(search_judgment=bad), FailureKind.FORMAT)


def test_the_header_is_checked_exactly_c13() -> None:
    renamed = write_csv(("market", "judgment", "search_state"), [[AAA, "긍정", "성공"]])
    reordered = write_csv(("judgment", "market", "search_status"), [["긍정", AAA, "성공"]])
    check_fail(answers(search_judgment=renamed), FailureKind.FORMAT)
    check_fail(answers(search_judgment=reordered), FailureKind.FORMAT)
    good = write_csv(("market", "judgment", "search_status"), [[AAA, "긍정", "성공"]])
    decorated = BOM + "```\r\n" + good + "```\r\n"  # BOM과 울타리 줄
    judged = run(answers(search_judgment=decorated))
    assert judged.failure is None
    assert judged.search_judgment == "긍정"


@pytest.mark.parametrize(
    "rows",
    [
        evidence(6) + source(1),  # 근거 6줄
        evidence(1) + source(11),  # 출처 11줄
        [line("근거", 1), line("근거", 3), *source(1)],  # 번호가 빠짐
        [line("근거", 1), line("근거", 1), *source(1)],  # 번호가 겹침
        [
            *evidence(1),
            *source(1),
            line("견준 결과", 1, part="봉 판별", ref_seq="1", verdict="맞음"),
        ],
        [*evidence(1), *source(1), line("검증 목록", 1, belief="높음", compared="1", hits="1")],
        [*evidence(1), *source(1), line("기타", 1)],
        [*evidence(1), line("출처", 1, "제목", url="ftp://example.invalid/a")],
        [*evidence(1), line("출처", 1, "제목", url="")],
        [*evidence(1), line("출처", 1, "제목", url="https://example.invalid/" + "a" * 500)],
        [line("근거", 1, "가" * 301), *source(1)],
        [*evidence(1), line("출처", 1, "가" * 201, url="https://example.invalid/a")],
        [line("근거", 1, ""), *source(1)],
        [line("근거", 1, "줄\n바꿈"), *source(1)],
        [line("근거", 1, part="봉 판별"), *source(1)],  # 비어 있어야 하는 칸이 참
        [*evidence(1), line("출처", 1, "제목", url="https://example.invalid/a", belief="높음")],
        [["KRW-BBB", *line("근거", 1)[1:]], *source(1)],  # 줄 하나만 코인이 다름 (C14)
    ],
)
def test_a_bad_search_rows_table_is_a_format_error(rows: list[list[str]]) -> None:
    check_fail(answers(search_rows=rows_text(rows)), FailureKind.FORMAT)


def test_the_limits_of_the_search_rows_pass() -> None:
    rows = [line("근거", i, "가" * 300) for i in range(1, 6)] + [
        line("출처", i, "나" * 200, url="https://example.invalid/" + "a" * 476)
        for i in range(1, 11)
    ]
    assert max(len(r[6]) for r in rows) == 500
    assert run(answers(search_rows=rows_text(rows))).failure is None


@pytest.mark.parametrize(
    "bad",
    [
        write_csv(("market", "judgment"), [[AAA, "오름"], [AAA, "내림"]]),
        write_csv(("market", "judgment"), [["KRW-BBB", "오름"]]),
        write_csv(("market", "judgment"), [[AAA, "보합"]]),
        write_csv(("market", "judgment"), []),
    ],
)
def test_a_bad_candle_judgment_is_a_format_error(bad: str) -> None:
    check_fail(answers(candle_judgment=bad), FailureKind.FORMAT)


@pytest.mark.parametrize(
    "rows",
    [
        [],  # 근거가 0줄
        evidence(6),
        [*evidence(1), *source(1)],  # 봉 판별에는 출처가 없다
        [*evidence(1), line("검증 목록", 1, belief="높음", compared="1", hits="1")],
    ],
)
def test_bad_candle_rows_are_a_format_error(rows: list[list[str]]) -> None:
    check_fail(answers(candle_rows=rows_text(rows)), FailureKind.FORMAT)


def test_a_cell_count_that_does_not_match_the_header_is_a_format_error() -> None:
    short = "market,kind,seq\r\nKRW-AAA,근거,1\r\n"
    check_fail(answers(candle_rows=short), FailureKind.FORMAT)
    check_fail(answers(candle_rows="market,kind\r\n"), FailureKind.FORMAT)


# --- 5. 최종 예측 --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("predicted", "kind"),
    [("99", FailureKind.FORMAT), ("100", None), ("101", None)],
)
def test_an_up_prediction_below_the_given_price_is_wrong_c8(
    predicted: str, kind: FailureKind | None
) -> None:
    judged = run(answers(final_judgment=final_judgment(predicted_price=predicted)))
    assert judged.failure is kind


@pytest.mark.parametrize(
    ("predicted", "kind"),
    [("101", FailureKind.FORMAT), ("100", None), ("99", None)],
)
def test_a_down_prediction_above_the_given_price_is_wrong(
    predicted: str, kind: FailureKind | None
) -> None:
    judged = run(
        answers(
            final_judgment=final_judgment(
                direction="내릴 것", predicted_price=predicted, buy_price="", sell_price="101"
            )
        )
    )
    assert judged.failure is kind


@pytest.mark.parametrize(
    "overrides",
    [
        {"direction": ""},
        {"direction": "보류"},
        {"predicted_price": ""},
        {"predicted_reason": ""},
        {"predicted_price": "0"},
        {"predicted_price": "-5"},
        {"predicted_price": "abc"},
        {"predicted_price": "1e3"},
        {"predicted_price": "1,000"},
        {"buy_price": "abc"},
        {"buy_price": "0"},
        {"market": "KRW-BBB"},
        {"predicted_reason": "가" * 201},
        {"predicted_reason": "줄\n바꿈"},
        {"result_text": "가" * 1001},
    ],
)
def test_a_wrong_final_judgment_is_a_format_error_c9(overrides: dict[str, str]) -> None:
    judged = check_fail(answers(final_judgment=final_judgment(**overrides)), FailureKind.FORMAT)
    assert "final_judgment" in judged.reason
    if overrides.get("direction") == "보류":
        assert judged.direction == "보류"  # 저장된 값은 그대로다
    if overrides.get("direction") == "":
        assert judged.direction is None


def test_the_suggested_price_that_must_exist_c10() -> None:
    check_fail(answers(final_judgment=final_judgment(buy_price="")), FailureKind.FORMAT)
    check_fail(
        answers(
            final_judgment=final_judgment(
                direction="내릴 것", predicted_price="95", buy_price="90", sell_price=""
            )
        ),
        FailureKind.FORMAT,
    )
    ok = run(
        answers(
            final_judgment=final_judgment(buy_price="99", sell_price="")
        )  # 반대 제안가는 없어도 된다
    )
    assert ok.failure is None
    both = run(answers(final_judgment=final_judgment(buy_price="99", sell_price="105")))
    assert both.failure is None
    assert both.sell_price == "105"


def test_the_final_judgment_must_be_a_single_row_with_the_right_header() -> None:
    columns = FINAL_JUDGMENT_COLUMNS
    two = write_csv(columns, [["KRW-AAA", "", "오를 것", "102", "까닭", "99", ""]] * 2)
    check_fail(answers(final_judgment=two), FailureKind.FORMAT)
    check_fail(answers(final_judgment=write_csv(columns, [])), FailureKind.FORMAT)
    check_fail(
        answers(final_judgment=write_csv(columns[:-1], [["KRW-AAA"] * 6])), FailureKind.FORMAT
    )
    check_fail(answers(final_judgment=None), FailureKind.FORMAT)
    check_fail(answers(final_rows=None), FailureKind.FORMAT)


def test_a_last_judgment_makes_the_result_text_required() -> None:
    last = ((AiSlot.SEARCH, 1),)
    context = JudgeContext(AAA, P, last)
    compared = [line("견준 결과", 1, "앞 근거", part="검색 판별", ref_seq="1", verdict="맞음")]
    rows = rows_text(compared + checklist(1) + evidence(1))
    check_fail(
        answers(final_rows=rows, final_judgment=final_judgment(result_text="")),
        FailureKind.FORMAT,
        context,
    )
    ok = run(answers(final_rows=rows, final_judgment=final_judgment(result_text="결과")), context)
    assert ok.failure is None
    assert ok.result_text == "결과"
    # 지난 판단이 없으면 비어도 된다
    assert run(answers(final_judgment=final_judgment(result_text=""))).failure is None


def test_the_compared_rows_match_the_evidence_of_the_last_report_one_by_one_c7() -> None:
    last = (
        (AiSlot.SEARCH, 1),
        (AiSlot.SEARCH, 2),
        (AiSlot.CANDLE, 1),
        (AiSlot.FINAL, 1),
    )
    context = JudgeContext(AAA, P, last)
    titles = {AiSlot.SEARCH: "검색 판별", AiSlot.CANDLE: "봉 판별", AiSlot.FINAL: "최종 예측"}

    def compared(pairs: Sequence[tuple[AiSlot, int]]) -> list[list[str]]:
        return [
            line("견준 결과", i, "앞 근거", part=titles[part], ref_seq=str(ref), verdict="맞음")
            for i, (part, ref) in enumerate(pairs, start=1)
        ]

    def attempt(pairs: Sequence[tuple[AiSlot, int]]):
        rows = rows_text(compared(pairs) + checklist(1) + evidence(1))
        return run(
            answers(final_rows=rows, final_judgment=final_judgment(result_text="결과")), context
        )

    assert attempt(list(last)).failure is None  # 네 줄, 부분과 번호가 맞음
    assert attempt(list(reversed(last))).failure is None  # 순서는 따지지 않는다
    for pairs in (
        list(last[:3]),  # 세 줄
        [*last, (AiSlot.FINAL, 2)],  # 없는 번호
        [last[0], last[0], last[2], last[3]],  # 같은 번호 둘
        [*last[:3], (AiSlot.CANDLE, 1)],  # 다른 줄을 두 번
        [(AiSlot.SEARCH, 1), (AiSlot.SEARCH, 2), (AiSlot.CANDLE, 1), (AiSlot.CANDLE, 2)],
    ):
        assert attempt(pairs).failure is FailureKind.FORMAT, pairs


def test_compared_rows_are_not_allowed_without_a_last_judgment() -> None:
    compared = [line("견준 결과", 1, "앞 근거", part="검색 판별", ref_seq="1", verdict="맞음")]
    rows = rows_text(compared + checklist(1) + evidence(1))
    check_fail(answers(final_rows=rows), FailureKind.FORMAT)  # 지난 판단이 없는데 견준 결과가 있다


def test_a_compared_row_needs_a_known_part_an_integer_reference_and_a_verdict() -> None:
    context = JudgeContext(AAA, P, ((AiSlot.SEARCH, 1),))

    def attempt(**cells: str):
        base = {"part": "검색 판별", "ref_seq": "1", "verdict": "맞음"}
        base.update(cells)
        rows = rows_text([line("견준 결과", 1, "앞 근거", **base), *checklist(1), *evidence(1)])
        return run(
            answers(final_rows=rows, final_judgment=final_judgment(result_text="결과")), context
        )

    assert attempt().failure is None
    assert attempt(verdict="가릴 수 없음").failure is None
    for bad in (
        {"part": "코인 세션"},
        {"part": ""},
        {"ref_seq": "x"},
        {"ref_seq": ""},
        {"verdict": "보류"},
    ):
        assert attempt(**bad).failure is FailureKind.FORMAT, bad
    assert attempt(belief="높음").failure is FailureKind.FORMAT  # 비어 있어야 하는 칸


@pytest.mark.parametrize(
    "rows",
    [
        [],  # 검증 목록이 0줄
        [
            line("검증 목록", i, f"항목 {i}", belief="높음", compared="1", hits="0")
            for i in range(1, 22)
        ],
        [line("검증 목록", 1, "항목", belief="모름", compared="1", hits="0")],
        [line("검증 목록", 1, "항목", belief="높음", compared="x", hits="0")],
        [line("검증 목록", 1, "항목", belief="높음", compared="1", hits="")],
        [
            line("검증 목록", 1, "항목", belief="높음", compared="1", hits="2")
        ],  # 맞은 횟수가 견준 횟수보다 큼
        [line("검증 목록", 1, "가" * 101, belief="높음", compared="1", hits="0")],
        [
            line(
                "검증 목록",
                1,
                "항목",
                belief="높음",
                compared="1",
                hits="0",
                url="https://example.invalid/a",
            )
        ],
        [
            line("검증 목록", 2, "항목", belief="높음", compared="1", hits="0")
        ],  # 번호가 1부터가 아님
        [line("출처", 1, "제목", url="https://example.invalid/a")],  # 최종 예측에는 출처가 없다
    ],
)
def test_bad_checklist_rows_are_a_format_error(rows: list[list[str]]) -> None:
    final = rows_text(rows + evidence(1))
    check_fail(answers(final_rows=final), FailureKind.FORMAT)


def test_the_final_evidence_must_be_one_to_five_lines_and_the_checklist_up_to_twenty() -> None:
    check_fail(answers(final_rows=rows_text(checklist(1))), FailureKind.FORMAT)  # 근거 0줄
    check_fail(answers(final_rows=rows_text(checklist(1) + evidence(6))), FailureKind.FORMAT)
    assert run(answers(final_rows=rows_text(checklist(20) + evidence(5)))).failure is None
    assert run(answers(final_rows=rows_text(checklist(1) + evidence(1)))).failure is None


# --- 실패한 답의 저장 ----------------------------------------------------------------------


def test_a_failed_final_answer_keeps_the_values_of_the_tables_with_a_good_header_c9() -> None:
    judged = check_fail(
        answers(final_judgment=final_judgment(direction="보류", predicted_price="수가 아님")),
        FailureKind.FORMAT,
    )
    assert (judged.direction, judged.predicted_price, judged.predicted_reason) == (
        "보류", "수가 아님", "까닭",
    )  # fmt: skip
    assert (judged.search_judgment, judged.candle_judgment) == ("긍정", "오름")
    assert any(
        row.answer_part is AiSlot.FINAL for row in judged.rows
    )  # 두 판별이 맞으면 최종 표도 남긴다


def test_tables_with_another_header_or_no_field_are_not_saved() -> None:
    judged = check_fail(
        answers(
            final_judgment=final_judgment(direction="보류"),
            final_rows=write_csv(("market", "kind"), [[AAA, "근거"]]),
        ),
        FailureKind.FORMAT,
    )
    assert judged.direction == "보류"
    assert not any(row.answer_part is AiSlot.FINAL for row in judged.rows)
    missing = check_fail(answers(final_judgment=None, candle_rows=None), FailureKind.FORMAT)
    assert missing.direction is None
    assert not any(row.answer_part is AiSlot.CANDLE for row in missing.rows)


def test_rows_that_cannot_be_stored_are_dropped_and_repeated_keys_keep_the_first() -> None:
    rows = [
        line("근거", 1, "먼저"),
        line("근거", 1, "겹치는 뒤 줄"),
        line("근거", 2, "정수 아님", ref_seq="x"),
        line("근거", 3, ""),  # text가 빔
        line("근거", "x", "번호가 정수가 아님"),
        line("근거", 4, "다음"),
        line("기타", 1, "아무 종류"),
        line("출처", 1, "제목", url="https://example.invalid/a"),
    ]
    judged = check_fail(answers(candle_rows=rows_text(rows)), FailureKind.FORMAT)
    candle = [(r.kind, r.seq, r.text) for r in judged.rows if r.answer_part is AiSlot.CANDLE]
    assert candle == [
        ("근거", 1, "먼저"),
        ("근거", 4, "다음"),
        ("기타", 1, "아무 종류"),
        ("출처", 1, "제목"),
    ]
    assert not any(r.text == "정수 아님" for r in judged.rows)


def test_a_search_failure_keeps_the_candle_tables_that_have_a_good_header() -> None:
    failed = write_csv(("market", "judgment", "search_status"), [[AAA, "긍정", "실패"]])
    judged = check_fail(answers(search_judgment=failed), FailureKind.SEARCH)
    assert judged.candle_judgment == "오름"
    assert any(r.answer_part is AiSlot.CANDLE for r in judged.rows)
    assert not any(r.answer_part is AiSlot.FINAL for r in judged.rows)


def test_the_reason_never_carries_a_value_of_the_answer() -> None:
    secret = "지시-ZZZ"
    judged = check_fail(
        answers(final_judgment=final_judgment(direction=secret, predicted_reason=secret)),
        FailureKind.FORMAT,
    )
    assert secret not in judged.reason
    assert judged.reason == "final_judgment의 direction이 정한 값이 아닙니다."
    another = check_fail(
        answers(search_judgment=write_csv(("market", "judgment"), [[AAA, secret]])),
        FailureKind.FORMAT,
    )
    assert secret not in another.reason
    assert another.reason == "search_judgment의 머리줄이 다릅니다."


def test_the_stored_cells_are_trimmed_and_empty_cells_become_none() -> None:
    judged = run(answers(final_judgment=final_judgment(buy_price=" 99 ", sell_price="")))
    assert judged.buy_price == "99"
    assert judged.sell_price is None
    assert judged.result_text is None


# --- 검토의 고칠 것 -----------------------------------------------------------------------


def with_extra_line(text: str, extra: str) -> str:
    return text + extra + "\r\n"


WIDE = "KRW-AAA,근거,2,,,글,,,,,,x"  # 칸이 열둘이다(줄 표의 칸은 열하나)


@pytest.mark.parametrize(
    "field",
    [
        "search_judgment",
        "search_rows",
        "candle_judgment",
        "candle_rows",
        "final_rows",
        "final_judgment",
    ],
)
def test_a_row_with_the_wrong_number_of_cells_is_a_format_error_in_every_table(field: str) -> None:
    given = answers()
    extra = "KRW-AAA,긍정,성공,x" if field.endswith("judgment") else WIDE
    given[field] = with_extra_line(given[field], extra)
    judged = check_fail(given, FailureKind.FORMAT)  # 줄을 말없이 버리고 `OK`가 되지 않는다
    assert field in judged.reason
    short = answers()
    short[field] = with_extra_line(short[field], "KRW-AAA")
    check_fail(short, FailureKind.FORMAT)


def test_a_search_rows_table_without_sources_is_a_search_failure_even_with_a_broken_row() -> None:
    rows = with_extra_line(
        rows_text(evidence(1)), WIDE
    )  # 출처가 0줄이고 칸 수가 틀린 근거 줄이 있다
    judged = check_fail(answers(search_rows=rows), FailureKind.SEARCH)
    assert judged.reason == "검색 판별이 검색에 실패했다고 알렸습니다."


def test_a_broken_row_that_is_a_source_means_there_are_sources_so_it_is_a_format_error() -> None:
    wide_source = "KRW-AAA,출처,1,,,제목,https://example.invalid/a,,,,,x"
    rows = with_extra_line(rows_text(evidence(1)), wide_source)
    judged = check_fail(answers(search_rows=rows), FailureKind.FORMAT)
    assert "search_rows" in judged.reason
    check_fail(
        answers(search_rows=with_extra_line(rows_text(evidence(1)), "KRW-AAA,출처")),
        FailureKind.FORMAT,
    )
    # 둘째 칸이 아예 없는 줄은 출처로 세지 않는다
    check_fail(
        answers(search_rows=with_extra_line(rows_text(evidence(1)), "KRW-AAA")), FailureKind.SEARCH
    )


def test_the_compared_text_has_no_length_limit_in_the_spec() -> None:
    context = JudgeContext(AAA, P, ((AiSlot.SEARCH, 1),))
    for length in (300, 301, 1000):
        compared = [
            line("견준 결과", 1, "가" * length, part="검색 판별", ref_seq="1", verdict="맞음")
        ]
        rows = rows_text(compared + checklist(1) + evidence(1))
        judged = run(
            answers(final_rows=rows, final_judgment=final_judgment(result_text="결과")), context
        )
        assert judged.failure is None, length
    empty = [line("견준 결과", 1, "", part="검색 판별", ref_seq="1", verdict="맞음")]
    rows = rows_text(empty + checklist(1) + evidence(1))
    judged = run(
        answers(final_rows=rows, final_judgment=final_judgment(result_text="결과")), context
    )
    assert judged.failure is FailureKind.FORMAT  # 빈 글은 그대로 형식 오류다
