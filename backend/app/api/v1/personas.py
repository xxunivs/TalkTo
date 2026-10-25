from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, update

from app.api.deps import DB, AdminUser, CurrentUser
from app.db.models import Person, Persona
from app.schemas.api import ActivePersonaView, PersonaUpdate, PersonaView
from app.schemas.common import Envelope
from app.services import persona_import

router = APIRouter(tags=["Personas"])


@router.get("/personas/active", response_model=Envelope[ActivePersonaView])
async def active_persona(user: CurrentUser, db: DB):
    persona = await db.scalar(
        select(Persona)
        .join(Person)
        .where(Person.owner_user_id == user.id, Persona.is_active.is_(True))
    )
    if persona is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "대화 상대를 찾지 못했어요.")
    return Envelope(data=ActivePersonaView.model_validate(persona))


@router.patch("/admin/personas/{persona_id}", response_model=Envelope[PersonaView])
async def update_persona(persona_id: str, payload: PersonaUpdate, admin: AdminUser, db: DB):
    persona = await db.get(Persona, persona_id)
    if persona is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "대화 상대를 찾지 못했어요.")
    values = payload.model_dump(exclude_unset=True)
    if values.get("is_active"):
        # 대화 상대는 주인마다 하나만 켠다. 다른 사람의 대화 상대는 건드리지 않는다.
        person = await db.get(Person, persona.person_id)
        owned = select(Person.id).where(Person.owner_user_id == person.owner_user_id)
        await db.execute(
            update(Persona).where(Persona.person_id.in_(owned)).values(is_active=False)
        )
    if "voice_id" in values and values["voice_id"] != persona.voice_id:
        # 손으로 넣은 목소리는 어느 회사 것인지 모른다. 예전 회사 이름이 남아 있으면 다시 만들기·
        # 지우기가 그 회사에서 이 목소리를 지울 수 있어 비운다(persona_builder.VENDOR_PROVIDERS).
        persona.voice_provider = None
    if "voice_id" in values and not values["voice_id"]:
        # 가져온 대화 상대면 기다리던 목소리도 거둔다(주인이 다시 확인해도 같은 목소리가 붙지 않게)
        await persona_import.cancel_voice(db, persona, admin.id)
    for key, value in values.items():
        setattr(persona, key, value)
    await db.commit()
    await db.refresh(persona)
    return Envelope(data=PersonaView.model_validate(persona))
