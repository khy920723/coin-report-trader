"""실거래 허용 값과 연결 고르기(M5 명세 3.7.1), 실거래 켜기의 조건(3.7.2)의 시험.

3.7.2의 조건마다와 조건 8의 예를 넣는다. 값은 모두 만든 숫자다.
"""

import ast
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fakes.clock import FakeClock
from fakes.quote import FakeQuoteSource
from fakes.upbit import TEST_ACCESS_KEY, TEST_SECRET_KEY, FakeUpbitTransport

from coin_report_trader.core.alerts import Mode
from coin_report_trader.core.exchange import (
    ExchangeAccount,
    ExchangeHolding,
    LiveBlock,
    LiveSettings,
)
from coin_report_trader.core.trade import (
    ErrorPauseCause,
    PauseStopLoss,
    TradeMethod,
    TradeSettings,
)
from coin_report_trader.trading import live
from coin_report_trader.trading.live import (
    LIVE_TRADING_ALLOWED,
    LiveNotAllowed,
    RoundReport,
    SettingEvent,
    UpbitExchange,
    count_paper_rounds,
    live_blocks,
    live_budget_fits,
    live_settings_ok,
    select_exchange,
)
from coin_report_trader.trading.paper.exchange import PaperExchange
from coin_report_trader.trading.quote import QuoteClient
from coin_report_trader.trading.risk import PauseState
from coin_report_trader.trading.upbit import UpbitClient, UpbitKeys

A = "KRW-AAA"
B = "KRW-BBB"
C = "KRW-CCC"
D = Decimal
T0 = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
TREND = TradeMethod.TREND
MR = TradeMethod.MEAN_REVERSION


def hours(n: int) -> datetime:
    return T0 + timedelta(hours=n)


# --- 실거래 허용 값(3.7.1) --------------------------------------------------------------------


def test_live_trading_is_allowed_from_the_first_5th_stage_build() -> None:
    assert LIVE_TRADING_ALLOWED is True


def test_the_allowed_value_is_a_plain_literal_the_check_can_read() -> None:
    # 3.13의 검사는 이 값을 ast로 읽는다. 글자 그대로의 True여야 한다.
    source = Path(live.switch.__file__).read_text(encoding="utf-8")
    values = [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "LIVE_TRADING_ALLOWED"
    ]
    assert len(values) == 1
    assert isinstance(values[0], ast.Constant)
    assert values[0].value is True


# --- 연결 고르기(3.7.1) ----------------------------------------------------------------------


def parts():
    clock = FakeClock(T0)
    quotes = QuoteClient(FakeQuoteSource(prices={A: D(1000)}, clock=clock), clock)
    client = UpbitClient(
        UpbitKeys(TEST_ACCESS_KEY, TEST_SECRET_KEY), FakeUpbitTransport(clock=clock), clock
    )
    store = object()
    return clock, quotes, client, store


def test_paper_mode_gives_the_paper_exchange_without_a_key() -> None:
    clock, quotes, _, store = parts()
    exchange = select_exchange(
        Mode.PAPER, paper_store=store, client=None, allowed=False, quotes=quotes, clock=clock
    )  # type: ignore[arg-type]
    assert isinstance(exchange, PaperExchange)
    assert exchange.mode is Mode.PAPER


def test_live_mode_gives_the_upbit_exchange() -> None:
    clock, quotes, client, store = parts()
    exchange = select_exchange(
        Mode.LIVE, paper_store=store, client=client, allowed=True, quotes=quotes, clock=clock
    )  # type: ignore[arg-type]
    assert isinstance(exchange, UpbitExchange)
    assert exchange.mode is Mode.LIVE


def test_live_mode_needs_the_allowed_value_and_a_key() -> None:
    clock, quotes, client, _ = parts()
    with pytest.raises(LiveNotAllowed, match="켤 수 없다"):
        select_exchange(
            Mode.LIVE, paper_store=None, client=client, allowed=False, quotes=quotes, clock=clock
        )
    with pytest.raises(LiveNotAllowed, match="키"):
        select_exchange(
            Mode.LIVE, paper_store=None, client=None, allowed=True, quotes=quotes, clock=clock
        )


def test_live_mode_is_refused_when_the_build_value_is_false(monkeypatch) -> None:
    clock, quotes, client, _ = parts()
    monkeypatch.setattr(live.select, "LIVE_TRADING_ALLOWED", False)
    with pytest.raises(LiveNotAllowed):
        select_exchange(
            Mode.LIVE, paper_store=None, client=client, allowed=True, quotes=quotes, clock=clock
        )


def test_paper_mode_needs_its_store_and_quotes() -> None:
    clock, quotes, _, store = parts()
    with pytest.raises(ValueError, match="paper_store"):
        select_exchange(
            Mode.PAPER, paper_store=None, client=None, allowed=True, quotes=quotes, clock=clock
        )
    with pytest.raises(ValueError, match="quotes"):
        select_exchange(Mode.PAPER, paper_store=store, client=None, allowed=True, clock=clock)  # type: ignore[arg-type]


def test_select_exchange_reads_the_allowed_value() -> None:
    # 3.13: select_exchange의 몸통이 LIVE_TRADING_ALLOWED를 읽는다.
    source = Path(live.select.__file__).read_text(encoding="utf-8")
    function = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "select_exchange"
    )
    names = {n.id for n in ast.walk(function) if isinstance(n, ast.Name)}
    assert "LIVE_TRADING_ALLOWED" in names


# --- 조건 5: 모의 회차를 세는 셈 ----------------------------------------------------------------


def report(market: str, hour: int, *, ok: bool = True, method=TREND, mode=Mode.PAPER):
    return RoundReport(market=market, mode=mode, ok=ok, method=method, utc=hours(hour))


def test_counts_ok_paper_reports_of_the_current_method() -> None:
    reports = [
        report(A, 1),
        report(A, 2),
        report(A, 3, ok=False),  # 쉼, 실패, 버림은 세지 않는다
        report(A, 4, method=MR),  # 다른 방식
        report(A, 5, mode=Mode.LIVE),  # 실거래의 보고서
        report(A, 6, method=None),  # 2단계의 보고서
        report(B, 7),  # 다른 코인
    ]
    assert count_paper_rounds(A, TREND, reports, []) == 2
    assert count_paper_rounds(A, MR, reports, []) == 1
    assert count_paper_rounds(B, TREND, reports, []) == 1


@pytest.mark.parametrize(
    "event",
    [
        SettingEvent("coin_added", A, hours(10)),
        SettingEvent("method_changed", A, hours(10)),
        SettingEvent("candle_count_changed", None, hours(10)),
        SettingEvent("n_changed", None, hours(10)),
    ],
)
def test_reset_events_count_again_from_zero(event: SettingEvent) -> None:
    reports = [report(A, h) for h in (8, 9, 10, 11, 12)]
    # 바뀐 때(10시)와 같은 때의 보고서는 세지 않는다(그 뒤인 것만).
    assert count_paper_rounds(A, TREND, reports, [event]) == 2


def test_events_of_other_coins_and_other_kinds_do_not_reset() -> None:
    reports = [report(A, h) for h in (8, 9, 10, 11)]
    events = [
        SettingEvent("coin_added", B, hours(10)),
        SettingEvent("coin_removed", A, hours(10)),
        SettingEvent("model_changed", None, hours(10)),
    ]
    assert count_paper_rounds(A, TREND, reports, events) == 4


def test_the_last_reset_counts() -> None:
    reports = [report(A, h) for h in range(1, 11)]
    events = [SettingEvent("coin_added", A, hours(2)), SettingEvent("method_changed", A, hours(7))]
    assert count_paper_rounds(A, TREND, reports, events) == 3


# --- 조건 7: 실거래 설정 -------------------------------------------------------------------------


def trade_settings() -> TradeSettings:
    return TradeSettings(
        budget=D(100_000),
        coin_ratios={A: D(50), B: D(50)},
        first_ratio=D(20),
        mr_first_ratio=None,
        add_ratio=D(15),
        tolerance=D(1),
        stop_ratio=D(5),
        daily_loss_limit=D(3000),
        order_interval_s=60,
        method=TREND,
        pause_stop_loss=PauseStopLoss.KEEP,
        mr_sell_unknown_fee=False,
        mr_tolerable_loss=D(5),
        paper_funds=D(100_000),
        backup_days=7,
    )


GOOD_LIVE = LiveSettings(
    budget=D(200_000), coin_ratios={A: D(60), B: D(40)}, daily_loss_limit=D(5000)
)


def test_live_settings_ok_when_complete_and_passing_the_checks() -> None:
    assert live_settings_ok(GOOD_LIVE, trade_settings(), (A, B), D(5000))


@pytest.mark.parametrize(
    "live_settings",
    [
        None,
        LiveSettings(),
        replace(GOOD_LIVE, budget=None),
        replace(GOOD_LIVE, daily_loss_limit=None),
        replace(GOOD_LIVE, coin_ratios={A: D(100)}),  # BBB의 비율이 없다
        replace(GOOD_LIVE, coin_ratios={A: D(60), B: D(30)}),  # 합 90
        replace(GOOD_LIVE, budget=D(0)),
        replace(GOOD_LIVE, budget=D(20_000)),  # 코인 예산 8,000원 × 20% = 1,600원 < 하한
    ],
)
def test_live_settings_fail(live_settings: LiveSettings | None) -> None:
    assert not live_settings_ok(live_settings, trade_settings(), (A, B), D(5000))


# --- 조건 1~7 -------------------------------------------------------------------------------


def blocks(**changes):
    values = {
        "allowed": True,
        "key_present": True,
        "pause": PauseState(user=True),
        "coins": (A, B),
        "counts": {A: 30, B: 31},
        "rounds_n": 30,
        "sell_all_done": True,
        "settings_ok": True,
    }
    values.update(changes)
    return live_blocks(**values)


def test_nothing_blocks_when_every_condition_is_met() -> None:
    assert blocks() == ()


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"allowed": False}, LiveBlock.NOT_ALLOWED),
        ({"key_present": False}, LiveBlock.NO_KEY),
        ({"pause": PauseState()}, LiveBlock.NOT_PAUSED),
        (
            {"pause": PauseState(user=True, error=True, error_cause=ErrorPauseCause.SAVE)},
            LiveBlock.NOT_PAUSED,
        ),
        ({"coins": ()}, LiveBlock.NO_COINS),
        ({"counts": {A: 30, B: 29}}, LiveBlock.NOT_ENOUGH_ROUNDS),
        ({"counts": {A: 30}}, LiveBlock.NOT_ENOUGH_ROUNDS),
        ({"sell_all_done": False}, LiveBlock.NO_SELL_ALL),
        ({"settings_ok": False}, LiveBlock.NO_SETTINGS),
    ],
)
def test_each_condition_blocks(changes: dict, expected: LiveBlock) -> None:
    assert blocks(**changes) == (expected,)


def test_blocks_come_in_condition_order_and_short_coins_give_one_reason() -> None:
    everything = blocks(
        allowed=False,
        key_present=False,
        pause=PauseState(),
        counts={A: 1, B: 2},
        sell_all_done=False,
        settings_ok=False,
    )
    assert everything == (
        LiveBlock.NOT_ALLOWED,
        LiveBlock.NO_KEY,
        LiveBlock.NOT_PAUSED,
        LiveBlock.NOT_ENOUGH_ROUNDS,
        LiveBlock.NO_SELL_ALL,
        LiveBlock.NO_SETTINGS,
    )


def test_n_must_be_positive() -> None:
    with pytest.raises(ValueError, match="N"):
        blocks(rounds_n=0)


# --- 조건 8: 자산 검사 ----------------------------------------------------------------------


def account(krw: str, *holdings: ExchangeHolding) -> ExchangeAccount:
    return ExchangeAccount(krw=D(krw), krw_locked=D(0), holdings=holdings, read_utc=T0)


@pytest.mark.parametrize(("budget", "fits"), [("250000", True), ("300000", False)])
def test_budget_fits_example_of_3_7_2(budget: str, fits: bool) -> None:
    # 원화 200,000원, KRW-AAA 50개, 현재가 1,000원이면 합은 250,000원이다. 예산 250,000원은 되고
    # 300,000원은 걸린다.
    acc = account("200000", ExchangeHolding(A, D(50), D(0), D(900)))
    assert live_budget_fits(D(budget), acc, {A: D(1000)}) == (fits, D(250_000))


def test_budget_counts_locked_qty_and_only_the_chosen_coins() -> None:
    acc = account(
        "10000",
        ExchangeHolding(A, D(3), D(2), D(900)),  # (3 + 2) × 1,000
        ExchangeHolding(C, D(100), D(0), D(10)),  # 고르지 않은 코인
    )
    assert live_budget_fits(D(15_000), acc, {A: D(1000), B: D(500)}) == (True, D(15_000))
    assert live_budget_fits(D("15000.01"), acc, {A: D(1000)}) == (False, D(15_000))


def test_unread_price_of_a_held_coin_cannot_be_checked() -> None:
    acc = account("10000", ExchangeHolding(A, D(1), D(0), D(900)))
    assert live_budget_fits(D(1), acc, {A: None}) == (False, None)
    # 보유가 없는 코인의 현재가는 없어도 된다.
    empty = account("10000", ExchangeHolding(B, D(0), D(0), None))
    assert live_budget_fits(D(10_000), empty, {B: None}) == (True, D(10_000))


def test_live_settings_need_coin_ratios_even_without_coins() -> None:
    # 고른 코인이 없어도 비율이 하나도 없으면 다 찬 것이 아니다(LiveSettings.complete).
    empty_ratios = replace(GOOD_LIVE, coin_ratios={})
    assert not live_settings_ok(empty_ratios, trade_settings(), (), D(5000))
