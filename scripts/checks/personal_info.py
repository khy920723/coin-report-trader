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
- Office 파일(이름이 `.pptx`, `.docx`, `.xlsx`로 끝나는 파일): ZIP으로 열어, 안의 부분 가운데
  이름이 `.xml`이나 `.rels`로 끝나는 것을 모두 글로 풀고 위의 이메일·경로 검사를 그대로 건다. 부분의
  XML 그대로의 글과, XML의 글 마디를 이은 글(한 낱말이 여러 마디로 나뉜 경우) 둘 다 본다. 슬라이드,
  메모, 댓글, 표의 글이 여기에 든다.
  - 문서 속성: `docProps/core.xml`의 작성자(`dc:creator`)와 마지막 수정자(`cp:lastModifiedBy`)가
    비어 있지 않고 허용 이름(`ALLOWED_AUTHORS`)도 아니면 실패다. `docProps/app.xml`의 회사
    (`Company`)와 관리자(`Manager`)는 비어 있지 않으면 실패다.
  - ZIP이 아니거나 깨져서 열 수 없는 Office 파일, 읽거나 풀 수 없는 부분은 실패다.
  - 보지 않는 것: 그림과 미디어(예: `ppt/media/`)와 그 밖의 이진 부분은 글로 볼 수 없어서 검사하지
    않는다. 안에 넣은 다른 파일(예: `ppt/embeddings/`의 이진 개체)도 열지 않는다.
- 찾은 값은 출력하지 않는다(공개 기록에 다시 남지 않게). 파일, 줄(Office 파일은 안의 부분 이름),
  종류만 쓴다.
"""

import codecs
import os
import re
import subprocess
import xml.etree.ElementTree as ElementTree
import zipfile
import zlib
from pathlib import Path

TITLE = "개인정보"

EXCLUDED_PREFIXES = ("legacy/", "packaging/licenses/", ".github/")
PRIVACY_WORKFLOW = ".github/workflows/checks.yml"
ALLOWED_DOMAIN = "example.com"

# checks.yml privacy가 메시지에서 주소를 뽑는 식과 같다([[:alnum:]]는 ASCII로 본다).
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.ASCII)
# 이름 자리는 글자·숫자·밑줄로 시작해야 한다. 앞에 글자가 붙은 경로(`/srv/home/...`)는 빼려고
# 바로 앞이 글자·숫자·`.`·`-`·`~`가 아닌 것만 본다.
_NAME = r"[^\W][\w.-]*"
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

OFFICE_SUFFIXES = (".pptx", ".docx", ".xlsx")
TEXT_PART_SUFFIXES = (".xml", ".rels")
ALLOWED_AUTHORS = frozenset({"Claude", "python-pptx", "coin-report-trader"})
"""문서 속성의 작성자·마지막 수정자로 허용하는 이름."""
_CORE_PART = "docProps/core.xml"
_APP_PART = "docProps/app.xml"
_DC = "{http://purl.org/dc/elements/1.1/}"
_CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
_EP = "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}"
_PERSON_FIELDS = (_DC + "creator", _CP + "lastModifiedBy")
_COMPANY_FIELDS = (_EP + "Company", _EP + "Manager")


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
    """Office 파일 안의 글 부분과 문서 속성을 본다(모듈 설명)."""
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile, ValueError):
        return [f"{name}: 열 수 없는 Office 파일(ZIP이 아니거나 깨졌다)"]
    problems: list[str] = []
    with archive:
        for info in archive.infolist():
            part = info.filename
            if info.is_dir() or not part.lower().endswith(TEXT_PART_SUFFIXES):
                continue
            try:
                data = archive.read(info)
            except (OSError, zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError):
                problems.append(f"{name}:{part}: 읽을 수 없는 부분")
                continue
            problems.extend(_check_part(name, part, _decode(data), allowed))
    return problems


def _check_part(name: str, part: str, text: str, allowed: re.Pattern[str]) -> list[str]:
    kinds: list[str] = []
    for line in text.splitlines():
        kinds.extend(_line_problems(line, allowed))
    try:
        tree = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return [f"{name}:{part}: 읽을 수 없는 XML"] + [f"{name}:{part}: {k}" for k in kinds]
    kinds.extend(_line_problems("".join(tree.itertext()), allowed))
    if part == _CORE_PART:
        kinds.extend(
            "문서 속성의 사람 이름"
            for element in tree.iter()
            if element.tag in _PERSON_FIELDS and _filled(element) not in ("", *ALLOWED_AUTHORS)
        )
    if part == _APP_PART:
        kinds.extend(
            "문서 속성의 회사·관리자"
            for element in tree.iter()
            if element.tag in _COMPANY_FIELDS and _filled(element)
        )
    return [f"{name}:{part}: {kind}" for kind in dict.fromkeys(kinds)]


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
