"""⑦ 턴 뒤 처리(응답 전, 빠른 것만). 무거운 기억 추출·델타 기억 저장은 여기서 하지 않는다(비동기로 따로)."""
from __future__ import annotations

from orchestrator.contracts import GuardResult, SessionState, Turn, TurnInput


class RecordTurn:
    impl = "record_v0"

    def __init__(self, max_turns: int = 8) -> None:
        self.max_turns = max_turns

    def after(self, turn: TurnInput, state: SessionState, guard: GuardResult) -> SessionState:
        s = state.model_copy(deep=True)
        s.recent_turns.append(Turn(role="user", content=turn.message, speaker=s.speaker))
        s.recent_turns.append(Turn(role="assistant", content=guard.text))
        s.recent_turns = s.recent_turns[-self.max_turns:]
        for k, v in guard.counter_delta.items():
            s.counters[k] = s.counters.get(k, 0) + v
        s.turn_index += 1
        return s
