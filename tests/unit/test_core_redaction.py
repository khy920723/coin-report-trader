"""웹후크 주소의 꼴, 가리기, 경로 줄이기 시험(M1 명세 3.9, 3.10, B13, B20).

가짜 웹후크 주소와 사용자 폴더 꼴의 경로는 소스에 통째로 적지 않고 실행 중에 이어 붙여 만든다
(개인정보 검사와 웹후크 주소 검사가 글 파일을 본다).
"""

import sys
import unicodedata

import pytest

from coin_report_trader.core import MASK, is_webhook_url, redact, shorten_path

NUMBER = "123456789012345678"  # 18자리
TOKEN = ("Ab9-_." * 12)[:64]  # `.`, `-`, `_`가 든 64자


def webhook(
    *,
    host: str = "discord.com",
    path: str = "api/webhooks",
    number: str = NUMBER,
    token: str = TOKEN,
    scheme: str = "https",
) -> str:
    return f"{scheme}://{host}/{path}/{number}/{token}"


def unix_home(name: str) -> str:
    return "/" + "home" + "/" + name


def mac_home(name: str) -> str:
    return "/" + "Users" + "/" + name


def windows_user_dir(name: str, *, separator: str = "\\") -> str:
    return separator.join(["C:", "Users", name])


# --- is_webhook_url (B13) ---------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        webhook(),
        webhook(host="canary.discord.com", path="api/v10/webhooks"),  # canary, v숫자, `.`가 든 토큰
        webhook(host="ptb.discord.com"),
        webhook(host="discordapp.com"),
        webhook(host="canary.discordapp.com", path="api/v9/webhooks"),
        webhook(host="ptb.discordapp.com", path="api/v123/webhooks"),
        webhook(number="1" * 17),
        webhook(number="9" * 20),
        webhook(token="a" * 60),
        webhook(token="Z" * 200),
        "  " + webhook() + "\t\n",  # 앞뒤 빈칸은 지우고 본다
    ],
)
def test_valid_webhook_urls(url: str) -> None:
    assert is_webhook_url(url) is True


def test_the_token_with_dot_is_really_in_the_valid_example() -> None:
    assert "." in TOKEN
    assert "-" in TOKEN
    assert "_" in TOKEN
    assert len(TOKEN) >= 60


@pytest.mark.parametrize(
    "url",
    [
        webhook(scheme="http"),
        webhook(scheme="HTTPS"),
        webhook(host="example.com"),
        webhook(host="discord.com.example.com"),
        webhook(host="www.discord.com"),
        webhook(host="evil.discord.com"),
        webhook(host="notdiscord.com"),
        webhook(host="discord.com:443"),
        webhook(host="Discord.com"),
        webhook(host="canary.ptb.discord.com"),
        webhook(path="api/webhook"),
        webhook(path="api/v/webhooks"),
        webhook(path="api/vx/webhooks"),
        webhook(path="webhooks"),
        webhook(number="12345678901234567" + "a"),  # 번호에 글자
        webhook(number="1" * 16),
        webhook(number="1" * 21),
        webhook(number="١" * 18),  # 아랍 숫자는 숫자로 보지 않는다
        webhook(token="a" * 59),
        webhook(token=("a" * 59) + "+"),
        webhook(token=("a" * 59) + "%"),
        webhook() + "/",  # 끝의 `/`
        webhook() + "?wait=true",
        webhook() + "#x",
        webhook() + "/github",
        webhook() + "\n" + webhook(),
        webhook() + " x",
        "x " + webhook(),
        "https://" + "discord.com/api/webhooks/" + NUMBER,  # 토큰 없음
        "",
        " ",
        " \t\n ",
    ],
)
def test_invalid_webhook_urls(url: str) -> None:
    assert is_webhook_url(url) is False


# --- redact: 웹후크 주소와 비밀 값 ----------------------------------------------------------


def test_redact_masks_a_webhook_url_wherever_it_is() -> None:
    url = webhook()
    assert redact(f"실패: {url}") == f"실패: {MASK}"
    assert redact(f"{url}?wait=true 그리고 {url}/") == f"{MASK}?wait=true 그리고 {MASK}/"
    assert redact(f"('{url}')") == f"('{MASK}')"


def test_redact_masks_whatever_follows_the_url_up_to_the_token_end() -> None:
    # 끝 조건은 `is_webhook_url`에만 쓴다. 가릴 때는 뒤에 무엇이 이어지든 토큰 끝까지 가린다.
    url = webhook()
    assert redact(url + "/extra?x=1") == f"{MASK}/extra?x=1"
    assert TOKEN not in redact(f"a {url}, b {url}.")


@pytest.mark.parametrize(
    "url",
    [
        webhook(host="canary.discord.com", path="api/v10/webhooks"),
        webhook(host="ptb.discordapp.com"),
        webhook(host="DISCORD.COM"),
        webhook(scheme="HTTPS"),
        webhook().replace("/", "\\/"),  # JSON에서 이스케이프한 꼴
    ],
)
def test_redact_masks_every_form_of_the_url(url: str) -> None:
    shown = redact(f"응답: {url} 끝")
    assert shown == f"응답: {MASK} 끝"
    assert TOKEN not in shown


def test_redact_leaves_text_that_is_not_a_webhook_url() -> None:
    short = webhook(token="a" * 59)
    assert redact(short) == short
    other = webhook(host="example.com")
    assert redact(other) == other
    assert redact("그냥 글입니다.") == "그냥 글입니다."


def test_redact_masks_given_secrets_longest_first() -> None:
    shown = redact("키 abcdef 와 abc 와 abcdef", secrets=["abc", "", "abcdef"])
    assert shown == f"키 {MASK} 와 {MASK} 와 {MASK}"
    assert redact("그대로", secrets=[""]) == "그대로"
    assert redact("그대로") == "그대로"


def test_redact_truncates_to_2000_characters_after_masking() -> None:
    url = webhook()
    text = "x" * 1995 + url + "y" * 10
    shown = redact(text)
    assert shown == "x" * 1995 + MASK[:5]
    assert len(shown) == 2000
    assert TOKEN not in shown
    assert len(redact("가" * 5000)) == 2000
    assert redact("가" * 2000) == "가" * 2000


def test_redact_shortens_paths_after_masking() -> None:
    env = {"HOME": "/srv/h"}
    shown = redact(f"/srv/h/a {webhook()}", env=env)
    assert shown == f"~/a {MASK}"


# --- shorten_path와 redact의 경로 (B20) -----------------------------------------------------


ENV = {"LOCALAPPDATA": "D:\\AppData\\Local", "XDG_DATA_HOME": "/srv/u", "HOME": "/srv/h"}


def test_b20_error_text_with_webhook_and_paths() -> None:
    url = webhook()
    text = (
        f"주소 {url} 를 열지 못함: D:\\AppData\\Local\\coin-report-trader\\data\\app.db, "
        f"{unix_home('alice')}/x.db, {windows_user_dir('bob')}\\y"
    )
    assert redact(text, env=ENV) == (
        f"주소 {MASK} 를 열지 못함: %LOCALAPPDATA%\\coin-report-trader\\data\\app.db, ~/x.db, ~\\y"
    )


def test_b20_gives_the_same_text_whatever_the_running_os(monkeypatch: pytest.MonkeyPatch) -> None:
    text = (
        f"{webhook()} D:\\AppData\\Local\\a /srv/u/b /srv/h/c "
        f"{unix_home('alice')}/d {mac_home('carol')}/e {windows_user_dir('bob', separator='/')}/f"
    )
    first = redact(text, env=ENV)
    monkeypatch.setattr(sys, "platform", "win32")
    assert redact(text, env=ENV) == first
    monkeypatch.setattr(sys, "platform", "darwin")
    assert redact(text, env=ENV) == first
    assert first == f"{MASK} %LOCALAPPDATA%\\a $XDG_DATA_HOME/b ~/c ~/d ~/e ~/f"


def test_shorten_path_uses_the_env_prefixes() -> None:
    text = "D:\\AppData\\Local\\x /srv/u/y /srv/h/z"
    assert shorten_path(text, ENV) == "%LOCALAPPDATA%\\x $XDG_DATA_HOME/y ~/z"


def test_shorten_path_lets_the_longer_prefix_win() -> None:
    env = {"HOME": "/srv/h", "XDG_DATA_HOME": "/srv/h/.local/share"}
    assert shorten_path("/srv/h/.local/share/x /srv/h/y", env) == "$XDG_DATA_HOME/x ~/y"


@pytest.mark.parametrize(
    "written",
    [
        "D:\\AppData\\Local\\x",
        "d:\\appdata\\local\\x",  # 대소문자만 다르다
        "D:/AppData/Local/x",  # /가 섞였다
        "D:\\AppData/Local\\x",
        "D:\\\\AppData\\\\Local\\\\x",  # str(OSError)처럼 역슬래시가 둘
        "D://AppData//Local//x",
    ],
)
def test_shorten_path_sees_windows_forms(written: str) -> None:
    shown = shorten_path(f"열지 못함 '{written}'", {"LOCALAPPDATA": "D:\\AppData\\Local"})
    assert shown.startswith("열지 못함 '%LOCALAPPDATA%")
    assert shown.endswith("x'")
    assert "AppData\\" not in shown.replace("%LOCALAPPDATA%", "")
    assert "D:" not in shown


@pytest.mark.parametrize(
    "base", ["D:\\AppData\\Local\\", "D:\\AppData\\Local/", "D:/AppData/Local//"]
)
def test_shorten_path_accepts_an_env_value_ending_with_a_separator(base: str) -> None:
    assert shorten_path("D:\\AppData\\Local\\x", {"LOCALAPPDATA": base}) == "%LOCALAPPDATA%\\x"
    assert shorten_path("D:/AppData/Local/x", {"LOCALAPPDATA": base}) == "%LOCALAPPDATA%/x"


def test_shorten_path_is_case_sensitive_for_unix_prefixes() -> None:
    assert shorten_path("/SRV/H/x", {"HOME": "/srv/h"}) == "/SRV/H/x"
    assert shorten_path("/srv/h/x", {"HOME": "/srv/h"}) == "~/x"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (unix_home("alice") + "/x", "~/x"),
        (unix_home("alice"), "~"),
        (f"({unix_home('alice')})", "(~)"),
        (f"{unix_home('alice')}: 거부됨", "~: 거부됨"),
        (mac_home("carol") + "/Library/x", "~/Library/x"),
        (windows_user_dir("bob") + "\\AppData\\x", "~\\AppData\\x"),
        (windows_user_dir("bob", separator="/") + "/x", "~/x"),
        (windows_user_dir("bob", separator="\\\\") + "\\\\x", "~\\\\x"),
        (windows_user_dir("bob").lower() + "\\x", "~\\x"),
        ("X" + windows_user_dir("bob")[1:] + "\\x", "~\\x"),  # 드라이브는 아무 글자
        (windows_user_dir("John Smith") + "\\x", "~\\x"),  # 공백이 든 이름
        (windows_user_dir("김민수") + "\\x", "~\\x"),  # 한글 이름
        (f"{unix_home('alice')}/a 와 {unix_home('bob')}/b", "~/a 와 ~/b"),
    ],
)
def test_shorten_path_replaces_user_names_in_known_places(text: str, expected: str) -> None:
    assert shorten_path(text, {}) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 이름만 `~`로 바뀌고, 이름 뒤의 글과 집 폴더가 아닌 다른 경로는 그대로 남는다.
        (f"open {unix_home('u1')} failed, retry /tmp/x", "open ~ failed, retry /tmp/x"),
        (f"users: {unix_home('u1')} and {unix_home('u2')}", "users: ~ and ~"),
        (f"path={unix_home('u1')} count=3 dir=/opt/app", "path=~ count=3 dir=/opt/app"),
        (f"{windows_user_dir('u1', separator='/')}, then /var/log/x", "~, then /var/log/x"),
        (
            f"{windows_user_dir('u1')} failed, retry D:\\\\tmp\\\\x",
            "~ failed, retry D:\\\\tmp\\\\x",
        ),
        (f"{mac_home('u1')} and then /opt/app/y", "~ and then /opt/app/y"),
        (f"[{unix_home('u1')}] ok; {unix_home('u2')} done", "[~] ok; ~ done"),
        (f"{unix_home('u1')}/a.db copied to /var/b.db", "~/a.db copied to /var/b.db"),
        # 공백이 든 이름은 바로 뒤에 구분자가 올 때만 이름으로 본다.
        (f"{windows_user_dir('John Smith')}\\x failed, retry /tmp/x", "~\\x failed, retry /tmp/x"),
    ],
)
def test_shorten_path_changes_only_the_user_name(text: str, expected: str) -> None:
    assert shorten_path(text, {}) == expected
    assert redact(text, env={}) == expected


# 만든 사용자 이름. Windows 사용자 이름에는 공백(둘 이상도), 괄호, `&`, `!`, `#`, `'`가 들 수 있다.
HARD_NAMES = [
    "Kim (Work)",
    "A&B",
    "u1(2)",
    "u1!",
    "u1#a",
    "John  Smith",  # 공백 둘
    "O'Brien",
    "Kim (Work Stuff) Lee",
    "a (b) (c)",
    "r&d  team (z9)",
    "u1.tmp-3",
    "김민수 (회사)",
    # 짝이 맞지 않는 괄호와 따옴표가 든 이름도 한 글자도 남지 않는다.
    "A)B",
    "Kim (Work",
    "u1)",
    "(u1",
    "Kims'",
    "'Kim",
    "u1 ((a))",
    # ASCII 공백이 아닌 공백 문자(전각 공백, NBSP, 가는 공백 등 유니코드 Zs)가 든 이름
    "김\u3000민수",
    "a\u00a0b",
    "a\u2009b",
    "a\u202fb",
    "a\u1680b",
    "a\u205fb",
]


@pytest.mark.parametrize("name", HARD_NAMES)
@pytest.mark.parametrize("separator", ["\\", "/", "\\\\"])
def test_shorten_path_removes_the_whole_windows_user_name(name: str, separator: str) -> None:
    path = windows_user_dir(name, separator=separator)
    shown = shorten_path(f"열지 못함 '{path}{separator}x'", {})
    assert shown == f"열지 못함 '~{separator}x'"
    # 이름의 어떤 조각도 글에 남지 않는다.
    for piece in name.replace("(", " ").replace(")", " ").replace("&", " ").split():
        assert piece not in shown


@pytest.mark.parametrize("name", HARD_NAMES)
def test_shorten_path_removes_the_whole_unix_user_name(name: str) -> None:
    for make in (unix_home, mac_home):
        shown = shorten_path(f"cannot open {make(name)}/x.db now", {})
        assert shown == "cannot open ~/x.db now"


@pytest.mark.parametrize(
    "separator",
    [
        "\t",
        "\n",
        "\r\n",
        "\r",
        "\f",
        "\v",
        "\x1c",
        "\x1d",
        "\x1e",
        "\x1f",
        "\x85",
        "\u2028",
        "\u2029",
    ],
)
def test_shorten_path_does_not_take_a_tab_or_a_line_break_as_part_of_a_name(
    separator: str,
) -> None:
    assert shorten_path(f"{unix_home('u1')}{separator}col/x", {}) == f"~{separator}col/x"
    windows = f"{windows_user_dir('u1')}{separator}col\\x"
    assert shorten_path(windows, {}) == f"~{separator}col\\x"


@pytest.mark.parametrize("gap", ["\u3000", "\u00a0", "\u2009", " ", "   "])
def test_shorten_path_joins_words_with_any_other_whitespace(gap: str) -> None:
    assert shorten_path(f"{unix_home('Kim' + gap + 'Lee')}/x", {}) == "~/x"
    assert shorten_path(f"{windows_user_dir('Kim' + gap + 'Lee')}\\x", {}) == "~\\x"


def test_shorten_path_takes_exactly_the_space_separators_as_part_of_a_name() -> None:
    # 공백 분류(Zs)의 글자는 낱말을 잇고, 그 밖의 공백(제어 글자, 줄·문단 구분)은 이름을 끝낸다.
    for code in range(0x3100):
        char = chr(code)
        if not char.isspace():
            continue
        shown = shorten_path(f"{unix_home('u1')}{char}a/b", {})
        if unicodedata.category(char) == "Zs":
            assert shown == "~/b", hex(code)
        else:
            assert shown == f"~{char}a/b", hex(code)


def test_shorten_path_keeps_quoted_paths_that_end_at_the_name() -> None:
    # 따옴표로 싼 경로는 이름에서 끝나면(뒤에 구분자가 없으면) 따옴표 밖의 글을 먹지 않는다.
    assert shorten_path(f"No such file: '{windows_user_dir('u1')}'", {}) == "No such file: '~'"
    renamed = f"rename '{windows_user_dir('u1')}\\a' -> '{windows_user_dir('u1')}\\b'"
    assert shorten_path(renamed, {}) == "rename '~\\a' -> '~\\b'"
    assert shorten_path(f"'{unix_home('u1')}/a' and '{unix_home('u2')}/b'", {}) == "'~/a' and '~/b'"
    assert shorten_path(f'"{unix_home("u1")}" and "{unix_home("u2")}"', {}) == '"~" and "~"'
    assert shorten_path(f"{unix_home('u1')}) and {unix_home('u2')}'", {}) == "~) and ~'"


def test_shorten_path_stops_the_name_at_characters_a_windows_name_cannot_have() -> None:
    for stop in (",", ";", "=", ":", "|", "?", "*", "<", ">", "[", "]", "+", "@"):
        text = f"{unix_home('u1')} done{stop} then a/b"
        assert shorten_path(text, {}) == f"~ done{stop} then a/b"


@pytest.mark.parametrize(
    "text",
    [
        "/srv/" + "home" + "/alice/x",  # 앞에 폴더가 더 있다
        "/" + "home",
        "/" + "home" + "/",
        "C:\\" + "Users",
        "C:\\" + "Users" + "\\",
        "그냥 글",
        "~/x",
    ],
)
def test_shorten_path_leaves_other_text(text: str) -> None:
    assert shorten_path(text, {}) == text


@pytest.mark.parametrize("value", ["", "relative/dir", "rel", "/", "\\", "D:\\", "D:"])
def test_shorten_path_ignores_env_values_that_are_not_useful_prefixes(value: str) -> None:
    text = "a/b x D:\\y /z rel/q"
    assert (
        shorten_path(text, {"HOME": value, "LOCALAPPDATA": value, "XDG_DATA_HOME": value}) == text
    )


def test_shorten_path_does_not_touch_a_longer_folder_name() -> None:
    assert shorten_path("/srv/h2/x /srv/hx", {"HOME": "/srv/h"}) == "/srv/h2/x /srv/hx"


def test_shorten_path_is_idempotent() -> None:
    text = f"{unix_home('alice')}/x D:\\AppData\\Local\\y"
    once = shorten_path(text, ENV)
    assert shorten_path(once, ENV) == once


def test_shorten_path_defaults_to_the_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", "/srv/process-home")
    assert shorten_path("/srv/process-home/x") == "~/x"
    assert redact("/srv/process-home/x") == "~/x"
