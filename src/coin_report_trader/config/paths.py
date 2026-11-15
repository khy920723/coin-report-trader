"""자료 폴더 위치와 화면·로그·보고서에 보여 주는 줄인 경로.

Windows는 `%LOCALAPPDATA%\\coin-report-trader\\`, 그 밖(개발·CI)은
`$XDG_DATA_HOME/coin-report-trader/`(값이 없거나 절대 경로가 아니면
`~/.local/share/coin-report-trader/`)다. 자료 폴더를 바꾸는 명령줄 옵션이나 전용 환경 변수는 없다.
"""

import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

from coin_report_trader.core import APP_ID

DATABASE_FILE_NAME = "app.db"
LOG_FILE_NAME = "app.log"

_LOCALAPPDATA_TOKEN = "%LOCALAPPDATA%"
_XDG_DATA_HOME_TOKEN = "$XDG_DATA_HOME"
_HOME_TOKEN = "~"


class AppPathError(Exception):
    """자료 폴더 위치를 정할 수 없다. 다른 곳으로 대신하지 않는다."""


@dataclass(frozen=True)
class AppPaths:
    """자료 폴더 구성: `root` 아래 `data\\app.db`와 `logs\\app.log`."""

    root: Path

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def database_file(self) -> Path:
        return self.data_dir / DATABASE_FILE_NAME

    @property
    def log_file(self) -> Path:
        return self.logs_dir / LOG_FILE_NAME

    def ensure(self) -> None:
        """폴더를 만든다. 여러 번 불러도 된다."""
        for directory in (self.root, self.data_dir, self.logs_dir):
            directory.mkdir(parents=True, exist_ok=True)


def resolve_app_paths(
    env: Mapping[str, str] | None = None, platform: str | None = None
) -> AppPaths:
    """환경 변수와 플랫폼으로 자료 폴더를 정한다. 인자를 빼면 현재 프로세스 값을 쓴다.

    절대 경로인지는 실행 중인 OS가 아니라 `platform`의 규칙으로 본다(`win32`는 Windows 규칙).
    """
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    return AppPaths(root=_base_dir(env, platform) / APP_ID)


def display_path(
    path: Path | str, env: Mapping[str, str] | None = None, platform: str | None = None
) -> str:
    """화면, 로그, 보고서에 쓰는 경로. 사용자 이름이 드러나는 앞부분을 줄인다.

    Windows는 `LOCALAPPDATA` 아래면 `%LOCALAPPDATA%`로, Linux는 `XDG_DATA_HOME` 아래면
    `$XDG_DATA_HOME`으로, 그 밖에 `HOME` 아래면 `~`로 줄인다. 어느 것에도 해당하지 않으면
    마지막 이름만 쓴다.
    """
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    flavor = _flavor(platform)
    target = flavor(_as_text(path, flavor))
    for prefix, token in _prefixes(env, platform):
        try:
            relative = target.relative_to(flavor(prefix))
        except ValueError:
            continue
        return str(flavor(token, relative))
    return target.name or "."


def display_text(
    text: str, env: Mapping[str, str] | None = None, platform: str | None = None
) -> str:
    """오류 문구 안에 든 자료 폴더와 집 폴더의 앞부분을 줄인다.

    Windows 규칙(`win32`)에서는 대소문자를 가리지 않고, 접두어 끝의 구분자를 뗀 뒤 견주며,
    구분자 자리는 역슬래시 하나, 역슬래시 둘(`str(OSError)`의 꼴), `/`를 모두 맞춘다.
    """
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    windows = platform == "win32"
    flags = re.IGNORECASE if windows else 0
    chars = "\\/" if windows else "/"
    separator = f"[{re.escape(chars)}]+"
    shortened = []
    for prefix, token in _prefixes(env, platform):
        # 앞뒤 구분자를 뗀 이름들을 구분자 패턴으로 잇는다.
        names = [re.escape(name) for name in re.split(separator, prefix.strip(chars))]
        pattern = (separator if prefix[0] in chars else "") + separator.join(names)
        shortened.append((pattern, token, len(prefix.rstrip(chars))))
    # 긴 접두어부터 바꿔야 `~`가 더 구체적인 줄임을 가리지 않는다.
    for pattern, token, _ in sorted(shortened, key=lambda item: item[2], reverse=True):
        # 이름이 더 이어지는 다른 폴더(`/srv/u2`)는 바꾸지 않는다.
        text = re.sub(pattern + r"(?![\w.-])", lambda _, t=token: t, text, flags=flags)
    return text


def describe_error(
    exc: BaseException, env: Mapping[str, str] | None = None, platform: str | None = None
) -> str:
    """보고서와 로그에 쓰는 오류 설명. 경로가 새지 않게 줄인 것만 쓴다.

    `OSError`는 `str(exc)`에 파일 이름이 통째로(Windows에서는 역슬래시가 두 번 든 꼴로) 들어가므로
    종류와 `strerror`를 쓰고 `filename`, `filename2`는 `display_path`로 줄인 값만 덧붙인다.
    """
    name = type(exc).__name__
    if not isinstance(exc, OSError) or not exc.strerror:
        return f"{name}: {display_text(str(exc), env, platform)}"
    text = f"{name}: {exc.strerror}"
    files = [
        display_path(
            os.fsdecode(f) if isinstance(f, bytes | os.PathLike) else str(f), env, platform
        )
        for f in (exc.filename, exc.filename2)
        if f is not None
    ]
    return f"{text} ({', '.join(files)})" if files else text


def _filename_text(filename: object) -> str:
    if isinstance(filename, bytes | os.PathLike):
        return os.fsdecode(filename)
    return str(filename)


def _as_text(path: Path | str, flavor: type[PurePath]) -> str:
    # Windows에서 linux 규칙의 Path를 `str()`로 바꾸면 역슬래시가 된다. 그 platform의 구분자를 쓴다.
    if flavor is PurePosixPath and isinstance(path, PurePath):
        return path.as_posix()
    return str(path)


def _flavor(platform: str) -> type[PurePath]:
    return PureWindowsPath if platform == "win32" else PurePosixPath


def _prefixes(env: Mapping[str, str], platform: str) -> list[tuple[str, str]]:
    """(줄일 앞부분, 대신 쓸 글) 목록. 앞에 있는 것이 먼저 맞는다.

    집 폴더를 정할 수 없거나 절대 경로가 아닌 값, 루트 폴더(`/`, `C:\\`)는 넣지 않는다.
    """
    candidates = []
    if platform == "win32":
        candidates.append((env.get("LOCALAPPDATA", ""), _LOCALAPPDATA_TOKEN))
    else:
        candidates.append((env.get("XDG_DATA_HOME", ""), _XDG_DATA_HOME_TOKEN))
    candidates.append((_home_text(env), _HOME_TOKEN))
    flavor = _flavor(platform)
    result = []
    for prefix, token in candidates:
        path = flavor(prefix)
        if prefix and path.is_absolute() and path.parent != path:
            result.append((prefix, token))
    return result


def _base_dir(env: Mapping[str, str], platform: str) -> Path:
    if platform == "win32":
        base = env.get("LOCALAPPDATA", "")
        if not base or not PureWindowsPath(base).is_absolute():
            raise AppPathError("LOCALAPPDATA 환경 변수가 없거나 절대 경로가 아닙니다")
        return Path(base)
    base = env.get("XDG_DATA_HOME", "")
    if base and PurePosixPath(base).is_absolute():
        return Path(base)
    home = _home_text(env)
    if not home:
        raise AppPathError("집 폴더를 정할 수 없습니다")
    return Path(home) / ".local" / "share"


def _home_text(env: Mapping[str, str]) -> str:
    """`env`의 `HOME` 글자 그대로. 없을 때만 실행 중인 OS의 집 폴더. 정할 수 없으면 빈 글."""
    home = env.get("HOME")
    if home:
        return home
    try:
        return str(Path.home())
    except (RuntimeError, OSError, KeyError):
        return ""
