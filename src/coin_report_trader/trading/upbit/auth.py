"""업비트 비공개 API의 인증(M5 명세 3.5.1). 표준 라이브러리만 쓴다(3.2).

- 요청마다 JWT를 만든다. 서명은 Secret Key의 HMAC-SHA512(`alg` "HS512". 공식 문서가 권장하는 지금의
  알고리즘)이다.
- 몸통(payload): `access_key`, 요청마다 새 `nonce`(`uuid4`의 글). 쿼리나 몸통이 있으면 `query_hash`
  (SHA512 16진 글)와 `query_hash_alg`("SHA512")를 더한다.
- `query_text`가 보낼 쿼리 글을 한 번 만든다. 인코딩하지 않은 `key=value&…`이고 배열은
  `key[]=a&key[]=b`다. 보내는 글과 해시에 넣는 글은 이 함수가 만든 같은 글이다. 그래서 값에는
  인코딩이 필요 없는 글자(영문, 숫자, `-`, `_`, `.`, `~`)만 받는다(아니면 보내기 전에 `ValueError`).
  POST의 몸통도 같은 필드를 같은 순서로 이 함수에 넣어 해시한다.
- 키는 `UpbitKeys`에만 둔다. `repr`, `str`은 값을 가린다. 토큰과 키는 오류 글에 넣지 않는다.
"""

import base64
import hashlib
import hmac
import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

QUERY_HASH_ALG = "SHA512"
JWT_ALG = "HS512"

_SAFE = re.compile(r"[A-Za-z0-9._~-]+")
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[\])?")

type Params = Sequence[tuple[str, str]]


@dataclass(frozen=True)
class UpbitKeys:
    """업비트 키(Access Key, Secret Key). `repr`와 `str`에는 값이 들지 않는다."""

    access: str = field(repr=False)
    secret: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.access, str) or not isinstance(self.secret, str):
            raise TypeError("업비트 키는 글이다")
        if not self.access or not self.secret:
            raise ValueError("업비트 키가 비어 있다")

    def __repr__(self) -> str:
        return "UpbitKeys(access=(가려서 보임), secret=(가려서 보임))"

    __str__ = __repr__

    def __reduce__(self) -> object:
        # 피클로 키가 새어 나가지 않게 막는다(로그나 예외에 실려 직렬화되는 일을 막는다).
        raise TypeError("UpbitKeys는 직렬화하지 않는다")


def query_text(params: Params) -> str:
    """보낼 쿼리 글이자 해시에 넣을 글. 인코딩하지 않는다(모듈 설명)."""
    parts = []
    for key, value in params:
        if not isinstance(key, str) or _KEY.fullmatch(key) is None:
            raise ValueError(f"쿼리의 키가 꼴이 아니다: {key!r}")
        if not isinstance(value, str) or _SAFE.fullmatch(value) is None:
            # 값은 문구에 넣지 않는다(키 이름만).
            raise ValueError(f"쿼리 값에 인코딩이 필요한 글자가 있다: 키 {key!r}")
        parts.append(f"{key}={value}")
    return "&".join(parts)


def query_hash(text: str) -> str:
    """UTF-8 글의 SHA512 소문자 16진 글."""
    return hashlib.sha512(text.encode("utf-8")).hexdigest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_token(keys: UpbitKeys, hashed: str, *, nonce: str | None = None) -> str:
    """JWT 하나. `hashed`가 비어 있으면 `query_hash`를 넣지 않는다.

    `nonce`는 기본으로 새 `uuid4`다.
    """
    payload: dict[str, str] = {
        "access_key": keys.access,
        "nonce": nonce if nonce is not None else str(uuid.uuid4()),
    }
    if hashed:
        payload["query_hash"] = query_hash(hashed)
        payload["query_hash_alg"] = QUERY_HASH_ALG
    header = {"alg": JWT_ALG, "typ": "JWT"}
    signing_input = (
        _b64(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    )
    signature = hmac.new(
        keys.secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha512
    ).digest()
    return signing_input + "." + _b64(signature)
