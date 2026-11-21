"""봉과 마켓. 가격과 수량은 `Decimal`, 시각은 UTC가 붙은 `datetime`이다."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

MARKET_PREFIX = "KRW-"


class CandleUnit(StrEnum):
    """봉 단위. 정의 순서가 화면의 차례다(M1 명세 3.4). 1단계 보완 전에는 `DAY`와 `MINUTE_60`
    둘이었고 두 값은 그대로라 예전 DB의 봉도 읽힌다."""

    MINUTE_30 = "minute30"
    MINUTE_60 = "minute60"
    MINUTE_240 = "minute240"
    DAY = "day"


CANDLE_UNIT_TITLES: Mapping[CandleUnit, str] = MappingProxyType(
    {
        CandleUnit.MINUTE_30: "30분봉",
        CandleUnit.MINUTE_60: "60분봉",
        CandleUnit.MINUTE_240: "240분봉",
        CandleUnit.DAY: "일봉",
    }
)
"""화면에 보이는 봉 단위 이름."""

CANDLE_UNIT_LENGTHS: Mapping[CandleUnit, timedelta] = MappingProxyType(
    {
        CandleUnit.MINUTE_30: timedelta(minutes=30),
        CandleUnit.MINUTE_60: timedelta(minutes=60),
        CandleUnit.MINUTE_240: timedelta(minutes=240),
        CandleUnit.DAY: timedelta(days=1),
    }
)
"""봉 하나의 길이. 시작 시각에 더한 때가 지금보다 뒤이면 미완성이다."""

CANDLE_COUNTS: tuple[int, ...] = (100, 200, 500)
"""사용자가 고르는 봉 개수."""
DEFAULT_CANDLE_COUNT = 200


@dataclass(frozen=True)
class Candle:
    market: str
    unit: CandleUnit
    start: datetime
    """봉의 시작 시각."""
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    value: Decimal
    """거래대금."""


@dataclass(frozen=True)
class Market:
    market: str
    """마켓 코드. 예: `KRW-AAA` 꼴."""
    name: str
    """한글 이름."""
    caution: bool
    """유의·주의 표시."""


def coin_label(market: str) -> str:
    """화면과 디스코드에 보이는 코인 글: 마켓 코드에서 앞의 `KRW-`를 뗀 기호."""
    return market.removeprefix(MARKET_PREFIX)
