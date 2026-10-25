"""그분 동의 녹음(app/services/subject_consent.py · SPEC §5.6 · §5.7).

만든 분(owned_persona — 함께 쓰는 가족·다른 사람은 404)
  GET  /personas/{id}/subject-consent                 지금 상태
  POST /personas/{id}/subject-consent/start           확인 단어 발급(새 시도)
  POST /personas/{id}/subject-consent/{rid}/audio     녹음 올리기(multipart, 변환 없이 서울에만)
  POST /personas/{id}/subject-consent/{rid}/abandon   올리지 못하고 끊김
관리자
  GET  /admin/subject-consents?status=&page=&size=    검수 대기열(오래된 것부터, 원하지 않음 먼저)
  GET  /admin/subject-consents/{id}                   하나(서명 주소로 듣기 — 내려받기 단추 없음)
  POST /admin/subject-consents/{id}/review            10항목 판정

동의 녹음은 어떤 해외 회사로도 보내지 않는다(받아쓰기·목소리 만들기에 쓰지 않는다). 막히면
GateError → {"detail": 글, "code": 코드}.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Query, UploadFile
from pydantic import Field
from sqlalchemy import func, select

from app.api.deps import DB, AdminUser, CurrentUser
from app.api.v1.persona_setup import owned_persona
from app.core import ratelimit
from app.core.config import get_settings
from app.db.models import (
    Consent,
    Person,
    Persona,
    PersonaMinorCheck,
    SubjectConsentRecording,
    User,
)
from app.schemas.common import APIModel, Envelope, Meta
from app.services import ages, legal, notices, notify, persona_builder, subject_consent
from app.services.storage import get_storage

router = APIRouter(tags=["SubjectConsent"])
admin_router = APIRouter(prefix="/admin", tags=["Admin"])

BASE = "/personas/{persona_id}/subject-consent"
# 관리자 목록 status 값(all = 모두)
ListState = Literal["submitted", "hold", "passed", "rerequest", "refused", "interrupted", "all"]


def _started_view(row: SubjectConsentRecording) -> dict:
    settings = get_settings()
    return {
        "id": row.id,
        "attempt": row.attempt,
        "words": (row.words or "").split(),
        "issuedAt": row.words_issued_at,
        "expiresAt": row.expires_at,
        "mode": row.mode,
        "scriptDoc": row.script_doc,
        "scriptVersion": row.script_version,
        "maxSeconds": settings.subject_consent_max_seconds,
        "maxMb": settings.subject_consent_max_mb,
    }


# ────────────────────────── 만든 분 ──────────────────────────


@router.get(BASE, response_model=Envelope[dict])
async def consent_state(persona_id: str, user: CurrentUser, db: DB):
    persona = await owned_persona(db, persona_id, user.id)
    return Envelope(data=await subject_consent.status_for(db, persona))


@router.post(BASE + "/start", response_model=Envelope[dict], status_code=201)
async def start_recording(persona_id: str, user: CurrentUser, db: DB):
    """확인 단어 두 개를 발급한다. 화면은 녹음을 시작한 뒤에야 단어를 크게 보인다."""
    persona = await owned_persona(db, persona_id, user.id)
    ratelimit.SUBJECT_CONSENT_START.check(persona.id)
    row = await subject_consent.issue(db, user, persona)
    return Envelope(data=_started_view(row))


@router.post(BASE + "/{record_id}/audio", response_model=Envelope[dict])
async def upload_recording(
    persona_id: str,
    record_id: str,
    user: CurrentUser,
    db: DB,
    audio: Annotated[UploadFile, File()],
    steps: Annotated[str | None, Form(max_length=5000)] = None,
    interrupted: Annotated[str | None, Form(max_length=10)] = None,
    refused: Annotated[str | None, Form(max_length=10)] = None,
    reason: Annotated[str | None, Form(max_length=20)] = None,
    format: Annotated[str | None, Form(max_length=10)] = None,  # noqa: A002 — 화면이 보내는 칸 이름
):
    """끝까지 녹음했거나('원하지 않으세요'를 눌렀거나) 중간에 멈춘 녹음을 받는다. 다시 듣기·고르기
    없이 끝내면 바로 올린다(편집하지 않은 한 파일)."""
    ratelimit.SUBJECT_CONSENT_UPLOAD.check(user.id)
    persona = await owned_persona(db, persona_id, user.id)
    content = await audio.read()
    row = await subject_consent.store_audio(
        db,
        user,
        persona,
        record_id,
        content=content,
        filename=audio.filename,
        fmt=format,
        steps=subject_consent.parse_steps(steps),
        interrupted=_truthy(interrupted),
        refused=_truthy(refused),
        reason=(reason or "").strip() or None,
    )
    return Envelope(data={"id": row.id, "status": row.status})


class AbandonRequest(APIModel):
    reason: Literal["hidden", "mic", "network", "user", "error"] = "error"


@router.post(BASE + "/{record_id}/abandon", response_model=Envelope[dict])
async def abandon_recording(
    persona_id: str,
    record_id: str,
    user: CurrentUser,
    db: DB,
    payload: AbandonRequest | None = None,
):
    """올리지 못하고 끊겼다(조각이 없거나 올리기에 실패). 발급 줄을 interrupted 로 남긴다."""
    persona = await owned_persona(db, persona_id, user.id)
    reason = payload.reason if payload is not None else "error"
    row = await subject_consent.abandon(db, persona, record_id, reason)
    return Envelope(data={"id": row.id, "status": row.status})


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("true", "1", "on", "yes")


# ────────────────────────── 관리자 ──────────────────────────


def _age_band(mode: str | None) -> str | None:
    return subject_consent.AGE_BANDS.get(mode or "")


async def _owners(db: DB, personas: list[Persona]) -> dict[str, User]:
    """대화 상대 id → 만든 분."""
    person_ids = {p.person_id for p in personas}
    if not person_ids:
        return {}
    rows = (
        await db.execute(
            select(Person.id, User)
            .join(User, User.id == Person.owner_user_id)
            .where(Person.id.in_(person_ids))
        )
    ).all()
    by_person = {person_id: user for person_id, user in rows}
    return {p.id: by_person.get(p.person_id) for p in personas}


def _masked(user: User | None) -> str:
    return notify.masked_email(user.email if user else None)


@admin_router.get("/subject-consents", response_model=Envelope[list[dict]])
async def review_queue(
    _: AdminUser,
    db: DB,
    status_filter: Annotated[ListState, Query(alias="status")] = "submitted",
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
):
    """검수 대기열. 원하지 않으세요를 누른 녹음 먼저, 그다음 오래된 것부터."""
    query = select(SubjectConsentRecording)
    if status_filter != "all":
        query = query.where(SubjectConsentRecording.status == status_filter)
    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(
        (
            await db.scalars(
                query.order_by(
                    SubjectConsentRecording.refused_flag.desc(),
                    SubjectConsentRecording.submitted_at.asc(),
                    SubjectConsentRecording.created_at.asc(),
                )
                .offset((page - 1) * size)
                .limit(size)
            )
        ).all()
    )
    persona_ids = list({row.persona_id for row in rows})
    personas = (
        {p.id: p for p in (await db.scalars(select(Persona).where(Persona.id.in_(persona_ids))))}
        if persona_ids
        else {}
    )
    owners = await _owners(db, list(personas.values()))
    counts: dict[tuple[str, str], int] = {}
    if persona_ids:
        for pid, state, n in (
            await db.execute(
                select(
                    SubjectConsentRecording.persona_id,
                    SubjectConsentRecording.status,
                    func.count(),
                )
                .where(SubjectConsentRecording.persona_id.in_(persona_ids))
                .group_by(SubjectConsentRecording.persona_id, SubjectConsentRecording.status)
            )
        ).all():
            counts[(pid, state)] = n

    def count(pid: str, states) -> int:
        return sum(counts.get((pid, state), 0) for state in states)

    data = [
        {
            "id": row.id,
            "personaId": row.persona_id,
            "title": notices.persona_label(personas.get(row.persona_id)),
            "attempt": row.attempt,
            "submittedCount": count(row.persona_id, subject_consent.SUBMITTED_STATES),
            "interruptedCount": count(row.persona_id, ("interrupted",)),
            "mode": row.mode,
            "status": row.status,
            "refusedFlag": row.refused_flag,
            "submittedAt": row.submitted_at,
            "ownerMasked": _masked(owners.get(row.persona_id)),
            "ageBand": _age_band(row.mode),
        }
        for row in rows
    ]
    return Envelope(data=data, meta=Meta(page=page, limit=size, total=total))


async def _row(db: DB, record_id: str) -> SubjectConsentRecording:
    row = await db.get(SubjectConsentRecording, record_id, populate_existing=True)
    if row is None:
        raise subject_consent._error(404, "not_found", subject_consent.NOT_FOUND)
    return row


async def _guardian_view(db: DB, persona: Persona, owner: User | None) -> dict | None:
    """아이의 대화 상대: 법정대리인 확인 기록과 확인 문자(가린 번호만)."""
    if (persona.basics or {}).get("minor") is not True:
        return None
    agreed: dict[str, datetime | None] = {}
    if owner is not None:
        prefixes = ("guardian_attest", "guardian_consent", "guardian_overseas")
        types = [legal.persona_type(prefix, persona.id) for prefix in prefixes]
        rows = (
            await db.scalars(
                select(Consent).where(Consent.user_id == owner.id, Consent.consent_type.in_(types))
            )
        ).all()
        agreed = {
            legal.split_type(row.consent_type)[0]: row.agreed_at for row in rows if row.agreed
        }
    check = await db.scalar(
        select(PersonaMinorCheck).where(PersonaMinorCheck.persona_id == persona.id)
    )
    return {
        "attestAt": agreed.get("guardian_attest"),
        "consentAt": agreed.get("guardian_consent"),
        "overseasAt": agreed.get("guardian_overseas"),
        "smsVerifiedAt": check.sms_verified_at if check else None,
        "phoneMasked": check.sms_phone_masked if check else None,
    }


def _persona_view(persona: Persona) -> dict:
    basics = persona.basics or {}
    age = None
    if basics.get("minor") is True and basics.get("birthYm"):
        try:
            age = ages.age_on(basics["birthYm"], ages.today_kst())
        except ValueError:
            age = None
    return {
        "id": persona.id,
        "title": notices.persona_label(persona),
        "name": persona.name,
        "relation": basics.get("relation"),
        "myRelation": basics.get("myRelation"),
        "status": basics.get("status"),
        "minor": basics.get("minor"),
        "birthYm": basics.get("birthYm"),
        "age": age,
        "guardianBasis": basics.get("guardianBasis"),
        "step": persona.setup_step,
        "createdAt": persona.created_at,
    }


def _review_view(row: SubjectConsentRecording, reviewer: User | None) -> dict | None:
    if row.reviewed_at is None:
        return None
    verdict = {v: k for k, v in subject_consent.VERDICTS.items()}.get(row.status)
    return {
        "checklist": row.checklist,
        "verdict": verdict,
        "reviewedAt": row.reviewed_at,
        "reviewer": _masked(reviewer) if reviewer else None,
        "noteToOwner": row.note_to_owner,
        "adminNote": row.admin_note,
    }


@admin_router.get("/subject-consents/{record_id}", response_model=Envelope[dict])
async def review_detail(record_id: str, _: AdminUser, db: DB):
    """검수 화면 하나: 지금 녹음, 앞선 녹음들, 목소리 비교용 이야기 녹음(서울 저장 — 해외로 보내지
    않음), 아이면 법정대리인 확인, 10항목과 판정 안내."""
    row = await _row(db, record_id)
    persona = await db.get(Persona, row.persona_id)
    if persona is None:
        raise subject_consent._error(404, "not_found", subject_consent.NOT_FOUND)
    owner = await subject_consent.owner_of(db, persona)
    reviewer = await db.get(User, row.reviewer_user_id) if row.reviewer_user_id else None
    others = [r for r in await subject_consent.rows_of(db, persona.id) if r.id != row.id]
    recordings = await persona_builder.load_recordings(db, persona.id)
    storage = get_storage()
    meta = row.client_meta or {}
    data = {
        "id": row.id,
        "attempt": row.attempt,
        "mode": row.mode,
        "ageBand": _age_band(row.mode),
        "status": row.status,
        "refusedFlag": row.refused_flag,
        "words": (row.words or "").split(),
        "wordsIssuedAt": row.words_issued_at,
        "submittedAt": row.submitted_at,
        "durationMs": row.duration_ms,
        "sizeBytes": row.size_bytes,
        "sha256": row.sha256,
        "contentType": row.content_type,
        "scriptDoc": row.script_doc,
        "scriptVersion": row.script_version,
        # 확인 단어를 받은 뒤 녹음 길이보다 300초 넘게 더 걸렸으면 화면이 8번 판단 보조 글을 보인다
        "gapSeconds": subject_consent.gap_seconds(row),
        "hasStoryRecordings": bool(recordings),
        "steps": meta.get("steps") or [],
        "interrupted": bool(meta.get("interrupted")),
        "reason": meta.get("reason"),
        "audioUrl": subject_consent.listen_url(row),
        "persona": _persona_view(persona),
        "owner": {"masked": _masked(owner), "joinedAt": owner.created_at if owner else None},
        "history": [
            {
                "id": r.id,
                "attempt": r.attempt,
                "status": r.status,
                "words": (r.words or "").split(),
                "issuedAt": r.words_issued_at,
                "submittedAt": r.submitted_at,
                "reviewedAt": r.reviewed_at,
                "durationMs": r.duration_ms,
                "audioUrl": subject_consent.listen_url(r),
                "checklist": r.checklist,
                "noteToOwner": r.note_to_owner,
            }
            for r in reversed(others)
        ],
        "recordings": [
            {
                "id": rec.id,
                "name": rec.original_name,
                "durationMs": rec.duration_ms,
                "url": storage.signed_url(rec.storage_key, subject_consent.LISTEN_TTL),
            }
            for rec in recordings
            if rec.storage_key
        ],
        "guardian": await _guardian_view(db, persona, owner),
        "checklist": subject_consent.CHECKLIST,
        "rerequestHead": subject_consent.REREQUEST_HEAD,
        "guide": subject_consent.GUIDE,
        "review": _review_view(row, reviewer),
    }
    return Envelope(data=data)


class ReviewRequest(APIModel):
    checklist: dict[str, bool] = Field(default_factory=dict)
    verdict: Literal["pass", "rerequest", "hold", "refused"]
    note_to_owner: str | None = Field(default=None, max_length=500)
    admin_note: str | None = Field(default=None, max_length=1000)
    # 재요청일 때만: 녹음 상한을 하나 늘린다(고객센터로 연락이 온 경우)
    allow_more: bool = False


@admin_router.post("/subject-consents/{record_id}/review", response_model=Envelope[dict])
async def review_recording(record_id: str, payload: ReviewRequest, admin: AdminUser, db: DB):
    row = await subject_consent.review(
        db,
        admin,
        record_id,
        checklist=payload.checklist,
        verdict=payload.verdict,
        note_to_owner=payload.note_to_owner,
        admin_note=payload.admin_note,
        allow_more=payload.allow_more,
    )
    return Envelope(
        data={
            "id": row.id,
            "status": row.status,
            "reviewedAt": row.reviewed_at,
            "extraAllowed": row.extra_allowed,
        }
    )
