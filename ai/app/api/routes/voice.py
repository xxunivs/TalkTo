from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from app.api.dependencies import SettingsDependency, require_internal_request
from app.error import AudioTooLargeError
from app.external_api.tts import create_tts_provider, provider_for_voice
from app.schema.voice import (
    AnswerTranscriptionResponse,
    OkResponse,
    SpeechSynthesisRequest,
    SubjectSampleCollectionRequest,
    SubjectSampleCollectionResponse,
    VoiceCloneFromTranscriptRequest,
    VoiceCloneRequest,
    VoiceCloneResponse,
    VoiceDeleteRequest,
    VoiceTranscriptionResponse,
)
from pipeline.s4_voice.a_voice import (
    clone_voice_from_sample,
    clone_voice_from_transcript,
    collect_subject_samples,
    synthesize_speech,
    transcribe_voice_message,
)
from pipeline.s4_voice.d_answer_transcription import (
    ANSWER_AUDIO_MAX_BYTES,
    transcribe_answer,
)

router = APIRouter(tags=["voice"])
InternalRequest = Annotated[None, Depends(require_internal_request)]

# 채팅 음성 메시지 상한 (OpenAI 받아쓰기 한도 25MB, 초과 시 끝까지 읽지 않고 413)
VOICE_MESSAGE_MAX_BYTES = 25 * 1024 * 1024


@router.post("/transcriptions", response_model=VoiceTranscriptionResponse)
def create_voice_transcription(
    _authorized: InternalRequest,
    settings: SettingsDependency,
    audio_file: Annotated[UploadFile, File()],
    language: Annotated[str, Form()] = "ko",
) -> VoiceTranscriptionResponse:
    """채팅 음성 메시지 audio_file -> 텍스트 (블로킹 호출이라 일반 def, 스레드풀 실행)"""
    content = audio_file.file.read(VOICE_MESSAGE_MAX_BYTES + 1)
    if len(content) > VOICE_MESSAGE_MAX_BYTES:
        raise AudioTooLargeError(VOICE_MESSAGE_MAX_BYTES)
    return transcribe_voice_message(
        content, audio_file.filename or "audio.wav", language=language, settings=settings
    )


@router.post("/answer-transcriptions", response_model=AnswerTranscriptionResponse)
def create_answer_transcription(
    _authorized: InternalRequest,
    settings: SettingsDependency,
    audio_file: Annotated[UploadFile, File()],
) -> AnswerTranscriptionResponse:
    """말로 답하기 audio_file(webm, m4a, wav, 3분까지) -> 텍스트 (ElevenLabs Scribe, 녹음과 기록 즉시 삭제, 무음 422)"""
    content = audio_file.file.read(ANSWER_AUDIO_MAX_BYTES + 1)
    if len(content) > ANSWER_AUDIO_MAX_BYTES:
        raise AudioTooLargeError(ANSWER_AUDIO_MAX_BYTES)
    return transcribe_answer(content, audio_file.filename or "answer.webm", settings=settings)


@router.post("/speech")
def create_speech(
    request: SpeechSynthesisRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> Response:
    """페르소나 답변 text(+voiceId) -> 대상자 목소리 audio/mpeg"""
    result = synthesize_speech(request, settings=settings)
    return Response(content=result.audio, media_type=result.content_type)


@router.post("/clone", response_model=VoiceCloneResponse)
def create_voice_clone(
    request: VoiceCloneRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> VoiceCloneResponse:
    """대상자 목소리 샘플 -> 클론 음성 voiceId (요청 provider 와 무관하게 항상 Fish Audio)"""
    return clone_voice_from_sample(request, settings=settings)


@router.post("/clone-from-transcript", response_model=VoiceCloneResponse)
def create_voice_clone_from_transcript(
    request: VoiceCloneFromTranscriptRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> VoiceCloneResponse:
    """전사 구간, subjectSpeakerLabel -> 대상자 구간 자동 수집 -> 클론 voiceId (samples 조립 불필요)"""
    return clone_voice_from_transcript(request, settings=settings)


@router.post("/collect-samples", response_model=SubjectSampleCollectionResponse)
def collect_voice_samples(
    request: SubjectSampleCollectionRequest,
    _authorized: InternalRequest,
) -> SubjectSampleCollectionResponse:
    """한 통화의 대상자 구간 추출 (클론 없음, BE 가 대상자별 풀에 누적, 상한과 선별은 클론 시점)"""
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


@router.post("/delete", response_model=OkResponse)
def delete_voice(
    request: VoiceDeleteRequest, _authorized: InternalRequest, settings: SettingsDependency
) -> OkResponse:
    """목소리 제공사에서 삭제 (voice_id 접두어 -> provider -> ElevenLabs 순으로 판별)"""
    name = provider_for_voice(request.voice_id)
    if name == "elevenlabs" and request.provider is not None:
        name = request.provider
    create_tts_provider(settings, provider=name).delete_voice(request.voice_id)
    return OkResponse()
