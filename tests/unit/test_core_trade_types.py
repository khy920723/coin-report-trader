"""매매 계산의 공용 타입(M3 명세 3.4). 값은 모두 만든 값이다."""

import ast
import dataclasses
import sys
import typing
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from coin_report_trader import core
from coin_report_trader.core import (
    BACKUP_DAYS_RANGE,
    CANCEL_CONFIRM_SECONDS,
    DAILY_CANCEL_LIMIT,
    ERROR_PAUSE_CAUSE_TITLES,
    FIRST_BUY_FLOOR_FACTOR,
    MIN_ORDER_FALLBACK,
    ORDER_KIND_TITLES,
    ORDER_REASON_TITLES,
    ORDER_SIDE_TITLES,
    ORDER_STATE_TITLES,
    PAPER_FEE_RATE,
    PAUSE_STOP_LOSS_TITLES,
    QTY_PLACES,
    SAFETY_DOWN,
    SAFETY_SELL_UP,
    SAFETY_UP,
    SAVE_TARGET_EXPORT,
    STOP_CHECK_SECONDS,
    TOLERANCE_MAX,
    TRADE_METHOD_TITLES,
    UNCONFIRMED_CLOSE_SECONDS,
    BelowStopView,
    BookSource,
    CoinTradeView,
    ErrorPauseCause,
    HistoryRow,
    HistoryTab,
    HistoryView,
    LoadState,
    MinuteCandle,
    NoOrderReason,
    OrderKind,
    OrderPlan,
    OrderReason,
    OrderRules,
    OrderSide,
    OrderState,
    PaperAccount,
    PaperHolding,
    PauseKind,
    PauseStopLoss,
    PerformanceRow,
    PerformanceView,
    ReportView,
    RoundNowView,
    RoundStage,
    SaveResult,
    SettingsView,
    TodayView,
    TradeMethod,
    TradeSettings,
    TradeStatusView,
    UiUpdate,
)
from coin_report_trader.core import trade as trade_module

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
D = Decimal


def names(cls: type) -> list[str]:
    return [f.name for f in dataclasses.fields(cls)]


def settings(**changes: Any) -> TradeSettings:
    base: dict[str, Any] = {
        "budget": D("1000000"),
        "coin_ratios": {"KRW-AAA": D("20"), "KRW-BBB": D("12.5")},
        "first_ratio": D("30"),
        "mr_first_ratio": None,
        "add_ratio": D("10"),
        "tolerance": D("1.25"),
        "stop_ratio": D("5"),
        "daily_loss_limit": D("3000"),
        "order_interval_s": 600,
        "method": TradeMethod.TREND,
        "pause_stop_loss": PauseStopLoss.KEEP,
        "mr_sell_unknown_fee": False,
        "mr_tolerable_loss": D("5"),
        "paper_funds": D("1000000"),
        "backup_days": 7,
    }
    base.update(changes)
    return TradeSettings(**base)


# --- 값(StrEnum) -------------------------------------------------------------------------------


def test_the_enums_have_the_values_and_titles_of_the_spec() -> None:
    assert [(m.name, m.value) for m in TradeMethod] == [
        ("MEAN_REVERSION", "mean_reversion"),
        ("TREND", "trend"),
    ]
    assert dict(TRADE_METHOD_TITLES) == {
        TradeMethod.MEAN_REVERSION: "평균회귀",
        TradeMethod.TREND: "추세추종",
    }
    assert [(m.name, m.value) for m in PauseStopLoss] == [("KEEP", "keep"), ("STOP", "stop")]
    assert dict(PAUSE_STOP_LOSS_TITLES) == {
        PauseStopLoss.KEEP: "손절 계속",
        PauseStopLoss.STOP: "손절 중지",
    }
    assert [(m.name, m.value) for m in OrderSide] == [("BUY", "buy"), ("SELL", "sell")]
    assert dict(ORDER_SIDE_TITLES) == {OrderSide.BUY: "매수", OrderSide.SELL: "매도"}
    assert [(m.name, m.value) for m in OrderKind] == [("LIMIT", "limit"), ("MARKET", "market")]
    assert dict(ORDER_KIND_TITLES) == {OrderKind.LIMIT: "지정가", OrderKind.MARKET: "시장가"}
    assert [(m.name, m.value) for m in OrderReason] == [
        ("JUDGMENT", "judgment"),
        ("STOP_LOSS", "stop_loss"),
        ("SELL_ALL", "sell_all"),
    ]
    assert [ORDER_REASON_TITLES[m] for m in OrderReason] == ["판단", "손절", "긴급 매도"]
    assert [(m.name, m.value) for m in OrderState] == [
        ("OPEN", "open"),
        ("FILLED", "filled"),
        ("CANCELED", "canceled"),
        ("UNCONFIRMED", "unconfirmed"),
        ("NOT_SENT", "not_sent"),
        ("FAILED", "failed"),
    ]
    assert [ORDER_STATE_TITLES[m] for m in OrderState] == [
        "걸림",
        "체결",
        "취소",
        "확인 안 됨",
        "보내지지 않음",
        "실패",
    ]
    assert [(m.name, m.value) for m in PauseKind] == [("USER", "user"), ("ERROR", "error")]
    assert [(m.name, m.value) for m in ErrorPauseCause] == [
        ("EXCHANGE", "exchange"),
        ("UNEXPECTED", "unexpected"),
        ("SAVE", "save"),
    ]
    assert [ERROR_PAUSE_CAUSE_TITLES[m] for m in ErrorPauseCause] == [
        "일시 차단",
        "예상하지 못한 오류",
        "저장 실패",
    ]


def test_the_round_stage_values_are_the_stage_texts() -> None:
    assert [(m.name, m.value) for m in RoundStage] == [
        ("CLEANUP", "정리"),
        ("STATS", "봉 통계"),
        ("AI", "AI 호출"),
        ("ACCOUNT", "계정 읽기"),
        ("ORDER", "검사와 주문"),
        ("RECORD", "기록"),
    ]


def test_the_no_order_reasons_are_the_texts_of_the_spec_and_are_unique() -> None:
    texts = [m.value for m in NoOrderReason]
    assert len(set(texts)) == len(texts)
    for text in (
        "아무것도 하지 않음",
        "현재가가 평균 매수가 이하",
        "예측 가격이 평균 매수가와 수수료를 넘지 않음",
        "수수료율을 몰라 팔지 않음",
        "평균 매수가 없음",
        "판단을 받지 못함",
        "손절로 판단을 버림",
        "손절 뒤라 새로 사지 않음",
        "멈춤으로 버림",
        "가격 안전 검사",
        "최소 주문 금액보다 작음",
        "팔 수 있는 수량이 없음",
        "현재가나 호가 단위를 받지 못함",
    ):
        assert text in texts, text
    # 3.8.2의 한도 표(차례대로)와 걸었다 지우기의 상한
    for text in (
        "하루 손실 한도",
        "유의·주의",
        "설정이 검사에 걸림",
        "주문 간격",
        "코인 예산",
        "예산",
        "가상 원화가 모자람",
        "확인 안 된 주문",
        "하루 걸었다 지우기 상한",
    ):
        assert text in texts, text
    assert len(texts) == 22
    assert NoOrderReason("예산") is NoOrderReason.BUDGET  # 값이 곧 글이다


def test_the_numbers_of_the_spec() -> None:
    assert D("0.0005") == PAPER_FEE_RATE
    assert D("5000") == MIN_ORDER_FALLBACK
    assert (D("0.02"), D("0.5"), D("2")) == (SAFETY_UP, SAFETY_DOWN, SAFETY_SELL_UP)
    assert D("1.1") == FIRST_BUY_FLOOR_FACTOR
    assert QTY_PLACES == 8
    assert D("2") == TOLERANCE_MAX
    assert STOP_CHECK_SECONDS == 60
    assert (CANCEL_CONFIRM_SECONDS, UNCONFIRMED_CLOSE_SECONDS) == (10, 600)
    assert DAILY_CANCEL_LIMIT == 48
    assert BACKUP_DAYS_RANGE == (1, 365)
    for value in (PAPER_FEE_RATE, MIN_ORDER_FALLBACK, SAFETY_UP, SAFETY_DOWN, SAFETY_SELL_UP):
        assert isinstance(value, Decimal)


# --- 설정, 계정, 주문 -----------------------------------------------------------------------------


def test_trade_settings_have_the_fields_of_the_spec_in_order_and_are_frozen() -> None:
    assert names(TradeSettings) == [
        "budget", "coin_ratios", "first_ratio", "mr_first_ratio", "add_ratio", "tolerance",
        "stop_ratio", "daily_loss_limit", "order_interval_s", "method", "pause_stop_loss",
        "mr_sell_unknown_fee", "mr_tolerable_loss", "paper_funds", "backup_days",
    ]  # fmt: skip
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings().budget = D("1")  # type: ignore[misc]
    assert settings().mr_first_ratio is None  # 고르지 않은 방식의 첫 매수 비율만 None일 수 있다


def test_the_coin_budget_is_budget_times_ratio_over_100_without_cutting_below_a_won() -> None:
    assert settings().coin_budget("KRW-AAA") == D("200000")  # 1,000,000 × 20 ÷ 100
    assert settings().coin_budget("KRW-BBB") == D("125000")
    odd = settings(budget=D("1000001"), coin_ratios={"KRW-AAA": D("33.3")})
    assert odd.coin_budget("KRW-AAA") == D("333000.333")  # 원 아래를 버리지 않는다
    with pytest.raises(KeyError):
        settings().coin_budget("KRW-ZZZ")


def test_the_account_and_order_types_have_the_fields_of_the_spec() -> None:
    assert names(OrderRules) == ["tick_size", "min_order"]
    assert names(PaperHolding) == [
        "market", "qty", "locked_qty", "avg_price", "buy_amount_sum", "buy_qty_sum",
    ]  # fmt: skip
    assert names(PaperAccount) == ["krw", "locked_krw", "holdings"]
    assert names(core.OrderRecord) == [
        "order_id", "uid", "mode", "round_id", "market", "side", "kind", "reason", "price", "qty",
        "state", "created_utc", "closed_utc", "error_name",
    ]  # fmt: skip
    assert names(core.FillRecord) == [
        "fill_id", "order_id", "mode", "market", "side", "price", "qty", "fee", "realized_pnl",
        "filled_utc",
    ]  # fmt: skip
    assert names(OrderPlan) == ["market", "side", "kind", "reason", "price", "qty", "amount"]
    plan = OrderPlan("KRW-AAA", OrderSide.BUY, OrderKind.LIMIT, OrderReason.JUDGMENT,
                     D("1000"), D("2"), D("2000"))  # fmt: skip
    assert plan.amount == plan.price * plan.qty  # type: ignore[operator]


# --- 화면이 받는 값 ------------------------------------------------------------------------------


def test_the_views_have_the_fields_of_the_spec_in_order_with_the_defaults() -> None:
    assert names(TradeStatusView) == [
        "user_paused", "error_paused", "error_cause", "error_detail", "stop_loss_off",
        "starting_step", "next_round_utc", "blocked_until", "uncanceled", "as_of",
    ]  # fmt: skip
    status = TradeStatusView(False, False, None, None, False, None, None)
    assert (status.blocked_until, status.uncanceled, status.as_of) == (None, (), None)
    assert names(CoinTradeView) == [
        "market", "price", "price_read_utc", "holding", "sellable_qty", "value", "stop_line",
        "dust", "last_direction", "last_round", "open_orders", "buy_blocked", "caution",
        "sell_all", "as_of",
    ]  # fmt: skip
    assert names(TodayView) == [
        "realized_pnl", "daily_loss_limit", "used", "budget", "paper_krw", "limit_reached", "as_of",
    ]  # fmt: skip
    assert names(RoundNowView) == ["round_number", "stage", "next_round_utc", "paused", "as_of"]
    assert names(PerformanceRow) == [
        "market", "ok_rounds", "hits", "countable", "realized_pnl", "wins", "sells",
        "unrealized_pnl", "asset_change", "diff_mean", "diff_abs_mean",
    ]  # fmt: skip
    assert names(PerformanceView) == ["rows", "as_of"]
    assert [(m.name, m.value) for m in HistoryTab] == [
        ("ROUNDS", "rounds"),
        ("JUDGMENTS", "judgments"),
        ("ORDERS", "orders"),
        ("FILLS", "fills"),
        ("PAUSES", "pauses"),
    ]
    assert names(HistoryRow) == ["key", "cells", "detail", "report_id", "market"]
    assert HistoryRow("k", ("a",), (("이름", "값"),), None).market is None
    assert names(HistoryView) == ["tab", "market", "day", "columns", "rows", "state"]
    assert names(BelowStopView) == ["market", "avg_price", "price", "stop_line"]
    for cls in (TodayView, RoundNowView, PerformanceView, CoinTradeView):
        assert dataclasses.fields(cls)[-1].name == "as_of"
        assert dataclasses.fields(cls)[-1].default is None


def test_the_new_views_are_ui_updates_and_the_helper_rows_are_not() -> None:
    members = set(
        typing.get_args(UiUpdate.__value__ if hasattr(UiUpdate, "__value__") else UiUpdate)
    )
    for cls in (
        TradeStatusView, CoinTradeView, TodayView, RoundNowView, PerformanceView, HistoryView,
        BelowStopView,
    ):  # fmt: skip
        assert cls in members, cls.__name__
    assert PerformanceRow not in members
    assert HistoryRow not in members


def test_the_old_views_get_the_new_fields_last_with_defaults() -> None:
    assert names(ReportView)[-3:] == ["order_summary", "no_order_reason", "elapsed_order_seconds"]
    assert names(SettingsView)[-1] == "trade"
    assert SettingsView().trade is None
    assert names(SaveResult)[-1] == "field_errors"
    plain = SaveResult("settings", True)
    assert dict(plain.field_errors) == {}
    with pytest.raises(TypeError):
        plain.field_errors["x"] = "y"  # type: ignore[index]  # 읽기만 되는 빈 묶음
    assert SAVE_TARGET_EXPORT == "history_export"
    errors = SaveResult("settings", False, field_errors={"budget": "글"})
    assert errors.field_errors["budget"] == "글"
    chosen = SettingsView(trade=settings())
    assert chosen.trade is not None
    assert chosen.trade.backup_days == 7


def test_a_history_view_and_a_trade_status_view_can_be_built() -> None:
    view = HistoryView(HistoryTab.ORDERS, None, date(2026, 1, 2), ("열",), (), LoadState.READY)
    assert view.market is None
    status = TradeStatusView(False, True, ErrorPauseCause.EXCHANGE, "일시 차단", True, None, NOW)
    assert status.error_cause is ErrorPauseCause.EXCHANGE


# --- 경계 ---------------------------------------------------------------------------------------


class _Book:
    def get_best_prices(self, market: str) -> tuple[Decimal, Decimal]:
        return D("101"), D("100")

    def get_tick_size(self, market: str) -> Decimal:
        return D("1")

    def get_minute_candles(
        self, market: str, *, to: datetime | None, count: int
    ) -> tuple[MinuteCandle, ...]:
        return ()


def test_the_book_source_port_and_the_minute_candle() -> None:
    assert isinstance(_Book(), BookSource)
    assert not isinstance(object(), BookSource)
    methods = ("get_best_prices", "get_tick_size", "get_minute_candles")
    assert {name for name in dir(BookSource) if name.startswith("get_")} == set(methods)
    for missing in methods:  # 셋 가운데 하나라도 빠지면 이 경계가 아니다
        partial = type("Partial", (), {n: getattr(_Book, n) for n in methods if n != missing})
        assert not isinstance(partial(), BookSource), missing
    assert names(MinuteCandle) == ["market", "start", "high", "low", "close"]
    candle = MinuteCandle("KRW-AAA", NOW, D("3"), D("1"), D("2"))
    assert candle.close == D("2")
    quote_methods = {"list_markets", "get_candles", "get_price"}
    assert not quote_methods & set(dir(BookSource))  # 다른 경계에 메서드를 더하지 않는다


def test_the_candle_unit_names_are_unchanged_by_the_minute_candle() -> None:
    assert [m.value for m in core.CandleUnit] == ["minute30", "minute60", "minute240", "day"]


# --- 내보내기와 import ---------------------------------------------------------------------------


def test_every_new_name_is_exported_by_core() -> None:
    for name in (
        "TradeMethod", "TradeSettings", "OrderRules", "PaperHolding", "PaperAccount",
        "OrderRecord", "FillRecord", "OrderPlan", "NoOrderReason", "RoundStage", "PauseKind",
        "ErrorPauseCause", "TradeStatusView", "CoinTradeView", "TodayView", "RoundNowView",
        "PerformanceView", "PerformanceRow", "HistoryView", "HistoryTab", "HistoryRow",
        "BelowStopView", "BookSource", "MinuteCandle", "SAVE_TARGET_EXPORT", "TOLERANCE_MAX",
        "PAPER_FEE_RATE", "TRADE_METHOD_TITLES",
    ):  # fmt: skip
        assert name in core.__all__, name
        assert hasattr(core, name), name
    assert len(core.__all__) == len(set(core.__all__))


def test_the_trade_module_imports_only_the_standard_library_and_core() -> None:
    source = Path(trade_module.__file__).read_text(encoding="utf-8")
    roots = {
        node.module.split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert roots <= {"coin_report_trader", *sys.stdlib_module_names}
