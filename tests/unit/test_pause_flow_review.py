"""25번 검토의 고칠 것(M3 명세 3.10, 3.13, 3.15)을 시험으로 더한다. 값은 모두 만든 값이다."""

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fakes.clock import FakeClock
from fakes.quote import MINUTES
from fakes.trade_rig import (
    AAA,
    BBB,
    HOUR,
    PARTS,
    Rig,
    holding_seed,
    make_rig,
    order_seed,
    raise_in_minute_check,
    rig_builder,
    seeds,
)

from coin_report_trader.ai.csv_files import read_csv
from coin_report_trader.core import (
    ErrorPauseCause,
    HistoryTab,
    HistoryView,
    MinuteCandle,
    OrderKind,
    OrderSide,
    QuoteError,
    RoundNowView,
    TradeStatusView,
)
from coin_report_trader.core.texts import (
    ERROR_BLOCKED,
    ERROR_NETWORK,
    STARTING_RECONCILE,
    STARTING_STOP_CHECK,
)

BUY_99 = order_seed(AAA, OrderSide.BUY, OrderKind.LIMIT, Decimal(99), "10")
LOW = MinuteCandle(
    AAA, datetime(2026, 1, 5, 2, 10, tzinfo=UTC), Decimal(100), Decimal(98), Decimal(98)
)
DAY = date(2026, 1, 5)


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Callable[..., Rig]]:
    yield from rig_builder(tmp_path)


def stuck_order_rig(build: Callable[..., Rig]) -> Rig:
    """걸린 지정가 주문이 있고 1분봉을 받지 못하는 틀(대조가 끝나지 않는다)."""
    rig = build(seed=BUY_99)
    rig.quote.script(*[QuoteError(ERROR_NETWORK)] * 60, kind=MINUTES, market=AAA)
    rig.start()
    return rig


# --- 1, 2. 풀 때의 대조(3.10.1, 3.10.2) ---------------------------------------------------------


def test_resuming_the_user_pause_judges_the_open_order_before_the_stop_check(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = stuck_order_rig(build)
    rig.worker.pause()  # 판정하지 못해 취소가 확인되지 않은 주문은 걸린 채 남는다
    rig.settle()
    assert rig.query("SELECT state FROM orders") == [("open",)]
    rig.quote.clear_script()
    rig.quote.put_minutes(LOW)
    calls: list[int] = []
    monkeypatch.setattr(rig.worker, "_minute_check", lambda: calls.append(1))  # 대조만 남긴다
    rig.sink.items.clear()
    rig.worker.resume_user()
    rig.settle()
    assert rig.query("SELECT state FROM orders") == [("filled",)]  # 대조가 판정했다
    assert calls == [1]  # 그 뒤에 손절 점검
    steps = [s.starting_step for s in rig.sink.of(TradeStatusView)]
    assert steps[:2] == [STARTING_RECONCILE, STARTING_STOP_CHECK]


def test_a_reconcile_that_fails_keeps_the_error_pause_until_it_succeeds(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = stuck_order_rig(build)
    raise_in_minute_check(rig, monkeypatch)
    assert rig.status().error_paused is True
    monkeypatch.undo()
    rig.worker.resume_error()
    rig.settle()
    assert rig.status().error_paused is True  # 1분봉을 받지 못해 대조가 끝나지 않았다
    assert rig.pauses() == [("error", "unexpected", 0, 1)]
    assert rig.status().starting_step is None
    rig.round()
    assert rig.query("SELECT COUNT(*) FROM rounds") == [(0,)]  # 회차도 없다
    rig.quote.clear_script()
    rig.quote.put_minutes(LOW)
    rig.worker.resume_error()
    rig.settle()
    assert rig.status().error_paused is False
    assert rig.pauses() == [("error", "unexpected", 0, 0)]
    assert rig.query("SELECT state FROM orders") == [("filled",)]


# --- 3, 4. 전량 매도(3.10.3) --------------------------------------------------------------------


def test_sell_all_is_refused_during_an_error_pause(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build(seed=holding_seed(AAA, "100", "100"))
    rig.start()
    raise_in_minute_check(rig, monkeypatch)
    rig.worker.pause_and_sell_all()
    rig.settle()
    assert rig.orders() == []
    assert rig.status().user_paused is False
    assert rig.pauses() == [("error", "unexpected", 0, 1)]


def test_a_retrying_sell_all_stops_when_an_error_pause_begins(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build(seed=holding_seed(AAA, "100", "100"))
    rig.start()
    rig.quote.script(*[QuoteError(ERROR_NETWORK)] * 50, kind="book", market=AAA)
    rig.worker.pause_and_sell_all()
    rig.settle()
    raise_in_minute_check(rig, monkeypatch)
    assert rig.status().error_paused is True
    rig.quote.clear_script()
    rig.advance(180)  # 오류 멈춤이면 다시 팔지 않는다
    assert rig.orders() == []


def test_pressing_sell_all_while_already_paused_marks_the_pause_row(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=holding_seed(AAA, "100", "100"))
    rig.start()
    rig.worker.pause()
    rig.settle()
    assert rig.pauses() == [("user", None, 0, 1)]
    rig.worker.pause_and_sell_all()
    rig.settle()
    assert rig.pauses() == [("user", None, 1, 1)]
    assert rig.coin_view(AAA).sell_all == "전량 매도: 끝남"


def test_a_holding_below_the_minimum_order_shows_the_quantity_that_could_not_be_sold(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=holding_seed(AAA, "10", "100"))  # 평가 금액 1,000원 < 최소 주문 금액
    rig.start()
    rig.worker.pause_and_sell_all()
    rig.settle()
    assert rig.coin_view(AAA).sell_all == "전량 매도: 10 팔지 못함"
    assert rig.orders() == []
    (a25,) = [a for a in rig.alerts() if a.code == "A-25"]
    assert a25.detail is not None
    assert a25.detail.error_name == "최소 주문 금액보다 작음"


# --- 5. 상태 띠의 값(3.4, 3.10.2) ---------------------------------------------------------------


def test_the_status_fills_blocked_until_and_uncanceled_only_where_the_spec_says(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build(seed=BUY_99)
    rig.start()
    assert rig.status().uncanceled == ()
    assert rig.status().blocked_until is None
    raise_in_minute_check(rig, monkeypatch)
    status = rig.status()
    assert status.error_cause is ErrorPauseCause.UNEXPECTED
    assert status.blocked_until is None  # 원인이 일시 차단이 아니면 채우지 않는다
    assert status.uncanceled == ("AAA 지정가 매수 99원 × 10.0",)
    rig2 = build(seed=BUY_99)
    rig2.start()
    rig2.worker.pause()  # 사용자 멈춤은 주문을 취소한다
    rig2.settle()
    assert rig2.status().uncanceled == ()


def test_the_a23_text_names_the_time_the_block_ends_and_the_error_pause_needs_the_first_setup(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    rig.quote.script(QuoteError(ERROR_BLOCKED, 60.0), kind="candles", market=AAA, unit=HOUR)
    rig.start()
    (a23,) = [a for a in rig.alerts() if a.code == "A-23"]
    assert a23.detail is not None
    assert "12:01까지 공개 시세 요청을 내지 않습니다." in a23.detail.content
    # 처음 설정을 마치기 전에는 오류 멈춤이 없다(3.10.2)
    early = build(done=False)

    def broken() -> None:
        raise RuntimeError("뜻밖")

    monkeypatch.setattr(early.worker, "_load_directions", broken)
    early.start()
    assert "A-30" in early.codes()
    assert early.worker._pause.error is False
    assert early.pauses() == []
    (a30,) = [a for a in early.alerts() if a.code == "A-30"]
    assert a30.detail is not None
    assert a30.detail.after == "그 일을 그만두고 다음 일을 계속합니다."


# --- 7. 오류 멈춤 동안의 1분 점검과 `RoundNowView.paused`(3.10.2) --------------------------------


def test_an_error_pause_with_stop_loss_keep_does_not_check_the_stop_loss_and_the_round_is_paused(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build(seed=holding_seed(AAA, "100", "100"))
    rig.start()
    assert rig.sink.of(RoundNowView)[-1].paused is False
    raise_in_minute_check(rig, monkeypatch)
    assert rig.sink.of(RoundNowView)[-1].paused is True
    monkeypatch.undo()
    rig.quote.set_prices({AAA: Decimal(90)})  # 손절선 95 아래
    rig.advance(180)
    assert rig.orders() == []
    rig.worker.resume_error()
    rig.settle()
    assert [o[3] for o in rig.orders()] == ["stop_loss"]  # 풀 때의 손절 점검
    rig2 = build()
    rig2.start()
    rig2.worker.pause()
    rig2.settle()
    assert rig2.sink.of(RoundNowView)[-1].paused is True


def test_an_error_pause_kept_at_start_skips_the_reconcile_and_the_stop_check(
    build: Callable[..., Rig],
) -> None:
    def seed(conn: Any) -> None:
        from coin_report_trader.core import Mode, PauseKind
        from coin_report_trader.storage import trading as trade_store

        trade_store.insert_pause(
            conn,
            mode=Mode.PAPER,
            kind=PauseKind.ERROR,
            cause=ErrorPauseCause.SAVE,
            detail=None,
            sell_all=False,
            started_at=datetime(2026, 1, 5, 2, tzinfo=UTC),
        )

    rig = build(seed=seeds(holding_seed(AAA, "100", "100"), BUY_99, seed))
    rig.start()
    assert [s.starting_step for s in rig.sink.of(TradeStatusView)] == [None]
    assert rig.quote.price_requests(AAA) == []
    assert rig.quote.book_requests(MINUTES, AAA) == []
    assert rig.query("SELECT state FROM orders") == [("open",)]


# --- 8. CSV의 차례, 멈춤 탭의 글, 백업 지우기의 실패 -------------------------------------------


def test_every_export_writes_the_oldest_row_first(
    build: Callable[..., Rig], tmp_path: Path
) -> None:
    rig = build(
        coins=(AAA, BBB), seed=seeds(holding_seed(AAA, "100", "90"), holding_seed(BBB, "100", "90"))
    )
    rig.start()
    rig.answer(direction="오를 것", buy_price="99")
    rig.round()
    rig.answer(direction="오를 것", buy_price="98", result_text="지난 결과", compared=PARTS)
    rig.round()
    rig.worker.pause_and_sell_all()  # 두 코인의 체결이 차례로 쓰인다
    rig.settle()
    rig.worker.resume_user()
    rig.settle()
    rig.advance(60)
    rig.worker.pause()
    rig.settle()

    def rows(tab: HistoryTab) -> list[dict[str, str]]:
        path = tmp_path / f"{tab.value}.csv"
        rig.worker.export_history(tab, None, str(path))
        rig.settle()
        header, body = read_csv(path.read_text(encoding="utf-8").lstrip(chr(0xFEFF)))
        return [dict(zip(header, row, strict=True)) for row in body]

    rounds = rows(HistoryTab.ROUNDS)
    assert [r["number"] for r in rounds] == ["1", "2"]
    judgments = rows(HistoryTab.JUDGMENTS)
    assert [j["round_number"] for j in judgments][:1] == ["1"]
    orders = rows(HistoryTab.ORDERS)
    times = [o["created_utc"] for o in orders]
    assert len(times) >= 2
    assert times == sorted(times)
    fills = rows(HistoryTab.FILLS)
    assert [f["market"] for f in fills] == [AAA, BBB]
    pauses = rows(HistoryTab.PAUSES)
    assert len(pauses) == 2
    assert [p["started_utc"] for p in pauses] == sorted(p["started_utc"] for p in pauses)
    assert pauses[0]["released_utc"] != ""
    assert pauses[1]["released_utc"] == ""


def test_the_pauses_tab_names_the_error_kind(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    rig.quote.script(QuoteError(ERROR_BLOCKED, 60.0), kind="candles", market=AAA, unit=HOUR)
    rig.start()
    rig.sink.items.clear()
    rig.worker.load_history(HistoryTab.PAUSES, None, DAY)
    rig.settle()
    (row,) = rig.sink.of(HistoryView)[-1].rows
    assert (row.cells[1], row.cells[2]) == ("오류: 일시 차단", "멈춰 있음")
    rig2 = build()
    rig2.start()
    raise_in_minute_check(rig2, monkeypatch)
    rig2.sink.items.clear()
    rig2.worker.load_history(HistoryTab.PAUSES, None, DAY)
    rig2.settle()
    (row2,) = rig2.sink.of(HistoryView)[-1].rows
    assert row2.cells[1] == "오류: 예상하지 못한 오류"


def test_a_backup_that_only_fails_to_delete_is_an_a29(tmp_path: Path) -> None:
    clock = FakeClock(datetime(2026, 12, 5, 0, 10, tzinfo=UTC))
    root = tmp_path / "r"
    root.mkdir()
    folder = root / "backups"
    folder.mkdir()
    (folder / "app-2026-12-05.db").write_bytes(b"x")  # 그날의 파일이 이미 있어 만들 일은 없다
    (folder / "app-2026-12-01.db").mkdir()  # 지울 수 없는 오래된 이름(폴더)
    rig = make_rig(root, clock=clock, backup_days=3)
    try:
        rig.start()
        (a29,) = [a for a in rig.alerts() if a.code == "A-29"]
        assert a29.detail is not None
        assert a29.detail.stage == "DB 백업"
        assert rig.worker._pause.error is False
    finally:
        rig.worker.stop()
        rig.worker.join(10)


def test_a_block_during_another_error_pause_does_not_show_a_blocked_time(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    rig.start()
    raise_in_minute_check(rig, monkeypatch)
    monkeypatch.undo()
    rig.quote.script(QuoteError(ERROR_BLOCKED, 600.0), kind="candles", market=AAA, unit=HOUR)
    rig.advance(3600)  # 봉 받기는 계속하다 차단을 받는다
    assert rig.worker._blocked_until is not None
    status = rig.status()
    assert status.error_cause is ErrorPauseCause.UNEXPECTED
    assert status.blocked_until is None
    assert rig.codes().count("A-23") == 0  # 이미 오류 멈춤이라 새로 들어가지 않는다
