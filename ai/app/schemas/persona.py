from typing import Literal

from pydantic import Field

from app.schemas.common import ApiModel


class ConversationMessage(ApiModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class MemoryContext(ApiModel):
    id: str
    title: str = Field(default="", max_length=500)
    content: str = Field(min_length=1, max_length=50_000)
    tags: list[str] = Field(default_factory=list)


class PersonaContext(ApiModel):
    subject_id: str
    instructions: str = Field(min_length=1, max_length=50_000)
    voice_id: str | None = None


class PersonaResponseRequest(ApiModel):
    message: str = Field(min_length=1, max_length=20_000)
    history: list[ConversationMessage] = Field(default_factory=list)
    memories: list[MemoryContext] = Field(default_factory=list)
    persona: PersonaContext


class PersonaResponseResult(ApiModel):
    content: str
    retrieved_memory_ids: list[str] = Field(default_factory=list)
    provider: str
    model: str


class MemoryCandidateRequest(ApiModel):
    user_message: str = Field(min_length=1, max_length=20_000)
    assistant_message: str = Field(min_length=1, max_length=20_000)
    history: list[ConversationMessage] = Field(default_factory=list)


class MemoryCandidate(ApiModel):
    summary: str
    category: str | None = None
    importance: int = Field(ge=1, le=10)
    confidence: float = Field(ge=0, le=1)


class MemoryCandidateResponse(ApiModel):
    candidates: list[MemoryCandidate]
    provider: str
    model: str
