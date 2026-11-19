"""앱의 TLS 설정(`make_tls_context`)과 화면 글 상수의 시험."""

import ssl
from datetime import UTC, datetime
from pathlib import Path

import pytest

from coin_report_trader.core import KST, make_tls_context, texts

# 이 시험을 위해 만든 자체 서명 인증서(CA). 개인 키는 버렸다. 실제 서버를 믿는 데는 쓰지 않는다.
TEST_CA_PEM = """\
-----BEGIN CERTIFICATE-----
MIIDPTCCAiWgAwIBAgIUXjc89Is+93yvoHeGPgSL8zwBeE8wDQYJKoZIhvcNAQEL
BQAwJTEjMCEGA1UEAwwaQ29pbiBSZXBvcnQgVHJhZGVyIFRlc3QgQ0EwIBcNMjYx
MTE2MDk1MTQ4WhgPMjEyNjEwMjMwOTUxNDhaMCUxIzAhBgNVBAMMGkNvaW4gUmVw
b3J0IFRyYWRlciBUZXN0IENBMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKC
AQEA2y++n7spkTJZgmtC/YK6Bj5ORTMCUw7ZQdffeL+TkCcRRiEXtuvOV/Y9h6GV
fiB9gc5xLxlROayq+8FrlLTCbAArSQOZtC1vN7tuetM+Y3Cr0t3pJG6LSt9dMY3/
9hCETjkS7GafUcjYweErrqivNtxbXOjZ6H/KGogBjM+U1u54QUaoMcrZAJBtQPTH
CJzzyfDNR0DRYT5pqSl0tws3uaLEAakBmPRkbnXbedLBHt2W4tNid54m6oTK2tQf
7qKZcT503IBa0fu9PhfRwixRLH6CT9pMdeb5wTLVcwVW0ZAdr8SS3yoqbetk8TUq
tUVZlLSngKeHd3K7q2mmQGHvnwIDAQABo2MwYTAdBgNVHQ4EFgQUXmplyQbxX9ul
SISCt69ob92qoKkwHwYDVR0jBBgwFoAUXmplyQbxX9ulSISCt69ob92qoKkwDwYD
VR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMCAQYwDQYJKoZIhvcNAQELBQADggEB
AHPZAN2f1OAjKm5qCC39wFz20wAjq2BFACyD468GzaXlECP0n+vwIqrKTsL9JCWm
cOWYwamfzFhHpUl8P+GqgImuGyYBFOLRLoVAu/hSjUCwgYkzThywOGbImqyxYUGf
VFeiU88AZwAIdLsjTKhlqaLJsuMFlHp0xm7PEsds1mi0xYA+YNTP65Ebx1LVzgPB
yV2MvOQsenahutx//uiPHm8QvBjiiQJAjFlSvi0y/ocyd0stV5GcxD6CLv7g6nTI
kkZHhpe8wV9WcCV7y6+/GpBCqBWKrbjxLXTFD22tx7S8pArai9q2RAHau5qCaLOE
AeJKOhQ3dQMou75d1F4avY8=
-----END CERTIFICATE-----
"""


@pytest.fixture
def cafile(tmp_path: Path) -> str:
    path = tmp_path / "bundle.pem"
    path.write_text(TEST_CA_PEM, encoding="ascii")
    return str(path)


def test_tls_context_follows_the_policy(cafile: str) -> None:
    context = make_tls_context(cafile)
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.keylog_filename is None


def test_tls_context_trusts_only_the_given_file(cafile: str) -> None:
    stats = make_tls_context(cafile).cert_store_stats()
    assert stats["x509"] == 1  # 운영체제 저장소의 인증서를 읽지 않았다
    assert stats["x509_ca"] == 1


def test_tls_context_is_new_every_time(cafile: str) -> None:
    assert make_tls_context(cafile) is not make_tls_context(cafile)


def test_tls_context_ignores_the_environment(cafile: str, monkeypatch: pytest.MonkeyPatch) -> None:
    other = Path(cafile).with_name("other.pem")
    other.write_text("not a certificate", encoding="ascii")
    monkeypatch.setenv("SSL_CERT_FILE", str(other))
    monkeypatch.setenv("SSL_CERT_DIR", str(other.parent))
    monkeypatch.setenv("SSLKEYLOGFILE", str(other.with_name("keys.log")))
    context = make_tls_context(cafile)
    assert context.cert_store_stats()["x509"] == 1
    assert context.keylog_filename is None


def test_tls_context_needs_an_existing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        make_tls_context(str(tmp_path / "missing.pem"))


def test_tls_context_refuses_an_empty_path() -> None:
    # 빈 글을 그대로 넘기면 운영체제 저장소를 읽는다.
    with pytest.raises(FileNotFoundError):
        make_tls_context("")


def test_tls_context_refuses_a_file_without_certificates(tmp_path: Path) -> None:
    path = tmp_path / "empty.pem"
    path.write_text("nothing here", encoding="ascii")
    with pytest.raises(ssl.SSLError):
        make_tls_context(str(path))


# --- 화면 글 상수(M1 명세 3.9, 설계서 8.4, 12) ----------------------------------------------


def test_stage_names_follow_the_spec() -> None:
    assert texts.STAGES == (
        "코인 목록 받기",
        "봉 받기",
        "봉 저장",
        "봉 통계 계산",
        "설정 저장",
        "알림 저장",
        "알림 지우기",
        "주소 저장",
        "주소 지우기",
        "켜기",
    )
    assert len(set(texts.STAGES)) == len(texts.STAGES)


def test_error_names_follow_the_spec() -> None:
    assert [
        texts.ERROR_NETWORK,
        texts.ERROR_TIMEOUT,
        texts.ERROR_RATE_LIMIT,
        texts.ERROR_BLOCKED,
        texts.ERROR_MAINTENANCE,
        texts.ERROR_SERVER,
        texts.ERROR_BAD_RESPONSE,
        texts.ERROR_NOT_ENOUGH_CANDLES,
        texts.ERROR_STORAGE,
    ] == [
        "네트워크 끊김",
        "시간 초과",
        "요청 한도 초과",
        "일시 차단",
        "거래소 점검",
        "서버 오류",
        "응답 형식 오류",
        "봉이 모자람",
        "DB 오류",
    ]
    assert texts.error_unknown_exchange(418) == "알 수 없는 거래소 오류(418)"
    assert [
        texts.ERROR_VAULT_UNAVAILABLE,
        texts.ERROR_VAULT,
        texts.ERROR_DISCORD_TIMEOUT,
        texts.ERROR_DISCORD_NETWORK,
        texts.ERROR_DISCORD_RATE_LIMIT,
        texts.ERROR_WEBHOOK_UNREADABLE,
    ] == [
        "보관소를 쓸 수 없음",
        "보관소 오류",
        "디스코드 시간 초과",
        "디스코드 네트워크 끊김",
        "디스코드 한도 초과",
        "주소를 읽지 못함",
    ]
    assert texts.error_discord_rejected(404) == "디스코드 거절(404)"


def test_alert_contents() -> None:
    from coin_report_trader.core import CandleUnit

    assert texts.content_candles_failed((CandleUnit.DAY,)) == "실패한 봉: 일봉"
    assert texts.content_candles_failed((CandleUnit.MINUTE_60,)) == "실패한 봉: 60분봉"
    both = (CandleUnit.DAY, CandleUnit.MINUTE_60)
    assert texts.content_candles_failed(both) == "실패한 봉: 일봉, 60분봉"
    assert texts.content_candles_failed(both[::-1]) == "실패한 봉: 60분봉, 일봉"
    assert (
        texts.CONTENT_NOT_ENOUGH_CANDLES
        == "완성된 봉이 모자라 다섯 기간 모두 통계를 셈하지 못했습니다."
    )
    assert texts.CONTENT_BLOCKED == "거래소가 공개 시세 요청을 잠시 막았습니다."
    assert (
        texts.CONTENT_BLOCKED_NOT_REQUESTED
        == "앞 코인에서 일시 차단을 받아 이 코인은 요청하지 않았습니다."
    )
    assert texts.CONTENT_MARKETS_FAILED == "코인 목록 요청이 실패했습니다."


def test_alert_codes_and_texts() -> None:
    assert [
        texts.CODE_CANDLES_FAILED,
        texts.CODE_SAVE_FAILED,
        texts.CODE_UNEXPECTED,
        texts.CODE_MARKETS_FAILED,
    ] == ["A-1", "A-29", "A-30", "A-31"]
    assert texts.text_candles_failed("일시 차단") == "봉 통계를 만들지 못했습니다(일시 차단)."
    assert texts.text_save_failed(texts.STAGE_SETTINGS_SAVE, "보관소 오류") == (
        "저장하지 못했습니다(보관소 오류)."
    )
    assert texts.text_save_failed(texts.STAGE_ALERT_DELETE, "x") == "지우지 못했습니다(x)."
    assert texts.text_save_failed(texts.STAGE_WEBHOOK_DELETE, "x") == "지우지 못했습니다(x)."
    assert texts.text_save_failed(texts.STAGE_WEBHOOK_SAVE, "x") == "저장하지 못했습니다(x)."
    assert texts.text_unexpected("ValueError") == "예상하지 못한 오류가 났습니다(ValueError)."
    assert texts.text_markets_failed("시간 초과") == "코인 목록을 받지 못했습니다(시간 초과)."


def test_after_texts() -> None:
    assert texts.after_candles_retry("10:30") == "다음 받는 때(10:30)에 다시 받습니다."
    assert texts.after_candles_blocked("10:30") == "10:30까지 봉 받기를 멈췄습니다."
    assert texts.AFTER_MARKETS_FAILED == "처음 설정이나 설정 화면에서 다시 시도할 수 있습니다."
    assert texts.AFTER_UNEXPECTED == "그 일을 그만두고 다음 일을 계속합니다."
    assert dict(texts.AFTER_SAVE_FAILED) == {
        "설정 저장": "저장하지 못한 값은 화면에 남겨 두었습니다.",
        "봉 저장": "다음 받는 때에 다시 받아 저장합니다.",
        "알림 저장": "이 알림은 이번 실행 동안 보이고, 다음에 켤 때 알림센터에 옮깁니다.",
        "알림 지우기": "지우지 못한 알림은 남겨 두었습니다.",
        "주소 저장": "주소는 저장되지 않았습니다. 다시 넣어 저장하세요.",
        "주소 지우기": "저장된 주소는 그대로 남아 있습니다.",
        "켜기": "창을 띄우지 못하고 끝냈습니다. 다음에 켤 때 다시 시도합니다.",
    }
    # A-29가 생기는 단계마다 "그 뒤에 한 일"이 있다. 봉 받기, 봉 통계 계산, 코인 목록 받기는
    # A-1, A-31의 것이라 여기에 없다.
    assert set(texts.AFTER_SAVE_FAILED) <= set(texts.STAGES)


def test_screen_texts() -> None:
    assert texts.WEBHOOK_BAD_FORMAT == "디스코드 웹후크 주소의 형식이 아닙니다."
    assert texts.WEBHOOK_SAVE_FAILED == "저장하지 못했습니다."
    assert texts.WEBHOOK_DELETE_FAILED == "지우지 못했습니다. 저장된 주소는 그대로 남아 있습니다."
    assert (
        texts.WEBHOOK_NOT_SET == "디스코드 주소를 넣지 않은 분류의 알림은 디스코드로 가지 않습니다."
    )
    assert texts.TEST_SENDING == "확인용 글을 보내는 중입니다."
    assert texts.TEST_SENT == "확인용 글을 보냈습니다. 디스코드 채널을 확인하세요."
    assert texts.TEST_FAILED == "확인용 글을 보내지 못했습니다."
    assert texts.SETTINGS_COIN_NOT_LISTED == (
        "코인 목록에 없는 코인이 있어 저장하지 않았습니다. 코인 목록을 다시 받은 뒤 저장하세요."
    )
    assert texts.CANDLES_LOADING == "봉을 받는 중입니다."
    assert texts.MARKETS_LOADING == "코인 목록을 불러오는 중입니다."
    assert texts.MARKETS_FAILED == (
        "코인 목록을 받지 못했습니다. 네트워크를 확인한 뒤 다시 시도하세요."
    )
    assert texts.SETTINGS_UNREADABLE == "설정을 읽지 못했습니다. 60분마다 다시 읽습니다."
    assert texts.SETTINGS_SAVED == "저장했습니다."
    assert texts.SETTINGS_SAVE_FAILED == "저장하지 못했습니다."
    assert texts.blocked_candles_text("10:30") == (
        "거래소가 잠시 요청을 막았습니다. 10:30 뒤에 다시 받습니다."
    )
    assert texts.blocked_markets_text("10:30") == (
        "거래소가 잠시 요청을 막았습니다. 10:30 뒤에 다시 시도할 수 있습니다."
    )


def test_kst_time_text() -> None:
    assert texts.kst_time_text(datetime(2026, 1, 1, 1, 30, tzinfo=UTC)) == "10:30"
    # 날짜를 넘겨도 한국 시간의 시와 분만 쓴다. 두 자리씩 채운다.
    assert texts.kst_time_text(datetime(2026, 1, 1, 15, 5, tzinfo=UTC)) == "00:05"
    # 다른 시간대의 값도 한국 시간으로 바꾼다.
    assert texts.kst_time_text(datetime(2026, 1, 1, 10, 30, tzinfo=KST)) == "10:30"
    with pytest.raises(ValueError, match="시간대"):
        texts.kst_time_text(datetime(2026, 1, 1, 1, 30))


def test_kst_datetime_text() -> None:
    assert texts.kst_datetime_text(datetime(2026, 1, 2, 1, 30, tzinfo=UTC)) == "2026-01-02 10:30"
    # 날짜는 한국 시간으로 바뀐다. 초는 넣지 않는다.
    assert (
        texts.kst_datetime_text(datetime(2026, 1, 1, 15, 5, 59, tzinfo=UTC)) == "2026-01-02 00:05"
    )
    assert texts.kst_datetime_text(datetime(2026, 3, 4, 10, 30, tzinfo=KST)) == "2026-03-04 10:30"
    with pytest.raises(ValueError, match="시간대"):
        texts.kst_datetime_text(datetime(2026, 1, 2, 1, 30))


def test_kst_when_text() -> None:
    now = datetime(2026, 1, 1, 14, 50, tzinfo=UTC)  # 한국 23:50
    # 한국 날짜가 같으면 시와 분, 다르면 날짜와 시각이다.
    assert texts.kst_when_text(datetime(2026, 1, 1, 14, 55, tzinfo=UTC), now) == "23:55"
    assert texts.kst_when_text(datetime(2026, 1, 1, 15, 20, tzinfo=UTC), now) == "2026-01-02 00:20"
    # 기준이 없으면 시와 분이다.
    assert texts.kst_when_text(datetime(2026, 1, 1, 15, 20, tzinfo=UTC), None) == "00:20"
    # 이미 지난 날(어제)도 날짜가 붙는다.
    assert (
        texts.kst_when_text(datetime(2025, 12, 31, 14, 55, tzinfo=UTC), now) == "2025-12-31 23:55"
    )
    with pytest.raises(ValueError, match="시간대"):
        texts.kst_when_text(datetime(2026, 1, 1, 15, 20), now)
    with pytest.raises(ValueError, match="시간대"):
        texts.kst_when_text(datetime(2026, 1, 1, 15, 20, tzinfo=UTC), datetime(2026, 1, 1))


def test_test_message_has_the_fixed_head_and_no_secrets() -> None:
    assert texts.TEST_MESSAGE_PREFIX == "[Coin Report Trader] 확인용 글"
    message = texts.test_message_text("오류 알림")
    assert message == "[Coin Report Trader] 확인용 글입니다. 이 채널로 오류 알림이 옵니다."
    assert message.startswith(texts.TEST_MESSAGE_PREFIX)
