"""손절과 멈춤(M3 명세 3.9, 3.10): 손절 판정, 멈춤 상태, '멈추고 전량 매도'의 결과 글.

계산만 하고 DB를 열지 않고 시세를 부르지 않는다(3.2). `httpx`를 쓰지 않는다(3.3). 취소, 조회,
매도를 차례로 부르는 것은 `app`(23번, 25번)이다.

- `stop_loss`: 손절선, 손절 판정, 손절 매도의 계획(3.9).
- `pause`: 멈춤 상태와 바꾸는 함수, 허락을 묻는 함수(3.10).
- `sell_all`: 전량 매도의 결과와 그 글(3.10.3).

`__all__`에는 이 폴더에서 정의한 이름만 넣는다(`core/trade.py`의 타입을 다시 내보내지 않는다.
M3 명세 4절 3번).
"""

from coin_report_trader.trading.risk.pause import (
    PauseState,
    enter_error,
    may_fetch_quotes,
    may_run_round,
    may_sell_all,
    may_stop_check,
    pause_user,
    release_error,
    release_user,
    stop_loss_off,
)
from coin_report_trader.trading.risk.sell_all import SellAllResult, sell_all_text
from coin_report_trader.trading.risk.stop_loss import (
    StopCheck,
    StopDecision,
    stop_line,
    stop_loss_check,
    stop_loss_plan,
)

__all__ = [
    "PauseState",
    "SellAllResult",
    "StopCheck",
    "StopDecision",
    "enter_error",
    "may_fetch_quotes",
    "may_run_round",
    "may_sell_all",
    "may_stop_check",
    "pause_user",
    "release_error",
    "release_user",
    "sell_all_text",
    "stop_line",
    "stop_loss_check",
    "stop_loss_off",
    "stop_loss_plan",
]
