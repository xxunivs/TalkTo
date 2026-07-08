"""3-C 파생 산출물 — 전체 요약(LLM) + 태그 집계(코드).

요약: 녹음 1건 → 2~3문장 (S3 형식은 코드로 문장 수 검증).
태그: 3-A 기억들의 태그를 녹음 단위로 합집합 — 이미 통제 어휘라 재검증만.
"""

import json
import logging
import re

from app.providers.llm import create_openai_client

from app.core.config import Settings
from app.pipeline.enrichment.prompts import (
    SUMMARY_SYSTEM_PROMPT,
    build_summary_user_prompt,
)
from app.pipeline.memory_segments.service import _ALLOWED_TAGS
from app.schemas.transcript import TranscriptSegment

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"[.!?]+\s*")


def summarize_recording(
    segments: list[TranscriptSegment], *, settings: Settings
) -> str:
    if settings.openai_api_key is None:
        raise RuntimeError("OPENAI_API_KEY is required for summary")
    client = create_openai_client(settings)
    response = client.chat.completions.create(
        model=settings.openai_analysis_model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": build_summary_user_prompt(segments)},
        ],
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    return str(payload.get("summary") or "").strip()


def sentence_count(summary: str) -> int:
    return len([s for s in _SENTENCE_SPLIT.split(summary) if s.strip()])


def aggregate_tags(memory_result: dict, *, max_tags: int = 7) -> list[str]:
    """녹음의 주요 주제 태그 — 기억 전체 합집합이 아니라 빈도순 상위.

    Memories 필터는 그 녹음의 '대표 주제'가 필요하지, 한 기억만 스친 태그까지
    다 붙으면 필터가 무의미해진다. 빈도로 정렬해 상위 max_tags개만 남긴다.
    """
    from collections import Counter

    counts: Counter[str] = Counter()
    for memory in memory_result.get("memorySegments") or []:
        for tag in memory.get("tags") or []:
            if tag in _ALLOWED_TAGS:
                counts[tag] += 1
    # 빈도 내림차순, 동률은 어휘순 안정 정렬
    ranked = sorted(counts, key=lambda t: (-counts[t], t))
    return ranked[:max_tags]
