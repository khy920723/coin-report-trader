"""앱의 TLS 설정 하나. 공개 시세, 디스코드, `--self-test`가 같은 함수를 쓴다."""

import ssl


def make_tls_context(cafile: str) -> ssl.SSLContext:
    """인증서 묶음 파일 하나만 믿는 클라이언트 TLS 설정. 연결하지 않고 파일만 읽는다.

    최소 TLS 1.2이고 호스트와 인증서를 확인한다. 운영체제 저장소와 `SSL_CERT_FILE`,
    `SSLKEYLOGFILE`는 보지 않는다(PC마다 같은 결과를 내고, 세션 키를 파일로 남기지 않는다).
    파일이 없으면 `FileNotFoundError`, 인증서가 없거나 깨졌으면 `ssl.SSLError`다(둘 다 `OSError`).
    """
    if not cafile:
        # 빈 글을 넘기면 `ssl.create_default_context`가 운영체제 저장소를 읽는다. 막는다.
        raise FileNotFoundError("인증서 묶음 파일 경로가 비어 있다")
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=cafile)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.keylog_filename = None
    return context
