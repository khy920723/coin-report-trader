"""매매 계산의 공용 타입(M3 명세 3.4). 값, 설정, 가상 계정과 주문의 타입.

표준 라이브러리만 쓴다. 가격, 수량, 금액, 비율은 `Decimal`, 시각은 UTC가 붙은 `datetime`이다.
비율은 퍼센트 수다(예: 20은 20%).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from coin_report_trader.core.alerts import Mode


class TradeMethod(StrEnum):
    MEAN_REVERSION = "mean_reversion"
    TREND = "trend"


TRADE_METHOD_TITLES: Mapping[TradeMethod, str] = MappingProxyType(
    {TradeMethod.MEAN_REVERSION: "평균회귀", TradeMethod.TREND: "추세추종"}
)


class PauseStopLoss(StrEnum):
    """'멈춤' 중 손절(11절)."""

    KEEP = "keep"
    STOP = "stop"


PAUSE_STOP_LOSS_TITLES: Mapping[PauseStopLoss, str] = MappingProxyType(
    {PauseStopLoss.KEEP: "손절 계속", PauseStopLoss.STOP: "손절 중지"}
)


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


ORDER_SIDE_TITLES: Mapping[OrderSide, str] = MappingProxyType(
    {OrderSide.BUY: "매수", OrderSide.SELL: "매도"}
)


class OrderKind(StrEnum):
    LIMIT = "limit"
    MARKET = "market"


ORDER_KIND_TITLES: Mapping[OrderKind, str] = MappingProxyType(
    {OrderKind.LIMIT: "지정가", OrderKind.MARKET: "시장가"}
)


class OrderReason(StrEnum):
    JUDGMENT = "judgment"
    STOP_LOSS = "stop_loss"
    SELL_ALL = "sell_all"


ORDER_REASON_TITLES: Mapping[OrderReason, str] = MappingProxyType(
    {
        OrderReason.JUDGMENT: "판단",
        OrderReason.STOP_LOSS: "손절",
        OrderReason.SELL_ALL: "긴급 매도",
    }
)


class OrderState(StrEnum):
    OPEN = "open"
    FILLED = "filled"
    CANCELED = "canceled"
    UNCONFIRMED = "unconfirmed"
    NOT_SENT = "not_sent"
    FAILED = "failed"


ORDER_STATE_TITLES: Mapping[OrderState, str] = MappingProxyType(
    {
        OrderState.OPEN: "걸림",
        OrderState.FILLED: "체결",
        OrderState.CANCELED: "취소",
        OrderState.UNCONFIRMED: "확인 안 됨",
        OrderState.NOT_SENT: "보내지지 않음",
        OrderState.FAILED: "실패",
    }
)


class PauseKind(StrEnum):
    USER = "user"
    ERROR = "error"


class ErrorPauseCause(StrEnum):
    EXCHANGE = "exchange"
    """거래를 계속 막는 거래소 오류."""
    UNEXPECTED = "unexpected"
    """예상하지 못한 오류."""
    SAVE = "save"
    """저장 실패."""


ERROR_PAUSE_CAUSE_TITLES: Mapping[ErrorPauseCause, str] = MappingProxyType(
    {
        ErrorPauseCause.EXCHANGE: "일시 차단",
        ErrorPauseCause.UNEXPECTED: "예상하지 못한 오류",
        ErrorPauseCause.SAVE: "저장 실패",
    }
)
"""{오류 종류}의 글(3.10.2). 3단계에 거래를 계속 막는 거래소 오류는 "일시 차단"뿐이다(3.8.4)."""


class RoundStage(StrEnum):
    """값이 곧 설계서 4.2의 3의 단계 글이다. 화면은 `CLEANUP`을 "주문 정리"로 보인다(3.17.2)."""

    CLEANUP = "정리"
    STATS = "봉 통계"
    AI = "AI 호출"
    ACCOUNT = "계정 읽기"
    ORDER = "검사와 주문"
    RECORD = "기록"


class NoOrderReason(StrEnum):
    """주문하지 않은 까닭. 값이 곧 화면과 기록의 글이다(3.5.2, 3.8.2)."""

    NOTHING = "아무것도 하지 않음"
    PRICE_NOT_ABOVE_AVG = "현재가가 평균 매수가 이하"
    NOT_OVER_AVG_AND_FEE = "예측 가격이 평균 매수가와 수수료를 넘지 않음"
    FEE_UNKNOWN = "수수료율을 몰라 팔지 않음"
    NO_AVG_PRICE = "평균 매수가 없음"
    NO_JUDGMENT = "판단을 받지 못함"
    DROPPED_BY_STOP_LOSS = "손절로 판단을 버림"
    NO_BUY_AFTER_STOP_LOSS = "손절 뒤라 새로 사지 않음"
    DROPPED_BY_PAUSE = "멈춤으로 버림"
    DAILY_LOSS_LIMIT = "하루 손실 한도"
    CAUTION = "유의·주의"
    SETTINGS_CHECK = "설정이 검사에 걸림"
    ORDER_INTERVAL = "주문 간격"
    COIN_BUDGET = "코인 예산"
    BUDGET = "예산"
    NO_KRW = "가상 원화가 모자람"
    UNCONFIRMED_ORDER = "확인 안 된 주문"
    DAILY_CANCEL_CAP = "하루 걸었다 지우기 상한"
    PRICE_GUARD = "가격 안전 검사"
    BELOW_MIN_ORDER = "최소 주문 금액보다 작음"
    NO_SELLABLE_QTY = "팔 수 있는 수량이 없음"
    NO_PRICE_OR_TICK = "현재가나 호가 단위를 받지 못함"


PAPER_FEE_RATE: Final = Decimal("0.0005")
"""모의매매의 수수료율(3.7.3)."""
MIN_ORDER_FALLBACK: Final = Decimal("5000")
"""최소 주문 금액을 모를 때의 값(5.1절)."""
SAFETY_UP: Final = Decimal("0.02")
SAFETY_DOWN: Final = Decimal("0.5")
SAFETY_SELL_UP: Final = Decimal("2")
FIRST_BUY_FLOOR_FACTOR: Final = Decimal("1.1")
QTY_PLACES: Final = 8
TOLERANCE_MAX: Final = Decimal("2")
"""허용 범위의 위 끝(%). 0 이상 이것 이하(3.8.5)."""
STOP_CHECK_SECONDS: Final = 60
CANCEL_CONFIRM_SECONDS: Final = 10
UNCONFIRMED_CLOSE_SECONDS: Final = 600
DAILY_CANCEL_LIMIT: Final = 48
BACKUP_DAYS_RANGE: Final = (1, 365)


@dataclass(frozen=True)
class TradeSettings:
    budget: Decimal
    """예산(원)."""
    coin_ratios: Mapping[str, Decimal]
    """마켓 코드에서 투자 비율(%)."""
    first_ratio: Decimal | None
    """추세추종 첫 매수 비율(%). 고르지 않은 방식이면 `None`일 수 있다."""
    mr_first_ratio: Decimal | None
    """평균회귀 첫 매수 비율(%). 고르지 않은 방식이면 `None`일 수 있다."""
    add_ratio: Decimal
    """추가 한 번의 비율(%)."""
    tolerance: Decimal
    """허용 범위(%)(3.8.5)."""
    stop_ratio: Decimal
    """손절 비율(%)."""
    daily_loss_limit: Decimal
    """하루 손실 한도(원)."""
    order_interval_s: int
    method: TradeMethod
    pause_stop_loss: PauseStopLoss
    mr_sell_unknown_fee: bool
    """'팔지'."""
    mr_tolerable_loss: Decimal
    """감내할 손해(%)."""
    paper_funds: Decimal
    """모의매매 가상 자금(원)."""
    backup_days: int

    def coin_budget(self, market: str) -> Decimal:
        """코인 예산: `budget × coin_ratios[market] ÷ 100`. 원 아래를 버리지 않는다(5.5절)."""
        return self.budget * self.coin_ratios[market] / Decimal(100)


@dataclass(frozen=True)
class OrderRules:
    """주문을 만들 때마다 코인마다 쓰는 거래소 규칙(3.8.1)."""

    tick_size: Decimal
    min_order: Decimal


@dataclass(frozen=True)
class PaperHolding:
    market: str
    qty: Decimal
    """보유 전체."""
    locked_qty: Decimal
    """프로그램 매도 주문에 묶인 수량."""
    avg_price: Decimal | None
    """평균 매수가(9절). 보유가 0이면 `None`."""
    buy_amount_sum: Decimal
    buy_qty_sum: Decimal
    """보유가 0이 된 뒤의 매수 합(3.7.1)."""


@dataclass(frozen=True)
class PaperAccount:
    krw: Decimal
    """가상 원화 전체."""
    locked_krw: Decimal
    """걸린 매수 주문에 묶인 원화(수수료 포함)."""
    holdings: tuple[PaperHolding, ...]


@dataclass(frozen=True)
class OrderRecord:
    order_id: int
    uid: str
    """식별값."""
    mode: Mode
    round_id: int | None
    market: str
    side: OrderSide
    kind: OrderKind
    reason: OrderReason
    price: Decimal | None
    """시장가는 `None`."""
    qty: Decimal
    state: OrderState
    created_utc: datetime
    closed_utc: datetime | None
    error_name: str | None


@dataclass(frozen=True)
class FillRecord:
    fill_id: int
    order_id: int
    mode: Mode
    market: str
    side: OrderSide
    price: Decimal
    qty: Decimal
    fee: Decimal
    realized_pnl: Decimal | None
    """매도만."""
    filled_utc: datetime


@dataclass(frozen=True)
class OrderPlan:
    market: str
    side: OrderSide
    kind: OrderKind
    reason: OrderReason
    price: Decimal | None
    qty: Decimal
    amount: Decimal
    """가격 × 수량. 시장가 매도는 그때 최우선 매수 호가 × 수량의 어림."""
