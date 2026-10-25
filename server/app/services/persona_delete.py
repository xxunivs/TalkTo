"""대화 상대 지우기. '이 대화 상대 지우기'와 회원 탈퇴가 같이 쓴다.

지우는 것: 사람(Person)과 그 아래 대화 상대·녹음·기억·대화·구독(외래 키로 함께), 저장소의
녹음·남긴 글·서류(personas/{id}/)와 대화 음성(conversations/{id}/), 목소리를 만든 회사에 둔
목소리(지금 목소리와, 예전 똑 닮은 목소리 기록에 남은 목소리). 결제 기록·대화 횟수 기록·동의
이력은 남긴다.

지운 일시는 deletion_records 에 남기고(내용 없이), 화면·메일에 보일 문구(서버 백업에 남는 날짜와
목소리 회사에서 지웠는지)를 돌려준다(app/services/deletion.py · SPEC §9).
"""

from __future__ import annotations

import asyncio

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Consent, Conversation, JobStatus, Person, Persona, Subscription
from app.services import billing, deletion, jobs
from app.services.persona_builder import (
    RETRAIN_REASON,
    VENDOR_PROVIDERS,
    delete_vendor_voice,
    pvc_leftovers,
    voice_in_use,
)
from app.services.storage import get_storage

# 대화 상대마다 도는 일. 예전 'PVC'(똑 닮은 목소리) 작업은 이제 걸지 않는다.
JOB_KINDS = ("TRANSCRIBE", "ANALYZE", "BUILD", "VOICE", "UPDATE", "EXTEND")
JOB_RUNNING = "지금 하고 있는 일이 끝난 뒤에 지울 수 있어요. 잠시 뒤 다시 해 주세요."
PAYING = "결제를 처리하고 있어요. 잠시 뒤 지워 주세요."
PAYMENT_PENDING = "결제 결과를 확인하고 있어요. 몇 분 뒤 지워 주세요."


async def blocker(db: AsyncSession, persona: Persona) -> str | None:
    """지금 지울 수 없는 까닭(고객에게 보일 글). 지울 수 있으면 None. 자물쇠는 잡지 않는다.

    뒤에서 도는 목소리 다시 만들기(voice_retrain, 고객이 건 일이 아니다)는 막지 않는다. 지우면 그
    작업은 바꾸기 저장에서 실패하고, 받은 새 목소리를 회사에서 지운다(persona_builder.run_voice).
    동의를 거두는 길이 탈퇴라서, 동의하자마자 걸린 다시 만들기가 탈퇴를 막으면 안 된다.
    """
    for kind in JOB_KINDS:
        job = await jobs.latest(db, persona.id, kind)
        if job is None or job.status != JobStatus.RUNNING:
            continue
        if kind == "VOICE" and (job.options or {}).get("reason") == RETRAIN_REASON:
            continue
        return JOB_RUNNING
    if await billing.pending_for_persona(db, persona.id):
        return PAYMENT_PENDING
    return None


async def claim(db: AsyncSession, persona: Persona) -> tuple[str | None, Subscription | None]:
    """지우기 전에 구독 자물쇠를 잡는다. 갱신·결제가 도는 중에 지우면 결제는 되고 구독만
    사라질 수 있어서다. (까닭, 잡은 구독) — 까닭이 있으면 자물쇠는 이미 풀었다."""
    message = await blocker(db, persona)
    if message:
        return message, None
    sub = await billing.get_subscription(db, persona.id)
    if sub is not None and not await billing._claim(db, sub):
        return PAYING, None
    if await billing.pending_for_persona(db, persona.id):
        if sub is not None:
            await billing._release(db, sub)
        return PAYMENT_PENDING, None
    return None, sub


async def delete_voices(voices: list[tuple[str | None, str | None]]) -> bool | None:
    """목소리를 만든 회사에서 지운다(이 대화 상대를 지운 commit 뒤에 부른다). 다 지웠으면 True,
    하나라도 못 지웠으면 False, 지울 목소리가 없었으면 None(회사에 둔 목소리가 아니거나, 다른 대화
    상대가 아직 쓰는 목소리라 남겨 둔 것은 지운 것이 아니다 — '지웠어요'라고 하지 않는다).

    delete_vendor_voice 가 True·False 를 돌려주기 전(예전 판, None)에는 지웠는지 알 수 없으므로
    '지웠어요'라고 하지 않게 None 으로 본다(TalkTo 서버에서 지운 것만 알린다).
    """
    done: set[str] = set()
    results = []
    for voice_id, provider in voices:
        if voice_id and voice_id not in done:
            done.add(voice_id)
            # 관리자가 같은 목소리를 넣은 다른 대화 상대가 있으면 회사에서 지우지 않는다
            kept = provider in VENDOR_PROVIDERS and await voice_in_use(voice_id)
            outcome = await delete_vendor_voice(voice_id, provider)
            if provider in VENDOR_PROVIDERS and not kept:
                results.append(outcome)
    if any(r is False for r in results):
        return False
    if results and all(r is True for r in results):
        return True
    return None


async def remove(
    db: AsyncSession,
    persona: Persona,
    *,
    requested_by: str = "owner",
    kind: str = "persona",
) -> deletion.DeletionResult:
    """대화 상대 하나를 지운다(부르는 쪽이 claim 을 마친 뒤). commit 한다.

    지운 일시를 남기고(kind = persona 또는 멈춘 까닭, requested_by = owner · admin · system) 화면에
    보일 문구를 돌려준다.
    """
    # 뒤에서 도는 다시 만들기가 목소리를 바꾸는 중이면 그 저장을 기다려 바뀐 목소리까지 지운다
    # (먼저 잡으면 그쪽 저장이 실패하고 새 목소리를 그쪽이 지운다)
    await db.refresh(persona, with_for_update=True)
    persona_id = persona.id
    vendor = [(persona.voice_id, persona.voice_provider)]
    # 예전 똑 닮은 목소리(PVC)와 그때 둔 처음 목소리도 회사에서 지운다(기록은 함께 지워진다)
    vendor += await pvc_leftovers(db, persona_id)
    conversation_ids = list(
        (
            await db.scalars(select(Conversation.id).where(Conversation.persona_id == persona_id))
        ).all()
    )
    # 이 대화 상대의 동의 '지금 상태' 줄도 지운다(이력 consent_records 는 남긴다)
    await db.execute(delete(Consent).where(Consent.consent_type.like(f"%:{persona_id}")))
    person = await db.get(Person, persona.person_id)
    owner_id = person.owner_user_id if person else None
    # 사람(Person)을 지우면 페르소나·녹음·기억·대화·구독이 같이 지워진다
    await db.delete(person)
    await db.commit()

    storage = get_storage()
    # 저장소 지우기는 따로 돌린다(파일이 많으면 오래 걸려 다른 요청이 멈추지 않게)
    await asyncio.to_thread(storage.delete_prefix, f"personas/{persona_id}")
    # 말로 보낸 음성·대답 음성
    for conversation_id in conversation_ids:
        await asyncio.to_thread(storage.delete_prefix, f"conversations/{conversation_id}")
    vendor_ok = await delete_voices(vendor)
    return await deletion.record(
        db,
        kind=kind,
        user_id=owner_id,
        persona_id=persona_id,
        requested_by=requested_by,
        vendor_ok=vendor_ok,
    )


async def owned_personas(db: AsyncSession, user_id: str) -> list[Persona]:
    return list(
        (
            await db.scalars(
                select(Persona)
                .join(Person, Person.id == Persona.person_id)
                .where(Person.owner_user_id == user_id)
                .order_by(Persona.created_at)
            )
        ).all()
    )
