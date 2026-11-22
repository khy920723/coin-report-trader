"""화면과 조립이 같이 쓰는 글: 단계 이름, 알림 번호, 오류 이름, 알림과 칸 아래의 글.

M1 명세 3.9절과 화면 설계서(8.4, 12)의 글이다. `{시각}`처럼 값이 들어가는 글은 함수로 둔다.
"""

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Final

from coin_report_trader.core.clock import KST
from coin_report_trader.core.market import CANDLE_UNIT_TITLES, CandleUnit

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
STAGE_PRICE: Final = "현재가 읽기"
STAGE_REPORT: Final = "AI 보고서"
STAGE_REPORT_SAVE: Final = "보고서 저장"
STAGE_ACCOUNT: Final = "계정 읽기"
STAGE_ORDER: Final = "주문"
STAGE_ORDER_SAVE: Final = "주문 저장"
STAGE_STOP_LOSS: Final = "손절"
STAGE_SELL_ALL: Final = "긴급 매도"
STAGE_RECONCILE: Final = "거래소와 맞춰 보기"
STAGE_BACKUP: Final = "DB 백업"
STAGE_PAUSE_SAVE: Final = "멈춤 저장"
STAGE_KEY_SAVE: Final = "업비트 키 저장"
STAGE_LIVE_ON: Final = "실거래 켜기"
STAGE_EXCHANGE_ACCOUNT: Final = STAGE_ACCOUNT
"""M3의 `STAGE_ACCOUNT`와 같은 글을 쓴다(M5 명세 3.4)."""
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
    STAGE_PRICE,
    STAGE_REPORT,
    STAGE_REPORT_SAVE,
    STAGE_ACCOUNT,
    STAGE_ORDER,
    STAGE_ORDER_SAVE,
    STAGE_STOP_LOSS,
    STAGE_SELL_ALL,
    STAGE_RECONCILE,
    STAGE_BACKUP,
    STAGE_PAUSE_SAVE,
)

# --- 알림 번호(설계서 12) ---
CODE_CANDLES_FAILED: Final = "A-1"
CODE_SAVE_FAILED: Final = "A-29"
CODE_UNEXPECTED: Final = "A-30"
CODE_MARKETS_FAILED: Final = "A-31"
CODE_PRICE_FAILED: Final = "A-2"
CODE_REPORT_FAILED: Final = "A-17"
CODE_LOGGED_OUT: Final = "A-18"
CODE_CLAUDE_OFF: Final = "A-19"
CODE_REPORT_DONE: Final = "A-26"
CODE_TOO_SMALL: Final = "A-3"
CODE_PRICE_GUARD: Final = "A-4"
CODE_STOP_FAILED: Final = "A-8"
CODE_NO_AVG: Final = "A-9"
CODE_BUDGET: Final = "A-10"
CODE_NO_KRW: Final = "A-11"
CODE_DAILY_LOSS: Final = "A-12"
CODE_CAUTION: Final = "A-13"
CODE_MIN_ORDER_CHANGED: Final = "A-15"
CODE_COIN_REMOVED: Final = "A-16"
CODE_ERROR_PAUSE: Final = "A-23"
CODE_UNKNOWN_EXCHANGE: Final = "A-24"
CODE_SELL_ALL_LEFT: Final = "A-25"
CODE_ORDER_PLACED: Final = "A-27"
CODE_FILLED: Final = "A-28"
CODE_DIRECT_LOCKED: Final = "A-6"
CODE_DIRECT_CANCELED: Final = "A-7"
CODE_LIVE_OFF: Final = "A-20"
CODE_EXTERNAL: Final = "A-22"
CODE_KEY_EXPIRY: Final = "A-32"

# --- 오류 이름: 봉 받기와 코인 목록 받기가 만나는 것(3.9) ---
ERROR_NETWORK: Final = "네트워크 끊김"
ERROR_TIMEOUT: Final = "시간 초과"
ERROR_RATE_LIMIT: Final = "요청 한도 초과"
ERROR_BLOCKED: Final = "일시 차단"
ERROR_MAINTENANCE: Final = "거래소 점검"
ERROR_SERVER: Final = "서버 오류"
ERROR_BAD_RESPONSE: Final = "응답 형식 오류"
ERROR_NOT_ENOUGH_CANDLES: Final = "봉이 모자람"
ERROR_STORAGE: Final = "DB 오류"
"""`storage/`가 낸 `StorageError`의 오류 이름(3.9). 단계와 상관없이 이 하나다."""


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


# --- 오류 이름과 글: 2단계 AI 보고서(M2 명세 3.4) ---
# `FailureKind`의 다섯 값("한도 초과", "로그인 만료", "형식 오류", "시간 초과", "검색 실패")이
# 8절의 실패 오류 이름이다(`core/ai.py`).
ERROR_CLAUDE_NOT_FOUND: Final = "Claude Code를 찾지 못함"
ERROR_VERSION_UNREADABLE: Final = "버전을 읽지 못함"


def error_version_out_of_range(version: str) -> str:
    return f"버전 범위 밖({version})"


AI_MARK_TEXT: Final = "AI가 쓴 글입니다. 틀릴 수 있습니다."
"""보고서의 AI 표시(설계서 5.2의 4, 10절)."""
REPORT_UNREADABLE: Final = "보고서를 읽지 못했습니다."


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


# --- 알림의 내용(`AlertDetail.content`, 3.9) ---
def content_candles_failed(unit: CandleUnit) -> str:
    """A-1(요청의 실패)의 내용: 실패한 봉의 단위 이름(예: "실패한 봉: 60분봉")."""
    return f"실패한 봉: {CANDLE_UNIT_TITLES[CandleUnit(unit)]}"


def content_not_enough_candles(have: int, count: int) -> str:
    """A-1("봉이 모자람")의 내용: 완성된 봉이 고른 개수보다 적어 봉 통계를 셈하지 못했다."""
    return (
        f"완성된 봉이 고른 개수({count}개)보다 적어 봉 통계를 셈하지 못했습니다"
        f"(완성된 봉 {have}개)."
    )


CONTENT_BLOCKED: Final = "거래소가 공개 시세 요청을 잠시 막았습니다."
CONTENT_BLOCKED_NOT_REQUESTED: Final = (
    "앞 코인에서 일시 차단을 받아 이 코인은 요청하지 않았습니다."
)
CONTENT_MARKETS_FAILED: Final = "코인 목록 요청이 실패했습니다."

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
        STAGE_REPORT_SAVE: (
            "AI를 껐습니다. 봉 받기는 계속하고, 프로그램을 다시 켜면 AI가 다시 돕니다."
        ),
        STAGE_ORDER_SAVE: "쓰지 못한 주문은 내지 않았고, 쓰지 못한 체결은 반영하지 않았습니다.",
        STAGE_PAUSE_SAVE: (
            "이번 실행에서는 멈춤 상태를 메모리에 지켜 새 주문을 내지 않습니다."
            " 다시 켜면 저장된 마지막 상태로 시작합니다."
        ),
        STAGE_BACKUP: (
            "백업을 만들거나 오래된 백업을 지우지 못했습니다. 다음 하루가 바뀌는 때에 다시 합니다."
        ),
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
MARKETS_LOADING: Final = "코인 목록을 불러오는 중입니다."
MARKETS_FAILED: Final = "코인 목록을 받지 못했습니다. 네트워크를 확인한 뒤 다시 시도하세요."
SETTINGS_UNREADABLE: Final = "설정을 읽지 못했습니다. 60분마다 다시 읽습니다."
SETTINGS_SAVED: Final = "저장했습니다."
SETTINGS_SAVE_FAILED: Final = "저장하지 못했습니다."
AUTOSTART_REGISTER_FAILED: Final = "시작 앱에 등록하지 못했습니다."
AUTOSTART_REMOVE_FAILED: Final = "시작 앱에서 지우지 못했습니다."


def kst_time_text(moment: datetime) -> str:
    """`{시각}`의 글: 한국 시간의 시와 분을 24시간제 `HH:MM`으로(날짜는 넣지 않는다)."""
    if moment.utcoffset() is None:
        raise ValueError("시간대가 없는 시각은 글로 바꿀 수 없다")
    return moment.astimezone(KST).strftime("%H:%M")


def test_message_text(category_title: str) -> str:
    """확인용 글. 비밀 값, 금액, 잔고는 넣지 않는다."""
    return f"{TEST_MESSAGE_PREFIX}입니다. 이 채널로 {category_title}이 옵니다."


def kst_datetime_text(moment: datetime) -> str:
    """`{날짜와 시각}`의 글: 한국 시간의 `YYYY-MM-DD HH:MM`(초는 넣지 않는다)."""
    if moment.utcoffset() is None:
        raise ValueError("시간대가 없는 시각은 글로 바꿀 수 없다")
    local = moment.astimezone(KST)
    return f"{local.year:04d}-{local.month:02d}-{local.day:02d} {local.hour:02d}:{local.minute:02d}"


def kst_when_text(moment: datetime, now: datetime | None) -> str:
    """지금 가까이의 때를 가리키는 `{시각}`의 글.

    `moment`와 `now`의 한국 날짜가 같으면 `HH:MM`, 다르면 `YYYY-MM-DD HH:MM`이다.
    `now`가 `None`이면 `HH:MM`이다. 시간대가 없는 값이 있으면 `ValueError`다.
    """
    time_text = kst_time_text(moment)
    if now is None:
        return time_text
    if now.utcoffset() is None:
        raise ValueError("시간대가 없는 시각은 글로 바꿀 수 없다")
    if moment.astimezone(KST).date() == now.astimezone(KST).date():
        return time_text
    return kst_datetime_text(moment)


def blocked_candles_text(time_text: str) -> str:
    """차단 중에 현황 화면의 "다시 시도"를 눌렀을 때(M1 명세 2의 3)."""
    return f"거래소가 잠시 요청을 막았습니다. {time_text} 뒤에 다시 받습니다."


def blocked_markets_text(time_text: str) -> str:
    """차단 중에 코인 목록의 "다시 시도"를 눌렀을 때(설계서 3.4)."""
    return f"거래소가 잠시 요청을 막았습니다. {time_text} 뒤에 다시 시도할 수 있습니다."
