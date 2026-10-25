"""사용 통계 모으기(개발자 화면 /admin/stats 에서 본다).

이벤트 이름과 작은 값(props)만 남긴다. 대화 내용·녹음·질문 답·이름·이메일은 절대 넣지 않는다.
props 는 글자(80자까지)·숫자·참거짓·None 만 남기고 나머지는 버린다.

track 은 요청을 깨지 않는다. 실패하면 로그만 남긴다. 부르는 쪽은 자기 일을 commit 한 **뒤에**
부른다(실패하면 이 세션을 되돌리는데, 그때 아직 commit 하지 않은 일이 같이 사라지지 않게).
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AnalyticsEvent, Person, Persona
from app.db.session import SessionLocal
from app.services import notify

logger = logging.getLogger(__name__)

RETENTION = timedelta(days=365)
MAX_TEXT = 80
MAX_PROPS = 12
_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")

# 서버가 남기는 이벤트(이름 고정)
SERVER_EVENTS = frozenset(
    {
        "signup",
        "login",
        "consent_accepted",
        "account_deleted",
        "persona_created",
        "recordings_uploaded",
        "speakers_chosen",
        "build_started",
        "build_done",
        "build_failed",
        "voice_created",
        "message_sent",
        "reply_played",
        "more_answers_saved",
        "material_added",
        "subscription_requested",
        "subscription_request_canceled",
        "checkout_started",
        "payment_succeeded",
        # 관리자가 손으로 만든 페르소나를 가져옴/되돌림(persona_import, props 에 관리자 id)
        "persona_imported",
        "persona_import_undone",
        # 가족 공유(app/services/sharing.py). 수와 참거짓만 — 이름·관계·부르는 말은 넣지 않는다
        "share_link_created",
        "share_joined",
        "share_profile_updated",
        "share_ended",
        # 기억 인터뷰 신청·취소(app/services/interview.py). 방법(phone/video)만, 연락처는 빼고
        "interview_requested",
        "interview_request_canceled",
    }
)

# 화면이 보낼 수 있는 이벤트와 받는 값 이름. 나머지 이름은 무시한다.
CLIENT_EVENTS: dict[str, frozenset[str]] = {
    "screen_view": frozenset({"screen"}),
    "more_tile_clicked": frozenset(),
    "plans_viewed": frozenset({"withPersona"}),
    "legal_doc_opened": frozenset({"doc"}),
    # 가족 공유: 링크 보내기(share = 휴대폰 공유 창, copy = 복사)·초대 받은 분이 물러남
    "share_link_sent": frozenset({"method"}),
    "join_declined": frozenset({"reason"}),
}


def now() -> datetime:
    return datetime.now(UTC)


def sanitize(props: dict[str, Any] | None, allowed: frozenset[str] | None = None) -> dict:
    """작은 값만 남긴다: 글자(80자까지)·정수·실수·참거짓·None. 키도 짧은 영문 이름만."""
    out: dict[str, Any] = {}
    for key, value in (props or {}).items():
        if len(out) >= MAX_PROPS:
            break
        if not isinstance(key, str) or not _KEY.match(key):
            continue
        if allowed is not None and key not in allowed:
            continue
        if value is None or isinstance(value, bool):
            out[key] = value
        elif isinstance(value, int):
            out[key] = value
        elif isinstance(value, float):
            if value == value and value not in (float("inf"), float("-inf")):
                out[key] = round(value, 3)
        elif isinstance(value, str):
            out[key] = value[:MAX_TEXT]
        # 목록·사전·그 밖의 값은 버린다
    return out


async def track(
    db: AsyncSession,
    name: str,
    *,
    user_id: str | None = None,
    persona_id: str | None = None,
    commit: bool = True,
    notify_email: str | None = None,
    **props: Any,
) -> None:
    """이벤트 하나를 남긴다. 무슨 일이 있어도 예외를 밖으로 내지 않는다.

    props 이름은 짧은 영문(예: plan, count, seconds)이고 값은 작은 값만 남는다.
    notify_email 은 디스코드 알림에만 가려서 싣는다(notify.masked_email). props 가 아니라서
    이벤트 줄(analytics_events)에는 남지 않는다.

    commit=True(기본)는 따로 연 세션에 바로 남긴다. 실패해도 부르는 쪽 세션은 그대로라
    (되돌리지 않는다) 요청이 깨지지 않는다. 부르는 쪽은 자기 쓰기를 commit 한 뒤에 부른다.
    commit=False 면 db 에 더하기만 하고 부르는 쪽의 commit 에 함께 실린다.
    """
    try:
        event = AnalyticsEvent(
            user_id=user_id,
            persona_id=persona_id,
            name=str(name)[:50],
            props=sanitize(props),
            created_at=now(),
        )
        # 새 가입·구독 신청 같은 일은 디스코드에도 알린다(개인 정보 없이, 이메일은 가려서)
        notify.event(event.name, event.props, notify_email)
        if not commit:
            db.add(event)
            return
        async with SessionLocal() as own:
            own.add(event)
            await own.commit()
    except Exception as error:  # noqa: BLE001 — 통계 때문에 요청이 깨지면 안 된다
        logger.warning("analytics track failed (%s): %s", name, error)


async def owner_of(db: AsyncSession, persona_id: str) -> str | None:
    """대화 상대 주인(사용자 id). 뒤에서 도는 작업이 이벤트를 남길 때 쓴다."""
    try:
        return await db.scalar(
            select(Person.owner_user_id)
            .join(Persona, Persona.person_id == Person.id)
            .where(Persona.id == persona_id)
        )
    except Exception as error:  # noqa: BLE001
        logger.warning("analytics owner lookup failed: %s", error)
        return None


async def track_persona(
    db: AsyncSession, name: str, persona_id: str, *, user_id: str | None = None, **props: Any
) -> None:
    """대화 상대 이벤트(주인을 몰라도 된다 — 찾아서 남긴다)."""
    if user_id is None:
        user_id = await owner_of(db, persona_id)
    await track(db, name, user_id=user_id, persona_id=persona_id, **props)


async def forget_user(db: AsyncSession, user_id: str, persona_ids: list[str] = ()) -> None:
    """회원 탈퇴: 그 사람의 이벤트에서 사용자·대화 상대 칸을 비운다(부르는 쪽이 commit 한다).

    대화 상대 번호가 남으면 탈퇴 뒤에도 남는 대화 횟수·결제 기록으로 계정과 다시 이어지므로
    그 사람이 갖고 있던 대화 상대의 번호도 지운다.
    """
    await db.execute(
        update(AnalyticsEvent)
        .where(AnalyticsEvent.user_id == user_id)
        .values(user_id=None, persona_id=None)
    )
    if persona_ids:
        await db.execute(
            update(AnalyticsEvent)
            .where(AnalyticsEvent.persona_id.in_(list(persona_ids)))
            .values(persona_id=None)
        )


async def cleanup(db: AsyncSession, at: datetime | None = None) -> int:
    """1년 지난 이벤트를 지운다. 지운 수를 돌려준다."""
    cutoff = (at or now()) - RETENTION
    result = await db.execute(delete(AnalyticsEvent).where(AnalyticsEvent.created_at < cutoff))
    await db.commit()
    return int(result.rowcount or 0)
