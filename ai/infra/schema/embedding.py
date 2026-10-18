from uuid import UUID

from pydantic import Field

from infra.schema.field_rule import ApiModel
from infra.schema.persona import ConversationMessage


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
    # 선택: 앞 대화. QUERY_REWRITE=on 이면 최근 메시지 4개로 생략된 질문("얼마나 넣어?")을 채워 그 문장을 임베딩한다.
    history: list[ConversationMessage] = Field(default_factory=list, max_length=50)


class QueryEmbeddingResponse(ApiModel):
    provider: str
    model: str
    dimensions: int = Field(gt=0)
    embedding: list[float] = Field(min_length=1)
    # 다시 쓴 질문(다시 쓴 때만 들어간다. 원문 그대로 임베딩했으면 칸이 없다)
    rewritten_query: str | None = None
