"""단계 연결. 순서를 아는 곳은 여기 하나뿐이다.

run(turn, until=…) 으로 중간 단계에서 멈출 수 있다 → 모듈 하나만 따로 돌려 보기(CLI --until).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from infra.error import EmptyReplyError
from orchestrator.contracts import (
    LLM, Assembler, Generation, Guard, GuardResult, MemorySelection, MemorySelector, MemoryStore,
    PostTurn, Precheck, PrecheckResult, PromptBundle, SessionState, SessionStore, StageTrace,
    TurnInput, TurnResult,
)
from orchestrator.voice import VoiceMarks, VoiceRun

STAGES = ("session", "select", "precheck", "assemble", "generate", "guard", "post_turn")


@dataclass
class TurnRun:
    """한 턴 동안 각 단계가 낸 값. until 로 멈추면 뒤 단계 값은 None."""
    turn: TurnInput
    state: SessionState | None = None
    selection: MemorySelection | None = None
    precheck: PrecheckResult | None = None
    bundle: PromptBundle | None = None
    # 모델이 낸 글 그대로(음성 표시 포함). 가드에는 voice.guard_text 가 간다.
    generation: Generation | None = None
    # 음성 표시를 떼어 낸 결과(FISH_EMOTION 이 켜진 Fish 목소리일 때만)
    voice: VoiceRun | None = None
    guard: GuardResult | None = None
    next_state: SessionState | None = None
    result: TurnResult | None = None
    trace: list[StageTrace] = field(default_factory=list)


@dataclass
class PersonaOrchestrator:
    sessions: SessionStore
    memories: MemoryStore
    selector: MemorySelector
    precheck: Precheck
    assembler: Assembler
    llm: LLM
    guard: Guard
    post_turn: PostTurn
    # 생성 뒤·가드 앞 음성 표시 떼기(FISH_EMOTION). 기본 off 면 아무것도 하지 않는다.
    voice: VoiceMarks = field(default_factory=VoiceMarks)

    def describe(self) -> dict[str, str]:
        return {
            "session": self.sessions.impl, "memory_store": self.memories.impl, "select": self.selector.impl,
            "precheck": self.precheck.impl, "assemble": self.assembler.impl, "generate": self.llm.impl,
            "voice": self.voice.impl, "guard": self.guard.impl, "post_turn": self.post_turn.impl,
        }

    def _timed(self, run: TurnRun, stage: str, impl: str, fn, summarize):
        t0 = time.perf_counter()
        out = fn()
        run.trace.append(StageTrace(stage=stage, impl=impl, ms=round((time.perf_counter() - t0) * 1000, 2),
                                    summary=summarize(out)))
        return out

    def run(self, turn: TurnInput, *, until: str | None = None, state: SessionState | None = None) -> TurnRun:
        """state 를 주면 저장소 대신 그것을 쓰고, 끝나도 저장하지 않는다(fixture 재현용)."""
        if until is not None and until not in STAGES:
            raise ValueError(f"until must be one of {STAGES}")
        r = TurnRun(turn=turn)
        given_state = state is not None

        r.state = state if given_state else self._timed(
            r, "session", self.sessions.impl, lambda: self.sessions.load(turn),
            lambda s: {"turn_index": s.turn_index, "speaker": s.speaker, "recent": len(s.recent_turns)})
        if until == "session":
            return r
        r.selection = self._timed(
            r, "select", self.selector.impl, lambda: self.selector.select(turn, r.state, self.memories),
            lambda m: {"n": len(m.memories), "delta": len(m.delta), "strategy": m.strategy})
        if until == "select":
            return r
        r.precheck = self._timed(
            r, "precheck", self.precheck.impl, lambda: self.precheck.check(turn, r.state, r.selection),
            lambda p: {"known": p.known, "coverage": p.coverage, "directive": bool(p.directive)})
        if until == "precheck":
            return r
        r.bundle = self._timed(
            r, "assemble", self.assembler.impl,
            lambda: self.assembler.assemble(turn, r.state, r.selection, r.precheck),
            lambda b: {"sections": [f"{s.name}:{len(s.content)}" for s in b.sections]})
        if until == "assemble":
            return r
        r.generation = self._timed(
            r, "generate", self.llm.impl, lambda: self.llm.generate(r.bundle.messages()),
            lambda g: {"model": g.model, "chars": len(g.text), **g.usage})
        if until == "generate":
            return r
        # 감정 이름·연기 표시는 가드 앞에서 뗀다. 가드·언어 가드·턴 뒤 처리는 표시 없는 글만 본다.
        clean = r.generation
        if self.voice.mode(turn) != "off":
            r.voice = self._timed(r, "voice", self.voice.impl, lambda: self.voice.split(r.generation.text, turn),
                                  lambda v: v.summary())
            clean = r.generation.model_copy(update={"text": r.voice.guard_text})
        r.guard = self._timed(
            r, "guard", self.guard.impl, lambda: self.guard.apply(clean, turn, r.state, r.selection),
            lambda g: {"action": g.action, "reasons": g.reasons, "cited": g.cited_memory_ids})
        if until == "guard":
            return r
        if not r.guard.text.strip():
            # 빈 대답은 돌려주지 않는다(BE 가 저장하면 그 대화의 다음 턴이 모두 422 로 막혔다). 세션에도 남기지 않는다.
            raise EmptyReplyError()
        r.next_state = self._timed(
            r, "post_turn", self.post_turn.impl, lambda: self.post_turn.after(turn, r.state, r.guard),
            lambda s: {"turn_index": s.turn_index, "counters": s.counters})
        if not given_state:
            self.sessions.save(r.next_state)

        cited = r.guard.cited_memory_ids
        # 헤더가 없으면(인용 정보 모름) 기존처럼 넣은 기억 전부를 '사용됨'으로 돌려준다.
        ids = cited if cited is not None else [m.id for m in r.selection.memories]
        emotion, speech_text = self.voice.finish(r.voice, r.guard)
        r.result = TurnResult(content=r.guard.text, retrieved_memory_ids=ids,
                              provider=r.generation.provider, model=r.generation.model,
                              emotion=emotion, speech_text=speech_text, trace=r.trace)
        return r
