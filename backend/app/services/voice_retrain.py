"""예전 목소리(ElevenLabs·MiniMax·똑 닮은 목소리)로 만든 대화 상대를 Fish drama-3 로 다시 만든다.

다시 만들기는 '목소리 다시 만들기'(VOICE 작업, persona_builder.run_voice)를 그대로 쓴다
(reason=fish). 새 목소리를 다 만들 때까지 예전 목소리로 대답하고, 다 만들면 한 번에 바꾼 뒤 예전
회사의 목소리를 지운다. 녹음은 이미 있어서 다시 올리거나 다시 옮기지 않는다.

- 서버 전체에서 한 번에 하나만 건다(목소리 회사에 몰아서 보내지 않게, 하나씩 들어 볼 수 있게).
- 그분 녹음을 Fish Audio(미국)로 보내므로 주인이 overseas_fish 에 동의했어야 한다. 관리자는 TalkTo
  직원이라 동의 화면 없이 간다(persona_builder.fish_allowed).
- 주인이 필수 동의를 다 마쳤어야 한다(게이트에 막힌 분의 녹음은 보내지 않는다. 관리자는 빼고).
- 처리 게이트(app/services/gate.py)에 막힌 대화 상대는 보내지 않는다: 멈춤, 대화가 어려우신 분,
  법정대리인 확인 전, 그분 동의 녹음 통과 전(살아 계신 분). 관리자 대화 상대도 같다.
- 같은 대화 상대가 MAX_FAILURES 번 실패하면 더 걸지 않는다(관리자가 보고 손으로 다시 한다).
  목소리 회사 문제(VOICE_DOWN)와 서버를 다시 켜며 끊긴 것은 세지 않는다: 녹음 탓이 아니라서,
  충전·복구한 뒤 다시 걸어야 한다.
- 목소리 회사 문제(크레딧·키 등)가 풀리지 않았으면 잠시 걸지 않는다(실패 횟수만 쌓이지 않게).
- 관리자가 손으로 넣은 목소리(voice_provider 없음)는 다시 만들지 않는다(그 목소리를 고른 까닭이
  있다). 필요하면 관리자가 '목소리 다시 만들기'를 누른다.
- 기본은 꺼져 있다(VOICE_RETRAIN_ENABLED). 배포 뒤 몇 분 먼저 들어 보고 켠다.
- POST /consents/accept(동의하자마자 그 사람 것)와 main.periodic(10분마다)이 부른다.

예전 똑 닮은 목소리(PVC) 확인 서류도 여기서 지운다(drop_pvc_documents, 켤 때마다). 만들다 만
똑 닮은 목소리는 ElevenLabs 에서 지운다(drop_pvc_voices, 켠 뒤 한 번).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import (
    JobStatus,
    Person,
    Persona,
    PersonaJob,
    PremiumVoice,
    SetupStep,
    User,
    UserRole,
    UserStatus,
)
from app.db.session import SessionLocal
from app.services import ai, gate, jobs, legal, persona_builder, persona_delete
from app.services.storage import get_storage

logger = logging.getLogger(__name__)

REASON = persona_builder.RETRAIN_REASON
MAX_FAILURES = 3
# 목소리 회사 문제가 아직 풀리지 않았으면 이만큼은 새로 걸지 않는다
PAUSE_ON_TROUBLE = timedelta(hours=1)
# 예전 똑 닮은 목소리를 만들다 만 기록(서류가 남아 있을 수 있다)
OPEN_PVC = ("UPLOADING", "VERIFYING", "TRAINING", "FAILED")

_lock = asyncio.Lock()


def is_retrain(job: PersonaJob) -> bool:
    return (job.options or {}).get("reason") == REASON


async def _retrain_running(db: AsyncSession) -> bool:
    rows = await db.scalars(
        select(PersonaJob).where(PersonaJob.kind == "VOICE", PersonaJob.status == JobStatus.RUNNING)
    )
    return any(is_retrain(job) for job in rows)


# 실패로 세지 않는 까닭: 서버를 다시 켜며 끊김(배포 등), 목소리 회사 문제(크레딧·키·장애),
# AI 서버와 연결이 끊김(AI 서버를 다시 켬 — 녹음 탓이 아니다)
NOT_COUNTED = (jobs.INTERRUPTED, ai.VOICE_DOWN, ai.AI_UNREACHABLE)


async def failures(db: AsyncSession, persona_id: str) -> int:
    """이 대화 상대의 다시 만들기가 실패한 수. 녹음 탓이 아닌 실패(NOT_COUNTED)는 세지 않는다."""
    rows = await db.scalars(
        select(PersonaJob).where(
            PersonaJob.persona_id == persona_id,
            PersonaJob.kind == "VOICE",
            PersonaJob.status == JobStatus.FAILED,
        )
    )
    return sum(1 for job in rows if is_retrain(job) and job.error not in NOT_COUNTED)


async def eligible(db: AsyncSession, persona: Persona, owner: User | None) -> bool:
    """지금 다시 만들어도 되나(모두 맞아야 한다)."""
    if persona.setup_step != SetupStep.READY or not persona.voice_id:
        return False
    if persona.voice_provider in (persona_builder.FISH, None) or not persona.subject_voices:
        return False
    if persona.voice_id.startswith(ai.FISH_VOICE_PREFIX):  # 이미 Fish 목소리
        return False
    if owner is None or owner.status != UserStatus.ACTIVE:
        return False
    # 필수 동의가 하나라도 빠졌으면(앱도 못 쓴다) 녹음을 보내지 않는다. 관리자는 게이트가 없다.
    if owner.role != UserRole.ADMIN and await legal.missing_required(db, owner):
        return False
    # 처리 게이트(멈춤·대화가 어려우신 분·법정대리인·그분 동의 녹음)에 막혔으면 보내지 않는다
    if await gate.block(db, persona, "voice"):
        return False
    if not await persona_builder.fish_allowed(db, persona):
        return False
    # 목소리 만들기 확인(그분 상태마다 다른 문서)이 지금도 맞아야 한다
    if not await persona_builder.voice_consented(db, owner.id, persona):
        return False
    for kind in persona_delete.JOB_KINDS:
        if await jobs.is_running(db, persona.id, kind):
            return False
    return await failures(db, persona.id) < MAX_FAILURES


def _paused() -> bool:
    trouble = ai.voice_provider_trouble()
    if trouble is None:
        return False
    return datetime.now(UTC) - trouble["at"] < PAUSE_ON_TROUBLE


async def sweep(db: AsyncSession, owner_id: str | None = None) -> str | None:
    """다시 만들 대화 상대 하나에 VOICE 작업을 건다. 건 대화 상대 id(없으면 None).

    owner_id 를 주면 그 사람의 대화 상대만 본다(동의한 바로 그때). 이미 하나가 돌고 있으면 아무것도
    걸지 않는다(다음 periodic 이 잇는다). 무슨 일이 있어도 예외를 밖으로 내지 않는다.
    """
    if not get_settings().voice_retrain_enabled or _lock.locked():
        return None
    async with _lock:
        try:
            if _paused() or await _retrain_running(db):
                return None
            query = (
                select(Persona, User)
                .join(Person, Person.id == Persona.person_id)
                .join(User, User.id == Person.owner_user_id)
                .where(
                    Persona.setup_step == SetupStep.READY,
                    Persona.voice_id.is_not(None),
                    # 회사를 모르는 목소리(None, 관리자가 손으로 넣은 것)는 SQL 에서 빠진다
                    Persona.voice_provider != persona_builder.FISH,
                )
                .order_by(Persona.created_at)
            )
            if owner_id is not None:
                query = query.where(Person.owner_user_id == owner_id)
            for persona, owner in (await db.execute(query)).all():
                if await eligible(db, persona, owner):
                    await jobs.start(
                        db, persona.id, "VOICE", persona_builder.run_voice, {"reason": REASON}
                    )
                    logger.info("voice retrain started for persona %s", persona.id)
                    return persona.id
        except Exception:  # noqa: BLE001 — 다시 만들기 때문에 동의·결제 확인이 깨지면 안 된다
            logger.exception("voice retrain sweep failed")
            await db.rollback()
    return None


async def drop_pvc_documents() -> int:
    """예전 똑 닮은 목소리 확인 서류(personas/{id}/pvc_docs/)를 지운다. 지운 대화 상대 수.

    PVC 를 없애며 '확인 뒤·14일 뒤 지운다'던 정리 작업도 사라져서, 켤 때마다 남은 것을 지운다.
    서류 목록이 비었어도 만들다 만 기록(OPEN_PVC)은 폴더를 한 번 더 비운다(목록과 파일이 어긋난
    경우). 수만 남기고 내용·이름은 남기지 않는다.
    """
    async with SessionLocal() as db:
        rows = (await db.scalars(select(PremiumVoice))).all()
        storage = get_storage()
        dropped = 0
        for row in rows:
            if not row.document_keys and row.status not in OPEN_PVC:
                continue
            await asyncio.to_thread(storage.delete_prefix, f"personas/{row.persona_id}/pvc_docs/")
            if row.document_keys:
                row.document_keys = []
                dropped += 1
        await db.commit()
    if dropped:
        logger.info("removed premium voice documents for %s personas", dropped)
    return dropped


async def drop_pvc_voices(db: AsyncSession) -> int:
    """만들다 만 예전 똑 닮은 목소리(ElevenLabs PVC)를 ElevenLabs 에서 지운다. 지운 수.

    PVC 를 없애며 만들던 것(올린 녹음 30분~2시간이 ElevenLabs 에 있다)이 기록에 남았다. 대화
    상대가 지금 쓰는 PVC 는 그 목소리를 Fish 로 다시 만들 때 지운다(persona_builder.pvc_leftovers).
    켤 때 한 번, AI 서버가 뜬 뒤에 부른다(main.periodic). 지우지 못한 것은 다음에 켤 때 다시 한다.
    """
    rows = (
        await db.scalars(
            select(PremiumVoice).where(
                PremiumVoice.voice_id.is_not(None), PremiumVoice.status != "REMOVED"
            )
        )
    ).all()
    dropped = 0
    for row in rows:
        voice_id = row.voice_id
        current = await db.scalar(select(Persona.voice_id).where(Persona.id == row.persona_id))
        if current == voice_id or await persona_builder.voice_in_use(voice_id):
            continue  # 지금 쓰는 목소리 — 다시 만들 때 지운다
        try:
            await ai.ai_service.delete_voice(voice_id=voice_id, provider="elevenlabs")
        except Exception as error:  # noqa: BLE001 — 다음에 켤 때 다시 한다
            logger.warning("premium voice delete failed: %s", type(error).__name__)
            continue
        # 처음 목소리(previous_voice_id)는 pvc_leftovers 가 다시 만들 때·지울 때 본다
        row.voice_id = None
        row.detail = "똑 닮은 목소리를 없애며 정리했어요"
        await db.commit()  # 하나씩 저장한다(목소리 회사를 부르는 동안 쓰기를 열어 두지 않게)
        dropped += 1
    if dropped:
        logger.info("removed %s unfinished premium voices at the vendor", dropped)
    return dropped
