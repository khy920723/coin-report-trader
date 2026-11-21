"""루트 패키지에서는 `__version__`만 import한다(명세 3.3). exe로 묶였을 때 순환과 누락을 막는다."""

import ast
import importlib.util
from pathlib import Path
from typing import Any

import pytest

import coin_report_trader

PACKAGE = "coin_report_trader"
SOURCE_DIR = Path(coin_report_trader.__file__).parent


def _package_parts(path: Path) -> list[str]:
    relative = path.relative_to(SOURCE_DIR)
    return [PACKAGE, *relative.parts[:-1]]


def root_imports_other_than_version(source: str, package: list[str]) -> list[tuple[int, str]]:
    """루트 패키지에서 `__version__` 말고 가져오는 import의 (줄, 글)."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, f"import {a.name}") for a in node.names if a.name == PACKAGE)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                target = [*base, *(node.module.split(".") if node.module else [])]
            else:
                target = node.module.split(".") if node.module else []
            if target == [PACKAGE]:
                found.extend(
                    (node.lineno, f"from {PACKAGE} import {a.name}")
                    for a in node.names
                    if a.name != "__version__"
                )
    return found


def test_no_source_file_imports_the_root_package_except_its_version() -> None:
    files = sorted(SOURCE_DIR.rglob("*.py"))
    assert len(files) > 10
    violations = {
        str(path.relative_to(SOURCE_DIR)): found
        for path in files
        if (found := root_imports_other_than_version(path.read_text("utf-8"), _package_parts(path)))
    }
    assert violations == {}


def test_the_rule_catches_each_way_of_importing_the_root_package() -> None:
    sub = [PACKAGE, "app"]
    root = [PACKAGE]
    assert root_imports_other_than_version("import coin_report_trader", sub)
    assert root_imports_other_than_version("import coin_report_trader as crt", sub)
    assert root_imports_other_than_version("from coin_report_trader import core", sub)
    assert root_imports_other_than_version("from coin_report_trader import *", sub)
    assert root_imports_other_than_version("from .. import core", sub)
    assert root_imports_other_than_version("from . import core", root)
    assert root_imports_other_than_version("def f():\n    import coin_report_trader\n", sub)


def test_the_rule_allows_the_version_and_submodules() -> None:
    sub = [PACKAGE, "app"]
    assert root_imports_other_than_version("from coin_report_trader import __version__", sub) == []
    assert root_imports_other_than_version("from coin_report_trader.core import X", sub) == []
    assert root_imports_other_than_version("import coin_report_trader.core", sub) == []
    assert root_imports_other_than_version("from . import paths", sub) == []
    assert root_imports_other_than_version("from .. import __version__", sub) == []
    assert root_imports_other_than_version("import os\nfrom pathlib import Path", sub) == []


# --- 검사 스크립트의 표: M2 명세 3.3의 `ai` 줄 -------------------------------------------------

CHECKS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "checks"


def _import_rules() -> Any:
    spec = importlib.util.spec_from_file_location(
        "import_rules_under_test", CHECKS_DIR / "import_rules.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _problems(tmp_path: Path, files: dict[str, str]) -> list[str]:
    """`src/coin_report_trader/` 아래에 `files`를 두고 import 방향 검사를 돌린 결과."""
    package = tmp_path / "src" / PACKAGE
    for relative, text in files.items():
        path = package / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return _import_rules().check(tmp_path)


def test_the_ai_folder_may_import_core_and_the_standard_library(tmp_path: Path) -> None:
    files = {
        "ai/__init__.py": "",
        "ai/runner.py": (
            "import csv\nimport json\nimport subprocess\nfrom pathlib import Path\n"
            "from coin_report_trader.core import AiChoice\nfrom coin_report_trader import core\n"
            "from coin_report_trader import __version__\nfrom . import claude_env\n"
            "from .claude_env import NAMES\n"
        ),
        "ai/claude_env.py": "NAMES = ()\n",
    }
    assert _problems(tmp_path, files) == []


@pytest.mark.parametrize(
    "line",
    [
        "from coin_report_trader import trading",
        "from coin_report_trader.storage import database",
        "from coin_report_trader.stats import bars",
        "from coin_report_trader import notify",
        "from coin_report_trader.vault import store",
        "from coin_report_trader import ui",
        "from coin_report_trader import config",
        "from coin_report_trader import app",
        "import httpx",
        "import certifi",
        "import keyring",
        "import PySide6",
        "from coin_report_trader import types_that_do_not_exist",
    ],
)
def test_the_ai_folder_may_not_import_anything_else(tmp_path: Path, line: str) -> None:
    problems = _problems(tmp_path, {"ai/bad.py": f'"""위반 예."""\n\n{line}\n'})
    assert len(problems) == 1, problems
    assert problems[0].startswith(f"src/{PACKAGE}/ai/bad.py:3: ")


def test_only_app_may_import_the_ai_folder(tmp_path: Path) -> None:
    line = "from coin_report_trader.ai import runner\n"
    folders = ("core", "stats", "storage", "config", "trading", "notify", "vault", "ui")
    files = {f"{folder}/uses_ai.py": line for folder in folders}
    files["app/uses_ai.py"] = line + "from coin_report_trader import ai\n"
    problems = _problems(tmp_path, files)
    assert sorted(p.split(":")[0] for p in problems) == sorted(
        f"src/{PACKAGE}/{folder}/uses_ai.py" for folder in folders
    )
    assert all("ai/을(를) import하지 않는다" in p for p in problems), problems


def test_the_table_has_the_ai_row() -> None:
    rules = _import_rules()
    assert rules.ALLOWED["ai"] == frozenset({rules.STDLIB, "core"})
    assert "ai" in rules.FOLDERS
    assert "ai" in rules.ALLOWED["app"]
    assert all("ai" not in allowed for owner, allowed in rules.ALLOWED.items() if owner != "app")
