"""한 코인의 저장된 봉으로 다섯 기간의 통계와 지표를 셈한다(M1 명세 3.5, 3.6).

- 완성된 봉만 쓴다: 봉의 시작 시각에 단위(1일, 60분)를 더한 때가 `now`보다 뒤이면 미완성이다.
  더한 때가 `now`와 같으면 완성이다. 단위마다 L은 마지막으로 완성된 봉의 시작 시각이다.
- 셈은 `Decimal`(정밀도 28자리)이고 `float`를 거치지 않는다. 비율은 0.0001 단위로
  `ROUND_HALF_EVEN`으로 맞춘다. 가격은 받은 값 그대로이고, `avg_value`는 맞추지 않는다.
- 기간 안의 봉이 2개보다 적으면 그 `PeriodStats`는 `count`만 있고 나머지 값은 `None`이다.
"""

import itertools
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Final

from coin_report_trader.core import (
    PERIODS,
    Candle,
    CandleIndicator,
    CandleUnit,
    PeriodStats,
    empty_periods,
)

_PRECISION: Final = 28
_RATIO_STEP: Final = Decimal("0.0001")
_UNIT_LENGTH: Final = {
    CandleUnit.DAY: timedelta(days=1),
    CandleUnit.MINUTE_60: timedelta(hours=1),
}
# 기간 이름 → L에서 거슬러 오르는 길이(`None`이면 저장된 것 전부). 3.5 표.
_LOOKBACK: Final = {
    "전체": None,
    "1년": timedelta(days=364),
    "3개월": timedelta(days=89),
    "1개월": timedelta(days=29),
    "1주": timedelta(hours=167),
}
_MA_SHORT: Final = 20
_MA_LONG: Final = 120


@dataclass(frozen=True)
class CandleStats:
    """한 코인의 셈 결과. `app`이 `CoinView`의 세 필드(`periods` 등)에 담는다."""

    periods: tuple[PeriodStats, ...]
    """늘 다섯이고 `core.PERIODS`의 차례다."""
    indicator: CandleIndicator | None
    """완성된 일봉이 없으면 `None`."""
    last_incomplete: bool
    """저장된 마지막 일봉이 미완성인지(저장된 일봉이 없으면 거짓)."""

    @property
    def has_values(self) -> bool:
        """다섯 기간 가운데 하나 이상에 값이 나왔는지(모두 "봉이 모자람"이면 거짓)."""
        return any(period.change is not None for period in self.periods)


def compute_candle_stats(candles: Iterable[Candle], now: datetime) -> CandleStats:
    """한 코인의 봉(일봉과 60분봉, 차례 무관)과 지금(`Clock.now()`의 값)으로 셈한다.

    같은 단위에서 시작 시각이 같은 봉은 나중에 온 것을 쓴다. `now`와 봉의 시작 시각은 UTC가
    붙은 시각이어야 한다(아니면 `ValueError`). 코인을 가리지 않으므로 한 코인의 봉만 넘긴다.
    """
    _require_aware(now, "now")
    by_unit: dict[CandleUnit, dict[datetime, Candle]] = {unit: {} for unit in _UNIT_LENGTH}
    for candle in candles:
        _require_aware(candle.start, "봉의 시작 시각")
        by_unit[CandleUnit(candle.unit)][candle.start] = candle
    stored = {unit: [found[start] for start in sorted(found)] for unit, found in by_unit.items()}
    complete = {
        unit: [c for c in series if c.start + _UNIT_LENGTH[unit] <= now]
        for unit, series in stored.items()
    }
    periods = tuple(
        _period_stats(name, unit, complete[unit]) if complete[unit] else empty
        for (name, unit), empty in zip(PERIODS, empty_periods(), strict=True)
    )
    days = stored[CandleUnit.DAY]
    last_incomplete = bool(days) and days[-1].start + _UNIT_LENGTH[CandleUnit.DAY] > now
    done_days = complete[CandleUnit.DAY]
    indicator = _indicator(periods, done_days) if done_days else None
    return CandleStats(periods, indicator, last_incomplete)


def _require_aware(moment: datetime, what: str) -> None:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{what}은 시간대가 붙은 시각이어야 합니다")


def _period_stats(name: str, unit: CandleUnit, complete: Sequence[Candle]) -> PeriodStats:
    last = complete[-1].start  # L
    lookback = _LOOKBACK[name]
    if lookback is None:
        used = list(complete)
    else:
        used = [c for c in complete if c.start >= last - lookback]
    if len(used) < 2:
        return PeriodStats(period=name, unit=unit, count=len(used))
    with localcontext() as context:
        context.prec = _PRECISION
        return _full_stats(name, unit, used)


def _full_stats(name: str, unit: CandleUnit, used: Sequence[Candle]) -> PeriodStats:
    count = len(used)
    first, last = used[0], used[-1]
    high = max(c.high for c in used)
    low = min(c.low for c in used)
    closes = [c.close for c in used]
    return PeriodStats(
        period=name,
        unit=unit,
        count=count,
        first_start=first.start,
        last_start=last.start,
        open_price=first.open,
        close_price=last.close,
        change=_ratio(last.close, first.open),
        high=high,
        low=low,
        from_high=_ratio(last.close, high),
        max_drawdown=_max_drawdown(closes),
        volatility=_volatility(closes),
        up_ratio=_round(Decimal(sum(1 for c in used if c.close > c.open)) / count),
        avg_value=sum((c.value for c in used), Decimal(0)) / count,
    )


def _round(value: Decimal) -> Decimal:
    return value.quantize(_RATIO_STEP, rounding=ROUND_HALF_EVEN)


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """`numerator ÷ denominator − 1`을 맞춘 값. 나눌 수 없으면 `None`."""
    change = _change(numerator, denominator)
    return None if change is None else _round(change)


def _change(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if not denominator.is_finite() or not numerator.is_finite() or denominator == 0:
        return None
    return numerator / denominator - 1


def _max_drawdown(closes: Sequence[Decimal]) -> Decimal | None:
    peak = closes[0]
    worst = Decimal(0)
    for close in closes:
        peak = max(peak, close)
        drop = _change(close, peak)
        if drop is None:
            return None
        worst = min(worst, drop)
    return _round(worst)


def _volatility(closes: Sequence[Decimal]) -> Decimal | None:
    changes = []
    for before, after in itertools.pairwise(closes):
        change = _change(after, before)
        if change is None:
            return None
        changes.append(change)
    mean = sum(changes, Decimal(0)) / len(changes)
    variance = sum(((c - mean) ** 2 for c in changes), Decimal(0)) / len(changes)
    return _round(variance.sqrt())


def _moving_average_gap(done_days: Sequence[Candle], length: int) -> Decimal | None:
    if len(done_days) < length:
        return None
    window = [c.close for c in done_days[-length:]]
    with localcontext() as context:
        context.prec = _PRECISION
        return _ratio(done_days[-1].close, sum(window, Decimal(0)) / length)


def _indicator(periods: Sequence[PeriodStats], done_days: Sequence[Candle]) -> CandleIndicator:
    by_name = {period.period: period for period in periods}
    year, quarter, week = by_name["1년"], by_name["3개월"], by_name["1주"]
    return CandleIndicator(
        trend_1y=year.change,
        trend_3m=quarter.change,
        vs_ma20=_moving_average_gap(done_days, _MA_SHORT),
        vs_ma120=_moving_average_gap(done_days, _MA_LONG),
        volatility_1y=year.volatility,
        volatility_1w=week.volatility,
        max_drawdown_1y=year.max_drawdown,
    )
