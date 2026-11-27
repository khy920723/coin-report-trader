"""3단계 공용 타입의 나머지(M3 명세 4절 1번): `DISCARDED`, 글, `UiActions`의 새 동작, `Services`."""

import dataclasses
import inspect
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from coin_report_trader.app import alert_rules
from coin_report_trader.app.actions import AppActions
from coin_report_trader.app.data_thread import DataWorker
from coin_report_trader.app.services import Services
from coin_report_trader.core import (
    BookSource,
    HistoryTab,
    PauseStopLoss,
    ReportState,
    SystemClock,
    TradeMethod,
    TradeSettings,
    UiActions,
    texts,
)

NEW_ACTIONS = (
    "pause",
    "pause_and_sell_all",
    "resume_user",
    "resume_error",
    "load_history",
    "export_history",
)


def test_the_report_state_gets_discarded_last() -> None:
    assert [(s.name, s.value) for s in ReportState][-1] == ("DISCARDED", "discarded")
    assert ReportState("discarded") is ReportState.DISCARDED


def test_the_eight_stage_names_follow_the_spec_and_end_the_stage_list_in_order() -> None:
    names = (
        texts.STAGE_ACCOUNT,
        texts.STAGE_ORDER,
        texts.STAGE_ORDER_SAVE,
        texts.STAGE_STOP_LOSS,
        texts.STAGE_SELL_ALL,
        texts.STAGE_RECONCILE,
        texts.STAGE_BACKUP,
        texts.STAGE_PAUSE_SAVE,
    )
    assert names == (
        "계정 읽기", "주문", "주문 저장", "손절", "긴급 매도", "거래소와 맞춰 보기", "DB 백업",
        "멈춤 저장",
    )  # fmt: skip
    assert texts.STAGES[-8:] == names
    assert len(set(texts.STAGES)) == len(texts.STAGES)


def test_the_fifteen_alert_codes_follow_the_spec() -> None:
    assert [
        texts.CODE_TOO_SMALL,
        texts.CODE_PRICE_GUARD,
        texts.CODE_STOP_FAILED,
        texts.CODE_NO_AVG,
        texts.CODE_BUDGET,
        texts.CODE_NO_KRW,
        texts.CODE_DAILY_LOSS,
        texts.CODE_CAUTION,
        texts.CODE_MIN_ORDER_CHANGED,
        texts.CODE_COIN_REMOVED,
        texts.CODE_ERROR_PAUSE,
        texts.CODE_UNKNOWN_EXCHANGE,
        texts.CODE_SELL_ALL_LEFT,
        texts.CODE_ORDER_PLACED,
        texts.CODE_FILLED,
    ] == [
        "A-3", "A-4", "A-8", "A-9", "A-10", "A-11", "A-12", "A-13", "A-15", "A-16", "A-23",
        "A-24", "A-25", "A-27", "A-28",
    ]  # fmt: skip


def test_the_three_new_after_texts_follow_the_spec_and_every_stage_with_an_a29_has_one() -> None:
    assert texts.AFTER_SAVE_FAILED["주문 저장"] == (
        "쓰지 못한 주문은 내지 않았고, 쓰지 못한 체결은 반영하지 않았습니다."
    )
    assert texts.AFTER_SAVE_FAILED["멈춤 저장"] == (
        "이번 실행에서는 멈춤 상태를 메모리에 지켜 새 주문을 내지 않습니다."
        " 다시 켜면 저장된 마지막 상태로 시작합니다."
    )
    assert texts.AFTER_SAVE_FAILED["DB 백업"] == (
        "백업을 만들거나 오래된 백업을 지우지 못했습니다. 다음 하루가 바뀌는 때에 다시 합니다."
    )
    assert set(texts.AFTER_SAVE_FAILED) <= set(texts.STAGES)
    for stage in ("주문 저장", "멈춤 저장", "DB 백업"):  # A-29를 만들어도 `KeyError`가 나지 않는다
        spec = alert_rules.save_failed(stage, None, "글")
        assert spec.after == texts.AFTER_SAVE_FAILED[stage]
        assert spec.stage == stage


def test_save_settings_of_the_port_gets_trade_and_accept_below_stop_last_with_defaults() -> None:
    parameters = inspect.signature(UiActions.save_settings).parameters
    assert list(parameters)[-3:-1] == ["trade", "accept_below_stop"]
    for name in ("trade", "accept_below_stop"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["trade"].default is None
    assert parameters["accept_below_stop"].default == ()
    assert list(inspect.signature(AppActions.save_settings).parameters) == list(parameters)
    assert list(inspect.signature(DataWorker.save_settings).parameters) == list(parameters)


def test_the_new_actions_of_the_port_have_the_spec_arguments() -> None:
    for name in ("pause", "pause_and_sell_all", "resume_user", "resume_error"):
        assert list(inspect.signature(getattr(UiActions, name)).parameters) == ["self"]
    assert list(inspect.signature(UiActions.load_history).parameters) == [
        "self", "tab", "market", "day", "mode",
    ]  # fmt: skip
    assert list(inspect.signature(UiActions.export_history).parameters) == [
        "self", "tab", "market", "path",
    ]  # fmt: skip
    for name in NEW_ACTIONS:
        assert name in dir(UiActions)
        assert (
            list(inspect.signature(getattr(AppActions, name)).parameters)[1:]
            == list(inspect.signature(getattr(UiActions, name)).parameters)[1:]
        )


class Spy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        def record(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))

        return record


def test_the_app_actions_hand_every_new_call_to_the_data_thread() -> None:
    data = Spy()
    actions = AppActions(data, Spy())  # type: ignore[arg-type]
    day = date(2026, 1, 5)
    actions.pause()
    actions.pause_and_sell_all()
    actions.resume_user()
    actions.resume_error()
    actions.load_history(HistoryTab.ORDERS, "KRW-AAA", day)
    actions.export_history(HistoryTab.FILLS, None, "경로")
    trade = TradeSettings(
        budget=Decimal("1000000"),
        coin_ratios={"KRW-AAA": Decimal("20")},
        first_ratio=Decimal("30"),
        mr_first_ratio=None,
        add_ratio=Decimal("10"),
        tolerance=Decimal("1"),
        stop_ratio=Decimal("5"),
        daily_loss_limit=Decimal("3000"),
        order_interval_s=600,
        method=TradeMethod.TREND,
        pause_stop_loss=PauseStopLoss.KEEP,
        mr_sell_unknown_fee=False,
        mr_tolerable_loss=Decimal("5"),
        paper_funds=Decimal("1000000"),
        backup_days=7,
    )
    actions.save_settings(
        ("KRW-AAA",), 7, first_setup=False, trade=trade, accept_below_stop=("KRW-AAA",)
    )
    assert data.calls[:4] == [(name, (), {}) for name in NEW_ACTIONS[:4]]
    assert data.calls[4] == ("load_history", (HistoryTab.ORDERS, "KRW-AAA", day, None), {})
    assert data.calls[5] == ("export_history", (HistoryTab.FILLS, None, "경로"), {})
    name, args, kwargs = data.calls[6]
    assert (name, args) == ("save_settings", (("KRW-AAA",), 7))
    assert kwargs["trade"] is trade
    assert kwargs["accept_below_stop"] == ("KRW-AAA",)


def test_the_data_thread_queues_the_pause_and_history_calls_and_not_the_settings_ones(
    tmp_path: Path,
) -> None:
    worker = DataWorker(
        database_file=tmp_path / "app.db",
        logs_dir=tmp_path,
        clock=SystemClock(),
        quote=None,
        sink=lambda update: None,
    )
    worker.pause()
    worker.pause_and_sell_all()
    worker.resume_user()
    worker.resume_error()
    worker.load_history(HistoryTab.ROUNDS, None, date(2026, 1, 5))
    worker.export_history(HistoryTab.PAUSES, "KRW-AAA", "경로")
    assert len(worker._queue) == 6  # 25번이 여섯 동작을 모두 자료 스레드의 큐에 넣는다
    worker._queue.clear()
    worker.save_settings(
        ("KRW-AAA",), 7, first_setup=False, trade=None, accept_below_stop=("KRW-AAA",)
    )
    (saved,) = list(worker._queue)  # 설정 저장 하나뿐이다. 23번부터 `trade`와 확인한 코인을 담는다
    assert saved.trade is None
    assert saved.accept_below_stop == ("KRW-AAA",)


def test_the_services_get_book_and_min_order_last_and_none_by_default() -> None:
    assert [f.name for f in dataclasses.fields(Services)][-3:-1] == ["book", "min_order"]
    services = Services()
    assert (services.book, services.min_order) == (None, None)
    assert Services(min_order=Decimal("5000")).min_order == Decimal("5000")


class _Book:
    def get_best_prices(self, market: str) -> tuple[Decimal, Decimal]:
        return Decimal(2), Decimal(1)

    def get_tick_size(self, market: str) -> Decimal:
        return Decimal(1)

    def get_minute_candles(self, market: str, *, to: Any, count: int) -> tuple[Any, ...]:
        return ()


def test_a_book_source_can_be_put_in_the_services() -> None:
    book = _Book()
    assert isinstance(book, BookSource)
    assert Services(book=book).book is book  # type: ignore[arg-type]


@pytest.mark.parametrize("name", NEW_ACTIONS)
def test_the_new_actions_are_in_the_port_listing(name: str) -> None:
    assert callable(getattr(UiActions, name))
