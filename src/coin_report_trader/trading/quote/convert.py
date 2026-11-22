"""응답 변환(M1 명세 3.2, 3.4, M2 명세 3.6): 공개 시세 응답의 JSON을 `Market`, `Candle`, 현재가로
바꾼다.

- JSON의 숫자는 모두 `Decimal`로 읽는다(부동소수점을 거치지 않는다). 숫자 글(`"123.4"`)도 받는다.
  참·거짓, `null`, NaN, 무한대는 숫자로 받지 않는다.
- 봉의 시작 시각은 `candle_date_time_utc`(시간대 없는 UTC 글)이고, UTC가 붙은 `datetime`으로 만든다.
- 꼴이 다르면 `ShapeError`다. 응답의 값은 오류 글에 넣지 않는다.
- 응답의 꼴(필드 이름)은 옛 코드의 변환 코드에서 읽었다.
"""

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from coin_report_trader.core.market import MARKET_PREFIX, Candle, CandleUnit, Market
from coin_report_trader.core.ports import MinuteCandle

MARKET_CODE = re.compile(r"[A-Z0-9]+-[A-Z0-9]+")
"""마켓 코드의 꼴(`KRW-AAA`). 대문자·숫자와 `-` 하나."""


class ShapeError(ValueError):
    """응답이 예상한 꼴이 아니다(값은 담지 않는다)."""


def _reject_constant(name: str) -> Any:
    raise ShapeError("JSON 표준 밖의 상수다")


def parse_json(content: bytes) -> Any:
    """응답 본문. 소수와 정수는 모두 `Decimal`로 읽고, NaN·Infinity는 `ShapeError`다."""
    try:
        return json.loads(
            content, parse_float=Decimal, parse_int=Decimal, parse_constant=_reject_constant
        )
    except (ValueError, RecursionError):
        raise ShapeError("JSON으로 읽지 못했다") from None


def decimal_value(value: Any) -> Decimal:
    """`Decimal`이나 숫자 글 → 유한한 `Decimal`."""
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, str):
        try:
            number = Decimal(value.strip())
        except InvalidOperation:
            raise ShapeError("숫자 글이 아니다") from None
    else:
        raise ShapeError(f"숫자가 아니다: {type(value).__name__}")
    if not number.is_finite():
        raise ShapeError("유한한 숫자가 아니다")
    return number


def text_value(value: Any) -> str:
    if not isinstance(value, str):
        raise ShapeError(f"글이 아니다: {type(value).__name__}")
    return value


def utc_from_naive(value: Any) -> datetime:
    """시간대 없는 UTC 글 → UTC가 붙은 `datetime`. 시간대가 붙어 있으면 UTC로 바꾼다."""
    try:
        moment = datetime.fromisoformat(text_value(value))
    except ValueError:
        raise ShapeError("시각 글이 아니다") from None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def items(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ShapeError(f"배열이 아니다: {type(value).__name__}")
    return value


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ShapeError(f"객체가 아니다: {type(value).__name__}")
    return value


def _field(item: Mapping[str, Any], name: str) -> Any:
    if name not in item:
        raise ShapeError(f"필드가 없다: {name}")
    return item[name]


def market_code(value: Any) -> str:
    code = text_value(value)
    if MARKET_CODE.fullmatch(code) is None:
        raise ShapeError("마켓 코드의 꼴이 아니다")
    return code


def _caution(item: Mapping[str, Any]) -> bool:
    """유의·주의 표시.

    `market_event.warning`(유의)이 참이거나 `market_event.caution`의 값 가운데 하나가 참이면 참이다.
    `market_event`나 그 안의 `warning`, `caution`을 읽을 수 없으면(없음, 참·거짓이나 객체가 아님)
    표시가 있는 것으로 본다(옛 코드와 같다. 모르면 조심하는 쪽).
    """
    event = item.get("market_event")
    if not isinstance(event, Mapping):
        return True
    warning = event.get("warning")
    caution = event.get("caution")
    if not isinstance(warning, bool) or not isinstance(caution, Mapping):
        return True
    return warning or any(flag is not False for flag in caution.values())


def to_market(value: Any) -> Market:
    """`market/all`(`is_details=true`)의 항목 하나."""
    item = _mapping(value)
    return Market(
        market=market_code(_field(item, "market")),
        name=text_value(_field(item, "korean_name")),
        caution=_caution(item),
    )


def to_markets(data: Any) -> tuple[Market, ...]:
    """코인 목록 응답. 원화 마켓만 받은 차례대로 준다."""
    markets = (to_market(item) for item in items(data))
    return tuple(m for m in markets if m.market.startswith(MARKET_PREFIX))


def _single(data: Any, market: str, what: str) -> Mapping[str, Any]:
    """마켓 하나로 부른 응답의 항목. 꼭 하나이고 그 마켓의 것이어야 한다."""
    entries = items(data)
    if len(entries) != 1:
        raise ShapeError(f"{what} 응답의 항목이 하나가 아니다")
    item = _mapping(entries[0])
    if market_code(_field(item, "market")) != market:
        raise ShapeError(f"다른 마켓의 {what}다")
    return item


def _positive(value: Any) -> Decimal:
    number = decimal_value(value)
    if number <= 0:
        raise ShapeError("0보다 큰 값이 아니다")
    return number


def to_best_prices(data: Any, market: str) -> tuple[Decimal, Decimal]:
    """호가 응답(`/v1/orderbook`, 마켓 하나)의 첫 줄: (최우선 매도 호가, 최우선 매수 호가).

    M3 명세 3.6. 값은 0보다 큰 유한한 `Decimal`이어야 한다.
    """
    item = _single(data, market, "호가")
    units = items(_field(item, "orderbook_units"))
    if not units:
        raise ShapeError("호가가 비어 있다")
    first = _mapping(units[0])
    return _positive(_field(first, "ask_price")), _positive(_field(first, "bid_price"))


def to_tick_size(data: Any, market: str) -> Decimal:
    """호가 정책 응답(`/v1/orderbook/instruments`, 마켓 하나)의 `tick_size`(M3 명세 3.6)."""
    return _positive(_field(_single(data, market, "호가 정책"), "tick_size"))


def to_minute_candle(value: Any) -> MinuteCandle:
    """1분봉 응답의 항목 하나. 봉 받기와 같은 필드 이름과 변환이다(M1 명세 3.4)."""
    item = _mapping(value)
    return MinuteCandle(
        market=market_code(_field(item, "market")),
        start=utc_from_naive(_field(item, "candle_date_time_utc")),
        high=decimal_value(_field(item, "high_price")),
        low=decimal_value(_field(item, "low_price")),
        close=decimal_value(_field(item, "trade_price")),
    )


def to_minute_candles(data: Any) -> tuple[MinuteCandle, ...]:
    return tuple(to_minute_candle(item) for item in items(data))


def to_price(data: Any, market: str) -> Decimal:
    """현재가 응답(`/v1/ticker`, 마켓 하나). 그 마켓의 최근 체결가(`trade_price`)다(M2 명세 3.6).

    항목이 꼭 하나이고 그 마켓의 것이어야 한다. 값은 0보다 큰 유한한 `Decimal`이어야 한다.
    """
    entries = items(data)
    if len(entries) != 1:
        raise ShapeError("현재가 응답의 항목이 하나가 아니다")
    item = _mapping(entries[0])
    if market_code(_field(item, "market")) != market:
        raise ShapeError("다른 마켓의 현재가다")
    price = decimal_value(_field(item, "trade_price"))
    if price <= 0:
        raise ShapeError("현재가가 0보다 크지 않다")
    return price


def to_prices(data: Any, markets: tuple[str, ...]) -> dict[str, Decimal]:
    """여러 마켓의 현재가 응답(`/v1/ticker`, 마켓 여럿. M5 명세 3.15.2).

    물은 마켓마다 항목이 꼭 하나씩 있어야 한다(빠지거나, 겹치거나, 묻지 않은 마켓이면
    `ShapeError`). 값은 그 마켓의 최근 체결가(`trade_price`)이고 0보다 큰 유한한 `Decimal`이다.
    """
    wanted = set(markets)
    prices: dict[str, Decimal] = {}
    for value in items(data):
        item = _mapping(value)
        market = market_code(_field(item, "market"))
        if market in prices:
            raise ShapeError("겹친 마켓의 현재가다")
        prices[market] = _positive(_field(item, "trade_price"))
    if set(prices) != wanted:
        raise ShapeError("물은 마켓과 받은 마켓이 다르다")
    return prices


def to_candle(value: Any, unit: CandleUnit) -> Candle:
    """봉 응답의 항목 하나."""
    item = _mapping(value)
    return Candle(
        market=market_code(_field(item, "market")),
        unit=unit,
        start=utc_from_naive(_field(item, "candle_date_time_utc")),
        open=decimal_value(_field(item, "opening_price")),
        high=decimal_value(_field(item, "high_price")),
        low=decimal_value(_field(item, "low_price")),
        close=decimal_value(_field(item, "trade_price")),
        volume=decimal_value(_field(item, "candle_acc_trade_volume")),
        value=decimal_value(_field(item, "candle_acc_trade_price")),
    )


def to_candles(data: Any, unit: CandleUnit) -> tuple[Candle, ...]:
    """봉 응답. 받은 차례 그대로다(차례를 맞추는 것은 부르는 쪽이 한다)."""
    return tuple(to_candle(item, unit) for item in items(data))
