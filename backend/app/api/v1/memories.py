"""기억 직접 다루기(만들기·고치기·끄기). 관리자만 쓴다.

화면은 이 API 를 쓰지 않는다. 지침·기억(artifacts)은 고객에게 보이지 않는다는 원칙(admin.py
artifacts)에 맞춰 관리자 전용으로 둔다. 예전에는 누구나 길이 제한 없이 기억을 쌓을 수 있어 DB 가
끝없이 커지거나, AI 서버가 받지 않는 긴 기억 하나로 그 대화 상대의 대화가 모두 막힐 수 있었다.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser, require_admin
from app.db.models import Memory, Person
from app.schemas.api import MemoryCreate, MemoryUpdate, MemoryView
from app.schemas.common import Envelope

router = APIRouter(
    prefix="/memories", tags=["Memories"], dependencies=[Depends(require_admin)]
)
# 한 분에게 손으로 넣을 수 있는 기억 수
MAX_MANUAL_MEMORIES = 500


async def owned_memory(db: DB, memory_id: str, user_id: str) -> Memory:
    item = await db.scalar(
        select(Memory).join(Person).where(Memory.id == memory_id, Person.owner_user_id == user_id)
    )
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "기억을 찾지 못했어요.")
    return item


@router.get("", response_model=Envelope[list[MemoryView]])
async def list_memories(user: CurrentUser, db: DB, person_id: str | None = None):
    query = select(Memory).join(Person).where(Person.owner_user_id == user.id)
    if person_id:
        query = query.where(Memory.person_id == person_id)
    rows = (await db.scalars(query.order_by(Memory.updated_at.desc()))).all()
    return Envelope(data=[MemoryView.model_validate(x) for x in rows])


@router.post("", response_model=Envelope[MemoryView], status_code=201)
@router.post("/", response_model=Envelope[MemoryView], status_code=201, include_in_schema=False)
async def create_memory(payload: MemoryCreate, user: CurrentUser, db: DB):
    person = await db.scalar(
        select(Person).where(Person.id == payload.person_id, Person.owner_user_id == user.id)
    )
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "그분 정보를 찾지 못했어요.")
    manual = await db.scalar(
        select(func.count(Memory.id)).where(
            Memory.person_id == person.id, Memory.source == "MANUAL"
        )
    )
    if (manual or 0) >= MAX_MANUAL_MEMORIES:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"기억은 {MAX_MANUAL_MEMORIES}개까지 넣을 수 있어요."
        )
    item = Memory(created_by_user_id=user.id, **payload.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return Envelope(data=MemoryView.model_validate(item))


@router.get("/{memory_id}", response_model=Envelope[MemoryView])
async def memory_detail(memory_id: str, user: CurrentUser, db: DB):
    return Envelope(data=MemoryView.model_validate(await owned_memory(db, memory_id, user.id)))


@router.patch("/{memory_id}", response_model=Envelope[MemoryView])
async def update_memory(memory_id: str, payload: MemoryUpdate, user: CurrentUser, db: DB):
    item = await owned_memory(db, memory_id, user.id)
    # 비울 수 있는 칸은 시기(related_period)뿐이다. 다른 칸에 보낸 null 은 '바꾸지 않음'으로 본다.
    changes = {
        key: value
        for key, value in payload.model_dump(exclude_unset=True).items()
        if value is not None or key == "related_period"
    }
    for key, value in changes.items():
        setattr(item, key, value)
    if "body_markdown" in changes:
        # 예전 글의 벡터로 찾히지 않게 비운다(다시 만들 때까지는 찾히지 않는다)
        item.embedding = None
    item.embedding_status = "PENDING"
    await db.commit()
    await db.refresh(item)
    return Envelope(data=MemoryView.model_validate(item))


@router.post("/{memory_id}/deactivate", response_model=Envelope[MemoryView])
async def deactivate_memory(memory_id: str, user: CurrentUser, db: DB):
    item = await owned_memory(db, memory_id, user.id)
    item.status = "INACTIVE"
    await db.commit()
    await db.refresh(item)
    return Envelope(data=MemoryView.model_validate(item))


@router.post("/{memory_id}/reembed", response_model=Envelope[MemoryView], status_code=202)
async def reembed_memory(memory_id: str, user: CurrentUser, db: DB):
    item = await owned_memory(db, memory_id, user.id)
    item.embedding_status = "PENDING"
    await db.commit()
    await db.refresh(item)
    return Envelope(data=MemoryView.model_validate(item))
