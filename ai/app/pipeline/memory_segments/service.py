"""3-A 기억 조각 추출 — LLM 제안 + 코드 검증/파생.

LLM은 memoryText·근거·인물·확실성만 제안한다. 시간 구간(startMs/endMs)과
화자 라벨은 근거 세그먼트에서 코드가 파생하고(LLM이 지어낼 수 없게),
민감플래그는 ⑤ 분석 결과와 근거 구간 교집합으로 코드가 조인한다.
"""

import json
import logging
import re
import unicodedata

from app.providers.llm import create_openai_client

from app.core.config import Settings
from app.pipeline.analysis.persons import run_persons_analysis
from app.pipeline.analysis.sensitivity import run_sensitivity_analysis
from app.pipeline.memory_segments.prompts import (
    MEMORY_SYSTEM_PROMPT,
    build_memory_user_prompt,
)
from app.schemas.context import SubjectContext
from app.schemas.memory import (
    MemorySegment,
    MemorySegmentExtractionRequest,
    MemorySegmentExtractionResponse,
)
from app.schemas.transcript import TranscriptSegment

logger = logging.getLogger(__name__)

_CONFIDENCE_VALUES = {"confirmed", "inferred"}
# 허용 문자: 한글(완성형·자모), 영숫자, 공백, 기본 문장부호 — 그 외 스크립트가
# 섞이면 LLM 출력 오염(실측: 벵골어 혼입)으로 보고 그 기억을 버린다
_FOREIGN_CHARS = re.compile(r"[^가-힣ㄱ-ㅎㅏ-ㅣa-zA-Z0-9\s.,!?~%:;'\"()\[\]·\-—…]+")


def has_foreign_script(text: str) -> bool:
    return bool(_FOREIGN_CHARS.search(text))


# 태그 통제 어휘 (노션 확정 초안) — 이 밖의 값은 게이트에서 버린다
_ALLOWED_TAGS = {
    # 주제
    "음식요리", "건강병원", "가족안부", "명절기념일", "날씨계절",
    "신앙", "일·학교", "추억", "장소고향",
    # 생애시기
    "유년", "젊은시절", "중년", "노년", "최근",
    # 감정
    "애정", "그리움", "걱정", "기쁨", "슬픔", "유머",
}


def extract_memory_segments(
    segments: list[TranscriptSegment],
    *,
    subject_context: SubjectContext | None,
    subject_speaker_label: str | None,
    persons_result: dict | None,
    sensitivity_result: dict | None,
    settings: Settings,
    conversation_partner_name: str | None = None,
) -> dict:

    client = create_openai_client(settings, "memory extraction")
    response = client.chat.completions.create(
        model=settings.openai_analysis_model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": MEMORY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_memory_user_prompt(
                    segments, subject_context, subject_speaker_label, persons_result,
                    conversation_partner_name,
                ),
            },
        ],
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    subject = subject_context.subject if subject_context is not None else None
    return validate_memory_payload(
        payload,
        segments,
        sensitivity_result,
        subject_name=subject.name if subject else None,
    )


def _normalized(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    return "".join(ch for ch in text if ch.isalnum())


def validate_memory_payload(
    payload: dict,
    segments: list[TranscriptSegment],
    sensitivity_result: dict | None = None,
    *,
    subject_name: str | None = None,
) -> dict:
    """R1(근거 실존)·enum·중복을 코드로 강제하고 시간·화자·민감플래그를 파생한다."""
    by_index = {segment.segment_index: segment for segment in segments}
    dropped = {"memories": 0, "duplicates": 0}
    seen_texts: set[str] = set()

    flag_map: list[tuple[set[int], str]] = []
    for flag in (sensitivity_result or {}).get("sensitivityFlags") or []:
        flag_map.append((set(flag["sourceSegmentIds"]), flag["type"]))

    memories = []
    for memory in payload.get("memorySegments") or []:
        if not isinstance(memory, dict):
            dropped["memories"] += 1
            continue
        text = str(memory.get("memoryText") or "").strip()
        source_ids_raw = memory.get("sourceSegmentIds")
        if not text or has_foreign_script(text) or not isinstance(source_ids_raw, list):
            dropped["memories"] += 1
            continue
        source_ids = sorted(
            {i for i in source_ids_raw if isinstance(i, int) and i in by_index}
        )
        if not source_ids or memory.get("confidence") not in _CONFIDENCE_VALUES:
            dropped["memories"] += 1
            continue

        key = _normalized(text)
        if key in seen_texts:
            dropped["duplicates"] += 1
            continue
        seen_texts.add(key)

        sources = [by_index[i] for i in source_ids]
        speaker_counts: dict[str, int] = {}
        for source in sources:
            speaker_counts[source.speaker_label] = (
                speaker_counts.get(source.speaker_label, 0) + 1
            )
        # 민감플래그 조인은 정확히 인용된 세그먼트가 아니라 기억이 걸친 범위
        # [최소~최대]로 한다 — 중간 세그먼트를 명시 인용하지 않아도 그 범위 안의
        # 민감 내용을 놓치지 않기 위함 (근거 인용은 완전하지 않을 수 있음)
        span = range(source_ids[0], source_ids[-1] + 1)
        span_set = set(span)
        flags = sorted(
            {
                flag_type
                for flag_ids, flag_type in flag_map
                if flag_ids & span_set
            }
        )
        related = [
            str(name).strip()
            for name in (memory.get("relatedPeople") or [])
            if str(name).strip() and str(name).strip() != subject_name
        ]
        # importance: 1~10 정수로 클램프 (범위 밖·비정수는 중앙값 5)
        raw_importance = memory.get("importance")
        importance = (
            min(10, max(1, int(raw_importance)))
            if isinstance(raw_importance, (int, float))
            else 5
        )
        # tags: 통제 어휘 밖 값은 버린다 (코드 게이트, T1 룰)
        tags = [
            tag for tag in (memory.get("tags") or []) if tag in _ALLOWED_TAGS
        ]
        memories.append(
            {
                "segmentIndex": len(memories),
                "sourceSegmentIds": source_ids,
                "startMs": min(s.start_ms for s in sources),
                "endMs": max(s.end_ms for s in sources),
                "speakerLabel": max(speaker_counts, key=speaker_counts.get),
                "memoryText": text,
                "relatedPeople": related,
                "confidence": memory["confidence"],
                "importanceScore": importance,
                "tags": tags,
                "sensitivityFlags": flags,
            }
        )

    if any(dropped.values()):
        logger.warning("memory validation dropped items: %s", dropped)
    return {"memorySegments": memories, "validationDropped": dropped}


def _pick_subject_speaker_label(
    segments: list[TranscriptSegment], provided: str | None
) -> str | None:
    """대상자 화자 라벨: 요청에 있으면 그대로, 없으면 발화량 최다 화자로 자동 판정."""
    if provided:
        return provided
    counts: dict[str, int] = {}
    for segment in segments:
        counts[segment.speaker_label] = counts.get(segment.speaker_label, 0) + 1
    return max(counts, key=counts.get) if counts else None


def run_recording_memory_extraction(
    request: MemorySegmentExtractionRequest,
    *,
    settings: Settings,
) -> MemorySegmentExtractionResponse:
    """녹음 1건의 전사 세그먼트 → 인물·민감·3-A 기억을 연결해 서빙 응답으로 조립.

    stateless: 인물/민감 결과는 memoryText 파생·민감플래그 조인에만 쓰고 돌려주지 않는다.
    통화 상대(conversationPartnerName)가 오면 상대 화자를 그 이름으로 확정 귀속한다.
    """
    segments = [
        TranscriptSegment(
            segment_index=item.segment_index,
            start_ms=item.start_ms,
            end_ms=item.end_ms,
            speaker_label=item.speaker_label,
            transcript_text=item.transcript_text,
        )
        for item in request.transcript_segments
    ]
    id_by_index = {item.segment_index: item.id for item in request.transcript_segments}
    subject_label = _pick_subject_speaker_label(segments, request.subject_speaker_label)
    partner = request.conversation_partner_name

    persons = run_persons_analysis(
        segments,
        subject_context=request.subject_context,
        subject_speaker_label=subject_label,
        settings=settings,
        conversation_partner_name=partner,
    )
    sensitivity = run_sensitivity_analysis(segments, settings=settings)
    result = extract_memory_segments(
        segments,
        subject_context=request.subject_context,
        subject_speaker_label=subject_label,
        persons_result=persons,
        sensitivity_result=sensitivity,
        settings=settings,
        conversation_partner_name=partner,
    )

    out: list[MemorySegment] = []
    for memory in result["memorySegments"]:
        source_ids = [
            id_by_index[i] for i in memory["sourceSegmentIds"] if i in id_by_index
        ]
        if not source_ids:  # 근거 세그먼트가 요청에 없으면(방어) 그 기억은 버린다
            continue
        out.append(
            MemorySegment(
                segment_index=memory["segmentIndex"],
                source_transcript_segment_ids=source_ids,
                start_ms=memory["startMs"],
                end_ms=memory["endMs"],
                speaker_label=memory["speakerLabel"],
                memory_text=memory["memoryText"],
                confidence=memory["confidence"],
                importance_score=memory["importanceScore"],
                tags=memory["tags"],
                related_people=memory["relatedPeople"],
                sensitivity_flags=memory["sensitivityFlags"],
            )
        )

    return MemorySegmentExtractionResponse(
        segments=out,
        provider="openai",
        model=settings.openai_analysis_model,
    )
