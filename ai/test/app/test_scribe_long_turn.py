"""혼자 오래 말한 녹음(AI-2): 구간 하나가 20,000자 한도를 넘어 받아쓰기 값을 낸 뒤 500 으로 실패하던 문제."""

import itertools
import logging
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.external_api.stt.elevenlabs_scribe import (
    MAX_SEGMENT_CHARS,
    MAX_SEGMENT_MS,
    ElevenLabsScribeProvider,
    group_scribe_words,
    split_text,
)
from app.schema.transcript import TranscriptSegment
from main import app

WORD = "할머니가옛날이야기를"  # 10글자


def _monologue(minutes: int, words_per_minute: int = 100, speaker: str = "speaker_0") -> list[dict]:
    """한 사람이 minutes 분 동안 쉬지 않고 말한 Scribe 낱말 목록(낱말 + 띄어쓰기)."""
    out: list[dict] = []
    step = 60.0 / words_per_minute
    for i in range(minutes * words_per_minute):
        start = i * step
        out.append({"text": WORD, "start": start, "end": start + step * 0.8,
                    "speaker_id": speaker, "type": "word", "logprob": -0.1})
        out.append({"text": " ", "start": start + step * 0.8, "end": start + step,
                    "speaker_id": speaker, "type": "spacing"})
    return out


class LongTurnSplitTest(unittest.TestCase):
    def test_hour_long_monologue_fits_the_segment_limit(self) -> None:
        words = _monologue(60)  # 6,000 낱말 ≈ 66,000자(예전엔 구간 하나 → ValidationError)
        segments = group_scribe_words(words)
        self.assertGreater(len(segments), 1)
        for i, seg in enumerate(segments):
            self.assertEqual(seg.segment_index, i)
            self.assertEqual(seg.speaker_label, "SPK_0")
            self.assertLessEqual(len(seg.transcript_text), MAX_SEGMENT_CHARS + len(WORD) + 1)
            self.assertLessEqual(seg.end_ms - seg.start_ms, MAX_SEGMENT_MS + 1000)
            self.assertTrue(seg.transcript_text.startswith(WORD))  # 낱말 경계에서 나눈다
        for left, right in itertools.pairwise(segments):
            self.assertLessEqual(left.end_ms, right.start_ms)
        # 글자는 하나도 잃지 않는다
        total = sum(len(s.transcript_text.split()) for s in segments)
        self.assertEqual(total, 6000)

    def test_slow_long_turn_is_split_by_time(self) -> None:
        # 글자는 적어도(분당 10낱말) 5분이 넘으면 나눈다 — 클론·지문·분석 구간이 한없이 길어지지 않게
        segments = group_scribe_words(_monologue(12, words_per_minute=10))
        self.assertGreaterEqual(len(segments), 3)
        for seg in segments:
            self.assertLessEqual(seg.end_ms - seg.start_ms, MAX_SEGMENT_MS + 6000)

    def test_normal_turns_are_not_split(self) -> None:
        words = _monologue(2) + [
            {"text": "네", "start": 130.0, "end": 130.3, "speaker_id": "speaker_1", "type": "word"}
        ]
        segments = group_scribe_words(words)
        self.assertEqual([s.speaker_label for s in segments], ["SPK_0", "SPK_1"])


class SplitTextTest(unittest.TestCase):
    def test_splits_on_spaces_and_hard_splits_long_runs(self) -> None:
        self.assertEqual(split_text("가나 다라 마바", 5), ["가나 다라", "마바"])
        self.assertEqual(split_text("가" * 12, 5), ["가" * 5, "가" * 5, "가" * 2])
        self.assertEqual(split_text("   ", 5), [])

    @patch("app.external_api.stt.elevenlabs_scribe.forget_transcript")
    @patch("app.external_api.stt.elevenlabs_scribe.httpx.post")
    def test_text_only_fallback_is_chunked(self, post: MagicMock, _forget: MagicMock) -> None:
        text = " ".join([WORD] * 4000)  # 44,000자, 시간 정보 없음
        post.return_value = MagicMock(status_code=200, json=lambda: {"text": text, "words": []})
        provider = ElevenLabsScribeProvider(api_key="k")
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        audio = tmp / "a.m4a"
        audio.write_bytes(b"x")
        result = provider.transcribe(audio)
        self.assertGreater(len(result.segments), 1)
        self.assertEqual([s.segment_index for s in result.segments], list(range(len(result.segments))))
        for seg in result.segments:
            self.assertLessEqual(len(seg.transcript_text), MAX_SEGMENT_CHARS)


class InternalValidationErrorTest(unittest.TestCase):
    """처리 중 스키마 위반은 422 + 코드. 받은 글(가족 대화)을 응답·로그에 남기지 않는다."""

    def test_returns_coded_422_without_echoing_input(self) -> None:
        secret = "할머니비밀이야기"
        try:
            TranscriptSegment(segment_index=0, start_ms=0, end_ms=0,
                              transcript_text=secret * 3000)
        except ValidationError as error:
            raised = error
        client = TestClient(app, raise_server_exceptions=False)
        with patch("app.api.routes.analysis.transcribe_recording", side_effect=raised), \
                patch("app.api.routes.analysis.create_stt_provider"), \
                self.assertLogs("app.error", level=logging.ERROR) as logs:
            response = client.post(
                "/v1/analysis/transcriptions",
                json={"jobId": "00000000-0000-0000-0000-000000000001",
                      "recordingId": "00000000-0000-0000-0000-000000000002",
                      "audioUrl": "https://storage.example.com/a.m4a"},
            )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "AI_DATA_INVALID")
        self.assertNotIn(secret, response.text)
        self.assertNotIn(secret, "\n".join(logs.output))
        self.assertIn("transcript_text", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
