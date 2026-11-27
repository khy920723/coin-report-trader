"""core 공용 타입과 상수 시험."""

import ast
import dataclasses
import sys
from pathlib import Path

import pytest

import coin_report_trader
from coin_report_trader import core
from coin_report_trader.core import (
    APP_ID,
    SCREEN_TITLES,
    WINDOW_TITLE,
    ScreenId,
    StorageError,
    UiContext,
)

CORE_DIR = Path(core.__file__).parent


def test_version() -> None:
    assert coin_report_trader.__version__ == "0.1.4"


def test_constants() -> None:
    assert APP_ID == "coin-report-trader"
    assert WINDOW_TITLE == "Coin Report Trader"


def test_screen_ids_follow_screen_order() -> None:
    assert [s.value for s in ScreenId] == [
        "first_setup",
        "status",
        "account",
        "reports",
        "history",
        "settings",
    ]
    assert isinstance(ScreenId.STATUS, str)
    assert ScreenId("history") is ScreenId.HISTORY


def test_screen_titles() -> None:
    assert list(SCREEN_TITLES) == list(ScreenId)
    assert list(SCREEN_TITLES.values()) == ["처음 설정", "현황", "계정", "보고서", "기록", "설정"]


def test_screen_titles_are_read_only() -> None:
    with pytest.raises(TypeError):
        SCREEN_TITLES[ScreenId.STATUS] = "x"  # type: ignore[index]


def test_ui_context_is_frozen() -> None:
    ctx = UiContext(version="0.0.1", data_dir_display="~/x")
    assert (ctx.version, ctx.data_dir_display) == ("0.0.1", "~/x")
    assert ctx == UiContext("0.0.1", "~/x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.version = "2"  # type: ignore[misc]


def test_storage_error_is_an_exception() -> None:
    with pytest.raises(StorageError, match="boom"):
        raise StorageError("boom")


def _imported_modules(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def test_core_imports_only_stdlib_and_itself() -> None:
    stdlib = set(sys.stdlib_module_names)
    for path in sorted(CORE_DIR.glob("*.py")):
        for name in _imported_modules(path):
            top = name.split(".")[0]
            ok = (
                top in stdlib
                or name == "coin_report_trader.core"
                or name.startswith("coin_report_trader.core.")
            )
            assert ok, f"{path.name} imports {name}"
