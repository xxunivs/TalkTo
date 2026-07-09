"""2단계 ④ 언어 스타일 — LLM 제안 + 코드 검증(근거 실존).

인물 분석과 같은 정책: 안전 항목이 아니므로 구조 위반 항목은 예외 대신 조용히
버린다(재현율보다 신뢰 가능한 것만 남기기). sentencePatterns는 관찰 일반화라
근거를 요구하지 않는다.
"""

import json
import logging

from app.core.config import Settings
from app.pipeline.analysis.linguistic_style_prompts import (
    LINGUISTIC_STYLE_SYSTEM_PROMPT,
    build_linguistic_style_user_prompt,
)
from app.providers.llm import create_openai_client
from app.schemas.context import SubjectContext
from app.schemas.transcript import TranscriptSegment

logger = logging.getLogger(__name__)


def run_linguistic_style_analysis(
    segments: list[TranscriptSegment],
    *,
    subject_context: SubjectContext | None,
    subject_speaker_label: str | None,
    settings: Settings,
) -> dict:

    client = create_openai_client(settings, "linguistic style analysis")
    response = client.chat.completions.create(
        model=settings.openai_analysis_model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": LINGUISTIC_STYLE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_linguistic_style_user_prompt(
                    segments, subject_context, subject_speaker_label
                ),
            },
        ],
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    return validate_linguistic_style_payload(payload, segments)


def _clean_source_ids(raw: object, valid_ids: set[int]) -> list[int] | None:
    if not isinstance(raw, list):
        return None
    cleaned = sorted({i for i in raw if isinstance(i, int) and i in valid_ids})
    return cleaned or None


def validate_linguistic_style_payload(
    payload: dict, segments: list[TranscriptSegment]
) -> dict:
    """근거 실존을 강제하고, 근거 없는 인용 항목은 버린다. 파생 없음."""
    valid_ids = {segment.segment_index for segment in segments}
    style = payload.get("linguisticStyle")
    if not isinstance(style, dict):
        style = {}
    dropped = 0

    recurring_phrases = []
    for item in style.get("recurringPhrases") or []:
        phrase = str((item or {}).get("phrase") or "").strip() if isinstance(item, dict) else ""
        ids = _clean_source_ids(item.get("sourceSegmentIds") if isinstance(item, dict) else None, valid_ids)
        if not phrase or ids is None:
            dropped += 1
            continue
        recurring_phrases.append({"phrase": phrase, "sourceSegmentIds": ids})

    address_terms = []
    for item in style.get("addressTerms") or []:
        if not isinstance(item, dict):
            dropped += 1
            continue
        person = str(item.get("person") or "").strip()
        term = str(item.get("term") or "").strip()
        ids = _clean_source_ids(item.get("sourceSegmentIds"), valid_ids)
        if not person or not term or ids is None:
            dropped += 1
            continue
        address_terms.append({"person": person, "term": term, "sourceSegmentIds": ids})

    sentence_patterns = [
        str(pattern).strip()
        for pattern in (style.get("sentencePatterns") or [])
        if str(pattern).strip()
    ]

    emotional_expressions = []
    for item in style.get("emotionalExpressions") or []:
        if not isinstance(item, dict):
            dropped += 1
            continue
        emotion = str(item.get("emotion") or "").strip()
        expression = str(item.get("expression") or "").strip()
        ids = _clean_source_ids(item.get("sourceSegmentIds"), valid_ids)
        if not emotion or not expression or ids is None:
            dropped += 1
            continue
        emotional_expressions.append(
            {"emotion": emotion, "expression": expression, "sourceSegmentIds": ids}
        )

    if dropped:
        logger.info("linguistic style dropped %d unsupported items", dropped)
    return {
        "linguisticStyle": {
            "recurringPhrases": recurring_phrases,
            "addressTerms": address_terms,
            "sentencePatterns": sentence_patterns,
            "emotionalExpressions": emotional_expressions,
        }
    }
