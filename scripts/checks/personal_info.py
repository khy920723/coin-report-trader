"""항목 4. 개인정보: 저장소의 글 파일에 개인 이메일 주소나 사용자 폴더 경로가 없다.

- 대상: `git ls-files`의 파일(아직 커밋하지 않은 새 파일도 넣는다. `.gitignore`가 빼는 것은 뺀다)
  중 글 파일. `legacy/`, `packaging/licenses/`, `.github/`는 보지 않는다. 커밋 이메일은 privacy
  검사(`.github/workflows/checks.yml`)가 본다.
- 이메일: privacy 검사의 허용 목록(`checks.yml`의 `allowed=`를 읽는다)과 `example.com` 주소는
  허용한다.
- 경로: `C:\\Users\\<이름>`, `/home/<이름>`, `/Users/<이름>` 꼴. `<이름>`이나 `$USER`처럼 글자나
  숫자로 시작하지 않는 자리표시는 잡지 않는다.
- 찾은 값은 출력하지 않는다(공개 기록에 다시 남지 않게). 파일, 줄, 종류만 쓴다.
"""

import re
import subprocess
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
_ALLOWED_LINE = re.compile(r"""^\s*allowed='(?P<pattern>[^']+)'\s*$""", re.MULTILINE)


def check(root: Path) -> list[str]:
    allowed = _privacy_allowlist(root)
    if allowed is None:
        return [f"{PRIVACY_WORKFLOW}: privacy 검사의 허용 목록(allowed=)을 읽지 못했다"]
    files = _listed_files(root)
    if files is None:
        return ["git ls-files를 실행하지 못했다(git 저장소가 아니거나 git이 없다)"]
    problems = []
    for name in files:
        if name.startswith(EXCLUDED_PREFIXES):
            continue
        text = _read_text(root / name)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if any(not _is_allowed_email(m.group(), allowed) for m in EMAIL.finditer(line)):
                problems.append(f"{name}:{number}: 허용 목록 밖의 이메일 주소")
            if any(pattern.search(line) for pattern in USER_DIRS):
                problems.append(f"{name}:{number}: 사용자 폴더 경로")
    return problems


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
    try:
        result = subprocess.run(command, capture_output=True, check=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    names = result.stdout.decode("utf-8", "replace").split("\0")
    return sorted({name for name in names if name})


def _read_text(path: Path) -> str | None:
    """글 파일이면 내용, 아니면(없음, 이진, UTF-8 아님) None."""
    try:
        data = path.read_bytes()
    except OSError:
        return None  # 지웠지만 아직 커밋하지 않은 파일 등
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None
