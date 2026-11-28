"""5단계 잇기: 실거래 켜기·끄기, 켤 때의 모드, 키, 실거래 설정(M5 명세 3.7, 3.7.4, 3.7.5, 3.8,
4절 15번, 6절의 F1~F4, F16~F23, F25).

가짜 업비트, 가짜 보관소, 가짜 시세, 가짜 시계로만 돈다. 값은 모두 만든 값이다.
"""

import logging
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fakes.live_rig import AAA, BBB, KEYS, LIVE, LiveRig, live_rig_builder
from fakes.upbit import TEST_ACCESS_KEY, TEST_SECRET_KEY, Reply

from coin_report_trader.app import live_flow
from coin_report_trader.core import (
    BelowStopView,
    LiveConfirmView,
    LiveSettings,
    LiveStatusView,
    Mode,
    SaveResult,
    SettingsView,
    TradeStatusView,
    UpbitKeyView,
)
from coin_report_trader.vault import UPBIT_ACCESS_KEY_NAME, UPBIT_SECRET_KEY_NAME

ACCOUNTS = ("GET", "/v1/accounts")
CHANCE = ("GET", "/v1/orders/chance")
CONNECT = "업비트에 연결하지 못했습니다({}). 키, 허용 IP, 권한을 확인하세요."


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Callable[..., LiveRig]]:
    yield from live_rig_builder(tmp_path)


def started(build: Callable[..., LiveRig], **kwargs: object) -> LiveRig:
    rig = build(**kwargs)
    rig.start()
    return rig


def status(rig: LiveRig) -> LiveStatusView:
    return rig.sink.of(LiveStatusView)[-1]


def act(rig: LiveRig, action: Callable[[], None]) -> None:
    action()
    rig.settle()


def mode_messages(rig: LiveRig) -> list[str | None]:
    return [r.message for r in rig.sink.of(SaveResult) if r.target == "live_on"]


# --- 켜는 조건과 `LiveStatusView`(3.7.2의 1~7), F1, F2, F21 ---------------------------------------


def test_f1_no_key_blocks_with_its_reason(build: Callable[..., LiveRig]) -> None:
    rig = started(build, key=False)
    assert status(rig).blocks == ("업비트 키가 없습니다",)
    assert rig.sink.of(UpbitKeyView)[-1].present is False


def test_f2_each_missing_condition_is_its_own_reason(build: Callable[..., LiveRig]) -> None:
    rig = started(build, rounds=29, sold=False, paused=False)
    assert status(rig).blocks == (
        "멈춤 상태가 아닙니다",
        "KRW-AAA의 모의 회차가 모자랍니다(29/30)",
        "모의매매에서 '멈추고 전량 매도'를 한 번 해야 합니다",
    )
    assert status(rig).counts == ((AAA, 29, 30),)
    assert status(rig).sell_all_done is False


def test_a_method_change_counts_the_rounds_from_zero(build: Callable[..., LiveRig]) -> None:
    rig = started(build)
    assert status(rig).counts == ((AAA, 30, 30),)
    trade = rig.worker._settings.trade
    from coin_report_trader.core import TradeMethod

    changed = type(trade)(**{**trade.__dict__, "method": TradeMethod.MEAN_REVERSION})
    act(rig, lambda: rig.worker.save_settings((AAA,), 30, first_setup=False, trade=changed))
    assert status(rig).counts == ((AAA, 0, 30),)


@pytest.mark.parametrize(
    "live",
    [None, LiveSettings(budget=Decimal(100_000))],
    ids=["empty", "budget-only"],
)
def test_f21_missing_live_settings_block_and_paper_runs_on(
    build: Callable[..., LiveRig], live: LiveSettings | None
) -> None:
    rig = started(build, live=live)
    assert status(rig).blocks == (
        "실거래 설정(예산, 코인별 투자 비율, 하루 손실 한도)을 넣어야 합니다",
    )
    assert rig.status().mode is Mode.PAPER


def test_the_status_changes_when_the_pause_changes(build: Callable[..., LiveRig]) -> None:
    rig = started(build)
    assert status(rig).blocks == ()
    act(rig, rig.worker.resume_user)
    assert status(rig).blocks == ("멈춤 상태가 아닙니다",)


# --- 실거래 켜기(3.7.2), F3, F4, F22, F23 ---------------------------------------------------------


def test_f3_prepare_reads_once_and_shows_the_confirm_values(build: Callable[..., LiveRig]) -> None:
    rig = started(build)
    act(rig, rig.worker.prepare_live_on)
    assert len(rig.upbit_calls(*ACCOUNTS)) == 1
    assert len(rig.upbit_calls(*CHANCE)) == 1
    (view,) = rig.sink.of(LiveConfirmView)
    assert view == LiveConfirmView(
        budget=Decimal(100_000),
        coins=((AAA, Decimal(100), Decimal(100_000)),),
        krw_available=Decimal(200_000),
        holdings=((AAA, Decimal(0), Decimal(0)),),
        total=Decimal(200_000),
        as_of=rig.clock.now(),
    )
    assert rig.mode() == "paper"  # 취소면(확인 창 뒤 부르지 않음) 그대로 모의매매다


def test_f3_turning_on_reads_again_writes_live_and_keeps_the_pause(
    build: Callable[..., LiveRig],
) -> None:
    rig = started(build)
    act(rig, rig.worker.prepare_live_on)
    act(rig, rig.worker.turn_live_on)
    assert len(rig.upbit_calls(*ACCOUNTS)) >= 2  # 계정을 다시 읽었다
    assert rig.mode() == "live"
    assert rig.sink.of(TradeStatusView)[-1].mode is Mode.LIVE
    assert rig.sink.of(TradeStatusView)[-1].user_paused is True  # 멈춤은 그대로다
    assert status(rig).mode is Mode.LIVE
    assert mode_messages(rig)[-1] is None
    assert rig.query("SELECT market FROM live_snapshots ORDER BY market") == [("KRW",), (AAA,)]
    assert rig.upbit_calls("POST", "/v1/orders") == []


def test_f4_a_key_error_on_prepare_is_a_message_not_an_error_pause(
    build: Callable[..., LiveRig],
) -> None:
    rig = started(build)
    rig.upbit.script(Reply(401, "no_authorization_ip"), method="GET", path="/v1/accounts")
    act(rig, rig.worker.prepare_live_on)
    assert mode_messages(rig) == [CONNECT.format("IP 미등록")]
    assert rig.sink.of(LiveConfirmView) == []
    assert rig.status().error_paused is False
    assert "A-23" not in rig.codes()
    assert rig.mode() == "paper"


def test_f4_a_key_error_on_the_confirm_does_not_turn_on(build: Callable[..., LiveRig]) -> None:
    rig = started(build)
    act(rig, rig.worker.prepare_live_on)
    rig.upbit.script(Reply(401, "no_authorization_ip"), method="GET", path="/v1/accounts")
    act(rig, rig.worker.turn_live_on)
    assert mode_messages(rig)[-1] == CONNECT.format("IP 미등록")
    assert rig.mode() == "paper"
    assert rig.status().error_paused is False


@pytest.mark.parametrize(
    ("budget", "confirmed"), [(Decimal(300_000), False), (Decimal(250_000), True)]
)
def test_f22_the_budget_must_fit_the_krw_and_the_holdings(
    build: Callable[..., LiveRig], budget: Decimal, confirmed: bool
) -> None:
    live = LiveSettings(
        budget=budget, coin_ratios={AAA: Decimal(100)}, daily_loss_limit=Decimal(3000)
    )
    rig = started(build, live=live, holdings={"AAA": (Decimal(50), Decimal(900))})
    act(rig, rig.worker.prepare_live_on)
    assert bool(rig.sink.of(LiveConfirmView)) is confirmed
    if not confirmed:
        assert status(rig).blocks[-1] == (
            "실거래 예산이 원화 잔고와 보유 평가 금액의 합보다 큽니다(예산 300,000원, 합 250,000원)"
        )


def test_f23_the_confirm_shows_each_coin_and_holding(build: Callable[..., LiveRig]) -> None:
    live = LiveSettings(
        budget=Decimal(100_000),
        coin_ratios={AAA: Decimal(60), BBB: Decimal(40)},
        daily_loss_limit=Decimal(3000),
    )
    rig = started(
        build,
        coins=(AAA, BBB),
        live=live,
        holdings={"BBB": (Decimal(10), Decimal(900))},
        trade_changes={"add_ratio": Decimal(15)},  # 40,000원의 추가 한 번이 최소 주문 금액을 넘게
    )
    act(rig, rig.worker.prepare_live_on)
    (view,) = rig.sink.of(LiveConfirmView)
    assert view.coins == (
        (AAA, Decimal(60), Decimal(60_000)),
        (BBB, Decimal(40), Decimal(40_000)),
    )
    assert view.holdings == ((AAA, Decimal(0), Decimal(0)), (BBB, Decimal(10), Decimal(10_000)))
    assert view.total == Decimal(210_000)


def test_an_unread_holding_price_does_not_turn_on(build: Callable[..., LiveRig]) -> None:
    from coin_report_trader.core import QuoteError

    rig = started(build, holdings={"AAA": (Decimal(5), Decimal(900))})
    rig.quote.script(*[QuoteError("서버 오류")] * 3, kind="price", market=AAA)
    act(rig, rig.worker.prepare_live_on)
    assert mode_messages(rig) == ["현재가를 읽지 못해 확인하지 못했습니다. 다시 누르세요."]


def test_a_holding_below_the_stop_line_asks_first_and_turns_on_after_the_confirm(
    build: Callable[..., LiveRig],
) -> None:
    rig = started(build, holdings={"AAA": (Decimal(10), Decimal(1100))})
    act(rig, rig.worker.prepare_live_on)
    act(rig, rig.worker.turn_live_on)
    (below,) = rig.sink.of(BelowStopView)
    assert (below.market, below.avg_price, below.price) == (AAA, Decimal(1100), Decimal(1000))
    assert rig.mode() == "paper"  # 취소하면(다시 부르지 않음) 켜지 않는다
    act(rig, rig.worker.turn_live_on)  # 확인
    assert rig.mode() == "live"


def test_a_mode_that_cannot_be_written_stays_paper_with_a29(
    build: Callable[..., LiveRig], monkeypatch: pytest.MonkeyPatch
) -> None:
    from coin_report_trader.core import StorageError

    rig = started(build)

    def broken(conn: object, mode: Mode) -> None:
        raise StorageError("만든 쓰기 실패")

    monkeypatch.setattr(live_flow, "write_trade_mode", broken)
    act(rig, rig.worker.turn_live_on)
    assert rig.worker._mode is Mode.PAPER
    assert rig.mode() == "paper"
    (a29,) = [a for a in rig.alerts() if a.code == "A-29"]
    assert a29.detail.stage == "실거래 켜기"
    assert rig.status().error_paused is True  # 매매 DB 쓰기의 실패(3.9)


# --- 실거래 끄기(3.7.3), F16 ------------------------------------------------------------------


def go_live(build: Callable[..., LiveRig], **kwargs: object) -> LiveRig:
    rig = started(build, mode=Mode.LIVE, **kwargs)
    return rig


def test_f16_turning_off_cancels_writes_paper_and_raises_a20(
    build: Callable[..., LiveRig],
) -> None:
    from fakes.ai import answered, good_answers

    rig = go_live(build)
    act(rig, rig.worker.resume_user)
    rig.runner.plan(AAA, lambda req: answered(good_answers(AAA, buy_price="990", predicted="1010")))
    rig.round()
    (uid,) = rig.query("SELECT uid FROM orders WHERE mode = 'live'")
    act(rig, rig.worker.pause)
    assert rig.upbit.order_state(uid[0]) == "cancel"
    act(rig, rig.worker.turn_live_off)
    assert rig.mode() == "paper"
    (a20,) = [a for a in rig.alerts() if a.code == "A-20"]
    assert a20.text == "실거래를 끝냈습니다. 남은 실제 보유에는 손절이 없습니다."
    count = len(rig.upbit_calls(*ACCOUNTS))
    rig.advance(60)
    rig.advance(3600)
    assert len(rig.upbit_calls(*ACCOUNTS)) == count  # 매매 엔진은 실제 계정을 읽지 않는다


def test_turning_off_with_an_order_that_cannot_be_canceled_does_not_turn_off(
    build: Callable[..., LiveRig],
) -> None:
    from fakes.ai import answered, good_answers
    from fakes.upbit import CANCEL_FAILS

    rig = go_live(build)
    act(rig, rig.worker.resume_user)
    rig.runner.plan(AAA, lambda req: answered(good_answers(AAA, buy_price="990", predicted="1010")))
    rig.round()
    rig.upbit.script(*[CANCEL_FAILS] * 4, method="DELETE", path="/v1/order")
    act(rig, rig.worker.pause)
    act(rig, rig.worker.turn_live_off)
    assert rig.mode() == "live"
    assert mode_messages(rig)[-1] == "취소하지 못한 주문이 있어 끄지 못했습니다."


def test_turning_off_in_an_error_pause_lists_the_orders_left(
    build: Callable[..., LiveRig],
) -> None:
    from fakes.ai import answered, good_answers

    rig = go_live(build)
    act(rig, rig.worker.resume_user)
    rig.runner.plan(AAA, lambda req: answered(good_answers(AAA, buy_price="990", predicted="1010")))
    rig.round()
    rig.upbit.script(Reply(401, "expired_access_key"), method="GET", path="/v1/order")
    rig.advance(60)
    assert rig.status().error_paused is True
    act(rig, rig.worker.turn_live_off)
    assert rig.mode() == "paper"
    (a20,) = [a for a in rig.alerts() if a.code == "A-20"]
    assert a20.text.endswith("취소하지 못한 주문: AAA 지정가 매수 990원 × 20.20202020")


def test_turning_off_needs_a_pause(build: Callable[..., LiveRig]) -> None:
    rig = go_live(build)
    act(rig, rig.worker.resume_user)
    act(rig, rig.worker.turn_live_off)
    assert rig.mode() == "live"


# --- 켤 때의 모드(3.7.2의 끝), F19, F20 ---------------------------------------------------------


def test_a_live_mode_is_kept_on_restart_with_the_key(build: Callable[..., LiveRig]) -> None:
    rig = go_live(build)
    assert rig.worker._exchange is not None
    assert rig.status().mode is Mode.LIVE
    calls = [(r.method, r.path) for r in rig.upbit.requests]
    assert calls[:2] == [("GET", "/v1/orders/open"), ACCOUNTS]  # 대조


def test_a_live_mode_without_a_key_is_a23_and_an_error_pause(
    build: Callable[..., LiveRig],
) -> None:
    rig = go_live(build, key=False)
    status_view = rig.status()
    assert (status_view.error_paused, status_view.error_detail) == (True, "키가 맞지 않음")
    (a23,) = [a for a in rig.alerts() if a.code == "A-23"]
    assert a23.detail.error_name == "키가 맞지 않음"
    assert rig.upbit.requests == []


def test_f20_a_build_without_live_turns_a_live_mode_back_with_a20(
    build: Callable[..., LiveRig], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live_flow, "LIVE_TRADING_ALLOWED", False)
    rig = go_live(build)
    assert rig.mode() == "paper"
    assert "A-20" in rig.codes()
    assert status(rig).blocks[0] == "이 판에서는 실거래를 켤 수 없습니다"
    assert status(rig).allowed is False
    assert rig.upbit.requests == []


@pytest.mark.parametrize(
    ("days", "text", "error_name"),
    [
        (340, "업비트 키가 2026-01-30에 만료됩니다. 새 키를 만들어 넣으세요.", "키 만료 임박"),
        (370, "업비트 키가 만료되었습니다. 새 키를 만들어 넣으세요.", "키 만료"),
    ],
)
def test_f19_a_key_near_its_end_is_one_a32(
    build: Callable[..., LiveRig], days: int, text: str, error_name: str
) -> None:
    rig = started(build, key_created=date(2026, 1, 5) - timedelta(days=days))
    (a32,) = [a for a in rig.alerts() if a.code == "A-32"]
    assert (a32.text, a32.detail.stage, a32.detail.error_name) == (text, "업비트 키", error_name)
    assert a32.detail.after == "실거래 켜기 전에 새 키를 넣으세요."


@pytest.mark.parametrize(("left", "alerted"), [(31, False), (30, True)])
def test_a32_starts_thirty_days_before_the_end(
    build: Callable[..., LiveRig], left: int, alerted: bool
) -> None:
    created = date(2026, 1, 5) + timedelta(days=left) - timedelta(days=365)
    rig = started(build, key_created=created)
    assert ("A-32" in rig.codes()) is alerted


# --- 키(3.8), F18 --------------------------------------------------------------------------------


def test_f18_a_key_is_saved_in_a_pause_and_appears_nowhere_else(
    build: Callable[..., LiveRig], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    rig = started(build, key=False)
    access = "KEYA" + "0" * 30
    secret = "KEYS" + "1" * 30
    act(rig, lambda: rig.worker.save_upbit_key(access, secret, date(2026, 1, 2)))
    assert rig.vault.values == {UPBIT_ACCESS_KEY_NAME: access, UPBIT_SECRET_KEY_NAME: secret}
    view = rig.sink.of(UpbitKeyView)[-1]
    assert (view.present, view.created, view.expires) == (
        True,
        date(2026, 1, 2),
        date(2027, 1, 2),
    )
    assert rig.query("SELECT value FROM settings WHERE key = 'upbit_key_created'") == [
        ("2026-01-02",)
    ]
    dump = rig.db.read_bytes()
    assert access.encode() not in dump
    assert secret.encode() not in dump
    assert access not in caplog.text
    assert secret not in caplog.text
    shown = repr(rig.sink.items) + repr([a.text for a in rig.alerts()])
    assert access not in shown
    assert secret not in shown
    problems = rig.db.parent / "problems"
    for path in problems.glob("*") if problems.exists() else ():
        assert access not in path.read_text(encoding="utf-8")
    act(rig, rig.worker.delete_upbit_key)
    assert rig.vault.values == {}
    assert rig.sink.of(UpbitKeyView)[-1].present is False
    assert rig.query("SELECT value FROM settings WHERE key = 'upbit_key_created'") == []


def test_a_key_cannot_be_changed_outside_a_pause(build: Callable[..., LiveRig]) -> None:
    rig = started(build, paused=False)
    act(rig, lambda: rig.worker.save_upbit_key("A" * 30, "B" * 30, date(2026, 1, 2)))
    assert rig.vault.values == KEYS
    results = [r for r in rig.sink.of(SaveResult) if r.target == "upbit_key"]
    assert results[-1].message == "멈춤 상태에서만 키를 고칠 수 있습니다."
    assert rig.sink.of(UpbitKeyView)[-1].can_edit is False


def test_f17_a_key_cannot_be_deleted_in_live(build: Callable[..., LiveRig]) -> None:
    rig = go_live(build)
    act(rig, rig.worker.delete_upbit_key)
    assert rig.vault.values == KEYS
    assert rig.sink.of(UpbitKeyView)[-1].can_edit is False


def test_a_key_that_cannot_be_saved_or_deleted_is_a29_without_an_error_pause(
    build: Callable[..., LiveRig],
) -> None:
    rig = started(build)
    rig.vault.fail_delete()
    act(rig, rig.worker.delete_upbit_key)
    assert rig.sink.of(UpbitKeyView)[-1].message == "키를 지우지 못했습니다."
    rig.vault.heal()
    rig.vault.fail_set()
    act(rig, lambda: rig.worker.save_upbit_key("A" * 30, "B" * 30, date(2026, 1, 2)))
    a29 = [a for a in rig.alerts() if a.code == "A-29"]
    assert [a.detail.stage for a in a29] == ["업비트 키 저장"]  # 같은 단계는 한 번(되풀이)
    assert rig.status().error_paused is False
    assert TEST_ACCESS_KEY not in repr(a29)
    assert TEST_SECRET_KEY not in repr(a29)


# --- 실거래 설정(3.7.5)과 실거래 중의 막음(3.7.4), F17, F25 ---------------------------------------


def saved(rig: LiveRig) -> SettingsView:
    return rig.sink.of(SettingsView)[-1]


def test_f25_live_settings_are_saved_apart_from_the_paper_ones(
    build: Callable[..., LiveRig],
) -> None:
    rig = started(build)
    new = LiveSettings(
        budget=Decimal(150_000), coin_ratios={AAA: Decimal(100)}, daily_loss_limit=Decimal(4000)
    )
    act(rig, lambda: rig.worker.save_settings((AAA,), 30, first_setup=False, live=new))
    assert saved(rig).live == new
    keys = dict(rig.query("SELECT key, value FROM settings WHERE key LIKE '%budget%'"))
    assert keys == {"budget": "1000000", "live_budget": "150000"}


def floors(coin: str) -> str:
    """코인 예산 10,000원에서 첫 매수 20%와 추가 한 번 10%의 하한 글(M3 명세 3.11.2)."""
    return (
        f"{coin}: 첫 매수 금액(2,000원)이 하한(5,790원)보다 작습니다.\n"
        f"{coin}: 추가 한 번의 금액(1,000원)이 최소 주문 금액의 1.1배(5,500원)보다 작습니다."
    )


@pytest.mark.parametrize(
    ("live", "errors"),
    [
        (
            LiveSettings(budget=Decimal(0), coin_ratios={}, daily_loss_limit=Decimal(-1)),
            {
                "live_budget": "0보다 큰 값을 넣으세요.",
                "live_daily_loss_limit": "0보다 큰 값을 넣으세요.",
            },
        ),
        (
            LiveSettings(coin_ratios={AAA: Decimal(60), BBB: Decimal(30)}),
            {"live_coin_ratios": "코인별 투자 비율의 합이 100%여야 합니다(지금 90.00%)."},
        ),
        (
            LiveSettings(
                budget=Decimal(20_000),
                coin_ratios={AAA: Decimal(50), BBB: Decimal(50)},
                daily_loss_limit=Decimal(3000),
            ),
            {f"live_coin_ratio:KRW-{coin}": floors(coin) for coin in ("AAA", "BBB")},
        ),
    ],
    ids=["positive", "sum", "floors"],
)
def test_bad_live_values_are_refused_under_the_live_keys(
    build: Callable[..., LiveRig], live: LiveSettings, errors: dict[str, str]
) -> None:
    rig = started(build, coins=(AAA, BBB), live=None)
    act(rig, lambda: rig.worker.save_settings((AAA, BBB), 30, first_setup=False, live=live))
    result = rig.sink.of(SaveResult)[-1]
    assert (result.target, result.ok) == ("settings", False)
    assert dict(result.field_errors) == errors


def test_f25_live_settings_are_not_saved_in_live(build: Callable[..., LiveRig]) -> None:
    rig = go_live(build)
    new = LiveSettings(
        budget=Decimal(150_000), coin_ratios={AAA: Decimal(100)}, daily_loss_limit=Decimal(4000)
    )
    act(rig, lambda: rig.worker.save_settings((AAA,), 30, first_setup=False, live=new))
    assert saved(rig).live == LIVE
    assert rig.query("SELECT value FROM settings WHERE key = 'live_budget'") == [("100000",)]


def test_f17_live_refuses_a_new_coin_and_a_method_change(build: Callable[..., LiveRig]) -> None:
    from coin_report_trader.core import TradeMethod

    rig = go_live(build)
    act(rig, rig.worker.refresh_markets)
    act(rig, lambda: rig.worker.save_settings((AAA, BBB), 30, first_setup=False))
    assert rig.sink.of(SaveResult)[-1].message == (
        "실거래 중에는 새 코인을 고를 수 없습니다. 모의매매로 돌아가 모의 회차를 채우세요."
    )
    trade = rig.worker._settings.trade
    changed = type(trade)(**{**trade.__dict__, "method": TradeMethod.MEAN_REVERSION})
    act(rig, lambda: rig.worker.save_settings((AAA,), 30, first_setup=False, trade=changed))
    assert rig.sink.of(SaveResult)[-1].message == "모의매매에서만 바꿀 수 있습니다."
    assert saved(rig).coins == (AAA,)


def test_f17_removing_a_coin_in_live_drops_its_live_ratio(build: Callable[..., LiveRig]) -> None:
    live = LiveSettings(
        budget=Decimal(200_000),
        coin_ratios={AAA: Decimal(50), BBB: Decimal(50)},
        daily_loss_limit=Decimal(3000),
    )
    rig = started(build, coins=(AAA, BBB), live=live, mode=Mode.LIVE)
    from fakes.stage3 import stage3_trade

    act(rig, rig.worker.refresh_markets)
    act(
        rig,
        lambda: rig.worker.save_settings(
            (AAA,),
            30,
            first_setup=False,
            trade=stage3_trade((AAA,), method=rig.worker._settings.trade.method),
        ),
    )
    assert saved(rig).coins == (AAA,)
    ratios = rig.query("SELECT key, value FROM settings WHERE key LIKE 'live_coin_ratio:%'")
    assert ratios == [(f"live_coin_ratio:{AAA}", "50")]


def test_turning_off_cancels_an_order_that_is_still_open_and_raises_a20_once(
    build: Callable[..., LiveRig],
) -> None:
    from fakes.ai import answered, good_answers
    from fakes.upbit import CANCEL_FAILS

    rig = go_live(build)
    act(rig, rig.worker.resume_user)
    rig.runner.plan(AAA, lambda req: answered(good_answers(AAA, buy_price="990", predicted="1010")))
    rig.round()
    (uid,) = rig.query("SELECT uid FROM orders WHERE mode = 'live'")
    rig.upbit.script(CANCEL_FAILS, method="DELETE", path="/v1/order")
    act(rig, rig.worker.pause)  # 취소가 한 번 거절되어 주문이 걸린 채 남는다
    assert rig.query("SELECT state FROM orders WHERE mode = 'live'") == [("open",)]
    assert rig.upbit.order_state(uid[0]) == "wait"
    act(rig, rig.worker.turn_live_off)  # 끄기가 남은 주문을 취소한다
    assert rig.mode() == "paper"
    assert rig.query("SELECT state FROM orders WHERE mode = 'live'") == [("canceled",)]
    assert rig.upbit.order_state(uid[0]) == "cancel"
    assert [a.code for a in rig.alerts()].count("A-20") == 1
