"""화면과 조립이 같이 쓰는 글: 단계 이름, 알림 번호, 오류 이름, 알림과 칸 아래의 글.

M1 명세 3.9절과 화면 설계서(8.4, 12)의 글이다. `{시각}`처럼 값이 들어가는 글은 함수로 둔다.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

# --- 단계 이름("단계"에 쓴다) ---
STAGE_MARKETS: Final = "코인 목록 받기"
STAGE_CANDLES: Final = "봉 받기"
STAGE_CANDLE_SAVE: Final = "봉 저장"
STAGE_STATS: Final = "봉 통계 계산"
STAGE_SETTINGS_SAVE: Final = "설정 저장"
STAGE_ALERT_SAVE: Final = "알림 저장"
STAGE_ALERT_DELETE: Final = "알림 지우기"
STAGE_WEBHOOK_SAVE: Final = "주소 저장"
STAGE_WEBHOOK_DELETE: Final = "주소 지우기"
STAGE_STARTUP: Final = "켜기"
STAGES: Final = (
    STAGE_MARKETS,
    STAGE_CANDLES,
    STAGE_CANDLE_SAVE,
    STAGE_STATS,
    STAGE_SETTINGS_SAVE,
    STAGE_ALERT_SAVE,
    STAGE_ALERT_DELETE,
    STAGE_WEBHOOK_SAVE,
    STAGE_WEBHOOK_DELETE,
    STAGE_STARTUP,
)

# --- 알림 번호(설계서 12) ---
CODE_CANDLES_FAILED: Final = "A-1"
CODE_SAVE_FAILED: Final = "A-29"
CODE_UNEXPECTED: Final = "A-30"
CODE_MARKETS_FAILED: Final = "A-31"

# --- 오류 이름: 봉 받기와 코인 목록 받기가 만나는 것(3.9) ---
ERROR_NETWORK: Final = "네트워크 끊김"
ERROR_TIMEOUT: Final = "시간 초과"
ERROR_RATE_LIMIT: Final = "요청 한도 초과"
ERROR_BLOCKED: Final = "일시 차단"
ERROR_MAINTENANCE: Final = "거래소 점검"
ERROR_SERVER: Final = "서버 오류"
ERROR_BAD_RESPONSE: Final = "응답 형식 오류"
ERROR_NOT_ENOUGH_CANDLES: Final = "봉이 모자람"


def error_unknown_exchange(status: int) -> str:
    return f"알 수 없는 거래소 오류({status})"


# --- 오류 이름: 보관소와 디스코드 ---
ERROR_VAULT_UNAVAILABLE: Final = "보관소를 쓸 수 없음"
ERROR_VAULT: Final = "보관소 오류"
ERROR_DISCORD_TIMEOUT: Final = "디스코드 시간 초과"
ERROR_DISCORD_NETWORK: Final = "디스코드 네트워크 끊김"
ERROR_DISCORD_RATE_LIMIT: Final = "디스코드 한도 초과"
ERROR_WEBHOOK_UNREADABLE: Final = "주소를 읽지 못함"


def error_discord_rejected(status: int) -> str:
    return f"디스코드 거절({status})"


# --- 알림의 글(앞에 코인을 붙이지 않은 글) ---
def text_candles_failed(error_name: str) -> str:
    """A-1(M1에서 쓰는 모습. M1 명세 2의 2)."""
    return f"봉 통계를 만들지 못했습니다({error_name})."


def text_save_failed(stage: str, error_name: str) -> str:
    """A-29. 단계가 "알림 지우기"나 "주소 지우기"이면 "지우지 못했습니다"다."""
    verb = "지우지" if stage in (STAGE_ALERT_DELETE, STAGE_WEBHOOK_DELETE) else "저장하지"
    return f"{verb} 못했습니다({error_name})."


def text_unexpected(error_name: str) -> str:
    """A-30."""
    return f"예상하지 못한 오류가 났습니다({error_name})."


def text_markets_failed(error_name: str) -> str:
    """A-31."""
    return f"코인 목록을 받지 못했습니다({error_name})."


# --- 그 뒤에 한 일(3.9) ---
def after_candles_retry(time_text: str) -> str:
    return f"다음 받는 때({time_text})에 다시 받습니다."


def after_candles_blocked(time_text: str) -> str:
    return f"{time_text}까지 봉 받기를 멈췄습니다."


AFTER_MARKETS_FAILED: Final = "처음 설정이나 설정 화면에서 다시 시도할 수 있습니다."
AFTER_SAVE_FAILED: Final[Mapping[str, str]] = MappingProxyType(
    {
        STAGE_SETTINGS_SAVE: "저장하지 못한 값은 화면에 남겨 두었습니다.",
        STAGE_CANDLE_SAVE: "다음 받는 때에 다시 받아 저장합니다.",
        STAGE_ALERT_SAVE: "이 알림은 이번 실행 동안 보이고, 다음에 켤 때 알림센터에 옮깁니다.",
        STAGE_ALERT_DELETE: "지우지 못한 알림은 남겨 두었습니다.",
        STAGE_WEBHOOK_SAVE: "주소는 저장되지 않았습니다. 다시 넣어 저장하세요.",
        STAGE_WEBHOOK_DELETE: "저장된 주소는 그대로 남아 있습니다.",
        STAGE_STARTUP: "창을 띄우지 못하고 끝냈습니다. 다음에 켤 때 다시 시도합니다.",
    }
)
AFTER_UNEXPECTED: Final = "그 일을 그만두고 다음 일을 계속합니다."

# --- 화면의 글 ---
WEBHOOK_BAD_FORMAT: Final = "디스코드 웹후크 주소의 형식이 아닙니다."
WEBHOOK_NOT_SET: Final = "디스코드 주소를 넣지 않은 분류의 알림은 디스코드로 가지 않습니다."
WEBHOOK_SAVE_FAILED: Final = "저장하지 못했습니다."
WEBHOOK_DELETE_FAILED: Final = "지우지 못했습니다. 저장된 주소는 그대로 남아 있습니다."
TEST_SENDING: Final = "확인용 글을 보내는 중입니다."
TEST_SENT: Final = "확인용 글을 보냈습니다. 디스코드 채널을 확인하세요."
TEST_FAILED: Final = "확인용 글을 보내지 못했습니다."
TEST_MESSAGE_PREFIX: Final = "[Coin Report Trader] 확인용 글"
"""확인용 글의 정해진 첫머리. 가짜 디스코드가 이것으로 알림과 확인용 글을 가른다."""

SETTINGS_COIN_NOT_LISTED: Final = (
    "코인 목록에 없는 코인이 있어 저장하지 않았습니다. 코인 목록을 다시 받은 뒤 저장하세요."
)
CANDLES_LOADING: Final = "봉을 받는 중입니다."


def test_message_text(category_title: str) -> str:
    """확인용 글. 비밀 값, 금액, 잔고는 넣지 않는다."""
    return f"{TEST_MESSAGE_PREFIX}입니다. 이 채널로 {category_title}이 옵니다."


def blocked_candles_text(time_text: str) -> str:
    """차단 중에 현황 화면의 "다시 시도"를 눌렀을 때(M1 명세 2의 3)."""
    return f"거래소가 잠시 요청을 막았습니다. {time_text} 뒤에 다시 받습니다."


def blocked_markets_text(time_text: str) -> str:
    """차단 중에 코인 목록의 "다시 시도"를 눌렀을 때(설계서 3.4)."""
    return f"거래소가 잠시 요청을 막았습니다. {time_text} 뒤에 다시 시도할 수 있습니다."
