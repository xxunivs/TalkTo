from uuid import UUID

from pydantic import Field, field_validator

from infra.schema.field_rule import ApiModel
from infra.schema.persona import ConversationMessage, drop_empty_turns


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
    """기억 검색 질문 하나 (job, segment 없음)"""

    text: str = Field(min_length=1, max_length=50_000)
    # 앞 대화 (선택, QUERY_REWRITE=on 이면 최근 4개로 생략된 질문을 채워 임베딩)
    history: list[ConversationMessage] = Field(default_factory=list, max_length=50)

    _drop_empty_history = field_validator("history", mode="before")(drop_empty_turns)


class QueryEmbeddingResponse(ApiModel):
    provider: str
    model: str
    dimensions: int = Field(gt=0)
    embedding: list[float] = Field(min_length=1)
    # 재작성된 질문 (재작성 시에만 포함)
    rewritten_query: str | None = None
