import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.errors import AudioUrlExpiredError
from app.main import app
from app.providers.stt.interface import STTResult
from app.schemas.transcript import TranscriptSegment


def request_body(**overrides) -> dict:
    body = {
        "jobId": "11111111-1111-1111-1111-111111111111",
        "recordingId": "22222222-2222-2222-2222-222222222222",
        "audioUrl": "https://storage.example.com/recordings/test.m4a?sig=abc",
        "language": "ko",
        "speakerDiarization": True,
        "glossary": ["향", "정읍"],
    }
    body.update(overrides)
    return body


class FakeProvider:
    def __init__(self, segments: list[TranscriptSegment]) -> None:
        self._segments = segments

    def transcribe(self, audio_path, *, language, speaker_diarization, num_speakers=None):
        return STTResult(provider="elevenlabs", model="scribe_v1", segments=self._segments)


SEGMENTS = [
    TranscriptSegment(
        segment_index=0,
        start_ms=0,
        end_ms=5_000,
        speaker_label="SPK_0",
        transcript_text="밥은 먹었냐",
        confidence=0.93,
    )
]


class TranscriptionsEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_success_returns_camel_case_contract(self) -> None:
        with (
            patch(
                "app.api.routes.analysis.create_stt_provider",
                return_value=FakeProvider(SEGMENTS),
            ),
            patch(
                "app.pipeline.transcription.service.download_audio",
                return_value=Path("/nonexistent/audio.m4a"),
            ),
            patch(
                "app.pipeline.transcription.service.correct_segments",
                side_effect=lambda segments, **_kwargs: segments,
            ),
        ):
            response = self.client.post(
                "/v1/analysis/transcriptions", json=request_body()
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["provider"], "elevenlabs")
        self.assertEqual(body["model"], "scribe_v1")
        self.assertEqual(
            body["segments"][0],
            {
                "id": None,
                "segmentIndex": 0,
                "startMs": 0,
                "endMs": 5000,
                "speakerLabel": "SPK_0",
                "transcriptText": "밥은 먹었냐",
                "confidence": 0.93,
                "correctedText": None,
                "needsReview": False,
            },
        )

    def test_expired_url_maps_to_422_with_code(self) -> None:
        with (
            patch(
                "app.api.routes.analysis.create_stt_provider",
                return_value=FakeProvider(SEGMENTS),
            ),
            patch(
                "app.pipeline.transcription.service.download_audio",
                side_effect=AudioUrlExpiredError(),
            ),
        ):
            response = self.client.post(
                "/v1/analysis/transcriptions", json=request_body()
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "AUDIO_URL_EXPIRED")

    def test_missing_audio_url_fails_validation(self) -> None:
        body = request_body()
        del body["audioUrl"]

        response = self.client.post("/v1/analysis/transcriptions", json=body)

        self.assertEqual(response.status_code, 422)

    def test_invalid_mode_fails_validation(self) -> None:
        response = self.client.post(
            "/v1/analysis/transcriptions", json=request_body(mode="sample")
        )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
