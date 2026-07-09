import logging
from pathlib import Path

from app.core.config import Settings
from app.core.errors import AudioTooShortError, EmptyTranscriptError
from app.providers.stt.interface import STTProvider
from app.schemas.transcript import (
    TranscriptionRequest,
    TranscriptionResponse,
    TranscriptSegment,
)
from app.pipeline.transcription.audio import download_audio
from app.pipeline.correction.service import correct_segments
from app.pipeline.correction.glossary import build_glossary

logger = logging.getLogger(__name__)


def _identify_subject_speaker(
    audio_path: Path,
    segments: list[TranscriptSegment],
    request: TranscriptionRequest,
    settings: Settings,
) -> str | None:
    """참조 목소리 샘플과 녹음 화자를 ECAPA 성문 매칭해 대상자 화자 라벨을 확정.

    참조 미제공·패키지 미설치·매칭 실패는 전사를 막지 않고 None으로 진행한다
    (2단계 내용 기반 판단으로 폴백).
    """
    if request.reference_voice_sample_url is None:
        return None
    try:
        from app.pipeline.speaker_id.service import SpeakerEmbedder, identify_subject
    except ImportError:
        logger.warning("speaker_id 패키지 미설치 — 대상자 화자 식별 생략")
        return None

    ref_path = download_audio(
        str(request.reference_voice_sample_url),
        timeout_seconds=settings.audio_download_timeout_seconds,
        max_bytes=settings.max_audio_bytes,
        temp_dir=settings.temp_dir,
    )
    try:
        embedder = SpeakerEmbedder(
            cache_dir=Path(settings.speaker_model_dir)
            if settings.speaker_model_dir
            else None
        )
        reference = embedder.embed_clip(ref_path)
        speakers = embedder.embed_speakers(audio_path, segments)
        match = identify_subject(
            speakers, reference, threshold=settings.speaker_id_threshold
        )
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
        temp_dir=settings.temp_dir,
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
        # last_end_ms == 0 means the provider fell back to plain text without
        # timestamps — duration is unknown there, so only positive values gate.
        if 0 < last_end_ms < settings.min_speech_ms:
            raise AudioTooShortError(last_end_ms, settings.min_speech_ms)
        # 화자 식별은 오디오가 아직 살아있을 때(unlink 전) 수행한다.
        subject_speaker_label = _identify_subject_speaker(
            audio_path, segments, request, settings
        )
    finally:
        audio_path.unlink(missing_ok=True)

    if request.mode == "preview":
        segments = [
            segment
            for segment in segments
            if segment.start_ms < settings.preview_window_ms
        ]

    derived = build_glossary(request.subject_context, request.intake_context)
    merged_glossary = list(dict.fromkeys([*request.glossary, *derived]))
    segments = correct_segments(
        segments, glossary=merged_glossary, settings=settings
    )

    return TranscriptionResponse(
        segments=segments,
        provider=result.provider,
        model=result.model,
        subject_speaker_label=subject_speaker_label,
    )
