"""봉과 마켓. 가격과 수량은 `Decimal`, 시각은 UTC가 붙은 `datetime`이다."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

MARKET_PREFIX = "KRW-"


class CandleUnit(StrEnum):
    DAY = "day"
    MINUTE_60 = "minute60"


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
