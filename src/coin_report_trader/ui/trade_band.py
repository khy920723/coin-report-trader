"""상태 띠와 긴급 정지 두 단추(M3 명세 3.17.1, 설계서 2.2~2.3, PPT 5-0 판 4의 28~30장).

화면 자리 위의 한 줄이고 화면 자리와 함께 넘기지 않는다. 칸은 왼쪽부터 모드, 멈춤(과 풀기 단추),
손절, 켜는 중, 알림이고 해당하지 않는 칸은 보이지 않는다(모드는 늘 보인다). 오른쪽 끝에 "멈춤"과
"멈추고 전량 매도"가 있다. 이 모듈은 동작을 직접 부르지 않고 신호만 낸다(창이 부른다).

객체 이름: 띠 `tradeBand`, 칸 `tradeBandMode`, `tradeBandPause`, `tradeBandStopOff`,
`tradeBandStarting`, `tradeBandAlerts`(누르는 칸), 단추 `pauseButton`, `sellAllButton`,
`resumeUserButton`, `resumeErrorButton`, 곁의 글 `tradeBandResumeNote`
("{시각} 뒤에 풀 수 있습니다.")와 `tradeBandSellAllNote`("오류 멈춤을 푼 뒤 누를 수 있습니다.").
"""

from datetime import UTC, datetime

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from coin_report_trader.core import ErrorPauseCause, Mode, TradeStatusView
from coin_report_trader.core.texts import kst_when_text
from coin_report_trader.ui import theme
from coin_report_trader.ui.texts import (
    BAND_MODE_LIVE,
    BAND_MODE_PAPER,
    BAND_PAUSE_BOTH,
    BAND_PAUSE_ERROR,
    BAND_PAUSE_USER,
    BAND_STOP_OFF,
    PAUSE_TEXT,
    RESUME_ERROR_TEXT,
    RESUME_USER_TEXT,
    SELL_ALL_BLOCKED_NOTE,
    SELL_ALL_TEXT,
    band_alerts_text,
    resume_wait_text,
)
from coin_report_trader.ui.widgets import live_border_style

BAND_NAME = "tradeBand"


def _tag_style(color: str, *, bold: bool = False) -> str:
    """띠 칸의 꼬리표: '고른 칸' 바탕, 모서리 12px(M3 명세 3.17.1)."""
    weight = "bold" if bold else "normal"
    return (
        f"background: {theme.SELECTED}; color: {color}; font-weight: {weight};"
        f" border-radius: {theme.TAG_RADIUS}px; padding: 2px {theme.FIELD_GAP}px;"
        f" font-size: {theme.FONT_SUB}px;"
    )


def danger_button_style(name: str) -> str:
    """위험한 동작의 단추 꼴: 채움 없이 `error` 2px 테두리와 `error` 굵은 글, 마우스를 올리거나
    누르면 `error` 채움에 `bg` 색 글, 누를 수 없으면 `text-disabled`(M3 명세 3.17.1)."""
    width = theme.DANGER_BORDER
    return (
        f"QPushButton#{name} {{ background: transparent; color: {theme.ERROR};"
        f" border: {width}px solid {theme.ERROR}; font-weight: bold; }}"
        f"QPushButton#{name}:hover, QPushButton#{name}:pressed {{"
        f" background: {theme.ERROR}; color: {theme.BG}; }}"
        f"QPushButton#{name}:focus {{ border: {width}px solid {theme.FOCUS}; }}"
        f"QPushButton#{name}:disabled {{ background: transparent;"
        f" color: {theme.TEXT_DISABLED}; border: {width}px solid {theme.TEXT_DISABLED}; }}"
    )


class TradeBand(QFrame):
    pause_requested = Signal()
    sell_all_requested = Signal()
    resume_user_requested = Signal()
    resume_error_requested = Signal()
    alerts_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName(BAND_NAME)
        self.setProperty(theme.CARD_PROPERTY, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.FIELD_GAP, theme.UNIT, theme.FIELD_GAP, theme.UNIT)
        row.setSpacing(theme.FIELD_GAP)

        self._mode = self._tag("tradeBandMode", BAND_MODE_PAPER, theme.TEXT)
        self._pause = self._tag("tradeBandPause", "", theme.CAUTION, bold=True)
        self._resume_user = QPushButton(RESUME_USER_TEXT)
        self._resume_user.setObjectName("resumeUserButton")
        self._resume_user.clicked.connect(self.resume_user_requested)
        self._resume_error = QPushButton(RESUME_ERROR_TEXT)
        self._resume_error.setObjectName("resumeErrorButton")
        self._resume_error.clicked.connect(self.resume_error_requested)
        self._resume_note = QLabel("")
        self._resume_note.setObjectName("tradeBandResumeNote")
        self._stop_off = self._tag("tradeBandStopOff", BAND_STOP_OFF, theme.ERROR, bold=True)
        self._starting = self._tag("tradeBandStarting", "", theme.TEXT)
        self._alerts = QPushButton("")
        self._alerts.setObjectName("tradeBandAlerts")
        self._alerts.setFlat(True)
        self._alerts.setStyleSheet(f"color: {theme.ACCENT_TEXT}; font-size: {theme.FONT_SUB}px;")
        self._alerts.clicked.connect(self.alerts_requested)
        for widget in (
            self._mode,
            self._pause,
            self._resume_user,
            self._resume_error,
            self._resume_note,
            self._stop_off,
            self._starting,
            self._alerts,
        ):
            row.addWidget(widget)
        row.addStretch(1)

        self._sell_all_note = QLabel(SELL_ALL_BLOCKED_NOTE)
        self._sell_all_note.setObjectName("tradeBandSellAllNote")
        self._sell_all_note.setStyleSheet(f"color: {theme.TEXT_SUB};")
        row.addWidget(self._sell_all_note)
        self._pause_button = QPushButton(PAUSE_TEXT)
        self._pause_button.setObjectName("pauseButton")
        self._pause_button.clicked.connect(self.pause_requested)
        self._sell_all_button = QPushButton(SELL_ALL_TEXT)
        self._sell_all_button.setObjectName("sellAllButton")
        self._sell_all_button.setStyleSheet(danger_button_style("sellAllButton"))
        self._sell_all_button.clicked.connect(self.sell_all_requested)
        row.addWidget(self._pause_button)
        row.addWidget(self._sell_all_button)
        self.render(None, setup_done=False, unseen=0)

    @staticmethod
    def _tag(name: str, text: str, color: str, *, bold: bool = False) -> QLabel:
        label = QLabel(text)
        label.setObjectName(name)
        label.setStyleSheet(_tag_style(color, bold=bold))
        return label

    def render(
        self, status: TradeStatusView | None, *, setup_done: bool, unseen: int
    ) -> None:
        """값을 그린다. `status`가 `None`이면(값이 오기 전) 모드 칸만 보인다."""
        user = status is not None and status.user_paused
        error = status is not None and status.error_paused
        mode = status.mode if status is not None else Mode.PAPER
        live = mode is Mode.LIVE
        self._mode.setText(BAND_MODE_LIVE if live else BAND_MODE_PAPER)
        # 실거래이면 띠 전체에 `error` 2px 테두리, 모드 글은 `error` 굵게(M5 명세 3.11).
        self._mode.setStyleSheet(
            _tag_style(theme.ERROR if live else theme.TEXT, bold=live)
        )
        self.setStyleSheet(live_border_style(BAND_NAME) if live else "")
        pause_text = BAND_PAUSE_BOTH if user and error else BAND_PAUSE_USER if user else (
            BAND_PAUSE_ERROR if error else ""
        )
        self._pause.setText(pause_text)
        self._pause.setVisible(bool(pause_text))
        self._resume_user.setVisible(user)
        self._resume_error.setVisible(error)
        blocked = self._blocked_text(status) if error and status is not None else None
        self._resume_error.setEnabled(blocked is None)
        self._resume_note.setText(blocked or "")
        self._resume_note.setVisible(blocked is not None)
        self._stop_off.setVisible(status is not None and status.stop_loss_off)
        step = status.starting_step if status is not None else None
        self._starting.setText(step or "")
        self._starting.setVisible(bool(step))
        self._alerts.setText(band_alerts_text(unseen))
        self._alerts.setVisible(unseen > 0)
        # 처음 설정 전에는 두 단추 모두 누를 수 없다. 사용자 멈춤 동안 "멈춤"은 달라지지 않으므로
        # 흐리고, 오류 멈춤 동안 "멈추고 전량 매도"는 흐리며 곁에 까닭이 보인다(설계서 2.3, 3.10.3).
        self._pause_button.setEnabled(setup_done and not user)
        self._sell_all_button.setEnabled(setup_done and not error)
        self._sell_all_note.setVisible(setup_done and error)

    @staticmethod
    def _blocked_text(status: TradeStatusView) -> str | None:
        """원인이 일시 차단이고 풀리는 때가 아직이면 "{시각} 뒤에 풀 수 있습니다."(3.10.2)."""
        until = status.blocked_until
        if status.error_cause is not ErrorPauseCause.EXCHANGE or until is None:
            return None
        now = status.as_of or datetime.now(UTC)
        if until <= now:
            return None
        return resume_wait_text(kst_when_text(until, status.as_of))

    def buttons(self) -> tuple[QWidget, ...]:
        """시험이 쓰는 두 긴급 정지 단추."""
        return (self._pause_button, self._sell_all_button)
