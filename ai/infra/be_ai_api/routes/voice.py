from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from infra.be_ai_api.dependencies import SettingsDependency, require_internal_request
from pipeline.s4_voice.a_voice import (
    clone_voice_from_sample,
    clone_voice_from_transcript,
    collect_subject_samples,
    synthesize_speech,
    transcribe_voice_message,
)
from infra.schema.voice import (
    SpeechSynthesisRequest,
    SubjectSampleCollectionRequest,
    SubjectSampleCollectionResponse,
    VoiceCloneFromTranscriptRequest,
    VoiceCloneRequest,
    VoiceCloneResponse,
    VoiceTranscriptionResponse,
)


router = APIRouter(tags=["voice"])
InternalRequest = Annotated[None, Depends(require_internal_request)]


@router.post("/transcriptions", response_model=VoiceTranscriptionResponse)
async def create_voice_transcription(
    _authorized: InternalRequest,
    settings: SettingsDependency,
    audio_file: Annotated[UploadFile, File()],
    language: Annotated[str, Form()] = "ko",
) -> VoiceTranscriptionResponse:
    """채팅 음성 메시지 받아쓰기 — 멀티파트 audio_file → 텍스트."""
    content = await audio_file.read()
    return transcribe_voice_message(
        content, audio_file.filename or "audio.wav", language=language, settings=settings
    )


@router.post("/speech")
def create_speech(
    request: SpeechSynthesisRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> Response:
    """페르소나 답변 음성 합성 — text(+voiceId) → 고인 목소리 audio/mpeg."""
    result = synthesize_speech(request, settings=settings)
    return Response(content=result.audio, media_type=result.content_type)


@router.post("/clone", response_model=VoiceCloneResponse)
def create_voice_clone(
    request: VoiceCloneRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> VoiceCloneResponse:
    """고인 목소리 샘플 클립 → 클론 음성 등록 → voiceId(대상자별 TTS 음성)."""
    return clone_voice_from_sample(request, settings=settings)


@router.post("/clone-from-transcript", response_model=VoiceCloneResponse)
def create_voice_clone_from_transcript(
    request: VoiceCloneFromTranscriptRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> VoiceCloneResponse:
    """전사 결과 → 대상자 구간 자동 수집 → 클론(IVC) → voiceId.

    /clone과 달리 samples[]를 호출자가 조립할 필요 없이, 화자 라벨이 붙은 전사 구간과
    확정된 subjectSpeakerLabel만 넘기면 AI가 대상자 구간을 모아 클론한다.
    """
    return clone_voice_from_transcript(request, settings=settings)


@router.post("/collect-samples", response_model=SubjectSampleCollectionResponse)
def collect_voice_samples(
    request: SubjectSampleCollectionRequest,
    _authorized: InternalRequest,
) -> SubjectSampleCollectionResponse:
    """한 통화에서 대상자 구간만 뽑아 반환(클론하지 않음).

    누적 풀(B안): BE가 통화마다 호출해 대상자별 풀에 저장·누적한다. 대상자 라벨이 없으면
    이 통화의 기여분은 빈 목록. 상한·선별은 클론 시점(/voice/clone)에 적용한다.
    """
    samples = (
        collect_subject_samples(
            request.transcript_segments,
            request.subject_speaker_label,
            request.audio_url,
        )
        if request.subject_speaker_label
        else []
    )
    return SubjectSampleCollectionResponse(samples=samples)
