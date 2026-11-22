"""'멈추고 전량 매도'의 결과 글(M3 명세 3.10.3, 설계서 2.3).

팔기의 차례는 `app`이 `stop_loss_plan`(`reason=SELL_ALL`)으로 한다. 여기는 코인마다의 결과와 그
글(`CoinTradeView.sell_all`)만 둔다.
"""

from decimal import Decimal
from enum import StrEnum


class SellAllResult(StrEnum):
    DONE = "done"
    """끝남(보유가 없던 코인도 이것이다)."""
    RETRYING = "retrying"
    """다시 시도하는 중."""
    LEFT = "left"
    """팔지 못한 수량이 남음."""
    STOPPED = "stopped"
    """오류 멈춤으로 그만둠."""


TEXT_DONE = "전량 매도: 끝남"
TEXT_RETRYING = "전량 매도: 다시 시도하는 중"
TEXT_STOPPED = "전량 매도: 오류 멈춤으로 그만둠. 오류 멈춤을 푼 뒤 다시 누르세요"


def sell_all_text(result: SellAllResult, left_qty: Decimal | None = None) -> str:
    """코인마다의 결과 글. `LEFT`는 팔지 못한 수량 `left_qty`를 담는다("{수량} 팔지 못함").

    수량은 지수 꼴 없이 쓰고 끝의 0을 뗀다.
    """
    if result is SellAllResult.LEFT:
        if left_qty is None or left_qty <= 0:
            raise ValueError("팔지 못한 수량이 있어야 한다")
        return f"전량 매도: {_qty_text(left_qty)} 팔지 못함"
    if left_qty is not None:
        raise ValueError("팔지 못한 수량은 LEFT에만 담는다")
    if result is SellAllResult.DONE:
        return TEXT_DONE
    if result is SellAllResult.RETRYING:
        return TEXT_RETRYING
    return TEXT_STOPPED


def _qty_text(qty: Decimal) -> str:
    text = format(qty, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
