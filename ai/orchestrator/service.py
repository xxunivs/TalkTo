"""BE 계약(/v1/persona/responses) ↔ 중간계층.

- 요청·응답 모양은 기존과 같다. 기억 원본은 BE 가 보낸 top-5(RequestMemoryStore).
- 세션 상태가 프로세스 메모리에 있으므로 오케스트레이터는 프로세스당 하나(get_orchestrator).
- ORCHESTRATOR_TRACE_DIR 를 주면 턴마다 입력·단계 추적·결과를 jsonl 로 남긴다(대화 원문 포함 → git 제외 경로에).
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from infra.schema.persona import MemoryContext, PersonaResponseRequest, PersonaResponseResult
from infra.setting import Settings
from orchestrator.assembler import LegacyAssembler, StructuredAssembler
from orchestrator.lang_guard import LanguageGuard
from orchestrator.contracts import MemoryItem, Turn, TurnInput
from orchestrator.guard import GuardV1, GuardV11, PassGuard
from orchestrator.llm import OpenAIChatLLM
from orchestrator.memory import (
    CachedEmbedder, EmbedOnDemandVectors, OpenAIEmbedder, PassThroughSelector, RequestMemoryStore, TieredSelector,
)
from orchestrator.persona import PersonaRegistry
from orchestrator.restricted import RestrictedSelector
from orchestrator.pipeline import PersonaOrchestrator, TurnRun
from orchestrator.post_turn import RecordTurn
from orchestrator.precheck import CoveragePrecheck, CoveragePrecheckV11, NoopPrecheck
from orchestrator.session import InMemorySessionStore
from pipeline.s3_synthesis.c_build_system_prompt import warn_if_over_full_max

log = logging.getLogger(__name__)
_lock = threading.Lock()
_instance: PersonaOrchestrator | None = None


def memory_from_context(m: MemoryContext) -> MemoryItem:
    return MemoryItem(id=m.id, title=m.title, text=m.content, tags=list(m.tags),
                      memory_type=m.memory_type or "LONG_TERM")


def turn_from_request(request: PersonaResponseRequest) -> TurnInput:
    return TurnInput(
        subject_id=request.persona.subject_id,
        message=request.message,
        persona_instructions=request.persona.instructions,
        history=[Turn(role=h.role, content=h.content) for h in request.history],
        be_memories=[memory_from_context(m) for m in request.memories],
        session_id=request.session_id,
        speaker_hint=request.speaker_hint,
        voice_id=request.persona.voice_id,
    )


def build_registry(settings: Settings) -> PersonaRegistry:
    return PersonaRegistry(settings.orchestrator_persona_dir)


def build_assembler(settings: Settings, kind: str | None = None, registry: PersonaRegistry | None = None):
    kind = kind or settings.orchestrator_assembler
    late = settings.style_late_note == "on"
    if kind == "structured":
        return StructuredAssembler(registry or build_registry(settings), header=settings.orchestrator_guard in ("v1", "v11"),
                                   rules=settings.orchestrator_rules, shots_mode=settings.orchestrator_shots, late_note=late)
    return LegacyAssembler(late_note=late)


def build_guard(settings: Settings, kind: str | None = None, registry: PersonaRegistry | None = None):
    """v1: 헤더(structured 일 때)·누출·지어내기·애정·챙김 검사. legacy 조립이면 헤더 없이 넣은 기억 전부를 근거로 본다."""
    kind = kind or settings.orchestrator_guard
    registry = registry or build_registry(settings)
    if kind in ("v1", "v11"):
        from orchestrator.text import morphs
        morphs("준비")
        guard = (GuardV11 if kind == "v11" else GuardV1)(registry)
    else:
        guard = PassGuard()
    if settings.orchestrator_lang_guard != "off":
        guard = LanguageGuard(guard, registry, mode=settings.orchestrator_lang_guard)
    return guard


def build_inner_selector(settings: Settings, registry: PersonaRegistry | None = None, embedder=None):
    """ORCHESTRATOR_SELECTOR: passthrough(기존) | tiered(장기 전부 + 단기 분해 검색)."""
    if settings.orchestrator_selector == "tiered":
        emb = embedder or CachedEmbedder(OpenAIEmbedder(settings))
        return TieredSelector(registry or build_registry(settings), emb, EmbedOnDemandVectors(emb),
                              st_top_k=settings.orchestrator_selector_st_top_k,
                              threshold=settings.orchestrator_selector_threshold)
    return PassThroughSelector()


def build_selector(settings: Settings, kind: str | None = None, registry: PersonaRegistry | None = None,
                   embedder=None):
    """structured 경로면 제한 정보(restricted.json)의 기억을 뺀다. legacy 는 기존과 동일."""
    kind = kind or settings.orchestrator_assembler
    inner = build_inner_selector(settings, registry, embedder)
    if kind == "structured":
        return RestrictedSelector(inner, registry or build_registry(settings))
    return inner


def build_precheck(settings: Settings, kind: str | None = None, registry: PersonaRegistry | None = None):
    kind = kind or settings.orchestrator_precheck
    if kind in ("coverage", "coverage_v11"):
        from orchestrator.text import morphs
        morphs("준비")  # 형태소 분석기 로드(약 1.6초)를 첫 사용자 응답이 아니라 여기서 치른다
        cls = CoveragePrecheckV11 if kind == "coverage_v11" else CoveragePrecheck
        return cls(registry or build_registry(settings), threshold=settings.orchestrator_precheck_threshold)
    return NoopPrecheck()


def build_default(settings: Settings) -> PersonaOrchestrator:
    registry = build_registry(settings)
    return PersonaOrchestrator(
        sessions=InMemorySessionStore(),
        memories=RequestMemoryStore(),
        selector=build_selector(settings, registry=registry),
        precheck=build_precheck(settings, registry=registry),
        assembler=build_assembler(settings, registry=registry),
        llm=OpenAIChatLLM(settings),
        guard=build_guard(settings, registry=registry),
        post_turn=RecordTurn(),
    )


def get_orchestrator(settings: Settings) -> PersonaOrchestrator:
    global _instance
    with _lock:
        if _instance is None:
            _instance = build_default(settings)
            log.info("persona orchestrator on: %s", _instance.describe())
        return _instance


def reset_orchestrator() -> None:
    """테스트용."""
    global _instance
    with _lock:
        _instance = None


def write_trace(trace_dir: Path, run: TurnRun, components: dict[str, str], flags: dict | None = None) -> None:
    trace_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    row = {
        "at": datetime.now(timezone.utc).isoformat(),
        "components": components,
        **({"flags": flags} if flags else {}),
        "turn": run.turn.model_dump(),
        "state": run.state.model_dump() if run.state else None,
        "trace": [t.model_dump() for t in run.trace],
        "result": run.result.model_dump(exclude={"trace"}) if run.result else None,
    }
    with (trace_dir / f"turns_{day}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def orchestrate_persona_response(request: PersonaResponseRequest, *, settings: Settings,
                                 orchestrator: PersonaOrchestrator | None = None) -> PersonaResponseResult:
    over = warn_if_over_full_max(len(request.memories), settings)
    orch = orchestrator or get_orchestrator(settings)
    run = orch.run(turn_from_request(request))
    if settings.orchestrator_trace_dir:
        flags = {"memory_over_full_max": {"n": len(request.memories), "max": settings.memory_full_max}} if over else None
        try:
            write_trace(settings.orchestrator_trace_dir, run, orch.describe(), flags)
        except OSError:  # 추적 로그 실패가 응답을 막으면 안 된다
            log.exception("orchestrator trace write failed")
    res = run.result
    return PersonaResponseResult(content=res.content, retrieved_memory_ids=res.retrieved_memory_ids,
                                 provider=res.provider, model=res.model)
