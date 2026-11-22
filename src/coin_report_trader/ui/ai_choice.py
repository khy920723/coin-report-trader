"""모델·노력 값 칸(M2 명세 3.12.2). 처음 설정과 설정 화면이 같이 쓴다.

`AiSlot`의 차례로 네 칸이고, 칸마다 "{칸 이름} 모델"과 "{칸 이름} 노력 값" 고르기(내림 목록)다.
목록의 첫 항목은 빈 글(값 없음)이고 처음에는 그것이 골라져 있다.

`objectName`(앞의 `prefix`는 `firstSetup`이나 `settings`): `<prefix>AiModel_<칸 값>`,
`<prefix>AiEffort_<칸 값>`(내림 목록. 예: `firstSetupAiModel_search`), `<prefix>AiInfo`(카드 제목
옆 'ⓘ'. 툴팁이 "앱의 AI 사용은 …").
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QGridLayout, QLabel, QVBoxLayout, QWidget

from coin_report_trader.core import (
    AI_SLOT_TITLES,
    CLAUDE_MODELS,
    EFFORT_TITLES,
    AiChoice,
    AiSlot,
    Effort,
)
from coin_report_trader.ui import theme
from coin_report_trader.ui.cards import title_row
from coin_report_trader.ui.info_icon import InfoIcon
from coin_report_trader.ui.texts import (
    AI_CARD_TITLE,
    AI_CARD_TITLE_REQUIRED,
    AI_USAGE_NOTE,
    ai_effort_label,
    ai_model_label,
)
from coin_report_trader.ui.widgets import make_label, set_role


class AiChoices(QWidget):
    """네 칸의 모델과 노력 값. 사용자가 고르면 `changed`가 난다(`set_choices`는 내지 않는다)."""

    changed = Signal()

    def __init__(self, prefix: str, *, required: bool) -> None:
        super().__init__()
        self.setObjectName(f"{prefix}Ai")
        title = AI_CARD_TITLE_REQUIRED if required else AI_CARD_TITLE
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.FIELD_GAP)
        info = InfoIcon(title, AI_USAGE_NOTE, f"{prefix}AiInfo")
        layout.addLayout(title_row(title, f"{prefix}AiTitle", info))
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(theme.FIELD_GAP)
        grid.setVerticalSpacing(theme.FIELD_GAP)
        grid.setColumnStretch(1, 1)
        self._models: dict[AiSlot, QComboBox] = {}
        self._efforts: dict[AiSlot, QComboBox] = {}
        for row, slot in enumerate(AiSlot):
            slot_title = AI_SLOT_TITLES[slot]
            model = self._combo(f"{prefix}AiModel_{slot.value}", list(CLAUDE_MODELS))
            effort = self._combo(
                f"{prefix}AiEffort_{slot.value}", [EFFORT_TITLES[value] for value in Effort]
            )
            self._models[slot] = model
            self._efforts[slot] = effort
            grid.addWidget(
                self._caption(ai_model_label(slot_title, required=required), model), row * 2, 0
            )
            grid.addWidget(model, row * 2, 1)
            grid.addWidget(
                self._caption(ai_effort_label(slot_title, required=required), effort),
                row * 2 + 1,
                0,
            )
            grid.addWidget(effort, row * 2 + 1, 1)
        layout.addLayout(grid)

    def _combo(self, name: str, items: list[str]) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName(name)
        combo.addItem("", None)  # 빈 항목: 고르지 않음
        for text in items:
            combo.addItem(text, text)
        combo.currentIndexChanged.connect(lambda _index: self.changed.emit())
        return combo

    def _caption(self, text: str, buddy: QComboBox) -> QLabel:
        label = make_label(text, "")
        set_role(label, theme.ROLE_SUB)
        label.setBuddy(buddy)
        buddy.setAccessibleName(text)
        return label

    def model_of(self, slot: AiSlot) -> str | None:
        data = self._models[slot].currentData()
        return data if isinstance(data, str) else None

    def effort_of(self, slot: AiSlot) -> Effort | None:
        index = self._efforts[slot].currentIndex()
        return list(Effort)[index - 1] if index > 0 else None

    def chosen_count(self) -> int:
        """골라진 칸(모델과 노력 값 합쳐 여덟 가운데)의 수."""
        return sum(
            (self.model_of(slot) is not None) + (self.effort_of(slot) is not None)
            for slot in AiSlot
        )

    def choices(self) -> tuple[AiChoice, ...] | None:
        """여덟 칸이 모두 골라져 있으면 네 칸의 `AiChoice`(칸의 차례), 아니면 `None`."""
        result = []
        for slot in AiSlot:
            model, effort = self.model_of(slot), self.effort_of(slot)
            if model is None or effort is None:
                return None
            result.append(AiChoice(slot, model, effort))
        return tuple(result)

    def is_partial(self) -> bool:
        """일부만 골라져 있다(하나 이상 골랐고 여덟이 다는 아니다)."""
        return 0 < self.chosen_count() < 2 * len(AiSlot)

    def set_choices(self, choices: tuple[AiChoice, ...] | None) -> None:
        """고른 값을 바꾼다(`changed`를 내지 않는다). 없는 칸은 빈 항목으로 둔다."""
        by_slot = {choice.slot: choice for choice in choices or ()}
        for slot in AiSlot:
            choice = by_slot.get(slot)
            model_index = 0
            effort_index = 0
            if choice is not None:
                if choice.model in CLAUDE_MODELS:
                    model_index = CLAUDE_MODELS.index(choice.model) + 1
                effort_index = list(Effort).index(Effort(choice.effort)) + 1
            for combo, index in (
                (self._models[slot], model_index),
                (self._efforts[slot], effort_index),
            ):
                combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(False)
