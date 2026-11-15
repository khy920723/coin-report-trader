"""조립: GUI 실행과 `--self-test`. 무거운 모듈(PySide6)은 쓰는 함수 안에서 import한다."""

from coin_report_trader.app.application import run_gui

__all__ = ["run_gui"]
