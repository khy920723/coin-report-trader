"""명세 7절 A11: 검사 스크립트(`scripts/checks/run_all.py`)가 어긴 사본을 잡는다.

저장소의 파일(`git ls-files`, 커밋하지 않은 새 파일 포함)을 `tmp_path`에 옮긴 사본마다 하나를
어기게 바꾸고, 그 사본의 `run_all.py`를 따로 된 프로세스로 돌린다. 검사가 보지 않는 `legacy/`는
Windows의 경로 길이를 줄이려고 옮기지 않는다. 사본은 `git init`만 한다(네트워크를 쓰지 않는다).
위반 예(가짜 개인 주소와 경로)는 소스에 통째로 적지 않고 실행 중에 이어 붙여 만든다(명세 3.7절 4번).
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
ITEMS = ["필수 파일", "legacy 제외", "import 방향", "개인정보", "상표"]
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


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def _fake_personal_address() -> str:
    return "kim.min" + "su" + "@" + "personal-mail" + ".net"


def _fake_user_dir() -> str:
    return "C:" + "\\" + "Us" + "ers" + "\\" + "minsu" + "\\Documents"


@pytest.mark.scenario("A11")
def test_a11_untouched_copy_passes(make_copy: Callable[[str], Path]) -> None:
    code, output = _run_all(make_copy("clean"))
    assert code == 0, output
    assert _status(output) == dict.fromkeys(ITEMS, "통과")
    assert output.splitlines()[-1] == "결과: 통과 (5/5)"


@pytest.mark.scenario("A11")
def test_a11_ui_importing_storage_fails(make_copy: Callable[[str], Path]) -> None:
    copy = make_copy("ui-imports-storage")
    target = "src/coin_report_trader/ui/screens.py"
    _append(copy / target, "\nfrom coin_report_trader.storage import database  # 위반 예\n")
    code, output = _run_all(copy)
    assert code == 1, output
    assert _status(output)["import 방향"] == "실패"
    assert target in output
    assert [item for item, status in _status(output).items() if status == "실패"] == ["import 방향"]


@pytest.mark.scenario("A11")
def test_a11_fake_personal_address_fails(make_copy: Callable[[str], Path]) -> None:
    copy = make_copy("personal-address")
    address = _fake_personal_address()
    _append(copy / "README.md", f"\n연락: {address}\n")
    code, output = _run_all(copy)
    assert code == 1, output
    assert _status(output)["개인정보"] == "실패"
    assert "README.md" in output
    assert address not in output  # 찾은 값은 출력하지 않는다


@pytest.mark.scenario("A11")
def test_a11_trademark_in_project_name_fails(make_copy: Callable[[str], Path]) -> None:
    copy = make_copy("trademark")
    pyproject = copy / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert 'name = "coin-report-trader"' in text
    pyproject.write_text(
        text.replace('name = "coin-report-trader"', 'name = "coin-upbit-trader"', 1),
        encoding="utf-8",
    )
    code, output = _run_all(copy)
    assert code == 1, output
    assert _status(output)["상표"] == "실패"
    assert "pyproject.toml" in output


# --- 명세의 세 사본 밖에서 더 본 것 ---------------------------------------------------------


@pytest.mark.scenario("A11")
def test_a11_user_folder_path_in_a_test_file_fails(make_copy: Callable[[str], Path]) -> None:
    copy = make_copy("user-dir")
    target = "tests/unit/test_paths.py"
    _append(copy / target, f"\n# {_fake_user_dir()}\n")
    code, output = _run_all(copy)
    assert code == 1, output
    assert _status(output)["개인정보"] == "실패"
    assert target in output


@pytest.mark.scenario("A11")
def test_a11_placeholders_and_example_addresses_pass(make_copy: Callable[[str], Path]) -> None:
    copy = make_copy("placeholders")
    lines = [
        "C:" + "\\Users\\<이름>",
        "/ho" + "me/<이름>",
        "/Us" + "ers/$USER",
        "someone" + "@" + "example.com",
        "noreply" + "@" + "anthropic.com",
    ]
    _append(copy / "README.md", "\n" + "\n".join(lines) + "\n")
    code, output = _run_all(copy)
    assert code == 0, output


@pytest.mark.scenario("A11")
def test_a11_second_spec_file_fails(make_copy: Callable[[str], Path]) -> None:
    copy = make_copy("two-specs")
    (copy / "packaging").mkdir(exist_ok=True)
    for name in ("one.spec", "two.spec"):
        spec = copy / "packaging" / name
        spec.write_text('exe = EXE(name="coin-report-trader")\n', encoding="utf-8")
    code, output = _run_all(copy)
    assert code == 1, output
    assert _status(output)["필수 파일"] == "실패"
    assert "packaging/" in output


@pytest.mark.scenario("A11")
def test_a11_pytest_collecting_legacy_fails(make_copy: Callable[[str], Path]) -> None:
    copy = make_copy("legacy-collected")
    pyproject = copy / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert 'testpaths = ["tests"]' in text
    pyproject.write_text(
        text.replace('testpaths = ["tests"]', 'testpaths = ["tests", "legacy"]'), encoding="utf-8"
    )
    code, output = _run_all(copy)
    assert code == 1, output
    assert _status(output)["legacy 제외"] == "실패"
