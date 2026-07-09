from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class TTSResult:
    audio: bytes
    content_type: str
    provider: str
    model: str


class TTSProvider(Protocol):
    """대상자(고인) 목소리 provider 경계 — 합성(TTS) + 샘플로 클론 등록."""

    def synthesize(self, text: str, *, voice_id: str, speed: float = 1.0) -> TTSResult: ...

    def clone_voice(self, name: str, audio_path: Path) -> str: ...
