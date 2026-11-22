"""처음 설정의 3단계 칸을 채우는 시험 도구(`tests/acceptance/m3_support.py`. M3 명세 4절 15번).

3.17.5의 객체 이름으로 만든 흉내 낸 화면에서, 칸을 만든 값으로 채우고 단추를 고르며, 칸이 없으면
아무것도 하지 않고, 일부만 있으면 실패하는지 본다. 값은 모두 만든 값이다.
"""

from decimal import Decimal

import pytest
from acceptance.m3_support import (
    BUTTONS,
    LINE_VALUES,
    fill_trade_fields,
    trade_fields_exist,
)
from PySide6.QtWidgets import QApplication, QCheckBox, QLineEdit, QRadioButton, QWidget

from coin_report_trader.core import PauseStopLoss, TradeMethod, TradeSettings
from coin_report_trader.trading.orders import check_trade_settings

MARKETS = ("KRW-AAA", "KRW-BBB", "KRW-CCC")
# M3 명세 3.17.5의 객체 이름(앞머리 `firstSetup` 뒤). 도구의 이름과 따로 글로 적는다.
SPEC_LINES = ("Budget", "FirstRatio", "MrFirstRatio", "AddRatio", "Tolerance", "StopRatio",
              "DailyLoss", "OrderInterval", "MrLoss", "PaperFunds", "BackupDays")  # fmt: skip
SPEC_BUTTONS = ("Method_trend", "PauseStop_keep", "MrSell_no")


def screen(*, coins: tuple[str, ...] = MARKETS, skip: tuple[str, ...] = ()) -> QWidget:
    """3.17.5의 이름을 가진 칸을 둔 흉내 낸 처음 설정 화면(`skip`의 이름은 두지 않는다)."""
    root = QWidget()
    for name in SPEC_LINES:
        if name not in skip:
            QLineEdit(root).setObjectName(f"firstSetup{name}")
    for market in coins:
        QLineEdit(root).setObjectName(f"firstSetupCoinRatio_{market}")
    for name in SPEC_BUTTONS:
        button = QRadioButton(root)
        button.setObjectName(f"firstSetup{name}")
        button.setAutoExclusive(False)  # 진짜 화면은 묶음마다 따로다. 흉내 낸 화면은 한 부모 아래다
    check = QCheckBox(root)
    check.setObjectName("firstSetupRead")
    return root


def text(root: QWidget, name: str) -> str:
    box = root.findChild(QLineEdit, name)
    assert box is not None, name
    return box.text()


def test_the_names_of_the_tool_are_the_object_names_of_the_spec() -> None:
    assert tuple(name for name, _ in LINE_VALUES) == SPEC_LINES
    assert BUTTONS == SPEC_BUTTONS


def test_without_the_trade_fields_nothing_is_done_and_zero_is_returned(qapp: QApplication) -> None:
    root = QWidget()
    QLineEdit(root).setObjectName("firstSetupCoinSearch")  # 17번 앞의 화면에 있는 칸
    assert not trade_fields_exist(root)
    assert fill_trade_fields(root) == 0
    assert root.findChild(QLineEdit, "firstSetupCoinSearch").text() == ""  # 아무것도 쓰지 않는다


def test_every_field_is_filled_and_the_buttons_and_the_read_box_are_chosen(
    qapp: QApplication,
) -> None:
    root = screen()
    assert trade_fields_exist(root)
    filled = fill_trade_fields(root)
    assert filled == len(SPEC_LINES) + len(MARKETS) + len(SPEC_BUTTONS) + 1
    for name, value in LINE_VALUES:
        assert text(root, f"firstSetup{name}") == value, name
    for name in SPEC_BUTTONS:
        assert root.findChild(QRadioButton, f"firstSetup{name}").isChecked(), name
    assert root.findChild(QCheckBox, "firstSetupRead").isChecked()


def test_the_coin_ratios_sum_to_one_hundred_for_any_number_of_coins(qapp: QApplication) -> None:
    for count in (1, 2, 3, 7):
        coins = tuple(f"KRW-C{i}" for i in range(count))
        root = screen(coins=coins)
        fill_trade_fields(root)
        shares = [Decimal(text(root, f"firstSetupCoinRatio_{c}")) for c in coins]
        assert sum(shares) == Decimal(100), count
        assert all(share > 0 for share in shares), count


def test_the_read_box_is_left_alone_when_asked(qapp: QApplication) -> None:
    root = screen()
    fill_trade_fields(root, read=False)
    assert not root.findChild(QCheckBox, "firstSetupRead").isChecked()


def test_a_partial_screen_fails_and_a_screen_without_coin_ratios_fails(qapp: QApplication) -> None:
    root = QWidget()
    QCheckBox(root).setObjectName("firstSetupRead")  # 매매 칸은 없고 확인란만 있다
    with pytest.raises(AssertionError, match="일부"):
        fill_trade_fields(root)
    with pytest.raises(AssertionError, match="투자 비율"):
        fill_trade_fields(screen(coins=()))
    with pytest.raises(AssertionError, match="없다"):
        fill_trade_fields(screen(skip=("StopRatio",)))


def test_the_filled_values_pass_the_trade_settings_check_for_one_to_three_coins(
    qapp: QApplication,
) -> None:
    """채우는 값이 3.11.2의 검사를 지난다(그래서 17번 뒤의 시작 단추가 눌린다)."""
    for count in (1, 2, 3):
        coins = MARKETS[:count]
        root = screen(coins=coins)
        fill_trade_fields(root)
        ratios = {c: Decimal(text(root, f"firstSetupCoinRatio_{c}")) for c in coins}
        settings = TradeSettings(
            budget=Decimal(text(root, "firstSetupBudget").replace(",", "")),
            coin_ratios=ratios,
            first_ratio=Decimal(text(root, "firstSetupFirstRatio")),
            mr_first_ratio=Decimal(text(root, "firstSetupMrFirstRatio")),
            add_ratio=Decimal(text(root, "firstSetupAddRatio")),
            tolerance=Decimal(text(root, "firstSetupTolerance")),
            stop_ratio=Decimal(text(root, "firstSetupStopRatio")),
            daily_loss_limit=Decimal(text(root, "firstSetupDailyLoss").replace(",", "")),
            order_interval_s=int(text(root, "firstSetupOrderInterval")),
            method=TradeMethod.TREND,
            pause_stop_loss=PauseStopLoss.KEEP,
            mr_sell_unknown_fee=False,
            mr_tolerable_loss=Decimal(text(root, "firstSetupMrLoss")),
            paper_funds=Decimal(text(root, "firstSetupPaperFunds").replace(",", "")),
            backup_days=int(text(root, "firstSetupBackupDays")),
        )
        assert check_trade_settings(settings, coins, Decimal(5000)) == {}, count
