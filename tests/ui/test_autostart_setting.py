"""설정 화면의 "PC를 켜면 자동으로 시작" 칸(M1 명세 3.16.1, 69번).

`SettingsView.autostart_supported`가 참이거나 거짓인 값으로 흐림, 'ⓘ'의 줄, 넘기는 값, 결과 글의
색을 본다. 값은 모두 시험이 만든 값이다.
"""

import pytest
from PySide6.QtWidgets import QApplication, QCheckBox, QLabel, QPushButton, QWidget

from coin_report_trader.core import (
    CandleUnit,
    LoadState,
    Market,
    MarketsView,
    ScreenId,
    SettingsView,
    UiContext,
)
from coin_report_trader.ui import (
    MainWindow,
    create_main_window,
    screen_widget,
    select_screen,
    theme,
)
from coin_report_trader.ui.cards import Card
from coin_report_trader.ui.info_icon import InfoIcon

INFO = (
    "켜 두면 Windows에 로그인할 때 이 프로그램이 저절로 켜집니다. "
    "작업 관리자의 '시작 앱'에서 끈 것은 이 칸이 되살리지 않습니다."
)
UNSUPPORTED = "이 실행 방식에서는 고를 수 없습니다. 내려받은 exe로 켤 때만 고를 수 있습니다."
LABEL = "PC를 켜면 자동으로 시작"
MARKETS = tuple(Market(f"KRW-C{i}", f"가짜{i}", caution=False) for i in range(5))


class Actions:
    """`save_settings`가 받은 인자(키워드까지)를 적는다. `autostart`는 받는 쪽이 받는다."""

    def __init__(self) -> None:
        self.saves: list[tuple[tuple, dict]] = []

    def refresh_markets(self) -> None:
        pass

    def save_settings(self, coins, retention_days, **kwargs) -> None:
        self.saves.append(((coins, retention_days), kwargs))

    def retry_candles(self) -> None:
        pass

    def mark_seen(self, alert_ids) -> None:
        pass

    def delete_alert(self, alert_id) -> None:
        pass

    def clear_alerts(self, category) -> None:
        pass

    def save_webhook(self, category, url) -> None:
        pass

    def delete_webhook(self, category) -> None:
        pass

    def send_test(self, category) -> None:
        pass


@pytest.fixture
def actions() -> Actions:
    return Actions()


@pytest.fixture
def window(qapp: QApplication, actions: Actions):
    made = create_main_window(UiContext("0.0.1", "X", actions))
    made.set_confirm_handler(lambda _title, _body: True)
    made.resize(1100, 1000)
    made.show()  # 놓임을 재려면 크기가 정해져야 한다
    yield made
    made.close()
    made.deleteLater()


def _view(**changes) -> SettingsView:
    values = {
        "coins": ("KRW-C1",),
        "retention_days": 30,
        "first_setup_done": True,
        "candle_unit": CandleUnit.MINUTE_60,
        "candle_count": 200,
    }
    values.update(changes)
    return SettingsView(**values)


def _open(window: MainWindow, view: SettingsView) -> QWidget:
    window.apply(view)
    window.apply(MarketsView(LoadState.READY, MARKETS))
    select_screen(window, ScreenId.SETTINGS)
    QApplication.processEvents()
    return screen_widget(window, ScreenId.SETTINGS)


def _box(screen: QWidget) -> QCheckBox:
    box = screen.findChild(QCheckBox, "settingsAutostart")
    assert box is not None
    return box


def _info(screen: QWidget) -> InfoIcon:
    info = screen.findChild(InfoIcon, "settingsAutostartInfo")
    assert info is not None
    return info


def _message(screen: QWidget) -> QLabel:
    label = screen.findChild(QLabel, "settingsAutostartMessage")
    assert label is not None
    return label


# --- 자리와 이름 ---


def test_the_check_box_the_icon_and_the_message_have_the_spec_names_and_text(window):
    screen = _open(window, _view(autostart_supported=True))
    assert _box(screen).text() == LABEL
    assert isinstance(_box(screen), QCheckBox)
    assert not _box(screen).isChecked()  # 처음 값은 꺼짐
    assert _info(screen).toolTip() == INFO
    assert _message(screen).isHidden()


def test_the_field_sits_in_the_other_values_card_under_the_retention_choice(window):
    screen = _open(window, _view(autostart_supported=True))
    retention = screen.findChild(QWidget, "settingsRetention")
    assert retention is not None
    card = retention.parentWidget()
    assert isinstance(card, Card)
    names = []
    layout = card.layout()
    for index in range(layout.count()):
        widget = layout.itemAt(index).widget()
        if widget is not None:
            names.append(widget.objectName())
    assert names[-2] == "settingsRetention"  # 그 밖의 값 카드의 끝: 알림 보관 기간, 자동 시작
    box = _box(screen)
    assert card.isAncestorOf(box)
    assert box.mapTo(card, box.rect().topLeft()).y() > retention.mapTo(
        card, retention.rect().bottomLeft()
    ).y() - 1
    # 카드는 코인, 예산과 투자 비율, 매수 비율, 손절과 한도, 봉 단위·봉 개수, Claude 모델, 그 밖의
    # 값이다(M3 명세 3.17.5).
    assert len(screen.findChildren(Card)) == 7
    # 'ⓘ'는 확인란 바로 오른쪽이다.
    row = box.parentWidget().layout().itemAt(0).layout()
    assert [row.itemAt(i).widget().objectName() for i in range(2)] == [
        "settingsAutostart",
        "settingsAutostartInfo",
    ]


def test_the_first_setup_screen_has_no_autostart_field(window):
    first = screen_widget(window, ScreenId.FIRST_SETUP)
    for name in ("settingsAutostart", "settingsAutostartInfo", "settingsAutostartMessage"):
        assert first.findChild(QWidget, name) is None


# --- 'ⓘ'의 줄 ---


def test_the_icon_text_is_one_line_when_supported_and_two_bulleted_lines_when_not(window):
    screen = _open(window, _view(autostart_supported=True))
    assert _info(screen).toolTip() == INFO
    assert _info(screen).accessibleDescription() == INFO
    window.apply(_view(autostart_supported=False))
    expected = f"• {INFO}\n• {UNSUPPORTED}"
    assert _info(screen).toolTip() == expected
    assert _info(screen).accessibleDescription() == expected
    assert _info(screen).accessibleName() == f"{LABEL} 설명"
    window.apply(_view(autostart_supported=True))
    assert _info(screen).toolTip() == INFO


# --- 흐림 ---


@pytest.mark.parametrize(
    ("view", "box_enabled"),
    [
        (_view(autostart_supported=True), True),
        (_view(autostart_supported=False), False),  # 이 실행에서 쓸 수 없다
        (_view(autostart_supported=True, first_setup_done=False, coins=()), False),  # 처음 설정 전
    ],
    ids=["supported", "unsupported", "before_first_setup"],
)
def test_the_check_box_is_dim_unless_supported_and_editable_but_the_icon_always_listens(
    window, view, box_enabled
):
    screen = _open(window, view)
    assert _box(screen).isEnabled() is box_enabled
    assert _info(screen).isEnabled()
    assert _info(screen).isVisibleTo(screen)


def test_an_unreadable_settings_view_dims_the_check_box_and_keeps_the_icon(window):
    window.apply(SettingsView(state=LoadState.FAILED))
    screen = screen_widget(window, ScreenId.SETTINGS)
    assert not _box(screen).isEnabled()
    assert _info(screen).isEnabled()


def test_a_dim_unsupported_check_box_still_shows_the_saved_value(window):
    screen = _open(window, _view(autostart=True, autostart_supported=False))
    assert _box(screen).isChecked()
    assert not _box(screen).isEnabled()
    window.apply(_view(autostart=False, autostart_supported=False))
    assert not _box(screen).isChecked()


# --- 저장 ---


def _save(screen: QWidget) -> None:
    button = screen.findChild(QPushButton, "settingsSave")
    assert button is not None
    assert button.isEnabled()
    button.click()


def test_saving_passes_the_check_box_value_when_supported(window, actions):
    screen = _open(window, _view(autostart=False, autostart_supported=True))
    _box(screen).click()
    assert _box(screen).isChecked()
    _save(screen)
    assert [kwargs["autostart"] for _args, kwargs in actions.saves] == [True]
    # 켜진 값을 다시 끄고 저장하면 False를 넘긴다.
    window.apply(_view(autostart=True, autostart_supported=True))
    assert _box(screen).isChecked()
    _box(screen).click()
    _save(screen)
    assert [kwargs["autostart"] for _args, kwargs in actions.saves] == [True, False]


def test_saving_without_touching_the_box_passes_the_saved_value(window, actions):
    screen = _open(window, _view(autostart=True, autostart_supported=True))
    _save(screen)
    assert actions.saves[0][1]["autostart"] is True


def test_saving_does_not_pass_autostart_when_it_is_not_supported(window, actions):
    screen = _open(window, _view(autostart=True, autostart_supported=False))
    _save(screen)
    (_args, kwargs) = actions.saves[0]
    assert "autostart" not in kwargs  # 저장된 값을 그대로 둔다
    assert kwargs["candle_unit"] is CandleUnit.MINUTE_60
    assert kwargs["candle_count"] == 200


def test_a_changed_box_alone_keeps_the_save_button_enabled_as_before(window):
    screen = _open(window, _view(autostart_supported=True))
    button = screen.findChild(QPushButton, "settingsSave")
    assert button is not None
    assert button.isEnabled()
    _box(screen).click()
    assert button.isEnabled()


def test_the_box_value_the_user_chose_is_kept_when_only_the_support_or_error_changes(window):
    screen = _open(window, _view(autostart=False, autostart_supported=True))
    _box(screen).click()
    window.apply(_view(autostart=False, autostart_supported=True, autostart_error="글"))
    assert _box(screen).isChecked()  # 같은 저장된 값이라 고르던 값을 버리지 않는다
    window.apply(_view(autostart=True, autostart_supported=True))
    assert _box(screen).isChecked()
    window.apply(_view(autostart=False, autostart_supported=True))
    assert not _box(screen).isChecked()  # 저장된 값이 바뀌면 따라간다


# --- 결과 글 ---


def test_the_error_text_is_shown_with_the_error_color_and_the_warning_tone(window):
    screen = _open(window, _view(autostart_supported=True))
    assert _message(screen).isHidden()
    window.apply(_view(autostart_supported=True, autostart_error="시작 앱에 등록하지 못했습니다."))
    message = _message(screen)
    assert not message.isHidden()
    assert message.text() == "시작 앱에 등록하지 못했습니다."
    assert message.property("tone") == "warning"
    assert theme.ERROR in message.styleSheet()
    window.apply(_view(autostart_supported=True, autostart_error="시작 앱에서 지우지 못했습니다."))
    assert message.text() == "시작 앱에서 지우지 못했습니다."
    window.apply(_view(autostart_supported=True, autostart_error=None))
    assert message.isHidden()
    assert message.text() == ""


def test_the_message_is_under_the_check_box_and_is_not_an_icon_text(window):
    error = "시작 앱에 등록하지 못했습니다."
    screen = _open(window, _view(autostart_supported=True, autostart_error=error))
    box, message = _box(screen), _message(screen)
    assert message.parentWidget() is box.parentWidget()
    assert message.mapTo(screen, message.rect().topLeft()).y() >= box.mapTo(
        screen, box.rect().bottomLeft()
    ).y()
    assert "시작 앱에 등록하지 못했습니다." not in _info(screen).toolTip()
