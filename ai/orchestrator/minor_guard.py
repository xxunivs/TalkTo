"""미성년 가드 — 만 19세 미만 대화 상대(turn.minor)의 대답을 한 번 더 본다. LLM 은 부르지 않는다.

다른 가드(LanguageGuard 까지) 바깥에 늘 붙는다(build_guard). turn.minor 가 아니면 안쪽 결과를 그대로 돌려준다.
대답에 성적·비밀 만남·위험한 내용이 있으면 대체 글(말 높이별)로 통째로 바꾼다(action=redact → 음성 표시 버림).
위기·죽음을 묻는 턴이거나 대답에 위기 상담 번호가 있으면 성적인 내용만 바꾼다. 판정·문구: pipeline/safety/minor_guard.py
"""
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

    def apply(self, generation: Generation, turn: TurnInput, state: SessionState,
              selection: MemorySelection) -> GuardResult:
        res = self.inner.apply(generation, turn, state, selection)
        if not turn.minor:
            return res
        # filter_reply 와 같은 판정(위기 턴이면 성적인 것만) — 까닭도 바꾼 까닭만 남긴다
        hits = blocking_hits(res.text, safety_turn=is_safety_turn(turn.message))
        if not hits:
            return res
        log.warning("minor guard replaced reply for subject %s", turn.subject_id)  # 내용은 남기지 않는다
        text = FALLBACK[is_polite(turn.persona_instructions)]
        return res.model_copy(update={"text": text, "action": "redact",
                                      "reasons": res.reasons + [f"minor:{h}" for h in hits]})
