"""① 세션 상태 저장소.

프로세스 메모리. 주의 — uvicorn workers=2 면 워커마다 따로라서 같은 세션이 다른 워커로 가면
상태가 끊긴다. 여러 워커에서 이으려면 DB 테이블이나 공유 캐시로 바꿔야 한다.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict

from orchestrator.contracts import SessionState, TurnInput


def fresh_state(turn: TurnInput) -> SessionState:
    return SessionState(session_id=turn.session_id, subject_id=turn.subject_id, speaker=turn.speaker_hint)


class InMemorySessionStore:
    impl = "memory_v0"

    def __init__(self, *, max_sessions: int = 1000, ttl_s: float = 3600.0) -> None:
        self._data: OrderedDict[str, tuple[float, SessionState]] = OrderedDict()
        self._lock = threading.Lock()
        self.max_sessions = max_sessions
        self.ttl_s = ttl_s

    def load(self, turn: TurnInput) -> SessionState:
        if not turn.session_id:  # 세션 id 가 없으면(지금 BE) 매 턴 새 상태
            return fresh_state(turn)
        now = time.monotonic()
        with self._lock:
            hit = self._data.get(turn.session_id)
            if hit is None or now - hit[0] > self.ttl_s or hit[1].subject_id != turn.subject_id:
                state = fresh_state(turn)
            else:
                state = hit[1].model_copy(deep=True)
        if turn.speaker_hint:
            state.speaker = turn.speaker_hint
        return state

    def save(self, state: SessionState) -> None:
        if not state.session_id:
            return
        with self._lock:
            self._data[state.session_id] = (time.monotonic(), state.model_copy(deep=True))
            self._data.move_to_end(state.session_id)
            while len(self._data) > self.max_sessions:
                self._data.popitem(last=False)
