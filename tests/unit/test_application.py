"""`run_gui`와 `main([])` 시험. 창이 뜬 직후 이벤트 루프를 끝낸다."""

import logging
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fakes.clock import FakeClock
from fakes.discord import FakeDiscord
from fakes.quote import FakeQuoteSource
from fakes.vault import FakeVault
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel

from coin_report_trader import __main__ as entry
from coin_report_trader import ui
from coin_report_trader.app import application, run_gui
from coin_report_trader.app.services import Services
from coin_report_trader.config.paths import resolve_app_paths
from coin_report_trader.core import (
    CandleUnit,
    LoadState,
    MarketsView,
    QuoteError,
    SettingsView,
    StorageError,
)
from coin_report_trader.storage.database import migrate, open_database

ROOT_TOKEN = "%LOCALAPPDATA%" if sys.platform == "win32" else "$XDG_DATA_HOME"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request: pytest.FixtureRequest) -> Any:
    """자료 폴더를 `tmp_path` 아래로 바꾸고 offscreen QApplication을 얻는다."""
    data_home = tmp_path / "share"
    data_home.mkdir()
    key = "LOCALAPPDATA" if sys.platform == "win32" else "XDG_DATA_HOME"
    monkeypatch.setenv(key, str(data_home))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qapp = request.getfixturevalue("qapp")
    return SimpleNamespace(data_home=data_home, root=data_home / "coin-report-trader", app=qapp)


def _services(quote: FakeQuoteSource | None = None) -> Services:
    """창을 띄우는 시험은 가짜 시세, 가짜 디스코드, 가짜 보관소, 가짜 시계로 돈다. 가짜 보관소가
    없으면 Windows에서 진짜 보관소(keyring)를 부르므로 늘 함께 넣는다."""
    return Services(
        quote=quote or FakeQuoteSource(),
        webhook=FakeDiscord(),
        vault=FakeVault(),
        clock=FakeClock(),
    )


def _quit_after_look(app: QApplication, look: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """이벤트 루프가 돌기 시작하면 한 번 보고 끝낸다. 보지 못해도 10초 뒤에는 끝낸다."""
    seen: dict[str, Any] = {}
    safety = QTimer()
    safety.setSingleShot(True)
    safety.timeout.connect(app.quit)
    safety.start(10_000)
    # 이벤트 루프가 끝난 뒤에도 타이머가 남지 않게 seen에 붙잡아 둔다.
    seen["_safety"] = safety

    def tick() -> None:
        try:
            seen.update(look())
        except Exception as exc:  # 슬롯의 예외는 Qt가 삼키므로 시험이 읽게 남긴다.
            seen["error"] = repr(exc)
        finally:
            app.quit()

    QTimer.singleShot(0, tick)
    return seen


def _look_at_window() -> dict[str, Any]:
    windows = [w for w in QApplication.topLevelWidgets() if w.objectName() == "mainWindow"]
    assert len(windows) == 1
    window = windows[0]
    status = window.findChild(QLabel, "statusLine")
    assert status is not None
    return {"visible": window.isVisible(), "title": window.windowTitle(), "status": status.text()}


def _finish(seen: dict[str, Any]) -> dict[str, Any]:
    seen.pop("_safety").stop()
    assert "error" not in seen, seen["error"]
    return seen


def _log_text(env: Any) -> str:
    return (env.root / "logs" / "app.log").read_text(encoding="utf-8")


def test_run_gui_shows_the_window_and_logs_start_and_stop(env: Any) -> None:
    seen = _quit_after_look(env.app, _look_at_window)
    assert run_gui([], services=_services()) == 0
    seen = _finish(seen)
    assert seen["visible"] is True
    assert seen["title"] == "Coin Report Trader"
    assert seen["status"].startswith("바탕 버전 0.0.1 · 매매 기능 없음 · 자료 폴더 ")
    assert seen["status"].endswith(
        f"{ROOT_TOKEN}{'\\' if sys.platform == 'win32' else '/'}coin-report-trader"
    )
    assert str(env.data_home) not in seen["status"]
    for folder in (env.root, env.root / "data", env.root / "logs"):
        assert folder.is_dir()
    assert (env.root / "data" / "app.db").is_file()
    log = _log_text(env)
    assert "시작: 버전 0.0.1" in log
    assert "종료: 코드 0" in log
    assert log.index("시작") < log.index("종료")
    assert str(env.data_home) not in log


def test_run_gui_uses_the_existing_application_and_cleans_up(env: Any) -> None:
    seen = _quit_after_look(env.app, lambda: {"same": QApplication.instance() is env.app})
    assert run_gui([], services=_services()) == 0
    assert _finish(seen)["same"] is True
    assert QApplication.instance() is env.app
    assert logging.getLogger("coin_report_trader").handlers == []
    # 로그 파일이 닫혔으므로 지울 수 있다(Windows).
    (env.root / "logs" / "app.log").unlink()


def test_main_without_options_runs_the_window(env: Any) -> None:
    seen = _quit_after_look(env.app, _look_at_window)
    assert entry.main([], services=_services()) == 0
    assert _finish(seen)["visible"] is True
    assert (env.root / "data" / "app.db").is_file()
    assert "종료: 코드 0" in _log_text(env)


def test_run_twice_reuses_the_database_and_appends_to_the_log(env: Any) -> None:
    for _ in range(2):
        seen = _quit_after_look(env.app, _look_at_window)
        assert run_gui([], services=_services()) == 0
        _finish(seen)
    assert _log_text(env).count("종료: 코드 0") == 2


def _make_newer_database(env: Any) -> None:
    paths = resolve_app_paths()
    paths.ensure()
    conn = open_database(paths.database_file)
    try:
        conn.execute("PRAGMA user_version=2")
    finally:
        conn.close()


def test_a_newer_database_stops_before_the_window(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_window(_context: object) -> object:
        raise AssertionError("창을 만들면 안 된다")

    monkeypatch.setattr(ui, "create_main_window", no_window)
    _make_newer_database(env)
    with pytest.raises(StorageError, match="스키마 번호"):
        run_gui([])
    log = _log_text(env)
    assert "오류: StorageError" in log
    assert "종료: 코드 1" in log
    assert logging.getLogger("coin_report_trader").handlers == []


def test_main_turns_a_startup_error_into_exit_code_one(
    env: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    _make_newer_database(env)
    assert entry.main([]) == 1
    err = capsys.readouterr().err
    assert "StorageError" in err
    assert str(env.data_home) not in err


def test_database_open_failure_is_logged_with_shortened_paths(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(path: Path) -> object:
        raise StorageError(f"cannot open {path}")

    monkeypatch.setattr(application, "open_database", refuse)
    with pytest.raises(StorageError):
        run_gui([])
    log = _log_text(env)
    assert f"오류: StorageError: cannot open {ROOT_TOKEN}" in log
    assert str(env.data_home) not in log


def test_an_unusable_data_location_fails_before_any_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    key = "LOCALAPPDATA" if sys.platform == "win32" else "XDG_DATA_HOME"
    monkeypatch.setenv(key, str(blocker))
    with pytest.raises(OSError):  # noqa: PT011
        run_gui([])
    assert entry.main([]) == 1


def test_closing_the_window_ends_the_program(env: Any) -> None:
    """명세 2절 6번: 창을 닫으면 프로그램이 끝난다. `quit()`을 부르지 않는다."""
    fired = {"safety": False}
    safety = QTimer()
    safety.setSingleShot(True)
    safety.timeout.connect(lambda: (fired.update(safety=True), env.app.quit()))
    safety.start(10_000)
    seen: dict[str, Any] = {}

    def close_the_window() -> None:
        try:
            windows = [w for w in QApplication.topLevelWidgets() if w.objectName() == "mainWindow"]
            visible = [w for w in windows if w.isVisible()]
            seen["visible"] = len(visible)
            for window in visible:
                window.close()
        except Exception as exc:
            seen["error"] = repr(exc)
            env.app.quit()

    QTimer.singleShot(0, close_the_window)
    code = run_gui([], services=_services())
    safety.stop()
    assert "error" not in seen, seen["error"]
    assert seen["visible"] == 1
    assert fired["safety"] is False, "창을 닫았는데 프로그램이 끝나지 않아 안전 타이머가 울렸다"
    assert code == 0
    assert "종료: 코드 0" in _log_text(env)


def test_run_gui_makes_the_last_closed_window_quit_the_program(env: Any) -> None:
    env.app.setQuitOnLastWindowClosed(False)
    seen = _quit_after_look(env.app, lambda: {"quit": QApplication.quitOnLastWindowClosed()})
    assert run_gui([], services=_services()) == 0
    assert _finish(seen)["quit"] is True


def test_an_oserror_is_logged_without_the_path(env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = env.root / "data" / "app.db"

    def denied(path: Path) -> object:
        raise PermissionError(13, "Permission denied", str(secret))

    monkeypatch.setattr(application, "open_database", denied)
    with pytest.raises(PermissionError):
        run_gui([])
    log = _log_text(env)
    assert f"오류: PermissionError: Permission denied ({ROOT_TOKEN}" in log
    for form in (str(env.data_home), env.data_home.as_posix(), repr(str(secret))[1:-1]):
        assert form not in log


def _quit_when(
    app: QApplication, done: Callable[[], bool], limit_ms: int = 10_000
) -> dict[str, Any]:
    """`done()`이 참이 될 때까지 이벤트 루프를 돌리다 끝낸다(안전 타이머 `limit_ms`)."""
    held: dict[str, Any] = {}
    safety = QTimer()
    safety.setSingleShot(True)
    safety.timeout.connect(app.quit)
    safety.start(limit_ms)
    poll = QTimer()
    poll.setInterval(5)

    def check() -> None:
        try:
            if done():
                app.quit()
        except Exception as exc:
            held["error"] = repr(exc)
            app.quit()

    poll.timeout.connect(check)
    poll.start()
    held.update(safety=safety, poll=poll)
    return held


def _release(held: dict[str, Any]) -> None:
    held.pop("safety").stop()
    held.pop("poll").stop()
    assert "error" not in held, held["error"]


def _record_updates(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    """창이 받는 값을 (받은 스레드의 이름, 값)으로 적는다."""
    import threading

    updates: list[tuple[str, object]] = []
    real = ui.create_main_window

    def create(context: object) -> object:
        window = real(context)
        original = window.apply

        def apply(update: object) -> None:
            updates.append((threading.current_thread().name, update))
            original(update)

        window.apply = apply
        return window

    monkeypatch.setattr(ui, "create_main_window", create)
    return updates


def test_the_first_run_asks_for_the_market_list_and_applies_on_the_main_thread(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    updates = _record_updates(monkeypatch)
    quote = FakeQuoteSource()
    held = _quit_when(
        env.app,
        lambda: any(isinstance(u, MarketsView) and u.state is LoadState.READY for _, u in updates),
    )  # 받는 중(`LOADING`)이 아니라 받음(`READY`)까지 기다린다
    assert run_gui([], services=_services(quote)) == 0
    _release(held)
    # 보내기 스레드의 `WebhookView`(분류마다 하나)와 알림센터의 `AlertsView`는 따로 센다.
    others = [u for _, u in updates if type(u).__name__ not in ("WebhookView", "AlertsView")]
    kinds = [type(update).__name__ for update in others]
    assert kinds == ["SettingsView", "MarketsView", "MarketsView"]  # 받는 중, 받음
    assert [state.state for state in others[1:]] == [LoadState.LOADING, LoadState.READY]
    assert sum(1 for _, u in updates if type(u).__name__ == "WebhookView") == 6
    first = others[0]
    assert isinstance(first, SettingsView)
    assert (first.state, first.first_setup_done, first.coins) == (LoadState.READY, False, ())
    assert {name for name, _ in updates} == {threading.main_thread().name}
    assert len(quote.market_requests()) == 1
    assert quote.candle_requests() == []  # 처음 설정 전에는 봉을 받지 않는다


def test_an_unreadable_settings_row_gives_a_failed_settings_view_and_no_requests(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = resolve_app_paths()
    paths.ensure()
    conn = open_database(paths.database_file)
    try:
        migrate(conn)
        conn.execute("INSERT INTO settings VALUES ('coins', '이것은 JSON이 아니다')")
        conn.commit()
    finally:
        conn.close()
    updates = _record_updates(monkeypatch)
    quote = FakeQuoteSource()
    held = _quit_when(env.app, lambda: any(isinstance(u, SettingsView) for _, u in updates))
    assert run_gui([], services=_services(quote)) == 0
    _release(held)
    first = next(u for _, u in updates if isinstance(u, SettingsView))
    assert isinstance(first, SettingsView)
    assert first.state is LoadState.FAILED
    assert [u for _, u in updates if type(u).__name__ == "CoinView"] == []
    assert quote.requests == []
    reopened = open_database(paths.database_file)
    try:
        rows = reopened.execute("SELECT code, category, stage, error_name, coin FROM alerts")
        assert rows.fetchall() == [("A-30", "other", "켜기", "DB 오류", None)]
        # 저장된 설정은 덮어쓰지 않는다.
        assert reopened.execute("SELECT value FROM settings").fetchall() == [
            ("이것은 JSON이 아니다",)
        ]
    finally:
        reopened.close()


def _jsonl(env: Any) -> list[dict[str, Any]]:
    import json

    path = env.root / "logs" / "startup-errors.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_a_database_that_cannot_be_opened_is_written_to_the_startup_file_and_moved_next_time(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    real_open = application.open_database

    def refuse(path: Path) -> object:
        raise StorageError(f"cannot open {path}")

    monkeypatch.setattr(application, "open_database", refuse)
    with pytest.raises(StorageError):
        run_gui(
            [],
            services=Services(
                quote=FakeQuoteSource(), webhook=FakeDiscord(), vault=FakeVault(), clock=clock
            ),
        )
    (line,) = _jsonl(env)
    assert line["kind"] == "startup"
    assert (line["code"], line["category"], line["stage"], line["error_name"]) == (
        "A-29", "error", "켜기", "DB 오류",
    )  # fmt: skip
    assert line["created_utc"] == "2026-01-05T03:00:00Z"
    assert str(env.data_home) not in line["content"]
    assert "종료: 코드 1" in _log_text(env)

    # 정상인 두 번째 실행: 알림으로 옮기고 파일을 비운다(디스코드로 보내지 않는다).
    monkeypatch.setattr(application, "open_database", real_open)
    clock.advance(86_400)
    held = _quit_when(
        env.app,
        lambda: (env.root / "logs" / "startup-errors.jsonl").read_text(encoding="utf-8") == "",
    )
    services = Services(
        quote=FakeQuoteSource(), webhook=FakeDiscord(), vault=FakeVault(), clock=clock
    )
    assert run_gui([], services=services) == 0
    _release(held)
    assert _jsonl(env) == []
    conn = open_database(resolve_app_paths().database_file)
    try:
        rows = conn.execute(
            "SELECT created_utc, code, discord_state, seen, stage, error_name FROM alerts"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("2026-01-05T03:00:00Z", "A-29", "local_only", 0, "켜기", "DB 오류")]


def test_the_schema_is_raised_on_the_way_in(env: Any) -> None:
    held = _quit_when(env.app, lambda: True)
    assert run_gui([], services=_services()) == 0
    _release(held)
    conn = open_database(resolve_app_paths().database_file)
    try:
        assert conn.execute("PRAGMA user_version").fetchone() == (1,)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"settings", "candles", "alerts"} <= names


def test_a_window_that_cannot_be_made_is_an_a30_startup_line(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_context: object) -> object:
        raise RuntimeError("창을 만들지 못했다")

    monkeypatch.setattr(ui, "create_main_window", broken)
    with pytest.raises(RuntimeError):
        run_gui([], services=_services())
    (line,) = _jsonl(env)
    assert (line["code"], line["category"], line["error_name"]) == ("A-30", "other", "RuntimeError")
    assert line["stage"] == "켜기"


def test_an_unwritable_startup_file_leaves_only_the_log(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(path: Path) -> object:
        raise StorageError("열지 못했다")

    paths = resolve_app_paths()
    paths.ensure()
    (paths.logs_dir / "startup-errors.jsonl").mkdir()  # 쓸 수 없는 자리
    monkeypatch.setattr(application, "open_database", refuse)
    with pytest.raises(StorageError):
        run_gui([], services=_services())
    assert "오류: StorageError" in _log_text(env)
    assert entry.main([], services=_services()) == 1


def test_the_log_of_a_failed_start_redacts_webhook_text(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://" + "discord.com/api/webhooks/" + "1" * 18 + "/" + "Tok_" * 16

    def refuse(path: Path) -> object:
        raise StorageError(f"열지 못했다 {url}")

    monkeypatch.setattr(application, "open_database", refuse)
    with pytest.raises(StorageError):
        run_gui([], services=_services())
    log = _log_text(env)
    assert "Tok_" not in log
    assert "(가려서 보임)" in log
    assert "Tok_" not in (env.root / "logs" / "startup-errors.jsonl").read_text(encoding="utf-8")


def test_the_database_is_raised_before_the_window_and_the_thread_starts_after_it_is_shown(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from coin_report_trader.app.data_thread import DATA_THREAD_NAME

    def data_threads() -> list[str]:
        return [t.name for t in threading.enumerate() if t.name == DATA_THREAD_NAME]

    def user_version() -> int:
        conn = open_database(resolve_app_paths().database_file)
        try:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])
        finally:
            conn.close()

    seen: dict[str, Any] = {}
    real = ui.create_main_window

    def create(context: object) -> object:
        seen["create"] = (user_version(), data_threads())
        window = real(context)
        original = window.show

        def show() -> None:
            seen["show"] = data_threads()  # 창을 띄우기 전에는 자료 스레드가 없다
            original()

        window.show = show
        return window

    monkeypatch.setattr(ui, "create_main_window", create)
    updates = _record_updates(monkeypatch)  # `create`를 한 번 더 감싼다
    held = _quit_when(env.app, lambda: bool(updates))
    assert run_gui([], services=_services()) == 0
    _release(held)
    assert seen["create"] == (1, [])  # 스키마 번호는 1이고 스레드는 아직 없다
    assert seen["show"] == []
    assert updates  # 창을 띄운 뒤에는 자료 스레드가 첫 값을 보냈다


@pytest.mark.parametrize("target", ["DataWorker", "UiBridge"])
def test_a_failure_while_making_the_window_pieces_leaves_a_startup_line(
    env: Any, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    from coin_report_trader.app import bridge, data_thread

    def broken(*args: object, **kwargs: object) -> object:
        raise RuntimeError(f"{target}를 만들지 못했다")

    monkeypatch.setattr(data_thread if target == "DataWorker" else bridge, target, broken)
    with pytest.raises(RuntimeError, match=target):
        run_gui([], services=_services())
    (line,) = _jsonl(env)
    assert (line["kind"], line["code"], line["category"]) == ("startup", "A-30", "other")
    assert (line["stage"], line["error_name"]) == ("켜기", "RuntimeError")
    assert f"{target}를 만들지 못했다" in line["content"]
    assert "오류: RuntimeError" in _log_text(env)
    assert "종료: 코드 1" in _log_text(env)


def test_a_failure_while_making_the_application_leaves_a_startup_line(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> object:
        raise RuntimeError("QApplication을 만들지 못했다")

    real = QApplication.instance
    monkeypatch.setattr(QApplication, "instance", staticmethod(broken))
    with pytest.raises(RuntimeError, match="QApplication"):
        run_gui([], services=_services())
    monkeypatch.setattr(QApplication, "instance", real)
    (line,) = _jsonl(env)
    assert (line["code"], line["error_name"]) == ("A-30", "RuntimeError")


def test_a_migrate_failure_and_a_newer_schema_leave_a_startup_line_of_the_database(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(conn: object) -> int:
        raise StorageError("DB 스키마 올리기에 실패했습니다: OperationalError")

    real = application.migrate
    monkeypatch.setattr(application, "migrate", refuse)
    with pytest.raises(StorageError):
        run_gui([], services=_services())
    (line,) = _jsonl(env)
    assert (line["code"], line["category"], line["error_name"]) == ("A-29", "error", "DB 오류")
    assert "스키마 올리기" in line["content"]
    monkeypatch.setattr(application, "migrate", real)
    _make_newer_database(env)
    with pytest.raises(StorageError, match="스키마 번호"):
        run_gui([], services=_services())
    first, second = _jsonl(env)
    assert first["content"] != second["content"]
    assert (second["code"], second["stage"], second["error_name"]) == ("A-29", "켜기", "DB 오류")
    assert "스키마 번호" in second["content"]


def _webhook_url() -> str:
    return "https://" + "discord.com/api/webhooks/" + "1" * 18 + "/" + "a" * 64


def test_the_actions_go_to_the_thread_that_owns_them() -> None:
    from coin_report_trader.app.actions import AppActions
    from coin_report_trader.core import AlertCategory

    calls: list[tuple[str, str]] = []

    class Spy:
        def __init__(self, name: str) -> None:
            self._name = name

        def __getattr__(self, method: str) -> Callable[..., None]:
            def record(*args: object, **kwargs: object) -> None:
                calls.append((self._name, method))

            return record

    actions = AppActions(Spy("data"), Spy("send"))  # type: ignore[arg-type]
    category = AlertCategory.ERROR
    actions.refresh_markets()
    actions.save_settings(("KRW-AAA",), 7, first_setup=False)
    actions.retry_candles()
    actions.mark_seen((1,))
    actions.delete_alert(1)
    actions.clear_alerts(category)
    actions.save_webhook(category, "x")
    actions.delete_webhook(category)
    actions.send_test(category)
    assert calls == [
        ("data", "refresh_markets"), ("data", "save_settings"), ("data", "retry_candles"),
        ("data", "mark_seen"), ("data", "delete_alert"), ("data", "clear_alerts"),
        ("send", "save_webhook"), ("send", "delete_webhook"), ("send", "send_test"),
    ]  # fmt: skip


def test_a_send_thread_left_from_an_earlier_run_touches_nothing_of_a_later_run(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from coin_report_trader.core import AlertCategory
    from coin_report_trader.core.texts import ERROR_NETWORK
    from coin_report_trader.storage.settings import write_settings
    from coin_report_trader.vault import webhook_secret_name

    monkeypatch.setattr(application, "JOIN_SECONDS", 0.05)  # 막힌 보내기를 오래 기다리지 않는다
    paths = resolve_app_paths()
    paths.ensure()
    conn = open_database(paths.database_file)
    try:
        migrate(conn)
        write_settings(
            conn,
            SettingsView(
                ("KRW-AAA",), 30, True, candle_unit=CandleUnit.MINUTE_60, candle_count=100
            ),
        )
    finally:
        conn.close()
    discord = FakeDiscord()
    discord.hold()
    vault = FakeVault({webhook_secret_name(AlertCategory.ERROR): _webhook_url()})
    quote = FakeQuoteSource()
    quote.script(QuoteError(ERROR_NETWORK), kind="candles", market="KRW-AAA")
    updates = _record_updates(monkeypatch)
    services = Services(quote=quote, webhook=discord, vault=vault, clock=FakeClock())
    held = _quit_when(env.app, discord.entered.is_set)
    assert run_gui([], services=services) == 0  # 보내기가 막힌 채 끝낸다: 기다리지 않는다
    _release(held)
    stale = [t for t in threading.enumerate() if t.name == "crt-send"]
    assert stale  # 막힌 보내기 스레드가 아직 남아 있다
    shown, reads = len(updates), len(vault.calls)
    discord.release()
    for thread in stale:
        thread.join(10.0)
        assert not thread.is_alive()
    assert len(updates) == shown  # 닫힌 다리와 창에 아무것도 보내지 않는다
    assert len(vault.calls) == reads  # 보관소도 더 읽지 않는다
    assert len(discord.sent) == 1
    reopened = open_database(paths.database_file)
    try:
        earlier = dict(reopened.execute("SELECT id, discord_state FROM alerts"))
    finally:
        reopened.close()
    assert earlier
    assert set(earlier.values()) == {"not_sent"}  # 멈춘 뒤의 결과는 버렸다(다음에 켤 때 바뀐다)
    later = Services(
        quote=FakeQuoteSource(), webhook=FakeDiscord(), vault=FakeVault(), clock=FakeClock()
    )
    before = len(updates)
    held = _quit_when(
        env.app, lambda: any(type(u).__name__ == "AlertsView" for _, u in updates[before:])
    )
    assert run_gui([], services=later) == 0
    _release(held)
    assert len(discord.sent) == 1  # 앞 실행의 가짜에는 더 쌓이지 않는다
    assert len(vault.calls) == reads
    reopened = open_database(paths.database_file)
    try:
        later_states = dict(reopened.execute("SELECT id, discord_state FROM alerts"))
    finally:
        reopened.close()
    # 켤 때 차례 2: 뒤 실행의 보관소에는 주소가 없다. 뒤 실행이 새로 만든 알림은 그 실행이 끝나는
    # 때에 따라 아직 결과가 없을 수 있으므로 앞 실행에서 만든 알림만 본다.
    assert {later_states[alert_id] for alert_id in earlier} == {"no_address"}
