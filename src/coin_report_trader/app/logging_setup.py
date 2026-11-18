"""로그 파일. M0에는 시작과 종료, 오류만 쓴다.

앱 로거(`coin_report_trader`)에만 붙이고 루트 로거는 건드리지 않는다. 글은 모두 `redact`를 거친다
(웹후크 주소는 가리고 경로는 줄인다).
"""

import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from coin_report_trader.core import redact

LOGGER_NAME = "coin_report_trader"
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
LOG_MAX_BYTES = 1024 * 1024
LOG_BACKUP_COUNT = 3


class RedactingFormatter(logging.Formatter):
    """로그에 쓰는 모든 글(인자를 채운 뒤의 글, 예외 글, 스택)을 `redact`로 거른다(M1 명세 3.10).

    글마다 따로 거른다(`redact`는 2,000자에서 자르므로 한 줄 전체를 한 번에 거르지 않는다).
    """

    def format(self, record: logging.LogRecord) -> str:
        message = redact(record.getMessage())
        shown = logging.makeLogRecord(record.__dict__)
        shown.msg, shown.args, shown.exc_info, shown.exc_text, shown.stack_info = (
            message, None, None, None, None,
        )  # fmt: skip
        text = super().format(shown)
        if record.exc_info:
            text += "\n" + redact(self.formatException(record.exc_info))
        elif record.exc_text:
            text += "\n" + redact(record.exc_text)
        if record.stack_info:
            text += "\n" + redact(self.formatStack(record.stack_info))
        return text


class AppLogHandler(RotatingFileHandler):
    """이 앱이 붙인 로그 파일 핸들러."""


def configure_logging(log_file: Path, level: int = logging.INFO) -> AppLogHandler:
    """앱 로거에 로그 파일을 붙인다. 폴더가 있다고 보고 파일을 연다."""
    handler = AppLogHandler(
        log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    formatter = RedactingFormatter(LOG_FORMAT, LOG_DATE_FORMAT)
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
