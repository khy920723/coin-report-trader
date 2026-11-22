"""모의 체결(M3 명세 3.7.2)과 소급 판정의 셈(3.7.4)의 시험.

1분봉은 가짜 시세(`tests/fakes/quote.py`)의 `make_minutes`나 만든 숫자로 만든다. 3.7.2의 예를 모두
넣는다.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fakes.clock import FakeClock
from fakes.quote import FakeQuoteSource, make_minutes

from coin_report_trader.core.alerts import Mode
from coin_report_trader.core.ports import MinuteCandle
from coin_report_trader.core.trade import (
    OrderKind,
    OrderReason,
    OrderRecord,
    OrderSide,
    OrderState,
)
from coin_report_trader.trading.paper import (
    Fill,
    PendingOrder,
    apply_fill,
    ceil_minute,
    fill_on_place,
    first_judged_until,
    floor_minute,
    judge_fills,
    judge_window,
    lock_order,
    new_account,
)
from coin_report_trader.trading.quote import QuoteClient

M = "KRW-AAA"
N = "KRW-BBB"
D = Decimal


def at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 1, 5, hour, minute, second, tzinfo=UTC)


def order(
    side: OrderSide,
    price: str | None,
    *,
    qty: str = "1",
    kind: OrderKind = OrderKind.LIMIT,
    order_id: int = 1,
    market: str = M,
    created: datetime | None = None,
) -> OrderRecord:
    return OrderRecord(
        order_id=order_id,
        uid=f"uid-{order_id}",
        mode=Mode.PAPER,
        round_id=7,
        market=market,
        side=side,
        kind=kind,
        reason=OrderReason.JUDGMENT if kind is OrderKind.LIMIT else OrderReason.SELL_ALL,
        price=None if price is None else D(price),
        qty=D(qty),
        state=OrderState.OPEN,
        created_utc=created or at(10, 3, 20),
        closed_utc=None,
        error_name=None,
    )


def candle(start: datetime, high: str, low: str, market: str = M) -> MinuteCandle:
    return MinuteCandle(market=market, start=start, high=D(high), low=D(low), close=D(low))


def pending(o: OrderRecord, judged_until: datetime | None = None) -> PendingOrder:
    return PendingOrder(o, judged_until or first_judged_until(o.created_utc))


# --- 넣을 때 ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("price", "expected"),
    [("1001", D(1001)), ("1005", D(1001)), ("1000", None)],
)
def test_limit_buy_at_or_above_the_best_ask_fills_at_the_best_ask(
    price: str, expected: Decimal | None
) -> None:
    o = order(OrderSide.BUY, price, qty="3")
    now = at(10, 3, 20)
    fill = fill_on_place(o, best_ask=D(1001), best_bid=D(999), now=now)
    if expected is None:
        assert fill is None  # 걸린다
    else:
        assert fill == Fill(1, M, OrderSide.BUY, expected, D(3), now, at_place=True)


@pytest.mark.parametrize(
    ("price", "expected"),
    [("999", D(999)), ("995", D(999)), ("1000", None)],
)
def test_limit_sell_at_or_below_the_best_bid_fills_at_the_best_bid(
    price: str, expected: Decimal | None
) -> None:
    o = order(OrderSide.SELL, price, qty="2")
    fill = fill_on_place(o, best_ask=D(1001), best_bid=D(999), now=at(10, 3, 20))
    if expected is None:
        assert fill is None
    else:
        assert fill is not None
        assert (fill.price, fill.qty, fill.at_place) == (expected, D(2), True)


def test_market_sell_fills_at_the_best_bid_for_all_qty() -> None:
    o = order(OrderSide.SELL, None, qty="5", kind=OrderKind.MARKET)
    fill = fill_on_place(o, best_ask=D(1001), best_bid=D(999), now=at(10, 3, 20))
    assert fill == Fill(1, M, OrderSide.SELL, D(999), D(5), at(10, 3, 20), at_place=True)


def test_market_buy_and_bad_prices_are_refused() -> None:
    with pytest.raises(ValueError, match="시장가는 매도뿐"):
        fill_on_place(order(OrderSide.BUY, None, kind=OrderKind.MARKET), D(1001), D(999), at(10, 4))
    with pytest.raises(ValueError, match="최우선 호가"):
        fill_on_place(order(OrderSide.BUY, "1000"), D(0), D(999), at(10, 4))
    with pytest.raises(ValueError, match="걸린 주문"):
        fill_on_place(
            replace(order(OrderSide.BUY, "1000"), state=OrderState.CANCELED),
            D(1001),
            D(999),
            at(10, 4),
        )
    with pytest.raises(ValueError, match="시간대"):
        fill_on_place(order(OrderSide.BUY, "1000"), D(1001), D(999), datetime(2026, 1, 5))


# --- 걸린 뒤 ----------------------------------------------------------------------------------


def test_buy_example_of_3_7_2() -> None:
    # 10:03:20에 990에 매수를 걸었다. 10:04의 저가가 990이면 체결이 아니고, 10:05의 저가가 989이면
    # 990에 체결되고 체결 때는 10:06:00이다.
    o = order(OrderSide.BUY, "990", created=at(10, 3, 20))
    assert first_judged_until(o.created_utc) == at(10, 4)
    minutes = [candle(at(10, 4), "995", "990"), candle(at(10, 5), "995", "989")]
    assert judge_fills([pending(o)], minutes[:1]) == ()
    fills = judge_fills([pending(o)], minutes)
    assert fills == (Fill(1, M, OrderSide.BUY, D(990), D(1), at(10, 6), at_place=False),)


def test_sell_fills_only_when_the_high_is_above_the_price() -> None:
    o = order(OrderSide.SELL, "1010", qty="4", created=at(10, 3, 20))
    touched = [candle(at(10, 4), "1010", "1000")]
    assert judge_fills([pending(o)], touched) == ()
    crossed = [*touched, candle(at(10, 5), "1011", "1000")]
    (fill,) = judge_fills([pending(o)], crossed)
    assert (fill.price, fill.qty, fill.filled_utc, fill.at_place) == (
        D(1010),
        D(4),
        at(10, 6),
        False,
    )


def test_candles_before_the_order_are_not_counted() -> None:
    # 10:03의 봉(건 때 10:03:20이 든 분)은 건 때보다 앞의 체결이 섞이므로 보지 않는다.
    o = order(OrderSide.BUY, "990", created=at(10, 3, 20))
    early = [candle(at(10, 2), "995", "980"), candle(at(10, 3), "995", "980")]
    assert judge_fills([pending(o)], early) == ()
    # judged_until을 앞으로 주어도 건 때를 올림한 분보다 앞은 보지 않는다.
    assert judge_fills([pending(o, at(10, 0))], early) == ()


def test_an_order_placed_on_the_minute_sees_that_minute() -> None:
    o = order(OrderSide.BUY, "990", created=at(10, 3))
    assert first_judged_until(o.created_utc) == at(10, 3)
    (fill,) = judge_fills([pending(o)], [candle(at(10, 3), "995", "989")])
    assert fill.filled_utc == at(10, 4)


def test_candles_already_judged_are_not_seen_again() -> None:
    o = order(OrderSide.BUY, "990", created=at(10, 3, 20))
    minutes = [candle(at(10, 4), "995", "985"), candle(at(10, 5), "995", "991")]
    # 10:04의 봉까지 이미 보았다(judged_until 10:05).
    assert judge_fills([pending(o, at(10, 5))], minutes) == ()


def test_the_first_crossing_candle_decides_the_time() -> None:
    o = order(OrderSide.BUY, "990", created=at(10, 3, 20))
    minutes = [
        candle(at(10, 7), "995", "980"),
        candle(at(10, 5), "995", "989"),  # 차례가 섞여 와도 시작 시각 순으로 본다
        candle(at(10, 6), "995", "985"),
    ]
    (fill,) = judge_fills([pending(o)], minutes)
    assert fill.filled_utc == at(10, 6)


def test_each_order_sees_only_its_coin_and_results_come_in_time_order() -> None:
    a = order(OrderSide.BUY, "990", order_id=1, created=at(10, 3, 20))
    b = order(OrderSide.SELL, "120", order_id=2, market=N, created=at(10, 1, 5))
    c = order(OrderSide.BUY, "980", order_id=3, created=at(10, 3, 30))
    minutes = [
        candle(at(10, 5), "995", "989"),  # a는 10:06에 체결, c는 아니다
        candle(at(10, 2), "121", "110", market=N),  # b는 10:03에 체결
        candle(at(10, 4), "130", "100", market=N),
    ]
    fills = judge_fills([pending(a), pending(b), pending(c)], minutes)
    assert [(f.order_id, f.filled_utc) for f in fills] == [(2, at(10, 3)), (1, at(10, 6))]


def test_same_time_fills_are_in_order_id_order() -> None:
    a = order(OrderSide.BUY, "990", order_id=5)
    b = order(OrderSide.BUY, "995", order_id=2)
    fills = judge_fills([pending(a), pending(b)], [candle(at(10, 4), "999", "980")])
    assert [f.order_id for f in fills] == [2, 5]


def test_only_open_limit_orders_are_judged() -> None:
    with pytest.raises(ValueError, match="지정가"):
        judge_fills([pending(order(OrderSide.SELL, None, kind=OrderKind.MARKET))], [])
    with pytest.raises(ValueError, match="걸린 주문"):
        judge_fills([pending(replace(order(OrderSide.BUY, "990"), state=OrderState.FILLED))], [])
    with pytest.raises(ValueError, match="시간대"):
        judge_fills([PendingOrder(order(OrderSide.BUY, "990"), datetime(2026, 1, 5))], [])


def test_a_fill_found_later_settles_against_its_lock() -> None:
    # 걸린 뒤에 체결된 지정가 매수는 묶은 원화로 산다(3.7.1과 3.7.2를 잇는 예).
    o = order(OrderSide.BUY, "990", qty="10")
    account = lock_order(new_account(D(100_000)), o)
    (fill,) = judge_fills([pending(o)], [candle(at(10, 5), "995", "989")])
    settled = apply_fill(account, o, fill)
    assert settled.account.locked_krw == 0
    assert settled.account.krw == D("90095.05")


# --- 판정의 구간(3.7.4) ------------------------------------------------------------------------


def test_minute_helpers() -> None:
    assert floor_minute(at(10, 3, 20)) == at(10, 3)
    assert ceil_minute(at(10, 3, 20)) == at(10, 4)
    assert ceil_minute(at(10, 3)) == at(10, 3)
    assert ceil_minute(at(10, 3).replace(microsecond=1)) == at(10, 4)
    kst = at(10, 3, 20).astimezone(timezone(timedelta(hours=9)))
    assert ceil_minute(kst) == at(10, 4)
    with pytest.raises(ValueError, match="시간대"):
        floor_minute(datetime(2026, 1, 5, 10, 3))


def test_judge_window_is_from_judged_until_to_the_start_of_now() -> None:
    assert judge_window(at(10, 4), at(10, 9, 30)) == (at(10, 4), at(10, 9))
    # 지금 분 안은 보지 않는다: 볼 완성된 봉이 없으면 None이다.
    assert judge_window(at(10, 9), at(10, 9, 59)) is None
    assert judge_window(at(10, 10), at(10, 9, 59)) is None
    with pytest.raises(ValueError, match="시간대"):
        judge_window(datetime(2026, 1, 5, 10, 4), at(10, 9))


def test_retro_judgment_over_a_long_time_off_is_not_shortened() -> None:
    # 켤 때의 소급 판정: 꺼져 있던 5시간(1분봉 300개)을 줄이지 않고 받아 판정한다.
    clock = FakeClock(at(15, 3, 40))
    source = FakeQuoteSource(prices={M: D(1000)}, clock=clock)
    candles = make_minutes(M, 400, last_start=at(15, 2))  # 8:23~15:02, 저가는 998 이상
    late = replace(candles[-5], low=D(989))  # 14:58에 처음 990 아래
    source.put_minutes(*candles[:-5], late, *candles[-4:])
    client = QuoteClient(source, clock)
    o = order(OrderSide.BUY, "990", created=at(10, 3, 20))
    window = judge_window(first_judged_until(o.created_utc), clock.now())
    assert window == (at(10, 4), at(15, 3))
    since, until = window
    minutes = client.fetch_minutes(M, since=since, until=until)
    assert len(minutes) == 299  # 10:04~15:02
    (fill,) = judge_fills([pending(o)], minutes)
    assert (fill.price, fill.filled_utc) == (D(990), at(14, 59))


def test_unfilled_orders_move_judged_until_to_the_window_end() -> None:
    # 판정한 뒤 체결되지 않은 주문은 judged_until을 구간의 끝으로 두고, 다음 판정은 그 뒤만 본다.
    o = order(OrderSide.BUY, "990", created=at(10, 3, 20))
    window = judge_window(first_judged_until(o.created_utc), at(10, 6, 10))
    assert window is not None
    _, until = window
    assert judge_fills([pending(o)], [candle(at(10, 4), "995", "991")]) == ()
    later = [candle(at(10, 4), "995", "980"), candle(at(10, 6), "995", "989")]
    (fill,) = judge_fills([pending(o, until)], later)
    assert fill.filled_utc == at(10, 7)


def test_judgment_before_a_cancel_uses_the_same_rule() -> None:
    # 취소하기 전의 판정: 지금 바로 앞의 완성된 1분봉까지 보고, 체결이면 취소하지 않는다.
    o = order(OrderSide.SELL, "1010", created=at(10, 3, 20))
    window = judge_window(first_judged_until(o.created_utc), at(10, 5, 40))
    assert window == (at(10, 4), at(10, 5))  # 지금 분(10:05)의 봉은 보지 않는다
    (fill,) = judge_fills([pending(o)], [candle(at(10, 4), "1011", "1000")])
    assert fill.filled_utc == at(10, 5)
