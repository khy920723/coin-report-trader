"""알림 분류와 모드."""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class AlertCategory(StrEnum):
    """알림 분류. 정의 순서가 알림센터 탭의 순서다."""

    TRADE = "trade"
    REPORT = "report"
    LIMIT = "limit"
    HOLDING = "holding"
    ERROR = "error"
    OTHER = "other"


ALERT_CATEGORY_TITLES: Mapping[AlertCategory, str] = MappingProxyType(
    {
        AlertCategory.TRADE: "매매 알림",
        AlertCategory.REPORT: "보고서 알림",
        AlertCategory.LIMIT: "제한 알림",
        AlertCategory.HOLDING: "보유 알림",
        AlertCategory.ERROR: "오류 알림",
        AlertCategory.OTHER: "기타 알림",
    }
)


class Mode(StrEnum):
    PAPER = "paper"
    LIVE = "live"


MODE_TITLES: Mapping[Mode, str] = MappingProxyType({Mode.PAPER: "모의매매", Mode.LIVE: "실거래"})
