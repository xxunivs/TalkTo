from dataclasses import dataclass
from typing import Protocol


@dataclass
class TTSResult:
    audio: bytes
    content_type: str
    provider: str
    model: str


class TTSProvider(Protocol):
    """대상자(고인) 목소리로 텍스트를 음성 합성하는 provider 경계."""

    def synthesize(self, text: str, *, voice_id: str) -> TTSResult: ...
