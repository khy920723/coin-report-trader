"""5단계의 계정 화면(M5 명세 3.15.1, 3.15.3)의 시험. 값은 모두 만든 값이다."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QWidget,
)

from coin_report_trader.core import (
    SCREEN_TITLES,
    AccountRow,
    AccountState,
    AccountView,
    ScreenId,
    SettingsView,
    UiContext,
    UpbitKeyView,
)
from coin_report_trader.ui import (
    create_main_window,
    current_screen,
    screen_widget,
    select_screen,
    theme,
)
from coin_report_trader.ui.account_screen import AccountScreen, qty_text, row_cells
from coin_report_trader.ui.icons import screen_icon

NOW = datetime(2026, 1, 2, 1, 30, tzinfo=UTC)  # 한국 시간 10:30
READ = datetime(2026, 1, 2, 1, 29, tzinfo=UTC)  # 한국 시간 10:29


class Actions:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def refresh_markets(self) -> None: ...

    def save_settings(self, coins, retention_days, **kwargs) -> None:
        self.calls.append(("save_settings",))

    def load_reports(self, market, mode=None) -> None: ...

    def load_report(self, report_id) -> None: ...

    def load_history(self, tab, market, day, mode=None) -> None: ...

    def export_history(self, tab, market, path) -> None: ...

    def retry_candles(self) -> None: ...

    def mark_seen(self, alert_ids) -> None: ...

    def delete_alert(self, alert_id) -> None: ...

    def clear_alerts(self, category) -> None: ...

    def save_webhook(self, category, url) -> None: ...

    def delete_webhook(self, category) -> None: ...

    def send_test(self, category) -> None: ...

    def pause(self) -> None: ...

    def pause_and_sell_all(self) -> None: ...

    def resume_user(self) -> None: ...

    def resume_error(self) -> None: ...

    def account_visible(self, visible) -> None:
        self.calls.append(("account_visible", visible))

    def refresh_account(self) -> None:
        self.calls.append(("refresh_account",))

    def names(self) -> list[tuple]:
        return list(self.calls)


@pytest.fixture
def actions() -> Actions:
    return Actions()


@pytest.fixture
def window(qapp: QApplication, actions: Actions):
    made = create_main_window(UiContext("0.0.1", "X", actions))
    made.resize(1280, 1000)
    made.show()
    yield made
    made.close()
    made.deleteLater()


def _find(parent: QWidget, kind: type, name: str):
    found = parent.findChild(kind, name)
    assert found is not None, name
    return found


def _account(window) -> QWidget:
    return screen_widget(window, ScreenId.ACCOUNT)


def _text(window, name: str) -> str:
    return _find(_account(window), QLabel, name).text()


def _done(window, coins=("KRW-AAA", "KRW-BBB")) -> None:
    window.apply(SettingsView(coins, 30, True))


def _row(currency="AAA", **changes) -> AccountRow:
    values = {
        "currency": currency,
        "market": f"KRW-{currency}",
        "qty": Decimal(10),
        "locked": Decimal(0),
        "avg_buy_price": Decimal(1000),
        "avg_modified": False,
        "price": Decimal(1100),
        "buy_amount": Decimal(10000),
        "value": Decimal(11000),
        "pnl": Decimal(1000),
        "pnl_rate": Decimal("0.1"),
        "picked": True,
        "note": None,
    }
    values.update(changes)
    return AccountRow(**values)


def _example_rows() -> tuple[AccountRow, ...]:
    """3.15.2의 예(만든 값): AAA는 셈이 다 되고, BBB는 평균 매수가가 없고, CCC는 마켓이 없다."""
    return (
        _row("AAA"),
        _row(
            "BBB",
            qty=Decimal(2),
            avg_buy_price=None,
            price=Decimal(500),
            buy_amount=None,
            value=Decimal(1000),
            pnl=None,
            pnl_rate=None,
            picked=False,
            note="평균 매수가 없음",
        ),
        _row(
            "CCC",
            market=None,
            qty=Decimal(5),
            avg_buy_price=Decimal(300),
            price=None,
            buy_amount=None,
            value=None,
            pnl=None,
            pnl_rate=None,
            picked=False,
            note="원화 마켓 없음",
        ),
    )


def _view(state=AccountState.OK, rows=None, **changes) -> AccountView:
    values = {
        "state": state,
        "krw_available": Decimal(100000),
        "krw_locked": Decimal(20000),
        "holdings_value": Decimal(12000),
        "total_assets": Decimal(132000),
        "total_buy": Decimal(10000),
        "total_pnl": Decimal(1000),
        "total_pnl_rate": Decimal("0.1"),
        "rows": _example_rows() if rows is None else rows,
        "excluded_value": 1,
        "excluded_pnl": 2,
        "read_utc": READ,
        "error_name": None,
        "reading": False,
        "as_of": NOW,
    }
    values.update(changes)
    return AccountView(**values)


def _table(window) -> QTableWidget:
    return _find(_account(window), QTableWidget, "accountTable")


def _cell(window, row: int, column: int) -> str:
    return _table(window).item(row, column).text()


# --- 화면 목록과 단축키(3.15.1) ---


def test_the_account_comes_right_after_the_status_in_the_screen_list(window):
    order = list(ScreenId)
    assert order[order.index(ScreenId.STATUS) + 1] is ScreenId.ACCOUNT
    assert SCREEN_TITLES[ScreenId.ACCOUNT] == "계정"
    nav = _find(window, QListWidget, "screenNav")
    assert [nav.item(i).text() for i in range(nav.count())] == [
        "처음 설정",
        "현황",
        "계정",
        "보고서",
        "기록",
        "설정",
    ]
    assert not nav.item(2).icon().isNull()
    assert not screen_icon(ScreenId.ACCOUNT).isNull()
    assert _account(window).objectName() == "screen_account"
    assert _text(window, "screenTitle") == "계정"


def test_ctrl_3_opens_the_account_and_the_others_move_by_one(window):
    shortcuts = {
        shortcut.key().toString(): shortcut
        for shortcut in window.findChildren(QShortcut)
        if shortcut.key().toString().startswith("Ctrl+")
    }
    assert sorted(shortcuts) == [f"Ctrl+{n}" for n in range(1, 7)]
    expected = {
        1: ScreenId.FIRST_SETUP,
        2: ScreenId.STATUS,
        3: ScreenId.ACCOUNT,
        4: ScreenId.REPORTS,
        5: ScreenId.HISTORY,
        6: ScreenId.SETTINGS,
    }
    for number, screen_id in expected.items():
        elsewhere = ScreenId.STATUS if screen_id is ScreenId.SETTINGS else ScreenId.SETTINGS
        select_screen(window, elsewhere)
        shortcuts[QKeySequence(f"Ctrl+{number}").toString()].activated.emit()
        assert current_screen(window) is screen_id


def test_the_account_is_in_the_list_before_the_first_setup(window):
    select_screen(window, ScreenId.ACCOUNT)
    assert current_screen(window) is ScreenId.ACCOUNT


# --- 배치와 'ⓘ' ---


def test_the_parts_exist_and_there_is_no_export_button(window):
    _done(window)
    window.apply(_view())
    screen = _account(window)
    assert _find(screen, QPushButton, "accountRefresh").text() == "새로 고침"
    for name in (
        "accountMessage",
        "accountKrwAvailable",
        "accountKrwLocked",
        "accountHoldingsValue",
        "accountTotalAssets",
        "accountTotalBuy",
        "accountTotalPnl",
        "accountTotalPnlRate",
        "accountReadAt",
        "accountExcluded",
    ):
        assert _find(screen, QLabel, name) is not None
    _find(screen, QWidget, "accountSummary")
    _find(screen, QTableWidget, "accountTable")
    assert [b.objectName() for b in screen.findChildren(QPushButton)] == [
        "accountRefresh",
        "accountGoKey",
    ]
    captions = {
        "accountKrwAvailableLabel": "원화 주문 가능",
        "accountKrwLockedLabel": "원화 주문에 묶임",
        "accountHoldingsValueLabel": "보유 코인 평가 합",
        "accountTotalAssetsLabel": "총자산",
        "accountTotalBuyLabel": "총 매수 금액",
        "accountTotalPnlLabel": "총 평가 손익",
        "accountTotalPnlRateLabel": "수익률",
    }
    for name, text in captions.items():
        assert _text(window, name) == text


def test_the_summary_info_has_the_four_lines(window):
    _done(window)
    window.apply(_view())
    tip = _find(_account(window), QWidget, "accountSummaryInfo").toolTip()
    for line in (
        "업비트 계정에서 읽은 값입니다. 모의매매의 가상 계정은 현황에 있습니다.",
        "이 화면은 보기만 하고 주문하지 않습니다. "
        "실제 보유의 손절은 실거래 중에 고른 코인에만 걸립니다.",
        "계정 화면만 쓰려면 키에 자산조회 권한만 있으면 됩니다.",
        "원화 마켓이 없는 코인과 계좌 조회에 들지 않는 자산은 총자산에 들지 않아 업비트 앱의 값과 "
        "다를 수 있습니다.",
    ):
        assert line in tip


def test_the_table_columns(window):
    _done(window)
    window.apply(_view())
    table = _table(window)
    assert [table.horizontalHeaderItem(i).text() for i in range(table.columnCount())] == [
        "코인",
        "보유 수량",
        "평균 매수가",
        "현재가",
        "매수 금액",
        "평가 금액",
        "평가 손익",
        "수익률",
        "고른 코인",
        "비고",
    ]


# --- 상태별 모습 ---


def test_before_the_first_setup_only_one_line_shows(window):
    window.apply(SettingsView((), 30, False))
    window.apply(_view())  # 값이 와도 처음 설정 전에는 이 한 줄만 보인다
    select_screen(window, ScreenId.ACCOUNT)
    screen = _account(window)
    message = _find(screen, QLabel, "accountMessage")
    assert message.text() == "처음 설정을 마치면 계정을 볼 수 있습니다."
    assert not message.isHidden()
    assert _find(screen, QWidget, "accountSummary").isHidden()
    assert _find(screen, QTableWidget, "accountTable").isHidden()
    assert _find(screen, QPushButton, "accountGoKey").isHidden()
    assert not _find(screen, QPushButton, "accountRefresh").isEnabled()


def test_without_a_key_the_go_key_button_opens_the_key_group(window, actions):
    _done(window)
    window.apply(UpbitKeyView(False, None, None, True, None))
    select_screen(window, ScreenId.ACCOUNT)
    screen = _account(window)
    assert _text(window, "accountMessage") == "업비트 키가 없습니다"
    go = _find(screen, QPushButton, "accountGoKey")
    assert go.text() == "업비트 키 넣으러 가기"
    assert not go.isHidden()
    assert not _find(screen, QPushButton, "accountRefresh").isEnabled()
    actions.calls.clear()
    go.click()
    assert current_screen(window) is ScreenId.SETTINGS
    key = _find(screen_widget(window, ScreenId.SETTINGS), QWidget, "settingsUpbitKey")
    scroll = _find(window, QScrollArea, "screenScroll_settings")
    assert scroll.isAncestorOf(key)
    # 가기만 한다: 계정을 읽으라고 부르지 않는다.
    assert ("refresh_account",) not in actions.calls


def test_a_state_from_the_backend_wins_over_the_key_guess(window):
    _done(window)
    window.apply(UpbitKeyView(False, None, None, True, None))
    window.apply(_view(AccountState.NO_KEY, rows=(), krw_available=None))
    assert _text(window, "accountMessage") == "업비트 키가 없습니다"
    assert not _find(_account(window), QPushButton, "accountGoKey").isHidden()


def test_the_first_read_shows_loading(window):
    _done(window)
    window.apply(UpbitKeyView(True, None, None, True, None))
    screen = _account(window)
    assert _text(window, "accountMessage") == "불러오는 중"
    assert _find(screen, QWidget, "accountSummary").isHidden()
    assert _find(screen, QTableWidget, "accountTable").isHidden()
    assert _find(screen, QPushButton, "accountGoKey").isHidden()
    window.apply(
        _view(AccountState.LOADING, rows=(), krw_available=None, reading=True, read_utc=None)
    )
    assert _text(window, "accountMessage") == "불러오는 중"
    assert _find(screen, QWidget, "accountSummary").isHidden()


def test_the_summary_values_of_the_example(window):
    _done(window)
    window.apply(_view())
    assert _find(_account(window), QLabel, "accountMessage").isHidden()
    assert _text(window, "accountKrwAvailable") == "100,000원"
    assert _text(window, "accountKrwLocked") == "20,000원"
    assert _text(window, "accountHoldingsValue") == "12,000원"
    assert _text(window, "accountTotalAssets") == "132,000원"
    assert _text(window, "accountTotalBuy") == "10,000원"
    assert _text(window, "accountTotalPnl") == "▲ +1,000원"
    assert _text(window, "accountTotalPnlRate") == "▲ +10.00%"
    assert _text(window, "accountReadAt") == "10:29에 읽음"
    assert (
        _text(window, "accountExcluded")
        == "평가하지 못해 총자산에서 뺀 코인 1개 · 손익을 셈하지 못한 코인 2개"
    )


def test_the_summary_money_floors_and_missing_values_are_dashes(window):
    _done(window)
    window.apply(
        _view(
            krw_available=Decimal("1234.9"),
            total_buy=None,
            total_pnl=None,
            total_pnl_rate=None,
            holdings_value=Decimal(0),
        )
    )
    assert _text(window, "accountKrwAvailable") == "1,234원"
    assert _text(window, "accountTotalBuy") == "-"
    assert _text(window, "accountTotalPnl") == "-"
    assert _text(window, "accountTotalPnlRate") == "-"
    assert _text(window, "accountHoldingsValue") == "0원"


@pytest.mark.parametrize(
    ("value_n", "pnl_n", "text"),
    [
        (1, 0, "평가하지 못해 총자산에서 뺀 코인 1개"),
        (0, 2, "손익을 셈하지 못한 코인 2개"),
        (3, 1, "평가하지 못해 총자산에서 뺀 코인 3개 · 손익을 셈하지 못한 코인 1개"),
    ],
)
def test_the_excluded_line_only_writes_the_nonzero_parts(window, value_n, pnl_n, text):
    _done(window)
    window.apply(_view(excluded_value=value_n, excluded_pnl=pnl_n))
    assert _text(window, "accountExcluded") == text


def test_no_excluded_line_when_nothing_was_left_out(window):
    _done(window)
    window.apply(_view(excluded_value=0, excluded_pnl=0))
    assert _find(_account(window), QLabel, "accountExcluded").isHidden()


def test_the_profit_colors_follow_the_sign(window):
    _done(window)
    window.apply(_view(total_pnl=Decimal(-500), total_pnl_rate=Decimal("-0.05")))
    pnl = _find(_account(window), QLabel, "accountTotalPnl")
    rate = _find(_account(window), QLabel, "accountTotalPnlRate")
    assert pnl.text() == "▼ -500원"
    assert pnl.property(theme.TREND_PROPERTY) == theme.TREND_FALL
    assert rate.property(theme.TREND_PROPERTY) == theme.TREND_FALL
    window.apply(_view())
    assert pnl.property(theme.TREND_PROPERTY) == theme.TREND_RISE
    window.apply(_view(total_pnl=Decimal(0), total_pnl_rate=Decimal(0)))
    assert not pnl.property(theme.TREND_PROPERTY)
    assert pnl.text() == "0원"


# --- 표 ---


def test_the_table_rows_of_the_example(window):
    _done(window)
    window.apply(_view())
    table = _table(window)
    assert table.rowCount() == 3
    assert [_cell(window, 0, c) for c in range(10)] == [
        "AAA",
        "10",
        "1,000",
        "1,100",
        "10,000원",
        "11,000원",
        "▲ +1,000원",
        "▲ +10.00%",
        "고름",
        "",
    ]
    assert [_cell(window, 1, c) for c in range(10)] == [
        "BBB",
        "2",
        "-",
        "500",
        "-",
        "1,000원",
        "-",
        "-",
        "",
        "평균 매수가 없음",
    ]
    assert [_cell(window, 2, c) for c in range(10)] == [
        "CCC",
        "5",
        "300",
        "-",
        "-",
        "-",
        "-",
        "-",
        "",
        "원화 마켓 없음",
    ]


def test_the_rows_come_in_the_order_the_backend_gave(window):
    _done(window)
    window.apply(_view(rows=(_row("ZZZ"), _row("AAA"), _row("MMM"))))
    assert [_cell(window, i, 0) for i in range(3)] == ["ZZZ", "AAA", "MMM"]


def test_numbers_are_right_aligned_and_the_rest_left(window):
    _done(window)
    window.apply(_view())
    table = _table(window)
    right = int(Qt.AlignmentFlag.AlignRight.value)
    for column in range(10):
        aligned = bool(int(table.item(0, column).textAlignment()) & right)
        assert aligned is (column in (1, 2, 3, 4, 5, 6, 7)), column


def test_profit_cells_are_painted_and_dashes_are_sub_text(window):
    _done(window)
    rows = (
        _row("AAA"),
        _row("BBB", pnl=Decimal(-300), pnl_rate=Decimal("-0.03"), buy_amount=Decimal(1000)),
        _row("CCC", pnl=None, pnl_rate=None),
    )
    window.apply(_view(rows=rows))
    table = _table(window)

    def color(row: int, column: int) -> str:
        return table.item(row, column).foreground().color().name().lower()

    assert color(0, 6) == theme.RISE.lower()
    assert color(0, 7) == theme.RISE.lower()
    assert color(1, 6) == theme.FALL.lower()
    assert color(1, 7) == theme.FALL.lower()
    assert color(2, 6) == theme.TEXT_SUB.lower()
    assert table.item(0, 1).foreground().style() == Qt.BrushStyle.NoBrush


def test_a_changed_average_gets_a_star_and_a_tip(window):
    _done(window)
    window.apply(_view(rows=(_row("AAA", avg_modified=True), _row("BBB"))))
    assert _cell(window, 0, 2) == "1,000*"
    tip = _table(window).item(0, 2).toolTip()
    assert tip == "업비트가 이 평균 매수가를 고쳤다고 알렸습니다."
    assert _cell(window, 1, 2) == "1,000"
    assert _table(window).item(1, 2).toolTip() == ""


def test_the_quantity_has_eight_places_without_trailing_zeros_and_the_locked_part():
    assert qty_text(Decimal("0.123456789")) == "0.12345678"
    assert qty_text(Decimal("10")) == "10"
    assert qty_text(Decimal("10.50000000")) == "10.5"
    assert qty_text(Decimal("0.00000001")) == "0.00000001"
    assert qty_text(Decimal("0")) == "0"
    cells = row_cells(_row("AAA", qty=Decimal("10.5"), locked=Decimal("2.25")))
    assert cells[1] == "10.5 (묶임 2.25)"
    assert row_cells(_row("AAA", qty=Decimal(3), locked=Decimal(0)))[1] == "3"


def test_the_price_keeps_its_places(window):
    _done(window)
    rows = (_row("AAA", avg_buy_price=Decimal("0.00001234"), price=Decimal("1234567.5")),)
    window.apply(_view(rows=rows))
    assert _cell(window, 0, 2) == "0.00001234"
    assert _cell(window, 0, 3) == "1,234,567.5"


def test_many_rows_have_no_limit_and_scroll_inside(window):
    _done(window)
    rows = tuple(_row(f"C{i:03d}") for i in range(60))
    window.apply(_view(rows=rows))
    table = _table(window)
    assert table.rowCount() == 60
    assert table.horizontalScrollMode() == QTableWidget.ScrollMode.ScrollPerPixel
    assert not table.wordWrap()


def test_no_holdings_says_so_in_the_table_place(window):
    _done(window)
    window.apply(_view(rows=(), excluded_value=0, excluded_pnl=0))
    note = _find(_account(window), QLabel, "accountTableNote")
    assert note.text() == "보유한 코인이 없습니다."
    assert not note.isHidden()
    assert _table(window).isHidden()
    assert not _find(_account(window), QWidget, "accountSummary").isHidden()
    window.apply(_view())
    assert note.isHidden()
    assert not _table(window).isHidden()


# --- 읽기 실패와 일시 차단 ---


def test_a_failed_read_keeps_the_last_values_dim_with_the_error_line(window):
    _done(window)
    window.apply(_view(AccountState.FAILED, error_name="키 만료"))
    message = _find(_account(window), QLabel, "accountMessage")
    assert message.text() == "계정을 읽지 못했습니다(마지막으로 읽은 시각 10:29). 키 만료"
    assert message.property("tone") == "warning"
    assert theme.ERROR.lower() in message.styleSheet().lower()
    summary = _find(_account(window), QWidget, "accountSummary")
    assert not summary.isHidden()
    assert not _find(_account(window), QLabel, "accountTotalAssets").isEnabled()
    assert _text(window, "accountTotalAssets") == "132,000원"  # 마지막 값 그대로
    assert not _table(window).isEnabled()
    assert _find(_account(window), QWidget, "accountSummaryInfo").isEnabled()  # 'ⓘ'는 듣는다
    window.apply(_view())  # 다시 읽으면 흐림이 걷힌다
    assert _find(_account(window), QLabel, "accountTotalAssets").isEnabled()
    assert _table(window).isEnabled()
    assert _find(_account(window), QLabel, "accountMessage").isHidden()


def test_a_failed_first_read_has_a_dash_for_the_time_and_no_values(window):
    _done(window)
    window.apply(
        _view(
            AccountState.FAILED,
            rows=(),
            krw_available=None,
            read_utc=None,
            error_name="응답 형식 오류",
        )
    )
    assert _text(window, "accountMessage") == (
        "계정을 읽지 못했습니다(마지막으로 읽은 시각 -). 응답 형식 오류"
    )
    assert _find(_account(window), QWidget, "accountSummary").isHidden()
    assert _table(window).isHidden()


def test_a_blocked_read_keeps_the_values_dim_and_says_why(window):
    _done(window)
    window.apply(_view(AccountState.BLOCKED))
    message = _find(_account(window), QLabel, "accountMessage")
    assert message.text() == "오류 멈춤(일시 차단) 동안에는 계정을 읽지 않습니다."
    assert not _find(_account(window), QLabel, "accountTotalAssets").isEnabled()
    assert not _table(window).isEnabled()
    assert not _find(_account(window), QPushButton, "accountRefresh").isEnabled()


def test_the_last_read_time_may_be_on_another_day(window):
    _done(window)
    old = datetime(2025, 12, 31, 1, 0, tzinfo=UTC)
    window.apply(_view(AccountState.FAILED, error_name="x", read_utc=old))
    assert "2025-12-31 10:00" in _text(window, "accountMessage")
    assert _text(window, "accountReadAt") == "2025-12-31 10:00에 읽음"


# --- 새로 고침 ---


def test_the_refresh_button_asks_once_and_is_dim_while_reading(window, actions):
    _done(window)
    window.apply(_view())
    button = _find(_account(window), QPushButton, "accountRefresh")
    assert button.isEnabled()
    actions.calls.clear()
    button.click()
    assert actions.calls == [("refresh_account",)]
    window.apply(_view(reading=True))
    assert not button.isEnabled()
    button.click()
    assert actions.calls == [("refresh_account",)]
    window.apply(_view(AccountState.FAILED, error_name="x"))
    assert button.isEnabled()  # 읽기에 실패한 뒤에는 다시 읽을 수 있다


# --- 보임과 숨음(3.15.4) ---


def test_choosing_and_leaving_the_account_tell_the_backend(window, actions):
    _done(window)
    actions.calls.clear()
    select_screen(window, ScreenId.ACCOUNT)
    assert actions.calls == [("account_visible", True)]
    select_screen(window, ScreenId.STATUS)
    assert actions.calls == [("account_visible", True), ("account_visible", False)]
    select_screen(window, ScreenId.HISTORY)  # 계정 아닌 화면 사이는 알리지 않는다
    assert len(actions.calls) == 2


def test_a_hidden_window_is_not_visible_for_the_account(window, actions):
    _done(window)
    select_screen(window, ScreenId.ACCOUNT)
    actions.calls.clear()
    window.hide()  # 트레이로 숨음
    assert actions.calls == [("account_visible", False)]
    window.show()
    assert actions.calls == [("account_visible", False), ("account_visible", True)]


def test_a_minimized_window_is_not_visible_for_the_account(window, actions):
    _done(window)
    select_screen(window, ScreenId.ACCOUNT)
    actions.calls.clear()
    window.setWindowState(Qt.WindowState.WindowMinimized)
    assert actions.calls == [("account_visible", False)]
    window.setWindowState(Qt.WindowState.WindowNoState)
    assert actions.calls == [("account_visible", False), ("account_visible", True)]


def test_no_visibility_calls_without_actions(qapp):
    made = create_main_window(UiContext("0.0.1", "X"))
    made.show()
    try:
        select_screen(made, ScreenId.ACCOUNT)
        select_screen(made, ScreenId.STATUS)
    finally:
        made.close()
        made.deleteLater()


def test_the_account_screen_makes_no_other_calls(window, actions):
    _done(window)
    window.apply(_view())
    select_screen(window, ScreenId.ACCOUNT)
    window.apply(_view(AccountState.FAILED, error_name="x"))
    window.apply(_view(AccountState.BLOCKED))
    kinds = {call[0] for call in actions.calls}
    assert kinds <= {"account_visible"}


def test_the_screen_object_renders_by_itself(qapp):
    screen = AccountScreen()
    try:
        screen.render(state=AccountState.OK, view=_view())
        assert screen.findChild(QLabel, "accountTotalAssets").text() == "132,000원"
        screen.render(state=AccountState.BEFORE_SETUP, view=None)
        assert screen.findChild(QLabel, "accountMessage").text().startswith("처음 설정")
    finally:
        screen.deleteLater()
