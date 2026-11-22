"""판단표(M3 명세 3.5.2, 제품 규칙 4절).

`decide`가 방식, 방향 예측, 보유로 할 일을 정하고, 허용 범위로 넓히기(3.8.5), 호가 단위 맞추기,
가격 안전 검사, 수량, 최소 주문 금액(3.8.1)까지 해서 맞춘 값으로 `OrderPlan`을 만든다. 한도는
`check_buy`와 `check_cancel_cap`(3.8.2)이 따로 본다. 계산만 한다.

- 판단표(3.5.2의 표): 추세추종은 보유가 없고 '오를 것'이면 매수 제안가에 첫 매수, 보유 중
  '오를 것'이면 현재가 > 평균 매수가일 때 추가 매수, 보유 중 '내릴 것'이면 매도 제안가에 전량
  매도다. 평균회귀는 보유가 없고 '내릴 것'이면 예측 가격에 첫 매수, 보유 중 '오를 것'이면 예측
  가격에 평균회귀의 매도, 보유 중 '내릴 것'이면 예측 가격에 추가 매수(손실 중이어도)다. 그 밖은
  "아무것도 하지 않음"이다. 매수 금액은 코인 예산 × 그 비율, 매도 수량은 팔 수 있는 수량이다.
- 차례: 넓히기 → 호가 단위 맞추기 → (평균회귀의 매도 조건) → 가격 안전 검사 → 수량 → 최소 주문
  금액. 평균회귀의 매도 조건은 넓혀 맞춘 가격 P로 본다.
- 보유 중은 `holding.qty > 0`이다(팔 수 없는 잔량만 있어도 보유 중. 2절).
- 알림: 평균 매수가가 없거나 0이면 A-9, 가격 안전 검사에 걸리면 A-4, 매도가 최소 주문 금액보다
  작으면 A-3이다. 알림을 낼지(되풀이)는 `app`이 정한다(3.14.1).
"""

from dataclasses import dataclass
from decimal import Decimal

from coin_report_trader.core.ai import Direction, ReportState
from coin_report_trader.core.trade import (
    NoOrderReason,
    OrderKind,
    OrderPlan,
    OrderReason,
    OrderRules,
    OrderSide,
    PaperHolding,
    TradeMethod,
    TradeSettings,
)
from coin_report_trader.core.views import ReportView
from coin_report_trader.trading.orders.prices import buy_qty, price_guard_ok
from coin_report_trader.trading.orders.tolerance import widen_price

_ALERT_TOO_SMALL = "A-3"
_ALERT_PRICE_GUARD = "A-4"
_ALERT_NO_AVG = "A-9"
_HUNDRED = Decimal(100)


@dataclass(frozen=True)
class Decision:
    """`decide`의 결과. `plan`과 `reason` 가운데 꼭 하나만 있다."""

    plan: OrderPlan | None
    reason: NoOrderReason | None
    alert: str | None
    """그때 만들 알림 번호(예: "A-4"). 없으면 `None`."""

    def __post_init__(self) -> None:
        if (self.plan is None) == (self.reason is None):
            raise ValueError("plan과 reason 가운데 꼭 하나만 있어야 한다")


def decide(
    method: TradeMethod,
    direction: Direction,
    holding: PaperHolding | None,
    price: Decimal,
    report: ReportView,
    settings: TradeSettings,
    fee_rate: Decimal | None,
    rules: OrderRules,
) -> Decision:
    """판단표로 할 일을 정하고 주문 값을 만든다.

    - `price`: 계정 읽기에서 새로 읽은 현재가(가격 안전 검사의 C, 추세추종의 추가 매수 조건).
    - `report`: 그 코인의 이번 회차 보고서(`OK`). 매수·매도 제안가와 예측 가격을 쓴다. `OK`가
      아니거나 쓸 가격이 없으면 "판단을 받지 못함"이다.
    - `fee_rate`: 수수료율. 모르면 `None`(모의매매는 늘 `PAPER_FEE_RATE`).
    - `rules`: 그 코인의 호가 단위와 최소 주문 금액.
    """
    if price <= 0:
        raise ValueError("현재가는 0보다 커야 한다")
    market = report.market
    held = holding is not None and holding.qty > 0
    if report.state is not ReportState.OK:
        return _skip(NoOrderReason.NO_JUDGMENT)
    if method is TradeMethod.TREND:
        if not held:
            if direction is Direction.DOWN:
                return _skip(NoOrderReason.NOTHING)
            return _buy(
                market, report.buy_price, _ratio(settings.first_ratio), price, settings, rules
            )
        assert holding is not None
        if direction is Direction.DOWN:
            return _sell(market, report.sell_price, holding, price, settings, rules, None)
        if not _has_avg(holding):
            return _skip(NoOrderReason.NO_AVG_PRICE, _ALERT_NO_AVG)
        assert holding.avg_price is not None
        if price <= holding.avg_price:
            return _skip(NoOrderReason.PRICE_NOT_ABOVE_AVG)
        return _buy(market, report.buy_price, settings.add_ratio, price, settings, rules)
    if not held:
        if direction is Direction.UP:
            return _skip(NoOrderReason.NOTHING)
        return _buy(
            market, report.predicted_price, _ratio(settings.mr_first_ratio), price, settings, rules
        )
    assert holding is not None
    if not _has_avg(holding):
        return _skip(NoOrderReason.NO_AVG_PRICE, _ALERT_NO_AVG)
    if direction is Direction.DOWN:
        return _buy(market, report.predicted_price, settings.add_ratio, price, settings, rules)
    return _sell(
        market,
        report.predicted_price,
        holding,
        price,
        settings,
        rules,
        _MeanReversionSell(fee_rate, settings),
    )


@dataclass(frozen=True)
class _MeanReversionSell:
    fee_rate: Decimal | None
    settings: TradeSettings

    def blocked(self, sell_price: Decimal, avg_price: Decimal) -> NoOrderReason | None:
        """평균회귀의 매도 조건(4절, REQ-TRADE-011, REQ-TRADE-025). P는 넓혀 맞춘 가격이다."""
        f = self.fee_rate
        if f is not None:
            if sell_price * (1 - f) > avg_price * (1 + f):
                return None
            return NoOrderReason.NOT_OVER_AVG_AND_FEE
        if not self.settings.mr_sell_unknown_fee:
            return NoOrderReason.FEE_UNKNOWN
        floor = avg_price * (1 - self.settings.mr_tolerable_loss / _HUNDRED)
        return None if sell_price >= floor else NoOrderReason.NOT_OVER_AVG_AND_FEE


def _buy(
    market: str,
    base: Decimal | None,
    ratio: Decimal,
    current: Decimal,
    settings: TradeSettings,
    rules: OrderRules,
) -> Decision:
    if base is None or base <= 0:
        return _skip(NoOrderReason.NO_JUDGMENT)
    price = widen_price(base, OrderSide.BUY, settings.tolerance, rules.tick_size)
    if not price_guard_ok(price, OrderSide.BUY, current):
        return _skip(NoOrderReason.PRICE_GUARD, _ALERT_PRICE_GUARD)
    amount = _coin_budget(settings, market) * ratio / _HUNDRED
    qty = buy_qty(amount, price)
    if price * qty < rules.min_order:
        return _skip(NoOrderReason.BELOW_MIN_ORDER)
    return _plan(market, OrderSide.BUY, price, qty)


def _sell(
    market: str,
    base: Decimal | None,
    holding: PaperHolding,
    current: Decimal,
    settings: TradeSettings,
    rules: OrderRules,
    mean_reversion: _MeanReversionSell | None,
) -> Decision:
    if base is None or base <= 0:
        return _skip(NoOrderReason.NO_JUDGMENT)
    price = widen_price(base, OrderSide.SELL, settings.tolerance, rules.tick_size)
    if mean_reversion is not None:
        assert holding.avg_price is not None
        blocked = mean_reversion.blocked(price, holding.avg_price)
        if blocked is not None:
            return _skip(blocked)
    if not price_guard_ok(price, OrderSide.SELL, current):
        return _skip(NoOrderReason.PRICE_GUARD, _ALERT_PRICE_GUARD)
    qty = holding.qty - holding.locked_qty
    if qty <= 0:
        return _skip(NoOrderReason.NO_SELLABLE_QTY)
    if price * qty < rules.min_order:
        return _skip(NoOrderReason.BELOW_MIN_ORDER, _ALERT_TOO_SMALL)
    return _plan(market, OrderSide.SELL, price, qty)


def _plan(market: str, side: OrderSide, price: Decimal, qty: Decimal) -> Decision:
    plan = OrderPlan(
        market=market,
        side=side,
        kind=OrderKind.LIMIT,
        reason=OrderReason.JUDGMENT,
        price=price,
        qty=qty,
        amount=price * qty,
    )
    return Decision(plan=plan, reason=None, alert=None)


def _skip(reason: NoOrderReason, alert: str | None = None) -> Decision:
    return Decision(plan=None, reason=reason, alert=alert)


def _has_avg(holding: PaperHolding) -> bool:
    return holding.avg_price is not None and holding.avg_price > 0


def _ratio(value: Decimal | None) -> Decimal:
    if value is None:
        raise ValueError("고른 방식의 첫 매수 비율이 없다")
    return value


def _coin_budget(settings: TradeSettings, market: str) -> Decimal:
    if market not in settings.coin_ratios:
        raise ValueError(f"투자 비율이 없는 코인이다: {market}")
    return settings.coin_budget(market)
