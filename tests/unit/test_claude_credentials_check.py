"""M2 명세 3.13의 Claude 자격 정보 검사(`scripts/checks/claude_credentials.py`).

`run_all.py`의 7번째 항목이다.

`tmp_path`에 작은 git 저장소(`git init`만 한다. 네트워크를 쓰지 않는다)를 만들고 `src/` 아래에 글을
둔 뒤 검사의 `check(root)`를 부른다. 검사는 커밋하지 않은 새 파일도 본다.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

CHECKS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "checks"
ENV_FILE = "src/coin_report_trader/ai/claude_env.py"
_GIT_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")


@pytest.fixture(scope="module")
def checker() -> Any:
    sys.path.insert(0, str(CHECKS_DIR))  # 검사는 같은 폴더의 personal_info를 import한다
    try:
        spec = importlib.util.spec_from_file_location(
            "claude_credentials_under_test", CHECKS_DIR / "claude_credentials.py"
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(CHECKS_DIR))
    return module


def _repo(root: Path, files: dict[str, str]) -> Path:
    env = {key: value for key, value in os.environ.items() if key not in _GIT_VARIABLES}
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", "-q"],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        timeout=60,
    )
    files = {"src/coin_report_trader/__init__.py": '__version__ = "0.1.0"\n', **files}
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_the_item_is_the_seventh_of_run_all() -> None:
    text = (CHECKS_DIR / "run_all.py").read_text(encoding="utf-8")
    assert '    "webhook_urls",\n    "claude_credentials",\n)' in text


def test_the_title(checker) -> None:
    assert checker.TITLE == "Claude 자격 정보"


def test_the_repository_passes(checker) -> None:
    assert checker.check(CHECKS_DIR.parents[1]) == []


def test_clean_sources_pass(checker, tmp_path: Path) -> None:
    files = {
        "src/coin_report_trader/ai/claude_cli.py": (
            'COMMAND = ("auth", "status")  # 로그인 여부만 본다\n'
            "LOGIN_HINT = '로그인은 사용자가 Claude Code에서 한다'\n"
        ),
        ENV_FILE: 'REMOVED = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")\n',
    }
    assert checker.check(_repo(tmp_path, files)) == []


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ('PATH = "~/.claude/.credentials.json"', "자격 정보 파일의 이름"),
        ('NAME = ".Credentials.JSON"', "자격 정보 파일의 이름"),
        ('ARGS = ["setup-token"]', "setup-token"),
        ("# claude SETUP-TOKEN 을 부른다", "setup-token"),
        ('ARGS = "auth login"', "auth login"),
        ('ARGS = ["auth", "login"]', "auth login"),
        ("ARGS = ('auth','login')", "auth login"),
        ("# 먼저 Auth  Login 을 한다", "auth login"),
    ],
)
def test_the_forbidden_texts_are_found(checker, tmp_path: Path, line: str, kind: str) -> None:
    files = {"src/coin_report_trader/ai/claude_cli.py": f"X = 1\n{line}\n"}
    problems = checker.check(_repo(tmp_path, files))
    assert problems == [f"src/coin_report_trader/ai/claude_cli.py:2: {kind}"]


def test_a_forbidden_text_is_found_even_in_the_env_file(checker, tmp_path: Path) -> None:
    problems = checker.check(_repo(tmp_path, {ENV_FILE: 'LOGIN = ("auth", "login")\n'}))
    assert problems == [f"{ENV_FILE}:1: auth login"]


def test_texts_outside_src_are_not_checked(checker, tmp_path: Path) -> None:
    files = {
        "tests/unit/test_x.py": 'ARGS = ["auth", "login", "setup-token", ".credentials.json"]\n',
        "docs/guide.md": "ANTHROPIC_API_KEY와 setup-token을 쓰지 않는다\n",
    }
    assert checker.check(_repo(tmp_path, files)) == []


@pytest.mark.parametrize("name", [
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AWS_API_KEY",
    "ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_AUTH_TOKEN", "AWS_BEARER_TOKEN_BEDROCK",
    "ANTHROPIC_PROFILE",
])  # fmt: skip
def test_an_auth_variable_outside_the_env_file_is_found(checker, tmp_path: Path, name: str) -> None:
    files = {
        ENV_FILE: f'REMOVED = ("{name}",)\n',
        "src/coin_report_trader/app/runner.py": f'os.environ.pop("{name}", None)\n',
    }
    problems = checker.check(_repo(tmp_path, files))
    assert problems == [
        f"src/coin_report_trader/app/runner.py:1: 인증 환경 변수의 이름({ENV_FILE} 밖)"
    ]


@pytest.mark.parametrize(
    "text",
    [
        "ANTHROPIC_API_KEYS = 1",  # 뒤에 글자가 붙은 다른 이름
        "MY_ANTHROPIC_API_KEY = 1",  # 앞에 글자가 붙은 다른 이름
        "anthropic_api_key = 1",  # 환경 변수 이름은 대소문자를 가린다
        "ANTHROPIC_BASE_URL = 1",  # 인증 변수가 아닌 접속 주소 변수
        "CLAUDE_CODE_EFFORT_LEVEL = 1",
    ],
)
def test_other_names_are_not_auth_variables(checker, tmp_path: Path, text: str) -> None:
    files = {"src/coin_report_trader/app/runner.py": f"{text}\n"}
    assert checker.check(_repo(tmp_path, files)) == []


def test_a_new_file_not_yet_committed_is_checked(checker, tmp_path: Path) -> None:
    root = _repo(tmp_path, {})
    (root / "src" / "coin_report_trader" / "late.txt").write_text("setup-token\n", encoding="utf-8")
    assert checker.check(root) == ["src/coin_report_trader/late.txt:1: setup-token"]


def test_no_source_file_is_a_failure(checker, tmp_path: Path) -> None:
    root = tmp_path
    env = {key: value for key, value in os.environ.items() if key not in _GIT_VARIABLES}
    subprocess.run(
        ["git", "init", "-q"], cwd=root, env=env, check=True, capture_output=True, timeout=60
    )
    (root / "README.md").write_text("글\n", encoding="utf-8")
    assert checker.check(root) == ["src/ 아래에 검사할 글 파일이 하나도 없다"]


def test_run_all_reports_the_item(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"src/coin_report_trader/ai/x.py": 'A = ("auth", "login")\n'})
    checks = root / "scripts" / "checks"
    checks.mkdir(parents=True)
    for name in ("run_all.py", "claude_credentials.py", "personal_info.py"):
        (checks / name).write_bytes((CHECKS_DIR / name).read_bytes())
    script = (
        "import sys; sys.path.insert(0, sys.argv[1]); import run_all; "
        "print(run_all.run_item('claude_credentials', run_all.ROOT))"
    )
    env = {key: value for key, value in os.environ.items() if key not in _GIT_VARIABLES}
    env["PYTHONUTF8"] = "1"
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
        "('Claude 자격 정보', ['src/coin_report_trader/ai/x.py:1: auth login'])"
    )
