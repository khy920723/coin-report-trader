"""M3 3단계 수용 시험(명세 6절의 D1~D38)이 함께 쓰는 도구.

2단계의 틀(`m2_support.Rig`: 자료 스레드, 보내기 스레드, 가짜 실행기, 가짜 시세, 가짜 디스코드, 가짜
보관소, 가짜 시계)에 3단계의 값을 더한다. 거래소, 진짜 `claude`, 네트워크를 쓰지 않는다. 숫자는
모두 만든 값이다.

명세 6절의 첫 글: 처음 설정은 3단계로 마쳐 있고, 봉 단위는 60분봉, 코인은 `KRW-AAA`(와 `KRW-BBB`),
호가 단위 1, 가상 자금 1,000,000원, 예산 200,000원, 투자 비율 `KRW-AAA` 50%·`KRW-BBB` 50%(코인
예산 100,000원씩. 코인이 하나이면 예산 100,000원에 100%), 첫 매수 비율 20%, 평균회귀 첫 매수 비율
20%, 추가 비율 10%, 허용 범위 0%, 손절 5%, 하루 손실 한도 3,000원, 주문 간격 60초.
현재가는 1,000이다.

이 파일은 시험을 담지 않는다(`test_`로 시작하지 않아 모으지 않는다).
"""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from acceptance.m2_support import AAA, BBB, HOUR, THIS_HOUR, Rig, make_rig
from fakes.ai import answered, good_answers

from coin_report_trader.core import (
    CoinTradeView,
    FillRecord,
    Mode,
    OrderKind,
    OrderPlan,
    OrderReason,
    OrderSide,
    OrderState,
    PaperAccount,
    PaperHolding,
    PauseStopLoss,
    TradeMethod,
    TradeSettings,
    TradeStatusView,
)
from coin_report_trader.storage import trading as trade_store
from coin_report_trader.storage.database import open_database
from coin_report_trader.trading.paper import apply_fill, lock_order
from coin_report_trader.trading.paper.fills import Fill

__all__ = [
    "AAA",
    "BBB",
    "D_PRICE",
    "HOUR",
    "PARTS",
    "SEED_AT",
    "THIS_HOUR",
    "Rig",
    "account",
    "advance",
    "all_codes",
    "answer",
    "apply_all",
    "blank_rig",
    "choose_first_setup",
    "coin_view",
    "d_rig",
    "d_trade",
    "fill_seed",
    "fills",
    "holding_seed",
    "open_window",
    "order_seed",
    "orders",
    "pauses",
    "seeds",
    "status",
    "trade_fill_seed",
    "visible_texts",
]

D_PRICE = Decimal(1000)
"""D 시나리오의 현재가(1,000. 명세 6절)."""
PARTS = [("검색 판별", 1), ("봉 판별", 1), ("최종 예측", 1)]
"""둘째 회차부터 가짜 실행기의 답에 넣는, 지난 판단의 근거 줄마다의 (부분 이름, 번호)."""
SEED_AT = datetime(2026, 1, 5, 2, tzinfo=UTC)
"""심는 주문과 체결의 시각(가짜 시계의 처음 03:00 UTC보다 앞이고 한국 09:00 이후인 그날)."""


def d_trade(
    coins: tuple[str, ...] = (AAA,),
    *,
    method: TradeMethod = TradeMethod.TREND,
    **changes: Any,
) -> TradeSettings:
    """명세 6절의 3단계 값. 코인이 하나이면 예산 100,000원에 100%, 둘이면 200,000원에 50%씩."""
    count = len(coins)
    ratios = dict.fromkeys(coins, Decimal(100) / count) if count else {}
    trade = TradeSettings(
        budget=Decimal(100_000) * max(count, 1),
        coin_ratios=ratios,
        first_ratio=Decimal(20),
        mr_first_ratio=Decimal(20),
        add_ratio=Decimal(10),
        tolerance=Decimal(0),
        stop_ratio=Decimal(5),
        daily_loss_limit=Decimal(3000),
        order_interval_s=60,
        method=method,
        pause_stop_loss=PauseStopLoss.KEEP,
        mr_sell_unknown_fee=False,
        mr_tolerable_loss=Decimal(5),
        paper_funds=Decimal(1_000_000),
        backup_days=7,
    )
    return replace(trade, **changes)


def d_rig(
    tmp_path: Path,
    *,
    coins: tuple[str, ...] = (AAA,),
    method: TradeMethod = TradeMethod.TREND,
    seed: Callable[[Any], None] | None = None,
    min_order: Decimal | None = None,
    trade: TradeSettings | None = None,
    **kwargs: Any,
) -> Rig:
    """3단계로 마친 DB와 틀. 현재가는 1,000, 호가 단위 1이다(호가는 현재가와 같다)."""
    rig = make_rig(
        tmp_path,
        coins=coins,
        seed=seed,
        trade=trade if trade is not None else d_trade(coins, method=method),
        min_order=min_order,
        **kwargs,
    )
    rig.quote.set_prices(dict.fromkeys((AAA, BBB), D_PRICE))
    return rig


def answer(rig: Rig, market: str = AAA, **kwargs: Any) -> None:
    """그 코인의 가짜 실행기의 답(3.8의 정상 답. 인자는 `good_answers`의 것).

    예측 가격을 주지 않으면 방향에 맞게 현재가(1,000)보다 10 높거나 낮게 둔다(답의 검증을 지킨다).
    """
    if "predicted" not in kwargs:
        up = kwargs.get("direction", "오를 것") == "오를 것"
        kwargs["predicted"] = "1010" if up else "990"
    kwargs.setdefault("buy_price", "990")
    kwargs.setdefault("sell_price", "1010")
    rig.runner.plan(market, lambda req: answered(good_answers(market, **kwargs)))


# --- 읽기 --------------------------------------------------------------------------------------


def orders(rig: Rig) -> list[tuple[Any, ...]]:
    """주문 줄: (코인, 방향, 종류, 까닭, 상태, 가격, 수량). 오래된 것이 앞."""
    return rig.rows(
        "SELECT market, side, kind, reason, state, price, qty FROM orders ORDER BY id"
    )


def advance(rig: Rig, seconds: float) -> None:
    """시계를 옮기고 두 스레드가 쉴 때까지 기다린다."""
    rig.clock.advance(seconds)
    rig.settle()


def all_codes(rig: Rig) -> list[str]:
    """알림 번호(오래된 것이 앞). `Rig.codes()`와 달리 매매 알림(A-27, A-28)도 센다."""
    return [alert.code for alert in rig.alerts()]


def fills(rig: Rig) -> list[tuple[Any, ...]]:
    return rig.rows(
        "SELECT market, side, price, qty, fee, realized_pnl, filled_utc FROM fills ORDER BY id"
    )


def pauses(rig: Rig) -> list[tuple[Any, ...]]:
    return rig.rows("SELECT kind, cause, sell_all, released_utc IS NULL FROM pauses ORDER BY id")


def account(rig: Rig) -> PaperAccount:
    def read(conn: Any) -> PaperAccount | None:
        return trade_store.read_paper_account(conn, Mode.PAPER)

    found = rig._read(read)
    assert found is not None
    return found


def status(rig: Rig) -> TradeStatusView:
    return rig.sink.of(TradeStatusView)[-1]


def coin_view(rig: Rig, market: str = AAA) -> CoinTradeView:
    return [view for view in rig.sink.of(CoinTradeView) if view.market == market][-1]


# --- 심는 것(DB에 바로 쓴다) ---------------------------------------------------------------------


def holding_seed(
    market: str, qty: str, avg: str, *, amount: str | None = None
) -> Callable[[Any], None]:
    """가상 보유 하나를 심는다. `amount`가 없으면 매수 합 = 평균 매수가 × 수량이다."""

    def seed(conn: Any) -> None:
        found = trade_store.read_paper_account(conn, Mode.PAPER)
        assert found is not None
        total = Decimal(amount) if amount is not None else Decimal(avg) * Decimal(qty)
        holding = PaperHolding(
            market=market,
            qty=Decimal(qty),
            locked_qty=Decimal(0),
            avg_price=Decimal(avg),
            buy_amount_sum=total,
            buy_qty_sum=Decimal(qty),
        )
        trade_store.write_paper_account(
            conn, Mode.PAPER, replace(found, holdings=(*found.holdings, holding))
        )

    return seed


def order_seed(
    market: str,
    side: OrderSide,
    kind: OrderKind,
    price: Decimal | None,
    qty: str,
    *,
    at: datetime = SEED_AT,
    reason: OrderReason = OrderReason.JUDGMENT,
    uid: str | None = None,
) -> Callable[[Any], None]:
    """걸린 주문 하나를 심는다(`OPEN`, 보냄. 지정가는 묶음도 쓴다)."""

    def seed(conn: Any) -> None:
        amount = (price or Decimal(0)) * Decimal(qty)
        plan = OrderPlan(market, side, kind, reason, price, Decimal(qty), amount)
        order = trade_store.insert_order(
            conn,
            plan,
            mode=Mode.PAPER,
            uid=uid or f"seed-{market}-{side.value}-{kind.value}-{qty}-{at.timestamp():.0f}",
            round_id=None,
            created_at=at,
            judged_until=at,
        )
        trade_store.update_order(conn, order.order_id, state=OrderState.OPEN, sent=True)
        if kind is OrderKind.LIMIT:
            found = trade_store.read_paper_account(conn, Mode.PAPER)
            assert found is not None
            trade_store.write_paper_account(conn, Mode.PAPER, lock_order(found, order))

    return seed


def fill_seed(
    market: str,
    pnl: str,
    *,
    at: datetime = SEED_AT,
    uid: str | None = None,
    reason: OrderReason = OrderReason.JUDGMENT,
) -> Callable[[Any], None]:
    """프로그램이 판 체결 하나를 심는다(실현 손익 `pnl`. 수량과 가격은 1. 까닭은 `reason`)."""

    def seed(conn: Any) -> None:
        plan = OrderPlan(
            market, OrderSide.SELL, OrderKind.MARKET, reason, None,
            Decimal(1), Decimal(1),
        )  # fmt: skip
        order = trade_store.insert_order(
            conn, plan, mode=Mode.PAPER, uid=uid or f"seed-fill-{pnl}-{at.timestamp():.0f}",
            round_id=None, created_at=at, judged_until=at,
        )  # fmt: skip
        found = trade_store.read_paper_account(conn, Mode.PAPER)
        assert found is not None
        fill = FillRecord(
            0, order.order_id, Mode.PAPER, market, OrderSide.SELL, Decimal(1), Decimal(1),
            Decimal(0), Decimal(pnl), at,
        )  # fmt: skip
        trade_store.record_fill(conn, fill, found)

    return seed


def trade_fill_seed(
    market: str, side: OrderSide, price: str, qty: str, *, at: datetime = SEED_AT
) -> Callable[[Any], None]:
    """시장가 주문과 그 체결 하나를 심고 가상 계정에 넣는다(판단의 주문. 수수료율은 모의의 값).

    D13처럼 판단의 주문 금액과 달라 앱이 만들기 어려운 체결을 DB에 바로 쓴다(명세 6절 끝).
    """

    def seed(conn: Any) -> None:
        found = trade_store.read_paper_account(conn, Mode.PAPER)
        assert found is not None
        plan = OrderPlan(
            market, side, OrderKind.MARKET, OrderReason.JUDGMENT, None, Decimal(qty),
            Decimal(price) * Decimal(qty),
        )  # fmt: skip
        order = trade_store.insert_order(
            conn, plan, mode=Mode.PAPER,
            uid=f"seed-{side.value}-{price}-{qty}-{at.timestamp():.0f}",
            round_id=None, created_at=at, judged_until=at,
        )  # fmt: skip
        fill = Fill(order.order_id, market, side, Decimal(price), Decimal(qty), at, True)
        settled = apply_fill(found, order, fill)
        record = FillRecord(
            0, order.order_id, Mode.PAPER, market, side, Decimal(price), Decimal(qty),
            settled.fee, settled.realized_pnl, at,
        )  # fmt: skip
        trade_store.record_fill(conn, record, settled.account)

    return seed


def seeds(*parts: Callable[[Any], None]) -> Callable[[Any], None]:
    def seed(conn: Any) -> None:
        for part in parts:
            part(conn)

    return seed


def apply_all(window: Any, rig: Rig) -> None:
    """자료 스레드가 지금까지 보낸 값을 차례대로 창에 넣는다(주 스레드에서 부른다)."""
    from PySide6.QtWidgets import QApplication

    for item in list(rig.sink.items):
        window.apply(item)
    QApplication.processEvents()


def open_window(rig: Rig) -> Any:
    """`rig`의 자료 스레드를 동작으로 하는 진짜 창을 띄우고 지금까지의 값을 넣는다."""
    from coin_report_trader.core import UiContext
    from coin_report_trader.ui import create_main_window

    window = create_main_window(UiContext("0.0.1", "X", rig.data))
    window.resize(1100, 800)
    window.show()
    apply_all(window, rig)
    return window


def open_db(rig: Rig) -> Any:
    return open_database(rig.db)


def after(when: datetime, **delta: float) -> datetime:
    return when + timedelta(**delta)


# --- 처음 설정과 설정 화면(D27, D28, D29, D34) ----------------------------------------------------


def blank_rig(root: Path, *, coins: tuple[str, ...] = (AAA, BBB)) -> Rig:
    """DB는 있고 설정은 없는 틀(처음 켠 모습). 현재가는 1,000, 호가 단위 1이다."""
    from acceptance.m2_support import make_rig

    from coin_report_trader.storage.database import migrate

    db = root / "data" / "app.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = open_database(db)
    try:
        migrate(conn)
    finally:
        conn.close()
    rig = make_rig(root, fresh=False, coins=coins)
    rig.quote.set_prices(dict.fromkeys((AAA, BBB), D_PRICE))
    return rig


def choose_first_setup(first: Any, *coins: str) -> None:
    """처음 설정의 3단계 앞 칸을 고른다: 코인, 보관 기간, 봉 단위, 모델과 노력 여덟 칸."""
    from PySide6.QtWidgets import QAbstractButton, QCheckBox, QComboBox, QRadioButton

    from coin_report_trader.core import AiSlot

    for market in coins:
        box = first.findChild(QCheckBox, f"firstSetupCoin_{market}")
        assert box is not None, market
        assert box.isEnabled(), market
        box.click()
    first.findChild(QRadioButton, "firstSetupRetention30").click()
    unit = first.findChild(QAbstractButton, "firstSetupUnit_minute60")
    assert unit is not None
    unit.click()
    for slot in AiSlot:
        for kind in ("Model", "Effort"):
            combo = first.findChild(QComboBox, f"firstSetupAi{kind}_{slot.value}")
            assert combo is not None
            combo.setCurrentIndex(next(i for i in range(combo.count()) if combo.itemText(i)))


def visible_texts(widget: Any) -> list[str]:
    """그 화면의 보이는 글(라벨)을 모두 모은다."""
    from PySide6.QtWidgets import QLabel

    return [
        label.text()
        for label in widget.findChildren(QLabel)
        if label.text() and not label.isHidden()
    ]
