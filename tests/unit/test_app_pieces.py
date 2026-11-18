"""조립의 작은 부품: `Services`, 다리(`UiBridge`), 로그 거르기, 알림의 글과 되풀이."""

import logging
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from fakes.clock import FakeClock
from PySide6.QtWidgets import QApplication

from coin_report_trader.app import alert_rules as rules
from coin_report_trader.app.bridge import UiBridge
from coin_report_trader.app.logging_setup import (
    LOGGER_NAME,
    configure_logging,
    remove_log_handler,
)
from coin_report_trader.app.services import Services, resolve_clock
from coin_report_trader.core import AlertCategory, CoinView, LoadState, SystemClock

# --- Services ----------------------------------------------------------------------------------


def test_services_default_to_the_real_ones() -> None:
    assert Services() == Services(None, None, None, None)
    assert isinstance(resolve_clock(None), SystemClock)
    assert isinstance(resolve_clock(Services()), SystemClock)


def test_a_given_clock_is_used() -> None:
    clock = FakeClock()
    assert resolve_clock(Services(clock=clock)) is clock


# --- UiBridge ----------------------------------------------------------------------------------


def pump(qapp: QApplication, done: Callable[[], bool]) -> None:
    waiter = threading.Event()
    for _ in range(2000):
        qapp.processEvents()
        if done():
            return
        waiter.wait(0.005)
    raise AssertionError("안전 시간 안에 값이 오지 않았다")


def test_the_bridge_applies_values_posted_from_another_thread_on_the_main_thread(
    qapp: QApplication,
) -> None:
    seen: list[tuple[str, object]] = []
    bridge = UiBridge(lambda update: seen.append((threading.current_thread().name, update)))
    view = CoinView("KRW-AAA", LoadState.LOADING)
    thread = threading.Thread(target=bridge.post, args=(view,), name="다른-스레드")
    thread.start()
    thread.join()
    assert seen == []  # 주 스레드가 이벤트를 처리하기 전에는 오지 않는다
    pump(qapp, lambda: bool(seen))
    assert seen == [(threading.main_thread().name, view)]


def test_the_bridge_drops_values_after_it_is_closed(qapp: QApplication) -> None:
    seen: list[object] = []
    bridge = UiBridge()
    bridge.set_apply(seen.append)
    first = CoinView("KRW-AAA", LoadState.LOADING)
    bridge.post(first)
    bridge.close()
    bridge.post(CoinView("KRW-BBB", LoadState.LOADING))  # 닫은 뒤에 넣은 값
    for _ in range(20):
        qapp.processEvents()
    assert seen == []  # 닫기 전에 넣었어도 아직 오지 않은 값은 버린다


def test_the_bridge_without_a_receiver_ignores_values(qapp: QApplication) -> None:
    bridge = UiBridge()
    bridge.post(CoinView("KRW-AAA", LoadState.LOADING))
    for _ in range(5):
        qapp.processEvents()


# --- 로그 거르기(M1 명세 3.10) --------------------------------------------------------------------


def webhook() -> str:
    return "https://" + "discord.com/api/webhooks/" + "1" * 18 + "/" + "Tok_" * 16


def unix_home(name: str) -> str:
    return "/" + "home" + "/" + name


@pytest.fixture
def log_file(tmp_path: Path) -> Callable[[], str]:
    path = tmp_path / "app.log"
    handler = configure_logging(path)
    yield lambda: path.read_text(encoding="utf-8")  # type: ignore[misc]
    remove_log_handler(handler)
    assert logging.getLogger(LOGGER_NAME).handlers == []


def test_log_messages_are_redacted_after_the_arguments_are_filled(
    log_file: Callable[[], str],
) -> None:
    log = logging.getLogger(LOGGER_NAME + ".시험")
    log.error("주소 %s 와 경로 %s", webhook(), unix_home("u1") + "/data/app.db")
    text = log_file()
    assert "(가려서 보임)" in text
    assert "Tok_" not in text
    assert "~/data/app.db" in text
    assert unix_home("u1") not in text


def test_exception_text_and_tracebacks_are_redacted(log_file: Callable[[], str]) -> None:
    log = logging.getLogger(LOGGER_NAME + ".시험")
    try:
        raise RuntimeError(f"보냄 {webhook()} {unix_home('u1')}/x")
    except RuntimeError:
        log.exception("실패")
    text = log_file()
    assert "RuntimeError" in text
    assert "Traceback" in text
    assert "Tok_" not in text
    assert unix_home("u1") not in text
    assert "(가려서 보임)" in text


def test_a_long_message_is_cut_but_each_line_stays_whole(log_file: Callable[[], str]) -> None:
    log = logging.getLogger(LOGGER_NAME + ".시험")
    log.error("가" * 3000)
    log.error("끝")
    lines = log_file().splitlines()
    assert len(lines) == 2
    assert lines[0].count("가") == 2000
    assert lines[1].endswith("| 끝")


def test_a_message_without_arguments_may_contain_percent_signs(log_file: Callable[[], str]) -> None:
    logging.getLogger(LOGGER_NAME + ".시험").error("100% 진행 %s")
    assert "100% 진행 %s" in log_file()


# --- 알림의 글 ---------------------------------------------------------------------------------


def test_alert_specs_follow_the_spec() -> None:
    a1 = rules.candles_failed("KRW-AAA", "봉 받기", "시간 초과", "실패한 봉: 일봉", "그 뒤")
    assert (a1.category, a1.code, a1.coin) == (AlertCategory.ERROR, "A-1", "KRW-AAA")
    assert a1.text == "봉 통계를 만들지 못했습니다(시간 초과)."
    assert a1.detail.coin == "KRW-AAA"
    a29 = rules.save_failed("설정 저장", None, "내용")
    assert (a29.category, a29.code, a29.coin) == (AlertCategory.ERROR, "A-29", None)
    assert (a29.text, a29.error_name) == ("저장하지 못했습니다(DB 오류).", "DB 오류")
    assert a29.after == "저장하지 못한 값은 화면에 남겨 두었습니다."
    a30 = rules.unexpected("봉 통계 계산", "KRW-AAA", "RuntimeError", "내용")
    assert (a30.category, a30.code) == (AlertCategory.OTHER, "A-30")
    assert a30.text == "예상하지 못한 오류가 났습니다(RuntimeError)."
    a31 = rules.markets_failed("시간 초과", "내용")
    assert (a31.category, a31.code, a31.coin, a31.stage) == (
        AlertCategory.ERROR, "A-31", None, "코인 목록 받기",
    )  # fmt: skip
    assert a31.after == "처음 설정이나 설정 화면에서 다시 시도할 수 있습니다."
    deleted = rules.save_failed("알림 지우기", None, "내용")
    assert deleted.text == "지우지 못했습니다(DB 오류)."


# --- 되풀이(3.9) -------------------------------------------------------------------------------


def test_a1_repeats_per_coin_and_error_name_until_a_success() -> None:
    state = rules.RepeatRules()
    assert state.want_a1("KRW-AAA", "시간 초과") is True
    assert state.want_a1("KRW-AAA", "시간 초과") is False
    assert state.want_a1("KRW-BBB", "시간 초과") is True  # 코인마다 따로 센다
    assert state.want_a1("KRW-AAA", "네트워크 끊김") is True  # 이름이 다르면 새 알림
    state.a1_succeeded("KRW-AAA")
    assert state.want_a1("KRW-AAA", "시간 초과") is True
    assert state.want_a1("KRW-BBB", "시간 초과") is False  # 다른 코인의 성공은 풀지 않는다


def test_a29_repeats_per_stage_and_coin_until_that_save_succeeds() -> None:
    state = rules.RepeatRules()
    assert state.want_a29("봉 저장", "KRW-AAA") is True
    assert state.want_a29("봉 저장", "KRW-AAA") is False
    assert state.want_a29("봉 저장", "KRW-BBB") is True
    assert state.want_a29("설정 저장", None) is True
    state.a29_succeeded("봉 저장", "KRW-AAA")
    assert state.want_a29("봉 저장", "KRW-AAA") is True
    assert state.want_a29("설정 저장", None) is False


def test_a31_repeats_per_error_name_until_the_list_is_received() -> None:
    state = rules.RepeatRules()
    assert state.want_a31("시간 초과") is True
    assert state.want_a31("시간 초과") is False
    assert state.want_a31("서버 오류") is True
    state.a31_succeeded()
    assert state.want_a31("시간 초과") is True


def test_a30_is_quiet_for_sixty_minutes_per_stage_coin_and_kind() -> None:
    state = rules.RepeatRules()
    assert state.want_a30("봉 받기", "KRW-AAA", "StorageError", 1000.0) is True
    assert state.want_a30("봉 받기", "KRW-AAA", "StorageError", 1000.0 + 3599) is False
    assert state.want_a30("봉 받기", "KRW-BBB", "StorageError", 1001.0) is True
    assert state.want_a30("봉 받기", "KRW-AAA", "ValueError", 1001.0) is True
    assert state.want_a30("봉 통계 계산", "KRW-AAA", "StorageError", 1001.0) is True
    assert state.want_a30("봉 받기", "KRW-AAA", "StorageError", 1000.0 + 3600) is True
    assert state.want_a30("봉 받기", "KRW-AAA", "StorageError", 1000.0 + 3600 + 3599) is False
