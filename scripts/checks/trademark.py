"""항목 5. 상표: 배포 이름, 실행 이름, exe 이름에 거래소 상표가 없다(제품 규칙 11절).

보는 곳: `pyproject.toml`의 `[project].name`, `[project.gui-scripts]`의 이름, `packaging/` 아래
`.spec`의 `EXE`·`COLLECT`의 `name`(글자이거나 글자를 담은 이름. `ast`로 읽고 실행하지 않는다).
"""

import ast
import re
import tomllib
from pathlib import Path

TITLE = "상표"

MARK = re.compile("upbit|업비트", re.IGNORECASE)
SPEC_CALLS = ("EXE", "COLLECT")


def check(root: Path) -> list[str]:
    problems = _pyproject(root)
    packaging = root / "packaging"
    if packaging.is_dir():
        for spec in sorted(packaging.rglob("*.spec")):
            problems += _spec(spec, spec.relative_to(root).as_posix())
    return problems


def _pyproject(root: Path) -> list[str]:
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return [f"pyproject.toml: 읽지 못했다({type(exc).__name__})"]
    project = data.get("project", {})
    problems = []
    if MARK.search(str(project.get("name", ""))):
        problems.append("pyproject.toml: [project].name에 거래소 상표가 있다")
    for name in project.get("gui-scripts", {}):
        if MARK.search(name):
            problems.append("pyproject.toml: [project.gui-scripts]의 이름에 거래소 상표가 있다")
    return problems


def _spec(path: Path, shown: str) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=shown)
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        return [f"{shown}: 읽지 못했다({type(exc).__name__})"]
    constants = _string_constants(tree)
    problems = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _call_name(node) in SPEC_CALLS):
            continue
        for keyword in node.keywords:
            if keyword.arg != "name":
                continue
            value = _string_value(keyword.value, constants)
            if value is None:
                problems.append(f"{shown}:{keyword.value.lineno}: exe 이름을 글자로 읽지 못했다")
            elif MARK.search(value):
                problems.append(f"{shown}:{keyword.value.lineno}: exe 이름에 거래소 상표가 있다")
    return problems


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _string_constants(tree: ast.Module) -> dict[str, str]:
    """모듈 맨 위에서 글자 하나를 담은 이름(`APP = "..."`, `APP: str = "..."`)."""
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found.update((t.id, value.value) for t in targets if isinstance(t, ast.Name))
    return found


def _string_value(node: ast.expr, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None
