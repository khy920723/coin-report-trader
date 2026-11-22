"""AI에 주는 입력 파일의 글(M2 명세 3.8.2~3.8.4). 계산만 한다.

파일 이름은 `FILE_*`이고, 글은 `csv_files.write_csv`의 꼴(BOM 없음, CRLF)이다. 봉 통계는 봉 판별에만
주고(REQ-AI-007) 원본 봉은 어느 파일에도 주지 않는다. 웹후크 주소, 보관소의 값, 자료 폴더의 경로는
이 글에 들어갈 길이 없다(받는 값이 마켓 코드, 시각, 가격, 판단의 글뿐이다).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final

from coin_report_trader.ai.csv_files import (
    ROWS_COLUMNS,
    Row,
    decimal_text,
    ratio_text,
    utc_text,
    write_csv,
)
from coin_report_trader.core import (
    AI_SLOT_TITLES,
    BAR_STATS_COLUMNS,
    AiSlot,
    BarStats,
    CandleUnit,
    Direction,
    Verdict,
    coin_label,
)

FILE_SEARCH_INPUT: Final = "search_input.csv"
FILE_CANDLE_STATS: Final = "candle_stats.csv"
FILE_FINAL_NOW: Final = "final_now.csv"
FILE_FINAL_LAST_JUDGMENT: Final = "final_last_judgment.csv"
FILE_FINAL_LAST_REPORT: Final = "final_last_report.csv"
FILE_FINAL_TRADES: Final = "final_trades.csv"

SEARCH_INPUT_COLUMNS: Final = ("market", "symbol", "unit", "round_close_utc", "next_round_utc")
FINAL_NOW_COLUMNS: Final = (
    "market",
    "current_price",
    "price_read_utc",
    "unit",
    "round_close_utc",
    "next_round_utc",
)
FINAL_LAST_JUDGMENT_COLUMNS: Final = (
    "round_number",
    "round_close_utc",
    "base_price",
    "direction",
    "predicted_price",
    "predicted_reason",
    "result_text",
    "target_close_utc",
    "actual_price",
    "diff",
    "hit",
)
FINAL_LAST_REPORT_COLUMNS: Final = ("answer_part", *ROWS_COLUMNS)
FINAL_TRADES_COLUMNS: Final = ("market", "kind", "price", "utc")

_RATIO_COLUMNS: Final = frozenset(
    {"change", "slope", "vs_mean", "up_ratio", "volatility", "max_drawdown", "from_high"}
)
_TIME_COLUMNS: Final = frozenset({"first_start_utc", "last_start_utc"})


@dataclass(frozen=True)
class LastJudgmentInput:
    """`final_last_judgment.csv`의 한 줄(지난 판단과 그 결과. 3.8.4, 3.9)."""

    round_number: int
    round_close: datetime
    base_price: Decimal
    direction: Direction
    predicted_price: Decimal
    predicted_reason: str
    result_text: str
    target_close: datetime
    actual_price: Decimal | None
    diff: Decimal | None
    hit: Verdict | None
    """`맞음`, `틀림`. 셀 수 없으면 `None`(빈 칸)."""


def search_input(
    *, market: str, unit: CandleUnit, round_close: datetime, next_round: datetime
) -> str:
    """`search_input.csv`(한 줄). 웹 검색은 이 부분만 한다(REQ-AI-025)."""
    row = [market, coin_label(market), CandleUnit(unit).value, utc_text(round_close)]
    row.append(utc_text(next_round))
    return write_csv(SEARCH_INPUT_COLUMNS, [row])


def candle_stats(stats: BarStats) -> str:
    """`candle_stats.csv`(한 줄). 머리줄은 `BAR_STATS_COLUMNS` 그대로이고 값의 글은 M1 명세 3.5.1의
    "CSV의 글"이다: 비율은 소수 넷째 자리까지, 가격은 받은 `Decimal`의 글, 시각은 시각 글, 빈 칸은
    빈 글."""
    cells: list[str] = []
    for column in BAR_STATS_COLUMNS:
        value = getattr(stats, column)
        if value is None:
            cells.append("")
        elif column == "unit":
            cells.append(CandleUnit(value).value)
        elif column in _TIME_COLUMNS:
            cells.append(utc_text(value))
        elif column in _RATIO_COLUMNS:
            cells.append(ratio_text(value))
        elif isinstance(value, Decimal):
            cells.append(decimal_text(value))
        else:
            cells.append(str(value))
    return write_csv(BAR_STATS_COLUMNS, [cells])


def final_now(
    *,
    market: str,
    current_price: Decimal,
    price_read_at: datetime,
    unit: CandleUnit,
    round_close: datetime,
    next_round: datetime,
) -> str:
    """`final_now.csv`(한 줄). 봉 통계는 주지 않는다(REQ-AI-008)."""
    row = [
        market,
        decimal_text(current_price),
        utc_text(price_read_at),
        CandleUnit(unit).value,
        utc_text(round_close),
        utc_text(next_round),
    ]
    return write_csv(FINAL_NOW_COLUMNS, [row])


def final_last_judgment(last: LastJudgmentInput | None) -> str:
    """`final_last_judgment.csv`: 지난 판단이 있으면 한 줄, 첫 회차에는 머리줄뿐이다(8절)."""
    if last is None:
        return write_csv(FINAL_LAST_JUDGMENT_COLUMNS, [])
    row = [
        str(last.round_number),
        utc_text(last.round_close),
        decimal_text(last.base_price),
        Direction(last.direction).value,
        decimal_text(last.predicted_price),
        last.predicted_reason,
        last.result_text,
        utc_text(last.target_close),
        "" if last.actual_price is None else decimal_text(last.actual_price),
        "" if last.diff is None else ratio_text(last.diff),
        "" if last.hit is None else Verdict(last.hit).value,
    ]
    return write_csv(FINAL_LAST_JUDGMENT_COLUMNS, [row])


def final_last_report(rows: Sequence[tuple[AiSlot, Row]]) -> str:
    """`final_last_report.csv`: `answer_part`(`검색 판별`, `봉 판별`, `최종 예측`. 그 줄을 낸 부분)
    뒤에 `ROWS_COLUMNS`가 이어진다. 지난 판단 회차의 세 부분의 줄 표 전부다. 지난 판단이 없으면
    머리줄뿐이다."""
    body = [[AI_SLOT_TITLES[AiSlot(part)], *row.cells()] for part, row in rows]
    return write_csv(FINAL_LAST_REPORT_COLUMNS, body)


def final_trades() -> str:
    """`final_trades.csv`: 2단계는 머리줄뿐이다(2의 1)."""
    return write_csv(FINAL_TRADES_COLUMNS, [])
