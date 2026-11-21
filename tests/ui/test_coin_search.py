"""코인 목록의 차례와 찾기 칸(M1 명세 3.14.3)의 시험. 시장 이름과 기호는 모두 시험이 만든 값이다."""

from datetime import UTC, datetime

import pytest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QWidget,
)

from coin_report_trader.core import (
    LoadState,
    Market,
    MarketsView,
    ScreenId,
    SettingsView,
    UiActions,
    UiContext,
)
from coin_report_trader.ui import MainWindow, create_main_window, screen_widget, select_screen

MARKETS = (
    Market("KRW-bnb", "비엔비", caution=False),
    Market("KRW-ETH", "이더", caution=False),
    Market("KRW-2Z", "투지", caution=False),
    Market("KRW-ADA", "에이다", caution=True),
    Market("KRW-1AB", "원에이비", caution=False),
    Market("KRW-AaB", "에이에이비", caution=False),
    Market("KRW-AAB", "더블에이", caution=False),
)
SORTED = ["1AB", "2Z", "AAB", "AaB", "ADA", "bnb", "ETH"]
"""정렬 키 (첫 글자가 숫자면 0, 아니면 1, 기호의 대문자, 기호)대로다."""
BLOCKED_UNTIL = datetime(2026, 1, 1, 1, 30, tzinfo=UTC)


class FakeActions:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

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


@pytest.fixture(params=["firstSetup", "settings"])
def screen(request, make_window):
    """코인 목록(`MARKETS`)을 받은 처음 설정 화면이나 설정 화면과 그 앞 이름(`prefix`)."""
    window = make_window(FakeActions())
    prefix = request.param
    if prefix == "firstSetup":
        window.apply(SettingsView())
        found = screen_widget(window, ScreenId.FIRST_SETUP)
    else:
        window.apply(SettingsView(("KRW-ETH",), 30, True))
        select_screen(window, ScreenId.SETTINGS)
        found = screen_widget(window, ScreenId.SETTINGS)
    window.apply(MarketsView(LoadState.READY, MARKETS))
    return window, found, prefix


def _find(parent: QWidget, kind: type, name: str):
    found = parent.findChild(kind, name)
    assert found is not None, name
    return found


def _rows(screen: QWidget, prefix: str) -> list[str]:
    """목록의 줄을 화면에 놓인 차례대로(기호만)."""
    layout = _find(screen, QWidget, f"{prefix}CoinsList").layout()
    names = [
        layout.itemAt(i).widget().objectName()
        for i in range(layout.count())
        if layout.itemAt(i).widget() is not None
    ]
    return [
        n.removeprefix(f"{prefix}CoinRow_KRW-") for n in names if n.startswith(f"{prefix}CoinRow_")
    ]


def _visible_rows(screen: QWidget, prefix: str) -> list[str]:
    return [
        symbol
        for symbol in _rows(screen, prefix)
        if not _find(screen, QWidget, f"{prefix}CoinRow_KRW-{symbol}").isHidden()
    ]


def _search(screen: QWidget, prefix: str) -> QLineEdit:
    return _find(screen, QLineEdit, f"{prefix}CoinSearch")


def _no_match_shown(screen: QWidget, prefix: str) -> str | None:
    label = _find(screen, QLabel, f"{prefix}CoinsNoMatch")
    return None if label.isHidden() else label.text()


# --- 차례 ---


def test_the_list_is_in_abc_order_with_digit_symbols_first(screen):
    _window, found, prefix = screen
    assert _rows(found, prefix) == SORTED


def test_each_line_is_the_symbol_and_the_korean_name(screen):
    _window, found, prefix = screen
    texts = {
        box.objectName().removeprefix(f"{prefix}Coin_KRW-"): box.text()
        for box in found.findChildren(QCheckBox)
    }
    assert texts == {
        "bnb": "bnb 비엔비",
        "ETH": "ETH 이더",
        "2Z": "2Z 투지",
        "ADA": "ADA 에이다",
        "1AB": "1AB 원에이비",
        "AaB": "AaB 에이에이비",
        "AAB": "AAB 더블에이",
    }


def test_a_new_list_is_sorted_again(screen):
    window, found, prefix = screen
    window.apply(
        MarketsView(
            LoadState.READY,
            (
                Market("KRW-ZZZ", "지", False),
                Market("KRW-9", "구", False),
                Market("KRW-B", "비", False),
            ),
        )
    )
    assert _rows(found, prefix) == ["9", "B", "ZZZ"]


def test_picking_order_is_the_chosen_order_not_the_list_order(screen):
    _window, found, prefix = screen
    if prefix == "settings":
        _find(found, QCheckBox, f"{prefix}Coin_KRW-ETH").click()  # 저장된 ETH를 뺀다
    _find(found, QCheckBox, f"{prefix}Coin_KRW-ETH").click()
    _find(found, QCheckBox, f"{prefix}Coin_KRW-1AB").click()
    selected = _find(found, QListWidget, f"{prefix}CoinsSelected")
    assert [selected.item(i).text() for i in range(selected.count())] == [
        "ETH 이더",
        "1AB 원에이비",
    ]


# --- 찾기 ---


def test_the_search_box_has_its_name_and_a_faint_hint(screen):
    _window, found, prefix = screen
    assert _find(found, QLabel, f"{prefix}CoinSearchTitle").text() == "찾기"
    assert _search(found, prefix).placeholderText() == "코인 기호나 한글 이름"
    assert _search(found, prefix).text() == ""
    assert _search(found, prefix).isEnabled()
    assert _visible_rows(found, prefix) == SORTED


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ad", ["ADA"]),
        ("ADA", ["ADA"]),
        ("aab", ["AAB", "AaB"]),  # 대소문자를 가리지 않는다
        ("a", ["1AB", "AAB", "AaB", "ADA"]),  # 기호의 어디든 든다
        ("에이", ["1AB", "AAB", "AaB", "ADA"]),  # 한글 이름(더블에이, 원에이비, 에이에이비, 에이다)
        ("비엔", ["bnb"]),
        ("  ada  ", ["ADA"]),  # 앞뒤 빈칸은 지운다
        ("\t투", ["2Z"]),
        ("", SORTED),
        ("   ", SORTED),  # 빈칸뿐이면 빈 글이다
    ],
)
def test_only_the_lines_that_contain_the_text_are_shown(screen, text, expected):
    _window, found, prefix = screen
    _search(found, prefix).setText(text)
    assert _visible_rows(found, prefix) == expected
    assert _no_match_shown(found, prefix) is None


def test_a_text_inside_the_text_still_needs_a_continuous_match(screen):
    _window, found, prefix = screen
    _search(found, prefix).setText("e d")  # 이름 둘에 걸친 글은 맞지 않는다
    assert _visible_rows(found, prefix) == []


def test_no_match_says_so_and_clearing_the_text_shows_every_line(screen):
    _window, found, prefix = screen
    _search(found, prefix).setText("없는코인")
    assert _visible_rows(found, prefix) == []
    assert _no_match_shown(found, prefix) == "찾는 코인이 없습니다."
    _search(found, prefix).setText("")
    assert _visible_rows(found, prefix) == SORTED
    assert _no_match_shown(found, prefix) is None


def test_an_empty_list_with_an_empty_search_does_not_say_nothing_was_found(screen):
    window, found, prefix = screen
    window.apply(MarketsView(LoadState.READY, ()))
    assert _rows(found, prefix) == []
    assert _no_match_shown(found, prefix) is None
    _search(found, prefix).setText("a")
    assert _no_match_shown(found, prefix) == "찾는 코인이 없습니다."


def test_the_chosen_state_stays_on_a_line_the_search_hides(screen):
    _window, found, prefix = screen
    _find(found, QCheckBox, f"{prefix}Coin_KRW-ADA").click()
    _search(found, prefix).setText("투")
    assert _visible_rows(found, prefix) == ["2Z"]
    assert _find(found, QCheckBox, f"{prefix}Coin_KRW-ADA").isChecked()
    selected = _find(found, QListWidget, f"{prefix}CoinsSelected")
    chosen = [selected.item(i).text() for i in range(selected.count())]
    assert "ADA 에이다" in chosen
    # 가려진 줄이어도 고른 코인은 순서 단추(빼기)로 다룰 수 있다.
    selected.setCurrentRow(chosen.index("ADA 에이다"))
    _find(found, QPushButton, f"{prefix}CoinRemove").click()
    assert not _find(found, QCheckBox, f"{prefix}Coin_KRW-ADA").isChecked()
    _search(found, prefix).setText("")
    assert _visible_rows(found, prefix) == SORTED
    assert not _find(found, QCheckBox, f"{prefix}Coin_KRW-ADA").isChecked()


def test_a_new_list_keeps_the_text_and_filters_again_with_it(screen):
    window, found, prefix = screen
    _search(found, prefix).setText("에이")
    assert _visible_rows(found, prefix) == ["1AB", "AAB", "AaB", "ADA"]
    window.apply(
        MarketsView(
            LoadState.READY,
            (
                Market("KRW-QQ", "큐에이", False),
                Market("KRW-ZZ", "제트", False),
                Market("KRW-AA", "에이", False),
            ),
        )
    )
    assert _search(found, prefix).text() == "에이"
    assert _rows(found, prefix) == ["AA", "QQ", "ZZ"]
    assert _visible_rows(found, prefix) == ["AA", "QQ"]  # 에이, 큐에이
    assert _no_match_shown(found, prefix) is None
    window.apply(MarketsView(LoadState.READY, (Market("KRW-ZZ", "제트", False),)))
    assert _visible_rows(found, prefix) == []
    assert _no_match_shown(found, prefix) == "찾는 코인이 없습니다."


@pytest.mark.parametrize(
    "view",
    [
        MarketsView(LoadState.LOADING),
        MarketsView(LoadState.FAILED, (), "네트워크 끊김"),
        MarketsView(LoadState.FAILED, (), "일시 차단", BLOCKED_UNTIL),
    ],
    ids=["loading", "failed", "blocked"],
)
def test_the_search_box_is_dim_while_there_is_no_list(screen, view):
    window, found, prefix = screen
    window.apply(view)
    assert not _search(found, prefix).isEnabled()
    # 목록을 다시 받으면 다시 쓸 수 있다.
    window.apply(MarketsView(LoadState.READY, MARKETS))
    assert _search(found, prefix).isEnabled()


def test_the_search_box_is_dim_before_any_list_was_asked_for(make_window):
    window = make_window(FakeActions())
    window.apply(SettingsView())
    found = screen_widget(window, ScreenId.FIRST_SETUP)
    assert not _search(found, "firstSetup").isEnabled()  # 받으라고 한 목록이 아직 없다
    window.apply(SettingsView(state=LoadState.FAILED))


def test_the_search_text_survives_a_failed_list_and_applies_to_the_next_one(screen):
    window, found, prefix = screen
    _search(found, prefix).setText("ada")
    window.apply(MarketsView(LoadState.FAILED, (), "네트워크 끊김"))
    assert _search(found, prefix).text() == "ada"
    window.apply(MarketsView(LoadState.READY, MARKETS))
    assert _visible_rows(found, prefix) == ["ADA"]
