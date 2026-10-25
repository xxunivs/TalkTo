"""오래 걸리는 일(전사·분석·만들기)을 요청과 따로 돌린다.

상태는 persona_jobs 표에 남기고, 화면이 몇 초마다 읽어 진행 상황을 보여준다. 지금은
API 서버 프로세스 안의 asyncio 작업으로 돈다(서버 한 대 전제). 서버를 여러 대로 늘리면
큐(SQS 등)로 옮긴다.
"""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JobStatus, Persona, PersonaJob
from app.db.session import SessionLocal
from app.services.ai import AI_DOWN, AI_TIMED_OUT, AI_TROUBLE, VOICE_DOWN, AIServerError

logger = logging.getLogger(__name__)

# progress(문구, 진행률 0~100). 진행률을 빼면 막대는 그대로 둔다.
Progress = Callable[..., Awaitable[None]]
JobFn = Callable[[AsyncSession, PersonaJob, Progress], Awaitable[None]]

_running: set[asyncio.Task] = set()

_HANGUL = re.compile(r"[가-힣]")
TIMED_OUT = AI_TIMED_OUT
INTERRUPTED = "잠시 문제가 생겨 멈췄어요. 다시 해 주세요."


def friendly_error(error: Exception) -> str:
    """화면에 보여 줄 한 문장. 오류 원문(AI 서버·라이브러리·경로)은 고객에게 보이지 않는다.

    우리가 고객에게 보이려고 쓴 문장(한국어 ValueError — 녹음 문제, 목소리가 짧음 등)만 그대로
    보여 준다. 나머지는 차분한 한 문장으로 바꾸고, 원문은 로그로만 남긴다(_run 이 남긴다).
    """
    if isinstance(error, ValueError) and not isinstance(
        error, (json.JSONDecodeError, UnicodeError)
    ):
        text = str(error).strip()
        if text and _HANGUL.search(text):
            return text
    if "Timeout" in type(error).__name__:
        return TIMED_OUT
    if isinstance(error, AIServerError):
        return str(error)
    return AI_TROUBLE


def next_percent(current: int | None, percent: float) -> int:
    """막대에 보일 진행률. 끝나기 전에는 99 를 넘기지 않고(100 은 끝났다는 뜻) 뒤로 가지 않는다."""
    return max(current or 0, min(99, max(0, round(percent))))


async def latest(db: AsyncSession, persona_id: str, kind: str) -> PersonaJob | None:
    return await db.scalar(
        select(PersonaJob)
        .where(PersonaJob.persona_id == persona_id, PersonaJob.kind == kind)
        .order_by(PersonaJob.created_at.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )


async def is_running(db: AsyncSession, persona_id: str, kind: str) -> bool:
    job = await latest(db, persona_id, kind)
    return job is not None and job.status == JobStatus.RUNNING


async def start(
    db: AsyncSession, persona_id: str, kind: str, fn: JobFn, options: dict | None = None
) -> PersonaJob:
    """작업 하나를 건다. 같은 일이 돌고 있으면 409.

    확인과 넣기 사이에 다른 요청(두 번 누름·두 창·뒤에서 도는 다시 만들기)이 끼어 같은 일이 둘 돌지
    않게, 대화 상대 줄을 잠근 채 보고 넣는다(넣은 commit 이 푼다). 둘이 돌면 목소리를 두 번 만들어
    하나가 목소리 회사에 남거나, 같은 녹음을 두 번 옮겨 비용이 두 배가 된다.
    """
    await db.execute(select(Persona.id).where(Persona.id == persona_id).with_for_update())
    if await is_running(db, persona_id, kind):
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 하고 있어요. 잠시만 기다려 주세요.")
    job = PersonaJob(persona_id=persona_id, kind=kind, detail="준비하는 중", options=options or {})
    db.add(job)
    await db.commit()
    task = asyncio.create_task(_run(job.id, fn))
    _running.add(task)
    task.add_done_callback(_running.discard)
    return job


async def _run(job_id: str, fn: JobFn) -> None:
    async with SessionLocal() as db:
        job = await db.get(PersonaJob, job_id)
        # 작업이 안에서 되돌리면(rollback) job 값을 다시 읽어야 해서 미리 잡아 둔다.
        kind = job.kind

        async def progress(detail: str, percent: float | None = None) -> None:
            job.detail = detail[:300]
            if percent is not None:
                job.percent = next_percent(job.percent, percent)
            await db.commit()

        try:
            try:
                await fn(db, job, progress)
                job.status = JobStatus.DONE
                job.percent = 100
                # 진행 문구("…하는 중 (1/2)")가 남지 않게. 작업이 직접 "완료 …"를 적었으면 둔다.
                if not (job.detail or "").startswith("완료"):
                    job.detail = "완료"
            except Exception as error:  # 실패는 화면에 보여주고 다시 누를 수 있게 둔다
                logger.exception("persona job %s (%s) failed", kind, job_id)
                await db.rollback()
                job = await db.get(PersonaJob, job_id)
                if job is None:
                    # 그사이 대화 상대를 지웠다(뒤에서 돌던 다시 만들기 등). 남길 곳이 없다
                    return
                job.status = JobStatus.FAILED
                job.error = friendly_error(error)
                # AI 회사·목소리 회사 문제는 따로 알렸다(같은 일을 두 번 알리지 않게)
                if job.error not in (AI_DOWN, VOICE_DOWN):
                    from app.services import notify

                    notify.error(f"{kind} 작업 실패", job.error)
            await db.commit()
        except Exception:  # noqa: BLE001 — 끝을 적다가 DB 가 끊겼다(장애 조치 등)
            # 그대로 두면 다시 켤 때까지 '하는 중'으로 남아 화면이 끝없이 기다리고 지우기도 막힌다
            logger.exception("persona job %s (%s): could not record the result", kind, job_id)
            await _force_fail(job_id)
            return
        if kind == "BUILD":
            await _track_build(db, job)


async def _force_fail(job_id: str) -> None:
    """새 연결로 작업을 실패로 적는다(다시 누를 수 있게). 이것도 실패하면 켤 때 정리한다."""
    try:
        async with SessionLocal() as db:
            await db.execute(
                update(PersonaJob)
                .where(PersonaJob.id == job_id, PersonaJob.status == JobStatus.RUNNING)
                .values(status=JobStatus.FAILED, error=INTERRUPTED)
            )
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("persona job %s: could not mark it failed", job_id)


async def _track_build(db: AsyncSession, job: PersonaJob) -> None:
    """만들기가 끝나면 통계에 남긴다(걸린 초). 실패해도 작업 결과는 그대로다."""
    from app.services import analytics

    try:
        started = job.created_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        seconds = max(0, int((datetime.now(UTC) - started).total_seconds()))
        done = job.status == JobStatus.DONE
        await analytics.track_persona(
            db, "build_done" if done else "build_failed", job.persona_id, seconds=seconds
        )
    except Exception:  # noqa: BLE001
        logger.warning("build analytics failed", exc_info=True)


async def fail_interrupted() -> None:
    """서버가 꺼지며 끊긴 작업은 다시 누를 수 있게 실패로 돌려둔다."""
    async with SessionLocal() as db:
        await db.execute(
            update(PersonaJob)
            .where(PersonaJob.status == JobStatus.RUNNING)
            .values(status=JobStatus.FAILED, error=INTERRUPTED)
        )
        await db.commit()


async def wait_all() -> None:
    """테스트·종료 때 돌고 있는 작업을 기다린다."""
    while _running:
        await asyncio.gather(*list(_running), return_exceptions=True)
