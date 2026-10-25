"""메일(AWS SES v2). 운영 알림(send_ops)과 고객 메일(send_user) 두 가지.

운영 알림: '새 기억 인터뷰 신청'처럼 운영 메일함으로 알린다. **개인 정보는 싣지 않는다**: 이름·
전화번호·대화 상대 정보 없이 '무슨 일이 몇 건' 정도만 보낸다. 자세한 내용은 관리자 화면에서 본다
(디스코드 알림 notify.py 와 같은 원칙). 운영(ENVIRONMENT=production)이고 OPS_EMAIL_TO·MAIL_FROM
이 둘 다 있을 때만 보낸다.

고객 메일(2026-10-22 법률 자문): 앱 안 알림(notices.py)과 같은 글을 그 계정 주소로 보낸다(계약 내용·
동의 녹음 결과·탈퇴 안내). 운영이고 MAIL_FROM 이 있고 USER_MAIL_ENABLED(SES 운영 권한을 받은 뒤
대표가 켬)일 때만 보낸다. 이메일 없이 가입한 소셜 계정(빈 자리 주소)에는 보내지 않는다.

보내는 주소는 SES 에서 확인한 도메인(noreply@<도메인>, deploy/aws/stack.yaml 의 EmailIdentity)이어야
한다. EC2 역할(ses:SendEmail)로 인증한다. 보내기는 요청과 따로 돌고(스레드), 설정이 없거나 실패해도
기록만 남기고 요청은 그대로 끝난다. 받는 주소·본문은 로그에 남기지 않는다.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings
from app.db.models import SOCIAL_PLACEHOLDER_EMAIL_SUFFIX

logger = logging.getLogger(__name__)

_tasks: set[asyncio.Task] = set()


def configured() -> bool:
    s = get_settings()
    return (
        s.environment == "production"
        and bool((s.ops_email_to or "").strip())
        and bool((s.mail_from or "").strip())
    )


def _client():
    """SES v2 클라이언트(시험에서 바꿔 끼운다)."""
    import boto3

    return boto3.client("sesv2", region_name=get_settings().mail_region)


def user_mail_configured() -> bool:
    """고객 메일을 보내도 되나. SES 샌드박스에서는 확인하지 않은 주소로 못 보내므로 대표가 켠다."""
    s = get_settings()
    return (
        s.environment == "production"
        and bool((s.mail_from or "").strip())
        and bool(s.user_mail_enabled)
    )


def _send_sync(subject: str, text: str, recipients: list[str] | None = None) -> None:
    s = get_settings()
    if recipients is None:
        recipients = [a.strip() for a in (s.ops_email_to or "").split(",") if a.strip()]
    _client().send_email(
        FromEmailAddress=(s.mail_from or "").strip(),
        Destination={"ToAddresses": recipients},
        Content={
            "Simple": {
                "Subject": {"Data": subject[:200], "Charset": "UTF-8"},
                "Body": {"Text": {"Data": text[:5000], "Charset": "UTF-8"}},
            }
        },
    )


async def _send(subject: str, text: str, recipients: list[str] | None = None) -> None:
    try:
        await asyncio.to_thread(_send_sync, subject, text, recipients)
    except Exception as error:  # noqa: BLE001 — 메일 때문에 서비스가 깨지면 안 된다
        # 받는 주소·본문은 남기지 않는다(종류만)
        kind = "ops" if recipients is None else "user"
        logger.warning("%s mail failed: %s", kind, type(error).__name__)


def _start(subject: str, text: str, recipients: list[str] | None = None) -> bool:
    try:
        task = asyncio.get_running_loop().create_task(_send(subject, text, recipients))
    except RuntimeError:  # 돌고 있는 이벤트 루프가 없다(시험 스크립트 등)
        return False
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


def send_ops(subject: str, text: str) -> bool:
    """운영 메일함으로 한 통(기다리지 않는다). 보내기를 걸었으면 True, 설정이 없으면 False."""
    if not configured():
        logger.info("ops mail skipped (OPS_EMAIL_TO/MAIL_FROM not set or not production)")
        return False
    return _start(subject, text)


def send_user(to: str | None, subject: str, text: str) -> bool:
    """고객 한 분께 한 통(기다리지 않는다). 보내기를 걸었으면 True.

    설정이 없거나(user_mail_configured), 주소가 비었거나, 이메일 없이 가입한 소셜 계정의 빈 자리
    주소(@social.talkto.invalid)면 보내지 않고 False. 부르는 쪽은 앱 안 알림을 따로 남긴다.
    """
    address = (to or "").strip()
    if not user_mail_configured():
        logger.info("user mail skipped (USER_MAIL_ENABLED/MAIL_FROM not set or not production)")
        return False
    if (
        not address
        or "@" not in address
        or address.lower().endswith(SOCIAL_PLACEHOLDER_EMAIL_SUFFIX)
        # 탈퇴한 계정의 바꾼 주소(@deleted.talkto.invalid) 같은 .invalid 는 실제로 없는 도메인
        or address.lower().endswith(".invalid")
    ):
        return False
    return _start(subject, text, [address])


async def wait_all() -> None:
    """시험·종료 때 보내는 중인 메일을 기다린다."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)
