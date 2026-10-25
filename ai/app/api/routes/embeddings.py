from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import SettingsDependency, require_internal_request
from app.schema.embedding import (
    EmbeddingRequest,
    EmbeddingResponse,
    QueryEmbeddingRequest,
    QueryEmbeddingResponse,
)
from pipeline.s3_synthesis.d_build_memory_embedding import (
    generate_embeddings,
    generate_query_embedding,
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


# exclude_none: 질문 재작성이 없으면 rewrittenQuery 칸 생략 (기존 응답 모양 유지)
@router.post(
    "/embeddings/query", response_model=QueryEmbeddingResponse, response_model_exclude_none=True
)
def create_query_embedding(
    request: QueryEmbeddingRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> QueryEmbeddingResponse:
    """대화 중 기억 검색용 질문 임베딩 (/embeddings 는 job, segment id 필수라 별도)"""
    return generate_query_embedding(request, settings=settings)
