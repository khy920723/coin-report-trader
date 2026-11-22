"""가짜 공개 시세(M1 명세 3.13). `core.QuoteSource`(3.4), `core.PriceSource`(M2 명세 3.4, 3.6),
`core.BookSource`(M3 명세 3.6), `core.MultiPriceSource`(M5 명세 3.4)의 약속을 진짜와 같게 지킨다.

- 봉 단위는 넷이다: 30분봉, 60분봉, 240분봉, 일봉(33번이 넓혔다).

- 돌려주는 것: 시험이 준 코인 목록(원화 마켓만, 준 차례대로)과 봉.
  - `get_candles(market, unit, *, to, count)`는 그 마켓·단위의 봉 가운데 `to` 앞에 시작한 봉을
    새것부터 `count`개 준다. `to`가 `None`이면 가장 최근 것부터 준다. 봉이 없으면 빈 튜플이다.
  - `count`가 1에서 200 밖이거나 `to`에 시간대가 없으면 진짜처럼 보내기 전에 `ValueError`다(기록하지
    않는다).
- 현재가(M2 명세 3.6): `get_price(market)`는 시험이 코인마다 정한 값(`prices=`나 `set_prices`)을
  준다. 정하지 않은 코인이면 시험의 실수라 `KeyError`다(기록하지 않는다). 정한 `QuoteError`는 아래
  `script`로 낸다(종류 `PRICE`).
- 여러 마켓의 현재가(M5 명세 3.15.2. `MultiPriceSource`): `get_prices(markets)`는 물은 마켓마다 같은
  값을 요청 한 번으로 준다(종류 `PRICES`. 기록의 `market`은 마켓 코드를 쉼표로 이은 글). 정하지 않은
  코인이 있으면 `KeyError`다(기록하지 않는다). 거르는 도우미는 `prices_requests()`다.
- 호가, 호가 단위, 1분봉(M3 명세 3.6, 3.19. `BookSource`):
  - `get_best_prices(market)`은 `set_book(market, ask, bid)`로 정한 (매도 호가, 매수 호가),
    `get_tick_size(market)`은 `set_tick_size(market, tick)`로 정한 값이다.
    `get_minute_candles(market, *, to, count)`는 `put_minutes(*candles)`로 넣은 1분봉 가운데 `to`
    앞에 시작한 것을 새것부터 `count`개 준다(`get_candles`와 같은 약속과 같은 인자 검사).
  - 정하지 않은 코인의 기본값: 호가 단위 1, 최우선 매도·매수 호가는 둘 다 그 코인의 `get_price`
    값(현재가도 정하지 않았으면 `KeyError`), 1분봉은 없음(빈 튜플).
  - 정한 `QuoteError`는 `script`로 낸다(종류 `BOOK`, `TICK`, `MINUTES`. 메서드마다).
  - `make_minutes(market, count, *, last_start)`가 `last_start`에서 끝나는 이어진 1분봉을 만든다.
- 봉 넣기: `set_candles(market, unit, candles)`는 그 마켓·단위의 봉을 통째로 바꾼다.
  `put_candles(*candles)`는 시작 시각이 같은 봉을 바꾸고 없는 봉을 더한다(새 봉이 생기고 마지막 봉의
  종가가 바뀌는 B9). `set_markets(markets)`는 코인 목록을 바꾼다. 받기 사이에 언제든 불러도 된다.
- 결과 정하기(`script`): 요청 하나 단위로 정한다. 받기 하나가 도는 동안 시험이 끼어들 수 없으므로
  미리 줄을 세워 둔다.
  - `script(*outcomes, kind=None, market=None, unit=None)`이 결과의 줄 하나를 더한다.
    `kind`(`MARKETS`, `CANDLES`, `PRICE`, `PRICES`, `BOOK`, `TICK`, `MINUTES`), `market`, `unit`
    가운데 준 것이 모두 맞는 요청만 그 줄을 쓴다(주지 않은 것은 가리지 않는다).
  - 요청이 오면 더한 차례대로 줄을 보고, 맞고 남은 결과가 있는 첫 줄에서 결과 하나를 꺼낸다.
    결과는 `QuoteError`(그 오류를 낸다)나 `OK`(정상으로 답한다)다. 맞는 줄이 없거나 다 썼으면
    정상으로 답한다.
  - 예: B35(일봉 처음 받기의 둘째 요청에서만 실패)는
    `script(OK, QuoteError(ERROR_NETWORK), kind=CANDLES, market=m, unit=CandleUnit.DAY)`다.
    B12는 같은 줄에 `QuoteError`, `QuoteError`, `OK`, `QuoteError`를 둔다. B11·B34의 일시 차단은
    `QuoteError(ERROR_BLOCKED, 120.0)`이다. 한 코인의 현재가 실패는
    `script(QuoteError(ERROR_NETWORK), kind=PRICE, market=m)`이다.
  - `clear_script()`는 남은 결과를 모두 버린다.
- 기록(`requests`): 요청마다 `QuoteRequest` 하나를 받은 차례대로 남긴다(실패한 요청도 남긴다).
  종류, 마켓, 단위, `to`, `count`, 낸 결과의 오류 이름(정상이면 `None`)을 담는다.
  - 시계: 만들 때 `clock`(가짜 시계)을 주면 요청마다 그때의 `clock.monotonic()`과 `clock.now()`를
    `monotonic`, `now`에 적는다. 주지 않으면 둘 다 `None`이다. B10은 `monotonic`으로 본다.
  - 거르는 도우미: `candle_requests(market=None, unit=None)`, `market_requests()`,
    `price_requests(market=None)`, `book_requests(kind=None, market=None)`. 현재가, 호가, 호가 단위
    요청의 단위, `to`, `count`는 `None`이고, 1분봉 요청은 `to`와 `count`가 있고 단위가 `None`이다.
- 만든 숫자의 봉: `make_candles(market, unit, count, *, last_start)`가 `last_start`에서 끝나는
  이어진 봉 `count`개를 시작 시각 순으로 만든다(값은 차례에서 셈한 만든 숫자다).
- 여러 스레드가 함께 써도 된다(잠금 하나).
"""

import threading
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final

from coin_report_trader.core import (
    CANDLE_UNIT_LENGTHS,
    Candle,
    CandleUnit,
    Clock,
    Market,
    MinuteCandle,
    QuoteError,
)

MARKETS: Final = "markets"
"""코인 목록 요청의 종류."""
CANDLES: Final = "candles"
"""봉 요청의 종류."""
PRICE: Final = "price"
"""현재가 요청의 종류(M2 명세 3.6)."""
PRICES: Final = "prices"
"""여러 마켓의 현재가를 한 번에 묻는 요청의 종류(M5 명세 3.15.2)."""
BOOK: Final = "book"
"""최우선 호가 요청의 종류(M3 명세 3.6)."""
TICK: Final = "tick"
"""호가 단위 요청의 종류(M3 명세 3.6)."""
MINUTES: Final = "minutes"
"""1분봉 요청의 종류(M3 명세 3.6)."""
BOOK_KINDS: Final = (BOOK, TICK, MINUTES)
DEFAULT_TICK_SIZE: Final = Decimal(1)
"""정하지 않은 코인의 호가 단위(M3 명세 3.6)."""
MAX_COUNT: Final = 200

UNIT_STEP: Final = CANDLE_UNIT_LENGTHS
"""단위의 길이 넷(30분봉, 60분봉, 240분봉, 일봉. 3.4의 `CANDLE_UNIT_LENGTHS`)."""


class _Ok:
    """정상으로 답하는 결과."""

    def __repr__(self) -> str:
        return "OK"


OK: Final = _Ok()

type Outcome = QuoteError | _Ok


@dataclass(frozen=True)
class QuoteRequest:
    """받은 요청 하나의 기록."""

    kind: str
    """`MARKETS`, `CANDLES`, `PRICE`."""
    market: str | None
    unit: CandleUnit | None
    to: datetime | None
    count: int | None
    error_name: str | None
    """낸 `QuoteError`의 오류 이름. 정상으로 답했으면 `None`."""
    monotonic: float | None
    """요청을 받은 때의 `clock.monotonic()`. 시계를 주지 않았으면 `None`."""
    now: datetime | None
    """요청을 받은 때의 `clock.now()`. 시계를 주지 않았으면 `None`."""


@dataclass
class _Script:
    kind: str | None
    market: str | None
    unit: CandleUnit | None
    outcomes: deque[Outcome]

    def matches(self, kind: str, market: str | None, unit: CandleUnit | None) -> bool:
        return (
            bool(self.outcomes)
            and (self.kind is None or self.kind == kind)
            and (self.market is None or self.market == market)
            and (self.unit is None or self.unit == unit)
        )


class FakeQuoteSource:
    """`QuoteSource`, `PriceSource`, `BookSource`의 가짜(모듈 설명)."""

    def __init__(
        self,
        *,
        markets: Iterable[Market] = (),
        candles: Iterable[Candle] = (),
        prices: Mapping[str, Decimal] | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._markets: tuple[Market, ...] = tuple(markets)
        self._prices: dict[str, Decimal] = dict(prices or {})
        self._books: dict[str, tuple[Decimal, Decimal]] = {}
        self._ticks: dict[str, Decimal] = {}
        self._minutes: dict[str, dict[datetime, MinuteCandle]] = {}
        self._candles: dict[tuple[str, CandleUnit], dict[datetime, Candle]] = {}
        self._scripts: list[_Script] = []
        self.requests: list[QuoteRequest] = []
        """받은 요청(차례대로)."""
        self.put_candles(*candles)

    # --- 시험이 정하는 것 ------------------------------------------------------------------

    def set_markets(self, markets: Iterable[Market]) -> None:
        with self._lock:
            self._markets = tuple(markets)

    def set_book(self, market: str, ask: Decimal, bid: Decimal) -> None:
        """그 코인의 최우선 매도 호가와 매수 호가를 정한다."""
        with self._lock:
            self._books[market] = (Decimal(ask), Decimal(bid))

    def set_tick_size(self, market: str, tick: Decimal) -> None:
        with self._lock:
            self._ticks[market] = Decimal(tick)

    def put_minutes(self, *candles: MinuteCandle) -> None:
        """1분봉을 더한다. 시작 시각이 같은 봉은 바꾼다."""
        with self._lock:
            for candle in candles:
                self._minutes.setdefault(candle.market, {})[candle.start] = candle

    def set_prices(self, prices: Mapping[str, Decimal]) -> None:
        """코인마다의 현재가를 더하거나 바꾼다(준 코인만)."""
        with self._lock:
            self._prices.update(prices)

    def set_candles(self, market: str, unit: CandleUnit, candles: Iterable[Candle]) -> None:
        """그 마켓·단위의 봉을 통째로 바꾼다. 다른 마켓·단위의 봉이 섞이면 `ValueError`다."""
        unit = CandleUnit(unit)
        items = tuple(candles)
        for candle in items:
            if candle.market != market or candle.unit != unit:
                raise ValueError("set_candles: 다른 마켓이나 단위의 봉이 섞였다")
        with self._lock:
            self._candles[(market, unit)] = {candle.start: candle for candle in items}

    def put_candles(self, *candles: Candle) -> None:
        """시작 시각이 같은 봉은 바꾸고, 없는 봉은 더한다."""
        with self._lock:
            for candle in candles:
                key = (candle.market, CandleUnit(candle.unit))
                self._candles.setdefault(key, {})[candle.start] = candle

    def candles(self, market: str, unit: CandleUnit) -> tuple[Candle, ...]:
        """지금 가진 그 마켓·단위의 봉(시작 시각 순)."""
        with self._lock:
            stored = self._candles.get((market, CandleUnit(unit)), {})
            return tuple(sorted(stored.values(), key=lambda candle: candle.start))

    def script(
        self,
        *outcomes: Outcome,
        kind: str | None = None,
        market: str | None = None,
        unit: CandleUnit | None = None,
    ) -> None:
        """결과의 줄 하나를 더한다(모듈 설명)."""
        if kind not in (None, MARKETS, CANDLES, PRICE, PRICES, *BOOK_KINDS):
            raise ValueError(f"script: 모르는 종류다: {kind!r}")
        for outcome in outcomes:
            if not isinstance(outcome, QuoteError | _Ok):
                raise TypeError("script: 결과는 QuoteError나 OK다")
        with self._lock:
            self._scripts.append(
                _Script(kind, market, None if unit is None else CandleUnit(unit), deque(outcomes))
            )

    def clear_script(self) -> None:
        with self._lock:
            self._scripts.clear()

    # --- 기록을 보는 도우미 ---------------------------------------------------------------

    def candle_requests(
        self, market: str | None = None, unit: CandleUnit | None = None
    ) -> list[QuoteRequest]:
        with self._lock:
            return [
                r
                for r in self.requests
                if r.kind == CANDLES
                and (market is None or r.market == market)
                and (unit is None or r.unit == unit)
            ]

    def market_requests(self) -> list[QuoteRequest]:
        with self._lock:
            return [r for r in self.requests if r.kind == MARKETS]

    def prices_requests(self) -> list[QuoteRequest]:
        """여러 마켓의 현재가 요청. 기록의 `market`은 물은 마켓 코드를 쉼표로 이은 글이다."""
        with self._lock:
            return [r for r in self.requests if r.kind == PRICES]

    def price_requests(self, market: str | None = None) -> list[QuoteRequest]:
        with self._lock:
            return [
                r
                for r in self.requests
                if r.kind == PRICE and (market is None or r.market == market)
            ]

    def book_requests(
        self, kind: str | None = None, market: str | None = None
    ) -> list[QuoteRequest]:
        """호가, 호가 단위, 1분봉 요청(`kind`를 주면 그 종류만)."""
        with self._lock:
            return [
                r
                for r in self.requests
                if r.kind in BOOK_KINDS
                and (kind is None or r.kind == kind)
                and (market is None or r.market == market)
            ]

    # --- BookSource -----------------------------------------------------------------------

    def get_best_prices(self, market: str) -> tuple[Decimal, Decimal]:
        """`BookSource`. 정한 호가, 없으면 둘 다 그 코인의 현재가(모듈 설명)."""
        with self._lock:
            if market not in self._books and market not in self._prices:
                raise KeyError(f"get_best_prices: 호가도 현재가도 정하지 않은 코인이다: {market}")
            self._answer(BOOK, market, None, None, None)
            if market in self._books:
                return self._books[market]
            price = self._prices[market]
            return price, price

    def get_tick_size(self, market: str) -> Decimal:
        """`BookSource`. 정한 호가 단위, 없으면 1."""
        with self._lock:
            self._answer(TICK, market, None, None, None)
            return self._ticks.get(market, DEFAULT_TICK_SIZE)

    def get_minute_candles(
        self, market: str, *, to: datetime | None, count: int
    ) -> tuple[MinuteCandle, ...]:
        """`BookSource`. `to` 앞에 시작한 1분봉을 새것부터 `count`개. 없으면 빈 튜플."""
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_COUNT:
            raise ValueError(f"count는 1에서 {MAX_COUNT}까지의 정수다: {count!r}")
        if to is not None and (to.tzinfo is None or to.utcoffset() is None):
            raise ValueError("to는 시간대가 붙은 datetime이어야 한다")
        with self._lock:
            self._answer(MINUTES, market, None, to, count)
            stored = self._minutes.get(market, {})
            chosen = sorted(
                (c for c in stored.values() if to is None or c.start < to),
                key=lambda candle: candle.start,
                reverse=True,
            )
            return tuple(chosen[:count])

    # --- QuoteSource ----------------------------------------------------------------------

    def list_markets(self) -> tuple[Market, ...]:
        with self._lock:
            self._answer(MARKETS, None, None, None, None)
            return tuple(m for m in self._markets if m.market.startswith("KRW-"))

    def get_price(self, market: str) -> Decimal:
        """`PriceSource`. 정한 현재가를 준다(모듈 설명)."""
        with self._lock:
            if market not in self._prices:
                raise KeyError(f"get_price: 현재가를 정하지 않은 코인이다: {market}")
            self._answer(PRICE, market, None, None, None)
            return self._prices[market]

    def get_prices(self, markets: tuple[str, ...]) -> dict[str, Decimal]:
        """`MultiPriceSource`. 물은 마켓마다 정한 현재가를 요청 한 번으로 준다(모듈 설명)."""
        markets = tuple(markets)
        if not markets or len(set(markets)) != len(markets):
            raise ValueError("물을 마켓이 하나 이상이고 겹치지 않아야 한다")
        with self._lock:
            missing = [m for m in markets if m not in self._prices]
            if missing:
                raise KeyError(f"get_prices: 현재가를 정하지 않은 코인이다: {missing}")
            self._answer(PRICES, ",".join(markets), None, None, None)
            return {market: self._prices[market] for market in markets}

    def get_candles(
        self, market: str, unit: CandleUnit, *, to: datetime | None, count: int
    ) -> tuple[Candle, ...]:
        unit = CandleUnit(unit)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_COUNT:
            raise ValueError(f"count는 1에서 {MAX_COUNT}까지의 정수다: {count!r}")
        if to is not None and (to.tzinfo is None or to.utcoffset() is None):
            raise ValueError("to는 시간대가 붙은 datetime이어야 한다")
        with self._lock:
            self._answer(CANDLES, market, unit, to, count)
            stored = self._candles.get((market, unit), {})
            chosen = sorted(
                (c for c in stored.values() if to is None or c.start < to),
                key=lambda candle: candle.start,
                reverse=True,
            )
            return tuple(chosen[:count])

    def _answer(
        self,
        kind: str,
        market: str | None,
        unit: CandleUnit | None,
        to: datetime | None,
        count: int | None,
    ) -> None:
        """잠금 안에서 부른다. 결과를 꺼내 기록하고, 오류이면 낸다."""
        outcome: Outcome = OK
        for line in self._scripts:
            if line.matches(kind, market, unit):
                outcome = line.outcomes.popleft()
                break
        error = outcome if isinstance(outcome, QuoteError) else None
        self.requests.append(
            QuoteRequest(
                kind=kind,
                market=market,
                unit=unit,
                to=to,
                count=count,
                error_name=None if error is None else error.error_name,
                monotonic=None if self._clock is None else self._clock.monotonic(),
                now=None if self._clock is None else self._clock.now(),
            )
        )
        if error is not None:
            # 같은 객체를 여러 번 내도 traceback이 쌓이지 않게 새로 만든다.
            raise QuoteError(error.error_name, error.retry_after)


def make_candles(
    market: str, unit: CandleUnit, count: int, *, last_start: datetime
) -> tuple[Candle, ...]:
    """`last_start`에서 끝나는 이어진 봉 `count`개(시작 시각 순). 값은 차례에서 셈한 만든 숫자다."""
    unit = CandleUnit(unit)
    if last_start.tzinfo is None:
        raise ValueError("last_start는 시간대가 붙은 datetime이어야 한다")
    step = UNIT_STEP[unit]
    first = last_start - step * (count - 1)
    candles = []
    for i in range(count):
        base = Decimal(1000 + (i * 7) % 50)
        candles.append(
            Candle(
                market=market,
                unit=unit,
                start=first + step * i,
                open=base,
                high=base + Decimal(5),
                low=base - Decimal(5),
                close=base + Decimal((i % 3) - 1),
                volume=Decimal("1.5") + Decimal(i % 4),
                value=(base * Decimal("1.5")).quantize(Decimal("0.01")),
            )
        )
    return tuple(candles)


def with_close(candle: Candle, close: Decimal) -> Candle:
    """종가만 바꾼 봉(B9처럼 마지막 봉이 바뀌는 시험에 쓴다)."""
    return replace(candle, close=close)


def make_minutes(market: str, count: int, *, last_start: datetime) -> tuple[MinuteCandle, ...]:
    """`last_start`에서 끝나는 이어진 1분봉 `count`개(시작 시각 순).

    값은 차례에서 셈한 만든 숫자다.
    """
    if last_start.tzinfo is None:
        raise ValueError("last_start는 시간대가 붙은 datetime이어야 한다")
    step = timedelta(minutes=1)
    first = last_start - step * (count - 1)
    candles = []
    for i in range(count):
        base = Decimal(1000 + (i * 3) % 20)
        candles.append(
            MinuteCandle(
                market=market,
                start=first + step * i,
                high=base + Decimal(2),
                low=base - Decimal(2),
                close=base,
            )
        )
    return tuple(candles)
