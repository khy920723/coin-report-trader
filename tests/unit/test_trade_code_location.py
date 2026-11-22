"""M3 명세 4절 3번의 거래 코드 위치 검사(`scripts/checks/trade_code_location.py`).

`run_all.py`의 8번째 항목이다. `tmp_path`에 `src/coin_report_trader/`의 꼴을 만들어 검사의
`check(root)`를 부른다(검사는 파일만 읽고 git을 쓰지 않는다). 이름과 함수는 모두 만든 것이다.
"""

import importlib.util
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

CHECKS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "checks"
PKG = "src/coin_report_trader"


@pytest.fixture(scope="module")
def checker() -> Any:
    spec = importlib.util.spec_from_file_location(
        "trade_code_location_under_test", CHECKS_DIR / "trade_code_location.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree(root: Path, files: dict[str, str]) -> Path:
    files = {f"{PKG}/__init__.py": '__version__ = "0.0.0"\n', **files}
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    return root


ORDERS = {f"{PKG}/trading/orders/__init__.py": '__all__ = ["decide", "OrderPlan"]\n'}


def test_the_item_is_the_eighth_of_run_all() -> None:
    text = (CHECKS_DIR / "run_all.py").read_text(encoding="utf-8")
    assert '    "claude_credentials",\n    "trade_code_location",\n' in text


def test_the_title_and_the_folders(checker) -> None:
    assert checker.TITLE == "거래 코드 위치"
    assert checker.TRADE_FOLDERS == ("orders", "paper", "risk")


def test_the_repository_passes(checker) -> None:
    assert checker.check(CHECKS_DIR.parents[1]) == []


def test_without_the_trade_folders_it_passes(checker, tmp_path: Path) -> None:
    files = {
        f"{PKG}/trading/__init__.py": "",
        f"{PKG}/trading/quote/__init__.py": '__all__ = ["decide"]\n',  # 세 폴더가 아니다
        f"{PKG}/app/x.py": "def decide():\n    pass\n",
    }
    assert checker.check(_tree(tmp_path, files)) == []


def test_a_missing_package_is_a_failure(checker, tmp_path: Path) -> None:
    assert checker.check(tmp_path) == ["src/coin_report_trader/: 없다"]


@pytest.mark.parametrize("folder", ["orders", "paper", "risk"])
def test_a_function_with_a_trade_name_outside_trading_fails(
    checker, tmp_path: Path, folder: str
) -> None:
    files = {
        f"{PKG}/trading/{folder}/__init__.py": '__all__ = ("stop_line",)\n',
        f"{PKG}/app/round.py": "X = 1\n\n\ndef stop_line(price):\n    return price\n",
    }
    assert checker.check(_tree(tmp_path, files)) == [
        f"{PKG}/app/round.py:4: stop_line은(는) trading/{folder}/의 이름이다"
        "(거래 코드는 trading/ 아래에만 둔다)"
    ]


def test_a_class_and_an_async_function_are_found_in_any_folder_outside_trading(
    checker, tmp_path: Path
) -> None:
    files = {
        **ORDERS,
        f"{PKG}/ui/a.py": "class OrderPlan:\n    pass\n",
        f"{PKG}/core/b.py": "async def decide():\n    pass\n",
    }
    tail = "trading/orders/의 이름이다(거래 코드는 trading/ 아래에만 둔다)"
    assert checker.check(_tree(tmp_path, files)) == [
        f"{PKG}/core/b.py:1: decide은(는) {tail}",
        f"{PKG}/ui/a.py:1: OrderPlan은(는) {tail}",
    ]


def test_a_definition_under_a_module_level_if_or_try_is_module_level(
    checker, tmp_path: Path
) -> None:
    source = """\
        import sys

        if sys.platform == "win32":
            def decide():
                pass
        try:
            pass
        except ImportError:
            class OrderPlan:
                pass
        """
    problems = checker.check(_tree(tmp_path, {**ORDERS, f"{PKG}/app/c.py": source}))
    assert [p.split(": ")[0] for p in problems] == [f"{PKG}/app/c.py:4", f"{PKG}/app/c.py:9"]


def test_methods_nested_functions_variables_and_imports_are_not_checked(
    checker, tmp_path: Path
) -> None:
    source = """\
        from coin_report_trader.trading.orders import decide

        OrderPlan = None


        class AppActions:
            def decide(self):
                pass

            class OrderPlan:
                pass


        def outer():
            def decide():
                pass

            class OrderPlan:
                pass

            return decide, OrderPlan
        """
    assert checker.check(_tree(tmp_path, {**ORDERS, f"{PKG}/app/actions.py": source})) == []


def test_names_not_in_all_and_definitions_inside_trading_pass(checker, tmp_path: Path) -> None:
    files = {
        **ORDERS,
        f"{PKG}/trading/orders/rules.py": (
            "def decide():\n    pass\n\n\nclass OrderPlan:\n    pass\n"
        ),
        f"{PKG}/trading/quote/client.py": "def decide():\n    pass\n",
        f"{PKG}/app/d.py": "def helper():\n    pass\n\n\nclass Other:\n    pass\n",
    }
    assert checker.check(_tree(tmp_path, files)) == []


def test_without_all_the_folder_has_no_names(checker, tmp_path: Path) -> None:
    files = {
        f"{PKG}/trading/paper/__init__.py": '"""설명만 있다."""\n',
        f"{PKG}/trading/risk/rules.py": "__all__ = ['decide']\n",  # 패키지의 __all__이 아니다
        f"{PKG}/app/e.py": "def decide():\n    pass\n",
    }
    assert checker.check(_tree(tmp_path, files)) == []


def test_an_annotated_all_is_read(checker, tmp_path: Path) -> None:
    files = {
        f"{PKG}/trading/risk/__init__.py": '__all__: list[str] = ["sell_all_text"]\n',
        f"{PKG}/app/f.py": "def sell_all_text():\n    pass\n",
    }
    problems = checker.check(_tree(tmp_path, files))
    assert [p.split(": ")[0] for p in problems] == [f"{PKG}/app/f.py:1"]


@pytest.mark.parametrize(
    "text",
    ["__all__ = names()\n", '__all__ = ["decide", NAME]\n', '__all__ = ["a"] + ["b"]\n'],
    ids=["call", "name-in-list", "sum"],
)
def test_an_all_that_is_not_a_literal_list_of_strings_is_a_failure(
    checker, tmp_path: Path, text: str
) -> None:
    files = {f"{PKG}/trading/orders/__init__.py": text}
    assert checker.check(_tree(tmp_path, files)) == [
        f"{PKG}/trading/orders/__init__.py: __all__을 글만 든 목록이나 튜플로 읽지 못했다"
    ]


def test_an_unreadable_file_is_a_failure(checker, tmp_path: Path) -> None:
    files = {**ORDERS, f"{PKG}/app/g.py": "def (:\n"}
    assert checker.check(_tree(tmp_path, files)) == [f"{PKG}/app/g.py: 읽지 못했다(SyntaxError)"]


def test_run_all_reports_the_item(tmp_path: Path) -> None:
    root = _tree(tmp_path, {**ORDERS, f"{PKG}/app/x.py": "def decide():\n    pass\n"})
    checks = root / "scripts" / "checks"
    checks.mkdir(parents=True)
    for name in ("run_all.py", "trade_code_location.py"):
        (checks / name).write_bytes((CHECKS_DIR / name).read_bytes())
    script = (
        "import sys; sys.path.insert(0, sys.argv[1]); import run_all; "
        "print(run_all.run_item('trade_code_location', run_all.ROOT))"
    )
    env = {**os.environ, "PYTHONUTF8": "1"}
    result = subprocess.run(
        [sys.executable, "-c", script, str(checks)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=True,
    )
    assert result.stdout.strip() == (
        "('거래 코드 위치', ['src/coin_report_trader/app/x.py:1: decide은(는) trading/orders/의 "
        "이름이다(거래 코드는 trading/ 아래에만 둔다)'])"
    )
