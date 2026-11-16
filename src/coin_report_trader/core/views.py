"""화면이 받는 값(`UiUpdate`). 조립(`app`)이 만들어 `MainWindow.apply`로 보낸다."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from coin_report_trader.core.alerts import AlertCategory, Mode
from coin_report_trader.core.market import Market
from coin_report_trader.core.stats import CandleIndicator, PeriodStats, empty_periods

SAVE_TARGET_SETTINGS = "settings"
SAVE_TARGET_ALERT_DELETE = "alert_delete"


class LoadState(StrEnum):
    LOADING = "loading"
    """그 코인(또는 목록)의 받기가 돌고 있다."""
    READY = "ready"
    """마지막 받기가 성공했고 값이 있다."""
    NO_DATA = "no_data"
    """값이 하나도 없다."""
    FAILED = "failed"
    """마지막 받기가 실패했다."""


@dataclass(frozen=True)
class CoinView:
    """한 코인의 현황 칸. 각 `state`에 어떤 값이 담기는지는 M1 명세 3.4절의 묶음을 따른다."""

    market: str
    state: LoadState
    periods: tuple[PeriodStats, ...] = field(default_factory=empty_periods)
    """늘 다섯이고 3.5절 표의 차례다. 봉이 하나도 없으면 다섯 모두 값이 `None`이다."""
    indicator: CandleIndicator | None = None
    """일봉이 없으면 `None`이다."""
    last_incomplete: bool = False
    """저장된 마지막 일봉이 미완성인지."""
    fetched_at: datetime | None = None
    """마지막으로 성공한 받기가 끝난 때(요청과 저장이 오류 없이 끝난 것)."""
    error_name: str | None = None
    """마지막 받기가 실패했으면 그 오류 이름."""
    blocked_until: datetime | None = None
    """일시 차단 중이면 다시 받는 때."""


@dataclass(frozen=True)
class MarketsView:
    state: LoadState
    markets: tuple[Market, ...] = ()
    error_name: str | None = None


@dataclass(frozen=True)
class SettingsView:
    coins: tuple[str, ...] = ()
    """마켓 코드, 설정 목록 순서."""
    retention_days: int | None = None
    """7, 30, 90 가운데 하나. 고르기 전에는 `None`."""
    first_setup_done: bool = False


@dataclass(frozen=True)
class AlertDetail:
    """오류 알림과 기타 알림의 자세한 글."""

    stage: str
    coin: str | None
    error_name: str
    content: str
    after: str
    """그 뒤에 한 일."""


@dataclass(frozen=True)
class AlertView:
    id: int
    created_at: datetime
    mode: Mode
    category: AlertCategory
    code: str
    """알림 번호. 예: `A-1`."""
    coin: str | None
    """마켓 코드."""
    text: str
    """앞에 코인을 붙이지 않은 글."""
    detail: AlertDetail | None = None
    seen: bool = False
    discord_failed: bool = False
    """`discord_state`가 `"failed"`인 것."""


def _no_counts() -> Mapping[AlertCategory, int]:
    return MappingProxyType({})


@dataclass(frozen=True)
class AlertsView:
    alerts: tuple[AlertView, ...]
    """새것이 앞."""
    unseen: Mapping[AlertCategory, int] = field(default_factory=_no_counts)
    """분류마다 아직 보지 않은 알림의 수."""
    state: LoadState = LoadState.READY


@dataclass(frozen=True)
class WebhookView:
    """알림센터 탭의 디스코드 웹후크 주소 칸. 주소 자체는 담지 않는다."""

    category: AlertCategory
    saved: bool
    save_message: str | None = None
    """저장·지우기의 칸 아래 글."""
    test_message: str | None = None
    """확인용 글의 칸 아래 글. 두 글은 따로 담아 서로 덮어쓰지 않는다."""


@dataclass(frozen=True)
class SaveResult:
    target: str
    """`SAVE_TARGET_SETTINGS`나 `SAVE_TARGET_ALERT_DELETE`. 주소 칸의 결과는 쓰지 않는다."""
    ok: bool
    message: str | None = None


UiUpdate = CoinView | MarketsView | SettingsView | AlertsView | WebhookView | SaveResult
