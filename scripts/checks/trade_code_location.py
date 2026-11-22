"""항목 8. 거래 코드 위치: 주문·모의매매·위험의 이름을 `trading/` 밖에서 정의하지 않는다.

M3 명세 4절 3번이다.

M0 명세 3.7의 "거래 코드 위치의 세부 검사는 `trading/`이 생기는 M3 명세에서 더한다"를 채운다.

- 이름: `src/coin_report_trader/trading/`의 `orders/`, `paper/`, `risk/` 폴더마다 그 패키지의
  `__init__.py`가 모듈 수준에서 `__all__`에 넣은 글. `__all__`은 글만 든 목록이나 튜플이어야 한다
  (`ast`로 읽는다. 셈이 든 `__all__`은 읽지 못한 것으로 적는다). 폴더가 없거나 `__init__.py`가
  없거나 `__all__`이 없으면 그 폴더의 이름은 없다(통과).
- 보는 곳: `src/coin_report_trader/` 아래에서 `trading/` 밖의 모든 `.py`.
- 실패: 그 모듈이 모듈 수준(맨 위. 모듈 수준의 `if`, `try`, `with` 등의 안도 모듈 수준이다)에서
  위 이름과 같은 이름의 함수(`def`, `async def`)나 클래스를 정의함. 함수와 클래스 안의 정의(예:
  `app/actions.py`의 `AppActions.pause`)는 보지 않는다. 함수나 클래스가 아닌 이름(변수, import로
  가져온 이름)도 보지 않는다.
- 파일, 줄, 이름, 그 이름이 든 폴더를 쓴다. 표준 라이브러리만 쓴다.
"""

import ast
from pathlib import Path

TITLE = "거래 코드 위치"

PACKAGE = "coin_report_trader"
TRADING = "trading"
TRADE_FOLDERS = ("orders", "paper", "risk")

_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def check(root: Path) -> list[str]:
    package_dir = root / "src" / PACKAGE
    if not package_dir.is_dir():
        return [f"src/{PACKAGE}/: 없다"]
    problems: list[str] = []
    owners = _trade_names(root, package_dir, problems)
    if not owners:
        return problems
    trading_dir = package_dir / TRADING
    for path in sorted(package_dir.rglob("*.py")):
        if path.is_relative_to(trading_dir):
            continue
        shown = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_bytes(), filename=shown)
        except (OSError, SyntaxError, ValueError) as exc:
            problems.append(f"{shown}: 읽지 못했다({type(exc).__name__})")
            continue
        for node in _module_level_definitions(tree.body):
            folder = owners.get(node.name)
            if folder is not None:
                problems.append(
                    f"{shown}:{node.lineno}: {node.name}은(는) {TRADING}/{folder}/의 이름이다"
                    f"(거래 코드는 {TRADING}/ 아래에만 둔다)"
                )
    return problems


def _trade_names(root: Path, package_dir: Path, problems: list[str]) -> dict[str, str]:
    """이름 → 그 이름을 `__all__`에 넣은 폴더. 읽지 못한 `__all__`은 `problems`에 적는다."""
    owners: dict[str, str] = {}
    for folder in TRADE_FOLDERS:
        init = package_dir / TRADING / folder / "__init__.py"
        if not init.is_file():
            continue
        shown = init.relative_to(root).as_posix()
        try:
            tree = ast.parse(init.read_bytes(), filename=shown)
        except (OSError, SyntaxError, ValueError) as exc:
            problems.append(f"{shown}: 읽지 못했다({type(exc).__name__})")
            continue
        names = _all_names(tree)
        if names is None:
            problems.append(f"{shown}: __all__을 글만 든 목록이나 튜플로 읽지 못했다")
            continue
        for name in names:
            owners.setdefault(name, folder)
    return owners


def _all_names(tree: ast.Module) -> list[str] | None:
    """모듈 수준의 `__all__ = [...]`(또는 튜플, 타입을 붙인 꼴)의 글.

    없으면 빈 목록, 못 읽으면 None이다.
    """
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            continue
        value = node.value
        if not isinstance(value, ast.List | ast.Tuple):
            return None
        for item in value.elts:
            if not (isinstance(item, ast.Constant) and isinstance(item.value, str)):
                return None
            names.append(item.value)
    return names


def _module_level_definitions(body: list[ast.stmt]) -> list[ast.stmt]:
    """모듈 수준의 함수·클래스 정의. 함수와 클래스의 안으로는 들어가지 않는다."""
    found: list[ast.stmt] = []
    for node in body:
        if isinstance(node, _DEFINITIONS):
            found.append(node)
            continue
        for field in ("body", "orelse", "finalbody", "handlers", "cases"):
            inner = getattr(node, field, None)
            if not isinstance(inner, list):
                continue
            for part in inner:
                if isinstance(part, ast.ExceptHandler | ast.match_case):
                    found.extend(_module_level_definitions(part.body))
                elif isinstance(part, ast.stmt):
                    found.extend(_module_level_definitions([part]))
    return found
