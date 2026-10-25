"""고객센터(카카오톡 채널·이메일)와 문의 코드.

문의는 앱 안 게시판 없이 카카오톡 채널(KAKAO_CHANNEL_CHAT_URL)과 이메일(BUSINESS_EMAIL)로 받는다.
채널 주소는 pf.kakao.com 의 채널 주소만 내보낸다(channel_url — http 는 https 로 바꾼다).

문의 코드는 회원을 알아보는 짧은 글자(8자, 'K7M3-Q9XA')다. 카카오톡 채널 채팅 첫 줄에 붙여 받는다.
- 회원 id 의 sha256 에서 만든다(칸을 따로 두지 않는다). 코드로 id 를 거꾸로 알 수 없고, 코드만으로는
  아무것도 할 수 없다(관리자가 누구의 문의인지 찾는 데만 쓴다).
- 헷갈리는 글자(0과 O, 1과 I·L, U)는 쓰지 않는다(Crockford base32). 관리자 찾기는 소문자·O·I·L 도
  받아 고쳐 읽는다.
- 화면은 그 뒤에 대화 상대 짧은 코드(대화 상대 id 앞 6자)와 앱 버전을 붙인다
  ('문의 코드 K7M3-Q9XA · 대화 상대 3F2A9C · 앱 20261021093000'). 관리자 찾기에는 이 줄을 통째로
  붙여 넣어도 된다(parse).
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import User

# Crockford base32(0-9, A-Z 에서 I·L·O·U 를 뺀 32자)
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 8
PERSONA_CODE_LENGTH = 6
_SALT = "talkto-inquiry:"
# 사람이 잘못 옮겨 적기 쉬운 글자 → 코드 글자
_FIX = str.maketrans({"O": "0", "I": "1", "L": "1"})
_CODE = re.compile(r"(?<![0-9A-Za-z])([0-9A-Za-z]{4})[-\s]?([0-9A-Za-z]{4})(?![0-9A-Za-z])")
_PERSONA = re.compile(r"대화\s*상대\s*[:#]?\s*([0-9A-Fa-f]{6})(?![0-9A-Za-z])")
# 카카오톡 채널 1:1 채팅 주소: https://pf.kakao.com/_채널id/chat (채널 홈 /_채널id 도 받는다)
KAKAO_CHANNEL_HOST = "pf.kakao.com"
_CHANNEL_PATH = re.compile(r"/_[A-Za-z0-9]+(?:/chat)?/?")


def inquiry_code(user_id: str) -> str:
    """회원 문의 코드('K7M3-Q9XA'). 같은 회원은 늘 같은 코드다."""
    digest = hashlib.sha256(f"{_SALT}{user_id}".encode()).digest()
    number = int.from_bytes(digest[:5], "big")  # 40비트 = 8글자
    chars = [ALPHABET[(number >> (5 * i)) & 31] for i in reversed(range(CODE_LENGTH))]
    return f"{''.join(chars[:4])}-{''.join(chars[4:])}"


def persona_code(persona_id: str) -> str:
    """대화 상대 짧은 코드(id 앞 6자, 대문자). 화면이 문의 코드 뒤에 붙이는 것과 같다."""
    return persona_id.replace("-", "")[:PERSONA_CODE_LENGTH].upper()


def normalize(code: str) -> str | None:
    """사람이 옮겨 적은 문의 코드를 'XXXX-XXXX' 로. 코드 모양이 아니면 None."""
    plain = re.sub(r"[\s-]", "", code or "").upper().translate(_FIX)
    if len(plain) != CODE_LENGTH or any(c not in ALPHABET for c in plain):
        return None
    return f"{plain[:4]}-{plain[4:]}"


def parse(text: str) -> tuple[str | None, str | None]:
    """붙여 넣은 줄에서 (문의 코드, 대화 상대 코드)를 찾는다. 없으면 None."""
    text = text or ""
    persona = _PERSONA.search(text)
    rest = text[: persona.start()] + " " + text[persona.end() :] if persona else text
    code = None
    for match in _CODE.finditer(rest):
        code = normalize(match.group(1) + match.group(2))
        if code:
            break
    return code, persona.group(1).upper() if persona else None


def channel_url(raw: str | None) -> str | None:
    """설정에 적은 카카오톡 채널 주소를 화면이 열 주소로. 카카오톡 채널(pf.kakao.com/_채널
    또는 /_채널/chat)이 아니면 None(화면이 단추를 숨긴다).

    화면은 이 주소를 그대로 연다(카카오톡 인앱이면 그 창에서). 그래서 다른 곳으로 보내는 주소·
    javascript: 같은 것은 내보내지 않는다. 대표님이 주신 http 주소(http://pf.kakao.com/…)는
    카카오도 https 로 넘기니 https 로 바꿔 내보낸다.
    """
    try:
        parsed = urlparse((raw or "").strip())
    except ValueError:  # 예: 닫히지 않은 '[' (고객센터 화면이 깨지지 않게)
        return None
    if parsed.scheme not in ("http", "https") or parsed.hostname != KAKAO_CHANNEL_HOST:
        return None
    # 사용자 이름·비밀번호·포트·물음표·# 이 붙은 주소는 받지 않는다(채널 주소에는 없다)
    if parsed.netloc.lower() != KAKAO_CHANNEL_HOST:
        return None
    if parsed.params or parsed.query or parsed.fragment:
        return None
    if not _CHANNEL_PATH.fullmatch(parsed.path):
        return None
    return parsed._replace(scheme="https", netloc=KAKAO_CHANNEL_HOST).geturl()


def kakao_channel_url() -> str | None:
    """카카오톡 채널 채팅 주소(설정 KAKAO_CHANNEL_CHAT_URL, channel_url 로 거른 것)."""
    return channel_url(get_settings().kakao_channel_chat_url)


def info(user: User) -> dict:
    """GET /support 의 내용: {kakaoChannelUrl|null, hours, email|null, inquiryCode}."""
    s = get_settings()
    email = (s.business_email or "").strip() or None
    hours = (s.support_hours or "").strip() or None
    return {
        "kakaoChannelUrl": kakao_channel_url(),
        "hours": hours,
        "email": email,
        "inquiryCode": inquiry_code(user.id),
    }


async def find_users(db: AsyncSession, code: str) -> list[User]:
    """문의 코드가 맞는 회원(가입한 순). 코드는 id 에서 만들어 회원 id 를 하나씩 맞춰 본다."""
    ids = (await db.scalars(select(User.id))).all()
    matched = [user_id for user_id in ids if inquiry_code(user_id) == code]
    if not matched:
        return []
    return list(
        (await db.scalars(select(User).where(User.id.in_(matched)).order_by(User.created_at))).all()
    )
