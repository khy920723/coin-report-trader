"""안내 문서 `docs/guide.md`와 exe 묶음의 복사 확인(M2 명세 3.16, 4절 25번).

글의 원본은 하나다: 저장소의 `docs/guide.md`(Markdown, UTF-8, BOM 없음, 그림 없음).
도움말 화면(27번)이 그 글을 읽고, 빌드가 exe 옆의 `guide.md`로 그대로 복사한다.
이 파일의 앞쪽은 원본의 꼴을 보고, 뒤쪽은 `.spec`의 빌드 뒤 확인(`guide_copy_problems`)이
파일이 있고 비지 않았으며 원본과 바이트가 같은지 가려내는지 본다.
빌드는 돌리지 않는다(PyInstaller 없이 `.spec`의 그 함수만 꺼내 쓴다).
"""

import ast
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "guide.md"
SPEC = ROOT / "packaging" / "coin-report-trader.spec"
BOM = b"\xef\xbb\xbf"


# --- 원본의 꼴 ----------------------------------------------------------------------------------


def test_the_guide_exists_and_is_not_empty() -> None:
    assert GUIDE.is_file()
    assert GUIDE.read_bytes().strip()


def test_the_guide_is_utf_8_without_a_bom() -> None:
    data = GUIDE.read_bytes()
    assert not data.startswith(BOM)
    data.decode("utf-8")  # 읽히지 않으면 UnicodeDecodeError
    assert b"\x00" not in data


def test_the_first_line_is_a_title() -> None:
    first = GUIDE.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("# ")
    assert first.removeprefix("# ").strip()  # 제목 글이 있다


def test_the_guide_has_no_pictures() -> None:
    assert "![" not in GUIDE.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        (BOM + "# 제목\n".encode(), "BOM"),
        ("# 제목\n".encode("utf-16"), "UTF-8"),
        (b"# \xff\xfe\n", "UTF-8"),
        ("제목 없음\n".encode(), "제목"),
        ("#제목\n".encode(), "제목"),
        ("# 제목\n\n![그림](a.png)\n".encode(), "그림"),
    ],
    ids=["bom", "utf-16", "bad-bytes", "no-title", "no-space", "picture"],
)
def test_the_shape_checks_below_catch_a_bad_guide(data: bytes, problem: str) -> None:
    """위 시험이 쓰는 꼴의 확인이 틀린 글을 실제로 가려낸다(시험이 늘 통과하지 않는다는 확인)."""
    assert problem in " ".join(shape_problems(data))


def shape_problems(data: bytes) -> list[str]:
    problems: list[str] = []
    if data.startswith(BOM):
        problems.append("BOM이 있다")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return [*problems, "UTF-8로 읽히지 않는다"]
    first = text.splitlines()[0] if text.splitlines() else ""
    if not (first.startswith("# ") and first.removeprefix("# ").strip()):
        problems.append("첫 줄이 제목(# 로 시작)이 아니다")
    if "![" in text:
        problems.append("그림이 있다")
    return problems


def test_the_real_guide_passes_the_same_shape_checks() -> None:
    assert shape_problems(GUIDE.read_bytes()) == []


# --- `.spec`의 빌드 뒤 확인 ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def check() -> Callable[[Path, Path], list[str]]:
    """`.spec` 안의 `guide_copy_problems`만 꺼낸다(`.spec`은 PyInstaller 없이 실행할 수 없다)."""
    tree = ast.parse(SPEC.read_text(encoding="utf-8"))
    wanted = [
        node
        for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name == "guide_copy_problems")
        or (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "GUIDE_NAME" for t in node.targets)
        )
    ]
    assert len(wanted) == 2, "guide_copy_problems와 GUIDE_NAME을 .spec에서 찾지 못했다"
    namespace: dict[str, object] = {"Path": Path}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(SPEC), "exec"), namespace)
    return namespace["guide_copy_problems"]  # type: ignore[return-value]


def test_the_spec_copies_the_guide_with_copyfile_beside_the_exe() -> None:
    text = SPEC.read_text(encoding="utf-8")
    assert "shutil.copyfile(GUIDE_SOURCE, dist_dir / GUIDE_NAME)" in text
    assert 'GUIDE_SOURCE = ROOT / "docs" / "guide.md"' in text
    assert 'GUIDE_NAME = "guide.md"' in text
    assert "raise SystemExit(" in text.split("guide_problems = guide_copy_problems(")[1]


def test_a_byte_equal_copy_has_no_problem(check, tmp_path: Path) -> None:
    source = tmp_path / "src.md"
    copied = tmp_path / "out" / "guide.md"
    copied.parent.mkdir()
    source.write_bytes("# 안내\r\n글\r\n".encode())
    copied.write_bytes(source.read_bytes())
    assert check(source, copied) == []


def test_a_missing_copy_is_a_problem(check, tmp_path: Path) -> None:
    source = tmp_path / "src.md"
    source.write_bytes(b"# a\n")
    assert check(source, tmp_path / "guide.md") == ["guide.md이 없습니다"]


def test_an_empty_copy_is_a_problem(check, tmp_path: Path) -> None:
    source = tmp_path / "src.md"
    source.write_bytes(b"# a\n")
    copied = tmp_path / "guide.md"
    copied.write_bytes(b"")
    assert check(source, copied) == ["guide.md이 비었습니다"]


@pytest.mark.parametrize(
    "changed",
    [b"# b\n", b"# a\r\n", b"# a\n ", "﻿# a\n".encode()],
    ids=["text", "line-ending", "trailing-space", "bom"],
)
def test_a_copy_that_differs_by_even_a_byte_is_a_problem(
    check, tmp_path: Path, changed: bytes
) -> None:
    source = tmp_path / "src.md"
    source.write_bytes(b"# a\n")
    copied = tmp_path / "guide.md"
    copied.write_bytes(changed)
    assert check(source, copied) == ["guide.md가 docs/guide.md와 바이트가 다릅니다"]


def test_a_directory_in_place_of_the_copy_is_a_problem(check, tmp_path: Path) -> None:
    source = tmp_path / "src.md"
    source.write_bytes(b"# a\n")
    (tmp_path / "guide.md").mkdir()
    assert check(source, tmp_path / "guide.md") == ["guide.md이 없습니다"]
