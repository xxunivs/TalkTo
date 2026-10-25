"""고객센터. 회원은 문의 방법과 문의 코드를 보고(GET /support), 관리자는 받은 문의 코드로 회원을
찾는다(GET /admin/support/lookup). 앱 안 문의 게시판은 두지 않는다(카카오톡 채널·이메일로 받는다).
"""

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import DB, AdminUser, AuthedUser
from app.api.v1.billing import persona_title
from app.db.models import Person, Persona, PersonaShare
from app.schemas.common import Envelope
from app.services import persona_import, support

router = APIRouter(prefix="/support", tags=["Support"])
admin_router = APIRouter(prefix="/admin/support", tags=["Admin"])


@router.get("", response_model=Envelope[dict])
async def support_info(user: AuthedUser):
    """문의 방법: {kakaoChannelUrl|null, hours, email|null, inquiryCode}.

    카카오톡 채널 주소가 비었으면(설정 KAKAO_CHANNEL_CHAT_URL) null 이라 화면이 그 단추를 숨긴다.
    동의 게이트 앞(AuthedUser)이라 필수 동의 전에도 열린다(막힌 분도 물어볼 수 있게).
    화면은 문의 코드 뒤에 대화 상대 짧은 코드와 앱 버전을 붙인다(support 모듈 설명)."""
    return Envelope(data=support.info(user))


async def _personas_of(db: DB, user_id: str) -> list[dict]:
    """그 회원이 만든 대화 상대와 함께 쓰는 대화 상대: [{id, code, title, role, step}].
    성함은 싣지 않는다(호칭·관계만)."""
    out = []
    owned = await db.execute(
        select(Persona, Person)
        .join(Person, Person.id == Persona.person_id)
        .where(Person.owner_user_id == user_id)
        .order_by(Persona.created_at, Persona.id)
    )
    for persona, person in owned:
        out.append(
            {
                "id": persona.id,
                "code": support.persona_code(persona.id),
                "title": persona_title(persona, person),
                "role": "owner",
                "step": persona.setup_step,
            }
        )
    shared = await db.execute(
        select(Persona, PersonaShare)
        .join(PersonaShare, PersonaShare.persona_id == Persona.id)
        .where(PersonaShare.user_id == user_id)
        .order_by(PersonaShare.joined_at, PersonaShare.id)
    )
    for persona, share in shared:
        out.append(
            {
                "id": persona.id,
                "code": support.persona_code(persona.id),
                "title": (share.profile or {}).get("addressTerm") or "그분",
                "role": "member",
                "step": persona.setup_step,
            }
        )
    return out


@admin_router.get("/lookup", response_model=Envelope[dict])
async def lookup(_: AdminUser, db: DB, q: str = Query(min_length=1, max_length=300)):
    """문의 코드로 회원 찾기. q 에는 코드만('K7M3-Q9XA', 소문자·붙여 쓴 것도 된다) 넣어도 되고,
    화면이 복사해 준 줄('문의 코드 … · 대화 상대 3F2A9C · 앱 …')을 통째로 넣어도 된다.

    돌려주는 것: {code, personaCode|null, users: [AdminUserView 칸 + personas[{id, code, title,
    role(owner|member), step, matched}]]}. 이메일은 가린 값만, 대화 상대는 성함 없이 호칭으로."""
    code, persona_code = support.parse(q)
    if code is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "문의 코드를 찾지 못했어요. 'K7M3-Q9XA' 같은 8자예요."
        )
    users = await support.find_users(db, code)
    info = await persona_import.account_info(db, users)
    found = []
    for user in users:
        personas = await _personas_of(db, user.id)
        for item in personas:
            item["matched"] = persona_code is not None and item["code"] == persona_code
        found.append(
            {
                "id": user.id,
                "name": user.name,
                "role": user.role,
                "status": user.status,
                "createdAt": user.created_at,
                "provider": (info[user.id]["providers"] or [None])[0],
                # 가져온 대화 상대 목록(importedPersonas)에는 성함이 있어 빼고 넣는다
                **{k: v for k, v in info[user.id].items() if k != "importedPersonas"},
                "personas": personas,
            }
        )
    return Envelope(data={"code": code, "personaCode": persona_code, "users": found})
