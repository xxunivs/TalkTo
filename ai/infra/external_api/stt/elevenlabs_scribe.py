"""ElevenLabs Scribe STT with built-in diarization."""

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from infra.error import STTProviderError
from infra.external_api.elevenlabs_records import forget_transcript
from infra.schema.transcript import TranscriptSegment


@dataclass
class STTResult:
    provider: str
    model: str
    segments: list[TranscriptSegment]


class STTProvider(Protocol):
    """Provider boundary for speech-to-text with speaker diarization."""

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str,
        speaker_diarization: bool,
        num_speakers: int | None = None,
    ) -> STTResult: ...


ELEVENLABS_BASE = "https://api.elevenlabs.io"

# Scribe가 무음·소리를 텍스트로 서술하는 패턴 — tag_audio_events를 꺼도
# 새어 나온다 (예: "(2초간 멈춤)", "(침묵)").
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
        model_id: str = "scribe_v2",
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
            raise STTProviderError(f"Scribe error {response.status_code}: {response.text[:500]}")
        body = response.json()
        # ElevenLabs 계정에 남는 받아쓰기 기록(보낸 녹음·받아쓴 글)을 바로 지운다
        forget_transcript(self._api_key, body)

        segments = group_scribe_words(body.get("words") or [])
        if not segments and (body.get("text") or "").strip():
            # 시간 정보 없는 글만 온 경우. 길면 나눈다(구간 하나 20,000자 한도, transcript.py)
            segments = [
                TranscriptSegment(
                    segment_index=i,
                    start_ms=0,
                    end_ms=0,
                    speaker_label="SPK_0",
                    transcript_text=piece,
                )
                for i, piece in enumerate(
                    split_text(strip_non_speech_notes(body["text"]), MAX_SEGMENT_CHARS)
                )
            ]
        return STTResult(provider="elevenlabs", model=self.model_id, segments=segments)


# 한 사람이 이어 말한 구간 하나의 상한. 화자가 바뀔 때만 나누면 혼자 한 시간 가까이 말한 녹음(이야기·음성 일기)이
# 구간 하나가 되어 20,000자 한도(transcript.py)를 넘었고, 받아쓰기 값을 낸 뒤 500 으로 실패했다(다시 해도 같음).
# 보통 통화의 한 번 말하기(몇십 초~1분)는 나뉘지 않을 만큼 넉넉하게 둔다. 나눌 때는 낱말 경계에서, 같은 화자로.
MAX_SEGMENT_CHARS = 5_000
MAX_SEGMENT_MS = 300_000  # 5분


def split_text(text: str, limit: int) -> list[str]:
    """글을 limit 글자 이하 조각으로(띄어쓰기에서). 띄어쓰기 없이 긴 덩어리는 글자 수로 자른다."""
    pieces: list[str] = []
    current = ""
    for word in text.split():
        while len(word) > limit:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(word[:limit])
            word = word[limit:]
        if not word:
            continue
        if current and len(current) + 1 + len(word) > limit:
            pieces.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        pieces.append(current)
    return pieces


def group_scribe_words(words: list[dict]) -> list[TranscriptSegment]:
    """Group Scribe word entries into one segment per speaker turn.

    A very long turn is split at a word boundary (same speaker) once it passes
    MAX_SEGMENT_CHARS or MAX_SEGMENT_MS.
    """
    segments: list[TranscriptSegment] = []
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current and strip_non_speech_notes(current["text"]):
            logprobs: list[float] = current["logprobs"]
            confidence = round(math.exp(sum(logprobs) / len(logprobs)), 4) if logprobs else None
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
        too_long = (
            current is not None
            and word.get("type", "word")
            == "word"  # 낱말 앞에서만 나눈다(띄어쓰기에서 시작하지 않게)
            and (
                len(current["text"]) >= MAX_SEGMENT_CHARS
                or current["endMs"] - current["startMs"] >= MAX_SEGMENT_MS
            )
        )
        if current is None or current["speakerLabel"] != label or too_long:
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
