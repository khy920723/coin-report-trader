"""일반 실행: 자료 폴더, 로그, DB를 준비하고 창을 띄운다. 창을 닫으면 끝난다(트레이는 M4).

한 번 더 켜기(3.8.1): `QApplication`을 만든 뒤 켜는 순서 1보다 앞에서 `single_instance`가 잠금을
잡거나, 먼저 켠 쪽에 알리고 창 없이 0으로 끝낸다.

켜는 순서(M1 명세 3.8): 1. 자료 폴더와 로그를 준비하고 DB를 열어 `migrate`한 뒤 닫는다. 2. 창을
만든다. 3. 창을 띄운 뒤 자료 스레드와 보내기 스레드를 시작한다. 1과 2에서 난 오류는 로그에 쓰고
`logs/startup-errors.jsonl`에 `"startup"` 줄로 남긴 뒤 다시 낸다.
"""

import contextlib
import gc
import logging
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

from coin_report_trader import __version__
from coin_report_trader.app.logging_setup import configure_logging, remove_log_handler
from coin_report_trader.config.paths import (
    AppPaths,
    describe_error,
    display_path,
    resolve_app_paths,
)
from coin_report_trader.core import APP_ID, Clock, StorageError, UiContext
from coin_report_trader.storage.backups import BACKUPS_FOLDER, copy_before_upgrade
from coin_report_trader.storage.database import (
    SCHEMA_VERSION,
    migrate,
    open_database,
    schema_version,
)

if TYPE_CHECKING:
    from .single_instance import Started

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
    try:
        return _run_gui(argv, services)
    finally:
        _release_qt_objects()  # `_run_gui`의 지역 변수(한 번 더 켜기의 창 참조 포함)가 이미 없다


def _run_gui(argv: Sequence[str] | None, services: Services | None) -> int:
    instance = _claim_instance(argv)
    if instance is not None and instance.later:
        return 0  # 먼저 켠 쪽에 알렸다. 로그, DB, 창을 만들지 않는다.
    try:
        return _run(argv, services, instance)
    finally:
        if instance is not None:
            instance.close()


def _claim_instance(argv: Sequence[str] | None) -> "Started | None":
    """한 번 더 켜기의 차례(3.8.1). 자료 폴더의 경로를 정하지 못하거나 이 차례에서 예외가 나면
    건너뛰고 켜는 순서 1로 간다(그 오류는 거기서 지금처럼 다룬다)."""
    try:
        root = resolve_app_paths().root
    except Exception:
        return None
    try:
        from . import single_instance

        _ensure_application(argv)
        return single_instance.start(root)
    except Exception as exc:
        from .single_instance import Started

        return Started(note=f"한 번 더 켜기를 건너뜁니다({type(exc).__name__})")


def _ensure_application(argv: Sequence[str] | None) -> None:
    from PySide6.QtWidgets import QApplication

    if QApplication.instance() is None:
        program = sys.argv[0] if sys.argv and sys.argv[0] else APP_ID
        QApplication([program, *(argv or ())])


def _run(argv: Sequence[str] | None, services: Services | None, instance: "Started | None") -> int:
    paths = resolve_app_paths()
    paths.ensure()
    handler = configure_logging(paths.log_file)
    clock = resolve_clock(services)
    code = 1
    try:
        log.info("시작: 버전 %s, 자료 폴더 %s", __version__, display_path(paths.root))
        if instance is not None and instance.note:
            log.warning("%s", instance.note)  # 그대로 켠다(한 번 더 켜기만 막지 못한다)
        try:
            _check_database(paths)
            _migrate_database(paths, clock)
        except Exception as exc:
            _record_startup_error(paths, clock, exc)
            raise
        code = _run_window(paths, argv, services, clock, instance)
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


def _migrate_database(paths: AppPaths, clock: Clock) -> None:
    """스키마를 이 프로그램이 아는 번호로 올린다(`open_database`는 열기만 한다).

    올려야 하고 번호가 1 이상인 DB는 올리기 전에 `backups`에 사본을 만든다(M3 명세 3.13). 사본을
    만들지 못하면 올리지 않고 `StorageError`로 끝낸다(창을 띄우지 못한 오류, A-29 "켜기").
    """
    with contextlib.closing(open_database(paths.database_file)) as conn:
        copy_before_upgrade(conn, paths.root / BACKUPS_FOLDER, clock.now())
        migrate(conn)


def _record_startup_error(paths: AppPaths, clock: Clock, exc: Exception) -> None:
    """창을 띄우기 전에 난 오류를 `startup-errors.jsonl`에 남긴다(쓰지 못하면 로그에만 남는다)."""
    try:
        append_line(paths.logs_dir, startup_line(exc, clock.now()))
    except Exception as failure:
        log.warning("창을 띄우기 전의 오류를 기록하지 못했습니다: %s", type(failure).__name__)


def _release_qt_objects() -> None:
    """창이 끝난 뒤 남은 Qt 객체를 주 스레드에서 지운다.

    창, 다리, 동작이 서로를 붙든 순환 참조는 참조 수만으로는 지워지지 않고 가비지 수집이 지운다.
    그 수집이 자료 스레드 같은 다른 스레드에서 돌면 Qt 객체가 그 스레드에서 지워져 Windows에서
    `access violation`으로 죽을 수 있다. 그래서 이 함수가 주 스레드에서 미룬 지우기를 마치고
    수집을 한 번 돌린다. `run_gui`가 `_run_gui`가 돌아온 뒤에 부르므로, 창과 다리를 붙든 지역 변수는
    이미 없다.
    """
    from PySide6.QtCore import QCoreApplication, QEvent

    if QCoreApplication.instance() is not None:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


def _run_window(
    paths: AppPaths,
    argv: Sequence[str] | None,
    services: Services | None,
    clock: Clock,
    instance: "Started | None" = None,
) -> int:
    bridge = None
    try:
        from PySide6.QtWidgets import QApplication

        from coin_report_trader.ui import create_main_window

        from .actions import AppActions
        from .bridge import UiBridge
        from .data_thread import DataWorker
        from .report_round import SESSIONS_FOLDER
        from .send_thread import SendWorker

        _ensure_application(argv)
        app = QApplication.instance()
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
            autostart=None if services is None else services.autostart,
            ai=None if services is None else services.ai,
            sessions_root=paths.root / SESSIONS_FOLDER,
            book=None if services is None else services.book,
            min_order=None if services is None else services.min_order,
        )
        sender.attach(worker)
        actions = AppActions(worker, sender)
        window = create_main_window(UiContext(__version__, display_path(paths.root), actions))
        bridge.set_apply(window.apply)
        if instance is not None:
            instance.set_window(window)
        window.show()
    except Exception as exc:
        if bridge is not None:
            bridge.release()
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
        bridge.release()
