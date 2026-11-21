"""현황 화면(설계서 4, M1 명세 3.4의 `CoinView`와 "받아 둔 값")의 시험.

`CoinView`, `PeriodStats`, `CandleIndicator`는 시험이 만든 값으로 직접 만든다. 창을 띄우지 않으므로
보이는지는 `isHidden()`으로, 흐리게 남긴 값은 `isEnabled()`로 본다.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QWidget

from coin_report_trader.core import (
    CandleIndicator,
    CoinView,
    LoadState,
    Market,
    MarketsView,
    PeriodStats,
    ScreenId,
    SettingsView,
    UiActions,
    UiContext,
)
from coin_report_trader.core.stats import PERIODS
from coin_report_trader.ui import MainWindow, create_main_window, current_screen, screen_widget

LOADING = "불러오는 중"
CANDLES_LOADING = "봉을 받는 중입니다."
NO_CANDLES = "아직 받은 봉이 없습니다."
INSUFFICIENT = "봉이 모자람"
BEFORE_SETUP = "처음 설정을 마치면 여기에 코인별 현황이 보입니다."
UNREADABLE = "설정을 읽지 못했습니다. 60분마다 다시 읽습니다."
INCOMPLETE = "마지막 봉은 미완성입니다"
DISCLAIMER = "봉 통계는 계산 결과일 뿐이고, 이득을 보장하지 않습니다."
FETCHED_AT = datetime(2026, 1, 2, 1, 30, tzinfo=UTC)  # 한국 시간 2026-01-02 10:30
FETCHED_LINE = "마지막으로 받은 때 2026-01-02 10:30"
INDICATOR_NAMES = [
    "1년 변화",
    "3개월 변화",
    "20일 평균 대비",
    "120일 평균 대비",
    "1년 일간 변동성",
    "1주 시간 변동성",
    "1년 최대 낙폭",
]


class FakeActions:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def count(self, name: str) -> int:
        return sum(1 for call in self.calls if call[0] == name)

    def refresh_markets(self) -> None:
        self.calls.append(("refresh_markets",))

    def save_settings(
        self, coins, retention_days, *, first_setup, candle_unit=None, candle_count=None
    ) -> None:
        self.calls.append(("save_settings", coins, retention_days, first_setup))

    def retry_candles(self) -> None:
        self.calls.append(("retry_candles",))

    def mark_seen(self, alert_ids) -> None:
        self.calls.append(("mark_seen", alert_ids))

    def delete_alert(self, alert_id) -> None:
        self.calls.append(("delete_alert", alert_id))

    def clear_alerts(self, category) -> None:
        self.calls.append(("clear_alerts", category))

    def save_webhook(self, category, url) -> None:
        self.calls.append(("save_webhook", category))

    def delete_webhook(self, category) -> None:
        self.calls.append(("delete_webhook", category))

    def send_test(self, category) -> None:
        self.calls.append(("send_test", category))


@pytest.fixture
def make_window(qapp: QApplication):
    made: list[MainWindow] = []

    def factory(actions: UiActions | None = None) -> MainWindow:
        window = create_main_window(UiContext("0.0.1", "X", actions))
        made.append(window)
        return window

    yield factory
    for window in made:
        window.deleteLater()


@pytest.fixture
def actions() -> FakeActions:
    return FakeActions()


# --- 값을 만드는 도구 ---


def _period(index: int, count: int = 0, **values) -> PeriodStats:
    name, unit = PERIODS[index]
    return PeriodStats(period=name, unit=unit, count=count, **values)


def _full_periods() -> tuple[PeriodStats, ...]:
    """다섯 기간 모두 값이 있는 만든 값. 설계서 4.2의 8의 예와 같은 꼴이 나온다."""
    return (
        _period(
            0,
            400,
            change=Decimal("0.5"),
            high=Decimal("1234567.50"),
            low=Decimal("0.00012"),
            from_high=Decimal("-0.05"),
            max_drawdown=Decimal("-0.4"),
            volatility=Decimal("0.0321"),
            up_ratio=Decimal("0.51"),
        ),
        _period(
            1,
            365,
            change=Decimal("0.1234"),
            high=Decimal("200"),
            low=Decimal("100.0"),
            from_high=Decimal("-0.1"),
            max_drawdown=Decimal("-0.2"),
            volatility=Decimal("0.03"),
            up_ratio=Decimal("0.55"),
        ),
        _period(2, 90, change=Decimal("0"), high=Decimal("150"), low=Decimal("90")),
        _period(
            3,
            30,
            change=Decimal("0.1234"),
            high=Decimal("132"),
            low=Decimal("99"),
            from_high=Decimal("-0.05"),
            max_drawdown=Decimal("-0.1"),
            volatility=Decimal("0.0321"),
            up_ratio=Decimal("0.6"),
        ),
        _period(4, 1),
    )


def _indicator() -> CandleIndicator:
    return CandleIndicator(
        trend_1y=Decimal("0.1234"),
        trend_3m=Decimal("-0.05"),
        vs_ma20=Decimal("0"),
        volatility_1y=Decimal("0.1535"),
        max_drawdown_1y=Decimal("-0.3"),
    )


def _ready(market: str = "KRW-AAA", **changes) -> CoinView:
    values = {
        "market": market,
        "state": LoadState.READY,
        "periods": _full_periods(),
        "indicator": _indicator(),
        "fetched_at": FETCHED_AT,
    }
    values.update(changes)
    return CoinView(**values)


def _empty(market: str = "KRW-AAA", state: LoadState = LoadState.NO_DATA, **changes) -> CoinView:
    """받아 둔 값이 없는 `CoinView`(값이 모두 `None`, 쓴 봉이 0)."""
    return CoinView(market, state, **changes)


def _only_a_short_period(market: str = "KRW-AAA", **changes) -> CoinView:
    """받아 둔 값은 있는데(쓴 봉이 1) 모든 값이 모자란 `CoinView`."""
    periods = (_period(0, 1), _period(1), _period(2), _period(3), _period(4))
    return CoinView(market, LoadState.NO_DATA, periods, None, False, FETCHED_AT, **changes)


# --- 읽는 도구 ---


def _status(window: MainWindow) -> QWidget:
    return screen_widget(window, ScreenId.STATUS)


def _find(parent: QWidget, kind: type, name: str):
    found = parent.findChild(kind, name)
    assert found is not None, name
    return found


def _text(screen: QWidget, name: str) -> str | None:
    """라벨이 보이면 그 글, 숨겨져 있으면 `None`."""
    label = _find(screen, QLabel, name)
    return None if label.isHidden() else label.text()


def _tone(screen: QWidget, name: str) -> str:
    return _find(screen, QLabel, name).property("tone")


def _shown(screen: QWidget, name: str) -> bool:
    return not _find(screen, QWidget, name).isHidden()


def _indicator_rows(screen: QWidget, market: str) -> list[str]:
    return [_find(screen, QLabel, f"statusCoinIndicatorRow_{market}_{i}").text() for i in range(7)]


def _card_names(screen: QWidget) -> list[str]:
    """코인 칸의 `objectName`을 화면에 놓인 차례(설정 목록 순서)대로."""
    layout = _find(screen, QWidget, "statusCoins").layout()
    return [layout.itemAt(i).widget().objectName() for i in range(layout.count())]


def _cells(screen: QWidget, row: int) -> list[str]:
    return [_find(screen, QLabel, f"statusStatsCell_{row}_{col}").text() for col in range(9)]


def _open(window: MainWindow, *views: CoinView, coins=("KRW-AAA",)) -> QWidget:
    """처음 설정을 마친 `SettingsView`와 `CoinView`를 보내고 현황 화면을 돌려준다."""
    window.apply(SettingsView(coins, 30, True))
    for view in views:
        window.apply(view)
    return _status(window)


# --- 글 하나만 보이는 모습 ---


def _only_the_message(screen: QWidget, text: str, tone: str = "normal") -> None:
    assert _text(screen, "statusMessage") == text
    assert _tone(screen, "statusMessage") == tone
    # 코인 칸, 표, 코인 고르기, "마지막으로 받은 때", "다시 시도"는 보이지 않는다.
    assert _find(screen, QWidget, "statusContent").isHidden()


def test_without_actions_the_status_screen_is_the_before_setup_screen(make_window):
    window = make_window(None)
    screen = _status(window)
    assert _text(screen, "screenTitle") == "현황"
    assert screen.findChild(QLabel, "screenBody") is None
    _only_the_message(screen, BEFORE_SETUP)
    assert current_screen(window) is ScreenId.FIRST_SETUP


def test_before_the_first_settings_view_it_says_loading_and_calls_nothing(make_window, actions):
    window = make_window(actions)
    _only_the_message(_status(window), LOADING)
    # 단추는 보이지 않지만, 눌려도 동작을 부르지 않는다.
    _find(_status(window), QPushButton, "statusStatsRetry").click()
    assert actions.calls == []


def test_before_first_setup_it_says_so_and_never_retries(make_window, actions):
    window = make_window(actions)
    window.apply(SettingsView())
    screen = _status(window)
    _only_the_message(screen, BEFORE_SETUP)
    window.apply(_ready())  # 값이 와도 처음 설정 전이면 글 하나뿐이다.
    _only_the_message(screen, BEFORE_SETUP)
    _find(screen, QPushButton, "statusStatsRetry").click()
    assert actions.count("retry_candles") == 0


def test_unreadable_settings_show_the_warning_line_and_never_retry(make_window, actions):
    window = make_window(actions)
    window.apply(SettingsView(state=LoadState.FAILED))
    screen = _status(window)
    _only_the_message(screen, UNREADABLE, "warning")
    assert current_screen(window) is ScreenId.FIRST_SETUP
    _find(screen, QPushButton, "statusStatsRetry").click()
    assert actions.calls == []
    # 그 뒤에 읽은 `SettingsView`가 오면 그 값대로 그린다.
    window.apply(SettingsView(("KRW-AAA",), 7, True))
    window.apply(_ready())
    assert _text(screen, "statusMessage") is None
    assert current_screen(window) is ScreenId.STATUS
    assert _text(screen, "statusCoinName_KRW-AAA") == "AAA"


# --- 코인 칸의 목록 ---


def test_first_setup_done_selects_status_and_lists_the_coins_in_order_with_loading(
    make_window, actions
):
    window = make_window(actions)
    coins = ("KRW-CCC", "KRW-AAA", "KRW-BBB")
    window.apply(SettingsView(coins, 30, True))
    assert current_screen(window) is ScreenId.STATUS
    screen = _status(window)
    names = _card_names(screen)
    assert names == [f"statusCoin_{m}" for m in coins]
    for market in coins:
        # 그 코인의 첫 `CoinView`가 오기 전이다.
        assert _text(screen, f"statusCoinNote_{market}") == LOADING
        assert not _shown(screen, f"statusCoinIndicator_{market}")
        assert not _shown(screen, f"statusCoinRetry_{market}")
        # 그 코인의 첫 값이 오기 전에는 보장 아님 글을 보이지 않는다.
        assert not _shown(screen, f"statusCoinDisclaimer_{market}")
    assert _text(screen, "statusCoinName_KRW-CCC") == "CCC"
    assert _text(screen, "statusStatsNote") == LOADING
    assert not _shown(screen, "statusStatsTable")
    chooser = _find(screen, QComboBox, "statusStatsCoin")
    assert [chooser.itemText(i) for i in range(chooser.count())] == ["CCC", "AAA", "BBB"]
    assert actions.calls == []


def test_the_coin_cards_show_no_caution_and_no_empty_material_lines(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _ready(), coins=("KRW-AAA",))
    window.apply(MarketsView(LoadState.READY, (Market("KRW-AAA", "가짜", caution=True),)))
    texts = [label.text() for label in screen.findChildren(QLabel)]
    assert not any("유의" in text for text in texts)
    assert not any("아직 없습니다" in text for text in texts)


def test_at_most_three_coins_are_drawn(make_window):
    window = make_window(FakeActions())
    screen = _open(window, coins=("KRW-AAA", "KRW-BBB", "KRW-CCC", "KRW-DDD"))
    assert screen.findChild(QWidget, "statusCoin_KRW-DDD") is None
    assert _find(screen, QComboBox, "statusStatsCoin").count() == 3


# --- READY ---


def test_ready_shows_indicator_rows_in_order_and_the_disclaimer(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _ready())
    assert _text(screen, "statusCoinNote_KRW-AAA") is None
    assert _shown(screen, "statusCoinIndicator_KRW-AAA")
    assert _find(screen, QWidget, "statusCoinIndicator_KRW-AAA").isEnabled()
    rows = _indicator_rows(screen, "KRW-AAA")
    assert rows == [
        "1년 변화 +12.34%",
        "3개월 변화 -5.00%",
        "20일 평균 대비 0.00%",
        "120일 평균 대비 봉이 모자람",
        "1년 일간 변동성 15.35%",
        "1주 시간 변동성 봉이 모자람",
        "1년 최대 낙폭 -30.00%",
    ]
    assert [row.rsplit(" ", 1)[0] for row in rows][:3] == INDICATOR_NAMES[:3]
    assert _text(screen, "statusCoinDisclaimer_KRW-AAA") == DISCLAIMER
    assert not _shown(screen, "statusCoinRetry_KRW-AAA")
    assert not _shown(screen, "statusCoinIncomplete_KRW-AAA")


def test_ready_stats_table_has_five_rows_and_nine_cells_in_the_design_form(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _ready())
    headers = [_find(screen, QLabel, f"statusStatsHeader_{col}").text() for col in range(9)]
    assert headers == [
        "기간",
        "봉 수",
        "변화",
        "최고가",
        "최저가",
        "최고가 대비",
        "최대 낙폭",
        "변동성",
        "상승 봉 비율",
    ]
    assert _shown(screen, "statusStatsTable")
    assert _find(screen, QWidget, "statusStatsTable").isEnabled()
    assert _cells(screen, 0) == [
        "전체",
        "봉 400",
        "+50.00%",
        "1,234,567.5원",
        "0.00012원",
        "-5.00%",
        "-40.00%",
        "3.21%",
        "51.00%",
    ]
    assert _cells(screen, 3) == [
        "1개월",
        "봉 30",
        "+12.34%",
        "132원",
        "99원",
        "-5.00%",
        "-10.00%",
        "3.21%",
        "60.00%",
    ]
    # 값이 `None`인 칸은 "봉이 모자람"이고 봉 수는 그대로 보인다.
    assert _cells(screen, 4) == ["1주", "봉 1"] + [INSUFFICIENT] * 7
    assert _cells(screen, 2)[2:] == [
        "0.00%",
        "150원",
        "90원",
        INSUFFICIENT,
        INSUFFICIENT,
        INSUFFICIENT,
        INSUFFICIENT,
    ]
    assert _text(screen, "statusStatsFetched") == FETCHED_LINE
    assert _text(screen, "statusStatsNote") is None
    assert _text(screen, "statusStatsIncomplete") is None


def test_the_stats_chooser_switches_the_table_to_that_coin(make_window):
    window = make_window(FakeActions())
    other = _ready(
        "KRW-BBB",
        periods=(
            _period(0, 7, change=Decimal("-0.2"), high=Decimal("5"), low=Decimal("3")),
            *(_period(i) for i in range(1, 5)),
        ),
    )
    screen = _open(window, _ready(), other, coins=("KRW-AAA", "KRW-BBB"))
    chooser = _find(screen, QComboBox, "statusStatsCoin")
    assert _cells(screen, 0)[1] == "봉 400"
    chooser.setCurrentIndex(1)
    assert _cells(screen, 0)[:4] == ["전체", "봉 7", "-20.00%", "5원"]
    # 같은 코인의 새 값은 고른 코인이 그대로인 채 표만 바꾼다.
    window.apply(_ready("KRW-BBB", periods=(_period(0, 8), *(_period(i) for i in range(1, 5)))))
    assert chooser.currentIndex() == 1
    assert _cells(screen, 0)[1] == "봉 8"


def test_a_missing_indicator_draws_seven_names_with_insufficient(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _ready(indicator=None))
    rows = _indicator_rows(screen, "KRW-AAA")
    assert rows == [f"{name} 봉이 모자람" for name in INDICATOR_NAMES]
    assert _shown(screen, "statusCoinIndicator_KRW-AAA")


def test_last_incomplete_shows_next_to_the_indicator_and_under_the_table(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _ready(last_incomplete=True))
    assert _text(screen, "statusCoinIncomplete_KRW-AAA") == INCOMPLETE
    assert _text(screen, "statusStatsIncomplete") == INCOMPLETE


# --- 받는 중 ---


def test_loading_with_values_keeps_the_values_and_shows_the_note_above(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _ready(state=LoadState.LOADING))
    assert _text(screen, "statusCoinNote_KRW-AAA") == CANDLES_LOADING
    assert _text(screen, "statusStatsNote") == CANDLES_LOADING
    assert _shown(screen, "statusCoinIndicator_KRW-AAA")
    assert _find(screen, QWidget, "statusCoinIndicator_KRW-AAA").isEnabled()
    assert _shown(screen, "statusStatsTable")
    assert _find(screen, QWidget, "statusStatsTable").isEnabled()
    assert not _shown(screen, "statusCoinRetry_KRW-AAA")


# --- 아직 받은 봉이 없음, 봉이 모자람 ---


def test_no_data_without_values_and_never_fetched(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _empty())
    assert _text(screen, "statusCoinNote_KRW-AAA") == NO_CANDLES
    assert _text(screen, "statusStatsNote") == NO_CANDLES
    assert not _shown(screen, "statusCoinIndicator_KRW-AAA")
    assert not _shown(screen, "statusStatsTable")
    assert _text(screen, "statusStatsFetched") is None
    assert not _shown(screen, "statusCoinRetry_KRW-AAA")
    assert _shown(screen, "statusStatsRetry")
    assert _text(screen, "statusCoinDisclaimer_KRW-AAA") == DISCLAIMER


def test_no_data_without_values_but_fetched_says_insufficient_with_the_fetched_line(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _empty(fetched_at=FETCHED_AT))
    assert _text(screen, "statusCoinNote_KRW-AAA") == INSUFFICIENT
    assert _text(screen, "statusStatsNote") == INSUFFICIENT
    assert not _shown(screen, "statusCoinIndicator_KRW-AAA")
    assert not _shown(screen, "statusStatsTable")
    assert _text(screen, "statusStatsFetched") == FETCHED_LINE
    assert not _shown(screen, "statusCoinRetry_KRW-AAA")
    assert _text(screen, "statusCoinDisclaimer_KRW-AAA") == DISCLAIMER


def test_no_data_with_values_draws_the_rows_with_insufficient(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _only_a_short_period())
    assert _shown(screen, "statusStatsTable")
    assert _text(screen, "statusStatsNote") is None
    assert _text(screen, "statusCoinNote_KRW-AAA") is None
    assert _cells(screen, 0) == ["전체", "봉 1"] + [INSUFFICIENT] * 7
    assert _indicator_rows(screen, "KRW-AAA") == [f"{n} 봉이 모자람" for n in INDICATOR_NAMES]
    assert _text(screen, "statusStatsFetched") == FETCHED_LINE


def test_no_data_with_values_but_never_fetched_has_no_fetched_line(make_window):
    window = make_window(FakeActions())
    never_fetched = CoinView(
        "KRW-AAA",
        LoadState.NO_DATA,
        (_period(0, 1), _period(1), _period(2), _period(3), _period(4)),
    )
    screen = _open(window, never_fetched)
    assert _shown(screen, "statusStatsTable")
    assert _cells(screen, 0)[2] == INSUFFICIENT
    assert _text(screen, "statusStatsFetched") is None


# --- 받지 못함, 일시 차단 중 ---


def test_failed_keeps_the_values_dimmed_with_the_error_note_and_one_retry_each(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _ready(state=LoadState.FAILED, error_name="네트워크 끊김"))
    note = "봉을 받지 못했습니다(네트워크 끊김)."
    assert _text(screen, "statusCoinNote_KRW-AAA") == note
    assert _text(screen, "statusStatsNote") == note
    assert _shown(screen, "statusCoinIndicator_KRW-AAA")
    assert not _find(screen, QWidget, "statusCoinIndicator_KRW-AAA").isEnabled()
    assert _shown(screen, "statusStatsTable")
    assert not _find(screen, QWidget, "statusStatsTable").isEnabled()
    assert _text(screen, "statusStatsFetched") == FETCHED_LINE
    assert _shown(screen, "statusCoinRetry_KRW-AAA")
    assert _shown(screen, "statusStatsRetry")
    assert len(screen.findChildren(QPushButton, "statusStatsRetry")) == 1


def test_failed_without_values_draws_no_rows_and_no_fetched_line_when_never_fetched(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _empty(state=LoadState.FAILED, error_name="DB 오류"))
    note = "봉을 받지 못했습니다(DB 오류)."
    assert _text(screen, "statusCoinNote_KRW-AAA") == note
    assert _text(screen, "statusStatsNote") == note
    assert not _shown(screen, "statusCoinIndicator_KRW-AAA")
    assert not _shown(screen, "statusStatsTable")
    assert _text(screen, "statusStatsFetched") is None
    assert _shown(screen, "statusCoinRetry_KRW-AAA")
    assert _shown(screen, "statusStatsRetry")
    # 받은 적이 있으면 받아 둔 값이 없어도 그 줄은 보인다.
    window.apply(_empty(state=LoadState.FAILED, error_name="DB 오류", fetched_at=FETCHED_AT))
    assert _text(screen, "statusStatsFetched") == FETCHED_LINE


def test_failed_with_no_error_name_still_says_it_could_not_receive(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _empty(state=LoadState.FAILED))
    assert _text(screen, "statusCoinNote_KRW-AAA") == "봉을 받지 못했습니다."


@pytest.mark.parametrize(
    ("as_of", "shown"),
    [
        (None, "10:30"),
        (datetime(2026, 1, 2, 0, 10, tzinfo=UTC), "10:30"),  # 한국 09:10, 같은 날
        (datetime(2026, 1, 1, 14, 50, tzinfo=UTC), "2026-01-02 10:30"),  # 한국 23:50, 다음 날
    ],
)
def test_blocked_shows_only_the_blocked_line_with_the_when_text(make_window, as_of, shown):
    window = make_window(FakeActions())
    blocked = _ready(
        state=LoadState.FAILED,
        error_name="일시 차단",
        blocked_until=FETCHED_AT,
        as_of=as_of,
    )
    screen = _open(window, blocked)
    line = f"거래소가 잠시 요청을 막았습니다. {shown} 뒤에 다시 받습니다."
    assert _text(screen, "statusCoinNote_KRW-AAA") == line
    assert _text(screen, "statusStatsNote") == line
    texts = [label.text() for label in screen.findChildren(QLabel) if not label.isHidden()]
    assert not any("봉을 받지 못했습니다" in text for text in texts)
    assert not _find(screen, QWidget, "statusStatsTable").isEnabled()
    assert not _find(screen, QWidget, "statusCoinIndicator_KRW-AAA").isEnabled()
    assert _shown(screen, "statusCoinRetry_KRW-AAA")
    assert _shown(screen, "statusStatsRetry")


def test_blocked_on_the_next_day_uses_the_date_form_from_b11(make_window):
    window = make_window(FakeActions())
    blocked = _empty(
        state=LoadState.FAILED,
        error_name="일시 차단",
        blocked_until=datetime(2026, 1, 1, 15, 20, tzinfo=UTC),
        as_of=datetime(2026, 1, 1, 14, 50, tzinfo=UTC),
    )
    screen = _open(window, blocked)
    expected = "거래소가 잠시 요청을 막았습니다. 2026-01-02 00:20 뒤에 다시 받습니다."
    assert _text(screen, "statusCoinNote_KRW-AAA") == expected
    assert not _shown(screen, "statusCoinIndicator_KRW-AAA")
    assert not _shown(screen, "statusStatsTable")


def test_blocked_without_a_usable_time_falls_back_to_the_failed_line(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _empty(state=LoadState.FAILED, error_name="일시 차단"))
    assert _text(screen, "statusCoinNote_KRW-AAA") == "봉을 받지 못했습니다(일시 차단)."
    window.apply(
        _empty(
            state=LoadState.FAILED,
            error_name="일시 차단",
            blocked_until=datetime(2026, 1, 2, 1, 30),  # 시간대가 없다
        )
    )
    assert _text(screen, "statusCoinNote_KRW-AAA") == "봉을 받지 못했습니다(일시 차단)."


def test_a_naive_fetched_at_hides_the_fetched_line_without_an_error(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _ready(fetched_at=datetime(2026, 1, 2, 1, 30)))
    assert _text(screen, "statusStatsFetched") is None
    assert _shown(screen, "statusStatsTable")


def test_a_non_finite_price_is_drawn_as_insufficient(make_window):
    window = make_window(FakeActions())
    periods = (
        _period(0, 5, high=Decimal("NaN"), low=Decimal("1")),
        *(_period(i) for i in range(1, 5)),
    )
    screen = _open(window, _ready(periods=periods))
    assert _cells(screen, 0)[3:5] == [INSUFFICIENT, "1원"]


# --- 받아 둔 값이 없을 때의 표(설계서 4.5) ---


def _no_values_cases() -> list:
    blocked = {
        "error_name": "일시 차단",
        "blocked_until": datetime(2026, 1, 2, 1, 30, tzinfo=UTC),
    }
    return [
        # (CoinView 만들기, 표 자리 글, 마지막으로 받은 때, 지표 자리의 다시 시도)
        (lambda **kw: _empty(**kw), NO_CANDLES, False, False),
        (lambda **kw: _empty(fetched_at=FETCHED_AT, **kw), INSUFFICIENT, True, False),
        (
            lambda **kw: _empty(state=LoadState.LOADING, fetched_at=FETCHED_AT, **kw),
            CANDLES_LOADING,
            True,
            False,
        ),
        (
            lambda **kw: _empty(
                state=LoadState.FAILED, error_name="DB 오류", fetched_at=FETCHED_AT, **kw
            ),
            "봉을 받지 못했습니다(DB 오류).",
            True,
            True,
        ),
        (
            lambda **kw: _empty(state=LoadState.FAILED, fetched_at=FETCHED_AT, **blocked, **kw),
            "거래소가 잠시 요청을 막았습니다. 10:30 뒤에 다시 받습니다.",
            True,
            True,
        ),
    ]


@pytest.mark.parametrize("incomplete", [False, True])
@pytest.mark.parametrize("index", range(5))
def test_the_no_values_table_of_the_design(make_window, index, incomplete):
    build, note, fetched, indicator_retry = _no_values_cases()[index]
    window = make_window(FakeActions())
    screen = _open(window, build(last_incomplete=incomplete))
    assert _text(screen, "statusStatsNote") == note
    assert _text(screen, "statusCoinNote_KRW-AAA") == note
    assert not _shown(screen, "statusStatsTable")
    assert not _shown(screen, "statusCoinIndicator_KRW-AAA")
    assert (_text(screen, "statusStatsFetched") == FETCHED_LINE) is fetched
    expected = INCOMPLETE if incomplete else None
    assert _text(screen, "statusStatsIncomplete") == expected
    assert _text(screen, "statusCoinIncomplete_KRW-AAA") == expected
    assert _shown(screen, "statusStatsRetry")
    assert _shown(screen, "statusCoinRetry_KRW-AAA") is indicator_retry
    assert _text(screen, "statusCoinDisclaimer_KRW-AAA") == DISCLAIMER


# --- 다시 시도 ---


def test_retry_buttons_call_retry_candles_also_while_loading_and_blocked(make_window, actions):
    window = make_window(actions)
    screen = _open(window, _ready(state=LoadState.LOADING))
    stats_retry = _find(screen, QPushButton, "statusStatsRetry")
    assert stats_retry.text() == "다시 시도"
    stats_retry.click()
    assert actions.calls == [("retry_candles",)]
    window.apply(_ready(state=LoadState.FAILED, error_name="일시 차단", blocked_until=FETCHED_AT))
    stats_retry.click()
    coin_retry = _find(screen, QPushButton, "statusCoinRetry_KRW-AAA")
    assert coin_retry.text() == "다시 시도"
    coin_retry.click()
    assert actions.calls == [("retry_candles",)] * 3
    window.apply(_ready(state=LoadState.FAILED, error_name="네트워크 끊김"))
    coin_retry.click()
    assert actions.count("retry_candles") == 4
    # 부르는 동작은 이것 하나뿐이다.
    assert {call[0] for call in actions.calls} == {"retry_candles"}


def test_the_retry_button_exists_whatever_the_coin_state(make_window):
    window = make_window(FakeActions())
    screen = _open(window)
    assert _shown(screen, "statusStatsRetry")  # 첫 `CoinView` 전
    for view in (_ready(), _empty(), _only_a_short_period()):
        window.apply(view)
        assert _shown(screen, "statusStatsRetry")


def test_without_actions_the_retry_button_calls_nothing(make_window):
    window = make_window(None)
    screen = _open(window, _ready(state=LoadState.FAILED, error_name="시간 초과"))
    _find(screen, QPushButton, "statusStatsRetry").click()
    _find(screen, QPushButton, "statusCoinRetry_KRW-AAA").click()
    assert _text(screen, "statusCoinNote_KRW-AAA") == "봉을 받지 못했습니다(시간 초과)."


# --- 고른 코인이 바뀔 때, 다루지 않는 값 ---


def test_new_coins_follow_the_new_settings_and_start_as_loading(make_window, actions):
    window = make_window(actions)
    screen = _open(window, _ready(), _ready("KRW-BBB"), coins=("KRW-AAA", "KRW-BBB"))
    assert _shown(screen, "statusCoinIndicator_KRW-BBB")
    window.apply(SettingsView(("KRW-CCC", "KRW-AAA"), 30, True))
    assert screen.findChild(QWidget, "statusCoin_KRW-BBB") is None
    names = _card_names(screen)
    assert names == ["statusCoin_KRW-CCC", "statusCoin_KRW-AAA"]
    # 이미 받은 코인의 값은 그대로 두고, 새로 든 코인은 "불러오는 중"이다.
    assert _text(screen, "statusCoinNote_KRW-CCC") == LOADING
    assert _shown(screen, "statusCoinIndicator_KRW-AAA")
    chooser = _find(screen, QComboBox, "statusStatsCoin")
    assert [chooser.itemText(i) for i in range(chooser.count())] == ["CCC", "AAA"]
    # 빠졌던 코인을 다시 고르면 옛 값이 아니라 "불러오는 중"이다.
    window.apply(SettingsView(("KRW-BBB", "KRW-AAA"), 30, True))
    assert _text(screen, "statusCoinNote_KRW-BBB") == LOADING
    assert actions.count("retry_candles") == 0


def test_a_coin_view_for_a_coin_that_is_not_chosen_is_ignored(make_window, actions):
    window = make_window(actions)
    screen = _open(window, coins=("KRW-AAA",))
    window.apply(_ready("KRW-ZZZ"))
    assert screen.findChild(QWidget, "statusCoin_KRW-ZZZ") is None
    window.apply(SettingsView(("KRW-AAA", "KRW-ZZZ"), 30, True))
    assert _text(screen, "statusCoinNote_KRW-ZZZ") == LOADING
    assert actions.calls == []


def test_a_coin_view_before_the_first_settings_view_is_kept_for_a_chosen_coin(make_window):
    window = make_window(FakeActions())
    window.apply(_ready())
    window.apply(_ready("KRW-ZZZ"))
    window.apply(SettingsView(("KRW-AAA",), 30, True))
    screen = _status(window)
    assert _shown(screen, "statusCoinIndicator_KRW-AAA")
    assert screen.findChild(QWidget, "statusCoin_KRW-ZZZ") is None


def test_other_updates_do_not_disturb_the_status_screen(make_window, actions):
    from coin_report_trader.core import AlertCategory, AlertsView, SaveResult, WebhookView

    window = make_window(actions)
    screen = _open(window, _ready())
    window.apply(AlertsView(()))
    window.apply(WebhookView(AlertCategory.OTHER, saved=False))
    window.apply(SaveResult("alert_delete", ok=False, message="지우지 못했습니다."))
    window.apply(MarketsView(LoadState.READY, ()))
    assert _shown(screen, "statusCoinIndicator_KRW-AAA")
    assert actions.calls == []


def test_the_empty_body_and_the_other_two_screens_are_unchanged(make_window):
    window = make_window(FakeActions())
    window.apply(SettingsView(("KRW-AAA",), 30, True))
    assert current_screen(window) is ScreenId.STATUS
    for screen_id in (ScreenId.REPORTS, ScreenId.HISTORY):
        label = _find(screen_widget(window, screen_id), QLabel, "screenBody")
        assert label.text() == "이 화면은 아직 비어 있습니다."
    assert _status(window).findChild(QLabel, "screenBody") is None


def test_the_markets_blocked_line_uses_the_as_of_of_the_markets_view(make_window):
    """코인 목록의 차단 중 글도 `MarketsView.as_of`로 오늘인지 가른다(15번의 `_failed_text`)."""
    window = make_window(FakeActions())
    window.apply(SettingsView())
    screen = screen_widget(window, ScreenId.FIRST_SETUP)
    until = datetime(2026, 1, 1, 15, 20, tzinfo=UTC)  # 한국 2026-01-02 00:20
    blocked = "거래소가 잠시 요청을 막았습니다. {} 뒤에 다시 시도할 수 있습니다."
    window.apply(MarketsView(LoadState.FAILED, (), "일시 차단", until))
    assert _text(screen, "firstSetupCoinsMessage") == blocked.format("00:20")
    window.apply(
        MarketsView(
            LoadState.FAILED, (), "일시 차단", until, datetime(2026, 1, 1, 14, 50, tzinfo=UTC)
        )
    )
    assert _text(screen, "firstSetupCoinsMessage") == blocked.format("2026-01-02 00:20")
    window.apply(
        MarketsView(
            LoadState.FAILED, (), "일시 차단", until, datetime(2026, 1, 1, 15, 0, tzinfo=UTC)
        )
    )
    assert _text(screen, "firstSetupCoinsMessage") == blocked.format("00:20")
