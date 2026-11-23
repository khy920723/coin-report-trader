"""알림센터(설계서 8, M1 명세 4절 25번)의 시험.

`AlertView`, `AlertsView`, `WebhookView`는 시험이 만든 값으로 직접 만든다. 웹후크 주소 꼴의 글은
소스에 통째로 적지 않고 실행 중에 이어 붙여 만든다.
창을 띄우지 않으므로 보이는지는 `isHidden()`으로 본다.
"""

from datetime import UTC, datetime

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QWidget,
)

from coin_report_trader.core import (
    AlertCategory,
    AlertDetail,
    AlertsView,
    AlertView,
    CoinView,
    LoadState,
    Mode,
    SaveResult,
    ScreenId,
    SettingsView,
    UiActions,
    UiContext,
    WebhookView,
)
from coin_report_trader.ui import (
    MainWindow,
    create_main_window,
    current_screen,
    screen_widget,
    select_screen,
)
from coin_report_trader.ui.info_icon import InfoIcon

CREATED = datetime(2026, 1, 2, 1, 30, tzinfo=UTC)  # 한국 시간 2026-01-02 10:30
TITLES = {
    AlertCategory.TRADE: "매매 알림",
    AlertCategory.REPORT: "보고서 알림",
    AlertCategory.LIMIT: "제한 알림",
    AlertCategory.HOLDING: "보유 알림",
    AlertCategory.ERROR: "오류 알림",
    AlertCategory.OTHER: "기타 알림",
}
DELETE_TITLE = "알림을 지울까요?"
DELETE_BODY = (
    "이 알림 한 줄을 지웁니다. 지운 알림은 되살릴 수 없습니다. "
    "보고서, 판단, 주문, 체결, 외부 변동의 기록과 디스코드로 이미 보낸 글은 그대로 남습니다."
)
CLEAR_TITLE = "이 탭의 알림을 모두 지울까요?"
WEBHOOK_DELETE_TITLE = "디스코드 웹후크 주소를 지울까요?"
NOT_SET = "디스코드 주소를 넣지 않은 분류의 알림은 디스코드로 가지 않습니다."
BAD_FORMAT = "디스코드 웹후크 주소의 형식이 아닙니다."
SAVE_FAILED = "저장하지 못했습니다."
DELETE_FAILED = "지우지 못했습니다. 저장된 주소는 그대로 남아 있습니다."
SENDING = "확인용 글을 보내는 중입니다."
SENT = "확인용 글을 보냈습니다. 디스코드 채널을 확인하세요."
TEST_FAILED = "확인용 글을 보내지 못했습니다."


def make_url() -> str:
    """실행 중에 이어 붙인 주소 꼴의 글(소스에 통째로 적지 않는다)."""
    host = "disc" + "ord.com"
    path = "/api/" + "webhooks/"
    return "https:" + "//" + host + path + "123456789012345678" + "/" + ("Ab9-_." * 12)[:64]


class FakeActions:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def count(self, name: str) -> int:
        return sum(1 for call in self.calls if call[0] == name)

    def last(self, name: str) -> tuple:
        return [call for call in self.calls if call[0] == name][-1]

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
        trade=None,
        accept_below_stop=(),
    ) -> None:
        self.calls.append(("save_settings", coins, retention_days, first_setup))

    def load_reports(self, market) -> None:
        pass  # 2단계의 동작. 화면이 부르기 전까지 아무것도 하지 않는다

    def load_report(self, report_id) -> None:
        pass

    def retry_candles(self) -> None:
        self.calls.append(("retry_candles",))

    def mark_seen(self, alert_ids) -> None:
        self.calls.append(("mark_seen", tuple(alert_ids)))

    def delete_alert(self, alert_id) -> None:
        self.calls.append(("delete_alert", alert_id))

    def clear_alerts(self, category) -> None:
        self.calls.append(("clear_alerts", category))

    def save_webhook(self, category, url) -> None:
        self.calls.append(("save_webhook", category, url))

    def delete_webhook(self, category) -> None:
        self.calls.append(("delete_webhook", category))

    def send_test(self, category) -> None:
        self.calls.append(("send_test", category))


class Confirm:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.asked: list[tuple[str, str]] = []

    def __call__(self, title: str, body: str) -> bool:
        self.asked.append((title, body))
        return self.answer


def _unexpected_confirm(title: str, body: str) -> bool:
    raise AssertionError(f"예상하지 못한 확인 창: {title}")


@pytest.fixture
def make_window(qapp: QApplication):
    made: list[MainWindow] = []

    def factory(actions: UiActions | None = None) -> MainWindow:
        window = create_main_window(UiContext("0.0.1", "X", actions))
        window.set_confirm_handler(_unexpected_confirm)
        made.append(window)
        return window

    yield factory
    for window in made:
        window.deleteLater()


@pytest.fixture
def actions() -> FakeActions:
    return FakeActions()


# --- 값을 만드는 도구 ---


def _alert(alert_id: int, category: AlertCategory = AlertCategory.TRADE, **changes) -> AlertView:
    values = {
        "id": alert_id,
        "created_at": CREATED,
        "mode": Mode.PAPER,
        "category": category,
        "code": "A-1",
        "coin": None,
        "text": f"글 {alert_id}",
        "seen": True,
    }
    values.update(changes)
    return AlertView(**values)


def _detail(coin: str | None = "KRW-AAA") -> AlertDetail:
    return AlertDetail(
        "봉 받기", coin, "네트워크 끊김", "연결이 끊어졌다", "다음 받는 때 다시 받습니다."
    )


def _view(*alerts: AlertView, unseen: dict | None = None, state=LoadState.READY) -> AlertsView:
    if unseen is None:
        unseen = {}
        for alert in alerts:
            if not alert.seen:
                unseen[alert.category] = unseen.get(alert.category, 0) + 1
    return AlertsView(alerts, unseen, state)


# --- 읽는 도구 ---


def _status(window: MainWindow) -> QWidget:
    return screen_widget(window, ScreenId.STATUS)


def _find(parent: QWidget, kind: type, name: str):
    found = parent.findChild(kind, name)
    assert found is not None, name
    return found


def _assert_not_set_info(screen: QWidget) -> None:
    """주소가 없을 때의 글은 늘 보이는 글이 아니라 칸 이름 옆 'ⓘ'(`alertWebhookInfo`)의 툴팁이다."""
    assert _text(screen, "alertWebhookMessage") is None
    info = _find(screen, InfoIcon, "alertWebhookInfo")
    assert not info.isHidden()
    assert info.toolTip() == NOT_SET
    assert info.accessibleDescription() == NOT_SET
    assert info.accessibleName() == f"{_text(screen, 'alertWebhookTitle')} 설명"


def _text(screen: QWidget, name: str) -> str | None:
    label = _find(screen, QLabel, name)
    return None if label.isHidden() else label.text()


def _tone(screen: QWidget, name: str) -> str:
    return _find(screen, QLabel, name).property("tone")


def _shown(screen: QWidget, name: str, kind: type = QWidget) -> bool:
    return not _find(screen, kind, name).isHidden()


def _tab(screen: QWidget, category: AlertCategory) -> QPushButton:
    return _find(screen, QPushButton, f"alertTab_{category.value}")


def _rows(screen: QWidget) -> list[int]:
    layout = _find(screen, QWidget, "alertList").layout()
    return [
        int(layout.itemAt(i).widget().objectName().removeprefix("alertRow_"))
        for i in range(layout.count() - 1)
    ]


def _click_line(screen: QWidget, alert_id: int) -> None:
    QTest.mouseClick(_find(screen, QLabel, f"alertLine_{alert_id}"), Qt.MouseButton.LeftButton)


def _open(window: MainWindow, *updates, coins=("KRW-AAA",)) -> QWidget:
    """처음 설정을 마친 `SettingsView`와 값들을 보내고 현황 화면을 돌려준다."""
    window.apply(SettingsView(coins, 30, True))
    for update in updates:
        window.apply(update)
    return _status(window)


def _all_texts(window: MainWindow) -> list[str]:
    texts = [label.text() for label in window.findChildren(QLabel)]
    for edit in window.findChildren(QLineEdit):
        texts += [edit.text(), edit.placeholderText(), edit.toolTip()]
    texts += [widget.toolTip() for widget in window.findChildren(QWidget)]
    return texts


# --- 탭 ---


def test_six_tabs_in_the_category_order_with_titles(make_window):
    window = make_window(FakeActions())
    screen = _open(window)
    tabs = [
        child.objectName()
        for child in _find(screen, QWidget, "alertCenter").findChildren(QPushButton)
        if child.objectName().startswith("alertTab_")
    ]
    assert tabs == [f"alertTab_{c.value}" for c in AlertCategory]
    assert [_tab(screen, c).text() for c in AlertCategory] == list(TITLES.values())


def test_a_tab_shows_the_unseen_count_after_its_name(make_window):
    window = make_window(FakeActions())
    screen = _open(window)
    window.apply(
        _view(
            _alert(1, AlertCategory.ERROR, seen=False),
            _alert(2, AlertCategory.ERROR, seen=False),
            _alert(3, AlertCategory.ERROR),
            unseen={AlertCategory.ERROR: 2, AlertCategory.OTHER: 1},
        )
    )
    assert _tab(screen, AlertCategory.ERROR).text() == "오류 알림 (2)"
    assert _tab(screen, AlertCategory.OTHER).text() == "기타 알림 (1)"
    assert _tab(screen, AlertCategory.TRADE).text() == "매매 알림"  # 키가 없으면 0이다.


def test_the_first_tab_is_trade_with_bold_underline_and_the_last_chosen_one_stays(
    make_window, actions
):
    window = make_window(actions)
    screen = _open(window, _view(_alert(1, AlertCategory.ERROR)))
    center = _find(screen, QWidget, "alertCenter")
    assert center is not None
    for category in AlertCategory:
        font = _tab(screen, category).font()
        assert (font.bold(), font.underline()) == (
            (True, True) if category is AlertCategory.TRADE else (False, False)
        )
    _tab(screen, AlertCategory.ERROR).click()
    assert _tab(screen, AlertCategory.ERROR).font().bold()
    assert not _tab(screen, AlertCategory.TRADE).font().underline()
    assert _rows(screen) == [1]
    # 다른 화면을 보았다가 돌아와도 마지막에 고른 탭이다.
    select_screen(window, ScreenId.SETTINGS)
    select_screen(window, ScreenId.STATUS)
    assert _tab(screen, AlertCategory.ERROR).font().underline()
    assert _rows(screen) == [1]


def test_only_the_chosen_tabs_alerts_are_listed_newest_first(make_window):
    window = make_window(FakeActions())
    screen = _open(
        window,
        _view(
            _alert(5),
            _alert(9, AlertCategory.ERROR),
            _alert(4),
            _alert(7),
            _alert(2, AlertCategory.OTHER),
        ),
    )
    assert _rows(screen) == [5, 4, 7]  # 받은 차례 그대로다(새것이 앞이라고 보낸 차례).
    _tab(screen, AlertCategory.OTHER).click()
    assert _rows(screen) == [2]
    assert screen.findChild(QWidget, "alertRow_5") is None


# --- 화면 이름 목록에는 " ({n})"이 없고 상태 띠의 "알림 {n}건"이다(M3 명세 3.17.1) ---


def _nav_text(window: MainWindow) -> str:
    nav = _find(window, QListWidget, "screenNav")
    return nav.item(list(ScreenId).index(ScreenId.STATUS)).text()


def _chip(window: MainWindow) -> QPushButton:
    return _find(window, QPushButton, "tradeBandAlerts")


def test_the_total_unseen_count_is_in_the_band_and_not_in_the_status_name(make_window):
    window = make_window(FakeActions())
    assert _nav_text(window) == "현황"
    assert _chip(window).isHidden()  # 0건이면 보이지 않는다
    _open(window)
    window.apply(_view(unseen={AlertCategory.ERROR: 2, AlertCategory.OTHER: 1}))
    assert _nav_text(window) == "현황"
    assert not _chip(window).isHidden()
    assert _chip(window).text() == "알림 3건"
    window.apply(_view(unseen={}))
    assert _chip(window).isHidden()
    window.apply(_view(unseen={AlertCategory.TRADE: 0}))
    assert _chip(window).isHidden()
    nav = _find(window, QListWidget, "screenNav")
    assert [nav.item(i).text() for i in range(5)] == ["처음 설정", "현황", "보고서", "기록", "설정"]


def test_a_click_on_the_alert_chip_opens_the_status_screen_at_the_first_unseen_tab(
    make_window, actions
):
    window = make_window(actions)
    screen = _open(
        window, _view(_alert(1, AlertCategory.TRADE), _alert(2, AlertCategory.ERROR, seen=False))
    )
    select_screen(window, ScreenId.SETTINGS)
    _chip(window).click()
    assert current_screen(window) is ScreenId.STATUS
    assert _tab(screen, AlertCategory.ERROR).font().bold()  # 보지 않은 알림이 있는 맨 앞 탭


def test_a_click_on_the_status_name_opens_the_first_tab_with_unseen_alerts(make_window, actions):
    window = make_window(actions)
    screen = _open(
        window,
        _view(
            _alert(1, AlertCategory.OTHER, seen=False),
            _alert(2, AlertCategory.ERROR, seen=False),
        ),
    )
    nav = _find(window, QListWidget, "screenNav")
    item = nav.item(list(ScreenId).index(ScreenId.STATUS))
    # 켤 때처럼 `select_screen`으로 현황을 고른 때는 "매매 알림" 탭을 그대로 둔다.
    select_screen(window, ScreenId.SETTINGS)
    select_screen(window, ScreenId.STATUS)
    assert _tab(screen, AlertCategory.TRADE).font().underline()
    # 사용자가 누르면 보지 않은 알림이 있는 맨 앞 탭(오류 알림)이 열린다.
    nav.itemClicked.emit(item)
    assert _tab(screen, AlertCategory.ERROR).font().underline()
    assert _rows(screen) == [2]
    # 이미 현황이 보일 때 다시 눌러도 같은 규칙이다.
    _tab(screen, AlertCategory.TRADE).click()
    nav.itemClicked.emit(item)
    assert _tab(screen, AlertCategory.ERROR).font().underline()


def _click_nav(window: MainWindow, screen_id: ScreenId, *, release: bool = True) -> None:
    """화면 이름 목록의 줄을 실제 마우스 이벤트로 누른다(누르면 줄이 바뀌고, 떼면 `itemClicked`)."""
    nav = _find(window, QListWidget, "screenNav")
    item = nav.item(list(ScreenId).index(screen_id))
    pos = nav.visualItemRect(item).center()
    viewport = nav.viewport()
    if release:
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    else:
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)


def test_a_real_click_on_the_status_name_marks_only_the_tab_it_opens(make_window, actions):
    window = make_window(actions)
    window.apply(SettingsView(("KRW-AAA",), 30, True))
    # 기타 탭을 고른 채 설정 화면으로 간다. 그동안 기타와 오류에 보지 않은 알림이 하나씩 들어온다.
    select_screen(window, ScreenId.STATUS)
    _tab(_status(window), AlertCategory.OTHER).click()
    select_screen(window, ScreenId.SETTINGS)
    window.apply(
        _view(
            _alert(1, AlertCategory.OTHER, seen=False),
            _alert(2, AlertCategory.ERROR, seen=False),
        )
    )
    assert actions.count("mark_seen") == 0
    _click_nav(window, ScreenId.STATUS)
    screen = _status(window)
    assert current_screen(window) is ScreenId.STATUS
    # 열린 탭은 오류 알림이고, 열지 않은 기타 알림의 알림은 본 것으로 알리지 않는다.
    assert _tab(screen, AlertCategory.ERROR).font().underline()
    assert [c for c in actions.calls if c[0] == "mark_seen"] == [("mark_seen", (2,))]


def test_a_press_that_ends_elsewhere_still_marks_the_shown_tab_later(make_window, actions):
    window = make_window(actions)
    window.apply(SettingsView(("KRW-AAA",), 30, True))
    select_screen(window, ScreenId.SETTINGS)
    window.apply(_view(_alert(1, seen=False)))
    _click_nav(window, ScreenId.STATUS, release=False)  # 누르고 아직 떼지 않았다.
    assert actions.count("mark_seen") == 0
    # 다른 곳에서 손을 떼면 `itemClicked`가 오지 않는다. 손을 뗀 뒤의 다음 값에서 알린다.
    QTest.mouseRelease(
        _find(window, QListWidget, "screenNav").viewport(), Qt.MouseButton.LeftButton
    )
    window.apply(_view(_alert(1, seen=False)))
    assert [c for c in actions.calls if c[0] == "mark_seen"] == [("mark_seen", (1,))]


def test_a_tab_opened_after_a_press_that_ended_elsewhere_marks_its_lines(make_window, actions):
    window = make_window(actions)
    window.apply(SettingsView(("KRW-AAA",), 30, True))
    select_screen(window, ScreenId.SETTINGS)
    window.apply(_view(_alert(1, AlertCategory.ERROR, seen=False)))
    _click_nav(window, ScreenId.STATUS, release=False)
    nav = _find(window, QListWidget, "screenNav")
    # 목록의 빈 곳에서 손을 뗀다. `itemClicked`는 오지 않는다.
    QTest.mouseRelease(
        nav.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        nav.viewport().rect().bottomRight(),
    )
    assert current_screen(window) is ScreenId.STATUS
    assert actions.count("mark_seen") == 0
    # 사용자가 탭 단추를 눌러 연 탭의 줄은 바로 본 알림이 된다.
    QTest.mouseClick(_tab(_status(window), AlertCategory.ERROR), Qt.MouseButton.LeftButton)
    assert [c for c in actions.calls if c[0] == "mark_seen"] == [("mark_seen", (1,))]


def test_a_click_without_unseen_alerts_keeps_the_chosen_tab(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _view(_alert(1, AlertCategory.ERROR)))
    _tab(screen, AlertCategory.ERROR).click()
    nav = _find(window, QListWidget, "screenNav")
    nav.itemClicked.emit(nav.item(list(ScreenId).index(ScreenId.STATUS)))
    assert _tab(screen, AlertCategory.ERROR).font().underline()


# --- 줄 ---


def test_a_line_shows_time_mode_coin_and_text(make_window):
    window = make_window(FakeActions())
    screen = _open(
        window,
        _view(
            _alert(1, AlertCategory.ERROR, coin="KRW-AAA", text="봉 통계를 만들지 못했습니다."),
            _alert(2, AlertCategory.ERROR, text="설정을 읽지 못했습니다."),
        ),
    )
    _tab(screen, AlertCategory.ERROR).click()
    assert _text(screen, "alertLine_1") == (
        "2026-01-02 10:30 · 모의매매 · AAA: 봉 통계를 만들지 못했습니다."
    )
    assert _text(screen, "alertLine_2") == "2026-01-02 10:30 · 모의매매: 설정을 읽지 못했습니다."


def test_the_coin_is_added_to_every_alert_that_has_one(make_window):
    window = make_window(FakeActions())
    screen = _open(
        window,
        _view(
            _alert(
                1, AlertCategory.ERROR, code="A-29", coin="KRW-BBB", text="저장하지 못했습니다."
            ),
            _alert(2, AlertCategory.OTHER, code="A-30", coin="KRW-CCC", text="예상하지 못한 오류"),
        ),
    )
    _tab(screen, AlertCategory.ERROR).click()
    assert _text(screen, "alertLine_1").split(": ")[0].endswith("· BBB")
    _tab(screen, AlertCategory.OTHER).click()
    assert _text(screen, "alertLine_2").split(": ")[0].endswith("· CCC")


def test_unseen_lines_have_a_dot_and_bold_text_seen_ones_do_not(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _view(_alert(1, seen=False), _alert(2, seen=True)))
    unseen = _find(screen, QLabel, "alertLine_1")
    seen = _find(screen, QLabel, "alertLine_2")
    assert unseen.text().startswith("● ")
    assert unseen.font().bold()
    assert not seen.text().startswith("●")
    assert not seen.font().bold()


def test_the_discord_failed_mark_is_a_warning_at_the_end_of_the_line(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _view(_alert(1, discord_failed=True), _alert(2)))
    assert _text(screen, "alertDiscordFailed_1") == "디스코드로 보내지 못함"
    assert _tone(screen, "alertDiscordFailed_1") == "warning"
    assert screen.findChild(QLabel, "alertDiscordFailed_2") is None


def test_a_naive_created_at_does_not_raise_and_negative_ids_work(make_window, actions):
    window = make_window(actions)
    screen = _open(window, _view(_alert(-1, created_at=datetime(2026, 1, 2, 1, 30), seen=False)))
    assert _text(screen, "alertLine_-1") == "● - · 모의매매: 글 -1"
    assert actions.last("mark_seen") == ("mark_seen", (-1,))


# --- 펼친 글 ---


def test_error_and_other_lines_unfold_and_fold_by_clicking(make_window, actions):
    window = make_window(actions)
    screen = _open(
        window,
        _view(
            _alert(1, AlertCategory.ERROR, detail=_detail()),
            _alert(2, AlertCategory.OTHER, detail=_detail(None)),
        ),
    )
    _tab(screen, AlertCategory.ERROR).click()
    assert _text(screen, "alertDetail_1") is None
    _click_line(screen, 1)
    assert _text(screen, "alertDetail_1").split("\n") == [
        "단계: 봉 받기",
        "코인: AAA",
        "오류 이름: 네트워크 끊김",
        "내용: 연결이 끊어졌다",
        "그 뒤에 한 일: 다음 받는 때 다시 받습니다.",
    ]
    _click_line(screen, 1)
    assert _text(screen, "alertDetail_1") is None
    _tab(screen, AlertCategory.OTHER).click()
    _click_line(screen, 2)
    assert _text(screen, "alertDetail_2").split("\n") == [
        "단계: 봉 받기",
        "오류 이름: 네트워크 끊김",
        "내용: 연결이 끊어졌다",
        "그 뒤에 한 일: 다음 받는 때 다시 받습니다.",
    ]
    assert actions.count("delete_alert") == 0


def test_an_unfolded_line_stays_unfolded_when_a_new_alerts_view_arrives(make_window):
    window = make_window(FakeActions())
    first = _alert(1, AlertCategory.ERROR, detail=_detail())
    screen = _open(window, _view(first))
    _tab(screen, AlertCategory.ERROR).click()
    _click_line(screen, 1)
    window.apply(_view(_alert(2, AlertCategory.ERROR, detail=_detail()), first))
    assert _text(screen, "alertDetail_1") is not None
    assert _text(screen, "alertDetail_2") is None
    window.apply(_view(_alert(2, AlertCategory.ERROR, detail=_detail())))  # 1은 지워졌다
    window.apply(_view(first, _alert(2, AlertCategory.ERROR, detail=_detail())))
    assert _text(screen, "alertDetail_1") is None  # 지워진 줄이 돌아와도 접힌 채다.


def test_clicking_other_categories_or_alerts_without_detail_does_nothing(make_window, actions):
    window = make_window(actions)
    screen = _open(
        window,
        _view(
            _alert(1, AlertCategory.TRADE),
            _alert(2, AlertCategory.ERROR),  # 자세한 글이 없다
        ),
    )
    _click_line(screen, 1)
    assert _text(screen, "alertDetail_1") is None
    _tab(screen, AlertCategory.ERROR).click()
    _click_line(screen, 2)
    assert _text(screen, "alertDetail_2") is None
    assert actions.count("mark_seen") == 0


# --- 본 알림 ---


def test_mark_seen_is_called_for_the_unseen_lines_of_the_shown_tab(make_window, actions):
    window = make_window(actions)
    window.apply(SettingsView(("KRW-AAA",), 30, True))
    assert current_screen(window) is ScreenId.STATUS
    window.apply(_view(_alert(1, seen=False), _alert(2), _alert(3, seen=False)))
    assert actions.calls == [("mark_seen", (1, 3))]
    # 같은 줄은 다시 알리지 않는다.
    window.apply(_view(_alert(1, seen=False), _alert(2), _alert(3, seen=False)))
    assert actions.count("mark_seen") == 1
    # 새 보지 않은 줄은 알린다.
    window.apply(_view(_alert(4, seen=False), _alert(1, seen=False), _alert(2), _alert(3)))
    assert actions.last("mark_seen") == ("mark_seen", (4,))


def test_mark_seen_waits_for_the_status_screen_and_the_tab(make_window, actions):
    window = make_window(actions)
    window.apply(SettingsView(("KRW-AAA",), 30, True))
    select_screen(window, ScreenId.SETTINGS)
    window.apply(_view(_alert(1, seen=False), _alert(2, AlertCategory.ERROR, seen=False)))
    assert actions.count("mark_seen") == 0  # 다른 화면이 보이는 동안은 알리지 않는다.
    select_screen(window, ScreenId.STATUS)
    assert [c for c in actions.calls if c[0] == "mark_seen"] == [("mark_seen", (1,))]
    _tab(_status(window), AlertCategory.ERROR).click()
    assert actions.last("mark_seen") == ("mark_seen", (2,))


def test_mark_seen_is_not_called_without_unseen_lines_or_a_readable_list(make_window, actions):
    window = make_window(actions)
    _open(window, _view(_alert(1), _alert(2)))
    window.apply(_view(_alert(3, seen=False), state=LoadState.FAILED))
    window.apply(_view(_alert(3, seen=False), state=LoadState.LOADING))
    assert actions.count("mark_seen") == 0


def test_mark_seen_is_asked_again_after_the_screen_is_opened_again(make_window, actions):
    window = make_window(actions)
    _open(window, _view(_alert(1, seen=False)))
    assert actions.count("mark_seen") == 1
    select_screen(window, ScreenId.SETTINGS)
    select_screen(window, ScreenId.STATUS)
    # 조립이 아직 본 것으로 바꾸지 못했다면(같은 줄이 그대로 보지 않음) 다시 연 때 다시 알린다.
    assert actions.count("mark_seen") == 2


# --- 손으로 지우기와 확인 창 ---


def test_delete_a_line_asks_and_only_confirm_calls(make_window, actions):
    window = make_window(actions)
    screen = _open(window, _view(_alert(1), _alert(2)))
    cancel = Confirm(False)
    window.set_confirm_handler(cancel)
    _find(screen, QPushButton, "alertDelete_2").click()
    assert cancel.asked == [(DELETE_TITLE, DELETE_BODY)]
    assert actions.count("delete_alert") == 0
    confirm = Confirm(True)
    window.set_confirm_handler(confirm)
    _find(screen, QPushButton, "alertDelete_2").click()
    assert confirm.asked == [(DELETE_TITLE, DELETE_BODY)]
    assert actions.last("delete_alert") == ("delete_alert", 2)
    assert _find(screen, QPushButton, "alertDelete_1").text() == "지우기"


def test_clear_the_tab_counts_seen_and_unseen_alerts(make_window, actions):
    window = make_window(actions)
    screen = _open(
        window,
        _view(
            _alert(1, seen=False),
            _alert(2),
            _alert(3),
            _alert(4, AlertCategory.ERROR),
            unseen={AlertCategory.TRADE: 1},
        ),
    )
    clear = _find(screen, QPushButton, "alertClear")
    assert clear.text() == "이 탭 모두 지우기"
    assert clear.isEnabled()
    cancel = Confirm(False)
    window.set_confirm_handler(cancel)
    clear.click()
    expected = (
        "매매 알림 3건을 모두 지웁니다. 지운 알림은 되살릴 수 없습니다. "
        "보고서, 판단, 주문, 체결, 외부 변동의 기록과 디스코드로 이미 보낸 글은 그대로 남습니다."
    )
    assert cancel.asked == [(CLEAR_TITLE, expected)]
    assert actions.count("clear_alerts") == 0
    window.set_confirm_handler(Confirm(True))
    clear.click()
    assert actions.last("clear_alerts") == ("clear_alerts", AlertCategory.TRADE)


def test_clear_the_tab_is_dim_when_the_tab_has_no_alerts(make_window, actions):
    window = make_window(actions)
    screen = _open(window, _view(_alert(1, AlertCategory.ERROR)))
    clear = _find(screen, QPushButton, "alertClear")
    assert not clear.isEnabled()
    clear.click()
    assert actions.count("clear_alerts") == 0
    _tab(screen, AlertCategory.ERROR).click()
    assert clear.isEnabled()


def test_delete_results_show_a_warning_only_when_failed_and_not_on_other_screens(
    make_window, actions
):
    window = make_window(actions)
    screen = _open(window, _view(_alert(1)))
    window.apply(SaveResult("alert_delete", ok=False, message="지우지 못했습니다."))
    assert _text(screen, "alertDeleteMessage") == "지우지 못했습니다."
    assert _tone(screen, "alertDeleteMessage") == "warning"
    # 처음 설정과 설정 화면의 글은 바뀌지 않는다.
    assert _text(screen_widget(window, ScreenId.SETTINGS), "settingsMessage") is None
    assert _text(screen_widget(window, ScreenId.FIRST_SETUP), "firstSetupMessage") is None
    window.apply(SaveResult("alert_delete", ok=True))
    assert _text(screen, "alertDeleteMessage") is None
    window.apply(SaveResult("alert_delete", ok=False, message="지우지 못했습니다."))
    window.set_confirm_handler(Confirm(True))
    _find(screen, QPushButton, "alertDelete_1").click()
    assert _text(screen, "alertDeleteMessage") is None  # 다시 지우면 앞의 글은 지워진다.
    window.apply(SaveResult("alert_delete", ok=False))
    assert _text(screen, "alertDeleteMessage") is None


# --- 목록 자리의 상태 ---


def test_the_list_area_states(make_window):
    window = make_window(FakeActions())
    screen = _open(window)
    assert _text(screen, "alertListMessage") == "불러오는 중"
    window.apply(_view())
    assert _text(screen, "alertListMessage") == "매매 알림이 없습니다."
    _tab(screen, AlertCategory.OTHER).click()
    assert _text(screen, "alertListMessage") == "기타 알림이 없습니다."
    window.apply(_view(_alert(1), state=LoadState.FAILED))
    assert _text(screen, "alertListMessage") == "알림을 읽지 못했습니다."
    assert _tone(screen, "alertListMessage") == "warning"
    assert not _find(screen, QPushButton, "alertClear").isEnabled()
    window.apply(_view(state=LoadState.LOADING))
    assert _text(screen, "alertListMessage") == "불러오는 중"
    window.apply(_view(_alert(1), _alert(2, AlertCategory.OTHER)))
    assert _text(screen, "alertListMessage") is None
    assert _rows(screen) == [2]


def test_a_failed_alerts_view_removes_the_lines_of_the_chosen_tab(make_window):
    window = make_window(FakeActions())
    screen = _open(window, _view(_alert(1)))
    assert _rows(screen) == [1]
    assert _text(screen, "alertListMessage") is None
    # 골라진 탭(매매 알림)의 알림이 든 채 읽지 못함이 오면 줄은 없어지고 글만 보인다.
    window.apply(_view(_alert(1), state=LoadState.FAILED))
    assert _rows(screen) == []
    assert screen.findChild(QWidget, "alertRow_1") is None
    assert _text(screen, "alertListMessage") == "알림을 읽지 못했습니다."


# --- 알림센터는 글 하나만 보이는 현황에서도 보인다 ---


@pytest.mark.parametrize("phase", ["waiting", "before_setup", "unreadable"])
def test_the_alert_center_stays_when_the_status_screen_shows_one_line(make_window, actions, phase):
    window = make_window(actions)
    if phase == "before_setup":
        window.apply(SettingsView())
    elif phase == "unreadable":
        window.apply(SettingsView(state=LoadState.FAILED))
    window.apply(_view(_alert(1, text="먼저 온 알림")))
    screen = _status(window)
    assert not _find(screen, QWidget, "statusContent").isVisibleTo(screen)
    assert _find(screen, QWidget, "statusMessage").isVisibleTo(screen)
    # 부모가 숨겨진 것까지 보는 `isVisibleTo`로, 알림센터가 숨겨진 묶음 안에 있지 않음을 확인한다.
    assert _find(screen, QWidget, "alertCenter").isVisibleTo(screen)
    assert _find(screen, QLabel, "alertLine_1").isVisibleTo(screen)
    assert _rows(screen) == [1]
    assert "먼저 온 알림" in _text(screen, "alertLine_1")
    assert actions.count("retry_candles") == 0


# --- 주소 칸 ---


def _webhook_screen(window: MainWindow, *views: WebhookView) -> QWidget:
    screen = _open(window, _view())
    for view in views:
        window.apply(view)
    return screen


def test_the_address_box_is_for_the_chosen_tab_and_hides_the_typed_text(make_window):
    window = make_window(FakeActions())
    screen = _webhook_screen(window, WebhookView(AlertCategory.TRADE, saved=False))
    assert _text(screen, "alertWebhookTitle") == "매매 알림 디스코드 웹후크 주소"
    edit = _find(screen, QLineEdit, "alertWebhookInput")
    assert edit.echoMode() == QLineEdit.EchoMode.Password
    assert edit.placeholderText() == "넣지 않음"
    assert edit.isEnabled()
    _tab(screen, AlertCategory.ERROR).click()
    assert _text(screen, "alertWebhookTitle") == "오류 알림 디스코드 웹후크 주소"


def test_before_the_first_webhook_view_the_box_is_not_usable(make_window, actions):
    window = make_window(actions)
    screen = _webhook_screen(window)
    edit = _find(screen, QLineEdit, "alertWebhookInput")
    assert not edit.isEnabled()
    assert edit.placeholderText() == "불러오는 중"
    assert not _find(screen, QPushButton, "alertWebhookSave").isEnabled()
    assert not _find(screen, QPushButton, "alertWebhookTest").isEnabled()
    assert _text(screen, "alertWebhookMessage") is None
    _find(screen, QPushButton, "alertWebhookSave").click()
    assert actions.count("save_webhook") == 0


def test_empty_and_blank_addresses_do_nothing_and_show_no_warning(make_window, actions):
    window = make_window(actions)
    screen = _webhook_screen(window, WebhookView(AlertCategory.TRADE, saved=False))
    edit = _find(screen, QLineEdit, "alertWebhookInput")
    save = _find(screen, QPushButton, "alertWebhookSave")
    for text in ("", "   ", "\t "):
        edit.setText(text)
        save.click()
        assert actions.count("save_webhook") == 0
        _assert_not_set_info(screen)  # 경고 글이 아닌 평소 글이고, 칸 이름 옆 'ⓘ'의 툴팁이다.
        assert _tone(screen, "alertWebhookMessage") == "normal"


@pytest.mark.parametrize(
    "bad",
    ["not an address", "http://example.com", "https://example.com/x"],
)
def test_a_wrongly_shaped_address_is_not_sent_and_shows_the_warning(make_window, actions, bad):
    window = make_window(actions)
    screen = _webhook_screen(window, WebhookView(AlertCategory.TRADE, saved=False))
    edit = _find(screen, QLineEdit, "alertWebhookInput")
    edit.setText(bad)
    _find(screen, QPushButton, "alertWebhookSave").click()
    assert actions.count("save_webhook") == 0
    assert _text(screen, "alertWebhookMessage") == BAD_FORMAT
    assert _tone(screen, "alertWebhookMessage") == "warning"
    # 글을 고치기 시작하면 경고는 사라진다.
    edit.setText("")
    QTest.keyClicks(edit, "x")
    _assert_not_set_info(screen)


def test_a_good_address_calls_save_webhook_with_the_trimmed_text_and_clears_the_box(
    make_window, actions
):
    window = make_window(actions)
    screen = _webhook_screen(window, WebhookView(AlertCategory.ERROR, saved=False))
    _tab(screen, AlertCategory.ERROR).click()
    url = make_url()
    edit = _find(screen, QLineEdit, "alertWebhookInput")
    edit.setText(f"  {url}\n")
    _find(screen, QPushButton, "alertWebhookSave").click()
    assert actions.last("save_webhook") == ("save_webhook", AlertCategory.ERROR, url)
    assert edit.text() == ""
    # 창 안의 어느 입력 칸, 글, 말풍선에도 주소가 없다.
    token = url.rsplit("/", 1)[1]
    assert not any(token in text for text in _all_texts(window))
    assert not any("webhooks" in text for text in _all_texts(window))


def test_the_box_keeps_no_undo_history_of_the_address(make_window, actions):
    window = make_window(actions)
    screen = _webhook_screen(window, WebhookView(AlertCategory.TRADE, saved=False))
    edit = _find(screen, QLineEdit, "alertWebhookInput")
    edit.setText(make_url())
    _find(screen, QPushButton, "alertWebhookSave").click()
    assert edit.text() == ""
    # 칸을 보통 글 모양으로 바꾸고 되돌리기를 해도 넣었던 주소가 돌아오지 않는다.
    edit.setEchoMode(QLineEdit.EchoMode.Normal)
    edit.setEnabled(True)
    edit.undo()
    assert edit.text() == ""
    # 바꾸기 뒤 그만두기로 비운 칸도 같다.
    window.apply(WebhookView(AlertCategory.TRADE, saved=True))
    _find(screen, QPushButton, "alertWebhookChange").click()
    edit.setText(make_url())
    _find(screen, QPushButton, "alertWebhookCancel").click()
    edit.setEchoMode(QLineEdit.EchoMode.Normal)
    edit.undo()
    assert edit.text() == ""
    # 주소를 넣은 채 다른 탭 단추를 눌러 비운 칸도 같다.
    window.apply(WebhookView(AlertCategory.TRADE, saved=False))
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    edit.setText(make_url())
    _tab(_status(window), AlertCategory.OTHER).click()
    assert edit.text() == ""
    edit.setEchoMode(QLineEdit.EchoMode.Normal)
    edit.setEnabled(True)
    edit.undo()
    assert edit.text() == ""


def test_the_box_keeps_no_undo_history_after_the_address_is_deleted(make_window, actions):
    window = make_window(actions)
    screen = _webhook_screen(window, WebhookView(AlertCategory.TRADE, saved=True))
    edit = _find(screen, QLineEdit, "alertWebhookInput")
    # 바꾸기로 주소를 넣은 채 주소 지우기를 확인한다.
    _find(screen, QPushButton, "alertWebhookChange").click()
    edit.setText(make_url())
    window.set_confirm_handler(Confirm(True))
    _find(screen, QPushButton, "alertWebhookDelete").click()
    assert edit.text() == ""
    edit.setEchoMode(QLineEdit.EchoMode.Normal)
    edit.setEnabled(True)
    edit.undo()
    assert edit.text() == ""


def test_the_save_result_shows_through_the_webhook_view_and_unlocks_the_box(make_window, actions):
    window = make_window(actions)
    screen = _webhook_screen(window, WebhookView(AlertCategory.TRADE, saved=False))
    edit = _find(screen, QLineEdit, "alertWebhookInput")
    edit.setText(make_url())
    _find(screen, QPushButton, "alertWebhookSave").click()
    assert not edit.isEnabled()  # 결과가 오기 전에는 다시 누를 수 없다.
    window.apply(WebhookView(AlertCategory.TRADE, saved=False, save_message=SAVE_FAILED))
    assert edit.isEnabled()
    assert _text(screen, "alertWebhookMessage") == SAVE_FAILED
    assert _tone(screen, "alertWebhookMessage") == "warning"
    window.apply(WebhookView(AlertCategory.TRADE, saved=True))
    assert not edit.isEnabled()
    assert edit.placeholderText() == "(가려서 보임) 저장됨"
    assert edit.text() == ""
    assert _text(screen, "alertWebhookMessage") is None


def test_saved_boxes_show_change_and_delete_and_the_test_button(make_window, actions):
    window = make_window(actions)
    screen = _webhook_screen(window, WebhookView(AlertCategory.TRADE, saved=True))
    assert not _shown(screen, "alertWebhookSave", QPushButton)
    assert _find(screen, QPushButton, "alertWebhookChange").text() == "바꾸기"
    assert _find(screen, QPushButton, "alertWebhookDelete").text() == "지우기"
    assert _shown(screen, "alertWebhookChange", QPushButton)
    assert _shown(screen, "alertWebhookDelete", QPushButton)
    assert _find(screen, QPushButton, "alertWebhookSave").text() == "저장"
    # 바꾸기: 칸이 열리고 "저장"이 보인다. 그만두기로 되돌린다.
    _find(screen, QPushButton, "alertWebhookChange").click()
    edit = _find(screen, QLineEdit, "alertWebhookInput")
    assert edit.isEnabled()
    assert _shown(screen, "alertWebhookSave", QPushButton)
    assert not _shown(screen, "alertWebhookChange", QPushButton)
    assert _find(screen, QPushButton, "alertWebhookCancel").text() == "그만두기"
    _find(screen, QPushButton, "alertWebhookCancel").click()
    assert not edit.isEnabled()
    assert edit.placeholderText() == "(가려서 보임) 저장됨"
    assert _shown(screen, "alertWebhookChange", QPushButton)
    # 바꾸어 저장한다.
    _find(screen, QPushButton, "alertWebhookChange").click()
    url = make_url()
    edit.setText(url)
    _find(screen, QPushButton, "alertWebhookSave").click()
    assert actions.last("save_webhook") == ("save_webhook", AlertCategory.TRADE, url)


def test_not_saved_boxes_show_save_and_a_dim_test_button(make_window, actions):
    window = make_window(actions)
    screen = _webhook_screen(window, WebhookView(AlertCategory.TRADE, saved=False))
    assert _shown(screen, "alertWebhookSave", QPushButton)
    assert not _shown(screen, "alertWebhookChange", QPushButton)
    assert not _shown(screen, "alertWebhookDelete", QPushButton)
    test = _find(screen, QPushButton, "alertWebhookTest")
    assert test.text() == "확인용 글 보내기"
    assert not test.isEnabled()
    test.click()
    assert actions.count("send_test") == 0


def test_delete_the_address_asks_first(make_window, actions):
    window = make_window(actions)
    screen = _webhook_screen(window, WebhookView(AlertCategory.REPORT, saved=True))
    _tab(screen, AlertCategory.REPORT).click()
    window.apply(WebhookView(AlertCategory.REPORT, saved=True))
    cancel = Confirm(False)
    window.set_confirm_handler(cancel)
    _find(screen, QPushButton, "alertWebhookDelete").click()
    assert cancel.asked == [
        (
            WEBHOOK_DELETE_TITLE,
            "보고서 알림의 디스코드 웹후크 주소를 지우면, "
            "이 분류의 알림은 디스코드로 가지 않습니다.",
        )
    ]
    assert actions.count("delete_webhook") == 0
    assert _find(screen, QLineEdit, "alertWebhookInput").placeholderText() == "(가려서 보임) 저장됨"
    window.set_confirm_handler(Confirm(True))
    _find(screen, QPushButton, "alertWebhookDelete").click()
    assert actions.last("delete_webhook") == ("delete_webhook", AlertCategory.REPORT)
    window.apply(WebhookView(AlertCategory.REPORT, saved=False, save_message=DELETE_FAILED))
    assert _text(screen, "alertWebhookMessage") == DELETE_FAILED
    assert _tone(screen, "alertWebhookMessage") == "warning"


def test_the_test_button_calls_send_test_only_when_saved(make_window, actions):
    window = make_window(actions)
    screen = _webhook_screen(window, WebhookView(AlertCategory.OTHER, saved=True))
    _tab(screen, AlertCategory.OTHER).click()
    window.apply(WebhookView(AlertCategory.OTHER, saved=True))
    test = _find(screen, QPushButton, "alertWebhookTest")
    assert test.isEnabled()
    test.click()
    assert actions.calls[-1] == ("send_test", AlertCategory.OTHER)
    window.apply(WebhookView(AlertCategory.OTHER, saved=True, test_message=SENDING))
    assert not test.isEnabled()  # 보내는 중에는 다시 누를 수 없다.
    window.apply(WebhookView(AlertCategory.OTHER, saved=True, test_message=SENT))
    assert test.isEnabled()


def test_the_two_lines_under_the_box_do_not_overwrite_each_other(make_window):
    window = make_window(FakeActions())
    screen = _webhook_screen(window)
    window.apply(
        WebhookView(AlertCategory.TRADE, saved=True, save_message=DELETE_FAILED, test_message=SENT)
    )
    assert _text(screen, "alertWebhookMessage") == DELETE_FAILED
    assert _text(screen, "alertWebhookTestMessage") == SENT
    assert _tone(screen, "alertWebhookTestMessage") == "normal"
    window.apply(WebhookView(AlertCategory.TRADE, saved=True, test_message=TEST_FAILED))
    assert _text(screen, "alertWebhookMessage") is None
    assert _text(screen, "alertWebhookTestMessage") == TEST_FAILED
    assert _tone(screen, "alertWebhookTestMessage") == "warning"
    window.apply(WebhookView(AlertCategory.TRADE, saved=True, test_message=SENDING))
    assert _text(screen, "alertWebhookTestMessage") == SENDING
    # 다른 분류의 글은 그 분류의 탭에만 보인다.
    window.apply(WebhookView(AlertCategory.ERROR, saved=False, test_message=SENT))
    assert _text(screen, "alertWebhookTestMessage") == SENDING
    _tab(screen, AlertCategory.ERROR).click()
    assert _text(screen, "alertWebhookTestMessage") == SENT
    _assert_not_set_info(screen)


def test_not_saved_without_a_message_shows_the_not_set_line(make_window):
    window = make_window(FakeActions())
    screen = _webhook_screen(window, WebhookView(AlertCategory.TRADE, saved=False))
    _assert_not_set_info(screen)
    window.apply(WebhookView(AlertCategory.TRADE, saved=False, save_message=NOT_SET))
    _assert_not_set_info(screen)
    window.apply(WebhookView(AlertCategory.TRADE, saved=True))
    assert _text(screen, "alertWebhookMessage") is None


# --- actions가 None, 보관 기간 글, 글의 규칙 ---


def test_without_actions_no_button_calls_anything_and_asks_nothing(make_window):
    window = make_window(None)
    screen = _open(window, _view(_alert(1, AlertCategory.ERROR, seen=False, detail=_detail())))
    window.apply(WebhookView(AlertCategory.TRADE, saved=True))
    _find(screen, QPushButton, "alertClear").click()
    _find(screen, QPushButton, "alertWebhookDelete").click()
    _find(screen, QPushButton, "alertWebhookTest").click()
    _tab(screen, AlertCategory.ERROR).click()
    _find(screen, QPushButton, "alertDelete_1").click()
    assert _text(screen, "alertLine_1").startswith("● ")  # 값을 받아 그리기는 한다.
    window.apply(WebhookView(AlertCategory.ERROR, saved=False))
    _find(screen, QLineEdit, "alertWebhookInput").setText(make_url())
    _find(screen, QPushButton, "alertWebhookSave").click()


def test_the_retention_notices_are_next_to_the_retention_choice_of_both_screens(make_window):
    window = make_window(FakeActions())
    for screen_id, prefix in (
        (ScreenId.FIRST_SETUP, "firstSetup"),
        (ScreenId.SETTINGS, "settings"),
    ):
        screen = screen_widget(window, screen_id)
        info = _find(screen, InfoIcon, f"{prefix}RetentionInfo")
        assert info.toolTip() == (
            "• 생긴 때부터 고른 기간이 지난 알림은 영구히 지워집니다.\n"
            "• 기간을 줄이면 새 기간이 지난 알림도 지워집니다."
        )
        assert info.accessibleDescription() == info.toolTip()
        # 보관 기간의 제목 바로 뒤에 있다.
        row = _find(screen, QWidget, f"{prefix}Retention").layout().itemAt(0).layout()
        assert [row.itemAt(i).widget().objectName() for i in range(2)] == [
            f"{prefix}RetentionTitle",
            f"{prefix}RetentionInfo",
        ]
        for old in ("RetentionNoticeKeep", "RetentionNoticeShorten"):
            assert screen.findChild(QLabel, f"{prefix}{old}") is None


def test_no_forbidden_text_in_the_alert_center(make_window):
    window = make_window(FakeActions())
    screen = _open(
        window,
        _view(
            _alert(1, AlertCategory.ERROR, coin="KRW-AAA", detail=_detail(), discord_failed=True),
            unseen={AlertCategory.ERROR: 1},
        ),
    )
    window.apply(WebhookView(AlertCategory.TRADE, saved=False))
    center = _find(screen, QWidget, "alertCenter")
    texts = [label.text() for label in center.findChildren(QLabel)]
    for word in ("유의", "아직 없습니다", "봉을 받지 못했습니다", "투자 자문", "비어 있습니다"):
        assert not any(word in text for text in texts), word
    assert "처음 설정을 마치면 여기에 코인별 현황이 보입니다." not in texts


def test_other_updates_and_odd_values_raise_nothing(make_window, actions):
    window = make_window(actions)
    screen = _open(window)
    window.apply(CoinView("KRW-AAA", LoadState.LOADING))
    window.apply(WebhookView(AlertCategory.OTHER, saved=False))  # 고르지 않은 탭의 값
    window.apply(AlertsView((), {}, LoadState.READY))
    window.apply(AlertsView((_alert(1, AlertCategory.ERROR),), {AlertCategory.ERROR: 3}))
    _tab(screen, AlertCategory.ERROR).click()
    assert _rows(screen) == [1]
    assert _tab(screen, AlertCategory.ERROR).text() == "오류 알림 (3)"
    # 새 `AlertsView`가 오기 전 지운 줄의 id로 `mark_seen`을 불러도 된다(조립이 처리한다).
    assert actions.count("save_settings") == 0
