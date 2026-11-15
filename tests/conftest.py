"""시험 공통 설정(M0 명세 3.5절, 4절 5번).

- 자료 폴더: autouse 픽스처가 모든 시험에서 `LOCALAPPDATA`(Windows)와 `XDG_DATA_HOME`(Linux)를
  그 시험의 `tmp_path`로 바꾼다. 실제 사용자 폴더를 쓰는 시험은 없다. 시험이 이 값을 다시 바꿔도
  된다(같은 `monkeypatch`라 시험이 끝나면 함께 되돌아간다).
- Qt: `QT_QPA_PLATFORM`이 없으면 offscreen으로 둔다(첫 `QApplication`을 만들기 전에 정해야 한다).
- 네트워크 차단: `pytest_configure`에서 걸고 `pytest_unconfigure`에서 푼다. 수집, 모든 픽스처,
  모든 시험이 그 안에서 돈다. 끄는 마커나 옵션은 없다.
  - 막는 것: IP 소켓(`AF_INET`, `AF_INET6`)의 `connect`·`connect_ex`·`sendto`·`sendmsg`에서 대상이
    루프백이나 미지정 주소가 아닌 것, `socket.create_connection`, 이름 해석(`getaddrinfo`,
    `gethostbyname`, `gethostbyname_ex`)에서 "localhost"가 아닌 이름, 역방향 조회(`gethostbyaddr`,
    `getnameinfo`)에서 루프백과 이 PC 이름이 아닌 것, Windows의 `IocpProactor.connect`·`sendto`.
  - 프록시 설정(`*_proxy` 환경 변수, Windows 레지스트리의 프록시)을 비운다. 비우기 전에 적힌 로컬
    프록시의 포트로 가는 루프백 연결도 막는다(로컬 프록시를 거쳐 밖으로 나갈 수 있다).
  - 막으면 `RealNetworkBlocked`(AssertionError의 하위)를 낸다. `OSError`가 아니어서 앱 코드가
    "연결 실패"로 삼키지 않는다. 막을 때마다 기록하고, 시험의 정리 단계에서 기록이 남아 있으면 그
    시험을 실패로 만든다(예외가 삼켜졌어도). 일부러 막는 시험은 `network_guard.expect_blocked()`로
    감싼다.
  - 한계: 자식 프로세스, Python `socket`을 거치지 않는 확장 모듈(앱은 QtNetwork를 쓰지 않는다),
    원래 함수를 일부러 되돌리는 코드는 막지 못한다.
- 수용 시나리오: `@pytest.mark.scenario("A1")`처럼 명세 7절의 번호를 붙인다. A12 시험은 수집한
  시험의 맨 끝으로 옮겨, 앞에서 돈 시나리오의 막힌 시도 기록을 본다.

`--import-mode=importlib`이고 `pythonpath`가 없어서 시험 파일은 이 파일을 import하지 못한다.
시험은 `network_guard` 픽스처로 차단 장치를 받는다.
"""

import ipaddress
import os
import socket
import sys
import threading
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

DATA_DIR_VARIABLES = ("LOCALAPPDATA", "XDG_DATA_HOME")
SCENARIO_MARKER = "scenario"
LAST_SCENARIO = "A12"

_INET_FAMILIES = (socket.AF_INET, socket.AF_INET6)
_REPORT_LIMIT = 10
_DEFAULT_PROXY_PORTS = {"http": 80, "https": 443, "socks": 1080, "socks5": 1080, "socks5h": 1080}


class RealNetworkBlocked(AssertionError):
    """시험이 실제 네트워크로 나가려 했다."""


# --- 자료 폴더 -------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _data_dir_in_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """모든 시험에서 자료 폴더의 기준(`LOCALAPPDATA`, `XDG_DATA_HOME`)을 `tmp_path`로 바꾼다."""
    for name in DATA_DIR_VARIABLES:
        monkeypatch.setenv(name, str(tmp_path))


# --- 네트워크 차단 ---------------------------------------------------------------------------


def _host_text(host: object) -> str | None:
    if host is None:
        return None
    if isinstance(host, bytes | bytearray):
        return bytes(host).decode("ascii", "replace")
    return str(host)


def _ip(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def is_local_host(host: object) -> bool:
    """루프백, 미지정 주소(바인드용), 빈 값, "localhost"면 True. 그 밖의 주소와 이름은 False."""
    text = _host_text(host)
    if text is None or text == "" or text.lower().rstrip(".") == "localhost":
        return True
    address = _ip(text)
    if address is None:
        return False  # 이름은 해석하면 밖으로 나간다
    return address.is_loopback or address.is_unspecified


def _proxy_ports(values: dict[str, str]) -> frozenset[int]:
    """`*_proxy` 값 가운데 호스트가 로컬인 것의 포트."""
    from urllib.parse import urlsplit

    ports: set[int] = set()
    for name, value in values.items():
        lower = name.lower()
        if not lower.endswith("_proxy") or lower.endswith("no_proxy"):
            continue
        text = value.strip()
        if not text:
            continue
        if "://" not in text:
            text = "http://" + text
        try:
            parts = urlsplit(text)
            host, port = parts.hostname, parts.port
        except ValueError:
            continue
        if not host or not is_local_host(host):
            continue
        port = port if port is not None else _DEFAULT_PROXY_PORTS.get(parts.scheme.lower())
        if port is not None and 0 < port < 65536:
            ports.add(port)
    return frozenset(ports)


class NetworkGuard:
    """실제 네트워크 차단 장치. 시험은 `network_guard` 픽스처로 받는다."""

    Blocked = RealNetworkBlocked

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._attempts: list[str] = []
        self._installed: dict[tuple[object, str], object] = {}
        self.proxy_ports: frozenset[int] = frozenset()
        # 시험(nodeid)마다 정리 단계에서 본 막힌 시도 수.
        self.blocked_by_test: dict[str, int] = {}
        # 수집한 시험의 시나리오 번호 → nodeid 목록.
        self.scenarios: dict[str, list[str]] = {}

    # 기록

    def _enforce(self, blocked: RealNetworkBlocked | None) -> None:
        if blocked is None:
            return
        with self._lock:
            self._attempts.append(str(blocked))
        raise blocked

    def take_attempts(self) -> list[str]:
        with self._lock:
            taken = list(self._attempts)
            self._attempts.clear()
        return taken

    @contextmanager
    def expect_blocked(self) -> Iterator[list[str]]:
        """안에서 막힌 시도를 기대한 것으로 보고 기록에서 뺀다. 하나도 없으면 AssertionError."""
        with self._lock:
            start = len(self._attempts)
        caught: list[str] = []
        try:
            yield caught
        finally:
            with self._lock:
                caught.extend(self._attempts[start:])
                del self._attempts[start:]
        if not caught:
            raise AssertionError("expect_blocked() 안에서 막힌 시도가 없었다")

    @staticmethod
    def report(attempts: list[str]) -> str:
        lines = [f"- {item}" for item in attempts[:_REPORT_LIMIT]]
        if len(attempts) > _REPORT_LIMIT:
            lines.append(f"- 외 {len(attempts) - _REPORT_LIMIT}건")
        return (
            f"실제 네트워크 시도를 {len(attempts)}번 막았다(예외가 삼켜졌어도 실패로 본다. "
            "일부러 막는 시험은 network_guard.expect_blocked()로 감싼다):\n" + "\n".join(lines)
        )

    # 판정

    def address_problem(self, family: int, address: Any, what: str) -> RealNetworkBlocked | None:
        if family not in _INET_FAMILIES:
            return None
        host = address[0] if isinstance(address, tuple) and address else address
        port = address[1] if isinstance(address, tuple) and len(address) >= 2 else None
        if isinstance(port, str) and port.strip().isdigit():
            port = int(port)
        if not is_local_host(host):
            return RealNetworkBlocked(f"실제 네트워크 {what}을 막았다: {address!r}")
        if host is not None and port in self.proxy_ports:
            return RealNetworkBlocked(f"로컬 프록시(포트 {port})로 가는 {what}을 막았다")
        return None

    def name_problem(self, host: Any, what: str = "이름 해석") -> RealNetworkBlocked | None:
        text = _host_text(host)
        if text is None or text.lower().rstrip(".") == "localhost" or _ip(text) is not None:
            return None  # 주소를 그대로 쓰는 해석은 밖으로 나가지 않는다(연결 단계가 본다)
        return RealNetworkBlocked(f"실제 네트워크 {what}을 막았다: {text!r}")

    def reverse_problem(self, host: Any) -> RealNetworkBlocked | None:
        text = _host_text(host)
        if text and text.lower().rstrip(".") in ("localhost", socket.gethostname().lower()):
            return None
        address = _ip(text) if text else None
        if address is not None and address.is_loopback:
            return None
        return RealNetworkBlocked(f"실제 네트워크 역방향 조회를 막았다: {text!r}")

    # 설치

    def is_installed(self) -> bool:
        return bool(self._installed) and all(
            getattr(owner, name, None) is wrapper
            for (owner, name), wrapper in self._installed.items()
        )

    def _set(self, patch: pytest.MonkeyPatch, owner: object, name: str, wrapper: Any) -> None:
        patch.setattr(owner, name, wrapper)
        self._installed[(owner, name)] = wrapper

    def _clear_proxies(self, patch: pytest.MonkeyPatch) -> None:
        values = dict(os.environ)
        registry = getattr(urllib.request, "getproxies_registry", None)
        if registry is not None:
            values |= {f"{key}_proxy": str(value) for key, value in registry().items()}
        self.proxy_ports = _proxy_ports(values)
        for name in list(os.environ):
            lower = name.lower()
            if lower.endswith("_proxy") and not lower.endswith("no_proxy"):
                patch.delenv(name, raising=False)
        if registry is not None:
            patch.setattr(urllib.request, "getproxies_registry", dict)

    def install(self, patch: pytest.MonkeyPatch) -> None:
        """차단을 건다. `patch.undo()`로 풀린다."""
        self._installed.clear()
        self._clear_proxies(patch)
        guard = self
        sock_cls = socket.socket
        original = {
            name: getattr(sock_cls, name)
            for name in ("connect", "connect_ex", "sendto", "sendmsg")
            if hasattr(sock_cls, name)  # Windows에는 sendmsg가 없다
        }
        lookups = {
            name: getattr(socket, name)
            for name in (
                "getaddrinfo",
                "gethostbyname",
                "gethostbyname_ex",
                "gethostbyaddr",
                "getnameinfo",
                "create_connection",
            )
        }

        def connect(sock: socket.socket, address: Any) -> Any:
            guard._enforce(guard.address_problem(sock.family, address, "연결"))
            return original["connect"](sock, address)

        def connect_ex(sock: socket.socket, address: Any) -> Any:
            guard._enforce(guard.address_problem(sock.family, address, "연결"))
            return original["connect_ex"](sock, address)

        def sendto(sock: socket.socket, data: Any, *args: Any) -> Any:
            target = args[-1] if args else None
            guard._enforce(guard.address_problem(sock.family, target, "송신"))
            return original["sendto"](sock, data, *args)

        def sendmsg(sock: socket.socket, buffers: Any, *args: Any, **kwargs: Any) -> Any:
            target = kwargs.get("address", args[2] if len(args) >= 3 else None)
            if target is not None:
                guard._enforce(guard.address_problem(sock.family, target, "송신"))
            return original["sendmsg"](sock, buffers, *args, **kwargs)

        def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
            guard._enforce(guard.name_problem(host))
            return lookups["getaddrinfo"](host, *args, **kwargs)

        def gethostbyname(host: Any) -> Any:
            guard._enforce(guard.name_problem(host))
            return lookups["gethostbyname"](host)

        def gethostbyname_ex(host: Any) -> Any:
            guard._enforce(guard.name_problem(host))
            return lookups["gethostbyname_ex"](host)

        def gethostbyaddr(host: Any) -> Any:
            guard._enforce(guard.reverse_problem(host))
            return lookups["gethostbyaddr"](host)

        def getnameinfo(sockaddr: Any, flags: int) -> Any:
            host = sockaddr[0] if isinstance(sockaddr, tuple) and sockaddr else sockaddr
            guard._enforce(guard.reverse_problem(host))
            return lookups["getnameinfo"](sockaddr, flags)

        def create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
            # 소켓을 만들기 전에 본다(이름이면 이름 해석도 밖으로 나가므로 연결로 막는다).
            guard._enforce(guard.address_problem(socket.AF_INET, address, "연결"))
            return lookups["create_connection"](address, *args, **kwargs)

        wrappers = {
            "connect": connect,
            "connect_ex": connect_ex,
            "sendto": sendto,
            "sendmsg": sendmsg,
        }
        for name in original:
            self._set(patch, sock_cls, name, wrappers[name])
        for name, wrapper in (
            ("getaddrinfo", getaddrinfo),
            ("gethostbyname", gethostbyname),
            ("gethostbyname_ex", gethostbyname_ex),
            ("gethostbyaddr", gethostbyaddr),
            ("getnameinfo", getnameinfo),
            ("create_connection", create_connection),
        ):
            self._set(patch, socket, name, wrapper)
        if sys.platform == "win32":  # Proactor 루프는 socket.connect를 거치지 않는다.
            import asyncio.windows_events as windows_events

            self._wrap_proactor(patch, windows_events.IocpProactor)

    def _wrap_proactor(self, patch: pytest.MonkeyPatch, proactor: type) -> None:
        guard = self
        original_connect = proactor.connect  # type: ignore[attr-defined]
        original_sendto = proactor.sendto  # type: ignore[attr-defined]

        def connect(self: Any, conn: socket.socket, address: Any) -> Any:
            guard._enforce(guard.address_problem(conn.family, address, "연결"))
            return original_connect(self, conn, address)

        def sendto(
            self: Any, conn: socket.socket, buf: Any, flags: int = 0, addr: Any = None
        ) -> Any:
            if addr is not None:
                guard._enforce(guard.address_problem(conn.family, addr, "송신"))
            return original_sendto(self, conn, buf, flags, addr)

        self._set(patch, proactor, "connect", connect)
        self._set(patch, proactor, "sendto", sendto)


GUARD = NetworkGuard()
_PATCH = pytest.StashKey[pytest.MonkeyPatch]()


@pytest.fixture
def network_guard() -> NetworkGuard:
    """이 세션의 네트워크 차단 장치."""
    return GUARD


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", f"{SCENARIO_MARKER}(id): 명세 7절의 수용 시나리오 번호")
    patch = pytest.MonkeyPatch()
    GUARD.install(patch)
    config.stash[_PATCH] = patch


def pytest_unconfigure(config: pytest.Config) -> None:
    patch = config.stash.get(_PATCH, None)
    if patch is not None:
        patch.undo()
        del config.stash[_PATCH]


def pytest_collection_finish(session: pytest.Session) -> None:
    """수집 중의 막힌 시도를 보고, 시나리오를 적고, A12 시험을 맨 끝으로 옮긴다.

    순서는 여기서 옮긴다. `pytest_collection_modifyitems`에서 옮기면 `--ff`, `--nf`처럼 그 뒤에
    순서를 바꾸는 플러그인이 A12를 앞으로 되돌릴 수 있다. 이 훅은 모든 정렬과 `-k` 등의 선택이
    끝난 뒤에 불리고, 실행은 `session.items`의 순서를 따른다.
    """
    attempts = GUARD.take_attempts()
    if attempts:
        raise pytest.UsageError("수집 중 " + GUARD.report(attempts))
    items = session.items
    last = [item for item in items if _scenario(item) == LAST_SCENARIO]
    if last:
        items[:] = [item for item in items if _scenario(item) != LAST_SCENARIO] + last
    GUARD.scenarios.clear()
    for item in items:
        scenario = _scenario(item)
        if scenario is not None:
            GUARD.scenarios.setdefault(scenario, []).append(item.nodeid)


def _scenario(item: pytest.Item) -> str | None:
    marker = item.get_closest_marker(SCENARIO_MARKER)
    return str(marker.args[0]) if marker is not None and marker.args else None


def _guard_problem() -> str | None:
    if not GUARD.is_installed():
        return "실제 네트워크 차단이 풀려 있다(socket 함수를 되돌린 코드가 있다)"
    attempts = GUARD.take_attempts()
    return GUARD.report(attempts) if attempts else None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Iterator[None]:
    problem = _guard_problem()  # 앞 시험이 끝난 뒤(남은 스레드 등)의 시도도 여기서 잡는다
    if problem is not None:
        pytest.fail(f"시험 준비 전: {problem}", pytrace=False)
    return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> Iterator[None]:
    try:
        return (yield)
    finally:
        # 정리 단계는 함수·모듈·세션 범위 픽스처의 정리까지 담는다.
        installed = GUARD.is_installed()
        attempts = GUARD.take_attempts()
        GUARD.blocked_by_test[item.nodeid] = len(attempts)
        if not installed:
            pytest.fail("실제 네트워크 차단이 풀려 있다", pytrace=False)
        if attempts:
            pytest.fail(GUARD.report(attempts), pytrace=False)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    attempts = GUARD.take_attempts()
    if attempts:
        sys.stderr.write(f"\n세션 끝: {GUARD.report(attempts)}\n")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
