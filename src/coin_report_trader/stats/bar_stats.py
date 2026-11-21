"""한 코인의 저장된 봉으로 봉 통계(`BarStats`) 하나를 셈한다(M1 명세 3.5, 3.5.1).

- 쓰는 봉: 고른 단위의 완성된 봉 가운데 시작 시각이 가장 늦은 것부터 N개. 시작 시각 순으로
  늘어놓아 셈한다. 완성은 봉의 시작 시각에 단위의 길이(`CANDLE_UNIT_LENGTHS`)를 더한 때가 `now`와
  같거나 앞인 것이다(뒤이면 미완성이라 셈에 넣지 않는다). 봉이 없는 구간은 채우지 않는다.
- 봉이 모자람: 완성된 봉이 N개보다 적으면 셈하지 않는다. `bar_count`에 있는 완성된 봉의 수를
  담고 `first_start_utc`부터 `low`까지는 모두 `None`이다.
- 셈은 `Decimal`(정밀도 28자리)이고 `float`를 거치지 않는다. 비율은 0.0001 단위로
  `ROUND_HALF_EVEN`으로 맞춘다. 가격은 받은 값 그대로다. 나누는 값이 0이면 그 요소는 `None`이다.

계산만 한다. 파일, DB, 네트워크, 시계를 읽지 않는다.
"""

import itertools
from collections.abc import Iterable
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Final

from coin_report_trader.core import CANDLE_UNIT_LENGTHS, BarStats, Candle, CandleUnit

_PRECISION: Final = 28
_RATIO_STEP: Final = Decimal("0.0001")


def compute_bar_stats(
    candles: Iterable[Candle], now: datetime, *, market: str, unit: CandleUnit, count: int
) -> tuple[BarStats, bool]:
    """`market`의 `unit` 봉으로 마지막 `count`개의 완성된 봉의 통계를 셈한다.

    `candles`에서 다른 코인과 다른 단위의 봉은 거른다. 같은 시작 시각이 겹치면 뒤의 것을 쓴다.
    돌려주는 `bool`은 `last_incomplete`: 그 단위의 저장된 마지막 봉이 미완성인지다.
    """
    unit = CandleUnit(unit)
    length = CANDLE_UNIT_LENGTHS[unit]
    by_start = {c.start: c for c in candles if c.market == market and c.unit == unit}
    ordered = [by_start[start] for start in sorted(by_start)]
    last_incomplete = bool(ordered) and ordered[-1].start + length > now
    complete = [c for c in ordered if c.start + length <= now]
    if len(complete) < count:
        return BarStats(market, unit, count, len(complete)), last_incomplete
    used = complete[len(complete) - count :] if count > 0 else []
    return _stats(market, unit, count, used), last_incomplete


def _stats(market: str, unit: CandleUnit, count: int, used: list[Candle]) -> BarStats:
    if not used:
        return BarStats(market, unit, count, 0)
    with localcontext() as context:
        context.prec = _PRECISION
        n = len(used)
        closes = [c.close for c in used]
        first, last = used[0], used[-1]
        mean = sum(closes, Decimal(0)) / n
        highest = max(c.high for c in used)
        return BarStats(
            market=market,
            unit=unit,
            requested_count=count,
            bar_count=n,
            first_start_utc=first.start,
            last_start_utc=last.start,
            close_last=last.close,
            change=_ratio(last.close, first.open),
            slope=_slope(closes, mean),
            vs_mean=_ratio(last.close, mean),
            up_ratio=_round(Decimal(sum(1 for c in used if c.close > c.open)) / n),
            volatility=_volatility(closes),
            max_drawdown=_max_drawdown(closes),
            from_high=_ratio(last.close, highest),
            high=highest,
            low=min(c.low for c in used),
        )


def _round(value: Decimal) -> Decimal:
    """0.0001 단위로 맞춘다. 음의 0은 0으로 쓴다."""
    rounded = value.quantize(_RATIO_STEP, rounding=ROUND_HALF_EVEN)
    return rounded.copy_abs() if rounded == 0 else rounded


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """`numerator ÷ denominator − 1`. 나누는 값이 0이면 `None`이다."""
    if denominator == 0:
        return None
    return _round(numerator / denominator - 1)


def _slope(closes: list[Decimal], mean: Decimal) -> Decimal | None:
    """봉 번호 i(1…n)에 대한 종가의 최소제곱 기울기를 종가 평균으로 나눈 값."""
    n = len(closes)
    if n < 2 or mean == 0:
        return None
    center = Decimal(n + 1) / 2
    pairs = ((Decimal(i) - center, c - mean) for i, c in enumerate(closes, 1))
    numerator = sum((offset * gap for offset, gap in pairs), Decimal(0))
    denominator = sum(((Decimal(i) - center) ** 2 for i in range(1, n + 1)), Decimal(0))
    return _round(numerator / denominator / mean)


def _volatility(closes: list[Decimal]) -> Decimal | None:
    """이웃한 두 봉의 종가 변화율(c_i ÷ c_{i-1} − 1)들의 모표준편차."""
    if len(closes) < 2 or any(c == 0 for c in closes[:-1]):
        return None
    changes = [after / before - 1 for before, after in itertools.pairwise(closes)]
    mean = sum(changes, Decimal(0)) / len(changes)
    variance = sum(((x - mean) ** 2 for x in changes), Decimal(0)) / len(changes)
    return _round(variance.sqrt())


def _max_drawdown(closes: list[Decimal]) -> Decimal | None:
    """종가를 차례로 볼 때 c_i ÷ (그때까지의 가장 큰 종가) − 1의 가장 작은 값(0 이하)."""
    peak = closes[0]
    if peak == 0:
        return None
    worst = Decimal(0)
    for close in closes:
        peak = max(peak, close)
        worst = min(worst, close / peak - 1)
    return _round(worst)
