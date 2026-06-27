from typing import Protocol


class ElevenLabsProvider(Protocol):
    """Provider boundary for future speech synthesis capabilities."""

    async def synthesize_speech(
        self,
        *,
        text: str,
        voice_id: str,
        model: str,
        output_format: str,
    ) -> bytes: ...
