"""언어 누출 가드 — 답에 이번 턴 맥락에 없던 문자 체계나 추론 문장이 새어 나왔는지 본다. LLM 은 부르지 않는다.

한글이 아닌 글자를 일괄로 막지 않는다. 영어가 섞이는 대화나 다른 언어로의 확장을 막으면 안 되기 때문이다.
대신 '이번 턴 맥락'(가족 말, 지시문, 넣은 기억, 앞 대화)과 비교한다.
    script:<이름>   맥락에 한 글자도 없던 문자 체계(한자·가나·키릴 등)가 답에 나옴
    latin:<낱말>    맥락에 없던 영어 낱말이 답에 나옴(맥락에 있던 낱말·약어는 통과)
    leak:<표지>     "Wait", "Okay,", "Let me", "The user" 같은 추론 문장이 답에 나옴
가족이 영어로 말하고 있으면(가족 말·앞 대화에 영어 낱말 3개 이상) 영어 답은 문제 삼지 않는다.
무슨 언어로 답할지는 프롬프트(L1 말하는 형식)가 정하고, 이 가드는 맥락에서 튀어나온 것만 본다.
모드(ORCHESTRATOR_LANG_GUARD): off(기본) / flag(답은 그대로, 이유만 기록) / fix(걸린 문장을 지운다).
"""
from __future__ import annotations

import re
import unicodedata

from orchestrator.contracts import Generation, GuardResult, MemorySelection, SessionState, TurnInput

LANG_GUARD_MODES = ("off", "flag", "fix")

_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]+")
_SENT = re.compile(r"(?<=[.?!。])\s+|\n+")
# 추론·메타 문장의 표지(문장 첫머리). 대소문자 무시.
# 강한 표지는 영어로 대화할 때도 누출이고, 약한 표지는 영어 대화에서는 평범한 말이라 한국어 대화에서만 본다.
LEAK_STRONG = re.compile(r"^\s*(?:<\/?think>|the user\b|user:|assistant:|final answer\b|reasoning:|note:)", re.IGNORECASE)
LEAK_WEAK = re.compile(r"^\s*(?:wait\b|okay\b|alright\b|hmm+\b|let me\b|let's\b|i (?:should|need|will|think|must)\b"
                       r"|answer:|thinking\b|good\.)", re.IGNORECASE)
# 가족 말·앞 대화에 영어 낱말이 이만큼 있으면 영어로 대화하는 중으로 본다
ENGLISH_DIALOGUE_WORDS = 3
# 맥락에 없어도 넘길 영어 약어(대화에서 흔한 것만)
ALLOWED_LATIN = {"ok", "tv", "pc", "sns", "ktx", "kt", "lg", "sk"}
ASK_BACK = {"family": "응? 다시 말해 줄래?", "public": "네? 다시 한 번 말씀해 주시겠어요?"}


def script_of(ch: str) -> str | None:
    """글자 하나의 문자 체계. 숫자·문장부호·공백은 None."""
    if not unicodedata.category(ch).startswith("L"):
        return None
    o = ord(ch)
    if 0xAC00 <= o <= 0xD7A3 or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F:
        return "hangul"
    if ch.isascii() or 0x00C0 <= o <= 0x024F:
        return "latin"
    if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF:
        return "han"
    if 0x3040 <= o <= 0x30FF:
        return "kana"
    if 0x0400 <= o <= 0x04FF:
        return "cyrillic"
    name = unicodedata.name(ch, "OTHER").split()[0].lower()
    return name


def scripts(text: str) -> set[str]:
    return {s for s in map(script_of, text) if s}


def english_dialogue(dialogue: str) -> bool:
    return len(_LATIN_WORD.findall(dialogue)) >= ENGLISH_DIALOGUE_WORDS


def leak_head(sent: str, english: bool) -> re.Match | None:
    return LEAK_STRONG.match(sent) or (None if english else LEAK_WEAK.match(sent))


def findings(answer: str, context: str, dialogue: str = "") -> list[str]:
    """답에서 걸린 것들. 비어 있으면 통과.

    context  이번 턴에 모델이 본 글 전부(가족 말·지시문·기억·앞 대화)
    dialogue 그중 대화(가족 말·앞 대화). 여기서 영어로 말하고 있으면 영어 낱말은 문제 삼지 않는다.
    """
    out: list[str] = []
    for s in sorted(scripts(answer) - scripts(context) - {"hangul", "latin"}):
        out.append(f"script:{s}")
    english = english_dialogue(dialogue)
    if not english:
        ctx_words = {w.lower() for w in _LATIN_WORD.findall(context)}
        for w in dict.fromkeys(_LATIN_WORD.findall(answer)):
            if w.lower() not in ctx_words and w.lower() not in ALLOWED_LATIN:
                out.append(f"latin:{w}")
    for sent in _SENT.split(answer):
        m = leak_head(sent, english)
        if m:
            out.append(f"leak:{m.group(0).strip().lower()}")
    return out


def sentence_hit(sent: str, hits: list[str], english: bool) -> bool:
    if leak_head(sent, english):
        return True
    bad_scripts = {h.split(":", 1)[1] for h in hits if h.startswith("script:")}
    bad_words = {h.split(":", 1)[1] for h in hits if h.startswith("latin:")}
    return bool(scripts(sent) & bad_scripts) or any(w in _LATIN_WORD.findall(sent) for w in bad_words)


def turn_dialogue(turn: TurnInput, state: SessionState) -> str:
    history = turn.history or state.recent_turns
    return "\n".join([turn.message, *(t.content for t in history)])


def turn_context(turn: TurnInput, state: SessionState, selection: MemorySelection) -> str:
    mems = [f"{m.title} {m.text}" for m in list(selection.memories) + list(selection.delta)]
    return "\n".join([turn_dialogue(turn, state), turn.persona_instructions, *mems])


class LanguageGuard:
    """다른 가드 뒤에 붙는다. 안쪽 가드 결과 글을 한 번 더 본다."""

    def __init__(self, inner, profiles, *, mode: str = "flag") -> None:
        if mode not in ("flag", "fix"):
            raise ValueError("mode must be flag or fix")
        self.inner, self.profiles, self.mode = inner, profiles, mode
        self.impl = f"{inner.impl}+lang_{mode}"

    def apply(self, generation: Generation, turn: TurnInput, state: SessionState,
              selection: MemorySelection) -> GuardResult:
        res = self.inner.apply(generation, turn, state, selection)
        dialogue = turn_dialogue(turn, state)
        prof = self.profiles.profile_for(turn)
        extra = [prof.style_card or "", *(f"{s.user} {s.persona}" for s in prof.shots)]  # 조립기가 덧붙이는 말투 재료
        hits = findings(res.text, "\n".join([turn_context(turn, state, selection), *extra]), dialogue)
        if not hits:
            return res
        reasons = res.reasons + [f"lang:{h}" for h in hits]
        if self.mode == "flag":
            return res.model_copy(update={"reasons": reasons})
        sents = [s for s in _SENT.split(res.text) if s.strip()]
        english = english_dialogue(dialogue)
        kept = [s for s in sents if not sentence_hit(s, hits, english)]
        if kept:
            return res.model_copy(update={"text": " ".join(kept), "action": "trim", "reasons": reasons})
        kind = "public" if prof.public else "family"
        return res.model_copy(update={"text": ASK_BACK[kind], "action": "ask_back", "reasons": reasons})
