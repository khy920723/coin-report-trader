"""판 번호와 Windows 실행 파일의 판 정보 값(M1 명세 3.15, REQ-MAINT-009·010).

네 곳의 판이 같고 REQ-MAINT-010의 꼴인지 본다: `pyproject.toml`의 `version`, `uv.lock`의
`coin-report-trader` 줄의 `version`, 루트의 `__version__`, `version_fields(__version__)`의 값.
`packaging/version_info.py`는 PyPI의 `packaging` 패키지와 이름이 겹치므로 파일 경로로 읽는다.
"""

import ast
import importlib.util
import re
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

import coin_report_trader

ROOT = Path(__file__).resolve().parents[2]
VERSION_INFO = ROOT / "packaging" / "version_info.py"
# REQ-MAINT-009·010: MAJOR.MINOR.PATCH, 숫자 앞에 0이 없음, v 없음.
FORM = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
STRINGS = (
    "ProductName",
    "FileDescription",
    "InternalName",
    "OriginalFilename",
    "ProductVersion",
    "FileVersion",
)


def load_version_info() -> ModuleType:
    spec = importlib.util.spec_from_file_location("version_info_under_test", VERSION_INFO)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def version_info() -> ModuleType:
    return load_version_info()


def pyproject_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)["project"]["version"]


def lock_version() -> str:
    with (ROOT / "uv.lock").open("rb") as file:
        packages = tomllib.load(file)["package"]
    (own,) = [package for package in packages if package["name"] == "coin-report-trader"]
    return own["version"]


def test_the_four_places_carry_the_same_version_in_the_required_form(version_info) -> None:
    version = coin_report_trader.__version__
    assert FORM.fullmatch(version), version
    assert pyproject_version() == version
    assert lock_version() == version
    fields = version_info.version_fields(version)
    major, minor, patch = (int(part) for part in version.split("."))
    assert fields["filevers"] == (major, minor, patch, 0)
    assert fields["prodvers"] == (major, minor, patch, 0)
    assert fields["strings"]["ProductVersion"] == f"v{version}"
    assert fields["strings"]["FileVersion"] == f"v{version}"


def test_this_release_is_v0_1_3(version_info) -> None:
    assert coin_report_trader.__version__ == "0.1.3"
    fields = version_info.version_fields("0.1.3")
    assert fields["filevers"] == fields["prodvers"] == (0, 1, 3, 0)
    assert fields["strings"]["ProductVersion"] == fields["strings"]["FileVersion"] == "v0.1.3"


def test_the_fields_follow_section_3_15(version_info) -> None:
    fields = version_info.version_fields("12.3.45")
    assert fields == {
        "filevers": (12, 3, 45, 0),
        "prodvers": (12, 3, 45, 0),
        "language": "0412",
        "codepage": "04B0",
        "strings": {
            "ProductName": "Coin Report Trader",
            "FileDescription": "Coin Report Trader",
            "InternalName": "coin-report-trader",
            "OriginalFilename": "coin-report-trader.exe",
            "ProductVersion": "v12.3.45",
            "FileVersion": "v12.3.45",
        },
    }
    assert (
        tuple(fields["strings"]) == STRINGS
    )  # 넣지 않는 칸(CompanyName, LegalCopyright 등)이 없다
    assert all(isinstance(number, int) for number in fields["filevers"])


@pytest.mark.parametrize(
    "version",
    [
        "",
        "1",
        "1.0",
        "1.0.0.0",
        "v1.0.0",
        "01.0.0",
        "1.00.0",
        "1.0.00",
        "1.0.0-rc1",
        "1.0.0+build",
        " 1.0.0",
        "1.0.0\n",
        "1..0",
        "a.b.c",
        "١.0.0",  # 다른 문자의 숫자
    ],
)
def test_a_version_outside_the_form_is_a_value_error(version_info, version: str) -> None:
    with pytest.raises(ValueError, match=r"MAJOR\.MINOR\.PATCH"):
        version_info.version_fields(version)


def test_the_version_module_uses_only_the_standard_library() -> None:
    tree = ast.parse(VERSION_INFO.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "상대 import를 쓰지 않는다"
            assert node.module is not None
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names), imported
