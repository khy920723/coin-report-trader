"""공용 타입, 동작, 경계(Protocol), 상수. 표준 라이브러리만 import한다."""

from coin_report_trader.core.alerts import (
    ALERT_CATEGORY_TITLES,
    MODE_TITLES,
    AlertCategory,
    Mode,
)
from coin_report_trader.core.clock import KST, Clock, SystemClock
from coin_report_trader.core.constants import APP_ID, WINDOW_TITLE
from coin_report_trader.core.context import UiContext
from coin_report_trader.core.errors import (
    QuoteError,
    SecretStoreError,
    StorageError,
    WebhookError,
)
from coin_report_trader.core.market import (
    CANDLE_COUNTS,
    CANDLE_UNIT_LENGTHS,
    CANDLE_UNIT_TITLES,
    DEFAULT_CANDLE_COUNT,
    Candle,
    CandleUnit,
    Market,
    coin_label,
)
from coin_report_trader.core.ports import QuoteSource, SecretStore, UiActions, WebhookSender
from coin_report_trader.core.redaction import MASK, is_webhook_url, redact, shorten_path
from coin_report_trader.core.screens import SCREEN_TITLES, ScreenId
from coin_report_trader.core.stats import (
    BAR_STATS_COLUMNS,
    BAR_STATS_TITLES,
    INSUFFICIENT_TEXT,
    BarStats,
    empty_bar_stats,
    format_percent,
    format_price,
)
from coin_report_trader.core.tls import make_tls_context
from coin_report_trader.core.views import (
    SAVE_TARGET_ALERT_DELETE,
    SAVE_TARGET_SETTINGS,
    AlertDetail,
    AlertsView,
    AlertView,
    CoinView,
    LoadState,
    MarketsView,
    SaveResult,
    SettingsView,
    UiUpdate,
    WebhookView,
)

__all__ = [
    "ALERT_CATEGORY_TITLES",
    "APP_ID",
    "BAR_STATS_COLUMNS",
    "BAR_STATS_TITLES",
    "CANDLE_COUNTS",
    "CANDLE_UNIT_LENGTHS",
    "CANDLE_UNIT_TITLES",
    "DEFAULT_CANDLE_COUNT",
    "INSUFFICIENT_TEXT",
    "KST",
    "MASK",
    "MODE_TITLES",
    "SAVE_TARGET_ALERT_DELETE",
    "SAVE_TARGET_SETTINGS",
    "SCREEN_TITLES",
    "WINDOW_TITLE",
    "AlertCategory",
    "AlertDetail",
    "AlertView",
    "AlertsView",
    "BarStats",
    "Candle",
    "CandleUnit",
    "Clock",
    "CoinView",
    "LoadState",
    "Market",
    "MarketsView",
    "Mode",
    "QuoteError",
    "QuoteSource",
    "SaveResult",
    "ScreenId",
    "SecretStore",
    "SecretStoreError",
    "SettingsView",
    "StorageError",
    "SystemClock",
    "UiActions",
    "UiContext",
    "UiUpdate",
    "WebhookError",
    "WebhookSender",
    "WebhookView",
    "coin_label",
    "empty_bar_stats",
    "format_percent",
    "format_price",
    "is_webhook_url",
    "make_tls_context",
    "redact",
    "shorten_path",
]
