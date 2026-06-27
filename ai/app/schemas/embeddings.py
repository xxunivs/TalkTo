from uuid import UUID

from pydantic import Field

from app.schemas.common import ApiModel


class EmbeddingInput(ApiModel):
    memory_segment_id: UUID
    embedding_index: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=50_000)


class EmbeddingRequest(ApiModel):
    job_id: UUID
    items: list[EmbeddingInput] = Field(min_length=1)


class EmbeddingResult(ApiModel):
    memory_segment_id: UUID
    embedding_index: int = Field(ge=0)
    provider: str
    model: str
    dimensions: int = Field(gt=0)
    embedding: list[float] = Field(min_length=1)


class EmbeddingResponse(ApiModel):
    embeddings: list[EmbeddingResult]
