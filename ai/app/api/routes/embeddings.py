from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import require_internal_request
from app.core.errors import FeatureNotImplementedError
from app.schemas.embeddings import EmbeddingRequest, EmbeddingResponse


router = APIRouter(tags=["embeddings"])
InternalRequest = Annotated[None, Depends(require_internal_request)]


@router.post("/embeddings", response_model=EmbeddingResponse)
def create_embeddings(
    _request: EmbeddingRequest,
    _authorized: InternalRequest,
) -> EmbeddingResponse:
    raise FeatureNotImplementedError("Embedding generation")
