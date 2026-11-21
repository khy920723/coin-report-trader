"""M1 명세 6절의 글 시나리오: B13(웹후크 주소의 꼴, 3.9), B20(가리기와 경로 줄이기, 3.10).

주소와 경로처럼 생긴 글은 실행 중에 이어 붙여 만든다(저장소에 그 꼴을 두지 않는다). `redact`에는
환경 변수의 값을 직접 넘겨, 운영체제와 상관없이 같은 글이 나오는지 본다.
"""

import pytest

from coin_report_trader.core import MASK, is_webhook_url, redact

SCHEME = "https:" + "//"
HOST = "discord" + ".com"
NUMBER = "1234567890" + "12345678"  # 18자
TOKEN = ("Ab9" * 30)[:68]


def url(
    *,
    scheme: str = SCHEME,
    host: str = HOST,
    path: str = "/api/webhooks/",
    number: str = NUMBER,
    token: str = TOKEN,
    tail: str = "",
) -> str:
    return scheme + host + path + number + "/" + token + tail


def users(drive: str, *parts: str) -> str:
    return drive + ":\\" + "\\".join(parts)


@pytest.mark.scenario("B13")
def test_b13_only_the_right_shape_is_a_webhook_url() -> None:
    right = [
        url(),
        url(host="canary." + HOST),
        url(path="/api/v10/webhooks/"),
        url(token=TOKEN[:30] + "." + TOKEN[31:]),
    ]
    wrong = [
        url(scheme="http:" + "//"),
        url(host="example" + ".com"),
        url(number=NUMBER[:10] + "x" + NUMBER[11:]),
        url(tail="/"),
        url(tail="?wait=true"),
    ]
    assert [is_webhook_url(text) for text in right] == [True] * len(right)
    assert [is_webhook_url(text) for text in wrong] == [False] * len(wrong)
    assert is_webhook_url("  " + url() + "\n")  # 앞뒤 빈칸은 지우고 본다
    assert is_webhook_url("") is False
    assert is_webhook_url("   ") is False


@pytest.mark.scenario("B20")
def test_b20_addresses_are_masked_and_paths_are_shortened_the_same_everywhere() -> None:
    name, other = "kim" + "minsu", "lee" + "jiwon"
    profile = users("C", "Users", name)
    env = {
        "USERPROFILE": profile,
        "LOCALAPPDATA": users("C", "Users", name, "AppData", "Local"),
    }
    cases = {
        "주소 " + url() + " 끝": f"주소 {MASK} 끝",
        users("C", "Users", name, "AppData", "Local", "coin-report-trader", "app.db"): (
            "%LOCALAPPDATA%\\coin-report-trader\\app.db"
        ),
        "/" + "home/" + name + "/x.txt": "~/x.txt",
        users("C", "Users", name, "Documents", "a.txt"): "~\\Documents\\a.txt",
        users("C", "Users", name + " Work", "b.txt"): "~\\b.txt",  # 값 뒤에 공백과 낱말
    }
    for text, expected in cases.items():
        assert redact(text, env=env) == expected, text
    # `USERPROFILE`이 `Users` 바로 아래가 아닐 때도 결과가 같다.
    away = {"USERPROFILE": users("D", "Profiles", name)}
    away_cases = {
        users("D", "Profiles", name, "Documents", "a.txt"): "~\\Documents\\a.txt",
        users("D", "Profiles", name + " Work", "b.txt"): "~\\b.txt",
        users("D", "Profiles", other, "c.txt"): "~\\c.txt",  # 같은 부모 폴더 아래 다른 계정
    }
    for text, expected in away_cases.items():
        assert redact(text, env=away) == expected, text
    # 한 글에 모두 있어도 같다(주소와 경로가 섞인 오류 글).
    mixed = " | ".join([*cases, *away_cases][:5])
    assert redact(mixed, env=env) == " | ".join(cases.values())
