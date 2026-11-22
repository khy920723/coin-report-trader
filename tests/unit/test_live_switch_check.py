"""M5 명세 3.13(4절 5번)의 실거래 허용 값 검사(`scripts/checks/live_switch.py`).

`run_all.py`의 9번째 항목이다. `tmp_path`에 `src/coin_report_trader/`의 꼴을 만들어 검사의
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
LIVE = f"{PKG}/trading/live"
SWITCH = f"{LIVE}/switch.py"
SELECT = f"{LIVE}/select.py"
EXCHANGE = f"{LIVE}/upbit_exchange.py"
_GIT_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")


@pytest.fixture(scope="module")
def checker() -> Any:
    spec = importlib.util.spec_from_file_location(
        "live_switch_under_test", CHECKS_DIR / "live_switch.py"
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


GOOD_SWITCH = "LIVE_TRADING_ALLOWED: Final[bool] = True\n"
GOOD_SELECT = """\
    from coin_report_trader.trading.live.switch import LIVE_TRADING_ALLOWED
    from coin_report_trader.trading.live.upbit_exchange import UpbitExchange


    def select_exchange(mode, allowed, client):
        if not (allowed and LIVE_TRADING_ALLOWED):
            raise RuntimeError("막힘")
        return UpbitExchange(client)
    """
GOOD_EXCHANGE = """\
    class UpbitExchange:
        def __init__(self, client):
            self._client = client

        def place(self, plan):
            return self._client.create_order(plan)

        def cancel(self, uid):
            return self._client.cancel_order(uid)
    """
GOOD = {SWITCH: GOOD_SWITCH, SELECT: GOOD_SELECT, EXCHANGE: GOOD_EXCHANGE}
TAIL_EXCHANGE = "(실거래 연결은 select.py의 select_exchange와 upbit_exchange.py 안에서만 만든다)"
TAIL_ORDER = "(주문을 내고 취소하는 길은 upbit_exchange.py뿐이다)"


def test_the_item_is_the_ninth_and_last_of_run_all() -> None:
    text = (CHECKS_DIR / "run_all.py").read_text(encoding="utf-8")
    assert '    "trade_code_location",\n    "live_switch",\n)' in text


def test_the_title_and_the_approved_value(checker) -> None:
    assert checker.TITLE == "실거래 허용 값"
    assert checker.APPROVED_LIVE_TRADING_ALLOWED is True  # 5단계의 승인 값(3.13)


def test_the_repository_passes(checker) -> None:
    assert checker.check(CHECKS_DIR.parents[1]) == []


def test_a_good_tree_passes(checker, tmp_path: Path) -> None:
    assert checker.check(_tree(tmp_path, GOOD)) == []


# --- 파일이 없으면 통과다(3.13) -----------------------------------------------------------------


def test_without_the_package_or_the_live_files_it_passes(checker, tmp_path: Path) -> None:
    assert checker.check(tmp_path) == []
    assert checker.check(_tree(tmp_path, {f"{PKG}/trading/quote/q.py": "X = 1\n"})) == []


@pytest.mark.parametrize("missing", [SWITCH, SELECT, EXCHANGE])
def test_any_one_file_missing_passes(checker, tmp_path: Path, missing: str) -> None:
    files = {name: text for name, text in GOOD.items() if name != missing}
    assert checker.check(_tree(tmp_path, files)) == []


# --- 1. 값 -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    ["LIVE_TRADING_ALLOWED = False", "LIVE_TRADING_ALLOWED: bool = False"],
    ids=["plain", "annotated"],
)
def test_a_value_different_from_the_approved_one_fails(checker, tmp_path: Path, line: str) -> None:
    files = {**GOOD, SWITCH: f"{line}\n"}
    assert checker.check(_tree(tmp_path, files)) == [
        f"{SWITCH}:1: LIVE_TRADING_ALLOWED가 False다(승인된 값은 True다. "
        "scripts/checks/live_switch.py의 APPROVED_LIVE_TRADING_ALLOWED와 같아야 한다)"
    ]


def test_the_check_follows_the_approved_constant_not_a_fixed_true(
    checker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tree(tmp_path, GOOD)
    monkeypatch.setattr(checker, "APPROVED_LIVE_TRADING_ALLOWED", False)
    problems = checker.check(root)
    assert [p.split(": ")[0] for p in problems] == [f"{SWITCH}:1"]
    assert "True다(승인된 값은 False다" in problems[0]
    files = {**GOOD, SWITCH: "LIVE_TRADING_ALLOWED = False\n"}
    assert checker.check(_tree(tmp_path / "again", files)) == []


@pytest.mark.parametrize(
    "source",
    [
        "LIVE_TRADING_ALLOWED = True\nLIVE_TRADING_ALLOWED = False\n",
        "LIVE_TRADING_ALLOWED = True\nif True:\n    LIVE_TRADING_ALLOWED = False\n",
        "LIVE_TRADING_ALLOWED = True\ntry:\n    pass\nexcept OSError:\n"
        "    LIVE_TRADING_ALLOWED = False\n",
    ],
    ids=["again", "in-if", "in-except"],
)
def test_every_assignment_at_module_level_is_checked(checker, tmp_path: Path, source: str) -> None:
    problems = checker.check(_tree(tmp_path, {**GOOD, SWITCH: source}))
    assert len(problems) == 1
    assert "LIVE_TRADING_ALLOWED가 False다" in problems[0]


@pytest.mark.parametrize(
    "source",
    [
        "LIVE_TRADING_ALLOWED = 1\n",
        "LIVE_TRADING_ALLOWED = 'True'\n",
        "LIVE_TRADING_ALLOWED = not False\n",
        "LIVE_TRADING_ALLOWED = ENABLED\n",
        "LIVE_TRADING_ALLOWED = None\n",
    ],
    ids=["int", "text", "expression", "name", "none"],
)
def test_a_value_that_is_not_a_bool_literal_fails(checker, tmp_path: Path, source: str) -> None:
    problems = checker.check(_tree(tmp_path, {**GOOD, SWITCH: source}))
    assert problems == [
        f"{SWITCH}:1: LIVE_TRADING_ALLOWED의 값을 읽지 못했다(True나 False 글자여야 한다)"
    ]


@pytest.mark.parametrize(
    "source",
    [
        "LIVE_TRADING_ALLOWED = True\nLIVE_TRADING_ALLOWED &= False\n",
        "LIVE_TRADING_ALLOWED = True\nLIVE_TRADING_ALLOWED, other = False, 1\n",
        "LIVE_TRADING_ALLOWED = True\nfor LIVE_TRADING_ALLOWED in (False,):\n    pass\n",
        "LIVE_TRADING_ALLOWED = True\nfrom elsewhere import LIVE_TRADING_ALLOWED\n",
        "LIVE_TRADING_ALLOWED = True\nimport elsewhere as LIVE_TRADING_ALLOWED\n",
        "LIVE_TRADING_ALLOWED = True\ndel LIVE_TRADING_ALLOWED\n",
        "LIVE_TRADING_ALLOWED = True\nwith open('x') as LIVE_TRADING_ALLOWED:\n    pass\n",
    ],
    ids=["augmented", "unpacked", "loop", "from-import", "import-as", "del", "with"],
)
def test_other_ways_to_bind_the_name_fail(checker, tmp_path: Path, source: str) -> None:
    problems = checker.check(_tree(tmp_path, {**GOOD, SWITCH: source}))
    assert len(problems) == 1
    assert problems[0].startswith(f"{SWITCH}:2: LIVE_TRADING_ALLOWED를 글자 값 대입이 아닌 꼴로")


def test_changing_the_value_from_a_function_fails(checker, tmp_path: Path) -> None:
    source = """\
        LIVE_TRADING_ALLOWED = True


        def unlock():
            global LIVE_TRADING_ALLOWED
            LIVE_TRADING_ALLOWED = False
        """
    assert checker.check(_tree(tmp_path, {**GOOD, SWITCH: source})) == [
        f"{SWITCH}:5: 함수 안에서 LIVE_TRADING_ALLOWED를 바꾼다(global)"
    ]


def test_a_local_name_in_a_function_or_class_is_not_the_switch(checker, tmp_path: Path) -> None:
    source = """\
        LIVE_TRADING_ALLOWED = True


        def helper():
            LIVE_TRADING_ALLOWED = False
            return LIVE_TRADING_ALLOWED


        class Holder:
            LIVE_TRADING_ALLOWED = False
        """
    assert checker.check(_tree(tmp_path, {**GOOD, SWITCH: source})) == []


def test_a_switch_file_without_the_name_fails(checker, tmp_path: Path) -> None:
    problems = checker.check(_tree(tmp_path, {**GOOD, SWITCH: "OTHER = True\n"}))
    assert problems == [f"{SWITCH}: LIVE_TRADING_ALLOWED를 모듈 수준에서 글자 값으로 정하지 않았다"]


def test_a_declaration_without_a_value_fails(checker, tmp_path: Path) -> None:
    problems = checker.check(_tree(tmp_path, {**GOOD, SWITCH: "LIVE_TRADING_ALLOWED: bool\n"}))
    assert problems == [f"{SWITCH}: LIVE_TRADING_ALLOWED를 모듈 수준에서 글자 값으로 정하지 않았다"]


# --- 2. 경로: UpbitExchange ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "action"),
    [
        ("ex = UpbitExchange(client)\n", "부른다"),
        ("ex = mod.UpbitExchange(client)\n", "부른다"),
        ("ex = getattr(mod, 'UpbitExchange')(client)\n", "getattr로 꺼낸다"),
        ("class Mine(UpbitExchange):\n    pass\n", "물려받는다"),
        ("class Mine(mod.UpbitExchange):\n    pass\n", "물려받는다"),
    ],
    ids=["call", "attribute-call", "getattr", "subclass", "attribute-subclass"],
)
@pytest.mark.parametrize(
    "where", ["app/wiring.py", "trading/orders/engine.py", "trading/live/x.py"]
)
def test_another_place_making_an_upbit_exchange_fails(
    checker, tmp_path: Path, source: str, action: str, where: str
) -> None:
    files = {**GOOD, f"{PKG}/{where}": f"x = 1\n{source}"}
    assert checker.check(_tree(tmp_path, files)) == [
        f"{PKG}/{where}:2: UpbitExchange를 {action}{TAIL_EXCHANGE}"
    ]


def test_an_import_under_another_name_is_followed(checker, tmp_path: Path) -> None:
    source = """\
        from coin_report_trader.trading.live.upbit_exchange import UpbitExchange as Make

        exchange = Make(None)
        """
    files = {**GOOD, f"{PKG}/app/wiring.py": source}
    assert checker.check(_tree(tmp_path, files)) == [
        f"{PKG}/app/wiring.py:3: UpbitExchange를 부른다{TAIL_EXCHANGE}"
    ]


def test_only_importing_or_naming_the_class_is_not_making_one(checker, tmp_path: Path) -> None:
    source = """\
        from coin_report_trader.trading.live.upbit_exchange import UpbitExchange


        def kind(x: UpbitExchange) -> bool:
            return isinstance(x, UpbitExchange)
        """
    assert checker.check(_tree(tmp_path, {**GOOD, f"{PKG}/app/kinds.py": source})) == []


def test_the_exchange_file_itself_may_make_one(checker, tmp_path: Path) -> None:
    extra = (
        textwrap.dedent(GOOD_EXCHANGE)
        + "\n\ndef again(client):\n    return UpbitExchange(client)\n"
    )
    assert checker.check(_tree(tmp_path, {**GOOD, EXCHANGE: extra})) == []


def test_select_exchange_may_make_one_but_other_code_in_select_py_may_not(
    checker, tmp_path: Path
) -> None:
    source = """\
        from coin_report_trader.trading.live.switch import LIVE_TRADING_ALLOWED
        from coin_report_trader.trading.live.upbit_exchange import UpbitExchange

        EARLY = UpbitExchange(None)


        def select_exchange(mode, allowed, client):
            if not (allowed and LIVE_TRADING_ALLOWED):
                raise RuntimeError("막힘")
            return UpbitExchange(client)


        def helper(client):
            return UpbitExchange(client)
        """
    problems = checker.check(_tree(tmp_path, {**GOOD, SELECT: source}))
    assert problems == [
        f"{SELECT}:4: UpbitExchange를 부른다{TAIL_EXCHANGE}",
        f"{SELECT}:14: UpbitExchange를 부른다{TAIL_EXCHANGE}",
    ]


def test_select_exchange_in_another_file_does_not_count(checker, tmp_path: Path) -> None:
    source = "def select_exchange(client):\n    return UpbitExchange(client)\n"
    files = {**GOOD, f"{PKG}/app/select.py": source}
    assert checker.check(_tree(tmp_path, files)) == [
        f"{PKG}/app/select.py:2: UpbitExchange를 부른다{TAIL_EXCHANGE}"
    ]


# --- 2. 경로: select_exchange가 값을 읽는다 ------------------------------------------------------


def test_select_exchange_not_reading_the_value_fails(checker, tmp_path: Path) -> None:
    source = """\
        from coin_report_trader.trading.live.switch import LIVE_TRADING_ALLOWED
        from coin_report_trader.trading.live.upbit_exchange import UpbitExchange


        def select_exchange(mode, allowed, client):
            if not allowed:
                raise RuntimeError("막힘")
            return UpbitExchange(client)
        """
    assert checker.check(_tree(tmp_path, {**GOOD, SELECT: source})) == [
        f"{SELECT}:5: select_exchange의 몸통이 LIVE_TRADING_ALLOWED를 읽지 않는다"
    ]


def test_reading_the_value_outside_the_body_does_not_count(checker, tmp_path: Path) -> None:
    source = """\
        from coin_report_trader.trading.live.switch import LIVE_TRADING_ALLOWED
        from coin_report_trader.trading.live.upbit_exchange import UpbitExchange

        CACHED = LIVE_TRADING_ALLOWED


        def select_exchange(mode, allowed, client, flag=LIVE_TRADING_ALLOWED):
            return UpbitExchange(client)
        """
    assert checker.check(_tree(tmp_path, {**GOOD, SELECT: source})) == [
        f"{SELECT}:7: select_exchange의 몸통이 LIVE_TRADING_ALLOWED를 읽지 않는다"
    ]


def test_reading_it_through_a_module_attribute_counts(checker, tmp_path: Path) -> None:
    source = """\
        from coin_report_trader.trading.live import switch
        from coin_report_trader.trading.live.upbit_exchange import UpbitExchange


        def select_exchange(mode, allowed, client):
            if not (allowed and switch.LIVE_TRADING_ALLOWED):
                raise RuntimeError("막힘")
            return UpbitExchange(client)
        """
    assert checker.check(_tree(tmp_path, {**GOOD, SELECT: source})) == []


def test_select_py_without_select_exchange_fails(checker, tmp_path: Path) -> None:
    source = "from coin_report_trader.trading.live.switch import LIVE_TRADING_ALLOWED\n"
    assert checker.check(_tree(tmp_path, {**GOOD, SELECT: source})) == [
        f"{SELECT}: select_exchange가 없다"
    ]


@pytest.mark.parametrize(
    ("source", "what"),
    [
        ("LIVE_TRADING_ALLOWED = True\n", "새로 정한다"),
        (
            "def select_exchange(mode, LIVE_TRADING_ALLOWED=True):\n"
            "    return LIVE_TRADING_ALLOWED\n",
            "인자 이름으로 쓴다",
        ),
        ("from os import name as LIVE_TRADING_ALLOWED\n", "붙인 이름으로 쓴다"),
        (
            "def select_exchange(mode):\n"
            "    LIVE_TRADING_ALLOWED = True\n"
            "    return LIVE_TRADING_ALLOWED\n",
            "새로 정한다",
        ),
    ],
    ids=["module-assignment", "argument", "import-as", "local-assignment"],
)
def test_select_py_making_its_own_value_fails(
    checker, tmp_path: Path, source: str, what: str
) -> None:
    body = (
        source
        if "def select_exchange" in source
        else f"{source}\n\ndef select_exchange(mode):\n    return LIVE_TRADING_ALLOWED\n"
    )
    problems = checker.check(_tree(tmp_path, {**GOOD, SELECT: body}))
    assert len(problems) == 1
    assert what in problems[0]
    assert problems[0].startswith(f"{SELECT}:")


# --- 2. 경로: create_order와 cancel_order -------------------------------------------------------


@pytest.mark.parametrize("name", ["create_order", "cancel_order"])
@pytest.mark.parametrize(
    "source",
    [
        "def f(client):\n    return client.NAME(1)\n",
        "def f(client):\n    return run(client.NAME, 1)\n",
        "def f(client):\n    return getattr(client, 'NAME')(1)\n",
        "from elsewhere import NAME\n\n\ndef f():\n    return NAME(1)\n",
    ],
    ids=["call", "reference", "getattr", "imported-function"],
)
@pytest.mark.parametrize(
    "where", ["app/wiring.py", "trading/orders/engine.py", "trading/upbit/x.py"]
)
def test_calling_the_order_functions_anywhere_else_fails(
    checker, tmp_path: Path, name: str, source: str, where: str
) -> None:
    text = source.replace("NAME", name)
    problems = checker.check(_tree(tmp_path, {**GOOD, f"{PKG}/{where}": text}))
    assert len(problems) == 1
    assert problems[0].startswith(f"{PKG}/{where}:")
    assert f": {name}를 부르거나 가져간다{TAIL_ORDER}" in problems[0]


def test_defining_the_order_functions_and_other_names_is_not_calling_them(
    checker, tmp_path: Path
) -> None:
    source = """\
        class UpbitClient:
            def create_order(self, plan):
                return plan

            def cancel_order(self, uid):
                return uid

            def order_chance(self, market):
                return market


        def other(client):
            return client.order_chance("KRW-AAA")
        """
    files = {**GOOD, f"{PKG}/trading/upbit/client.py": source}
    assert checker.check(_tree(tmp_path, files)) == []


def test_the_order_functions_may_be_called_in_the_exchange_file(checker, tmp_path: Path) -> None:
    assert checker.check(_tree(tmp_path, GOOD)) == []  # GOOD_EXCHANGE가 둘 다 부른다


# --- 읽지 못하는 파일, 보고서의 꼴 --------------------------------------------------------------


def test_a_file_that_cannot_be_parsed_is_a_failure(checker, tmp_path: Path) -> None:
    files = {**GOOD, f"{PKG}/app/broken.py": "def (:\n"}
    assert checker.check(_tree(tmp_path, files)) == [
        f"{PKG}/app/broken.py: 읽지 못했다(SyntaxError)"
    ]


def test_problems_come_in_one_run_with_their_files_and_lines(checker, tmp_path: Path) -> None:
    files = {
        **GOOD,
        SWITCH: "LIVE_TRADING_ALLOWED = False\n",
        f"{PKG}/app/wiring.py": "ex = UpbitExchange(None)\nclient.create_order(1)\n",
    }
    problems = checker.check(_tree(tmp_path, files))
    assert [p.split(": ")[0] for p in problems] == [
        f"{SWITCH}:1",
        f"{PKG}/app/wiring.py:1",
        f"{PKG}/app/wiring.py:2",
    ]


def test_run_all_reports_the_item_with_the_failure(tmp_path: Path) -> None:
    checks = tmp_path / "scripts" / "checks"
    checks.mkdir(parents=True)
    for name in ("run_all.py", "live_switch.py"):
        (checks / name).write_bytes((CHECKS_DIR / name).read_bytes())
    _tree(tmp_path, {**GOOD, SWITCH: "LIVE_TRADING_ALLOWED = False\n"})
    script = (
        "import sys; sys.path.insert(0, sys.argv[1]); import run_all; "
        "print(run_all.run_item('live_switch', run_all.ROOT))"
    )
    env = {key: value for key, value in os.environ.items() if key not in _GIT_VARIABLES}
    env["PYTHONUTF8"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", script, str(checks)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=True,
    )
    assert result.stdout.strip().startswith("('실거래 허용 값', ['")
    assert f"{SWITCH}:1: LIVE_TRADING_ALLOWED가 False다" in result.stdout
