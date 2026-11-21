"""항목 4. 개인정보: 저장소의 글 파일에 개인 이메일 주소나 사용자 폴더 경로가 없다.

- 대상: `git ls-files`의 파일(아직 커밋하지 않은 새 파일도 넣는다. `.gitignore`가 빼는 것은 뺀다)
  중 글 파일. BOM이 있으면 그 인코딩(UTF-8, UTF-16, UTF-32)으로 풀고, 없으면 UTF-8로 풀되 풀리지
  않는 바이트는 바꿔 넣고 그대로 본다(cp949 같은 파일도 ASCII 글자는 남는다). BOM 없이 NUL이 있는
  파일만 이진으로 보고 건너뛴다. 볼 파일이 하나도 없으면 실패로 적는다. `legacy/`,
  `packaging/licenses/`, `.github/`는 보지 않는다. 커밋 이메일은 privacy 검사
  (`.github/workflows/checks.yml`)가 본다.
- 이메일: privacy 검사의 허용 목록(`checks.yml`의 `allowed=`를 읽는다)과 `example.com` 주소는
  허용한다.
- 경로: `C:\\Users\\<이름>`, `/home/<이름>`, `/Users/<이름>` 꼴. `<이름>`이나 `$USER`처럼 글자나
  숫자로 시작하지 않는 자리표시는 잡지 않는다.
- Office 파일(이름이 `OFFICE_SUFFIXES`로 끝나는 파일, 대소문자 무관): OOXML(`.docx`,
  `.xlsx`, `.pptx`와 매크로·서식·보기 판 `.docm`, `.dotx`, `.xlsm`, `.potx`, `.ppsx` 등,
  `.thmx`, Visio의 `.vsdx` 등)과 ODF(`.odt`, `.ods`, `.odp`, `.odg`)를 ZIP으로 열어
  (`read_office`) 같은 길로 본다.
  웹후크 주소 항목(`webhook_urls.py`)도 이 읽는 법(`office_texts`)을 쓴다.
  - 볼 부분: `[Content_Types].xml`의 내용 형식(`Default`의 확장자, `Override`의 부분 이름.
    대소문자와 `%` 인코딩은 가리지 않는다)으로 고른다. 이름이 `.xml`·`.rels`로 끝나는 부분은
    내용 형식과 상관없이 늘 XML로 읽는다(목록이 없거나 비었거나 틀린 파일, ODF 포함). 그 밖의
    이름은 XML 형식(`+xml`, `application/xml`, `text/xml`)이면 XML로 읽고, 그 밖의 글 형식
    (`text/…`, VML 그림)이면 글로만 본다.
  - XML 그대로의 글(줄마다): 속성 값(관계의 `Target`, 통합 문서의 `absPath` 등)도 여기서
    본다. 문자 참조(`&#64;`, `&#x40;` 등)를 푼 줄도 본다.
  - 문단 같은 마디(지역 이름 `p`, `h`, `si`, `is`, `text`, `Text`. ODF office 이름공간의 본문
    `text`는 뺀다)마다 그 안의 글 조각(`.text`)만 이은 글과, 마디 사이의 꼬리 글까지 이은 글
    (`itertext()`. ODF의 `span` 뒤, Visio의 `cp` 뒤): 한 낱말이 여러 조각으로 나뉜 경우를 본다.
    마디를 넘어 잇지 않는다(표의 칸끼리, 문단끼리 이어지지 않는다).
  - 부분 이름: 모든 부분(그림 등 글이 아닌 것도)의 이름과 `%` 인코딩을 푼 이름을 본다. 출력에는 이름
    대신 몇째 부분인지만 쓴다. 이름에 이메일 꼴, 사용자 폴더 경로, 웹후크 주소 꼴이 든 부분은 다른
    문제를 적을 때도 이름 대신 `(몇째 부분)`으로 쓴다(`name_hides_value`).
  - 문서 속성: 핵심 속성 부분(내용 형식이 `…core-properties+xml`인 부분. 옛 꼴의 `.psmdcp`
    포함. 또는 이름이 `docProps/core.xml`)의 작성자(`creator`)와 마지막 수정자
    (`lastModifiedBy`), 어느 부분에서든 `dc:creator`(ODF의 `meta.xml`, 댓글과 변경 기록 포함),
    ODF의 `meta:initial-creator`·`meta:printed-by`가 비어 있지 않고 허용 이름(`ALLOWED_AUTHORS`)도
    아니면 실패다. 확장 속성 부분(`…extended-properties+xml`이나
    `docProps/app.xml`)의 회사(`Company`)와 관리자(`Manager`)는 비어 있지 않으면 실패다. 마디는
    지역 이름으로 고르므로 Transitional과 Strict의 이름공간을 모두 본다.
  - 댓글과 변경 기록의 사람 이름: 비어 있지 않고 허용 이름도 아니면 실패다. 보는 곳은
    PowerPoint의 댓글 작성자 부분과 작성자 부분(`ppt/commentAuthors.xml`, `ppt/authors.xml`이나 그
    내용 형식)의 `name`, 모든 부분의 `author` 속성(`word/`의 댓글과 변경 기록의 `w:author` 등),
    엑셀 댓글 부분(`xl/comments*.xml`이나 그 내용 형식)의 `author` 마디의 글, 엑셀 사람 부분
    (`xl/persons/*.xml`이나 그 내용 형식)의 `displayName`이다.
  - XML은 바이트로 읽어(BOM과 선언의 인코딩을 따른다) 풀고, ZIP이 아니거나 깨져서 열 수 없는 Office
    파일, 읽거나 풀 수 없는 부분(CRC가 맞지 않는 것, 압축을 풀다 난 어느 예외든), XML로 읽을 수 없는
    부분은 실패다.
  - 보지 않는 것: 내용 형식이 글이 아니고 이름도 `.xml`·`.rels`가 아닌 부분(그림, 프린터 설정,
    `.xlsb`의 셀 `.bin`, 매크로 `vbaProject.bin` 등)의 내용. 안에 넣은 다른 파일
    (`ppt/embeddings/` 등)을 열지 않는다. 사용자 정의 속성(`docProps/custom.xml`)은 이메일·경로
    검사만 받고 사람 이름 검사는 받지 않는다.
- 찾은 값은 출력하지 않는다(공개 기록에 다시 남지 않게). 파일, 줄(Office 파일은 안의 부분 이름),
  종류만 쓴다.
"""

import codecs
import html
import os
import re
import subprocess
import xml.etree.ElementTree as ElementTree
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

TITLE = "개인정보"

EXCLUDED_PREFIXES = ("legacy/", "packaging/licenses/", ".github/")
PRIVACY_WORKFLOW = ".github/workflows/checks.yml"
ALLOWED_DOMAIN = "example.com"

# checks.yml privacy가 메시지에서 주소를 뽑는 식과 같다([[:alnum:]]는 ASCII로 본다).
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.ASCII)
# 이름 자리는 글자·숫자·밑줄로 시작해야 한다. 앞에 글자가 붙은 경로(`/srv/home/...`)는 빼려고
# 바로 앞이 글자·숫자·`.`·`-`·`~`가 아닌 것만 본다.
_NAME = r"[^\W][\w.-]*"
_SLASH = r"(?:\\?/)"
# 호스트 바로 앞에 글자, 숫자, `.`, `-`가 오면 다른 호스트(이름이 `discord.com`으로 끝나는 것, 다른
# 하위 도메인)이므로 잡지 않는다. `canary.`와 `ptb.`는 호스트에 든다.
_HOST = r"(?<![A-Za-z0-9.-])(?:(?:canary|ptb)\.)?(?:discord|discordapp)\.com"
WEBHOOK = re.compile(
    _HOST
    + _SLASH
    + "api"
    + _SLASH
    + r"(?:v[0-9]+"
    + _SLASH
    + r")?webhooks"
    + _SLASH
    + r"[0-9]{17,20}"
    + _SLASH
    + r"[A-Za-z0-9._-]{60,}",
    re.IGNORECASE,
)
"""디스코드 웹후크 주소의 꼴(웹후크 주소 항목 `webhook_urls.py`의 설명). Office 부분 이름을 찍을지
가릴 때도 쓰므로 두 항목이 함께 쓰는 이곳에 둔다."""
USER_DIRS = (
    re.compile(r"(?<![\w.-])[A-Za-z]:[\\/]+[Uu][Ss][Ee][Rr][Ss][\\/]+" + _NAME),
    re.compile(r"(?<![\w.~-])/(?:home|Users)/" + _NAME),
)
# 바깥에서 넘어온 git 환경 변수(훅 등)가 다른 저장소를 가리키지 않게 지운다.
GIT_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")
# 긴 BOM(UTF-32)을 먼저 본다. UTF-32 LE의 BOM은 UTF-16 LE의 BOM으로 시작한다.
BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
_ALLOWED_LINE = re.compile(r"""^\s*allowed='(?P<pattern>[^']+)'\s*$""", re.MULTILINE)

OFFICE_SUFFIXES = (
    # Word
    ".docx", ".docm", ".dotx", ".dotm",
    # Excel
    ".xlsx", ".xlsm", ".xltx", ".xltm", ".xlsb", ".xlam",
    # PowerPoint
    ".pptx", ".pptm", ".potx", ".potm", ".ppsx", ".ppsm", ".ppam",
    # 테마, Visio
    ".thmx", ".vsdx", ".vsdm", ".vssx", ".vssm", ".vstx", ".vstm",
    # ODF
    ".odt", ".ods", ".odp", ".odg",
)  # fmt: skip
TEXT_PART_SUFFIXES = (".xml", ".rels")
CONTENT_TYPES_PART = "[content_types].xml"
ALLOWED_AUTHORS = frozenset({"Claude", "python-pptx", "coin-report-trader"})
"""문서 속성의 작성자·마지막 수정자로 허용하는 이름."""
# 부분의 구실: 내용 형식(작은 글자)과, 내용 형식이 없을 때의 부분 이름(작은 글자).
CORE = "core"
APP = "app"
COMMENT_AUTHORS = "comment_authors"
XL_COMMENTS = "xl_comments"
XL_PERSONS = "xl_persons"
_ROLE_TYPES = {
    "application/vnd.openxmlformats-package.core-properties+xml": CORE,
    "application/vnd.openxmlformats-officedocument.extended-properties+xml": APP,
    "application/vnd.openxmlformats-officedocument.presentationml.commentauthors+xml": (
        COMMENT_AUTHORS
    ),
    "application/vnd.ms-powerpoint.authors+xml": COMMENT_AUTHORS,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml": XL_COMMENTS,
    "application/vnd.ms-excel.person+xml": XL_PERSONS,
}
_ROLE_NAMES = {
    "docprops/core.xml": CORE,
    "docprops/app.xml": APP,
    "ppt/commentauthors.xml": COMMENT_AUTHORS,
    "ppt/authors.xml": COMMENT_AUTHORS,
}
_XML_TYPES = frozenset({"application/xml", "text/xml"})
_VML_TYPE = "application/vnd.openxmlformats-officedocument.vmldrawing"
_PERSON_FIELDS = ("creator", "lastModifiedBy")
"""핵심 속성 부분의 사람 이름 마디(지역 이름)."""
_DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"
"""어느 부분이든 사람 이름으로 보는 마디(OOXML의 핵심 속성, ODF의 `meta.xml`과 댓글·변경 기록)."""
_ODF_META = "urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
_ODF_PERSON_FIELDS = frozenset(
    {"{" + _ODF_META + "}initial-creator", "{" + _ODF_META + "}printed-by"}
)
_ODF_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_COMPANY_FIELDS = ("Company", "Manager")
_PARAGRAPHS = frozenset({"p", "h", "si", "is", "text", "Text"})
"""글 조각을 이어 볼 문단 같은 마디의 지역 이름(DrawingML·Word·ODF의 `p`, ODF의 제목 `h`, 엑셀의
`si`·`is`·`text`, Visio의 `Text`). ODF office 이름공간의 `text`(본문 전체)는 빼고 본다."""


@dataclass(frozen=True)
class OfficePart:
    """Office 파일 안의 볼 부분 하나."""

    name: str
    data: bytes
    xml: bool
    """XML로 읽는 부분인지(아니면 글로만 본다)."""
    role: str | None
    """문서 속성, 댓글 등 사람 이름을 볼 구실. 없으면 None."""
    label: str
    """출력에 쓰는 이름. 이름에 값이 숨은 부분은 이름 대신 `(몇째 부분)`이다."""


@dataclass
class OfficeFile:
    """`read_office`의 결과."""

    parts: list[OfficePart] = field(default_factory=list)
    part_names: list[str] = field(default_factory=list)
    """모든 부분의 이름(ZIP의 차례)."""
    problems: list[str] = field(default_factory=list)
    """열거나 읽지 못한 것(`파일:부분: 종류`. 열지 못하면 `파일: 종류`)."""


def check(root: Path) -> list[str]:
    allowed = _privacy_allowlist(root)
    if allowed is None:
        return [f"{PRIVACY_WORKFLOW}: privacy 검사의 허용 목록(allowed=)을 읽지 못했다"]
    files = _listed_files(root)
    if files is None:
        return ["git ls-files를 실행하지 못했다(git 저장소가 아니거나 git이 없다)"]
    problems = []
    checked = 0
    for name in files:
        if name.startswith(EXCLUDED_PREFIXES):
            continue
        if name.lower().endswith(OFFICE_SUFFIXES):
            if not (root / name).is_file():
                continue  # 목록에 있지만 지운 파일(글 파일처럼 건너뛴다)
            checked += 1
            problems.extend(_check_office(root / name, name, allowed))
            continue
        text = _read_text(root / name)
        if text is None:
            continue
        checked += 1
        for number, line in enumerate(text.splitlines(), start=1):
            problems.extend(f"{name}:{number}: {kind}" for kind in _line_problems(line, allowed))
    if checked == 0:
        problems.append("git ls-files가 내놓은 파일 가운데 검사할 글 파일이 하나도 없다")
    return problems


def _line_problems(line: str, allowed: re.Pattern[str]) -> list[str]:
    """한 줄에서 찾은 문제의 종류(찾은 값은 담지 않는다)."""
    kinds = []
    if any(not _is_allowed_email(m.group(), allowed) for m in EMAIL.finditer(line)):
        kinds.append("허용 목록 밖의 이메일 주소")
    if any(pattern.search(line) for pattern in USER_DIRS):
        kinds.append("사용자 폴더 경로")
    return kinds


def _check_office(path: Path, name: str, allowed: re.Pattern[str]) -> list[str]:
    """Office 파일 안의 글 부분, 부분 이름, 사람 이름을 본다(모듈 설명)."""
    office = read_office(path, name)
    problems = list(office.problems)
    for index, kind in _part_name_problems(office, lambda text: _line_problems(text, allowed)):
        problems.append(f"{name}:({index}째 부분의 이름): {kind}")
    for part in office.parts:
        problems.extend(_check_part(name, part, allowed))
    return problems


def read_office(path: Path, name: str) -> OfficeFile:
    """Office 파일을 ZIP으로 열어 볼 부분을 고른다(모듈 설명). 예외를 내지 않는다."""
    office = OfficeFile()
    try:
        archive = zipfile.ZipFile(path)
    except Exception:  # ZIP이 아니거나 깨진 파일은 어느 예외든 실패로 적는다
        office.problems.append(f"{name}: 열 수 없는 Office 파일(ZIP이 아니거나 깨졌다)")
        return office
    with archive:
        try:
            infos = archive.infolist()
            types = _content_types(archive)
        except Exception:
            office.problems.append(f"{name}: 열 수 없는 Office 파일(ZIP이 아니거나 깨졌다)")
            return office
        for index, info in enumerate(infos, start=1):
            office.part_names.append(info.filename)
            if info.is_dir():
                continue
            label = f"({index}째 부분)" if name_hides_value(info.filename) else info.filename
            content_type = types.get(info.filename)
            kind = _part_kind(info.filename, content_type)
            if kind is None:
                continue
            try:
                data = archive.read(info)
            except Exception:  # CRC, 압축 풀기(zlib, lzma, bz2), 머리의 어긋남 등 어느 것이든
                office.problems.append(f"{name}:{label}: 읽을 수 없는 부분")
                continue
            role = _ROLE_TYPES.get(content_type or "") or _role_by_name(info.filename)
            office.parts.append(OfficePart(info.filename, data, kind == "xml", role, label))
    return office


def office_texts(part: OfficePart) -> tuple[list[str], bool]:
    """부분에서 볼 글들과, XML로 읽지 못했는지(XML 부분만 참이 될 수 있다).

    글은 날것의 줄, 문자 참조를 푼 줄, 문단 같은 마디마다 이은 글이다(모듈 설명).
    """
    texts, tree = _part_texts(part)
    return texts, part.xml and tree is None


def _part_texts(part: OfficePart) -> tuple[list[str], ElementTree.Element | None]:
    """`office_texts`의 글과, XML로 읽은 나무(XML 부분이 아니거나 읽지 못하면 None)."""
    texts: list[str] = []
    for line in _decode(part.data).splitlines():
        texts.append(line)
        unescaped = html.unescape(line)
        if unescaped != line:
            texts.append(unescaped)
    if not part.xml:
        return texts, None
    tree = _parse(part.data)
    if tree is None:
        return texts, None
    for element in tree.iter():
        if _local(element.tag) in _PARAGRAPHS and element.tag != "{" + _ODF_OFFICE + "}text":
            texts.append("".join(piece.text or "" for piece in element.iter()))
            texts.append("".join(element.itertext()))  # 마디 사이의 꼬리 글까지(ODF, Visio)
    return texts, tree


def name_hides_value(part_name: str) -> bool:
    """부분 이름(이나 `%`를 푼 이름)에 이메일 꼴, 사용자 폴더 경로, 웹후크 주소 꼴이 있는지.

    허용된 주소라도 참이다(이름을 출력하지 않을지만 가린다. 실패인지는 각 항목이 정한다).
    """
    for text in (part_name, unquote(part_name)):
        if EMAIL.search(text) or WEBHOOK.search(text):
            return True
        if any(pattern.search(text) for pattern in USER_DIRS):
            return True
    return False


def _part_name_problems(office: OfficeFile, problems_of) -> list[tuple[int, str]]:
    """부분 이름(과 `%` 인코딩을 푼 이름)에서 찾은 (몇째 부분, 종류). 이름 자체는 담지 않는다."""
    found = []
    for index, part in enumerate(office.part_names, start=1):
        kinds = [*problems_of(part), *problems_of(unquote(part))]
        found.extend((index, kind) for kind in dict.fromkeys(kinds))
    return found


def _content_types(archive: zipfile.ZipFile) -> dict[str, str]:
    """부분 이름에서 내용 형식(작은 글자)으로. `[Content_Types].xml`이 없거나 읽지 못하면 빈 값."""
    listed = {info.filename.lower(): info for info in archive.infolist()}
    info = listed.get(CONTENT_TYPES_PART)
    if info is None:
        return {}
    try:
        tree = _parse(archive.read(info))
    except Exception:
        return {}  # 읽지 못하는 목록은 그 부분 자체를 볼 때 실패로 적힌다
    if tree is None:
        return {}
    defaults: dict[str, str] = {}
    overrides: dict[str, str] = {}
    for element in tree.iter():
        content_type = element.get("ContentType", "").strip().lower()
        if _local(element.tag) == "Default":
            defaults[element.get("Extension", "").strip().lower()] = content_type
        elif _local(element.tag) == "Override":
            overrides[_part_key(element.get("PartName", ""))] = content_type
    types = {}
    for part in archive.infolist():
        key = _part_key(part.filename)
        extension = key.rsplit(".", 1)[-1] if "." in key.rsplit("/", 1)[-1] else ""
        found = overrides.get(key) or defaults.get(extension)
        if found:
            types[part.filename] = found
    return types


def _part_key(part_name: str) -> str:
    return unquote(part_name).strip().lstrip("/").lower()


def _part_kind(part_name: str, content_type: str | None) -> str | None:
    """부분을 보는 길: `"xml"`(XML로 읽는다), `"text"`(글로만 본다), None(보지 않는다)."""
    if part_name.lower().endswith(TEXT_PART_SUFFIXES):
        return "xml"  # 이름이 `.xml`·`.rels`면 내용 형식과 상관없이 늘 XML로 읽는다
    if content_type is not None:
        if content_type.endswith("+xml") or content_type in _XML_TYPES:
            return "xml"
        if content_type.startswith("text/") or content_type == _VML_TYPE:
            return "text"
    return None


def _role_by_name(part_name: str) -> str | None:
    lower = _part_key(part_name)
    if lower in _ROLE_NAMES:
        return _ROLE_NAMES[lower]
    if lower.startswith("xl/comments"):
        return XL_COMMENTS
    if lower.startswith("xl/persons/"):
        return XL_PERSONS
    return None


def _parse(data: bytes) -> ElementTree.Element | None:
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return None


def _check_part(name: str, part: OfficePart, allowed: re.Pattern[str]) -> list[str]:
    texts, tree = _part_texts(part)
    kinds: list[str] = []
    for text in texts:
        kinds.extend(_line_problems(text, allowed))
    if part.xml and tree is None:
        return [f"{name}:{part.label}: 읽을 수 없는 XML"] + [
            f"{name}:{part.label}: {kind}" for kind in dict.fromkeys(kinds)
        ]
    if tree is not None:
        kinds.extend(_people(part.role, tree))
    return [f"{name}:{part.label}: {kind}" for kind in dict.fromkeys(kinds)]


def _people(role: str | None, tree: ElementTree.Element) -> list[str]:
    """문서 속성, 댓글, 변경 기록에서 찾은 사람 이름의 종류(찾은 값은 담지 않는다)."""
    kinds = []
    for element in tree.iter():
        tag = _local(element.tag)
        person_field = (role == CORE and tag in _PERSON_FIELDS) or element.tag == _DC_CREATOR
        if (person_field or element.tag in _ODF_PERSON_FIELDS) and _is_person(_filled(element)):
            kinds.append("문서 속성의 사람 이름")
        if role == APP and tag in _COMPANY_FIELDS and _filled(element):
            kinds.append("문서 속성의 회사·관리자")
        names = [value for key, value in element.attrib.items() if _local(key) == "author"]
        if role == COMMENT_AUTHORS:
            names += [value for key, value in element.attrib.items() if _local(key) == "name"]
        if role == XL_PERSONS:
            names += [v for key, v in element.attrib.items() if _local(key) == "displayName"]
        if role == XL_COMMENTS and tag == "author":
            names.append(_filled(element))
        if any(_is_person(value.strip()) for value in names):
            kinds.append("댓글·변경 기록의 사람 이름")
    return kinds


def _is_person(value: str) -> bool:
    return value not in ("", *ALLOWED_AUTHORS)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _filled(element: ElementTree.Element) -> str:
    return "".join(element.itertext()).strip()


def _privacy_allowlist(root: Path) -> re.Pattern[str] | None:
    try:
        text = (root / PRIVACY_WORKFLOW).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    match = _ALLOWED_LINE.search(text)
    if match is None:
        return None
    try:
        return re.compile(match.group("pattern"))
    except re.error:
        return None


def _is_allowed_email(address: str, allowed: re.Pattern[str]) -> bool:
    domain = address.rsplit("@", 1)[1].lower()
    if domain == ALLOWED_DOMAIN or domain.endswith("." + ALLOWED_DOMAIN):
        return True
    return allowed.search(address) is not None


def _listed_files(root: Path) -> list[str] | None:
    command = ["git", "-C", str(root), "ls-files", "-z"]
    command += ["--cached", "--others", "--exclude-standard"]
    env = {key: value for key, value in os.environ.items() if key not in GIT_VARIABLES}
    try:
        result = subprocess.run(command, capture_output=True, check=True, timeout=60, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    names = result.stdout.decode("utf-8", "replace").split("\0")
    return sorted({name for name in names if name})


def _decode(data: bytes) -> str:
    """BOM이 있으면 그 인코딩으로, 없으면 UTF-8로 푼다(풀리지 않는 바이트는 바꿔 넣는다)."""
    for bom, encoding in BOMS:
        if data.startswith(bom):
            return data.decode(encoding, errors="replace")
    return data.decode("utf-8", errors="replace")


def _read_text(path: Path) -> str | None:
    """글 파일이면 내용, 아니면(없음, BOM 없이 NUL이 든 이진 파일) None."""
    try:
        data = path.read_bytes()
    except OSError:
        return None  # 지웠지만 아직 커밋하지 않은 파일 등
    for bom, encoding in BOMS:
        if data.startswith(bom):
            return data.decode(encoding, errors="replace")
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")
