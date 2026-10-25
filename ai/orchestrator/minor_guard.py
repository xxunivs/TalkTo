"""미성년(turn.minor) 대답 재검사 -> 성적, 비밀 만남, 위험 내용이면 대체 글로 교체 (가장 바깥 가드, LLM 미호출)"""

from __future__ import annotations

import logging

from orchestrator.contracts import Generation, GuardResult, MemorySelection, SessionState, TurnInput
from orchestrator.precheck import is_safety_turn
from pipeline.safety.minor_guard import FALLBACK, blocking_hits, is_polite

log = logging.getLogger(__name__)


class MinorGuard:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.impl = f"{inner.impl}+minor"

    def apply(
        self,
        generation: Generation,
        turn: TurnInput,
        state: SessionState,
        selection: MemorySelection,
    ) -> GuardResult:
        res = self.inner.apply(generation, turn, state, selection)
        if not turn.minor:
            return res
        # filter_reply 와 같은 판정 (위기 턴은 성적 내용만), 까닭은 바꾼 것만 기록
        hits = blocking_hits(res.text, safety_turn=is_safety_turn(turn.message))
        if not hits:
            return res
        log.warning("minor guard replaced reply for subject %s", turn.subject_id)  # 내용 미기록
        text = FALLBACK[is_polite(turn.persona_instructions)]
        return res.model_copy(
            update={
                "text": text,
                "action": "redact",
                "reasons": res.reasons + [f"minor:{h}" for h in hits],
            }
        )
