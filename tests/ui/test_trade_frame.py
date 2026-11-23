"""3단계의 공통 틀, 현황, 보고서 자리(M3 명세 3.17.1~3.17.3, 3.17.6)의 시험. 값은 만든 값이다."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QWidget,
)

from coin_report_trader.core import (
    AlertCategory,
    AlertDetail,
    AlertsView,
    AlertView,
    CoinTradeView,
    Direction,
    ErrorPauseCause,
    LoadState,
    Mode,
    OrderKind,
    OrderReason,
    OrderRecord,
    OrderSide,
    OrderState,
    PaperHolding,
    PerformanceRow,
    PerformanceView,
    ReportListView,
    ReportState,
    ReportView,
    RoundItem,
    RoundNowView,
    RoundStage,
    SaveResult,
    ScreenId,
    SettingsView,
    TodayView,
    TradeStatusView,
    UiContext,
)
from coin_report_trader.ui import (
    create_main_window,
    current_screen,
    screen_widget,
    select_screen,
    status_line_text,
)
from coin_report_trader.ui.info_icon import InfoIcon
from coin_report_trader.ui.widgets import set_trend

NOW = datetime(2026, 1, 2, 1, 30, tzinfo=UTC)  # 한국 시간 10:30
COINS = ("KRW-AAA", "KRW-BBB")


class Actions:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def refresh_markets(self) -> None: ...

    def save_settings(self, coins, retention_days, **kwargs) -> None: ...

    def load_reports(self, market, mode=None) -> None:
        self.calls.append(f"load_reports:{market}")

    def load_report(self, report_id) -> None: ...

    def retry_candles(self) -> None: ...

    def mark_seen(self, alert_ids) -> None: ...

    def delete_alert(self, alert_id) -> None: ...

    def clear_alerts(self, category) -> None: ...

    def save_webhook(self, category, url) -> None: ...

    def delete_webhook(self, category) -> None: ...

    def send_test(self, category) -> None: ...

    def pause(self) -> None:
        self.calls.append("pause")

    def pause_and_sell_all(self) -> None:
        self.calls.append("pause_and_sell_all")

    def resume_user(self) -> None:
        self.calls.append("resume_user")

    def resume_error(self) -> None:
        self.calls.append("resume_error")


class Confirm:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.asked: list[tuple[str, str]] = []

    def __call__(self, title: str, body: str) -> bool:
        self.asked.append((title, body))
        return self.answer


@pytest.fixture
def actions() -> Actions:
    return Actions()


@pytest.fixture
def window(qapp: QApplication, actions: Actions):
    made = create_main_window(UiContext("0.0.1", "X", actions))
    made.resize(1200, 900)
    made.show()
    yield made
    made.close()
    made.deleteLater()


def _done(window, coins=COINS) -> None:
    window.apply(SettingsView(coins, 30, True))


def _find(parent: QWidget, kind: type, name: str):
    found = parent.findChild(kind, name)
    assert found is not None, name
    return found


def _status(window) -> QWidget:
    return screen_widget(window, ScreenId.STATUS)


def _text(parent: QWidget, name: str) -> str:
    return _find(parent, QLabel, name).text()


def _view(**changes) -> TradeStatusView:
    values = {
        "user_paused": False,
        "error_paused": False,
        "error_cause": None,
        "error_detail": None,
        "stop_loss_off": False,
        "starting_step": None,
        "next_round_utc": NOW + timedelta(hours=1, minutes=30),
        "as_of": NOW,
    }
    values.update(changes)
    return TradeStatusView(**values)


# --- 상태 띠 ---


def test_the_band_sits_above_the_screens_and_the_mode_is_always_shown(window):
    band = _find(window, QWidget, "tradeBand")
    stack = _find(window, QWidget, "screenStack")
    assert band.mapTo(window, QPoint(0, 0)).y() < stack.mapTo(window, QPoint(0, 0)).y()
    mode = _find(window, QLabel, "tradeBandMode")
    assert mode.text() == "모의매매"
    assert not mode.isHidden()
    for name in ("tradeBandPause", "tradeBandStopOff", "tradeBandStarting"):
        assert _find(window, QLabel, name).isHidden(), name
    assert _find(window, QPushButton, "tradeBandAlerts").isHidden()
    for name in ("resumeUserButton", "resumeErrorButton"):
        assert _find(window, QPushButton, name).isHidden(), name


def test_the_band_stays_when_the_screen_is_scrolled(window):
    _done(window)
    band = _find(window, QWidget, "tradeBand")
    for screen_id in ScreenId:
        select_screen(window, screen_id)
        assert not band.isHidden()
        scroll = _find(window, QScrollArea, f"screenScroll_{screen_id.value}")
        assert not scroll.isAncestorOf(band)


@pytest.mark.parametrize(
    ("user", "error", "text"),
    [
        (True, False, "멈춤(사용자)"),
        (False, True, "오류 멈춤"),
        (True, True, "멈춤(사용자) · 오류 멈춤"),
    ],
)
def test_the_pause_tag_and_the_resume_buttons(window, actions, user, error, text):
    _done(window)
    window.apply(
        _view(
            user_paused=user,
            error_paused=error,
            error_cause=ErrorPauseCause.UNEXPECTED if error else None,
        )
    )
    tag = _find(window, QLabel, "tradeBandPause")
    assert not tag.isHidden()
    assert tag.text() == text
    resume_user = _find(window, QPushButton, "resumeUserButton")
    resume_error = _find(window, QPushButton, "resumeErrorButton")
    assert resume_user.isHidden() is not user
    assert resume_error.isHidden() is not error
    if user:
        assert resume_user.text() == "멈춤 풀기"
        resume_user.click()
        assert actions.calls == ["resume_user"]
    if error:
        assert resume_error.text() == "오류 멈춤 풀기"
        resume_error.click()
        assert actions.calls[-1] == "resume_error"


def test_the_stop_loss_off_tag_and_the_starting_tag(window):
    _done(window)
    stop_off = _find(window, QLabel, "tradeBandStopOff")
    starting = _find(window, QLabel, "tradeBandStarting")
    assert stop_off.isHidden()
    assert starting.isHidden()
    window.apply(_view(stop_loss_off=True, starting_step="켜는 중: 손절 점검"))
    assert not stop_off.isHidden()
    assert stop_off.text() == "지금 손절이 동작하지 않음"
    assert not starting.isHidden()
    assert starting.text() == "켜는 중: 손절 점검"
    window.apply(_view(starting_step="켜는 중: 거래소와 맞춰 보는 중"))
    assert starting.text() == "켜는 중: 거래소와 맞춰 보는 중"
    assert stop_off.isHidden()
    window.apply(_view())
    assert stop_off.isHidden()
    assert starting.isHidden()


def test_the_band_tags_have_the_decided_colors_weights_and_corners(window):
    _done(window)
    window.apply(_view(user_paused=True, stop_loss_off=True, starting_step="켜는 중: 손절 점검"))
    style = {
        name: _find(window, QLabel, name).styleSheet()
        for name in ("tradeBandMode", "tradeBandPause", "tradeBandStopOff", "tradeBandStarting")
    }
    for name, text in style.items():
        assert "border-radius: 12px" in text, name  # 꼬리표 모서리 12px
        assert "background: #1A3529" in text, name  # '고른 칸' 바탕
    assert "color: #EDD05A" in style["tradeBandPause"]  # `caution`
    assert "font-weight: bold" in style["tradeBandPause"]
    assert "color: #FF9548" in style["tradeBandStopOff"]  # `error`
    assert "font-weight: bold" in style["tradeBandStopOff"]
    for name in ("tradeBandMode", "tradeBandStarting"):
        assert "color: #EEF5F1" in style[name], name  # `text`
        assert "font-weight: normal" in style[name], name


def test_a_blocked_error_pause_cannot_be_resumed_until_the_time_with_the_note(window):
    _done(window)
    until = NOW + timedelta(minutes=30)
    window.apply(
        _view(
            error_paused=True,
            error_cause=ErrorPauseCause.EXCHANGE,
            error_detail="일시 차단",
            blocked_until=until,
        )
    )
    resume = _find(window, QPushButton, "resumeErrorButton")
    note = _find(window, QLabel, "tradeBandResumeNote")
    assert not resume.isEnabled()
    assert not note.isHidden()
    assert note.text() == "11:00 뒤에 풀 수 있습니다."
    window.apply(
        _view(
            error_paused=True,
            error_cause=ErrorPauseCause.EXCHANGE,
            blocked_until=NOW - timedelta(minutes=1),
        )
    )
    assert resume.isEnabled()
    assert note.isHidden()


def test_the_two_stop_buttons_are_off_before_the_first_setup(window, actions):
    window.apply(SettingsView())
    pause = _find(window, QPushButton, "pauseButton")
    sell = _find(window, QPushButton, "sellAllButton")
    assert pause.text() == "멈춤"
    assert sell.text() == "멈추고 전량 매도"
    assert not pause.isEnabled()
    assert not sell.isEnabled()
    pause.click()
    sell.click()
    assert actions.calls == []
    _done(window)
    assert pause.isEnabled()
    assert sell.isEnabled()


def test_the_pause_button_calls_pause_at_once_and_is_off_while_the_user_pause_stays(
    window, actions
):
    _done(window)
    window.set_confirm_handler(Confirm(False))  # 확인 창이 뜨면 멈춤이 아니다
    _find(window, QPushButton, "pauseButton").click()
    assert actions.calls == ["pause"]
    window.apply(_view(user_paused=True))
    assert not _find(window, QPushButton, "pauseButton").isEnabled()
    window.apply(_view(error_paused=True, error_cause=ErrorPauseCause.SAVE))
    assert _find(window, QPushButton, "pauseButton").isEnabled()  # 오류 멈춤만이면 누를 수 있다


def test_sell_all_asks_scr_06_7_and_calls_only_when_confirmed(window, actions):
    _done(window)
    sell = _find(window, QPushButton, "sellAllButton")
    cancel = Confirm(False)
    window.set_confirm_handler(cancel)
    sell.click()
    assert cancel.asked == [
        (
            "멈추고 전량 매도할까요?",
            "멈춤 상태로 들어가고 프로그램이 건 주문을 취소한 뒤, "
            "보유 코인을 그때 시세로 모두 팝니다. 직접 걸어 둔 주문에 묶인 수량은 팔지 못합니다.",
        )
    ]
    assert actions.calls == []
    window.set_confirm_handler(Confirm(True))
    sell.click()
    assert actions.calls == ["pause_and_sell_all"]
    # 사용자 멈춤 상태에서도 누를 수 있다.
    window.apply(_view(user_paused=True))
    assert sell.isEnabled()


def test_sell_all_is_off_during_an_error_pause_with_the_note(window):
    _done(window)
    note = _find(window, QLabel, "tradeBandSellAllNote")
    assert note.isHidden()
    window.apply(_view(error_paused=True, error_cause=ErrorPauseCause.UNEXPECTED))
    assert not _find(window, QPushButton, "sellAllButton").isEnabled()
    assert not note.isHidden()
    assert note.text() == "오류 멈춤을 푼 뒤 누를 수 있습니다."
    window.apply(_view())
    assert _find(window, QPushButton, "sellAllButton").isEnabled()
    assert note.isHidden()


def test_the_sell_all_button_has_the_danger_look(window):
    style = _find(window, QPushButton, "sellAllButton").styleSheet()
    assert "border: 2px solid #FF9548" in style  # `error` 2px 테두리
    assert "font-weight: bold" in style
    assert "QPushButton#sellAllButton:hover" in style
    assert "background: #FF9548; color: #0A1A14" in style  # 올리거나 누르면 `error` 채움에 `bg` 글
    assert "#56695F" in style  # 누를 수 없으면 `text-disabled`


def test_the_alert_chip_counts_unseen_alerts_and_opens_the_alert_center(window):
    _done(window)
    chip = _find(window, QPushButton, "tradeBandAlerts")
    alert = AlertView(
        1, NOW, Mode.PAPER, AlertCategory.ERROR, "A-30", None, "글", detail=None, seen=False
    )
    window.apply(AlertsView((alert,), {AlertCategory.ERROR: 1}, LoadState.READY))
    assert chip.text() == "알림 1건"
    select_screen(window, ScreenId.SETTINGS)
    chip.click()
    assert current_screen(window) is ScreenId.STATUS


def test_the_nav_names_have_no_count_and_the_band_is_the_only_place_for_it(window):
    _done(window)
    window.apply(
        AlertsView(
            (
                AlertView(
                    1, NOW, Mode.PAPER, AlertCategory.TRADE, "A-3", "KRW-AAA", "글", seen=False
                ),
            ),
            {AlertCategory.TRADE: 1},
            LoadState.READY,
        )
    )
    nav = _find(window, QListWidget, "screenNav")
    assert [nav.item(i).text() for i in range(nav.count())] == [
        "처음 설정",
        "현황",
        "보고서",
        "기록",
        "설정",
    ]


# --- 상태 줄 ---


def test_the_status_line_variants(window):
    context = UiContext("0.1.4", "경로")
    assert status_line_text(context) == "판 v0.1.4 · 다음 예정 시각 - · 자료 폴더 경로"
    running = _view()
    assert status_line_text(context, running) == (
        "판 v0.1.4 · 다음 예정 시각 12:00 · 자료 폴더 경로"
    )
    far = _view(next_round_utc=NOW + timedelta(days=1))
    assert "다음 예정 시각 2026-01-03 10:30" in status_line_text(context, far)
    for paused in (_view(user_paused=True), _view(error_paused=True)):
        assert status_line_text(context, paused) == (
            "판 v0.1.4 · 멈춤 동안 회차를 돌리지 않음 · 자료 폴더 경로"
        )
    assert status_line_text(context, running, before_setup=True) == (
        "판 v0.1.4 · 처음 설정 전 · 자료 폴더 경로"
    )


def test_the_status_line_of_the_window_follows_the_state(window):
    line = lambda: _text(window, "statusLine")  # noqa: E731
    assert line() == "판 v0.0.1 · 다음 예정 시각 - · 자료 폴더 X"
    window.apply(SettingsView())
    assert line() == "판 v0.0.1 · 처음 설정 전 · 자료 폴더 X"
    _done(window)
    assert line() == "판 v0.0.1 · 다음 예정 시각 - · 자료 폴더 X"
    window.apply(_view())
    assert line() == "판 v0.0.1 · 다음 예정 시각 12:00 · 자료 폴더 X"
    window.apply(_view(user_paused=True))
    assert line() == "판 v0.0.1 · 멈춤 동안 회차를 돌리지 않음 · 자료 폴더 X"


# --- ▲▼ ---


def test_set_trend_paints_the_arrow_forms_and_leaves_zero_alone(qapp):
    label = QLabel()
    for text, expected in (
        ("▲ +1,234원", "rise"),
        ("▼ -300원", "fall"),
        ("+1.00%", "rise"),
        ("-1.00%", "fall"),
        ("0원", ""),
        ("0.00%", ""),
        ("", ""),
    ):
        set_trend(label, text)
        assert (label.property("trend") or "") == expected, text


# --- 현황 ---


def _today(**changes) -> TodayView:
    values = {
        "realized_pnl": Decimal("1234.9"),
        "daily_loss_limit": Decimal(3000),
        "used": Decimal(45000),
        "budget": Decimal(100000),
        "paper_krw": Decimal("55000.5"),
        "limit_reached": False,
        "as_of": NOW,
    }
    values.update(changes)
    return TodayView(**values)


def test_the_status_title_has_the_info_icon_and_the_error_pause_line(window):
    _done(window)
    status = _status(window)
    icon = _find(status, InfoIcon, "statusIntroInfo")
    assert icon.toolTip() == "PC가 꺼져 있으면 손절이 없습니다."
    assert icon.accessibleName() == "현황 설명"
    line = _find(status, QLabel, "statusErrorPause")
    assert line.isHidden()
    window.apply(
        _view(
            error_paused=True,
            error_cause=ErrorPauseCause.EXCHANGE,
            error_detail="일시 차단",
            uncanceled=("AAA 지정가 매수 990원 × 6.0",),
        )
    )
    assert not line.isHidden()
    assert line.text() == (
        "오류 멈춤: 일시 차단. 지금 손절이 동작하지 않습니다. "
        "취소하지 못한 주문: AAA 지정가 매수 990원 × 6.0"
    )
    window.apply(_view(error_paused=True, error_cause=ErrorPauseCause.SAVE))
    assert (
        line.text()
        == "오류 멈춤: 저장 실패. 지금 손절이 동작하지 않습니다. 취소하지 못한 주문: 없음"
    )
    window.apply(_view(error_paused=True, error_cause=ErrorPauseCause.UNEXPECTED))
    assert "예상하지 못한 오류" in line.text()
    window.apply(_view())
    assert line.isHidden()


def test_the_error_pause_line_is_shown_even_when_the_screen_shows_only_a_message(window):
    window.apply(SettingsView())  # 처음 설정 전: 글 하나만 보인다
    window.apply(_view(error_paused=True, error_cause=ErrorPauseCause.UNEXPECTED))
    assert not _find(_status(window), QLabel, "statusErrorPause").isHidden()


def test_the_today_summary_texts_and_trend_colors(window):
    _done(window)
    status = _status(window)
    assert _find(status, QWidget, "statusToday").isHidden()  # 값이 오기 전
    window.apply(_today())
    assert not _find(status, QWidget, "statusToday").isHidden()
    pnl = _find(status, QLabel, "statusTodayPnl")
    assert pnl.text() == "▲ +1,234원"  # 원 아래를 버린다
    assert pnl.property("trend") == "rise"
    assert _text(status, "statusTodayLimit") == " / 하루 손실 한도 3,000원"
    assert _text(status, "statusTodayUsed") == (
        "전체 매수 원가와 걸린 매수 주문 45,000원 / 예산 100,000원"
    )
    assert _text(status, "statusTodayKrw") == "가상 원화 55,000원"
    window.apply(_today(realized_pnl=Decimal("-300")))
    assert pnl.text() == "▼ -300원"
    assert pnl.property("trend") == "fall"
    window.apply(_today(realized_pnl=Decimal(0)))
    assert pnl.text() == "0원"
    assert not pnl.property("trend")


def test_the_first_line_is_error_colored_when_the_daily_loss_limit_is_reached(window):
    _done(window)
    status = _status(window)
    window.apply(_today(realized_pnl=Decimal("-3000"), limit_reached=True))
    for name in ("statusTodayPnlName", "statusTodayPnl", "statusTodayLimit"):
        assert "#FF9548" in _find(status, QLabel, name).styleSheet(), name
    window.apply(_today())
    assert "#FF9548" not in _find(status, QLabel, "statusTodayLimit").styleSheet()


def _holding(qty="10", avg="1000") -> PaperHolding:
    return PaperHolding(
        "KRW-AAA", Decimal(qty), Decimal(0), Decimal(avg) if avg is not None else None,
        Decimal("10000"), Decimal(qty),
    )  # fmt: skip


def _order(state=OrderState.OPEN) -> OrderRecord:
    return OrderRecord(
        1, "uid", Mode.PAPER, 12, "KRW-AAA", OrderSide.BUY, OrderKind.LIMIT, OrderReason.JUDGMENT,
        Decimal(990), Decimal("6.0"), state, NOW, None, None,
    )  # fmt: skip


def _coin(**changes) -> CoinTradeView:
    values = {
        "market": "KRW-AAA",
        "price": Decimal(1050),
        "price_read_utc": NOW,
        "holding": _holding(),
        "sellable_qty": Decimal("10"),
        "value": Decimal("10500.7"),
        "stop_line": Decimal(950),
        "dust": False,
        "last_direction": Direction.UP,
        "last_round": 12,
        "open_orders": (_order(),),
        "buy_blocked": (),
        "caution": False,
        "sell_all": None,
        "as_of": NOW,
    }
    values.update(changes)
    return CoinTradeView(**values)


def _coin_text(status: QWidget, prefix: str, market: str = "KRW-AAA") -> str | None:
    label = _find(status, QLabel, f"{prefix}{market}")
    return None if label.isHidden() else label.text()


def test_a_coin_card_shows_the_third_stage_lines_of_a_holding(window):
    _done(window)
    status = _status(window)
    window.apply(_coin())
    assert _coin_text(status, "statusCoinPrice_") == "현재가 1,050원"
    assert _coin_text(status, "statusCoinHolding_") == "보유 10 · 팔 수 있음 10"
    assert _coin_text(status, "statusCoinAvg_") == "평균 매수가 1,000원"
    assert _coin_text(status, "statusCoinValue_") == "평가 금액 10,500원"
    assert _coin_text(status, "statusCoinStop_") == "손절선 950원"
    assert _coin_text(status, "statusCoinDust_") is None
    assert _coin_text(status, "statusCoinLast_") == "지난 판단: 오를 것 (회차 12)"
    assert _text(status, "statusCoinOrder_KRW-AAA_0") == "지정가 매수 대기 990원 × 6.0"
    assert _coin_text(status, "statusCoinBlocked_") is None
    assert _coin_text(status, "statusCoinSellAll_") is None
    assert _find(status, QLabel, "statusCoinCaution_KRW-AAA").isHidden()


def test_a_coin_card_without_a_holding_shows_one_line_instead_of_four(window):
    _done(window)
    status = _status(window)
    window.apply(_coin(holding=None, sellable_qty=Decimal(0), value=None, stop_line=None))
    assert _coin_text(status, "statusCoinHolding_") == "보유 없음"
    for prefix in ("statusCoinAvg_", "statusCoinValue_", "statusCoinStop_"):
        assert _coin_text(status, prefix) is None, prefix
    assert _text(status, "statusCoinOrder_KRW-AAA_0") == "지정가 매수 대기 990원 × 6.0"
    window.apply(_coin(holding=None, open_orders=()))
    assert _text(status, "statusCoinOrder_KRW-AAA_0") == "걸린 주문 없음"


def test_a_holding_row_with_zero_quantity_counts_as_no_holding(window):
    _done(window)
    status = _status(window)
    zero = _holding(qty="0", avg="1000")  # 줄은 남고 수량이 0이다(3.12)
    window.apply(_coin(holding=zero, sellable_qty=Decimal(0), value=Decimal(0)))
    assert _coin_text(status, "statusCoinHolding_") == "보유 없음"
    for prefix in ("statusCoinAvg_", "statusCoinValue_", "statusCoinStop_", "statusCoinDust_"):
        assert _coin_text(status, prefix) is None, prefix


def test_sellable_quantity_is_the_views_value_and_not_the_holding_quantity(window):
    _done(window)
    status = _status(window)
    window.apply(_coin(holding=_holding(qty="10"), sellable_qty=Decimal("7.5")))
    assert _coin_text(status, "statusCoinHolding_") == "보유 10 · 팔 수 있음 7.5"


def test_error_colored_and_caution_colored_lines(window):
    _done(window)
    status = _status(window)
    window.apply(
        _coin(
            holding=_holding(avg=None),
            open_orders=(_order(OrderState.UNCONFIRMED),),
            buy_blocked=("하루 손실 한도", "유의·주의"),
            sell_all="전량 매도: 다시 시도하는 중",
            dust=True,
            caution=True,
            last_direction=None,
            last_round=None,
            price=None,
        )
    )
    avg = _find(status, QLabel, "statusCoinAvg_KRW-AAA")
    assert avg.text() == "평균 매수가 없음"
    assert "#FF9548" in avg.styleSheet()  # `error`
    order = _find(status, QLabel, "statusCoinOrder_KRW-AAA_0")
    assert order.text() == "확인 안 됨"
    assert "#FF9548" in order.styleSheet()
    blocked = _find(status, QLabel, "statusCoinBlocked_KRW-AAA")
    assert blocked.text() == "새 매수 막힘: 하루 손실 한도, 유의·주의"
    assert "#EDD05A" in blocked.styleSheet()  # `caution`
    assert "#EDD05A" in _find(status, QLabel, "statusCoinSellAll_KRW-AAA").styleSheet()
    assert _coin_text(status, "statusCoinSellAll_") == "전량 매도: 다시 시도하는 중"
    assert _coin_text(status, "statusCoinDust_") == "팔 수 없는 잔량"
    assert _coin_text(status, "statusCoinLast_") == "아직 판단이 없습니다."
    assert _coin_text(status, "statusCoinPrice_") == "현재가 -"
    tag = _find(status, QLabel, "statusCoinCaution_KRW-AAA")
    assert not tag.isHidden()
    assert tag.text() == "유의·주의"
    # 평균 매수가가 0이어도 "평균 매수가 없음"이다.
    window.apply(_coin(holding=_holding(avg="0")))
    assert avg.text() == "평균 매수가 없음"


def test_several_open_orders_get_one_line_each(window):
    _done(window)
    status = _status(window)
    window.apply(_coin(open_orders=(_order(), _order(OrderState.UNCONFIRMED))))
    assert _text(status, "statusCoinOrder_KRW-AAA_0") == "지정가 매수 대기 990원 × 6.0"
    assert _text(status, "statusCoinOrder_KRW-AAA_1") == "확인 안 됨"


@pytest.mark.parametrize(
    ("view", "expected"),
    [
        (RoundNowView(13, RoundStage.ORDER, NOW, False, NOW), "회차 13: 검사와 주문"),
        (RoundNowView(13, RoundStage.CLEANUP, NOW, False, NOW), "회차 13: 주문 정리"),
        (RoundNowView(13, RoundStage.AI, NOW, False, NOW), "회차 13: AI 호출"),
        (
            RoundNowView(None, None, NOW + timedelta(hours=1, minutes=30), False, NOW),
            "다음 회차 12:00",
        ),
        (RoundNowView(13, RoundStage.ORDER, NOW, True, NOW), "멈춤 동안 회차를 돌리지 않습니다."),
    ],
)
def test_the_current_round_texts(window, view, expected):
    _done(window)
    window.apply(view)
    assert _text(_status(window), "statusRoundNow") == expected


def _row(market, **changes) -> PerformanceRow:
    values = {
        "market": market,
        "ok_rounds": 12,
        "hits": 7,
        "countable": 11,
        "realized_pnl": Decimal("1234.5"),
        "wins": 3,
        "sells": 5,
        "unrealized_pnl": Decimal("450"),
        "asset_change": None,
        "diff_mean": Decimal("0.0021"),
        "diff_abs_mean": Decimal("0.0113"),
    }
    values.update(changes)
    return PerformanceRow(**values)


def _cells(status: QWidget, row: int) -> list[str]:
    return [_text(status, f"statusPerformanceCell_{row}_{column}") for column in range(9)]


def test_the_performance_table_values_trend_colors_and_the_total_row(window):
    _done(window)
    status = _status(window)
    assert _find(status, QWidget, "statusPerformanceCard").isHidden()
    window.apply(
        PerformanceView(
            (
                _row("KRW-AAA"),
                _row(
                    "KRW-BBB",
                    hits=5,
                    countable=10,
                    realized_pnl=Decimal("-300"),
                    wins=1,
                    sells=3,
                    unrealized_pnl=Decimal(0),
                    diff_mean=Decimal("-0.0098"),
                    diff_abs_mean=None,
                ),
                _row(
                    None,
                    ok_rounds=24,
                    hits=12,
                    countable=21,
                    realized_pnl=Decimal("934.5"),
                    wins=4,
                    sells=8,
                    unrealized_pnl=Decimal("450"),
                    asset_change=Decimal("0.005"),
                ),
            ),
            NOW,
        )
    )
    assert _cells(status, 0) == [
        "AAA", "12", "63.64% (7/11)", "▲ +0.21%", "1.13%", "▲ +1,234원", "3/5", "▲ +450원", "",
    ]  # fmt: skip
    assert _cells(status, 1) == [
        "BBB", "12", "50.00% (5/10)", "▼ -0.98%", "-", "▼ -300원", "1/3", "0원", "",
    ]  # fmt: skip
    assert _cells(status, 2) == [
        "합계", "24", "57.14% (12/21)", "▲ +0.21%", "1.13%", "▲ +934원", "4/8", "▲ +450원",
        "▲ +0.50%",
    ]  # fmt: skip
    heads = [_text(status, f"statusPerformanceHeader_{c}") for c in range(9)]
    assert heads == [
        "코인", "판단 회차", "오름·내림 맞음", "예측 차이 평균", "예측 차이 크기 평균",
        "실현 손익", "이익 낸 매도", "평가 손익", "자산 변화",
    ]  # fmt: skip
    cell = lambda r, c: _find(status, QLabel, f"statusPerformanceCell_{r}_{c}")  # noqa: E731
    assert cell(0, 5).property("trend") == "rise"
    assert cell(1, 5).property("trend") == "fall"
    assert not cell(1, 7).property("trend")  # 0은 칠하지 않는다
    assert cell(0, 4).property("trend") in (None, "")  # 크기 평균은 부호가 없다
    assert cell(2, 8).property("trend") == "rise"
    assert "bold" in cell(2, 0).styleSheet()


def test_the_performance_table_before_the_first_judgment_says_so_and_has_the_info(window):
    _done(window)
    status = _status(window)
    empty = _row(None, ok_rounds=0, hits=0, countable=0, wins=0, sells=0, realized_pnl=Decimal(0))
    window.apply(PerformanceView((empty,), NOW))
    note = _find(status, QLabel, "statusPerformanceEmpty")
    assert not note.isHidden()
    assert note.text() == "아직 판단이 없습니다."
    assert _find(status, QScrollArea, "statusPerformanceScroll").isHidden()
    info = _find(status, InfoIcon, "statusPerformanceInfo")
    assert info.toolTip() == "실거래를 켜는 기준이 아닙니다. 판단에 참고하는 값입니다."
    assert info.accessibleName() == "모의 성적 설명"


def test_the_performance_total_row_marks_a_missing_price_as_a_dash(window):
    _done(window)
    status = _status(window)
    row = _row(None, unrealized_pnl=None, asset_change=None, diff_mean=None, diff_abs_mean=None)
    window.apply(PerformanceView((row,), NOW))
    assert _cells(status, 0)[3:5] == ["-", "-"]
    assert _cells(status, 0)[7] == "-"
    assert _cells(status, 0)[8] == "-"


def test_the_status_order_is_title_today_coins_round_performance_stats_alerts(window):
    _done(window)
    status = _status(window)
    window.apply(_today())
    window.apply(RoundNowView(None, None, NOW, False, NOW))
    window.apply(PerformanceView((_row(None),), NOW))
    names = [
        "statusToday",
        "statusCoins",
        "statusRoundNowCard",
        "statusPerformanceCard",
        "statusStats",
    ]
    ys = [_find(status, QWidget, name).mapTo(status, QPoint(0, 0)).y() for name in names]
    assert ys == sorted(ys)
    alert = _find(status, QWidget, "statusAlertCard").mapTo(status, QPoint(0, 0)).y()
    assert alert > ys[-1]


# --- 알림의 가는 곳 ---


def _alert(code: str, category: AlertCategory, coin=None, **changes) -> AlertView:
    values = {"seen": True, "detail": None}
    values.update(changes)
    return AlertView(1, NOW, Mode.PAPER, category, code, coin, "글", **values)


def _click_line(window, alert: AlertView) -> QWidget:
    window.apply(AlertsView((alert,), {}, LoadState.READY))
    status = _status(window)
    select_screen(window, ScreenId.STATUS)
    _find(status, QPushButton, f"alertTab_{alert.category.value}").click()
    QApplication.processEvents()
    QTest.mouseClick(
        _find(status, QLabel, "alertLine_1"),
        Qt.MouseButton.LeftButton,
    )
    return status


def _record_scrolls(window, monkeypatch):
    """창이 화면 자리를 넘기는 곳과 그 코인 칸으로 가는 곳을 적는다(실제 동작은 그대로 부른다)."""
    scrolls: list[tuple[QWidget, QWidget | None]] = []
    coins: list[str | None] = []
    real_scroll = type(window)._scroll_page_to
    real_coin = window._go_status_coin

    def scroll(screen, widget):
        scrolls.append((screen, widget))
        real_scroll(screen, widget)

    def coin(market):
        coins.append(market)
        real_coin(market)

    monkeypatch.setattr(window, "_scroll_page_to", scroll)
    monkeypatch.setattr(window, "_go_status_coin", coin)
    return scrolls, coins


@pytest.mark.parametrize(
    ("code", "category"),
    [
        ("A-3", AlertCategory.TRADE),
        ("A-4", AlertCategory.TRADE),
        ("A-8", AlertCategory.LIMIT),
        ("A-9", AlertCategory.LIMIT),
        ("A-10", AlertCategory.LIMIT),
        ("A-11", AlertCategory.LIMIT),
        ("A-13", AlertCategory.HOLDING),
        ("A-24", AlertCategory.HOLDING),
    ],
)
def test_coin_alerts_go_to_that_coins_card_on_the_status_screen(
    window, monkeypatch, code, category
):
    _done(window)
    _scrolls, coins = _record_scrolls(window, monkeypatch)
    _click_line(window, _alert(code, category, "KRW-BBB"))
    assert coins == ["KRW-BBB"]
    assert current_screen(window) is ScreenId.STATUS


def test_a_25_goes_to_the_coin_card_through_the_go_button(window, monkeypatch):
    _done(window)
    _scrolls, coins = _record_scrolls(window, monkeypatch)
    detail = AlertDetail("전량 매도", "KRW-BBB", "오류", "내용", "그 뒤")
    alert = _alert("A-25", AlertCategory.ERROR, "KRW-BBB", detail=detail)
    window.apply(AlertsView((alert,), {}, LoadState.READY))
    status = _status(window)
    select_screen(window, ScreenId.STATUS)
    _find(status, QPushButton, "alertTab_error").click()
    QTest.mouseClick(_find(status, QLabel, "alertLine_1"), Qt.MouseButton.LeftButton)
    assert coins == []  # 줄을 누르면 펼치기만 한다
    select_screen(window, ScreenId.SETTINGS)
    _find(status, QPushButton, "alertGo_1").click()
    assert coins == ["KRW-BBB"]
    assert current_screen(window) is ScreenId.STATUS


def test_a_12_goes_to_the_today_summary(window, monkeypatch):
    _done(window)
    window.apply(_today())
    scrolls, _coins = _record_scrolls(window, monkeypatch)
    _click_line(window, _alert("A-12", AlertCategory.LIMIT))
    status = _status(window)
    assert scrolls == [(status, status.today_widget())]
    assert current_screen(window) is ScreenId.STATUS


def test_a_23_goes_to_the_top_of_the_status_screen(window, monkeypatch):
    _done(window)
    scrolls, _coins = _record_scrolls(window, monkeypatch)
    detail = AlertDetail("켜기", None, "오류", "내용", "그 뒤")
    alert = _alert("A-23", AlertCategory.ERROR, detail=detail)
    window.apply(AlertsView((alert,), {}, LoadState.READY))
    status = _status(window)
    select_screen(window, ScreenId.STATUS)
    _find(status, QPushButton, "alertTab_error").click()
    QTest.mouseClick(_find(status, QLabel, "alertLine_1"), Qt.MouseButton.LeftButton)
    select_screen(window, ScreenId.SETTINGS)
    _find(status, QPushButton, "alertGo_1").click()
    assert scrolls == [(status, None)]
    assert current_screen(window) is ScreenId.STATUS


def test_a_15_goes_to_the_first_field_with_an_error_in_the_settings(window, monkeypatch):
    _done(window)
    window.apply(
        SaveResult("settings", ok=False, field_errors={"tolerance": "x", "backup_days": "y"})
    )
    scrolls, _coins = _record_scrolls(window, monkeypatch)
    select_screen(window, ScreenId.STATUS)
    _click_line(window, _alert("A-15", AlertCategory.LIMIT))
    settings = screen_widget(window, ScreenId.SETTINGS)
    assert current_screen(window) is ScreenId.SETTINGS
    assert scrolls == [(settings, _find(settings, QWidget, "settingsTolerance").parentWidget())]


def test_a_15_without_a_visible_error_goes_to_the_settings_top(window, monkeypatch):
    _done(window)
    scrolls, _coins = _record_scrolls(window, monkeypatch)
    select_screen(window, ScreenId.STATUS)
    _click_line(window, _alert("A-15", AlertCategory.LIMIT))
    settings = screen_widget(window, ScreenId.SETTINGS)
    assert current_screen(window) is ScreenId.SETTINGS
    assert scrolls == [(settings, None)]


def test_a_16_goes_to_the_coins_of_the_settings(window, monkeypatch):
    _done(window)
    scrolls, _coins = _record_scrolls(window, monkeypatch)
    select_screen(window, ScreenId.STATUS)
    _click_line(window, _alert("A-16", AlertCategory.HOLDING))
    settings = screen_widget(window, ScreenId.SETTINGS)
    assert current_screen(window) is ScreenId.SETTINGS
    assert scrolls == [(settings, _find(settings, QWidget, "settingsCoins"))]


# --- 보고서 ---


def _report(**changes) -> ReportView:
    values = {
        "report_id": 120,
        "market": "KRW-AAA",
        "round_number": 12,
        "close_utc": NOW,
        "state": ReportState.OK,
        "failure": None,
        "base_price": Decimal(1234),
        "search_judgment": None,
        "candle_judgment": None,
        "direction": Direction.UP,
        "predicted_price": Decimal(1260),
        "predicted_reason": "까닭",
        "buy_price": Decimal(990),
        "sell_price": None,
        "result_text": None,
        "last_result": None,
        "compared": (),
        "checklist": (),
        "evidence": (),
        "sources": (),
        "tokens": None,
        "elapsed_report_seconds": 192,
        "order_summary": None,
        "no_order_reason": None,
        "elapsed_order_seconds": None,
    }
    values.update(changes)
    return ReportView(**values)


def _show_report(window, view: ReportView, state=ReportState.OK) -> QWidget:
    _done(window)
    select_screen(window, ScreenId.REPORTS)
    item = RoundItem(120, 12, NOW, state, Direction.UP if state is ReportState.OK else None, None)
    window.apply(ReportListView("KRW-AAA", (item,), LoadState.READY))
    window.apply(view)
    QApplication.processEvents()
    return screen_widget(window, ScreenId.REPORTS)


def _visible_label(screen: QWidget, name: str) -> str | None:
    label = _find(screen, QLabel, name)
    return None if label.isHidden() else label.text()


def test_the_order_line_shows_the_order_or_the_reason_just_below_the_decision(window):
    screen = _show_report(window, _report(order_summary="지정가 매수 990원 × 6.0"))
    assert _visible_label(screen, "reportOrder") == "이 판단으로 낸 주문: 지정가 매수 990원 × 6.0"
    decision_y = _find(screen, QWidget, "reportDecision").mapTo(screen, QPoint(0, 0)).y()
    order_y = _find(screen, QLabel, "reportOrder").mapTo(screen, QPoint(0, 0)).y()
    assert decision_y < order_y  # 판단 바로 아래
    window.apply(_report(no_order_reason="멈춤으로 버림"))
    assert _visible_label(screen, "reportOrder") == "주문하지 않은 까닭: 멈춤으로 버림"
    window.apply(_report())  # 둘 다 없으면 보이지 않는다
    assert _visible_label(screen, "reportOrder") is None


def test_the_order_elapsed_line_is_in_the_usage_section_and_hidden_without_a_value(window):
    screen = _show_report(window, _report(elapsed_order_seconds=200))
    usage = _find(screen, QWidget, "reportUsage")
    assert usage.isAncestorOf(_find(screen, QLabel, "reportOrderElapsed"))
    assert _visible_label(screen, "reportOrderElapsed") == "주문까지 걸린 시간: 3분 20초"
    window.apply(_report(elapsed_order_seconds=None))
    assert screen.findChild(QLabel, "reportOrderElapsed") is None


def test_the_decision_info_icon_is_gone_and_the_note_is_not_shown(window):
    screen = _show_report(window, _report())
    assert screen.findChild(InfoIcon, "reportDecisionInfo") is None
    texts = [label.text() for label in screen.findChildren(QLabel)]
    assert "예측 가격은 보여 주는 값이고 주문을 정하는 데 쓰지 않습니다." not in texts


def test_the_pause_line_is_at_the_top_while_paused_and_old_reports_stay(window):
    screen = _show_report(window, _report())
    line = _find(screen, QLabel, "reportPause")
    assert line.isHidden()
    window.apply(_view(user_paused=True))
    assert not line.isHidden()
    assert line.text() == "멈춤 동안 새 보고서를 받지 않습니다."
    assert _visible_label(screen, "reportMessage") is None  # 지난 보고서는 그대로 보인다
    assert not _find(screen, QWidget, "reportDecision").isHidden()
    window.apply(_view(error_paused=True, error_cause=ErrorPauseCause.SAVE))
    assert not line.isHidden()
    window.apply(_view())
    assert line.isHidden()


def test_a_discarded_round_says_so_and_still_shows_the_usage(window):
    view = _report(state=ReportState.DISCARDED, elapsed_report_seconds=65)
    screen = _show_report(window, view, ReportState.DISCARDED)
    rounds = _find(screen, QListWidget, "reportRounds")
    assert rounds.item(0).text().endswith("멈춤으로 버림")
    assert _visible_label(screen, "reportMessage") == "이 회차는 멈춤으로 버렸습니다."
    for name in ("reportDecision", "reportEvidence", "reportLastResult", "reportChecks"):
        assert _find(screen, QWidget, name).isHidden(), name
    assert _find(screen, QLabel, "reportOrder").isHidden()
    assert not _find(screen, QWidget, "reportUsage").isHidden()  # 12는 보인다


def test_an_ok_report_kept_after_the_pause_shows_the_reason_for_no_order(window):
    screen = _show_report(window, _report(no_order_reason="멈춤으로 버림"))
    assert _visible_label(screen, "reportOrder") == "주문하지 않은 까닭: 멈춤으로 버림"
    assert not _find(screen, QWidget, "reportDecision").isHidden()
