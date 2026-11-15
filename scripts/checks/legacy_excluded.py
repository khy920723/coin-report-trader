"""항목 2. `legacy/` 제외: ruff 설정이 `legacy`를 빼고, pytest `testpaths`가 `["tests"]`다."""

import tomllib
from pathlib import Path
from typing import Any

TITLE = "legacy 제외"


def check(root: Path) -> list[str]:
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return [f"pyproject.toml: 읽지 못했다({type(exc).__name__})"]
    tool = data.get("tool", {})
    problems = []

    ruff = tool.get("ruff", {})
    excludes = [*ruff.get("exclude", []), *ruff.get("extend-exclude", [])]
    if not any(_is_legacy(entry) for entry in excludes):
        problems.append("pyproject.toml: [tool.ruff]의 exclude나 extend-exclude에 legacy가 없다")

    testpaths = _pytest_option(tool.get("pytest", {}), "testpaths")
    if testpaths != ["tests"]:
        problems.append(f'pyproject.toml: pytest testpaths가 ["tests"]가 아니다: {testpaths!r}')
    return problems


def _is_legacy(entry: object) -> bool:
    if not isinstance(entry, str):
        return False
    text = entry.strip().replace("\\", "/").removeprefix("./")
    for suffix in ("/**", "/*", "/"):
        text = text.removesuffix(suffix)
    return text == "legacy"


def _pytest_option(pytest_table: dict[str, Any], name: str) -> object:
    """`[tool.pytest.ini_options]`를 먼저 보고, 없으면 `[tool.pytest]`(pytest 9의 TOML)를 본다."""
    ini = pytest_table.get("ini_options")
    if isinstance(ini, dict) and name in ini:
        return ini[name]
    return pytest_table.get(name)
