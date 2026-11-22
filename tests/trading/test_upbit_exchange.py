"""`UpbitExchange`(M5 명세 3.6.1~3.6.4)의 시험. 가짜 업비트(`tests/fakes/upbit.py`)와 가짜 시계로
한다.

9절의 "모의매매에서 생기지 않는 일" 넷(부분 체결, 취소 실패, 주문 거절, 응답 없음)과 3.6.4의 예,
3.6.1의 원화 잔고의 예를 넣는다. 값은 모두 만든 숫자다.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fakes.clock import FakeClock
from fakes.upbit import (
    CANCEL_FAILS,
    TEST_ACCESS_KEY,
    TEST_SECRET_KEY,
    FakeUpbitTransport,
    NoResponse,
    Reply,
)

from coin_report_trader.core.alerts import Mode
from coin_report_trader.core.errors import ExchangeError
from coin_report_trader.core.exchange import (
    ExchangeAccount,
    ExchangeHolding,
    ExchangeRules,
    ExchangeTrade,
)
from coin_report_trader.core.ports import ExchangePort
from coin_report_trader.core.texts import (
    ERROR_BAD_RESPONSE,
    ERROR_BLOCKED,
    ERROR_RATE_LIMIT,
    ERROR_SERVER,
    ERROR_TIMEOUT,
)
from coin_report_trader.core.trade import (
    NoOrderReason,
    OrderKind,
    OrderPlan,
    OrderReason,
    OrderRules,
    OrderSide,
    OrderState,
    PauseStopLoss,
    TradeMethod,
    TradeSettings,
)
from coin_report_trader.trading.live import (
    UpbitExchange,
    executed_qty,
    live_account_for_limits,
    live_krw_enough,
    live_realized_pnl,
    new_trades,
)
from coin_report_trader.trading.orders import BuyContext, check_buy
from coin_report_trader.trading.upbit import UpbitClient, UpbitKeys
from coin_report_trader.trading.upbit.client import UpbitChance, UpbitOrder

A = "KRW-AAA"
B = "KRW-BBB"
D = Decimal
START = datetime(2026, 1, 5, 3, 0, tzinfo=UTC)
KEYS = UpbitKeys(TEST_ACCESS_KEY, TEST_SECRET_KEY)


def setup(krw: str = "100000", coins: dict | None = None):
    clock = FakeClock(START)
    fake = FakeUpbitTransport(clock=clock)
    fake.set_account(krw=D(krw), coins=coins or {})
    fake.set_chance(A)
    fake.set_chance(B, bid_fee=D("0.001"), ask_fee=D("0.002"), min_total=D(10_000))
    exchange = UpbitExchange(UpbitClient(KEYS, fake, clock), clock)
    return exchange, fake, clock


def limit(side: OrderSide, price: str, qty: str, market: str = A) -> OrderPlan:
    return OrderPlan(
        market=market,
        side=side,
        kind=OrderKind.LIMIT,
        reason=OrderReason.JUDGMENT,
        price=D(price),
        qty=D(qty),
        amount=D(price) * D(qty),
    )


def market_sell(qty: str, market: str = A) -> OrderPlan:
    return OrderPlan(
        market=market,
        side=OrderSide.SELL,
        kind=OrderKind.MARKET,
        reason=OrderReason.STOP_LOSS,
        price=None,
        qty=D(qty),
        amount=D(qty) * D(1000),
    )


# --- 경계와 계정(3.6.1) -------------------------------------------------------------------------


def test_upbit_exchange_is_an_exchange_port_in_live_mode() -> None:
    exchange, _, _ = setup()
    assert exchange.mode is Mode.LIVE
    for name in ("read_account", "read_rules", "place", "query", "cancel", "open_orders"):
        assert callable(getattr(exchange, name))
    port: ExchangePort = exchange
    assert port.mode is Mode.LIVE
    assert repr(exchange) == "UpbitExchange()"
    with pytest.raises(TypeError, match="UpbitClient"):
        UpbitExchange(object(), FakeClock(START))  # type: ignore[arg-type]


def test_read_account_gives_krw_and_holdings_of_the_real_account() -> None:
    exchange, fake, clock = setup(coins={"AAA": (D(3), D(980)), "BBB": (D("0.5"), D(0))})
    fake.add_direct_order(A, "ask", D(1100), D(1))  # 사용자가 직접 건 매도 1개가 묶인다
    account = exchange.read_account()
    assert account == ExchangeAccount(
        krw=D(100_000),
        krw_locked=D(0),
        holdings=(
            ExchangeHolding(market=A, balance=D(2), locked=D(1), avg_buy_price=D(980)),
            ExchangeHolding(market=B, balance=D("0.5"), locked=D(0), avg_buy_price=None),
        ),
        read_utc=clock.now(),
    )


def test_read_rules_comes_from_the_order_chance() -> None:
    exchange, _, _ = setup()
    assert exchange.read_rules(B) == ExchangeRules(
        bid_fee=D("0.001"), ask_fee=D("0.002"), min_total=D(10_000), max_total=D(1_000_000_000)
    )


def test_failures_become_exchange_errors_with_the_error_name() -> None:
    exchange, fake, _ = setup()
    fake.script(Reply(500), path="/v1/accounts")
    with pytest.raises(ExchangeError) as caught:
        exchange.read_account()
    assert caught.value.error_name == ERROR_SERVER
    fake.script(Reply(418, headers={"Retry-After": "600"}), path="/v1/orders/chance")
    with pytest.raises(ExchangeError) as blocked:
        exchange.read_rules(A)
    assert blocked.value.error_name == ERROR_BLOCKED
    assert blocked.value.retry_after == pytest.approx(600)
    assert blocked.value.__context__ is None


# --- 주문 내기(3.6.2) --------------------------------------------------------------------------


def test_place_a_limit_buy_with_our_identifier() -> None:
    exchange, fake, _ = setup()
    result = exchange.place(limit(OrderSide.BUY, "990", "10"), "uid-1")
    assert (result.error_name, result.unknown) == (None, False)
    snapshot = result.snapshot
    assert snapshot is not None
    assert (snapshot.uid, snapshot.market, snapshot.side, snapshot.kind) == (
        "uid-1",
        A,
        OrderSide.BUY,
        OrderKind.LIMIT,
    )
    assert (snapshot.price, snapshot.qty, snapshot.executed_qty, snapshot.state) == (
        D(990),
        D(10),
        D(0),
        OrderState.OPEN,
    )
    assert snapshot.program is True
    sent = next(r for r in fake.requests if r.path == "/v1/orders")
    assert json.loads(sent.body or "{}")["identifier"] == "uid-1"


def test_place_a_market_sell_fills_at_once() -> None:
    exchange, fake, _ = setup(coins={"AAA": (D(6), D(1000))})
    fake.set_market_price(A, D(960))
    result = exchange.place(market_sell("6"), "uid-2")
    assert result.snapshot is not None
    assert (result.snapshot.kind, result.snapshot.state) == (OrderKind.MARKET, OrderState.FILLED)
    assert result.snapshot.executed_qty == D(6)


def test_market_buy_and_limit_without_price_are_refused() -> None:
    exchange, _, _ = setup()
    buy = replace(market_sell("1"), side=OrderSide.BUY)
    with pytest.raises(ValueError, match="매도뿐"):
        exchange.place(buy, "uid-3")
    with pytest.raises(ValueError, match="가격"):
        exchange.place(replace(limit(OrderSide.BUY, "990", "1"), price=None), "uid-4")


# --- 9절: 주문 거절 ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("plan", "name"),
    [
        (limit(OrderSide.BUY, "1000", "200"), "잔고 부족"),  # 200,100원 > 100,000원
        (limit(OrderSide.SELL, "1000", "9"), "잔고 부족"),  # 보유 5개
        (limit(OrderSide.BUY, "1000", "4"), "금액·호가 단위 거절"),  # 4,000원 < 5,000원
    ],
)
def test_rejected_orders_are_known_failures(plan: OrderPlan, name: str) -> None:
    exchange, _, _ = setup(coins={"AAA": (D(5), D(1000))})
    result = exchange.place(plan, "uid-5")
    assert (result.snapshot, result.error_name, result.unknown) == (None, name, False)


# --- 9절: 응답 없음(3.6.3) ---------------------------------------------------------------------


def test_no_response_after_the_order_was_made_is_found_by_query() -> None:
    exchange, fake, _ = setup()
    fake.script(NoResponse(sent=True), method="POST", path="/v1/orders")
    result = exchange.place(limit(OrderSide.BUY, "990", "10"), "uid-6")
    assert (result.error_name, result.unknown) == (None, False)
    assert result.snapshot is not None
    assert (result.snapshot.uid, result.snapshot.state) == ("uid-6", OrderState.OPEN)
    # 다시 보내지 않았다: 주문 요청은 하나이고, 이어서 식별값으로 조회했다.
    paths = [(r.method, r.path) for r in fake.requests]
    assert paths.count(("POST", "/v1/orders")) == 1
    assert paths[-1] == ("GET", "/v1/order")


def test_no_response_without_sending_is_a_known_failure() -> None:
    # 요청이 거래소에 닿지 않은 것이 확실하면(보내지 않음) 결과를 모르는 것이 아니다.
    # 그 주문만 실패다.
    exchange, fake, _ = setup()
    fake.script(NoResponse(sent=False), method="POST", path="/v1/orders")
    result = exchange.place(limit(OrderSide.BUY, "990", "10"), "uid-7")
    assert (result.snapshot, result.error_name, result.unknown) == (None, ERROR_TIMEOUT, False)
    assert fake.order_state("uid-7") is None
    assert [(r.method, r.path) for r in fake.requests].count(("POST", "/v1/orders")) == 1


@pytest.mark.parametrize(
    ("reply", "name"),
    [(Reply(500), ERROR_SERVER), (Reply(429), ERROR_RATE_LIMIT)],
)
def test_order_5xx_and_429_go_to_the_query(reply: Reply, name: str) -> None:
    exchange, fake, _ = setup()
    fake.script(reply, method="POST", path="/v1/orders")
    result = exchange.place(limit(OrderSide.BUY, "990", "10"), "uid-8")
    assert (result.snapshot, result.error_name, result.unknown) == (None, name, True)
    assert ("GET", "/v1/order") in [(r.method, r.path) for r in fake.requests]


def test_unreadable_order_reply_is_unknown_then_queried() -> None:
    exchange, fake, _ = setup()
    fake.script(Reply(200, message="만든 글"), method="POST", path="/v1/orders")
    result = exchange.place(limit(OrderSide.BUY, "990", "10"), "uid-9")
    assert (result.error_name, result.unknown) == (ERROR_BAD_RESPONSE, True)


def test_a_failing_query_after_no_response_is_unknown() -> None:
    exchange, fake, _ = setup()
    fake.script(NoResponse(sent=True), method="POST", path="/v1/orders")
    fake.script(Reply(500), method="GET", path="/v1/order")
    result = exchange.place(limit(OrderSide.BUY, "990", "10"), "uid-10")
    assert (result.snapshot, result.error_name, result.unknown) == (None, ERROR_TIMEOUT, True)


# --- 조회, 9절: 부분 체결(3.6.4) ---------------------------------------------------------------


def test_query_of_a_missing_identifier_is_none() -> None:
    exchange, _, _ = setup()
    assert exchange.query("uid-none") is None


def test_partial_fills_come_as_trades_with_their_fees() -> None:
    exchange, fake, _ = setup()
    exchange.place(limit(OrderSide.BUY, "990", "20", market=B), "uid-11")
    fake.fill("uid-11", D(3))
    fake.fill("uid-11", D(2), price=D(985))
    snapshot = exchange.query("uid-11")
    assert snapshot is not None
    assert (snapshot.state, snapshot.executed_qty) == (OrderState.OPEN, D(5))
    assert [(t.price, t.qty) for t in snapshot.trades] == [(D(990), D(3)), (D(985), D(2))]
    # 수수료 = 체결 금액 × 매수 수수료율 0.001
    assert [t.fee for t in snapshot.trades] == [D("2.970"), D("1.970")]
    assert executed_qty(snapshot.trades) == D(5)
    assert len({t.trade_key for t in snapshot.trades}) == 2
    fake.fill("uid-11", D(15))
    done = exchange.query("uid-11")
    assert done is not None
    assert (done.state, done.executed_qty, len(done.trades)) == (OrderState.FILLED, D(20), 3)


def test_new_trades_skip_the_ones_already_written() -> None:
    exchange, fake, _ = setup()
    exchange.place(limit(OrderSide.BUY, "990", "10"), "uid-12")
    fake.fill("uid-12", D(4))
    first = exchange.query("uid-12")
    assert first is not None
    written = [t.trade_key for t in new_trades(first, ())]
    fake.fill("uid-12", D(6))
    second = exchange.query("uid-12")
    assert second is not None
    later = new_trades(second, written)
    assert [t.qty for t in later] == [D(6)]


def test_partially_filled_then_canceled_keeps_the_filled_part() -> None:
    exchange, fake, _ = setup()
    exchange.place(limit(OrderSide.BUY, "990", "10"), "uid-13")
    fake.fill("uid-13", D(4))
    canceled = exchange.cancel("uid-13")
    assert canceled is not None
    assert (canceled.state, canceled.executed_qty) == (OrderState.CANCELED, D(4))
    after = exchange.query("uid-13")
    assert after is not None
    assert executed_qty(after.trades) == D(4)


# --- 9절: 취소 실패(3.6.4) ---------------------------------------------------------------------


def test_cancel_404_is_queried_again_and_still_open_means_unconfirmed() -> None:
    exchange, fake, _ = setup()
    exchange.place(limit(OrderSide.BUY, "990", "10"), "uid-14")
    fake.script(CANCEL_FAILS, method="DELETE", path="/v1/order")
    result = exchange.cancel("uid-14")
    assert result is not None
    assert result.state is OrderState.OPEN  # 취소가 확인되지 않은 주문(app이 다룬다)
    assert fake.order_state("uid-14") == "wait"


def test_cancel_of_a_finished_order_gives_its_final_state_and_trades() -> None:
    exchange, fake, _ = setup()
    exchange.place(limit(OrderSide.BUY, "990", "10"), "uid-15")
    fake.fill("uid-15", D(10))
    result = exchange.cancel("uid-15")
    assert result is not None
    assert (result.state, executed_qty(result.trades)) == (OrderState.FILLED, D(10))


def test_cancel_of_an_unknown_identifier_is_none() -> None:
    # 조회도 "없음"이면 보내지 않은 주문으로 닫는다(지휘가 정한 것. app이 NOT_SENT로 쓴다).
    exchange, _, _ = setup()
    assert exchange.cancel("uid-never") is None


def test_cancel_failure_other_than_404_is_an_exchange_error() -> None:
    exchange, fake, _ = setup()
    exchange.place(limit(OrderSide.BUY, "990", "10"), "uid-16")
    fake.script(Reply(500), method="DELETE", path="/v1/order")
    with pytest.raises(ExchangeError) as caught:
        exchange.cancel("uid-16")
    assert caught.value.error_name == ERROR_SERVER


# --- 체결 대기 주문 목록 -------------------------------------------------------------------------


def test_open_orders_mark_direct_orders_as_not_ours() -> None:
    exchange, fake, _ = setup(coins={"AAA": (D(5), D(1000))})
    exchange.place(limit(OrderSide.BUY, "990", "10"), "uid-17")
    direct = fake.add_direct_order(A, "ask", D(1100), D(2))
    orders = exchange.open_orders(A)
    assert [(o.uid, o.program, o.side) for o in orders] == [
        ("uid-17", True, OrderSide.BUY),
        (direct, False, OrderSide.SELL),
    ]


# --- 3.6.4의 실현 손익 ---------------------------------------------------------------------------


def test_realized_pnl_example_of_3_6_4() -> None:
    # 평균 매수가 1,000, bid_fee 0.0005, 2개를 1,100에 팔고 수수료 1.1원이면
    # 2,200 − 1.1 − 2,001 = 197.9원이다.
    trade = ExchangeTrade(trade_key="t-1", price=D(1100), qty=D(2), fee=D("1.1"), filled_utc=START)
    assert live_realized_pnl(trade, D(1000), D("0.0005")) == D("197.9")
    with pytest.raises(ValueError, match="평균 매수가"):
        live_realized_pnl(trade, D(0), D("0.0005"))


# --- 3.6.1의 원화 잔고의 예 ----------------------------------------------------------------------


def trade_settings() -> TradeSettings:
    return TradeSettings(
        budget=D(100_000),
        coin_ratios={A: D(100)},
        first_ratio=D(20),
        mr_first_ratio=None,
        add_ratio=D(15),
        tolerance=D(0),
        stop_ratio=D(5),
        daily_loss_limit=D(3000),
        order_interval_s=60,
        method=TradeMethod.TREND,
        pause_stop_loss=PauseStopLoss.KEEP,
        mr_sell_unknown_fee=False,
        mr_tolerable_loss=D(5),
        paper_funds=D(100_000),
        backup_days=7,
    )


@pytest.mark.parametrize(("krw", "buys"), [("15000", False), ("30000", True)])
def test_krw_balance_example_of_3_6_1(krw: str, buys: bool) -> None:
    # 코인 예산 100,000원, 보유 원가 0, 첫 매수 20%(20,000원)에 원화 15,000원이면 사지 않고
    # A-11이다. 원화 30,000원이면 20,000원어치를 산다.
    exchange, _, _ = setup(krw=krw)
    account = exchange.read_account()
    rules = exchange.read_rules(A)
    plan = limit(OrderSide.BUY, "1000", "20")
    assert live_krw_enough(plan, account, rules) is buys
    ctx = BuyContext(
        account=live_account_for_limits(account),
        open_orders=(),
        day_realized_pnl=D(0),
        caution=False,
        settings_check_failed=False,
        last_order_utc=None,
        unconfirmed=False,
    )
    rules_for_orders = OrderRules(tick_size=D(1), min_order=rules.min_total)
    reason = check_buy(plan, ctx, trade_settings(), rules_for_orders, START)
    assert reason is (None if buys else NoOrderReason.NO_KRW)


def test_budget_counts_real_holdings_with_the_exchange_average() -> None:
    # 보유 원가 = (balance + locked) × 거래소의 평균 매수가. 직접 건 주문에 묶인 수량도 든다.
    exchange, fake, _ = setup(krw="500000", coins={"AAA": (D(80), D(1000))})
    fake.add_direct_order(A, "ask", D(1500), D(10))
    account = live_account_for_limits(exchange.read_account())
    holding = account.holdings[0]
    assert (holding.qty, holding.avg_price) == (D(80), D(1000))
    assert holding.buy_amount_sum == D(80) * D(1000)  # 매수 원가 80,000원
    ctx = BuyContext(
        account=account,
        open_orders=(),
        day_realized_pnl=D(0),
        caution=False,
        settings_check_failed=False,
        last_order_utc=None,
        unconfirmed=False,
    )
    rules = OrderRules(tick_size=D(1), min_order=D(5000))
    assert (
        check_buy(limit(OrderSide.BUY, "1000", "20"), ctx, trade_settings(), rules, START) is None
    )
    over = limit(OrderSide.BUY, "1000", "21")
    assert check_buy(over, ctx, trade_settings(), rules, START) is NoOrderReason.COIN_BUDGET


def test_sells_never_need_krw() -> None:
    account = ExchangeAccount(krw=D(0), krw_locked=D(0), holdings=(), read_utc=START)
    rules = ExchangeRules(D("0.0005"), D("0.0005"), D(5000), None)
    assert live_krw_enough(limit(OrderSide.SELL, "1000", "5"), account, rules) is True


@pytest.mark.parametrize(("krw", "enough"), [("20010", True), ("20009.99", False)])
def test_krw_balance_must_cover_the_bid_fee_too(krw: str, enough: bool) -> None:
    # 20,000원어치 매수에는 20,000 × (1 + 0.0005) = 20,010원이 있어야 한다(같으면 된다).
    acc = ExchangeAccount(krw=D(krw), krw_locked=D(0), holdings=(), read_utc=START)
    rules = ExchangeRules(D("0.0005"), D("0.0005"), D(5000), None)
    assert live_krw_enough(limit(OrderSide.BUY, "1000", "20"), acc, rules) is enough


def test_min_total_is_the_larger_of_the_bid_and_ask_minimums() -> None:
    exchange, _, _ = setup()
    client = exchange._client  # 매수·매도의 최소가 다른 응답을 흉내 낸다
    client.order_chance = lambda market: UpbitChance(  # type: ignore[method-assign]
        market=market,
        bid_fee=D("0.0005"),
        ask_fee=D("0.0005"),
        bid_min_total=D(5000),
        ask_min_total=D(7000),
        max_total=None,
    )
    assert exchange.read_rules(A).min_total == D(7000)


def test_read_account_keeps_the_locked_krw_apart() -> None:
    # 걸린 매수 주문에 묶인 원화는 krw(주문 가능)에 들지 않고 krw_locked다(3.6.1).
    exchange, _, _ = setup(krw="100000")
    exchange.place(limit(OrderSide.BUY, "990", "10"), "uid-lock")  # 9,904.95원이 묶인다
    account = exchange.read_account()
    assert (account.krw, account.krw_locked) == (D("90095.05"), D("9904.95"))
    limits = live_account_for_limits(account)
    assert (limits.krw, limits.locked_krw) == (D("90095.05"), D(0))


def test_holding_cost_decides_the_coin_budget_and_none_average_costs_nothing() -> None:
    # 코인 예산 100,000원. 보유 원가 90,000원이 들면 20,000원어치는 넘고, 원가가 없는(평균 매수가
    # 없음) 보유는 원가 0이라 산다.
    with_cost = ExchangeAccount(
        krw=D(500_000),
        krw_locked=D(0),
        holdings=(ExchangeHolding(market=A, balance=D(90), locked=D(0), avg_buy_price=D(1000)),),
        read_utc=START,
    )
    no_avg = replace(
        with_cost,
        holdings=(ExchangeHolding(market=A, balance=D(90), locked=D(0), avg_buy_price=None),),
    )
    assert live_account_for_limits(no_avg).holdings[0].buy_amount_sum == 0
    rules = OrderRules(tick_size=D(1), min_order=D(5000))
    plan = limit(OrderSide.BUY, "1000", "20")
    reasons = []
    for acc in (with_cost, no_avg):
        ctx = BuyContext(
            account=live_account_for_limits(acc),
            open_orders=(),
            day_realized_pnl=D(0),
            caution=False,
            settings_check_failed=False,
            last_order_utc=None,
            unconfirmed=False,
        )
        reasons.append(check_buy(plan, ctx, trade_settings(), rules, START))
    assert reasons == [NoOrderReason.COIN_BUDGET, None]


def test_404_with_another_code_is_an_error_for_query_and_cancel() -> None:
    # "없음"으로 읽는 것은 404 order_not_found뿐이다(3.6.3, 3.6.4).
    exchange, fake, _ = setup()
    exchange.place(limit(OrderSide.BUY, "990", "10"), "uid-404")
    fake.script(Reply(404, code="made_up_code"), method="GET", path="/v1/order")
    with pytest.raises(ExchangeError) as queried:
        exchange.query("uid-404")
    assert queried.value.error_name.startswith("알 수 없는 거래소 오류(404")
    fake.script(Reply(404, code="made_up_code"), method="DELETE", path="/v1/order")
    with pytest.raises(ExchangeError) as canceled:
        exchange.cancel("uid-404")
    assert canceled.value.error_name.startswith("알 수 없는 거래소 오류(404")


@pytest.mark.parametrize(("state", "side"), [("made_up_state", "bid"), ("done", "made_up_side")])
def test_unknown_state_or_side_is_a_bad_response(state: str, side: str) -> None:
    # 모르는 상태나 방향을 걸린 주문으로 읽지 않는다(체결인 주문을 걸림으로 읽는 길을 막음).
    exchange, _, _ = setup()
    order = UpbitOrder(
        uuid="made-up-uuid",
        identifier="uid-odd",
        market=A,
        side=side,
        ord_type="limit",
        price=D(990),
        volume=D(1),
        executed_volume=D(0),
        state=state,
        created_at=None,
    )
    exchange._client.get_order = lambda identifier: order  # type: ignore[method-assign]
    with pytest.raises(ExchangeError) as caught:
        exchange.query("uid-odd")
    assert caught.value.error_name == ERROR_BAD_RESPONSE
