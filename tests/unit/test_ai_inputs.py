"""`ai/inputs.py`(M2 명세 3.8.2~3.8.4): 입력 파일의 글. 값은 모두 만든 값이다."""

from datetime import UTC, datetime
from decimal import Decimal

from coin_report_trader.ai import inputs
from coin_report_trader.ai.csv_files import ROWS_COLUMNS, Row, read_csv
from coin_report_trader.core import (
    BAR_STATS_COLUMNS,
    AiSlot,
    BarStats,
    CandleUnit,
    Direction,
    RowKind,
    Verdict,
    empty_bar_stats,
)

AAA = "KRW-AAA"
T = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
NEXT = datetime(2026, 1, 5, 11, 0, tzinfo=UTC)
D = Decimal
BOM = chr(0xFEFF)


def table(text: str) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    assert not text.startswith(BOM)  # 파일에 쓸 때 BOM을 붙이는 것은 실행기다
    assert text.endswith("\r\n")
    return read_csv(text)


def test_the_file_names() -> None:
    assert (
        inputs.FILE_SEARCH_INPUT,
        inputs.FILE_CANDLE_STATS,
        inputs.FILE_FINAL_NOW,
        inputs.FILE_FINAL_LAST_JUDGMENT,
        inputs.FILE_FINAL_LAST_REPORT,
        inputs.FILE_FINAL_TRADES,
    ) == (
        "search_input.csv",
        "candle_stats.csv",
        "final_now.csv",
        "final_last_judgment.csv",
        "final_last_report.csv",
        "final_trades.csv",
    )


def test_the_search_input_has_one_row_with_the_symbol_and_the_times() -> None:
    header, rows = table(
        inputs.search_input(market=AAA, unit=CandleUnit.MINUTE_60, round_close=T, next_round=NEXT)
    )
    assert header == ("market", "symbol", "unit", "round_close_utc", "next_round_utc")
    assert rows == ((AAA, "AAA", "minute60", "2026-01-05T10:00:00Z", "2026-01-05T11:00:00Z"),)


def full_stats() -> BarStats:
    return BarStats(
        market=AAA,
        unit=CandleUnit.MINUTE_60,
        requested_count=100,
        bar_count=100,
        first_start_utc=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        last_start_utc=datetime(2026, 1, 5, 9, 0, tzinfo=UTC),
        close_last=D("132"),
        change=D("0.32"),
        slope=D("-0.1"),
        vs_mean=D("0.0098"),
        up_ratio=D("0.6"),
        volatility=D("0.02"),
        max_drawdown=D("-0.25"),
        from_high=D("0"),
        high=D("1234567.50"),
        low=D("0.00001"),
    )


def test_the_candle_stats_file_has_the_bar_stats_columns_and_the_csv_text_of_the_values() -> None:
    header, rows = table(inputs.candle_stats(full_stats()))
    assert header == tuple(BAR_STATS_COLUMNS)
    (row,) = rows
    assert dict(zip(header, row, strict=True)) == {
        "market": AAA,
        "unit": "minute60",
        "requested_count": "100",
        "bar_count": "100",
        "first_start_utc": "2026-01-01T00:00:00Z",
        "last_start_utc": "2026-01-05T09:00:00Z",
        "close_last": "132",
        "change": "0.3200",
        "slope": "-0.1000",
        "vs_mean": "0.0098",
        "up_ratio": "0.6000",
        "volatility": "0.0200",
        "max_drawdown": "-0.2500",
        "from_high": "0.0000",
        "high": "1234567.50",
        "low": "0.00001",
    }


def test_missing_values_are_empty_and_the_unit_is_still_written() -> None:
    header, rows = table(inputs.candle_stats(empty_bar_stats(AAA, CandleUnit.DAY, 200)))
    cells = dict(zip(header, rows[0], strict=True))
    assert (cells["unit"], cells["requested_count"], cells["bar_count"]) == ("day", "200", "0")
    assert all(cells[c] == "" for c in header[4:])


def test_no_file_carries_a_raw_candle_column() -> None:
    files = [
        inputs.search_input(market=AAA, unit=CandleUnit.DAY, round_close=T, next_round=NEXT),
        inputs.candle_stats(full_stats()),
        inputs.final_now(
            market=AAA,
            current_price=D("100"),
            price_read_at=T,
            unit=CandleUnit.DAY,
            round_close=T,
            next_round=NEXT,
        ),
        inputs.final_last_judgment(None),
        inputs.final_last_report([]),
        inputs.final_trades(),
    ]
    for text in files:
        header = set(table(text)[0])
        assert not header & {"open", "high_price", "low_price", "close", "volume", "start_utc"}
    assert "close_last" in table(files[1])[0]  # 봉 통계의 열은 `candle_stats.csv`에만 있다
    for text in files[:1] + files[2:]:
        assert not set(table(text)[0]) & set(BAR_STATS_COLUMNS[4:])


def test_the_final_now_has_the_price_read_before_the_session() -> None:
    header, rows = table(
        inputs.final_now(
            market=AAA,
            current_price=D("100.50"),
            price_read_at=datetime(2026, 1, 5, 10, 0, 7, tzinfo=UTC),
            unit=CandleUnit.MINUTE_240,
            round_close=T,
            next_round=NEXT,
        )
    )
    assert header == (
        "market", "current_price", "price_read_utc", "unit", "round_close_utc", "next_round_utc",
    )  # fmt: skip
    assert rows == (
        (AAA, "100.50", "2026-01-05T10:00:07Z", "minute240", "2026-01-05T10:00:00Z",
         "2026-01-05T11:00:00Z"),
    )  # fmt: skip


def test_the_last_judgment_is_header_only_for_a_first_round() -> None:
    text = inputs.final_last_judgment(None)
    assert text == (
        "round_number,round_close_utc,base_price,direction,predicted_price,predicted_reason,"
        "result_text,target_close_utc,actual_price,diff,hit\r\n"
    )


def test_the_last_judgment_row_has_the_values_and_the_result_cells() -> None:
    last = inputs.LastJudgmentInput(
        round_number=1,
        round_close=T,
        base_price=D("100"),
        direction=Direction.UP,
        predicted_price=D("102"),
        predicted_reason="이유",
        result_text="결과 글",
        target_close=NEXT,
        actual_price=D("101"),
        diff=D("-0.0098"),
        hit=Verdict.HIT,
    )
    header, (row,) = table(inputs.final_last_judgment(last))
    assert dict(zip(header, row, strict=True)) == {
        "round_number": "1",
        "round_close_utc": "2026-01-05T10:00:00Z",
        "base_price": "100",
        "direction": "오를 것",
        "predicted_price": "102",
        "predicted_reason": "이유",
        "result_text": "결과 글",
        "target_close_utc": "2026-01-05T11:00:00Z",
        "actual_price": "101",
        "diff": "-0.0098",
        "hit": "맞음",
    }


def test_the_unknown_actual_price_leaves_actual_diff_and_hit_empty() -> None:
    last = inputs.LastJudgmentInput(
        3, T, D("100"), Direction.DOWN, D("98"), "이유", "결과", NEXT, None, None, None
    )
    header, (row,) = table(inputs.final_last_judgment(last))
    cells = dict(zip(header, row, strict=True))
    assert (cells["actual_price"], cells["diff"], cells["hit"]) == ("", "", "")
    assert cells["direction"] == "내릴 것"


def test_the_last_report_has_the_part_name_before_the_rows_columns() -> None:
    rows = [
        (AiSlot.SEARCH, Row(AAA, RowKind.EVIDENCE, 1, "검색 근거")),
        (AiSlot.SEARCH, Row(AAA, RowKind.SOURCE, 1, "제목", url="https://example.invalid/a")),
        (AiSlot.CANDLE, Row(AAA, RowKind.EVIDENCE, 1, "봉 근거")),
        (AiSlot.FINAL, Row(AAA, RowKind.CHECKLIST, 1, "항목", belief="높음", compared=2, hits=1)),
    ]
    header, body = table(inputs.final_last_report(rows))
    assert header == ("answer_part", *ROWS_COLUMNS)
    assert [row[0] for row in body] == ["검색 판별", "검색 판별", "봉 판별", "최종 예측"]
    assert body[1][1:3] == (AAA, "출처")
    assert body[3][-3:] == ("높음", "2", "1")


def test_the_last_report_is_header_only_without_a_last_judgment_and_trades_too() -> None:
    assert table(inputs.final_last_report([])) == (("answer_part", *ROWS_COLUMNS), ())
    assert inputs.final_trades() == "market,kind,price,utc\r\n"  # 2단계는 머리줄뿐이다
