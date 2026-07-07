from app.core.config import Settings
from app.core.errors import AudioTooShortError, EmptyTranscriptError
from app.providers.stt.interface import STTProvider
from app.schemas.transcript import TranscriptionRequest, TranscriptionResponse
from app.pipeline.transcription.audio import download_audio
from app.pipeline.correction.service import correct_segments
from app.pipeline.correction.glossary import build_glossary


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
    finally:
        audio_path.unlink(missing_ok=True)

    segments = result.segments
    if not segments:
        raise EmptyTranscriptError()
    last_end_ms = max(segment.end_ms for segment in segments)
    # last_end_ms == 0 means the provider fell back to plain text without
    # timestamps — duration is unknown there, so only positive values gate.
    if 0 < last_end_ms < settings.min_speech_ms:
        raise AudioTooShortError(last_end_ms, settings.min_speech_ms)

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
        segments=segments, provider=result.provider, model=result.model
    )
