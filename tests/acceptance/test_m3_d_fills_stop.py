"""M3 명세 6절 D11~D19, D30, D33: 모의 체결, 평균 매수가와 손익, 손절, 디스코드 매매 알림.

자료 스레드, 보내기 스레드, 가짜 실행기, 가짜 시세(호가, 호가 단위, 1분봉), 가짜 디스코드, 가짜
시계로 본다. 거래소와 진짜 `claude`를 부르지 않는다. 값은 모두 만든 값이다.
"""

import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from acceptance.m2_support import restart, webhook_url
from acceptance.m3_d_support import (
    AAA,
    D_PRICE,
    PARTS,
    Rig,
    account,
    advance,
    all_codes,
    answer,
    coin_view,
    d_rig,
    fills,
    holding_seed,
    order_seed,
    orders,
    seeds,
    trade_fill_seed,
)
from fakes.ai import answered, failed, good_answers

from coin_report_trader.core import (
    AlertCategory,
    MinuteCandle,
    OrderKind,
    OrderSide,
    PerformanceView,
    QuoteError,
)
from coin_report_trader.core.texts import (
    ERROR_NETWORK,
    STOP_ONLY_LEFT,
)

D = Decimal
FEE = D("0.0005")
BOOK = "book"


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Callable[..., Rig]]:
    rigs: list[Rig] = []

    def factory(**kwargs: Any) -> Rig:
        rig = d_rig(tmp_path / f"r{len(rigs)}", **kwargs)
        rigs.append(rig)
        return rig

    yield factory
    for rig in rigs:
        rig.stop()


def reports(rig: Rig) -> list[tuple[Any, ...]]:
    return rig.rows("SELECT state, order_id, no_order_reason FROM reports ORDER BY id")


def minute(start: datetime, low: str) -> MinuteCandle:
    """1분봉(고가와 종가는 저가에서 만든 값)."""
    return MinuteCandle(AAA, start, D(low) + 10, D(low), D(low) + 5)


# --- D11: 지정가 매수의 소급 체결(저가가 가격 아래로 내려가야 한다) -------------------------------


@pytest.mark.scenario("D11")
def test_d11_a_resting_buy_fills_at_its_price_when_a_later_minute_low_goes_below_it(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    answer(rig, direction="오를 것", buy_price="990")
    rig.round()  # 04:00:10에 걸림(그 뒤의 첫 1분봉은 04:01)
    (placed,) = orders(rig)
    assert placed[4] == "open"
    assert fills(rig) == []
    krw_before = account(rig).krw
    first = datetime(2026, 1, 5, 4, 1, tzinfo=UTC)
    rig.quote.put_minutes(minute(first, "990"), minute(first + timedelta(minutes=1), "989"))
    # 04:01 봉은 저가가 990(가격과 같음)이라 체결이 아니다. 04:02 봉이 끝나는 04:03:00 뒤에 체결된다
    advance(rig, 90 - 10)  # 04:01:30: 첫 봉도 아직 끝나기 전이 아니라 끝난 뒤다
    assert fills(rig) == []
    advance(rig, 60)  # 04:02:30: 첫 봉(04:01)만 끝남. 저가 990은 체결이 아니다
    assert fills(rig) == []
    advance(rig, 60)  # 04:03:30: 둘째 봉(저가 989)이 끝났다
    ((market, side, price, qty, _fee, _pnl, filled_at),) = fills(rig)
    assert (market, side, D(price)) == (AAA, "buy", D(990))
    assert filled_at == "2026-01-05T04:03:00Z"  # 체결 때는 봉이 끝나는 때
    assert D(qty) == D("20.20202020")
    assert orders(rig)[0][4] == "filled"
    assert all_codes(rig).count("A-28") == 1
    cost = D(990) * D(qty) * (1 + FEE)
    held = account(rig)
    assert held.locked_krw == 0  # 걸 때 묶인 원화가 체결 때 쓰인다
    assert D(1_000_000) - held.krw == cost  # 가상 원화가 990 × 수량 × 1.0005만큼 준다
    assert krw_before == D(1_000_000)  # 걸 때는 묶이기만 하고 가상 원화는 그대로다


# --- D12: 넣을 때 바로 체결 -----------------------------------------------------------------------


@pytest.mark.scenario("D12")
def test_d12_a_buy_at_or_above_the_best_ask_fills_at_once_at_the_ask(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.quote.set_book(AAA, D(1001), D(999))
    rig.start()
    answer(rig, direction="오를 것", buy_price="1005")
    rig.round()
    (order,) = orders(rig)
    assert (order[1], order[2], order[4], D(order[5])) == ("buy", "limit", "filled", D(1005))
    ((_market, side, price, qty, _fee, _pnl, _filled_at),) = fills(rig)
    assert (side, D(price), D(qty)) == ("buy", D(1001), D("19.90049751"))  # 20,000 ÷ 1,005
    assert all_codes(rig).count("A-27") == 1
    assert all_codes(rig).count("A-28") == 1
    assert account(rig).locked_krw == 0  # 바로 체결이라 묶이지 않았다


# --- D13: 평균 매수가와 실현 손익, 모의 성적 ------------------------------------------------------


@pytest.mark.scenario("D13")
def test_d13_the_average_buy_price_the_realized_pnl_and_the_paper_performance_follow_the_fills(
    build: Callable[..., Rig],
) -> None:
    at = datetime(2026, 1, 5, 1, tzinfo=UTC)
    rig = build(
        seed=seeds(
            trade_fill_seed(AAA, OrderSide.BUY, "1000", "10", at=at),
            trade_fill_seed(AAA, OrderSide.BUY, "1100", "10", at=at + timedelta(minutes=1)),
            trade_fill_seed(AAA, OrderSide.SELL, "1020", "5", at=at + timedelta(minutes=2)),
        )
    )
    rig.start()
    pnl = D(5) * (D(1020) * (1 - FEE) - D(1050) * (1 + FEE))
    assert pnl == D("-155.175")
    sells = [row for row in fills(rig) if row[1] == "sell"]
    assert [D(row[5]) for row in sells] == [pnl]
    (holding,) = account(rig).holdings
    assert (holding.qty, holding.avg_price) == (D(15), D(1050))  # 매도 뒤에도 1,050
    view = rig.sink.of(PerformanceView)[-1]
    coin = next(row for row in view.rows if row.market == AAA)
    assert coin.realized_pnl == pnl
    assert (coin.sells, coin.wins) == (1, 0)
    total = view.rows[-1]
    assert total.market is None
    assert total.realized_pnl == pnl
    krw = account(rig).krw
    spent = (D(10_000) + D(11_000)) * (1 + FEE)
    assert krw == D(1_000_000) - spent + D(5_100) * (1 - FEE)
    assert total.asset_change is not None
    assert total.asset_change == (krw + D(15) * D_PRICE) / D(1_000_000) - 1
    # 평가 손익: 15 × (1,000 × (1 − f) − 1,050 × (1 + f))
    assert coin.unrealized_pnl == D(15) * (D(1000) * (1 - FEE) - D(1050) * (1 + FEE))


# --- D14: 손절선 경계와 손절 매도 -----------------------------------------------------------------


@pytest.mark.scenario("D14")
def test_d14_the_stop_loss_sells_everything_at_market_one_order_after_canceling_program_orders(
    build: Callable[..., Rig],
) -> None:
    held = holding_seed(AAA, "10", "980")  # 손절선 980 × 0.95 = 931
    open_sell = order_seed(AAA, OrderSide.SELL, OrderKind.LIMIT, D(1100), "4")
    rig = build(seed=seeds(held, open_sell))
    rig.quote.set_book(AAA, D(932), D(930))
    rig.start()
    rig.quote.set_prices({AAA: D(932)})
    advance(rig, 60)
    assert [row[4] for row in orders(rig)] == ["open"]  # 932는 손절선 위
    rig.quote.set_prices({AAA: D(931)})
    advance(rig, 60)
    rows = orders(rig)
    assert [row[4] for row in rows] == ["canceled", "filled"]  # 프로그램 주문을 취소한 뒤
    sold = rows[1]
    assert (sold[1], sold[2], sold[3], D(sold[6])) == ("sell", "market", "stop_loss", D(10))
    ((_market, side, price, qty, _fee, _pnl, _filled_at),) = fills(rig)
    assert (side, D(price), D(qty)) == ("sell", D(930), D(10))  # 최우선 매수 호가에 한 건
    assert account(rig).holdings[0].qty == 0
    codes = all_codes(rig)
    assert codes.count("A-27") == 1
    assert codes.count("A-28") == 1


# --- D15: 회차 도중의 손절은 그 회차에서만 사지 않는다 --------------------------------------------


@pytest.mark.scenario("D15")
def test_d15_a_stop_loss_during_a_round_drops_that_coins_judgment_for_that_round_only(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=holding_seed(AAA, "10", "980"))
    rig.start()
    hold = threading.Event()
    buy = answer_result(direction="오를 것", buy_price="990")
    started = rig.runner.plan(AAA, buy, wait=hold)
    due = rig.data._next_due
    assert due is not None
    rig.clock.advance((due - rig.clock.now()).total_seconds())
    assert started.wait(10.0)  # 세션이 돌고 있다(회차 1이 도는 중)
    rig.quote.set_prices({AAA: D(930)})
    advance(rig, 60)  # 1분 점검의 손절
    assert [row[3] for row in orders(rig)] == ["stop_loss"]
    advance(rig, 5)  # 회차는 손절보다 늦게 끝난다
    hold.set()
    rig.drain()
    ((state, order_id, reason),) = reports(rig)
    assert state == "ok"
    assert order_id is None
    assert reason == "손절로 판단을 버림"
    assert [row[3] for row in orders(rig)] == ["stop_loss"]  # 회차 1에는 산 것이 없다
    rig.quote.set_prices({AAA: D_PRICE})
    answer(rig, direction="오를 것", buy_price="990", result_text="지난 결과", compared=PARTS)
    rig.round()  # 회차 2는 매수 판단대로 산다
    assert [row[3:5] for row in orders(rig)] == [("stop_loss", "filled"), ("judgment", "open")]


def answer_result(**kwargs: Any) -> Callable[[Any], Any]:
    kwargs.setdefault("predicted", "1010")
    return lambda req: answered(good_answers(AAA, **kwargs))


# --- D16: 손절선은 새 평균 매수가를 따른다 --------------------------------------------------------


@pytest.mark.scenario("D16")
def test_d16_the_stop_line_follows_the_new_average_after_an_add_buy(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=holding_seed(AAA, "10", "900"))
    rig.quote.set_book(AAA, D(990), D(989))
    rig.start()
    before = coin_view(rig).stop_line
    assert before == D(900) * D("0.95")
    answer(rig, direction="오를 것", buy_price="990")  # 990에 바로 체결되는 추가 매수
    rig.round()
    (holding,) = account(rig).holdings
    assert holding.avg_price is not None
    assert holding.avg_price != D(900)
    assert coin_view(rig).stop_line == holding.avg_price * D("0.95")


# --- D17: 팔 수 있는 수량이 작으면 팔지 않고 A-3 한 번 --------------------------------------------


@pytest.mark.scenario("D17")
def test_d17_a_holding_too_small_to_sell_is_one_a3_and_not_a_failure(
    build: Callable[..., Rig],
) -> None:
    # 현재가 1,000이 손절선 1,045 아래이고, 팔 수 있는 수량의 평가 금액은 4,000원이다
    rig = build(seed=holding_seed(AAA, "4", "1100"))
    rig.start()
    advance(rig, 60)
    assert orders(rig) == []
    assert all_codes(rig).count("A-3") == 1
    advance(rig, 60)  # 다음 점검에서 또 닿아도 A-3이 다시 생기지 않는다
    advance(rig, 60)
    assert all_codes(rig).count("A-3") == 1
    assert "A-8" not in all_codes(rig)  # 실패로 세지 않는다


# --- D18: 최우선 호가를 읽지 못한 손절 ------------------------------------------------------------


@pytest.mark.scenario("D18")
def test_d18_a_stop_loss_that_cannot_read_the_book_is_an_a8_and_sells_at_the_next_check(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=holding_seed(AAA, "10", "980"))
    rig.start()
    rig.quote.set_prices({AAA: D(930)})
    rig.quote.script(QuoteError(ERROR_NETWORK), kind=BOOK, market=AAA)
    advance(rig, 60)
    assert orders(rig) == []
    assert all_codes(rig).count("A-8") == 1
    advance(rig, 60)  # 다음 1분 점검에서 다시 판다
    assert [row[3:5] for row in orders(rig)] == [("stop_loss", "filled")]
    assert all_codes(rig).count("A-8") == 1


# --- D19: 매도 주문이 걸린 채 AI가 실패한 회차 ----------------------------------------------------


@pytest.mark.scenario("D19")
def test_d19_a_canceled_sell_with_a_failed_report_leaves_only_the_stop_loss(
    build: Callable[..., Rig],
) -> None:
    sell = order_seed(AAA, OrderSide.SELL, OrderKind.LIMIT, D(1100), "10")
    rig = build(seed=seeds(holding_seed(AAA, "10", "900"), sell))
    rig.runner.plan(AAA, failed())
    rig.start()
    rig.round()
    # 정리에서 매도가 취소되고 새 매도가 없다
    assert [row[4] for row in orders(rig)] == ["canceled"]
    (a17,) = [a for a in rig.alerts() if a.code == "A-17"]
    assert a17.text.endswith(f" {STOP_ONLY_LEFT}")
    assert account(rig).holdings[0].locked_qty == 0


# --- D30: 디스코드 매매 알림의 셋째 줄 ------------------------------------------------------------


@pytest.mark.scenario("D30")
def test_d30_the_discord_trade_alerts_have_the_amount_and_balance_on_the_third_line(
    build: Callable[..., Rig],
) -> None:
    url = webhook_url("t")
    rig = build(saved={AlertCategory.TRADE: url})
    rig.quote.set_book(AAA, D(1001), D(999))
    rig.start()
    answer(rig, direction="오를 것", buy_price="1005")
    rig.round()
    assert rig.discord.wait_for(2)
    rig.settle()
    placed, filled = rig.discord.alerts
    assert placed.url == filled.url == url
    # A-27: 주문 금액은 `OrderPlan.amount`(1,005 × 19.90049751), 잔고는 그 알림을 만든 때(체결 전)
    assert placed.text.splitlines() == [
        "[모의매매] 매매 알림",
        "AAA: 주문을 냈습니다(지정가 매수 1,005원 × 19.90049751).",
        "주문 금액 19,999원 · 계정 잔고: 원화 1,000,000원, AAA 0.0",
    ]
    # A-28: 주문 금액은 체결 가격 × 체결 수량(1,001 × 19.90049751), 잔고는 체결 뒤
    assert filled.text.splitlines() == [
        "[모의매매] 매매 알림",
        "AAA: 매수 19.90049751이 1,001원에 체결되었습니다.",
        "주문 금액 19,920원 · 계정 잔고: 원화 980,069원, AAA 19.90049751",
    ]


# --- D33: 회차 밖의 손절 뒤 첫 회차는 사지 않는다 -------------------------------------------------


def _stop_loss_outside_a_round(rig: Rig) -> None:
    """회차 1이 끝난 뒤(주문 없이) 손절이 난다."""
    rig.start()
    answer(rig, direction="오를 것", buy_price="990")  # 평균 매수가 1,000과 같아 사지 않는다
    rig.round()
    assert orders(rig) == []
    rig.quote.set_prices({AAA: D(930)})
    advance(rig, 60)
    assert [row[3] for row in orders(rig)] == ["stop_loss"]
    rig.quote.set_prices({AAA: D_PRICE})


@pytest.mark.scenario("D33")
def test_d33_the_first_round_after_an_out_of_round_stop_loss_does_not_buy_but_the_next_does(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=holding_seed(AAA, "10", "1000"))
    _stop_loss_outside_a_round(rig)
    answer(rig, direction="오를 것", buy_price="990", result_text="지난 결과", compared=PARTS)
    rig.round()  # 회차 2: 손절 뒤 첫 회차
    assert [row[3] for row in orders(rig)] == ["stop_loss"]
    assert reports(rig)[-1][2] == "손절 뒤라 새로 사지 않음"
    rig.round()  # 회차 3
    assert [row[3:5] for row in orders(rig)] == [("stop_loss", "filled"), ("judgment", "open")]


@pytest.mark.scenario("D33")
def test_d33_the_stop_loss_is_counted_from_the_saved_records_across_a_restart(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=holding_seed(AAA, "10", "1000"))
    _stop_loss_outside_a_round(rig)
    rig.stop()
    again = restart(rig)
    try:
        again.start()
        answer(again, direction="오를 것", buy_price="990", result_text="지난 결과", compared=PARTS)
        again.round()
        assert [row[3] for row in orders(again)] == ["stop_loss"]
        assert reports(again)[-1][2] == "손절 뒤라 새로 사지 않음"
    finally:
        again.stop()
