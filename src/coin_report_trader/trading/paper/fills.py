"""모의 체결(M3 명세 3.7.2)과 켤 때·멈춤을 풀 때의 소급 판정의 셈(3.7.4).

- 계산만 한다. 시세를 부르지 않는다(3.2). 최우선 호가와 1분봉은 `app`이 `QuoteClient`로 읽어
  넘긴다.
- 넣을 때(`fill_on_place`): 지정가 매수의 가격이 최우선 매도 호가 이상이면 최우선 매도 호가에,
  지정가 매도의 가격이 최우선 매수 호가 이하이면 최우선 매수 호가에 바로 체결된다. 시장가
  (손절, 긴급 매도)는 최우선 매수 호가에 체결된다(3단계의 시장가는 매도뿐이다). 수량은 늘
  전부다(모의매매에는 부분 체결이 없다. 9절).
- 걸린 뒤(`judge_fills`): 지정가 매수는 1분봉의 저가 < 지정가, 지정가 매도는 1분봉의 고가 >
  지정가일 때만 체결이다(닿기만 한 것은 체결이 아니다). 체결 가격은 지정가, 체결 때는 그 1분봉의
  끝(시작 + 1분)이다. 보는 1분봉은 그 주문의 `judged_until_utc`(마지막으로 본 1분봉의 끝. 처음에는
  건 때를 올림한 분) 뒤에 시작한 것이고, 건 때를 올림한 분보다 앞의 봉은 보지 않는다.
- 소급 판정(3.7.4)은 같은 규칙이다. 걸린 주문마다 `judge_window`가 준 구간(마지막으로 본 1분봉
  뒤부터 지금 바로 앞의 완성된 1분봉까지)을 받아 `judge_fills`에 넘긴다. 꺼져 있던 구간이 길어도
  구간을 줄이지 않는다.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from coin_report_trader.core.ports import MinuteCandle
from coin_report_trader.core.trade import OrderKind, OrderRecord, OrderSide, OrderState

MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class PendingOrder:
    """판정할 걸린 주문: 주문의 줄과 그 줄의 `orders.judged_until_utc`(3.12)."""

    order: OrderRecord
    judged_until_utc: datetime


@dataclass(frozen=True)
class Fill:
    """모의 체결 하나. 모의매매는 주문 하나에 체결 하나다(3.7.2)."""

    order_id: int
    market: str
    side: OrderSide
    price: Decimal
    qty: Decimal
    filled_utc: datetime
    at_place: bool
    """넣을 때 바로 체결되었거나 시장가이면 참(묶음이 없었다). 걸린 뒤의 체결이면 거짓."""


def floor_minute(moment: datetime) -> datetime:
    """그 분의 처음(초 아래를 버림)."""
    _check_aware(moment)
    return moment.replace(second=0, microsecond=0)


def ceil_minute(moment: datetime) -> datetime:
    """그 때를 올림한 분. 이미 분의 처음이면 그대로다."""
    start = floor_minute(moment)
    return start if start == moment else start + MINUTE


def first_judged_until(created_utc: datetime) -> datetime:
    """새 주문의 `judged_until_utc`: 건 때를 올림한 분(3.7.2)."""
    return ceil_minute(created_utc)


def judge_window(judged_until_utc: datetime, now: datetime) -> tuple[datetime, datetime] | None:
    """판정에 받을 1분봉의 구간 `(since, until)`(3.7.2, 3.7.4). 그 사이에 시작한 봉을 본다.

    `since`는 마지막으로 본 1분봉의 끝, `until`은 지금 분의 처음이다(그 앞에 시작한 봉은 모두
    완성되었다. 지금 분 안의 체결은 보지 않는다). 볼 봉이 없으면 `None`이다. 판정한 뒤 체결되지 않은
    주문의 `judged_until_utc`는 `until`이다.
    """
    _check_aware(judged_until_utc)
    until = floor_minute(now)
    if judged_until_utc >= until:
        return None
    return judged_until_utc, until


def fill_on_place(
    order: OrderRecord, best_ask: Decimal, best_bid: Decimal, now: datetime
) -> Fill | None:
    """넣을 때의 바로 체결과 시장가(3.7.2). 바로 체결되지 않는 지정가는 `None`(걸린다)."""
    _check_open(order)
    _check_aware(now)
    if best_ask <= 0 or best_bid <= 0:
        raise ValueError("최우선 호가는 0보다 커야 한다")
    if order.kind is OrderKind.MARKET:
        if order.side is OrderSide.BUY:
            raise ValueError("3단계의 시장가는 매도뿐이다")
        return _fill(order, best_bid, now, at_place=True)
    price = _limit_price(order)
    if order.side is OrderSide.BUY and price >= best_ask:
        return _fill(order, best_ask, now, at_place=True)
    if order.side is OrderSide.SELL and price <= best_bid:
        return _fill(order, best_bid, now, at_place=True)
    return None


def judge_fills(
    orders: Iterable[PendingOrder], minutes: Iterable[MinuteCandle]
) -> tuple[Fill, ...]:
    """걸린 지정가 주문마다 1분봉으로 체결을 판정한다(3.7.2, 3.7.4).

    `minutes`는 완성된 1분봉이어야 한다(`judge_window`의 구간으로 받은 것). 여러 코인의 봉이
    섞여 있어도 되고, 주문마다 그 코인의 봉만 본다. 결과는 체결 때 순(같으면 주문 번호 순)이다.
    """
    candles: dict[str, list[MinuteCandle]] = {}
    for candle in minutes:
        candles.setdefault(candle.market, []).append(candle)
    for group in candles.values():
        group.sort(key=lambda candle: candle.start)
    fills = []
    for pending in orders:
        fill = _judge_one(pending, candles.get(pending.order.market, ()))
        if fill is not None:
            fills.append(fill)
    return tuple(sorted(fills, key=lambda fill: (fill.filled_utc, fill.order_id)))


def _judge_one(pending: PendingOrder, candles: Sequence[MinuteCandle]) -> Fill | None:
    order = pending.order
    _check_open(order)
    price = _limit_price(order)
    _check_aware(pending.judged_until_utc)
    since = max(pending.judged_until_utc, first_judged_until(order.created_utc))
    for candle in candles:
        if candle.start < since:
            continue
        crossed = candle.low < price if order.side is OrderSide.BUY else candle.high > price
        if crossed:
            return _fill(order, price, candle.start + MINUTE, at_place=False)
    return None


def _fill(order: OrderRecord, price: Decimal, when: datetime, *, at_place: bool) -> Fill:
    return Fill(
        order_id=order.order_id,
        market=order.market,
        side=order.side,
        price=price,
        qty=order.qty,
        filled_utc=when,
        at_place=at_place,
    )


def _check_open(order: OrderRecord) -> None:
    if order.state is not OrderState.OPEN:
        raise ValueError("걸린 주문만 판정한다")
    if order.qty <= 0:
        raise ValueError("수량은 0보다 커야 한다")


def _limit_price(order: OrderRecord) -> Decimal:
    if order.kind is not OrderKind.LIMIT or order.price is None:
        raise ValueError("걸린 뒤의 판정은 지정가 주문만 한다")
    if order.price <= 0:
        raise ValueError("가격은 0보다 커야 한다")
    return order.price


def _check_aware(moment: datetime) -> None:
    if not isinstance(moment, datetime) or moment.utcoffset() is None:
        raise ValueError("시각은 시간대가 붙은 datetime이어야 한다")
