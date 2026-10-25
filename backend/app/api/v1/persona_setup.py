"""페르소나 만들기 흐름.

UPLOAD     음성·영상 올리기 → 소리 뽑기·전사·목소리 묶기(뒤에서)
SPEAKERS   그분 목소리 고르기(녹음이 여러 개여도 목소리 단위) → 녹음 분석 시작(뒤에서)
QUESTIONS  핵심 질문 답하기 (분석과 동시에)
BUILDING   분석이 끝나길 기다렸다가 페르소나 조립
READY      대화
"""

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from app.api.deps import DB, CurrentUser
from app.core import ratelimit
from app.core.config import get_settings
from app.db.models import (
    ORIGIN_IMPORT,
    Conversation,
    InterviewEntry,
    JobStatus,
    Memory,
    Message,
    Person,
    Persona,
    PersonaJob,
    PersonaShare,
    Recording,
    SetupStep,
    SubjectConsentRecording,
    User,
    UserRole,
)
from app.schemas.common import APIModel, Envelope
from app.services import (
    analytics,
    audio,
    bereaved,
    billing,
    deletion,
    gaps,
    gate,
    holds,
    intake,
    jobs,
    legal,
    mailer,
    minors,
    notify,
    persona_builder,
    persona_delete,
    persona_import,
    sharing,
    sms,
    subject_consent,
)
from app.services.persona_builder import pledge_doc
from app.services.storage import get_storage

router = APIRouter(tags=["Persona setup"])
logger = logging.getLogger(__name__)

MAX_RECORDINGS = 20
JOB_KINDS = persona_delete.JOB_KINDS


def _limit_job(user, persona: Persona, kind: str) -> None:
    """AI 비용이 드는 작업을 마구 다시 걸지 못하게 센다(관리자는 세지 않는다). 넘으면 429.

    저장·동의 기록처럼 되돌리기 어려운 일보다 먼저 부른다(막혔는데 저장만 남지 않게).
    """
    if user.role != UserRole.ADMIN:
        ratelimit.JOB_STARTS.check(f"{persona.id}:{kind}")


def _file_size(upload: UploadFile) -> int:
    """받아 둔 파일 크기(읽지 않고 잰다 — 몇백 MB 를 메모리에 올리지 않게)."""
    handle = upload.file
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    handle.seek(0)
    return size


async def _used_recordings(db: DB, recordings: list[Recording]) -> set[str]:
    """기억(Memory)을 남긴 녹음 id."""
    ids = [r.id for r in recordings]
    if not ids:
        return set()
    rows = await db.scalars(
        select(Memory.recording_id).where(Memory.recording_id.in_(ids)).distinct()
    )
    return set(rows.all())


def _removable(persona: Persona, rec: Recording, used: set[str]) -> bool:
    """이 녹음을 뺄 수 있나(화면이 지우기 단추를 보일지).

    다 만든 대화 상대의 옮긴 녹음은 그 녹음으로 말투·기억·목소리를 만들었으니 빼지 않는다. 다만
    그분 목소리가 하나도 없고 분석·기억도 남기지 않은 녹음(잘못 올린 남의 통화 등)은 뺄 수 있다.
    """
    if persona.setup_step not in (SetupStep.UPLOAD, SetupStep.READY):
        return False
    if persona.setup_step != SetupStep.READY or rec.status != "TRANSCRIBED":
        return True
    subject = set(persona.subject_voices or [])
    return (
        rec.id not in used
        and rec.analysis is None
        and not any(persona_builder._is_subject(s, subject) for s in rec.segments)
    )


async def _voice_issue(db: DB, persona: Persona) -> str | None:
    """다 만들 때 목소리를 만들지 못한 까닭(그 뒤로 목소리를 다시 만들지 않았으면). 없으면 None."""
    if persona.setup_step != SetupStep.READY or persona.voice_id:
        return None
    build = await jobs.latest(db, persona.id, "BUILD")
    if build is None or build.status != JobStatus.DONE:
        return None
    reason = (build.options or {}).get("voiceError")
    voice = await jobs.latest(db, persona.id, "VOICE")
    if not reason or (voice is not None and voice.created_at >= build.created_at):
        return None
    return reason


class PersonaCreate(APIModel):
    name: str = Field(max_length=40)
    relation: str = Field(max_length=40)
    address_term: str | None = Field(default=None, max_length=30)
    status: str = Field(max_length=20)
    my_name: str = Field(max_length=40)
    my_relation: str | None = Field(default=None, max_length=40)
    called_me: str | None = Field(default=None, max_length=30)
    # 살아 계신 분(나 자신 아님)이면 만 19세 미만인지(새로 만들 때 필수 — intake.validate_basics).
    # 만 19세 미만이면 태어난 해와 달(YYYY-MM), 부모 양쪽(both_agreed · sole_custody), 법정대리인
    # 확인(guardianAttest — basics 에 두지 않고 동의 기록으로 남긴다)
    minor: bool | None = None
    birth_ym: str | None = Field(default=None, max_length=7)
    guardian_basis: str | None = Field(default=None, max_length=20)
    guardian_attest: bool = False


class PersonaUpdate(APIModel):
    basics: PersonaCreate
    # 질문 답. 없으면 그대로 둔다. 다 만든 페르소나면 꼭 필요한 답이 다 있어야 한다.
    answers: dict | None = None


class VoiceChoice(APIModel):
    # 그분으로 고른 목소리(VOICE_1 …). 하나가 보통이고, 한 사람이 나뉘었으면 여럿.
    voice_keys: list[str] = Field(max_length=20)


class BuildRequest(APIModel):
    answers: dict
    clone_voice: bool = False
    # 목소리는 목소리 주인(또는 유족)의 동의 한 번으로 만든다(Fish 즉시 복제).
    # 동의는 페르소나마다 남긴다(다시 만들기·재시도 때는 다시 묻지 않는다).
    voice_consent: bool = False


def _voice_consent_type(persona_id: str) -> str:
    return legal.persona_type("voice_clone", persona_id)


class VoiceRequest(APIModel):
    voice_consent: bool = False


# 목소리 만들기 확인(그분 상태마다 다른 pledge 문서)은 persona_builder 에 둔다.
# 뒤에서 도는 목소리 다시 만들기(voice_retrain)도 같은 규칙으로 본다.
_voice_consented = persona_builder.voice_consented


async def _ensure_voice_consent(db: DB, user, persona: Persona, agreed: bool) -> None:
    """목소리는 목소리 만들기 확인 한 번으로 만든다(Fish 즉시 복제). 확인이 없으면 400.

    지금 상태는 대화 상대마다 한 줄(consents), 이력은 consent_records 에 쌓는다(doc=pledge_*).
    """
    if await _voice_consented(db, user.id, persona):
        return
    if not agreed:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "목소리를 만들려면 목소리 사용 동의가 필요해요."
        )
    await legal.record(
        db,
        user,
        _voice_consent_type(persona.id),
        True,
        persona_id=persona.id,
        doc=pledge_doc(persona),
    )


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("true", "1", "on", "yes")


async def persona_consents(db: DB, user_id: str, persona: Persona) -> dict:
    """대화 상대 상세에 싣는 대화 상대별 동의."""

    async def has(prefix: str) -> bool:
        return await legal.has(db, user_id, legal.persona_type(prefix, persona.id))

    return {
        "voice": await _voice_consented(db, user_id, persona),
        "recordingPledge": await has("recording_pledge"),
        "materialPledge": await has("material_pledge"),
        "sensitiveReligion": await has("sensitive_religion"),
        "sensitiveHealth": await has("sensitive_health"),
    }


SENSITIVE_MESSAGES = {
    "religion": "종교 이야기를 적으려면 먼저 동의해 주세요.",
    "health": "건강 이야기를 적으려면 먼저 동의해 주세요.",
}


async def _check_sensitive(db: DB, user_id: str, persona: Persona, answers: dict) -> None:
    """동의 없는 민감 범주(종교·건강)의 답이 새로 들어오거나 바뀌면 400.

    이 기능 전에 저장된 같은 답은 그대로 둔다(정보 고치기가 지금 답을 다시 보내도 막지 않게).
    """
    old = persona.answers or {}
    for category in intake.SENSITIVE:
        keys = intake.sensitive_keys(category)
        if not any(answers.get(k) and answers.get(k) != old.get(k) for k in keys):
            continue
        kind = legal.persona_type(f"sensitive_{category}", persona.id)
        if not await legal.has(db, user_id, kind):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, SENSITIVE_MESSAGES[category])


# ────────────────────────── 공통 ──────────────────────────


async def owned_persona(db: DB, persona_id: str, user_id: str) -> Persona:
    persona = await db.scalar(
        select(Persona)
        .join(Person)
        .where(Persona.id == persona_id, Person.owner_user_id == user_id)
        .execution_options(populate_existing=True)
    )
    if persona is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "대화 상대를 찾지 못했어요.")
    return persona


def require_step(persona: Persona, *steps: str) -> None:
    if persona.setup_step not in steps:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "지금 단계에서는 할 수 없는 일이에요. 화면을 새로고침해 주세요.",
        )


IMPORTED_BLOCKED = "가져온 대화 상대는 이 기능을 아직 쓸 수 없어요."


def require_built_here(persona: Persona) -> None:
    """녹음·질문으로 만든 대화 상대만 받는 일(녹음 더하기·만들기 단계·질문 답).

    관리자가 가져온 대화 상대(origin=import)는 지침을 손으로 다듬어 넣어서, 녹음·질문으로 다시
    만들면 그 지침을 덮어쓴다(persona_builder._assemble). 정보 고치기(기본 정보만)·민감 동의·목소리
    붙이기는 따로 받는다.
    """
    if persona.origin == ORIGIN_IMPORT:
        raise HTTPException(status.HTTP_409_CONFLICT, IMPORTED_BLOCKED)


def summary(persona: Persona, person: Person) -> dict:
    basics = persona.basics or {}
    return {
        "id": persona.id,
        "personId": person.id,
        "name": persona.name,
        "relation": basics.get("relation") or person.relationship_label,
        "addressTerm": basics.get("addressTerm") or person.address_term,
        "lifeStatus": person.life_status,
        "step": persona.setup_step,
        "hasVoice": bool(persona.voice_id),
        "voiceProvider": persona.voice_provider,
        # None = 만들기 흐름, "import" = 관리자가 가져옴(녹음 더하기·질문 더 답하기를 숨긴다)
        "origin": persona.origin,
        "updatedAt": persona.updated_at,
    }


async def _minor_view(db: DB, persona: Persona) -> dict | None:
    """만 19세 미만 대화 상대의 상태(나이·동의 녹음 종류·법정대리인 확인·재동의 기한)."""
    if (persona.basics or {}).get("minor") is not True:
        return None
    return minors.view(await minors.state(db, persona))


async def _bereaved_declared(db: DB, persona: Persona) -> bool | None:
    """돌아가신 분(나 자신 아님)이면 신청 자격·유족 명단을 적었나. 그 밖이면 None."""
    basics = persona.basics or {}
    if basics.get("status") != "deceased" or intake.is_self(basics):
        return None
    return await bereaved.get(db, persona.id) is not None


async def _voice_pending(db: DB, persona: Persona) -> bool:
    if persona.origin != ORIGIN_IMPORT:
        return False
    record = await persona_import.import_record(db, persona.id)
    return persona_import.pending_voice(persona, record) is not None


async def job_states(db: DB, persona_id: str) -> dict:
    out = {}
    for kind in JOB_KINDS:
        job = await jobs.latest(db, persona_id, kind)
        if job:
            out[kind] = {
                "status": job.status,
                "detail": job.detail,
                "percent": job.percent,
                "error": job.error,
            }
    return out


async def _require_plan(db: DB, user, persona: Persona, doing: str) -> None:
    """다 만든 대화 상대를 키우는 일(녹음 더하기·답 고치기·목소리 다시 만들기)은 AI 비용이 들어
    그 대화 상대의 무료 체험이나 요금제가 있을 때만 한다(요금제는 대화 상대마다)."""
    ent = await billing.entitlement(db, user, persona)
    if not ent.active:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"무료 체험이나 구독이 끝나서 지금은 {doing} 수 없어요. "
            "요금제를 고르면 다시 할 수 있어요.",
        )


# 질문 더 답하기(기억 더 채우기) 목록이 바뀐 차례. 2(2026-10-17): 몇 질문을 만들 때 묻는 질문으로
# 옮겨 count 가 그 전보다 적게 나온다. 3(2026-10-21): 첫마디·마지막 말·챙겨 주시던 장면을 만들 때로,
# 언제 무렵·고향·하신 일·사투리·말끝을 이리로 옮겼다. 통계에서 차례가 다른 줄은 따로 본다.
# 나 자신은 개편 대상이 아니라 여전히 2(개편 전 목록 — intake.more_questions_for)다.
MORE_QUESTION_SET = 3
SELF_MORE_QUESTION_SET = 2


def _more_answer_count(answers: dict, basics: dict | None) -> int:
    """기억 더 채우기에서 답한 질문 수(통계용 — 내용은 보지 않는다). 목록은 대화 상대마다 다르다
    (나 자신은 개편 전 목록 — intake.more_questions_for)."""
    ids = {q["id"] for q in intake.more_questions_for(basics)}
    return sum(1 for k, v in answers.items() if k in ids and v not in (None, "", [], {}))


def _intake_error(error: intake.IntakeError) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(error))


# ────────────────────────── 질문·선지 ──────────────────────────


@router.get("/intake/meta", response_model=Envelope[dict])
async def intake_meta():
    settings = get_settings()
    return Envelope(
        data={
            # required 는 개편 뒤 가족·고인 기준. 대화 상대마다 꼭 받는 질문은 상세의
            # requiredQuestions 다(예전 대화 상대·나 자신은 legacyRequired 넷).
            "questions": intake.QUESTIONS,
            "legacyRequired": list(intake.LEGACY_REQUIRED),
            # 만들 때 질문의 묶음 순서와 묶음 머리 한 줄 안내(lead)
            "sections": intake.QUESTION_SECTIONS,
            # 다 만든 뒤 '더 알려주기'(모두 선택). when=deceased 는 돌아가신 분만.
            "moreQuestions": intake.MORE_QUESTIONS,
            "moreModules": intake.MORE_MODULES,
            "relations": intake.RELATIONS,
            "myRelations": intake.MY_RELATIONS,
            # 만들 때 고르는 그분 상태(대화가 어려우신 분은 2026-10-22부터 빠졌다)
            "statuses": intake.STATUSES,
            # 만 19세 미만 아이의 대화 상대: 관계는 부모만, 부모 양쪽, 만 14세 미만을 받는지
            "minorRelations": {
                "relation": list(intake.MINOR_RELATIONS),
                "myRelation": list(intake.MINOR_MY_RELATIONS),
            },
            "guardianBases": intake.GUARDIAN_BASES,
            "under14Open": sms.under14_open(),
            "minorShareRelations": list(intake.MINOR_SHARE_RELATIONS),
            # 돌아가신 분: 신청 자격, 동의해 준 유족의 관계, 유족 명단의 관계
            "applicantRelations": intake.APPLICANT_RELATIONS,
            "consentingRelations": intake.CONSENTING_RELATIONS,
            "bereavedRelations": intake.BEREAVED_RELATIONS,
            "maxUploadMb": settings.max_upload_mb,
            # 녹음 하나의 길이 상한(분). 넘으면 그 녹음은 '너무 길어요'로 실패한다.
            "maxRecordingMinutes": settings.max_recording_minutes,
            "allowSignup": settings.allow_signup,
        }
    )


# ────────────────────────── 목록·생성·삭제 ──────────────────────────


@router.get("/personas", response_model=Envelope[list[dict]])
async def list_personas(user: CurrentUser, db: DB):
    """내가 만든 대화 상대와, 가족이 나눠 줘 함께 쓰는(다 만든) 대화 상대. 최근에 바뀐 것부터.

    함께 쓰는 대화 상대는 그 가족의 쪽에서 본 모양이다(성함·personId·목소리 회사 없이).
    마지막 말은 늘 내 대화방의 것이다(다른 가족의 말은 보이지 않는다).
    """
    rows = (
        await db.execute(
            select(Persona, Person)
            .join(Person)
            .where(Person.owner_user_id == user.id)
            .order_by(Persona.updated_at.desc())
        )
    ).all()
    joined = (
        await db.execute(
            select(Persona, Person, PersonaShare)
            .join(Person, Person.id == Persona.person_id)
            .join(PersonaShare, PersonaShare.persona_id == Persona.id)
            .where(PersonaShare.user_id == user.id, Persona.setup_step == SetupStep.READY)
        )
    ).all()
    members = await sharing.joined_counts(db, [persona.id for persona, _ in rows])
    owners = {}
    if joined:
        owner_ids = {person.owner_user_id for _, person, _ in joined}
        owners = {
            u.id: u for u in (await db.scalars(select(User).where(User.id.in_(owner_ids)))).all()
        }
    entries = []
    for persona, person in rows:
        # 만든 분이 할 일(그분 동의 녹음·유족 확인 등)과 멈춤. 목록은 종류와 막는지만 싣는다
        cards = await gate.actions(db, persona)
        entries.append(
            (
                persona,
                summary(persona, person)
                | {
                    "role": "owner",
                    "members": members.get(persona.id, 0),
                    "actions": [{"kind": c["kind"], "blocking": c["blocking"]} for c in cards],
                    "hold": holds.view(persona),
                },
            )
        )
    for persona, person, share in joined:
        access = sharing.ChatAccess(persona, person, owners.get(person.owner_user_id), share)
        item = sharing.member_item(access)
        if persona.hold_reason:
            item = item | {"stopped": True}
        entries.append((persona, item))
    if joined:
        entries.sort(key=lambda entry: entry[0].updated_at, reverse=True)
    items = []
    for persona, item in entries:
        # 목록에서 메신저처럼 마지막 말 한 줄을 보여준다
        last = (
            await db.execute(
                select(Message.content, Message.role, Message.created_at)
                .join(Conversation)
                .where(Conversation.persona_id == persona.id, Conversation.user_id == user.id)
                .order_by(Message.created_at.desc())
                .limit(1)
            )
        ).first()
        items.append(
            item
            | {
                "lastMessage": last.content[:80] if last else None,
                "lastMessageRole": last.role if last else None,
                "lastMessageAt": last.created_at if last else None,
            }
        )
    return Envelope(data=items)


async def _check_prelaunch_cap(db: DB, user) -> None:
    """결제 전 모드에서는 계정 하나가 가진 대화 상대 수를 막는다(무료라 마구 만들지 못하게)."""
    cap = get_settings().prelaunch_personas_per_account
    if cap is None or billing.payments_open(user) or user.role == UserRole.ADMIN:
        return
    owned = await db.scalar(
        select(func.count(Persona.id)).join(Person).where(Person.owner_user_id == user.id)
    )
    if (owned or 0) >= cap:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"지금은 대화 상대를 {cap}명까지 만들 수 있어요."
        )


@router.post("/personas", response_model=Envelope[dict], status_code=201)
async def create_persona(payload: PersonaCreate, user: CurrentUser, db: DB):
    try:
        # 가족·고인 대화 상대는 개편 뒤 만들 때 질문(필수 13개 + 조심할 이야기 확인)을 받는다.
        # 살아 계신 분은 만 19세 미만인지도 받는다(아이면 부모만·법정대리인 확인)
        basics = intake.with_version(intake.validate_basics(payload.model_dump(by_alias=True)))
    except intake.IntakeError as error:
        raise _intake_error(error) from error
    await _check_prelaunch_cap(db, user)
    person = Person(
        owner_user_id=user.id,
        name=basics["name"],
        relationship_label=basics["relation"],
        address_term=basics["addressTerm"],
        life_status=intake.LIFE_STATUS[basics["status"]],
    )
    db.add(person)
    await db.flush()
    persona = Persona(
        person_id=person.id,
        name=basics["name"],
        status="DRAFT",
        setup_step=SetupStep.UPLOAD,
        basics=basics,
        answers={},
    )
    db.add(persona)
    if basics.get("minor") is True:
        # 아이의 법정대리인(부모)이라는 확인(guardianAttest)을 동의 기록으로 남긴다
        await db.flush()
        await minors.record_attest(db, user, persona)
    await db.commit()
    await analytics.track(
        db, "persona_created", user_id=user.id, persona_id=persona.id, status=basics["status"]
    )
    return Envelope(data=summary(persona, person))


@router.get("/personas/{persona_id}", response_model=Envelope[dict])
async def persona_detail(persona_id: str, user: CurrentUser, db: DB):
    """대화 상대 상세. 함께 쓰는 가족에게는 가족 쪽에서 본 모양만
    (기본 정보·질문 답·녹음·동의 없이)."""
    access = await sharing.chat_access(db, persona_id, user)
    # '더 알려 주실 기억' 배지(알림은 보내지 않는다): 만든 분은 열린 질문 수,
    # 가족은 물어봐 달라고 한 질문 수
    gap_count = await gaps.badge_count(db, persona_id, user.id, member=access.member)
    if access.member:
        member = sharing.member_detail(access) | {"gapCount": gap_count}
        if access.persona.hold_reason:
            # 가족에게는 멈췄다는 것만(까닭·지우는 날은 만든 분만 본다)
            member["stopped"] = True
        return Envelope(data=member)
    persona, person = access.persona, access.person
    recordings = await persona_builder.load_recordings(db, persona.id)
    used = await _used_recordings(db, recordings)
    consents = await persona_consents(db, user.id, persona)
    data = summary(persona, person) | {
        "role": "owner",
        # 들어와 함께 쓰는 가족 수(쓰지 않은 링크는 세지 않는다)
        "members": await sharing.joined_count(db, persona.id),
        "gapCount": gap_count,
        # 만든 분이 확인할 것: 확인을 기다리는 가족 답 + 가족이 고쳐 보낸 대답(배지에 더한다)
        "gapWaiting": await gaps.waiting_count(db, persona.id),
        "basics": persona.basics,
        # 개편 뒤 대화 상대는 미리 채워 둔 답(조심할 이야기 기본 등급)도 실린다(확인만 하면 된다)
        "answers": intake.with_defaults(persona.basics, persona.answers),
        # 이 대화 상대가 꼭 답할 질문 id(개편 전·후·나 자신, 기본 정보가 대신한 질문은 뺀다)
        "requiredQuestions": intake.required_ids(persona.basics),
        # 이 대화 상대의 만들 때 질문(필수·문구·미리 채울 답을 맞춘 것)과 묶음. 화면은 이것으로
        # 그리고, 없으면 /intake/meta 로 그린다(intake.questions_for). 나 자신은 개편 전 묶음·차례.
        "questions": intake.questions_for(persona.basics),
        "sections": intake.sections_for(persona.basics),
        # 기억 더 채우기 질문(모두 선택, 문구를 이 대화 상대에 맞춘 것)과 묶음
        "moreQuestions": intake.more_questions_for(persona.basics),
        "moreModules": intake.more_modules_for(persona.basics),
        "recordings": [
            {
                "id": r.id,
                "originalName": r.original_name,
                "sizeBytes": r.size_bytes,
                "status": r.status,
                "error": r.error,
                "phoneBand": r.phone_band,
                # 뺄 수 있나(다 만든 뒤에는 아무것도 익히지 않은 녹음만)
                "removable": _removable(persona, r, used),
            }
            for r in recordings
        ],
        # 다 만들 때 목소리를 만들지 못한 까닭(목소리를 다시 만들면 사라진다)
        "voiceIssue": await _voice_issue(db, persona),
        "jobs": await job_states(db, persona.id),
        "consents": consents,
        # 목소리 만들기 직전에 보여 줄 확인 문서(그분 상태마다 다르다)
        "pledgeDoc": pledge_doc(persona),
        # 목소리 사용 동의를 받았나(없으면 '목소리 만들기' 때 묻는다). consents.voice 와 같다.
        "voiceConsented": consents["voice"],
        # 가져온 대화 상대에 붙일 목소리가 기다리는 중(주인이 확인하면 POST /voice 로 붙는다)
        "voicePending": await _voice_pending(db, persona),
        # 2026-10-22 법률 자문: 그분 동의 녹음 상태, 지금 할 수 있는 일(true = 됨)과 막힌 까닭,
        # 만든 분이 할 일 카드, 멈춤, 아이의 대화 상대 상태, 돌아가신 분의 유족 확인 여부
        "subjectConsent": await subject_consent.status_for(db, persona),
        "gates": await gate.summary(db, persona),
        "actions": await gate.actions(db, persona),
        "hold": holds.view(persona),
        "minor": await _minor_view(db, persona),
        "bereavedDeclared": await _bereaved_declared(db, persona),
    }
    if persona.origin == ORIGIN_IMPORT:
        # 가져온 종교·건강 기억을 대화에 쓰는가: {religion|health: 'on'|'off'|'none'}
        data["sensitiveMemories"] = await persona_import.sensitive_states(db, persona)
    if persona.setup_step in (SetupStep.SPEAKERS, SetupStep.QUESTIONS):
        data["voices"] = persona_builder.voice_overview(
            await persona_builder.load_voices(db, persona.id), recordings
        )
        data["subjectVoices"] = persona.subject_voices or []
    return Envelope(data=data)


@router.put("/personas/{persona_id}", response_model=Envelope[dict])
async def update_persona(persona_id: str, payload: PersonaUpdate, user: CurrentUser, db: DB):
    """입력한 정보(기본 정보·질문 답)를 고친다.

    대화는 만들 때 조립한 지침으로 하므로, 다 만든 페르소나면 지침과 기억을 뒤에서 다시 만든다
    (UPDATE 작업). 다 될 때까지는 예전 지침으로 대화한다. 돌아가셨는지 여부는 바로 바뀐다.
    관리자가 가져온 대화 상대는 기본 정보만 바꾸고 다시 만들지 않는다(_update_imported).
    """
    persona = await owned_persona(db, persona_id, user.id)
    if persona.origin == ORIGIN_IMPORT:
        return await _update_imported(db, persona, payload)
    if persona.setup_step == SetupStep.BUILDING:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "아직 만드는 중이에요. 다 만든 뒤에 고쳐 주세요."
        )
    for kind in ("UPDATE", "EXTEND"):
        if await jobs.is_running(db, persona.id, kind):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "앞서 고친 내용이나 새로 올린 녹음을 익히는 중이에요. 잠시 뒤 다시 저장해 주세요.",
            )
    ready = persona.setup_step == SetupStep.READY
    try:
        # 화면은 질문 차례(intakeVersion)를 보내지 않는다. 원래 값을 잇는다. 원래 basics 를 넘겨
        # 예전 상태(unable)·만 19세 미만 칸을 잇고 '나 자신'으로 바꾸기를 막는다(validate_basics)
        basics = intake.keep_version(
            intake.validate_basics(
                payload.basics.model_dump(by_alias=True), previous=persona.basics or {}
            ),
            persona.basics,
        )
        answers = persona.answers or {}
        if payload.answers is not None and persona.setup_step in (
            SetupStep.SPEAKERS,
            SetupStep.QUESTIONS,
            SetupStep.READY,
        ):
            # 필수 질문은 바꾼 기본 정보로 정한다(개편 전·후, 나 자신, 부르던 말 칸)
            answers = intake.validate_answers(payload.answers, final=ready, basics=basics)
        elif ready and intake.is_v2(basics):
            # 기본 정보만 고쳐도(예: 부르던 말을 비움) 개편 뒤 필수 답이 그대로 채워져 있어야 한다.
            # 예전 대화 상대는 지금처럼 보지 않는다.
            intake.check_required(answers, basics)
    except intake.IntakeError as error:
        raise _intake_error(error) from error
    # 살아 계신 분·나 자신을 돌아가신 분으로 바꾸면 새 고인 대화 상대처럼 유족 선언 전에는 녹음
    # 처리·목소리·만들기를 하지 않는다(게이트 #3 — 그분 동의 녹음을 피하지 못하게). 표시는 이어 간다
    basics = gate.mark_new_deceased(persona.basics, basics)
    await _check_subject_change(db, persona, basics)
    await _check_sensitive(db, user.id, persona, answers)
    answers_changed = answers != (persona.answers or {})
    changed = basics != persona.basics or answers_changed
    # 앞서 반영하다 실패했으면(AI 서버 오류·서버 재시작) 같은 답으로 다시 저장해도 다시 반영한다
    last = await jobs.latest(db, persona.id, "UPDATE")
    retry = last is not None and last.status == JobStatus.FAILED
    updating = ready and (changed or retry)
    if updating:
        # 다시 익히기는 해외 회사(OpenAI)로 글을 보낸다. 멈춘 대화 상대는 하지 않는다(409 code).
        # 만드는 중의 질문 답 저장(updating 아님)은 막지 않는다
        await gate.require(db, persona, "text")
        await _require_plan(db, user, persona, "고친 내용을 익힐")
        _limit_job(user, persona, "UPDATE")

    person = await db.get(Person, persona.person_id)
    kind = sharing.subject_kind(persona)
    person.name = basics["name"]
    person.relationship_label = basics["relation"]
    person.address_term = basics["addressTerm"]
    person.life_status = intake.LIFE_STATUS[basics["status"]]
    basis = basics.get("guardianBasis")
    if basis and basis != (persona.basics or {}).get("guardianBasis"):
        # 부모 양쪽 근거(guardianBasis)는 고칠 수 있지만 바꾼 기록을 남긴다(SPEC §6.2)
        legal.log(
            db,
            user.id,
            legal.persona_type("guardian_attest", persona.id),
            True,
            persona_id=persona.id,
            doc="guardian_consent",
            channel=f"basis_{basis}",
        )
    persona.name = basics["name"]
    persona.basics = basics
    persona.answers = answers
    if sharing.subject_kind(persona) != kind:
        # 그분 상태가 바뀌면 받을 가족 공유 확인이 달라진다. 거둬 두면 함께 쓰는 가족은 멈추고
        # (나눈 대화는 그대로), 주인이 다시 확인하면 이어서 이야기한다.
        await sharing.withdraw_owner_consents(db, user, persona.id, channel=sharing.STATUS_CHANNEL)
    await db.commit()

    if updating:
        await jobs.start(db, persona.id, "UPDATE", persona_builder.run_update)
    if ready and payload.answers is not None and answers_changed:
        # 질문 더 답하기. 몇 개에 답했는지만 남긴다(답 내용은 남기지 않는다).
        await analytics.track(
            db,
            "more_answers_saved",
            user_id=user.id,
            persona_id=persona.id,
            count=_more_answer_count(answers, basics),
            questionSet=SELF_MORE_QUESTION_SET if intake.is_self(basics) else MORE_QUESTION_SET,
        )
    return Envelope(data=summary(persona, person) | {"updating": updating})


# 그분 동의 녹음에서 그분이 원하지 않으신 대화 상대는 상태·관계를 바꿔 그 결과를 피하지 못하게 한다
SUBJECT_LOCKED = "이 대화 상대의 상태는 지금 바꿀 수 없어요. 고객센터로 알려 주세요."


async def _check_subject_change(db: DB, persona: Persona, basics: dict) -> None:
    """그분 상태·관계를 바꿀 때(SPEC §6.2): 그분 동의 녹음에서 원하지 않으신(refused) 대화
    상대는 바꾸지 못하게 하고(400), 그분 동의 녹음이 확인되기 전에 살아 계심 → 돌아가심으로 바꾸면
    운영 알림을 남긴다(id 없이 — 바꾸는 것은 막지 않는다. 그 뒤로는 유족 확인을 받는다)."""
    old = persona.basics or {}
    if (old.get("status"), old.get("relation")) == (basics.get("status"), basics.get("relation")):
        return
    refused = await db.scalar(
        select(SubjectConsentRecording.id)
        .where(
            SubjectConsentRecording.persona_id == persona.id,
            SubjectConsentRecording.status == "refused",
        )
        .limit(1)
    )
    if refused is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, SUBJECT_LOCKED)
    if gate.living_other(old) and basics.get("status") == "deceased":
        mode = subject_consent.mode_of(persona)
        if not await subject_consent.passed(db, persona, mode):
            notify.send(
                "SUBJECT_STATUS_CHANGED · 그분 동의 녹음이 확인되기 전에 살아 계신 분을 돌아가신 "
                "분으로 바꾼 대화 상대 1건",
                channel="alerts",
            )


async def _update_imported(db: DB, persona: Persona, payload: PersonaUpdate) -> Envelope[dict]:
    """가져온 대화 상대의 정보 고치기: 기본 정보(이름·관계·호칭·내 이름)만 바꾼다.

    지침은 손으로 다듬어 가져온 것이라 다시 만들지 않는다(UPDATE 작업을 걸지 않는다). 그래서
    질문 답은 받지 않는다(보낸 답이 지금과 다르면 409). 기본 정보를 바꿔도 지침 글은 그대로다.
    가져올 때와 같은 규칙으로 본다(persona_import.imported_basics): 돌아가신 분으로만 두고
    (지침의 안전 규칙·목소리 확인 문서가 그 기준), 관계는 목록에서(나 자신은 안 됨), 길이를
    넘으면 자르지 않는다.
    """
    if payload.answers is not None and payload.answers != (persona.answers or {}):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "가져온 대화 상대는 질문 답을 고칠 수 없어요. 기본 정보만 고칠 수 있어요.",
        )
    basics = persona_import.imported_basics(payload.basics.model_dump(by_alias=True))
    person = await db.get(Person, persona.person_id)
    person.name = basics["name"]
    person.relationship_label = basics["relation"]
    person.address_term = basics["addressTerm"]
    person.life_status = intake.LIFE_STATUS[basics["status"]]
    persona.name = basics["name"]
    persona.basics = basics
    await db.commit()
    return Envelope(data=summary(persona, person) | {"updating": False})


@router.delete("/personas/{persona_id}", response_model=Envelope[dict])
async def delete_persona(persona_id: str, user: CurrentUser, db: DB):
    """대화 상대를 지운다(녹음·대화·대화 음성·만든 목소리·그분 동의 녹음까지). 결제·동의 기록은
    남는다. 멈춘 대화 상대도 지금 바로 지울 수 있다.

    응답(SPEC §9.3): {deleted, doneAt, backupUntil(서버 백업이 지워지는 한국 날짜), vendorOk,
    message(화면에 보일 문장 — 실제로 지운 곳만 '지웠어요')}.
    """
    persona = await owned_persona(db, persona_id, user.id)
    message, _ = await persona_delete.claim(db, persona)
    if message:
        raise HTTPException(status.HTTP_409_CONFLICT, message)
    result = await persona_delete.remove(db, persona)
    # 처리방침 6번: 지운 일과 백업이 지워지는 날은 화면으로, 받을 수 있는 이메일이 있으면 메일로도
    mailer.send_user(user.email, "[TalkTo] 대화 상대를 지웠어요", result.message)
    return Envelope(data=deletion.response(result))


# ────────────────────────── 1. 녹음 올리기 ──────────────────────────


def _safe_suffix(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,5}", suffix) else ""


@router.post("/personas/{persona_id}/recordings", response_model=Envelope[list[dict]])
async def upload_recordings(
    persona_id: str,
    user: CurrentUser,
    db: DB,
    files: Annotated[list[UploadFile], File()],
    pledge: Annotated[str | None, Form()] = None,
):
    """녹음을 올린다. 처음 올릴 때 '녹음 올리기 전 확인'(pledge=true)을 받는다.

    파일은 메모리에 올리지 않고(몇백 MB 영상도 있다) 받아 둔 임시 파일에서 저장소로 바로 흘려
    보낸다. 다 올린 뒤 개수 확인과 저장만 대화 상대 줄을 잠근 채 짧게 한다: 올리는 몇 분 동안
    잠그면 그동안 이 대화 상대의 대화 보내기가 기다리고, 잠그지 않으면 동시에 올려 20개를 넘는다.
    """
    ratelimit.UPLOADS.check(f"{user.id}:recordings")
    persona = await owned_persona(db, persona_id, user.id)
    require_built_here(persona)
    # 다 만든 페르소나도 녹음을 더 받는다(기억·말투 더하기)
    require_step(persona, SetupStep.UPLOAD, SetupStep.READY)
    await _no_extend(db, persona)
    pledge_type = legal.persona_type("recording_pledge", persona.id)
    pledged = _truthy(pledge)
    if not pledged and not await legal.has(db, user.id, pledge_type):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "녹음을 올리기 전에 확인이 필요해요.")
    existing = await persona_builder.load_recordings(db, persona.id)
    if len(existing) + len(files) > MAX_RECORDINGS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"녹음은 {MAX_RECORDINGS}개까지 올릴 수 있어요."
        )
    limit = get_settings().max_upload_mb * 1024 * 1024
    plan: list[tuple[UploadFile, str, str, int]] = []
    for upload in files:
        name = Path(upload.filename or "recording").name[:200]
        suffix = _safe_suffix(name)
        if suffix not in audio.UPLOAD_SUFFIXES:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{name}: 이 파일은 올릴 수 없어요. "
                "통화 녹음·음성 메모·영상 파일만 올릴 수 있어요.",
            )
        size = _file_size(upload)
        if size > limit:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"{name}: 파일이 너무 커요 (최대 {get_settings().max_upload_mb}MB).",
            )
        if not size:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{name}: 빈 파일이에요.")
        plan.append((upload, name, suffix, size))
    # 읽기를 마쳤다. 저장소로 올리는 동안 DB 트랜잭션을 열어 두지 않는다.
    await db.commit()
    storage = get_storage()
    stored: list[str] = []
    rows: list[Recording] = []
    try:
        for upload, name, suffix, size in plan:
            rec_id = str(uuid4())
            key = persona_builder.recording_key(persona.id, rec_id, suffix)
            await asyncio.to_thread(storage.put_file, key, upload.file, upload.content_type)
            stored.append(key)
            rows.append(
                Recording(
                    id=rec_id,
                    persona_id=persona.id,
                    original_name=name,
                    storage_key=key,
                    content_type=upload.content_type,
                    size_bytes=size,
                )
            )
        await db.execute(select(Persona.id).where(Persona.id == persona.id).with_for_update())
        await db.refresh(persona)
        require_step(persona, SetupStep.UPLOAD, SetupStep.READY)
        await _no_extend(db, persona)
        count = await db.scalar(
            select(func.count(Recording.id)).where(Recording.persona_id == persona.id)
        )
        if (count or 0) + len(rows) > MAX_RECORDINGS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"녹음은 {MAX_RECORDINGS}개까지 올릴 수 있어요."
            )
        if pledged:
            await legal.record(db, user, pledge_type, True, persona_id=persona.id)
        db.add_all(rows)
        await db.commit()
    except BaseException:
        await db.rollback()
        # 표에 넣지 못한 파일은 저장소에도 남기지 않는다
        for key in stored:
            try:
                await asyncio.to_thread(storage.delete_prefix, key)
            except Exception as error:  # noqa: BLE001
                logger.warning("uploaded recording cleanup failed: %s", type(error).__name__)
        raise
    # 통화 녹음인지(phone)는 소리를 뽑을 때 알 수 있어 여기서는 모른다(None).
    # 녹음 종류는 voice_created{phone} 로 본다(그분 목소리가 주로 통화 녹음이면 true).
    await analytics.track(
        db,
        "recordings_uploaded",
        user_id=user.id,
        persona_id=persona.id,
        count=len(files),
        phone=None,
        more=persona.setup_step == SetupStep.READY,
    )
    return await _recording_list(db, persona)


async def _no_extend(db: DB, persona: Persona) -> None:
    if await jobs.is_running(db, persona.id, "EXTEND"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "새로 올린 녹음을 익히는 중이에요. 끝난 뒤에 더 올려 주세요.",
        )


async def _recording_list(db: DB, persona: Persona) -> Envelope[list[dict]]:
    recordings = await persona_builder.load_recordings(db, persona.id)
    used = await _used_recordings(db, recordings)
    return Envelope(
        data=[
            {
                "id": r.id,
                "originalName": r.original_name,
                "sizeBytes": r.size_bytes,
                "status": r.status,
                "removable": _removable(persona, r, used),
            }
            for r in recordings
        ]
    )


@router.delete(
    "/personas/{persona_id}/recordings/{recording_id}", response_model=Envelope[list[dict]]
)
async def delete_recording(persona_id: str, recording_id: str, user: CurrentUser, db: DB):
    persona = await owned_persona(db, persona_id, user.id)
    require_built_here(persona)
    require_step(persona, SetupStep.UPLOAD, SetupStep.READY)
    if await jobs.is_running(db, persona.id, "TRANSCRIBE") or await jobs.is_running(
        db, persona.id, "EXTEND"
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "녹음을 살펴보는 중에는 지울 수 없어요. 끝난 뒤에 지워 주세요.",
        )
    rec = await db.scalar(
        select(Recording)
        .where(Recording.id == recording_id)
        .options(selectinload(Recording.segments))
    )
    if rec is None or rec.persona_id != persona.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "녹음을 찾지 못했어요.")
    if not _removable(persona, rec, await _used_recordings(db, [rec])):
        # 이미 페르소나에 익힌 녹음은 빼지 않는다(기억·말투·목소리가 그 녹음으로 만들어졌다).
        # 그분 목소리가 없어 아무것도 익히지 않은 녹음은 뺄 수 있다(_removable).
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 익힌 녹음은 뺄 수 없어요.")
    key = rec.storage_key
    await db.delete(rec)
    await db.commit()
    await asyncio.to_thread(get_storage().delete_prefix, key)
    return await _recording_list(db, persona)


@router.post("/personas/{persona_id}/recordings/apply", response_model=Envelope[dict])
async def apply_recordings(persona_id: str, user: CurrentUser, db: DB):
    """다 만든 페르소나에 더 올린 녹음을 반영한다(새 녹음만 옮기고, 목소리를 다시 묶고,
    말투·기억을 더한다). 그동안에도 대화할 수 있다. 진행은 jobs.EXTEND 로 본다."""
    persona = await owned_persona(db, persona_id, user.id)
    require_built_here(persona)
    require_step(persona, SetupStep.READY)
    await gate.require(db, persona, "audio")
    await _require_plan(db, user, persona, "새 녹음을 익힐")
    pending = [
        r for r in await persona_builder.load_recordings(db, persona.id) if r.status == "UPLOADED"
    ]
    last = await jobs.latest(db, persona.id, "EXTEND")
    retry = last is not None and last.status == JobStatus.FAILED
    if not pending and not retry:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "새로 올린 녹음이 없어요.")
    for kind in ("UPDATE", "VOICE"):
        if await jobs.is_running(db, persona.id, kind):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "지금 하고 있는 일이 끝난 뒤에 새 녹음을 익힐 수 있어요. 잠시 뒤 다시 해 주세요.",
            )
    _limit_job(user, persona, "EXTEND")
    await jobs.start(db, persona.id, "EXTEND", persona_builder.run_extend)
    return Envelope(data={"started": True})


@router.post("/personas/{persona_id}/transcribe", response_model=Envelope[dict])
async def start_transcribe(persona_id: str, user: CurrentUser, db: DB):
    persona = await owned_persona(db, persona_id, user.id)
    require_built_here(persona)
    require_step(persona, SetupStep.UPLOAD)
    if not await persona_builder.load_recordings(db, persona.id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "녹음을 하나 이상 올려 주세요.")
    # 받아쓰기는 그분 녹음을 해외 회사로 보낸다. 살아 계신 분은 그분 동의 녹음이 확인된 뒤에
    # (녹음 올리기는 서울 저장만이라 그 전에도 된다)
    await gate.require(db, persona, "audio")
    _limit_job(user, persona, "TRANSCRIBE")
    await jobs.start(db, persona.id, "TRANSCRIBE", persona_builder.run_transcribe)
    return Envelope(data={"started": True})


# ────────────────────────── 2. 화자 고르기 ──────────────────────────


@router.post("/personas/{persona_id}/speakers", response_model=Envelope[dict])
async def choose_voices(persona_id: str, payload: VoiceChoice, user: CurrentUser, db: DB):
    """그분 목소리를 고른다. 한 사람이 둘로 나뉘어 묶였으면 여럿을 골라도 된다.

    살펴보는 중에 다시 골라도 된다(골랐다가 돌아와 다시 누르면 막혀 넘어가지 못했다, 2026-10-18).
    같은 목소리면 그대로 넘어가고, 다른 목소리면 돌고 있는 살펴보기가 알아채고 새 목소리로 다시
    한다(persona_builder._analyze). 대화 상대 줄을 잠가 그 확인과 엇갈리지 않게 한다.
    """
    persona = await owned_persona(db, persona_id, user.id)
    require_built_here(persona)
    require_step(persona, SetupStep.SPEAKERS, SetupStep.QUESTIONS)
    # 고르면 받아쓴 글을 살펴본다(해외 회사). 처리 게이트를 먼저 본다
    await gate.require(db, persona, "audio")
    # 다시 고를 때마다 녹음을 처음부터 다시 살펴본다(AI 비용). 마구 바꾸지 못하게 센다.
    _limit_job(user, persona, "SPEAKERS")
    await db.execute(select(Persona.id).where(Persona.id == persona.id).with_for_update())
    await db.refresh(persona)
    running = await jobs.is_running(db, persona.id, "ANALYZE")
    known = {v.voice_key for v in await persona_builder.load_voices(db, persona.id)}
    chosen = list(dict.fromkeys(payload.voice_keys))
    if not chosen:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "그분 목소리를 하나 이상 골라 주세요.")
    if any(key not in known for key in chosen):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "없는 목소리예요. 화면을 새로고침해 주세요."
        )
    recordings = await persona_builder.load_recordings(db, persona.id)
    changed = sorted(chosen) != sorted(persona.subject_voices or [])
    if changed:
        for rec in recordings:
            rec.analysis = None
    subject = set(chosen)
    # 녹음을 더 올리고 다시 묶은 뒤 같은 목소리를 골랐어도, 새 녹음은 아직 분석 전이다
    missing = any(
        r.status == "TRANSCRIBED"
        and r.analysis is None
        and any(persona_builder._is_subject(s, subject) for s in r.segments)
        for r in recordings
    )
    persona.subject_voices = chosen
    persona.setup_step = SetupStep.QUESTIONS
    await db.commit()
    await analytics.track(
        db, "speakers_chosen", user_id=user.id, persona_id=persona.id, count=len(chosen)
    )
    if running:
        # 돌고 있는 살펴보기가 이 목소리로 마저 한다(바뀌었으면 처음부터 다시, 같아도 빠진 녹음은
        # 끝내기 전에 마저 — persona_builder._analyze). 그 살펴보기는 끝을 적을 때까지 이 줄을 잡고
        # 있어서, 여기서 '돌고 있다'고 본 뒤에 끝나 빠뜨리는 틈은 없다. 그래도 남으면 만들기가
        # 빠진 것만 다시 살펴본다(run_build).
        return Envelope(data={"ok": True})
    last = await jobs.latest(db, persona.id, "ANALYZE")
    if changed or missing or last is None or last.status != JobStatus.DONE:
        await jobs.start(db, persona.id, "ANALYZE", persona_builder.run_analyze)
    return Envelope(data={"ok": True})


@router.post("/personas/{persona_id}/back-to-upload", response_model=Envelope[dict])
async def back_to_upload(persona_id: str, user: CurrentUser, db: DB):
    """녹음을 더 올리거나 빼러 돌아간다. 이미 옮긴 녹음은 다시 전사하지 않는다(새 녹음만).

    녹음이 바뀌면 목소리 묶음을 다시 만들므로 고른 목소리는 비운다. 적어 둔 답은 그대로 둔다.
    """
    persona = await owned_persona(db, persona_id, user.id)
    require_built_here(persona)
    require_step(persona, SetupStep.SPEAKERS, SetupStep.QUESTIONS)
    # 살펴보는 중이어도 돌아갈 수 있다. 고른 목소리를 비우면 돌고 있는 살펴보기가 알아채고 멈춘다
    await db.execute(select(Persona.id).where(Persona.id == persona.id).with_for_update())
    await db.refresh(persona)
    persona.setup_step = SetupStep.UPLOAD
    persona.subject_voices = []
    await db.commit()
    return Envelope(data={"ok": True})


@router.post("/personas/{persona_id}/back-to-speakers", response_model=Envelope[dict])
async def back_to_speakers(persona_id: str, user: CurrentUser, db: DB):
    persona = await owned_persona(db, persona_id, user.id)
    require_built_here(persona)
    require_step(persona, SetupStep.QUESTIONS)
    persona.setup_step = SetupStep.SPEAKERS
    await db.commit()
    return Envelope(data={"ok": True})


# ────────────────────────── 3. 질문 → 만들기 ──────────────────────────


@router.get("/personas/{persona_id}/phrase-suggestions", response_model=Envelope[list[str]])
async def phrase_suggestions(persona_id: str, user: CurrentUser, db: DB):
    persona = await owned_persona(db, persona_id, user.id)
    recordings = await persona_builder.load_recordings(db, persona.id)
    return Envelope(
        data=persona_builder.phrase_suggestions(recordings, set(persona.subject_voices or []))
    )


@router.put("/personas/{persona_id}/answers", response_model=Envelope[dict])
async def save_answers(persona_id: str, payload: dict, user: CurrentUser, db: DB):
    persona = await owned_persona(db, persona_id, user.id)
    require_built_here(persona)
    require_step(persona, SetupStep.SPEAKERS, SetupStep.QUESTIONS)
    answers = intake.validate_answers(payload, final=False, basics=persona.basics)
    await _check_sensitive(db, user.id, persona, answers)
    persona.answers = answers
    await db.commit()
    return Envelope(data={"ok": True})


@router.post("/personas/{persona_id}/build", response_model=Envelope[dict])
async def start_build(persona_id: str, payload: BuildRequest, user: CurrentUser, db: DB):
    persona = await owned_persona(db, persona_id, user.id)
    require_built_here(persona)
    require_step(persona, SetupStep.QUESTIONS, SetupStep.BUILDING)
    # 살아 계신 분은 그분 동의 녹음, 돌아가신 분은 신청 자격·유족 명단이 먼저다(409 code)
    await gate.require(db, persona, "build")
    try:
        # 필수 질문은 이 대화 상대의 기본 정보로 정한다(개편 전·후, 나 자신)
        answers = intake.validate_answers(payload.answers, final=True, basics=persona.basics)
    except intake.IntakeError as error:
        raise _intake_error(error) from error
    await _check_sensitive(db, user.id, persona, answers)
    _limit_job(user, persona, "BUILD")
    persona.answers = answers
    if payload.clone_voice:
        await _ensure_voice_consent(db, user, persona, payload.voice_consent)
    # 일을 시작하기 전에 단계부터 바꾼다. 일이 빨리 끝나 READY 로 바꾼 뒤에 여기서 덮어쓰면 안 된다.
    persona.setup_step = SetupStep.BUILDING
    await db.commit()
    # 녹음 분석이 실패한 채였다면 다시 돌린다. 만들기는 그게 끝나길 기다린다.
    last = await jobs.latest(db, persona.id, "ANALYZE")
    if last is not None and last.status == JobStatus.FAILED:
        await jobs.start(db, persona.id, "ANALYZE", persona_builder.run_analyze)
    await jobs.start(
        db, persona.id, "BUILD", persona_builder.run_build, {"cloneVoice": payload.clone_voice}
    )
    await analytics.track(
        db,
        "build_started",
        user_id=user.id,
        persona_id=persona.id,
        cloneVoice=payload.clone_voice,
    )
    return Envelope(data={"ok": True})


@router.post("/personas/{persona_id}/voice", response_model=Envelope[dict])
async def remake_voice(
    persona_id: str, user: CurrentUser, db: DB, payload: VoiceRequest | None = None
):
    """다 만든 페르소나의 목소리만 다시 복제한다. 진행은 jobs.VOICE 로 본다.

    목소리 사용 동의가 없는 페르소나(예전에 목소리 없이 만든 것)는 이때 동의를 받는다.
    예전 목소리(똑 닮은 목소리 포함)도 다시 만들 수 있다(새 목소리는 늘 Fish drama-3).
    관리자가 가져온 대화 상대는 새로 만들지 않고 기다리던 목소리를 붙인다(_attach_imported_voice).
    """
    persona = await owned_persona(db, persona_id, user.id)
    require_step(persona, SetupStep.READY)
    await gate.require(db, persona, "voice")
    if persona.origin == ORIGIN_IMPORT:
        return await _attach_imported_voice(db, user, persona, payload)
    await _require_plan(db, user, persona, "목소리를 다시 만들")
    # 만들 수 있는지부터 본다. 확인(동의)을 남긴 뒤에 409 를 내면 쓰이지 않은 확인 기록만 남는다.
    if not persona.subject_voices:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "그분 목소리를 고르지 않아서 목소리를 만들 수 없어요."
        )
    if await jobs.is_running(db, persona.id, "EXTEND"):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "지금 하고 있는 일이 끝난 뒤에 다시 해 주세요."
        )
    _limit_job(user, persona, "VOICE")
    await _ensure_voice_consent(db, user, persona, bool(payload and payload.voice_consent))
    await db.commit()
    await jobs.start(db, persona.id, "VOICE", persona_builder.run_voice)
    return Envelope(data={"ok": True})


async def _attach_imported_voice(
    db: DB, user, persona: Persona, payload: VoiceRequest | None
) -> Envelope[dict]:
    """가져온 대화 상대의 목소리 붙이기. 주인이 앱에서 직접 확인(pledge)할 때만 붙인다.

    관리자가 가져올 때 묶음의 목소리를 '대기'로 두었다(persona_import). 새로 만들지 않고 그 목소리를
    붙인다. voice_provider 는 비워 둔다: 지우거나 동의를 거둘 때 그 회사의 목소리를 지우지 않게
    (관리자가 따로 만든 목소리라 그쪽에서 정리한다). 대답 읽기는 목소리 id 앞글자로 회사를 고른다.
    화면은 VOICE 작업이 끝난(DONE) 것을 보고 다 됐다고 보인다.
    """
    record = await persona_import.import_record(db, persona.id)
    voice_id = persona_import.pending_voice(persona, record)
    if voice_id is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "이미 목소리가 있어요." if persona.voice_id else "붙일 수 있는 목소리가 없어요.",
        )
    if await jobs.is_running(db, persona.id, "VOICE"):
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 하고 있어요. 잠시만 기다려 주세요.")
    await _require_plan(db, user, persona, "목소리를 붙일")
    await _ensure_voice_consent(db, user, persona, bool(payload and payload.voice_consent))
    persona.voice_id, persona.voice_provider = voice_id, None
    persona_import.mark_voice_attached(record)
    db.add(
        PersonaJob(
            persona_id=persona.id,
            kind="VOICE",
            status=JobStatus.DONE,
            percent=100,
            detail="완료",
            options={"reason": persona_import.VOICE_REASON},
        )
    )
    await db.commit()
    return Envelope(data={"ok": True})


@router.post("/personas/{persona_id}/back-to-questions", response_model=Envelope[dict])
async def back_to_questions(persona_id: str, user: CurrentUser, db: DB):
    """만들기가 실패했을 때 답을 고치러 돌아간다."""
    persona = await owned_persona(db, persona_id, user.id)
    require_built_here(persona)
    require_step(persona, SetupStep.BUILDING)
    if await jobs.is_running(db, persona.id, "BUILD"):
        raise HTTPException(status.HTTP_409_CONFLICT, "아직 만드는 중이에요. 잠시만 기다려 주세요.")
    persona.setup_step = SetupStep.QUESTIONS
    await db.commit()
    return Envelope(data={"ok": True})


class PersonaConsentRequest(APIModel):
    type: Literal["sensitive_religion", "sensitive_health"]
    agreed: bool


@router.post("/personas/{persona_id}/consents", response_model=Envelope[dict])
async def persona_consent(
    persona_id: str, payload: PersonaConsentRequest, user: CurrentUser, db: DB
):
    """질문 더 답하기의 민감 범주(종교·건강) 동의. 선택이고 언제든 거둘 수 있다.

    거두면 그 범주 질문의 답과, 그 답으로 만든 기억(source=INTAKE)을 같은 commit 에 바로 지운다
    (처리방침: 동의를 거두면 지운다 — 다시 익히기를 기다리는 동안 대화에 실리지 않게). 다 만든 대화
    상대면 지침도 그 이야기 없이 다시 익힌다(UPDATE). 체험·구독이 끝났어도 다시 익힌다(지침에
    남은 민감 정보를 지우는 일이라).
    관리자가 가져온 대화 상대는 답이 없어 다시 익히지 않고, 그 분류의 가져온 기억을 끄거나 켠다.
    """
    persona = await owned_persona(db, persona_id, user.id)
    kind = legal.persona_type(payload.type, persona.id)
    if persona.origin == ORIGIN_IMPORT:
        category = payload.type.removeprefix("sensitive_")
        await legal.record(db, user, kind, payload.agreed, persona_id=persona.id)
        await persona_import.set_sensitive_memories(db, persona, category, payload.agreed)
        await db.commit()
        return Envelope(
            data={"consents": await persona_consents(db, user.id, persona), "updating": False}
        )
    updating = False
    if not payload.agreed:
        category = payload.type.removeprefix("sensitive_")
        keys = intake.sensitive_keys(category)
        answers = dict(persona.answers or {})
        removed = [k for k in keys if k in answers]
        if removed:
            if persona.setup_step == SetupStep.BUILDING:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "아직 만드는 중이에요. 다 만든 뒤에 다시 해 주세요."
                )
            for job_kind in ("UPDATE", "EXTEND"):
                if await jobs.is_running(db, persona.id, job_kind):
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        "지금 하고 있는 일이 끝난 뒤에 다시 해 주세요.",
                    )
            for key in removed:
                answers.pop(key)
            persona.answers = answers
            updating = persona.setup_step == SetupStep.READY
        # 그 범주 답으로 만든 기억은 답이 없어도(앞서 다시 익히기가 실패한 경우 등) 지운다
        titles = intake.sensitive_memory_titles(category)
        if titles:
            await db.execute(
                delete(Memory).where(
                    Memory.person_id == persona.person_id,
                    Memory.source == "INTAKE",
                    Memory.title.in_(titles),
                )
            )
        # 기억 인터뷰로 넣은 그 범주 답의 기록(관리자 화면의 '넣은 답')도 같은 commit 에 지운다
        sensitive_ids = intake.SENSITIVE.get(category, [])
        if sensitive_ids:
            await db.execute(
                delete(InterviewEntry).where(
                    InterviewEntry.persona_id == persona.id,
                    InterviewEntry.question_id.in_(sensitive_ids),
                )
            )
    if updating and await gate.block(db, persona, "text") is not None:
        # 멈춘 대화 상대는 다시 익히지 않는다(해외 회사로 보내지 않는다). 답·기억은 위에서 지웠다
        updating = False
    await legal.record(db, user, kind, payload.agreed, persona_id=persona.id)
    removal = None
    if not payload.agreed:
        # 지운 일시를 남기고 서버 백업이 지워지는 날을 알린다(commit 까지 한다)
        removal = await deletion.record(
            db,
            kind="sensitive_consent",
            user_id=user.id,
            persona_id=persona.id,
            requested_by="owner",
            vendor_ok=None,
        )
    await db.commit()
    if updating:
        await jobs.start(db, persona.id, "UPDATE", persona_builder.run_update)
    data = {"consents": await persona_consents(db, user.id, persona), "updating": updating}
    if removal is not None:
        data["deletion"] = deletion.response(removal)
        mailer.send_user(user.email, "[TalkTo] 동의를 거두고 적은 답을 지웠어요", removal.message)
    return Envelope(data=data)


# 지침·기억 보기(artifacts)는 고객에게 보이지 않는다.
# 관리자만 본다: GET /admin/personas/{id}/artifacts (admin.py)


# ────────────────────────── 4. 대화 ──────────────────────────


@router.post("/personas/{persona_id}/conversation", response_model=Envelope[dict])
async def persona_conversation(persona_id: str, user: CurrentUser, db: DB):
    """이 페르소나와 나눈 대화방. 없으면 만든다(한 사람당 하나 — 함께 쓰는 가족도 자기 방)."""
    access = await sharing.chat_access(db, persona_id, user)
    persona = access.persona
    require_step(persona, SetupStep.READY)
    conversation = await db.scalar(
        select(Conversation)
        .where(Conversation.persona_id == persona.id, Conversation.user_id == user.id)
        .order_by(Conversation.created_at.desc())
        .limit(1)
    )
    if conversation is None:
        # 가족의 방 이름은 그 가족이 부르는 말이다(성함은 대화 목록으로도 내보내지 않는다)
        title = (access.share.profile or {}).get("addressTerm") if access.member else persona.name
        conversation = Conversation(
            user_id=user.id, persona_id=persona.id, channel="TEXT", title=title
        )
        db.add(conversation)
        await db.commit()
    return Envelope(data={"conversationId": conversation.id})
