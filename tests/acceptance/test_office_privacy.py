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


def _pptx(slide: str = "화면 목록", note: str = "메모", **props: str) -> dict[str, str | bytes]:
    return {
        "[Content_Types].xml": _DECLARATION + "<Types/>",
        "_rels/.rels": _DECLARATION + "<Relationships/>",
        "ppt/slides/slide1.xml": (
            _DECLARATION + f"<p:sld {_P} {_A}><a:p><a:r><a:t>{slide}</a:t></a:r></a:p></p:sld>"
        ),
        "ppt/notesSlides/notesSlide1.xml": (
            _DECLARATION + f"<p:notes {_P} {_A}><a:p><a:r><a:t>{note}</a:t></a:r></a:p></p:notes>"
        ),
        "ppt/media/image1.png": PNG,
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


def _write_office(
    path: Path, parts: dict[str, str | bytes], compression: int = zipfile.ZIP_DEFLATED
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression) as archive:
        for name, text in parts.items():
            archive.writestr(name, text if isinstance(text, bytes) else text.encode("utf-8"))


def _slide(*paragraphs: list[str]) -> str:
    """문단마다 글 조각 여럿(`<a:r><a:t>`)을 둔 슬라이드."""
    body = "".join(
        "<a:p>" + "".join(f"<a:r><a:t>{piece}</a:t></a:r>" for piece in pieces) + "</a:p>"
        for pieces in paragraphs
    )
    return _DECLARATION + f"<p:sld {_P} {_A}>{body}</p:sld>"


def _expect(copy: Path, *lines: str, forbidden: tuple[str, ...] = ()) -> str:
    """사본의 검사가 실패하고, 출력에 `lines`가 다 있고 `forbidden`(찾은 값)은 없는지 본다."""
    code, output = _run_all(copy)
    assert code == 1, output
    assert _status(output)[ITEM] == "실패"
    for line in lines:
        assert line in output, output
    for value in forbidden:
        assert value not in output
    return output


def _expect_pass(copy: Path) -> None:
    code, output = _run_all(copy)
    assert code == 0, output
    assert _status(output)[ITEM] == "통과"


PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
"""진짜 이진 값으로 만든 그림 부분(PNG의 앞머리)."""


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

    이름이 `.xml`·`.rels`가 아닌 부분(그림 `ppt/media/`, 진짜 이진 값)은 보지 않는다. 허용된
    주소(example.com, noreply)도 통과한다.
    """
    copy = make_copy("office-clean")
    design = copy / "docs" / "design"
    allowed = "someone" + "@" + "example.com"
    _write_office(design / "a.pptx", _pptx(slide="연락: " + allowed, creator="Claude"))
    _write_office(design / "b.docx", _docx(creator="python-pptx", modified_by="Claude"))
    _write_office(design / "c.xlsx", _xlsx(creator="coin-report-trader"))
    media: dict[str, str | bytes] = dict(_pptx())
    media["ppt/media/image1.png"] = PNG + _fake_address().encode("ascii")
    _write_office(design / "d.pptx", media)
    _expect_pass(copy)


# --- 지휘 검토(고칠 것 1): 문단 같은 마디마다 잇는다 ---


def test_a_noreply_address_alone_in_a_table_cell_passes(make_copy: Callable[[str], Path]) -> None:
    """(마) 표의 칸 `user.email` 다음 칸에 홀로 있는 허용 주소는 통과한다(칸끼리 잇지 않는다)."""
    copy = make_copy("office-table")
    allowed = "noreply" + "@" + "anthropic.com"
    cell = "<a:tc><a:txBody><a:p><a:r><a:t>{}</a:t></a:r></a:p></a:txBody></a:tc>"
    table = "<a:tbl><a:tr>" + cell.format("user.email") + cell.format(allowed) + "</a:tr></a:tbl>"
    parts = _pptx()
    parts["ppt/slides/slide1.xml"] = _DECLARATION + f"<p:sld {_P} {_A}>{table}</p:sld>"
    _write_office(copy / "docs" / "design" / "table.pptx", parts)
    cells = "<si><t>user.email</t></si><si><t>" + allowed + "</t></si>"
    book = _xlsx()
    book["xl/sharedStrings.xml"] = _DECLARATION + f"<sst {_S}>{cells}</sst>"
    _write_office(copy / "docs" / "design" / "table.xlsx", book)
    _expect_pass(copy)


def test_a_path_split_right_after_a_title_paragraph_fails(make_copy: Callable[[str], Path]) -> None:
    """(나) 제목 문단 바로 뒤의 문단에서 여러 조각으로 나뉜 사용자 폴더 경로를 잡는다."""
    copy = make_copy("office-title-path")
    users = "Us" + "ers"
    pieces = ["C:" + "\\" + users[:2], users[2:] + "\\" + "minsu", "\\a.txt"]
    parts = _pptx()
    parts["ppt/slides/slide1.xml"] = _slide(["화면 제목"], pieces)
    _write_office(copy / "docs" / "design" / "screens.pptx", parts)
    _expect(copy, "screens.pptx:ppt/slides/slide1.xml: 사용자 폴더 경로", forbidden=("minsu",))


# --- 지휘 검토(고칠 것 2): 날것 XML, `.rels`, 읽기·파싱의 실패, 대소문자, UTF-16 ---


def test_an_email_and_a_path_in_a_relationship_target_fail(
    make_copy: Callable[[str], Path],
) -> None:
    """`.rels`의 `Target`에 든 `mailto:` 주소와 `file:///` 사용자 폴더 경로를 잡는다(속성 값)."""
    copy = make_copy("office-rels")
    address = _fake_address()
    path = "file:///" + "C:/" + "Us" + "ers" + "/minsu/a.pptx"
    rels = (
        _DECLARATION + "<Relationships>"
        f'<Relationship Id="r1" Target="mailto:{address}" TargetMode="External"/>'
        f'<Relationship Id="r2" Target="{path}" TargetMode="External"/>'
        "</Relationships>"
    )
    parts = _pptx()
    parts["ppt/slides/_rels/slide1.xml.rels"] = rels
    _write_office(copy / "docs" / "design" / "screens.pptx", parts)
    part = "screens.pptx:ppt/slides/_rels/slide1.xml.rels"
    _expect(
        copy,
        f"{part}: 허용 목록 밖의 이메일 주소",
        f"{part}: 사용자 폴더 경로",
        forbidden=(address, "minsu"),
    )


def test_a_path_only_in_an_attribute_of_the_workbook_fails(
    make_copy: Callable[[str], Path],
) -> None:
    """`xl/workbook.xml`의 속성(`absPath url`)에만 든 사용자 폴더 경로를 잡는다."""
    copy = make_copy("office-abspath")
    path = "C:" + "\\" + "Us" + "ers" + "\\minsu\\Desktop\\"
    book = _xlsx()
    book["xl/workbook.xml"] = (
        _DECLARATION + f'<workbook {_S} xmlns:x15ac="http://schemas.microsoft.com/office/'
        f'spreadsheetml/2010/11/ac"><x15ac:absPath url="{path}"/></workbook>'
    )
    _write_office(copy / "docs" / "design" / "list.xlsx", book)
    _expect(copy, "list.xlsx:xl/workbook.xml: 사용자 폴더 경로", forbidden=("minsu",))


def test_a_slide_whose_xml_cannot_be_read_fails(make_copy: Callable[[str], Path]) -> None:
    """닫는 꼬리가 어긋난 슬라이드 XML은 '읽을 수 없는 XML'로 적는다."""
    copy = make_copy("office-bad-xml")
    parts = _pptx()
    parts["ppt/slides/slide1.xml"] = _DECLARATION + f"<p:sld {_P} {_A}><a:p></p:sld>"
    _write_office(copy / "docs" / "design" / "screens.pptx", parts)
    _expect(copy, "screens.pptx:ppt/slides/slide1.xml: 읽을 수 없는 XML")


def test_a_stored_part_with_a_changed_byte_fails(make_copy: Callable[[str], Path]) -> None:
    """저장(stored) 방식 ZIP에서 슬라이드 데이터 한 바이트를 바꾸면(CRC가 맞지 않음) '읽을 수 없는
    부분'으로 적는다."""
    copy = make_copy("office-bad-crc")
    target = copy / "docs" / "design" / "screens.pptx"
    _write_office(target, _pptx(slide="바꿀 글자QQ"), compression=zipfile.ZIP_STORED)
    data = bytearray(target.read_bytes())
    spot = data.index("QQ".encode("ascii"))
    data[spot] = ord("R")
    target.write_bytes(bytes(data))
    _expect(copy, "screens.pptx:ppt/slides/slide1.xml: 읽을 수 없는 부분")


def test_upper_case_file_and_part_names_are_checked(make_copy: Callable[[str], Path]) -> None:
    """파일 이름(`.PPTX`)과 부분 이름(`PPT/SLIDES/SLIDE1.XML`, `DOCPROPS/CORE.XML`)이
    대문자여도 본다."""
    copy = make_copy("office-upper")
    address = _fake_address()
    parts = {
        "PPT/SLIDES/SLIDE1.XML": _slide(["연락: " + address]),
        "DOCPROPS/CORE.XML": _core(creator=_fake_person()),
    }
    _write_office(copy / "docs" / "design" / "SCREENS.PPTX", parts)
    _expect(
        copy,
        "SCREENS.PPTX:PPT/SLIDES/SLIDE1.XML: 허용 목록 밖의 이메일 주소",
        "SCREENS.PPTX:DOCPROPS/CORE.XML: 문서 속성의 사람 이름",
        forbidden=(address, _fake_person()),
    )


def _utf16(text: str) -> bytes:
    return text.replace('encoding="UTF-8"', 'encoding="UTF-16"').encode("utf-16")  # BOM이 붙는다


def test_a_person_in_utf16_document_properties_fails_and_a_clean_one_passes(
    make_copy: Callable[[str], Path],
) -> None:
    """UTF-16(BOM)으로 쓴 `docProps/core.xml`의 사람 이름을 잡고, 깨끗한 UTF-16 파일은 통과한다."""
    person = _fake_person()
    bad = make_copy("office-utf16-bad")
    parts = _pptx()
    parts["docProps/core.xml"] = _utf16(_core(creator=person))
    _write_office(bad / "docs" / "design" / "screens.pptx", parts)
    _expect(bad, "screens.pptx:docProps/core.xml: 문서 속성의 사람 이름", forbidden=(person,))
    clean = make_copy("office-utf16-clean")
    parts = _pptx()
    parts["docProps/core.xml"] = _utf16(_core(creator="Claude"))
    parts["ppt/slides/slide1.xml"] = _utf16(_slide(["화면 목록"]))
    _write_office(clean / "docs" / "design" / "screens.pptx", parts)
    _expect_pass(clean)


# --- 지휘 검토(고칠 것 3): 댓글과 변경 기록의 사람 이름 ---


@pytest.mark.parametrize(
    ("file_name", "part", "make_xml"),
    [
        (
            "screens.pptx",
            "ppt/commentAuthors.xml",
            lambda n: f'<p:cmAuthorLst {_P}><p:cmAuthor id="1" name="{n}"/></p:cmAuthorLst>',
        ),
        (
            "screens.pptx",
            "ppt/authors.xml",
            lambda n: (
                f'<p188:authorLst xmlns:p188="urn:x"><p188:author name="{n}"/></p188:authorLst>'
            ),
        ),
        (
            "spec.docx",
            "word/comments.xml",
            lambda n: f'<w:comments {_W}><w:comment w:id="0" w:author="{n}"/></w:comments>',
        ),
        (
            "spec.docx",
            "word/document.xml",
            lambda n: (
                f'<w:document {_W}><w:body><w:p><w:ins w:author="{n}"/></w:p></w:body></w:document>'
            ),
        ),
        (
            "list.xlsx",
            "xl/comments1.xml",
            lambda n: f"<comments {_S}><authors><author>{n}</author></authors></comments>",
        ),
        (
            "list.xlsx",
            "xl/persons/person.xml",
            lambda n: f'<personList xmlns="urn:x"><person displayName="{n}"/></personList>',
        ),
    ],
    ids=["슬라이드 댓글", "슬라이드 작성자", "문서 댓글", "변경 기록", "셀 댓글", "사람 목록"],
)
def test_a_person_in_comments_or_tracked_changes_fails(
    make_copy: Callable[[str], Path], file_name: str, part: str, make_xml: Callable[[str], str]
) -> None:
    """댓글과 변경 기록의 작성자 이름이 비어 있지 않고 허용 이름이 아니면 잡는다."""
    copy = make_copy("office-comments")
    person = _fake_person()
    parts = {
        "[Content_Types].xml": _DECLARATION + "<Types/>",
        part: _DECLARATION + make_xml(person),
    }
    _write_office(copy / "docs" / "design" / file_name, parts)
    _expect(
        copy, f"docs/design/{file_name}:{part}: 댓글·변경 기록의 사람 이름", forbidden=(person,)
    )


def test_allowed_names_in_comments_pass(make_copy: Callable[[str], Path]) -> None:
    """댓글의 작성자가 허용 이름이거나 비어 있으면 통과한다."""
    copy = make_copy("office-comments-ok")
    parts = _pptx()
    parts["ppt/commentAuthors.xml"] = (
        _DECLARATION + f'<p:cmAuthorLst {_P}><p:cmAuthor id="1" name="Claude"/>'
        '<p:cmAuthor id="2" name=""/></p:cmAuthorLst>'
    )
    _write_office(copy / "docs" / "design" / "screens.pptx", parts)
    _expect_pass(copy)


# --- 함께 고칠 작은 것 ----------------------------------------------------------------------------


def test_a_listed_office_file_that_was_deleted_is_skipped(make_copy: Callable[[str], Path]) -> None:
    """목록에 있지만(색인에 올림) 지운 Office 파일은 글 파일처럼 건너뛴다."""
    copy = make_copy("office-deleted")
    target = copy / "docs" / "design" / "gone.pptx"
    _write_office(target, _pptx())
    _git("add", "docs/design/gone.pptx", cwd=copy)
    target.unlink()
    _expect_pass(copy)


def test_the_planned_shape_of_a_design_pptx_passes(make_copy: Callable[[str], Path]) -> None:
    """(마) 계획된 꼴의 PPTX(작성자와 마지막 수정자 `coin-report-trader`, 회사와 관리자 빈칸)는
    통과한다."""
    copy = make_copy("office-planned")
    parts = _pptx(creator="coin-report-trader", modified_by="coin-report-trader")
    _write_office(copy / "docs" / "design" / "screens.pptx", parts)
    _expect_pass(copy)
