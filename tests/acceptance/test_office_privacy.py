"""개인정보 검사가 Office 파일(`.pptx`, `.docx`, `.xlsx`) 안의 글과 문서 속성도 본다.

A11(`test_a11_checks.py`)과 같은 방법으로, 저장소 사본을 `tmp_path`에 만들고 Office 파일
하나를 더한 뒤 그 사본의 `run_all.py`를 따로 된 프로세스로 돌린다. Office 파일은 시험 안에서
`zipfile`로 아주 작게 만든다(슬라이드, 메모, 문서 속성 등 검사가 보는 부분만 둔다). 실패 예로
쓰는 이메일, 사용자 폴더 경로, 사람 이름은 이 파일의 글에 그대로 쓰지 않고 실행 중에 이어 붙여
만든다(그대로 쓰면 이 시험 파일이 개인정보 검사에 걸린다).
"""

import os
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUN_ALL = Path("scripts") / "checks" / "run_all.py"
_GIT_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")
ITEM = "개인정보"

_A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
_P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
_S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
_DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


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


# --- 실패 예의 값(실행 중에 이어 붙인다) ----------------------------------------------------------


def _fake_address() -> str:
    return "kim.min" + "su" + "@" + "personal-mail" + ".net"


def _fake_user_dir() -> str:
    return "C:" + "\\" + "Us" + "ers" + "\\" + "minsu" + "\\Documents"


def _fake_person() -> str:
    return "홍" + "길동"


# --- 아주 작은 Office 파일 ------------------------------------------------------------------------


def _core(creator: str = "", modified_by: str = "") -> str:
    return (
        _DECLARATION
        + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/'
        + 'core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        + f"<dc:creator>{creator}</dc:creator>"
        + f"<cp:lastModifiedBy>{modified_by}</cp:lastModifiedBy>"
        + "</cp:coreProperties>"
    )


def _app(company: str = "", manager: str = "") -> str:
    return (
        _DECLARATION
        + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
        + 'extended-properties">'
        + f"<Company>{company}</Company><Manager>{manager}</Manager></Properties>"
    )


def _pptx(slide: str = "화면 목록", note: str = "메모", **props: str) -> dict[str, str]:
    return {
        "[Content_Types].xml": _DECLARATION + "<Types/>",
        "_rels/.rels": _DECLARATION + "<Relationships/>",
        "ppt/slides/slide1.xml": (
            _DECLARATION + f"<p:sld {_P} {_A}><a:p><a:r><a:t>{slide}</a:t></a:r></a:p></p:sld>"
        ),
        "ppt/notesSlides/notesSlide1.xml": (
            _DECLARATION + f"<p:notes {_P} {_A}><a:p><a:r><a:t>{note}</a:t></a:r></a:p></p:notes>"
        ),
        "ppt/media/image1.png": "PNG 자리(그림은 보지 않는다)",
        "docProps/core.xml": _core(props.get("creator", ""), props.get("modified_by", "")),
        "docProps/app.xml": _app(props.get("company", ""), props.get("manager", "")),
    }


def _docx(body: str = "요구사항", **props: str) -> dict[str, str]:
    return {
        "[Content_Types].xml": _DECLARATION + "<Types/>",
        "word/document.xml": (
            _DECLARATION + f"<w:document {_W}><w:body><w:p><w:r><w:t>{body}</w:t></w:r></w:p>"
            "</w:body></w:document>"
        ),
        "docProps/core.xml": _core(props.get("creator", ""), props.get("modified_by", "")),
        "docProps/app.xml": _app(props.get("company", ""), props.get("manager", "")),
    }


def _xlsx(cell: str = "화면 번호", **props: str) -> dict[str, str]:
    return {
        "[Content_Types].xml": _DECLARATION + "<Types/>",
        "xl/sharedStrings.xml": _DECLARATION + f"<sst {_S}><si><t>{cell}</t></si></sst>",
        "docProps/core.xml": _core(props.get("creator", ""), props.get("modified_by", "")),
        "docProps/app.xml": _app(props.get("company", ""), props.get("manager", "")),
    }


def _write_office(path: Path, parts: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in parts.items():
            archive.writestr(name, text.encode("utf-8"))


# --- 시험 ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("file_name", "make", "part"),
    [
        ("docs/design/screens.pptx", lambda v: _pptx(slide="연락: " + v), "ppt/slides/slide1.xml"),
        ("docs/design/spec.docx", lambda v: _docx(body="연락: " + v), "word/document.xml"),
        ("docs/design/list.xlsx", lambda v: _xlsx(cell="연락: " + v), "xl/sharedStrings.xml"),
    ],
    ids=["pptx", "docx", "xlsx"],
)
def test_an_email_in_the_text_of_an_office_file_fails(
    make_copy: Callable[[str], Path], file_name: str, make: Callable[[str], dict], part: str
) -> None:
    """(가) 슬라이드·본문·셀의 글에 든 이메일을 잡는다. (바) 찾은 값은 출력에 없다."""
    copy = make_copy("office-email")
    address = _fake_address()
    _write_office(copy / file_name, make(address))
    code, output = _run_all(copy)
    assert code == 1, output
    assert _status(output)[ITEM] == "실패"
    assert f"{file_name}:{part}: 허용 목록 밖의 이메일 주소" in output
    assert address not in output


def test_a_user_folder_path_in_a_speaker_note_fails(make_copy: Callable[[str], Path]) -> None:
    """(나) 슬라이드 메모의 사용자 폴더 경로를 잡는다. (바) 찾은 값은 출력에 없다."""
    copy = make_copy("office-path")
    path = _fake_user_dir()
    _write_office(copy / "docs" / "design" / "screens.pptx", _pptx(note="원본: " + path))
    code, output = _run_all(copy)
    assert code == 1, output
    assert "docs/design/screens.pptx:ppt/notesSlides/notesSlide1.xml: 사용자 폴더 경로" in output
    assert path not in output
    assert "minsu" not in output


def test_an_email_split_over_two_text_runs_fails(make_copy: Callable[[str], Path]) -> None:
    """(가) 한 낱말이 글 마디 둘로 나뉘어도(PowerPoint가 흔히 나눈다) 이은 글로 잡는다."""
    copy = make_copy("office-split")
    user, domain = "kim.minsu", "@" + "personal-mail" + ".net"
    parts = _pptx()
    parts["ppt/slides/slide1.xml"] = (
        _DECLARATION + f"<p:sld {_P} {_A}><a:p><a:r><a:t>{user}</a:t></a:r>"
        f"<a:r><a:t>{domain}</a:t></a:r></a:p></p:sld>"
    )
    _write_office(copy / "docs" / "design" / "screens.pptx", parts)
    code, output = _run_all(copy)
    assert code == 1, output
    assert "screens.pptx:ppt/slides/slide1.xml: 허용 목록 밖의 이메일 주소" in output


@pytest.mark.parametrize(
    ("props", "kind"),
    [
        ({"creator": "NAME"}, "문서 속성의 사람 이름"),
        ({"modified_by": "NAME"}, "문서 속성의 사람 이름"),
        ({"company": "NAME"}, "문서 속성의 회사·관리자"),
        ({"manager": "NAME"}, "문서 속성의 회사·관리자"),
    ],
    ids=["작성자", "마지막 수정자", "회사", "관리자"],
)
def test_a_person_in_the_document_properties_fails(
    make_copy: Callable[[str], Path], props: dict[str, str], kind: str
) -> None:
    """(다) 문서 속성의 사람 이름(작성자, 마지막 수정자)과 회사·관리자를 잡는다. (바) 값은 없다."""
    copy = make_copy("office-props")
    person = _fake_person()
    filled = dict.fromkeys(props, person)
    _write_office(copy / "docs" / "design" / "screens.pptx", _pptx(**filled))
    code, output = _run_all(copy)
    assert code == 1, output
    part = "docProps/core.xml" if kind == "문서 속성의 사람 이름" else "docProps/app.xml"
    assert f"docs/design/screens.pptx:{part}: {kind}" in output
    assert person not in output


@pytest.mark.parametrize(
    "content",
    [b"PK\x03\x04" + b"\x00" * 40 + b"broken", b"not a zip file at all"],
    ids=["깨진 ZIP", "ZIP 아님"],
)
def test_an_office_file_that_cannot_be_opened_fails(
    make_copy: Callable[[str], Path], content: bytes
) -> None:
    """(라) ZIP이 아니거나 깨진 Office 파일은 실패로 적는다."""
    copy = make_copy("office-broken")
    target = copy / "docs" / "design" / "list.xlsx"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    code, output = _run_all(copy)
    assert code == 1, output
    assert "docs/design/list.xlsx: 열 수 없는 Office 파일" in output


def test_clean_office_files_and_allowed_names_pass(make_copy: Callable[[str], Path]) -> None:
    """(마) 깨끗한 파일과 허용 이름(Claude, python-pptx, coin-report-trader, 빈 값)은 통과한다.

    그림 자리(`ppt/media/`)의 글은 보지 않는다. 허용된 주소(example.com, noreply)도 통과한다.
    """
    copy = make_copy("office-clean")
    design = copy / "docs" / "design"
    allowed = "someone" + "@" + "example.com"
    _write_office(design / "a.pptx", _pptx(slide="연락: " + allowed, creator="Claude"))
    _write_office(design / "b.docx", _docx(creator="python-pptx", modified_by="Claude"))
    _write_office(design / "c.xlsx", _xlsx(creator="coin-report-trader"))
    media = _pptx()
    media["ppt/media/image1.png"] = "그림 자리: " + _fake_address()
    _write_office(design / "d.pptx", media)
    code, output = _run_all(copy)
    assert code == 0, output
    assert _status(output)[ITEM] == "통과"
