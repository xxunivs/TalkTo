"""내 동의. 계정 필수 동의는 /consents/accept 로 받고, 대화 상대별 동의는 그 화면의 API 가 받는다.

/consents/me·/consents/accept 는 동의 게이트 앞(AuthedUser)이라 동의 전에도 열린다.
"""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import DB, AuthedUser, CurrentUser
from app.api.v1.billing import persona_title
from app.db.models import Consent, Person, Persona, PersonaShare
from app.schemas.api import ConsentAccept, ConsentSave, ConsentSaved, ConsentView
from app.schemas.common import Envelope
from app.services import (
    analytics,
    deletion,
    interview,
    jobs,
    legal,
    mailer,
    persona_builder,
    voice_retrain,
)

router = APIRouter(prefix="/consents", tags=["Consents"])

# 여기서 거둘 수 있는 대화 상대별 종류(거둘 때 할 일이 아래에 정해진 것만).
# 나머지는 그 화면 API 로만 받는다: 민감 동의(이야기까지 지운다)·가족 공유(그만두기·나가기)·
# 그분 동의 녹음(그분이 거두면 관리자 이의 처리로 멈추고 지운다)·법정대리인·유족 확인·
# 결제마다 남기는 확인(recurring·withdrawal_limit). 여기서 '거둠'만 적으면 처리는 그대로인데
# 기록만 거둔 것이 된다.
WITHDRAWABLE_PREFIXES = (
    "voice_clone",
    "recording_pledge",
    "material_pledge",
    interview.USE_TYPE,
    interview.RECORDING_TYPE,
)


class ConsentWithdrawn(ConsentSaved):
    # 동의를 거두며 지운 것이 있으면 그 결과(deletion.response — 서버 백업에 남는 날짜와 문구)
    deletion: dict | None = None


@router.get("/me", response_model=Envelope[list[dict]])
async def my_consents(user: AuthedUser, db: DB):
    """지금 상태 줄(종류마다 한 줄). 대화 상대별 종류는 personaId·personaTitle 을 채운다."""
    rows = (await db.scalars(select(Consent).where(Consent.user_id == user.id))).all()
    order = {t: i for i, t in enumerate(legal.ACCOUNT_TYPES)}
    rows = sorted(
        rows,
        key=lambda r: (
            order.get(r.consent_type, len(order)),
            str(r.agreed_at or r.created_at)[:19],
        ),
    )
    titles: dict[str, str | None] = {}
    out = []
    for row in rows:
        prefix, persona_id = legal.split_type(row.consent_type)
        if persona_id is not None and persona_id not in titles:
            persona = await db.get(Persona, persona_id)
            person = await db.get(Person, persona.person_id) if persona else None
            owned = person is not None and person.owner_user_id == user.id
            titles[persona_id] = persona_title(persona, person) if owned else None
            if not owned and persona is not None and prefix == legal.SHARE_MEMBER_TYPE:
                # 가족이 나눠 준 대화 상대: 내가 그분을 부르는 말(자리가 아직 있을 때만)
                share = await db.scalar(
                    select(PersonaShare).where(
                        PersonaShare.persona_id == persona_id, PersonaShare.user_id == user.id
                    )
                )
                titles[persona_id] = (share.profile or {}).get("addressTerm") if share else None
        doc_key = (
            await legal.last_doc(db, user.id, row.consent_type)
            if prefix in ("voice_clone", "subject_consent")
            else None
        )
        out.append(
            {
                "type": row.consent_type,
                "title": legal.title_of(row.consent_type, doc_key),
                "agreed": row.agreed,
                "version": row.version,
                "agreedAt": row.agreed_at,
                "personaId": persona_id,
                "personaTitle": titles.get(persona_id) if persona_id else None,
            }
        )
    return Envelope(data=out)


@router.post("/accept", response_model=Envelope[dict])
async def accept(payload: ConsentAccept, user: AuthedUser, db: DB):
    """첫 로그인 동의 화면. 계정 필수 종류(아홉 가지)만 기록한다(모르는 종류는 무시).
    남은 필수 동의(consentsNeeded)를 돌려준다.

    Fish Audio 로 보내는 동의(overseas_fish)를 받고 필수 동의가 다 찼으면 그 사람의 예전 목소리를
    Fish 로 다시 만들기 시작한다(voice_retrain, 한 번에 하나 — 나머지는 10분마다 이어 간다).
    필수 동의가 빠져 있으면(게이트에 막혀 앱을 못 쓴다) 녹음을 보내지 않는다.
    """
    before = await legal.missing_required(db, user)
    for consent_type in dict.fromkeys(payload.types):
        if consent_type in legal.ACCOUNT_TYPES:
            await legal.record(db, user, consent_type, True)
    await db.commit()
    needed = await legal.missing_required(db, user)
    if before and not needed:
        await analytics.track(db, "consent_accepted", user_id=user.id, at="consent")
    if persona_builder.FISH_CONSENT in payload.types and not needed:
        await voice_retrain.sweep(db, owner_id=user.id)
    return Envelope(data={"consentsNeeded": needed})


@router.post("", response_model=Envelope[ConsentWithdrawn])
async def save_consent(payload: ConsentSave, user: CurrentUser, db: DB):
    """(예전 API) 계정 필수 동의 기록(아홉 가지 어느 것이든)과 대화 상대별 동의 거두기
    (WITHDRAWABLE_PREFIXES)만 받는다.
    /consents/accept 처럼 남은 필수 동의(consentsNeeded)도 싣는다.

    계정 필수 동의를 거두는 것은 회원 탈퇴로 한다. 대화 상대별 동의는 그 화면에서 받는다.
    목소리 만들기 확인(voice_clone)을 거두면 그 목소리를 지운다(대답 음성·회사의 목소리까지).
    글 대화는 그대로이고, 다시 확인하면 목소리를 새로 만들 수 있다. 지웠으면 지운 일시를 남기고
    응답 deletion 에 서버 백업에 남는 날짜와 문구를 싣는다(지운 것이 없으면 null).
    """
    consent_type = payload.consent_type
    removed: dict | None = None
    prefix, persona_id = legal.split_type(consent_type)
    if persona_id is None and consent_type in legal.ACCOUNT_TYPES:
        if not payload.agreed:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "필수 동의를 거두려면 회원 탈퇴를 해 주세요."
            )
        await legal.record(db, user, consent_type, True)
    elif persona_id is not None and prefix in WITHDRAWABLE_PREFIXES and not payload.agreed:
        persona = await db.get(Persona, persona_id)
        person = await db.get(Person, persona.person_id) if persona else None
        if person is None or person.owner_user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "대화 상대를 찾지 못했어요.")
        if prefix == "voice_clone":
            # 목소리를 만들거나 바꾸는 중이면 끝난 뒤에(그 작업이 새 목소리를 다시 넣지 않게)
            for kind in ("BUILD", "VOICE"):
                if await jobs.is_running(db, persona.id, kind):
                    raise HTTPException(
                        status.HTTP_409_CONFLICT, "지금 하고 있는 일이 끝난 뒤에 다시 해 주세요."
                    )
        # 목소리 만들기 확인은 그분 상태마다 문서가 다르다: 거둘 때도 동의할 때 보여 준 문서의
        # 판으로 남긴다
        doc = await legal.last_doc(db, user.id, consent_type) if prefix == "voice_clone" else None
        await legal.record(db, user, consent_type, False, persona_id=persona_id, doc=doc)
        if prefix == "voice_clone" and persona.voice_id:
            # remove_voice 가 돌려주는 목소리 회사 삭제 결과(True·False, 지울 것이 없으면 None)
            vendor_ok = await persona_builder.remove_voice(db, persona)
            result = await deletion.record(
                db,
                kind="voice_consent",
                user_id=user.id,
                persona_id=persona_id,
                requested_by="owner",
                vendor_ok=vendor_ok,
            )
            removed = deletion.response(result)
            # 처리방침 6번: 화면으로, 받을 수 있는 이메일이 있으면 메일로도
            mailer.send_user(user.email, "[TalkTo] 그분 목소리를 지웠어요", result.message)
        if prefix == interview.USE_TYPE:
            # 인터뷰 내용을 넣는 데 동의를 거두면 아직 끝나지 않은 신청도 취소한다(관리자도 더는
            # 넣지 못한다 — interview.require_interview_consent)
            request = await interview.open_request(db, persona.id)
            if request is not None:
                interview.set_status(request, "cancelled")
        if prefix == interview.RECORDING_TYPE:
            # 통화 녹음 동의를 거두면 관리자 화면(신청의 recordingOk)에도 바로 '동의 안 함'으로
            # 보인다(녹음하지 않게). 끝나지 않은 신청이 없으면 마지막 신청에 적는다.
            request = await interview.open_request(db, persona.id) or (
                await interview.latest_request(db, persona.id)
            )
            if request is not None:
                request.recording_ok = False
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "이 동의는 대화 상대 화면에서 해 주세요.")
    await db.commit()
    row = await db.scalar(
        select(Consent).where(Consent.user_id == user.id, Consent.consent_type == consent_type)
    )
    view = ConsentView.model_validate(row)
    return Envelope(
        data=ConsentWithdrawn(
            **view.model_dump(),
            consents_needed=await legal.missing_required(db, user),
            deletion=removed,
        )
    )
