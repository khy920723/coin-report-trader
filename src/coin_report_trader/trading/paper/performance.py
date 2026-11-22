"""모의 성적의 셈(M3 명세 3.16.1, REQ-LIVE-016).

- 계산만 한다. 보고서, 체결, 계정, 현재가는 `app`이 읽어 넘긴다(25번). 판단마다의 결과
  (`LastResult`. 실제 값, 차이, 맞았는지)는 `app`이 `compute_last_result`로 셈해 넘긴다.
- 범위는 모의매매(`Mode.PAPER`)의 기록이다. 다른 모드의 체결은 세지 않는다.
- 코인마다 한 줄(넘긴 `markets`의 차례)과 끝에 합계 한 줄(`market`이 `None`)이다. 합계 줄의 판단
  회차, 맞음, 셀 수 있는 수, 실현 손익, 이익 낸 매도, 매도, 평가 손익은 코인 줄의 합이고, 예측 차이
  평균 둘은 고른 코인 모두의 셀 수 있는 판단을 모아 셈한다. 자산 변화는 합계 줄만이고 가상 계정
  전체(뺀 코인의 보유 포함)로 셈한다.
- 비율의 값은 비율 그대로다(0.005는 0.50%). 백분율의 글과 ▲▼는 화면이 만든다(3.17).
"""

from collections.abc import Iterable, Mapping, Sequence
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from coin_report_trader.core.ai import Verdict
from coin_report_trader.core.alerts import Mode
from coin_report_trader.core.trade import (
    PAPER_FEE_RATE,
    FillRecord,
    OrderSide,
    PaperAccount,
    PaperHolding,
)
from coin_report_trader.core.views import LastResult, PerformanceRow

DIFF_STEP = Decimal("0.0001")
"""예측 차이 평균 둘의 단위(`ROUND_HALF_EVEN`. M2 명세 3.9)."""

_ZERO = Decimal(0)


def unrealized_pnl(
    holding: PaperHolding | None, price: Decimal | None, fee_rate: Decimal = PAPER_FEE_RATE
) -> Decimal | None:
    """평가 손익: 보유 수량 × (현재가 × (1 − f) − 평균 매수가 × (1 + f)).

    현재가를 읽지 못했으면 `None`("-")이다. 보유가 없거나 0이면 0이다.
    """
    if price is None:
        return None
    if holding is None or holding.qty <= 0:
        return _ZERO
    if holding.avg_price is None:
        raise ValueError("평균 매수가가 없는 보유다")
    return holding.qty * (price * (1 - fee_rate) - holding.avg_price * (1 + fee_rate))


def asset_change(
    account: PaperAccount, prices: Mapping[str, Decimal | None], funds: Decimal
) -> Decimal | None:
    """자산 변화: (가상 원화 + 보유마다 수량 × 현재가) ÷ 가상 자금 − 1(비율).

    수량이 0보다 큰 보유 가운데 현재가를 모르는 것이 있으면 `None`("-")이다.
    """
    if funds <= 0:
        raise ValueError("가상 자금은 0보다 커야 한다")
    total = account.krw
    for holding in account.holdings:
        if holding.qty <= 0:
            continue
        price = prices.get(holding.market)
        if price is None:
            return None
        total += holding.qty * price
    return total / funds - 1


def mean_diffs(results: Iterable[LastResult]) -> tuple[Decimal | None, Decimal | None]:
    """셀 수 있는 판단(실제 값이 있는 것)마다의 차이의 평균과 그 절댓값의 평균.

    0.0001 단위로 맞춘다(`ROUND_HALF_EVEN`). 셀 수 있는 판단이 없으면 둘 다 `None`이다.
    """
    diffs = [result.diff for result in results if _countable(result) and result.diff is not None]
    if not diffs:
        return None, None
    count = Decimal(len(diffs))
    with localcontext() as context:
        context.prec = 28
        mean = (sum(diffs, _ZERO) / count).quantize(DIFF_STEP, rounding=ROUND_HALF_EVEN)
        abs_mean = (sum((abs(diff) for diff in diffs), _ZERO) / count).quantize(
            DIFF_STEP, rounding=ROUND_HALF_EVEN
        )
    return mean, abs_mean


def compute_performance(
    markets: Sequence[str],
    *,
    judgments: Mapping[str, Sequence[LastResult]],
    fills: Iterable[FillRecord],
    account: PaperAccount,
    prices: Mapping[str, Decimal | None],
    funds: Decimal,
    fee_rate: Decimal = PAPER_FEE_RATE,
) -> tuple[PerformanceRow, ...]:
    """모의 성적의 줄(3.16.1).

    - `judgments`: 코인마다 그 코인의 `OK` 보고서마다 하나의 결과(실제 값이 아직 없으면 `hit`이
      `None`). 판단 회차는 그 수, 셀 수 있는 수는 `hit`이 맞음이나 틀림인 수다.
    - `fills`: 체결 기록. 모의매매의 매도 체결만 실현 손익과 이익 낸 매도에 든다.
    - `prices`: 코인마다 현재가(읽지 못했으면 `None`이나 없음).
    - `funds`: 처음 가상 자금(`paper_accounts.funds`).
    """
    sells: dict[str, list[Decimal]] = {}
    for fill in fills:
        if fill.mode is not Mode.PAPER or fill.side is not OrderSide.SELL:
            continue
        sells.setdefault(fill.market, []).append(fill.realized_pnl or _ZERO)
    holdings = {holding.market: holding for holding in account.holdings}
    rows = []
    pooled: list[LastResult] = []
    for market in markets:
        results = tuple(judgments.get(market, ()))
        pooled.extend(results)
        pnls = sells.get(market, [])
        diff_mean, diff_abs_mean = mean_diffs(results)
        rows.append(
            PerformanceRow(
                market=market,
                ok_rounds=len(results),
                hits=sum(1 for result in results if result.hit is Verdict.HIT),
                countable=sum(1 for result in results if _countable(result)),
                realized_pnl=sum(pnls, _ZERO),
                wins=sum(1 for pnl in pnls if pnl > 0),
                sells=len(pnls),
                unrealized_pnl=unrealized_pnl(holdings.get(market), prices.get(market), fee_rate),
                asset_change=None,
                diff_mean=diff_mean,
                diff_abs_mean=diff_abs_mean,
            )
        )
    unrealized = [row.unrealized_pnl for row in rows]
    diff_mean, diff_abs_mean = mean_diffs(pooled)
    rows.append(
        PerformanceRow(
            market=None,
            ok_rounds=sum(row.ok_rounds for row in rows),
            hits=sum(row.hits for row in rows),
            countable=sum(row.countable for row in rows),
            realized_pnl=sum((row.realized_pnl for row in rows), _ZERO),
            wins=sum(row.wins for row in rows),
            sells=sum(row.sells for row in rows),
            unrealized_pnl=None
            if any(value is None for value in unrealized)
            else sum((value for value in unrealized if value is not None), _ZERO),
            asset_change=asset_change(account, prices, funds),
            diff_mean=diff_mean,
            diff_abs_mean=diff_abs_mean,
        )
    )
    return tuple(rows)


def _countable(result: LastResult) -> bool:
    """셀 수 있는 판단: 실제 값이 있어 맞음이나 틀림으로 가려진 것(M2 명세 3.9)."""
    return result.hit in (Verdict.HIT, Verdict.MISS)
