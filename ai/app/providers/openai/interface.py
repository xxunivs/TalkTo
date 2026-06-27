from typing import Protocol, Sequence


class OpenAIProvider(Protocol):
    """Provider boundary for future OpenAI-backed capabilities."""

    async def create_chat_response(
        self,
        *,
        messages: Sequence[dict[str, str]],
        model: str,
    ) -> str: ...

    async def create_structured_analysis(
        self,
        *,
        instructions: str,
        input_text: str,
        model: str,
    ) -> dict[str, object]: ...

    async def transcribe_audio(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        model: str,
        language: str,
    ) -> dict[str, object]: ...

    async def create_embeddings(
        self,
        *,
        inputs: Sequence[str],
        model: str,
        dimensions: int,
    ) -> list[list[float]]: ...
