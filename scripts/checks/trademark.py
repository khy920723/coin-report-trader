"""항목 5. 상표: 배포 이름, 실행 이름, exe 이름에 거래소 상표가 없다(제품 규칙 11절).

보는 곳: `pyproject.toml`의 `[project].name`, `[project.gui-scripts]`의 이름, `packaging/` 아래
`.spec`의 파일 이름과 `EXE`·`COLLECT`의 `name`(글자이거나 글자를 담은 이름. `ast`로 읽고 실행하지
않는다). `EXE` 호출마다 글자로 읽히는 `name=`이 있어야 한다(없거나 `**opts`로 넘기면 exe 이름을
확인하지 못하므로 실패다). `EXE` 호출이 없는 `.spec`도 같다.
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
    problems = []
    if MARK.search(path.name):
        problems.append(f"{shown}: .spec 파일 이름에 거래소 상표가 있다")
    try:
        # 바이트로 넘겨 ast가 BOM과 인코딩 선언을 직접 읽게 한다.
        tree = ast.parse(path.read_bytes(), filename=shown)
    except (OSError, SyntaxError, ValueError) as exc:
        return [*problems, f"{shown}: 읽지 못했다({type(exc).__name__})"]
    constants = _string_constants(tree)
    exe_calls = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _call_name(node) in SPEC_CALLS):
            continue
        is_exe = _call_name(node) == "EXE"
        exe_calls += is_exe
        names = [keyword for keyword in node.keywords if keyword.arg == "name"]
        if is_exe and not names:
            problems.append(f"{shown}:{node.lineno}: exe 이름을 확인하지 못했다(name=이 없다)")
        for keyword in names:
            value = _string_value(keyword.value, constants)
            if value is None:
                line = keyword.value.lineno
                problems.append(f"{shown}:{line}: exe 이름을 확인하지 못했다(글자로 읽히지 않는다)")
            elif MARK.search(value):
                problems.append(f"{shown}:{keyword.value.lineno}: exe 이름에 거래소 상표가 있다")
    if exe_calls == 0:
        problems.append(f"{shown}: exe 이름을 확인하지 못했다(EXE 호출이 없다)")
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
