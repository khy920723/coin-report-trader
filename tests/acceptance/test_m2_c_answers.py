"""M2 명세 6절의 답과 지난 판단 시나리오: C4~C15(3.8, 3.8.5, 3.9, 3.11, 3.12.1).

가짜 실행기가 코인마다 정한 답(만든 CSV)을 내고, 자료 스레드가 `judge`로 가려 저장한다. 화면의
글은 자료 스레드가 보낸 `ReportListView`·`ReportView`를 창의 `apply`에 넣어 본다(`show_report`).
값은 모두 만든 값이다.
"""

import threading
from collections.abc import Callable, Iterator, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from acceptance.m2_support import AAA, BBB, FIRST_CLOSE, HOUR, Rig, make_rig, show_report
from fakes.ai import FakeAiRunner, answered, good_answers, limit, timeout
from fakes.quote import make_candles, with_close
from PySide6.QtWidgets import QApplication

from coin_report_trader.ai import inputs as ai_inputs
from coin_report_trader.ai.answers import (
    CANDLE_JUDGMENT_COLUMNS,
    FINAL_JUDGMENT_COLUMNS,
    SEARCH_JUDGMENT_COLUMNS,
)
from coin_report_trader.ai.csv_files import ROWS_COLUMNS, read_csv, write_csv
from coin_report_trader.core import (
    AiStatus,
    LoadState,
    ReportListView,
    ReportState,
    ReportView,
    SaveResult,
    Verdict,
)

SAFETY = 10.0
PARTS = [("검색 판별", 1), ("봉 판별", 1), ("최종 예측", 1)]
FORMAT = "형식 오류"


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Callable[..., Rig]]:
    made: list[Rig] = []

    def factory(**kwargs: Any) -> Rig:
        rig = make_rig(tmp_path / f"r{len(made)}", **kwargs)
        made.append(rig)
        return rig

    yield factory
    for rig in made:
        rig.stop()


def row(market: str, kind: str, seq: int, text: str, **cells: str) -> list[str]:
    values = dict.fromkeys(ROWS_COLUMNS, "")
    values.update(market=market, kind=kind, seq=str(seq), text=text, **cells)
    return [values[column] for column in ROWS_COLUMNS]


def rows_csv(*rows: list[str]) -> str:
    return write_csv(ROWS_COLUMNS, list(rows))


def final_judgment(
    *,
    market: str = AAA,
    result_text: str = "",
    direction: str = "오를 것",
    predicted: str = "102",
    reason: str = "까닭",
    buy: str = "99",
    sell: str = "",
) -> str:
    return write_csv(
        FINAL_JUDGMENT_COLUMNS, [[market, result_text, direction, predicted, reason, buy, sell]]
    )


def answers(**changes: str) -> dict[str, str]:
    """`KRW-AAA`의 정상 답(첫 회차의 것)에서 필드를 바꾼 답."""
    base = good_answers(AAA)
    base.update(changes)
    return base


def follow_up(compared: Sequence[tuple[str, int]] = PARTS, **changes: str) -> dict[str, str]:
    """지난 판단이 있는 회차의 정상 답(결과 글과 견준 결과가 있다)에서 필드를 바꾼 답."""
    base = good_answers(AAA, result_text="지난 결과 글", compared=compared)
    base.update(changes)
    return base


def one_round(build: Callable[..., Rig], answer: dict[str, str]) -> Rig:
    runner = FakeAiRunner()
    runner.plan(AAA, answered(answer))
    rig = build(runner=runner)
    rig.start()
    rig.round()
    return rig


def only(rig: Rig) -> Any:
    (item,) = rig.reports(AAA)
    return item


def put_actual(rig: Rig, close: str) -> None:
    """회차 1의 T(04:00)에 시작하는 봉(그 예측이 가리킨 05:00에 끝남)의 종가."""
    candle = make_candles(AAA, HOUR, 1, last_start=FIRST_CLOSE)[0]
    rig.quote.put_candles(with_close(candle, Decimal(close)))


def last_judgment_row(rig: Rig, index: int) -> dict[str, str]:
    header, rows = read_csv(rig.runner.requests[index].files[ai_inputs.FILE_FINAL_LAST_JUDGMENT])
    (cells,) = rows
    return dict(zip(header, cells, strict=True))


# --- C4, C5 -----------------------------------------------------------------------------------


@pytest.mark.scenario("C4")
def test_c4_the_next_round_gets_the_last_judgment_with_the_actual_value_and_the_screen_diff(
    build: Callable[..., Rig], qapp: QApplication
) -> None:
    runner = FakeAiRunner()
    rig = build(runner=runner)
    rig.start()
    rig.round()  # 회차 1: 현재가 100, 오를 것, 예측 102
    put_actual(rig, "101")
    runner.plan(AAA, answered(follow_up()))
    rig.round()
    cells = last_judgment_row(rig, 1)
    assert (cells["round_number"], cells["base_price"], cells["predicted_price"]) == (
        "1",
        "100",
        "102",
    )
    assert (cells["actual_price"], cells["diff"], cells["hit"]) == ("101", "-0.0098", "맞음")
    second = rig.reports(AAA)[0]
    view = rig.report(second.report_id)
    assert view.last_result is not None
    assert (view.last_result.round_number, view.last_result.predicted_price) == (1, Decimal(102))
    assert (view.last_result.actual_price, view.last_result.diff, view.last_result.hit) == (
        Decimal(101),
        Decimal("-0.0098"),
        Verdict.HIT,
    )
    probe = show_report(rig, AAA, second.report_id)
    try:
        assert "차이: -0.98%" in probe.lines("reportLastResult")
    finally:
        probe.close()


@pytest.mark.scenario("C5")
@pytest.mark.parametrize(
    ("direction", "predicted", "buy", "sell", "actual", "hit", "diff"),
    [
        ("오를 것", "102", "99", "", "99", "틀림", "-0.0294"),
        ("오를 것", "102", "99", "", "100", "맞음", "-0.0196"),
        ("내릴 것", "98", "", "101", "100", "맞음", None),
    ],
    ids=["up-99", "up-100", "down-100"],
)
def test_c5_the_hit_is_judged_against_the_base_price(
    build: Callable[..., Rig],
    direction: str,
    predicted: str,
    buy: str,
    sell: str,
    actual: str,
    hit: str,
    diff: str | None,
) -> None:
    runner = FakeAiRunner()
    runner.plan(
        AAA,
        answered(
            answers(
                final_judgment=final_judgment(
                    direction=direction, predicted=predicted, buy=buy, sell=sell
                )
            )
        ),
    )
    rig = build(runner=runner)
    rig.start()
    rig.round()
    assert only(rig).state is ReportState.OK
    put_actual(rig, actual)
    runner.plan(AAA, answered(follow_up()))
    rig.round()
    cells = last_judgment_row(rig, 1)
    assert (cells["direction"], cells["actual_price"], cells["hit"]) == (direction, actual, hit)
    if diff is not None:
        assert cells["diff"] == diff
    view = rig.report(rig.reports(AAA)[0].report_id)
    assert view.last_result is not None
    assert view.last_result.hit is Verdict(hit)


# --- C6 ---------------------------------------------------------------------------------------


@pytest.mark.scenario("C6")
def test_c6_a_failed_round_is_not_the_last_judgment_and_its_rows_are_not_given(
    build: Callable[..., Rig],
) -> None:
    runner = FakeAiRunner()
    rig = build(runner=runner)
    rig.start()
    rig.round()  # 회차 1: OK
    second = follow_up(
        final_judgment=final_judgment(result_text="결과", direction="보류"),
        search_rows=rows_csv(
            row(AAA, "근거", 1, "둘째 회차의 근거"),
            row(AAA, "출처", 1, "둘째 회차의 출처", url="https://example.com/b"),
        ),
    )
    runner.plan(AAA, answered(second))
    rig.round()  # 회차 2: FAILED
    assert [i.state for i in rig.reports(AAA)] == [ReportState.FAILED, ReportState.OK]
    runner.plan(AAA, answered(follow_up()))
    rig.round()
    assert last_judgment_row(rig, 2)["round_number"] == "1"  # 지난 판단은 회차 1이다
    report = rig.runner.requests[2].files[ai_inputs.FILE_FINAL_LAST_REPORT]
    assert "둘째 회차" not in report  # 회차 1의 줄만 있다
    _, rows = read_csv(report)
    assert len(rows) == 5  # 회차 1의 줄 표 전부(검색 판별 둘, 봉 판별 하나, 최종 예측 둘)
    assert {cells[0] for cells in rows} == {"검색 판별", "봉 판별", "최종 예측"}
    assert rig.reports(AAA)[0].state is ReportState.OK


# --- C7 ---------------------------------------------------------------------------------------


def first_with_four_evidence() -> dict[str, str]:
    """근거가 검색 판별 2줄, 봉 판별 1줄, 최종 예측 1줄인 회차 1의 답."""
    return answers(
        search_rows=rows_csv(
            row(AAA, "근거", 1, "검색 근거 하나"),
            row(AAA, "근거", 2, "검색 근거 둘"),
            row(AAA, "출처", 1, "제목", url="https://example.com/a"),
        )
    )


def compared_rows(pairs: Sequence[tuple[int, str, int]]) -> str:
    """(줄 번호, 부분, 근거 번호)의 `견준 결과`와 검증 목록, 근거."""
    found = [
        row(AAA, "견준 결과", seq, "앞 근거", part=part, ref_seq=str(ref), verdict="맞음")
        for seq, part, ref in pairs
    ]
    found += [
        row(AAA, "검증 목록", 1, "항목", belief="높음", compared="2", hits="1"),
        row(AAA, "근거", 1, "최종 근거"),
    ]
    return rows_csv(*found)


FOUR_COMPARED = [(1, "검색 판별", 1), (2, "검색 판별", 2), (3, "봉 판별", 1), (4, "최종 예측", 1)]


def second_round(build: Callable[..., Rig], final_rows: str) -> Rig:
    runner = FakeAiRunner()
    runner.plan(AAA, answered(first_with_four_evidence()))
    rig = build(runner=runner)
    rig.start()
    rig.round()
    assert only(rig).state is ReportState.OK
    runner.plan(AAA, answered(follow_up(final_rows=final_rows)))
    rig.round()
    return rig


@pytest.mark.scenario("C7")
def test_c7_four_comparisons_that_match_are_ok_and_shown_with_their_part_names(
    build: Callable[..., Rig], qapp: QApplication
) -> None:
    rig = second_round(build, compared_rows(FOUR_COMPARED))
    newest = rig.reports(AAA)[0]
    assert newest.state is ReportState.OK
    probe = show_report(rig, AAA, newest.report_id)
    try:
        lines = [line for line in probe.lines("reportLastResult") if "→" in line]
        assert lines == [
            "검색 판별 1. 앞 근거 → 맞음",
            "검색 판별 2. 앞 근거 → 맞음",
            "봉 판별 1. 앞 근거 → 맞음",
            "최종 예측 1. 앞 근거 → 맞음",
        ]
    finally:
        probe.close()


@pytest.mark.scenario("C7")
def test_c7_three_comparisons_are_a_format_error(build: Callable[..., Rig]) -> None:
    rig = second_round(build, compared_rows(FOUR_COMPARED[:3]))
    newest = rig.reports(AAA)[0]
    assert (newest.state, newest.failure) == (ReportState.FAILED, FORMAT)


@pytest.mark.scenario("C7")
def test_c7_a_repeated_number_is_a_format_error_saved_without_the_later_row(
    build: Callable[..., Rig],
) -> None:
    repeated = [(1, "검색 판별", 1), (1, "검색 판별", 2), (3, "봉 판별", 1), (4, "최종 예측", 1)]
    rig = second_round(build, compared_rows(repeated))
    newest = rig.reports(AAA)[0]
    assert (newest.state, newest.failure) == (ReportState.FAILED, FORMAT)
    assert "A-29" not in rig.codes()  # 겹친 뒤 줄을 버려 저장이 실패하지 않는다
    assert AiStatus.ERROR_OFF not in rig.statuses()  # AI가 꺼지지 않는다
    stored = rig.rows(
        "SELECT seq, ref_seq FROM report_rows WHERE report_id = ? AND kind = '견준 결과'"
        " ORDER BY seq",
        newest.report_id,
    )
    assert stored == [(1, 1), (3, 1), (4, 1)]  # (최종 예측, 견준 결과, 1)이 겹친 뒤 줄은 없다
    sent = len(rig.sink.items)
    rig.data.load_reports(AAA)
    rig.data.load_report(newest.report_id)
    rig.settle()
    with rig.sink.lock:
        after = rig.sink.items[sent:]
    listing = [v for v in after if isinstance(v, ReportListView)][-1]  # 마지막으로 보낸 목록
    assert (listing.market, listing.state) == (AAA, LoadState.READY)
    assert [i.state for i in listing.items] == [ReportState.FAILED, ReportState.OK]
    (shown,) = [v for v in after if isinstance(v, ReportView)]
    assert (shown.report_id, shown.state) == (newest.report_id, ReportState.FAILED)
    assert [r for r in rig.sink.of(SaveResult) if not r.ok] == []  # 읽기가 깨지지 않았다


# --- C8 ---------------------------------------------------------------------------------------


@pytest.mark.scenario("C8")
@pytest.mark.parametrize(
    ("predicted", "state"),
    [("99", ReportState.FAILED), ("100", ReportState.OK), ("101", ReportState.OK)],
)
def test_c8_an_up_prediction_below_the_price_is_a_format_error(
    build: Callable[..., Rig], predicted: str, state: ReportState
) -> None:
    rig = one_round(build, answers(final_judgment=final_judgment(predicted=predicted)))
    item = only(rig)
    assert item.state is state
    if state is ReportState.FAILED:
        assert item.failure == FORMAT


# --- C9 ---------------------------------------------------------------------------------------


@pytest.mark.scenario("C9")
def test_c9_a_missing_or_wrong_direction_price_or_reason_is_a_format_error_with_one_a17(
    build: Callable[..., Rig], qapp: QApplication
) -> None:
    runner = FakeAiRunner()
    rig = build(runner=runner)
    rig.start()
    for answer in (
        final_judgment(direction=""),
        final_judgment(direction="보류"),
        final_judgment(predicted=""),
        final_judgment(reason=""),
    ):
        runner.plan(AAA, answered(answers(final_judgment=answer)))
        rig.round()
    items = rig.reports(AAA)
    assert [(i.state, i.failure) for i in items] == [(ReportState.FAILED, FORMAT)] * 4
    assert rig.codes().count("A-17") == 1  # 되풀이로 둘째부터는 없다
    held = items[2]  # 회차 2: "보류"
    assert rig.rows("SELECT direction FROM reports WHERE id = ?", held.report_id) == [("보류",)]
    assert held.direction is None
    assert rig.report(held.report_id).direction is None
    probe = show_report(rig, AAA, held.report_id)
    try:
        assert probe.text("reportMessage") == (
            "이 회차에 이 코인의 보고서를 받지 못했습니다: 형식 오류"
        )
    finally:
        probe.close()


# --- C10 --------------------------------------------------------------------------------------


@pytest.mark.scenario("C10")
@pytest.mark.parametrize(
    ("direction", "predicted", "buy", "sell", "state"),
    [
        ("오를 것", "102", "", "101", ReportState.FAILED),
        ("내릴 것", "98", "97", "", ReportState.FAILED),
        ("오를 것", "102", "99", "", ReportState.OK),
    ],
    ids=["up-without-buy", "down-without-sell", "up-without-sell"],
)
def test_c10_the_needed_suggested_price_must_be_there(
    build: Callable[..., Rig],
    direction: str,
    predicted: str,
    buy: str,
    sell: str,
    state: ReportState,
) -> None:
    answer = final_judgment(direction=direction, predicted=predicted, buy=buy, sell=sell)
    rig = one_round(build, answers(final_judgment=answer))
    assert only(rig).state is state


# --- C11 --------------------------------------------------------------------------------------


@pytest.mark.scenario("C11")
@pytest.mark.parametrize(
    "field",
    ["search_judgment", "candle_judgment"],
)
def test_c11_a_neutral_judgment_is_a_format_error_and_not_a_stored_judgment(
    build: Callable[..., Rig], field: str
) -> None:
    if field == "search_judgment":
        wrong = write_csv(SEARCH_JUDGMENT_COLUMNS, [[AAA, "중립", "성공"]])
    else:
        wrong = write_csv(CANDLE_JUDGMENT_COLUMNS, [[AAA, "보합"]])
    runner = FakeAiRunner()
    runner.plan(AAA, answered(answers(**{field: wrong})))  # 최종 예측의 답은 담겨 있다
    rig = build(runner=runner)
    rig.start()
    rig.round()
    item = only(rig)
    assert (item.state, item.failure) == (ReportState.FAILED, FORMAT)
    # 틀린 판별의 값은 저장되어도(그 칸의 글) 저장된 판단이 아니다.
    assert rig.rows("SELECT search_judgment, candle_judgment FROM reports") == [
        ("중립", "오름") if field == "search_judgment" else ("긍정", "보합")
    ]
    view = rig.report(item.report_id)
    if field == "search_judgment":
        assert view.search_judgment is None
    else:
        assert view.candle_judgment is None
    assert view.direction is None
    # 담겨 온 최종 예측의 답은 DB 어디에도 없다(`read_report`가 `OK`가 아닌 보고서에 늘 `None`을
    # 주므로 화면 값이 아니라 DB를 직접 본다).
    assert rig.rows(
        "SELECT direction, predicted_price, predicted_reason, buy_price, sell_price, result_text"
        " FROM reports"
    ) == [(None, None, None, None, None, None)]
    assert "final" not in {part for (part,) in rig.rows("SELECT answer_part FROM report_rows")}
    runner.plan(AAA, None)
    rig.round()
    _, rows = read_csv(rig.runner.requests[1].files[ai_inputs.FILE_FINAL_LAST_JUDGMENT])
    assert list(rows) == []  # 다음 회차의 지난 판단이 아니다
    assert rig.reports(AAA)[0].state is ReportState.OK  # 견준 결과 없이도 OK다


# --- C12 --------------------------------------------------------------------------------------


@pytest.mark.scenario("C12")
@pytest.mark.parametrize("case", ["status", "no-source", "status-neutral-candle-header"])
def test_c12_a_failed_search_is_a_search_failure_before_other_checks(
    build: Callable[..., Rig], case: str
) -> None:
    if case == "status":
        changes = {"search_judgment": write_csv(SEARCH_JUDGMENT_COLUMNS, [[AAA, "긍정", "실패"]])}
    elif case == "no-source":
        changes = {"search_rows": rows_csv(row(AAA, "근거", 1, "검색 근거"))}
    else:
        changes = {
            "search_judgment": write_csv(SEARCH_JUDGMENT_COLUMNS, [[AAA, "중립", "실패"]]),
            "candle_judgment": write_csv(("market", "result"), [[AAA, "오름"]]),
            "candle_rows": write_csv(("market", "kind", "text"), [[AAA, "근거", "봉 근거"]]),
        }
    rig = one_round(build, answers(**changes))
    item = only(rig)
    assert (item.state, item.failure) == (ReportState.FAILED, "검색 실패")
    (alert,) = [a for a in rig.alerts() if a.code == "A-17"]
    assert alert.detail is not None
    assert alert.detail.error_name == "검색 실패"
    if case == "status-neutral-candle-header":
        parts = {p for (p,) in rig.rows("SELECT DISTINCT answer_part FROM report_rows")}
        assert "candle" not in parts  # 머리줄이 다른 봉 판별의 표는 저장하지 않는다
        assert "search" in parts
        assert rig.rows("SELECT candle_judgment FROM reports") == [(None,)]


# --- C13 --------------------------------------------------------------------------------------


@pytest.mark.scenario("C13")
@pytest.mark.parametrize(
    ("text", "state"),
    [
        (write_csv(("market", "judgment", "status"), [[AAA, "긍정", "성공"]]), ReportState.FAILED),
        (write_csv(("judgment", "market", "search_status"), [["긍정", AAA, "성공"]]),
         ReportState.FAILED),
        (chr(0xFEFF) + "```\n" + write_csv(SEARCH_JUDGMENT_COLUMNS, [[AAA, "긍정", "성공"]]),
         ReportState.OK),
    ],
    ids=["renamed", "reordered", "bom-and-fence"],
)  # fmt: skip
def test_c13_the_header_must_match_but_a_bom_and_a_fence_line_are_allowed(
    build: Callable[..., Rig], text: str, state: ReportState
) -> None:
    rig = one_round(build, answers(search_judgment=text))
    item = only(rig)
    assert item.state is state
    if state is ReportState.FAILED:
        assert item.failure == FORMAT


# --- C14 --------------------------------------------------------------------------------------


@pytest.mark.scenario("C14")
@pytest.mark.parametrize("case", ["judgment", "one-row"])
def test_c14_another_coin_code_in_any_table_is_a_format_error(
    build: Callable[..., Rig], case: str
) -> None:
    if case == "judgment":
        changes = {"final_judgment": final_judgment(market=BBB)}
    else:
        changes = {
            "candle_rows": rows_csv(
                row(AAA, "근거", 1, "봉 근거"), row(BBB, "근거", 2, "다른 코인")
            )
        }
    rig = one_round(build, answers(**changes))
    item = only(rig)
    assert (item.state, item.failure) == (ReportState.FAILED, FORMAT)


# --- C15 --------------------------------------------------------------------------------------


@pytest.mark.scenario("C15")
@pytest.mark.parametrize(
    ("result", "failure"),
    [(timeout(), "시간 초과"), (limit(), "한도 초과")],
    ids=["timeout", "limit"],
)
def test_c15_a_timeout_or_a_limit_is_a_failed_report_with_its_a17(
    build: Callable[..., Rig], result: Any, failure: str
) -> None:
    runner = FakeAiRunner()
    hold = threading.Event()
    started = runner.plan(AAA, result, wait=hold)
    rig = build(runner=runner)
    rig.start()
    rig.to_next_due()
    assert started.wait(SAFETY)
    checks = runner.check_calls
    hold.set()
    rig.drain()
    item = only(rig)
    assert (item.state, item.failure) == (ReportState.FAILED, failure)
    (alert,) = [a for a in rig.alerts() if a.code == "A-17"]
    assert alert.text == f"AI 보고서를 받지 못해 이번 회차에 주문하지 않았습니다({failure})."
    # 판단의 칸이 모두 비어 있고 줄 표도 없다(`read_report`가 아니라 DB를 직접 본다).
    assert rig.rows(
        "SELECT search_judgment, search_status, candle_judgment, direction, predicted_price,"
        " predicted_reason, buy_price, sell_price, result_text FROM reports"
    ) == [(None,) * 9]
    assert rig.rows("SELECT COUNT(*) FROM report_rows") == [(0,)]
    if failure == "한도 초과":
        assert alert.detail is not None
        assert "앱의 문제가 아닙니다." in (alert.detail.content or "")
        assert runner.check_calls == checks  # check()를 더 부르지 않는다
