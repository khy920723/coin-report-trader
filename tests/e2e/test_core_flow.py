"""핵심 흐름 시험: 처음 설정부터 디스코드 보내기까지 실제 앱의 조립을 한 번에 돌린다.

- 앱은 `run_gui`(창, 자료 스레드, 보내기 스레드, 알림센터)를 그대로 쓴다. 바깥만 `tests/fakes/`의
  가짜(시세, 디스코드, 보관소, 시계)로 바꾼다. 네트워크 차단(`tests/conftest.py`)은 그대로다.
- 사람이 하는 일은 화면의 단추를 눌러서 한다. 확인은 화면에 보이는 글과 가짜가 받은 것으로 한다.
- 자료 폴더는 `tmp_path` 아래로 바꾼다(실제 사용자 폴더에 쓰지 않는다). 주소처럼 생긴 글은 실행
  중에 이어 붙여 만든다(M1 명세 3.13).

차례:
1. 처음 설정 화면에서 코인 목록이 오면 코인 하나와 보관 기간 30일을 고르고 "저장하고 시작"(3단계는
   "모의매매 시작". 단추 이름은 `firstSetupStart` 그대로)을 누른다.
   봉 단위 칸(`firstSetupUnit_minute60`, M1 명세 3.14.1. 37번부터 있다)이 있으면 60분봉도 고른다.
   2단계의 여덟 칸(`firstSetupAiModel_{칸 값}`, `firstSetupAiEffort_{칸 값}`, M2 명세 3.12.2.
   13번부터 있다)이 있으면 모두 빈 항목이 아닌 값을 고른다. `services`에는 `FakeAiRunner`
   (`check()`는 `READY`, 정상 답)를 넣는다(M2 명세 3.15. 15번 뒤의 회차가 진짜 실행기를 부르지
   않게).
2. 봉을 받아 현황 화면의 봉 통계 표에 항목마다(`core.BAR_STATS_COLUMNS`의 `bar_count`부터
   `low`까지) 이름과 값이 보이고 "전체 변화"는 "%"로 끝난다. 설정 화면에는 고른 보관 기간(30일)과
   코인이 남아 있다.
3. 다음 받기에서 60분봉 요청이 "네트워크 끊김"을 내 A-1이 생기고, 알림센터의 오류 알림 탭에
   그 줄이 보인다. 다음 받기는 39번 전에는 60분 뒤의 받기이고, 39번 뒤에는 다음 60분봉이 끝나는
   때 + 10초의 받기다(3.5.3). 시계는 그 둘을 모두 넘게 옮긴다(`NEXT_FETCH_SECONDS`).
4. 오류 알림 탭에는 디스코드 주소가 있으므로, 가짜 디스코드가 그 주소로 그 글을 받는다.
"""

import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from acceptance.m3_support import fill_trade_fields, plant_prices
from fakes.ai import FakeAiRunner
from fakes.clock import DEFAULT_START, FakeClock
from fakes.discord import FakeDiscord
from fakes.quote import FakeQuoteSource, make_candles
from fakes.vault import FakeVault
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QCheckBox,
    QComboBox,
    QLabel,
    QListWidget,
    QPushButton,
    QRadioButton,
    QWidget,
)

from coin_report_trader import ui
from coin_report_trader.app import data_thread, run_gui
from coin_report_trader.app.services import Services
from coin_report_trader.core import (
    BAR_STATS_COLUMNS,
    BAR_STATS_TITLES,
    INSUFFICIENT_TEXT,
    AiSlot,
    AlertCategory,
    CandleUnit,
    Market,
    QuoteError,
    ScreenId,
)
from coin_report_trader.core.texts import ERROR_NETWORK
from coin_report_trader.ui import current_screen, screen_widget
from coin_report_trader.vault import webhook_secret_name

AAA = "KRW-AAA"
TABLE_COLUMNS = BAR_STATS_COLUMNS[BAR_STATS_COLUMNS.index("bar_count") :]
"""봉 통계 표의 줄(`bar_count`부터 `low`까지, 3.14.5)."""
LIMIT_SECONDS = 25.0
"""이 시험 하나가 넘지 않을 실제 시간(안전 타이머). 넘으면 실패로 끝낸다."""
NEXT_FETCH_SECONDS = 3660
"""다음 받기가 오게 시계를 옮기는 길이(61분). 가짜 시계는 03:00 UTC(`DEFAULT_START`)에서 시작한다.

- 39번 전: 받기가 끝난 때부터 60분(3600초) 뒤에 다시 받는다.
- 39번 뒤: 다음 60분봉이 끝나는 때(04:00 UTC) + 10초에 받는다(M1 명세 3.5.3). 처음 받기가 03:00
  근처에 끝나므로 3610초 남짓이다.

61분은 둘을 모두 넘고, 그 다음 받기(39번 전 120분, 39번 뒤 05:00:10)에는 닿지 않는다.
"""


def _webhook_url() -> str:
    return "https://" + "discord.com/api/webhooks/" + "4" * 18 + "/" + ("e2eToken" * 9)[:66]


def _find(parent: QWidget, kind: type, name: str) -> Any:
    return parent.findChild(kind, name)


def _shown(widget: QWidget | None, window: QWidget) -> bool:
    return widget is not None and widget.isVisibleTo(window)


def _choose_ai_slots(parent: QWidget) -> int:
    """처음 설정의 여덟 칸(M2 명세 3.12.2. 13번부터 있다)이 있으면 모두 빈 항목이 아닌 첫 값을
    고른다.

    고른 칸의 수(0이나 8)를 돌려준다. 일부만 있으면 13번이 덜 된 것이라 실패로 본다.
    """
    names = [f"firstSetupAi{kind}_{slot.value}" for slot in AiSlot for kind in ("Model", "Effort")]
    combos = [combo for name in names if (combo := _find(parent, QComboBox, name)) is not None]
    if len(combos) not in (0, len(names)):
        raise AssertionError(f"처음 설정의 여덟 칸 가운데 {len(combos)}칸만 있다")
    for combo in combos:
        index = next((i for i in range(combo.count()) if combo.itemText(i)), None)
        if index is None:
            raise AssertionError(f"{combo.objectName()}에 고를 값이 없다")
        combo.setCurrentIndex(index)
    return len(combos)


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
        make_candles(AAA, CandleUnit.MINUTE_60, 300, last_start=DEFAULT_START.replace(minute=0)),
    )
    plant_prices(quote, (AAA,))  # 23번 뒤의 손절 점검이 고른 코인의 현재가를 읽는다(M3 명세 3.9)
    url = _webhook_url()
    discord = FakeDiscord()
    vault = FakeVault({webhook_secret_name(AlertCategory.ERROR): url})
    services = Services(
        quote=quote, webhook=discord, vault=vault, clock=clock, ai=FakeAiRunner(), book=quote
    )

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
        unit = _find(first, QAbstractButton, f"firstSetupUnit_{CandleUnit.MINUTE_60.value}")
        if unit is not None:  # 37번부터 봉 단위 칸이 있다(3.14.1). 봉 개수는 "200개"가 골라져 있다
            unit.click()
        _choose_ai_slots(first)  # 13번부터 여덟 칸이 있다(M2 명세 3.12.2)
        fill_trade_fields(first)  # 17번부터 매매 칸과 확인란이 있다(M3 명세 3.17.5)
        start = _find(first, QPushButton, "firstSetupStart")
        if not start.isEnabled():
            raise AssertionError(
                "코인, 보관 기간(있으면 봉 단위, 여덟 칸, 매매 칸, 확인란)을 골랐는데 시작 단추를"
                " 누를 수 없다"
            )
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
            [_find(status, QLabel, f"statusStatsCell_{row}_{col}").text() for col in range(2)]
            for row in range(len(TABLE_COLUMNS))
        ]
        if not all(all(cell for cell in row) for row in rows):
            return False
        seen["stats"] = rows
        return True

    def the_settings_keep_what_was_chosen() -> bool:
        # 처음 설정에서 고른 보관 기간과 코인이 설정 화면에 남아 있는지 적는다(값은 끝에서
        # 본다). 통계가 보인 때에는 저장한 설정(`SettingsView`)이 이미 창에 왔으므로(자료
        # 스레드가 저장 뒤에 받는다) 기다리지 않고 한 번에 읽는다. 그래서 틀리면 안전 시간이
        # 아니라 끝의 확인에서 실패한다.
        settings = screen_widget(made[0], ScreenId.SETTINGS)
        radio = _find(settings, QRadioButton, "settingsRetention30")
        box = _find(settings, QCheckBox, f"settingsCoin_{AAA}")
        chosen = _find(settings, QListWidget, "settingsCoinsSelected")
        picked = [chosen.item(i).text() for i in range(chosen.count())] if chosen else []
        seen["settings"] = {
            "retention_30": radio is not None and radio.isChecked(),
            "coin": (box is not None and box.isChecked()) or any("AAA" in t for t in picked),
        }
        return True

    def the_next_fetch_time_comes_once_the_data_thread_rests() -> bool:
        # 받기가 다 끝나 자료 스레드가 다음 받는 때를 기다릴 때 시각을 옮긴다. 그 전에 옮기면 다음
        # 받는 때가 옮긴 시각에서 다시 60분 뒤로 잡혀 받기가 오지 않는다(시험의 경합).
        if not workers[0].wait_idle(0.002):
            return False
        # 다음 (라)의 받기에서 60분봉 요청이 실패하게 하고 시각을 옮긴다. 39번 뒤에는 고른 봉 단위
        # (60분봉)만 받으므로 일봉이 아니라 60분봉의 요청을 실패하게 한다(3.5.3).
        quote.script(
            QuoteError(ERROR_NETWORK), kind="candles", market=AAA, unit=CandleUnit.MINUTE_60
        )
        clock.advance(NEXT_FETCH_SECONDS)
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
            ("설정", the_settings_keep_what_was_chosen),
            ("다음 받기", the_next_fetch_time_comes_once_the_data_thread_rests),
            ("알림과 디스코드", the_alert_is_in_the_error_tab_and_on_discord),
        ],
    )
    # `run_gui`가 이벤트 루프 전에 실패하면 줄 서 있던 시작이 뒤의 Qt 시험 안에서 돌지 않게 한다.
    armed = {"on": True}
    QTimer.singleShot(0, lambda: driver.start() if armed["on"] else None)
    began = time.monotonic()
    try:
        code = run_gui([], services=services)
    finally:
        armed["on"] = False
        driver.stop()
        discord.release()
    elapsed = time.monotonic() - began
    if driver.error is not None:
        raise driver.error
    assert driver.done == ["처음 설정", "통계", "설정", "다음 받기", "알림과 디스코드"], (
        driver.steps[:1]
    )
    assert code == 0
    assert elapsed < 30.0

    # 2. 봉 통계 표: 항목마다(`bar_count`부터 `low`까지의 차례) 이름과 값이 보이고, 고른 봉
    #    개수(200개)가 다 모였으며 전체 변화가 "%"로 끝난다.
    assert [row[0] for row in seen["stats"]] == [BAR_STATS_TITLES[c] for c in TABLE_COLUMNS]
    values = dict(zip(TABLE_COLUMNS, [row[1] for row in seen["stats"]], strict=True))
    assert values["bar_count"] == "200개"  # 처음 설정의 봉 개수 기본값
    assert values["change"].endswith("%")
    assert INSUFFICIENT_TEXT not in values.values()  # 표의 자리 글("봉이 모자람")이 없다
    # 1. 처음 설정에서 고른 보관 기간(30일)과 코인이 설정 화면에 남는다.
    assert seen["settings"] == {"retention_30": True, "coin": True}
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
