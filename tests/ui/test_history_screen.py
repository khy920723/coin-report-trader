"""3단계의 기록 화면(M3 명세 3.15, 3.17.4, 3.17.6의 21번 줄)의 시험. 값은 만든 값이다."""

from datetime import UTC, date, datetime

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateEdit,
    QLabel,
    QPushButton,
    QStyleOptionViewItem,
    QTableWidget,
    QWidget,
)

from coin_report_trader.core import (
    KST,
    SAVE_TARGET_EXPORT,
    AlertCategory,
    HistoryRow,
    HistoryTab,
    HistoryView,
    LoadState,
    Mode,
    SaveResult,
    ScreenId,
    SettingsView,
    UiContext,
)
from coin_report_trader.core.views import AlertView
from coin_report_trader.ui import (
    create_main_window,
    current_screen,
    screen_widget,
    select_screen,
    theme,
)
from coin_report_trader.ui.history_screen import HistoryScreen, history_day_of
from coin_report_trader.ui.widgets import set_trend, trend_of

COINS = ("KRW-AAA", "KRW-BBB")
NOW = datetime(2026, 1, 2, 10, 30, tzinfo=KST)
TODAY = date(2026, 1, 2)
TABS = ("rounds", "judgments", "orders", "fills", "pauses")
COLUMNS = {
    HistoryTab.ROUNDS: ("번호", "시작 시각", "끝난 단계", "결과"),
    HistoryTab.JUDGMENTS: (
        "회차",
        "코인",
        "최종 판단",
        "예측 가격",
        "다음 회차 때의 실제 값",
        "차이(%)",
        "맞음·틀림",
        "매수 제안가",
        "매도 제안가",
        "받지 못한 까닭",
    ),
    HistoryTab.ORDERS: ("시각", "코인", "매수·매도", "종류", "가격", "수량", "까닭", "상태"),
    HistoryTab.FILLS: (
        "시각",
        "코인",
        "매수·매도",
        "체결 가격",
        "체결 수량",
        "수수료",
        "실현 손익",
    ),
    HistoryTab.PAUSES: ("시작 시각", "종류", "푼 시각"),
}


class Actions:
    def __init__(self) -> None:
        self.history: list[tuple[HistoryTab, str | None, date]] = []
        self.exports: list[tuple[HistoryTab, str | None, str]] = []
        self.calls: list[str] = []

    def refresh_markets(self) -> None: ...

    def save_settings(self, coins, retention_days, **kwargs) -> None: ...

    def load_reports(self, market, mode=None) -> None:
        self.calls.append(f"load_reports:{market}")

    def load_report(self, report_id) -> None:
        self.calls.append(f"load_report:{report_id}")

    def retry_candles(self) -> None: ...

    def mark_seen(self, alert_ids) -> None: ...

    def delete_alert(self, alert_id) -> None: ...

    def clear_alerts(self, category) -> None: ...

    def save_webhook(self, category, url) -> None: ...

    def delete_webhook(self, category) -> None: ...

    def send_test(self, category) -> None: ...

    def load_history(self, tab, market, day, mode=None) -> None:
        self.history.append((tab, market, day))

    def export_history(self, tab, market, path) -> None:
        self.exports.append((tab, market, path))


@pytest.fixture
def actions() -> Actions:
    return Actions()


@pytest.fixture
def window(qapp: QApplication, actions: Actions):
    made = create_main_window(UiContext("0.0.1", "X", actions))
    made.resize(1200, 900)
    made.show()
    _history(made).set_clock(lambda: NOW)
    yield made
    made.close()
    made.deleteLater()


def _history(window) -> HistoryScreen:
    screen = screen_widget(window, ScreenId.HISTORY)
    assert isinstance(screen, HistoryScreen)
    return screen


def _done(window, coins=COINS) -> None:
    window.apply(SettingsView(coins, 30, True))


def _open(window, actions: Actions) -> HistoryScreen:
    _done(window)
    select_screen(window, ScreenId.HISTORY)
    actions.history.clear()
    return _history(window)


def _find(parent: QWidget, kind: type, name: str):
    found = parent.findChild(kind, name)
    assert found is not None, name
    return found


def _table(screen: QWidget) -> QTableWidget:
    return _find(screen, QTableWidget, "historyTable")


def _message(screen: QWidget) -> QLabel:
    return _find(screen, QLabel, "historyMessage")


def _row(key: str, cells: tuple[str, ...], detail=(), report_id=None, market=None) -> HistoryRow:
    return HistoryRow(key, cells, tuple(detail), report_id, market)


def _judgment_rows() -> tuple[HistoryRow, ...]:
    return (
        _row(
            "j7",
            ("7", "AAA", "오를 것", "103원", "다음 회차 전", "다음 회차 전", "다음 회차 전",
             "100원", "-", ""),
            (("회차", "7"), ("예측 까닭", "만든 까닭 하나")),
            report_id=70,
            market="KRW-AAA",
        ),
        _row(
            "j6",
            ("6", "AAA", "내릴 것", "101원", "100원", "▼ -0.99%", "맞음", "-", "102원", ""),
            (("회차", "6"),),
            report_id=60,
            market="KRW-AAA",
        ),
        _row(
            "j5",
            ("5", "BBB", "오를 것", "205원", "206원", "▲ +0.49%", "맞음", "198원", "-", ""),
            (("회차", "5"),),
            report_id=50,
            market="KRW-BBB",
        ),
        _row(
            "j4",
            ("4", "AAA", "-", "-", "-", "-", "-", "-", "-", "형식 오류"),
            (("받지 못한 까닭", "형식 오류"),),
            report_id=40,
            market="KRW-AAA",
        ),
        _row("j3", ("3", "AAA", "오를 것", "100원", "100원", "0.00%", "맞음", "-", "-", ""), ()),
    )


def _view(
    tab: HistoryTab = HistoryTab.JUDGMENTS,
    rows: tuple[HistoryRow, ...] | None = None,
    *,
    market: str | None = None,
    day: date = TODAY,
    state: LoadState = LoadState.READY,
) -> HistoryView:
    if rows is None:
        rows = _judgment_rows()
    return HistoryView(tab, market, day, COLUMNS[tab], rows, state)


def _alert(code: str, coin: str | None, created_at: datetime) -> AlertView:
    return AlertView(1, created_at, Mode.PAPER, AlertCategory.TRADE, code, coin, "글")


def _color(table: QTableWidget, row: int, column: int) -> str:
    return table.item(row, column).foreground().color().name().lower()


def _right(table: QTableWidget, row: int, column: int) -> bool:
    align = int(table.item(row, column).textAlignment())
    return bool(align & int(Qt.AlignmentFlag.AlignRight.value))


# --- 자리와 객체 이름 ---


def test_the_history_screen_has_its_parts_and_no_empty_body(window):
    screen = _history(window)
    assert screen.findChild(QLabel, "screenBody") is None
    assert _find(screen, QComboBox, "historyCoin").itemText(0) == "모든 코인"
    assert isinstance(_find(screen, QDateEdit, "historyDay"), QDateEdit)
    for tab in TABS:
        assert _find(screen, QPushButton, f"historyTab_{tab}") is not None
    assert screen.findChild(QPushButton, "historyTab_external") is None
    _find(screen, QTableWidget, "historyTable")
    _find(screen, QLabel, "historyDetail")
    assert _find(screen, QPushButton, "historyExport").text() == "CSV로 내보내기"
    assert _find(screen, QPushButton, "historyReport").isHidden()
    assert _find(screen, QLabel, "historyExportMessage").isHidden()


def test_the_tab_buttons_are_titled_and_the_chosen_one_is_marked(window, actions):
    screen = _open(window, actions)
    titles = {
        tab: _find(screen, QPushButton, f"historyTab_{tab}").text() for tab in TABS
    }
    assert titles == {
        "rounds": "회차",
        "judgments": "판단",
        "orders": "주문",
        "fills": "체결",
        "pauses": "멈춤",
    }
    assert screen.chosen_tab() is HistoryTab.ROUNDS
    assert _find(screen, QPushButton, "historyTab_rounds").property("selected") is True
    _find(screen, QPushButton, "historyTab_orders").click()
    assert _find(screen, QPushButton, "historyTab_orders").property("selected") is True
    assert _find(screen, QPushButton, "historyTab_rounds").property("selected") is False


# --- 고르기와 부르기 ---


def test_the_coin_choice_lists_all_coins_then_the_settings_coins_in_order(window):
    _done(window, ("KRW-BBB", "KRW-AAA"))
    combo = _find(_history(window), QComboBox, "historyCoin")
    assert [combo.itemText(i) for i in range(combo.count())] == ["모든 코인", "BBB", "AAA"]
    assert combo.currentData() is None


def test_the_coin_choice_stays_and_a_removed_coin_falls_back_to_all(window, actions):
    screen = _open(window, actions)
    combo = _find(screen, QComboBox, "historyCoin")
    combo.setCurrentIndex(2)
    assert screen.chosen_market() == "KRW-BBB"
    actions.history.clear()
    _done(window, ("KRW-BBB", "KRW-AAA"))
    assert screen.chosen_market() == "KRW-BBB"
    assert actions.history == []
    _done(window, ("KRW-AAA",))
    assert screen.chosen_market() is None
    # 고르던 코인이 목록에서 빠져 "모든 코인"이 되었으므로 그 값으로 다시 부른다.
    assert actions.history == [(HistoryTab.ROUNDS, None, TODAY)]


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 1, 2, 8, 59, tzinfo=KST), date(2026, 1, 1)),
        (datetime(2026, 1, 2, 9, 0, tzinfo=KST), date(2026, 1, 2)),
        (datetime(2026, 1, 1, 23, 59, tzinfo=UTC), date(2026, 1, 1)),
        (datetime(2026, 1, 2, 0, 0, tzinfo=UTC), date(2026, 1, 2)),
    ],
)
def test_the_day_changes_at_nine_in_korea(now, expected):
    assert history_day_of(now) == expected


def test_the_day_starts_as_today_and_follows_the_clock_until_it_is_chosen(window, actions):
    _done(window)
    screen = _history(window)
    screen.set_clock(lambda: datetime(2026, 1, 2, 8, 0, tzinfo=KST))
    select_screen(window, ScreenId.HISTORY)
    assert screen.chosen_day() == date(2026, 1, 1)
    assert actions.history[-1] == (HistoryTab.ROUNDS, None, date(2026, 1, 1))
    select_screen(window, ScreenId.STATUS)
    screen.set_clock(lambda: datetime(2026, 1, 2, 9, 5, tzinfo=KST))
    select_screen(window, ScreenId.HISTORY)
    assert screen.chosen_day() == date(2026, 1, 2)
    # 사용자가 날짜를 고른 뒤에는 시계를 따르지 않는다.
    _find(screen, QDateEdit, "historyDay").setDate(QDate(2025, 12, 30))
    select_screen(window, ScreenId.STATUS)
    screen.set_clock(lambda: datetime(2026, 1, 3, 12, 0, tzinfo=KST))
    select_screen(window, ScreenId.HISTORY)
    assert screen.chosen_day() == date(2025, 12, 30)


def test_opening_the_screen_after_nine_moves_an_unchosen_day_to_the_new_day(window, actions):
    _done(window)
    screen = _history(window)
    screen.set_clock(lambda: datetime(2026, 1, 2, 8, 59, tzinfo=KST))
    select_screen(window, ScreenId.HISTORY)
    assert screen.chosen_day() == date(2026, 1, 1)
    select_screen(window, ScreenId.STATUS)
    actions.history.clear()
    screen.set_clock(lambda: datetime(2026, 1, 2, 9, 1, tzinfo=KST))
    assert screen.chosen_day() == date(2026, 1, 1)  # 시계만 옮겼다고 날짜가 바뀌지는 않는다
    select_screen(window, ScreenId.HISTORY)
    assert _find(screen, QDateEdit, "historyDay").date() == QDate(2026, 1, 2)
    assert actions.history == [(HistoryTab.ROUNDS, None, date(2026, 1, 2))]
    # 사용자가 날짜를 고른 뒤에는 옮겨지지 않는다.
    _find(screen, QDateEdit, "historyDay").setDate(QDate(2025, 12, 31))
    select_screen(window, ScreenId.STATUS)
    screen.set_clock(lambda: datetime(2026, 1, 3, 9, 1, tzinfo=KST))
    actions.history.clear()
    select_screen(window, ScreenId.HISTORY)
    assert actions.history == [(HistoryTab.ROUNDS, None, date(2025, 12, 31))]


def test_opening_the_screen_calls_load_history_once_with_what_is_chosen(window, actions):
    _done(window)
    assert actions.history == []
    select_screen(window, ScreenId.HISTORY)
    assert actions.history == [(HistoryTab.ROUNDS, None, TODAY)]
    select_screen(window, ScreenId.STATUS)
    select_screen(window, ScreenId.HISTORY)
    assert len(actions.history) == 2


def test_nothing_is_called_before_the_first_setup_is_done(window, actions):
    window.apply(SettingsView((), 30, False))
    select_screen(window, ScreenId.HISTORY)
    assert actions.history == []
    assert _message(_history(window)).text() == "이 날의 기록이 없습니다."
    _done(window)
    # 처음 설정을 마치면 벼른 부름이 간다.
    assert actions.history == [(HistoryTab.ROUNDS, None, TODAY)]


def test_before_the_settings_arrive_the_table_place_says_loading(window, actions):
    select_screen(window, ScreenId.HISTORY)
    assert _message(_history(window)).text() == "불러오는 중"
    assert actions.history == []


def test_choosing_a_coin_a_day_or_a_tab_calls_load_history(window, actions):
    screen = _open(window, actions)
    _find(screen, QComboBox, "historyCoin").setCurrentIndex(1)
    assert actions.history == [(HistoryTab.ROUNDS, "KRW-AAA", TODAY)]
    _find(screen, QDateEdit, "historyDay").setDate(QDate(2026, 1, 1))
    assert actions.history[-1] == (HistoryTab.ROUNDS, "KRW-AAA", date(2026, 1, 1))
    _find(screen, QPushButton, "historyTab_fills").click()
    assert actions.history[-1] == (HistoryTab.FILLS, "KRW-AAA", date(2026, 1, 1))
    assert len(actions.history) == 3
    _find(screen, QPushButton, "historyTab_fills").click()  # 같은 탭을 다시 눌러도 부르지 않는다
    assert len(actions.history) == 3


# --- 상태별 모습 ---


def test_the_table_place_says_loading_until_a_value_for_the_choice_arrives(window, actions):
    screen = _open(window, actions)
    assert _message(screen).text() == "불러오는 중"
    assert not _message(screen).isHidden()
    assert _table(screen).isHidden()
    # 지금 고른 것이 아닌 값(다른 탭, 코인, 날짜)은 표로 그리지 않는다.
    window.apply(_view(HistoryTab.JUDGMENTS))
    assert _table(screen).isHidden()
    window.apply(_view(HistoryTab.ROUNDS, (), market="KRW-AAA", state=LoadState.NO_DATA))
    assert _message(screen).text() == "불러오는 중"
    window.apply(_view(HistoryTab.ROUNDS, (), day=date(2026, 1, 1), state=LoadState.NO_DATA))
    assert _message(screen).text() == "불러오는 중"
    window.apply(_view(HistoryTab.ROUNDS, (), state=LoadState.LOADING))
    assert _message(screen).text() == "불러오는 중"


def test_no_records_and_unreadable_records_are_one_line_each(window, actions):
    screen = _open(window, actions)
    window.apply(_view(HistoryTab.ROUNDS, (), state=LoadState.NO_DATA))
    assert _message(screen).text() == "이 날의 기록이 없습니다."
    assert _message(screen).property("tone") == "normal"
    assert _table(screen).isHidden()
    window.apply(_view(HistoryTab.ROUNDS, (), state=LoadState.FAILED))
    assert _message(screen).text() == "기록을 읽지 못했습니다."
    assert _message(screen).property("tone") == "warning"
    assert theme.ERROR.lower() in _message(screen).styleSheet().lower()
    assert _table(screen).isHidden()


def test_an_empty_ready_view_reads_as_no_records(window, actions):
    screen = _open(window, actions)
    window.apply(_view(HistoryTab.ROUNDS, (), state=LoadState.READY))
    assert _message(screen).text() == "이 날의 기록이 없습니다."


def test_the_export_button_can_be_pressed_when_there_are_no_records(window, actions):
    screen = _open(window, actions)
    window.apply(_view(HistoryTab.ROUNDS, (), state=LoadState.NO_DATA))
    button = _find(screen, QPushButton, "historyExport")
    assert button.isEnabled()
    window.set_save_dialog_handler(lambda name: "out.csv")
    button.click()
    assert actions.exports == [(HistoryTab.ROUNDS, None, "out.csv")]


# --- 표 ---


def test_the_table_shows_the_columns_and_the_cells_of_the_view(window, actions):
    screen = _open(window, actions)
    _find(screen, QPushButton, "historyTab_judgments").click()
    window.apply(_view(HistoryTab.JUDGMENTS))
    table = _table(screen)
    assert not table.isHidden()
    assert _message(screen).isHidden()
    assert table.columnCount() == 10
    assert [table.horizontalHeaderItem(i).text() for i in range(10)] == list(
        COLUMNS[HistoryTab.JUDGMENTS]
    )
    assert table.rowCount() == 5
    assert [table.item(0, c).text() for c in range(10)][:4] == ["7", "AAA", "오를 것", "103원"]
    assert table.item(3, 9).text() == "형식 오류"
    assert table.verticalHeader().defaultSectionSize() == theme.ROW_HEIGHT
    assert table.verticalHeader().isHidden()
    assert table.editTriggers() == QTableWidget.EditTrigger.NoEditTriggers


def test_numbers_money_and_times_are_right_aligned(window, actions):
    screen = _open(window, actions)
    _find(screen, QPushButton, "historyTab_judgments").click()
    window.apply(_view(HistoryTab.JUDGMENTS))
    table = _table(screen)
    right = {c for c in range(10) if _right(table, 0, c)}
    assert right == {0, 3, 4, 5, 7, 8}
    for tab, expected in (
        (HistoryTab.ROUNDS, {0, 1}),
        (HistoryTab.ORDERS, {0, 4, 5}),
        (HistoryTab.FILLS, {0, 3, 4, 5, 6}),
        (HistoryTab.PAUSES, {0, 2}),
    ):
        _find(screen, QPushButton, f"historyTab_{tab.value}").click()
        width = len(COLUMNS[tab])
        window.apply(_view(tab, (_row("k", ("x",) * width),)))
        shown = {c for c in range(width) if _right(_table(screen), 0, c)}
        assert shown == expected, tab


def test_the_difference_is_painted_by_its_sign_and_zero_is_not(window, actions):
    screen = _open(window, actions)
    _find(screen, QPushButton, "historyTab_judgments").click()
    window.apply(_view(HistoryTab.JUDGMENTS))
    table = _table(screen)
    assert _color(table, 1, 5) == theme.FALL.lower()  # ▼ -0.99%
    assert _color(table, 2, 5) == theme.RISE.lower()  # ▲ +0.49%
    # 0.00%는 '글' 색(칠하지 않는다). 다음 회차 전과 "-"는 보조 글이다.
    assert table.item(4, 5).foreground().style() == Qt.BrushStyle.NoBrush
    assert _color(table, 0, 4) == theme.TEXT_SUB.lower()
    assert _color(table, 0, 8) == theme.TEXT_SUB.lower()
    assert _color(table, 3, 3) == theme.TEXT_SUB.lower()
    # 맞음·틀림과 최종 판단은 '글' 색이다(오름·내림의 뜻이 아니다).
    assert table.item(1, 6).foreground().style() == Qt.BrushStyle.NoBrush
    assert table.item(2, 2).foreground().style() == Qt.BrushStyle.NoBrush


def test_the_realized_profit_of_a_fill_is_painted_by_its_sign(window, actions):
    screen = _open(window, actions)
    _find(screen, QPushButton, "historyTab_fills").click()
    rows = (
        _row("f3", ("10:00:00", "AAA", "매도", "110원", "1.0", "0원", "▲ +9원")),
        _row("f2", ("09:30:00", "AAA", "매도", "90원", "1.0", "0원", "▼ -11원")),
        _row("f1", ("09:00:00", "AAA", "매수", "100원", "1.0", "0원", "-")),
    )
    window.apply(_view(HistoryTab.FILLS, rows))
    table = _table(screen)
    assert _color(table, 0, 6) == theme.RISE.lower()
    assert _color(table, 1, 6) == theme.FALL.lower()
    assert _color(table, 2, 6) == theme.TEXT_SUB.lower()
    # 가격 칸은 칠하지 않는다(체결 가격의 "-"가 아니라 값).
    assert table.item(0, 3).foreground().style() == Qt.BrushStyle.NoBrush


def test_an_unconfirmed_order_is_error_colored_and_the_side_is_not(window, actions):
    screen = _open(window, actions)
    _find(screen, QPushButton, "historyTab_orders").click()
    rows = (
        _row("o2", ("10:00:00", "AAA", "매수", "지정가", "100원", "1.0", "판단", "확인 안 됨")),
        _row("o1", ("09:00:00", "AAA", "매도", "시장가", "-", "1.0", "손절", "체결")),
    )
    window.apply(_view(HistoryTab.ORDERS, rows))
    table = _table(screen)
    assert _color(table, 0, 7) == theme.ERROR.lower()
    assert table.item(1, 7).foreground().style() == Qt.BrushStyle.NoBrush
    assert table.item(0, 2).foreground().style() == Qt.BrushStyle.NoBrush  # 매수·매도는 '글' 색
    assert table.item(1, 2).foreground().style() == Qt.BrushStyle.NoBrush


def test_a_pause_that_is_still_on_is_caution_colored(window, actions):
    screen = _open(window, actions)
    _find(screen, QPushButton, "historyTab_pauses").click()
    rows = (
        _row("p2", ("10:00:00", "오류: 저장 실패", "멈춰 있음")),
        _row("p1", ("09:00:00", "사용자", "09:30:00")),
    )
    window.apply(_view(HistoryTab.PAUSES, rows))
    table = _table(screen)
    assert table.item(0, 2).text() == "멈춰 있음"
    assert _color(table, 0, 2) == theme.CAUTION.lower()
    assert table.item(1, 2).foreground().style() == Qt.BrushStyle.NoBrush


def test_trend_of_reads_the_sign_and_the_arrows():
    assert trend_of("+1.00%") == theme.TREND_RISE
    assert trend_of("▲ +1,234원") == theme.TREND_RISE
    assert trend_of("-0.98%") == theme.TREND_FALL
    assert trend_of("▼ -0.98%") == theme.TREND_FALL
    # 0, 부호가 없는 값, 값이 없다는 뜻의 "-"는 칠하지 않는다.
    for text in ("0.00%", "0원", "다음 회차 전", "-", "", "맞음"):
        assert trend_of(text) == "", text


def test_set_trend_leaves_the_no_value_dash_unpainted(qapp):
    label = QLabel("-")
    set_trend(label, "-")
    assert not label.property(theme.TREND_PROPERTY)
    set_trend(label, "▼ -1원")
    assert label.property(theme.TREND_PROPERTY) == theme.TREND_FALL
    set_trend(label, "-")
    assert not label.property(theme.TREND_PROPERTY)


def test_a_wide_table_scrolls_inside_and_keeps_the_column_widths(window, actions):
    screen = _open(window, actions)
    _find(screen, QPushButton, "historyTab_judgments").click()
    window.resize(1024, 640)
    window.apply(_view(HistoryTab.JUDGMENTS))
    table = _table(screen)
    assert table.horizontalScrollMode() == QTableWidget.ScrollMode.ScrollPerPixel
    assert not table.wordWrap()
    assert table.textElideMode() == Qt.TextElideMode.ElideNone
    for column in range(table.columnCount()):
        header = table.horizontalHeaderItem(column).text()
        needed = table.fontMetrics().horizontalAdvance(header)
        assert table.columnWidth(column) >= needed, header


def test_a_chosen_row_keeps_the_colors_of_its_cells(window, actions):
    screen = _open(window, actions)
    _find(screen, QPushButton, "historyTab_judgments").click()
    window.apply(_view(HistoryTab.JUDGMENTS))
    table = _table(screen)
    table.selectRow(2)  # ▲ +0.49%
    delegate = table.itemDelegate()
    for row, column, expected in ((2, 5, theme.RISE), (1, 5, theme.FALL), (0, 4, theme.TEXT_SUB)):
        option = QStyleOptionViewItem()
        delegate.initStyleOption(option, table.model().index(row, column))
        shown = option.palette.color(QPalette.ColorRole.HighlightedText).name().lower()
        assert shown == expected.lower(), (row, column)


def test_the_day_edit_is_readable_on_the_dark_theme(window):
    day = _find(_history(window), QDateEdit, "historyDay")
    style = day.styleSheet().lower()
    assert f"color: {theme.TEXT}".lower() in style
    assert f"background: {theme.SURFACE}".lower() in style
    assert day.calendarPopup()
    assert day.displayFormat() == "yyyy-MM-dd"


# --- 고른 줄과 자세한 글 ---


def test_choosing_a_row_shows_all_of_its_values_and_a_judgment_has_the_report_button(
    window, actions
):
    screen = _open(window, actions)
    _find(screen, QPushButton, "historyTab_judgments").click()
    window.apply(_view(HistoryTab.JUDGMENTS))
    detail = _find(screen, QLabel, "historyDetail")
    button = _find(screen, QPushButton, "historyReport")
    assert detail.text() == ""
    assert button.isHidden()
    _table(screen).selectRow(0)
    assert detail.text() == "회차: 7\n예측 까닭: 만든 까닭 하나"
    assert not button.isHidden()
    assert button.text() == "보고서 보기"
    _table(screen).selectRow(4)  # 보고서가 없는 줄
    assert detail.text() == ""
    assert button.isHidden()


def test_the_detail_is_plain_text(window, actions):
    screen = _open(window, actions)
    _find(screen, QPushButton, "historyTab_judgments").click()
    rows = (
        _row(
            "j1",
            ("1", "AAA", "오를 것", "1원", "-", "-", "-", "-", "-", ""),
            (("예측 까닭", '<a href="https://example.com">누르세요</a>'),),
            report_id=1,
            market="KRW-AAA",
        ),
    )
    window.apply(_view(HistoryTab.JUDGMENTS, rows))
    _table(screen).selectRow(0)
    detail = _find(screen, QLabel, "historyDetail")
    assert detail.textFormat() == Qt.TextFormat.PlainText
    assert "<a href" in detail.text()


def test_the_chosen_row_stays_when_a_new_value_for_the_same_choice_arrives(window, actions):
    screen = _open(window, actions)
    _find(screen, QPushButton, "historyTab_judgments").click()
    window.apply(_view(HistoryTab.JUDGMENTS))
    _table(screen).selectRow(2)
    new = (_row("j8", ("8", "AAA", "-", "-", "-", "-", "-", "-", "-", "")), *_judgment_rows())
    window.apply(_view(HistoryTab.JUDGMENTS, new))
    selected = _table(screen).selectionModel().selectedRows()
    assert [index.row() for index in selected] == [3]  # 회차 5의 줄이 한 칸 내려갔다
    assert _find(screen, QLabel, "historyDetail").text() == "회차: 5"


def test_the_chosen_row_is_dropped_when_the_choice_changes(window, actions):
    screen = _open(window, actions)
    _find(screen, QPushButton, "historyTab_judgments").click()
    window.apply(_view(HistoryTab.JUDGMENTS))
    _table(screen).selectRow(0)
    _find(screen, QPushButton, "historyTab_fills").click()
    window.apply(_view(HistoryTab.FILLS, (_row("f1", ("1",) * 7),)))
    assert _table(screen).selectionModel().selectedRows() == []
    assert _find(screen, QLabel, "historyDetail").text() == ""
    assert _find(screen, QPushButton, "historyReport").isHidden()


def test_the_report_button_opens_that_report_of_that_coin(window, actions):
    screen = _open(window, actions)
    _find(screen, QPushButton, "historyTab_judgments").click()
    window.apply(_view(HistoryTab.JUDGMENTS))
    _table(screen).selectRow(2)  # 회차 5, BBB, 보고서 50
    actions.calls.clear()
    _find(screen, QPushButton, "historyReport").click()
    assert current_screen(window) is ScreenId.REPORTS
    reports = screen_widget(window, ScreenId.REPORTS)
    assert _find(reports, QWidget, "reportCoin").currentItem().data(Qt.ItemDataRole.UserRole) == (
        "KRW-BBB"
    )
    assert "load_reports:KRW-BBB" in actions.calls


# --- 내보내기 ---


def test_the_export_asks_for_a_path_with_the_first_name_and_calls_export_history(
    window, actions
):
    screen = _open(window, actions)
    asked: list[str] = []

    def dialog(name: str) -> str:
        asked.append(name)
        return "chosen.csv"

    window.set_save_dialog_handler(dialog)
    _find(screen, QPushButton, "historyTab_judgments").click()
    _find(screen, QComboBox, "historyCoin").setCurrentIndex(1)
    _find(screen, QDateEdit, "historyDay").setDate(QDate(2025, 12, 1))  # 날짜와 상관없다
    _find(screen, QPushButton, "historyExport").click()
    assert asked == ["판단-AAA-20260102.csv"]
    assert actions.exports == [(HistoryTab.JUDGMENTS, "KRW-AAA", "chosen.csv")]


def test_the_first_name_of_all_coins_is_all_coins_without_a_space(window, actions):
    screen = _open(window, actions)
    asked: list[str] = []
    window.set_save_dialog_handler(lambda name: asked.append(name) or "")
    titles = (("rounds", "회차"), ("orders", "주문"), ("fills", "체결"), ("pauses", "멈춤"))
    for tab, title in titles:
        _find(screen, QPushButton, f"historyTab_{tab}").click()
        _find(screen, QPushButton, "historyExport").click()
        assert asked[-1] == f"{title}-모든코인-20260102.csv"


def test_a_cancelled_dialog_exports_nothing(window, actions):
    screen = _open(window, actions)
    window.set_save_dialog_handler(lambda name: "")
    _find(screen, QPushButton, "historyExport").click()
    assert actions.exports == []


def test_the_export_does_nothing_without_actions(qapp):
    made = create_main_window(UiContext("0.0.1", "X"))
    asked: list[str] = []
    made.set_save_dialog_handler(lambda name: asked.append(name) or "x.csv")
    try:
        _find(_history(made), QPushButton, "historyExport").click()
        assert asked == []
    finally:
        made.close()
        made.deleteLater()


def test_the_export_result_is_ok_or_error_and_never_shows_the_path(window, actions):
    screen = _open(window, actions)
    message = _find(screen, QLabel, "historyExportMessage")
    window.set_save_dialog_handler(lambda name: "C:/secret/folder/out.csv")
    _find(screen, QPushButton, "historyExport").click()
    window.apply(SaveResult(SAVE_TARGET_EXPORT, ok=True, message="내보냈습니다."))
    assert not message.isHidden()
    assert message.text() == "내보냈습니다."
    assert message.property("ok") is True
    assert message.property("tone") == "normal"
    window.apply(SaveResult(SAVE_TARGET_EXPORT, ok=False, message="내보내지 못했습니다."))
    assert message.text() == "내보내지 못했습니다."
    assert message.property("tone") == "warning"
    assert theme.ERROR.lower() in message.styleSheet().lower()
    for label in window.findChildren(QLabel):
        assert "secret" not in label.text()


def test_a_new_export_clears_the_old_result_and_the_result_is_not_an_alert(window, actions):
    screen = _open(window, actions)
    message = _find(screen, QLabel, "historyExportMessage")
    window.apply(SaveResult(SAVE_TARGET_EXPORT, ok=True, message="내보냈습니다."))
    window.set_save_dialog_handler(lambda name: "")
    _find(screen, QPushButton, "historyExport").click()
    assert message.isHidden()
    assert message.text() == ""
    # 내보내기의 결과는 설정 화면의 글이나 다른 자리에 섞이지 않는다.
    settings = screen_widget(window, ScreenId.SETTINGS)
    assert "내보냈습니다." not in [label.text() for label in settings.findChildren(QLabel)]


# --- 알림에서 오는 길(3.17.6) ---


@pytest.mark.parametrize(
    ("code", "tab"), [("A-27", HistoryTab.ORDERS), ("A-28", HistoryTab.FILLS)]
)
def test_a_trade_alert_opens_that_tab_for_its_coin_and_its_day(window, actions, code, tab):
    _done(window)
    center = screen_widget(window, ScreenId.STATUS)
    assert center is not None
    created = datetime(2026, 1, 2, 0, 30, tzinfo=UTC)  # 한국 시간 09:30
    window._go_alert(_alert(code, "KRW-BBB", created))
    assert current_screen(window) is ScreenId.HISTORY
    screen = _history(window)
    assert screen.chosen_tab() is tab
    assert screen.chosen_market() == "KRW-BBB"
    assert screen.chosen_day() == date(2026, 1, 2)
    assert actions.history[-1] == (tab, "KRW-BBB", date(2026, 1, 2))
    assert _find(screen, QPushButton, f"historyTab_{tab.value}").property("selected") is True


def test_the_alert_day_follows_the_nine_oclock_edge(window, actions):
    _done(window)
    before = datetime(2026, 1, 1, 23, 30, tzinfo=UTC)  # 한국 시간 08:30(앞 하루)
    window._go_alert(_alert("A-27", "KRW-AAA", before))
    assert _history(window).chosen_day() == date(2026, 1, 1)
    after = datetime(2026, 1, 2, 0, 0, tzinfo=UTC)  # 한국 시간 09:00(새 하루)
    window._go_alert(_alert("A-28", "KRW-AAA", after))
    assert _history(window).chosen_day() == date(2026, 1, 2)


def test_an_alert_for_a_coin_that_is_not_listed_opens_all_coins(window, actions):
    _done(window, ("KRW-AAA",))
    window._go_alert(_alert("A-27", "KRW-ZZZ", datetime(2026, 1, 2, 1, 0, tzinfo=UTC)))
    combo = _find(_history(window), QComboBox, "historyCoin")
    assert combo.currentIndex() == 0
    assert combo.currentText() == "모든 코인"
    assert _history(window).chosen_market() is None
    assert actions.history[-1] == (HistoryTab.ORDERS, None, date(2026, 1, 2))


def test_an_alert_from_the_history_screen_itself_calls_again_with_the_new_choice(
    window, actions
):
    screen = _open(window, actions)
    window._go_alert(_alert("A-28", "KRW-AAA", datetime(2026, 1, 1, 3, 0, tzinfo=UTC)))
    assert current_screen(window) is ScreenId.HISTORY
    assert actions.history == [(HistoryTab.FILLS, "KRW-AAA", date(2026, 1, 1))]
    assert screen.chosen_tab() is HistoryTab.FILLS


def test_going_to_the_history_does_not_change_the_day_back_when_opened_again(window, actions):
    _done(window)
    window._go_alert(_alert("A-27", "KRW-AAA", datetime(2025, 12, 30, 3, 0, tzinfo=UTC)))
    select_screen(window, ScreenId.STATUS)
    select_screen(window, ScreenId.HISTORY)
    assert _history(window).chosen_day() == date(2025, 12, 30)
