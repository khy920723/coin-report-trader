"""화면의 보기 기준을 한곳에 둔다(M1 명세 3.14.7. 화면 설계서 5-0 판 3의 20장, 21장).

색 토큰, 글꼴 단계, 간격을 여기에만 적고, 다른 모듈은 이 이름으로 읽는다(색 값을 글로 박지 않는다).
앱 전체의 스타일시트(`app_stylesheet`)도 이 값으로 만든다. 어두운 초록 계열의 테마 하나다(판 3).
"""

from typing import Final

# --- 색 토큰 열여섯(21장) ---
BG: Final = "#0A1A14"
SURFACE: Final = "#11241C"
SELECTED: Final = "#1A3529"
DIVIDER: Final = "#1F3B2F"
BORDER: Final = "#5E8574"
TEXT: Final = "#EEF5F1"
TEXT_SUB: Final = "#8FAA9D"
TEXT_DISABLED: Final = "#56695F"
ACCENT: Final = "#1D7A4F"
ACCENT_TEXT: Final = "#4CC98B"
FOCUS: Final = "#8EE6BA"
RISE: Final = "#FF6468"
FALL: Final = "#5C9DFF"
OK: Final = "#7EE08A"
CAUTION: Final = "#EDD05A"
ERROR: Final = "#FF9548"

COLORS: Final = {
    "bg": BG,
    "surface": SURFACE,
    "selected": SELECTED,
    "divider": DIVIDER,
    "border": BORDER,
    "text": TEXT,
    "text-sub": TEXT_SUB,
    "text-disabled": TEXT_DISABLED,
    "accent": ACCENT,
    "accent-text": ACCENT_TEXT,
    "focus": FOCUS,
    "rise": RISE,
    "fall": FALL,
    "ok": OK,
    "caution": CAUTION,
    "error": ERROR,
}
"""토큰 이름과 값(5-0 판 3의 21장)."""

# --- 글꼴 단계(20장). 단위는 px ---
FONT_FAMILY: Final = "Malgun Gothic"
FONT_SCREEN_TITLE: Final = 20
FONT_KEY_NUMBER: Final = 28
FONT_BIG_VALUE: Final = 20
FONT_GROUP_TITLE: Final = 14
FONT_BODY: Final = 14
FONT_SUB: Final = 12

# --- 간격(20장). 단위는 px ---
UNIT: Final = 4
SCREEN_PADDING: Final = 24
CARD_PADDING: Final = 16
CARD_GAP: Final = 16
CARD_RADIUS: Final = 8
ROW_HEIGHT: Final = 40
TWO_LINE_ROW_HEIGHT: Final = 52
FIELD_GAP: Final = 12
GROUP_GAP: Final = 24
LABEL_GAP: Final = 4
BUTTON_HEIGHT: Final = 36
BUTTON_MIN_WIDTH: Final = 80
BUTTON_RADIUS: Final = 6
ICON_SIZE: Final = 16
ICON_GAP: Final = 4
TOOLTIP_WIDTH: Final = 320
SCROLLBAR_WIDTH: Final = 8

# --- 라벨과 단추의 모양을 고르는 동적 속성(스타일시트가 읽는다) ---
ROLE_PROPERTY: Final = "role"
ROLE_TITLE: Final = "title"
ROLE_BIG: Final = "big"
ROLE_GROUP: Final = "group"
ROLE_SUB: Final = "sub"
ROLE_PRIMARY: Final = "primary"
TREND_PROPERTY: Final = "trend"
TREND_RISE: Final = "rise"
TREND_FALL: Final = "fall"
CARD_PROPERTY: Final = "card"
OK_PROPERTY: Final = "ok"


def app_stylesheet() -> str:
    """앱 전체의 스타일시트. 색과 크기는 모두 이 모듈의 값이다."""
    return f"""
QWidget {{
    color: {TEXT};
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_BODY}px;
}}
QWidget:disabled {{ color: {TEXT_DISABLED}; }}
QMainWindow, QDialog, QMessageBox {{ background: {BG}; }}
QScrollArea {{ background: {BG}; border: 0; }}
QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QLabel, QRadioButton, QCheckBox {{ background: transparent; }}
QLabel[{ROLE_PROPERTY}="{ROLE_TITLE}"] {{
    font-size: {FONT_SCREEN_TITLE}px; font-weight: bold;
}}
QLabel[{ROLE_PROPERTY}="{ROLE_BIG}"] {{ font-size: {FONT_BIG_VALUE}px; font-weight: bold; }}
QLabel[{ROLE_PROPERTY}="{ROLE_GROUP}"] {{ font-size: {FONT_GROUP_TITLE}px; font-weight: bold; }}
QLabel[{ROLE_PROPERTY}="{ROLE_SUB}"] {{ color: {TEXT_SUB}; font-size: {FONT_SUB}px; }}
QLabel[{ROLE_PROPERTY}="{ROLE_SUB}"]:disabled {{ color: {TEXT_DISABLED}; }}
QLabel[{TREND_PROPERTY}="{TREND_RISE}"] {{ color: {RISE}; }}
QLabel[{TREND_PROPERTY}="{TREND_FALL}"] {{ color: {FALL}; }}
QLabel[{OK_PROPERTY}="true"] {{ color: {OK}; }}
QFrame[{CARD_PROPERTY}="true"] {{
    background: {SURFACE}; border: 0; border-radius: {CARD_RADIUS}px;
}}
QFrame[{CARD_PROPERTY}="true"] QScrollArea,
QFrame[{CARD_PROPERTY}="true"] QScrollArea > QWidget > QWidget {{ background: {SURFACE}; }}
QPushButton {{
    background: {SELECTED}; color: {ACCENT_TEXT}; border: 2px solid transparent;
    border-radius: {BUTTON_RADIUS}px; min-height: {BUTTON_HEIGHT - 4}px;
    min-width: {BUTTON_MIN_WIDTH - 4}px; padding: 0 {FIELD_GAP}px;
}}
QPushButton[{ROLE_PROPERTY}="{ROLE_PRIMARY}"] {{ background: {ACCENT}; color: #FFFFFF; }}
QPushButton:disabled, QPushButton[{ROLE_PROPERTY}="{ROLE_PRIMARY}"]:disabled {{
    background: {SURFACE}; color: {TEXT_DISABLED};
}}
QFrame[{CARD_PROPERTY}="true"][clickable="true"]:focus {{ border: 2px solid {FOCUS}; }}
QPushButton:flat {{ background: transparent; border-radius: 0; }}
QPushButton[tab="true"] {{
    background: transparent; color: {TEXT_SUB}; border: 0; border-bottom: 2px solid transparent;
    border-radius: 0; padding: 0 {FIELD_GAP}px;
}}
QPushButton[tab="true"][selected="true"] {{
    color: {TEXT}; border-bottom: 2px solid {ACCENT};
}}
QPushButton[tab="true"]:focus {{ border: 2px solid {FOCUS}; }}
QWidget[alertRow="true"] {{ border-bottom: 1px solid {DIVIDER}; }}
QLabel[alertLine="true"]:focus {{ border: 2px solid {FOCUS}; }}
QLabel[cell="true"] {{ border-bottom: 1px solid {DIVIDER}; min-height: {ROW_HEIGHT - 1}px; }}
QPushButton:focus, QLineEdit:focus, QComboBox:focus, QRadioButton:focus, QCheckBox:focus,
QListWidget:focus {{ border: 2px solid {FOCUS}; }}
QLineEdit, QComboBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {BUTTON_RADIUS}px;
    min-height: {BUTTON_HEIGHT - 4}px; padding: 0 {FIELD_GAP}px;
    selection-background-color: {ACCENT};
}}
QComboBox QAbstractItemView {{
    background: {SELECTED}; border: 1px solid {BORDER}; selection-background-color: {ACCENT};
}}
QRadioButton {{
    border: 1px solid {BORDER}; border-radius: {BUTTON_RADIUS}px;
    padding: 6px {FIELD_GAP}px; min-height: {BUTTON_HEIGHT - 14}px;
}}
QRadioButton::indicator {{ width: 0px; height: 0px; }}
QRadioButton:checked {{ border: 1px solid {ACCENT}; background: {SELECTED}; color: {TEXT}; }}
QRadioButton:disabled {{ border-color: {DIVIDER}; }}
QCheckBox {{ spacing: 8px; }}
QListWidget {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {BUTTON_RADIUS}px;
    outline: 0;
}}
QListWidget::item:selected {{ background: {SELECTED}; color: {TEXT}; }}
QListWidget#screenNav {{ background: {BG}; border: 0; color: {TEXT_SUB}; }}
QListWidget#screenNav::item {{
    height: {ROW_HEIGHT}px; border-radius: {BUTTON_RADIUS}px; padding-left: {UNIT * 2}px;
}}
QListWidget#screenNav::item:selected {{ background: {SELECTED}; color: {TEXT}; }}
QStatusBar {{ background: {SURFACE}; border-top: 1px solid {DIVIDER}; }}
QStatusBar QLabel {{ color: {TEXT_SUB}; font-size: {FONT_SUB}px; }}
QToolTip {{
    background: {SELECTED}; color: {TEXT}; border: 1px solid {BORDER};
    padding: 8px; border-radius: {BUTTON_RADIUS}px; font-size: {FONT_SUB}px;
}}
QScrollBar:vertical {{ background: transparent; width: {SCROLLBAR_WIDTH}px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: {SCROLLBAR_WIDTH}px; margin: 0; }}
QScrollBar::handle {{ background: {BORDER}; border-radius: {UNIT}px; min-height: 24px;
    min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""
