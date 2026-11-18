"""명세 7절 A10: 옵션 없이 켜면 창이 뜨고, 이벤트 루프가 끝나면 0으로 끝난다."""

from pathlib import Path
from typing import Any

import pytest
from fakes.clock import FakeClock
from fakes.quote import FakeQuoteSource
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from coin_report_trader.__main__ import main
from coin_report_trader.app.services import Services
from coin_report_trader.config.paths import resolve_app_paths


@pytest.mark.scenario("A10")
def test_a10_main_without_options_opens_the_window_and_logs(
    tmp_path: Path, qapp: QApplication
) -> None:
    root = resolve_app_paths().root
    # conftest가 자료 폴더를 tmp_path로 바꿨다. 아니면 실제 사용자 폴더를 쓰게 되므로 멈춘다.
    assert root.is_relative_to(tmp_path)
    assert not root.exists()

    seen: dict[str, Any] = {}
    state = {"armed": True}
    safety = QTimer()  # 창을 보지 못해도 10초 뒤에는 끝낸다
    safety.setSingleShot(True)
    safety.timeout.connect(qapp.quit)
    safety.start(10_000)

    def look_then_quit() -> None:
        if not state["armed"]:
            return  # main이 이벤트 루프 전에 끝났다면 다음 시험의 루프를 끝내지 않는다
        try:
            windows = [w for w in QApplication.topLevelWidgets() if w.objectName() == "mainWindow"]
            seen["count"] = len(windows)
            seen["visible"] = bool(windows) and windows[0].isVisible()
            seen["title"] = windows[0].windowTitle() if windows else None
        except Exception as exc:  # 슬롯의 예외는 Qt가 삼키므로 시험이 읽게 남긴다
            seen["error"] = repr(exc)
        finally:
            qapp.quit()

    QTimer.singleShot(0, look_then_quit)
    try:
        code = main([], services=Services(quote=FakeQuoteSource(), clock=FakeClock()))
    finally:
        state["armed"] = False
        safety.stop()

    assert code == 0
    assert "error" not in seen, seen.get("error")
    assert seen == {"count": 1, "visible": True, "title": "Coin Report Trader"}
    for folder in (root, root / "data", root / "logs"):
        assert folder.is_dir()
    assert (root / "data" / "app.db").is_file()
    log = (root / "logs" / "app.log").read_text(encoding="utf-8")
    assert "시작" in log
    assert "종료" in log
    assert log.index("시작") < log.index("종료")
