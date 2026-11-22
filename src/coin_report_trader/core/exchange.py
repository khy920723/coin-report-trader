"""거래소 경계의 값과 실거래 설정(M5 명세 3.4). 표준 라이브러리만 쓴다.

가격, 수량, 금액, 비율은 `Decimal`, 시각은 UTC가 붙은 `datetime`이다. 모의매매는 `PaperExchange`,
실거래는 `UpbitExchange`가 같은 `ExchangePort`(`core/ports.py`)로 이 값들을 주고받는다.
"""

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from coin_report_trader.core.trade import (
    OrderKind,
    OrderSide,
    OrderState,
    TradeSettings,
)

UPBIT_KEY_VALID_DAYS: Final = 365
KEY_EXPIRY_WARN_DAYS: Final = 30
LIVE_CONFIRM_SECONDS: Final = 10
"""켤 때의 키 확인 시간 제한(초)."""


@dataclass(frozen=True)
class ExchangeHolding:
    market: str
    balance: Decimal
    """주문 가능 수량."""
    locked: Decimal
    """주문에 묶인 수량."""
    avg_buy_price: Decimal | None
    """거래소의 평균 매수가. 없거나 0이면 `None`."""


@dataclass(frozen=True)
class ExchangeAccount:
    krw: Decimal
    krw_locked: Decimal
    holdings: tuple[ExchangeHolding, ...]
    read_utc: datetime


@dataclass(frozen=True)
class ExchangeRules:
    """실거래는 주문 가능 정보에서, 모의매매는 `PAPER_FEE_RATE`와 5,000원으로 만든다."""

    bid_fee: Decimal
    ask_fee: Decimal
    """수수료율(예: 0.0005)."""
    min_total: Decimal
    """최소 주문 금액."""
    max_total: Decimal | None


@dataclass(frozen=True)
class ExchangeTrade:
    trade_key: str
    """거래소의 체결 식별값. 모의매매는 `"paper"`."""
    price: Decimal
    qty: Decimal
    fee: Decimal
    filled_utc: datetime


@dataclass(frozen=True)
class OrderSnapshot:
    uid: str
    """우리 식별값 = 거래소의 identifier."""
    market: str
    side: OrderSide
    kind: OrderKind
    price: Decimal | None
    qty: Decimal
    executed_qty: Decimal
    state: OrderState
    trades: tuple[ExchangeTrade, ...]
    program: bool
    """우리가 낸 주문인지. 체결 대기 주문 목록에는 사용자가 직접 건 주문도 든다."""


@dataclass(frozen=True)
class PlaceResult:
    snapshot: OrderSnapshot | None
    error_name: str | None
    unknown: bool
    """결과를 모름: 응답이 없음, 주문 생성의 2xx를 읽지 못함, 주문 생성의 5xx(3.6.3)."""


class LiveBlock(StrEnum):
    """실거래 켜기가 막힌 까닭. 값이 곧 화면의 글이다. 값에 `{…}`가 든 둘은 자리를 채워 쓴다
    (`live_block_not_enough_rounds`, `live_block_budget_too_big`)."""

    NOT_ALLOWED = "이 판에서는 실거래를 켤 수 없습니다"
    NO_KEY = "업비트 키가 없습니다"
    NOT_PAUSED = "멈춤 상태가 아닙니다"
    NO_COINS = "고른 코인이 없습니다"
    NOT_ENOUGH_ROUNDS = "{코인}의 모의 회차가 모자랍니다({센 수}/{N})"
    NO_SELL_ALL = "모의매매에서 '멈추고 전량 매도'를 한 번 해야 합니다"
    NO_SETTINGS = "실거래 설정(예산, 코인별 투자 비율, 하루 손실 한도)을 넣어야 합니다"
    BUDGET_TOO_BIG = (
        "실거래 예산이 원화 잔고와 보유 평가 금액의 합보다 큽니다(예산 {금액}원, 합 {금액}원)"
    )


def _won(amount: Decimal) -> str:
    """원 아래를 버려 쉼표를 붙인 글(설계서 11)."""
    return f"{int(amount):,}"


def live_block_not_enough_rounds(coin: str, counted: int, needed: int) -> str:
    return LiveBlock.NOT_ENOUGH_ROUNDS.value.format(**{"코인": coin, "센 수": counted, "N": needed})


def live_block_budget_too_big(budget: Decimal, total: Decimal) -> str:
    text = LiveBlock.BUDGET_TOO_BIG.value
    return text.replace("{금액}", _won(budget), 1).replace("{금액}", _won(total), 1)


@dataclass(frozen=True)
class LiveSettings:
    """실거래 전용 설정(3.7.5). 처음 칸은 셋 모두 비어 있다."""

    budget: Decimal | None = None
    """예산(원)."""
    coin_ratios: Mapping[str, Decimal] = field(default_factory=lambda: MappingProxyType({}))
    """마켓 코드에서 투자 비율(%)."""
    daily_loss_limit: Decimal | None = None
    """하루 손실 한도(원)."""

    def complete(self, coins: tuple[str, ...]) -> bool:
        """셋이 다 있고 고른 코인마다 비율이 있으면 참이다."""
        if self.budget is None or self.daily_loss_limit is None or not self.coin_ratios:
            return False
        return all(coin in self.coin_ratios for coin in coins)

    def apply(self, trade: TradeSettings) -> TradeSettings:
        """`budget`, `coin_ratios`, `daily_loss_limit`만 바꾼 `TradeSettings`. `complete`가 참일
        때만 부른다(예산과 하루 손실 한도가 없으면 `ValueError`)."""
        if self.budget is None or self.daily_loss_limit is None:
            raise ValueError("실거래 설정이 다 채워지지 않았다")
        return dataclasses.replace(
            trade,
            budget=self.budget,
            coin_ratios=self.coin_ratios,
            daily_loss_limit=self.daily_loss_limit,
        )


@dataclass(frozen=True)
class LiveConfirmView:
    """SCR-06-9에 보일 값(3.7.2)."""

    budget: Decimal
    coins: tuple[tuple[str, Decimal, Decimal], ...]
    """(코인, 투자 비율, 코인 예산)."""
    krw_available: Decimal
    holdings: tuple[tuple[str, Decimal, Decimal | None], ...]
    """(코인, 보유 수량(`balance` + `locked`), 평가 금액). 현재가를 읽지 못하면 `None`."""
    total: Decimal
    """원화 잔고 + 평가 금액의 합."""
    as_of: datetime | None = None
