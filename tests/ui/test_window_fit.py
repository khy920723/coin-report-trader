"""작은 창과 최대화에서 화면의 글이 눌리지 않고, 내용이 스크롤로 닿고, 처음 크기가 화면 안에 드는지.

값은 모두 시험이 만든 값이다. 창을 실제로 띄워(`show`) 크기가 정해진 뒤의 칸을 잰다.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QWidget

from coin_report_trader.core import (
    BarStats,
    CandleUnit,
    CoinView,
    LoadState,
    ScreenId,
    SettingsView,
    UiContext,
)
from coin_report_trader.ui import (
    MainWindow,
    create_main_window,
    screen_order,
    screen_widget,
    select_screen,
)
from coin_report_trader.ui.main_window import INITIAL_SIZE, initial_size

COINS = ("KRW-AAA", "KRW-BBB", "KRW-CCC")
FETCHED_AT = datetime(2026, 1, 2, 1, 30, tzinfo=UTC)


@pytest.fixture
def window(qapp: QApplication):
    made = create_main_window(UiContext("0.0.1", "X"))
    yield made
    made.close()
    made.deleteLater()


def _stats(market: str) -> BarStats:
    return BarStats(
        market=market,
        unit=CandleUnit.MINUTE_60,
        requested_count=5,
        bar_count=5,
        first_start_utc=datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
        last_start_utc=datetime(2026, 1, 1, 5, 0, tzinfo=UTC),
        close_last=Decimal("132"),
        change=Decimal("0.3200"),
        slope=Decimal("0.0481"),
        vs_mean=Decimal("0.1538"),
        up_ratio=Decimal("0.6000"),
        volatility=Decimal("0.1535"),
        max_drawdown=Decimal("-0.1000"),
        from_high=Decimal("0.0000"),
        high=Decimal("132"),
        low=Decimal("99"),
    )


def _fill_status(window: MainWindow) -> QWidget:
    window.apply(SettingsView(COINS, 30, True))
    for market in COINS:
        window.apply(
            CoinView(
                market,
                LoadState.READY,
                stats=_stats(market),
                fetched_at=FETCHED_AT,
                last_incomplete=True,
            )
        )
    select_screen(window, ScreenId.STATUS)
    return screen_widget(window, ScreenId.STATUS)


def _settle(window: MainWindow, width: int, height: int) -> None:
    window.resize(width, height)
    window.show()
    QApplication.processEvents()
    QApplication.processEvents()


def _scroll_of(screen: QWidget) -> QScrollArea:
    scroll = screen.parentWidget().parentWidget()
    assert isinstance(scroll, QScrollArea)
    return scroll


def _squashed(screen: QWidget) -> list[str]:
    """보이는 글 라벨 가운데 제 `minimumSizeHint` 높이보다 낮은 것의 이름."""
    return [
        label.objectName()
        for label in screen.findChildren(QLabel)
        if label.isVisibleTo(screen)
        and label.text()
        and label.height() < label.minimumSizeHint().height()
    ]


@pytest.mark.parametrize("size", [(1024, 640), (1600, 900)])
def test_status_labels_are_never_smaller_than_their_own_height(window, size):
    screen = _fill_status(window)
    _settle(window, *size)
    names = [label.objectName() for label in screen.findChildren(QLabel)]
    assert "statusStatsCell_12_1" in names
    assert _squashed(screen) == []


def test_status_table_cells_keep_their_height_when_maximized(window):
    screen = _fill_status(window)
    window.showMaximized()
    QApplication.processEvents()
    QApplication.processEvents()
    cells = [
        label
        for label in screen.findChildren(QLabel)
        if label.objectName().startswith("statusStatsCell_")
    ]
    assert len(cells) == 13 * 2
    assert all(cell.height() >= cell.minimumSizeHint().height() for cell in cells)
    assert _squashed(screen) == []


def test_a_small_window_scrolls_to_the_end_of_the_status_screen(window):
    screen = _fill_status(window)
    _settle(window, 1024, 640)
    scroll = _scroll_of(screen)
    bar = scroll.verticalScrollBar()
    # 내용이 창보다 커서 스크롤이 생긴다.
    assert screen.height() > scroll.viewport().height()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum())
    QApplication.processEvents()
    retry = screen.findChild(QWidget, "statusStatsRetry")
    assert retry is not None
    bottom = retry.mapTo(scroll.viewport(), retry.rect().bottomLeft()).y()
    assert 0 <= bottom < scroll.viewport().height()
    # 맨 위로도 돌아온다.
    bar.setValue(0)
    QApplication.processEvents()
    title = screen.findChild(QLabel, "statusStatsTitle")
    assert title is not None
    assert title.mapTo(scroll.viewport(), title.rect().topLeft()).y() >= 0


@pytest.mark.parametrize("screen_id", list(ScreenId))
def test_every_screen_sits_in_a_scroll_area_and_is_still_found_by_name(window, screen_id):
    screen = screen_widget(window, screen_id)
    assert screen.objectName() == f"screen_{screen_id.value}"
    scroll = _scroll_of(screen)
    assert scroll.objectName() == f"screenScroll_{screen_id.value}"
    assert scroll.widgetResizable()
    assert screen_order(window) == tuple(ScreenId)


@pytest.mark.parametrize("screen_id", list(ScreenId))
def test_no_screen_squashes_its_labels_in_a_small_window(window, screen_id):
    window.apply(SettingsView(COINS, 30, True))
    select_screen(window, screen_id)
    _settle(window, 1024, 640)
    assert _squashed(screen_widget(window, screen_id)) == []


def test_the_first_size_is_inside_the_available_area(window):
    area = window.screen().availableGeometry()
    frame = QRect(window.pos(), window.size())
    assert area.contains(frame)
    assert window.width() <= area.width()
    assert window.height() <= area.height()


@pytest.mark.parametrize(
    ("area", "expected"),
    [
        ((1920, 1080), INITIAL_SIZE),
        ((1280, 800), INITIAL_SIZE),
        ((1180, 760), (1062, 684)),
        ((1024, 600), (921, 540)),
        ((1400, 700), (1260, 630)),
    ],
)
def test_initial_size_is_1280_by_800_or_ninety_percent_of_a_smaller_area(area, expected):
    assert initial_size(*area) == expected


def test_a_size_the_user_chose_is_not_touched_by_showing(window):
    window.resize(900, 700)
    window.show()
    QApplication.processEvents()
    assert (window.width(), window.height()) == (900, 700)
