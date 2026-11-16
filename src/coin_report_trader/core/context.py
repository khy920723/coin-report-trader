"""조립(app)이 화면(ui)에 넘기는 값."""

from dataclasses import dataclass

from coin_report_trader.core.ports import UiActions


@dataclass(frozen=True)
class UiContext:
    version: str
    data_dir_display: str
    actions: UiActions | None = None
    """화면이 부르는 동작. `None`이면 화면은 동작을 부르지 않는다(M0 시험처럼)."""
