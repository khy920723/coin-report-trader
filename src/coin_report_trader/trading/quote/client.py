"""감싸는 부품 `QuoteClient`(M1 명세 3.4, 3.5): 나눠 받기와 요청 한도를 맡는다. `app`은 이것만 쓴다.

- `list_markets()`: 요청 한 번이다.
- `read_best_prices(market)`, `read_tick_size(market)`: 호가의 첫 줄과 호가 단위(M3 명세 3.6). 요청
  한 번이다. `fetch_minutes(market, *, since, until)`: `since` 이상 `until` 미만에 시작한 1분봉을
  200개씩 나눠 받아 시작 시각 순으로 준다. 셋 다 소스가 `BookSource`가 아니면 요청을 내지 않고
  `TypeError`다.
- `read_price(market)`: 현재가(M2 명세 3.6). 요청 한 번이고 실패하면 `QuoteError`를 그대로 낸다.
  소스가 `PriceSource`가 아니면 `TypeError`다.
- `fetch_recent(market, unit, *, count, last_start, first_start, missing)`: 1단계 보완의
  받기(3.5.3). `RecentCandles(candles, reached_oldest)`를 돌려준다.
  - `last_start`가 `None`이면 처음 받기다. 가장 최근부터 200개씩 거슬러, 받은 완성된 봉이
    `count`(N)개 모이거나 거래소가 요청한 수보다 적게 줄 때까지 받는다. 적게 주면 `reached_oldest`가
    참이다. 완성은 봉의 시작 시각에 단위의 길이(`CANDLE_UNIT_LENGTHS`)를 더한 때가 지금
    (`clock.now()`)보다 뒤가 아닌 것이다. 받은 미완성 봉도 돌려준다(셈에서 빼는 것은 `stats`다).
  - 아니면 그 뒤의 받기를 한다. `last_start`부터 지금(`clock.now()`)까지를 가장 최근부터 거슬러
    받는다. 요청 하나의 개수는 그 구간에 들 수 있는 봉의 수(200 이하)다. 받은 봉이 `last_start`에
    닿거나 요청한 개수보다 적으면 멈추고, `last_start`보다 앞에 시작한 봉은 버린다(`last_start`의
    봉은 넣는다). 이어서 `missing`이 0보다 크면 거슬러 받기를 한다.
  - 거슬러 받기는 `first_start`(저장된 가장 이른 봉의 시작 시각) 앞을 200개씩, 모두 `missing`개만큼
    받는다. 거래소가 요청한 수보다 적게 주면 거기서 멈추고 `reached_oldest`가 참이다. `missing`이
    0이면 거슬러 받기 요청을 내지 않고 `reached_oldest`는 거짓이다.
  - 돌려주는 봉은 시작 시각 순이고 시작 시각이 겹치지 않는다.
- 중간에 실패하면 그때까지 받은 봉을 돌려주지 않고 `QuoteError`를 그대로 낸다
  (3.5의 "끝까지 성공했을 때만 저장한다").
- 요청 한도(3.10)는 `RequestLimiter`가 `clock`으로 지킨다. 코인 목록, 현재가, 봉 요청을 함께 센다.
- "요청 한도 초과"를 받아도 그 자리에서 기다려 다시 보내지 않는다(3.9). 일시 차단의
  `blocked_until`은 이 부품이 들지 않는다(`app`이 든다).
- `source`가 약속(3.4)을 어긴 응답(다른 마켓·단위의 봉, `to` 뒤에 시작한 봉, `count`보다 많은 봉)을
  주면 응답 형식 오류다. 그래서 거슬러 받기가 끝나지 않는 일이 없다.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from coin_report_trader.core.clock import Clock
from coin_report_trader.core.errors import QuoteError
from coin_report_trader.core.market import CANDLE_UNIT_LENGTHS, Candle, CandleUnit, Market
from coin_report_trader.core.ports import BookSource, MinuteCandle, PriceSource, QuoteSource
from coin_report_trader.core.texts import ERROR_BAD_RESPONSE
from coin_report_trader.trading.quote.ratelimit import RequestLimiter

MINUTE = timedelta(minutes=1)
MAX_CANDLES = 200
"""요청 하나로 받는 봉의 수의 상한."""

UNIT_LENGTH: Mapping[CandleUnit, timedelta] = CANDLE_UNIT_LENGTHS
"""단위의 길이 넷(3.4의 `CANDLE_UNIT_LENGTHS`)."""


@dataclass(frozen=True)
class RecentCandles:
    """`fetch_recent`의 결과(3.4, 3.5.3)."""

    candles: tuple[Candle, ...]
    """받은 봉. 시작 시각 순이고 시작 시각이 겹치지 않는다."""
    reached_oldest: bool
    """처음 받기나 거슬러 받기에서 거래소가 요청한 수보다 적게 주었다(더 이른 봉이 없다)."""


def _aware(moment: datetime, name: str) -> datetime:
    if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{name}는 시간대가 붙은 datetime이어야 한다")
    return moment


def _count(value: int, name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name}는 정수다")
    if value < minimum:
        raise ValueError(f"{name}는 {minimum} 이상이다: {value}")
    return value


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

    def read_price(self, market: str) -> Decimal:
        """현재가(M2 명세 3.6). 요청 한 번이고 봉 받기와 같은 요청 한도 장치를 쓴다.

        소스가 `PriceSource`가 아니면 요청을 내지 않고 `TypeError`다.
        """
        if not isinstance(self._source, PriceSource):
            raise TypeError("소스가 PriceSource가 아니어서 현재가를 읽을 수 없다")
        self._limiter.acquire()
        return self._source.get_price(market)

    def read_best_prices(self, market: str) -> tuple[Decimal, Decimal]:
        """(최우선 매도 호가, 최우선 매수 호가)(M3 명세 3.6). 요청 한 번이다."""
        source = self._book()
        self._limiter.acquire()
        return source.get_best_prices(market)

    def read_tick_size(self, market: str) -> Decimal:
        """호가 단위(M3 명세 3.6). 요청 한 번이다."""
        source = self._book()
        self._limiter.acquire()
        return source.get_tick_size(market)

    def fetch_minutes(
        self, market: str, *, since: datetime, until: datetime
    ) -> tuple[MinuteCandle, ...]:
        """`since` 이상 `until` 미만에 시작한 1분봉을 시작 시각 순으로(M3 명세 3.6).

        `until`부터 거슬러 200개씩 나눠 받는다. 요청 하나의 개수는 그 구간에 들 수 있는 1분봉의
        수(200 이하)다. 받은 봉이 `since`에 닿거나 요청한 개수보다 적으면 멈춘다. `since`가
        `until`보다 앞이 아니면 요청하지 않고 빈 튜플이다. 중간에 실패하면 받은 봉을 돌려주지 않고
        `QuoteError`를 그대로 낸다.
        """
        source = self._book()
        since = _aware(since, "since")
        until = _aware(until, "until")
        if since >= until:
            return ()
        collected: dict[datetime, MinuteCandle] = {}
        to = until
        while True:
            count = max(1, min(MAX_CANDLES, math.ceil((to - since) / MINUTE)))
            self._limiter.acquire()
            batch = tuple(source.get_minute_candles(market, to=to, count=count))
            if len(batch) > count or any(
                candle.market != market or candle.start >= to for candle in batch
            ):
                raise QuoteError(ERROR_BAD_RESPONSE)
            for candle in batch:
                if candle.start >= since:
                    collected.setdefault(candle.start, candle)
            if len(batch) < count:
                break
            oldest = min(candle.start for candle in batch)
            if oldest <= since:
                break
            to = oldest
        return tuple(sorted(collected.values(), key=lambda candle: candle.start))

    def _book(self) -> BookSource:
        if not isinstance(self._source, BookSource):
            raise TypeError("소스가 BookSource가 아니어서 호가와 1분봉을 읽을 수 없다")
        return self._source

    def fetch_recent(
        self,
        market: str,
        unit: CandleUnit,
        *,
        count: int,
        last_start: datetime | None,
        first_start: datetime | None,
        missing: int,
    ) -> RecentCandles:
        """1단계 보완의 받기(모듈 설명, 3.5.3)."""
        unit = CandleUnit(unit)
        count = _count(count, "count", minimum=1)
        missing = _count(missing, "missing", minimum=0)
        if last_start is None:
            candles, reached = self._fetch_initial(market, unit, count)
            return RecentCandles(_ascending(candles), reached)
        # 인자는 요청을 내기 전에 모두 본다(틀리면 아무 요청도 나가지 않는다).
        last_start = _aware(last_start, "last_start")
        if missing > 0:
            if first_start is None:
                raise ValueError("거슬러 받기(missing > 0)에는 first_start가 있어야 한다")
            first_start = _aware(first_start, "first_start")
        collected = list(self._fetch_since(market, unit, last_start))
        reached = False
        if missing > 0 and first_start is not None:
            older, reached = self._fetch_older(market, unit, first_start, missing)
            collected.extend(older)
        return RecentCandles(_ascending(collected), reached)

    def _fetch_initial(
        self, market: str, unit: CandleUnit, count: int
    ) -> tuple[list[Candle], bool]:
        """처음 받기. (받은 봉, `reached_oldest`)."""
        now = self._clock.now()
        length = UNIT_LENGTH[unit]
        collected: list[Candle] = []
        completed = 0
        to: datetime | None = None
        while True:
            batch = self._request(market, unit, to=to, count=MAX_CANDLES)
            collected.extend(batch)
            completed += sum(1 for candle in batch if candle.start + length <= now)
            if len(batch) < MAX_CANDLES:
                return collected, True
            if completed >= count:
                return collected, False
            to = min(candle.start for candle in batch)

    def _fetch_older(
        self, market: str, unit: CandleUnit, first_start: datetime, missing: int
    ) -> tuple[list[Candle], bool]:
        """거슬러 받기. `first_start` 앞을 200개씩 모두 `missing`개. (받은 봉, `reached_oldest`)."""
        collected: list[Candle] = []
        remaining = missing
        to = first_start
        while remaining > 0:
            asked = min(MAX_CANDLES, remaining)
            batch = self._request(market, unit, to=to, count=asked)
            collected.extend(batch)
            if len(batch) < asked:
                return collected, True
            remaining -= asked
            to = min(candle.start for candle in batch)
        return collected, False

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
