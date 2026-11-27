"""설정의 '업비트 키'와 '모드' 묶음, 키 넣기 창(M5 명세 3.8, 3.11).

- 키 묶음 `settingsUpbitKey`: 제목 옆 'ⓘ' `settingsUpbitKeyInfo`, 상태 `settingsUpbitKeyState`
  ("넣음" / "넣지 않음"), 만든 날 `settingsUpbitKeyCreated`, 만료일 `settingsUpbitKeyExpires`
  (30일 이하이면 `caution`, 지났으면 `error`), 단추 "키 넣기" `settingsUpbitKeySet`, "키 지우기"
  `settingsUpbitKeyDelete`(위험한 동작의 단추 꼴), 그 아래 글 `settingsUpbitKeyMessage`, 흐릴 때
  곁의 글 `settingsUpbitKeyLockNote`. 키의 값은 어디에도 들지 않는다.
- 모드 묶음 `settingsMode`: 제목 옆 'ⓘ' `settingsModeInfo`, 글 `settingsModeState`, 단추
  `settingsLiveOn` / `settingsLiveOff`(지금 모드에 맞는 하나만 보인다), 막힌 까닭
  `settingsLiveBlocks`(한 줄씩), 센 수 `settingsLiveCounts`, 전량 매도 기록
  `settingsLiveSellAll`, N `settingsLiveRoundsN`, 결과 글 `settingsModeMessage`.
- 키 넣기 창 `upbitKeyDialog`(창을 부모로 둔 모달): 칸 `upbitKeyAccess`, `upbitKeySecret`(가림
  표시), `upbitKeyCreated`(`QDateEdit`), 칸 아래 글 `<칸 이름>Error`, 단추 "저장" `upbitKeySave`,
  "취소" `upbitKeyCancel`(기본). 닫히면 칸의 글을 지운다.

이 모듈은 동작을 직접 부르지 않고 신호만 낸다(창이 부른다).
"""

from datetime import date

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from coin_report_trader.core import LiveStatusView, Mode, UpbitKeyView, coin_label
from coin_report_trader.ui import theme
from coin_report_trader.ui.cards import Card, title_row
from coin_report_trader.ui.history_screen import date_edit_style
from coin_report_trader.ui.info_icon import InfoIcon, info_text
from coin_report_trader.ui.state import Message, Tone
from coin_report_trader.ui.texts import (
    CANCEL_TEXT,
    KEY_ABSENT,
    KEY_ACCESS_LABEL,
    KEY_CREATED_LABEL,
    KEY_DATE_ERROR,
    KEY_DELETE_TEXT,
    KEY_DIALOG_TITLE,
    KEY_EXPIRY_WARN_DAYS,
    KEY_INFO,
    KEY_LIVE_LOCKED_NOTE,
    KEY_LOCKED_NOTE,
    KEY_MAX_LENGTH,
    KEY_MIN_LENGTH,
    KEY_PRESENT,
    KEY_SECRET_LABEL,
    KEY_SET_TEXT,
    KEY_SHAPE_ERROR,
    KEY_TITLE,
    LIVE_OFF_TEXT,
    LIVE_ON_TEXT,
    MODE_INFO,
    MODE_LIVE_TEXT,
    MODE_PAPER_TEXT,
    MODE_TITLE,
    NO_VALUE,
    ROUNDS_N_DEFAULT,
    ROUNDS_N_LABEL,
    ROUNDS_N_TIPS,
    ROUNDS_N_VALUES,
    SAVE_TEXT,
    key_created_text,
    key_expires_text,
    live_count_line,
    sell_all_record_text,
)
from coin_report_trader.ui.trade_band import danger_button_style
from coin_report_trader.ui.widgets import (
    LOCK_PROPERTY,
    make_label,
    set_role,
    set_tone,
    show_message,
)

KEY_PREFIX = "settingsUpbitKey"
DIALOG_NAME = "upbitKeyDialog"


def key_shape_ok(text: str) -> bool:
    """키 한 칸의 꼴: 공백 없는 ASCII이고 길이가 20자 이상 100자 이하(M5 명세 3.8)."""
    if not (KEY_MIN_LENGTH <= len(text) <= KEY_MAX_LENGTH):
        return False
    return text.isascii() and not any(ch.isspace() for ch in text) and text.isprintable()


class UpbitKeyCard(Card):
    """설정의 '업비트 키' 묶음."""

    set_requested = Signal()
    delete_requested = Signal()

    def __init__(self) -> None:
        super().__init__(KEY_PREFIX)
        info = InfoIcon(KEY_TITLE, info_text(*KEY_INFO), f"{KEY_PREFIX}Info")
        self.body.addLayout(title_row(KEY_TITLE, f"{KEY_PREFIX}Title", info))
        self._state = make_label(KEY_ABSENT, f"{KEY_PREFIX}State")
        self._created = make_label(key_created_text(None), f"{KEY_PREFIX}Created")
        self._expires = make_label(key_expires_text(None), f"{KEY_PREFIX}Expires")
        for label in (self._state, self._created, self._expires):
            self.body.addWidget(label)
        self._set = QPushButton(KEY_SET_TEXT)
        self._set.setObjectName(f"{KEY_PREFIX}Set")
        self._set.clicked.connect(self.set_requested)
        self._delete = QPushButton(KEY_DELETE_TEXT)
        self._delete.setObjectName(f"{KEY_PREFIX}Delete")
        self._delete.setStyleSheet(danger_button_style(f"{KEY_PREFIX}Delete"))
        self._delete.clicked.connect(self.delete_requested)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._set)
        row.addWidget(self._delete)
        row.addStretch(1)
        self.body.addLayout(row)
        self._note = make_label("", f"{KEY_PREFIX}LockNote")
        set_role(self._note, theme.ROLE_SUB)
        self._note.hide()
        self.body.addWidget(self._note)
        self._message = make_label("", f"{KEY_PREFIX}Message")
        self._message.hide()
        self.body.addWidget(self._message)
        self.body.addStretch(1)
        self._view: UpbitKeyView | None = None
        self._live = False
        self._active = False
        self._today: date | None = None

    def render(
        self,
        view: UpbitKeyView | None,
        *,
        active: bool,
        live: bool,
        today: date,
        message: Message | None,
    ) -> None:
        """`active`는 설정을 고칠 수 있는 때(처음 설정을 마치고 설정을 읽었다)다. 키를 넣거나 지우는
        것은 멈춤 상태(`can_edit`)에서만, 실거래 중이 아닐 때만 된다."""
        self._view = view
        self._live = live
        self._active = active
        self._today = today
        present = view is not None and view.present
        self._state.setText(KEY_PRESENT if present else KEY_ABSENT)
        created = view.created.isoformat() if view is not None and view.created else None
        expires = view.expires.isoformat() if view is not None and view.expires else None
        self._created.setText(key_created_text(created))
        self._expires.setText(key_expires_text(expires))
        self._paint_expires(view.expires if view is not None else None, today)
        can_edit = view is not None and view.can_edit and not live
        self._apply_enabled(can_edit and active, present)
        if view is not None and live:
            note = KEY_LIVE_LOCKED_NOTE
        elif view is not None and not view.can_edit:
            note = KEY_LOCKED_NOTE
        else:
            note = ""
        self._note.setText(note)
        self._note.setVisible(bool(note))
        show_message(self._message, message)

    def reapply(self) -> None:
        """`set_dim`이 칸을 다시 켠 뒤 단추의 쓸 수 있음을 다시 맞춘다."""
        view = self._view
        present = view is not None and view.present
        can_edit = view is not None and view.can_edit and not self._live
        self._apply_enabled(can_edit and self._active, present)

    def _apply_enabled(self, editable: bool, present: bool) -> None:
        self._set.setEnabled(editable)
        self._delete.setEnabled(editable and present)

    def _paint_expires(self, expires: date | None, today: date) -> None:
        color = None
        if expires is not None:
            days = (expires - today).days
            if days < 0:
                color = theme.ERROR
            elif days <= KEY_EXPIRY_WARN_DAYS:
                color = theme.CAUTION
        self._expires.setStyleSheet(f"color: {color};" if color else "")


class ModeCard(Card):
    """설정의 '모드' 묶음."""

    live_on_requested = Signal()
    live_off_requested = Signal()

    def __init__(self) -> None:
        super().__init__("settingsMode")
        info = InfoIcon(MODE_TITLE, info_text(*MODE_INFO), "settingsModeInfo")
        self.body.addLayout(title_row(MODE_TITLE, "settingsModeTitle", info))
        self._state = make_label(MODE_PAPER_TEXT, "settingsModeState")
        set_role(self._state, theme.ROLE_GROUP)
        self.body.addWidget(self._state)
        self._on = QPushButton(LIVE_ON_TEXT)
        self._on.setObjectName("settingsLiveOn")
        self._on.clicked.connect(self.live_on_requested)
        self._off = QPushButton(LIVE_OFF_TEXT)
        self._off.setObjectName("settingsLiveOff")
        self._off.clicked.connect(self.live_off_requested)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._on)
        row.addWidget(self._off)
        row.addStretch(1)
        self.body.addLayout(row)
        self._blocks = make_label("", "settingsLiveBlocks")
        self._blocks.setStyleSheet(f"color: {theme.ERROR};")
        self._blocks.hide()
        self.body.addWidget(self._blocks)
        self._counts = make_label("", "settingsLiveCounts")
        self._counts.hide()
        self.body.addWidget(self._counts)
        self._sell_all = make_label("", "settingsLiveSellAll")
        self._sell_all.hide()
        self.body.addWidget(self._sell_all)
        n_label = make_label(ROUNDS_N_LABEL, "settingsLiveRoundsNLabel")
        set_role(n_label, theme.ROLE_SUB)
        self.body.addWidget(n_label)
        self._n = QComboBox()
        self._n.setObjectName("settingsLiveRoundsN")
        for value in ROUNDS_N_VALUES:
            self._n.addItem(f"{value}회", value)
            tip = ROUNDS_N_TIPS[value]
            self._n.setItemData(self._n.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
        self._n.currentIndexChanged.connect(self._tip)
        # N을 저장하는 동작이 `UiActions`에 없어 고를 수 없다(PR 본문의 알림). 지금 값만 보인다.
        self._n.setProperty(LOCK_PROPERTY, True)
        self._n.setEnabled(False)
        self.body.addWidget(self._n)
        self._message = make_label("", "settingsModeMessage")
        self._message.hide()
        self.body.addWidget(self._message)
        self.body.addStretch(1)
        self._live = False
        self._active = False
        self._status: LiveStatusView | None = None
        self._paused = False
        self.render(Mode.PAPER, None, paused=False, active=False, message=None)

    def _tip(self, _index: int = 0) -> None:
        value = self._n.currentData()
        self._n.setToolTip(ROUNDS_N_TIPS.get(value, ""))

    def render(
        self,
        mode: Mode,
        status: LiveStatusView | None,
        *,
        paused: bool,
        active: bool,
        message: Message | None,
    ) -> None:
        """`mode`는 지금 모드, `status`는 막힌 까닭과 센 수, `paused`는 멈춤 상태(사용자 멈춤이나
        오류 멈춤)인지다. "실거래 켜기"는 막힌 까닭이 없을 때만, "모의매매로 바꾸기"는 멈춤
        상태에서만 누를 수 있다."""
        live = mode is Mode.LIVE
        self._live, self._active, self._status, self._paused = live, active, status, paused
        self._state.setText(MODE_LIVE_TEXT if live else MODE_PAPER_TEXT)
        self._on.setVisible(not live)
        self._off.setVisible(live)
        self._apply_enabled()
        blocks = status.blocks if status is not None else ()
        self._blocks.setText("\n".join(blocks))
        self._blocks.setVisible(bool(blocks))
        counts = status.counts if status is not None else ()
        lines = (live_count_line(coin_label(m), counted, needed) for m, counted, needed in counts)
        self._counts.setText("\n".join(lines))
        self._counts.setVisible(bool(counts))
        self._sell_all.setText(sell_all_record_text(status.sell_all_done) if status else "")
        self._sell_all.setVisible(status is not None)
        needed_n = counts[0][2] if counts else ROUNDS_N_DEFAULT
        index = self._n.findData(needed_n)
        self._n.blockSignals(True)
        self._n.setCurrentIndex(index if index >= 0 else self._n.findData(ROUNDS_N_DEFAULT))
        self._n.blockSignals(False)
        self._tip()
        show_message(self._message, message)
        if message is not None and message.tone is Tone.NORMAL:
            set_tone(self._message, Tone.NORMAL)

    def reapply(self) -> None:
        """`set_dim`이 칸을 다시 켠 뒤 단추의 쓸 수 있음을 다시 맞춘다."""
        self._apply_enabled()
        self._n.setEnabled(False)

    def _apply_enabled(self) -> None:
        status = self._status
        self._on.setEnabled(self._active and status is not None and not status.blocks)
        self._off.setEnabled(self._active and self._paused)


class UpbitKeyDialog(QDialog):
    """키 넣기 창. 꼴이 맞으면 `key_chosen(access, secret, created)`를 내고 닫는다. 닫히면 칸의 글을
    지운다(키의 값을 들고 있지 않는다)."""

    key_chosen = Signal(str, str, object)

    def __init__(self, parent: QWidget | None, today: date) -> None:
        super().__init__(parent)
        self.setObjectName(DIALOG_NAME)
        self.setWindowTitle(KEY_DIALOG_TITLE)
        self.setModal(True)
        self._today = today
        layout = QVBoxLayout(self)
        layout.setSpacing(theme.FIELD_GAP)
        self.access = self._secret_edit("upbitKeyAccess", KEY_ACCESS_LABEL, layout)
        self.secret = self._secret_edit("upbitKeySecret", KEY_SECRET_LABEL, layout)
        caption = make_label(KEY_CREATED_LABEL, "upbitKeyCreatedLabel")
        set_role(caption, theme.ROLE_SUB)
        layout.addWidget(caption)
        self.created = QDateEdit()
        self.created.setObjectName("upbitKeyCreated")
        self.created.setCalendarPopup(True)
        self.created.setDisplayFormat("yyyy-MM-dd")
        self.created.setStyleSheet(date_edit_style())
        self.created.setDate(QDate(today.year, today.month, today.day))
        layout.addWidget(self.created)
        self._created_error = make_label("", "upbitKeyCreatedError", Tone.WARNING)
        self._created_error.hide()
        layout.addWidget(self._created_error)
        self._save = QPushButton(SAVE_TEXT)
        self._save.setObjectName("upbitKeySave")
        self._save.clicked.connect(self._on_save)
        self._cancel = QPushButton(CANCEL_TEXT)
        self._cancel.setObjectName("upbitKeyCancel")
        self._cancel.setDefault(True)
        self._cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._cancel)
        row.addWidget(self._save)
        layout.addLayout(row)

    def _secret_edit(self, name: str, label: str, layout: QVBoxLayout) -> QLineEdit:
        caption = make_label(label, f"{name}Label")
        set_role(caption, theme.ROLE_SUB)
        layout.addWidget(caption)
        edit = QLineEdit()
        edit.setObjectName(name)
        # 가림 표시는 복사와 잘라내기를 막는다. 입력기가 글을 모으지 못하게 한다.
        edit.setEchoMode(QLineEdit.EchoMode.Password)
        edit.setInputMethodHints(
            Qt.InputMethodHint.ImhHiddenText
            | Qt.InputMethodHint.ImhNoPredictiveText
            | Qt.InputMethodHint.ImhNoAutoUppercase
            | Qt.InputMethodHint.ImhSensitiveData
        )
        edit.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        edit.textChanged.connect(lambda _t, n=name: self._clear_error(n))
        layout.addWidget(edit)
        error = make_label("", f"{name}Error", Tone.WARNING)
        error.hide()
        layout.addWidget(error)
        return edit

    def _error_label(self, name: str) -> QLabel:
        label = self.findChild(QLabel, f"{name}Error")
        assert label is not None
        return label

    def _clear_error(self, name: str) -> None:
        label = self._error_label(name)
        label.setText("")
        label.hide()

    def _set_error(self, name: str, text: str) -> None:
        label = self._error_label(name)
        label.setText(text)
        label.show()

    def _on_save(self) -> None:
        access, secret = self.access.text(), self.secret.text()
        qdate = self.created.date()
        created = date(qdate.year(), qdate.month(), qdate.day())
        ok = True
        for name, value in (("upbitKeyAccess", access), ("upbitKeySecret", secret)):
            self._clear_error(name)
            if not key_shape_ok(value):
                self._set_error(name, KEY_SHAPE_ERROR)
                ok = False
        self._clear_error("upbitKeyCreated")
        if created > self._today:
            self._set_error("upbitKeyCreated", KEY_DATE_ERROR)
            ok = False
        if not ok:
            return
        self.key_chosen.emit(access, secret, created)
        self.accept()

    def done(self, result: int) -> None:
        """닫히면 칸의 글을 지운다."""
        self.access.clear()
        self.secret.clear()
        super().done(result)


__all__ = ["DIALOG_NAME", "NO_VALUE", "ModeCard", "UpbitKeyCard", "UpbitKeyDialog", "key_shape_ok"]
