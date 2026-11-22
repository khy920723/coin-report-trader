"""M2 명세 6절의 문제 보고 표 시나리오: C31, C32, C33(3.11.1).

자료 스레드가 오류 알림과 기타 알림마다 자료 폴더의 `problems\\problems-YYYY-MM.csv`에 한 줄을
덧붙인다. 시각은 가짜 시계로 정한다(세션을 붙잡은 동안 시계를 옮겨 예외가 나는 때를 맞춘다). 주소와
사용자 폴더 꼴의 글은 실행 중에 이어 붙여 만든다. 값은 모두 만든 값이다.
"""

import hashlib
import json
import logging
import re
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from acceptance.m2_support import AAA, BBB, HOUR, Rig, make_rig, restart, token_of, webhook_url
from fakes.ai import FakeAiRunner, answered, good_answers, wrong_header_answers
from fakes.clock import FakeClock
from fakes.quote import make_candles
from PySide6.QtWidgets import QApplication

from coin_report_trader import __version__
from coin_report_trader.__main__ import main
from coin_report_trader.ai.answers import FINAL_JUDGMENT_COLUMNS
from coin_report_trader.ai.csv_files import write_csv
from coin_report_trader.app import data_thread, report_round, startup_errors
from coin_report_trader.config.paths import resolve_app_paths
from coin_report_trader.core import QuoteError, StorageError
from coin_report_trader.core.texts import ERROR_NETWORK

SAFETY = 10.0
HEADER = ("alert_id,occurred_kst,app_version,mode,category,code,stage,error_name,source,coin,"
          "round_number,repeat_24h,fingerprint,message")  # fmt: skip
BOM = chr(0xFEFF)


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Callable[..., Rig]]:
    made: list[Rig] = []

    def factory(**kwargs: Any) -> Rig:
        rig = make_rig(tmp_path / f"r{len(made)}", **kwargs)
        made.append(rig)
        return rig

    def track(rig: Rig) -> Rig:
        made.append(rig)
        return rig

    factory.track = track  # type: ignore[attr-defined]
    yield factory
    for rig in made:
        rig.stop()


def fresh_candles(rig: Rig, *markets: str) -> None:
    """지금 시각에 걸친 봉까지 이어진 봉을 가짜 시세에 둔다."""
    last = rig.clock.now().replace(minute=0, second=0, microsecond=0)
    for market in markets:
        rig.quote.set_candles(market, HOUR, make_candles(market, HOUR, 200, last_start=last))


def round_failing_at(rig: Rig, when: datetime, market: str = AAA) -> None:
    """다음 회차의 세션을 붙잡고 시계를 `when`으로 옮긴 뒤 놓는다.

    그 세션이 `RuntimeError`를 낸다.
    """
    hold = threading.Event()
    started = rig.runner.plan(market, raises=RuntimeError("만든 실패"), wait=hold)
    rig.to_next_due()
    assert started.wait(SAFETY)
    assert rig.clock.now() <= when
    rig.clock.set_now(when)
    hold.set()
    rig.drain()


def fingerprint(*values: str) -> str:
    return hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()[:12]


def file_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


# --- C31 --------------------------------------------------------------------------------------


@pytest.mark.scenario("C31")
def test_c31_the_problem_table_follows_the_spec_columns_time_source_and_repeat_count(
    build: Callable[..., Rig],
) -> None:
    clock = FakeClock(datetime(2026, 11, 30, 14, 20, tzinfo=UTC))
    rig = build(coins=(AAA, BBB), clock=clock)
    fresh_candles(rig, AAA, BBB)
    rig.start()
    first = datetime(2026, 11, 30, 15, 30, tzinfo=UTC)  # 한국 2026-12-01 00:30
    round_failing_at(rig, first)  # 회차 1. BBB는 같은 때에 보고서가 OK다
    path = rig.problems / "problems-2026-12.csv"
    text = file_text(path)
    assert text.startswith(BOM)
    lines = text.removeprefix(BOM).split("\r\n")
    assert lines[0] == HEADER  # 3.11.1의 열넷, 그 차례 그대로
    assert lines[-1] == ""  # 줄 끝은 CRLF
    assert "\n" not in text.replace("\r\n", "")
    (row,) = rig.problem_rows()
    assert row["code"] == "A-30"
    assert row["occurred_kst"] == "2026-12-01T00:30:00+09:00"
    assert row["app_version"] == f"v{__version__}"
    assert (row["category"], row["round_number"], row["repeat_24h"]) == ("기타", "1", "1")
    assert re.fullmatch(r"app/[\w/]+\.py:\d+", row["source"]), row["source"]
    assert not Path(row["source"].split(":")[0]).is_absolute()
    assert row["fingerprint"] == fingerprint("A-30", "AI 보고서", "RuntimeError", row["source"])
    assert [r["code"] for r in rig.problem_rows()] == ["A-30"]  # A-26의 줄은 없다
    assert "A-26" in rig.codes()

    # 첫 예외로 AI가 꺼지므로 같은 자료 폴더로 앱을 다시 켜고, 첫 예외 + 61분에 같은 예외.
    rig = build.track(restart(rig))  # type: ignore[attr-defined]
    fresh_candles(rig, AAA, BBB)
    rig.start()
    second = first + timedelta(minutes=61)
    round_failing_at(rig, second)
    # 다시 켜고 둘째 예외 + 25시간에 같은 예외.
    rig = build.track(restart(rig))  # type: ignore[attr-defined]
    rig.clock.set_now(second + timedelta(hours=24, minutes=10))
    fresh_candles(rig, AAA, BBB)
    rig.start()
    third = second + timedelta(hours=25)
    round_failing_at(rig, third)
    rows = [r for r in rig.problem_rows() if r["code"] == "A-30"]
    assert [r["occurred_kst"] for r in rows] == [
        "2026-12-01T00:30:00+09:00",
        "2026-12-01T01:31:00+09:00",
        "2026-12-02T02:31:00+09:00",
    ]
    assert len({r["fingerprint"] for r in rows}) == 1
    assert [r["repeat_24h"] for r in rows] == ["1", "2", "1"]


@pytest.mark.scenario("C31")
def test_c31_a_repeated_a17_writes_a_line_without_an_id_and_counts_up(
    build: Callable[..., Rig],
) -> None:
    runner = FakeAiRunner()
    runner.plan(AAA, answered(wrong_header_answers(AAA)))
    rig = build(runner=runner)
    rig.start()
    rig.round()
    rig.round()
    rows = [r for r in rig.problem_rows() if r["code"] == "A-17"]
    assert len(rows) == 2
    assert rows[0]["alert_id"].isdigit()
    assert rows[0]["repeat_24h"] == "1"
    assert rows[1]["alert_id"] == ""  # 되풀이로 알림을 만들지 않았다
    assert rows[1]["repeat_24h"] == "2"
    assert rig.codes().count("A-17") == 1


# --- C32 --------------------------------------------------------------------------------------


@pytest.mark.scenario("C32")
def test_c32_the_message_hides_addresses_paths_and_the_user_name_and_is_one_line_of_500(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    user = "zuser" + "9"
    profile = "C:\\" + "Users\\" + user
    monkeypatch.setenv("USERPROFILE", profile)
    url = webhook_url("t")
    text = (
        f"주소 {url} 서버 192.0.2.10 다른 2001:db8::1 파일 {profile}\\docs\\a.txt "
        "라이브러리 C:\\Program Files\\x\\y.dll 또 /opt/app/lib.so 시각 10:30:00 "
        "참고 https://example.com/a/b\n둘째 줄 " + "가" * 600
    )
    runner = FakeAiRunner()
    runner.plan(AAA, raises=RuntimeError(text))
    rig = build(runner=runner)
    rig.start()
    rig.round()
    (row,) = [r for r in rig.problem_rows() if r["code"] == "A-30"]
    message = row["message"]
    for hidden in (token_of(url), "192.0.2.10", "2001:db8::1", user, profile,
                   "C:\\Program Files", "/opt/app/lib.so"):  # fmt: skip
        assert hidden not in message, hidden
    assert "(가려서 보임)" in message
    assert "(경로)" in message
    assert "10:30:00" in message
    assert "https://example.com/a/b" in message
    assert "\n" not in message
    assert "\r" not in message
    assert len(message) == 500
    assert list(row) == HEADER.split(",")  # 열은 열넷뿐이다(금액, 잔고, 주문을 담는 열이 없다)
    assert user not in json.dumps(rig.problem_rows(), ensure_ascii=False)


@pytest.mark.scenario("C32")
def test_c32_answer_text_never_reaches_the_message_of_the_a17_or_the_a30(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    marked = "지시-" + "ZZZ"
    answers = good_answers(AAA)
    answers["final_judgment"] = write_csv(
        FINAL_JUDGMENT_COLUMNS, [[AAA, "", marked, "102", "까닭", "99", ""]]
    )
    runner = FakeAiRunner()
    runner.plan(AAA, answered(answers))
    rig = build(runner=runner)
    rig.start()
    rig.round()  # FAILED(A-17)
    runner.plan(AAA, None)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise ValueError(marked)

    monkeypatch.setattr(report_round.ai_answers, "judge", broken)
    rig.round()  # AI의 답을 다루다 난 예외(A-30)
    rows = rig.problem_rows()
    assert [r["code"] for r in rows] == ["A-17", "A-30"]
    for row in rows:
        assert marked not in row["message"]
    assert rows[1]["message"].startswith(
        "예상하지 못한 오류가 났습니다(ValueError). · ValueError · "
    )


@pytest.mark.scenario("C32")
def test_c32_a_memory_alert_has_a_negative_id_and_a_moved_old_line_is_app_startup_errors(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rig = build()
    line = startup_errors.startup_line(RuntimeError("창 오류"), rig.clock.now())
    del line["source"]  # 21번 앞의 줄: `source` 키가 없다
    startup_errors.append_line(rig.logs, line)
    rig.start()
    (moved,) = rig.problem_rows()
    assert moved["source"] == "app.startup_errors"
    assert moved["alert_id"].isdigit()

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise StorageError("만든 쓰기 실패")

    monkeypatch.setattr(data_thread.alert_store, "insert_alert", broken)
    rig.quote.script(QuoteError(ERROR_NETWORK), kind="price", market=AAA)
    rig.round()
    memory = [r for r in rig.problem_rows() if r["code"] == "A-29"]
    assert len(memory) == 1
    assert int(memory[0]["alert_id"]) < 0  # 메모리 알림의 음수 번호


# --- C33 --------------------------------------------------------------------------------------


@pytest.mark.scenario("C33")
def test_c33_old_files_are_pruned_a_bad_header_file_is_kept_as_bad_and_a_new_file_is_started(
    build: Callable[..., Rig],
) -> None:
    clock = FakeClock(datetime(2026, 12, 5, 2, 20, tzinfo=UTC))  # 한국 2026-12-05 11:20
    runner = FakeAiRunner()
    runner.plan(AAA, raises=RuntimeError("만든 실패"))
    rig = build(clock=clock, runner=runner)
    fresh_candles(rig, AAA)
    folder = rig.problems
    folder.mkdir()
    for name in ("problems-2026-08.csv", "problems-2026-08.csv.bad", "problems-2026-09.csv",
                 "problems-2026-10.csv", "problems-2026-11.csv", "notes.txt"):  # fmt: skip
        (folder / name).write_text("만든 글\r\n", encoding="utf-8")
    (folder / "problems-2026-12.csv").write_text(BOM + "a,b,c\r\n1,2,3\r\n", encoding="utf-8")
    rig.start()
    rig.round()  # A-30
    assert sorted(p.name for p in folder.iterdir()) == [
        "notes.txt",
        "problems-2026-10.csv",
        "problems-2026-11.csv",
        "problems-2026-12.csv",
        "problems-2026-12.csv.bad",
    ]
    assert file_text(folder / "problems-2026-12.csv.bad") == BOM + "a,b,c\r\n1,2,3\r\n"
    lines = file_text(folder / "problems-2026-12.csv").removeprefix(BOM).split("\r\n")
    assert lines[0] == HEADER
    assert lines[1].split(",")[5] == "A-30"
    assert lines[2:] == [""]


@pytest.mark.scenario("C33")
def test_c33_when_problems_is_a_file_the_failure_is_only_logged_and_the_work_goes_on(
    build: Callable[..., Rig], caplog: pytest.LogCaptureFixture
) -> None:
    rig = build()
    rig.problems.write_text("폴더가 아니라 파일", encoding="utf-8")
    caplog.set_level(logging.WARNING)
    rig.start()
    rig.quote.script(*[QuoteError(ERROR_NETWORK)] * 3, kind="price", market=AAA)
    rig.round()  # A-2: 문제 보고 표에 쓰지 못한다
    fetches = len(rig.quote.candle_requests(market=AAA))
    rig.round()
    assert rig.codes() == ["A-2"]  # 줄을 쓰지 못한 것에 대한 새 알림이 없다
    assert any("문제 보고" in record.getMessage() for record in caplog.records)
    assert rig.rows("SELECT COUNT(*) FROM rounds") == [(2,)]  # 회차는 그대로 돈다
    assert len(rig.quote.candle_requests(market=AAA)) > fetches  # 봉 받기도 그대로다
    assert rig.problems.read_text(encoding="utf-8") == "폴더가 아니라 파일"


@pytest.mark.scenario("C33")
def test_c33_the_self_test_does_not_make_the_problems_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp: QApplication
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["--self-test"]) == 0
    assert not (resolve_app_paths().root / "problems").exists()
    assert not list(tmp_path.rglob("problems"))
