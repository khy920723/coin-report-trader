"""모의매매 엔진(M3 명세 3.7, 3.16.1): 가상 계정, 모의 체결, 수수료와 손익, 소급 판정, 모의 성적.

계산과 판정만 하고 DB를 열지 않고 시세를 부르지 않는다(3.2). 가상 계정과 주문의 값은 `app`이
`storage`에서 읽어 넘기고, 돌려받은 결과(체결, 바뀐 계정)를 `app`이 쓴다.

- `account`: 가상 계정의 셈(3.7.1), 묶음, 체결을 계정에 넣기, 수수료와 손익(3.7.3).
- `fills`: 넣을 때의 바로 체결과 시장가, 걸린 뒤의 판정 `judge_fills`(3.7.2), 판정의 구간(3.7.4).
- `performance`: 모의 성적의 셈(3.16.1).

`__all__`에는 이 폴더에서 정의한 이름만 넣는다(`core/trade.py`의 타입을 다시 내보내지 않는다.
M3 명세 4절 3번).
"""

from coin_report_trader.trading.paper.account import (
    Settlement,
    apply_fill,
    available_krw,
    buy_lock_amount,
    fee_of,
    holding_of,
    is_dust,
    lock_order,
    make_holding,
    new_account,
    sellable_qty,
    unlock_order,
)
from coin_report_trader.trading.paper.fills import (
    Fill,
    PendingOrder,
    ceil_minute,
    fill_on_place,
    first_judged_until,
    floor_minute,
    judge_fills,
    judge_window,
)
from coin_report_trader.trading.paper.performance import (
    DIFF_STEP,
    asset_change,
    compute_performance,
    mean_diffs,
    unrealized_pnl,
)

__all__ = [
    "DIFF_STEP",
    "Fill",
    "PendingOrder",
    "Settlement",
    "apply_fill",
    "asset_change",
    "available_krw",
    "buy_lock_amount",
    "ceil_minute",
    "compute_performance",
    "fee_of",
    "fill_on_place",
    "first_judged_until",
    "floor_minute",
    "holding_of",
    "is_dust",
    "judge_fills",
    "judge_window",
    "lock_order",
    "make_holding",
    "mean_diffs",
    "new_account",
    "sellable_qty",
    "unlock_order",
    "unrealized_pnl",
]
