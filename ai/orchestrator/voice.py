"""⑤ 뒤 음성 표시 떼기(FISH_EMOTION). LLM 을 다시 부르지 않는다.

Fish 목소리이고 FISH_EMOTION 이 cue·inline 이면
- 조립기가 음성 지침(system 한 개)을 넣는다(instruction).
- 생성 바로 뒤, 가드 앞에서 감정 이름·연기 표시를 떼어 낸다(split). 가드·언어 가드·턴 뒤 처리는 표시 없는 글만 본다.
  근거 헤더("[근거: m3]", guard v1·v11)는 가드가 읽어야 해서 떼지 않고 맨 앞에 둔다(표시가 헤더 앞에 와도).
- 가드 뒤에 감정과 목소리용 글을 정한다(finish). 연기 표시는 가드가 남긴 글의 같은 자리에 다시 꽂는다
  (가드가 지운 문장의 표시는 빠진다). 가드가 답을 갈아 끼웠으면(되묻기·가림) 감정·표시를 버린다
  (다른 글에 고른 것이라서).
MiniMax 감정(VOICE_EMOTION)은 기존 경로(generate_persona_response)에만 있다. 여기서는 지금처럼 감정이 없다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from orchestrator.contracts import GuardResult, TurnInput
from pipeline.s4_voice.b_emotion import VoiceSplit, fish_mode, split_voice, voice_instruction

FISH_MODES = ("off", "cue", "inline")
# 근거 헤더(guard.HEADER 와 같은 꼴). 앞에 감정·연기 표시가 먼저 와도 찾는다.
_LEAD_HEADER = re.compile(
    r"^\s*((?:\[[^\[\]\n]*\]\s*|[(（【][^()（）【】\n]*[)）】]\s*)*?)(\[근거:[^\]\n]*\])[ \t]*\n?"
)
# 가드가 답을 통째로 갈아 끼운 경우
_REPLACED = ("ask_back", "redact")


def peel_header(text: str) -> tuple[str, str]:
    """(근거 헤더, 나머지 글). 헤더가 없으면 ("", 글)."""
    m = _LEAD_HEADER.match(text)
    if not m:
        return "", text
    return m.group(2), (m.group(1) + text[m.end():]).strip()


@dataclass
class VoiceRun:
    guard_text: str  # 가드에 넘길 글(근거 헤더 + 표시 뗀 대답)
    split: VoiceSplit  # 떼어 낸 감정·연기 표시(split.text 는 헤더 없는 대답)
    header: bool = False

    def summary(self) -> dict:
        return {"mode": self.split.mode, "emotion": self.split.emotion,
                "cues": [cue for _, cue in self.split.cues], "header": self.header}


class VoiceMarks:
    def __init__(self, fish_emotion: str = "off") -> None:
        if fish_emotion not in FISH_MODES:
            raise ValueError(f"fish_emotion must be one of {FISH_MODES}")
        self.fish_emotion = fish_emotion
        self.impl = f"voice_fish_{fish_emotion}"

    def mode(self, turn: TurnInput) -> str:
        """off | cue | inline. Fish 목소리가 아니면 off."""
        return fish_mode(self.fish_emotion, turn.voice_id)

    def instruction(self, turn: TurnInput) -> str | None:
        return voice_instruction(self.mode(turn))

    def split(self, text: str, turn: TurnInput) -> VoiceRun:
        header, body = peel_header(text)
        split = split_voice(body, self.mode(turn), deceased=turn.deceased)
        guard_text = f"{header}\n{split.text}" if header else split.text
        return VoiceRun(guard_text=guard_text, split=split, header=bool(header))

    def finish(self, run: VoiceRun | None, guard: GuardResult) -> tuple[str | None, str | None]:
        """(감정, 목소리용 글). 가드가 답을 갈아 끼웠으면 둘 다 None."""
        if run is None or run.split.mode == "off" or guard.action in _REPLACED:
            return None, None
        return run.split.emotion, run.split.speech_text(guard.text)
