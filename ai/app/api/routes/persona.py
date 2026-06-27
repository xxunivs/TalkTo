from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import require_internal_request
from app.core.errors import FeatureNotImplementedError
from app.schemas.persona import (
    MemoryCandidateRequest,
    MemoryCandidateResponse,
    PersonaResponseRequest,
    PersonaResponseResult,
)


router = APIRouter(tags=["persona"])
InternalRequest = Annotated[None, Depends(require_internal_request)]


@router.post("/responses", response_model=PersonaResponseResult)
def create_persona_response(
    _request: PersonaResponseRequest,
    _authorized: InternalRequest,
) -> PersonaResponseResult:
    raise FeatureNotImplementedError("Persona response generation")


@router.post("/memory-candidates", response_model=MemoryCandidateResponse)
def create_memory_candidates(
    _request: MemoryCandidateRequest,
    _authorized: InternalRequest,
) -> MemoryCandidateResponse:
    raise FeatureNotImplementedError("Persona memory candidate extraction")
