from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import SettingsDependency, require_internal_request
from app.pipeline.persona.reflection import run_reflection
from app.pipeline.persona.service import (
    assemble_persona_instructions,
    extract_memory_candidates,
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
from app.schemas.reflection import ReflectionRequest, ReflectionResponse


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
    request: MemoryCandidateRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> MemoryCandidateResponse:
    return extract_memory_candidates(request, settings=settings)


@router.post("/reflection", response_model=ReflectionResponse)
def create_reflection(
    request: ReflectionRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> ReflectionResponse:
    """누적 기억에서 상위 통찰(성향·가치관·반복주제)을 도출 — 페르소나 프로필 재료."""
    return run_reflection(request, settings=settings)
