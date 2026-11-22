"""1단계 보완의 공용 타입(M1 명세 3.4, 3.5.1, 31번): 봉 단위 넷, 봉 개수, `BarStats`, 뷰의 새 칸,
`save_settings`의 새 인자. 값은 모두 만든 값이다."""

import dataclasses
import inspect
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

import coin_report_trader.core as core
from coin_report_trader.app.actions import AppActions
from coin_report_trader.core import (
    BAR_STATS_COLUMNS,
    BAR_STATS_TITLES,
    CANDLE_COUNTS,
    CANDLE_UNIT_LENGTHS,
    CANDLE_UNIT_TITLES,
    DEFAULT_CANDLE_COUNT,
    BarStats,
    CandleUnit,
    CoinView,
    LoadState,
    SettingsView,
    UiActions,
    empty_bar_stats,
)


def test_there_are_four_candle_units_in_the_screen_order() -> None:
    assert list(CandleUnit) == [
        CandleUnit.MINUTE_30,
        CandleUnit.MINUTE_60,
        CandleUnit.MINUTE_240,
        CandleUnit.DAY,
    ]
    assert [unit.value for unit in CandleUnit] == ["minute30", "minute60", "minute240", "day"]


def test_the_two_old_unit_values_still_parse_so_old_stored_candles_can_be_read() -> None:
    assert CandleUnit("day") is CandleUnit.DAY
    assert CandleUnit("minute60") is CandleUnit.MINUTE_60


def test_candle_unit_titles_and_lengths() -> None:
    assert [CANDLE_UNIT_TITLES[unit] for unit in CandleUnit] == [
        "30분봉",
        "60분봉",
        "240분봉",
        "일봉",
    ]
    assert [CANDLE_UNIT_LENGTHS[unit] for unit in CandleUnit] == [
        timedelta(minutes=30),
        timedelta(minutes=60),
        timedelta(minutes=240),
        timedelta(days=1),
    ]
    assert set(CANDLE_UNIT_TITLES) == set(CANDLE_UNIT_LENGTHS) == set(CandleUnit)


def test_the_tables_cannot_be_changed() -> None:
    with pytest.raises(TypeError):
        CANDLE_UNIT_TITLES[CandleUnit.DAY] = "다른 글"  # type: ignore[index]
    with pytest.raises(TypeError):
        CANDLE_UNIT_LENGTHS[CandleUnit.DAY] = timedelta(days=2)  # type: ignore[index]
    with pytest.raises(TypeError):
        BAR_STATS_TITLES["market"] = "다른 글"  # type: ignore[index]


def test_candle_counts() -> None:
    assert CANDLE_COUNTS == (100, 200, 500)
    assert DEFAULT_CANDLE_COUNT == 200
    assert DEFAULT_CANDLE_COUNT in CANDLE_COUNTS


EXPECTED_COLUMNS = (
    "market", "unit", "requested_count", "bar_count", "first_start_utc", "last_start_utc",
    "close_last", "change", "slope", "vs_mean", "up_ratio", "volatility", "max_drawdown",
    "from_high", "high", "low",
)  # fmt: skip


def test_the_bar_stats_columns_are_the_table_of_the_spec_in_order() -> None:
    assert BAR_STATS_COLUMNS == EXPECTED_COLUMNS
    assert [field.name for field in dataclasses.fields(BarStats)] == list(EXPECTED_COLUMNS)


def test_the_bar_stats_titles_cover_every_column() -> None:
    assert list(BAR_STATS_TITLES) == list(BAR_STATS_COLUMNS)
    assert [BAR_STATS_TITLES[column] for column in BAR_STATS_COLUMNS] == [
        "코인", "봉 단위", "고른 봉 개수", "쓴 봉 수", "첫 봉 시작", "마지막 봉 시작",
        "마지막 종가", "전체 변화", "추세 기울기", "평균 대비", "오른 봉 비율", "변동성",
        "최대 낙폭", "최고가 대비", "최고가", "최저가",
    ]  # fmt: skip


def test_empty_bar_stats_has_no_bars_and_no_values() -> None:
    empty = empty_bar_stats("KRW-AAA", CandleUnit.MINUTE_240, 500)
    assert (empty.market, empty.unit, empty.requested_count, empty.bar_count) == (
        "KRW-AAA",
        CandleUnit.MINUTE_240,
        500,
        0,
    )
    values = [getattr(empty, column) for column in BAR_STATS_COLUMNS[4:]]
    assert values == [None] * 12


def test_bar_stats_is_a_frozen_value_with_the_types_of_the_table() -> None:
    stats = BarStats("KRW-AAA", CandleUnit.DAY, 100, 100, close_last=Decimal("10.5"))
    assert stats == BarStats("KRW-AAA", CandleUnit.DAY, 100, 100, close_last=Decimal("10.5"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        stats.bar_count = 1  # type: ignore[misc]
    hints = {field.name: str(field.type) for field in dataclasses.fields(BarStats)}
    assert "Decimal" in hints["change"]
    assert "datetime" in hints["first_start_utc"]


def test_coin_view_gets_stats_last_with_none_as_the_default() -> None:
    names = [field.name for field in dataclasses.fields(CoinView)]
    assert names[-1] == "stats"
    assert CoinView("KRW-AAA", LoadState.NO_DATA).stats is None
    stats = empty_bar_stats("KRW-AAA", CandleUnit.DAY, 200)
    assert CoinView("KRW-AAA", LoadState.NO_DATA, stats=stats).stats is stats


def test_the_old_stats_values_are_gone_from_coin_view() -> None:
    names = {field.name for field in dataclasses.fields(CoinView)}
    assert "periods" not in names
    assert "indicator" not in names
    assert names == {
        "market", "state", "last_incomplete", "fetched_at", "error_name", "blocked_until",
        "as_of", "stats",
    }  # fmt: skip


def test_settings_view_gets_unit_and_count_last_with_none_as_the_default() -> None:
    names = [field.name for field in dataclasses.fields(SettingsView)]
    assert names[-7:-4] == ["candle_unit", "candle_count", "ai_choices"]  # 2단계가 맨 뒤에 더했다
    view = SettingsView(("KRW-AAA",), 30, True)
    assert (view.candle_unit, view.candle_count) == (None, None)
    chosen = SettingsView(
        ("KRW-AAA",), 30, True, candle_unit=CandleUnit.MINUTE_30, candle_count=500
    )
    assert (chosen.candle_unit, chosen.candle_count) == (CandleUnit.MINUTE_30, 500)


def test_save_settings_of_the_port_takes_unit_and_count_with_none_as_the_default() -> None:
    parameters = inspect.signature(UiActions.save_settings).parameters
    assert list(parameters) == [
        "self", "coins", "retention_days", "first_setup", "candle_unit", "candle_count",
        "ai_choices", "autostart", "trade", "accept_below_stop",
    ]  # fmt: skip
    for name in (
        "first_setup", "candle_unit", "candle_count", "ai_choices", "autostart", "trade",
        "accept_below_stop",
    ):  # fmt: skip
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["candle_unit"].default is None
    assert parameters["candle_count"].default is None
    assert parameters["ai_choices"].default is None
    assert parameters["autostart"].default is None
    assert parameters["first_setup"].default is inspect.Parameter.empty


class Spy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def save_settings(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("save_settings", args, kwargs))


def test_the_actions_hand_unit_and_count_to_the_data_thread() -> None:
    data = Spy()
    actions = AppActions(data, Spy())  # type: ignore[arg-type]
    actions.save_settings(("KRW-AAA",), 7, first_setup=True)
    actions.save_settings(
        ("KRW-AAA",), 7, first_setup=False, candle_unit=CandleUnit.MINUTE_240, candle_count=100
    )
    assert data.calls == [
        (
            "save_settings",
            (("KRW-AAA",), 7),
            {
                "first_setup": True,
                "candle_unit": None,
                "candle_count": None,
                "ai_choices": None,
                "autostart": None,
                "trade": None,
                "accept_below_stop": (),
            },
        ),
        (
            "save_settings",
            (("KRW-AAA",), 7),
            {
                "first_setup": False,
                "candle_unit": CandleUnit.MINUTE_240,
                "candle_count": 100,
                "ai_choices": None,
                "autostart": None,
                "trade": None,
                "accept_below_stop": (),
            },
        ),
    ]


def test_the_app_actions_still_keep_the_ports_signature() -> None:
    port = inspect.signature(UiActions.save_settings)
    mine = inspect.signature(AppActions.save_settings)
    assert list(mine.parameters) == list(port.parameters)
    assert {n: p.default for n, p in mine.parameters.items()} == {
        n: p.default for n, p in port.parameters.items()
    }


def test_the_new_names_are_exported_from_core() -> None:
    for name in (
        "BAR_STATS_COLUMNS", "BAR_STATS_TITLES", "CANDLE_COUNTS", "CANDLE_UNIT_LENGTHS",
        "CANDLE_UNIT_TITLES", "DEFAULT_CANDLE_COUNT", "BarStats", "empty_bar_stats",
    ):  # fmt: skip
        assert name in core.__all__
        assert hasattr(core, name)
    for name in ("PeriodStats", "CandleIndicator", "PERIODS", "empty_periods"):
        assert name not in core.__all__  # 옛 이름은 51번이 지웠다
        assert not hasattr(core, name)


def test_the_data_thread_takes_unit_and_count_and_queues_them(tmp_path: Any) -> None:
    from coin_report_trader.app.data_thread import DataWorker
    from coin_report_trader.core import SystemClock

    port = inspect.signature(UiActions.save_settings)
    mine = inspect.signature(DataWorker.save_settings)
    assert list(mine.parameters) == list(port.parameters)
    worker = DataWorker(
        database_file=tmp_path / "app.db",
        logs_dir=tmp_path,
        clock=SystemClock(),
        quote=None,
        sink=lambda update: None,
    )
    worker.save_settings(
        ("KRW-AAA",), 7, first_setup=True, candle_unit=CandleUnit.MINUTE_30, candle_count=500
    )
    worker.save_settings(("KRW-AAA",), 7, first_setup=True)
    first, second = list(worker._queue)  # 39번부터 두 값을 일에 담는다
    assert first != second
    assert (first.candle_unit, first.candle_count) == (CandleUnit.MINUTE_30, 500)
    assert (second.candle_unit, second.candle_count) == (None, None)
