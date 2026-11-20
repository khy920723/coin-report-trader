"""핵심 흐름 시험: 처음 설정부터 디스코드 보내기까지 실제 앱의 조립을 한 번에 돌린다.

- 앱은 `run_gui`(창, 자료 스레드, 보내기 스레드, 알림센터)를 그대로 쓴다. 바깥만 `tests/fakes/`의
  가짜(시세, 디스코드, 보관소, 시계)로 바꾼다. 네트워크 차단(`tests/conftest.py`)은 그대로다.
- 사람이 하는 일은 화면의 단추를 눌러서 한다. 확인은 화면에 보이는 글과 가짜가 받은 것으로 한다.
- 자료 폴더는 `tmp_path` 아래로 바꾼다(실제 사용자 폴더에 쓰지 않는다). 주소처럼 생긴 글은 실행
  중에 이어 붙여 만든다(M1 명세 3.13).

차례:
1. 처음 설정 화면에서 코인 목록이 오면 코인 하나와 보관 기간 30일을 고르고 "저장하고 시작"을 누른다.
2. 봉을 받아 현황 화면의 기간별 통계 표에 다섯 기간의 글이 보인다.
3. 60분 뒤의 받기에서 일봉 요청이 "네트워크 끊김"을 내 A-1이 생기고, 알림센터의 오류 알림 탭에
   그 줄이 보인다.
4. 오류 알림 탭에는 디스코드 주소가 있으므로, 가짜 디스코드가 그 주소로 그 글을 받는다.
"""

import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fakes.clock import DEFAULT_START, FakeClock
from fakes.discord import FakeDiscord
from fakes.quote import FakeQuoteSource, make_candles
from fakes.vault import FakeVault
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QRadioButton, QWidget

from coin_report_trader import ui
from coin_report_trader.app import data_thread, run_gui
from coin_report_trader.app.services import Services
from coin_report_trader.core import AlertCategory, CandleUnit, Market, QuoteError, ScreenId
from coin_report_trader.core.texts import ERROR_NETWORK
from coin_report_trader.ui import current_screen, screen_widget
from coin_report_trader.vault import webhook_secret_name

AAA = "KRW-AAA"
LIMIT_SECONDS = 25.0
"""이 시험 하나가 넘지 않을 실제 시간(안전 타이머). 넘으면 실패로 끝낸다."""


def _webhook_url() -> str:
    return "https://" + "discord.com/api/webhooks/" + "4" * 18 + "/" + ("e2eToken" * 9)[:66]


def _find(parent: QWidget, kind: type, name: str) -> Any:
    return parent.findChild(kind, name)


def _shown(widget: QWidget | None, window: QWidget) -> bool:
    return widget is not None and widget.isVisibleTo(window)


class Driver:
    """이벤트 루프 안에서 차례를 하나씩 밟는다. 조건이 서면 그 일을 하고 다음 차례로 간다."""

    def __init__(self, app: QApplication, steps: list[tuple[str, Callable[[], bool]]]) -> None:
        self.steps = steps
        self.done: list[str] = []
        self.error: BaseException | None = None
        self._app = app
        self._poll = QTimer()
        self._poll.setInterval(10)
        self._poll.timeout.connect(self._tick)
        self._safety = QTimer()
        self._safety.setSingleShot(True)
        self._safety.timeout.connect(app.quit)

    def start(self) -> None:
        self._poll.start()
        self._safety.start(int(LIMIT_SECONDS * 1000))

    def stop(self) -> None:
        self._poll.stop()
        self._safety.stop()

    def _tick(self) -> None:
        if not self.steps:
            self._poll.stop()
            self._app.quit()
            return
        name, step = self.steps[0]
        try:
            finished = step()
        except BaseException as exc:
            self.error = exc
            self._poll.stop()
            self._app.quit()
            return
        if finished:
            self.done.append(name)
            self.steps.pop(0)


@pytest.fixture
def app_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, qapp: QApplication) -> QApplication:
    home = tmp_path / "share"
    home.mkdir()
    monkeypatch.setenv("LOCALAPPDATA" if sys.platform == "win32" else "XDG_DATA_HOME", str(home))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return qapp


def test_first_setup_to_stats_to_an_alert_in_the_alert_center_and_on_discord(
    app_env: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    today = DEFAULT_START.replace(hour=0, minute=0)
    quote = FakeQuoteSource(
        markets=[Market(AAA, "에이에이에이", False), Market("KRW-BBB", "비비비", False)],
        clock=clock,
    )
    quote.set_candles(AAA, CandleUnit.DAY, make_candles(AAA, CandleUnit.DAY, 40, last_start=today))
    quote.set_candles(
        AAA,
        CandleUnit.MINUTE_60,
        make_candles(AAA, CandleUnit.MINUTE_60, 200, last_start=DEFAULT_START.replace(minute=0)),
    )
    url = _webhook_url()
    discord = FakeDiscord()
    vault = FakeVault({webhook_secret_name(AlertCategory.ERROR): url})
    services = Services(quote=quote, webhook=discord, vault=vault, clock=clock)

    made: list[Any] = []
    real_create = ui.create_main_window

    def create(context: object) -> object:
        window = real_create(context)
        made.append(window)
        return window

    monkeypatch.setattr(ui, "create_main_window", create)
    workers: list[Any] = []

    class SeenDataWorker(data_thread.DataWorker):
        """조립이 만든 자료 스레드를 붙잡는다(쉬는지 보는 데만 쓴다. 동작은 그대로다)."""

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            workers.append(self)

    monkeypatch.setattr(data_thread, "DataWorker", SeenDataWorker)
    seen: dict[str, Any] = {}

    def first_setup_is_ready() -> bool:
        if not made:
            return False
        first = screen_widget(made[0], ScreenId.FIRST_SETUP)
        box = _find(first, QWidget, f"firstSetupCoin_{AAA}")
        if box is None or not box.isEnabled():
            return False
        box.click()
        _find(first, QRadioButton, "firstSetupRetention30").click()
        start = _find(first, QPushButton, "firstSetupStart")
        if not start.isEnabled():
            raise AssertionError("코인과 보관 기간을 골랐는데 저장 단추를 누를 수 없다")
        start.click()
        return True

    def stats_are_shown() -> bool:
        window = made[0]
        if current_screen(window) is not ScreenId.STATUS:
            return False
        status = screen_widget(window, ScreenId.STATUS)
        table = _find(status, QWidget, "statusStatsTable")
        if not _shown(table, window):
            return False
        rows = [
            [_find(status, QLabel, f"statusStatsCell_{row}_{col}").text() for col in range(3)]
            for row in range(5)
        ]
        if not all(all(cell for cell in row) for row in rows):
            return False
        seen["stats"] = rows
        return True

    def an_hour_passes_once_the_data_thread_rests() -> bool:
        # 받기가 다 끝나 자료 스레드가 다음 받는 때를 기다릴 때 시각을 옮긴다. 그 전에 옮기면 다음
        # 받는 때가 옮긴 시각에서 다시 60분 뒤로 잡혀 받기가 오지 않는다(시험의 경합).
        if not workers[0].wait_idle(0.002):
            return False
        # 다음 (라)의 받기(60분 뒤)에서 일봉 요청이 실패하게 하고 시각을 옮긴다.
        quote.script(QuoteError(ERROR_NETWORK), kind="candles", market=AAA, unit=CandleUnit.DAY)
        clock.advance(3600)
        return True

    def the_alert_is_in_the_error_tab_and_on_discord() -> bool:
        if not discord.alerts:
            return False
        window = made[0]
        status = screen_widget(window, ScreenId.STATUS)
        if "tab" not in seen:
            # 사용자처럼 오류 알림 탭을 한 번 누른다. 새로 그린 줄은 다음 이벤트 차례에
            # 보이므로 보는 것은 다음 차례부터 한다.
            _find(status, QPushButton, f"alertTab_{AlertCategory.ERROR.value}").click()
            seen["tab"] = True
            return False
        lines = [
            label.text()
            for label in status.findChildren(QLabel)
            if label.objectName().startswith("alertLine_") and label.isVisibleTo(window)
        ]
        if not any(ERROR_NETWORK in text for text in lines):
            return False
        seen["lines"] = lines
        return True

    driver = Driver(
        app_env,
        [
            ("처음 설정", first_setup_is_ready),
            ("통계", stats_are_shown),
            ("60분 뒤", an_hour_passes_once_the_data_thread_rests),
            ("알림과 디스코드", the_alert_is_in_the_error_tab_and_on_discord),
        ],
    )
    QTimer.singleShot(0, driver.start)
    began = time.monotonic()
    try:
        code = run_gui([], services=services)
    finally:
        driver.stop()
        discord.release()
    elapsed = time.monotonic() - began
    if driver.error is not None:
        raise driver.error
    assert driver.done == ["처음 설정", "통계", "60분 뒤", "알림과 디스코드"], driver.steps[:1]
    assert code == 0
    assert elapsed < 30.0

    # 2. 통계 표: 다섯 기간의 이름과 봉 수, 변화율이 보인다.
    assert [row[0] for row in seen["stats"]] == ["전체", "1년", "3개월", "1개월", "1주"]
    # 3. 알림센터의 오류 알림 탭에 그 줄이 보인다(코인과 오류 이름).
    (line,) = [text for text in seen["lines"] if ERROR_NETWORK in text]
    assert "AAA" in line
    # 4. 가짜 디스코드가 그 분류의 주소로 그 글을 받았다(첫 줄은 모드와 분류).
    (message,) = discord.alerts
    assert message.url == url
    first, second, *_ = message.text.split("\n")
    assert first == "[모의매매] 오류 알림"
    assert second.startswith("AAA: ")
    assert ERROR_NETWORK in second
