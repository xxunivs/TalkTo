from typing import Annotated

from fastapi import APIRouter, Depends

from infra.be_ai_api.dependencies import SettingsDependency, require_internal_request
from infra.be_ai_api.recording_analysis import run_recording_analysis
from pipeline.s1_transcription.a_transcript import transcribe_recording
from pipeline.s1_transcription.b_speaker_id import identify_subject_by_content
from pipeline.s1_transcription.d_voice_groups import group_voices
from infra.schema.recording_analysis import (
    RecordingAnalysisRequest,
    RecordingAnalysisResponse,
    SubjectSpeakerRequest,
    SubjectSpeakerResponse,
    VoiceGroup,
    VoiceGroupMember,
    VoiceGroupRequest,
    VoiceGroupResponse,
)
from infra.schema.transcript import TranscriptSegment
from infra.schema.transcript import TranscriptionRequest, TranscriptionResponse
from infra.external_api.stt import create_stt_provider


router = APIRouter(tags=["analysis"])
InternalRequest = Annotated[None, Depends(require_internal_request)]


@router.post("/transcriptions", response_model=TranscriptionResponse)
def create_analysis_transcription(
    request: TranscriptionRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> TranscriptionResponse:
    provider = create_stt_provider(settings)
    return transcribe_recording(request, settings=settings, provider=provider)


@router.post("/recording", response_model=RecordingAnalysisResponse)
def create_recording_analysis(
    request: RecordingAnalysisRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> RecordingAnalysisResponse:
    """녹음 1건 통합 분석 — 3-A 기억 + 요약 + 태그 + 말투(④) + 안전플래그를 한 번에."""
    return run_recording_analysis(request, settings=settings)


def _segments(items) -> list[TranscriptSegment]:
    return [
        TranscriptSegment(
            segment_index=item.segment_index,
            start_ms=item.start_ms,
            end_ms=item.end_ms,
            speaker_label=item.speaker_label,
            transcript_text=item.transcript_text,
        )
        for item in items
    ]


@router.post("/voice-groups", response_model=VoiceGroupResponse)
def create_voice_groups(
    request: VoiceGroupRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> VoiceGroupResponse:
    """녹음 여러 개에 걸쳐 같은 사람의 목소리를 묶는다(ECAPA 지문). 사람은 묶음 하나를 고른다."""
    clusters, method = group_voices(
        [(r.recording_id, str(r.audio_url), _segments(r.transcript_segments))
         for r in request.recordings],
        settings=settings,
    )
    return VoiceGroupResponse(
        method=method,
        groups=[
            VoiceGroup(
                group_index=i,
                speech_ms=cluster.speech_ms,
                members=[
                    VoiceGroupMember(
                        recording_id=m.recording_id, speaker_label=m.speaker_label,
                        speech_ms=m.speech_ms,
                    )
                    for m in cluster.members
                ],
            )
            for i, cluster in enumerate(clusters)
        ],
    )


@router.post("/subject-speaker", response_model=SubjectSpeakerResponse)
def suggest_subject_speaker(
    request: SubjectSpeakerRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> SubjectSpeakerResponse:
    """전사문 호칭·맥락으로 대상자 화자를 추천한다. 화자가 하나뿐이면 LLM 없이 그 화자."""
    segments = _segments(request.transcript_segments)
    labels = {s.speaker_label for s in segments}
    if len(labels) == 1:
        return SubjectSpeakerResponse(subject_speaker_label=labels.pop())
    label = identify_subject_by_content(segments, request.subject_context, settings=settings)
    return SubjectSpeakerResponse(subject_speaker_label=label)
