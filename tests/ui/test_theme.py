"""색 토큰, 글꼴 단계, 간격과 앱 전체의 스타일시트(M1 명세 3.14.7, 화면 설계서 5-0 판 2의 20·21장)."""

import re
from pathlib import Path

import pytest
from PySide6.QtWidgets import QLabel

from coin_report_trader.core import UiContext
from coin_report_trader.ui import create_main_window, theme
from coin_report_trader.ui.state import Tone
from coin_report_trader.ui.widgets import WARNING_COLOR, make_label

UI_DIR = Path(theme.__file__).parent

TOKENS = {
    "bg": "#0B1220",
    "surface": "#131C2E",
    "selected": "#1C2840",
    "divider": "#1F2A3D",
    "border": "#66758F",
    "text": "#F1F4F9",
    "text-sub": "#8B95A7",
    "text-disabled": "#5C6779",
    "accent": "#2B6BE6",
    "accent-text": "#5C93F2",
    "focus": "#7AA7FF",
    "rise": "#F0585D",
    "fall": "#4B8DF8",
    "ok": "#3CCB7F",
    "caution": "#F5B544",
    "error": "#FF8A65",
}


def test_the_sixteen_color_tokens_have_the_design_names_and_values():
    assert theme.COLORS == TOKENS
    assert len(theme.COLORS) == 16


def test_font_steps_and_spacing_are_the_design_values():
    assert theme.FONT_FAMILY == "Malgun Gothic"
    assert (
        theme.FONT_SCREEN_TITLE,
        theme.FONT_KEY_NUMBER,
        theme.FONT_BIG_VALUE,
        theme.FONT_GROUP_TITLE,
        theme.FONT_BODY,
        theme.FONT_SUB,
    ) == (20, 28, 20, 14, 14, 12)
    assert (theme.SCREEN_PADDING, theme.CARD_PADDING, theme.CARD_GAP, theme.CARD_RADIUS) == (
        24,
        16,
        16,
        8,
    )
    assert (theme.ROW_HEIGHT, theme.TWO_LINE_ROW_HEIGHT) == (40, 52)
    assert (theme.BUTTON_HEIGHT, theme.BUTTON_MIN_WIDTH, theme.BUTTON_RADIUS) == (36, 80, 6)
    assert (theme.ICON_SIZE, theme.ICON_GAP, theme.TOOLTIP_WIDTH) == (16, 4, 320)
    for value in (
        theme.UNIT,
        theme.SCREEN_PADDING,
        theme.CARD_PADDING,
        theme.CARD_GAP,
        theme.ROW_HEIGHT,
        theme.TWO_LINE_ROW_HEIGHT,
        theme.FIELD_GAP,
        theme.GROUP_GAP,
        theme.LABEL_GAP,
        theme.BUTTON_HEIGHT,
    ):
        assert value % theme.UNIT == 0  # 크기와 간격은 4의 배수다


def test_the_style_sheet_is_made_from_the_tokens():
    sheet = theme.app_stylesheet()
    for name in ("bg", "surface", "selected", "border", "text", "text-sub", "accent", "focus"):
        assert TOKENS[name] in sheet
    assert f'font-family: "{theme.FONT_FAMILY}"' in sheet
    assert f"font-size: {theme.FONT_SCREEN_TITLE}px" in sheet
    assert f"border-radius: {theme.CARD_RADIUS}px" in sheet
    # 밝은 바탕은 없다(어두운 테마 하나). 흰색은 주 단추의 글뿐이다.
    colors = {c.upper() for c in re.findall(r"#[0-9A-Fa-f]{6}", sheet)}
    assert colors <= {*(v.upper() for v in TOKENS.values()), "#FFFFFF"}
    assert sheet.count("#FFFFFF") == 1


def test_no_other_ui_module_writes_a_color_value():
    for path in sorted(UI_DIR.glob("*.py")):
        if path.name == "theme.py":
            continue
        assert not re.search(r"#[0-9A-Fa-f]{6}\b", path.read_text(encoding="utf-8")), path.name


def test_the_warning_color_is_the_error_token_and_the_tone_property_is_unchanged(qapp):
    assert WARNING_COLOR == "#FF8A65" == theme.ERROR
    label = make_label("글", "x", Tone.WARNING)
    assert label.property("tone") == "warning"
    assert theme.ERROR in label.styleSheet()
    normal = make_label("글", "y")
    assert normal.property("tone") == "normal"
    assert normal.styleSheet() == ""


def test_the_window_uses_the_style_sheet_and_the_dark_background(qapp):
    window = create_main_window(UiContext("0.0.1", "X"))
    try:
        assert window.styleSheet() == theme.app_stylesheet()
        status = window.findChild(QLabel, "statusLine")
        assert status is not None
    finally:
        window.deleteLater()


@pytest.mark.parametrize("role", ["title", "big", "group", "sub"])
def test_label_roles_are_picked_by_the_dynamic_property(role):
    assert f'[{theme.ROLE_PROPERTY}="{role}"]' in theme.app_stylesheet()
