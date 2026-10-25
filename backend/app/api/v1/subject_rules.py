"""만 19세 미만 · 법정대리인 · 유족 확인(2026-10-22 법률 자문 · SPEC §6.3 · §6.4 · §7.2).

만든 분(owned_persona — 가족·모르는 사람은 같은 404)
  PUT  /personas/{id}/minor                    기존 살아 계신 분 대화 상대의 미성년 여부(한 번만)
  GET  /personas/{id}/guardian                 법정대리인 확인 상태
  POST /personas/{id}/guardian/consent         법정대리인 확인·동의(만 14세 미만은 동의 둘)
  POST /personas/{id}/guardian/sms             확인 문자 요청(manual 이면 TalkTo 가 손으로 보낸다)
  POST /personas/{id}/guardian/sms/verify      확인 번호 넣기
  GET · PUT /personas/{id}/bereaved            신청 자격과 유족 명단

오류는 {"detail": 글, "code": 코드}(gate.GateError) 또는 400 {"detail": 글}이다. 화면은 글을 그대로
보인다.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import Field

from app.api.deps import DB, CurrentUser
from app.api.v1.persona_setup import owned_persona
from app.schemas.common import APIModel, Envelope
from app.services import bereaved, holds, intake, legal, minors, sharing
from app.services.gate import GateError

router = APIRouter(tags=["SubjectRules"])

NOT_ALIVE = "살아 계신 다른 분의 대화 상대에서만 답해요."
NOT_DECEASED = "돌아가신 분의 대화 상대에서만 적어요."
NOT_MINOR = "법정대리인 확인은 만 19세 미만 아이의 대화 상대에서만 해요."
GUARDIAN_BOTH = "법정대리인 동의 두 가지에 모두 체크해 주세요."


class MinorAnswer(APIModel):
    minor: bool | None = None
    birth_ym: str | None = Field(default=None, max_length=7)
    guardian_basis: str | None = Field(default=None, max_length=20)
    guardian_attest: bool = False
    # 나는 그분의(그분이 아들·딸인데 예전에 비워 두었거나 직접 적은 경우에만 — 어머니·아버지면 고쳐
    # 저장하고, 그 밖이면 부모가 아니라고 답한 것으로 본다)
    my_relation: str | None = Field(default=None, max_length=20)


class GuardianConsent(APIModel):
    # 법정대리인(부모) 확인(만들 때 이미 했으면 다시 하지 않아도 된다)
    attest: bool = False
    # 만 14세 미만: 아이 정보 수집·이용과 해외 회사로 보내는 데 대한 법정대리인 동의
    consent: bool = False
    overseas: bool = False


class GuardianPhone(APIModel):
    phone: str = Field(max_length=20)


class GuardianCode(APIModel):
    code: str = Field(max_length=10)


def _now() -> datetime:
    return datetime.now(UTC)


def _bad(error: Exception) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(error))


def _guardian_error(error: minors.GuardianError) -> GateError:
    return GateError(error.status_code, error.code, error.detail)


# ────────────────────────── 만 19세 미만 ──────────────────────────


@router.put("/personas/{persona_id}/minor", response_model=Envelope[dict])
async def answer_minor(persona_id: str, payload: MinorAnswer, user: CurrentUser, db: DB):
    """이미 만든 살아 계신 분 대화 상대가 만 19세 미만인지(미성년 칸이 없을 때 한 번만, SPEC §6.4).

    기본 정보만 고치고 다시 익히지 않는다(다음 대화부터 아이 안전 규칙이 켜진다). 부모가 아닌 분이
    예전에 만든 아이면 400 을 내지 않고 사실대로 적어 둔다(notGuardian — 처리·목소리·새 초대는
    게이트가 막고 대화는 그대로, 관리자 목록으로 보고). 범위 밖 관계로 들어와 있는 가족은 멈춘다.

    notGuardian 은 한 번 서면 풀 수 없어 부모를 잘못 거기 넣지 않는다: 그분이 아들·딸인데 '나는
    그분의'가 비었거나 직접 적은 말이면 400 code minor_my_relation(어머니·아버지를 고르게)이고,
    myRelation 을 함께 보내면 어머니·아버지는 고쳐 저장하고 그 밖은 부모가 아니라고 답한 것으로
    본다.
    """
    persona = await owned_persona(db, persona_id, user.id)
    basics = dict(persona.basics or {})
    if basics.get("status") != "alive" or intake.is_self(basics):
        raise GateError(status.HTTP_409_CONFLICT, "not_required", NOT_ALIVE)
    if "minor" in basics:
        raise GateError(status.HTTP_409_CONFLICT, "minor_answered", intake.MINOR_FIXED)
    if payload.minor is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, intake.MINOR_REQUIRED)
    data = payload.model_dump(by_alias=True)
    relation = basics.get("relation")
    told = " ".join((payload.my_relation or "").split()) or None
    my_relation = told or basics.get("myRelation")
    try:
        if not payload.minor:
            fields: dict = {"minor": False}
        elif relation in intake.MINOR_RELATIONS and my_relation in intake.MINOR_MY_RELATIONS:
            fields = intake.minor_fields(data, relation, my_relation, creating=False)
            if told:
                fields["myRelation"] = told
        elif relation in intake.MINOR_RELATIONS and not told:
            # 부모일 수 있다: 먼저 '나는 그분의'를 고르게 한다(부모를 notGuardian 으로 잠그지 않게)
            raise GateError(
                status.HTTP_400_BAD_REQUEST, "minor_my_relation", intake.MINOR_MY_RELATION
            )
        else:
            birth, _ = intake.minor_birth(data.get("birthYm"))
            fields = {"minor": True, "birthYm": birth, "notGuardian": True}
    except intake.IntakeError as error:
        raise _bad(error) from error
    persona.basics = basics | fields
    paused = 0
    if fields["minor"]:
        # 나이 바뀜(minors.sweep)은 지켜보기 시작한 이때를 기준으로 한다
        await minors.check_row(db, persona.id, create=True)
        if not fields.get("notGuardian"):
            await minors.record_attest(db, user, persona)
        paused = await sharing.pause_minor_scope(db, persona)
    await db.commit()
    return Envelope(
        data={
            "minor": fields["minor"],
            "notGuardian": bool(fields.get("notGuardian")),
            "pausedMembers": paused,
            "guardian": await minors.guardian_view(db, persona),
        }
    )


# ────────────────────────── 법정대리인 ──────────────────────────


@router.get("/personas/{persona_id}/guardian", response_model=Envelope[dict])
async def guardian_status(persona_id: str, user: CurrentUser, db: DB):
    persona = await owned_persona(db, persona_id, user.id)
    return Envelope(data=await minors.guardian_view(db, persona))


@router.post("/personas/{persona_id}/guardian/consent", response_model=Envelope[dict])
async def guardian_consent(persona_id: str, payload: GuardianConsent, user: CurrentUser, db: DB):
    """법정대리인 확인(attest)과 만 14세 미만 동의 둘(consent · overseas — 둘 다여야)."""
    persona = await owned_persona(db, persona_id, user.id)
    found = await minors.state(db, persona)
    if found is None or found.mode == "adult":
        raise GateError(status.HTTP_409_CONFLICT, "not_needed", NOT_MINOR)
    if found.not_guardian:
        raise GateError(status.HTTP_409_CONFLICT, "not_guardian", minors.NOT_GUARDIAN_TEXT)
    if "attest" in found.missing and not payload.attest:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, intake.GUARDIAN_ATTEST_REQUIRED)
    child = found.mode == "minor_child"
    if child and not (payload.consent and payload.overseas):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, GUARDIAN_BOTH)
    if payload.attest:
        await minors.record_attest(db, user, persona)
    if child:
        for prefix in ("guardian_consent", "guardian_overseas"):
            await legal.record(
                db,
                user,
                legal.persona_type(prefix, persona.id),
                True,
                persona_id=persona.id,
                doc=minors.GUARDIAN_DOC,
            )
    await db.commit()
    return Envelope(data=await minors.guardian_view(db, persona))


@router.post("/personas/{persona_id}/guardian/sms", response_model=Envelope[dict])
async def guardian_sms(persona_id: str, payload: GuardianPhone, user: CurrentUser, db: DB):
    """확인 문자 요청. manual 이면 {queued: true}(TalkTo 가 보통 하루 안에 보낸다)."""
    persona = await owned_persona(db, persona_id, user.id)
    try:
        sent = await minors.request_code(db, persona, payload.phone, _now())
    except minors.GuardianError as error:
        await db.rollback()
        raise _guardian_error(error) from error
    await db.commit()
    return Envelope(data=sent)


@router.post("/personas/{persona_id}/guardian/sms/verify", response_model=Envelope[dict])
async def guardian_sms_verify(persona_id: str, payload: GuardianCode, user: CurrentUser, db: DB):
    persona = await owned_persona(db, persona_id, user.id)
    try:
        await minors.verify_code(db, user, persona, payload.code, _now())
    except minors.GuardianError as error:
        # 틀린 횟수는 남긴다(5번 넘으면 새로 받아야 한다)
        await db.commit()
        raise _guardian_error(error) from error
    await db.commit()
    return Envelope(data=await minors.guardian_view(db, persona))


# ────────────────────────── 유족 확인 ──────────────────────────


@router.get("/personas/{persona_id}/bereaved", response_model=Envelope[dict | None])
async def get_bereaved(persona_id: str, user: CurrentUser, db: DB):
    """적어 둔 신청 자격과 유족 명단(만든 분만). 아직 없으면 null."""
    persona = await owned_persona(db, persona_id, user.id)
    return Envelope(data=bereaved.view(await bereaved.get(db, persona.id)))


@router.put("/personas/{persona_id}/bereaved", response_model=Envelope[dict])
async def put_bereaved(persona_id: str, payload: dict, user: CurrentUser, db: DB):
    """신청 자격·유족 명단·확약(pledge_deceased). 돌아가신 분만, 멈춘 대화 상대는 고치지 못한다."""
    persona = await owned_persona(db, persona_id, user.id)
    basics = persona.basics or {}
    if basics.get("status") != "deceased" or intake.is_self(basics):
        raise GateError(status.HTTP_409_CONFLICT, "not_deceased", NOT_DECEASED)
    if persona.hold_reason:
        raise GateError(status.HTTP_409_CONFLICT, "stopped", holds.blocked_message(persona))
    try:
        row = await bereaved.save(db, user, persona, payload)
    except bereaved.BereavedError as error:
        raise _bad(error) from error
    await db.commit()
    return Envelope(data=bereaved.view(row))
