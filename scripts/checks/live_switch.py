"""항목 9. 실거래 허용 값: 실거래로 가는 길이 한곳이고 허용 값이 승인된 값이다.

M5 명세 3.13(4절 5번)이다. 아래 둘을 모두 본다. 표준 라이브러리만 쓰고 `ast`로 읽는다.

1. 값: `src/coin_report_trader/trading/live/switch.py`의 `LIVE_TRADING_ALLOWED`(모듈 수준에서
   글자 `True`나 `False`로 한 번 이상 정함)가 아래 `APPROVED_LIVE_TRADING_ALLOWED`와 같다.
   값을 바꾸는 PR은 이 줄도 함께 바꾸므로 품질·빌드와 지휘가 둘을 같이 본다(사용자 결정
   (2026-11-22)). 글자가 아닌 값과 읽지 못하는 꼴(튜플로 풀어 받기, `+=`, 반복문의 이름,
   `import`, 함수에서 `global`로 바꾸기)도 실패다.
2. 경로:
   - `UpbitExchange`를 부르거나 만드는 코드(이름 부르기, 가져오며 바꾼 이름 부르기,
     `모듈.UpbitExchange` 부르기, 물려받기, `getattr(…, "UpbitExchange")`)가
     `trading/live/select.py`의 `select_exchange` 몸통과 `trading/live/upbit_exchange.py`
     안에만 있다.
   - `select_exchange`의 몸통이 `LIVE_TRADING_ALLOWED`를 읽는다(이름이나
     `모듈.LIVE_TRADING_ALLOWED`). `select.py`가 같은 이름을 새로 정하거나(대입, 인자, `as`로
     바꿔 가져오기) 몸통에 이름이 없으면 실패다.
   - `trading/upbit/`의 `create_order`·`cancel_order`를 부르는 곳이
     `trading/live/upbit_exchange.py`뿐이다. 부르는 것만이 아니라 함수로 넘기려고 쓴
     `객체.create_order`도 같다(`getattr(…, "create_order")` 포함).

파일이 없으면 통과다(`switch.py`가 없으면 값을, `select.py`가 없으면 `select_exchange`를
보지 않는다. 1번과 7번 전에도 main이 통과하게). `src/coin_report_trader/`가 없는 것은 "필수 파일"
항목이 잡으므로 여기서는 통과다. 읽지 못하는 `.py`(문법 오류)는 이 항목에서도 실패다. 한계: 이름을
글자로 이어 붙여 만드는 `getattr`, `importlib`로 가져오는 것, `src/` 밖의 코드는 보지 않는다.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

TITLE = "실거래 허용 값"

APPROVED_LIVE_TRADING_ALLOWED = True
"""승인된 실거래 허용 값. `switch.py`의 값이 이것과 같아야 한다(5단계에는 `True`)."""

PACKAGE = "coin_report_trader"
LIVE_DIR = f"src/{PACKAGE}/trading/live"
SWITCH_FILE = f"{LIVE_DIR}/switch.py"
SELECT_FILE = f"{LIVE_DIR}/select.py"
EXCHANGE_FILE = f"{LIVE_DIR}/upbit_exchange.py"

FLAG = "LIVE_TRADING_ALLOWED"
EXCHANGE_CLASS = "UpbitExchange"
SELECT_FUNCTION = "select_exchange"
ORDER_METHODS = ("create_order", "cancel_order")

_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def check(root: Path) -> list[str]:
    package_dir = root / "src" / PACKAGE
    if not package_dir.is_dir():
        return []
    problems: list[str] = []
    trees: dict[str, ast.Module] = {}
    for path in sorted(package_dir.rglob("*.py")):
        shown = path.relative_to(root).as_posix()
        try:
            trees[shown] = ast.parse(path.read_bytes(), filename=shown)
        except (OSError, SyntaxError, ValueError) as exc:
            problems.append(f"{shown}: 읽지 못했다({type(exc).__name__})")
    if SWITCH_FILE in trees:
        problems.extend(_value_problems(SWITCH_FILE, trees[SWITCH_FILE]))
    allowed_lines = _select_function_lines(trees.get(SELECT_FILE))
    for shown, tree in trees.items():
        problems.extend(_exchange_problems(shown, tree, allowed_lines))
        problems.extend(_order_problems(shown, tree))
    if SELECT_FILE in trees:
        problems.extend(_select_problems(SELECT_FILE, trees[SELECT_FILE]))
    return problems


# --- 1. 값 -------------------------------------------------------------------------------------


def _value_problems(shown: str, tree: ast.Module) -> list[str]:
    problems: list[str] = []
    values: list[tuple[int, ast.expr | None]] = []
    handled: set[int] = set()
    for node in _module_level(tree):
        if isinstance(node, ast.Assign):
            names = [t for t in node.targets if isinstance(t, ast.Name) and t.id == FLAG]
            if names:
                values.append((node.lineno, node.value))
                handled.update(id(t) for t in names)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == FLAG
        ):
            handled.add(id(node.target))
            if node.value is not None:
                values.append((node.lineno, node.value))
    for node in _module_level(tree):
        if _binds_flag(node) and id(node) not in handled:
            problems.append(f"{shown}:{node.lineno}: {FLAG}를 글자 값 대입이 아닌 꼴로 바꾼다")
    for node in ast.walk(tree):
        if isinstance(node, ast.Global) and FLAG in node.names:
            problems.append(f"{shown}:{node.lineno}: 함수 안에서 {FLAG}를 바꾼다(global)")
    for lineno, value in values:
        if not (isinstance(value, ast.Constant) and isinstance(value.value, bool)):
            problems.append(
                f"{shown}:{lineno}: {FLAG}의 값을 읽지 못했다(True나 False 글자여야 한다)"
            )
        elif value.value is not APPROVED_LIVE_TRADING_ALLOWED:
            problems.append(
                f"{shown}:{lineno}: {FLAG}가 {value.value}다"
                f"(승인된 값은 {APPROVED_LIVE_TRADING_ALLOWED}다. "
                "scripts/checks/live_switch.py의 APPROVED_LIVE_TRADING_ALLOWED와 같아야 한다)"
            )
    if not values and not problems:
        problems.append(f"{shown}: {FLAG}를 모듈 수준에서 글자 값으로 정하지 않았다")
    return problems


def _binds_flag(node: ast.AST) -> bool:
    """`FLAG` 이름을 대입(글자 값 대입이 아닌 꼴)·삭제·반복·가져오기로 묶는 노드."""
    if isinstance(node, ast.Name):
        return node.id == FLAG and isinstance(node.ctx, ast.Store | ast.Del)
    if isinstance(node, ast.alias):
        return (node.asname or node.name.split(".")[0]) == FLAG
    return False


def _module_level(tree: ast.Module) -> Iterator[ast.AST]:
    """모듈 수준의 모든 노드. 함수·클래스·람다의 안으로는 들어가지 않는다(그 이름은 따로 놓인다)."""
    stack: list[ast.AST] = list(reversed(tree.body))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, _SCOPES):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


# --- 2. 경로 -----------------------------------------------------------------------------------


def _select_function_lines(tree: ast.Module | None) -> tuple[int, int] | None:
    """`select_exchange`의 몸통이 차지한 줄(첫 줄, 끝 줄). 서명과 기본값은 몸통이 아니다."""
    function = _select_function(tree)
    if function is None or not function.body:
        return None
    return function.body[0].lineno, function.end_lineno or function.body[0].lineno


def _select_function(tree: ast.Module | None) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    if tree is None:
        return None
    for node in tree.body:
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == SELECT_FUNCTION
        ):
            return node
    return None


def _exchange_problems(
    shown: str, tree: ast.Module, select_lines: tuple[int, int] | None
) -> list[str]:
    if shown == EXCHANGE_FILE:
        return []
    names = {EXCHANGE_CLASS}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(a.asname for a in node.names if a.name == EXCHANGE_CLASS and a.asname)
    problems: list[str] = []
    for node in ast.walk(tree):
        action = _exchange_use(node, names)
        if action is None:
            continue
        line = node.lineno
        inside = select_lines is not None and select_lines[0] <= line <= select_lines[1]
        if shown == SELECT_FILE and inside:
            continue
        problems.append(
            f"{shown}:{line}: {EXCHANGE_CLASS}를 {action}"
            f"(실거래 연결은 {SELECT_FILE.rsplit('/', 1)[1]}의 {SELECT_FUNCTION}와 "
            f"{EXCHANGE_FILE.rsplit('/', 1)[1]} 안에서만 만든다)"
        )
    return problems


def _exchange_use(node: ast.AST, names: set[str]) -> str | None:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in names:
            return "부른다"
        if isinstance(func, ast.Attribute) and func.attr == EXCHANGE_CLASS:
            return "부른다"
        if _getattr_of(node, (EXCHANGE_CLASS,)):
            return "getattr로 꺼낸다"
    if isinstance(node, ast.ClassDef):
        for base in node.bases:
            if (isinstance(base, ast.Name) and base.id in names) or (
                isinstance(base, ast.Attribute) and base.attr == EXCHANGE_CLASS
            ):
                return "물려받는다"
    return None


def _order_problems(shown: str, tree: ast.Module) -> list[str]:
    if shown == EXCHANGE_FILE:
        return []
    problems: list[str] = []
    for node in ast.walk(tree):
        name = _order_use(node)
        if name is not None:
            problems.append(
                f"{shown}:{node.lineno}: {name}를 부르거나 가져간다"
                f"(주문을 내고 취소하는 길은 {EXCHANGE_FILE.rsplit('/', 1)[1]}뿐이다)"
            )
    return problems


def _order_use(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute) and node.attr in ORDER_METHODS:
        return node.attr
    if isinstance(node, ast.Name) and node.id in ORDER_METHODS and isinstance(node.ctx, ast.Load):
        return node.id
    if isinstance(node, ast.Call):
        found = _getattr_of(node, ORDER_METHODS)
        if found:
            return found
    return None


def _getattr_of(call: ast.Call, names: tuple[str, ...]) -> str | None:
    """`getattr(객체, "이름")`(`hasattr`·`setattr`은 아니다)의 글자 이름."""
    if not (isinstance(call.func, ast.Name) and call.func.id == "getattr"):
        return None
    if len(call.args) >= 2:
        key = call.args[1]
        if isinstance(key, ast.Constant) and key.value in names:
            return str(key.value)
    return None


def _select_problems(shown: str, tree: ast.Module) -> list[str]:
    function = _select_function(tree)
    if function is None:
        return [f"{shown}: {SELECT_FUNCTION}가 없다"]
    problems: list[str] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Name) and node.id == FLAG and isinstance(node.ctx, ast.Store):
            problems.append(
                f"{shown}:{line}: {FLAG}를 이 파일에서 새로 정한다(switch.py의 값을 읽어야 한다)"
            )
        elif isinstance(node, ast.arg) and node.arg == FLAG:
            problems.append(
                f"{shown}:{line}: {FLAG}를 인자 이름으로 쓴다(switch.py의 값을 읽어야 한다)"
            )
        elif isinstance(node, ast.alias) and node.asname == FLAG:
            problems.append(f"{shown}:{line}: {FLAG}를 다른 값에 붙인 이름으로 쓴다")
    if not _reads_flag(function):
        problems.append(
            f"{shown}:{function.lineno}: {SELECT_FUNCTION}의 몸통이 {FLAG}를 읽지 않는다"
        )
    return problems


def _reads_flag(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for statement in function.body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Name) and node.id == FLAG and isinstance(node.ctx, ast.Load):
                return True
            if isinstance(node, ast.Attribute) and node.attr == FLAG:
                return True
    return False
