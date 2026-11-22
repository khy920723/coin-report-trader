"""가상 계정의 셈(M3 명세 3.7.1)과 수수료와 손익(3.7.3).

- 계산만 하고 DB를 열지 않는다(3.2). 값은 `app`이 `storage`에서 읽어 넘기고, 돌려받은 계정과
  보유를 `app`이 쓴다.
- 모든 값은 `Decimal`이고 원 아래를 버리지 않는다(3.7.3).
- 평균 매수가는 보유가 0이 된 뒤의 매수만으로 `buy_amount_sum ÷ buy_qty_sum`이다(수수료는 넣지
  않는다). 팔아도 바뀌지 않고, 보유가 0이 되면 두 합을 0으로, 평균 매수가를 `None`으로 둔다.
- 묶음: 지정가 매수를 걸면 가격 × 수량 × (1 + f)를 `locked_krw`에, 지정가 매도를 걸면 그 수량을
  `locked_qty`에 묶는다. 취소하면 푼다. 시장가는 바로 체결되므로 묶지 않는다(3.7.2).
- 계정의 규칙을 어기는 값(모자란 원화, 모자란 수량, 시장가 매수 등)은 부르는 쪽의 실수로 보고
  `ValueError`다. 모자람을 막는 것은 한도(3.8.2)가 먼저 한다.
"""

from dataclasses import dataclass, replace
from decimal import Decimal

from coin_report_trader.core.trade import (
    PAPER_FEE_RATE,
    OrderKind,
    OrderRecord,
    OrderSide,
    OrderState,
    PaperAccount,
    PaperHolding,
)
from coin_report_trader.trading.paper.fills import Fill

_ZERO = Decimal(0)


@dataclass(frozen=True)
class Settlement:
    """체결 하나를 계정에 넣은 결과. `app`이 한 트랜잭션에서 쓴다(3.7.2)."""

    account: PaperAccount
    """바뀐 계정(원화, 묶음, 보유)."""
    holding: PaperHolding
    """그 코인의 바뀐 보유(`paper_holdings`의 줄)."""
    fee: Decimal
    """체결 금액 × f."""
    realized_pnl: Decimal | None
    """매도만. 수량 × (매도 가격 × (1 − f) − 평균 매수가 × (1 + f))."""


def new_account(funds: Decimal) -> PaperAccount:
    """처음 계정: 가상 자금이 가상 원화이고 보유는 없다(3.7.1)."""
    if funds <= 0:
        raise ValueError("가상 자금은 0보다 커야 한다")
    return PaperAccount(krw=funds, locked_krw=_ZERO, holdings=())


def make_holding(
    market: str,
    qty: Decimal,
    locked_qty: Decimal,
    buy_amount_sum: Decimal,
    buy_qty_sum: Decimal,
) -> PaperHolding:
    """저장된 값(`paper_holdings`의 줄)으로 보유를 만든다. 평균 매수가를 셈한다(9절).

    보유가 0이거나 매수 수량의 합이 0이면 평균 매수가는 `None`이다.
    """
    if qty < 0 or locked_qty < 0 or locked_qty > qty:
        raise ValueError("보유 수량과 묶인 수량이 맞지 않는다")
    if buy_amount_sum < 0 or buy_qty_sum < 0:
        raise ValueError("매수 합은 0보다 작을 수 없다")
    avg = None
    if qty > 0 and buy_qty_sum > 0:
        avg = buy_amount_sum / buy_qty_sum
    return PaperHolding(
        market=market,
        qty=qty,
        locked_qty=locked_qty,
        avg_price=avg,
        buy_amount_sum=buy_amount_sum,
        buy_qty_sum=buy_qty_sum,
    )


def holding_of(account: PaperAccount, market: str) -> PaperHolding | None:
    """그 코인의 보유. 줄이 없으면 `None`이다."""
    for holding in account.holdings:
        if holding.market == market:
            return holding
    return None


def sellable_qty(holding: PaperHolding | None) -> Decimal:
    """팔 수 있는 수량(2절): `qty − locked_qty`. 보유가 없으면 0이다."""
    if holding is None:
        return _ZERO
    return holding.qty - holding.locked_qty


def is_dust(holding: PaperHolding | None, price: Decimal, min_order: Decimal) -> bool:
    """팔 수 없는 잔량(2절): 보유 전체의 평가 금액(`qty × 현재가`)이 최소 주문 금액보다 작다.

    보유가 없거나 0이면 거짓이다.
    """
    if holding is None or holding.qty <= 0:
        return False
    return holding.qty * price < min_order


def available_krw(account: PaperAccount) -> Decimal:
    """묶이지 않은 가상 원화: `krw − locked_krw`."""
    return account.krw - account.locked_krw


def fee_of(price: Decimal, qty: Decimal, fee_rate: Decimal = PAPER_FEE_RATE) -> Decimal:
    """수수료: 체결 금액(가격 × 수량) × f(3.7.3)."""
    return price * qty * fee_rate


def buy_lock_amount(price: Decimal, qty: Decimal, fee_rate: Decimal = PAPER_FEE_RATE) -> Decimal:
    """지정가 매수를 걸 때 묶는 원화: 가격 × 수량 × (1 + f)(3.7.1)."""
    return price * qty * (1 + fee_rate)


def lock_order(
    account: PaperAccount, order: OrderRecord, fee_rate: Decimal = PAPER_FEE_RATE
) -> PaperAccount:
    """걸린 지정가 주문의 묶음을 더한다(3.7.1)."""
    price = _limit_price(order)
    if order.side is OrderSide.BUY:
        amount = buy_lock_amount(price, order.qty, fee_rate)
        if amount > available_krw(account):
            raise ValueError("묶을 가상 원화가 모자란다")
        return replace(account, locked_krw=account.locked_krw + amount)
    holding = holding_of(account, order.market)
    if order.qty > sellable_qty(holding):
        raise ValueError("묶을 수량이 팔 수 있는 수량보다 많다")
    assert holding is not None
    return _put(account, replace(holding, locked_qty=holding.locked_qty + order.qty))


def unlock_order(
    account: PaperAccount, order: OrderRecord, fee_rate: Decimal = PAPER_FEE_RATE
) -> PaperAccount:
    """취소하거나 보내지 않은 것으로 닫은 지정가 주문의 묶음을 푼다(3.7.1, 3.8.3)."""
    price = _limit_price(order)
    if order.side is OrderSide.BUY:
        amount = buy_lock_amount(price, order.qty, fee_rate)
        if amount > account.locked_krw:
            raise ValueError("풀 원화가 묶인 원화보다 많다")
        return replace(account, locked_krw=account.locked_krw - amount)
    holding = holding_of(account, order.market)
    if holding is None or order.qty > holding.locked_qty:
        raise ValueError("풀 수량이 묶인 수량보다 많다")
    return _put(account, replace(holding, locked_qty=holding.locked_qty - order.qty))


def apply_fill(
    account: PaperAccount,
    order: OrderRecord,
    fill: Fill,
    fee_rate: Decimal = PAPER_FEE_RATE,
) -> Settlement:
    """체결 하나를 계정에 넣는다(3.7.2, 3.7.3).

    - 걸린 뒤에 체결된 지정가 주문(`fill.at_place`가 거짓)은 먼저 그 주문의 묶음을 푼다. 넣을 때
      바로 체결된 주문과 시장가는 묶이지 않았다.
    - 매수: 가상 원화에서 가격 × 수량 × (1 + f)를 빼고, 보유와 매수 합에 더한다.
    - 매도: 가상 원화에 가격 × 수량 × (1 − f)를 더하고, 보유에서 뺀다. 보유가 0이 되면 두 합을
      0으로 둔다.
    """
    if fill.order_id != order.order_id or fill.market != order.market or fill.side != order.side:
        raise ValueError("체결이 그 주문의 것이 아니다")
    if fill.qty != order.qty:
        raise ValueError("모의매매는 전부 체결이다")
    if order.state is not OrderState.OPEN:
        raise ValueError("걸린 주문만 체결된다")
    if order.kind is OrderKind.LIMIT and not fill.at_place:
        account = unlock_order(account, order, fee_rate)
    qty = fill.qty
    price = fill.price
    fee = fee_of(price, qty, fee_rate)
    holding = holding_of(account, order.market)
    if order.side is OrderSide.BUY:
        cost = buy_lock_amount(price, qty, fee_rate)
        if cost > available_krw(account):
            raise ValueError("살 가상 원화가 모자란다")
        if holding is None or holding.qty == 0:
            amount_sum, qty_sum, locked = _ZERO, _ZERO, _ZERO
            held = _ZERO
        else:
            amount_sum, qty_sum = holding.buy_amount_sum, holding.buy_qty_sum
            locked, held = holding.locked_qty, holding.qty
        new = make_holding(
            order.market, held + qty, locked, amount_sum + price * qty, qty_sum + qty
        )
        account = replace(account, krw=account.krw - cost)
        return Settlement(_put(account, new), new, fee, None)
    # 걸린 뒤의 체결은 위에서 묶음을 풀었으므로, 어느 때든 팔 수 있는 수량 안이어야 한다.
    if holding is None or qty > sellable_qty(holding):
        raise ValueError("팔 수량이 팔 수 있는 수량보다 많다")
    if holding.avg_price is None:
        raise ValueError("평균 매수가가 없는 보유다")
    pnl = qty * (price * (1 - fee_rate) - holding.avg_price * (1 + fee_rate))
    left = holding.qty - qty
    if left == 0:
        new = make_holding(order.market, _ZERO, _ZERO, _ZERO, _ZERO)
    else:
        new = make_holding(
            order.market, left, holding.locked_qty, holding.buy_amount_sum, holding.buy_qty_sum
        )
    account = replace(account, krw=account.krw + price * qty * (1 - fee_rate))
    return Settlement(_put(account, new), new, fee, pnl)


def _limit_price(order: OrderRecord) -> Decimal:
    if order.kind is not OrderKind.LIMIT or order.price is None:
        raise ValueError("묶음은 지정가 주문만 있다")
    if order.price <= 0 or order.qty <= 0:
        raise ValueError("가격과 수량은 0보다 커야 한다")
    return order.price


def _put(account: PaperAccount, holding: PaperHolding) -> PaperAccount:
    """그 코인의 보유를 바꾼다. 줄이 없으면 끝에 더한다."""
    holdings = list(account.holdings)
    for index, current in enumerate(holdings):
        if current.market == holding.market:
            holdings[index] = holding
            break
    else:
        holdings.append(holding)
    return replace(account, holdings=tuple(holdings))
