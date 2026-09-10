from typing import Annotated

from fastapi import APIRouter, Depends

from infra.be_ai_api.dependencies import SettingsDependency, require_internal_request
from pipeline.s3_synthesis.c_build_memory_embedding import generate_embeddings
from infra.schema.embedding import EmbeddingRequest, EmbeddingResponse


router = APIRouter(tags=["embeddings"])
InternalRequest = Annotated[None, Depends(require_internal_request)]


@router.post("/embeddings", response_model=EmbeddingResponse)
def create_embeddings(
    request: EmbeddingRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> EmbeddingResponse:
    return generate_embeddings(request, settings=settings)
