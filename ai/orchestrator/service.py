"""BE 계약(/v1/persona/responses) <-> 중간계층 (세션이 프로세스 메모리라 오케스트레이터는 프로세스당 하나)"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime, timezone
from pathlib import Path

from app.schema.persona import (
    MemoryContext,
    MemoryGap,
    PersonaResponseRequest,
    PersonaResponseResult,
)
from app.setting import Settings
from orchestrator.assembler import LegacyAssembler, StructuredAssembler
from orchestrator.contracts import MemoryItem, StyleFix, Turn, TurnInput
from orchestrator.guard import GuardV1, GuardV11, PassGuard
from orchestrator.lang_guard import LanguageGuard
from orchestrator.llm import OpenAIChatLLM
from orchestrator.memory import (
    CachedEmbedder,
    EmbedOnDemandVectors,
    OpenAIEmbedder,
    PassThroughSelector,
    RequestMemoryStore,
    TieredSelector,
)
from orchestrator.minor_guard import MinorGuard
from orchestrator.persona import PersonaRegistry
from orchestrator.pipeline import PersonaOrchestrator, TurnRun
from orchestrator.post_turn import RecordTurn
from orchestrator.precheck import CoveragePrecheck, CoveragePrecheckV11, NoopPrecheck
from orchestrator.restricted import RestrictedSelector
from orchestrator.session import InMemorySessionStore
from orchestrator.voice import VoiceMarks
from pipeline.s3_synthesis.c_build_system_prompt import warn_if_over_full_max
from pipeline.safety.minor_guard import blocked_result, input_blocked, is_minor, safe_history

log = logging.getLogger(__name__)
_lock = threading.Lock()
_instance: PersonaOrchestrator | None = None


def memory_from_context(m: MemoryContext) -> MemoryItem:
    return MemoryItem(
        id=m.id,
        title=m.title,
        text=m.content,
        tags=list(m.tags),
        memory_type=m.memory_type or "LONG_TERM",
    )


def turn_from_request(request: PersonaResponseRequest) -> TurnInput:
    minor = is_minor(request.persona.minor, request.persona.deceased)
    # 미성년이면 입력 검사에 걸린 가족 말과 그 대답을 앞 대화에서 제외 (기존 경로와 동일)
    history = safe_history(request.history) if minor else request.history
    return TurnInput(
        subject_id=request.persona.subject_id,
        message=request.message,
        persona_instructions=request.persona.instructions,
        history=[Turn(role=h.role, content=h.content) for h in history],
        be_memories=[memory_from_context(m) for m in request.memories],
        memory_total=request.memory_total,
        session_id=request.session_id,
        speaker_hint=request.speaker_hint,
        voice_id=request.persona.voice_id,
        deceased=request.persona.deceased,
        flag_gaps=request.flag_gaps,
        style_examples=[
            StyleFix(original=s.original, corrected=s.corrected) for s in request.style_examples
        ],
        minor=minor,
    )


def build_registry(settings: Settings) -> PersonaRegistry:
    return PersonaRegistry(settings.orchestrator_persona_dir)


def build_voice(settings: Settings) -> VoiceMarks:
    """FISH_EMOTION: off(기본) | cue | inline (Fish 목소리 대답 전용)"""
    return VoiceMarks(getattr(settings, "fish_emotion", "off"))


def build_assembler(
    settings: Settings, kind: str | None = None, registry: PersonaRegistry | None = None
):
    kind = kind or settings.orchestrator_assembler
    late = settings.style_late_note == "on"
    voice = build_voice(settings)
    # None = 기억이 적을 때 절 끔
    low = settings.low_memory_max if settings.low_memory_note == "on" else None
    if kind == "structured":
        return StructuredAssembler(
            registry or build_registry(settings),
            header=settings.orchestrator_guard in ("v1", "v11"),
            rules=settings.orchestrator_rules,
            shots_mode=settings.orchestrator_shots,
            late_note=late,
            voice=voice,
            low_memory_max=low,
        )
    return LegacyAssembler(late_note=late, voice=voice, low_memory_max=low)


def build_guard(
    settings: Settings, kind: str | None = None, registry: PersonaRegistry | None = None
):
    """v1, v11: 헤더, 누출, 지어내기, 애정, 챙김 검사 (맨 바깥은 설정 무관하게 미성년 가드)"""
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
    return MinorGuard(guard)


def build_inner_selector(
    settings: Settings, registry: PersonaRegistry | None = None, embedder=None
):
    """ORCHESTRATOR_SELECTOR: passthrough(기존) | tiered(장기 전부 + 단기 분해 검색)."""
    if settings.orchestrator_selector == "tiered":
        emb = embedder or CachedEmbedder(OpenAIEmbedder(settings))
        return TieredSelector(
            registry or build_registry(settings),
            emb,
            EmbedOnDemandVectors(emb),
            st_top_k=settings.orchestrator_selector_st_top_k,
            threshold=settings.orchestrator_selector_threshold,
        )
    return PassThroughSelector()


def build_selector(
    settings: Settings,
    kind: str | None = None,
    registry: PersonaRegistry | None = None,
    embedder=None,
):
    """structured 경로는 제한 정보(restricted.json) 기억 제외, legacy 는 기존과 동일"""
    kind = kind or settings.orchestrator_assembler
    inner = build_inner_selector(settings, registry, embedder)
    if kind == "structured":
        return RestrictedSelector(inner, registry or build_registry(settings))
    return inner


def build_precheck(
    settings: Settings, kind: str | None = None, registry: PersonaRegistry | None = None
):
    kind = kind or settings.orchestrator_precheck
    if kind in ("coverage", "coverage_v11"):
        from orchestrator.text import morphs

        morphs("준비")  # 형태소 분석기 로드(약 1.6초)를 첫 사용자 응답 대신 여기서
        cls = CoveragePrecheckV11 if kind == "coverage_v11" else CoveragePrecheck
        return cls(
            registry or build_registry(settings),
            threshold=settings.orchestrator_precheck_threshold,
            min_memories=settings.orchestrator_precheck_min_memories,
        )
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
        voice=build_voice(settings),
    )


def get_orchestrator(settings: Settings) -> PersonaOrchestrator:
    global _instance
    with _lock:
        if _instance is None:
            _instance = build_default(settings)
            log.info("persona orchestrator on: %s", _instance.describe())
        return _instance


def reset_orchestrator() -> None:
    """테스트용 초기화"""
    global _instance
    with _lock:
        _instance = None


def write_trace(
    trace_dir: Path, run: TurnRun, components: dict[str, str], flags: dict | None = None
) -> None:
    trace_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(UTC).strftime("%Y%m%d")
    row = {
        "at": datetime.now(UTC).isoformat(),
        "components": components,
        **({"flags": flags} if flags else {}),
        "turn": run.turn.model_dump(),
        "state": run.state.model_dump() if run.state else None,
        "trace": [t.model_dump() for t in run.trace],
        "result": run.result.model_dump(exclude={"trace"}) if run.result else None,
    }
    with (trace_dir / f"turns_{day}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def orchestrate_persona_response(
    request: PersonaResponseRequest,
    *,
    settings: Settings,
    orchestrator: PersonaOrchestrator | None = None,
) -> PersonaResponseResult:
    over = warn_if_over_full_max(len(request.memories), settings)
    turn = turn_from_request(request)
    if turn.minor and input_blocked(request.message):
        # 미성년 상대의 성적 이야기, 비밀 만남: 모델 미호출 (기존 경로와 같은 대체 글, 세션 미기록)
        log.info("minor guard skipped the model for subject %s", turn.subject_id)
        return blocked_result(turn.persona_instructions)
    orch = orchestrator or get_orchestrator(settings)
    run = orch.run(turn)
    # 턴마다 입력, 추적, 결과 jsonl 기록 (대화 원문 포함이라 git 제외 경로)
    if settings.orchestrator_trace_dir:
        flags = (
            {"memory_over_full_max": {"n": len(request.memories), "max": settings.memory_full_max}}
            if over
            else None
        )
        try:
            write_trace(settings.orchestrator_trace_dir, run, orch.describe(), flags)
        except OSError:  # 추적 로그 실패로 응답 차단 방지
            log.exception("orchestrator trace write failed")
    res = run.result
    gap = MemoryGap(topic=res.memory_gap) if res.memory_gap is not None else None
    if any(r.startswith("minor:") for r in run.guard.reasons):
        gap = None  # 미성년 가드가 대답을 바꿨으면 그 화제로 가족에게 묻지 않음 (기존 경로와 동일)
    return PersonaResponseResult(
        content=res.content,
        retrieved_memory_ids=res.retrieved_memory_ids,
        emotion=res.emotion,
        speech_text=res.speech_text,
        memory_gap=gap,
        provider=res.provider,
        model=res.model,
    )
