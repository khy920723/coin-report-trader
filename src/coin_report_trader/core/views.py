"""화면이 받는 값(`UiUpdate`). 조립(`app`)이 만들어 `MainWindow.apply`로 보낸다."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from coin_report_trader.core.ai import (
    AiChoice,
    AiSlot,
    AiStatus,
    Belief,
    CandleJudgment,
    Direction,
    ReportState,
    SearchJudgment,
    TokenUsage,
    Verdict,
)
from coin_report_trader.core.alerts import AlertCategory, Mode
from coin_report_trader.core.market import CandleUnit, Market
from coin_report_trader.core.stats import BarStats

SAVE_TARGET_SETTINGS = "settings"
SAVE_TARGET_ALERT_DELETE = "alert_delete"
SAVE_TARGET_REPORT = "report"
"""보고서를 읽지 못했을 때만 쓴다(M2 명세 3.12.1)."""


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
    last_incomplete: bool = False
    """저장된 마지막 봉(고른 단위)이 미완성인지."""
    fetched_at: datetime | None = None
    """마지막으로 성공한 받기가 끝난 때(요청과 저장이 오류 없이 끝난 것)."""
    error_name: str | None = None
    """마지막 받기가 실패했으면 그 오류 이름."""
    blocked_until: datetime | None = None
    """일시 차단 중이면 다시 받는 때."""
    as_of: datetime | None = None
    """조립이 이 값을 만든 때(`Clock.now()`). 화면은 "오늘"의 기준으로만 쓴다. 없으면 `None`."""
    stats: BarStats | None = None
    """그 코인의 봉 통계(3.4). 조립이 늘 담는다(봉이 하나도 없으면 `bar_count`가 0이고 값이 모두
    `None`). 기본값 `None`은 화면 시험이 값 없이 만들 때만 쓴다."""


@dataclass(frozen=True)
class MarketsView:
    state: LoadState
    markets: tuple[Market, ...] = ()
    error_name: str | None = None
    blocked_until: datetime | None = None
    """일시 차단 중이면 다시 받을 수 있는 때(3.4의 "일시 차단 중" 묶음)."""
    as_of: datetime | None = None
    """조립이 이 값을 만든 때(`Clock.now()`). `CoinView.as_of`와 같다."""


@dataclass(frozen=True)
class SettingsView:
    coins: tuple[str, ...] = ()
    """마켓 코드, 설정 목록 순서."""
    retention_days: int | None = None
    """7, 30, 90 가운데 하나. 고르기 전에는 `None`."""
    first_setup_done: bool = False
    state: LoadState = LoadState.READY
    """`READY`(읽었다)나 `FAILED`(켤 때 설정을 읽지 못했다). `FAILED`면 다른 세 필드는 뜻이 없다."""
    candle_unit: CandleUnit | None = None
    """고른 봉 단위(1단계 보완). 저장되지 않았으면 `None`."""
    candle_count: int | None = None
    """고른 봉 개수(1단계 보완). 저장되지 않았으면 `None`."""
    ai_choices: tuple[AiChoice, ...] = ()
    """저장된 AI의 칸만 담는다. 차례는 `AiSlot`의 차례(M2 명세 3.4)."""
    autostart: bool = False
    """PC를 켜면 자동으로 시작할지(저장된 값. M1 명세 3.16.3). 처음 값은 꺼짐."""
    autostart_supported: bool = False
    """이 실행에서 레지스트리를 쓸 수 있는지. 자료 스레드가 `supported()`로 채운다."""
    autostart_error: str | None = None
    """마지막 맞추기의 실패 글. 없으면 `None`."""


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
    report_id: int | None = None
    """A-26이 가리키는 보고서의 `reports.id`. 다른 알림은 `None`(M2 명세 3.4, 3.10)."""


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
    unreadable: bool = False
    """켤 때 보관소에서 그 분류의 주소를 읽지 못했으면 참(M2 명세 3.12.7)."""


@dataclass(frozen=True)
class SaveResult:
    target: str
    """`SAVE_TARGET_SETTINGS`나 `SAVE_TARGET_ALERT_DELETE`. 주소 칸의 결과는 쓰지 않는다."""
    ok: bool
    message: str | None = None


@dataclass(frozen=True)
class AiStatusView:
    """AI의 상태(M2 명세 3.4)."""

    status: AiStatus
    version: str | None
    """읽은 버전 글. 못 읽었으면 `None`."""
    as_of: datetime | None = None


@dataclass(frozen=True)
class RoundItem:
    """보고서 목록의 한 줄(한 회차)."""

    report_id: int
    round_number: int
    close_utc: datetime
    """그 회차의 봉 마감 시각."""
    state: ReportState
    direction: Direction | None
    failure: str | None
    """실패 종류, 쉰 까닭, 오류 이름 가운데 하나의 글."""


@dataclass(frozen=True)
class ReportListView:
    market: str
    items: tuple[RoundItem, ...]
    """새것이 앞. 많아야 `REPORT_LIST_LIMIT`개."""
    state: LoadState
    """`LOADING`, `READY`, `FAILED`."""


@dataclass(frozen=True)
class EvidenceItem:
    part: AiSlot
    """세 부분 가운데 하나."""
    seq: int
    text: str


@dataclass(frozen=True)
class ComparedItem:
    part: AiSlot
    seq: int
    text: str
    """앞 회차의 근거 글."""
    verdict: Verdict


@dataclass(frozen=True)
class ChecklistItem:
    seq: int
    text: str
    belief: Belief
    compared: int
    hits: int


@dataclass(frozen=True)
class SourceItem:
    seq: int
    title: str
    url: str


@dataclass(frozen=True)
class LastResult:
    round_number: int
    """지난 판단의 회차."""
    predicted_price: Decimal
    actual_price: Decimal | None
    diff: Decimal | None
    """비율(3.9)."""
    hit: Verdict | None
    """`HIT`나 `MISS`. 셀 수 없으면 `None`."""


@dataclass(frozen=True)
class ReportView:
    """한 회차의 보고서(M2 명세 3.4)."""

    report_id: int
    market: str
    round_number: int
    close_utc: datetime
    state: ReportState
    failure: str | None
    base_price: Decimal | None
    """최종 예측에 준 현재가."""
    search_judgment: SearchJudgment | None
    candle_judgment: CandleJudgment | None
    direction: Direction | None
    predicted_price: Decimal | None
    predicted_reason: str | None
    buy_price: Decimal | None
    sell_price: Decimal | None
    result_text: str | None
    last_result: LastResult | None
    compared: tuple[ComparedItem, ...]
    checklist: tuple[ChecklistItem, ...]
    evidence: tuple[EvidenceItem, ...]
    sources: tuple[SourceItem, ...]
    tokens: TokenUsage | None
    elapsed_report_seconds: int | None
    as_of: datetime | None = None


UiUpdate = (
    CoinView
    | MarketsView
    | SettingsView
    | AlertsView
    | WebhookView
    | SaveResult
    | AiStatusView
    | ReportListView
    | ReportView
)
