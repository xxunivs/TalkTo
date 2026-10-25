"""대화할 때 넣을 기억 고르기. AI 레포 pipeline/s5_chat/a_chat.retrieve_memories 와 같은 기준.

질문을 벡터로 바꿔 기억 벡터와 견주고, 비슷한 것 상위 5개(유사도 0.18 이상)만 넣는다.
벡터가 없는 예전 기억만 있으면 최근 기억으로 대신한다. RDS 로 옮기면 pgvector 로 바꾼다.
"""

import asyncio
import logging
import math

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Memory
from app.services.ai import AIServerError, AIServerNotConfigured, ai_service

logger = logging.getLogger(__name__)

TOP_K = 5
MIN_SIMILARITY = 0.18
CANDIDATE_LIMIT = 500
FALLBACK_RECENT = 20


def _usable(person_id: str) -> tuple:
    """대화에 쓸 수 있는 그분 기억(꺼 둔 기억은 뺀다). 검색과 총수가 같은 범위를 본다."""
    return (Memory.person_id == person_id, Memory.status == "ACTIVE")


async def count_memories(db: AsyncSession, person_id: str) -> int:
    """대화에 쓸 수 있는 그분 기억 수(COUNT 한 번). AI 서버 memoryTotal — 기억이 적은 페르소나를
    가린다('기억이 적을 때' 지시·사전 판정 건너뜀). 검색은 그 가운데 상위 몇 개만 보낸다."""
    return int(
        await db.scalar(select(func.count()).select_from(Memory).where(*_usable(person_id))) or 0
    )


def cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


def best(vector: list[float], memories: list[Memory]) -> list[Memory]:
    """질문 벡터와 비슷한 기억 상위 TOP_K(유사도 MIN_SIMILARITY 이상). 벡터 없는 기억은 뺀다.

    질문 벡터의 길이는 한 번만 잰다(기억마다 다시 재지 않는다).
    """
    return best_scored(vector, memories)[0]


def best_scored(vector: list[float], memories: list[Memory]) -> tuple[list[Memory], float | None]:
    """best 와 같은 기억과 가장 높은 유사도(견줄 기억이 없으면 None)."""
    query_norm = math.sqrt(sum(a * a for a in vector))
    if not query_norm:
        return [], None

    def score(embedding: list[float]) -> float:
        if len(embedding) != len(vector):
            return 0.0
        dot = sum(a * b for a, b in zip(vector, embedding, strict=True))
        norm = math.sqrt(sum(b * b for b in embedding))
        return dot / (query_norm * norm) if norm else 0.0

    scored = sorted(
        ((score(m.embedding), m) for m in memories if m.embedding),
        key=lambda pair: -pair[0],
    )
    top = scored[0][0] if scored else None
    return [m for score_, m in scored[:TOP_K] if score_ >= MIN_SIMILARITY], top


async def search_memories(
    db: AsyncSession, person_id: str, query: str, history: list[dict[str, str]] | None = None
) -> list[Memory]:
    """history: 이번 말 앞의 대화(시간순). AI 서버가 질문을 다시 쓸 때만 쓴다(QUERY_REWRITE)."""
    return (await search_memories_scored(db, person_id, query, history=history))[0]


async def search_memories_scored(
    db: AsyncSession, person_id: str, query: str, history: list[dict[str, str]] | None = None
) -> tuple[list[Memory], float | None]:
    """search_memories 와 같은 기억과 가장 높은 유사도.

    유사도는 벡터로 견줬을 때만 있다(벡터 있는 기억이 없거나 질문 벡터를 못 만들어 최근 기억으로
    대신했으면 None). '더 알려 주실 기억'의 기억 검색이 빈 신호(gaps.low_recall)에 쓴다.
    """
    rows = list(
        (
            await db.scalars(
                select(Memory)
                .where(*_usable(person_id))
                .order_by(Memory.updated_at.desc())
                .limit(CANDIDATE_LIMIT)
            )
        ).all()
    )
    embedded = [m for m in rows if m.embedding]
    if not embedded:
        return rows[:FALLBACK_RECENT], None
    try:
        vector = await ai_service.embed_query(query, history=history)
    except (AIServerError, AIServerNotConfigured, httpx.HTTPError) as error:
        logger.warning("memory search fell back to recent memories: %s", type(error).__name__)
        return rows[:TOP_K], None
    # 기억이 수백 개면 견주는 데 수백 ms 가 걸린다. 그동안 다른 요청이 멈추지 않게 따로 돌린다.
    return await asyncio.to_thread(best_scored, vector, embedded)
