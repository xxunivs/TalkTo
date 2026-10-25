from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from infra.be_ai_api.dependencies import SettingsDependency, require_internal_request
from infra.error import AudioTooLargeError
from infra.external_api.tts import create_tts_provider, provider_for_voice
from infra.schema.voice import (
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


@router.post("/answer-transcriptions", response_model=AnswerTranscriptionResponse)
def create_answer_transcription(
    _authorized: InternalRequest,
    settings: SettingsDependency,
    audio_file: Annotated[UploadFile, File()],
) -> AnswerTranscriptionResponse:
    """말로 답하기 — 멀티파트 audio_file(webm·m4a·wav, 3분까지) → 받아쓴 글(ElevenLabs Scribe).

    화자 나누기 없이 한국어로 받아쓴다. 녹음은 임시 파일로만 두고 바로 지우며, ElevenLabs 의
    받아쓰기 기록도 바로 지운다. 말소리가 없으면 422 NO_SPEECH. 받아쓰기 호출이 블로킹이라
    일반 def 로 둔다(스레드풀에서 돈다).
    """
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
    """페르소나 답변 음성 합성 — text(+voiceId) → 고인 목소리 audio/mpeg."""
    result = synthesize_speech(request, settings=settings)
    return Response(content=result.audio, media_type=result.content_type)


@router.post("/clone", response_model=VoiceCloneResponse)
def create_voice_clone(
    request: VoiceCloneRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> VoiceCloneResponse:
    """고인 목소리 샘플 클립 → 클론 음성 등록 → voiceId(대상자별 TTS 음성).

    늘 Fish Audio 에서 만든다(요청의 provider 와 무관). 답은 {voiceId: "fish_…", provider: "fish"}.
    """
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


@router.post("/delete", response_model=OkResponse)
def delete_voice(
    request: VoiceDeleteRequest, _authorized: InternalRequest, settings: SettingsDependency
) -> OkResponse:
    """목소리를 만든 회사에서 지운다(대화 상대를 지울 때·목소리를 다시 만들 때).

    voice_id 앞글자로 고른다: fish_… → Fish, TalkTo… → MiniMax. 앞글자로 모르면 provider 를 보고,
    그것도 없으면 ElevenLabs(예전 IVC·PVC 모두 같은 API, PVC 면 슬롯을 돌려받는다).
    """
    name = provider_for_voice(request.voice_id)
    if name == "elevenlabs" and request.provider is not None:
        name = request.provider
    create_tts_provider(settings, provider=name).delete_voice(request.voice_id)
    return OkResponse()
