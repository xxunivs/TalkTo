"""녹음 1건 통합 분석 — 인물·민감·3-A·요약·태그·④를 한 번에 돌려 서빙 응답으로 조립.

공통 분석(인물·민감·3-A)을 여러 엔드포인트가 중복 호출하지 않도록 한 오케스트레이터로
묶는다. 인물·민감 결과는 memoryText 파생·플래그 조인·안전플래그에만 쓰고, 통화 상대
(conversationPartnerName)가 오면 상대 화자를 그 이름으로 확정 귀속한다. stateless.
"""

from uuid import UUID

from app.core.config import Settings
from app.pipeline.analysis.linguistic_style import run_linguistic_style_analysis
from app.pipeline.analysis.persons import run_persons_analysis
from app.pipeline.analysis.sensitivity import run_sensitivity_analysis
from app.pipeline.enrichment.service import aggregate_tags, summarize_recording
from app.pipeline.memory_segments.service import extract_memory_segments
from app.schemas.memory import MemorySegment
from app.schemas.recording import (
    AddressTerm,
    EmotionalExpression,
    RecordingAnalysisRequest,
    RecordingAnalysisResponse,
    RecordingSafetyFlag,
    RecurringPhrase,
    SpeechStyle,
)
from app.schemas.transcript import TranscriptSegment


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


def run_recording_analysis(
    request: RecordingAnalysisRequest,
    *,
    settings: Settings,
) -> RecordingAnalysisResponse:
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
    id_by_index: dict[int, UUID] = {
        item.segment_index: item.id for item in request.transcript_segments
    }

    def to_uuids(int_ids: list[int]) -> list[UUID]:
        return [id_by_index[i] for i in int_ids if i in id_by_index]

    subject_context = request.subject_context
    label = _pick_subject_speaker_label(segments, request.subject_speaker_label)
    partner = request.conversation_partner_name

    persons = run_persons_analysis(
        segments,
        subject_context=subject_context,
        subject_speaker_label=label,
        settings=settings,
        conversation_partner_name=partner,
    )
    sensitivity = run_sensitivity_analysis(segments, settings=settings)
    memory_result = extract_memory_segments(
        segments,
        subject_context=subject_context,
        subject_speaker_label=label,
        persons_result=persons,
        sensitivity_result=sensitivity,
        settings=settings,
        conversation_partner_name=partner,
    )
    style = run_linguistic_style_analysis(
        segments,
        subject_context=subject_context,
        subject_speaker_label=label,
        settings=settings,
    )["linguisticStyle"]
    summary = summarize_recording(segments, settings=settings)
    tags = aggregate_tags(memory_result)

    memory_segments: list[MemorySegment] = []
    for memory in memory_result["memorySegments"]:
        source_ids = to_uuids(memory["sourceSegmentIds"])
        if not source_ids:  # 근거가 요청 세그먼트에 없으면(방어) 그 기억은 버린다
            continue
        memory_segments.append(
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

    speech_style = SpeechStyle(
        recurring_phrases=[
            RecurringPhrase(
                phrase=item["phrase"],
                source_transcript_segment_ids=to_uuids(item["sourceSegmentIds"]),
            )
            for item in style["recurringPhrases"]
        ],
        address_terms=[
            AddressTerm(
                person=item["person"],
                term=item["term"],
                source_transcript_segment_ids=to_uuids(item["sourceSegmentIds"]),
            )
            for item in style["addressTerms"]
        ],
        sentence_patterns=style["sentencePatterns"],
        emotional_expressions=[
            EmotionalExpression(
                emotion=item["emotion"],
                expression=item["expression"],
                source_transcript_segment_ids=to_uuids(item["sourceSegmentIds"]),
            )
            for item in style["emotionalExpressions"]
        ],
    )

    safety_flags = [
        RecordingSafetyFlag(
            type=flag["type"],
            description=flag["description"],
            source_transcript_segment_ids=to_uuids(flag["sourceSegmentIds"]),
        )
        for flag in sensitivity["sensitivityFlags"]
    ]

    return RecordingAnalysisResponse(
        memory_segments=memory_segments,
        summary=summary,
        tags=tags,
        speech_style=speech_style,
        safety_flags=safety_flags,
        provider="openai",
        model=settings.openai_analysis_model,
    )
