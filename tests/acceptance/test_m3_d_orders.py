"""M3 명세 6절 D1~D10, D36, D37: 회차의 판단이 주문이 되는 길(판단표, 가격, 한도, 허용 범위).

자료 스레드, 보내기 스레드, 가짜 실행기, 가짜 시세(호가, 호가 단위), 가짜 시계로 본다. 거래소와 진짜
`claude`를 부르지 않는다. 값은 모두 만든 값이다.
"""

from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from acceptance.m3_d_support import (
    AAA,
    BBB,
    HOUR,
    PARTS,
    Rig,
    account,
    advance,
    all_codes,
    answer,
    d_rig,
    d_trade,
    fill_seed,
    fills,
    holding_seed,
    order_seed,
    orders,
    seeds,
)
from fakes.quote import make_candles

from coin_report_trader.core import (
    Direction,
    Market,
    Mode,
    NoOrderReason,
    OrderKind,
    OrderSide,
    ReportState,
    ReportView,
    TradeMethod,
)
from coin_report_trader.core.trade import PAPER_FEE_RATE, OrderRules
from coin_report_trader.storage import trading as trade_store
from coin_report_trader.trading.orders import decide
from coin_report_trader.trading.paper import make_holding

TREND = TradeMethod.TREND
MR = TradeMethod.MEAN_REVERSION
D = Decimal


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Callable[..., Rig]]:
    rigs: list[Rig] = []

    def factory(**kwargs: Any) -> Rig:
        root = tmp_path / f"r{len(rigs)}"
        rig = d_rig(root, **kwargs)
        rigs.append(rig)
        return rig

    yield factory
    for rig in rigs:
        rig.stop()


def reports(rig: Rig) -> list[tuple[Any, ...]]:
    return rig.rows("SELECT state, order_id, no_order_reason FROM reports ORDER BY id")


# --- D1: 주문이 나가는 한 번 -------------------------------------------------------------------


@pytest.mark.scenario("D1")
def test_d1_a_trend_buy_is_stored_open_with_a_uid_and_the_report_points_to_it(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.quote.set_book(AAA, D(1001), D(999))
    rig.start()
    answer(rig, direction="오를 것", buy_price="990")
    rig.round()
    placed = orders(rig)
    assert [row[:5] for row in placed] == [(AAA, "buy", "limit", "judgment", "open")]
    assert (D(placed[0][5]), D(placed[0][6])) == (D(990), D("20.20202020"))  # 20,000 ÷ 990
    (uid,) = [row[0] for row in rig.rows("SELECT uid FROM orders")]
    assert len(uid) == 36  # 식별값
    ((state, order_id, reason),) = reports(rig)
    assert state == "ok"
    assert reason is None
    assert order_id == rig.rows("SELECT id FROM orders")[0][0]
    codes = all_codes(rig)
    assert codes.count("A-27") == 1
    assert "A-28" not in codes
    # 주문 전에 사용자가 누르는 것이 없다: 이 틀은 화면을 거치지 않고 자료 스레드가 혼자 낸다
    assert account(rig).locked_krw == D(990) * D("20.20202020") * D("1.0005")


# --- D2: 판단표의 여덟 줄 ------------------------------------------------------------------------

HELD = ("10", "900")  # 보유 10개, 평균 매수가 900(현재가 1,000이 더 높다)
HELD_AT_PRICE = ("10", "1000")  # 평균 매수가 1,000(현재가와 같다)

TABLE = [
    # (이름, 방식, 보유, 답, 주문 (방향, 종류, 가격, 금액) 또는 까닭)
    ("trend-none-up", TREND, None, {"direction": "오를 것", "buy_price": "990.7"},
     ("buy", "limit", D(990), D(20_000))),
    ("trend-none-down", TREND, None, {"direction": "내릴 것", "buy_price": "990"},
     NoOrderReason.NOTHING),
    ("trend-held-up", TREND, HELD, {"direction": "오를 것", "buy_price": "985.2"},
     ("buy", "limit", D(985), D(10_000))),
    ("trend-held-up-not-above", TREND, HELD_AT_PRICE, {"direction": "오를 것", "buy_price": "990"},
     NoOrderReason.PRICE_NOT_ABOVE_AVG),
    ("trend-held-down", TREND, HELD, {"direction": "내릴 것", "sell_price": "1010.2"},
     ("sell", "limit", D(1011), None)),
    ("mr-none-up", MR, None, {"direction": "오를 것", "predicted": "1010"},
     NoOrderReason.NOTHING),
    ("mr-none-down", MR, None, {"direction": "내릴 것", "predicted": "990.3"},
     ("buy", "limit", D(990), D(20_000))),
    ("mr-held-up", MR, HELD, {"direction": "오를 것", "predicted": "1005.3"},
     ("sell", "limit", D(1006), None)),
    ("mr-held-down", MR, HELD, {"direction": "내릴 것", "predicted": "990.2"},
     ("buy", "limit", D(990), D(10_000))),
]


@pytest.mark.scenario("D2")
@pytest.mark.parametrize(
    ("name", "method", "held", "ai", "expected"), TABLE, ids=[row[0] for row in TABLE]
)
def test_d2_the_judgment_table_places_or_declines_by_method_holding_and_direction(
    build: Callable[..., Rig],
    name: str,
    method: TradeMethod,
    held: tuple[str, str] | None,
    ai: dict[str, str],
    expected: Any,
) -> None:
    seed = holding_seed(AAA, *held) if held is not None else None
    rig = build(method=method, seed=seed)
    rig.start()
    answer(rig, **ai)
    rig.round()
    ((state, order_id, reason),) = reports(rig)
    assert state == "ok"
    if isinstance(expected, NoOrderReason):
        assert orders(rig) == []
        assert order_id is None
        assert reason == expected.value
        return
    side, kind, price, amount = expected
    (row,) = orders(rig)
    assert (row[1], row[2], row[3], row[4]) == (side, kind, "judgment", "open")
    assert D(row[5]) == price
    if amount is not None:  # 매수의 수량은 금액 ÷ 가격을 소수 여덟째 자리에서 내림한다
        assert D(row[6]) == (amount / price).quantize(D("0.00000001"), rounding="ROUND_DOWN")
    else:  # 매도는 팔 수 있는 수량 전부다
        assert D(row[6]) == D(10)
    assert reason is None
    assert order_id is not None


# --- D3: 평균회귀의 매도 경계(호가 단위 0.1) ------------------------------------------------------


@pytest.mark.scenario("D3")
@pytest.mark.parametrize(
    ("predicted", "sells"), [("100.1", False), ("100.2", True)], ids=["100.1", "100.2"]
)
def test_d3_a_mean_reversion_sell_needs_the_predicted_price_to_clear_the_average_and_fees(
    build: Callable[..., Rig], predicted: str, sells: bool
) -> None:
    rig = build(method=MR, seed=holding_seed(AAA, "100", "100"))
    rig.quote.set_prices({AAA: D(100)})
    rig.quote.set_tick_size(AAA, D("0.1"))
    rig.start()
    answer(rig, direction="오를 것", predicted=predicted)
    rig.round()
    ((state, _order_id, reason),) = reports(rig)
    assert state == "ok"
    if sells:
        (row,) = orders(rig)
        assert (row[1], row[2], D(row[5]), D(row[6])) == ("sell", "limit", D("100.2"), D(100))
        assert reason is None
    else:
        assert orders(rig) == []
        assert reason == NoOrderReason.NOT_OVER_AVG_AND_FEE.value


# --- D4: 엔진의 단위 시험(수수료율 모름) ----------------------------------------------------------


def _held(avg: str = "100") -> Any:
    return make_holding(AAA, D(100), D(0), D(100) * D(avg), D(100))


def _ok_report(predicted: str) -> Any:
    return ReportView(
        report_id=1, market=AAA, round_number=4, close_utc=datetime(2026, 1, 5, 3, tzinfo=UTC),
        state=ReportState.OK, failure=None, base_price=D(90), search_judgment=None,
        candle_judgment=None, direction=Direction.UP, predicted_price=D(predicted),
        predicted_reason=None, buy_price=None, sell_price=None, result_text=None,
        last_result=None, compared=(), checklist=(), evidence=(), sources=(), tokens=None,
        elapsed_report_seconds=None,
    )  # fmt: skip


@pytest.mark.scenario("D4")
def test_d4_without_a_fee_rate_the_engine_sells_only_when_the_user_accepts_the_loss() -> None:
    rules = OrderRules(tick_size=D(1), min_order=D(5000))
    report = _ok_report("96")
    settings = d_trade(method=MR, mr_sell_unknown_fee=True)
    for predicted, sells in (("96", True), ("95", True), ("94", False)):
        report = replace(report, predicted_price=D(predicted))
        decision = decide(MR, Direction.UP, _held(), D(90), report, settings, None, rules)
        assert (decision.plan is not None) is sells, predicted
        if not sells:
            assert decision.reason is NoOrderReason.NOT_OVER_AVG_AND_FEE
    refused = replace(settings, mr_sell_unknown_fee=False)
    for predicted in ("96", "95", "94"):
        report = replace(report, predicted_price=D(predicted))
        decision = decide(MR, Direction.UP, _held(), D(90), report, refused, None, rules)
        assert decision.plan is None
        assert decision.reason is NoOrderReason.FEE_UNKNOWN
    no_avg = make_holding(AAA, D(10), D(0), D(0), D(10))
    decision = decide(MR, Direction.UP, no_avg, D(90), report, settings, None, rules)
    assert decision.plan is None
    assert decision.alert == "A-9"
    assert D("0.0005") == PAPER_FEE_RATE  # 모의매매는 늘 수수료율을 안다


# --- D5: 가격 안전 검사와 호가 단위 맞추기 --------------------------------------------------------


@pytest.mark.scenario("D5")
@pytest.mark.parametrize(
    ("side", "suggested", "price"),
    [
        ("buy", "1020", D(1020)),
        ("buy", "1021", None),
        ("sell", "980", D(980)),
        ("sell", "979", None),
        ("buy", "991.7", D(991)),
        ("sell", "1003.2", D(1004)),
    ],
)
def test_d5_the_price_guard_blocks_far_prices_and_the_tick_rounds_the_rest(
    build: Callable[..., Rig], side: str, suggested: str, price: Decimal | None
) -> None:
    held = holding_seed(AAA, "10", "900") if side == "sell" else None
    rig = build(seed=held)
    rig.start()
    if side == "buy":
        answer(rig, direction="오를 것", buy_price=suggested)
    else:
        answer(rig, direction="내릴 것", sell_price=suggested)
    rig.round()
    ((state, _order_id, reason),) = reports(rig)
    assert state == "ok"
    if price is not None:
        (row,) = orders(rig)
        assert (row[1], row[2], D(row[5])) == (side, "limit", price)
        assert "A-4" not in all_codes(rig)
    else:
        assert orders(rig) == []
        assert reason == NoOrderReason.PRICE_GUARD.value
        assert all_codes(rig).count("A-4") == 1


# --- D6: 코인 예산의 경계 -------------------------------------------------------------------------


@pytest.mark.scenario("D6")
@pytest.mark.parametrize(("avg", "fits"), [("900", True), ("900.01", False)])
def test_d6_a_buy_that_would_pass_the_coin_budget_is_declined_not_shrunk(
    build: Callable[..., Rig], avg: str, fits: bool
) -> None:
    rig = build(seed=holding_seed(AAA, "100", avg))  # 보유 원가 90,000원 / 90,001원
    rig.start()
    # 추가 매수 10,000원(코인 예산의 10%). 1,000 × 10이라 금액이 꼭 10,000원이다
    answer(rig, direction="오를 것", buy_price="1000")
    rig.round()
    ((state, _order_id, reason),) = reports(rig)
    assert state == "ok"
    if fits:
        (row,) = orders(rig)  # 합 100,000원이라 걸린다
        assert (row[1], row[2], D(row[5])) == ("buy", "limit", D(1000))
        assert reason is None
    else:
        assert orders(rig) == []  # 줄인 금액의 주문도 없다
        assert reason == NoOrderReason.COIN_BUDGET.value
        assert all_codes(rig).count("A-10") == 1


# --- D7: 가상 원화가 모자람 -----------------------------------------------------------------------


def _krw_seed(krw: str) -> Callable[[Any], None]:
    def seed(conn: Any) -> None:
        found = trade_store.read_paper_account(conn, Mode.PAPER)
        assert found is not None
        trade_store.write_paper_account(conn, Mode.PAPER, replace(found, krw=D(krw)))

    return seed


@pytest.mark.scenario("D7")
def test_d7_a_buy_with_too_little_virtual_won_is_declined_with_a11(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=_krw_seed("5000"))
    rig.start()
    answer(rig, direction="오를 것", buy_price="990")
    rig.round()
    ((state, order_id, reason),) = reports(rig)
    assert state == "ok"
    assert order_id is None
    assert orders(rig) == []
    assert reason == NoOrderReason.NO_KRW.value
    assert all_codes(rig).count("A-11") == 1


# --- D8: 하루 손실 한도 ---------------------------------------------------------------------------


def _plant(rig: Rig, seed: Callable[[Any], None]) -> None:
    """돌고 있는 앱 밖에서 DB에 바로 쓴다(다른 연결)."""
    from coin_report_trader.storage.database import open_database

    conn = open_database(rig.db)
    try:
        seed(conn)
    finally:
        conn.close()


@pytest.mark.scenario("D8")
def test_d8_the_daily_loss_limit_cancels_buys_and_blocks_new_ones_until_nine_in_korea(
    build: Callable[..., Rig],
) -> None:
    program_buy = order_seed(AAA, OrderSide.BUY, OrderKind.LIMIT, D(990), "10")
    rig = build(
        seed=seeds(
            holding_seed(AAA, "10", "980"),
            fill_seed(AAA, "-1200"),
            fill_seed(AAA, "-1900"),
            program_buy,
        )
    )
    rig.start()
    # 켤 때의 점검에서 −3,100원이라 닿았다: 걸려 있던 프로그램 매수 주문은 취소된다
    assert [row[4] for row in orders(rig) if row[1] == "buy"] == ["canceled"]
    assert all_codes(rig).count("A-12") == 1
    answer(rig, direction="오를 것", buy_price="990")
    rig.round()
    ((state, order_id, reason),) = reports(rig)
    assert state == "ok"
    assert order_id is None
    assert reason == NoOrderReason.DAILY_LOSS_LIMIT.value  # 매수는 걸리지 않는다
    assert all_codes(rig).count("A-12") == 1  # 그날 한 번
    # 한국 09:00(00:00 UTC)이 지나면 다시 산다
    answer(rig, direction="오를 것", buy_price="990", result_text="지난 결과", compared=PARTS)
    last = datetime(2026, 1, 5, 23, tzinfo=UTC)  # 그 시각에 걸친 60분봉까지 받아 둔다
    rig.quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 200, last_start=last))
    advance(rig, (datetime(2026, 1, 6, 0, 5, tzinfo=UTC) - rig.clock.now()).total_seconds())
    rig.drain()
    buys = [row[4] for row in orders(rig) if row[1] == "buy" and row[3] == "judgment"]
    assert buys[-1] == "open"
    assert len(buys) == 2


@pytest.mark.scenario("D8")
def test_d8_a_stop_loss_still_sells_when_the_daily_loss_limit_is_reached(
    build: Callable[..., Rig],
) -> None:
    rig = build(
        seed=seeds(
            holding_seed(AAA, "10", "980"), fill_seed(AAA, "-1200"), fill_seed(AAA, "-1900")
        )
    )
    rig.start()
    assert all_codes(rig).count("A-12") == 1
    rig.quote.set_prices({AAA: D(930)})  # 손절선(931) 아래: 매도와 손절은 나간다
    advance(rig, 60)
    sells = [row[3:5] for row in orders(rig) if row[1] == "sell" and row[3] == "stop_loss"]
    assert sells == [("stop_loss", "filled")]
    assert all_codes(rig).count("A-12") == 1


# --- D9: 유의·주의 --------------------------------------------------------------------------------


def _caution(rig: Rig) -> None:
    rig.quote.set_markets([Market(AAA, "이름 KRW-AAA", True), Market(BBB, "이름 KRW-BBB", False)])


@pytest.mark.scenario("D9")
def test_d9_a_caution_coin_gets_no_new_buy_and_a13(build: Callable[..., Rig]) -> None:
    rig = build()
    _caution(rig)
    rig.start()
    answer(rig, direction="오를 것", buy_price="990")
    rig.round()
    ((state, order_id, reason),) = reports(rig)
    assert state == "ok"
    assert order_id is None
    assert orders(rig) == []
    assert reason == NoOrderReason.CAUTION.value
    assert all_codes(rig).count("A-13") == 1


@pytest.mark.scenario("D9")
def test_d9_a_caution_coin_still_sells(build: Callable[..., Rig]) -> None:
    rig = build(seed=holding_seed(AAA, "10", "900"))
    _caution(rig)
    rig.start()
    answer(rig, direction="내릴 것", sell_price="1010")
    rig.round()
    (row,) = orders(rig)
    assert (row[1], row[2], row[4]) == ("sell", "limit", "open")


# --- D10: 주문 간격 -------------------------------------------------------------------------------


@pytest.mark.scenario("D10")
def test_d10_a_new_buy_inside_the_order_interval_is_declined_but_the_stop_loss_goes_out(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=holding_seed(AAA, "10", "900"))
    rig.start()
    rig.clock.advance(3580)  # 03:59:40. 첫 회차의 T(04:00)를 받는 10초 뒤보다 30초 앞
    rig.settle()
    _plant(rig, order_seed(AAA, OrderSide.BUY, OrderKind.LIMIT, D(990), "5", at=rig.clock.now()))
    answer(rig, direction="오를 것", buy_price="990")
    rig.round()
    assert [row[4] for row in orders(rig)] == ["canceled"]  # 심은 주문은 정리에서 취소되고
    ((state, order_id, reason),) = reports(rig)
    assert state == "ok"
    assert order_id is None
    assert reason == NoOrderReason.ORDER_INTERVAL.value
    assert not any(code in ("A-10", "A-11", "A-12", "A-13") for code in all_codes(rig))
    rig.quote.set_prices({AAA: D(850)})  # 손절선 855 아래
    advance(rig, 60)
    assert [row[3:5] for row in orders(rig) if row[1] == "sell"] == [("stop_loss", "filled")]


# --- D36: 허용 범위의 매수 ------------------------------------------------------------------------


@pytest.mark.scenario("D36")
@pytest.mark.parametrize("tolerance", ["0", "1.25"])
def test_d36_the_tolerance_widens_a_mean_reversion_buy_to_fill_at_the_best_ask(
    build: Callable[..., Rig], tolerance: str
) -> None:
    trade = d_trade((AAA,), method=MR, tolerance=D(tolerance))
    rig = build(trade=trade)
    rig.quote.set_prices({AAA: D(1010)})
    rig.quote.set_book(AAA, D(1011), D(1009))
    rig.start()
    answer(rig, direction="내릴 것", predicted="1000", sell_price="1015")
    rig.round()
    (row,) = orders(rig)
    codes = all_codes(rig)
    if tolerance == "0":  # 1,000 × 20 = 20,000원의 지정가가 걸리고 바로 체결되지 않는다
        assert (row[1], row[2], row[4], D(row[5]), D(row[6])) == (
            "buy", "limit", "open", D(1000), D(20),
        )  # fmt: skip
        assert codes.count("A-27") == 1
        assert "A-28" not in codes
    else:  # 1,012.5를 내린 1,012 × 19.76284584, 최우선 매도 호가 1,011 이하라 1,011에 체결
        assert (row[1], row[2], row[4], D(row[5]), D(row[6])) == (
            "buy", "limit", "filled", D(1012), D("19.76284584"),
        )  # fmt: skip
        assert codes.count("A-27") == 1
        assert codes.count("A-28") == 1
        assert "A-4" not in codes  # 가격 안전 검사(1,030.2까지)에 걸리지 않는다
        (fill,) = fills(rig)
        assert (fill[1], D(fill[2]), D(fill[3])) == ("buy", D(1011), D("19.76284584"))


# --- D37: 허용 범위의 매도 ------------------------------------------------------------------------


@pytest.mark.scenario("D37")
@pytest.mark.parametrize(
    ("predicted", "sell_price"), [("100.3", D("100.2")), ("100.2", None)], ids=["100.3", "100.2"]
)
def test_d37_the_tolerance_lowers_a_mean_reversion_sell_price_before_the_fee_check(
    build: Callable[..., Rig], predicted: str, sell_price: Decimal | None
) -> None:
    trade = d_trade((AAA,), method=MR, tolerance=D("0.1"))
    rig = build(trade=trade, seed=holding_seed(AAA, "100", "100"))
    rig.quote.set_prices({AAA: D(100)})
    rig.quote.set_tick_size(AAA, D("0.1"))
    rig.start()
    answer(rig, direction="오를 것", predicted=predicted)
    rig.round()
    ((state, _order_id, reason),) = reports(rig)
    assert state == "ok"
    if sell_price is not None:  # 100.1997을 올린 100.2: 100.2 × 0.9995 > 100 × 1.0005
        (row,) = orders(rig)
        assert (row[1], row[2], D(row[5]), D(row[6])) == ("sell", "limit", sell_price, D(100))
    else:  # 100.0998을 올린 100.1은 기준 100.10005…보다 작다
        assert orders(rig) == []
        assert reason == NoOrderReason.NOT_OVER_AVG_AND_FEE.value


@pytest.mark.scenario("D37")
def test_d37_the_tolerance_lowers_a_trend_sell_by_the_percent(build: Callable[..., Rig]) -> None:
    trade = d_trade((AAA,), method=TREND, tolerance=D("1.25"))
    rig = build(trade=trade, seed=holding_seed(AAA, "10", "900"))
    rig.start()
    answer(rig, direction="내릴 것", sell_price="1000")
    rig.round()
    (row,) = orders(rig)  # 987.5를 올린 988에 전량 매도가 걸린다
    assert (row[1], row[2], D(row[5]), D(row[6])) == ("sell", "limit", D(988), D(10))
