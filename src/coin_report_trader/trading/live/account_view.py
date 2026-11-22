"""계정 화면의 읽기와 셈(M5 명세 3.15.2. SCR-08).

계산만 한다. 읽기(`UpbitClient.accounts()`, 원화 마켓 코인 목록, `QuoteClient.read_prices`)와 읽는
때는 `app`이 한다(3.15.4. 15번). 매매 엔진과 따로다: 이 값으로 외부 변동을 셈하지 않고, DB의 어느
표에도 쓰지 않는다. 계정 화면은 주문하지 않는다.

- 원화(`KRW`)는 요약의 원화 주문 가능·묶임이다. 원화가 아닌 통화마다 보유(주문 가능 + 묶임)가 0보다
  큰 것만 한 줄이다(고른 코인이라도 보유가 0이면 넣지 않는다).
- 매수 금액 = 보유 × 평균 매수가, 평가 금액 = 보유 × 현재가, 평가 손익 = 평가 금액 − 매수 금액,
  수익률 = 평가 손익 ÷ 매수 금액(비율. 0.1은 10%). 수수료는 따로 빼지 않는다.
- 비고(`note`)는 한 줄에 하나다. 앞의 것이 먼저다:
  1. 원화 마켓이 목록에 없는 통화: "원화 마켓 없음"(현재가를 묻지 않는다).
  2. 묶은 현재가 요청이 실패했거나 그 마켓의 값이 없음: "현재가를 읽지 못함".
  3. 평균 매수가가 없거나 0이거나 기준 화폐가 `KRW`가 아님: "평균 매수가 없음"(매수 금액과 손익을
     셈하지 않는다. 평가 금액은 셈할 수 있으면 셈한다).
- 요약: 평가 합 = 평가 금액이 있는 줄의 합. 총자산 = 원화 주문 가능 + 원화 묶임 + 평가 합. 총 매수
  금액, 총 평가 손익, 수익률은 매수 금액과 평가 금액이 둘 다 있는 줄로만 셈한다(수익률 = 그 줄들의
  손익의 합 ÷ 그 줄들의 매수 금액의 합). 셈할 줄이 없으면 수익률은 `None`이다.
- 줄의 차례(3.15.3): 평가 금액이 큰 것이 위, 평가하지 못한 줄은 아래, 같으면 코인 기호의 ABC 순서.
"""

from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Final

from coin_report_trader.core.market import MARKET_PREFIX
from coin_report_trader.core.views import AccountRow, AccountState, AccountView
from coin_report_trader.trading.upbit.client import UpbitBalance

KRW_CURRENCY: Final = "KRW"
NOTE_NO_KRW_MARKET: Final = "원화 마켓 없음"
NOTE_NO_PRICE: Final = "현재가를 읽지 못함"
NOTE_NO_AVG: Final = "평균 매수가 없음"

_ZERO = Decimal(0)


def account_price_markets(
    accounts: Iterable[UpbitBalance], krw_markets: Iterable[str]
) -> tuple[str, ...]:
    """현재가를 물을 마켓: 보유가 있는 원화가 아닌 통화 가운데 원화 마켓 목록에 있는 `KRW-{통화}`.

    요청 한 번에 묶어 묻는다(3.15.2의 읽는 것 3). 계좌의 차례대로이고 겹치지 않는다.
    """
    listed = set(krw_markets)
    markets: list[str] = []
    for balance in accounts:
        market = f"{MARKET_PREFIX}{balance.currency}"
        if (
            balance.currency != KRW_CURRENCY
            and balance.balance + balance.locked > 0
            and market in listed
            and market not in markets
        ):
            markets.append(market)
    return tuple(markets)


def build_account_view(
    accounts: Iterable[UpbitBalance],
    prices: Mapping[str, Decimal] | None,
    krw_markets: Iterable[str],
    picked: Iterable[str],
    read_utc: datetime,
) -> AccountView:
    """읽은 계좌와 현재가로 계정 화면의 값(`state` `OK`)을 만든다.

    - `prices`: 묶은 현재가 요청의 결과(마켓 → 현재가). 요청이 실패했으면 `None`이다.
    - `krw_markets`: 원화 마켓 코인 목록의 마켓 코드. `picked`: 이 앱이 고른 코인의 마켓 코드.
    """
    listed = set(krw_markets)
    chosen = set(picked)
    krw_available = _ZERO
    krw_locked = _ZERO
    rows: list[AccountRow] = []
    for balance in accounts:
        if balance.currency == KRW_CURRENCY:
            krw_available += balance.balance
            krw_locked += balance.locked
            continue
        qty = balance.balance + balance.locked
        if qty <= 0:
            continue
        rows.append(_row(balance, qty, prices, listed, chosen))
    valued = [row for row in rows if row.value is not None]
    both = [row for row in valued if row.buy_amount is not None and row.pnl is not None]
    holdings_value = sum((row.value for row in valued if row.value is not None), _ZERO)
    total_buy = sum((row.buy_amount for row in both if row.buy_amount is not None), _ZERO)
    total_pnl = sum((row.pnl for row in both if row.pnl is not None), _ZERO)
    return AccountView(
        state=AccountState.OK,
        krw_available=krw_available,
        krw_locked=krw_locked,
        holdings_value=holdings_value,
        total_assets=krw_available + krw_locked + holdings_value,
        total_buy=total_buy,
        total_pnl=total_pnl,
        total_pnl_rate=total_pnl / total_buy if total_buy > 0 else None,
        rows=tuple(sorted(rows, key=_order)),
        excluded_value=len(rows) - len(valued),
        excluded_pnl=len(rows) - len(both),
        read_utc=read_utc,
        error_name=None,
        reading=False,
    )


def _row(
    balance: UpbitBalance,
    qty: Decimal,
    prices: Mapping[str, Decimal] | None,
    listed: set[str],
    chosen: set[str],
) -> AccountRow:
    market = f"{MARKET_PREFIX}{balance.currency}"
    has_market = market in listed
    price = None
    note = None
    if not has_market:
        note = NOTE_NO_KRW_MARKET
    else:
        price = None if prices is None else prices.get(market)
        if price is None:
            note = NOTE_NO_PRICE
    avg = balance.avg_buy_price
    if avg is not None and (avg <= 0 or balance.unit_currency != KRW_CURRENCY):
        avg = None
    if avg is None and note is None:
        note = NOTE_NO_AVG
    value = None if price is None else qty * price
    buy_amount = None if avg is None else qty * avg
    pnl = None
    pnl_rate = None
    if value is not None and buy_amount is not None:
        pnl = value - buy_amount
        pnl_rate = pnl / buy_amount if buy_amount > 0 else None
    return AccountRow(
        currency=balance.currency,
        market=market if has_market else None,
        qty=qty,
        locked=balance.locked,
        avg_buy_price=avg,
        avg_modified=balance.avg_buy_price_modified,
        price=price,
        buy_amount=buy_amount,
        value=value,
        pnl=pnl,
        pnl_rate=pnl_rate,
        picked=market in chosen,
        note=note,
    )


def _order(row: AccountRow) -> tuple[int, Decimal, str]:
    """평가 금액이 큰 것이 위, 평가하지 못한 줄은 아래, 같으면 코인 기호의 ABC 순서."""
    if row.value is None:
        return (1, _ZERO, row.currency)
    return (0, -row.value, row.currency)
