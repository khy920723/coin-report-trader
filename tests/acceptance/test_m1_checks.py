"""M1 명세 4절 7번: 검사 스크립트의 import 방향 표(3.3)와 웹후크 주소 꼴 검사(3.9).

A11(`test_a11_checks.py`)과 같은 방법으로, 저장소 사본을 `tmp_path`에 만들고 하나를 바꾼 뒤 그
사본의 `run_all.py`를 따로 된 프로세스로 돌린다. 가짜 웹후크 주소는 소스에 통째로 적지 않고 실행
중에 이어 붙여 만든다(3.13).
"""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUN_ALL = Path("scripts") / "checks" / "run_all.py"
PACKAGE = Path("src") / "coin_report_trader"
_GIT_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")


def _clean_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _GIT_VARIABLES}
    env["PYTHONUTF8"] = "1"
    return env


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=_clean_env(), capture_output=True, check=True, timeout=60
    )
    return result.stdout.decode("utf-8", "replace")


@pytest.fixture(scope="module")
def repo_files() -> list[str]:
    listed = _git("ls-files", "-z", "--cached", "--others", "--exclude-standard", cwd=REPO)
    names = [name for name in listed.split("\0") if name and not name.startswith("legacy/")]
    return [name for name in names if (REPO / name).is_file()]


@pytest.fixture
def make_copy(tmp_path: Path, repo_files: list[str]) -> Callable[[str], Path]:
    def factory(name: str) -> Path:
        copy = tmp_path / name
        for relative in repo_files:
            target = copy / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / relative, target)
        _git("-c", "init.defaultBranch=main", "init", "-q", cwd=copy)
        return copy

    return factory


def _run_all(copy: Path) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(RUN_ALL)],
        cwd=copy,
        env=_clean_env(),
        capture_output=True,
        timeout=120,
        check=False,
    )
    return result.returncode, result.stdout.decode("utf-8", "replace")


def _status(output: str) -> dict[str, str]:
    found = {}
    for line in output.splitlines():
        for mark in ("[통과] ", "[실패] "):
            if line.startswith(mark):
                found[line.removeprefix(mark).split(":")[0]] = mark.strip("[] ")
    return found


def _write(copy: Path, relative: str, text: str) -> None:
    path = copy / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- import 방향(M1 명세 3.3) --------------------------------------------------------------

# 표가 허용하는 것: 폴더마다 하나씩 파일을 더한다.
ALLOWED_IMPORTS = {
    "stats/a.py": "from coin_report_trader.core import Candle\n",
    "trading/quote/a.py": "import certifi\nimport httpx\nfrom coin_report_trader import core\n",
    "notify/a.py": "import httpx\nimport certifi\n",
    "vault/a.py": "import keyring\nimport keyring.backends.Windows\n",
    "app/m1.py": (
        "import certifi\nimport httpx\nimport keyring\n"
        "from coin_report_trader import notify, stats, trading, vault\n"
        "from coin_report_trader.storage import database\n"
    ),
}

# 표가 막는 것: (파일, 덧붙이는 줄)
FORBIDDEN_IMPORTS = [
    ("ui/m1_bad.py", "import httpx"),
    ("ui/m1_bad2.py", "from coin_report_trader import trading"),
    ("core/m1_bad.py", "import keyring"),
    ("config/m1_bad.py", "import certifi"),
    ("storage/m1_bad.py", "from coin_report_trader.stats import a"),
    ("stats/m1_bad.py", "from coin_report_trader.storage import database"),
    ("trading/m1_bad.py", "import keyring"),
    ("trading/m1_bad2.py", "from coin_report_trader import ui"),
    ("notify/m1_bad.py", "from coin_report_trader import vault"),
    ("vault/m1_bad.py", "import httpx"),
    ("vault/m1_bad2.py", "from coin_report_trader.notify import a"),
    ("trading/m1_bad3.py", "import requests"),
]


def test_m1_import_table_allows_the_new_folders_and_packages(
    make_copy: Callable[[str], Path],
) -> None:
    copy = make_copy("allowed")
    for relative, text in ALLOWED_IMPORTS.items():
        _write(copy, str(PACKAGE / relative), text)
    code, output = _run_all(copy)
    assert code == 0, output
    assert _status(output)["import 방향"] == "통과"


def test_m1_import_table_rejects_every_other_direction(make_copy: Callable[[str], Path]) -> None:
    copy = make_copy("forbidden")
    for relative, line in FORBIDDEN_IMPORTS:
        _write(copy, str(PACKAGE / relative), f'"""위반 예."""\n\n{line}\n')
    code, output = _run_all(copy)
    assert code == 1, output
    assert _status(output)["import 방향"] == "실패"
    for relative, _line in FORBIDDEN_IMPORTS:
        assert f"{(PACKAGE / relative).as_posix()}:3:" in output, relative
    assert output.count(f"{PACKAGE.as_posix()}/") == len(FORBIDDEN_IMPORTS)


# --- 웹후크 주소 꼴(M1 명세 3.9) ------------------------------------------------------------


def _fake_webhook(
    *, scheme: str = "https://", host: str = "discord.com", path: str = "api/webhooks", **parts: str
) -> str:
    number = parts.get("number", "1" * 9 + "2" * 9)
    token = parts.get("token", ("Ab9-_." * 12)[:64])
    return f"{scheme}{host}/{path}/{number}/{token}"


@pytest.mark.parametrize(
    "written",
    [
        _fake_webhook(),
        _fake_webhook(host="canary." + "discord.com", path="api/v10/webhooks"),
        _fake_webhook(host="ptb." + "discordapp.com"),
        _fake_webhook(host="DISCORD.COM"),
        _fake_webhook(scheme=""),  # 앞의 https://가 없어도 토큰이 드러난다
        _fake_webhook().replace("/", "\\/"),  # JSON에서 이스케이프한 꼴
        _fake_webhook() + "?wait=true",
    ],
    ids=["plain", "canary-v10", "ptb-discordapp", "upper", "no-scheme", "json", "query"],
)
def test_m1_webhook_url_in_a_text_file_fails(
    make_copy: Callable[[str], Path], written: str
) -> None:
    copy = make_copy("webhook")
    target = "docs/notes.md"
    _write(copy, target, f"# 메모\n\n주소: {written}\n")
    code, output = _run_all(copy)
    assert code == 1, output
    assert _status(output)["웹후크 주소"] == "실패"
    assert f"{target}:3:" in output
    assert written not in output  # 찾은 값은 출력하지 않는다
    assert [item for item, status in _status(output).items() if status == "실패"] == ["웹후크 주소"]


def test_m1_webhook_url_in_a_test_file_and_a_workflow_fails(
    make_copy: Callable[[str], Path],
) -> None:
    copy = make_copy("webhook-tests")
    _write(copy, "tests/unit/test_m1_leak.py", f'URL = "{_fake_webhook()}"\n')
    _write(copy, ".github/workflows/m1_leak.yml", f"# {_fake_webhook()}\n")
    code, output = _run_all(copy)
    assert code == 1, output
    assert "tests/unit/test_m1_leak.py:1:" in output
    assert ".github/workflows/m1_leak.yml:1:" in output


@pytest.mark.parametrize(
    "written",
    [
        _fake_webhook(token="a" * 59),  # 토큰이 60자보다 짧다
        _fake_webhook(number="1" * 16),  # 번호가 17자보다 짧다
        _fake_webhook(host="example.com"),
        _fake_webhook(path="api/webhook"),
        "https://" + "discord.com/api/webhooks/<번호>/<토큰>",  # 자리표시
    ],
    ids=["short-token", "short-number", "other-host", "other-path", "placeholder"],
)
def test_m1_text_that_is_not_a_webhook_url_passes(
    make_copy: Callable[[str], Path], written: str
) -> None:
    copy = make_copy("not-webhook")
    _write(copy, "docs/notes.md", f"주소: {written}\n")
    code, output = _run_all(copy)
    assert code == 0, output
    assert _status(output)["웹후크 주소"] == "통과"


def test_m1_webhook_check_skips_legacy_and_license_texts(make_copy: Callable[[str], Path]) -> None:
    copy = make_copy("webhook-excluded")
    _write(copy, "legacy/notes.md", f"{_fake_webhook()}\n")
    _write(copy, "packaging/licenses/m1-note.txt", f"{_fake_webhook()}\n")
    code, output = _run_all(copy)
    assert code == 0, output
    assert _status(output)["웹후크 주소"] == "통과"
