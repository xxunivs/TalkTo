"""관리자 — 결제·환불 판단(WO 1-7 · SPEC §3.4).

  GET  /admin/payments?userId=&personaId=&q=&page=&size=
       결제 목록(새로 낸 순). 결제 후 첫 대화(초대한 가족의 대화도)·체험 사용·환불 짐작(refundHint)
  POST /admin/payments/{id}/refund
       손으로 환불한 뒤 기록(토스는 부르지 않는다 — 대표가 토스 상점관리자에서 취소한다). 한 번만.

성함·전체 이메일은 싣지 않는다(대화 상대는 호칭, 계정은 가린 이메일).
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import Field
from sqlalchemy import func, select, update

from app.api.deps import DB, AdminUser
from app.db.models import Payment
from app.schemas.common import APIModel, Envelope, Meta
from app.services import billing

router = APIRouter(prefix="/admin", tags=["Admin"])

ALREADY = "이미 환불을 적은 결제예요."


class RefundRecord(APIModel):
    amount: int = Field(gt=0)
    reason: Literal["no_chat_7d", "no_trial_7d", "mismatch", "prorated", "other"]
    note: str | None = Field(default=None, max_length=200)


def _like(term: str) -> str:
    """LIKE 에 넣을 '%…%'. 친 %·_ 는 글자 그대로 찾는다."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@router.get("/payments", response_model=Envelope[list[dict]])
async def list_payments(
    _: AdminUser,
    db: DB,
    user_id: str | None = Query(None, alias="userId", max_length=36),
    persona_id: str | None = Query(None, alias="personaId", max_length=36),
    q: str | None = Query(None, max_length=64),
    page: int = Query(1, ge=1),
    size: int = Query(25, ge=1, le=100),
):
    """결제 목록. q = 주문 번호(일부만 쳐도 된다). meta.total 은 거른 뒤 전체 수."""
    query = select(Payment)
    if user_id:
        query = query.where(Payment.user_id == user_id)
    if persona_id:
        query = query.where(Payment.persona_id == persona_id)
    term = (q or "").strip()
    if term:
        query = query.where(Payment.order_id.like(_like(term), escape="\\"))
    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(
        (
            await db.scalars(
                query.order_by(Payment.created_at.desc(), Payment.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        ).all()
    )
    return Envelope(
        data=await billing.admin_rows(db, rows),
        meta=Meta(page=page, limit=size, total=total),
    )


@router.post("/payments/{payment_id}/refund", response_model=Envelope[dict])
async def record_refund(payment_id: str, payload: RefundRecord, _: AdminUser, db: DB):
    """토스 상점관리자에서 돌려준 뒤 적는다(금액·까닭·메모). 이미 적은 결제는 409."""
    pay = await db.get(Payment, payment_id)
    if pay is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "결제를 찾지 못했어요.")
    if pay.status != "DONE":
        raise HTTPException(status.HTTP_409_CONFLICT, "결제가 된 기록만 환불을 적을 수 있어요.")
    if pay.refunded_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, ALREADY)
    if payload.amount > pay.amount:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "결제한 금액보다 많이 적을 수 없어요.")
    note = (payload.note or "").strip()
    # 두 번 눌러도(두 창에서 동시에) 한 번만 적힌다
    done = await db.execute(
        update(Payment)
        .where(Payment.id == pay.id, Payment.refunded_at.is_(None))
        .values(
            refunded_at=billing.now(),
            refund_amount=payload.amount,
            refund_note=f"{payload.reason}: {note}" if note else payload.reason,
        )
    )
    await db.commit()
    if done.rowcount != 1:
        raise HTTPException(status.HTTP_409_CONFLICT, ALREADY)
    await db.refresh(pay)
    return Envelope(data=(await billing.admin_rows(db, [pay]))[0])
