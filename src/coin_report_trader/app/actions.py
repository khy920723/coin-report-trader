"""화면이 부르는 `UiActions`(M1 명세 3.8의 동작 표). 동작마다 맡은 스레드의 큐에 넣고 바로 돌아온다.

- 자료 스레드의 큐: `refresh_markets`, `save_settings`, `retry_candles`, `mark_seen`,
  `delete_alert`, `clear_alerts`
- 보내기 스레드의 큐: `save_webhook`, `delete_webhook`, `send_test`(자료 스레드를 거치지 않는다)

모두 Qt의 주 스레드에서 부른다.
"""

from datetime import date

from coin_report_trader.core import (
    AiChoice,
    AlertCategory,
    CandleUnit,
    HistoryTab,
    LiveSettings,
    Mode,
    TradeSettings,
)

from .data_thread import DataWorker
from .send_thread import SendWorker


class AppActions:
    """`UiActions`를 지킨다."""

    def __init__(self, data: DataWorker, send: SendWorker) -> None:
        self._data = data
        self._send = send

    def refresh_markets(self) -> None:
        self._data.refresh_markets()

    def save_settings(
        self,
        coins: tuple[str, ...],
        retention_days: int,
        *,
        first_setup: bool,
        candle_unit: CandleUnit | None = None,
        candle_count: int | None = None,
        ai_choices: tuple[AiChoice, ...] | None = None,
        autostart: bool | None = None,
        trade: TradeSettings | None = None,
        accept_below_stop: tuple[str, ...] = (),
        live: LiveSettings | None = None,
    ) -> None:
        self._data.save_settings(
            coins,
            retention_days,
            first_setup=first_setup,
            candle_unit=candle_unit,
            candle_count=candle_count,
            ai_choices=ai_choices,
            autostart=autostart,
            trade=trade,
            accept_below_stop=accept_below_stop,
            live=live,
        )

    def load_reports(self, market: str, mode: Mode | None = None) -> None:
        self._data.load_reports(market, mode)

    def load_report(self, report_id: int) -> None:
        self._data.load_report(report_id)

    def retry_candles(self) -> None:
        self._data.retry_candles()

    def mark_seen(self, alert_ids: tuple[int, ...]) -> None:
        self._data.mark_seen(alert_ids)

    def delete_alert(self, alert_id: int) -> None:
        self._data.delete_alert(alert_id)

    def clear_alerts(self, category: AlertCategory) -> None:
        self._data.clear_alerts(category)

    def save_webhook(self, category: AlertCategory, url: str) -> None:
        self._send.save_webhook(category, url)

    def delete_webhook(self, category: AlertCategory) -> None:
        self._send.delete_webhook(category)

    def send_test(self, category: AlertCategory) -> None:
        self._send.send_test(category)

    def pause(self) -> None:
        self._data.pause()

    def pause_and_sell_all(self) -> None:
        self._data.pause_and_sell_all()

    def resume_user(self) -> None:
        self._data.resume_user()

    def resume_error(self) -> None:
        self._data.resume_error()

    def load_history(
        self, tab: HistoryTab, market: str | None, day: date, mode: Mode | None = None
    ) -> None:
        self._data.load_history(tab, market, day, mode)

    def export_history(self, tab: HistoryTab, market: str | None, path: str) -> None:
        self._data.export_history(tab, market, path)

    def save_upbit_key(self, access: str, secret: str, created: date) -> None:
        self._data.save_upbit_key(access, secret, created)

    def delete_upbit_key(self) -> None:
        self._data.delete_upbit_key()

    def prepare_live_on(self) -> None:
        self._data.prepare_live_on()

    def turn_live_on(self) -> None:
        self._data.turn_live_on()

    def turn_live_off(self) -> None:
        self._data.turn_live_off()

    def account_visible(self, visible: bool) -> None:
        self._data.account_visible(visible)

    def refresh_account(self) -> None:
        self._data.refresh_account()

    def __repr__(self) -> str:
        return "AppActions()"
