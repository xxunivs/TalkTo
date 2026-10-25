# BE 의 녹음 분석 요청을 받아 단계들을 돌리고 응답으로 조립
# 단계 순서는 pipeline/run_pipeline.py 와 동일 (변경 시 양쪽 확인)

from uuid import UUID

from app.schema.memory import MemorySegment
from app.schema.recording_analysis import (
    AddressTerm,
    EmotionalExpression,
    RecordingAnalysisRequest,
    RecordingAnalysisResponse,
    RecordingSafetyFlag,
    RecurringPhrase,
    SpeechStyle,
)
from app.schema.subject import SubjectContext
from app.schema.transcript import TranscriptSegment
from app.setting import Settings
from pipeline.s1_transcription.b_speaker_id import identify_subject_by_content
from pipeline.s2_extraction.a_extract_persons import run_persons_analysis
from pipeline.s2_extraction.b_extract_sensitivity import run_sensitivity_analysis
from pipeline.s2_extraction.c_extract_linguistic_style import run_linguistic_style_analysis
from pipeline.s2_extraction.e_extract_memory import extract_memory_segments
from pipeline.s2_extraction.f_extract_summary import aggregate_tags, summarize_recording


def _resolve_subject_speaker_label(
    segments: list[TranscriptSegment],
    provided: str | None,
    subject_context: SubjectContext | None,
    *,
    settings: Settings,
) -> str | None:
    """대상자 화자 라벨: ECAPA 성문(provided) -> LLM 호칭, 맥락 폴백 -> None (발화량은 신원 근거 아님)"""
    if provided:
        return provided
    return identify_subject_by_content(segments, subject_context, settings=settings)


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
    label = _resolve_subject_speaker_label(
        segments, request.subject_speaker_label, subject_context, settings=settings
    )
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
        if not source_ids:  # 근거 세그먼트가 요청에 없는 기억은 제외 (방어)
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
