"""항목 7. Claude 자격 정보: `src/` 아래에 Claude Code의 자격 정보와 로그인을 가리키는 글이 없다.

M2 명세 3.13(REQ-AI-034): 앱은 Claude Code의 설정 폴더와 자격 정보 파일을 읽거나 쓰지 않고, 그
자리를 코드에 적지 않는다. 로그인은 사용자가 Claude Code에서 한다.

- 대상: 개인정보 항목(`personal_info.py`)과 같은 파일 목록과 같은 읽는 법(`git ls-files`, 커밋하지
  않은 새 파일 포함, BOM을 보고 풀기) 가운데 `src/` 아래의 글 파일. 볼 파일이 하나도 없으면 실패로
  적는다.
- 찾는 글(대소문자를 가리지 않는다):
  - 자격 정보 파일의 이름 `.credentials.json`
  - `setup-token`
  - `auth login`: 두 낱말 사이에 빈칸, 따옴표, 쉼표만 있는 것도 잡는다(`["auth", "login"]`처럼
    명령의 인자 목록으로 적은 것).
- 인증 환경 변수의 이름은 `src/coin_report_trader/ai/claude_env.py`에만 있어야 한다(3.7.2의 지우는
  목록을 한곳에 둔다). 이름은 3.7.2가 인증 변수로 적은 여덟이다: `ANTHROPIC_API_KEY`,
  `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`과, 다른 제공자와 프로필의 인증 변수
  `ANTHROPIC_AWS_API_KEY`, `ANTHROPIC_FOUNDRY_API_KEY`, `ANTHROPIC_FOUNDRY_AUTH_TOKEN`,
  `AWS_BEARER_TOKEN_BEDROCK`, `ANTHROPIC_PROFILE`. 이름 앞뒤에 글자·숫자·밑줄이 붙으면 다른 이름이라
  잡지 않는다(대소문자는 가린다. 환경 변수 이름이다).
- 파일, 줄, 종류를 쓴다.
"""

import re
from pathlib import Path

from personal_info import _listed_files, _read_text

TITLE = "Claude 자격 정보"

SOURCE_PREFIX = "src/"
ENV_FILE = "src/coin_report_trader/ai/claude_env.py"

FORBIDDEN = (
    ("자격 정보 파일의 이름", re.compile(r"\.credentials\.json", re.IGNORECASE)),
    ("setup-token", re.compile(r"setup-token", re.IGNORECASE)),
    ("auth login", re.compile(r"\bauth[\s\"',]+login\b", re.IGNORECASE)),
)
AUTH_VARIABLES = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_AWS_API_KEY",
    "ANTHROPIC_FOUNDRY_API_KEY",
    "ANTHROPIC_FOUNDRY_AUTH_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
    "ANTHROPIC_PROFILE",
)
AUTH_VARIABLE = re.compile(
    r"(?<![A-Za-z0-9_])(?:" + "|".join(AUTH_VARIABLES) + r")(?![A-Za-z0-9_])"
)


def check(root: Path) -> list[str]:
    files = _listed_files(root)
    if files is None:
        return ["git ls-files를 실행하지 못했다(git 저장소가 아니거나 git이 없다)"]
    problems = []
    checked = 0
    for name in files:
        if not name.startswith(SOURCE_PREFIX):
            continue
        text = _read_text(root / name)
        if text is None:
            continue
        checked += 1
        for number, line in enumerate(text.splitlines(), start=1):
            for kind, pattern in FORBIDDEN:
                if pattern.search(line):
                    problems.append(f"{name}:{number}: {kind}")
            if name != ENV_FILE and AUTH_VARIABLE.search(line):
                problems.append(f"{name}:{number}: 인증 환경 변수의 이름({ENV_FILE} 밖)")
    if checked == 0:
        problems.append("src/ 아래에 검사할 글 파일이 하나도 없다")
    return problems
