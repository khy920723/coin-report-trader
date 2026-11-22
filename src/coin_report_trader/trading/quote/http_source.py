"""진짜 `QuoteSource`(M1 명세 3.4): 거래소의 공개 시세를 `httpx`로 부른다.

- 봉 단위는 넷이다: 30분봉, 60분봉, 240분봉, 일봉(요청 경로는 3.5.3).
- `BookSource`도 갖춘다(M3 명세 3.6): `get_best_prices(market)`은 `/v1/orderbook`(마켓 하나)의 첫
  줄의 (매도 호가, 매수 호가), `get_tick_size(market)`은 `/v1/orderbook/instruments`(마켓 하나)의
  `tick_size`, `get_minute_candles(market, *, to, count)`는 `/v1/candles/minutes/1`이다. 봉 받기와
  같은 변환과 오류 이름이고, 값이 0보다 크지 않거나 다른 마켓의 것이면 "응답 형식 오류"다.
- `PriceSource`도 갖춘다: `get_price(market)`는 `/v1/ticker`(마켓 코드 하나)의 최근 체결가다(M2 명세
  3.6). 응답 변환과 오류 이름은 다른 요청과 같다(읽지 못하면 "응답 형식 오류").

- 공개 시세만 부른다(키 없음). 계정, 주문, 서명, 웹소켓은 없다.
- 한 번 부르는 것이 요청 한 번이다. 안에서 다시 보내지 않고(`retries=0`), 리다이렉트를 따라가지
  않는다. 요청 한도는 이 부품이 아니라 `QuoteClient`가 지킨다.
- TLS 설정은 `core.make_tls_context(certifi.where())`다(3.10). `trust_env=False`라 환경 변수의
  프록시와 인증서 설정을 읽지 않는다(PC마다 같은 결과를 내려는 옛 코드의 방식).
- 쿠키는 저장하지도 보내지도 않는다.
- 시간 제한: 연결 5초, 그 밖(읽기, 쓰기, 연결 풀) 10초.
- 실패는 모두 `QuoteError`다(`errors.py`의 표). 응답의 글은 오류 이름으로 옮기지 않는다.
- 동기 `httpx.Client` 하나를 가진다. 한 스레드에서만 쓴다(3.12). 다 쓰면 `close()`를 부른다.
"""

import ssl
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http.cookiejar import CookieJar, DefaultCookiePolicy
from types import MappingProxyType
from typing import Any

import certifi
import httpx

from coin_report_trader import __version__
from coin_report_trader.core.market import Candle, CandleUnit, Market
from coin_report_trader.core.ports import MinuteCandle
from coin_report_trader.core.tls import make_tls_context
from coin_report_trader.trading.quote import convert
from coin_report_trader.trading.quote.errors import (
    bad_response,
    error_for_exception,
    error_for_status,
)

DEFAULT_BASE_URL = "https://api.upbit.com"
USER_AGENT = f"coin-report-trader/{__version__}"
CONNECT_TIMEOUT = 5.0
TIMEOUT = 10.0
MAX_COUNT = 200
"""요청 한 번에 받는 봉의 수의 상한(제품 규칙 7절)."""

MARKETS_PATH = "/v1/market/all"
PRICE_PATH = "/v1/ticker"
"""현재가(최근 체결가)의 요청 경로(M2 명세 3.6). 마켓 코드 하나로 부른다."""
BOOK_PATH = "/v1/orderbook"
"""호가(M3 명세 3.6). 마켓 코드 하나로 부르고 첫 줄을 쓴다."""
TICK_PATH = "/v1/orderbook/instruments"
"""호가 정책(호가 단위 `tick_size`. M3 명세 3.6). 마켓 코드 하나로 부른다."""
MINUTE_PATH = "/v1/candles/minutes/1"
"""1분봉(M3 명세 3.6)."""
CANDLE_PATHS: MappingProxyType[CandleUnit, str] = MappingProxyType(
    {
        CandleUnit.MINUTE_30: "/v1/candles/minutes/30",
        CandleUnit.MINUTE_60: "/v1/candles/minutes/60",
        CandleUnit.MINUTE_240: "/v1/candles/minutes/240",
        CandleUnit.DAY: "/v1/candles/days",
    }
)

# 응답 값을 읽다가 나면 "응답 형식 오류"로 보는 예외.
_SHAPE_ERRORS = (convert.ShapeError, KeyError, TypeError, ValueError, ArithmeticError)


def default_tls_context() -> ssl.SSLContext:
    """앱의 TLS 설정(3.10). 인증서 묶음은 `certifi`의 파일이다."""
    return make_tls_context(certifi.where())


def format_to(moment: datetime) -> str:
    """봉 요청의 `to`: UTC의 `YYYY-MM-DDTHH:MM:SSZ`. 초 아래가 있으면 다음 초로 올린다.

    봉의 시작 시각은 분 단위라서 올려도 "`to` 앞에 시작한 봉"이 바뀌지 않는다.
    """
    utc = moment.astimezone(UTC)
    if utc.microsecond:
        utc = utc.replace(microsecond=0) + timedelta(seconds=1)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _no_cookies() -> CookieJar:
    return CookieJar(policy=DefaultCookiePolicy(allowed_domains=()))


def _check_market(market: str) -> None:
    if not isinstance(market, str) or convert.MARKET_CODE.fullmatch(market) is None:
        raise ValueError(f"마켓 코드의 꼴이 아니다: {market!r}")


def check_request(market: str, unit: CandleUnit, to: datetime | None, count: int) -> CandleUnit:
    """봉 요청의 인자. 틀리면 보내기 전에 `TypeError`나 `ValueError`다(부르는 쪽의 실수)."""
    if not isinstance(market, str) or convert.MARKET_CODE.fullmatch(market) is None:
        raise ValueError(f"마켓 코드의 꼴이 아니다: {market!r}")
    unit = CandleUnit(unit)
    if to is not None and (
        not isinstance(to, datetime) or to.tzinfo is None or to.utcoffset() is None
    ):
        raise ValueError("to는 시간대가 붙은 datetime이어야 한다")
    if isinstance(count, bool) or not isinstance(count, int):
        raise TypeError("count는 정수다")
    if not 1 <= count <= MAX_COUNT:
        raise ValueError(f"count는 1에서 {MAX_COUNT}까지다: {count}")
    return unit


class HttpQuoteSource:
    """`QuoteSource`의 진짜.

    `transport`는 시험이 `httpx.MockTransport`를 넣는 길이다. 주지 않으면 앱의 TLS 설정으로
    `httpx.HTTPTransport`를 만든다.
    """

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        if base_url.endswith("/"):
            raise ValueError("base_url은 끝의 /없이 준다")
        if transport is None:
            transport = httpx.HTTPTransport(
                verify=default_tls_context(), retries=0, trust_env=False
            )
        self._client = httpx.Client(
            transport=transport,
            base_url=base_url,
            timeout=httpx.Timeout(TIMEOUT, connect=CONNECT_TIMEOUT),
            follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            cookies=_no_cookies(),
            trust_env=False,
        )

    def __repr__(self) -> str:
        return "HttpQuoteSource()"

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpQuoteSource":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def list_markets(self) -> tuple[Market, ...]:
        """원화 마켓만 준다(유의·주의 표시 포함)."""
        data = self._get(MARKETS_PATH, {"is_details": "true"})
        return _parse(convert.to_markets, data)

    def get_price(self, market: str) -> Decimal:
        """`PriceSource`(M2 명세 3.4, 3.6). 그 마켓의 현재가(최근 체결가)다. 요청 한 번이다."""
        _check_market(market)
        data = self._get(PRICE_PATH, {"markets": market})
        return _parse(lambda value: convert.to_price(value, market), data)

    def get_best_prices(self, market: str) -> tuple[Decimal, Decimal]:
        """`BookSource`(M3 명세 3.4, 3.6). (최우선 매도 호가, 최우선 매수 호가). 요청 한 번이다."""
        _check_market(market)
        data = self._get(BOOK_PATH, {"markets": market})
        return _parse(lambda value: convert.to_best_prices(value, market), data)

    def get_tick_size(self, market: str) -> Decimal:
        """`BookSource`. 그 마켓의 호가 단위다. 요청 한 번이다."""
        _check_market(market)
        data = self._get(TICK_PATH, {"markets": market})
        return _parse(lambda value: convert.to_tick_size(value, market), data)

    def get_minute_candles(
        self, market: str, *, to: datetime | None, count: int
    ) -> tuple[MinuteCandle, ...]:
        """`BookSource`. `to` 앞에 시작한 1분봉을 새것부터 `count`(200 이하)개까지 준다."""
        check_request(market, CandleUnit.MINUTE_60, to, count)  # 마켓, to, count의 검사
        params = {"market": market}
        if to is not None:
            params["to"] = format_to(to)
        params["count"] = str(count)
        data = self._get(MINUTE_PATH, params)
        candles = _parse(convert.to_minute_candles, data)
        if any(candle.market != market for candle in candles):
            raise bad_response()
        by_start: dict[datetime, MinuteCandle] = {}
        for candle in candles:
            if to is None or candle.start < to:
                by_start.setdefault(candle.start, candle)
        ordered = sorted(by_start.values(), key=lambda candle: candle.start, reverse=True)
        return tuple(ordered[:count])

    def get_candles(
        self, market: str, unit: CandleUnit, *, to: datetime | None, count: int
    ) -> tuple[Candle, ...]:
        """`to` 앞에 시작한 봉을 새것부터 `count`개까지 준다. `to`가 `None`이면 가장 최근부터."""
        unit = check_request(market, unit, to, count)
        params = {"market": market}
        if to is not None:
            params["to"] = format_to(to)
        params["count"] = str(count)
        data = self._get(CANDLE_PATHS[unit], params)
        candles = _parse(lambda value: convert.to_candles(value, unit), data)
        return _newest_first(candles, market, to, count)

    def _get(self, path: str, params: dict[str, str]) -> Any:
        try:
            response = self._client.get(path, params=params)
        except httpx.RequestError as exc:
            raise error_for_exception(exc) from exc
        status = response.status_code
        if not 200 <= status < 300:
            raise error_for_status(status, response.headers)
        content = response.content
        del response
        return _parse(convert.parse_json, content)


def _parse[T](parse: Callable[[Any], T], data: Any) -> T:
    """`parse(data)`. 꼴이 다르면 `QuoteError`(응답 형식 오류)다.

    예외는 이 함수 안에서 끝내고 except 밖에서 새로 낸다. 응답 값을 가진 프레임이 오류의
    traceback에 남지 않게 하려는 것이다.
    """
    failed = False
    result: Any = None
    try:
        result = parse(data)
    except _SHAPE_ERRORS:
        failed = True
    if failed:
        raise bad_response()
    return result


def _newest_first(
    candles: tuple[Candle, ...], market: str, to: datetime | None, count: int
) -> tuple[Candle, ...]:
    """약속(3.4)대로 맞춘다: 그 마켓의 봉이고, `to` 앞에 시작했고, 새것부터 `count`개까지.

    다른 마켓의 봉이 섞여 있으면 응답 형식 오류다. 시작 시각이 같은 봉은 하나만 남긴다.
    """
    if any(candle.market != market for candle in candles):
        raise bad_response()
    by_start: dict[datetime, Candle] = {}
    for candle in candles:
        if to is None or candle.start < to:
            by_start.setdefault(candle.start, candle)
    ordered = sorted(by_start.values(), key=lambda candle: candle.start, reverse=True)
    return tuple(ordered[:count])
