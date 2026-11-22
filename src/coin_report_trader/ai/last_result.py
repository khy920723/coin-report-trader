"""지난 판단의 결과(M2 명세 3.9). 계산만 한다.

봉은 `app`이 `storage`에서 읽어 넘긴다. 실제 값은 지난 판단의 단위의 봉 가운데 시작 시각이 지난
판단의 T인 봉(곧 예측이 가리킨 때에 끝나는 봉)의 종가이고, 그 봉이 저장되어 있고 완성되어 있을
때만 있다. 없으면 `None`이고, 차이와 맞았는지도 `None`이다.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Final

from coin_report_trader.core import (
    CANDLE_UNIT_LENGTHS,
    CandleUnit,
    Direction,
    LastResult,
    Verdict,
)

_RATIO_STEP: Final = Decimal("0.0001")


@dataclass(frozen=True)
class PastJudgment:
    """지난 판단(그 코인, 그 모드의 가장 최근 정상 보고서)에서 셈에 쓰는 값."""

    round_number: int
    close_utc: datetime
    """지난 판단의 T(그 회차의 봉 마감 시각)."""
    unit: CandleUnit
    base_price: Decimal
    """그때 최종 예측에 준 현재가 P."""
    direction: Direction
    predicted_price: Decimal


def target_close_utc(judgment: PastJudgment) -> datetime:
    """예측 가격이 가리킨 때: 지난 판단의 T + 단위의 길이(그 판단의 다음 회차 때)."""
    return judgment.close_utc + CANDLE_UNIT_LENGTHS[CandleUnit(judgment.unit)]


def candle_start_for_actual(judgment: PastJudgment) -> datetime:
    """실제 값을 읽을 봉의 시작 시각: 지난 판단의 T다."""
    return judgment.close_utc


def compute_last_result(judgment: PastJudgment, candle_close: Decimal | None) -> LastResult:
    """지난 판단의 결과를 셈한다.

    - 차이: 실제 값 ÷ 예측 가격 − 1의 비율, 0.0001 단위(`ROUND_HALF_EVEN`). 양수면 실제가 예측보다
      높다.
    - 맞았는지: 기준은 지난 판단에 준 현재가 P다. '오를 것'은 실제 값 ≥ P이면 맞음, < P이면
      틀림이다. '내릴 것'은 실제 값 ≤ P이면 맞음, > P이면 틀림이다(같으면 둘 다 맞음).
    - `candle_close`가 `None`이면 실제 값, 차이, 맞았는지가 모두 `None`이다.
    """
    if candle_close is None:
        return LastResult(judgment.round_number, judgment.predicted_price, None, None, None)
    diff = None
    if judgment.predicted_price != 0:
        with localcontext() as context:
            context.prec = 28
            ratio = candle_close / judgment.predicted_price - 1
            diff = ratio.quantize(_RATIO_STEP, rounding=ROUND_HALF_EVEN)
    if judgment.direction is Direction.UP:
        hit = Verdict.HIT if candle_close >= judgment.base_price else Verdict.MISS
    else:
        hit = Verdict.HIT if candle_close <= judgment.base_price else Verdict.MISS
    return LastResult(judgment.round_number, judgment.predicted_price, candle_close, diff, hit)
