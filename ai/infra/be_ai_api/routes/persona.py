from typing import Annotated

from fastapi import APIRouter, Depends

from infra.be_ai_api.dependencies import SettingsDependency, require_internal_request
from infra.schema.insight import ReflectionRequest, ReflectionResponse
from infra.schema.persona import (
    GapQuestionRequest,
    GapQuestionResponse,
    MemoryCandidateRequest,
    MemoryCandidateResponse,
    PersonaAssemblyRequest,
    PersonaAssemblyResponse,
    PersonaResponseRequest,
    PersonaResponseResult,
)
from orchestrator.service import orchestrate_persona_response
from pipeline.s3_synthesis.b_extract_insight import run_reflection
from pipeline.s3_synthesis.c_build_system_prompt import (
    assemble_persona_instructions,
    extract_memory_candidates,
    generate_persona_response,
)
from pipeline.s3_synthesis.e_memory_gap import make_gap_question

router = APIRouter(tags=["persona"])
InternalRequest = Annotated[None, Depends(require_internal_request)]


@router.post("/assembly", response_model=PersonaAssemblyResponse)
def create_persona_assembly(
    request: PersonaAssemblyRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> PersonaAssemblyResponse:
    # 기능 플래그: PERSONA_PROMPT_BUILDER=layered 면 4층 프롬프트, 아니면 기존 조립(요청·응답 계약 동일)
    return assemble_persona_instructions(request, settings)


@router.post("/responses", response_model=PersonaResponseResult)
def create_persona_response(
    request: PersonaResponseRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> PersonaResponseResult:
    # 기능 플래그: PERSONA_ORCHESTRATOR=1 이면 중간계층, 아니면 기존 경로(요청·응답 계약 동일).
    # 가져온 지침(fixedPrompt)은 늘 기존 경로다.
    if settings.persona_orchestrator and not request.persona.fixed_prompt:
        return orchestrate_persona_response(request, settings=settings)
    return generate_persona_response(request, settings=settings)


@router.post("/gap-question", response_model=GapQuestionResponse, response_model_exclude_none=True)
def create_gap_question(
    request: GapQuestionRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> GapQuestionResponse:
    """대화가 끝난 뒤(BE 뒤 작업) 기억 빈틈 후보 하나 → 가족에게 물을 질문 / 열린 질문에 합치기 / 건너뛰기.

    대답 경로 밖의 LLM 호출이다. 대화 중에는 부르지 않는다.
    """
    return make_gap_question(request, settings=settings)


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
