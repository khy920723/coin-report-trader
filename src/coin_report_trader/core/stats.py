"""기간별 봉 통계와 지표의 봉 계산 값(값의 모양과 글만. 셈은 `stats/`가 한다).

비율은 0.0001 단위로 맞춘 `Decimal`(예: 0.15351…은 0.1535)이고, 값이 없으면 `None`이다.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Final

from coin_report_trader.core.market import CandleUnit

INSUFFICIENT_TEXT: Final = "봉이 모자람"
"""값이 `None`일 때 보이는 글. M2는 이 글을 AI 입력에 그대로 쓴다."""

PERIODS: Final[tuple[tuple[str, CandleUnit], ...]] = (
    ("전체", CandleUnit.DAY),
    ("1년", CandleUnit.DAY),
    ("3개월", CandleUnit.DAY),
    ("1개월", CandleUnit.DAY),
    ("1주", CandleUnit.MINUTE_60),
)
"""기간 이름과 봉 단위. 3.5절 표의 차례다."""

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


@dataclass(frozen=True)
class PeriodStats:
    """한 기간의 봉 통계. 봉이 2개보다 적으면 `count`만 있고 나머지 값은 모두 `None`이다."""

    period: str
    unit: CandleUnit
    count: int
    first_start: datetime | None = None
    last_start: datetime | None = None
    open_price: Decimal | None = None
    close_price: Decimal | None = None
    change: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    from_high: Decimal | None = None
    max_drawdown: Decimal | None = None
    volatility: Decimal | None = None
    up_ratio: Decimal | None = None
    avg_value: Decimal | None = None


def empty_periods() -> tuple[PeriodStats, ...]:
    """봉이 하나도 없을 때의 다섯 기간(값은 모두 `None`)."""
    return tuple(PeriodStats(period=name, unit=unit, count=0) for name, unit in PERIODS)


# (필드 이름, 화면 이름, 부호를 붙이는지)
_INDICATOR_ROWS: Final[tuple[tuple[str, str, bool], ...]] = (
    ("trend_1y", "1년 변화", True),
    ("trend_3m", "3개월 변화", True),
    ("vs_ma20", "20일 평균 대비", True),
    ("vs_ma120", "120일 평균 대비", True),
    ("volatility_1y", "1년 일간 변동성", False),
    ("volatility_1w", "1주 시간 변동성", False),
    ("max_drawdown_1y", "1년 최대 낙폭", True),
)


@dataclass(frozen=True)
class CandleIndicator:
    """지표의 봉 계산 값. M2가 AI 입력에 그대로 쓴다. 어느 쪽이 이득인지 판정하지 않는다."""

    trend_1y: Decimal | None = None
    trend_3m: Decimal | None = None
    vs_ma20: Decimal | None = None
    vs_ma120: Decimal | None = None
    volatility_1y: Decimal | None = None
    volatility_1w: Decimal | None = None
    max_drawdown_1y: Decimal | None = None

    def items(self) -> tuple[tuple[str, str], ...]:
        """(화면 이름, 보이는 값) 쌍을 3.6절 표의 차례로 돌려준다."""
        return tuple(
            (title, format_percent(getattr(self, field), signed=signed))
            for field, title, signed in _INDICATOR_ROWS
        )
