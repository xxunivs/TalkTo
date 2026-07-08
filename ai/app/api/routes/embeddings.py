from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import SettingsDependency, require_internal_request
from app.pipeline.embeddings.service import generate_embeddings
from app.schemas.embeddings import EmbeddingRequest, EmbeddingResponse


router = APIRouter(tags=["embeddings"])
InternalRequest = Annotated[None, Depends(require_internal_request)]


@router.post("/embeddings", response_model=EmbeddingResponse)
def create_embeddings(
    request: EmbeddingRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> EmbeddingResponse:
    return generate_embeddings(request, settings=settings)
