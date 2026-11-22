"""Claude Code를 부를 때의 환경 변수(M2 명세 3.7.2). 지우고 넣는 목록은 이 파일 한곳에 둔다.

`run()`과 `check()`가 같이 쓴다. 인증 환경 변수의 이름은 이 파일에만 있어야 한다(3.13의 검사).
앱은 구독 로그인을 쓴다: API 키나 토큰, 모델 제공자와 접속 주소를 바꾸는 변수가 구독 로그인보다
앞서므로(authentication 문서의 'Authentication precedence') 모두 지운다. 네트워크 프록시
(`HTTPS_PROXY` 등)는 지우지 않는다.
"""

from collections.abc import Mapping
from typing import Final

AUTH_VARIABLES: Final = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
)
"""인증 값."""

PROVIDER_VARIABLES: Final = (
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_MANTLE",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
    "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
)
"""모델 제공자를 바꾸는 변수."""

BASE_URL_VARIABLES: Final = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_BEDROCK_MANTLE_BASE_URL",
    "ANTHROPIC_VERTEX_BASE_URL",
    "ANTHROPIC_FOUNDRY_BASE_URL",
    "ANTHROPIC_AWS_BASE_URL",
    "ANTHROPIC_GOOGLE_CLOUD_BASE_URL",
)
"""접속 주소를 바꾸는 변수."""

OTHER_AUTH_VARIABLES: Final = (
    "ANTHROPIC_AWS_API_KEY",
    "ANTHROPIC_FOUNDRY_API_KEY",
    "ANTHROPIC_FOUNDRY_AUTH_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
    "ANTHROPIC_PROFILE",
)
"""다른 제공자와 프로필의 인증 변수."""

OVERRIDE_VARIABLES: Final = (
    "CLAUDE_CODE_EFFORT_LEVEL",
    "CLAUDE_CODE_SUBAGENT_MODEL_FORCE",
)
"""사용자가 고른 노력 값과 모델이 덮이지 않게 지우는 변수(서브 에이전트의 `effort`와 `model`보다
앞선다)."""

REMOVED_VARIABLES: Final = (
    *AUTH_VARIABLES,
    *PROVIDER_VARIABLES,
    *BASE_URL_VARIABLES,
    *OTHER_AUTH_VARIABLES,
    *OVERRIDE_VARIABLES,
)
"""지우는 변수 전부."""

ADDED_VARIABLES: Final[Mapping[str, str]] = {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"}
"""넣는 변수: 서브 에이전트를 배경으로 옮기지 않는다. 그래서 코인 세션은 세 서브 에이전트가 끝나기를
기다린 뒤 답을 낸다."""


def build_env(base: Mapping[str, str]) -> dict[str, str]:
    """`base`(앱의 환경)를 복사해 `REMOVED_VARIABLES`를 지우고 `ADDED_VARIABLES`를 넣는다.

    Windows의 환경 변수는 이름의 대소문자를 가리지 않으므로 대소문자 없이 견준다.
    """
    removed = {name.upper() for name in REMOVED_VARIABLES}
    env = {key: value for key, value in base.items() if key.upper() not in removed}
    env.update(ADDED_VARIABLES)
    return env
