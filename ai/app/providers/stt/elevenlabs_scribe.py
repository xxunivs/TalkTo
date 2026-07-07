"""ElevenLabs Scribe STT with built-in diarization (bake-off winner, 2026-07-04)."""

import math
import re
from pathlib import Path

import httpx

from app.core.errors import STTProviderError
from app.providers.stt.interface import STTResult
from app.schemas.transcript import TranscriptSegment

ELEVENLABS_BASE = "https://api.elevenlabs.io"

# Scribe가 무음·소리를 텍스트로 서술하는 패턴 — tag_audio_events를 꺼도
# 새어 나온다 (외부 데이터 일반화 테스트에서 발견: "(2초간 멈춤)", "(침묵)").
# 발화가 아니므로 전사에서 제거한다
_NON_SPEECH_NOTE = re.compile(
    r"\(\s*(?:\d+\s*초[^)]*|침묵|멈춤|웃음|한숨|박수|기침|음악|잡음)[^)]*\)"
)


def strip_non_speech_notes(text: str) -> str:
    return " ".join(_NON_SPEECH_NOTE.sub(" ", text).split())


class ElevenLabsScribeProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model_id: str = "scribe_v1",
        timeout_seconds: float = 1800.0,
    ) -> None:
        self._api_key = api_key
        self.model_id = model_id
        self.timeout_seconds = timeout_seconds

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str = "ko",
        speaker_diarization: bool = True,
        num_speakers: int | None = None,
    ) -> STTResult:
        data: dict = {
            "model_id": self.model_id,
            "diarize": "true" if speaker_diarization else "false",
            "tag_audio_events": "false",
        }
        if language:
            data["language_code"] = language
        if num_speakers:
            data["num_speakers"] = str(num_speakers)

        with audio_path.open("rb") as audio_file:
            response = httpx.post(
                f"{ELEVENLABS_BASE}/v1/speech-to-text",
                headers={"xi-api-key": self._api_key},
                files={"file": (audio_path.name, audio_file)},
                data=data,
                timeout=self.timeout_seconds,
            )
        if response.status_code != 200:
            raise STTProviderError(
                f"Scribe error {response.status_code}: {response.text[:500]}"
            )
        body = response.json()

        segments = group_scribe_words(body.get("words") or [])
        if not segments and (body.get("text") or "").strip():
            segments = [
                TranscriptSegment(
                    segment_index=0,
                    start_ms=0,
                    end_ms=0,
                    speaker_label="SPK_0",
                    transcript_text=strip_non_speech_notes(body["text"]),
                )
            ]
        return STTResult(provider="elevenlabs", model=self.model_id, segments=segments)


def group_scribe_words(words: list[dict]) -> list[TranscriptSegment]:
    """Group Scribe word entries into one segment per speaker turn."""
    segments: list[TranscriptSegment] = []
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current and strip_non_speech_notes(current["text"]):
            logprobs: list[float] = current["logprobs"]
            confidence = (
                round(math.exp(sum(logprobs) / len(logprobs)), 4) if logprobs else None
            )
            segments.append(
                TranscriptSegment(
                    segment_index=len(segments),
                    start_ms=current["startMs"],
                    end_ms=current["endMs"],
                    speaker_label=current["speakerLabel"],
                    transcript_text=strip_non_speech_notes(current["text"]),
                    confidence=confidence,
                )
            )
        current = None

    for word in words:
        if word.get("type") == "audio_event":
            continue
        speaker = word.get("speaker_id") or "speaker_0"
        label = f"SPK_{speaker.rsplit('_', 1)[-1]}"
        start_ms = int(float(word.get("start") or 0) * 1000)
        end_ms = int(float(word.get("end") or 0) * 1000)
        if current is None or current["speakerLabel"] != label:
            flush()
            current = {
                "speakerLabel": label,
                "startMs": start_ms,
                "endMs": end_ms,
                "text": "",
                "logprobs": [],
            }
        current["text"] += word.get("text") or ""
        current["endMs"] = max(current["endMs"], end_ms)
        if word.get("type") == "word" and isinstance(word.get("logprob"), (int, float)):
            current["logprobs"].append(float(word["logprob"]))
    flush()
    return segments
