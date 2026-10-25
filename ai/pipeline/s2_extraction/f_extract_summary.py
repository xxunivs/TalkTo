"""녹음 단위 파생 산출물 — 전체 요약(LLM) + 태그 집계(코드).

요약: 녹음 1건 → 2~3문장 (문장 수는 sentence_count 로 검증).
태그: 기억 조각들의 태그를 녹음 단위로 집계 — 이미 통제 어휘라 재검증만.
"""

import logging
import re

from infra.external_api.openai_client import analysis_client, analysis_completion, json_reply

from infra.setting import Settings
from pipeline.s2_extraction.e_extract_memory import _ALLOWED_TAGS
from infra.schema.transcript import TranscriptSegment

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"[.!?]+\s*")


def summarize_recording(
    segments: list[TranscriptSegment], *, settings: Settings
) -> str:
    client = analysis_client(settings, "summary")
    response = analysis_completion(
        client,
        "summary",
        model=settings.openai_analysis_model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": build_summary_user_prompt(segments)},
        ],
    )
    payload = json_reply(response, "summary")
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


# ===== 프롬프트 =====
SUMMARY_SYSTEM_PROMPT = """너는 가족 통화 녹음을 Archive 목록에 보여줄 짧은 요약으로 만드는 분석기다.

규칙:
1. 2~3문장으로. 이 통화가 전반적으로 무엇에 관한 내용인지 보여준다.
2. 전사문에서 확인되는 내용만 쓴다. 없는 내용을 지어내지 않는다.
3. 핵심 주제(음식·건강·근황·추억 등)를 중심으로. 사소한 잡담은 뺀다.
4. 민감한 내용(구체적 병명·병원비·가족 갈등·재산)은 요약에 직접 노출하지 않는다. "건강 이야기를 나눴다" 수준으로만 (Archive 목록은 다른 가족도 볼 수 있다).
5. 담백한 서술체. 과장·감정 수식 없이.

다음 JSON으로만 답한다:
{"summary": "2~3문장 요약"}"""


def build_summary_user_prompt(segments: list[TranscriptSegment]) -> str:
    transcript = "\n".join(
        (segment.corrected_text or segment.transcript_text) for segment in segments
    )
    return f"전사문:\n{transcript}"
