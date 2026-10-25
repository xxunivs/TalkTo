"""기억 인터뷰 신청(app/services/interview.py).

주인(만든 분)
  GET  /personas/{id}/interview          신청할 수 있는지·마지막 신청·미리 채울 이름과 관계
  POST /personas/{id}/interview          신청(동의 둘 — 인터뷰 내용 넣기 필수, 통화 녹음 선택)
  POST /personas/{id}/interview/cancel   아직 끝나지 않은 신청 취소
관리자
  GET   /admin/interviews?status=        신청 목록(전화번호 없이)
  GET   /admin/interviews/{rid}          신청 하나(전화번호 포함)
  PATCH /admin/interviews/{rid}          상태·메모
  GET   /admin/personas/{id}/interview   그 대화 상대의 신청·넣은 답·인터뷰 기억·지금 답·다시 만들기
  POST  /admin/personas/{id}/interview/answers     질문 번호로 들은 답 넣기(answers 에 합친다)
  DELETE /admin/personas/{id}/interview/answers/{eid}  넣은 답 빼기(그 답이 더한 부분만)
  POST  /admin/personas/{id}/interview/memories    기억 카드 넣기(INTERVIEW, 바로 임베딩)
  DELETE /admin/personas/{id}/interview/memories/{mid}
  POST  /admin/personas/{id}/rebuild     다시 만들기(UPDATE 작업)

함께 쓰는 가족·다른 사람에게 주인 경로는 404 다(대화 상대를 찾지 못했어요).
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import Field
from sqlalchemy import func, select

from app.api.deps import DB, AdminUser, CurrentUser
from app.api.v1.persona_setup import owned_persona
from app.core import ratelimit
from app.db.models import InterviewRequest, Person, Persona
from app.schemas.common import APIModel, Envelope, Meta
from app.services import gate, interview

router = APIRouter(tags=["Interview"])
admin_router = APIRouter(prefix="/admin", tags=["Admin"])


def _bad(error: interview.InterviewError, code: int = status.HTTP_400_BAD_REQUEST):
    return HTTPException(code, str(error))


class InterviewApply(APIModel):
    name: str = Field(max_length=40)
    relation: str = Field(max_length=40)
    phone: str = Field(max_length=30)
    days: list[str] = Field(max_length=7)
    time_text: str = Field(max_length=100)
    method: Literal["phone", "video"]
    # 인터뷰 내용을 이 대화 상대의 기억·말투에 넣는 데 동의(필수)
    agree_use: bool = False
    # 통화 녹음 동의(선택)
    agree_recording: bool = False


def _owner_view(persona: Persona, request: InterviewRequest | None) -> dict:
    reason = interview.blocked_reason(persona)
    open_ = request is not None and request.status in interview.OPEN
    shown = request is not None and request.wiped_at is None
    return {
        "available": reason is None and not open_,
        "reason": reason,
        "request": interview.view(request) if shown else None,
        "prefill": interview.prefill(persona),
        "days": list(interview.DAYS),
    }


@router.get("/personas/{persona_id}/interview", response_model=Envelope[dict])
async def interview_state(persona_id: str, user: CurrentUser, db: DB):
    persona = await owned_persona(db, persona_id, user.id)
    return Envelope(data=_owner_view(persona, await interview.latest_request(db, persona.id)))


@router.post("/personas/{persona_id}/interview", response_model=Envelope[dict], status_code=201)
async def apply_interview(persona_id: str, payload: InterviewApply, user: CurrentUser, db: DB):
    persona = await owned_persona(db, persona_id, user.id)
    ratelimit.INTERVIEW_REQUESTS.check(user.id)
    # 신청은 하루 3번까지(신청·취소를 되풀이해 운영 메일·알림을 쏟아내지 못하게). 받지 않은
    # 신청(잘못 적음·이미 신청함)은 세지 않는다.
    user_id = user.id
    ratelimit.INTERVIEW_APPLY_DAILY.check(user_id)
    try:
        request = await interview.apply(
            db,
            user,
            persona,
            name=payload.name,
            relation=payload.relation,
            phone=payload.phone,
            days=payload.days,
            time_text=payload.time_text,
            method=payload.method,
            agree_use=payload.agree_use,
            agree_recording=payload.agree_recording,
        )
    except interview.InterviewConflict as error:
        ratelimit.INTERVIEW_APPLY_DAILY.undo(user_id)
        raise _bad(error, status.HTTP_409_CONFLICT) from error
    except interview.InterviewError as error:
        ratelimit.INTERVIEW_APPLY_DAILY.undo(user_id)
        raise _bad(error) from error
    return Envelope(data=_owner_view(persona, request))


@router.post("/personas/{persona_id}/interview/cancel", response_model=Envelope[dict])
async def cancel_interview(persona_id: str, user: CurrentUser, db: DB):
    persona = await owned_persona(db, persona_id, user.id)
    ratelimit.INTERVIEW_REQUESTS.check(user.id)
    try:
        request = await interview.cancel(db, user, persona)
    except interview.InterviewError as error:
        raise _bad(error, status.HTTP_409_CONFLICT) from error
    return Envelope(data=_owner_view(persona, request))


# ────────────────────────── 관리자 ──────────────────────────


async def _persona_card(db: DB, persona: Persona) -> dict:
    """관리자 화면에 보일 대화 상대. 성함 대신 호칭·관계로 보인다(admin artifacts 와 같다)."""
    basics = persona.basics or {}
    person = await db.get(Person, persona.person_id)
    return {
        "id": persona.id,
        "title": (basics.get("addressTerm") or basics.get("relation") or "그분").strip(),
        "relation": basics.get("relation"),
        "status": basics.get("status"),
        "self": basics.get("relation") == "나 자신",
        "step": persona.setup_step,
        "origin": persona.origin,
        "ownerUserId": person.owner_user_id if person else None,
    }


async def _admin_persona(db: DB, persona_id: str) -> Persona:
    persona = await db.get(Persona, persona_id)
    if persona is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "대화 상대를 찾지 못했어요.")
    return persona


@admin_router.get("/interviews", response_model=Envelope[list[dict]])
async def list_interviews(
    _: AdminUser,
    db: DB,
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    """신청 목록(새 신청부터). 전화번호는 싣지 않는다(하나를 열어 본다)."""
    query = select(InterviewRequest)
    if status_filter:
        if status_filter not in interview.STATUSES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "모르는 상태예요.")
        query = query.where(InterviewRequest.status == status_filter)
    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = (
        await db.scalars(
            query.order_by(InterviewRequest.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).all()
    data = []
    for row in rows:
        persona = await db.get(Persona, row.persona_id)
        item = interview.view(row, contact=False)
        item["persona"] = await _persona_card(db, persona) if persona else None
        data.append(item)
    return Envelope(data=data, meta=Meta(page=page, limit=limit, total=total))


async def _request_detail(db: DB, request: InterviewRequest) -> dict:
    persona = await db.get(Persona, request.persona_id)
    out = interview.view(request) | {"adminNote": request.admin_note}
    out["persona"] = await _persona_card(db, persona) if persona else None
    return out


@admin_router.get("/interviews/{request_id}", response_model=Envelope[dict])
async def interview_detail(request_id: str, _: AdminUser, db: DB):
    request = await db.get(InterviewRequest, request_id)
    if request is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "신청을 찾지 못했어요.")
    return Envelope(data=await _request_detail(db, request))


class InterviewUpdate(APIModel):
    status: Literal["requested", "scheduled", "done", "cancelled"] | None = None
    # 빈 글이면 메모를 지운다. 보내지 않으면 그대로 둔다.
    admin_note: str | None = Field(default=None, max_length=interview.NOTE_MAX)


@admin_router.patch("/interviews/{request_id}", response_model=Envelope[dict])
async def update_interview(request_id: str, payload: InterviewUpdate, _: AdminUser, db: DB):
    request = await db.get(InterviewRequest, request_id)
    if request is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "신청을 찾지 못했어요.")
    fields = payload.model_fields_set
    if payload.status is not None:
        if payload.status in interview.OPEN and request.status in interview.CLOSED:
            if request.wiped_at is not None:
                # 이름·연락처를 지운 신청은 연락할 수 없고, 다시 열면 만든 분이 새로 신청하지
                # 못한다(만든 분 화면에는 보이지 않는 열린 신청이 남는다)
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "연락처를 지운 신청은 다시 열 수 없어요."
                )
            # 다시 열 때 같은 대화 상대에 열린 신청이 또 있으면 안 된다
            other = await interview.open_request(db, request.persona_id)
            if other is not None and other.id != request.id:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "이 대화 상대에 아직 끝나지 않은 다른 신청이 있어요."
                )
        interview.set_status(request, payload.status)
    if "admin_note" in fields:
        request.admin_note = (payload.admin_note or "").strip() or None
    await db.commit()
    return Envelope(data=await _request_detail(db, request))


@admin_router.get("/personas/{persona_id}/interview", response_model=Envelope[dict])
async def persona_interview(persona_id: str, _: AdminUser, db: DB):
    """들은 내용을 넣는 화면: 신청들·넣은 답·인터뷰 기억·지금 답·다시 만들기 상태.

    질문 목록은 화면이 /intake/meta 로 읽는다(서버의 질문 구조 그대로).
    """
    persona = await _admin_persona(db, persona_id)
    requests = (
        await db.scalars(
            select(InterviewRequest)
            .where(InterviewRequest.persona_id == persona.id)
            .order_by(InterviewRequest.created_at.desc())
        )
    ).all()
    titles = {qid: q.get("title") for qid, q in interview.questions().items()}
    return Envelope(
        data={
            "persona": await _persona_card(db, persona),
            "requests": [interview.view(r, contact=False) for r in requests],
            "entries": [
                {
                    "id": e.id,
                    "requestId": e.request_id,
                    "questionId": e.question_id,
                    "questionTitle": titles.get(e.question_id),
                    "text": e.text,
                    "source": e.source,
                    "createdAt": e.created_at,
                }
                for e in await interview.entries(db, persona.id)
            ],
            "memories": [
                {"id": m.id, "title": m.title, "text": m.body_markdown, "createdAt": m.created_at}
                for m in await interview.memories(db, persona)
            ],
            "answers": persona.answers or {},
            "rebuild": await interview.job_state(db, persona.id),
            # 만든 분의 동의: 들은 내용을 넣어도 되는지(interviewUse), 종교·건강 기억 카드를
            # 넣어도 되는지(sensitiveReligion·sensitiveHealth)
            "consents": await interview.consent_state(db, persona),
        }
    )


class InterviewAnswer(APIModel):
    question_id: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=interview.ENTRY_MAX)
    # 지금 답을 버리고 들은 답으로 바꾼다(기본은 긴 글·목록에 덧붙인다)
    replace: bool = False
    request_id: str | None = Field(default=None, max_length=36)


@admin_router.post(
    "/personas/{persona_id}/interview/answers", response_model=Envelope[dict], status_code=201
)
async def add_interview_answer(persona_id: str, payload: InterviewAnswer, _: AdminUser, db: DB):
    persona = await _admin_persona(db, persona_id)
    try:
        entry = await interview.add_answer(
            db,
            persona,
            payload.question_id,
            payload.text,
            replace=payload.replace,
            request_id=payload.request_id,
        )
    except interview.InterviewConflict as error:
        raise _bad(error, status.HTTP_409_CONFLICT) from error
    except interview.InterviewError as error:
        raise _bad(error) from error
    return Envelope(
        data={
            "entryId": entry.id,
            "questionId": entry.question_id,
            "answer": (persona.answers or {}).get(entry.question_id),
        }
    )


@admin_router.delete(
    "/personas/{persona_id}/interview/answers/{entry_id}", response_model=Envelope[dict]
)
async def remove_interview_answer(persona_id: str, entry_id: str, _: AdminUser, db: DB):
    """넣은 답 기록 하나를 지우고 그 답이 더한 부분만 answers 에서 뺀다(만든 분이 빼 달라고 할 때).

    지침·질문 답 기억에는 '다시 만들기'로 반영한다. 만들거나 다시 만드는 중이면 409.
    """
    persona = await _admin_persona(db, persona_id)
    try:
        removed = await interview.remove_answer(db, persona, entry_id)
    except interview.InterviewNotFound as error:
        raise _bad(error, status.HTTP_404_NOT_FOUND) from error
    except interview.InterviewError as error:
        raise _bad(error, status.HTTP_409_CONFLICT) from error
    return Envelope(data={"deleted": True, **removed})


class InterviewMemory(APIModel):
    text: str = Field(min_length=1, max_length=interview.MEMORY_MAX)
    title: str | None = Field(default=None, max_length=60)
    # 먼저 꺼내지 않고 물을 때만 말하는 기억('물으면만')
    asked_only: bool = False


@admin_router.post(
    "/personas/{persona_id}/interview/memories", response_model=Envelope[dict], status_code=201
)
async def add_interview_memory(persona_id: str, payload: InterviewMemory, _: AdminUser, db: DB):
    persona = await _admin_persona(db, persona_id)
    try:
        memory = await interview.add_memory(
            db, persona, text=payload.text, title=payload.title, asked_only=payload.asked_only
        )
    except interview.InterviewConflict as error:
        raise _bad(error, status.HTTP_409_CONFLICT) from error
    except interview.InterviewError as error:
        raise _bad(error) from error
    return Envelope(data={"id": memory.id, "title": memory.title, "text": memory.body_markdown})


@admin_router.delete(
    "/personas/{persona_id}/interview/memories/{memory_id}", response_model=Envelope[dict]
)
async def remove_interview_memory(persona_id: str, memory_id: str, _: AdminUser, db: DB):
    persona = await _admin_persona(db, persona_id)
    try:
        await interview.remove_memory(db, persona, memory_id)
    except interview.InterviewError as error:
        raise _bad(error, status.HTTP_404_NOT_FOUND) from error
    return Envelope(data={"deleted": True})


@admin_router.post("/personas/{persona_id}/rebuild", response_model=Envelope[dict])
async def rebuild_persona(persona_id: str, _: AdminUser, db: DB):
    """다시 만들기: 지금 답으로 지침·질문 답 기억을 다시 만든다(UPDATE 작업, 뒤에서 돈다).
    멈춘 대화 상대는 다시 만들지 않는다(처리 게이트 — 409 code)."""
    persona = await _admin_persona(db, persona_id)
    await gate.require(db, persona, "text")
    try:
        await interview.rebuild(db, persona)
    except interview.InterviewError as error:
        raise _bad(error, status.HTTP_409_CONFLICT) from error
    return Envelope(data={"rebuild": await interview.job_state(db, persona.id)})
