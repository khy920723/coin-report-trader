"""조립(app)이 화면(ui)에 넘기는 값."""

from dataclasses import dataclass


@dataclass(frozen=True)
class UiContext:
    version: str
    data_dir_display: str
