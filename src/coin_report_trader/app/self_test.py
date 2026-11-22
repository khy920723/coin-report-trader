"""`--self-test`: 창을 띄우지 않고 네트워크를 쓰지 않고 실행 환경을 점검한다.

점검마다 모든 예외를 잡아 실패로 적고, 하나가 실패해도 나머지를 한다. 결과는 현재 폴더의
`self-test-report.txt`(UTF-8, BOM 붙임)에 쓰고 모두 통과하면 0, 아니면 1을 돌려준다.
창 모드 exe에서 처리되지 않은 예외가 나면 오류 대화상자가 떠서 CI가 멈추므로 밖으로 내보내지 않는다.

점검 목록은 `default_checks()`가 부를 때마다 이 모듈의 `check_*` 함수로 만든다. 시험에서
`check_*`를 바꾸면 그 점검만 바뀐다. 무거운 import(PySide6)는 점검 안에서 한다.
"""

import contextlib
import importlib
import importlib.util
import os
import pkgutil
import struct
import sys
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from coin_report_trader import __version__
from coin_report_trader.config.paths import (
    AppPathError,
    describe_error,
    display_path,
    display_text,
    resolve_app_paths,
)
from coin_report_trader.core import (
    SCREEN_TITLES,
    WINDOW_TITLE,
    ScreenId,
    UiContext,
    make_tls_context,
    redact,
)
from coin_report_trader.storage.database import (
    SCHEMA_VERSION,
    migrate,
    open_database,
    schema_version,
)

from .services import Services

REPORT_FILE_NAME = "self-test-report.txt"
REPORT_TITLE = "Coin Report Trader 자체 점검 (--self-test)"
PACKAGE = "coin_report_trader"
# `pkgutil`이 찾지 못해도 빠졌는지 알 수 있게 하위 패키지는 이름으로도 센다.
SUBPACKAGES = ("core", "config", "storage", "trading", "stats", "vault", "notify", "app", "ui")
THIRD_PARTY_MODULES = (
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "sqlite3",
    "httpx",
    "certifi",
    "keyring",
)

# check_qt가 만든 QApplication. 프로세스가 끝날 때까지 붙잡아 둔다(다시 만들 수 없다).
_qt_app = None


class CheckFailed(Exception):
    """점검 실패. 메시지가 보고서에 그대로 들어간다."""


@dataclass(frozen=True)
class Check:
    key: str
    run: Callable[[], str]


@dataclass(frozen=True)
class CheckResult:
    key: str
    ok: bool
    detail: str


# --- 점검 ---------------------------------------------------------------------------


def check_imports() -> str:
    """패키지의 모든 모듈과 PySide6, sqlite3 모듈을 import한다. exe에 빠진 모듈을 찾는다."""
    names = _package_module_names()
    failures = []
    for name in (*names, *THIRD_PARTY_MODULES):
        try:
            importlib.import_module(name)
        except Exception as exc:
            failures.append(f"{name} ({_describe(exc)})")
    if failures:
        raise CheckFailed("import 실패: " + "; ".join(failures))
    import sqlite3

    import PySide6

    return f"모듈 {len(names)}개, PySide6 {PySide6.__version__}, SQLite {sqlite3.sqlite_version}"


def check_qt() -> str:
    """offscreen 플랫폼으로 Qt를 초기화하고 창을 만들어 본다(띄우지 않는다)."""
    global _qt_app
    import PySide6
    from PySide6.QtCore import QCoreApplication, QEvent, qVersion
    from PySide6.QtWidgets import QApplication

    from coin_report_trader.ui import create_main_window, screen_order, screen_widget

    app = QApplication.instance()
    if app is None:
        # 플러그인이 없으면 Qt가 프로세스를 강제 종료하므로 만들기 전에 먼저 찾는다.
        if find_offscreen_plugin(_qt_plugin_dirs(QCoreApplication.libraryPaths())) is None:
            raise CheckFailed("offscreen 플랫폼 플러그인(platforms/qoffscreen)을 찾지 못했습니다")
        app = QApplication([_program_name(), "-platform", "offscreen"])
        _qt_app = app
        if QApplication.platformName() != "offscreen":
            raise CheckFailed(f"Qt 플랫폼이 offscreen이 아닙니다: {QApplication.platformName()}")

    window = create_main_window(UiContext(__version__, "~"))
    try:
        app.processEvents()
        if window.objectName() != "mainWindow":
            raise CheckFailed(f"창 objectName이 mainWindow가 아닙니다: {window.objectName()}")
        if window.windowTitle() != WINDOW_TITLE:
            raise CheckFailed(f"창 제목이 {WINDOW_TITLE}이 아닙니다: {window.windowTitle()}")
        order = screen_order(window)
        if order != tuple(ScreenId):
            raise CheckFailed(f"다섯 자리의 순서가 다릅니다: {[s.value for s in order]}")
        for screen_id in ScreenId:
            name = screen_widget(window, screen_id).objectName()
            if name != f"screen_{screen_id.value}":
                title = SCREEN_TITLES[screen_id]
                raise CheckFailed(f"{title} 자리의 objectName이 다릅니다: {name}")
        if window.isVisible():
            raise CheckFailed("창이 화면에 표시되었습니다")
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return (
        f"Qt {qVersion()}, PySide6 {PySide6.__version__}, 플랫폼 {QApplication.platformName()}, "
        f"창 제목과 다섯 자리 확인(표시 안 함)"
    )


def check_sqlite() -> str:
    """임시 폴더의 DB를 열고 `migrate`한 뒤 WAL, synchronous, 스키마 번호, 표를 본다.

    실제 DB는 건드리지 않는다.
    """
    import sqlite3

    with tempfile.TemporaryDirectory(prefix="crt-self-test-", ignore_cleanup_errors=True) as tmp:
        conn = open_database(Path(tmp) / "self-test.db")
        try:
            migrate(conn)
            (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
            (synchronous,) = conn.execute("PRAGMA synchronous").fetchone()
            version = schema_version(conn)
            tables = {
                name
                for (name,) in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            }
        finally:
            conn.close()
    if str(mode).lower() != "wal" or synchronous != 2:
        raise CheckFailed(f"DB 설정이 맞지 않습니다: journal={mode}, synchronous={synchronous}")
    if version != SCHEMA_VERSION:
        raise CheckFailed(f"스키마 번호가 {version}입니다(기대 {SCHEMA_VERSION})")
    if tables != {"settings", "candles", "alerts", "rounds", "reports", "report_rows"}:
        raise CheckFailed(f"표가 맞지 않습니다: {sorted(tables)}")
    return (
        f"SQLite {sqlite3.sqlite_version}, wal, synchronous 2, 스키마 번호 {version}, "
        f"표 {len(tables)}개 (임시 DB)"
    )


def check_paths() -> str:
    """자료 폴더를 정해 만들고 root, data, logs에 임시 파일을 써 본다."""
    try:
        paths = resolve_app_paths()
    except AppPathError as exc:
        raise CheckFailed(str(exc)) from exc
    shown = display_path(paths.root)
    try:
        paths.ensure()
        for directory in (paths.root, paths.data_dir, paths.logs_dir):
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".self-test-"):
                pass
    except OSError as exc:
        raise CheckFailed(f"{type(exc).__name__}: {shown} ({exc.strerror})") from exc
    return f"{shown} (root, data, logs 쓰기 가능)"


def check_tls() -> str:
    """`certifi.where()`의 파일로 TLS 설정을 만든다(연결하지 않는다). 파일 이름만 보고서에 쓴다."""
    import certifi

    bundle = Path(certifi.where())
    if not bundle.is_file():
        raise CheckFailed(f"인증서 묶음 파일이 없습니다: {bundle.name}")
    if getattr(sys, "frozen", False) and not _inside_bundle(bundle):
        raise CheckFailed(f"인증서 묶음이 번들 안에 없습니다: {bundle.name}")
    context = make_tls_context(str(bundle))
    count = int(context.cert_store_stats().get("x509_ca", 0))
    if count < 1:
        raise CheckFailed(f"CA 인증서가 없습니다: {bundle.name}")
    return f"{bundle.name}, CA 인증서 {count}개"


def _inside_bundle(path: Path) -> bool:
    """exe의 번들 폴더(`sys._MEIPASS`)나 exe 옆 폴더 안에 있는 파일인지."""
    roots = [Path(sys.executable).parent]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    resolved = path.resolve()
    return any(resolved.is_relative_to(root.resolve()) for root in roots)


def check_vault() -> str:
    """keyring 백엔드의 종류만 본다(값을 읽거나 쓰지 않는다). Windows에서만 판정한다."""
    if sys.platform != "win32":
        return "Windows가 아니어서 판정하지 않음"
    import keyring

    backend = keyring.get_keyring()
    kind = type(backend)
    if not kind.__module__.startswith("keyring.backends.Windows"):
        raise CheckFailed(f"Windows 자격 증명 보관소가 아닙니다: {kind.__name__}")
    return f"Windows 자격 증명 보관소({kind.__name__})"


def check_notice() -> str:
    """exe일 때만 exe 옆 `NOTICE.txt`와 `_internal/licenses/`를 본다."""
    if not getattr(sys, "frozen", False):
        return "소스 실행이어서 판정하지 않음"
    exe_dir = Path(sys.executable).parent
    missing = []
    if not (exe_dir / "NOTICE.txt").is_file():
        missing.append("NOTICE.txt")
    if not (exe_dir / "_internal" / "licenses").is_dir():
        missing.append("_internal/licenses/")
    if missing:
        raise CheckFailed("exe 옆에 없습니다: " + ", ".join(missing))
    return "NOTICE.txt와 _internal/licenses/ 있음"


def default_checks() -> tuple[Check, ...]:
    """보고서에 적는 순서대로의 점검 목록."""
    return (
        Check("import", check_imports),
        Check("qt", check_qt),
        Check("sqlite", check_sqlite),
        Check("paths", check_paths),
        Check("notice", check_notice),
        Check("tls", check_tls),
        Check("vault", check_vault),
    )


# --- 실행과 보고서 ------------------------------------------------------------------


def run_checks(checks: Sequence[Check]) -> list[CheckResult]:
    """점검을 차례로 모두 실행한다. 하나가 실패해도 나머지를 계속한다."""
    results = []
    for check in checks:
        try:
            detail = check.run()
        except CheckFailed as exc:
            results.append(CheckResult(check.key, False, _describe_failure(exc)))
        except (Exception, SystemExit) as exc:
            results.append(CheckResult(check.key, False, _describe(exc)))
        else:
            results.append(CheckResult(check.key, True, detail))
    return results


def _describe(exc: BaseException) -> str:
    """오류 설명(경로는 줄인 것만). 설명을 만들다 난 오류가 다음 점검을 막지 않게 한다."""
    try:
        return redact(describe_error(exc))
    except Exception:
        return type(exc).__name__


def _describe_failure(exc: CheckFailed) -> str:
    try:
        return redact(display_text(str(exc)))
    except Exception:
        return type(exc).__name__


def format_report(results: Sequence[CheckResult], now: datetime | None = None) -> str:
    now = datetime.now(UTC) if now is None else now
    passed = sum(result.ok for result in results)
    version = sys.version_info
    lines = [
        REPORT_TITLE,
        f"버전: {__version__}",
        f"실행 형태: {'exe' if getattr(sys, 'frozen', False) else '소스'}",
        f"Python: {version.major}.{version.minor}.{version.micro} ({8 * struct.calcsize('P')}bit)",
        f"OS: {_os_description()}",
        f"점검 시각(UTC): {now.strftime('%Y-%m-%dT%H:%M:%SZ')}",
    ]
    # 항목마다 한 줄이 되게 설명 속 줄바꿈은 공백으로 바꾼다.
    for r in results:
        detail = " ".join(r.detail.splitlines())
        lines.append(f"[{'통과' if r.ok else '실패'}] {r.key}: {detail}")
    verdict = "통과" if passed == len(results) else "실패"
    lines.append(f"결과: {verdict} ({passed}/{len(results)})")
    return "\n".join(lines) + "\n"


def run_self_test(
    report_path: Path | None = None,
    checks: Sequence[Check] | None = None,
    *,
    services: Services | None = None,
) -> int:
    """점검하고 보고서를 쓴다. 모두 통과하고 보고서를 썼으면 0, 아니면 1.

    `services`는 받기만 하고 쓰지 않는다. 일곱 항목은 시세, 시계, 보관소를 쓰지 않아서 가짜를
    넘겨도 결과가 같다(M1 명세 3.11).
    """
    del services
    results = run_checks(default_checks() if checks is None else checks)
    report = format_report(results)
    target = Path.cwd() / REPORT_FILE_NAME if report_path is None else report_path
    try:
        # Windows 메모장과 PowerShell 5.1에서도 한글이 깨지지 않게 BOM을 붙인다.
        target.write_text(report, encoding="utf-8-sig")
    except OSError:
        return 1
    if sys.stdout is not None:  # 창 모드 exe에서는 None이다.
        with contextlib.suppress(Exception):
            print(report, end="")
    return 0 if all(result.ok for result in results) else 1


# --- 도우미 -------------------------------------------------------------------------


def find_offscreen_plugin(library_paths: Iterable[str]) -> Path | None:
    """Qt 플러그인 폴더들에서 offscreen 플랫폼 플러그인 파일을 찾는다."""
    for library_path in library_paths:
        platforms = Path(library_path) / "platforms"
        if platforms.is_dir():
            for candidate in sorted(platforms.iterdir()):
                if "offscreen" in candidate.name and candidate.is_file():
                    return candidate
    return None


def _qt_plugin_dirs(library_paths: Iterable[str]) -> list[str]:
    extra = os.environ.get("QT_PLUGIN_PATH", "")
    return [*library_paths, *(p for p in extra.split(os.pathsep) if p)]


def _package_module_names() -> list[str]:
    names = [PACKAGE, *(f"{PACKAGE}.{name}" for name in SUBPACKAGES)]
    # 루트 패키지는 이름으로 import하지 않고 spec에서 폴더를 얻는다(exe에서도 같은 값이 나온다).
    spec = importlib.util.find_spec(PACKAGE)
    locations = list(spec.submodule_search_locations or ()) if spec else []
    # import 실패는 check_imports가 이름별로 잡으므로 여기서는 찾기 오류를 무시한다.
    for info in pkgutil.walk_packages(locations, f"{PACKAGE}.", onerror=lambda _: None):
        if info.name not in names:
            names.append(info.name)
    return names


def _program_name() -> str:
    return sys.argv[0] if sys.argv and sys.argv[0] else "coin-report-trader"


def _os_description() -> str:
    # platform.platform()은 Windows에서 WMI를 불러 느리거나 멈출 수 있어 쓰지 않는다.
    # PC 이름이 드러나지 않게 uname의 nodename도 쓰지 않는다.
    if sys.platform == "win32":
        info = sys.getwindowsversion()
        return f"Windows {info.major}.{info.minor}.{info.build}"
    uname = os.uname()
    return f"{uname.sysname} {uname.release} {uname.machine}"
