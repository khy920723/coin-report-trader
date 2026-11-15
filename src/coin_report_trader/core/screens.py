"""다섯 화면의 식별자와 이름. 정의 순서가 화면 순서다."""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class ScreenId(StrEnum):
    FIRST_SETUP = "first_setup"
    STATUS = "status"
    REPORTS = "reports"
    HISTORY = "history"
    SETTINGS = "settings"


SCREEN_TITLES: Mapping[ScreenId, str] = MappingProxyType(
    {
        ScreenId.FIRST_SETUP: "처음 설정",
        ScreenId.STATUS: "현황",
        ScreenId.REPORTS: "보고서",
        ScreenId.HISTORY: "기록",
        ScreenId.SETTINGS: "설정",
    }
)
