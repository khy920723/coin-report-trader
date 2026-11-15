"""로그 파일. M0에는 시작과 종료, 오류만 쓴다.

앱 로거(`coin_report_trader`)에만 붙이고 루트 로거는 건드리지 않는다. 경로는 줄인 것만 쓴다.
"""

import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "coin_report_trader"
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
LOG_MAX_BYTES = 1024 * 1024
LOG_BACKUP_COUNT = 3


class AppLogHandler(RotatingFileHandler):
    """이 앱이 붙인 로그 파일 핸들러."""


def configure_logging(log_file: Path, level: int = logging.INFO) -> AppLogHandler:
    """앱 로거에 로그 파일을 붙인다. 폴더가 있다고 보고 파일을 연다."""
    handler = AppLogHandler(
        log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    formatter = logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT)
    formatter.converter = time.gmtime  # 시각은 UTC로 쓴다.
    handler.setFormatter(formatter)
    logger = logging.getLogger(LOGGER_NAME)
    logger.addHandler(handler)
    logger.setLevel(level)
    return handler


def remove_log_handler(handler: AppLogHandler) -> None:
    """붙인 핸들러를 떼고 파일을 닫는다(Windows에서 폴더를 지울 수 있게)."""
    logging.getLogger(LOGGER_NAME).removeHandler(handler)
    handler.close()
