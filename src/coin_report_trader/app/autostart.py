"""시작 앱 등록의 진짜(M1 명세 3.16.2). `AutostartStore`를 지킨다.

현재 사용자의 `HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`에 값 이름
`coin-report-trader`, 값 `"{exe의 절대 경로}"`(큰따옴표로 감싼 글. 인자 없음)를 쓴다.
표준 라이브러리 `winreg`만 쓰고, 메서드 안에서 import한다(`--self-test`의 import 점검이 Linux에서도
모든 모듈을 import한다). exe로 돌 때(`sys.frozen`)이고 Windows일 때만 지원하며, 그 밖에는 읽지도
쓰지도 않는다. 같은 키의 다른 값과 작업 관리자의 꺼짐 표시(`StartupApproved`)는 건드리지 않는다.
"""

import os
import sys
from typing import Final

RUN_KEY: Final = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME: Final = "coin-report-trader"


def current_command() -> str:
    """지금 exe의 명령: 절대 경로를 큰따옴표로 감싼 글(인자 없음)."""
    return '"' + os.path.abspath(sys.executable) + '"'


class WindowsAutostart:
    """`AutostartStore`의 진짜. 실패하면 `OSError`를 낸다."""

    def supported(self) -> bool:
        return bool(getattr(sys, "frozen", False)) and sys.platform == "win32"

    def read(self) -> str | None:
        """등록된 명령의 글. 없거나(키도 없을 때 포함) 글이 아니면 `None`."""
        if not self.supported():
            return None
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
                value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
        except FileNotFoundError:
            return None
        return value if isinstance(value, str) else None

    def write(self, command: str) -> None:
        if not self.supported():
            return
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)

    def delete(self) -> None:
        """값을 지운다. 값이나 키가 없으면 성공이다."""
        if not self.supported():
            return
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            return

    def __repr__(self) -> str:
        return "WindowsAutostart()"
