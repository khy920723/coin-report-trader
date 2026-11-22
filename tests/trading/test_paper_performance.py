"""모의 성적의 셈(M3 명세 3.16.1)의 시험. 3.16.1의 예를 모두 넣는다. 값은 모두 만든 숫자다."""

from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal

import pytest

from coin_report_trader.core.ai import Verdict
from coin_report_trader.core.alerts import Mode
from coin_report_trader.core.trade import FillRecord, OrderSide, PaperAccount
from coin_report_trader.core.views import LastResult
from coin_report_trader.trading.paper import (
    asset_change,
    compute_performance,
    make_holding,
    mean_diffs,
    unrealized_pnl,
)

A = "KRW-AAA"
B = "KRW-BBB"
C = "KRW-CCC"
D = Decimal
T = datetime(2026, 1, 5, 3, 20, tzinfo=UTC)


def result(hit: Verdict | None, diff: str | None = None, n: int = 1) -> LastResult:
    actual = None if hit is None else D(1000)
    return LastResult(n, D(1000), actual, None if diff is None else D(diff), hit)


def sell(market: str, pnl: str, *, mode: Mode = Mode.PAPER, n: int = 1) -> FillRecord:
    return FillRecord(n, n, mode, market, OrderSide.SELL, D(1000), D(1), D("0.5"), D(pnl), T)


def buy(market: str, n: int = 1) -> FillRecord:
    return FillRecord(n, n, Mode.PAPER, market, OrderSide.BUY, D(1000), D(1), D("0.5"), None, T)


def account(krw: str, *holdings) -> PaperAccount:
    return PaperAccount(krw=D(krw), locked_krw=D(0), holdings=tuple(holdings))


def held(market: str, qty: str, avg: str):
    return make_holding(market, D(qty), D(0), D(qty) * D(avg), D(qty))


# --- 예(3.16.1) -------------------------------------------------------------------------------


def test_asset_change_example() -> None:
    # 가상 자금 100,000원, 가상 원화 90,000원, AAA 10개, 현재가 1,050원이면 자산은 100,500원이고
    # 변화는 +0.50%다.
    acc = account("90000", held(A, "10", "1000"))
    change = asset_change(acc, {A: D(1050)}, D(100_000))
    assert change == D("0.005")
    assert f"{change * 100:+.2f}%" == "+0.50%"


def test_hit_rate_example() -> None:
    # 판단 회차 12, 맞음 7, 틀림 4, 셀 수 없음 1이면 "63.64% (7/11)"이다.
    results = [result(Verdict.HIT)] * 7 + [result(Verdict.MISS)] * 4 + [result(None)]
    (row, total) = compute_performance(
        [A],
        judgments={A: results},
        fills=[],
        account=account("100000"),
        prices={A: D(1000)},
        funds=D(100_000),
    )
    assert (row.ok_rounds, row.hits, row.countable) == (12, 7, 11)
    rate = (D(row.hits) / D(row.countable) * 100).quantize(D("0.01"), rounding=ROUND_HALF_EVEN)
    assert f"{rate}% ({row.hits}/{row.countable})" == "63.64% (7/11)"
    assert (total.ok_rounds, total.hits, total.countable) == (12, 7, 11)


def test_diff_means_example() -> None:
    # 차이 −0.0098, +0.0200, −0.0040이면 평균 0.0021, 크기 평균 0.0113이다.
    results = [
        result(Verdict.MISS, "-0.0098"),
        result(Verdict.HIT, "0.0200"),
        result(Verdict.HIT, "-0.0040"),
    ]
    assert mean_diffs(results) == (D("0.0021"), D("0.0113"))


def test_diff_means_without_countable_judgments_are_none() -> None:
    assert mean_diffs([]) == (None, None)
    assert mean_diffs([result(None)]) == (None, None)


def test_diff_means_skip_judgments_without_an_actual_value() -> None:
    results = [result(Verdict.HIT, "0.0100"), result(None), result(Verdict.MISS, "-0.0300")]
    assert mean_diffs(results) == (D("-0.0100"), D("0.0200"))


def test_diff_means_round_half_even() -> None:
    # 0.00025 → 0.0002, 0.00035 → 0.0004(ROUND_HALF_EVEN)
    assert mean_diffs([result(Verdict.HIT, "0.0001"), result(Verdict.HIT, "0.0004")])[0] == D(
        "0.0002"
    )
    assert mean_diffs([result(Verdict.HIT, "0.0003"), result(Verdict.HIT, "0.0004")])[0] == D(
        "0.0004"
    )


# --- 평가 손익과 자산 변화 ----------------------------------------------------------------------


def test_unrealized_pnl_formula() -> None:
    # 10 × (102 × 0.9995 − 100 × 1.0005) = 18.99
    assert unrealized_pnl(held(A, "10", "100"), D(102)) == D("18.99")
    assert unrealized_pnl(None, D(102)) == 0
    assert unrealized_pnl(make_holding(A, D(0), D(0), D(0), D(0)), D(102)) == 0
    # 현재가를 읽지 못했으면 "-"다.
    assert unrealized_pnl(held(A, "10", "100"), None) is None


def test_asset_change_needs_prices_of_every_holding() -> None:
    acc = account("50000", held(A, "10", "1000"), held(C, "5", "1000"))
    assert asset_change(acc, {A: D(1000)}, D(100_000)) is None
    assert asset_change(acc, {A: D(1000), C: None}, D(100_000)) is None
    # 뺀 코인(CCC)의 보유도 자산에 든다.
    # (50,000 + 10,000 + 10,000) ÷ 100,000 − 1
    assert asset_change(acc, {A: D(1000), C: D(2000)}, D(100_000)) == D("-0.3")
    # 수량이 0인 보유는 현재가가 없어도 된다.
    empty = account("100000", make_holding(B, D(0), D(0), D(0), D(0)))
    assert asset_change(empty, {}, D(100_000)) == 0
    with pytest.raises(ValueError, match="가상 자금"):
        asset_change(empty, {}, D(0))


# --- 줄 --------------------------------------------------------------------------------------


def test_rows_per_coin_and_a_total_row() -> None:
    acc = account("80000", held(A, "10", "1000"), held(B, "4", "100"), held(C, "1", "500"))
    fills = [
        sell(A, "18.99", n=1),
        sell(A, "-5.5", n=2),
        sell(A, "0", n=3),
        buy(A, n=4),
        sell(B, "7", n=5),
        sell(A, "100", mode=Mode.LIVE, n=6),  # 다른 모드는 세지 않는다
        sell(C, "50", n=7),  # 고르지 않은 코인
    ]
    judgments = {
        A: [result(Verdict.HIT, "0.0100"), result(Verdict.MISS, "-0.0300"), result(None)],
        B: [result(Verdict.HIT, "0.0050")],
    }
    rows = compute_performance(
        [A, B],
        judgments=judgments,
        fills=fills,
        account=acc,
        prices={A: D(1100), B: D(100), C: D(500)},
        funds=D(100_000),
    )
    assert [row.market for row in rows] == [A, B, None]
    a, b, total = rows
    assert (a.ok_rounds, a.hits, a.countable) == (3, 1, 2)
    assert (a.realized_pnl, a.wins, a.sells) == (D("13.49"), 1, 3)
    # 10 × (1100 × 0.9995 − 1000 × 1.0005) = 10 × (1099.45 − 1000.5)
    assert a.unrealized_pnl == D("989.5")
    assert (a.diff_mean, a.diff_abs_mean) == (D("-0.0100"), D("0.0200"))
    assert a.asset_change is None
    assert (b.ok_rounds, b.hits, b.countable) == (1, 1, 1)
    assert (b.realized_pnl, b.wins, b.sells) == (D(7), 1, 1)
    # 4 × (100 × 0.9995 − 100 × 1.0005) = 4 × (−0.1)
    assert b.unrealized_pnl == D("-0.4")
    assert (b.diff_mean, b.diff_abs_mean) == (D("0.0050"), D("0.0050"))
    assert (total.ok_rounds, total.hits, total.countable) == (4, 2, 3)
    assert (total.realized_pnl, total.wins, total.sells) == (D("20.49"), 2, 4)
    assert total.unrealized_pnl == D("989.1")
    # 합계의 차이는 고른 코인 모두의 셀 수 있는 판단을 모아 셈한다: (0.01 − 0.03 + 0.005) ÷ 3
    assert (total.diff_mean, total.diff_abs_mean) == (D("-0.0050"), D("0.0150"))
    # (80,000 + 11,000 + 400 + 500) ÷ 100,000 − 1
    assert total.asset_change == D("-0.081")


def test_coins_without_records_and_unread_prices() -> None:
    rows = compute_performance(
        [A, B],
        judgments={},
        fills=[],
        account=account("100000", held(A, "1", "1000")),
        prices={B: D(100)},
        funds=D(100_000),
    )
    a, b, total = rows
    assert (a.ok_rounds, a.realized_pnl, a.wins, a.sells) == (0, 0, 0, 0)
    assert a.unrealized_pnl is None  # 현재가를 읽지 못함
    assert (a.diff_mean, a.diff_abs_mean) == (None, None)
    assert b.unrealized_pnl == 0  # 보유가 없음
    assert total.unrealized_pnl is None
    assert total.asset_change is None
    assert (total.diff_mean, total.diff_abs_mean) == (None, None)


def test_no_coins_gives_only_the_total_row() -> None:
    (total,) = compute_performance(
        [], judgments={}, fills=[], account=account("100000"), prices={}, funds=D(100_000)
    )
    assert total.market is None
    assert (total.ok_rounds, total.realized_pnl, total.unrealized_pnl) == (0, 0, 0)
    assert total.asset_change == 0
