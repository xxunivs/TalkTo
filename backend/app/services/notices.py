"""앱 안 알림(owner_notices, 2026-10-22 법률 자문). 푸시·문자는 보내지 않는다.

- 글은 TalkTo 가 말하는 문장이다. 대화 상대가 말하거나 보낸 것처럼 쓰지 않는다("할머니가
  기다려요" 금지). 대화 상대는 persona_label("'할머니' 대화 상대")로만 가리킨다. 늘 '상대'로
  끝나므로 조사는 목적격 '를', 소유격 '의'로 고정한다.
- mail=True 면 같은 글을 그 계정 주소로도 보낸다(mailer.send_user — 설정이 켜져 있고 주소가 있을
  때만). 메일이 안 되면 앱 알림만 남는다(카카오 가입자처럼 주소가 없는 분).
- 같은 일을 두 번 알리지 않게 dedupe_key 를 쓴다(겹치면 만들지 않는다).
- 읽고 180일 지나면 하루 정리(cleanup)가 지운다. 단 계약 내용(kind=contract, 전자상거래법 제13조
  제2항)은 읽어도 두고 탈퇴 때 지운다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import OwnerNotice, Persona, User
from app.services import mailer

READ_RETENTION = timedelta(days=180)
# 읽어도 하루 정리에서 지우지 않는 종류(탈퇴 때 지운다)
KEEP_KINDS = ("contract",)
LIST_LIMIT = 50


def persona_label(persona: Persona | None) -> str:
    """알림에서 대화 상대를 가리키는 말: '{호칭}' 대화 상대(호칭이 없으면 이름)."""
    basics = (persona.basics if persona is not None else None) or {}
    name = (basics.get("addressTerm") or "").strip()
    if not name and persona is not None:
        name = (persona.name or "").strip()
    return f"'{name or '그분'}' 대화 상대"


def _mail_text(body: str, action: dict | None) -> str:
    route = (action or {}).get("route") or ""
    # 웹은 #/… 경로를 쓴다(billing.app_url 과 같은 꼴)
    base = get_settings().frontend_url.rstrip("/")
    return f"{body}\n\n앱에서 확인해 주세요: {base}/#{route}"


async def create(
    db: AsyncSession,
    user_id: str,
    kind: str,
    title: str,
    body: str,
    *,
    persona_id: str | None = None,
    action: dict | None = None,
    dedupe_key: str | None = None,
    mail: bool = False,
) -> OwnerNotice | None:
    """알림 하나를 더한다. 같은 dedupe_key 가 이미 있으면 만들지 않고 None. commit 은 부르는 쪽.

    mail=True 면 같은 글을 메일로도 보내고(보냈으면 mailed_at), 못 보내도 알림은 남긴다.
    """
    if dedupe_key is not None:
        exists = await db.scalar(
            select(OwnerNotice.id).where(
                OwnerNotice.user_id == user_id, OwnerNotice.dedupe_key == dedupe_key
            )
        )
        if exists is not None:
            return None
    notice = OwnerNotice(
        user_id=user_id,
        persona_id=persona_id,
        kind=kind,
        title=title[:120],
        body=body,
        action=action,
        dedupe_key=dedupe_key,
        created_at=datetime.now(UTC),
    )
    if dedupe_key is not None and db.get_bind().dialect.name == "postgresql":
        # 운영: 동시에 같은 알림을 만들면 하나만 남긴다(나머지는 겹친 것으로 본다). SQLite(개발·
        # 시험)는 드라이버가 SAVEPOINT 를 바르게 다루지 않아 위의 확인만 한다
        try:
            async with db.begin_nested():
                db.add(notice)
        except IntegrityError:
            return None
    else:
        db.add(notice)
    if mail:
        user = await db.get(User, user_id)
        if user is not None and mailer.send_user(
            user.email, "[TalkTo] " + title, _mail_text(body, action)
        ):
            notice.mailed_at = datetime.now(UTC)
    return notice


def view(notice: OwnerNotice) -> dict:
    return {
        "id": notice.id,
        "kind": notice.kind,
        "title": notice.title,
        "body": notice.body,
        "action": notice.action,
        "personaId": notice.persona_id,
        "createdAt": notice.created_at,
        "readAt": notice.read_at,
    }


async def cleanup(db: AsyncSession, now: datetime) -> int:
    """읽고 180일 지난 알림을 지운다(계약 내용은 남긴다). 지운 수. commit 까지 한다."""
    result = await db.execute(
        delete(OwnerNotice).where(
            OwnerNotice.read_at.is_not(None),
            OwnerNotice.read_at < now - READ_RETENTION,
            OwnerNotice.kind.not_in(KEEP_KINDS),
        )
    )
    await db.commit()
    return int(result.rowcount or 0)
