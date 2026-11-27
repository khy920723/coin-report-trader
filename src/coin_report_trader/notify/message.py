"""디스코드로 보내는 글(M1 명세 3.9의 "보내는 글", "확인용 글").

알림 글:
- 첫 줄: "[{모드}] {분류 이름}"(예: "[모의매매] 오류 알림")
- 둘째 줄: "{코인 기호}: {글}"(코인이 없으면 "{글}")
- 셋째 줄(오류 알림과 기타 알림이고 자세한 글이 있을 때만): "단계: {단계} · 오류: {오류 이름} ·
  그 뒤에 한 일: {한 일}". 내용 전체(`content`)는 넣지 않는다.
- 셋째 줄(보고서 알림만. M2 명세 3.11): AI 표시 "AI가 쓴 글입니다. 틀릴 수 있습니다."
- 셋째 줄(매매 알림만. M3 명세 3.14.2): "주문 금액 {금액}원 · 계정 잔고: 원화 {원화}원, {코인 기호}
  {수량}"(`trade_line`. 알림센터의 줄에는 붙이지 않는다).
- 1,500자에서 자른다(`MAX_MESSAGE_LENGTH`. 자른 표시는 붙이지 않는다).
재료(글, 단계, 오류 이름, 한 일)는 알림을 만들 때 이미 가린 값이라고 본다. 주소는 글에
들어가지 않는다.
"""

from typing import Final

from coin_report_trader.core import (
    ALERT_CATEGORY_TITLES,
    MODE_TITLES,
    AlertCategory,
    AlertDetail,
    Mode,
    coin_label,
)
from coin_report_trader.core.texts import AI_MARK_TEXT, test_message_text

MAX_MESSAGE_LENGTH: Final = 1500
_DETAILED_CATEGORIES: Final = frozenset({AlertCategory.ERROR, AlertCategory.OTHER})


def alert_message(
    *,
    mode: Mode,
    category: AlertCategory,
    coin: str | None,
    text: str,
    detail: AlertDetail | None = None,
    trade_line: str | None = None,
) -> str:
    """알림 하나의 글. `text`는 앞에 코인을 붙이지 않은 글이다. `trade_line`은 매매 알림의
    셋째 줄이다(다른 분류에서는 쓰지 않는다)."""
    category = AlertCategory(category)
    lines = [f"[{MODE_TITLES[Mode(mode)]}] {ALERT_CATEGORY_TITLES[category]}"]
    lines.append(text if coin is None else f"{coin_label(coin)}: {text}")
    if category in _DETAILED_CATEGORIES and detail is not None:
        lines.append(
            f"단계: {detail.stage} · 오류: {detail.error_name} · 그 뒤에 한 일: {detail.after}"
        )
    if category is AlertCategory.REPORT:
        lines.append(AI_MARK_TEXT)
    if category is AlertCategory.TRADE and trade_line:
        lines.append(trade_line)
    return "\n".join(lines)[:MAX_MESSAGE_LENGTH]


def confirmation_message(category: AlertCategory) -> str:
    """그 분류의 확인용 글(`test_message_text`). 비밀 값, 금액, 잔고는 넣지 않는다."""
    return test_message_text(ALERT_CATEGORY_TITLES[AlertCategory(category)])
