"""5단계 공용 타입과 거래소 경계(M5 명세 3.4, 3.15). 값은 모두 만든 값이다."""

import ast
import dataclasses
import inspect
import sys
import typing
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from coin_report_trader import core
from coin_report_trader.app.actions import AppActions
from coin_report_trader.app.data_thread import DataWorker
from coin_report_trader.app.services import Services
from coin_report_trader.core import (
    KEY_EXPIRY_WARN_DAYS,
    LIVE_CONFIRM_SECONDS,
    UPBIT_KEY_VALID_DAYS,
    AccountRow,
    AccountState,
    AccountView,
    ExchangeAccount,
    ExchangeError,
    ExchangeHolding,
    ExchangePort,
    ExchangeRules,
    ExchangeTrade,
    HistoryTab,
    LiveBlock,
    LiveConfirmView,
    LiveSettings,
    LiveStatusView,
    Mode,
    MultiPriceSource,
    OrderKind,
    OrderPlan,
    OrderReason,
    OrderSide,
    OrderSnapshot,
    OrderState,
    PaperStore,
    PauseStopLoss,
    PlaceResult,
    QuoteError,
    SettingsView,
    SystemClock,
    TradeMethod,
    TradeSettings,
    TradeStatusView,
    TransportError,
    TransportResponse,
    UiActions,
    UiUpdate,
    UpbitKeyView,
    UpbitTransport,
    live_block_budget_too_big,
    live_block_not_enough_rounds,
    texts,
)
from coin_report_trader.core import exchange as exchange_module
from coin_report_trader.trading.upbit import transport as upbit_transport

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
D = Decimal


def names(cls: type) -> list[str]:
    return [f.name for f in dataclasses.fields(cls)]


def trade() -> TradeSettings:
    return TradeSettings(
        budget=D("1000000"),
        coin_ratios={"KRW-AAA": D("100")},
        first_ratio=D("30"),
        mr_first_ratio=None,
        add_ratio=D("10"),
        tolerance=D("1"),
        stop_ratio=D("5"),
        daily_loss_limit=D("3000"),
        order_interval_s=600,
        method=TradeMethod.TREND,
        pause_stop_loss=PauseStopLoss.KEEP,
        mr_sell_unknown_fee=False,
        mr_tolerable_loss=D("5"),
        paper_funds=D("1000000"),
        backup_days=7,
    )


# --- 값 ----------------------------------------------------------------------------------------


def test_the_exchange_values_have_the_fields_of_the_spec_in_order() -> None:
    assert names(ExchangeHolding) == ["market", "balance", "locked", "avg_buy_price"]
    assert names(ExchangeAccount) == ["krw", "krw_locked", "holdings", "read_utc"]
    assert names(ExchangeRules) == ["bid_fee", "ask_fee", "min_total", "max_total"]
    assert names(ExchangeTrade) == ["trade_key", "price", "qty", "fee", "filled_utc"]
    assert names(OrderSnapshot) == [
        "uid", "market", "side", "kind", "price", "qty", "executed_qty", "state", "trades",
        "program",
    ]  # fmt: skip
    assert names(PlaceResult) == ["snapshot", "error_name", "unknown"]
    assert names(LiveSettings) == ["budget", "coin_ratios", "daily_loss_limit"]
    assert names(LiveConfirmView) == [
        "budget", "coins", "krw_available", "holdings", "total", "as_of",
    ]  # fmt: skip
    with pytest.raises(dataclasses.FrozenInstanceError):
        ExchangeHolding("KRW-AAA", D(1), D(0), None).balance = D(2)  # type: ignore[misc]


def test_the_constants_follow_the_spec() -> None:
    assert (UPBIT_KEY_VALID_DAYS, KEY_EXPIRY_WARN_DAYS, LIVE_CONFIRM_SECONDS) == (365, 30, 10)


def test_the_live_block_values_are_the_eight_texts_of_the_spec() -> None:
    assert [b.name for b in LiveBlock] == [
        "NOT_ALLOWED", "NO_KEY", "NOT_PAUSED", "NO_COINS", "NOT_ENOUGH_ROUNDS", "NO_SELL_ALL",
        "NO_SETTINGS", "BUDGET_TOO_BIG",
    ]  # fmt: skip
    assert [b.value for b in LiveBlock] == [
        "이 판에서는 실거래를 켤 수 없습니다",
        "업비트 키가 없습니다",
        "멈춤 상태가 아닙니다",
        "고른 코인이 없습니다",
        "{코인}의 모의 회차가 모자랍니다({센 수}/{N})",
        "모의매매에서 '멈추고 전량 매도'를 한 번 해야 합니다",
        "실거래 설정(예산, 코인별 투자 비율, 하루 손실 한도)을 넣어야 합니다",
        "실거래 예산이 원화 잔고와 보유 평가 금액의 합보다 큽니다(예산 {금액}원, 합 {금액}원)",
    ]


def test_the_two_block_texts_with_blanks_are_filled_by_helpers() -> None:
    assert live_block_not_enough_rounds("KRW-AAA", 3, 5) == "KRW-AAA의 모의 회차가 모자랍니다(3/5)"
    assert live_block_budget_too_big(D("1200000.9"), D("1000000")) == (
        "실거래 예산이 원화 잔고와 보유 평가 금액의 합보다 큽니다(예산 1,200,000원, 합 1,000,000원)"
    )


# --- 실거래 설정 ---------------------------------------------------------------------------------


def test_live_settings_start_empty_and_are_complete_only_with_all_three_and_every_coin_ratio() -> (
    None
):
    empty = LiveSettings()
    assert (empty.budget, dict(empty.coin_ratios), empty.daily_loss_limit) == (None, {}, None)
    coins = ("KRW-AAA", "KRW-BBB")
    ratios = {"KRW-AAA": D("60"), "KRW-BBB": D("40")}
    assert not empty.complete(coins)
    assert LiveSettings(D("1"), ratios, D("2")).complete(coins)
    assert not LiveSettings(None, ratios, D("2")).complete(coins)
    assert not LiveSettings(D("1"), ratios, None).complete(coins)
    assert not LiveSettings(D("1"), {}, D("2")).complete(coins)
    assert not LiveSettings(D("1"), {}, D("2")).complete(())  # 비율 묶음이 비어 있다
    assert not LiveSettings(D("1"), {"KRW-AAA": D("100")}, D("2")).complete(
        coins
    )  # 비율이 빠진 코인
    assert LiveSettings(D("1"), {**ratios, "KRW-CCC": D("1")}, D("2")).complete(coins)


def test_apply_replaces_only_budget_ratios_and_daily_loss_limit() -> None:
    base = trade()
    live = LiveSettings(D("500000"), {"KRW-AAA": D("70"), "KRW-BBB": D("30")}, D("1500"))
    applied = live.apply(base)
    assert applied is not base
    assert (applied.budget, dict(applied.coin_ratios), applied.daily_loss_limit) == (
        D("500000"), {"KRW-AAA": D("70"), "KRW-BBB": D("30")}, D("1500"),
    )  # fmt: skip
    for field in dataclasses.fields(TradeSettings):
        if field.name not in ("budget", "coin_ratios", "daily_loss_limit"):
            assert getattr(applied, field.name) == getattr(base, field.name), field.name
    assert applied.coin_budget("KRW-AAA") == D("350000")  # 500,000 × 70 ÷ 100
    assert base.budget == D("1000000")  # 모의매매의 값은 바뀌지 않는다


def test_apply_refuses_an_incomplete_setting() -> None:
    with pytest.raises(ValueError, match="실거래 설정"):
        LiveSettings(None, {"KRW-AAA": D("100")}, D("1")).apply(trade())
    with pytest.raises(ValueError, match="실거래 설정"):
        LiveSettings(D("1"), {"KRW-AAA": D("100")}, None).apply(trade())


# --- 경계 ---------------------------------------------------------------------------------------


class _Exchange:
    mode = Mode.PAPER

    def read_account(self) -> ExchangeAccount:
        return ExchangeAccount(D(0), D(0), (), NOW)

    def read_rules(self, market: str) -> ExchangeRules:
        return ExchangeRules(D("0.0005"), D("0.0005"), D(5000), None)

    def place(self, plan: OrderPlan, uid: str) -> PlaceResult:
        return PlaceResult(None, None, True)

    def query(self, uid: str) -> OrderSnapshot | None:
        return None

    def cancel(self, uid: str) -> OrderSnapshot | None:
        return None

    def open_orders(self, market: str) -> tuple[OrderSnapshot, ...]:
        return ()


def test_the_exchange_port_has_the_spec_members() -> None:
    members = {n for n in dir(ExchangePort) if not n.startswith("_")}
    assert {"read_account", "read_rules", "place", "query", "cancel", "open_orders"} <= members
    assert "mode" in typing.get_type_hints(ExchangePort)
    assert isinstance(_Exchange(), ExchangePort)
    partial = type("Partial", (), {"mode": Mode.PAPER, "read_account": _Exchange.read_account})
    assert not isinstance(partial(), ExchangePort)
    assert list(inspect.signature(ExchangePort.place).parameters) == ["self", "plan", "uid"]


def test_the_exchange_error_carries_the_error_name() -> None:
    error = ExchangeError("잔고 부족")
    assert error.error_name == "잔고 부족"
    assert isinstance(error, Exception)
    assert not isinstance(error, QuoteError)


def test_the_paper_store_is_a_protocol_of_account_orders_and_fills() -> None:
    members = {n for n in dir(PaperStore) if not n.startswith("_")}
    assert members == {
        "read_account", "write_account", "insert_order", "update_order", "find_order",
        "open_orders", "apply_fill", "fills_of",
    }  # fmt: skip

    class Store:
        def read_account(self, mode: Mode) -> None: ...
        def write_account(self, mode: Mode, account: Any) -> None: ...
        def insert_order(self, plan: Any, **kwargs: Any) -> Any: ...
        def update_order(self, order_id: int, **kwargs: Any) -> None: ...
        def find_order(self, uid: str) -> None: ...
        def open_orders(self, mode: Mode, market: str | None = None) -> tuple[Any, ...]: ...
        def apply_fill(self, fill: Any, account: Any) -> Any: ...
        def fills_of(self, order_id: int) -> tuple[Any, ...]: ...

    assert isinstance(Store(), PaperStore)
    assert not isinstance(object(), PaperStore)


def test_the_multi_price_source_reads_many_markets_in_one_call() -> None:
    class Prices:
        def get_prices(self, markets: tuple[str, ...]) -> dict[str, Decimal]:
            return dict.fromkeys(markets, D(1))

    assert isinstance(Prices(), MultiPriceSource)
    assert list(inspect.signature(MultiPriceSource.get_prices).parameters) == ["self", "markets"]
    assert "get_prices" not in dir(core.PriceSource)  # `PriceSource`는 바꾸지 않는다


def test_the_transport_moved_to_core_and_the_old_module_reexports_it() -> None:
    assert upbit_transport.TransportResponse is TransportResponse
    assert upbit_transport.TransportError is TransportError
    assert upbit_transport.UpbitTransport is UpbitTransport
    response = TransportResponse(200, {"Remaining-Req": "group=a; min=1; sec=2"}, "{}")
    assert response.header("remaining-req") == "group=a; min=1; sec=2"
    assert response.header("없음") is None
    assert (TransportResponse(404).headers, TransportResponse(404).text) == ({}, "")
    error = TransportError(timeout=True, sent=True)
    assert (error.timeout, error.sent) == (True, True)

    class Fake:
        def request(
            self, method: str, path: str, *, query: str, body: str | None, headers: Any
        ) -> TransportResponse:
            return TransportResponse(200)

    assert isinstance(Fake(), UpbitTransport)
    assert not isinstance(object(), UpbitTransport)


# --- 화면이 받는 값 ------------------------------------------------------------------------------


def test_the_account_views_follow_the_spec() -> None:
    assert [(s.name, s.value) for s in AccountState] == [
        ("BEFORE_SETUP", "before_setup"),
        ("NO_KEY", "no_key"),
        ("LOADING", "loading"),
        ("OK", "ok"),
        ("FAILED", "failed"),
        ("BLOCKED", "blocked"),
    ]
    assert names(AccountRow) == [
        "currency", "market", "qty", "locked", "avg_buy_price", "avg_modified", "price",
        "buy_amount", "value", "pnl", "pnl_rate", "picked", "note",
    ]  # fmt: skip
    assert names(AccountView) == [
        "state", "krw_available", "krw_locked", "holdings_value", "total_assets", "total_buy",
        "total_pnl", "total_pnl_rate", "rows", "excluded_value", "excluded_pnl", "read_utc",
        "error_name", "reading", "as_of",
    ]  # fmt: skip
    view = AccountView(
        AccountState.LOADING, None, None, None, None, None, None, None, (), 0, 0, None, None, True
    )
    assert view.as_of is None
    assert view.total_assets is None  # 처음 읽기 전의 금액 칸은 `None`


def test_the_live_and_key_views_follow_the_spec() -> None:
    assert names(LiveStatusView) == [
        "mode", "allowed", "blocks", "counts", "sell_all_done", "as_of",
    ]  # fmt: skip
    assert names(UpbitKeyView) == ["present", "created", "expires", "can_edit", "message"]
    key = UpbitKeyView(True, date(2026, 1, 1), date(2027, 1, 1), True, None)
    assert "secret" not in repr(key).lower()  # 키의 값은 들지 않는다
    assert LiveStatusView(Mode.PAPER, True, (), (), False).as_of is None


def test_the_old_views_get_the_m5_fields_last_with_defaults() -> None:
    assert names(SettingsView)[-1] == "live"
    assert SettingsView().live is None
    assert SettingsView(live=LiveSettings()).live == LiveSettings()
    assert names(TradeStatusView)[-1] == "mode"
    assert TradeStatusView(False, False, None, None, False, None, None).mode is Mode.PAPER
    assert HistoryTab.EXTERNAL.value == "external"
    assert list(HistoryTab)[-1] is HistoryTab.EXTERNAL


def test_the_new_views_are_ui_updates() -> None:
    members = set(
        typing.get_args(UiUpdate.__value__ if hasattr(UiUpdate, "__value__") else UiUpdate)
    )
    for cls in (AccountView, LiveStatusView, UpbitKeyView, LiveConfirmView):
        assert cls in members, cls.__name__
    assert AccountRow not in members


# --- 동작 ---------------------------------------------------------------------------------------

NEW_ACTIONS = (
    "save_upbit_key",
    "delete_upbit_key",
    "prepare_live_on",
    "turn_live_on",
    "turn_live_off",
    "account_visible",
    "refresh_account",
)


def test_the_new_actions_and_changed_arguments_of_the_port() -> None:
    for name in NEW_ACTIONS:
        assert name in dir(UiActions)
    assert list(inspect.signature(UiActions.save_upbit_key).parameters) == [
        "self", "access", "secret", "created",
    ]  # fmt: skip
    assert list(inspect.signature(UiActions.account_visible).parameters) == ["self", "visible"]
    for name in ("delete_upbit_key", "prepare_live_on", "turn_live_on", "turn_live_off",
                 "refresh_account"):  # fmt: skip
        assert list(inspect.signature(getattr(UiActions, name)).parameters) == ["self"]
    save = inspect.signature(UiActions.save_settings).parameters
    assert list(save)[-1] == "live"
    assert save["live"].kind is inspect.Parameter.KEYWORD_ONLY
    assert save["live"].default is None
    for name in ("load_reports", "load_history"):
        mode = inspect.signature(getattr(UiActions, name)).parameters["mode"]
        assert mode.default is None
        assert list(inspect.signature(getattr(AppActions, name)).parameters) == list(
            inspect.signature(getattr(UiActions, name)).parameters
        )
    assert list(inspect.signature(AppActions.save_settings).parameters) == list(save)
    assert list(inspect.signature(DataWorker.save_settings).parameters) == list(save)


class Spy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        def record(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))

        return record


def test_the_app_actions_hand_the_new_calls_to_the_data_thread() -> None:
    data = Spy()
    actions = AppActions(data, Spy())  # type: ignore[arg-type]
    created = date(2026, 1, 1)
    live = LiveSettings(D("1"), {"KRW-AAA": D("100")}, D("2"))
    actions.save_upbit_key("접근", "비밀", created)
    actions.delete_upbit_key()
    actions.prepare_live_on()
    actions.turn_live_on()
    actions.turn_live_off()
    actions.account_visible(True)
    actions.refresh_account()
    actions.load_reports("KRW-AAA", Mode.LIVE)
    actions.load_history(HistoryTab.EXTERNAL, None, created, Mode.LIVE)
    actions.save_settings(("KRW-AAA",), 7, first_setup=False, live=live)
    actions.account_visible(False)  # 숨음도 그대로 넘긴다
    assert data.calls[0] == ("save_upbit_key", ("접근", "비밀", created), {})
    assert [c[0] for c in data.calls[1:7]] == [
        "delete_upbit_key", "prepare_live_on", "turn_live_on", "turn_live_off",
        "account_visible", "refresh_account",
    ]  # fmt: skip
    assert data.calls[5][1] == (True,)
    assert data.calls[7] == ("load_reports", ("KRW-AAA", Mode.LIVE), {})
    assert data.calls[8] == ("load_history", (HistoryTab.EXTERNAL, None, created, Mode.LIVE), {})
    assert data.calls[9][2]["live"] is live
    assert data.calls[10] == ("account_visible", (False,), {})


def test_the_data_thread_takes_the_new_calls_and_keeps_no_key_value(tmp_path: Path) -> None:
    worker = DataWorker(
        database_file=tmp_path / "app.db",
        logs_dir=tmp_path,
        clock=SystemClock(),
        quote=None,
        sink=lambda update: None,
    )
    secret = "비밀" + "값"
    worker.save_upbit_key("접근" + "값", secret, date(2026, 1, 1))
    worker.delete_upbit_key()
    worker.prepare_live_on()
    worker.turn_live_on()
    worker.turn_live_off()
    worker.account_visible(True)
    worker.refresh_account()
    assert len(worker._queue) == 7  # 15번부터 키, 켜기·끄기, 계정의 동작을 큐에 넣는다
    worker.load_history(HistoryTab.EXTERNAL, None, date(2026, 1, 1), Mode.LIVE)
    assert len(worker._queue) == 8  # 기록은 25번부터 큐에 넣는다(외부 변동 탭은 빈 값을 보낸다)
    assert secret not in repr(vars(worker))


# --- 글, Services, 내보내기 -----------------------------------------------------------------------


def test_the_texts_and_the_services_field_follow_the_spec() -> None:
    assert texts.STAGE_KEY_SAVE == "업비트 키 저장"
    assert texts.STAGE_LIVE_ON == "실거래 켜기"
    assert texts.STAGE_EXCHANGE_ACCOUNT == "계정 읽기" == texts.STAGE_ACCOUNT
    assert [
        texts.CODE_DIRECT_LOCKED,
        texts.CODE_DIRECT_CANCELED,
        texts.CODE_LIVE_OFF,
        texts.CODE_EXTERNAL,
        texts.CODE_KEY_EXPIRY,
    ] == ["A-6", "A-7", "A-20", "A-22", "A-32"]
    assert [f.name for f in dataclasses.fields(Services)][-1] == "exchange_transport"
    assert Services().exchange_transport is None

    class Fake:
        def request(self, *args: Any, **kwargs: Any) -> TransportResponse:
            return TransportResponse(200)

    fake = Fake()
    assert Services(exchange_transport=fake).exchange_transport is fake  # type: ignore[arg-type]


def test_every_new_name_is_exported_by_core() -> None:
    for name in (
        "ExchangeHolding", "ExchangeAccount", "ExchangeRules", "ExchangeTrade", "OrderSnapshot",
        "PlaceResult", "LiveBlock", "LiveSettings", "LiveConfirmView", "UPBIT_KEY_VALID_DAYS",
        "KEY_EXPIRY_WARN_DAYS", "LIVE_CONFIRM_SECONDS", "ExchangePort", "PaperStore",
        "MultiPriceSource", "UpbitTransport", "TransportResponse", "TransportError",
        "ExchangeError", "AccountState", "AccountRow", "AccountView", "LiveStatusView",
        "UpbitKeyView",
    ):  # fmt: skip
        assert name in core.__all__, name
        assert hasattr(core, name), name
    assert len(core.__all__) == len(set(core.__all__))


def test_the_exchange_module_imports_only_the_standard_library_and_core() -> None:
    source = Path(exchange_module.__file__).read_text(encoding="utf-8")
    roots = {
        node.module.split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert roots <= {"coin_report_trader", *sys.stdlib_module_names}


def test_an_order_snapshot_and_a_place_result_can_be_built() -> None:
    snapshot = OrderSnapshot(
        "u1", "KRW-AAA", OrderSide.BUY, OrderKind.LIMIT, D(100), D(2), D(0), OrderState.OPEN, (),
        True,
    )  # fmt: skip
    result = PlaceResult(snapshot, None, False)
    assert result.snapshot is snapshot
    assert OrderReason.JUDGMENT.value == "judgment"
