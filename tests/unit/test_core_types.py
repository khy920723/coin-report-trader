"""M1 공용 타입(M1 명세 3.4)의 시험: 기본 값, 통계와 지표, 화면이 받는 값, 시계, 오류, 경계."""

import copy
import dataclasses
import pickle
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

import coin_report_trader.core as core
from coin_report_trader.core import (
    ALERT_CATEGORY_TITLES,
    INSUFFICIENT_TEXT,
    KST,
    MODE_TITLES,
    PERIODS,
    SAVE_TARGET_ALERT_DELETE,
    SAVE_TARGET_SETTINGS,
    AlertCategory,
    AlertDetail,
    AlertsView,
    AlertView,
    Candle,
    CandleIndicator,
    CandleUnit,
    Clock,
    CoinView,
    LoadState,
    Market,
    MarketsView,
    Mode,
    PeriodStats,
    QuoteError,
    QuoteSource,
    SaveResult,
    SecretStore,
    SecretStoreError,
    SettingsView,
    StorageError,
    SystemClock,
    UiActions,
    UiContext,
    UiUpdate,
    WebhookError,
    WebhookSender,
    WebhookView,
    coin_label,
    empty_periods,
    format_percent,
)

NOW = datetime(2026, 1, 2, tzinfo=UTC)
D = Decimal


# --- 기본 값 ----------------------------------------------------------------------------


def test_alert_categories_follow_the_tab_order_and_have_titles() -> None:
    assert [c.value for c in AlertCategory] == [
        "trade",
        "report",
        "limit",
        "holding",
        "error",
        "other",
    ]
    assert list(ALERT_CATEGORY_TITLES) == list(AlertCategory)
    assert list(ALERT_CATEGORY_TITLES.values()) == [
        "매매 알림",
        "보고서 알림",
        "제한 알림",
        "보유 알림",
        "오류 알림",
        "기타 알림",
    ]
    with pytest.raises(TypeError):
        ALERT_CATEGORY_TITLES[AlertCategory.TRADE] = "x"  # type: ignore[index]


def test_modes_and_their_titles() -> None:
    assert [m.value for m in Mode] == ["paper", "live"]
    assert MODE_TITLES[Mode.PAPER] == "모의매매"
    assert MODE_TITLES[Mode.LIVE] == "실거래"


def test_candle_units() -> None:
    assert [u.value for u in CandleUnit] == ["day", "minute60"]
    assert CandleUnit("minute60") is CandleUnit.MINUTE_60


def test_candle_and_market_are_frozen_values() -> None:
    candle = Candle(
        "KRW-AAA", CandleUnit.DAY, NOW, D("1"), D("3"), D("0.5"), D("2"), D("10"), D("20")
    )
    assert candle.value == D("20")
    assert candle == dataclasses.replace(candle)
    with pytest.raises(dataclasses.FrozenInstanceError):
        candle.close = D("9")  # type: ignore[misc]
    market = Market("KRW-AAA", "가짜코인", caution=True)
    assert (market.market, market.name, market.caution) == ("KRW-AAA", "가짜코인", True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        market.name = "x"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("market", "label"),
    [("KRW-AAA", "AAA"), ("KRW-A1B", "A1B"), ("AAA", "AAA"), ("BTC-AAA", "BTC-AAA"), ("", "")],
)
def test_coin_label_drops_the_leading_krw(market: str, label: str) -> None:
    assert coin_label(market) == label


def test_coin_label_drops_only_the_first_prefix() -> None:
    assert coin_label("KRW-KRW-X") == "KRW-X"


# --- 통계와 지표의 값 -----------------------------------------------------------------------


def test_periods_follow_the_table() -> None:
    assert PERIODS == (
        ("전체", CandleUnit.DAY),
        ("1년", CandleUnit.DAY),
        ("3개월", CandleUnit.DAY),
        ("1개월", CandleUnit.DAY),
        ("1주", CandleUnit.MINUTE_60),
    )
    periods = empty_periods()
    assert [(p.period, p.unit) for p in periods] == list(PERIODS)
    assert all(p.count == 0 for p in periods)


def test_period_stats_values_default_to_none() -> None:
    stats = PeriodStats("1년", CandleUnit.DAY, 1)
    for name in (
        "first_start",
        "last_start",
        "open_price",
        "close_price",
        "change",
        "high",
        "low",
        "from_high",
        "max_drawdown",
        "volatility",
        "up_ratio",
        "avg_value",
    ):
        assert getattr(stats, name) is None
    assert stats.count == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        stats.count = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    ("value", "signed", "shown"),
    [
        (D("0.1234"), True, "+12.34%"),
        (D("-0.1"), True, "-10.00%"),
        (D("0.1535"), False, "15.35%"),
        (D("0"), True, "0.00%"),
        (D("0"), False, "0.00%"),
        (D("-0.00001"), True, "0.00%"),  # 반올림해 0이 되면 부호가 없다
        (D("0.00001"), True, "0.00%"),
        (D("0.00125"), True, "+0.12%"),  # 짝수로 맞춘다(ROUND_HALF_EVEN)
        (D("0.00135"), True, "+0.14%"),
        (D("12.3456"), True, "+1234.56%"),
        (D("0.15351"), False, "15.35%"),
        (D("-0.5"), False, "-50.00%"),
        (D("1E-10"), True, "0.00%"),
        (None, True, INSUFFICIENT_TEXT),
        (None, False, INSUFFICIENT_TEXT),
        (D("NaN"), True, INSUFFICIENT_TEXT),
        (D("Infinity"), True, INSUFFICIENT_TEXT),
    ],
)
def test_format_percent(value: Decimal | None, signed: bool, shown: str) -> None:
    assert format_percent(value, signed=signed) == shown


def test_the_insufficient_text() -> None:
    assert INSUFFICIENT_TEXT == "봉이 모자람"


def test_candle_indicator_items_follow_the_table() -> None:
    indicator = CandleIndicator(
        trend_1y=D("0.1234"),
        trend_3m=D("-0.1"),
        vs_ma20=D("0"),
        vs_ma120=D("0.0001"),
        volatility_1y=D("0.1535"),
        volatility_1w=D("0.02"),
        max_drawdown_1y=D("-0.4567"),
    )
    assert indicator.items() == (
        ("1년 변화", "+12.34%"),
        ("3개월 변화", "-10.00%"),
        ("20일 평균 대비", "0.00%"),
        ("120일 평균 대비", "+0.01%"),
        ("1년 일간 변동성", "15.35%"),
        ("1주 시간 변동성", "2.00%"),
        ("1년 최대 낙폭", "-45.67%"),
    )


def test_candle_indicator_without_material_says_so() -> None:
    items = CandleIndicator().items()
    assert [title for title, _ in items] == [
        "1년 변화",
        "3개월 변화",
        "20일 평균 대비",
        "120일 평균 대비",
        "1년 일간 변동성",
        "1주 시간 변동성",
        "1년 최대 낙폭",
    ]
    assert {shown for _, shown in items} == {"봉이 모자람"}
    partial = CandleIndicator(trend_1y=D("0.5")).items()
    assert partial[0] == ("1년 변화", "+50.00%")
    assert partial[1] == ("3개월 변화", "봉이 모자람")


def test_candle_indicator_has_no_verdict_field() -> None:
    names = {f.name for f in dataclasses.fields(CandleIndicator)}
    assert names == {
        "trend_1y",
        "trend_3m",
        "vs_ma20",
        "vs_ma120",
        "volatility_1y",
        "volatility_1w",
        "max_drawdown_1y",
    }


# --- 화면이 받는 값 ---------------------------------------------------------------------------


def test_load_states() -> None:
    assert [s.value for s in LoadState] == ["loading", "ready", "no_data", "failed"]


def test_coin_view_defaults_to_five_empty_periods() -> None:
    view = CoinView("KRW-AAA", LoadState.NO_DATA)
    assert [(p.period, p.unit, p.count) for p in view.periods] == [
        (name, unit, 0) for name, unit in PERIODS
    ]
    assert view.indicator is None
    assert view.last_incomplete is False
    assert (view.fetched_at, view.error_name, view.blocked_until) == (None, None, None)
    assert view == CoinView("KRW-AAA", LoadState.NO_DATA)
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.state = LoadState.READY  # type: ignore[misc]


def test_coin_view_holds_a_blocked_failure() -> None:
    until = NOW + timedelta(minutes=5)
    view = CoinView(
        "KRW-AAA",
        LoadState.FAILED,
        error_name="일시 차단",
        blocked_until=until,
        last_incomplete=True,
    )
    assert (view.error_name, view.blocked_until, view.last_incomplete) == ("일시 차단", until, True)


def test_markets_and_settings_views() -> None:
    markets = MarketsView(LoadState.READY, (Market("KRW-AAA", "가짜", caution=False),))
    assert markets.markets[0].market == "KRW-AAA"
    assert markets.error_name is None
    assert MarketsView(LoadState.FAILED, error_name="시간 초과").markets == ()
    settings = SettingsView(("KRW-AAA", "KRW-BBB"), 30, True)
    assert (settings.coins, settings.retention_days, settings.first_setup_done) == (
        ("KRW-AAA", "KRW-BBB"),
        30,
        True,
    )
    assert SettingsView() == SettingsView((), None, False)


def test_alert_views() -> None:
    detail = AlertDetail("봉 받기", "KRW-AAA", "네트워크 끊김", "내용", "다음에 다시 받습니다.")
    alert = AlertView(
        id=1,
        created_at=NOW,
        mode=Mode.PAPER,
        category=AlertCategory.ERROR,
        code="A-1",
        coin="KRW-AAA",
        text="글",
        detail=detail,
    )
    assert alert.seen is False
    assert alert.discord_failed is False
    assert alert.detail is detail
    view = AlertsView((alert,), {AlertCategory.ERROR: 1}, LoadState.READY)
    assert view.unseen[AlertCategory.ERROR] == 1
    assert isinstance(view.unseen, Mapping)
    empty = AlertsView(())
    assert empty.unseen == {}
    assert empty.state is LoadState.READY
    with pytest.raises(TypeError):
        empty.unseen[AlertCategory.ERROR] = 1  # type: ignore[index]


def test_webhook_view_keeps_two_messages_apart() -> None:
    view = WebhookView(AlertCategory.ERROR, saved=True, save_message="저장", test_message="확인")
    assert (view.save_message, view.test_message) == ("저장", "확인")
    replaced = dataclasses.replace(view, test_message=None)
    assert replaced.save_message == "저장"
    assert WebhookView(AlertCategory.TRADE, saved=False).save_message is None
    assert not any("url" in f.name or "address" in f.name for f in dataclasses.fields(WebhookView))


def test_save_result() -> None:
    assert SAVE_TARGET_SETTINGS == "settings"
    assert SAVE_TARGET_ALERT_DELETE == "alert_delete"
    assert SaveResult("settings", ok=True).message is None
    assert SaveResult("alert_delete", ok=False, message="x").ok is False


def test_ui_update_is_one_of_the_views_or_save_result() -> None:
    updates = [
        CoinView("KRW-AAA", LoadState.LOADING),
        MarketsView(LoadState.LOADING),
        SettingsView(),
        AlertsView(()),
        WebhookView(AlertCategory.OTHER, saved=False),
        SaveResult("settings", ok=True),
    ]
    assert all(isinstance(update, UiUpdate) for update in updates)
    assert not isinstance(
        AlertView(1, NOW, Mode.PAPER, AlertCategory.OTHER, "A-30", None, "x"), UiUpdate
    )
    assert not isinstance("text", UiUpdate)


# --- 시계와 한국 시간 -----------------------------------------------------------------------


def test_kst_is_a_fixed_offset_of_nine_hours() -> None:
    assert KST.utcoffset(None) == timedelta(hours=9)
    moment = datetime(2026, 1, 1, 0, 0, tzinfo=UTC).astimezone(KST)
    assert (moment.hour, moment.day) == (9, 1)
    assert datetime(2026, 1, 1, 9, 0, tzinfo=KST) == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


def test_system_clock() -> None:
    clock = SystemClock()
    assert isinstance(clock, Clock)
    before = datetime.now(UTC)
    now = clock.now()
    after = datetime.now(UTC)
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
    assert before <= now <= after
    first = clock.monotonic()
    assert clock.monotonic() >= first
    started = time.monotonic()
    clock.sleep(0.01)
    assert time.monotonic() - started >= 0.005


# --- 오류 -----------------------------------------------------------------------------------


def test_named_errors_carry_the_error_name_and_wait_time() -> None:
    error = QuoteError("요청 한도 초과", 1.5)
    assert (error.error_name, error.retry_after) == ("요청 한도 초과", 1.5)
    assert str(error) == "요청 한도 초과"
    assert QuoteError("시간 초과").retry_after is None
    hook = WebhookError("디스코드 한도 초과", retry_after=2)
    assert (hook.error_name, hook.retry_after) == ("디스코드 한도 초과", 2)
    store = SecretStoreError("보관소 오류")
    assert store.error_name == "보관소 오류"
    assert isinstance(StorageError("x"), Exception)


@pytest.mark.parametrize(
    "error",
    [
        QuoteError("일시 차단", 120.0),
        WebhookError("디스코드 시간 초과"),
        SecretStoreError("보관소 오류"),
    ],
)
def test_errors_survive_copy_and_pickle(error: Exception) -> None:
    for clone in (copy.copy(error), copy.deepcopy(error), pickle.loads(pickle.dumps(error))):
        assert type(clone) is type(error)
        assert clone.error_name == error.error_name  # type: ignore[attr-defined]
        assert getattr(clone, "retry_after", None) == getattr(error, "retry_after", None)


# --- 경계(Protocol)와 UiContext -------------------------------------------------------------


class _Quote:
    def list_markets(self) -> tuple[Market, ...]:
        return ()

    def get_candles(
        self, market: str, unit: CandleUnit, *, to: datetime | None, count: int
    ) -> tuple[Candle, ...]:
        return ()


class _Sender:
    def send(self, url: str, text: str) -> None:
        return None


class _Store:
    def get(self, name: str) -> str | None:
        return None

    def set(self, name: str, value: str) -> None:
        return None

    def delete(self, name: str) -> None:
        return None


class _Actions:
    def refresh_markets(self) -> None: ...

    def save_settings(
        self, coins: tuple[str, ...], retention_days: int, *, first_setup: bool
    ) -> None: ...

    def retry_candles(self) -> None: ...

    def mark_seen(self, alert_ids: tuple[int, ...]) -> None: ...

    def delete_alert(self, alert_id: int) -> None: ...

    def clear_alerts(self, category: AlertCategory) -> None: ...

    def save_webhook(self, category: AlertCategory, url: str) -> None: ...

    def delete_webhook(self, category: AlertCategory) -> None: ...

    def send_test(self, category: AlertCategory) -> None: ...


def test_protocols_match_by_shape() -> None:
    assert isinstance(_Quote(), QuoteSource)
    assert isinstance(_Sender(), WebhookSender)
    assert isinstance(_Store(), SecretStore)
    assert isinstance(_Actions(), UiActions)
    assert not isinstance(object(), QuoteSource)
    assert not isinstance(_Quote(), WebhookSender)
    assert not isinstance(_Sender(), SecretStore)
    assert not isinstance(_Store(), UiActions)
    assert not isinstance(object(), Clock)


def test_ui_actions_has_the_nine_actions_of_the_spec() -> None:
    names = {name for name in dir(UiActions) if not name.startswith("_")}
    assert names == {
        "refresh_markets",
        "save_settings",
        "retry_candles",
        "mark_seen",
        "delete_alert",
        "clear_alerts",
        "save_webhook",
        "delete_webhook",
        "send_test",
    }


def test_ui_context_keeps_the_m0_way_of_making_it_and_adds_actions() -> None:
    context = UiContext("0.0.1", "X")
    assert (context.version, context.data_dir_display, context.actions) == ("0.0.1", "X", None)
    assert UiContext(version="0.0.1", data_dir_display="X") == context
    actions = _Actions()
    with_actions = UiContext("0.0.1", "X", actions)
    assert with_actions.actions is actions
    assert UiContext("0.0.1", "X", actions=actions) == with_actions
    with pytest.raises(dataclasses.FrozenInstanceError):
        context.actions = actions  # type: ignore[misc]


def test_every_exported_name_exists() -> None:
    assert len(set(core.__all__)) == len(core.__all__)
    for name in core.__all__:
        assert hasattr(core, name), name
    # M0에서 있던 이름은 그대로 있다.
    for name in (
        "APP_ID",
        "WINDOW_TITLE",
        "ScreenId",
        "SCREEN_TITLES",
        "StorageError",
        "UiContext",
    ):
        assert name in core.__all__
