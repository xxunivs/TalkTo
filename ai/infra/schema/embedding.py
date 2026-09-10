from uuid import UUID

from pydantic import Field

from infra.schema.field_rule import ApiModel


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


class QueryEmbeddingRequest(ApiModel):
    """기억을 찾을 때 쓰는 질문 하나. 기억 벡터화와 달리 job 도 segment 도 없다."""

    text: str = Field(min_length=1, max_length=50_000)


class QueryEmbeddingResponse(ApiModel):
    provider: str
    model: str
    dimensions: int = Field(gt=0)
    embedding: list[float] = Field(min_length=1)
