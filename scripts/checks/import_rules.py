"""항목 3. import 방향: `src/coin_report_trader/`의 `.py`를 `ast`로 읽어 명세 3.3절의 표와 맞춘다.

| 폴더 | import해도 되는 것 |
|---|---|
| `core` | 표준 라이브러리 |
| `config`, `storage` | `core`, 표준 라이브러리 |
| `ui` | `core`, PySide6, 표준 라이브러리 |
| `trading` | `core`, 표준 라이브러리 |
| `app` | 위 전부 |
| `__main__.py` | 표준 라이브러리. `app`은 함수 안에서만 |
| 루트 `__init__.py` | 표준 라이브러리 |

어느 폴더든 같은 폴더 안의 모듈(상대 import 포함)과 루트의 `__version__`은 import해도 된다.
루트에서는 `app`도 `__version__`만 가져온다(표의 "위 전부"는 위 줄의 폴더들이다).
표에 없는 폴더나 루트의 다른 모듈은 실패로 적는다(새 폴더는 그 마일스톤 명세가 표에 더한다).
"""

import ast
import sys
from pathlib import Path

TITLE = "import 방향"

PACKAGE = "coin_report_trader"
STDLIB = "표준 라이브러리"
PYSIDE = "PySide6"
FOLDERS = ("core", "config", "storage", "ui", "trading", "app")

# 폴더(또는 루트 파일) → import해도 되는 것. 패키지 안의 것은 폴더 이름으로 적는다.
ALLOWED: dict[str, frozenset[str]] = {
    "core": frozenset({STDLIB}),
    "config": frozenset({STDLIB, "core"}),
    "storage": frozenset({STDLIB, "core"}),
    "ui": frozenset({STDLIB, "core", PYSIDE}),
    "trading": frozenset({STDLIB, "core"}),
    "app": frozenset({STDLIB, PYSIDE, "core", "config", "storage", "ui", "trading", "app"}),
    "__main__": frozenset({STDLIB, "app"}),
    "__init__": frozenset({STDLIB}),
}
# 모든 폴더가 import해도 되는 루트의 이름.
ROOT_NAMES = frozenset({"__version__"})


def check(root: Path) -> list[str]:
    package_dir = root / "src" / PACKAGE
    if not package_dir.is_dir():
        return [f"src/{PACKAGE}/: 없다"]
    problems = []
    for path in sorted(package_dir.rglob("*.py")):
        shown = path.relative_to(root).as_posix()
        owner = _owner(path.relative_to(package_dir))
        if owner is None:
            problems.append(f"{shown}: 명세 3.3절의 표에 없는 자리다")
            continue
        try:
            # 바이트로 넘겨 ast가 BOM과 인코딩 선언을 직접 읽게 한다.
            tree = ast.parse(path.read_bytes(), filename=shown)
        except (OSError, SyntaxError, ValueError) as exc:
            problems.append(f"{shown}: 읽지 못했다({type(exc).__name__})")
            continue
        module = _module_name(path.relative_to(package_dir))
        for line, target, in_function in _imports(tree, module, path.name == "__init__.py"):
            problem = _problem(owner, target, in_function)
            if problem is not None:
                problems.append(f"{shown}:{line}: {problem}")
    return problems


def _owner(relative: Path) -> str | None:
    """파일이 속한 칸: 폴더 이름, 또는 루트의 `__main__`·`__init__`. 표에 없으면 None."""
    parts = relative.parts
    if len(parts) == 1:
        stem = relative.stem
        return stem if stem in ("__main__", "__init__") else None
    return parts[0] if parts[0] in FOLDERS else None


def _module_name(relative: Path) -> str:
    parts = [PACKAGE, *relative.with_suffix("").parts]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(tree: ast.Module, module: str, is_package: bool) -> list[tuple[int, str, bool]]:
    """(줄, 가져오는 것, 함수 안인지). 가져오는 것은 절대 모듈 이름이다.

    `from 패키지 import 이름`은 `패키지.이름`으로 본다(이름이 하위 모듈일 수 있다).
    `is_package`는 파일이 `__init__.py`인지다(상대 import의 기준이 달라진다).
    """
    found: list[tuple[int, str, bool]] = []

    def visit(node: ast.AST, in_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            inside = in_function or isinstance(
                child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
            )
            if isinstance(child, ast.Import):
                found.extend((child.lineno, alias.name, in_function) for alias in child.names)
            elif isinstance(child, ast.ImportFrom):
                base = _resolve(child, module, is_package)
                for alias in child.names:
                    found.append((child.lineno, f"{base}.{alias.name}", in_function))
            visit(child, inside)

    visit(tree, False)
    return found


def _resolve(node: ast.ImportFrom, module: str, is_package: bool) -> str:
    if node.level == 0:
        return node.module or ""
    parts = module.split(".")
    # 모듈 파일이면 상대 import의 기준은 그 모듈이 든 패키지다.
    base = parts if is_package else parts[:-1]
    if node.level > 1:
        base = base[: len(base) - (node.level - 1)]
    return ".".join([*base, node.module] if node.module else base)


def _problem(owner: str, target: str, in_function: bool) -> str | None:
    kind = _kind(target)
    if kind is None:
        return f"{target}: 명세 3.3절이 허용하지 않는 외부 패키지다"
    if kind == "root":
        name = target.split(".")[1] if target.count(".") >= 1 else ""
        if name in ROOT_NAMES:
            return None
        return f"{target}: 루트 {PACKAGE}에서는 __version__만 가져온다"
    if kind == owner:
        return None  # 같은 폴더 안의 모듈
    if kind not in ALLOWED[owner]:
        return f"{target}: {_owner_text(owner)}에서 {_kind_text(kind)}을(를) import하지 않는다"
    if owner == "__main__" and kind == "app" and not in_function:
        return f"{target}: __main__.py는 app을 함수 안에서만 import한다"
    return None


def _kind(target: str) -> str | None:
    """가져오는 것의 칸: 표준 라이브러리, PySide6, 패키지의 폴더 이름, "root", 또는 None(그 밖)."""
    parts = target.split(".")
    top = parts[0]
    if top == PACKAGE:
        if len(parts) >= 2 and parts[1] in FOLDERS:
            return parts[1]
        return "root"
    if top == PYSIDE:
        return PYSIDE
    if top in sys.stdlib_module_names:
        return STDLIB
    return None


def _owner_text(owner: str) -> str:
    if owner in ("__main__", "__init__"):
        return f"루트 {owner}.py"
    return f"{owner}/"


def _kind_text(kind: str) -> str:
    return kind if kind in (STDLIB, PYSIDE) else f"{kind}/"
