"""감싸는 부품 `QuoteClient`(M1 명세 3.4, 3.5): 나눠 받기와 요청 한도를 맡는다. `app`은 이것만 쓴다.

- `list_markets()`: 요청 한 번이다.
- `fetch_candles(market, unit, *, last_start)`:
  - `last_start`가 `None`이면 처음 받기다.
    - 일봉: 가장 최근부터 200개씩 거슬러 가장 오래된 봉까지 받는다. 받은 봉이 요청한 개수보다
      적으면 더 오래된 봉이 없는 것으로 보고 멈춘다(빈 응답을 확인하려고 요청을 더 내지 않는다).
    - 60분봉: 가장 최근 200개를 한 번만 받는다.
  - 아니면 그 뒤의 받기다. `last_start`부터 지금(`clock.now()`)까지를 가장 최근부터 거슬러 받는다.
    요청 하나의 개수는 그 구간에 들 수 있는 봉의 수(200 이하)다. 받은 봉이 `last_start`에 닿거나
    요청한 개수보다 적으면 멈춘다. `last_start`보다 앞에 시작한 봉은 버린다.
  - 돌려주는 봉은 시작 시각 순이고 시작 시각이 겹치지 않는다(그 뒤의 받기는 `last_start`의 봉 포함).
  - 받기 중간에 실패하면 그때까지 받은 봉을 돌려주지 않고 `QuoteError`를 그대로 낸다(3.5의 "끝까지
    성공했을 때만 저장한다").
- 요청 한도(3.10)는 `RequestLimiter`가 `clock`으로 지킨다. 코인 목록과 봉 요청을 함께 센다.
- "요청 한도 초과"를 받아도 그 자리에서 기다려 다시 보내지 않는다(3.9). 일시 차단의
  `blocked_until`은 이 부품이 들지 않는다(`app`이 든다).
- `source`가 약속(3.4)을 어긴 응답(다른 마켓·단위의 봉, `to` 뒤에 시작한 봉, `count`보다 많은 봉)을
  주면 응답 형식 오류다. 그래서 거슬러 받기가 끝나지 않는 일이 없다.
"""

import math
from datetime import datetime, timedelta
from types import MappingProxyType

from coin_report_trader.core.clock import Clock
from coin_report_trader.core.errors import QuoteError
from coin_report_trader.core.market import Candle, CandleUnit, Market
from coin_report_trader.core.ports import QuoteSource
from coin_report_trader.core.texts import ERROR_BAD_RESPONSE
from coin_report_trader.trading.quote.ratelimit import RequestLimiter

MAX_CANDLES = 200
"""요청 하나로 받는 봉의 수의 상한."""

UNIT_LENGTH: MappingProxyType[CandleUnit, timedelta] = MappingProxyType(
    {CandleUnit.DAY: timedelta(days=1), CandleUnit.MINUTE_60: timedelta(minutes=60)}
)


def _aware(moment: datetime, name: str) -> datetime:
    if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{name}는 시간대가 붙은 datetime이어야 한다")
    return moment


def _slots(newer: datetime, older: datetime, unit: CandleUnit) -> int:
    """`older` 이상 `newer` 이하에 시작하는 봉이 들 수 있는 수. 1에서 200 사이로 맞춘다."""
    count = math.floor((newer - older) / UNIT_LENGTH[unit]) + 1
    return max(1, min(MAX_CANDLES, count))


class QuoteClient:
    """`QuoteSource`를 감싼다. 한 `QuoteClient`가 낸 요청을 한 한도 장치로 센다."""

    def __init__(self, source: QuoteSource, clock: Clock) -> None:
        self._source = source
        self._clock = clock
        self._limiter = RequestLimiter(clock)

    def list_markets(self) -> tuple[Market, ...]:
        """원화 마켓의 코인 목록. 요청 한 번이다."""
        self._limiter.acquire()
        return tuple(self._source.list_markets())

    def fetch_candles(
        self, market: str, unit: CandleUnit, *, last_start: datetime | None
    ) -> tuple[Candle, ...]:
        """한 코인·단위의 받기(모듈 설명). 봉은 시작 시각 순이다."""
        unit = CandleUnit(unit)
        if last_start is None:
            if unit is CandleUnit.DAY:
                return self._fetch_all(market, unit)
            return _ascending(self._request(market, unit, to=None, count=MAX_CANDLES))
        return self._fetch_since(market, unit, _aware(last_start, "last_start"))

    def _fetch_all(self, market: str, unit: CandleUnit) -> tuple[Candle, ...]:
        collected: list[Candle] = []
        to: datetime | None = None
        while True:
            batch = self._request(market, unit, to=to, count=MAX_CANDLES)
            collected.extend(batch)
            if len(batch) < MAX_CANDLES:
                return _ascending(collected)
            to = min(candle.start for candle in batch)

    def _fetch_since(
        self, market: str, unit: CandleUnit, last_start: datetime
    ) -> tuple[Candle, ...]:
        collected: list[Candle] = []
        to: datetime | None = None
        count = _slots(self._clock.now(), last_start, unit)
        while True:
            batch = self._request(market, unit, to=to, count=count)
            collected.extend(candle for candle in batch if candle.start >= last_start)
            if len(batch) < count:
                break
            oldest = min(candle.start for candle in batch)
            if oldest <= last_start:
                break
            to = oldest
            # `to` 앞이고 `last_start` 이상인 시작 시각의 수(`to`는 빼므로 하나 적다).
            count = max(1, min(MAX_CANDLES, math.ceil((oldest - last_start) / UNIT_LENGTH[unit])))
        return _ascending(collected)

    def _request(
        self, market: str, unit: CandleUnit, *, to: datetime | None, count: int
    ) -> tuple[Candle, ...]:
        """요청 하나. 한도를 지킨 뒤에 보내고, 약속을 어긴 응답은 응답 형식 오류로 바꾼다."""
        self._limiter.acquire()
        batch = tuple(self._source.get_candles(market, unit, to=to, count=count))
        if len(batch) > count or any(
            candle.market != market
            or candle.unit != unit
            or (to is not None and candle.start >= to)
            for candle in batch
        ):
            raise QuoteError(ERROR_BAD_RESPONSE)
        return batch


def _ascending(candles: list[Candle] | tuple[Candle, ...]) -> tuple[Candle, ...]:
    """시작 시각 순. 시작 시각이 같은 봉은 먼저 받은 것(더 새로 받은 응답의 것)을 남긴다."""
    by_start: dict[datetime, Candle] = {}
    for candle in candles:
        by_start.setdefault(candle.start, candle)
    return tuple(sorted(by_start.values(), key=lambda candle: candle.start))
