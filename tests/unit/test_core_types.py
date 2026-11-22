"""M1 공용 타입(M1 명세 3.4)의 시험: 기본 값, 퍼센트 글, 화면이 받는 값, 시계, 오류, 경계."""

import copy
import dataclasses
import pickle
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

import coin_report_trader.core as core
from coin_report_trader.core import (
    ALERT_CATEGORY_TITLES,
    INSUFFICIENT_TEXT,
    KST,
    MODE_TITLES,
    SAVE_TARGET_ALERT_DELETE,
    SAVE_TARGET_SETTINGS,
    AlertCategory,
    AlertDetail,
    AlertsView,
    AlertView,
    Candle,
    CandleUnit,
    Clock,
    CoinView,
    LoadState,
    Market,
    MarketsView,
    Mode,
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
    format_percent,
    format_price,
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
    assert [u.value for u in CandleUnit] == ["minute30", "minute60", "minute240", "day"]
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


# --- 통계 글 -----------------------------------------------------------------------


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


# --- 화면이 받는 값 ---------------------------------------------------------------------------


def test_load_states() -> None:
    assert [s.value for s in LoadState] == ["loading", "ready", "no_data", "failed"]


def test_coin_view_defaults() -> None:
    view = CoinView("KRW-AAA", LoadState.NO_DATA)
    assert not hasattr(view, "periods")  # 옛 기간별 통계와 지표는 51번이 지웠다
    assert not hasattr(view, "indicator")
    assert view.stats is None
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


def test_markets_and_settings_view_added_fields() -> None:
    blocked_until = datetime(2026, 1, 1, 1, 30, tzinfo=UTC)
    assert MarketsView(LoadState.LOADING).blocked_until is None
    assert SettingsView().state is LoadState.READY
    # 새 필드는 맨 뒤에 기본값과 함께 있어, 위치 인자로 만든 값은 그대로 통과한다.
    assert MarketsView(LoadState.READY, ()) == MarketsView(LoadState.READY, (), None, None)
    assert SettingsView(("KRW-AAA",), 7, True) == SettingsView(
        ("KRW-AAA",), 7, True, LoadState.READY
    )
    blocked = MarketsView(LoadState.FAILED, (), "일시 차단", blocked_until)
    assert (blocked.error_name, blocked.blocked_until) == ("일시 차단", blocked_until)
    failed = SettingsView(state=LoadState.FAILED)
    assert (failed.coins, failed.retention_days, failed.first_setup_done) == ((), None, False)
    assert failed.state is LoadState.FAILED


def test_views_carry_as_of() -> None:
    now = datetime(2026, 1, 1, 14, 50, tzinfo=UTC)
    assert CoinView("KRW-AAA", LoadState.LOADING).as_of is None
    assert MarketsView(LoadState.LOADING).as_of is None
    # 새 필드는 맨 뒤에 기본값과 함께 있어, 위치 인자로 만든 값은 그대로 통과한다.
    assert CoinView("KRW-AAA", LoadState.NO_DATA) == CoinView(
        "KRW-AAA", LoadState.NO_DATA, False, None, None, None, None
    )
    assert MarketsView(LoadState.READY, (), None, None) == MarketsView(
        LoadState.READY, (), None, None, None
    )
    assert CoinView("KRW-AAA", LoadState.LOADING, as_of=now).as_of == now
    assert MarketsView(LoadState.FAILED, as_of=now).as_of == now


def test_format_price() -> None:
    assert format_price(Decimal("132")) == "132"
    assert format_price(Decimal("132.0")) == "132"
    assert format_price(Decimal("1234567.50")) == "1,234,567.5"
    assert format_price(Decimal("1E-5")) == "0.00001"
    # 자리를 줄이거나 반올림하지 않는다. 지수 꼴이 없다. 쉼표는 정수 부분에만 붙는다.
    assert format_price(Decimal("0.000120")) == "0.00012"
    assert format_price(Decimal("1E+3")) == "1,000"
    assert format_price(Decimal("12345.123456789")) == "12,345.123456789"
    assert format_price(Decimal("0.00")) == "0"
    for bad in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(ValueError, match="유한하지"):
            format_price(Decimal(bad))


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


class _ClockWithoutWait:
    """`wait`가 없는 옛 시계의 모양."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return 0.0

    def sleep(self, seconds: float) -> None:
        return None


class _Actions:
    def refresh_markets(self) -> None: ...

    def save_settings(
        self, coins: tuple[str, ...], retention_days: int, *, first_setup: bool
    ) -> None: ...

    def load_reports(self, market: str) -> None: ...

    def load_report(self, report_id: int) -> None: ...

    def retry_candles(self) -> None: ...

    def mark_seen(self, alert_ids: tuple[int, ...]) -> None: ...

    def delete_alert(self, alert_id: int) -> None: ...

    def clear_alerts(self, category: AlertCategory) -> None: ...

    def save_webhook(self, category: AlertCategory, url: str) -> None: ...

    def delete_webhook(self, category: AlertCategory) -> None: ...

    def send_test(self, category: AlertCategory) -> None: ...

    def pause(self) -> None: ...

    def pause_and_sell_all(self) -> None: ...

    def resume_user(self) -> None: ...

    def resume_error(self) -> None: ...

    def load_history(self, tab: Any, market: str | None, day: Any) -> None: ...

    def export_history(self, tab: Any, market: str | None, path: str) -> None: ...


def test_system_clock_wait() -> None:
    clock = SystemClock()
    event = threading.Event()
    assert clock.wait(event, 0) is False
    started = time.monotonic()
    assert clock.wait(event, 0.01) is False  # 시간이 다 흘렀다
    assert time.monotonic() - started >= 0.005
    event.set()
    assert clock.wait(event, 0) is True
    assert clock.wait(event, 60) is True  # 이미 선 event는 기다리지 않는다


def test_system_clock_wait_wakes_when_another_thread_sets_the_event() -> None:
    clock = SystemClock()
    event = threading.Event()
    threading.Timer(0.02, event.set).start()
    assert clock.wait(event, 30) is True


@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf"), float("-inf")])
def test_system_clock_wait_refuses_bad_seconds(bad: float) -> None:
    with pytest.raises(ValueError, match="0 이상"):
        SystemClock().wait(threading.Event(), bad)


def test_system_clock_wait_accepts_seconds_beyond_the_timeout_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`event.wait`가 받는 가장 큰 초보다 큰 초도 오류 없이 끝까지 나누어 기다린다."""
    monkeypatch.setattr(threading, "TIMEOUT_MAX", 0.005)
    clock = SystemClock()
    event = threading.Event()
    started = time.monotonic()
    assert clock.wait(event, 0.03) is False
    assert time.monotonic() - started >= 0.025
    threading.Timer(0.01, event.set).start()
    assert clock.wait(event, 30) is True


def test_system_clock_wait_beyond_the_real_timeout_limit_waits_in_pieces() -> None:
    """진짜 `threading.TIMEOUT_MAX`보다 큰 초도 오류 없이 기다리고, event가 서면 바로 참이다."""
    clock = SystemClock()
    event = threading.Event()
    huge = float(threading.TIMEOUT_MAX) * 2
    timer = threading.Timer(0.02, event.set)
    timer.start()
    started = time.monotonic()
    assert clock.wait(event, huge) is True
    assert time.monotonic() - started < 5
    timer.join()
    assert clock.wait(event, huge) is True  # 이미 선 event
    assert clock.wait(threading.Event(), 0) is False


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
    assert isinstance(SystemClock(), Clock)
    assert not isinstance(_ClockWithoutWait(), Clock)


def test_ui_actions_has_the_actions_of_the_spec() -> None:
    names = {name for name in dir(UiActions) if not name.startswith("_")}
    assert names == {
        "refresh_markets",
        "save_settings",
        "load_reports",
        "load_report",
        "retry_candles",
        "mark_seen",
        "delete_alert",
        "clear_alerts",
        "save_webhook",
        "delete_webhook",
        "send_test",
        "pause",
        "pause_and_sell_all",
        "resume_user",
        "resume_error",
        "load_history",
        "export_history",
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
