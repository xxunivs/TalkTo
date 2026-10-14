from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from infra.be_ai_api.dependencies import SettingsDependency, require_internal_request
from infra.error import AudioTooLargeError
from pipeline.s4_voice.a_voice import (
    clone_voice_from_sample,
    clone_voice_from_transcript,
    collect_subject_samples,
    synthesize_speech,
    transcribe_voice_message,
)
from infra.external_api.tts import create_tts_provider
from pipeline.s4_voice import d_pvc
from infra.schema.voice import (
    OkResponse,
    PvcCreateRequest,
    PvcCreateResponse,
    PvcSamplesRequest,
    PvcSamplesResponse,
    PvcStatusResponse,
    PvcVerificationRequest,
    PvcVoiceRequest,
    SpeechSynthesisRequest,
    SubjectSampleCollectionRequest,
    SubjectSampleCollectionResponse,
    VoiceCloneFromTranscriptRequest,
    VoiceDeleteRequest,
    VoiceCloneRequest,
    VoiceCloneResponse,
    VoiceTranscriptionResponse,
)


router = APIRouter(tags=["voice"])
InternalRequest = Annotated[None, Depends(require_internal_request)]

# 채팅 음성 메시지 상한(OpenAI 받아쓰기도 25MB 까지 받는다). 넘으면 끝까지 읽지 않고 413.
VOICE_MESSAGE_MAX_BYTES = 25 * 1024 * 1024


@router.post("/transcriptions", response_model=VoiceTranscriptionResponse)
def create_voice_transcription(
    _authorized: InternalRequest,
    settings: SettingsDependency,
    audio_file: Annotated[UploadFile, File()],
    language: Annotated[str, Form()] = "ko",
) -> VoiceTranscriptionResponse:
    """채팅 음성 메시지 받아쓰기 — 멀티파트 audio_file → 텍스트.

    받아쓰기 호출이 블로킹이라 일반 def 로 둔다(스레드풀에서 돈다).
    """
    content = audio_file.file.read(VOICE_MESSAGE_MAX_BYTES + 1)
    if len(content) > VOICE_MESSAGE_MAX_BYTES:
        raise AudioTooLargeError(VOICE_MESSAGE_MAX_BYTES)
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

    누적 풀: BE가 통화마다 호출해 대상자별 풀에 저장·누적한다. 대상자 라벨이 없으면
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


# ── Premium 정밀 목소리(ElevenLabs PVC) ──


@router.post("/pvc/create", response_model=PvcCreateResponse)
def create_pvc(
    request: PvcCreateRequest, _authorized: InternalRequest, settings: SettingsDependency
) -> PvcCreateResponse:
    """빈 PVC 를 만든다(ElevenLabs PVC 슬롯을 하나 쓴다)."""
    return PvcCreateResponse(voice_id=d_pvc.create(request, settings=settings))


@router.post("/pvc/samples", response_model=PvcSamplesResponse)
def add_pvc_samples(
    request: PvcSamplesRequest, _authorized: InternalRequest, settings: SettingsDependency
) -> PvcSamplesResponse:
    """그분 구간을 잘라 깨끗한 것만 30분 이하 mp3 로 이어 올린다."""
    return d_pvc.add_samples(request, settings=settings)


@router.post("/pvc/verification", response_model=OkResponse)
def request_pvc_verification(
    request: PvcVerificationRequest, _authorized: InternalRequest, settings: SettingsDependency
) -> OkResponse:
    """목소리 주인 확인(수동)을 신청한다. 고인은 사망진단서·가족 동의서."""
    d_pvc.request_verification(request, settings=settings)
    return OkResponse()


@router.post("/pvc/status", response_model=PvcStatusResponse)
def pvc_status(
    request: PvcVoiceRequest, _authorized: InternalRequest, settings: SettingsDependency
) -> PvcStatusResponse:
    return d_pvc.status(request.voice_id, settings=settings)


@router.post("/pvc/train", response_model=OkResponse)
def train_pvc(
    request: PvcVoiceRequest, _authorized: InternalRequest, settings: SettingsDependency
) -> OkResponse:
    return OkResponse(model_id=d_pvc.train(request.voice_id, settings=settings))


@router.post("/delete", response_model=OkResponse)
def delete_voice(
    request: VoiceDeleteRequest, _authorized: InternalRequest, settings: SettingsDependency
) -> OkResponse:
    """목소리를 만든 회사에서 지운다(대화 상대를 지울 때·목소리를 다시 만들 때).

    ElevenLabs 는 처음 만든 목소리(IVC)·똑 닮은 목소리(PVC) 모두 같은 API 로 지운다(PVC 면 슬롯을
    돌려받는다). MiniMax 는 복제 목소리 지우기 API 를 쓴다.
    """
    if request.provider == "minimax":
        create_tts_provider(settings, provider="minimax").delete_voice(request.voice_id)
    else:
        d_pvc.delete(request.voice_id, settings=settings)
    return OkResponse()
