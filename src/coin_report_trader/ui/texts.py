"""화면에 보이는 글 가운데 `core`에 상수가 없는 것. 글자는 설계서의 칸에 적힌 그대로다.

`core`에 상수가 있는 글(`MARKETS_LOADING`, `MARKETS_FAILED`, `SETTINGS_UNREADABLE`, `SCREEN_TITLES`,
`blocked_markets_text` 등)은 여기에 다시 적지 않고 `coin_report_trader.core`에서 받는다.
"""

from decimal import Decimal
from typing import Final

from coin_report_trader.core import CANDLE_COUNTS, INSUFFICIENT_TEXT, CandleUnit, HistoryTab

# --- 빈 화면(M0 명세 2절) ---
EMPTY_BODY: Final = "이 화면은 아직 비어 있습니다."
ADVICE_NOTICE: Final = "이 프로그램은 투자 자문이 아닙니다. AI 판단은 틀릴 수 있습니다."
"""설계서 10.2의 가."""

# --- 처음 설정 화면 10 자리(설계서 10.2의 마, 바) ---
NOTICES_TITLE: Final = "꼭 읽을 글"
"""처음 설정 10 자리 카드의 제목. 세 글은 이 제목 옆 'ⓘ'의 툴팁이다(M1 명세 3.14.6)."""
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

# --- 설정의 자동 시작 칸(M1 명세 3.16.1) ---
AUTOSTART_LABEL: Final = "PC를 켜면 자동으로 시작"
AUTOSTART_INFO: Final = (
    "켜 두면 Windows에 로그인할 때 이 프로그램이 저절로 켜집니다. "
    "작업 관리자의 '시작 앱'에서 끈 것은 이 칸이 되살리지 않습니다."
)
AUTOSTART_UNSUPPORTED: Final = (
    "이 실행 방식에서는 고를 수 없습니다. 내려받은 exe로 켤 때만 고를 수 있습니다."
)
UNIT_LABEL: Final = "봉 단위*"
COUNT_LABEL: Final = "봉 개수*"
CANDLE_CHOICE_NOTE: Final = "봉 통계를 셈할 봉의 단위와 개수입니다."
"""두 칸 아래 글(M1 명세 3.14.1)."""
COUNT_OPTION_SUFFIX: Final = "개"
RETENTION_OPTIONS: Final = ((7, "7일"), (30, "30일"), (90, "90일"))
CAUTION_LABEL: Final = "유의·주의"
LOADING_TEXT: Final = "불러오는 중"
RETRY_TEXT: Final = "다시 시도"
START_TEXT: Final = "모의매매 시작"
SAVE_TEXT: Final = "저장"
MOVE_UP_TEXT: Final = "위로"
MOVE_DOWN_TEXT: Final = "아래로"
REMOVE_TEXT: Final = "빼기"

# --- 2단계: 모델·노력 값 칸, 처음 설정과 설정의 글(M2 명세 3.12.2, 3.12.3, 3.12.5) ---
AI_CARD_TITLE_REQUIRED: Final = "Claude 모델*"
AI_CARD_TITLE: Final = "Claude 모델"
AI_USAGE_NOTE: Final = "앱의 AI 사용은 이 PC 사용자의 Claude 구독 사용 한도에서 빠집니다."
"""칸 아래 글(3.12.2)이자 10 자리의 아(3.12.3)."""
NOTICE_PERSONAL: Final = (
    "개인용입니다. 쓰는 사람은 각자 자기 업비트 키와 자기 Claude Code 로그인으로, "
    "자기 책임으로 씁니다. 아무것도 보증하지 않습니다. 팔거나 돈을 받고 넘기지 않습니다."
)
"""10 자리의 사(3.12.3)."""
FIRST_SETUP_INTRO: Final = (
    '아래 값을 고른 뒤 "모의매매 시작"을 누르세요. 별표(*)가 붙은 칸은 꼭 골라야 합니다.'
)
"""2 자리의 안내 글(M3 명세 3.17의 표). 제목 옆 'ⓘ'의 툴팁이다."""
START_REASON: Final = (
    "꼭 넣는 칸을 모두 채우고 '꼭 읽을 글을 읽었습니다'를 고르면 누를 수 있습니다."
)
"""모의매매 시작을 누를 수 없을 때 단추 곁의 까닭(M3 명세 3.11.3)."""
SETTINGS_INTRO: Final = "값을 고친 뒤 저장을 누르면 적용됩니다."
AI_PARTIAL_WARNING: Final = "네 칸의 모델과 노력 값을 모두 골라야 저장됩니다."
REVERT_TEXT: Final = "되돌리기"


def ai_model_label(slot_title: str, *, required: bool) -> str:
    return f"{slot_title} 모델" + ("*" if required else "")


def ai_effort_label(slot_title: str, *, required: bool) -> str:
    return f"{slot_title} 노력 값" + ("*" if required else "")


# --- 현황 화면의 보고서 보기(3.12.4)와 알림의 가 보기(3.12.6) ---
REPORT_VIEW_TEXT: Final = "보고서 보기"
GO_TEXT: Final = "가 보기"

# --- 보고서 화면(M2 명세 3.12.1) ---
REPORT_COIN_TITLE: Final = "코인"
REPORT_ROUNDS_TITLE: Final = "회차"
AI_NOT_CHOSEN: Final = "설정에서 네 칸의 모델과 노력 값을 고르면 다음 회차부터 보고서를 만듭니다."
AI_NOT_FOUND: Final = "Claude Code를 찾지 못했거나 버전을 읽지 못해 AI를 껐습니다."
AI_VERSION_OFF: Final = "Claude Code 버전이 시험한 범위 밖이라 AI를 껐습니다."
AI_ERROR_OFF: Final = "예상하지 못한 오류로 AI를 껐습니다. 프로그램을 다시 켜면 AI가 다시 돕니다."
REPORT_NONE: Final = "아직 받은 보고서가 없습니다."
REPORT_RUNNING: Final = "이 회차의 보고서를 만드는 중입니다."
REPORT_BEFORE_FIRST_SETUP: Final = "처음 설정을 마치면 보고서를 볼 수 있습니다."
REPORT_INTERRUPTED: Final = "이 회차는 끝까지 가지 못했습니다(끊긴 회차)."
REPORT_LAST_RESULT_TITLE: Final = "지난 판단의 결과"
REPORT_CHECKS_TITLE: Final = "검증 목록"
REPORT_CHECK_COLUMNS: Final = ("항목", "믿음", "견준 횟수", "맞은 횟수")
REPORT_DECISION_TITLE: Final = "판단"
REPORT_EVIDENCE_TITLE: Final = "근거"
REPORT_FINAL_PART: Final = "최종 예측"
REPORT_SOURCES_TITLE: Final = "출처"
REPORT_USAGE_TITLE: Final = "사용량과 걸린 시간"
REPORT_USAGE_NOTE: Final = "부분마다의 토큰은 나눠 재지 못했습니다."
REPORT_NOTICES_TITLE: Final = "꼭 읽을 글"
REPORT_PROGRAM_VALUES: Final = "프로그램이 계산한 값"
NO_VALUE: Final = "-"


def report_failed_text(failure: str | None) -> str:
    return f"이 회차에 이 코인의 보고서를 받지 못했습니다: {failure if failure else NO_VALUE}"


def report_skipped_text(reason: str | None) -> str:
    return f"이 회차에 이 코인은 쉬었습니다: {reason if reason else NO_VALUE}"


def report_round_text(number: int, when: str, end: str) -> str:
    return f"회차 {number} · {when} · {end}"


def report_compared_title(round_number: int) -> str:
    return f"근거를 견준 결과(회차 {round_number}의 근거)"


def report_last_price_text(round_number: int, value: str) -> str:
    return f"지난 예측 가격(회차 {round_number}가 예측한 다음 회차 때의 값): {value}"


def report_tokens_text(
    input_tokens: int, output_tokens: int, cache_read: int, cache_creation: int
) -> str:
    return (
        f"세션 전체 토큰: 입력 {input_tokens:,} · 출력 {output_tokens:,} · "
        f"캐시 읽기 {cache_read:,} · 캐시 만들기 {cache_creation:,}"
    )


REPORT_PAUSE_LINE: Final = "멈춤 동안 새 보고서를 받지 않습니다."
REPORT_DISCARDED: Final = "이 회차는 멈춤으로 버렸습니다."


def report_order_text(summary: str) -> str:
    return f"이 판단으로 낸 주문: {summary}"


def report_no_order_text(reason: str) -> str:
    return f"주문하지 않은 까닭: {reason}"


def report_order_elapsed_text(seconds: int) -> str:
    return f"주문까지 걸린 시간: {seconds // 60}분 {seconds % 60}초"


def report_elapsed_text(seconds: int) -> str:
    return f"보고서까지 걸린 시간: {seconds // 60}분 {seconds % 60}초"


# --- 코인 고르기의 찾기 칸(M1 명세 3.14.3) ---
SEARCH_LABEL: Final = "찾기"
SEARCH_PLACEHOLDER: Final = "코인 기호나 한글 이름"
NO_COIN_FOUND: Final = "찾는 코인이 없습니다."

# --- 예전 기록일 때의 처음 설정(M1 명세 3.14.4) ---
LEGACY_NOTICE: Final = (
    '모의매매에 필요한 칸이 새로 생겼습니다. 빈 칸을 넣고 "꼭 읽을 글을 읽었습니다"를 고른 뒤 '
    '"모의매매 시작"을 누르세요. 그 전에는 회차를 돌리지 않습니다.'
)

# --- 봉 개수의 툴팁(M1 명세 3.14.2) ---
_HIT_RATE_ANY: Final = "50~56%"
_HIT_RATES: Final = {
    None: _HIT_RATE_ANY,
    CandleUnit.MINUTE_30: _HIT_RATE_ANY,
    CandleUnit.MINUTE_60: "50~55%",
    CandleUnit.MINUTE_240: _HIT_RATE_ANY,
    CandleUnit.DAY: "48~53%",
}
_COUNT_MEANINGS: Final = {
    100: "근거가 받쳐 주는 가장 적은 개수입니다.",
    200: "거래소에서 한 번에 받는 가장 많은 개수입니다.",
    500: "더 긴 기간을 보지만 요청이 3번 들고 오래된 구간이 섞입니다.",
}
_UNIT_NOT_CHOSEN: Final = "봉 단위를 고르면 보입니다."
_PERIODS: Final = {
    CandleUnit.MINUTE_30: {100: "약 2.1일", 200: "약 4.2일", 500: "약 10.4일"},
    CandleUnit.MINUTE_60: {100: "약 4.2일", 200: "약 8.3일", 500: "약 20.8일"},
    CandleUnit.MINUTE_240: {100: "약 16.7일", 200: "약 33일", 500: "약 83일"},
    CandleUnit.DAY: {100: "100일", 200: "약 6.6개월", 500: "약 1.4년"},
}


def candle_count_tooltip(unit: CandleUnit | None, count: int) -> str:
    """봉 개수의 툴팁: 예상 적중률(참고값), 뜻, 덮는 기간의 세 줄(M1 명세 3.14.2).

    `unit`이 `None`이면 봉 단위를 고르기 전의 글이다. `count`가 `CANDLE_COUNTS`에 없으면
    `ValueError`다.
    """
    if count not in CANDLE_COUNTS:
        raise ValueError(f"고를 수 없는 봉 개수: {count}")
    period = _UNIT_NOT_CHOSEN if unit is None else _PERIODS[unit][count]
    return "\n".join(
        (
            f"예상 적중률(참고값): {_HIT_RATES[unit]}. 공개 연구에서 오르내림을 맞힌 비율이고, "
            "이 앱이 잰 값이 아닙니다. 수익을 뜻하지 않습니다.",
            f"뜻: {_COUNT_MEANINGS[count]}",
            f"덮는 기간: {period}",
        )
    )


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
INDICATOR_DISCLAIMER: Final = "봉 통계는 계산 결과일 뿐이고, 이득을 보장하지 않습니다."
"""보장 아님 글(M1 명세 3.14.5). 코인 칸의 요소 아래와 봉 통계 자리의 표 아래에 보인다."""
STATS_TITLE: Final = "봉 통계"
STATS_TABLE_COLUMNS: Final = ("항목", "값")


def stats_header_text(unit_title: str, count: int) -> str:
    """코인 칸의 첫 줄(M1 명세 3.14.5)."""
    return f"{unit_title} {count}개"


def stats_short_header_text(unit_title: str, bar_count: int, count: int) -> str:
    """완성된 봉이 고른 개수보다 적을 때의 코인 칸 첫 줄."""
    return f"{unit_title} {INSUFFICIENT_TEXT}(완성된 봉 {bar_count}개 / 고른 개수 {count}개)"


def stats_summary_text(unit_title: str, count: int) -> str:
    """봉 통계 자리의 표 위 글."""
    return f"{unit_title} · 고른 개수 {count}개"


def bar_count_text(bar_count: int, count: int, *, short: bool) -> str:
    """표의 "쓴 봉 수" 값."""
    return f"{bar_count}개(고른 개수 {count}개)" if short else f"{bar_count}개"


def candles_failed_text(error_name: str | None) -> str:
    """ "봉을 받지 못함" 줄(설계서 4.5)."""
    return f"봉을 받지 못했습니다({error_name})." if error_name else "봉을 받지 못했습니다."


def fetched_text(time_text: str) -> str:
    """ "마지막으로 받은 때" 줄(설계서 4.2의 8)."""
    return f"마지막으로 받은 때 {time_text}"


def indicator_row_text(title: str, value: str) -> str:
    """코인 칸의 요소 한 줄(예: "전체 변화 +12.34%")."""
    return f"{title} {value}"


# --- 알림센터(설계서 8) ---
ALERT_DELETE_TEXT: Final = "지우기"
ALERT_CLEAR_TEXT: Final = "이 탭 모두 지우기"
WEBHOOK_SAVE_TEXT: Final = "저장"
WEBHOOK_CHANGE_TEXT: Final = "바꾸기"
WEBHOOK_DELETE_TEXT: Final = "지우기"
WEBHOOK_CANCEL_TEXT: Final = "그만두기"
WEBHOOK_TEST_TEXT: Final = "확인용 글 보내기"
WEBHOOK_EMPTY_TEXT: Final = "넣지 않음"
WEBHOOK_SAVED_SUFFIX: Final = " 저장됨"
DISCORD_FAILED_TEXT: Final = "디스코드로 보내지 못함"
WEBHOOK_UNREADABLE: Final = "주소를 읽지 못했습니다."
"""`WebhookView.unreadable`일 때 주소 칸 아래(M2 명세 3.12.7). 늘 보이는 오류 글이다."""
UNSEEN_DOT: Final = "●"
ALERTS_UNREADABLE: Final = "알림을 읽지 못했습니다."


def webhook_title_text(category_title: str) -> str:
    """주소 칸의 이름(설계서 8.4)."""
    return f"{category_title} 디스코드 웹후크 주소"


def no_alerts_text(category_title: str) -> str:
    """알림이 없는 탭의 목록 자리 글(설계서 8.7)."""
    return f"{category_title}이 없습니다."


def tab_title_text(category_title: str, unseen: int) -> str:
    """탭 이름. 아직 보지 않은 알림이 있으면 뒤에 빈칸과 "({n})"(M2 명세 3.12.7)."""
    return f"{category_title} ({unseen})" if unseen > 0 else category_title



DETAIL_STAGE: Final = "단계"
DETAIL_COIN: Final = "코인"
DETAIL_ERROR_NAME: Final = "오류 이름"
DETAIL_CONTENT: Final = "내용"
DETAIL_AFTER: Final = "그 뒤에 한 일"

# --- 알림센터의 확인 창 셋(설계서 8.8) ---
ALERT_DELETE_TITLE: Final = "알림을 지울까요?"
ALERT_DELETE_BODY: Final = (
    "이 알림 한 줄을 지웁니다. 지운 알림은 되살릴 수 없습니다. "
    "보고서, 판단, 주문, 체결, 외부 변동의 기록과 디스코드로 이미 보낸 글은 그대로 남습니다."
)
ALERT_CLEAR_TITLE: Final = "이 탭의 알림을 모두 지울까요?"
WEBHOOK_DELETE_TITLE: Final = "디스코드 웹후크 주소를 지울까요?"


def alert_clear_body(category_title: str, count: int) -> str:
    """탭 전체 지우기의 본문. `count`는 그 탭의 알림 전부의 수(본 것과 보지 않은 것을 합친다)."""
    return (
        f"{category_title} {count}건을 모두 지웁니다. 지운 알림은 되살릴 수 없습니다. "
        "보고서, 판단, 주문, 체결, 외부 변동의 기록과 디스코드로 이미 보낸 글은 그대로 남습니다."
    )


def webhook_delete_body(category_title: str) -> str:
    return (
        f"{category_title}의 디스코드 웹후크 주소를 지우면, "
        "이 분류의 알림은 디스코드로 가지 않습니다."
    )


# --- 3단계: 처음 설정과 설정의 칸(M3 명세 3.11, 3.17.5) ---
READ_LABEL: Final = "꼭 읽을 글을 읽었습니다"
NOTICE_STOP_OFF: Final = "PC가 꺼져 있으면 손절이 없습니다."
"""10 자리의 나(M3 명세 3.17)."""
NOTICE_AUTO_ORDER: Final = "주문 전에 확인하는 단계 없이, 판단에 따라 자동으로 주문합니다."
"""10 자리의 라(M3 명세 3.17)."""
CAUTION_NOTE: Final = "이 코인은 유의·주의 표시가 있어 새로 사지 않습니다."
BUDGET_TITLE: Final = "예산과 투자 비율"
RATIO_TITLE: Final = "매수 비율"
LIMITS_TITLE: Final = "손절과 한도"
OTHER_TITLE: Final = "그 밖의 값"
BUDGET_INFO: Final = (
    "코인 예산은 예산 × 투자 비율입니다.",
    "고른 코인의 투자 비율을 합하면 100%여야 합니다. 예산이 고른 코인 전체의 매수 상한입니다.",
)
RATIO_INFO: Final = (
    "허용 범위: 판단의 주문 가격(추세추종은 제안가, 평균회귀는 예측 가격)에서 이 % 안의 가격이면 "
    "사고팝니다. 살 때는 주문 가격보다 이 %만큼 높은 가격까지, 팔 때는 이 %만큼 낮은 가격까지 "
    "겁니다.",
    "0%이면 주문 가격 그대로 겁니다. 0%에서 2%까지 넣습니다.",
    "모의 성적의 '예측 차이 평균'에서 예측 가격과 실제 값의 차이를 볼 수 있습니다.",
    "모의매매 수수료율: 살 때와 팔 때 각각 0.05%",
)
LIMITS_INFO: Final = "모의매매를 시작하기 위한 예시 값이고 투자 판단이 아닙니다."
METHOD_TIPS: Final = {
    "mean_reversion": (
        "싸게 사서 비싸게 팔기. 보유 중에 '내릴 것'이면 손실 중이어도 예산 안에서 더 삽니다."
    ),
    "trend": (
        "오를 때 사고 내릴 때 팔기. 보유 중에는 '오를 것'이고 현재가가 평균 매수가보다 "
        "높을 때만 더 삽니다."
    ),
}

# 칸 이름(별표는 꼭 넣는 칸, `{코인}`은 코인 기호)
BUDGET_LABEL: Final = "예산(원)"
COIN_RATIO_LABEL: Final = "{coin} 투자 비율(%)"
FIRST_RATIO_LABEL: Final = "추세추종 첫 매수 비율(%)"
MR_FIRST_RATIO_LABEL: Final = "평균회귀 첫 매수 비율(%)"
ADD_RATIO_LABEL: Final = "추가 한 번의 비율(%)"
TOLERANCE_LABEL: Final = "허용 범위(%)"
STOP_RATIO_LABEL: Final = "손절 비율(%)"
DAILY_LOSS_LABEL: Final = "하루 손실 한도(원)"
ORDER_INTERVAL_LABEL: Final = "같은 코인 주문 간격(초)"
METHOD_LABEL: Final = "매매 방식"
PAUSE_STOP_LABEL: Final = "'멈춤' 중 손절"
MR_SELL_LABEL: Final = "'팔지'"
MR_LOSS_LABEL: Final = "감내할 손해(%)"
PAPER_FUNDS_LABEL: Final = "모의매매 가상 자금(원)"
BACKUP_DAYS_LABEL: Final = "DB 백업을 남기는 날 수(일)"
YES_TEXT: Final = "예"
NO_TEXT: Final = "아니오"

DEFAULT_STOP_RATIO: Final = "5"
DEFAULT_DAILY_LOSS: Final = "3,000"
DEFAULT_ORDER_INTERVAL: Final = "60"

# 화면 쪽 검사의 글(M3 명세 3.11.2)
FIELD_REQUIRED: Final = "이 값을 넣어야 합니다."
FIELD_NOT_NUMBER: Final = "숫자로 넣으세요."
FIELD_TWO_PLACES: Final = "소수 둘째 자리까지 넣으세요."
FIELD_INTEGER: Final = "정수로 넣으세요."

# SCR-06-6, SCR-06-5(M3 명세 3.17.5, 설계서 3.5)
HOLDING_REMOVE_TITLE: Final = "보유 중인 코인을 뺄까요?"
BELOW_STOP_TITLE: Final = "손절선 아래인 보유가 있습니다"


def holding_remove_body(coin: str) -> str:
    return (
        f"{coin}을 목록에서 빼면 프로그램이 건 주문을 취소하고 이 코인을 더는 다루지 않습니다. "
        "보유는 그대로 남고, 이 코인의 손절도 없어집니다."
    )


def below_stop_body(coin: str, avg_price: str, price: str, stop_line: str) -> str:
    return (
        f"{coin}의 보유가 손절선 아래에 있습니다(평균 매수가 {avg_price}원, 현재가 {price}원, "
        f"손절선 {stop_line}원). 이 코인을 다루기 시작하면 바로 시장가로 팔립니다. "
        "다루기 시작할까요?"
    )


def coin_budget_text(amount: str | None) -> str:
    return f"코인 예산 {amount}원" if amount is not None else "코인 예산 -"


def ratio_sum_text(total: str) -> str:
    return f"합계 {total}%"


def buy_amounts_text(first: str | None, add: str | None) -> str:
    first_text = f"{first}원" if first is not None else "-"
    add_text = f"{add}원" if add is not None else "-"
    return f"첫 매수 금액 {first_text}, 추가 한 번 {add_text}"


def price_text(value: Decimal) -> str:
    """가격: 쉼표를 붙이고 뒤의 0은 뗀다(예: 1,000, 0.0012)."""
    return format(value.normalize(), ",f")


# --- 3단계: 상태 띠, 긴급 정지 두 단추, SCR-06-7, 상태 줄(M3 명세 3.17.1) ---
BAND_MODE_PAPER: Final = "모의매매"
BAND_MODE_LIVE: Final = "실거래"
BAND_PAUSE_USER: Final = "멈춤(사용자)"
BAND_PAUSE_ERROR: Final = "오류 멈춤"
BAND_PAUSE_BOTH: Final = "멈춤(사용자) · 오류 멈춤"
RESUME_USER_TEXT: Final = "멈춤 풀기"
RESUME_ERROR_TEXT: Final = "오류 멈춤 풀기"
BAND_STOP_OFF: Final = "지금 손절이 동작하지 않음"
PAUSE_TEXT: Final = "멈춤"
SELL_ALL_TEXT: Final = "멈추고 전량 매도"
SELL_ALL_TITLE: Final = "멈추고 전량 매도할까요?"
SELL_ALL_BODY: Final = (
    "멈춤 상태로 들어가고 프로그램이 건 주문을 취소한 뒤, 보유 코인을 그때 시세로 모두 팝니다. "
    "직접 걸어 둔 주문에 묶인 수량은 팔지 못합니다."
)
SELL_ALL_BLOCKED_NOTE: Final = "오류 멈춤을 푼 뒤 누를 수 있습니다."


def band_alerts_text(count: int) -> str:
    return f"알림 {count}건"


def resume_wait_text(when: str) -> str:
    return f"{when} 뒤에 풀 수 있습니다."


def status_line(version: str, middle: str, data_dir: str) -> str:
    return f"판 v{version} · {middle} · 자료 폴더 {data_dir}"


STATUS_PAUSED_MIDDLE: Final = "멈춤 동안 회차를 돌리지 않음"
STATUS_BEFORE_SETUP_MIDDLE: Final = "처음 설정 전"


def status_next_round_middle(when: str | None) -> str:
    return f"다음 예정 시각 {when if when is not None else '-'}"


# --- 3단계: 현황(M3 명세 3.17.2) ---
TODAY_TITLE: Final = "오늘 요약"
ROUND_NOW_TITLE: Final = "지금 회차"
PERFORMANCE_TITLE: Final = "모의 성적"
PERFORMANCE_INFO: Final = "실거래를 켜는 기준이 아닙니다. 판단에 참고하는 값입니다."
PERFORMANCE_COLUMNS: Final = (
    "코인",
    "판단 회차",
    "오름·내림 맞음",
    "예측 차이 평균",
    "예측 차이 크기 평균",
    "실현 손익",
    "이익 낸 매도",
    "평가 손익",
    "자산 변화",
)
PERFORMANCE_TOTAL: Final = "합계"
NO_JUDGMENT_YET: Final = "아직 판단이 없습니다."
HOLDING_NONE: Final = "보유 없음"
AVG_PRICE_NONE: Final = "평균 매수가 없음"
DUST_TEXT: Final = "팔 수 없는 잔량"
NO_OPEN_ORDER: Final = "걸린 주문 없음"
UNCONFIRMED_TEXT: Final = "확인 안 됨"
PAUSED_ROUNDS_TEXT: Final = "멈춤 동안 회차를 돌리지 않습니다."


def error_pause_line(kind: str, uncanceled: tuple[str, ...]) -> str:
    listing = ", ".join(uncanceled) if uncanceled else "없음"
    return f"오류 멈춤: {kind}. 지금 손절이 동작하지 않습니다. 취소하지 못한 주문: {listing}"

ERROR_PAUSE_KINDS: Final = {
    "exchange": "일시 차단",
    "unexpected": "예상하지 못한 오류",
    "save": "저장 실패",
}
"""오류 멈춤의 {오류 종류}(`ErrorPauseCause`의 값에서. M3 명세 3.10.2)."""

# --- 3단계의 기록 화면(M3 명세 3.15, 3.17.4) ---
HISTORY_COIN_CAPTION: Final = "코인"
HISTORY_DAY_CAPTION: Final = "날짜"
HISTORY_ALL_COINS: Final = "모든 코인"
HISTORY_ALL_COINS_FILE: Final = "모든코인"
"""내보내기 파일 이름의 "모든코인"(M3 명세 3.15.2)."""
HISTORY_EXPORT_TEXT: Final = "CSV로 내보내기"
HISTORY_EXPORT_OK: Final = "내보냈습니다."
HISTORY_EXPORT_FAILED: Final = "내보내지 못했습니다."
"""결과 글은 `SaveResult.message`가 오면 그것이고, 오지 않을 때만 쓴다(`ok`로 가른다)."""
HISTORY_EXPORT_FILTER: Final = "CSV (*.csv)"
HISTORY_TAB_TITLES: Final = {
    HistoryTab.ROUNDS: "회차",
    HistoryTab.JUDGMENTS: "판단",
    HistoryTab.ORDERS: "주문",
    HistoryTab.FILLS: "체결",
    HistoryTab.EXTERNAL: "외부 변동",
    HistoryTab.PAUSES: "멈춤",
}
"""탭 여섯의 글(SCR-04-4)."""
HISTORY_LOADING: Final = "불러오는 중"
HISTORY_NONE: Final = "이 날의 기록이 없습니다."
HISTORY_FAILED: Final = "기록을 읽지 못했습니다."
HISTORY_REPORT_TEXT: Final = "보고서 보기"
HISTORY_STILL_PAUSED: Final = "멈춰 있음"
HISTORY_BEFORE_NEXT: Final = "다음 회차 전"


def history_export_name(tab_title: str, market_symbol: str | None, today_text: str) -> str:
    """파일 저장 창의 처음 이름: "{탭 이름}-{코인 기호 또는 모든코인}-{YYYYMMDD}.csv"."""
    symbol = market_symbol if market_symbol else HISTORY_ALL_COINS_FILE
    return f"{tab_title}-{symbol}-{today_text}.csv"

# --- 5단계의 설정: 업비트 키, 실거래 설정, 모드, 확인 창(M5 명세 3.11) ---
KEY_TITLE: Final = "업비트 키"
KEY_INFO: Final = (
    "업비트 키: 자산조회, 주문조회, 주문하기 권한만. 출금 권한은 주지 않습니다.",
    "허용 IP 등록과 고정 IP인지 확인",
    "서브포켓을 쓸지 결정",
    "Claude Code 업데이트와 다시 로그인",
    "약관과 정책 확인",
    "키는 Windows 자격 증명 관리자에만 둡니다. 화면과 파일에는 남지 않습니다.",
    "출금 권한을 주지 마세요.",
    "키는 1년 동안 쓸 수 있고 연장할 수 없습니다.",
)
"""설계서 6.6의 '실거래 전에 준비할 것' 다섯 줄과 M5 명세 3.11의 셋."""
KEY_PRESENT: Final = "넣음"
KEY_ABSENT: Final = "넣지 않음"
KEY_SET_TEXT: Final = "키 넣기"
KEY_DELETE_TEXT: Final = "키 지우기"
KEY_LOCKED_NOTE: Final = "멈춤 상태에서만 키를 고칠 수 있습니다."
KEY_LIVE_LOCKED_NOTE: Final = "실거래 중에는 키를 고칠 수 없습니다. 먼저 실거래를 끄세요."
KEY_DIALOG_TITLE: Final = "업비트 키 넣기"
KEY_ACCESS_LABEL: Final = "Access Key"
KEY_SECRET_LABEL: Final = "Secret Key"
KEY_CREATED_LABEL: Final = "키를 만든 날"
KEY_SHAPE_ERROR: Final = "키의 꼴이 맞지 않습니다."
KEY_DATE_ERROR: Final = "만든 날이 오늘보다 뒤입니다."
KEY_MIN_LENGTH: Final = 20
KEY_MAX_LENGTH: Final = 100
KEY_EXPIRY_WARN_DAYS: Final = 30


def key_created_text(day: str | None) -> str:
    return f"만든 날: {day if day else NO_VALUE}"


def key_expires_text(day: str | None) -> str:
    return f"만료일: {day if day else NO_VALUE}"


LIVE_TITLE: Final = "실거래 설정"
LIVE_BUDGET_LABEL: Final = "실거래 예산(원)"
LIVE_COIN_RATIO_LABEL: Final = "{coin} 실거래 투자 비율(%)"
LIVE_DAILY_LOSS_LABEL: Final = "실거래 하루 손실 한도(원)"
LIVE_INFO: Final = (
    "실거래에서만 쓰는 값입니다. 모의매매의 예산, 투자 비율, 하루 손실 한도와 따로 저장됩니다.",
    "실거래를 켜기 전에 넣어야 합니다. 실거래 중에는 고칠 수 없습니다.",
    "실거래 예산은 실제 계정의 원화 잔고와 고른 코인 보유의 평가 금액의 합보다 클 수 없습니다.",
)
LIVE_LOCKED_NOTE: Final = "실거래를 끈 뒤에 고칠 수 있습니다."
PAPER_ONLY_NOTE: Final = "모의매매에서만 바꿀 수 있습니다."
LIVE_NEW_COIN_NOTE: Final = (
    "실거래 중에는 새 코인을 고를 수 없습니다. 모의매매로 돌아가 모의 회차를 채우세요."
)

MODE_TITLE: Final = "모드"
MODE_PAPER_TEXT: Final = "지금 모드: 모의매매"
MODE_LIVE_TEXT: Final = "지금 모드: 실거래"
LIVE_ON_TEXT: Final = "실거래 켜기"
LIVE_OFF_TEXT: Final = "모의매매로 바꾸기"
ROUNDS_N_LABEL: Final = "실거래 전 모의 회차 수 N"
ROUNDS_N_VALUES: Final = (10, 30, 100, 200)
ROUNDS_N_DEFAULT: Final = 30
ROUNDS_N_TIPS: Final = {
    10: "오름·내림 적중률의 95% 구간: 약 ±31%p",
    30: "오름·내림 적중률의 95% 구간: 약 ±18%p",
    100: "오름·내림 적중률의 95% 구간: 약 ±10%p",
    200: "오름·내림 적중률의 95% 구간: 약 ±7%p",
}
"""N의 툴팁(9절. 제품 규칙 9절의 95% 구간)."""
MODE_INFO: Final = (
    "고른 코인마다 모의 회차를 N회 채워야 합니다.",
    "모의매매에서 '멈추고 전량 매도'를 한 번 해야 합니다.",
    "업비트 키가 있어야 합니다.",
    "실거래 설정(예산, 코인별 투자 비율, 하루 손실 한도)을 넣어야 하고, 실거래 예산은 원화 잔고와 "
    "보유 평가 금액의 합보다 클 수 없습니다.",
    "모드는 멈춤 상태에서만 바꿀 수 있습니다.",
    "실거래에서도 손절, 하루 손실 한도, 가격 안전 검사가 그대로 걸립니다.",
    "실거래에서는 주문 전에 확인하는 단계 없이 판단대로 주문합니다.",
    ADVICE_NOTICE,
)
"""앞의 넷은 9절의 켜는 조건이다."""


def live_count_line(coin: str, counted: int, needed: int) -> str:
    return f"{coin} {counted}/{needed}회"


def sell_all_record_text(done: bool) -> str:
    state = "했음" if done else "하지 않음"
    return f"모의매매에서 '멈추고 전량 매도': {state}"


LIVE_CONFIRM_TITLE: Final = "실거래를 켤까요?"
LIVE_CONFIRM_BODY: Final = (
    "업비트 계정으로 실제 주문을 냅니다. 멈춤을 풀면 다음 회차부터 판단대로 사람의 확인 없이 "
    "주문합니다. 손절과 한도는 그대로 걸립니다. 이 프로그램은 투자 자문이 아니고 AI 판단은 "
    "틀릴 수 있습니다."
)
LIVE_CONFIRM_OK: Final = "실거래 켜기"
LIVE_OFF_CONFIRM_TITLE: Final = "모의매매로 바꿀까요?"
LIVE_OFF_CONFIRM_BODY: Final = (
    "프로그램이 건 실거래 주문을 취소합니다. 남은 실제 보유에는 손절이 없어집니다."
)
LIVE_OFF_CONFIRM_OK: Final = "바꾸기"
KEY_DELETE_CONFIRM_TITLE: Final = "업비트 키를 지울까요?"
KEY_DELETE_CONFIRM_BODY: Final = (
    "Windows 자격 증명 관리자에서 키를 지웁니다. 다시 쓰려면 키를 새로 넣어야 합니다."
)
KEY_DELETE_CONFIRM_OK: Final = "지우기"


def live_confirm_lines(
    budget: str,
    coins: tuple[tuple[str, str, str], ...],
    krw: str,
    holdings: tuple[tuple[str, str, str | None], ...],
    total: str,
) -> tuple[str, ...]:
    """SCR-06-9 표의 줄(글은 모두 만들어 넘긴다). `coins`는 (코인, 비율, 금액), `holdings`는
    (코인, 수량, 평가 금액 또는 `None`)이다. 보유가 없으면 "보유 없음"이다."""
    lines = [f"실거래 예산 {budget}원"]
    lines += [f"{coin}: {ratio}% · {amount}원" for coin, ratio, amount in coins]
    lines.append(f"읽은 원화 잔고(주문 가능) {krw}원")
    for coin, qty, value in holdings:
        if qty == "":
            lines.append(f"{coin} 보유: {HOLDING_NONE}")
        else:
            lines.append(f"{coin} 보유: {qty} · 평가 {value if value is not None else NO_VALUE}원")
    lines.append(f"원화 잔고와 보유 평가의 합 {total}원")
    return tuple(lines)


def live_read_at_text(when: str) -> str:
    return f"{when}에 읽은 값"


# --- 기록과 보고서의 모드 고르기, 외부 변동(M5 명세 3.11) ---
MODE_PAPER_RECORD: Final = "모의매매 기록"
MODE_LIVE_RECORD: Final = "실거래 기록"
LIVE_KRW_LABEL: Final = "원화"
"""현황 오늘 요약의 실거래 줄 "원화 {금액}원"(가상 원화 대신)."""

# --- 5단계의 계정 화면(M5 명세 3.15) ---
ACCOUNT_REFRESH_TEXT: Final = "새로 고침"
ACCOUNT_GO_KEY_TEXT: Final = "업비트 키 넣으러 가기"
ACCOUNT_SUMMARY_TITLE: Final = "실제 계정"
ACCOUNT_SUMMARY_INFO: Final = (
    "업비트 계정에서 읽은 값입니다. 모의매매의 가상 계정은 현황에 있습니다.",
    "이 화면은 보기만 하고 주문하지 않습니다. "
    "실제 보유의 손절은 실거래 중에 고른 코인에만 걸립니다.",
    "계정 화면만 쓰려면 키에 자산조회 권한만 있으면 됩니다.",
    "원화 마켓이 없는 코인과 계좌 조회에 들지 않는 자산은 총자산에 들지 않아 업비트 앱의 값과 "
    "다를 수 있습니다.",
)
ACCOUNT_BEFORE_SETUP: Final = "처음 설정을 마치면 계정을 볼 수 있습니다."
ACCOUNT_NO_KEY: Final = "업비트 키가 없습니다"
ACCOUNT_NO_HOLDINGS: Final = "보유한 코인이 없습니다."
ACCOUNT_BLOCKED: Final = "오류 멈춤(일시 차단) 동안에는 계정을 읽지 않습니다."
ACCOUNT_SUMMARY_LABELS: Final = {
    "krw_available": "원화 주문 가능",
    "krw_locked": "원화 주문에 묶임",
    "holdings_value": "보유 코인 평가 합",
    "total_assets": "총자산",
    "total_buy": "총 매수 금액",
    "total_pnl": "총 평가 손익",
    "total_pnl_rate": "수익률",
}
ACCOUNT_TABLE_COLUMNS: Final = (
    "코인",
    "보유 수량",
    "평균 매수가",
    "현재가",
    "매수 금액",
    "평가 금액",
    "평가 손익",
    "수익률",
    "고른 코인",
    "비고",
)
ACCOUNT_PICKED_TEXT: Final = "고름"
ACCOUNT_AVG_MODIFIED_TIP: Final = "업비트가 이 평균 매수가를 고쳤다고 알렸습니다."


def account_read_at_text(when: str) -> str:
    return f"{when}에 읽음"


def account_excluded_text(value_n: int, pnl_n: int) -> str | None:
    """뺀 줄이 있으면 한 줄(0인 쪽은 쓰지 않는다). 없으면 `None`."""
    parts = []
    if value_n:
        parts.append(f"평가하지 못해 총자산에서 뺀 코인 {value_n}개")
    if pnl_n:
        parts.append(f"손익을 셈하지 못한 코인 {pnl_n}개")
    return " · ".join(parts) if parts else None


def account_failed_text(when: str, error_name: str | None) -> str:
    return f"계정을 읽지 못했습니다(마지막으로 읽은 시각 {when}). {error_name or NO_VALUE}"


def account_qty_text(qty: str, locked: str | None) -> str:
    return f"{qty} (묶임 {locked})" if locked else qty
