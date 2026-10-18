from typing import Annotated

from fastapi import APIRouter, Depends

from infra.be_ai_api.dependencies import SettingsDependency, require_internal_request
from pipeline.s3_synthesis.d_build_memory_embedding import (
    generate_embeddings,
    generate_query_embedding,
)
from infra.schema.embedding import (
    EmbeddingRequest,
    EmbeddingResponse,
    QueryEmbeddingRequest,
    QueryEmbeddingResponse,
)


router = APIRouter(tags=["embeddings"])
InternalRequest = Annotated[None, Depends(require_internal_request)]


@router.post("/embeddings", response_model=EmbeddingResponse)
def create_embeddings(
    request: EmbeddingRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> EmbeddingResponse:
    return generate_embeddings(request, settings=settings)


# exclude_none: 질문을 다시 쓰지 않았으면 rewrittenQuery 칸 자체를 빼서 지금 응답과 같은 모양을 지킨다
@router.post("/embeddings/query", response_model=QueryEmbeddingResponse, response_model_exclude_none=True)
def create_query_embedding(
    request: QueryEmbeddingRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> QueryEmbeddingResponse:
    """대화 중 기억을 찾을 때 쓴다. /embeddings 는 job 과 segment id 를 요구해서
    질문 하나를 벡터로 만드는 데는 쓸 수 없다."""
    return generate_query_embedding(request, settings=settings)
