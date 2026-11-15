"""루트 패키지에서는 `__version__`만 import한다(명세 3.3). exe로 묶였을 때 순환과 누락을 막는다."""

import ast
from pathlib import Path

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
