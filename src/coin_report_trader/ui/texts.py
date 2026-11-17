"""화면에 보이는 글 가운데 `core`에 상수가 없는 것. 글자는 설계서의 칸에 적힌 그대로다.

`core`에 상수가 있는 글(`MARKETS_LOADING`, `MARKETS_FAILED`, `SETTINGS_UNREADABLE`, `SCREEN_TITLES`,
`blocked_markets_text` 등)은 여기에 다시 적지 않고 `coin_report_trader.core`에서 받는다.
"""

from typing import Final

# --- 빈 화면(M0 명세 2절) ---
EMPTY_BODY: Final = "이 화면은 아직 비어 있습니다."
ADVICE_NOTICE: Final = "이 프로그램은 투자 자문이 아닙니다. AI 판단은 틀릴 수 있습니다."
"""설계서 10.2의 가."""

# --- 처음 설정 화면 10 자리(설계서 10.2의 마, 바) ---
NOTICE_DISCORD_OFFLINE: Final = (
    "프로그램이나 PC가 꺼져 있거나 인터넷이 끊기면 디스코드 알림도 가지 않습니다. "
    "인터넷이 끊긴 동안 보내지 못한 알림은 다시 이어져도 디스코드로 가지 않습니다"
    "(알림센터에서 봅니다)."
)
NOTICE_DISCORD_NO_ADDRESS: Final = (
    "디스코드 주소를 넣지 않은 분류의 알림은 디스코드로 가지 않습니다"
    '(오류 알림의 주소가 비어 있으면 오류 멈춤의 "지금 손절이 동작하지 않음"도 가지 않습니다).'
)

# --- 칸 이름, 단추(설계서 3.2, 6.2) ---
COINS_LABEL_REQUIRED: Final = "코인*"
COINS_LABEL: Final = "코인"
RETENTION_LABEL_REQUIRED: Final = "알림 보관 기간*"
RETENTION_LABEL: Final = "알림 보관 기간"
RETENTION_OPTIONS: Final = ((7, "7일"), (30, "30일"), (90, "90일"))
CAUTION_LABEL: Final = "유의·주의"
LOADING_TEXT: Final = "불러오는 중"
RETRY_TEXT: Final = "다시 시도"
START_TEXT: Final = "저장하고 시작"
SAVE_TEXT: Final = "저장"
MOVE_UP_TEXT: Final = "위로"
MOVE_DOWN_TEXT: Final = "아래로"
REMOVE_TEXT: Final = "빼기"

# --- 알림 보관 기간 옆의 글(설계서 8.5) ---
RETENTION_NOTICE_KEEP: Final = "생긴 때부터 고른 기간이 지난 알림은 영구히 지워집니다."
RETENTION_NOTICE_SHORTEN: Final = "기간을 줄이면 새 기간이 지난 알림도 지워집니다."

# --- 처음 설정을 마친 뒤(설계서 3.4, 13.2) ---
FIRST_SETUP_DONE: Final = "처음 설정을 마쳤습니다. 값은 설정 화면에서 고칩니다."
TO_SETTINGS_TEXT: Final = "설정 화면으로"

# --- 처음 설정을 마치기 전의 설정 화면(설계서 6.7) ---
SETTINGS_BEFORE_FIRST_SETUP: Final = "처음 설정을 마치면 값을 고칠 수 있습니다."

# --- 확인 창(설계서 2.6, 6.8) ---
CONFIRM_TEXT: Final = "확인"
CANCEL_TEXT: Final = "취소"
RETENTION_SHRINK_TITLE: Final = "알림 보관 기간을 줄일까요?"


def retention_shrink_body(saved_days: int, new_days: int) -> str:
    """알림 보관 기간을 줄이는 확인 창의 본문(설계서 6.8)."""
    return (
        f"알림 보관 기간을 {saved_days}일에서 {new_days}일로 줄이면, "
        f"생긴 지 {new_days}일이 지난 알림이 바로 영구히 지워집니다. "
        "보고서, 판단, 주문, 체결, 외부 변동의 기록은 지워지지 않습니다."
    )


# --- 현황 화면(설계서 4) ---
STATUS_BEFORE_FIRST_SETUP: Final = "처음 설정을 마치면 여기에 코인별 현황이 보입니다."
NO_CANDLES_YET: Final = "아직 받은 봉이 없습니다."
LAST_CANDLE_INCOMPLETE: Final = "마지막 봉은 미완성입니다"
INDICATOR_DISCLAIMER: Final = (
    "지표는 계산 결과와 AI의 글을 보여 줄 뿐, 어느 쪽이 이득인지 보장하지 않습니다."
)
STATS_TITLE: Final = "기간별 봉 통계"
STATS_COLUMNS: Final = (
    "기간",
    "봉 수",
    "변화",
    "최고가",
    "최저가",
    "최고가 대비",
    "최대 낙폭",
    "변동성",
    "상승 봉 비율",
)
"""표의 머리 칸(설계서 4.2의 8, M1 명세 3.5의 화면 항목)."""


def candles_failed_text(error_name: str | None) -> str:
    """ "봉을 받지 못함" 줄(설계서 4.5)."""
    return f"봉을 받지 못했습니다({error_name})." if error_name else "봉을 받지 못했습니다."


def fetched_text(time_text: str) -> str:
    """ "마지막으로 받은 때" 줄(설계서 4.2의 8)."""
    return f"마지막으로 받은 때 {time_text}"


def count_text(count: int) -> str:
    """표의 봉 수 칸."""
    return f"봉 {count}"


def indicator_row_text(title: str, value: str) -> str:
    """코인 칸의 지표 한 줄(설계서 4.3의 예: "1년 변화 +12.34%")."""
    return f"{title} {value}"
