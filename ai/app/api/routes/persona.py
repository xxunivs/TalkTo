from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import SettingsDependency, require_internal_request
from app.core.errors import FeatureNotImplementedError
from app.pipeline.persona.service import (
    assemble_persona_instructions,
    generate_persona_response,
)
from app.schemas.persona import (
    MemoryCandidateRequest,
    MemoryCandidateResponse,
    PersonaAssemblyRequest,
    PersonaAssemblyResponse,
    PersonaResponseRequest,
    PersonaResponseResult,
)


router = APIRouter(tags=["persona"])
InternalRequest = Annotated[None, Depends(require_internal_request)]


@router.post("/assembly", response_model=PersonaAssemblyResponse)
def create_persona_assembly(
    request: PersonaAssemblyRequest,
    _authorized: InternalRequest,
) -> PersonaAssemblyResponse:
    return assemble_persona_instructions(request)


@router.post("/responses", response_model=PersonaResponseResult)
def create_persona_response(
    request: PersonaResponseRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> PersonaResponseResult:
    return generate_persona_response(request, settings=settings)


@router.post("/memory-candidates", response_model=MemoryCandidateResponse)
def create_memory_candidates(
    _request: MemoryCandidateRequest,
    _authorized: InternalRequest,
) -> MemoryCandidateResponse:
    raise FeatureNotImplementedError("Persona memory candidate extraction")
