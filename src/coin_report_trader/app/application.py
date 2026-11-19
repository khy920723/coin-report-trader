"""일반 실행: 자료 폴더, 로그, DB를 준비하고 창을 띄운다. 창을 닫으면 끝난다(트레이는 M4).

켜는 순서(M1 명세 3.8): 1. 자료 폴더와 로그를 준비하고 DB를 열어 `migrate`한 뒤 닫는다. 2. 창을
만든다. 3. 창을 띄운 뒤 자료 스레드와 보내기 스레드를 시작한다. 1과 2에서 난 오류는 로그에 쓰고
`logs/startup-errors.jsonl`에 `"startup"` 줄로 남긴 뒤 다시 낸다.
"""

import contextlib
import logging
import sys
from collections.abc import Sequence

from coin_report_trader import __version__
from coin_report_trader.app.logging_setup import configure_logging, remove_log_handler
from coin_report_trader.config.paths import (
    AppPaths,
    describe_error,
    display_path,
    resolve_app_paths,
)
from coin_report_trader.core import APP_ID, Clock, StorageError, UiContext
from coin_report_trader.storage.database import (
    SCHEMA_VERSION,
    migrate,
    open_database,
    schema_version,
)

from .services import Services, resolve_clock
from .startup_errors import append_line, startup_line

log = logging.getLogger(__name__)

JOIN_SECONDS = 2.0
"""끝낼 때 각 스레드가 닫히기를 기다리는 가장 긴 실제 시간(하던 일은 기다리지 않는다)."""


def run_gui(argv: Sequence[str] | None = None, *, services: Services | None = None) -> int:
    """자료 폴더를 만들고, 로그를 열고, DB를 열어 확인하고 올린 뒤 창을 띄운다.

    `services`는 바깥과 잇는 것(시세, 디스코드, 보관소, 시계)을 넣는 자리다. 없는 것은 진짜를
    쓴다(디스코드는 `HttpWebhookSender`, 보관소는 `vault.create_secret_store()`. 둘 다 보내기
    스레드가 만들어 쓴다).

    `QApplication.instance()`가 있으면 그것을 쓰고 없을 때만 만든다. 이벤트 루프가 끝나면
    종료 코드를 돌려준다. 준비나 창 만들기에서 난 오류는 로그에 쓰고 다시 낸다.
    """
    paths = resolve_app_paths()
    paths.ensure()
    handler = configure_logging(paths.log_file)
    clock = resolve_clock(services)
    code = 1
    try:
        log.info("시작: 버전 %s, 자료 폴더 %s", __version__, display_path(paths.root))
        try:
            _check_database(paths)
            _migrate_database(paths)
        except Exception as exc:
            _record_startup_error(paths, clock, exc)
            raise
        code = _run_window(paths, argv, services, clock)
        return code
    except Exception as exc:
        log.error("오류: %s", describe_error(exc))
        raise
    finally:
        log.info("종료: 코드 %s", code)
        remove_log_handler(handler)


def _check_database(paths: AppPaths) -> None:
    """DB를 열어 설정과 스키마 번호를 확인하고 닫는다."""
    with contextlib.closing(open_database(paths.database_file)) as conn:
        version = schema_version(conn)
    if version > SCHEMA_VERSION:
        raise StorageError(
            f"DB 스키마 번호 {version}이 이 프로그램이 아는 번호({SCHEMA_VERSION})보다 높습니다"
        )


def _migrate_database(paths: AppPaths) -> None:
    """스키마를 이 프로그램이 아는 번호로 올린다(`open_database`는 열기만 한다)."""
    with contextlib.closing(open_database(paths.database_file)) as conn:
        migrate(conn)


def _record_startup_error(paths: AppPaths, clock: Clock, exc: Exception) -> None:
    """창을 띄우기 전에 난 오류를 `startup-errors.jsonl`에 남긴다(쓰지 못하면 로그에만 남는다)."""
    try:
        append_line(paths.logs_dir, startup_line(exc, clock.now()))
    except Exception as failure:
        log.warning("창을 띄우기 전의 오류를 기록하지 못했습니다: %s", type(failure).__name__)


def _run_window(
    paths: AppPaths, argv: Sequence[str] | None, services: Services | None, clock: Clock
) -> int:
    bridge = None
    try:
        from PySide6.QtWidgets import QApplication

        from coin_report_trader.ui import create_main_window

        from .actions import AppActions
        from .bridge import UiBridge
        from .data_thread import DataWorker
        from .send_thread import SendWorker

        app = QApplication.instance()
        if app is None:
            program = sys.argv[0] if sys.argv and sys.argv[0] else APP_ID
            app = QApplication([program, *(argv or ())])
        assert isinstance(app, QApplication)
        app.setQuitOnLastWindowClosed(True)  # 창을 닫으면 프로그램이 끝난다.
        app.setApplicationName(APP_ID)
        app.setApplicationVersion(__version__)
        bridge = UiBridge()
        sender = SendWorker(
            webhook=None if services is None else services.webhook,
            vault=None if services is None else services.vault,
            clock=clock,
            sink=bridge.post,
        )
        worker = DataWorker(
            database_file=paths.database_file,
            logs_dir=paths.logs_dir,
            clock=clock,
            quote=None if services is None else services.quote,
            sink=bridge.post,
            send=sender,
        )
        sender.attach(worker)
        actions = AppActions(worker, sender)
        window = create_main_window(UiContext(__version__, display_path(paths.root), actions))
        bridge.set_apply(window.apply)
        window.show()
    except Exception as exc:
        if bridge is not None:
            bridge.close()
        _record_startup_error(paths, clock, exc)
        raise
    worker.start()
    sender.start()
    try:
        return int(app.exec())
    finally:
        bridge.close()
        worker.stop()
        sender.stop()
        window.close()
        window.deleteLater()
        worker.join(JOIN_SECONDS)
        sender.join(JOIN_SECONDS)
