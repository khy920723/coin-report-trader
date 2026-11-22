"""시작 앱 등록의 공용 타입, 저장, 자료 스레드의 맞추기(M1 명세 3.16.2, 3.16.3). 만든 값만 쓴다."""

import dataclasses
import inspect
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fakes.autostart import FakeAutostart
from fakes.clock import FakeClock
from fakes.quote import FakeQuoteSource, make_candles

from coin_report_trader.app import autostart as autostart_module
from coin_report_trader.app.actions import AppActions
from coin_report_trader.app.data_thread import DataWorker
from coin_report_trader.app.services import Services
from coin_report_trader.core import (
    CandleUnit,
    Market,
    SettingsView,
    StorageError,
    UiActions,
    UiUpdate,
    texts,
)
from coin_report_trader.storage.database import migrate, open_database
from coin_report_trader.storage.settings import KEY_AUTOSTART, read_settings, write_settings

AAA = "KRW-AAA"
HOUR = CandleUnit.MINUTE_60
SAFETY = 10.0
THIS_HOUR = datetime(2026, 1, 5, 3, tzinfo=UTC)
COMMAND = '"C:\\made\\up\\coin-report-trader.exe"'
OLD_COMMAND = '"C:\\made\\up\\old\\coin-report-trader.exe"'


@pytest.fixture(autouse=True)
def fixed_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autostart_module, "current_command", lambda: COMMAND)
    monkeypatch.setattr("coin_report_trader.app.data_thread.current_command", lambda: COMMAND)


# --- 공용 타입과 글 ------------------------------------------------------------------------------


def test_the_settings_view_gets_three_fields_last_with_the_spec_defaults() -> None:
    names = [f.name for f in dataclasses.fields(SettingsView)]
    assert names[-5:-2] == ["autostart", "autostart_supported", "autostart_error"]
    view = SettingsView()
    assert (view.autostart, view.autostart_supported, view.autostart_error) == (False, False, None)


def test_save_settings_of_the_port_gets_autostart_last_as_a_keyword_with_none() -> None:
    parameters = inspect.signature(UiActions.save_settings).parameters
    assert (
        list(parameters)[-4] == "autostart"
    )  # 3·5단계가 `trade`, `accept_below_stop`, `live`를 더했다
    assert parameters["autostart"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["autostart"].default is None
    assert list(inspect.signature(AppActions.save_settings).parameters) == list(parameters)


def test_the_two_texts_and_the_services_field_follow_the_spec() -> None:
    assert texts.AUTOSTART_REGISTER_FAILED == "시작 앱에 등록하지 못했습니다."
    assert texts.AUTOSTART_REMOVE_FAILED == "시작 앱에서 지우지 못했습니다."
    assert [f.name for f in dataclasses.fields(Services)][-4] == "autostart"
    assert Services().autostart is None


class Spy:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def save_settings(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def test_the_app_actions_hand_autostart_on_to_the_data_thread() -> None:
    data = Spy()
    actions = AppActions(data, Spy())  # type: ignore[arg-type]
    actions.save_settings((AAA,), 7, first_setup=False, autostart=True)
    actions.save_settings((AAA,), 7, first_setup=False)
    assert [call["autostart"] for call in data.calls] == [True, None]


# --- 저장(표 settings의 키 autostart) ------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_database(tmp_path / "app.db")
    migrate(connection)
    yield connection
    connection.close()


def stored(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (KEY_AUTOSTART,)).fetchone()
    return None if row is None else row[0]


def test_a_missing_key_reads_as_off_and_the_key_name_and_values_are_on_and_off(
    conn: sqlite3.Connection,
) -> None:
    assert KEY_AUTOSTART == "autostart"
    assert read_settings(conn).autostart is False
    write_settings(conn, SettingsView((AAA,), 30, True), autostart=True)
    assert stored(conn) == "on"
    assert read_settings(conn).autostart is True
    write_settings(conn, SettingsView((AAA,), 30, True), autostart=False)
    assert stored(conn) == "off"
    assert read_settings(conn).autostart is False


def test_none_leaves_the_stored_value_and_the_view_field_is_not_written(
    conn: sqlite3.Connection,
) -> None:
    write_settings(conn, SettingsView((AAA,), 30, True), autostart=True)
    write_settings(conn, SettingsView((AAA,), 7, True))  # None: 그대로
    assert stored(conn) == "on"
    write_settings(conn, SettingsView((AAA,), 7, True, autostart=False))  # 뷰의 값은 쓰지 않는다
    assert stored(conn) == "on"


def test_the_key_is_written_in_the_same_transaction_as_the_other_values(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(ValueError, match="보관 기간"):
        write_settings(conn, SettingsView((AAA,), 8, True), autostart=True)
    assert stored(conn) is None  # 앞의 검사에서 막혀 아무것도 쓰지 않았다
    conn.execute(
        "CREATE TRIGGER stop BEFORE INSERT ON settings WHEN NEW.key = 'autostart' "
        "BEGIN SELECT RAISE(ABORT, 'stop'); END"
    )
    with pytest.raises(StorageError):
        write_settings(conn, SettingsView((AAA,), 7, True), autostart=True)
    assert (
        conn.execute("SELECT count(*) FROM settings WHERE key = 'retention_days'").fetchone()[0]
        == 0
    )


def test_an_unknown_text_reads_as_off(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES ('autostart', 'yes')")
    assert read_settings(conn).autostart is False


# --- 자료 스레드 ---------------------------------------------------------------------------------


class Sink:
    def __init__(self) -> None:
        self.items: list[UiUpdate] = []

    def __call__(self, update: UiUpdate) -> None:
        self.items.append(update)

    def settings(self) -> list[SettingsView]:
        return [item for item in self.items if isinstance(item, SettingsView)]


class Rig:
    def __init__(self, tmp_path: Path, fake: FakeAutostart | None, *, saved: bool | None) -> None:
        self.db = tmp_path / "app.db"
        self.logs = tmp_path / "logs"
        self.logs.mkdir(parents=True)
        conn = open_database(self.db)
        try:
            migrate(conn)
            view = SettingsView((AAA,), 30, True, candle_unit=HOUR, candle_count=100)
            write_settings(conn, view, autostart=saved)
        finally:
            conn.close()
        clock = FakeClock()
        quote = FakeQuoteSource(markets=[Market(AAA, "이름", False)], clock=clock)
        quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 200, last_start=THIS_HOUR))
        self.fake = fake
        self.sink = Sink()
        self.worker = DataWorker(
            database_file=self.db,
            logs_dir=self.logs,
            clock=clock,
            quote=quote,
            sink=self.sink,
            autostart=fake,
        )

    def start(self) -> None:
        self.worker.start()
        assert self.worker.wait_idle(SAFETY)

    def save(self, autostart: bool | None, *, days: int = 30) -> None:
        self.worker.save_settings(
            (AAA,), days, first_setup=False, candle_unit=HOUR, candle_count=100, autostart=autostart
        )
        assert self.worker.wait_idle(SAFETY)

    def last(self) -> SettingsView:
        return self.sink.settings()[-1]

    def stored(self) -> SettingsView:
        conn = open_database(self.db)
        try:
            return read_settings(conn)
        finally:
            conn.close()


@pytest.fixture
def make(tmp_path: Path) -> Iterator[Any]:
    rigs: list[Rig] = []

    def factory(fake: FakeAutostart | None, *, saved: bool | None = None) -> Rig:
        rig = Rig(tmp_path / f"r{len(rigs)}", fake, saved=saved)
        rigs.append(rig)
        return rig

    yield factory
    for rig in rigs:
        rig.worker.stop()
        rig.worker.join(SAFETY)


def test_at_start_an_off_value_does_not_touch_the_registry_but_fills_supported(make: Any) -> None:
    fake = FakeAutostart(registered=OLD_COMMAND)
    rig = make(fake, saved=False)
    rig.start()
    first = rig.sink.settings()[0]
    assert (first.autostart, first.autostart_supported, first.autostart_error) == (
        False,
        True,
        None,
    )
    assert (fake.writes, fake.deletes, fake.registered) == ([], 0, OLD_COMMAND)


def test_at_start_an_on_value_registers_when_missing_or_different_but_not_when_same(
    make: Any,
) -> None:
    missing = FakeAutostart()
    make(missing, saved=True).start()
    assert missing.writes == [COMMAND]
    old = FakeAutostart(registered=OLD_COMMAND)
    make(old, saved=True).start()
    assert old.writes == [COMMAND]  # 옛 판의 폴더
    same = FakeAutostart(registered=COMMAND)
    rig = make(same, saved=True)
    rig.start()
    assert same.writes == []  # 같으면 쓰지 않는다
    assert rig.sink.settings()[0].autostart is True


def test_the_first_settings_view_carries_the_result_of_the_start_match(make: Any) -> None:
    fake = FakeAutostart()
    rig = make(fake, saved=True)
    rig.start()
    assert fake.writes == [COMMAND]  # 첫 SettingsView를 보내기 전에 맞춘다
    assert rig.sink.settings()[0].autostart_error is None


def test_at_start_an_unsupported_run_never_touches_the_registry(make: Any) -> None:
    fake = FakeAutostart(supported=False)
    rig = make(fake, saved=True)
    rig.start()
    view = rig.sink.settings()[0]
    assert (view.autostart, view.autostart_supported, view.autostart_error) == (True, False, None)
    assert (fake.writes, fake.deletes) == ([], 0)


def test_at_start_a_failed_registration_shows_the_text_and_keeps_the_stored_value(
    make: Any, caplog: pytest.LogCaptureFixture
) -> None:
    folder = "C:" + "\\Users\\" + "made-up"  # 사용자 폴더 꼴의 만든 경로(실행 중에 이어 붙인다)
    fake = FakeAutostart(write_error=OSError(f"막힘 {folder}\\x"))
    rig = make(fake, saved=True)
    rig.start()
    view = rig.sink.settings()[0]
    assert view.autostart_error == texts.AUTOSTART_REGISTER_FAILED
    assert view.autostart is True
    assert rig.stored().autostart is True
    assert "made-up" not in caplog.text  # 로그는 가린다
    assert "시작 앱 등록이 실패했습니다" in caplog.text


def test_saving_on_registers_and_the_view_and_the_key_follow(make: Any) -> None:
    fake = FakeAutostart()
    rig = make(fake, saved=None)
    rig.start()
    rig.save(True)
    assert fake.writes == [COMMAND]
    assert (rig.last().autostart, rig.last().autostart_error) == (True, None)
    assert rig.last().autostart_supported is True
    assert rig.stored().autostart is True
    rig.save(True)  # 같은 값이면 쓰지 않는다
    assert fake.writes == [COMMAND]


def test_saving_off_deletes_and_a_missing_value_is_a_success(make: Any) -> None:
    fake = FakeAutostart(registered=COMMAND)
    rig = make(fake, saved=True)
    rig.start()
    rig.save(False)
    assert (fake.deletes, fake.registered) == (1, None)
    assert (rig.last().autostart, rig.last().autostart_error) == (False, None)
    assert rig.stored().autostart is False
    rig.save(False)  # 없는 값의 지우기도 성공
    assert (fake.deletes, rig.last().autostart_error) == (2, None)


def test_a_failed_registration_keeps_the_other_values_and_the_key_and_a_retry_clears_it(
    make: Any,
) -> None:
    fake = FakeAutostart(write_error=OSError("막힘"))
    rig = make(fake, saved=None)
    rig.start()
    rig.save(True, days=7)
    view = rig.last()
    assert (view.autostart, view.autostart_error) == (True, texts.AUTOSTART_REGISTER_FAILED)
    stored = rig.stored()
    assert (stored.autostart, stored.retention_days) == (True, 7)  # 설정의 저장은 그대로 된다
    fake.write_error = None
    rig.save(True, days=7)  # 다음에 저장할 때 다시 맞춘다
    assert fake.writes == [COMMAND]
    assert rig.last().autostart_error is None


def test_a_failed_removal_shows_its_own_text(make: Any) -> None:
    fake = FakeAutostart(registered=COMMAND, delete_error=OSError("막힘"))
    rig = make(fake, saved=True)
    rig.start()
    rig.save(False)
    assert rig.last().autostart_error == texts.AUTOSTART_REMOVE_FAILED
    assert rig.stored().autostart is False
    assert fake.registered == COMMAND


def test_saving_without_autostart_keeps_the_stored_value_and_does_not_touch_the_registry(
    make: Any,
) -> None:
    fake = FakeAutostart(registered=COMMAND)
    rig = make(fake, saved=True)
    rig.start()
    rig.save(None, days=90)
    assert (fake.writes, fake.deletes) == ([], 0)
    assert rig.last().autostart is True
    assert rig.stored().autostart is True
    assert rig.stored().retention_days == 90


def test_an_unsupported_save_stores_the_key_and_leaves_the_registry_alone(make: Any) -> None:
    fake = FakeAutostart(supported=False)
    rig = make(fake, saved=None)
    rig.start()
    rig.save(True)
    assert (fake.writes, fake.deletes) == ([], 0)
    assert rig.stored().autostart is True
    assert rig.last().autostart_supported is False


def test_without_a_given_store_the_real_one_is_used_and_it_is_unsupported_under_uv_run(
    make: Any,
) -> None:
    rig = make(None, saved=True)
    rig.start()
    view = rig.sink.settings()[0]
    assert (view.autostart, view.autostart_supported, view.autostart_error) == (True, False, None)
