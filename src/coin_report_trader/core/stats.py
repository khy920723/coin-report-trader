"""봉 통계의 값(값의 모양과 글만. 셈은 `stats/`가 한다).

비율은 0.0001 단위로 맞춘 `Decimal`(예: 0.15351…은 0.1535)이고, 값이 없으면 `None`이다.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from types import MappingProxyType
from typing import Final

from coin_report_trader.core.market import CandleUnit

INSUFFICIENT_TEXT: Final = "봉이 모자람"
"""값이 `None`일 때 보이는 글. M2는 이 글을 AI 입력에 그대로 쓴다."""

_PERCENT_STEP = Decimal("0.01")


def format_percent(value: Decimal | None, *, signed: bool) -> str:
    """비율을 소수 둘째 자리까지의 백분율 글로 쓴다(예: `+12.34%`, `-10.00%`, `15.35%`).

    `signed`이면 양수에 "+"를 붙인다. 음수는 ASCII "-"이고 0은 부호 없이 `0.00%`다.
    값이 없으면 `INSUFFICIENT_TEXT`다.
    """
    if value is None or not value.is_finite():
        return INSUFFICIENT_TEXT
    try:
        with localcontext() as context:
            context.prec = 60
            percent = (value * 100).quantize(_PERCENT_STEP, rounding=ROUND_HALF_EVEN)
    except ArithmeticError:
        return INSUFFICIENT_TEXT
    if percent == 0:
        return "0.00%"
    sign = "+" if signed and percent > 0 else ""
    return f"{sign}{percent:f}%"


def format_price(value: Decimal) -> str:
    """가격 글(`원`은 붙이지 않는다). 받은 값의 자리를 그대로 쓰되 끝의 0과 빈 소수점은 지운다.

    지수 꼴 없이 숫자로만 쓰고 정수 부분에만 세 자리마다 쉼표를 붙인다. 반올림하지 않는다.
    예: `132`, `1,234,567.5`, `0.00001`. 유한하지 않은 값이면 `ValueError`다.
    """
    if not value.is_finite():
        raise ValueError("유한하지 않은 값은 가격 글로 바꿀 수 없다")
    text = format(value, "f")
    sign = "-" if text.startswith("-") else ""
    integer, _, fraction = text.lstrip("-").partition(".")
    fraction = fraction.rstrip("0")
    if not integer.strip("0") and not fraction:
        sign = ""
    grouped = f"{int(integer):,}"
    return f"{sign}{grouped}.{fraction}" if fraction else f"{sign}{grouped}"


@dataclass(frozen=True)
class BarStats:
    """봉 통계(M1 명세 3.5.1). 고른 봉 단위의 완성된 봉 가운데 마지막 것부터 고른 개수만큼으로
    셈한 값이다. 열은 `BAR_STATS_COLUMNS`와 같은 이름, 같은 차례다. 완성된 봉이 고른 개수보다
    적으면 `bar_count`는 있는 수이고 `first_start_utc`부터 `low`까지는 모두 `None`이다. 비율은
    0.0001 단위로 맞춘 `Decimal`이고 가격은 받은 값 그대로다."""

    market: str
    unit: CandleUnit
    requested_count: int
    bar_count: int
    first_start_utc: datetime | None = None
    last_start_utc: datetime | None = None
    close_last: Decimal | None = None
    change: Decimal | None = None
    slope: Decimal | None = None
    vs_mean: Decimal | None = None
    up_ratio: Decimal | None = None
    volatility: Decimal | None = None
    max_drawdown: Decimal | None = None
    from_high: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None


BAR_STATS_COLUMNS: Final[tuple[str, ...]] = (
    "market",
    "unit",
    "requested_count",
    "bar_count",
    "first_start_utc",
    "last_start_utc",
    "close_last",
    "change",
    "slope",
    "vs_mean",
    "up_ratio",
    "volatility",
    "max_drawdown",
    "from_high",
    "high",
    "low",
)
"""영어 열 이름(3.5.1 표의 차례). 2단계에서 CSV의 머리줄 그대로다."""

BAR_STATS_TITLES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "market": "코인",
        "unit": "봉 단위",
        "requested_count": "고른 봉 개수",
        "bar_count": "쓴 봉 수",
        "first_start_utc": "첫 봉 시작",
        "last_start_utc": "마지막 봉 시작",
        "close_last": "마지막 종가",
        "change": "전체 변화",
        "slope": "추세 기울기",
        "vs_mean": "평균 대비",
        "up_ratio": "오른 봉 비율",
        "volatility": "변동성",
        "max_drawdown": "최대 낙폭",
        "from_high": "최고가 대비",
        "high": "최고가",
        "low": "최저가",
    }
)
"""열 이름에서 한국어 이름으로."""


def empty_bar_stats(market: str, unit: CandleUnit, count: int) -> BarStats:
    """봉이 하나도 없을 때의 값: `bar_count`가 0이고 셀 수 없는 열은 모두 `None`이다."""
    return BarStats(market=market, unit=unit, requested_count=count, bar_count=0)
