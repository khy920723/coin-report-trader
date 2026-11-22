"""현황 화면의 봉 통계(M1 명세 3.14.5, `CoinView.stats`)의 시험.

값은 명세 3.5.2의 만든 예(60분봉 다섯 개)를 쓴다. 창을 띄우지 않으므로 보이는지는 `isHidden()`으로,
흐리게 남긴 값은 `isEnabled()`로 본다.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QWidget

from coin_report_trader.core import (
    BAR_STATS_TITLES,
    BarStats,
    CandleUnit,
    CoinView,
    LoadState,
    ScreenId,
    SettingsView,
    UiActions,
    UiContext,
    empty_bar_stats,
)
from coin_report_trader.ui import MainWindow, create_main_window, screen_widget
from coin_report_trader.ui.info_icon import InfoIcon

AAA = "KRW-AAA"
BBB = "KRW-BBB"
DISCLAIMER = "봉 통계는 계산 결과일 뿐이고, 이득을 보장하지 않습니다."
INSUFFICIENT = "봉이 모자람"
FETCHED_AT = datetime(2026, 1, 2, 1, 30, tzinfo=UTC)  # 한국 시간 2026-01-02 10:30
UNTIL = datetime(2026, 1, 2, 2, 0, tzinfo=UTC)
AS_OF = datetime(2026, 1, 2, 1, 40, tzinfo=UTC)
TABLE_TITLES = [
    "쓴 봉 수",
    "첫 봉 시작",
    "마지막 봉 시작",
    "마지막 종가",
    "전체 변화",
    "추세 기울기",
    "평균 대비",
    "오른 봉 비율",
    "변동성",
    "최대 낙폭",
    "최고가 대비",
    "최고가",
    "최저가",
]


class FakeActions:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def count(self, name: str) -> int:
        return sum(1 for call in self.calls if call[0] == name)

    def refresh_markets(self) -> None:
        self.calls.append(("refresh_markets",))

    def save_settings(
        self,
        coins,
        retention_days,
        *,
        first_setup,
        candle_unit=None,
        candle_count=None,
        ai_choices=None,
    ) -> None:
        self.calls.append(("save_settings", coins, retention_days, first_setup))

    def load_reports(self, market) -> None:
        pass  # 2단계의 동작. 화면이 부르기 전까지 아무것도 하지 않는다

    def load_report(self, report_id) -> None:
        pass

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


def _stats(market: str = AAA, **changes) -> BarStats:
    """명세 3.5.2의 예: 60분봉 다섯 개를 N 5로 셈한 값."""
    values = {
        "market": market,
        "unit": CandleUnit.MINUTE_60,
        "requested_count": 5,
        "bar_count": 5,
        "first_start_utc": datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
        "last_start_utc": datetime(2026, 1, 1, 5, 0, tzinfo=UTC),
        "close_last": Decimal("132"),
        "change": Decimal("0.3200"),
        "slope": Decimal("0.0481"),
        "vs_mean": Decimal("0.1538"),
        "up_ratio": Decimal("0.6000"),
        "volatility": Decimal("0.1535"),
        "max_drawdown": Decimal("-0.1000"),
        "from_high": Decimal("0.0000"),
        "high": Decimal("132"),
        "low": Decimal("99"),
    }
    values.update(changes)
    return BarStats(**values)


def _short(market: str = AAA, bars: int = 3, **changes) -> BarStats:
    """완성된 봉이 고른 개수(5)보다 적은 값: 있는 수만 담고 나머지는 `None`이다."""
    return BarStats(market, CandleUnit.MINUTE_60, 5, bars, **changes)


def _view(market: str = AAA, stats: BarStats | None = None, **changes) -> CoinView:
    values = {
        "market": market,
        "state": LoadState.READY,
        "stats": stats if stats is not None else _stats(market),
        "fetched_at": FETCHED_AT,
        "as_of": AS_OF,
    }
    values.update(changes)
    return CoinView(**values)


# --- 읽는 도구 ---


def _status(window: MainWindow) -> QWidget:
    return screen_widget(window, ScreenId.STATUS)


def _find(parent: QWidget, kind: type, name: str):
    found = parent.findChild(kind, name)
    assert found is not None, name
    return found


def _text(screen: QWidget, name: str) -> str | None:
    label = _find(screen, QLabel, name)
    return None if label.isHidden() else label.text()


def _shown(screen: QWidget, name: str) -> bool:
    return not _find(screen, QWidget, name).isHidden()


def _tip(screen: QWidget, name: str) -> str | None:
    """'ⓘ'(옮긴 보장 아님 글)가 보이면 그 툴팁 글, 숨겨져 있으면 `None`. 읽는 설명도 같아야 한다."""
    icon = _find(screen, InfoIcon, name)
    assert icon.accessibleDescription() == icon.toolTip()
    return None if icon.isHidden() else icon.toolTip()


def _flat(layout) -> list[str]:
    """레이아웃 안 위젯의 `objectName`을 놓인 차례대로(안쪽 레이아웃까지)."""
    names: list[str] = []
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.widget() is not None:
            names.append(item.widget().objectName())
        elif item.layout() is not None:
            names.extend(_flat(item.layout()))
    return names


def _card_rows(screen: QWidget, market: str = AAA) -> list[str]:
    return [_find(screen, QLabel, f"statusCoinStatsRow_{market}_{i}").text() for i in range(5)]


def _table(screen: QWidget) -> list[tuple[str, str]]:
    return [
        (
            _find(screen, QLabel, f"statusStatsCell_{row}_0").text(),
            _find(screen, QLabel, f"statusStatsCell_{row}_1").text(),
        )
        for row in range(13)
    ]


def _open(window: MainWindow, *views: CoinView, coins=(AAA,)) -> QWidget:
    window.apply(SettingsView(coins, 30, True))
    for view in views:
        window.apply(view)
    return _status(window)


# --- 코인 칸 ---


def test_the_card_shows_the_unit_and_count_then_five_elements_then_the_disclaimer(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _view())
    assert _text(screen, f"statusCoinStatsHeader_{AAA}") == "60분봉 5개"
    assert _card_rows(screen) == [
        "전체 변화 +32.00%",
        "추세 기울기 +4.81%",
        "평균 대비 +15.38%",
        "오른 봉 비율 60.00%",  # 늘 0 이상인 둘에는 부호가 없다
        "변동성 15.35%",
    ]
    assert _tip(screen, f"statusCoinDisclaimer_{AAA}") == DISCLAIMER
    assert _text(screen, f"statusCoinNote_{AAA}") is None
    # 옛 지표는 없다.
    assert screen.findChild(QWidget, f"statusCoinIndicator_{AAA}") is None
    assert _shown(screen, f"statusCoinStats_{AAA}")
    # 코인 칸 안의 차례: 코인 이름, 값 자리의 글, 첫 줄과 그 옆 'ⓘ', 다섯 요소, 미완성 봉 글.
    order = _flat(_find(screen, QWidget, f"statusCoin_{AAA}").layout())
    expected = [
        f"statusCoinName_{AAA}",
        f"statusCoinNote_{AAA}",
        f"statusCoinRetry_{AAA}",
        f"statusCoinStatsHeader_{AAA}",
        f"statusCoinDisclaimer_{AAA}",
        f"statusCoinStats_{AAA}",
        f"statusCoinIncomplete_{AAA}",
    ]
    assert [name for name in order if name in expected] == expected


def test_a_negative_change_has_a_minus_and_the_card_uses_the_chosen_unit_name(make_window):
    window = make_window(FakeActions())
    stats = _stats(
        unit=CandleUnit.DAY,
        requested_count=5,
        change=Decimal("-0.05"),
        slope=Decimal("-0.012"),
        vs_mean=Decimal("0"),
    )
    screen = _open(window, _view(stats=stats))
    assert _text(screen, f"statusCoinStatsHeader_{AAA}") == "일봉 5개"
    assert _card_rows(screen)[:3] == [
        "전체 변화 -5.00%",
        "추세 기울기 -1.20%",
        "평균 대비 0.00%",
    ]


def test_an_incomplete_last_bar_still_shows_its_line_under_the_stats(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _view(last_incomplete=True))
    assert _text(screen, f"statusCoinIncomplete_{AAA}") == "마지막 봉은 미완성입니다"
    assert _text(screen, "statusStatsIncomplete") == "마지막 봉은 미완성입니다"


def test_too_few_bars_with_some_saved_show_insufficient_for_every_element(make_window):
    window = make_window(FakeActions())
    view = _view(stats=_short(bars=3), state=LoadState.NO_DATA)
    screen = _open(window, view)
    assert _text(screen, f"statusCoinStatsHeader_{AAA}") == (
        "60분봉 봉이 모자람(완성된 봉 3개 / 고른 개수 5개)"
    )
    assert _card_rows(screen) == [
        "전체 변화 봉이 모자람",
        "추세 기울기 봉이 모자람",
        "평균 대비 봉이 모자람",
        "오른 봉 비율 봉이 모자람",
        "변동성 봉이 모자람",
    ]
    assert _text(screen, f"statusCoinNote_{AAA}") is None  # 요소마다의 글이 있어 하나는 없다
    assert _tip(screen, f"statusCoinDisclaimer_{AAA}") == DISCLAIMER
    assert _text(screen, f"statusCoinStatsHeader_{AAA}") != "60분봉 5개"


def test_too_few_bars_with_none_saved_show_one_insufficient_line_when_fetched(make_window):
    window = make_window(FakeActions())
    view = _view(stats=empty_bar_stats(AAA, CandleUnit.MINUTE_60, 5), state=LoadState.NO_DATA)
    screen = _open(window, view)
    assert _text(screen, f"statusCoinNote_{AAA}") == INSUFFICIENT
    assert not _shown(screen, f"statusCoinStats_{AAA}")
    assert _text(screen, "statusStatsNote") == INSUFFICIENT
    assert not _shown(screen, "statusStatsTable")
    assert not _shown(screen, "statusStatsSummary")
    # 늘 보이는 것들: 보장 아님 글, 마지막으로 받은 때.
    assert _tip(screen, f"statusCoinDisclaimer_{AAA}") == DISCLAIMER
    assert _tip(screen, "statusStatsDisclaimer") == DISCLAIMER
    assert _text(screen, "statusStatsFetched") == "마지막으로 받은 때 2026-01-02 10:30"


def test_no_bars_yet_and_never_fetched_says_so(make_window):
    window = make_window(FakeActions())
    view = _view(
        stats=empty_bar_stats(AAA, CandleUnit.MINUTE_60, 5),
        state=LoadState.NO_DATA,
        fetched_at=None,
    )
    screen = _open(window, view)
    assert _text(screen, f"statusCoinNote_{AAA}") == "아직 받은 봉이 없습니다."
    assert _text(screen, "statusStatsNote") == "아직 받은 봉이 없습니다."
    assert _text(screen, "statusStatsFetched") is None
    assert not _shown(screen, f"statusCoinStats_{AAA}")


def test_receiving_keeps_the_saved_stats_and_adds_the_receiving_line(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _view(state=LoadState.LOADING))
    assert _text(screen, f"statusCoinNote_{AAA}") == "봉을 받는 중입니다."
    assert _card_rows(screen)[0] == "전체 변화 +32.00%"
    assert _find(screen, QWidget, f"statusCoinStats_{AAA}").isEnabled()
    assert _table(screen)[4] == ("전체 변화", "+32.00%")


def test_receiving_without_saved_stats_shows_only_the_receiving_line(make_window):
    window = make_window(FakeActions())
    view = _view(
        stats=empty_bar_stats(AAA, CandleUnit.MINUTE_60, 5),
        state=LoadState.LOADING,
        fetched_at=None,
    )
    screen = _open(window, view)
    assert _text(screen, f"statusCoinNote_{AAA}") == "봉을 받는 중입니다."
    assert _text(screen, "statusStatsNote") == "봉을 받는 중입니다."
    assert not _shown(screen, f"statusCoinStats_{AAA}")
    assert not _shown(screen, "statusStatsTable")


def test_a_failed_receive_dims_the_saved_stats_and_shows_retry(make_window, actions):
    window = make_window(actions)
    screen = _open(window, _view(state=LoadState.FAILED, error_name="네트워크 끊김"))
    assert _text(screen, f"statusCoinNote_{AAA}") == "봉을 받지 못했습니다(네트워크 끊김)."
    assert _text(screen, "statusStatsNote") == "봉을 받지 못했습니다(네트워크 끊김)."
    assert _shown(screen, f"statusCoinStats_{AAA}")
    assert not _find(screen, QWidget, f"statusCoinStats_{AAA}").isEnabled()
    assert _shown(screen, "statusStatsTable")
    assert not _find(screen, QWidget, "statusStatsTable").isEnabled()
    assert _card_rows(screen)[0] == "전체 변화 +32.00%"  # 흐리게 남긴 값
    assert _text(screen, "statusStatsFetched") == "마지막으로 받은 때 2026-01-02 10:30"
    _find(screen, QPushButton, "statusStatsRetry").click()
    _find(screen, QPushButton, f"statusCoinRetry_{AAA}").click()
    assert actions.count("retry_candles") == 2


def test_a_failed_receive_without_saved_stats_shows_only_the_line(make_window):
    window = make_window(FakeActions())
    view = _view(
        stats=empty_bar_stats(AAA, CandleUnit.MINUTE_60, 5),
        state=LoadState.FAILED,
        error_name="DB 오류",
        fetched_at=None,
    )
    screen = _open(window, view)
    assert _text(screen, f"statusCoinNote_{AAA}") == "봉을 받지 못했습니다(DB 오류)."
    assert not _shown(screen, f"statusCoinStats_{AAA}")
    assert not _shown(screen, "statusStatsTable")
    assert _tip(screen, f"statusCoinDisclaimer_{AAA}") == DISCLAIMER


def test_a_blocked_receive_shows_the_blocked_line_with_the_saved_stats_dim(make_window):
    window = make_window(FakeActions())
    view = _view(state=LoadState.FAILED, error_name="일시 차단", blocked_until=UNTIL)
    screen = _open(window, view)
    blocked = "거래소가 잠시 요청을 막았습니다. 11:00 뒤에 다시 받습니다."
    assert _text(screen, f"statusCoinNote_{AAA}") == blocked
    assert _text(screen, "statusStatsNote") == blocked
    assert not _find(screen, QWidget, f"statusCoinStats_{AAA}").isEnabled()
    assert not _find(screen, QWidget, "statusStatsTable").isEnabled()


# --- 봉 통계 자리 ---


def test_the_stats_place_has_a_summary_a_table_and_the_disclaimer_by_the_title(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _view())
    assert _find(screen, QLabel, "statusStatsTitle").text() == "봉 통계"
    assert _text(screen, "statusStatsSummary") == "60분봉 · 고른 개수 5개"
    assert [_find(screen, QLabel, f"statusStatsHeader_{i}").text() for i in range(2)] == [
        "항목",
        "값",
    ]
    assert _table(screen) == [
        ("쓴 봉 수", "5개"),
        ("첫 봉 시작", "2026-01-01 10:00"),
        ("마지막 봉 시작", "2026-01-01 14:00"),
        ("마지막 종가", "132원"),
        ("전체 변화", "+32.00%"),
        ("추세 기울기", "+4.81%"),
        ("평균 대비", "+15.38%"),
        ("오른 봉 비율", "60.00%"),
        ("변동성", "15.35%"),
        ("최대 낙폭", "-10.00%"),
        ("최고가 대비", "0.00%"),
        ("최고가", "132원"),
        ("최저가", "99원"),
    ]
    assert _tip(screen, "statusStatsDisclaimer") == DISCLAIMER
    assert _text(screen, "statusStatsFetched") == "마지막으로 받은 때 2026-01-02 10:30"
    # 카드 안의 차례: 제목, 제목 옆 'ⓘ', 표, 마지막으로 받은 때, 미완성 봉 글, 다시 시도.
    names = _flat(_find(screen, QWidget, "statusStats").layout())
    assert names.index("statusStatsTitle") + 1 == names.index("statusStatsDisclaimer")
    assert names.index("statusStatsDisclaimer") < names.index("statusStatsTable")
    assert names.index("statusStatsTable") < names.index("statusStatsFetched")


def test_the_table_rows_are_the_titles_of_the_bar_stats_columns_in_order(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _view())
    columns = list(BAR_STATS_TITLES)
    assert [title for title, _value in _table(screen)] == [BAR_STATS_TITLES[c] for c in columns[3:]]
    assert [title for title, _value in _table(screen)] == TABLE_TITLES


def test_prices_use_the_spec_price_text_with_won(make_window):
    window = make_window(FakeActions())
    stats = _stats(
        close_last=Decimal("1234567.50"), high=Decimal("1234567.50"), low=Decimal("0.00012")
    )
    screen = _open(window, _view(stats=stats))
    rows = dict(_table(screen))
    assert rows["마지막 종가"] == "1,234,567.5원"
    assert rows["최고가"] == "1,234,567.5원"
    assert rows["최저가"] == "0.00012원"


def test_too_few_bars_make_the_bar_count_row_say_the_chosen_count(make_window):
    window = make_window(FakeActions())
    view = _view(stats=_short(bars=3), state=LoadState.NO_DATA)
    screen = _open(window, view)
    assert _text(screen, "statusStatsSummary") == "60분봉 · 고른 개수 5개"
    rows = _table(screen)
    assert rows[0] == ("쓴 봉 수", "3개(고른 개수 5개)")
    assert [value for _title, value in rows[1:]] == [INSUFFICIENT] * 12
    assert _text(screen, "statusStatsNote") is None
    assert _tip(screen, "statusStatsDisclaimer") == DISCLAIMER


def test_every_bar_is_counted_as_n_when_there_are_enough(make_window):
    window = make_window(FakeActions())
    stats = _stats(bar_count=200, requested_count=200)
    screen = _open(window, _view(stats=stats))
    assert _table(screen)[0] == ("쓴 봉 수", "200개")
    assert _text(screen, f"statusCoinStatsHeader_{AAA}") == "60분봉 200개"
    assert _text(screen, "statusStatsSummary") == "60분봉 · 고른 개수 200개"


def test_the_stats_place_follows_the_chosen_coin(make_window):
    window = make_window(FakeActions())
    day = _stats(
        BBB, unit=CandleUnit.DAY, requested_count=100, bar_count=100, change=Decimal("-0.2")
    )
    screen = _open(window, _view(), _view(BBB, stats=day), coins=(AAA, BBB))
    assert _text(screen, "statusStatsSummary") == "60분봉 · 고른 개수 5개"
    assert dict(_table(screen))["전체 변화"] == "+32.00%"
    chooser = _find(screen, QComboBox, "statusStatsCoin")
    chooser.setCurrentIndex(1)
    assert _text(screen, "statusStatsSummary") == "일봉 · 고른 개수 100개"
    assert dict(_table(screen))["전체 변화"] == "-20.00%"
    assert dict(_table(screen))["쓴 봉 수"] == "100개"
    assert _text(screen, f"statusCoinStatsHeader_{BBB}") == "일봉 100개"


def test_the_disclaimer_is_only_missing_before_the_first_value_of_a_coin(make_window):
    window = make_window(FakeActions())
    screen = _open(window)  # `CoinView`가 오기 전
    assert _text(screen, f"statusCoinNote_{AAA}") == "불러오는 중"
    assert _tip(screen, f"statusCoinDisclaimer_{AAA}") is None
    window.apply(_view())
    assert _tip(screen, f"statusCoinDisclaimer_{AAA}") == DISCLAIMER


# --- 옛 그리기는 없다(49번) ---


def test_nothing_of_the_old_table_or_indicator_is_drawn_without_stats(make_window):
    window = make_window(FakeActions())
    screen = _open(window, CoinView(AAA, LoadState.NO_DATA, fetched_at=FETCHED_AT))
    assert _text(screen, "statusStatsNote") == INSUFFICIENT
    assert _tip(screen, "statusStatsDisclaimer") == DISCLAIMER
    assert _tip(screen, f"statusCoinDisclaimer_{AAA}") == DISCLAIMER
    assert not _shown(screen, f"statusCoinStats_{AAA}")
    assert _find(screen, QLabel, "statusStatsTitle").text() == "봉 통계"
    names = [label.objectName() for label in screen.findChildren(QLabel)]
    assert not [name for name in names if "Indicator" in name]
    assert sorted(name for name in names if name.startswith("statusStatsHeader_")) == [
        "statusStatsHeader_0",
        "statusStatsHeader_1",
    ]
    assert len([name for name in names if name.startswith("statusStatsCell_")]) == 13 * 2
    assert screen.findChild(QWidget, f"statusCoinIndicator_{AAA}") is None


def test_a_coin_view_without_stats_has_no_values_to_draw(make_window):
    window = make_window(FakeActions())
    screen = _open(window, CoinView(AAA, LoadState.READY, fetched_at=FETCHED_AT))
    # `stats`가 없으면 받아 둔 값이 없는 쪽이다.
    assert _text(screen, "statusStatsNote") == INSUFFICIENT
    assert not _shown(screen, "statusStatsTable")
    assert not _shown(screen, f"statusCoinStats_{AAA}")
    assert _find(screen, QLabel, "statusStatsCell_0_0").text() == ""


def test_a_naive_time_in_the_stats_is_shown_as_insufficient_and_raises_nothing(make_window):
    window = make_window(FakeActions())
    stats = _stats(first_start_utc=datetime(2026, 1, 1, 1, 0))  # 시간대가 없다
    screen = _open(window, _view(stats=stats))
    assert dict(_table(screen))["첫 봉 시작"] == INSUFFICIENT
    assert dict(_table(screen))["마지막 봉 시작"] == "2026-01-01 14:00"
