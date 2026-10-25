"""만 14세 미만 아이 대화 상대의 법정대리인 확인 문자(2026-10-22 법률 자문 · SPEC §6.3).

SMS_PROVIDER(설정 sms_provider):
- off · 빈 값(기본, 대표 답 2026-10-23) → 공급자 없음 = 만 14세 미만 아이의 대화 상대를 만들 수 없다
- manual → 대표가 관리자 화면에 뜬 번호로 자기 휴대전화에서 직접 보낸다(새 수탁자 없음). send 는
  보내지 않는다 — 번호와 확인 번호를 대기열(persona_minor_checks)에 두는 일은 minors 가 한다
- log → 개발용(운영이 아닐 때만). 번호 뒤 4자리와 코드만 로그
- 그 밖 → 대표가 정한 문자 업체(정하면 그 업체 클래스를 만들고 처리방침 4번 위탁에 더하고 버전을
  올린다). 아직 없으니 경고만 남기고 공급자 없음으로 본다

문자 글에는 회사 이름(해외 회사)을 넣지 않는다(고객에게 보이는 글의 업체 이름 잠금 — 이름은
법정대리인이 체크하는 동의 항목과 guardian_consent 문서에 있다). 전체 번호는 저장하지 않고 가린
번호만 남긴다(manual 은 대표가 보낼 때까지만 전체 번호를 둔다).
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Protocol

from app.core.config import get_settings

logger = logging.getLogger(__name__)

OFF = ("", "off")
MANUAL = "manual"
LOG = "log"
# 확인 번호가 유효한 시간(문자 업체·log). manual 은 sms_manual_code_hours
CODE_MINUTES = 10
_PHONE = re.compile(r"^01[016789]\d{7,8}$")


@dataclass
class SmsResult:
    provider: str
    message_id: str | None


class SmsError(Exception):
    """문자를 보내지 못했다(번호·내용은 싣지 않는다)."""


class SmsProvider(Protocol):
    name: str

    async def send(self, to_digits: str, text: str) -> SmsResult: ...


class ManualSms:
    """대표가 손으로 보낸다. 여기서는 아무것도 보내지 않는다(대기열은 minors.request_code)."""

    name = MANUAL

    async def send(self, to_digits: str, text: str) -> SmsResult:
        return SmsResult(provider=self.name, message_id=None)


class LogSms:
    """개발용: 보내지 않고 로그에만 남긴다(번호 뒤 4자리와 확인 번호만)."""

    name = LOG

    async def send(self, to_digits: str, text: str) -> SmsResult:
        code = re.search(r"확인 번호 (\d{6})", text)
        logger.info("dev sms to ***%s code %s", to_digits[-4:], code.group(1) if code else "-")
        return SmsResult(provider=self.name, message_id=None)


def provider_name() -> str:
    return (get_settings().sms_provider or "").strip().lower()


def get_provider() -> SmsProvider | None:
    """설정된 공급자. off·빈 값이면 None. log 는 운영이 아닐 때만. 모르는 이름은 경고 + None."""
    name = provider_name()
    if name in OFF:
        return None
    if name == MANUAL:
        return ManualSms()
    if name == LOG:
        if get_settings().environment == "production":
            logger.warning("sms provider log is not allowed in production")
            return None
        return LogSms()
    logger.warning("sms provider %s is not available", name)
    return None


def under14_open() -> bool:
    """만 14세 미만 아이의 대화 상대를 만들 수 있나(확인 문자를 보낼 수 있나)."""
    return get_provider() is not None


# ────────────────────────── 번호·확인 번호·글 ──────────────────────────


def phone_digits(value) -> str | None:
    """휴대전화 번호의 숫자만. 01X 로 시작하는 10~11자리가 아니면 None."""
    digits = re.sub(r"\D", "", value) if isinstance(value, str) else ""
    return digits if _PHONE.match(digits) else None


def mask_phone(digits: str) -> str:
    """010-****-1234(가운데는 가린다). 저장은 이것만 한다."""
    return f"{digits[:3]}-****-{digits[-4:]}"


def code_hash(code: str, persona_id: str) -> str:
    """확인 번호의 해시. 번호 자체는 두지 않는다(manual 은 대표가 보낼 때까지만)."""
    raw = f"{code}{persona_id}{get_settings().jwt_secret}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def valid_for(manual: bool) -> str:
    """문자에 적는 유효 시간."""
    return f"{get_settings().sms_manual_code_hours}시간" if manual else f"{CODE_MINUTES}분"


def message(code: str, valid: str) -> str:
    """법정대리인 휴대전화로 보내는 확인 문자(최종 글, LMS 허용). 회사 이름은 넣지 않는다."""
    return (
        "[TalkTo] 아이의 AI 대화 상대를 만드는 데 법정대리인으로 동의하시는지 확인하려고 보내 "
        "드려요. 아이 목소리와 이야기는 미국에 있는 회사로 보내 처리해요(자세한 내용은 TalkTo "
        "화면의 '법정대리인 확인과 동의'에 있어요). 동의하시면 TalkTo 화면에 확인 번호 "
        f"{code}를 넣어 주세요({valid} 안). 요청하지 않으셨으면 이 문자를 지워 주세요."
    )
