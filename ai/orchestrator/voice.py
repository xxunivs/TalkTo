"""⑤ 뒤 음성 표시 떼기 (FISH_EMOTION cue, inline 이고 Fish 목소리일 때, LLM 미호출)"""

from __future__ import annotations

import re
from dataclasses import dataclass

from orchestrator.contracts import GuardResult, TurnInput
from pipeline.s4_voice.b_emotion import VoiceSplit, fish_mode, split_voice, voice_instruction

FISH_MODES = ("off", "cue", "inline")
# 근거 헤더 (guard.HEADER 와 같은 꼴, 가드가 읽어야 해서 유지), 앞에 감정, 연기 표시가 와도 탐색
_LEAD_HEADER = re.compile(
    r"^\s*((?:\[[^\[\]\n]*\]\s*|[(（【][^()（）【】\n]*[)）】]\s*)*?)(\[근거:[^\]\n]*\])[ \t]*\n?"
)
# 가드가 답을 통째로 교체한 경우 (다른 글에 고른 감정, 표시라 폐기)
_REPLACED = ("ask_back", "redact")


def peel_header(text: str) -> tuple[str, str]:
    """(근거 헤더, 나머지 글), 헤더 없으면 ("", 글)"""
    m = _LEAD_HEADER.match(text)
    if not m:
        return "", text
    return m.group(2), (m.group(1) + text[m.end() :]).strip()


@dataclass
class VoiceRun:
    guard_text: str  # 가드에 넘길 글 (근거 헤더 + 표시 뗀 대답)
    split: VoiceSplit  # 떼어 낸 감정, 연기 표시 (split.text 는 헤더 없는 대답)
    header: bool = False

    def summary(self) -> dict:
        return {
            "mode": self.split.mode,
            "emotion": self.split.emotion,
            "cues": [cue for _, cue in self.split.cues],
            "header": self.header,
        }


class VoiceMarks:
    def __init__(self, fish_emotion: str = "off") -> None:
        if fish_emotion not in FISH_MODES:
            raise ValueError(f"fish_emotion must be one of {FISH_MODES}")
        self.fish_emotion = fish_emotion
        self.impl = f"voice_fish_{fish_emotion}"

    def mode(self, turn: TurnInput) -> str:
        """off | cue | inline (Fish 목소리 아니면 off)"""
        return fish_mode(self.fish_emotion, turn.voice_id)

    def instruction(self, turn: TurnInput) -> str | None:
        return voice_instruction(self.mode(turn))

    def split(self, text: str, turn: TurnInput) -> VoiceRun:
        header, body = peel_header(text)
        split = split_voice(body, self.mode(turn), deceased=turn.deceased)
        guard_text = f"{header}\n{split.text}" if header else split.text
        return VoiceRun(guard_text=guard_text, split=split, header=bool(header))

    def finish(self, run: VoiceRun | None, guard: GuardResult) -> tuple[str | None, str | None]:
        """(감정, 목소리용 글), 가드가 답을 교체했으면 둘 다 None"""
        if run is None or run.split.mode == "off" or guard.action in _REPLACED:
            return None, None
        return run.split.emotion, run.split.speech_text(guard.text)
