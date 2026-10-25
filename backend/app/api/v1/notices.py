"""앱 안 알림(app/services/notices.py). 로그인하고 계정 필수 동의를 마친 사용자만.

  GET  /notices?unread=0|1     내 알림 최신 50개(unread=1 이면 안 읽은 것만)
                               + meta.unread(안 읽은 수)
  POST /notices/{id}/read      하나 읽음
  POST /notices/read-all       모두 읽음

다른 사람의 알림은 404 다(알림을 찾지 못했어요).
"""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import Field
from sqlalchemy import func, select, update

from app.api.deps import DB, CurrentUser
from app.db.models import OwnerNotice
from app.schemas.common import Envelope, Meta
from app.services import notices

router = APIRouter(prefix="/notices", tags=["Notices"])


class NoticeMeta(Meta):
    # 안 읽은 알림 수(앱 셸 종 아이콘)
    unread: int = 0


class NoticeList(Envelope[list[dict]]):
    meta: NoticeMeta = Field(default_factory=NoticeMeta)


@router.get("", response_model=NoticeList)
async def list_notices(user: CurrentUser, db: DB, unread: int = Query(0, ge=0, le=1)):
    query = select(OwnerNotice).where(OwnerNotice.user_id == user.id)
    if unread:
        query = query.where(OwnerNotice.read_at.is_(None))
    rows = (
        await db.scalars(
            query.order_by(OwnerNotice.created_at.desc(), OwnerNotice.id.desc()).limit(
                notices.LIST_LIMIT
            )
        )
    ).all()
    count = await db.scalar(
        select(func.count())
        .select_from(OwnerNotice)
        .where(OwnerNotice.user_id == user.id, OwnerNotice.read_at.is_(None))
    )
    return NoticeList(
        data=[notices.view(n) for n in rows],
        meta=NoticeMeta(unread=int(count or 0), limit=notices.LIST_LIMIT),
    )


@router.post("/read-all", response_model=Envelope[dict])
async def read_all(user: CurrentUser, db: DB):
    await db.execute(
        update(OwnerNotice)
        .where(OwnerNotice.user_id == user.id, OwnerNotice.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    await db.commit()
    return Envelope(data={"ok": True})


@router.post("/{notice_id}/read", response_model=Envelope[dict])
async def read_one(notice_id: str, user: CurrentUser, db: DB):
    notice = await db.get(OwnerNotice, notice_id)
    if notice is None or notice.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "알림을 찾지 못했어요.")
    if notice.read_at is None:
        notice.read_at = datetime.now(UTC)
        await db.commit()
    return Envelope(data={"ok": True})
