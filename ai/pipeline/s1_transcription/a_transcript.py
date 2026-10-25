import logging
from pathlib import Path

from infra.error import AppError, AudioTooShortError, EmptyTranscriptError
from infra.external_api.audio import download_audio
from infra.external_api.stt import STTProvider
from infra.schema.transcript import (
    TranscriptionRequest,
    TranscriptionResponse,
    TranscriptSegment,
)
from infra.scratch import scratch_dir
from infra.setting import Settings
from pipeline.s1_transcription.c_correction import build_glossary, correct_segments

logger = logging.getLogger(__name__)


def _identify_subject_speaker(
    audio_path: Path,
    segments: list[TranscriptSegment],
    request: TranscriptionRequest,
    settings: Settings,
) -> str | None:
    """참조 목소리 샘플 ECAPA 성문 매칭 -> 대상자 화자 라벨 (실패 시 None, 2단계 내용 기반 폴백)"""
    if request.reference_voice_sample_url is None:
        return None
    try:
        from pipeline.s1_transcription.b_speaker_id import (
            EMBED_LOCK,
            identify_subject,
            shared_embedder,
        )
    except ImportError:
        logger.warning("speaker_id 패키지 미설치 — 대상자 화자 식별 생략")
        return None

    try:
        ref_path = download_audio(
            str(request.reference_voice_sample_url),
            timeout_seconds=settings.audio_download_timeout_seconds,
            max_bytes=settings.max_audio_bytes,
            temp_dir=scratch_dir(settings),
        )
    except AppError as error:  # 참조 샘플 실패에도 전사 진행 (대상자 미확정)
        logger.warning("reference voice sample download failed: %s", error.code)
        return None
    try:
        embedder = shared_embedder(
            Path(settings.speaker_model_dir) if settings.speaker_model_dir else None
        )
        with EMBED_LOCK:
            reference = embedder.embed_clip(ref_path)
            speakers = embedder.embed_speakers(audio_path, segments)
        match = identify_subject(speakers, reference, threshold=settings.speaker_id_threshold)
        return match.speaker_label if match else None
    except Exception as error:  # 매칭 실패가 전사를 깨지 않게 (대상자 미확정 진행)
        logger.warning("speaker identification failed: %s", error)
        return None
    finally:
        ref_path.unlink(missing_ok=True)


def transcribe_recording(
    request: TranscriptionRequest,
    *,
    settings: Settings,
    provider: STTProvider,
) -> TranscriptionResponse:
    audio_path = download_audio(
        str(request.audio_url),
        mime_type=request.audio_mime_type,
        timeout_seconds=settings.audio_download_timeout_seconds,
        max_bytes=settings.max_audio_bytes,
        temp_dir=scratch_dir(settings),
    )
    try:
        result = provider.transcribe(
            audio_path,
            language=request.language,
            speaker_diarization=request.speaker_diarization,
        )
        segments = result.segments
        if not segments:
            raise EmptyTranscriptError()
        last_end_ms = max(segment.end_ms for segment in segments)
        # 0 이면 타임스탬프 없는 일반 텍스트 폴백이라 길이 미상 (양수일 때만 검사)
        if 0 < last_end_ms < settings.min_speech_ms:
            raise AudioTooShortError(last_end_ms, settings.min_speech_ms)
        # 화자 식별은 오디오 삭제(unlink) 전에
        subject_speaker_label = _identify_subject_speaker(audio_path, segments, request, settings)
    finally:
        audio_path.unlink(missing_ok=True)

    if request.mode == "preview":
        segments = [
            segment for segment in segments if segment.start_ms < settings.preview_window_ms
        ]

    derived = build_glossary(request.subject_context, request.intake_context)
    merged_glossary = list(dict.fromkeys([*request.glossary, *derived]))
    segments = correct_segments(segments, glossary=merged_glossary, settings=settings)

    return TranscriptionResponse(
        segments=segments,
        provider=result.provider,
        model=result.model,
        subject_speaker_label=subject_speaker_label,
    )
