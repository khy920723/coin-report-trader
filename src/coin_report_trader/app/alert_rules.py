"""알림 만들기(M1 명세 3.9): 글, 되풀이를 세는 상태. 저장은 자료 스레드가 한다.

되풀이를 세는 상태(A-1·A-31·A-29의 성공 전, A-30의 60분)는 메모리에만 두고 한 실행 안에서만
센다. 다시 켜면 처음부터 센다.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from coin_report_trader.core import (
    CLAUDE_VERSION_LIMIT,
    CLAUDE_VERSION_MIN,
    AlertCategory,
    AlertDetail,
    Direction,
    coin_label,
)
from coin_report_trader.core.texts import (
    AFTER_ERROR_PAUSE,
    AFTER_MARKETS_FAILED,
    AFTER_SAVE_FAILED,
    AFTER_UNEXPECTED,
    CODE_BUDGET,
    CODE_CANDLES_FAILED,
    CODE_CAUTION,
    CODE_CLAUDE_OFF,
    CODE_COIN_REMOVED,
    CODE_DAILY_LOSS,
    CODE_DIRECT_CANCELED,
    CODE_DIRECT_LOCKED,
    CODE_ERROR_PAUSE,
    CODE_EXTERNAL,
    CODE_FILLED,
    CODE_KEY_EXPIRY,
    CODE_LIVE_OFF,
    CODE_LOGGED_OUT,
    CODE_MARKETS_FAILED,
    CODE_MIN_ORDER_CHANGED,
    CODE_NO_AVG,
    CODE_NO_KRW,
    CODE_ORDER_PLACED,
    CODE_PRICE_FAILED,
    CODE_PRICE_GUARD,
    CODE_REPORT_DONE,
    CODE_REPORT_FAILED,
    CODE_SAVE_FAILED,
    CODE_SELL_ALL_LEFT,
    CODE_STOP_FAILED,
    CODE_TOO_SMALL,
    CODE_UNEXPECTED,
    CODE_UNKNOWN_EXCHANGE,
    ERROR_CLAUDE_NOT_FOUND,
    ERROR_STORAGE,
    ERROR_VERSION_UNREADABLE,
    STAGE_MARKETS,
    STAGE_ORDER,
    STAGE_PRICE,
    STAGE_REPORT,
    STAGE_SELL_ALL,
    STAGE_STOP_LOSS,
    STOP_ONLY_LEFT,
    text_candles_failed,
    text_filled,
    text_markets_failed,
    text_order_placed,
    text_save_failed,
    text_unexpected,
)

ALERT_WRITE_INSERT: Final = "insert"
"""알림 한 줄을 표에 쓰기."""
ALERT_WRITE_SEEN: Final = "seen"
"""보았다는 표시를 쓰기(`mark_seen`)."""
ALERT_WRITE_STATE: Final = "state"
"""`discord_state`를 쓰기(보낸 결과, 켤 때 남은 `"not_sent"` 바꾸기)."""

A30_QUIET_SECONDS: Final = 3600.0
"""같은 단계에 같은 예외 종류의 A-30은 이 초 안에 다시 만들지 않는다."""
QUIET_60_MINUTES: Final = 3600.0
"""A-8(같은 코인)과 A-24(같은 코인, 같은 오류 이름)를 다시 만들지 않는 초(M3 명세 3.14.1)."""


@dataclass(frozen=True)
class AlertSpec:
    """만들 알림 하나(생긴 때를 뺀 것)."""

    category: AlertCategory
    code: str
    coin: str | None
    text: str
    stage: str
    error_name: str
    content: str
    after: str
    trade_line: str | None = None
    """디스코드 매매 알림의 셋째 줄(M3 명세 3.14.2). 표와 알림센터에는 쓰지 않는다."""

    @property
    def detail(self) -> AlertDetail | None:
        """자세한 글은 오류 알림과 기타 알림에만 있다(보고서 알림 A-26은 M2 명세 3.11, 매매·제한·
        보유 알림은 M3 명세 3.14.1)."""
        if self.category not in (AlertCategory.ERROR, AlertCategory.OTHER):
            return None
        return AlertDetail(self.stage, self.coin, self.error_name, self.content, self.after)


def candles_failed(coin: str, stage: str, error_name: str, content: str, after: str) -> AlertSpec:
    """A-1(오류 알림)."""
    return AlertSpec(
        AlertCategory.ERROR, CODE_CANDLES_FAILED, coin, text_candles_failed(error_name),
        stage, error_name, content, after,
    )  # fmt: skip


def save_failed(
    stage: str, coin: str | None, content: str, *, error_pause: bool = False
) -> AlertSpec:
    """A-29(오류 알림). 오류 이름은 늘 "DB 오류"다. `error_pause`가 참이면(이 저장 실패가 오류
    멈춤에 새로 들어가게 했다. M3 명세 3.10.2) 단계마다의 글 뒤에 오류 멈춤의 글을 잇는다."""
    after = AFTER_SAVE_FAILED[stage]
    if error_pause:
        after = f"{after} {AFTER_ERROR_PAUSE}"
    return AlertSpec(
        AlertCategory.ERROR, CODE_SAVE_FAILED, coin, text_save_failed(stage, ERROR_STORAGE),
        stage, ERROR_STORAGE, content, after,
    )  # fmt: skip


def vault_failed(stage: str, error_name: str, content: str) -> AlertSpec:
    """A-29(오류 알림): 보관소에 주소를 저장하지 못함, 지우지 못함. 코인은 없고 주소는 글에 없다.

    오류 이름은 보관소의 것("보관소를 쓸 수 없음", "보관소 오류")이다.
    """
    return AlertSpec(
        AlertCategory.ERROR, CODE_SAVE_FAILED, None, text_save_failed(stage, error_name),
        stage, error_name, content, AFTER_SAVE_FAILED[stage],
    )  # fmt: skip


def unexpected(
    stage: str, coin: str | None, error_name: str, content: str, after: str = AFTER_UNEXPECTED
) -> AlertSpec:
    """A-30(기타 알림). AI 보고서의 일이면 `after`는 AI를 껐다는 글이다(M2 명세 3.11)."""
    return AlertSpec(
        AlertCategory.OTHER, CODE_UNEXPECTED, coin, text_unexpected(error_name),
        stage, error_name, content, after,
    )  # fmt: skip


def _version_text(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


AFTER_CLAUDE_OFF: Final = "AI를 껐습니다. 범위 안의 버전이 되면 다음 회차부터 다시 돕니다."
AFTER_AI_OFF_ERROR: Final = AFTER_ERROR_PAUSE
"""AI 보고서의 일에서 난 예외의 A-30: 3단계부터는 AI를 끄지 않고 오류 멈춤에 들어간다."""


def price_failed(coin: str, error_name: str, next_text: str) -> AlertSpec:
    """A-2(오류 알림): 현재가를 읽지 못해 그 코인의 회차를 쉬었다(M2 명세 3.11)."""
    return AlertSpec(
        AlertCategory.ERROR, CODE_PRICE_FAILED, coin,
        "현재가를 읽지 못해 이번 회차를 쉬었습니다.", STAGE_PRICE, error_name,
        "AI 보고서를 만들기 전에 현재가를 읽지 못했습니다.",
        "이번 회차에 이 코인의 AI를 부르지 않았습니다."
        f" 다음 회차({next_text})에 다시 합니다.",
    )  # fmt: skip


def report_failed(
    coin: str, kind: str, reason: str, next_text: str, *, stop_only: bool = False
) -> AlertSpec:
    """A-17(오류 알림): 보고서가 `FAILED`(로그인 만료 말고). `reason`은 정한 까닭의 글이다.

    3단계 글(M3 명세 3.14.1): "AI 보고서를 받지 못해 이번 회차에 주문하지 않았습니다({실패 종류})."
    `stop_only`가 참이면(그 회차의 정리에서 매도 주문을 취소함) 손절만 남음을 덧붙인다.
    """
    text = f"AI 보고서를 받지 못해 이번 회차에 주문하지 않았습니다({kind})."
    return AlertSpec(
        AlertCategory.ERROR, CODE_REPORT_FAILED, coin, _stop_only(text, stop_only),
        STAGE_REPORT, kind, reason,
        "이번 회차에 이 코인의 보고서를 만들지 않았습니다."
        f" 다음 회차({next_text})에 다시 부릅니다.",
    )  # fmt: skip


def logged_out(stop_only_coins: tuple[str, ...] = ()) -> AlertSpec:
    """A-18(오류 알림, 코인 없음): 로그인되지 않음. `stop_only_coins`는 그 회차에 매도 주문을
    취소한 코인이고, 코인마다 손절만 남음의 줄을 덧붙인다(M3 명세 3.14.1)."""
    text = "Claude 로그인이 만료되어 AI 보고서를 받지 못했습니다. 다시 로그인하세요."
    for coin in stop_only_coins:
        text += f"\n{coin_label(coin)}: {STOP_ONLY_LEFT}"
    return AlertSpec(
        AlertCategory.ERROR, CODE_LOGGED_OUT, None,
        text,
        STAGE_REPORT, "로그인 만료", "claude auth status가 로그인되지 않았다고 알렸습니다.",
        "이번 회차에 AI 보고서를 만들지 않았습니다. Claude Code에 다시 로그인하면 다음 회차부터"
        " 다시 부릅니다.",
    )  # fmt: skip


def claude_off(error_name: str) -> AlertSpec:
    """A-19(오류 알림, 코인 없음): 실행 파일을 찾지 못함, 버전을 읽지 못함, 버전이 범위 밖."""
    if error_name in (ERROR_CLAUDE_NOT_FOUND, ERROR_VERSION_UNREADABLE):
        text = "Claude Code를 찾지 못했거나 버전을 읽지 못해 AI를 껐습니다."
        content = "Claude Code를 설치하고 로그인했는지 확인하세요."
    else:
        text = "Claude Code 버전이 시험한 범위 밖이라 AI를 껐습니다."
        content = (
            f"시험한 범위: {_version_text(CLAUDE_VERSION_MIN)} 이상"
            f" {_version_text(CLAUDE_VERSION_LIMIT)} 미만"
        )
    return AlertSpec(
        AlertCategory.ERROR, CODE_CLAUDE_OFF, None, text, STAGE_REPORT, error_name, content,
        AFTER_CLAUDE_OFF,
    )  # fmt: skip


def report_done(coin: str, round_number: int, direction: Direction) -> AlertSpec:
    """A-26(보고서 알림): 보고서가 `OK`. 자세한 글이 없다."""
    return AlertSpec(
        AlertCategory.REPORT, CODE_REPORT_DONE, coin,
        f"회차 {round_number}의 보고서를 받았습니다. 최종 판단: {Direction(direction).value}.",
        "", "", "", "",
    )  # fmt: skip


def markets_failed(error_name: str, content: str) -> AlertSpec:
    """A-31(오류 알림). 코인은 없다."""
    return AlertSpec(
        AlertCategory.ERROR, CODE_MARKETS_FAILED, None, text_markets_failed(error_name),
        STAGE_MARKETS, error_name, content, AFTER_MARKETS_FAILED,
    )  # fmt: skip


def _stop_only(text: str, stop_only: bool) -> str:
    """손절만 남음의 덧붙임(M3 명세 3.14.1): 그 회차의 정리에서 매도 주문을 취소한 코인이 새 매도를
    걸지 못하면 까닭의 알림 글 뒤에 붙인다."""
    return f"{text} {STOP_ONLY_LEFT}" if stop_only else text


# --- 3단계(M3 명세 3.14.1) ------------------------------------------------------------------------


def order_price_failed(coin: str, error_name: str, next_text: str) -> AlertSpec:
    """A-2(주문 때. 오류 알림): 주문 전에 현재가, 호가 단위, 최우선 호가 가운데 하나를 읽지 못함."""
    return AlertSpec(
        AlertCategory.ERROR, CODE_PRICE_FAILED, coin,
        "계정이나 현재가를 읽지 못해 이번 회차에 주문하지 않았습니다.", STAGE_ORDER, error_name,
        "주문하기 전에 현재가, 호가 단위, 최우선 호가 가운데 하나를 읽지 못했습니다.",
        f"이번 회차에 이 코인의 주문을 하지 않았습니다. 다음 회차({next_text})에 다시 합니다.",
    )  # fmt: skip


def too_small(coin: str) -> AlertSpec:
    """A-3(제한 알림)."""
    return _limit(
        CODE_TOO_SMALL,
        coin,
        "팔 수 있는 수량이 최소 주문 금액보다 작아 매도와 손절을 하지 않습니다.",
    )


def price_guard(
    coin: str, *, predicted: bool, price: str, current: str, stop_only: bool = False
) -> AlertSpec:
    """A-4(제한 알림). 추세추종은 "제안가", 평균회귀는 "예측 가격"이다. {가격}은 넓혀 맞춘 가격."""
    name = "예측 가격" if predicted else "제안가"
    text = f"{name}({price}원)가 가격 안전 검사에 걸려 주문하지 않았습니다(현재가 {current}원)."
    return _limit(CODE_PRICE_GUARD, coin, _stop_only(text, stop_only))


def stop_failed(coin: str, error_name: str) -> AlertSpec:
    """A-8(오류 알림)."""
    return AlertSpec(
        AlertCategory.ERROR, CODE_STOP_FAILED, coin,
        "손절 매도에 실패했습니다. 1분 뒤 다시 시도합니다.", STAGE_STOP_LOSS, error_name,
        "손절 매도를 내지 못했습니다.", "1분 뒤 다시 시도합니다.",
    )  # fmt: skip


def no_avg_price(coin: str, stage: str) -> AlertSpec:
    """A-9(오류 알림). 단계는 1분 점검이면 "손절", 회차의 판단이면 "주문"이다."""
    return AlertSpec(
        AlertCategory.ERROR, CODE_NO_AVG, coin,
        "평균 매수가를 받지 못해 손절과 추가 매수를 하지 않습니다.", stage, "평균 매수가 없음",
        "보유 중인데 평균 매수가가 없거나 0입니다.",
        "평균 매수가가 생길 때까지 이 코인의 손절과 추가 매수를 하지 않습니다.",
    )  # fmt: skip


def over_budget(coin: str) -> AlertSpec:
    """A-10(제한 알림)."""
    return _limit(
        CODE_BUDGET, coin, "사면 코인 예산(또는 예산)을 넘어 이번 회차에 사지 않았습니다."
    )


def no_krw(coin: str) -> AlertSpec:
    """A-11(제한 알림)."""
    return _limit(CODE_NO_KRW, coin, "가상 원화가 모자라 이번 회차에 사지 않았습니다.")


def daily_loss() -> AlertSpec:
    """A-12(제한 알림, 코인 없음)."""
    return _limit(
        CODE_DAILY_LOSS, None,
        "하루 손실 한도에 닿았습니다. 오늘은 새로 사지 않습니다. 매도와 손절은 계속합니다.",
    )  # fmt: skip


def caution(coin: str) -> AlertSpec:
    """A-13(제한 알림)."""
    return _limit(CODE_CAUTION, coin, "유의·주의 표시가 있어 새로 사지 않습니다.")


def min_order_changed() -> AlertSpec:
    """A-15(제한 알림, 코인 없음)."""
    return _limit(
        CODE_MIN_ORDER_CHANGED, None,
        "최소 주문 금액이 바뀌어 지금 설정이 검사에 걸립니다."
        " 설정을 고칠 때까지 새로 사지 않습니다.",
    )  # fmt: skip


def coin_removed(coin: str) -> AlertSpec:
    """A-16(보유 알림)."""
    return AlertSpec(
        AlertCategory.HOLDING, CODE_COIN_REMOVED, coin,
        "목록에서 뺐습니다. 보유는 그대로 남고, 이 코인의 손절도 없어집니다.", "", "", "", "",
    )  # fmt: skip


def unknown_exchange(coin: str | None, stage: str, error_name: str) -> AlertSpec:
    """A-24(기타 알림): 목록에 없는 거래소 오류(정해진 알림이 없는 일)."""
    return AlertSpec(
        AlertCategory.OTHER, CODE_UNKNOWN_EXCHANGE, coin,
        f"거래소가 알 수 없는 오류를 냈습니다({stage}, {error_name}).", stage, error_name,
        f"{stage}에서 거래소가 목록에 없는 오류를 냈습니다.",
        "그 일만 그만두고 다음 회차나 다음 1분 점검에서 다시 합니다.",
    )  # fmt: skip


def error_pause(stage: str, kind: str, until_text: str | None) -> AlertSpec:
    """A-23(오류 알림): 거래를 계속 막는 오류로 오류 멈춤에 들어감(M3 명세 3.14.1). `kind`는 {오류
    종류}("일시 차단")이고 `until_text`는 차단이 풀리는 때의 글이다."""
    content = "거래소가 잠시 요청을 막았습니다."
    if until_text:
        content = f"{content} {until_text}까지 공개 시세 요청을 내지 않습니다."
    return AlertSpec(
        AlertCategory.ERROR, CODE_ERROR_PAUSE, None,
        f"거래소 오류({kind})로 오류 멈춤에 들어갔습니다. 지금 손절이 동작하지 않습니다.",
        stage, kind, content, "사용자가 풀 때까지 회차와 손절을 하지 않습니다.",
    )  # fmt: skip


def sell_all_left(coin: str, qty_text: str, error_name: str) -> AlertSpec:
    """A-25(오류 알림): '멈추고 전량 매도'에서 팔지 못한 수량(M3 명세 3.14.1)."""
    return AlertSpec(
        AlertCategory.ERROR, CODE_SELL_ALL_LEFT, coin, f"{qty_text}을 팔지 못했습니다.",
        STAGE_SELL_ALL, error_name, f"{qty_text} 팔지 못했습니다.",
        "멈춤을 풀거나 팔 수 있는 수량이 없어질 때까지 1분마다 다시 팝니다.",
    )  # fmt: skip


def order_placed(coin: str, summary: str, trade_line: str) -> AlertSpec:
    """A-27(매매 알림). `trade_line`은 디스코드의 셋째 줄이다(M3 명세 3.14.2)."""
    return AlertSpec(
        AlertCategory.TRADE, CODE_ORDER_PLACED, coin, text_order_placed(summary),
        "", "", "", "", trade_line,
    )  # fmt: skip


def filled(coin: str, side: str, qty: Decimal, price: Decimal, trade_line: str) -> AlertSpec:
    """A-28(매매 알림)."""
    return AlertSpec(
        AlertCategory.TRADE, CODE_FILLED, coin, text_filled(side, qty, price),
        "", "", "", "", trade_line,
    )  # fmt: skip


def _limit(code: str, coin: str | None, text: str) -> AlertSpec:
    return AlertSpec(AlertCategory.LIMIT, code, coin, text, "", "", "", "")


# --- 5단계(M5 명세 3.10) --------------------------------------------------------------------------

KEY_STAGE: Final = "업비트 키"
"""A-32의 단계(M5 명세 3.10)."""
KEY_EXPIRY_SOON: Final = "키 만료 임박"
KEY_EXPIRED: Final = "키 만료"
AFTER_KEY_EXPIRY_PAPER: Final = "실거래 켜기 전에 새 키를 넣으세요."
AFTER_KEY_EXPIRY_LIVE: Final = "새 키를 넣기 전까지 실거래는 그대로 돕니다."
AFTER_KEY_SAVE_FAILED: Final = "키는 그대로입니다. 다시 하세요."
"""A-29("업비트 키 저장")의 그 뒤에 한 일(명세에 글이 없어 웹후크 주소의 글을 따라 정했다)."""
AFTER_LIVE_ON_FAILED: Final = "실거래를 켜지 않았습니다. 모드는 모의매매 그대로입니다."
"""A-29("실거래 켜기")의 그 뒤에 한 일(M5 명세 3.7.2의 3. 모드를 쓰지 못하면 켜지 않는다)."""
KRW_COIN_TEXT: Final = "원화"
"""A-22의 {코인}이 원화일 때(M5 명세 3.10)."""


def live_no_krw(coin: str) -> AlertSpec:
    """A-11의 실거래 글(제한 알림. M5 명세 3.10)."""
    return _limit(CODE_NO_KRW, coin, "원화 잔고가 모자라 이번 회차에 사지 않았습니다.")


def direct_locked(coin: str, qty_text: str) -> AlertSpec:
    """A-6(보유 알림): 직접 걸어 둔 주문에 묶인 수량(설계서 12)."""
    return AlertSpec(
        AlertCategory.HOLDING, CODE_DIRECT_LOCKED, coin,
        f"직접 걸어 둔 주문에 묶인 {qty_text}은 팔 수 없습니다.", "", "", "", "",
    )  # fmt: skip


def direct_canceled(coin: str) -> AlertSpec:
    """A-7(보유 알림): 직접 걸어 둔 주문이 거래소에서 취소됨(설계서 12)."""
    return AlertSpec(
        AlertCategory.HOLDING, CODE_DIRECT_CANCELED, coin,
        "프로그램 주문과 만나 직접 걸어 둔 주문이 거래소에서 취소되었습니다.", "", "", "", "",
    )  # fmt: skip


def live_off(uncanceled: tuple[str, ...] = ()) -> AlertSpec:
    """A-20(보유 알림, 코인 없음): 실거래에서 나감. 취소하지 못한 주문이 있으면 덧붙인다(3.7.3)."""
    text = "실거래를 끝냈습니다. 남은 실제 보유에는 손절이 없습니다."
    if uncanceled:
        text = f"{text} 취소하지 못한 주문: {', '.join(uncanceled)}"
    return AlertSpec(AlertCategory.HOLDING, CODE_LIVE_OFF, None, text, "", "", "", "")


def external_change(coin: str | None) -> AlertSpec:
    """A-22(보유 알림): 외부 변동. 원화면 코인이 없고 {코인}은 "원화"다(설계서 12, 3.10)."""
    text = "프로그램이 내지 않은 잔고 변화가 있었습니다(외부 변동)."
    if coin is None:
        text = f"{KRW_COIN_TEXT}: {text}"
    return AlertSpec(AlertCategory.HOLDING, CODE_EXTERNAL, coin, text, "", "", "", "")


def key_expiry(expires_text: str, *, expired: bool, live: bool) -> AlertSpec:
    """A-32(기타 알림, 코인 없음): 업비트 키의 만료일까지 30일 이하이거나 지남(3.8, 3.10)."""
    if expired:
        text = "업비트 키가 만료되었습니다. 새 키를 만들어 넣으세요."
    else:
        text = f"업비트 키가 {expires_text}에 만료됩니다. 새 키를 만들어 넣으세요."
    return AlertSpec(
        AlertCategory.OTHER, CODE_KEY_EXPIRY, None, text, KEY_STAGE,
        KEY_EXPIRED if expired else KEY_EXPIRY_SOON, f"만료일 {expires_text}",
        AFTER_KEY_EXPIRY_LIVE if live else AFTER_KEY_EXPIRY_PAPER,
    )  # fmt: skip


def exchange_error_pause(stage: str, kind: str) -> AlertSpec:
    """A-23(오류 알림): 업비트의 거래를 계속 막는 오류(키 오류 넷. 3.5.5). 일시 차단은
    `error_pause`다."""
    return AlertSpec(
        AlertCategory.ERROR, CODE_ERROR_PAUSE, None,
        f"거래소 오류({kind})로 오류 멈춤에 들어갔습니다. 지금 손절이 동작하지 않습니다.",
        stage, kind, f"업비트가 요청을 받지 않았습니다({kind}).",
        "사용자가 풀 때까지 회차와 손절을 하지 않습니다.",
    )  # fmt: skip


def key_save_failed(stage: str, error_name: str) -> AlertSpec:
    """A-29(오류 알림): 업비트 키를 보관소에 저장하지 못함, 지우지 못함(3.8). 오류 멈춤이 아니다."""
    return AlertSpec(
        AlertCategory.ERROR, CODE_SAVE_FAILED, None, text_save_failed(stage, error_name),
        stage, error_name, error_name, AFTER_KEY_SAVE_FAILED,
    )  # fmt: skip


def live_on_save_failed(stage: str, content: str, *, error_pause: bool) -> AlertSpec:
    """A-29(오류 알림): 실거래 켜기·끄기의 모드나 실제 계정을 쓰지 못함(3.7.2의 3, 3.9)."""
    after = AFTER_LIVE_ON_FAILED
    if error_pause:
        after = f"{after} {AFTER_ERROR_PAUSE}"
    return AlertSpec(
        AlertCategory.ERROR, CODE_SAVE_FAILED, None, text_save_failed(stage, ERROR_STORAGE),
        stage, ERROR_STORAGE, content, after,
    )  # fmt: skip


class RepeatRules:
    """되풀이(3.9). `want_*`는 만들어도 되면 참을 돌려주고 그 알림을 센다."""

    def __init__(self) -> None:
        self._a1: dict[str, set[str]] = {}
        self._a29: set[tuple[str, str | None]] = set()
        self._a29_webhook: set[tuple[str, AlertCategory]] = set()
        self._alert_save: set[str] = set()
        self._a31: set[str] = set()
        self._a30: dict[tuple[str, str | None, str], float] = {}
        self._a2: dict[str, set[str]] = {}
        self._a17: dict[str, set[str]] = {}
        self._a18 = False
        self._a19 = False
        self._a3: set[str] = set()
        self._a8: dict[str, float] = {}
        self._a9: set[str] = set()
        self._a12: date | None = None
        self._a13: dict[str, date] = {}
        self._a15 = False
        self._a24: dict[tuple[str | None, str], float] = {}

    def want_a1(self, coin: str, error_name: str) -> bool:
        """같은 코인에 같은 오류 이름은 그 코인의 받기가 성공하기 전에는 다시 만들지 않는다."""
        names = self._a1.setdefault(coin, set())
        if error_name in names:
            return False
        names.add(error_name)
        return True

    def a1_succeeded(self, coin: str) -> None:
        """받기, 저장, 계산이 끝나고 다섯 기간 가운데 하나 이상에 값이 났다."""
        self._a1.pop(coin, None)

    def want_a29(self, stage: str, coin: str | None) -> bool:
        """같은 단계(코인마다)는 그 저장이 한 번 성공하기 전에는 다시 만들지 않는다."""
        key = (stage, coin)
        if key in self._a29:
            return False
        self._a29.add(key)
        return True

    def a29_succeeded(self, stage: str, coin: str | None) -> None:
        self._a29.discard((stage, coin))

    def want_alert_save(self, kind: str) -> bool:
        """A-29("알림 저장")는 실패한 쓰기의 종류(`ALERT_WRITE_*`)마다 따로 센다. 같은 종류의
        쓰기가 한 번 성공하기 전에는 다시 만들지 않는다. 다른 종류의 쓰기(그 A-29 자신을 표에 쓴
        것 포함)의 성공은 풀지 않는다."""
        if kind in self._alert_save:
            return False
        self._alert_save.add(kind)
        return True

    def alert_save_succeeded(self, kind: str) -> None:
        self._alert_save.discard(kind)

    def want_a29_webhook(self, stage: str, category: AlertCategory) -> bool:
        """주소 저장·지우기의 A-29는 분류마다 따로 센다. 그 저장(지우기)이 성공하기 전에는 다시
        만들지 않는다."""
        key = (stage, AlertCategory(category))
        if key in self._a29_webhook:
            return False
        self._a29_webhook.add(key)
        return True

    def a29_webhook_succeeded(self, stage: str, category: AlertCategory) -> None:
        self._a29_webhook.discard((stage, AlertCategory(category)))

    def want_a31(self, error_name: str) -> bool:
        """같은 오류 이름은 코인 목록 받기가 한 번 성공하기 전에는 다시 만들지 않는다."""
        if error_name in self._a31:
            return False
        self._a31.add(error_name)
        return True

    def a31_succeeded(self) -> None:
        self._a31.clear()

    def want_a30(self, stage: str, coin: str | None, kind: str, monotonic: float) -> bool:
        """같은 단계(코인마다)에 같은 예외 종류는 60분 안에 다시 만들지 않는다."""
        key = (stage, coin, kind)
        last = self._a30.get(key)
        if last is not None and monotonic - last < A30_QUIET_SECONDS:
            return False
        self._a30[key] = monotonic
        return True

    def want_a2(self, coin: str, error_name: str) -> bool:
        """같은 코인, 같은 오류 이름은 그 코인의 현재가 읽기가 한 번 성공하기 전에는 다시 만들지
        않는다(M2 명세 3.11)."""
        names = self._a2.setdefault(coin, set())
        if error_name in names:
            return False
        names.add(error_name)
        return True

    def a2_succeeded(self, coin: str) -> None:
        self._a2.pop(coin, None)

    def want_a17(self, coin: str, kind: str) -> bool:
        """같은 코인, 같은 실패 종류는 그 코인의 보고서가 한 번 `OK`가 되기 전에는 다시 만들지
        않는다."""
        kinds = self._a17.setdefault(coin, set())
        if kind in kinds:
            return False
        kinds.add(kind)
        return True

    def want_a18(self) -> bool:
        """어느 코인이든 보고서가 한 번 `OK`가 되기 전에는 다시 만들지 않는다."""
        if self._a18:
            return False
        self._a18 = True
        return True

    def report_ok(self, coin: str) -> None:
        """그 코인의 보고서가 `OK`가 됐다: 그 코인의 A-17과 모든 코인의 A-18의 되풀이를 푼다."""
        self._a17.pop(coin, None)
        self._a18 = False

    def want_a19(self) -> bool:
        """`AiStatus`가 `ON`으로 돌아오기 전에는 다시 만들지 않는다."""
        if self._a19:
            return False
        self._a19 = True
        return True

    def ai_on(self) -> None:
        self._a19 = False

    # --- 3단계(M3 명세 3.14.1) --------------------------------------------------------------------

    def want_a3(self, coin: str) -> bool:
        """그 코인이 "팔 수 있는 수량이 작음"의 상태로 바뀔 때 한 번(2절)."""
        if coin in self._a3:
            return False
        self._a3.add(coin)
        return True

    def a3_cleared(self, coin: str) -> None:
        """그 코인이 그 상태에서 벗어났다(팔았거나 팔 수 있게 됐거나 보유가 없다)."""
        self._a3.discard(coin)

    def want_a8(self, coin: str, monotonic: float) -> bool:
        """같은 코인은 손절이 성공하거나 보유가 없어지기 전에는 60분 안에 다시 만들지 않는다."""
        last = self._a8.get(coin)
        if last is not None and monotonic - last < QUIET_60_MINUTES:
            return False
        self._a8[coin] = monotonic
        return True

    def a8_cleared(self, coin: str) -> None:
        """손절이 성공했거나 보유가 없어졌다."""
        self._a8.pop(coin, None)

    def want_a9(self, coin: str) -> bool:
        """같은 코인은 평균 매수가가 생기기 전에는 다시 만들지 않는다."""
        if coin in self._a9:
            return False
        self._a9.add(coin)
        return True

    def a9_cleared(self, coin: str) -> None:
        """평균 매수가가 생겼다(또는 보유가 없다)."""
        self._a9.discard(coin)

    def want_a12(self, day: date) -> bool:
        """하루(한국 09:00 경계)에 한 번."""
        if self._a12 == day:
            return False
        self._a12 = day
        return True

    def want_a13(self, coin: str, day: date) -> bool:
        """같은 코인은 그날 한 번."""
        if self._a13.get(coin) == day:
            return False
        self._a13[coin] = day
        return True

    def want_a15(self) -> bool:
        """저장된 설정이 다시 통과하기 전에는 다시 만들지 않는다."""
        if self._a15:
            return False
        self._a15 = True
        return True

    def a15_cleared(self) -> None:
        self._a15 = False

    def want_a24(self, coin: str | None, error_name: str, monotonic: float) -> bool:
        """같은 코인, 같은 오류 이름은 60분 안에 다시 만들지 않는다."""
        key = (coin, error_name)
        last = self._a24.get(key)
        if last is not None and monotonic - last < QUIET_60_MINUTES:
            return False
        self._a24[key] = monotonic
        return True
