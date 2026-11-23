"""5단계의 설정 화면(업비트 키, 실거래 설정, 모드), 확인 창, 실거래 꼴의 시험(M5 명세 3.11).

값은 모두 만든 값이다. 키는 만든 꼴의 가짜 글이고 실제 키가 아니다.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QWidget,
)

from coin_report_trader.core import (
    AlertCategory,
    BelowStopView,
    HistoryRow,
    HistoryTab,
    HistoryView,
    LiveBlock,
    LiveConfirmView,
    LiveSettings,
    LiveStatusView,
    LoadState,
    Market,
    MarketsView,
    Mode,
    PerformanceRow,
    PerformanceView,
    SaveResult,
    ScreenId,
    SettingsView,
    TodayView,
    TradeStatusView,
    UiContext,
    UpbitKeyView,
)
from coin_report_trader.core.views import AlertView
from coin_report_trader.ui import create_main_window, screen_widget, select_screen, theme
from coin_report_trader.ui.dialogs import build_live_confirm_box, live_confirm_texts
from coin_report_trader.ui.history_screen import HistoryScreen
from coin_report_trader.ui.live_cards import UpbitKeyDialog, key_shape_ok

COINS = ("KRW-AAA", "KRW-BBB")
NOW = datetime(2026, 1, 2, 1, 30, tzinfo=UTC)
TODAY = date(2026, 1, 2)
ACCESS = "A" * 24  # 만든 꼴의 가짜 글
SECRET = "S" * 24


class Actions:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.saves: list[dict] = []

    def refresh_markets(self) -> None: ...

    def save_settings(self, coins, retention_days, **kwargs) -> None:
        self.saves.append({"coins": coins, "retention_days": retention_days, **kwargs})

    def load_reports(self, market, mode=None) -> None:
        self.calls.append(("load_reports", market, mode))

    def load_report(self, report_id) -> None: ...

    def load_history(self, tab, market, day, mode=None) -> None:
        self.calls.append(("load_history", tab, market, day, mode))

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

    def save_upbit_key(self, access, secret, created) -> None:
        self.calls.append(("save_upbit_key", access, secret, created))

    def delete_upbit_key(self) -> None:
        self.calls.append(("delete_upbit_key",))

    def prepare_live_on(self) -> None:
        self.calls.append(("prepare_live_on",))

    def turn_live_on(self) -> None:
        self.calls.append(("turn_live_on",))

    def turn_live_off(self) -> None:
        self.calls.append(("turn_live_off",))

    def account_visible(self, visible) -> None: ...

    def refresh_account(self) -> None: ...

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]


class Confirm:
    def __init__(self, answer: bool = True) -> None:
        self.answer = answer
        self.asked: list[tuple[str, str]] = []

    def __call__(self, title: str, body: str) -> bool:
        self.asked.append((title, body))
        return self.answer


@pytest.fixture
def actions() -> Actions:
    return Actions()


@pytest.fixture
def window(qapp: QApplication, actions: Actions):
    made = create_main_window(UiContext("0.0.1", "X", actions))
    made.resize(1280, 1400)
    made.show()
    yield made
    made.close()
    made.deleteLater()


def _find(parent: QWidget, kind: type, name: str):
    found = parent.findChild(kind, name)
    assert found is not None, name
    return found


def _settings(window) -> QWidget:
    return screen_widget(window, ScreenId.SETTINGS)


def _done(window, coins=COINS, live: LiveSettings | None = None) -> None:
    window.apply(SettingsView(coins, 30, True, live=live))
    select_screen(window, ScreenId.SETTINGS)


def _status(**changes) -> TradeStatusView:
    values = {
        "user_paused": True,
        "error_paused": False,
        "error_cause": None,
        "error_detail": None,
        "stop_loss_off": False,
        "starting_step": None,
        "next_round_utc": NOW + timedelta(hours=1),
        "as_of": NOW,
    }
    values.update(changes)
    return TradeStatusView(**values)


def _live_status(mode: Mode = Mode.PAPER, blocks=(), counts=(), sell_all=True) -> LiveStatusView:
    return LiveStatusView(mode, True, tuple(blocks), tuple(counts), sell_all, NOW)


def _key(present=True, created=TODAY, can_edit=True, message=None) -> UpbitKeyView:
    expires = created + timedelta(days=365) if created is not None else None
    return UpbitKeyView(present, created, expires, can_edit, message)


def _text(window, name: str) -> str:
    return _find(_settings(window), QLabel, name).text()


def _button(window, name: str) -> QPushButton:
    return _find(_settings(window), QPushButton, name)


# --- 묶음과 'ⓘ' ---


def test_the_three_groups_and_their_info_icons_exist(window):
    _done(window)
    settings = _settings(window)
    for name in ("settingsUpbitKey", "settingsLive", "settingsMode"):
        assert _find(settings, QWidget, name) is not None
    tips = {
        name: _find(settings, QWidget, name).toolTip()
        for name in ("settingsUpbitKeyInfo", "settingsLiveInfo", "settingsModeInfo")
    }
    key = tips["settingsUpbitKeyInfo"]
    for line in (
        "업비트 키: 자산조회, 주문조회, 주문하기 권한만. 출금 권한은 주지 않습니다.",
        "허용 IP 등록과 고정 IP인지 확인",
        "서브포켓을 쓸지 결정",
        "Claude Code 업데이트와 다시 로그인",
        "약관과 정책 확인",
        "키는 Windows 자격 증명 관리자에만 둡니다. 화면과 파일에는 남지 않습니다.",
        "출금 권한을 주지 마세요.",
        "키는 1년 동안 쓸 수 있고 연장할 수 없습니다.",
    ):
        assert line in key
    live = tips["settingsLiveInfo"]
    assert "실거래에서만 쓰는 값입니다. 모의매매의 예산, 투자 비율, 하루 손실 한도와 따로" in live
    assert "실거래를 켜기 전에 넣어야 합니다. 실거래 중에는 고칠 수 없습니다." in live
    assert "실제 계정의 원화 잔고와 고른 코인 보유의 평가 금액의 합보다 클 수 없습니다." in live
    mode = tips["settingsModeInfo"]
    for line in (
        "모드는 멈춤 상태에서만 바꿀 수 있습니다.",
        "실거래에서도 손절, 하루 손실 한도, 가격 안전 검사가 그대로 걸립니다.",
        "실거래에서는 주문 전에 확인하는 단계 없이 판단대로 주문합니다.",
        "이 프로그램은 투자 자문이 아닙니다. AI 판단은 틀릴 수 있습니다.",
    ):
        assert line in mode


# --- 업비트 키 ---


def test_the_key_state_dates_and_expiry_colors(window):
    _done(window)
    assert _text(window, "settingsUpbitKeyState") == "넣지 않음"
    window.apply(_key(present=True, created=TODAY))
    assert _text(window, "settingsUpbitKeyState") == "넣음"
    assert _text(window, "settingsUpbitKeyCreated") == "만든 날: 2026-01-02"
    assert _text(window, "settingsUpbitKeyExpires") == "만료일: 2027-01-02"
    expires = _find(_settings(window), QLabel, "settingsUpbitKeyExpires")
    assert expires.styleSheet() == ""  # 30일을 넘게 남음
    today = datetime.now(UTC).astimezone().date()
    window.apply(_key(created=today - timedelta(days=340)))  # 만료까지 25일
    assert theme.CAUTION.lower() in expires.styleSheet().lower()
    window.apply(_key(created=today - timedelta(days=370)))  # 지났다
    assert theme.ERROR.lower() in expires.styleSheet().lower()


def test_the_key_card_never_shows_a_key_value(window):
    _done(window)
    window.apply(_key())
    for label in _settings(window).findChildren(QLabel):
        assert ACCESS not in label.text()
        assert SECRET not in label.text()


def test_the_key_buttons_follow_the_pause_and_the_key(window):
    _done(window)
    window.apply(_key(present=False, can_edit=True))
    assert _button(window, "settingsUpbitKeySet").isEnabled()
    assert not _button(window, "settingsUpbitKeyDelete").isEnabled()  # 지울 키가 없다
    window.apply(_key(present=True, can_edit=True))
    assert _button(window, "settingsUpbitKeyDelete").isEnabled()
    assert _find(_settings(window), QLabel, "settingsUpbitKeyLockNote").isHidden()
    window.apply(_key(present=True, can_edit=False))
    assert not _button(window, "settingsUpbitKeySet").isEnabled()
    assert not _button(window, "settingsUpbitKeyDelete").isEnabled()
    note = _find(_settings(window), QLabel, "settingsUpbitKeyLockNote")
    assert note.text() == "멈춤 상태에서만 키를 고칠 수 있습니다."
    assert not note.isHidden()


def test_the_delete_button_has_the_danger_look(window):
    _done(window)
    sheet = _button(window, "settingsUpbitKeyDelete").styleSheet()
    assert f"border: {theme.DANGER_BORDER}px solid {theme.ERROR}" in sheet
    assert "font-weight: bold" in sheet


def test_the_key_buttons_are_dim_before_the_first_setup(window):
    window.apply(SettingsView((), 30, False))
    select_screen(window, ScreenId.SETTINGS)
    window.apply(_key())
    assert not _button(window, "settingsUpbitKeySet").isEnabled()
    assert not _button(window, "settingsUpbitKeyDelete").isEnabled()


def test_the_key_message_shows_under_the_card(window):
    _done(window)
    message = _find(_settings(window), QLabel, "settingsUpbitKeyMessage")
    assert message.isHidden()
    window.apply(_key(message="키를 지우지 못했습니다."))
    assert message.text() == "키를 지우지 못했습니다."
    assert message.property("tone") == "warning"
    window.apply(_key(message=None))
    assert message.isHidden()
    window.apply(SaveResult("upbit_key", ok=False, message="저장하지 못했습니다."))
    assert message.text() == "저장하지 못했습니다."


# --- 키 넣기 창 ---


def _open_dialog(window) -> UpbitKeyDialog:
    _done(window)
    window.apply(_key(present=False))
    _button(window, "settingsUpbitKeySet").click()
    dialog = window.findChild(UpbitKeyDialog, "upbitKeyDialog")
    assert dialog is not None
    return dialog


def test_the_key_dialog_is_a_modal_with_hidden_fields_and_today(window, actions):
    dialog = _open_dialog(window)
    assert dialog.isModal()
    assert dialog.parent() is window
    for name in ("upbitKeyAccess", "upbitKeySecret"):
        edit = _find(dialog, QLineEdit, name)
        assert edit.echoMode() == QLineEdit.EchoMode.Password  # 복사해 갈 수 없다
    created = _find(dialog, QDateEdit, "upbitKeyCreated")
    today = datetime.now(UTC).astimezone().date()
    shown = created.date().toPython()
    assert abs((shown - today).days) <= 1  # 한국 날짜와 이 PC의 날짜가 하루 어긋날 수 있다
    assert _find(dialog, QPushButton, "upbitKeySave").text() == "저장"
    cancel = _find(dialog, QPushButton, "upbitKeyCancel")
    assert cancel.text() == "취소"
    assert cancel.isDefault()
    assert actions.names() == []


@pytest.mark.parametrize(
    "text",
    ["", "A" * 19, "A" * 101, "A" * 10 + " " + "A" * 10, "가" * 24, "A" * 10 + "\t" + "A" * 10],
)
def test_a_badly_shaped_key_is_refused_under_its_field(window, actions, text):
    dialog = _open_dialog(window)
    _find(dialog, QLineEdit, "upbitKeyAccess").setText(text)
    _find(dialog, QLineEdit, "upbitKeySecret").setText(SECRET)
    _find(dialog, QPushButton, "upbitKeySave").click()
    error = _find(dialog, QLabel, "upbitKeyAccessError")
    assert error.text() == "키의 꼴이 맞지 않습니다."
    assert not error.isHidden()
    assert _find(dialog, QLabel, "upbitKeySecretError").isHidden()
    assert dialog.isVisible()
    assert actions.names() == []


def test_the_key_shape_limits():
    assert key_shape_ok("A" * 20)
    assert key_shape_ok("A" * 100)
    assert not key_shape_ok("A" * 19)
    assert not key_shape_ok("A" * 101)
    assert key_shape_ok("aB3-_=" * 4)


def test_a_future_day_is_refused_under_the_date(window, actions):
    dialog = _open_dialog(window)
    _find(dialog, QLineEdit, "upbitKeyAccess").setText(ACCESS)
    _find(dialog, QLineEdit, "upbitKeySecret").setText(SECRET)
    future = datetime.now(UTC).astimezone().date() + timedelta(days=3)
    picker = _find(dialog, QDateEdit, "upbitKeyCreated")
    picker.setDate(QDate(future.year, future.month, future.day))
    _find(dialog, QPushButton, "upbitKeySave").click()
    error = _find(dialog, QLabel, "upbitKeyCreatedError")
    assert error.text() == "만든 날이 오늘보다 뒤입니다."
    assert dialog.isVisible()
    assert actions.names() == []


def test_a_good_key_is_saved_once_and_the_fields_are_cleared(window, actions):
    dialog = _open_dialog(window)
    access = _find(dialog, QLineEdit, "upbitKeyAccess")
    secret = _find(dialog, QLineEdit, "upbitKeySecret")
    access.setText(ACCESS)
    secret.setText(SECRET)
    created = date(2025, 12, 1)
    _find(dialog, QDateEdit, "upbitKeyCreated").setDate(QDate(2025, 12, 1))
    _find(dialog, QPushButton, "upbitKeySave").click()
    assert actions.calls == [("save_upbit_key", ACCESS, SECRET, created)]
    assert not dialog.isVisible()
    assert access.text() == ""  # 키의 값을 들고 있지 않는다
    assert secret.text() == ""


def test_cancelling_the_dialog_saves_nothing_and_clears_the_fields(window, actions):
    dialog = _open_dialog(window)
    access = _find(dialog, QLineEdit, "upbitKeyAccess")
    access.setText(ACCESS)
    _find(dialog, QPushButton, "upbitKeyCancel").click()
    assert actions.names() == []
    assert not dialog.isVisible()
    assert access.text() == ""


def test_the_key_error_goes_when_the_field_is_edited(window):
    dialog = _open_dialog(window)
    _find(dialog, QPushButton, "upbitKeySave").click()
    error = _find(dialog, QLabel, "upbitKeyAccessError")
    assert not error.isHidden()
    _find(dialog, QLineEdit, "upbitKeyAccess").setText("A")
    assert error.isHidden()


def test_the_key_dialog_opens_only_when_the_buttons_can_work(window, actions):
    _done(window)
    window.apply(_key(present=False, can_edit=False))
    _button(window, "settingsUpbitKeySet").click()  # 흐린 단추는 눌리지 않는다
    assert window.findChild(UpbitKeyDialog) is None


# --- 키 지우기 ---


def test_deleting_the_key_asks_first(window, actions):
    _done(window)
    window.apply(_key())
    confirm = Confirm(False)
    window.set_confirm_handler(confirm)
    _button(window, "settingsUpbitKeyDelete").click()
    assert confirm.asked == [
        (
            "업비트 키를 지울까요?",
            "Windows 자격 증명 관리자에서 키를 지웁니다. 다시 쓰려면 키를 새로 넣어야 합니다.",
        )
    ]
    assert actions.names() == []
    confirm.answer = True
    _button(window, "settingsUpbitKeyDelete").click()
    assert actions.calls == [("delete_upbit_key",)]


# --- 실거래 설정 ---


def _live_field(window, name: str) -> QLineEdit:
    return _find(_settings(window), QLineEdit, name)


def test_the_live_settings_fields_and_their_names(window):
    _done(window)
    settings = _settings(window)
    for name in (
        "settingsLiveBudget",
        "settingsLiveCoinRatio_KRW-AAA",
        "settingsLiveCoinRatio_KRW-BBB",
        "settingsLiveDailyLoss",
    ):
        assert _find(settings, QLineEdit, name).text() == ""
    labels = {
        "settingsLiveBudgetLabel": "실거래 예산(원)",
        "settingsLiveCoinRatio_KRW-AAALabel": "AAA 실거래 투자 비율(%)",
        "settingsLiveDailyLossLabel": "실거래 하루 손실 한도(원)",
    }
    for name, text in labels.items():
        assert _find(settings, QLabel, name).text() == text  # 별표가 없다
    assert _text(window, "settingsLiveCoinBudget_KRW-AAA") == "코인 예산 -"
    assert _text(window, "settingsLiveCoinRatioSum") == "합계 0.00%"


def test_the_live_settings_compute_coin_budgets_and_the_sum(window):
    _done(window)
    _live_field(window, "settingsLiveBudget").setText("100000")
    _live_field(window, "settingsLiveCoinRatio_KRW-AAA").setText("60")
    _live_field(window, "settingsLiveCoinRatio_KRW-BBB").setText("40")
    assert _text(window, "settingsLiveCoinBudget_KRW-AAA") == "코인 예산 60,000원"
    assert _text(window, "settingsLiveCoinBudget_KRW-BBB") == "코인 예산 40,000원"
    assert _text(window, "settingsLiveCoinRatioSum") == "합계 100.00%"


def test_the_saved_live_settings_fill_the_fields(window):
    live = LiveSettings(
        Decimal(100000), {"KRW-AAA": Decimal(60), "KRW-BBB": Decimal(40)}, Decimal(3000)
    )
    _done(window, live=live)
    assert _live_field(window, "settingsLiveBudget").text() == "100,000"
    assert _live_field(window, "settingsLiveCoinRatio_KRW-AAA").text() == "60"
    assert _live_field(window, "settingsLiveDailyLoss").text() == "3,000"


def test_saving_passes_the_live_settings_and_empty_fields_pass_none(window, actions):
    _done(window)
    _button(window, "settingsSave").click()
    assert "live" not in actions.saves[-1]  # 모두 비었으면 저장된 값을 그대로 둔다
    _live_field(window, "settingsLiveBudget").setText("100,000")
    _live_field(window, "settingsLiveCoinRatio_KRW-AAA").setText("60.5")
    _live_field(window, "settingsLiveDailyLoss").setText("3000")
    _button(window, "settingsSave").click()
    live = actions.saves[-1]["live"]
    assert live == LiveSettings(Decimal(100000), {"KRW-AAA": Decimal("60.5")}, Decimal(3000))


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("settingsLiveBudget", "1.5"),
        ("settingsLiveBudget", "abc"),
        ("settingsLiveDailyLoss", "10.5"),
        ("settingsLiveCoinRatio_KRW-AAA", "33.333"),
        ("settingsLiveCoinRatio_KRW-AAA", "x"),
    ],
)
def test_a_badly_shaped_live_value_is_refused_under_its_field(window, actions, name, text):
    _done(window)
    _live_field(window, name).setText(text)
    _button(window, "settingsSave").click()
    assert actions.saves == []
    assert not _find(_settings(window), QLabel, f"{name}Error").isHidden()


def test_the_checks_of_the_data_thread_show_under_the_live_fields(window):
    _done(window)
    window.apply(
        SaveResult(
            "settings",
            ok=False,
            field_errors={
                "live_budget": "0보다 큰 값을 넣으세요.",
                "live_coin_ratio:KRW-AAA": "하한 글",
                "live_coin_ratios": "코인별 투자 비율의 합이 100%여야 합니다(지금 60%).",
                "live_daily_loss_limit": "0보다 큰 값을 넣으세요.",
            },
        )
    )
    assert _text(window, "settingsLiveBudgetError") == "0보다 큰 값을 넣으세요."
    assert _text(window, "settingsLiveCoinRatio_KRW-AAAError") == "하한 글"
    assert _text(window, "settingsLiveCoinRatioSumError").startswith("코인별 투자 비율의 합이")
    assert _text(window, "settingsLiveDailyLossError") == "0보다 큰 값을 넣으세요."
    window.apply(SaveResult("settings", ok=True))
    assert _find(_settings(window), QLabel, "settingsLiveBudgetError").isHidden()


def test_live_settings_are_not_in_the_first_setup(window):
    first = screen_widget(window, ScreenId.FIRST_SETUP)
    assert first.findChild(QWidget, "settingsLive") is None
    assert first.findChild(QLineEdit, "settingsLiveBudget") is None


# --- 모드 ---


def test_the_mode_card_in_paper(window):
    _done(window)
    window.apply(_status())
    window.apply(
        _live_status(
            blocks=("업비트 키가 없습니다", "멈춤 상태가 아닙니다"),
            counts=(("KRW-AAA", 29, 30), ("KRW-BBB", 30, 30)),
            sell_all=False,
        )
    )
    assert _text(window, "settingsModeState") == "지금 모드: 모의매매"
    assert not _button(window, "settingsLiveOn").isHidden()
    assert _button(window, "settingsLiveOff").isHidden()
    assert not _button(window, "settingsLiveOn").isEnabled()
    assert _text(window, "settingsLiveBlocks") == "업비트 키가 없습니다\n멈춤 상태가 아닙니다"
    assert _text(window, "settingsLiveCounts") == "AAA 29/30회\nBBB 30/30회"
    assert (
        _text(window, "settingsLiveSellAll") == "모의매매에서 '멈추고 전량 매도': 하지 않음"
    )


def test_the_live_on_button_works_only_without_blocks(window):
    _done(window)
    window.apply(_status())
    window.apply(_live_status(blocks=()))
    button = _button(window, "settingsLiveOn")
    assert button.isEnabled()
    assert _find(_settings(window), QLabel, "settingsLiveBlocks").isHidden()
    assert _text(window, "settingsLiveSellAll") == "모의매매에서 '멈추고 전량 매도': 했음"
    window.apply(_live_status(blocks=(LiveBlock.NO_KEY.value,)))
    assert not button.isEnabled()


def test_the_blocks_show_as_the_backend_wrote_them(window):
    _done(window)
    blocks = (
        "이 판에서는 실거래를 켤 수 없습니다",
        "KRW-AAA의 모의 회차가 모자랍니다(29/30)",
        "실거래 설정(예산, 코인별 투자 비율, 하루 손실 한도)을 넣어야 합니다",
    )
    window.apply(_live_status(blocks=blocks))
    assert _text(window, "settingsLiveBlocks").splitlines() == list(blocks)


def test_the_n_choice_lists_the_four_values_with_their_tips(window):
    _done(window)
    window.apply(_live_status(counts=(("KRW-AAA", 3, 100),)))
    combo = _find(_settings(window), QComboBox, "settingsLiveRoundsN")
    assert [combo.itemData(i) for i in range(combo.count())] == [10, 30, 100, 200]
    assert combo.currentData() == 100
    tips = [combo.itemData(i, Qt.ItemDataRole.ToolTipRole) for i in range(combo.count())]
    assert tips == [
        "오름·내림 적중률의 95% 구간: 약 ±31%p",
        "오름·내림 적중률의 95% 구간: 약 ±18%p",
        "오름·내림 적중률의 95% 구간: 약 ±10%p",
        "오름·내림 적중률의 95% 구간: 약 ±7%p",
    ]
    window.apply(_live_status(counts=()))
    assert combo.currentData() == 30  # 처음 값


def test_the_n_choice_is_not_usable_until_the_backend_can_save_it(window):
    _done(window)
    window.apply(_live_status())
    assert not _find(_settings(window), QComboBox, "settingsLiveRoundsN").isEnabled()


def test_the_live_off_button_works_only_while_paused(window):
    _done(window)
    window.apply(_status(mode=Mode.LIVE, user_paused=True))
    window.apply(_live_status(Mode.LIVE))
    assert _text(window, "settingsModeState") == "지금 모드: 실거래"
    assert _button(window, "settingsLiveOn").isHidden()
    off = _button(window, "settingsLiveOff")
    assert not off.isHidden()
    assert off.isEnabled()
    window.apply(_status(mode=Mode.LIVE, user_paused=False))
    assert not off.isEnabled()
    window.apply(_status(mode=Mode.LIVE, user_paused=False, error_paused=True))
    assert off.isEnabled()  # 오류 멈춤에서도 끌 수 있다(3.7.3)


# --- 실거래 켜기와 끄기의 차례 ---


def _ready_to_turn_on(window) -> None:
    _done(window)
    window.apply(_status())
    window.apply(_live_status(blocks=()))


def _confirm_view(**changes) -> LiveConfirmView:
    values = {
        "budget": Decimal(100000),
        "coins": (
            ("KRW-AAA", Decimal(60), Decimal(60000)),
            ("KRW-BBB", Decimal(40), Decimal(40000)),
        ),
        "krw_available": Decimal(200000),
        "holdings": (("KRW-BBB", Decimal(10), Decimal(10000)),),
        "total": Decimal(210000),
        "as_of": NOW,
    }
    values.update(changes)
    return LiveConfirmView(**values)


def test_the_live_on_button_prepares_and_the_confirm_decides(window, actions):
    _ready_to_turn_on(window)
    confirm = Confirm(False)
    window.set_confirm_handler(confirm)
    _button(window, "settingsLiveOn").click()
    assert actions.calls == [("prepare_live_on",)]
    window.apply(_confirm_view())
    assert confirm.asked == [
        (
            "실거래를 켤까요?",
            "업비트 계정으로 실제 주문을 냅니다. 멈춤을 풀면 다음 회차부터 판단대로 사람의 확인 "
            "없이 주문합니다. 손절과 한도는 그대로 걸립니다. 이 프로그램은 투자 자문이 아니고 AI "
            "판단은 틀릴 수 있습니다.",
        )
    ]
    assert actions.names() == ["prepare_live_on"]  # 취소면 그대로 모의매매다


def test_confirming_turns_it_on_once(window, actions):
    _ready_to_turn_on(window)
    window.set_confirm_handler(Confirm(True))
    _button(window, "settingsLiveOn").click()
    window.apply(_confirm_view())
    assert actions.names() == ["prepare_live_on", "turn_live_on"]


def test_a_confirm_view_nobody_asked_for_opens_nothing(window, actions):
    _ready_to_turn_on(window)
    confirm = Confirm(True)
    window.set_confirm_handler(confirm)
    window.apply(_confirm_view())
    assert confirm.asked == []
    assert actions.names() == []


def test_a_blocked_answer_shows_no_confirm_and_a_later_view_is_ignored(window, actions):
    _ready_to_turn_on(window)
    confirm = Confirm(True)
    window.set_confirm_handler(confirm)
    _button(window, "settingsLiveOn").click()
    window.apply(_live_status(blocks=(LiveBlock.NO_SELL_ALL.value,)))
    assert "'멈추고 전량 매도'를 한 번 해야 합니다" in _text(window, "settingsLiveBlocks")
    window.apply(_confirm_view())
    assert confirm.asked == []


def test_the_result_of_turning_on_shows_under_the_mode(window):
    _ready_to_turn_on(window)
    message = _find(_settings(window), QLabel, "settingsModeMessage")
    assert message.isHidden()
    text = "업비트에 연결하지 못했습니다(IP 미등록). 키, 허용 IP, 권한을 확인하세요."
    window.apply(SaveResult("live_on", ok=False, message=text))
    assert message.text() == text
    assert message.property("tone") == "warning"
    window.apply(SaveResult("live_on", ok=True, message=None))
    assert message.isHidden()


def test_pressing_live_on_again_clears_the_old_message(window, actions):
    _ready_to_turn_on(window)
    window.apply(SaveResult("live_on", ok=False, message="안 됨"))
    _button(window, "settingsLiveOn").click()
    assert _find(_settings(window), QLabel, "settingsModeMessage").isHidden()


def test_a_below_stop_holding_while_turning_on_asks_and_cancel_keeps_paper(window, actions):
    _ready_to_turn_on(window)
    confirm = Confirm(True)
    window.set_confirm_handler(confirm)
    _button(window, "settingsLiveOn").click()
    window.apply(_confirm_view())
    assert actions.names() == ["prepare_live_on", "turn_live_on"]
    view = BelowStopView("KRW-AAA", Decimal(1000), Decimal(800), Decimal(950))
    confirm.asked.clear()
    confirm.answer = False
    window.apply(view)
    assert confirm.asked[0][0] == "손절선 아래인 보유가 있습니다"
    assert actions.names() == ["prepare_live_on", "turn_live_on"]  # 취소하면 켜지 않는다
    window.apply(view)  # 켜는 중이 아니므로 다른 길(설정 저장)이고 저장한 적이 없어 아무 일도 없다
    assert actions.names() == ["prepare_live_on", "turn_live_on"]


def test_confirming_a_below_stop_holding_turns_on_again(window, actions):
    _ready_to_turn_on(window)
    window.set_confirm_handler(Confirm(True))
    _button(window, "settingsLiveOn").click()
    window.apply(_confirm_view())
    window.apply(BelowStopView("KRW-AAA", Decimal(1000), Decimal(800), Decimal(950)))
    assert actions.names() == ["prepare_live_on", "turn_live_on", "turn_live_on"]


def test_turning_off_asks_first(window, actions):
    _done(window)
    window.apply(_status(mode=Mode.LIVE))
    window.apply(_live_status(Mode.LIVE))
    confirm = Confirm(False)
    window.set_confirm_handler(confirm)
    _button(window, "settingsLiveOff").click()
    assert confirm.asked == [
        (
            "모의매매로 바꿀까요?",
            "프로그램이 건 실거래 주문을 취소합니다. 남은 실제 보유에는 손절이 없어집니다.",
        )
    ]
    assert actions.names() == []
    confirm.answer = True
    _button(window, "settingsLiveOff").click()
    assert actions.names() == ["turn_live_off"]


# --- SCR-06-9 ---


def test_the_live_confirm_table_shows_the_values(qapp):
    view = _confirm_view(holdings=(("KRW-BBB", Decimal(10), Decimal(10000)),))
    assert live_confirm_texts(view) == (
        "실거래 예산 100,000원",
        "AAA: 60% · 60,000원",
        "BBB: 40% · 40,000원",
        "읽은 원화 잔고(주문 가능) 200,000원",
        "AAA 보유: 보유 없음",
        "BBB 보유: 10 · 평가 10,000원",
        "원화 잔고와 보유 평가의 합 210,000원",
    )


def test_the_live_confirm_table_floors_money_and_handles_a_missing_price(qapp):
    view = _confirm_view(
        budget=Decimal("100000.9"),
        krw_available=Decimal("200000.99"),
        holdings=(("KRW-AAA", Decimal("1.5"), None), ("KRW-BBB", Decimal(0), Decimal(0))),
        total=Decimal("1234567.8"),
    )
    lines = live_confirm_texts(view)
    assert lines[0] == "실거래 예산 100,000원"
    assert lines[3] == "읽은 원화 잔고(주문 가능) 200,000원"
    assert lines[4] == "AAA 보유: 1.5 · 평가 -원"
    assert lines[5] == "BBB 보유: 보유 없음"  # 수량 0은 보유가 없다
    assert lines[6] == "원화 잔고와 보유 평가의 합 1,234,567원"


def test_the_live_confirm_box_has_the_table_the_read_time_and_two_buttons(qapp):
    box = build_live_confirm_box(None, _confirm_view(), now=NOW)
    try:
        assert box.windowTitle() == "실거래를 켤까요?"
        table = _find(box, QWidget, "liveConfirmTable")
        lines = [label.text() for label in table.findChildren(QLabel)]
        assert lines[0] == "실거래 예산 100,000원"
        assert len(lines) == 7
        read_at = _find(box, QLabel, "liveConfirmReadAt")
        assert read_at.text() == "10:30에 읽은 값"
        ok = _find(box, QPushButton, "confirmOk")
        cancel = _find(box, QPushButton, "confirmCancel")
        assert ok.text() == "실거래 켜기"
        assert cancel.text() == "취소"
        assert cancel.isDefault()
        assert not ok.isDefault()
    finally:
        box.deleteLater()


def test_the_live_confirm_box_buttons_decide(qapp):
    box = build_live_confirm_box(None, _confirm_view(as_of=None), now=NOW)
    assert box.findChild(QLabel, "liveConfirmReadAt") is None
    _find(box, QPushButton, "confirmOk").click()
    assert box.result() == QDialog.DialogCode.Accepted
    other = build_live_confirm_box(None, _confirm_view(), now=NOW)
    _find(other, QPushButton, "confirmCancel").click()
    assert other.result() == QDialog.DialogCode.Rejected
    box.deleteLater()
    other.deleteLater()


# --- 실거래 중에 막는 것(3.7.4) ---


def _go_live(window) -> None:
    window.apply(_status(mode=Mode.LIVE))
    window.apply(_live_status(Mode.LIVE))


def test_in_live_new_coins_cannot_be_added_but_picked_ones_can_be_removed(window):
    _done(window)
    window.apply(_live_status())
    markets = tuple(Market(f"KRW-{c}", c, False) for c in ("AAA", "BBB", "CCC"))
    window.apply(MarketsView(LoadState.READY, markets))
    note = _find(_settings(window), QLabel, "settingsCoinsLiveNote")
    assert note.isHidden()
    new = _find(_settings(window), QCheckBox, "settingsCoin_KRW-CCC")
    assert new.isEnabled()
    _go_live(window)
    assert not note.isHidden()
    assert note.text() == (
        "실거래 중에는 새 코인을 고를 수 없습니다. 모의매매로 돌아가 모의 회차를 채우세요."
    )
    assert not new.isEnabled()
    assert _find(_settings(window), QCheckBox, "settingsCoin_KRW-AAA").isEnabled()  # 빼기는 된다
    window.apply(_status(mode=Mode.PAPER))
    window.apply(_live_status(Mode.PAPER))
    assert new.isEnabled()
    assert note.isHidden()


def test_in_live_the_method_the_candle_and_the_live_fields_are_dim_with_notes(window):
    _done(window)
    window.apply(_live_status())
    settings = _settings(window)
    candle = [b for b in settings.findChildren(QRadioButton) if "settingsUnit_" in b.objectName()]
    count = [b for b in settings.findChildren(QRadioButton) if "settingsCount_" in b.objectName()]
    assert all(b.isEnabled() for b in candle + count)
    assert _find(settings, QLabel, "settingsMethodLockNote").isHidden()
    _go_live(window)
    assert not any(b.isEnabled() for b in candle + count)
    assert not _find(settings, QWidget, "settingsLiveFields").isEnabled()
    for name in ("settingsMethodLockNote", "settingsCandleLockNote"):
        note = _find(settings, QLabel, name)
        assert note.text() == "모의매매에서만 바꿀 수 있습니다."
        assert not note.isHidden()
    live_note = _find(settings, QLabel, "settingsLiveLockNote")
    assert live_note.text() == "실거래를 끈 뒤에 고칠 수 있습니다."
    assert not live_note.isHidden()
    method = [b for b in settings.findChildren(QRadioButton) if "settingsMethod_" in b.objectName()]
    assert len(method) == 2
    assert not any(b.isEnabled() for b in method)
    window.apply(_status(mode=Mode.PAPER))
    window.apply(_live_status(Mode.PAPER))
    assert all(b.isEnabled() for b in candle + count + method)
    assert _find(settings, QWidget, "settingsLiveFields").isEnabled()
    assert live_note.isHidden()


def test_in_live_the_live_settings_are_not_saved(window, actions):
    _done(window)
    _live_field(window, "settingsLiveBudget").setText("100000")
    _go_live(window)
    _button(window, "settingsSave").click()
    assert "live" not in actions.saves[-1]


def test_in_live_the_key_buttons_are_dim_even_when_paused(window):
    _done(window)
    window.apply(_key(can_edit=True))
    assert _button(window, "settingsUpbitKeySet").isEnabled()
    _go_live(window)
    assert not _button(window, "settingsUpbitKeySet").isEnabled()
    assert not _button(window, "settingsUpbitKeyDelete").isEnabled()
    assert not _find(_settings(window), QLabel, "settingsUpbitKeyLockNote").isHidden()


# --- 기록과 보고서의 모드 고르기 ---


def test_the_record_choices_follow_the_current_mode_first(window, actions):
    _done(window)
    history = screen_widget(window, ScreenId.HISTORY)
    reports = screen_widget(window, ScreenId.REPORTS)
    assert _find(history, QRadioButton, "historyMode_paper").isChecked()
    assert _find(reports, QRadioButton, "reportsMode_paper").isChecked()
    assert _find(history, QRadioButton, "historyMode_paper").text() == "모의매매 기록"
    assert _find(history, QRadioButton, "historyMode_live").text() == "실거래 기록"
    _go_live(window)
    assert _find(history, QRadioButton, "historyMode_live").isChecked()
    assert _find(reports, QRadioButton, "reportsMode_live").isChecked()


def test_choosing_the_other_record_asks_for_that_mode(window, actions):
    _done(window)
    select_screen(window, ScreenId.HISTORY)
    actions.calls.clear()
    history = screen_widget(window, ScreenId.HISTORY)
    _find(history, QRadioButton, "historyMode_live").click()
    assert actions.calls[-1][0] == "load_history"
    assert actions.calls[-1][4] is Mode.LIVE
    _find(history, QRadioButton, "historyMode_paper").click()
    assert actions.calls[-1][4] is None  # 지금 모드이면 기본값이다
    select_screen(window, ScreenId.REPORTS)
    actions.calls.clear()
    reports = screen_widget(window, ScreenId.REPORTS)
    _find(reports, QRadioButton, "reportsMode_live").click()
    assert actions.calls[-1] == ("load_reports", "KRW-AAA", Mode.LIVE)


def test_the_chosen_record_stays_when_the_mode_changes_after_the_user_chose(window):
    _done(window)
    history = screen_widget(window, ScreenId.HISTORY)
    _find(history, QRadioButton, "historyMode_live").click()
    window.apply(_status(mode=Mode.PAPER))
    assert _find(history, QRadioButton, "historyMode_live").isChecked()


def test_the_live_record_has_the_error_border(window):
    _done(window)
    history = screen_widget(window, ScreenId.HISTORY)
    card = _find(history, QWidget, "historyCard")
    assert card.styleSheet() == ""
    _find(history, QRadioButton, "historyMode_live").click()
    assert f"{theme.DANGER_BORDER}px solid {theme.ERROR}" in card.styleSheet()
    _find(history, QRadioButton, "historyMode_paper").click()
    assert card.styleSheet() == ""
    reports = screen_widget(window, ScreenId.REPORTS)
    left = _find(reports, QWidget, "reportLeftCard")
    _find(reports, QRadioButton, "reportsMode_live").click()
    assert theme.ERROR in left.styleSheet()


def test_the_external_tab_shows_time_coin_and_the_change_right_aligned(window, actions):
    _done(window)
    select_screen(window, ScreenId.HISTORY)
    history = screen_widget(window, ScreenId.HISTORY)
    assert isinstance(history, HistoryScreen)
    history.set_clock(lambda: datetime(2026, 1, 2, 10, 0, tzinfo=UTC))
    history.reset_day()
    _find(history, QPushButton, "historyTab_external").click()
    assert history.chosen_tab() is HistoryTab.EXTERNAL
    rows = (HistoryRow("e1", ("10:00:00", "AAA", "+2"), (("수량 변화", "+2"),), None),)
    window.apply(
        HistoryView(
            HistoryTab.EXTERNAL,
            None,
            history.chosen_day(),
            ("시각", "코인", "수량 변화"),
            rows,
            LoadState.READY,
        )
    )
    table = _find(history, QWidget, "historyTable")
    assert [table.horizontalHeaderItem(i).text() for i in range(3)] == ["시각", "코인", "수량 변화"]
    right = int(Qt.AlignmentFlag.AlignRight.value)
    assert int(table.item(0, 0).textAlignment()) & right
    assert not int(table.item(0, 1).textAlignment()) & right
    assert int(table.item(0, 2).textAlignment()) & right


def test_an_alert_from_live_opens_the_live_record(window, actions):
    _done(window)
    alert = AlertView(1, NOW, Mode.LIVE, AlertCategory.TRADE, "A-27", "KRW-AAA", "글")
    actions.calls.clear()
    window._go_alert(alert)
    last = actions.calls[-1]
    assert last[0] == "load_history"
    assert last[4] is Mode.LIVE


# --- 상태 띠와 현황의 실거래 꼴 ---


def test_the_band_in_live_has_the_error_border_and_a_bold_error_mode(window):
    _done(window)
    band = _find(window, QWidget, "tradeBand")
    mode = _find(window, QLabel, "tradeBandMode")
    assert band.styleSheet() == ""
    assert mode.text() == "모의매매"
    window.apply(_status(mode=Mode.LIVE))
    assert mode.text() == "실거래"
    assert f"border: {theme.DANGER_BORDER}px solid {theme.ERROR}" in band.styleSheet()
    sheet = mode.styleSheet()
    assert f"color: {theme.ERROR}" in sheet
    assert "font-weight: bold" in sheet
    window.apply(_status(mode=Mode.PAPER))
    assert band.styleSheet() == ""
    assert "font-weight: normal" in mode.styleSheet()


def _today() -> TodayView:
    return TodayView(
        Decimal(0), Decimal(3000), Decimal(0), Decimal(100000), Decimal(150000), False, NOW
    )


def test_the_status_shows_the_real_krw_line_in_live_and_hides_the_paper_score(window):
    _done(window)
    select_screen(window, ScreenId.STATUS)
    status = screen_widget(window, ScreenId.STATUS)
    row = PerformanceRow(
        "KRW-AAA", 3, 2, 3, Decimal(100), 1, 1, Decimal(50), None, Decimal("0.01"), Decimal("0.02")
    )
    window.apply(PerformanceView((row,), NOW))
    window.apply(_today())
    window.apply(_status())
    performance = _find(status, QWidget, "statusPerformanceCard")
    assert _find(status, QLabel, "statusTodayKrw").text() == "가상 원화 150,000원"
    assert not performance.isHidden()
    window.apply(_status(mode=Mode.LIVE))
    assert _find(status, QLabel, "statusTodayKrw").text() == "원화 150,000원"
    assert performance.isHidden()  # 모의 성적은 모의매매일 때만 보인다
    window.apply(_status(mode=Mode.PAPER))
    assert _find(status, QLabel, "statusTodayKrw").text() == "가상 원화 150,000원"
    assert not performance.isHidden()


def test_the_coin_cards_have_the_error_border_in_live(window):
    _done(window)
    select_screen(window, ScreenId.STATUS)
    status = screen_widget(window, ScreenId.STATUS)
    card = _find(status, QWidget, "statusCoin_KRW-AAA")
    assert card.styleSheet() == ""
    window.apply(_status(mode=Mode.LIVE))
    assert f"{theme.DANGER_BORDER}px solid {theme.ERROR}" in card.styleSheet()
    window.apply(_status(mode=Mode.PAPER))
    assert card.styleSheet() == ""
