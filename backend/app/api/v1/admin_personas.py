"""관리자 — 대화 상대의 법률 처리(2026-10-22 법률 자문 · SPEC §4 · §6.3 · §7.4 · §7.5).

  GET  /admin/personas/unable                   대화가 어려우신 분·부모 아닌 분이 만든 아이·
                                                살아 계심 → 돌아가심으로 바꾼 것(보고만)
  GET  /admin/holds                             멈춘 대화 상대(지우는 날 순)
  GET  /admin/guardian-sms                      확인 문자 대기열(manual 공급자 — 전체 번호는 여기만)
  POST /admin/guardian-sms/{personaId}/sent     대표가 자기 휴대전화로 보냈어요
  GET  /admin/personas/{id}/legal               유족 명단·미성년·멈춤·이의·직접 알림 문안·결제
  POST /admin/personas/{id}/objection           이의 접수(바로 멈춤, 7일 뒤 지우기 예약)
  POST /admin/personas/{id}/hold/clear          멈춤 풀기(이의 철회·착오)
  POST /admin/personas/{id}/hold/extend         지우는 날 미루기(1~30일)

자동 발송은 없다(문안은 대표가 복사해 손으로 보낸다). 계정은 가린 이메일만 싣는다. /admin/personas/
unable 이 다른 /admin/personas/{id} 경로에 먼저 잡히지 않게 router.py 에서 admin 보다 앞에 둔다.
"""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import Field
from sqlalchemy import func, select

from app.api.deps import DB, AdminUser
from app.db.models import (
    Conversation,
    LifeStatus,
    Payment,
    Person,
    Persona,
    PersonaObjection,
    SubjectConsentRecording,
    User,
)
from app.schemas.common import APIModel, Envelope, Meta
from app.services import bereaved, billing, gate, holds, minors, notify, sharing

router = APIRouter(prefix="/admin", tags=["Admin"])

NOT_FOUND = "대화 상대를 찾지 못했어요."
NOT_HELD = "멈춘 대화 상대가 아니에요."


class ObjectionCreate(APIModel):
    reason: Literal["objection", "prior_wish", "other_parent", "subject_withdrew"]
    from_name: str | None = Field(default=None, max_length=40)
    from_relation: str | None = Field(default=None, max_length=20)
    note: str | None = Field(default=None, max_length=1000)


class HoldClear(APIModel):
    note: str | None = Field(default=None, max_length=1000)


class HoldExtend(APIModel):
    days: int = Field(ge=1, le=30)
    note: str | None = Field(default=None, max_length=1000)


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


async def _persona(db: DB, persona_id: str) -> Persona:
    persona = await db.get(Persona, persona_id, populate_existing=True)
    if persona is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return persona


async def _owners(db: DB, personas: list[Persona]) -> dict[str, str]:
    """대화 상대 id → 만든 분의 가린 이메일."""
    if not personas:
        return {}
    rows = await db.execute(
        select(Persona.id, User.email)
        .join(Person, Person.id == Persona.person_id)
        .join(User, User.id == Person.owner_user_id)
        .where(Persona.id.in_([p.id for p in personas]))
    )
    return {pid: notify.masked_email(email) for pid, email in rows}


# ────────────────────────── 목록 ──────────────────────────


def _legacy_kind(persona: Persona) -> str | None:
    """보고할 예전 대화 상대: 대화가 어려우신 분(unable) · 부모가 아닌 분이 만든 아이(살아 계신
    분)."""
    basics = persona.basics or {}
    if basics.get("status") == "unable":
        return "unable"
    if (
        basics.get("status") == "alive"
        and basics.get("minor") is True
        and basics.get("notGuardian") is True
    ):
        return "minor_not_guardian"
    return None


def _status_changed(persona: Persona) -> bool:
    """살아 계신 분(나 자신 포함)에서 돌아가신 분으로 바꾼 대화 상대(gate.mark_new_deceased 가 붙인
    표시 — 유족 선언이 없는지는 따로 본다)."""
    basics = persona.basics or {}
    return gate.deceased_other(basics) and basics.get(gate.BEREAVED_REQUIRED) is True


@router.get("/personas/unable", response_model=Envelope[list[dict]])
async def unable_personas(_: AdminUser, db: DB):
    """처리를 멈춘 대화 상대(SPEC §4-6 · §6.4). 버튼 없음 — 처리 방법은 대표가 정한다.
    지우지 않고 대화는 그대로, 새 녹음 처리·목소리 다시 만들기만 멈췄다(gate).

    kind: unable(예전 상태) · minor_not_guardian(부모가 아닌 분이 만든 아이) · status_changed(살아
    계신 분을 돌아가신 분으로 바꿨는데 유족 선언이 아직 없음 — 그분 동의 녹음을 피해 간 것은 아닌지
    대표가 본다. 선언을 적으면 목록에서 빠진다)."""
    kinds: dict[str, str] = {}
    personas: list[Persona] = []
    for p in (
        await db.scalars(
            select(Persona)
            .join(Person, Person.id == Persona.person_id)
            .where(Person.life_status.in_((LifeStatus.LIVING, LifeStatus.DECEASED)))
            .order_by(Persona.created_at)
        )
    ).all():
        kind = _legacy_kind(p)
        if kind is None and _status_changed(p) and await bereaved.get(db, p.id) is None:
            kind = "status_changed"
        if kind is not None:
            kinds[p.id] = kind
            personas.append(p)
    ids = [p.id for p in personas]
    owners = await _owners(db, personas)
    members = await sharing.joined_counts(db, ids)
    last: dict[str, datetime] = {}
    if ids:
        rows = await db.execute(
            select(Conversation.persona_id, func.max(Conversation.last_message_at))
            .where(Conversation.persona_id.in_(ids))
            .group_by(Conversation.persona_id)
        )
        last = {pid: at for pid, at in rows}
    data = [
        {
            "personaId": p.id,
            "kind": kinds[p.id],
            "title": holds.title(p),
            "ownerMasked": owners.get(p.id),
            "step": p.setup_step,
            "hasVoice": bool(p.voice_id),
            "voiceProvider": p.voice_provider,
            "createdAt": _aware(p.created_at),
            "readyAt": _aware(p.ready_at),
            "lastChatAt": _aware(last.get(p.id)),
            "members": members.get(p.id, 0),
        }
        for p in personas
    ]
    return Envelope(data=data, meta=Meta(total=len(data)))


@router.get("/holds", response_model=Envelope[list[dict]])
async def held_personas(_: AdminUser, db: DB):
    """멈춘 대화 상대(지우는 날이 가까운 것부터)."""
    personas = list(
        (
            await db.scalars(
                select(Persona)
                .where(Persona.hold_reason.is_not(None))
                .order_by(Persona.delete_after, Persona.id)
            )
        ).all()
    )
    owners = await _owners(db, personas)
    data = [
        {
            "personaId": p.id,
            "title": holds.title(p),
            "ownerMasked": owners.get(p.id),
            "reason": p.hold_reason,
            "holdAt": _aware(p.hold_at),
            "deleteAfter": _aware(p.delete_after),
        }
        for p in personas
    ]
    return Envelope(data=data, meta=Meta(total=len(data)))


# ────────────────────────── 확인 문자(manual) ──────────────────────────


@router.get("/guardian-sms", response_model=Envelope[list[dict]])
async def guardian_sms_queue(_: AdminUser, db: DB):
    """대표가 손으로 보낼 법정대리인 확인 문자(번호·확인 번호·글). 자동 발송 없음."""
    data = await minors.sms_queue(db, _now())
    return Envelope(data=data, meta=Meta(total=len(data)))


@router.post("/guardian-sms/{persona_id}/sent", response_model=Envelope[dict])
async def guardian_sms_sent(persona_id: str, _: AdminUser, db: DB):
    """보냈어요: 전체 번호·확인 번호를 지우고(가린 번호만 남김) 만든 분께 알린다."""
    persona = await _persona(db, persona_id)
    if not await minors.mark_sent(db, persona, _now()):
        raise HTTPException(status.HTTP_409_CONFLICT, "보낼 확인 문자가 없어요.")
    await db.commit()
    return Envelope(data={"ok": True})


# ────────────────────────── 한 대화 상대 ──────────────────────────


def _objection(row: PersonaObjection) -> dict:
    return {
        "id": row.id,
        "reason": row.reason,
        "fromName": row.from_name,
        "fromRelation": row.from_relation,
        "note": row.note,
        "receivedAt": _aware(row.received_at),
        "outcome": row.outcome,
        "outcomeAt": _aware(row.outcome_at),
    }


async def _minor_view(db: DB, persona: Persona) -> dict | None:
    basics = persona.basics or {}
    if basics.get("minor") is not True:
        return None
    found = await minors.state(db, persona)
    check = await minors.check_row(db, persona.id)
    return {
        "birthYm": basics.get("birthYm"),
        "guardianBasis": basics.get("guardianBasis"),
        "notGuardian": basics.get("notGuardian") is True,
        **(minors.view(found) or {}),
        "smsVerifiedAt": _aware(check.sms_verified_at) if check else None,
        "phoneMasked": check.sms_phone_masked if check else None,
        "smsRequestedAt": _aware(check.sms_requested_at) if check else None,
        "reconsentDoneAt": _aware(check.reconsent_done_at) if check else None,
        "turned14NotifiedAt": _aware(check.turned14_notified_at) if check else None,
        "turned19NotifiedAt": _aware(check.turned19_notified_at) if check else None,
    }


@router.get("/personas/{persona_id}/legal", response_model=Envelope[dict])
async def persona_legal(persona_id: str, _: AdminUser, db: DB):
    """대화 상대 관리 화면(SPEC §7.5): 유족 명단(연락처 포함)과 사람마다 직접 알림 문안, 미성년·
    법정대리인, 멈춤·이의, 그분 동의 녹음 상태, 결제·체험과 남은 날 환불 제안."""
    persona = await _persona(db, persona_id)
    person = await db.get(Person, persona.person_id)
    owner = await db.get(User, person.owner_user_id) if person else None
    basics = persona.basics or {}
    decl = await bereaved.get(db, persona.id)
    objections = (
        await db.scalars(
            select(PersonaObjection)
            .where(PersonaObjection.persona_id == persona.id)
            .order_by(PersonaObjection.received_at.desc())
        )
    ).all()
    recordings = (
        await db.scalars(
            select(SubjectConsentRecording)
            .where(SubjectConsentRecording.persona_id == persona.id)
            .order_by(SubjectConsentRecording.attempt.desc())
        )
    ).all()
    shown = ("passed", "submitted", "rerequest", "hold", "refused", "issued")
    latest = next((r.status for r in recordings if r.status in shown), "none")
    payments = list(
        (
            await db.scalars(
                select(Payment)
                .where(Payment.persona_id == persona.id)
                .order_by(Payment.created_at.desc(), Payment.id.desc())
            )
        ).all()
    )
    decl_view = bereaved.view(decl)
    if decl_view is not None:
        decl_view["history"] = list(decl.history or [])
    return Envelope(
        data={
            "persona": {
                "id": persona.id,
                "title": holds.title(persona),
                "name": persona.name,
                "status": basics.get("status"),
                "step": persona.setup_step,
                "createdAt": _aware(persona.created_at),
                "origin": persona.origin,
            },
            "owner": {
                "masked": notify.masked_email(owner.email if owner else None),
                "name": basics.get("myName") or (owner.name if owner else None),
            },
            "bereaved": decl_view,
            "minor": await _minor_view(db, persona),
            "hold": holds.view(persona),
            "objections": [_objection(row) for row in objections],
            "subjectConsent": {"status": latest, "attempts": len(recordings)},
            "templates": bereaved.templates(persona, decl, owner) if decl else [],
            "billing": {
                "trialEndsAt": _aware(persona.trial_ends_at),
                "trialUsed": next(
                    (p.trial_used for p in reversed(payments) if p.kind == "first"), None
                ),
                "payments": await billing.admin_rows(db, payments),
                "proratedRefund": await billing.prorated_refund(db, persona.id, _now()),
            },
        }
    )


@router.post("/personas/{persona_id}/objection", response_model=Envelope[dict])
async def receive_objection(persona_id: str, payload: ObjectionCreate, admin: AdminUser, db: DB):
    """이의 접수(SPEC §7.4): 기록을 남기고 바로 멈추고(만든 분·가족 대화와 모든 처리) 7일 뒤
    지우기를 예약한다. 만든 분께 알린다. 이미 이의로 멈춘 대화 상대면 기록만 더한다(지우는 날은
    그대로). 시스템이 건 멈춤(동의 녹음에서 원하지 않음·아이 본인 동의 기한 넘김)이면 까닭을 이의로
    바꾸고 지우는 날은 더 이른 쪽으로 — 뒤에 동의 녹음이 통과해도 이의가 풀리지 않게(replaced).
    환불은 약관 2번(회원 잘못이 없으면 남은 날 일할) — 대표가 토스에서 손으로 하고 결제 화면에
    적는다.
    """
    persona = await _persona(db, persona_id)
    now = _now()
    person = await db.get(Person, persona.person_id)
    db.add(
        PersonaObjection(
            persona_id=persona.id,
            owner_user_id=person.owner_user_id if person else None,
            reason=payload.reason,
            from_name=(payload.from_name or "").strip() or None,
            from_relation=(payload.from_relation or "").strip() or None,
            note=(payload.note or "").strip() or None,
            received_at=now,
            admin_user_id=admin.id,
        )
    )
    outcome = await holds.take_objection(db, persona, payload.reason, by_user_id=admin.id, days=7)
    await db.commit()
    return Envelope(
        data={
            # already: 이미 멈춘 대화 상대였다(replaced 면 까닭을 이의로 바꾸고 만든 분께 다시
            # 알렸다)
            "already": outcome != "placed",
            "replaced": outcome == "replaced",
            "reason": persona.hold_reason,
            "holdAt": _aware(persona.hold_at),
            "deleteAfter": _aware(persona.delete_after),
            "refund": await billing.prorated_refund(db, persona.id, now),
        }
    )


@router.post("/personas/{persona_id}/hold/clear", response_model=Envelope[dict])
async def clear_hold(persona_id: str, payload: HoldClear, admin: AdminUser, db: DB):
    """멈춤 풀기(이의 철회·착오). 열린 이의 기록은 cancelled, 만든 분께 알린다."""
    persona = await _persona(db, persona_id)
    if not persona.hold_reason:
        raise HTTPException(status.HTTP_409_CONFLICT, NOT_HELD)
    note = (payload.note or "").strip() or None
    await holds.clear(db, persona, by_user_id=admin.id, note=note)
    await db.commit()
    return Envelope(data={"hold": None})


@router.post("/personas/{persona_id}/hold/extend", response_model=Envelope[dict])
async def extend_hold(persona_id: str, payload: HoldExtend, admin: AdminUser, db: DB):
    """지우는 날을 days 일 미룬다(1~30)."""
    persona = await _persona(db, persona_id)
    note = (payload.note or "").strip() or None
    try:
        await holds.extend(db, persona, payload.days, by_user_id=admin.id, note=note)
    except ValueError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, NOT_HELD) from error
    await db.commit()
    return Envelope(data={"hold": holds.view(persona)})
