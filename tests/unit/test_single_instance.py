"""한 번 더 켜기(M1 명세 3.8.1): 이름, 잠금, 서버, 먼저 켠 쪽에 알리기, `run_gui`의 차례.

시험끼리 막지 않게 서버 이름과 잠금 파일의 자리를 시험마다 `start`의 인자로 바꾼다.
"""

import hashlib
import os
import subprocess
import sys
import textwrap
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fakes.clock import FakeClock
from fakes.discord import FakeDiscord
from fakes.quote import FakeQuoteSource
from fakes.vault import FakeVault
from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtWidgets import QApplication, QWidget

from coin_report_trader.app import application, run_gui, single_instance
from coin_report_trader.app.services import Services
from coin_report_trader.config.paths import AppPathError


class FakeWindow(QWidget):
    """앞으로 내는 부름만 적는 창."""

    def __init__(self, *, minimized: bool = False) -> None:
        super().__init__()
        self.calls: list[str] = []
        self._minimized = minimized

    def isMinimized(self) -> bool:
        return self._minimized

    def showNormal(self) -> None:
        self.calls.append("showNormal")
        self._minimized = False

    def raise_(self) -> None:
        self.calls.append("raise_")

    def activateWindow(self) -> None:
        self.calls.append("activateWindow")


def pump(condition: Callable[[], bool], seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    QCoreApplication.processEvents()
    return condition()


@pytest.fixture
def started(qapp: QApplication) -> Iterator[list[single_instance.Started]]:
    """시험이 연 것을 끝에서 닫는다(잠금과 서버가 남지 않게)."""
    opened: list[single_instance.Started] = []
    yield opened
    for item in opened:
        item.close()


CHILD_START = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from PySide6.QtWidgets import QApplication
    from coin_report_trader.app import single_instance
    app = QApplication([])
    result = single_instance.start(Path(sys.argv[1]), name=sys.argv[2])
    print("later" if result.later else "first", flush=True)
    result.close()
    """
)
CHILD_RUN_GUI = textwrap.dedent(
    """
    import sys
    from coin_report_trader.app import run_gui
    sys.exit(run_gui([]))
    """
)


def spawn(code: str, *args: str, **env: str) -> subprocess.Popen[str]:
    """다른 프로세스의 두 번째 켜기. 같은 프로세스 안에서는 서버와 붙는 쪽이 한 이벤트 루프를
    나눠 쓰므로 Windows의 이름 있는 파이프에서 흐름이 막힌다."""
    child_env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", **env}
    return subprocess.Popen(
        [sys.executable, "-c", code, *args], stdout=subprocess.PIPE, text=True, env=child_env
    )


def finish(child: subprocess.Popen[str], seconds: float = 30.0) -> str:
    """자식이 끝나기를 기다리며 이벤트를 돌린다(먼저 켠 쪽의 서버가 일한다). 첫 줄을 돌려준다."""
    try:
        assert pump(lambda: child.poll() is not None, seconds), "자식이 끝나지 않았다"
        assert child.stdout is not None
        return child.stdout.read().strip()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(10)


def unique_name() -> str:
    return f"crt-test-{uuid.uuid4().hex[:12]}"


def begin(
    started: list[single_instance.Started], root: Path, **kwargs: Any
) -> single_instance.Started:
    kwargs.setdefault("name", unique_name())
    result = single_instance.start(root, **kwargs)
    started.append(result)
    return result


def test_the_name_is_the_prefix_and_sixteen_hex_digits_of_the_root_path(tmp_path: Path) -> None:
    root = tmp_path / "coin-report-trader"
    digest = hashlib.sha256(str(root.absolute()).encode("utf-8")).hexdigest()
    assert single_instance.server_name(root) == "coin-report-trader-" + digest[:16]
    name = single_instance.server_name(root)
    assert len(name) == len("coin-report-trader-") + 16
    assert name == name.lower()
    assert single_instance.server_name(tmp_path / "other") != name
    assert "root" not in name
    assert str(tmp_path) not in name
    assert single_instance.lock_path(root) == root / "instance.lock"


def test_the_first_start_takes_the_lock_makes_only_the_root_and_the_lock_file(
    started: list[single_instance.Started], tmp_path: Path
) -> None:
    root = tmp_path / "data-root"
    first = begin(started, root)
    assert (first.later, first.note) == (False, None)
    assert sorted(path.name for path in root.iterdir()) == ["instance.lock"]  # data, logs는 없다


def test_the_server_is_opened_for_the_user_only(
    started: list[single_instance.Started], tmp_path: Path
) -> None:
    from PySide6.QtNetwork import QLocalServer

    first = begin(started, tmp_path / "data-root")
    server = first._server
    assert server is not None
    assert server.socketOptions() == QLocalServer.SocketOption.UserAccessOption
    if sys.platform != "win32":  # 소켓 파일을 그 사용자만 쓴다(같은 PC의 다른 사용자가 못 붙는다)
        assert (Path(server.fullServerName()).stat().st_mode & 0o777) == 0o700


def test_a_second_start_asks_the_first_to_show_the_window_and_makes_nothing(
    started: list[single_instance.Started], tmp_path: Path
) -> None:
    root = tmp_path / "data-root"
    name = unique_name()
    first = begin(started, root, name=name)
    window = FakeWindow(minimized=True)
    first.set_window(window)
    assert finish(spawn(CHILD_START, str(root), name)) == "later"
    assert pump(lambda: "activateWindow" in window.calls)
    assert window.calls == ["showNormal", "raise_", "activateWindow"]  # 최소화는 되돌린다
    assert sorted(path.name for path in root.iterdir()) == ["instance.lock"]


def test_a_window_that_is_not_minimized_is_only_raised_and_activated(
    started: list[single_instance.Started], tmp_path: Path
) -> None:
    root = tmp_path / "data-root"
    name = unique_name()
    first = begin(started, root, name=name)
    window = FakeWindow()
    first.set_window(window)
    assert finish(spawn(CHILD_START, str(root), name)) == "later"
    assert pump(lambda: "activateWindow" in window.calls)
    assert window.calls == ["raise_", "activateWindow"]


def test_other_text_is_ignored_and_show_still_works_after_it(
    started: list[single_instance.Started], tmp_path: Path
) -> None:
    root = tmp_path / "data-root"
    name = unique_name()
    first = begin(started, root, name=name)
    window = FakeWindow()
    first.set_window(window)
    socket = QLocalSocket()
    socket.connectToServer(name)
    assert socket.waitForConnected(2000)
    socket.write(b"hello\nshowx\n")
    pump(lambda: False, 0.5)  # 이벤트를 돌려야 쓴 글이 간다
    assert window.calls == []
    socket.write(b"show\n")
    assert pump(lambda: "activateWindow" in window.calls)
    socket.abort()


def test_a_show_that_comes_before_the_window_exists_changes_nothing(
    started: list[single_instance.Started], tmp_path: Path
) -> None:
    root = tmp_path / "data-root"
    name = unique_name()
    begin(started, root, name=name)
    assert finish(spawn(CHILD_START, str(root), name)) == "later"
    pump(lambda: False, 0.3)  # 창이 없으므로 아무 일도 없다(예외도 없다)


def test_closing_frees_the_lock_and_the_name_and_may_be_repeated(
    started: list[single_instance.Started], tmp_path: Path
) -> None:
    root = tmp_path / "data-root"
    name = unique_name()
    first = begin(started, root, name=name)
    first.close()
    first.close()
    assert not (root / "instance.lock").exists()  # 객체가 남아 있어도 잠금은 바로 풀린다
    again = begin(started, root, name=name)
    assert (again.later, again.note) == (False, None)


def test_a_lock_that_nobody_serves_ends_the_new_start_after_the_wait(
    started: list[single_instance.Started],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PySide6.QtCore import QLockFile

    monkeypatch.setattr(single_instance, "CONNECT_WAIT_SECONDS", 0.3)
    root = tmp_path / "data-root"
    root.mkdir()
    holder = QLockFile(str(root / "instance.lock"))
    assert holder.tryLock(0)
    try:
        before = time.monotonic()
        second = begin(started, root)
        assert second.later is True  # 잠금을 쥔 쪽이 있으므로 창을 만들지 않고 끝낸다
        assert time.monotonic() - before >= 0.3
        assert sorted(path.name for path in root.iterdir()) == ["instance.lock"]
    finally:
        holder.unlock()


def test_a_lock_left_by_a_dead_process_is_taken_over(
    started: list[single_instance.Started], tmp_path: Path
) -> None:
    root = tmp_path / "data-root"
    root.mkdir()
    code = textwrap.dedent(
        """
        import sys, time
        from PySide6.QtCore import QLockFile
        lock = QLockFile(sys.argv[1])
        assert lock.tryLock(0)
        print("locked", flush=True)
        time.sleep(60)
        """
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(root / "instance.lock")],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"
        child.kill()  # 정리하지 못하고 죽었다
        child.wait(10)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(10)
    assert (root / "instance.lock").exists()
    taken = begin(started, root)
    assert (taken.later, taken.note) == (False, None)


@pytest.mark.skipif(sys.platform == "win32", reason="유닉스 소켓 파일은 Windows에 없다")
def test_a_socket_file_left_by_a_crashed_run_is_removed_before_listening(
    started: list[single_instance.Started], tmp_path: Path
) -> None:
    from PySide6.QtCore import QDir

    name = unique_name()
    stale = Path(QDir.tempPath()) / name
    stale.write_text("", encoding="utf-8")  # 비정상으로 끝나 남은 이름
    try:
        first = begin(started, tmp_path / "data-root", name=name)
        assert (first.later, first.note) == (False, None)
    finally:
        for item in started:
            item.close()
        stale.unlink(missing_ok=True)


@pytest.mark.skipif(sys.platform == "win32", reason="이름 있는 파이프는 같은 이름으로 열 수 있다")
def test_a_server_that_cannot_be_opened_is_noted_and_the_start_goes_on(
    started: list[single_instance.Started], tmp_path: Path
) -> None:
    root = tmp_path / "data-root"
    result = begin(started, root, name=str(tmp_path / "no-such-folder" / "socket"))
    assert result.later is False
    assert result.note is not None
    assert "서버를 열지 못했습니다" in result.note
    assert str(tmp_path) not in result.note  # 경로는 적지 않는다


def test_an_unusable_root_raises_for_the_caller_to_skip_the_step(tmp_path: Path) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(OSError):  # noqa: PT011
        single_instance.start(blocker / "root", name=unique_name())


def test_the_foreground_right_is_passed_only_on_windows_before_show_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ctypes

    events: list[str] = []

    class User32:
        def AllowSetForegroundWindow(self, who: Any) -> int:
            events.append(f"allow:{who.value}")
            return 1

    class Windll:
        user32 = User32()

    class FakeSocket:
        def connectToServer(self, name: str) -> None:
            events.append("connect")

        def waitForConnected(self, ms: int) -> bool:
            return True

        def write(self, data: bytes) -> int:
            events.append(f"write:{data!r}")
            return len(data)

        def waitForBytesWritten(self, ms: int) -> bool:
            events.append(f"wait:{ms}")
            return True

        def disconnectFromServer(self) -> None:
            events.append("disconnect")

        def abort(self) -> None:
            events.append("abort")

    monkeypatch.setattr(ctypes, "windll", Windll, raising=False)
    monkeypatch.setattr(single_instance, "QLocalSocket", FakeSocket)
    monkeypatch.setattr(single_instance.sys, "platform", "linux")
    assert single_instance.notify("name") is True
    assert events == [
        "connect",
        "write:b'show\\n'",
        "wait:500",
        "disconnect",
    ]  # 권한은 넘기지 않는다
    events.clear()
    monkeypatch.setattr(single_instance.sys, "platform", "win32")
    assert single_instance.notify("name") is True
    assert events == [
        "connect", "allow:4294967295", "write:b'show\\n'", "wait:500", "disconnect",
    ]  # fmt: skip  # 먼저 켠 쪽에 권한(ASFW_ANY)을 넘긴 뒤에 보낸다


# --- run_gui의 차례 ------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, qapp: QApplication) -> Any:
    data_home = tmp_path / "share"
    data_home.mkdir()
    key = "LOCALAPPDATA" if sys.platform == "win32" else "XDG_DATA_HOME"
    monkeypatch.setenv(key, str(data_home))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return data_home / "coin-report-trader"


def services() -> Services:
    return Services(
        quote=FakeQuoteSource(), webhook=FakeDiscord(), vault=FakeVault(), clock=FakeClock()
    )


def test_run_gui_ends_with_zero_and_makes_no_window_log_or_database_when_already_running(
    env: Path, started: list[single_instance.Started]
) -> None:
    first = begin(started, env, name=single_instance.server_name(env))
    window = FakeWindow()
    first.set_window(window)
    child = spawn(CHILD_RUN_GUI, XDG_DATA_HOME=str(env.parent), LOCALAPPDATA=str(env.parent))
    finish(child)
    assert child.returncode == 0
    assert pump(lambda: "activateWindow" in window.calls)
    assert sorted(path.name for path in env.iterdir()) == ["instance.lock"]  # 창, 로그, DB가 없다


def test_run_gui_holds_the_lock_while_it_runs_and_frees_it_at_the_end(
    env: Path, started: list[single_instance.Started]
) -> None:
    seen: dict[str, Any] = {}

    def look() -> None:
        try:
            probe = single_instance.start(env, name=single_instance.server_name(env))
            started.append(probe)
            seen["later"] = probe.later
            windows = [w for w in QApplication.topLevelWidgets() if w.objectName() == "mainWindow"]
            seen["windows"] = len(windows)
        finally:
            QApplication.quit()

    safety = QTimer()
    safety.setSingleShot(True)
    safety.timeout.connect(QApplication.quit)
    safety.start(10_000)
    QTimer.singleShot(0, look)
    assert run_gui([], services=services()) == 0
    safety.stop()
    assert seen == {"later": True, "windows": 1}  # 켜져 있는 동안 한 번 더 켜면 막힌다
    again = begin(started, env, name=single_instance.server_name(env))
    assert (again.later, again.note) == (False, None)  # 끝난 뒤에는 잠금과 이름이 풀려 있다


def test_a_show_during_a_run_brings_the_real_window_to_the_front(
    env: Path, started: list[single_instance.Started], monkeypatch: pytest.MonkeyPatch
) -> None:
    from coin_report_trader.ui import create_main_window

    made: list[QWidget] = []
    raised: list[str] = []

    def create(context: object) -> Any:
        window = create_main_window(context)  # type: ignore[arg-type]
        made.append(window)
        original = window.raise_
        window.raise_ = lambda: (raised.append("raise_"), original())[1]  # type: ignore[method-assign]
        return window

    from coin_report_trader import ui

    monkeypatch.setattr(ui, "create_main_window", create)

    children: list[subprocess.Popen[str]] = []

    def ask() -> None:
        children.append(spawn(CHILD_START, str(env), single_instance.server_name(env)))

    def quit_when_raised() -> None:
        if raised:
            QApplication.quit()
        else:
            QTimer.singleShot(20, quit_when_raised)

    safety = QTimer()
    safety.setSingleShot(True)
    safety.timeout.connect(QApplication.quit)
    safety.start(10_000)
    QTimer.singleShot(0, ask)
    QTimer.singleShot(50, quit_when_raised)
    assert run_gui([], services=services()) == 0
    safety.stop()
    assert raised == ["raise_"]
    assert finish(children[0]) == "later"


def test_a_note_of_the_step_is_logged_and_the_start_goes_on(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def noted(root: Path) -> single_instance.Started:
        return single_instance.Started(note="서버를 열지 못했습니다(시험)")

    monkeypatch.setattr(single_instance, "start", noted)
    QTimer.singleShot(0, QApplication.quit)
    assert run_gui([], services=services()) == 0
    log = (env / "logs" / "app.log").read_text(encoding="utf-8")
    assert "서버를 열지 못했습니다(시험)" in log
    assert "종료: 코드 0" in log


def test_an_exception_of_the_step_is_noted_and_the_start_goes_on(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(root: Path) -> object:
        raise RuntimeError("시험")

    monkeypatch.setattr(single_instance, "start", boom)
    QTimer.singleShot(0, QApplication.quit)
    assert run_gui([], services=services()) == 0
    log = (env / "logs" / "app.log").read_text(encoding="utf-8")
    assert "건너뜁니다(RuntimeError)" in log


def test_the_step_is_skipped_when_the_data_location_cannot_be_decided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse() -> object:
        raise AppPathError("정하지 못함")

    def never(root: Path) -> object:
        raise AssertionError("이 차례는 건너뛴다")

    monkeypatch.setattr(application, "resolve_app_paths", refuse)
    monkeypatch.setattr(single_instance, "start", never)
    with pytest.raises(AppPathError):
        run_gui([], services=services())  # 켜는 순서 1이 지금처럼 그 오류를 낸다


def test_the_self_test_does_not_take_part_in_the_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = Path(application.__file__).with_name("self_test.py").read_text(encoding="utf-8")
    assert "single_instance" not in source  # 잠금도 서버도 건드리지 않는다(3.11)
