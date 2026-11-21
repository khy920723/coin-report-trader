"""한 번 더 켜기(M1 명세 3.8.1): 프로그램이 이미 켜져 있으면 새로 켠 것은 바로 끝나고 켜져 있는
창이 앞으로 나온다.

- 이름: 서버 이름은 `coin-report-trader-` 뒤에 자료 폴더 `root`의 절대 경로 글을 UTF-8로 sha256한
  값의 앞 16자(소문자 16진수)다. 잠금 파일은 `root` 바로 아래의 `instance.lock`이다.
- 잠금(`QLockFile`)은 그 실행이 끝날 때까지 쥔다. 잠금을 쥔 쪽만 서버(`QLocalServer`)를 연다.
  잠금을 잡지 못하면 그 이름의 서버에 붙어 `show` 한 줄을 보내고 끝낸다.
- 이 길은 `root`와 잠금 파일 말고는 만들거나 쓰지 않는다(로그, DB, `startup-errors.jsonl`을
  건드리지 않는다).
- 시험은 `start`의 `name`과 `lock_file` 인자로 서로 막지 않게 한다. exe의 명령줄 옵션이나 환경
  변수는 두지 않는다. Windows가 아닌 곳에서는 앞으로 낼 권한을 넘기지 않는다.
"""

import hashlib
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from PySide6.QtCore import QLockFile
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QWidget

NAME_PREFIX: Final = "coin-report-trader-"
LOCK_FILE_NAME: Final = "instance.lock"
SHOW_LINE: Final = b"show"
"""앞으로 내라는 한 줄. 그 밖의 글은 무시한다."""
CONNECT_RETRY_SECONDS: Final = 0.1
"""먼저 켠 쪽이 서버를 열기 전일 수 있어 붙기를 다시 하는 간격."""
CONNECT_WAIT_SECONDS: Final = 3.0
"""붙기를 다시 하는 가장 긴 시간. 지나도 못 붙으면 창을 만들지 않고 끝낸다."""
WRITE_WAIT_MS: Final = 500
MAX_LINE_BYTES: Final = 64
_ASFW_ANY: Final = 0xFFFFFFFF


def server_name(root: Path) -> str:
    """`root`의 절대 경로 글로 정한 서버 이름(사용자 이름이 드러나지 않는다)."""
    digest = hashlib.sha256(str(root.absolute()).encode("utf-8")).hexdigest()
    return NAME_PREFIX + digest[:16]


def lock_path(root: Path) -> Path:
    return root / LOCK_FILE_NAME


@dataclass
class Started:
    """`start`의 결과. `later`가 참이면 먼저 켠 쪽에 알렸으니 창을 만들지 않고 0으로 끝낸다."""

    later: bool = False
    note: str | None = None
    """그대로 켜는 까닭(서버를 열지 못함 같은 것). 켜는 순서 1에서 로그를 준비한 뒤 남긴다."""
    _lock: QLockFile | None = None
    _server: QLocalServer | None = None
    _window: QWidget | None = None

    def set_window(self, window: QWidget) -> None:
        """서버에 연결이 와서 `show`를 받으면 앞으로 낼 창."""
        self._window = window

    def close(self) -> None:
        """서버를 닫고 잠금을 푼다. 여러 번 불러도 된다."""
        server, self._server = self._server, None
        if server is not None:
            server.close()
            server.deleteLater()
        lock, self._lock = self._lock, None
        if lock is not None:
            lock.unlock()

    def bring_to_front(self) -> None:
        """주 스레드에서 창을 앞으로 낸다: 최소화되어 있으면 되돌리고 앞으로 올리고 활성화한다."""
        window = self._window
        if window is None:
            return
        if window.isMinimized():
            window.showNormal()
        window.raise_()
        window.activateWindow()

    def _on_connection(self) -> None:
        server = self._server
        while server is not None and server.hasPendingConnections():
            socket = server.nextPendingConnection()
            if socket is None:
                return
            socket.disconnected.connect(socket.deleteLater)
            socket.readyRead.connect(lambda s=socket: self._read(s))
            if socket.bytesAvailable():
                self._read(socket)

    def _read(self, socket: QLocalSocket) -> None:
        while socket.canReadLine():
            line = bytes(socket.readLine(MAX_LINE_BYTES).data()).strip()
            if line == SHOW_LINE:
                self.bring_to_front()
            # 그 밖의 글은 무시한다.


def start(root: Path, *, name: str | None = None, lock_file: Path | None = None) -> Started:
    """잠금을 잡아 서버를 열거나, 먼저 켠 쪽에 알린다(3.8.1의 차례 1, 2, 3).

    `QApplication`을 만든 뒤에 부른다. `name`과 `lock_file`은 시험이 바꾸는 자리다. 자료 폴더를
    만들지 못하면 `OSError`를 낸다(`run_gui`가 이 차례를 건너뛰고 켜는 순서 1로 간다).
    """
    name = name or server_name(root)
    lock_file = lock_file or lock_path(root)
    lock_file.parent.mkdir(parents=True, exist_ok=True)  # 못 만들면 `OSError`(호출한 쪽이 다룬다)
    lock = QLockFile(str(lock_file))
    if lock.tryLock(0):
        return _serve(lock, name)
    notify(name)
    return Started(later=True)


def _serve(lock: QLockFile, name: str) -> Started:
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    QLocalServer.removeServer(name)
    started = Started(_lock=lock, _server=server)
    if not server.listen(name):
        started.note = f"한 번 더 켜기의 서버를 열지 못했습니다({server.serverError().name})"
        started._server = None
        server.deleteLater()
        return started
    server.newConnection.connect(started._on_connection)
    return started


def notify(name: str) -> bool:
    """그 이름의 서버에 `show` 한 줄을 보낸다. 100밀리초마다 3초까지 다시 붙어 본다.

    붙으면 Windows에서는 앞으로 낼 권한을 먼저 켠 쪽에 넘기고(사용자가 방금 켠 이 프로세스가
    가진 권한), 쓰기가 끝나기를 500밀리초까지 기다린다. 못 붙으면 거짓이다.
    """
    deadline = time.monotonic() + CONNECT_WAIT_SECONDS
    while True:
        socket = QLocalSocket()
        socket.connectToServer(name)
        if socket.waitForConnected(int(CONNECT_RETRY_SECONDS * 1000)):
            allow_foreground()
            socket.write(SHOW_LINE + b"\n")
            socket.waitForBytesWritten(WRITE_WAIT_MS)
            socket.disconnectFromServer()
            return True
        socket.abort()
        if time.monotonic() >= deadline:
            return False
        time.sleep(CONNECT_RETRY_SECONDS)


def allow_foreground() -> None:
    """Windows에서 먼저 켠 프로세스가 창을 앞으로 낼 수 있게 권한을 넘긴다(그 밖에서는 안 한다)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(ctypes.c_ulong(_ASFW_ANY))  # type: ignore[attr-defined]
    except Exception:  # 권한을 넘기지 못해도 창은 앞으로 나오려고 한다
        return
