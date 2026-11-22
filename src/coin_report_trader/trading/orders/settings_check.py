"""설정의 받지 않는 값(M3 명세 3.11.2, 제품 규칙 6절).

빈 칸과 꼴은 화면이 보고, 범위와 하한과 합은 `check_trade_settings`가 본다(자료 스레드가 저장
전에 부른다). 결과는 칸 이름(3.11.1의 설정 키)에서 글이고, 비면 통과다. 같은 칸에 글이 여럿이면
줄을 바꿔(`\\n`) 한 글로 잇는다.

- 투자 비율의 합은 `coin_ratios`, "첫 매수 + 추가 한 번 > 코인 예산"은 그 코인의
  `coin_ratio:{마켓 코드}`, 첫 매수 금액의 하한과 추가 한 번의 금액은 그 비율 칸에 단다.
- 첫 매수 비율의 검사는 고른 방식의 칸에 늘 걸고, 고르지 않은 방식의 칸에는 채웠을 때만 건다.
- 금액의 글: 하한과 최소 주문 금액의 1.1배는 원 아래를 올려, 첫 매수와 추가 한 번의 금액은 원
  아래를 버려 쉼표를 붙인다. {합}은 소수 둘째 자리까지 쉼표 없이 보인다.
- {코인}은 마켓 코드에서 앞의 `KRW-`를 뗀 기호다(`coin_label`).
"""

from collections.abc import Iterable, Mapping
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal

from coin_report_trader.core.market import coin_label
from coin_report_trader.core.trade import (
    BACKUP_DAYS_RANGE,
    FIRST_BUY_FLOOR_FACTOR,
    TOLERANCE_MAX,
    TradeMethod,
    TradeSettings,
)

TEXT_REQUIRED = "이 값을 넣어야 합니다."
TEXT_POSITIVE = "0보다 큰 값을 넣으세요."
TEXT_STOP_RATIO = "손절 비율은 0보다 크고 100%보다 작아야 합니다."
TEXT_TOLERABLE_LOSS = "0% 이상 100% 미만으로 넣으세요."
TEXT_TOLERANCE = "0%에서 2% 사이로 넣으세요."
TEXT_ORDER_INTERVAL = "1초에서 3,600초 사이로 넣으세요."
TEXT_BACKUP_DAYS = "1일에서 365일 사이로 넣으세요."

KEY_BUDGET = "budget"
KEY_COIN_RATIOS = "coin_ratios"
KEY_FIRST_RATIO = "first_ratio"
KEY_MR_FIRST_RATIO = "mr_first_ratio"
KEY_ADD_RATIO = "add_ratio"
KEY_TOLERANCE = "tolerance"
KEY_STOP_RATIO = "stop_ratio"
KEY_DAILY_LOSS_LIMIT = "daily_loss_limit"
KEY_ORDER_INTERVAL = "order_interval_s"
KEY_TOLERABLE_LOSS = "mr_tolerable_loss"
KEY_PAPER_FUNDS = "paper_funds"
KEY_BACKUP_DAYS = "backup_days"

ORDER_INTERVAL_RANGE = (1, 3600)
"""같은 코인 주문 간격의 받는 범위(초)."""

_HUNDRED = Decimal(100)


def coin_ratio_key(market: str) -> str:
    """투자 비율 칸의 이름(설정 키): `coin_ratio:{마켓 코드}`."""
    return f"coin_ratio:{market}"


def text_ratio_sum(total: Decimal) -> str:
    shown = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    return f"코인별 투자 비율의 합이 100%여야 합니다(지금 {shown:f}%)."


def text_first_floor(market: str, amount: Decimal, floor: Decimal) -> str:
    return (
        f"{coin_label(market)}: 첫 매수 금액({_won_down(amount)}원)이 "
        f"하한({_won_up(floor)}원)보다 작습니다."
    )


def text_add_floor(market: str, amount: Decimal, floor: Decimal) -> str:
    return (
        f"{coin_label(market)}: 추가 한 번의 금액({_won_down(amount)}원)이 "
        f"최소 주문 금액의 1.1배({_won_up(floor)}원)보다 작습니다."
    )


def text_over_coin_budget(market: str) -> str:
    return f"첫 매수와 추가 한 번을 합친 금액이 {coin_label(market)}의 예산보다 큽니다."


def first_buy_floor(min_order: Decimal, stop_ratio: Decimal) -> Decimal:
    """첫 매수 금액의 하한: 최소 주문 금액 ÷ (1 − 손절 비율) × 1.1(5.5절)."""
    return min_order / (1 - stop_ratio / _HUNDRED) * FIRST_BUY_FLOOR_FACTOR


def check_trade_settings(
    settings: TradeSettings, coins: Iterable[str], min_order: Decimal
) -> Mapping[str, str]:
    """받지 않는 값(3.11.2의 표의 셋째 줄부터). 칸 이름에서 글. 비면 통과다.

    `coins`는 고른 코인의 마켓 코드, `min_order`는 지금의 최소 주문 금액이다.
    """
    errors: dict[str, list[str]] = {}
    markets = tuple(coins)

    def add(key: str, text: str) -> None:
        errors.setdefault(key, []).append(text)

    first_keys = _first_ratio_keys(settings)
    # 0 이하
    if settings.budget <= 0:
        add(KEY_BUDGET, TEXT_POSITIVE)
    ratios: dict[str, Decimal] = {}
    for market in markets:
        ratio = settings.coin_ratios.get(market)
        if ratio is None:
            add(coin_ratio_key(market), TEXT_REQUIRED)
            continue
        ratios[market] = ratio
        if ratio <= 0:
            add(coin_ratio_key(market), TEXT_POSITIVE)
    for key, value in first_keys:
        if value is None:
            add(key, TEXT_REQUIRED)
        elif value <= 0:
            add(key, TEXT_POSITIVE)
    if settings.add_ratio <= 0:
        add(KEY_ADD_RATIO, TEXT_POSITIVE)
    if settings.daily_loss_limit <= 0:
        add(KEY_DAILY_LOSS_LIMIT, TEXT_POSITIVE)
    if settings.paper_funds <= 0:
        add(KEY_PAPER_FUNDS, TEXT_POSITIVE)
    # 투자 비율의 합
    total = sum(ratios.values(), Decimal(0))
    if markets and total != _HUNDRED:
        add(KEY_COIN_RATIOS, text_ratio_sum(total))
    # 범위
    stop_ok = 0 < settings.stop_ratio < _HUNDRED
    if not stop_ok:
        add(KEY_STOP_RATIO, TEXT_STOP_RATIO)
    if not 0 <= settings.mr_tolerable_loss < _HUNDRED:
        add(KEY_TOLERABLE_LOSS, TEXT_TOLERABLE_LOSS)
    if not 0 <= settings.tolerance <= TOLERANCE_MAX:
        add(KEY_TOLERANCE, TEXT_TOLERANCE)
    low, high = ORDER_INTERVAL_RANGE
    if not low <= settings.order_interval_s <= high:
        add(KEY_ORDER_INTERVAL, TEXT_ORDER_INTERVAL)
    low, high = BACKUP_DAYS_RANGE
    if not low <= settings.backup_days <= high:
        add(KEY_BACKUP_DAYS, TEXT_BACKUP_DAYS)
    # 금액의 하한과 코인 예산(쓸 수 있는 값일 때만 셈한다)
    if settings.budget > 0 and stop_ok:
        first_floor = first_buy_floor(min_order, settings.stop_ratio)
        add_floor = min_order * FIRST_BUY_FLOOR_FACTOR
        for market, ratio in ratios.items():
            if ratio <= 0:
                continue
            coin_budget = settings.budget * ratio / _HUNDRED
            for key, value in first_keys:
                if value is None or value <= 0:
                    continue
                amount = coin_budget * value / _HUNDRED
                if amount < first_floor:
                    add(key, text_first_floor(market, amount, first_floor))
            if settings.add_ratio > 0:
                amount = coin_budget * settings.add_ratio / _HUNDRED
                if amount < add_floor:
                    add(KEY_ADD_RATIO, text_add_floor(market, amount, add_floor))
                for _, value in first_keys:
                    if value is None or value <= 0:
                        continue
                    if coin_budget * (value + settings.add_ratio) / _HUNDRED > coin_budget:
                        add(coin_ratio_key(market), text_over_coin_budget(market))
                        break
    return {key: "\n".join(texts) for key, texts in errors.items()}


def _first_ratio_keys(settings: TradeSettings) -> tuple[tuple[str, Decimal | None], ...]:
    """검사할 첫 매수 비율 칸: 고른 방식의 칸은 늘, 고르지 않은 방식의 칸은 채웠을 때만."""
    chosen = KEY_FIRST_RATIO if settings.method is TradeMethod.TREND else KEY_MR_FIRST_RATIO
    keys: list[tuple[str, Decimal | None]] = []
    for key, value in (
        (KEY_FIRST_RATIO, settings.first_ratio),
        (KEY_MR_FIRST_RATIO, settings.mr_first_ratio),
    ):
        if key == chosen or value is not None:
            keys.append((key, value))
    return tuple(keys)


def _won_down(amount: Decimal) -> str:
    return f"{int(amount.to_integral_value(rounding=ROUND_FLOOR)):,}"


def _won_up(amount: Decimal) -> str:
    return f"{int(amount.to_integral_value(rounding=ROUND_CEILING)):,}"
