"""`ai/csv_files.py`(M2 명세 3.8.1): 쓰기와 읽기, 값의 글. 값은 모두 만든 값이다."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from coin_report_trader.ai import csv_files
from coin_report_trader.ai.csv_files import (
    ROWS_COLUMNS,
    CsvError,
    Row,
    clean_answer_text,
    decimal_text,
    ratio_text,
    read_csv,
    utc_text,
    write_csv,
)
from coin_report_trader.core import RowKind

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
BOM = chr(0xFEFF)


def test_the_rows_columns_are_the_eleven_of_the_spec() -> None:
    assert ROWS_COLUMNS == (
        "market", "kind", "seq", "part", "ref_seq", "text", "url", "verdict", "belief",
        "compared", "hits",
    )  # fmt: skip


def test_the_written_text_has_no_bom_crlf_lines_and_a_header_first() -> None:
    text = write_csv(("a", "b"), [("1", "2"), ("3", "4")])
    assert text == "a,b\r\n1,2\r\n3,4\r\n"
    assert not text.startswith(BOM)
    assert write_csv(("a", "b"), []) == "a,b\r\n"  # 머리줄뿐인 파일


def test_quotes_only_when_needed_and_a_roundtrip_keeps_the_cells() -> None:
    cells = ("한글, 쉼표", '큰따옴표 "안"', "그냥")
    text = write_csv(("x", "y", "z"), [cells])
    assert text == 'x,y,z\r\n"한글, 쉼표","큰따옴표 ""안""",그냥\r\n'
    header, rows = read_csv(text)
    assert header == ("x", "y", "z")
    assert rows == (cells,)


def test_the_row_cells_follow_the_rows_columns_and_blank_values_are_empty() -> None:
    evidence = Row("KRW-AAA", RowKind.EVIDENCE, 1, "근거 글")
    assert evidence.cells() == ["KRW-AAA", "근거", "1", "", "", "근거 글", "", "", "", "", ""]
    checklist = Row("KRW-AAA", RowKind.CHECKLIST, 2, "항목", belief="높음", compared=4, hits=0)
    assert checklist.cells() == [
        "KRW-AAA",
        "검증 목록",
        "2",
        "",
        "",
        "항목",
        "",
        "",
        "높음",
        "4",
        "0",
    ]
    compared = Row(
        "KRW-AAA", RowKind.COMPARED, 1, "앞 근거", part="봉 판별", ref_seq=3, verdict="맞음"
    )
    assert compared.cells()[3:5] == ["봉 판별", "3"]
    assert len(compared.cells()) == len(ROWS_COLUMNS)


def test_time_text_is_utc_to_the_second_with_z() -> None:
    assert utc_text(NOW) == "2026-01-02T03:04:05Z"
    kst = timezone(timedelta(hours=9))
    assert utc_text(datetime(2026, 1, 2, 12, 4, 5, 999999, tzinfo=kst)) == "2026-01-02T03:04:05Z"
    with pytest.raises(ValueError, match="시간대"):
        utc_text(datetime(2026, 1, 2))


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (Decimal("132"), "132"),
        (Decimal("1E+3"), "1000"),
        (Decimal("0.00001"), "0.00001"),
        (Decimal("1234567.50"), "1234567.50"),
        (Decimal("0E-8"), "0.00000000"),
    ],
)
def test_a_price_is_written_without_an_exponent_or_commas(value: Decimal, text: str) -> None:
    assert decimal_text(value) == text


def test_a_price_that_is_not_finite_is_refused() -> None:
    for bad in (Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(ValueError, match="유한하지"):
            decimal_text(bad)
        with pytest.raises(ValueError, match="유한하지"):
            ratio_text(bad)


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (Decimal("0.32"), "0.3200"),
        (Decimal("-0.1"), "-0.1000"),
        (Decimal("-0.0098"), "-0.0098"),
        (Decimal("0.00125"), "0.0012"),  # 짝수로 맞춘다(ROUND_HALF_EVEN)
        (Decimal("0.00135"), "0.0014"),
        (Decimal("0"), "0.0000"),
        (Decimal("-0.00001"), "0.0000"),  # 반올림해 0이 되면 부호가 없다
        (Decimal("12"), "12.0000"),
    ],
)
def test_a_ratio_is_written_to_four_decimals(value: Decimal, text: str) -> None:
    assert ratio_text(value) == text


def test_reading_removes_a_bom_blank_edge_lines_and_fences() -> None:
    plain = "a,b\r\n1,2\r\n"
    for given in (
        BOM + plain,
        "\r\n\r\n" + plain + "\r\n\r\n",
        "```csv\r\n" + plain + "```\r\n",
        BOM + "\r\n```\r\n" + plain + "\r\n```\r\n\r\n",
        "```\na,b\n1,2\n```",
    ):
        assert read_csv(given) == (("a", "b"), (("1", "2"),)), given


def test_only_the_first_and_last_fence_lines_are_removed_and_nothing_else_is_fixed() -> None:
    assert clean_answer_text("a,b\n```\n1,2") == ["a,b", "```", "1,2"]  # 가운데는 건드리지 않는다
    assert clean_answer_text("   \n\na\n") == ["a"]
    assert clean_answer_text("```") == []  # 한 줄뿐이면 한 번만 뗀다
    assert clean_answer_text("") == []
    assert clean_answer_text("앞 글\n```") == ["앞 글"]


def test_cells_are_trimmed_and_the_header_is_not_fixed() -> None:
    header, rows = read_csv("  a , b \r\n  1 ,2  \r\n")
    assert header == ("a", "b")
    assert rows == (("1", "2"),)
    assert read_csv("") == ((), ())
    assert read_csv("\r\n \r\n") == ((), ())


def test_rows_of_another_width_are_kept_as_they_are_for_the_caller_to_judge() -> None:
    header, rows = read_csv("a,b\r\n1\r\n1,2,3\r\n")
    assert header == ("a", "b")
    assert rows == (("1",), ("1", "2", "3"))


def test_a_quoted_newline_stays_in_the_cell() -> None:
    _, rows = read_csv('a,b\r\n"줄\r\n바꿈",2\r\n')
    assert rows == (("줄\n바꿈", "2"),)  # 줄 끝은 `\n`으로 모인다


def test_text_that_cannot_be_read_is_a_csv_error() -> None:
    with pytest.raises(CsvError, match="CSV"):
        read_csv('a,b\r\n"열고 닫지 않음,2\r\n')
    with pytest.raises(CsvError, match="CSV"):
        read_csv('a,b\r\n1,"2"x\r\n')  # strict: 닫은 따옴표 뒤의 글


def test_the_module_does_not_import_outside_core_and_the_standard_library() -> None:
    import ast
    import sys
    from pathlib import Path

    source = Path(csv_files.__file__).read_text(encoding="utf-8")
    roots = {
        node.module.split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert roots <= {"coin_report_trader", *sys.stdlib_module_names}
