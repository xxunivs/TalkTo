import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.core.config import Settings
from app.core.errors import AudioTooShortError, EmptyTranscriptError
from app.providers.stt.interface import STTResult
from app.schemas.transcript import TranscriptionRequest, TranscriptSegment
from app.services.transcription import transcribe_recording


def segment(index: int, start_ms: int, end_ms: int, text: str = "안녕") -> TranscriptSegment:
    return TranscriptSegment(
        segment_index=index,
        start_ms=start_ms,
        end_ms=end_ms,
        speaker_label="SPK_0",
        transcript_text=text,
    )


class FakeProvider:
    def __init__(self, segments: list[TranscriptSegment]) -> None:
        self._segments = segments

    def transcribe(self, audio_path, *, language, speaker_diarization, num_speakers=None):
        return STTResult(provider="elevenlabs", model="scribe_v1", segments=self._segments)


def request(**overrides) -> TranscriptionRequest:
    fields = {
        "job_id": uuid4(),
        "recording_id": uuid4(),
        "audio_url": "https://storage.example.com/recordings/a.m4a?sig=x",
    }
    fields.update(overrides)
    return TranscriptionRequest(**fields)


@patch(
    "app.services.transcription.download_audio",
    return_value=Path("/nonexistent/audio.m4a"),
)
class TranscribeRecordingTest(unittest.TestCase):
    settings = Settings()

    def test_returns_segments_with_provider_and_model(self, _download) -> None:
        provider = FakeProvider([segment(0, 0, 5_000), segment(1, 5_000, 9_000)])

        response = transcribe_recording(
            request(), settings=self.settings, provider=provider
        )

        self.assertEqual(len(response.segments), 2)
        self.assertEqual(response.provider, "elevenlabs")
        self.assertEqual(response.model, "scribe_v1")

    def test_empty_transcript_raises(self, _download) -> None:
        with self.assertRaises(EmptyTranscriptError):
            transcribe_recording(
                request(), settings=self.settings, provider=FakeProvider([])
            )

    def test_too_short_speech_raises(self, _download) -> None:
        provider = FakeProvider([segment(0, 0, 1_500)])

        with self.assertRaises(AudioTooShortError):
            transcribe_recording(request(), settings=self.settings, provider=provider)

    def test_zero_end_ms_fallback_is_not_too_short(self, _download) -> None:
        provider = FakeProvider([segment(0, 0, 0, text="타임스탬프 없는 fallback")])

        response = transcribe_recording(
            request(), settings=self.settings, provider=provider
        )

        self.assertEqual(len(response.segments), 1)

    def test_preview_mode_caps_segments_to_window(self, _download) -> None:
        provider = FakeProvider(
            [
                segment(0, 0, 5_000),
                segment(1, 170_000, 179_000),
                segment(2, 200_000, 210_000),
            ]
        )

        response = transcribe_recording(
            request(mode="preview"), settings=self.settings, provider=provider
        )

        self.assertEqual([s.segment_index for s in response.segments], [0, 1])


if __name__ == "__main__":
    unittest.main()
