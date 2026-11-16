"""항목 6. 웹후크 주소: 저장소의 글 파일에 디스코드 웹후크 주소의 꼴(M1 명세 3.9)이 없다.

- 대상: 개인정보 항목(`personal_info.py`)과 같은 파일 목록과 같은 읽는 법이다(`git ls-files`,
  커밋하지 않은 새 파일 포함, BOM을 보고 풀기). `legacy/`와 `packaging/licenses/`만 뺀다.
  `.github/`도 본다. 볼 파일이 하나도 없으면 실패로 적는다.
- 꼴: 호스트(`discord.com`, `discordapp.com`, 앞에 `canary.`나 `ptb.`. 호스트 바로 앞에 글자·숫자·
  `.`·`-`가 오면 다른 호스트라서 잡지 않는다), 경로 `/api/webhooks/`나
  `/api/v숫자/webhooks/`, 숫자로만 된 번호(17자에서 20자), `/`, 영문·숫자·`-`·`_`·`.`로 된
  60자 이상의 토큰. 대소문자는 가리지 않고, JSON에서 이스케이프한 `\\/`도 본다. 앞의 `https://`는
  있어도 없어도 잡는다(토큰이 드러나는 것이 문제이므로). 뒤에 무엇이 이어지든 잡는다.
- 시험의 가짜 주소는 소스에 통째로 적지 않고 실행 중에 이어 붙여 만든다(M1 명세 3.13).
- 찾은 값은 출력하지 않는다. 파일, 줄, 종류만 쓴다.
"""

import re
from pathlib import Path

from personal_info import _listed_files, _read_text

TITLE = "웹후크 주소"

EXCLUDED_PREFIXES = ("legacy/", "packaging/licenses/")

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


def check(root: Path) -> list[str]:
    files = _listed_files(root)
    if files is None:
        return ["git ls-files를 실행하지 못했다(git 저장소가 아니거나 git이 없다)"]
    problems = []
    checked = 0
    for name in files:
        if name.startswith(EXCLUDED_PREFIXES):
            continue
        text = _read_text(root / name)
        if text is None:
            continue
        checked += 1
        for number, line in enumerate(text.splitlines(), start=1):
            if WEBHOOK.search(line):
                problems.append(f"{name}:{number}: 디스코드 웹후크 주소 꼴")
    if checked == 0:
        problems.append("git ls-files가 내놓은 파일 가운데 검사할 글 파일이 하나도 없다")
    return problems
