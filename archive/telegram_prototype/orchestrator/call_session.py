"""
통화 1건의 수명 주기 상태 기계.

states: IDLE -> DIALING -> CONNECTED -> Q_SPEAKING -> LISTENING -> A_TRANSCRIBING
     -> GENERATING_Q -> (loop) -> HANGUP -> POST_PROCESS -> DONE
"""
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class CallSession:
    call_id: str
    target_user_id: int
    started_at: datetime = field(default_factory=datetime.utcnow)
    transcript: list = field(default_factory=list)  # [{"role","text","ts"}]
    audio_dir: Path | None = None

    def add_turn(self, role: str, text: str) -> None:
        self.transcript.append(
            {"role": role, "text": text, "ts": datetime.utcnow().isoformat()}
        )
