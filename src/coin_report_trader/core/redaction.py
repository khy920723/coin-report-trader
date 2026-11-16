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


def _webhook_pattern(slash: str) -> str:
    path = "{s}api{s}(?:v[0-9]+{s})?webhooks{s}[0-9]{{17,20}}{s}[A-Za-z0-9._-]{{60,}}"
    return "https:" + slash * 2 + _HOST + path.format(s=slash)


_WEBHOOK_URL = re.compile(_webhook_pattern("/"))
# 가릴 때는 더 넓게 본다: 대소문자를 가리지 않고, JSON에서 이스케이프한 `\/`도 본다.
_FIND_WEBHOOK = re.compile(_webhook_pattern(r"(?:\\?/)"), re.IGNORECASE)


def is_webhook_url(text: str) -> bool:
    """디스코드 웹후크 주소의 꼴인지. 앞뒤 빈칸은 지우고 본다. 빈 글은 거짓이다.

    `https://`, 호스트 `discord.com`이나 `discordapp.com`(앞에 `canary.`나 `ptb.`도 받는다),
    경로 `/api/webhooks/`나 `/api/v숫자/webhooks/`, 번호(숫자 17~20자), `/`, 토큰(영문,
    숫자, `-`, `_`, `.`로만 된 60자 이상). 그 뒤에 아무것도 없다(끝의 `/`, `?`, `#` 없음).
    """
    return _WEBHOOK_URL.fullmatch(text.strip()) is not None


# 경로의 사용자 이름: 공백이 든 이름(`John Smith`)은 공백 하나로 이은 낱말들 바로 뒤에 구분자가
# 올 때만 이름으로 본다. 그 밖에는 한 낱말만 이름으로 보고, 뒤의 글은 그대로 둔다.
_NAME = r"(?:[^\W][\w.-]*(?: [\w.-]+)+(?=[\\/])|[^\W][\w.-]*)"
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
)


def _prefix_pattern(prefix: str) -> re.Pattern[str] | None:
    """환경 변수 값(절대 경로)을 글 속에서 찾는 식. 상대 경로와 루트 폴더는 쓰지 않는다."""
    windows_like = _WINDOWS_LIKE.match(prefix) is not None
    names = [name for name in _SEPARATORS.split(prefix) if name]
    if not names or not (windows_like or prefix[0] in "\\/"):
        return None
    if len(names) == 1 and windows_like and names[0].endswith(":"):
        return None  # 드라이브 루트(`D:\`)
    body = r"[\\/]+".join(re.escape(name) for name in names)
    if prefix[0] in "\\/":
        body = r"[\\/]+" + body
    return re.compile(_BEFORE + body + _AFTER, re.IGNORECASE if windows_like else 0)


def shorten_path(text: str, env: Mapping[str, str] | None = None) -> str:
    """글 속의 경로에서 사용자 이름이 드러나는 앞부분을 줄인다.

    `LOCALAPPDATA` 아래는 `%LOCALAPPDATA%`로, `XDG_DATA_HOME` 아래는 `$XDG_DATA_HOME`으로,
    `HOME` 아래는 `~`로 바꾼다. 그 밖에 드라이브 문자 다음 `Users` 폴더, `/home`, `/Users`
    바로 아래의 사용자 이름도 `~`로 바꾼다. `env`를 빼면 현재 프로세스의 환경 변수를 쓴다.
    """
    env = os.environ if env is None else env
    found = []
    for name, token in _ENV_PREFIXES:
        value = env.get(name, "")
        pattern = _prefix_pattern(value) if value else None
        if pattern is not None:
            found.append((len(value.rstrip("\\/")), pattern, token))
    # 긴 값부터 바꿔야 `~`가 더 구체적인 줄임을 가리지 않는다.
    for _, pattern, token in sorted(found, key=lambda item: item[0], reverse=True):
        text = pattern.sub(lambda _, t=token: t, text)
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
