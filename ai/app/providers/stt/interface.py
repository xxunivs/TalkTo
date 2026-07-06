from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.schemas.transcript import TranscriptSegment


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
