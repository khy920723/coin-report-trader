"""AI와 주고받는 CSV의 쓰기와 읽기(M2 명세 3.8.1). 계산만 한다.

- 쓰기: 쉼표로 나누고 줄 끝은 CRLF이며 첫 줄은 머리줄이다. 표준 라이브러리 `csv`의 기본 꼴(필요할
  때만 큰따옴표)이다. 돌려주는 글에는 BOM이 없다(`SessionRequest.files`는 BOM 없는 CSV 글이고,
  파일로 쓸 때 UTF-8 BOM을 붙이는 것은 실행기다).
- 값의 글: 시각은 M1 명세 3.7의 시각 글(`2026-01-02T00:00:00Z` 꼴), 가격은 지수 꼴 없는 `Decimal`의
  글(쉼표 없음), 비율은 소수 넷째 자리까지, 정수는 10진수, 빈 값은 빈 글이다.
- 읽기(답의 글): 앞의 BOM과 앞뒤의 빈 줄을 뗀다. 첫 줄과 마지막 줄이 ```` ``` ````로 시작하면
  그 줄을 뗀다. 그 밖에는 고치지 않는다. 칸마다 앞뒤 빈칸을 지운다.
"""

import csv
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Final

from coin_report_trader.core import RowKind

ROWS_COLUMNS: Final = (
    "market",
    "kind",
    "seq",
    "part",
    "ref_seq",
    "text",
    "url",
    "verdict",
    "belief",
    "compared",
    "hits",
)
"""줄 표의 열(3.8.1). 세 부분이 같은 머리줄을 쓴다."""

_FENCE: Final = "```"
_BOM: Final = chr(0xFEFF)
_RATIO_STEP: Final = Decimal("0.0001")


class CsvError(ValueError):
    """CSV 글을 읽지 못했다(따옴표가 맞지 않는 것 등)."""


@dataclass(frozen=True)
class Row:
    """줄 표의 한 줄. 칸이 그 줄의 `kind`에 따라 차거나 빈다(3.8.1)."""

    market: str
    kind: RowKind | str
    seq: int
    text: str
    part: str | None = None
    ref_seq: int | None = None
    url: str | None = None
    verdict: str | None = None
    belief: str | None = None
    compared: int | None = None
    hits: int | None = None

    def cells(self) -> list[str]:
        """`ROWS_COLUMNS`의 차례로 칸의 글을 돌려준다(빈 값은 빈 글)."""
        return [
            self.market,
            str(self.kind),
            str(self.seq),
            self.part or "",
            "" if self.ref_seq is None else str(self.ref_seq),
            self.text,
            self.url or "",
            self.verdict or "",
            self.belief or "",
            "" if self.compared is None else str(self.compared),
            "" if self.hits is None else str(self.hits),
        ]


def utc_text(moment: datetime) -> str:
    """시각 글(UTC, 초까지, 끝에 `Z`). 시간대가 없는 시각은 `ValueError`다."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("시간대가 없는 시각은 CSV에 쓰지 않는다")
    utc = moment.astimezone(UTC)
    return f"{utc:%Y-%m-%dT%H:%M:%S}Z"


def decimal_text(value: Decimal) -> str:
    """가격의 글: 받은 `Decimal`을 지수 꼴 없이(쉼표 없음) 쓴다."""
    if not value.is_finite():
        raise ValueError("유한하지 않은 수는 CSV에 쓰지 않는다")
    return format(value, "f")


def ratio_text(value: Decimal) -> str:
    """비율의 글: 소수 넷째 자리까지(`ROUND_HALF_EVEN`). 0은 부호 없이 `0.0000`이다."""
    if not value.is_finite():
        raise ValueError("유한하지 않은 수는 CSV에 쓰지 않는다")
    with localcontext() as context:
        context.prec = 60
        rounded = value.quantize(_RATIO_STEP, rounding=ROUND_HALF_EVEN)
    text = format(rounded, "f")
    return text[1:] if text.startswith("-") and not rounded else text


def write_csv(columns: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    """머리줄과 줄들을 CSV 글로 쓴다(BOM 없음, 줄마다 CRLF로 끝난다)."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def clean_answer_text(text: str) -> list[str]:
    """답의 글을 줄로 나눠 앞의 BOM, 앞뒤의 빈 줄, 앞뒤의 ```` ``` ```` 줄을 뗀다."""
    lines = text.removeprefix(_BOM).splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[0].startswith(_FENCE):
        lines.pop(0)
    if lines and lines[-1].startswith(_FENCE):
        lines.pop()
    return lines


def read_csv(text: str) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    """답의 글을 (머리줄, 줄들)로 읽는다. 칸마다 앞뒤 빈칸을 지운다. 글이 비면 머리줄도 없다.

    읽지 못하면 `CsvError`다. 줄의 칸 수는 맞추지 않는다(부르는 쪽이 머리줄과 견준다).
    """
    lines = clean_answer_text(text)
    if not lines:
        return (), ()
    try:
        parsed = [
            tuple(cell.strip() for cell in row)
            for row in csv.reader(io.StringIO("\n".join(lines), newline=""), strict=True)
        ]
    except csv.Error as exc:
        raise CsvError("CSV 글을 읽지 못했다") from exc
    return parsed[0], tuple(parsed[1:])
