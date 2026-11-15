"""일반 실행: 자료 폴더, 로그, DB를 준비하고 창을 띄운다. 창을 닫으면 끝난다(트레이는 M4)."""

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
from coin_report_trader.core import APP_ID, StorageError, UiContext
from coin_report_trader.storage.database import SCHEMA_VERSION, open_database, schema_version

log = logging.getLogger(__name__)


def run_gui(argv: Sequence[str] | None = None) -> int:
    """자료 폴더를 만들고, 로그를 열고, DB를 열어 확인한 뒤 창을 띄운다.

    `QApplication.instance()`가 있으면 그것을 쓰고 없을 때만 만든다. 이벤트 루프가 끝나면
    종료 코드를 돌려준다. 준비나 창 만들기에서 난 오류는 로그에 쓰고 다시 낸다.
    """
    paths = resolve_app_paths()
    paths.ensure()
    handler = configure_logging(paths.log_file)
    code = 1
    try:
        log.info("시작: 버전 %s, 자료 폴더 %s", __version__, display_path(paths.root))
        _check_database(paths)
        code = _run_window(paths, argv)
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


def _run_window(paths: AppPaths, argv: Sequence[str] | None) -> int:
    from PySide6.QtWidgets import QApplication

    from coin_report_trader.ui import create_main_window

    app = QApplication.instance()
    if app is None:
        program = sys.argv[0] if sys.argv and sys.argv[0] else APP_ID
        app = QApplication([program, *(argv or ())])
    assert isinstance(app, QApplication)
    app.setQuitOnLastWindowClosed(True)  # 창을 닫으면 프로그램이 끝난다.
    app.setApplicationName(APP_ID)
    app.setApplicationVersion(__version__)
    window = create_main_window(UiContext(__version__, display_path(paths.root)))
    window.show()
    try:
        return int(app.exec())
    finally:
        window.close()
        window.deleteLater()
