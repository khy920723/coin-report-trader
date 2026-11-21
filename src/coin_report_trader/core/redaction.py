"""웹후크 주소의 꼴, 비밀 값 가리기, 경로 줄이기.

`redact`와 `shorten_path`는 같은 입력에 운영체제와 상관없이 같은 글을 낸다. 운영체제의 경로
규칙(`pathlib`, `os.sep`)이 아니라 글의 꼴만 보고, 경로의 구분자는 `\\`와 `/`(그리고 그 되풀이,
`str(OSError)`가 쓰는 역슬래시 둘)를 모두 본다.
"""

import os
import re
from collections.abc import Iterable, Mapping
from typing import Final

MASK: Final = "(가려서 보임)"
MAX_REDACTED_LENGTH: Final = 2000

# 웹후크 주소(M1 명세 3.9). 번호는 17자에서 20자의 숫자, 토큰은 60자 이상이다.
_HOST = r"(?:(?:canary|ptb)\.)?(?:discord|discordapp)\.com"


_TOKEN = r"[A-Za-z0-9._-]{60,}"
# 가릴 때의 토큰: 다음 주소의 시작(`https:`)이 나오면 거기서 끝난다. 토큰 글자에는 `:`가 없으므로
# 진짜 토큰 안에는 `https:`가 있을 수 없다. 그래서 바로 붙거나 `.`, `-`, `_`로 이어진 주소도
# 주소마다 따로 가려진다. 끝 조건이 없는 `_TOKEN`은 `is_webhook_url`이 쓴다.
_FIND_TOKEN = r"(?:(?!https:)[A-Za-z0-9._-]){60,}"


def _webhook_pattern(slash: str, token: str) -> str:
    path = "{s}api{s}(?:v[0-9]+{s})?webhooks{s}[0-9]{{17,20}}{s}"
    return "https:" + slash * 2 + _HOST + path.format(s=slash) + token


_WEBHOOK_URL = re.compile(_webhook_pattern("/", _TOKEN))
# 가릴 때는 더 넓게 본다: 대소문자를 가리지 않고, JSON에서 이스케이프한 `\/`나 `\\/`처럼
# `/` 앞에 역슬래시가 몇 개 붙어도 본다.
_FIND_WEBHOOK = re.compile(_webhook_pattern(r"(?:\\*/)", _FIND_TOKEN), re.IGNORECASE)


def is_webhook_url(text: str) -> bool:
    """디스코드 웹후크 주소의 꼴인지. 앞뒤 빈칸은 지우고 본다. 빈 글은 거짓이다.

    `https://`, 호스트 `discord.com`이나 `discordapp.com`(앞에 `canary.`나 `ptb.`도 받는다),
    경로 `/api/webhooks/`나 `/api/v숫자/webhooks/`, 번호(숫자 17~20자), `/`, 토큰(영문,
    숫자, `-`, `_`, `.`로만 된 60자 이상). 그 뒤에 아무것도 없다(끝의 `/`, `?`, `#` 없음).
    """
    return _WEBHOOK_URL.fullmatch(text.strip()) is not None


# 경로의 사용자 이름. 구분자(`/`, `\`) 바로 앞까지가 이름이면 이름 전체를 가린다.
# Windows 사용자 이름에는 공백(둘 이상도), `(`, `)`, `&`, `!`, `#`, `'` 같은 글자가 들 수 있다.
# - 낱말(`_CHUNK`): 공백 문자(`\s`)와 Windows 사용자 이름에 쓸 수 없는 글자
#   (`" / \ [ ] : ; | = , + * ? < > @`)만 뺀 글자들이다. 공백 문자가 아닌 제어 글자(U+0007,
#   U+001B, U+007F 등)는 낱말에 들고, 이름 뒤에 구분자가 이어지면 이름에 든다.
# - 낱말 사이(`_GAP`): 탭과 줄바꿈(`\n`, `\r`, U+0085, U+2028, U+2029 등)이 아닌 공백 문자
#   하나 이상이다. ASCII 공백, NBSP, 전각 공백, 가는 공백이 든다.
# - 이름: 낱말들을 `_GAP`으로 이은 것 바로 뒤에 구분자가 올 때 그 전체다. 아니면 첫 낱말의
#   맨 앞에서 `[\w.-]`가 이어지는 데까지만(첫 글자는 `[^\W]`) 이름으로 보고 뒤의 글은 그대로
#   둔다. 제어 글자는 낱말 사이(`_GAP`)와 이 끝 규칙에서만 이름에 들지 않는다.
_CHUNK = r"[^\s\\/\"\[\]:;|=,+*?<>@]+"
_GAP = r"[^\S\t\n\v\f\r\x1c-\x1f\x85\u2028\u2029]+"
_NAME = r"(?:" + _CHUNK + r"(?:" + _GAP + _CHUNK + r")*(?=[\\/])|[^\W][\w.-]*)"
_BEFORE = r"(?<![\w.~-])"
_WINDOWS_USERS = re.compile(_BEFORE + r"[A-Za-z]:[\\/]+Users[\\/]+" + _NAME, re.IGNORECASE)
_UNIX_USERS = re.compile(_BEFORE + r"[\\/]+(?:home|Users)[\\/]+" + _NAME, re.IGNORECASE)
_AFTER = r"(?![\w.-])"
_WINDOWS_LIKE = re.compile(r"[A-Za-z]:|[\\/]{2}")
_SEPARATORS = re.compile(r"[\\/]+")

# (환경 변수 이름, 대신 쓸 글)
_ENV_PREFIXES: Final = (
    ("LOCALAPPDATA", "%LOCALAPPDATA%"),
    ("XDG_DATA_HOME", "$XDG_DATA_HOME"),
    ("HOME", "~"),
    ("USERPROFILE", "~"),
)
# 사용자 폴더의 값. 값 바로 뒤에 폴더 이름이 이어지는 글(`_CONTINUATION`)이 구분자까지 이어지면
# 그 값만 `~`로 바꾸지 않고, 이름 찾기(`Users` 바로 아래, 또는 값의 부모 폴더 바로 아래)가 이어진
# 이름 전체를 한 번에 줄이게 한다.
_USER_FOLDER_VARIABLES: Final = frozenset({"HOME", "USERPROFILE"})
_CONTINUATION = r"(?:" + _GAP + r")?" + _CHUNK + r"(?:" + _GAP + _CHUNK + r")*[\\/]"


def _prefix_pattern(prefix: str, *, user_folder: bool = False) -> re.Pattern[str] | None:
    """환경 변수 값(절대 경로)을 글 속에서 찾는 식. 상대 경로와 루트 폴더는 쓰지 않는다.

    `user_folder`이면(`HOME`, `USERPROFILE`) 값 바로 뒤에 폴더 이름이
    구분자까지 이어지는 글은 찾지 않는다.
    """
    windows_like = _WINDOWS_LIKE.match(prefix) is not None
    names = [name for name in _SEPARATORS.split(prefix) if name]
    if not names or not (windows_like or prefix[0] in "\\/"):
        return None
    if len(names) == 1 and windows_like and names[0].endswith(":"):
        return None  # 드라이브 루트(`D:\`)
    body = r"[\\/]+".join(re.escape(name) for name in names)
    if prefix[0] in "\\/":
        body = r"[\\/]+" + body
    after = _AFTER + (r"(?!" + _CONTINUATION + r")" if user_folder else "")
    return re.compile(_BEFORE + body + after, re.IGNORECASE if windows_like else 0)


def _sibling_pattern(value: str) -> re.Pattern[str] | None:
    """`HOME`, `USERPROFILE`의 값 아래의 이름을 `Users` 바로 아래처럼 찾는 식(3.10).

    값의 부모 폴더를 사용자 폴더가 모인 폴더로 보고 그 바로 아래의 이름(`_NAME`)을 찾는다.
    부모가 루트(`/`)나 드라이브 루트(`D:\\`)이면 이름이 값의 폴더 이름으로 시작하는 폴더만 찾는다.
    """
    windows_like = _WINDOWS_LIKE.match(value) is not None
    names = [name for name in _SEPARATORS.split(value) if name]
    if not names or not (windows_like or value[0] in "\\/"):
        return None
    drive_root = len(names) == 1 and windows_like and names[0].endswith(":")
    if drive_root:
        return None  # 드라이브 루트 자체
    parents = names[:-1]
    root = not parents or (len(parents) == 1 and parents[0].endswith(":"))
    body = r"[\\/]+".join(re.escape(name) for name in parents)
    if value[0] in "\\/":
        body = r"[\\/]+" + body
    lead = _BEFORE + body + (r"[\\/]+" if parents else "")
    if root:  # 이름이 값의 폴더 이름으로 시작하는 폴더만
        lead += "(?=" + re.escape(names[-1]) + ")"
    return re.compile(lead + _NAME, re.IGNORECASE if windows_like else 0)


def shorten_path(text: str, env: Mapping[str, str] | None = None) -> str:
    """글 속의 경로에서 사용자 이름이 드러나는 앞부분을 줄인다.

    `LOCALAPPDATA` 아래는 `%LOCALAPPDATA%`로, `XDG_DATA_HOME` 아래는 `$XDG_DATA_HOME`으로,
    `HOME`과 `USERPROFILE` 아래는 `~`로 바꾼다. 그 두 값 바로 뒤에 다른 폴더 이름이 구분자까지
    이어지면(`{값} Work\\{파일}`) 값만 `~`로 바꾸지 않고, 아래의 이름 찾기가 이어진 이름 전체를
    `~` 하나로 줄인다. 그 밖에 드라이브 문자 다음 `Users` 폴더, `/home`, `/Users` 바로 아래의
    사용자 이름도 `~`로 바꾼다. `env`를 빼면 현재 프로세스의 환경 변수를 쓴다.
    """
    env = os.environ if env is None else env
    found = []
    siblings: list[re.Pattern[str]] = []
    for name, token in _ENV_PREFIXES:
        value = env.get(name, "")
        user_folder = name in _USER_FOLDER_VARIABLES
        pattern = _prefix_pattern(value, user_folder=user_folder)
        if user_folder and value:
            sibling = _sibling_pattern(value)
            if sibling is not None:
                siblings.append(sibling)
        if pattern is not None:
            found.append((len(value.rstrip("\\/")), pattern, token))
    # 긴 값부터 바꿔야 `~`가 더 구체적인 줄임을 가리지 않는다.
    for _, pattern, token in sorted(found, key=lambda item: item[0], reverse=True):
        text = pattern.sub(lambda _, t=token: t, text)
    for sibling in siblings:
        text = sibling.sub("~", text)
    text = _WINDOWS_USERS.sub("~", text)
    return _UNIX_USERS.sub("~", text)


def redact(
    text: str,
    secrets: Iterable[str] = (),
    env: Mapping[str, str] | None = None,
) -> str:
    """오류 글, 거래소 응답, 디스코드 응답을 화면, 기록, 로그, 알림에 쓰기 전에 거치는 함수.

    1. 웹후크 주소의 꼴(글 가운데에서 찾아 `https://`부터 토큰 끝까지)과 넘겨받은 비밀 값을
       "(가려서 보임)"으로 바꾼다.
    2. 경로를 `shorten_path`로 줄인다.
    3. 2,000자에서 자른다.
    """
    text = _FIND_WEBHOOK.sub(MASK, text)
    # 긴 값부터 바꿔야 다른 값을 품은 비밀 값이 반쪽만 남지 않는다.
    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
        text = text.replace(secret, MASK)
    return shorten_path(text, env)[:MAX_REDACTED_LENGTH]
