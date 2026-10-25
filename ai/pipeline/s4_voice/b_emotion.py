"""대답 음성의 감정 — 대답을 만드는 LLM 이 같은 호출에서 함께 고른다.

감정만 고르는 LLM 을 따로 부르지 않는다. 따로 부르면 첫 소리가 약 1.3초 늦어진다.
지침(system 한 개, 100~150토큰쯤)을 더 넣고, 대답에 붙어 나온 표시를 여기서 떼어 낸다. 화면·대화 기록·
기억 추출·가드에는 떼어 낸 글만 간다.

목소리마다 방식이 다르다(voice_mode).
- minimax: MiniMax 예전 목소리 + VOICE_EMOTION=inline. 대답 맨 앞 [감정:이름] 하나(EMOTIONS) → emotion.
- cue: Fish 목소리 + FISH_EMOTION=cue. 대답 맨 앞 [감정:이름] 하나(FISH_EMOTIONS) → emotion.
  Fish 가 읽을 때 이름을 한국어 연기 표시로 바꿔 맨 앞에 붙인다(infra/external_api/tts/fish.py FISH_CUES).
- inline: Fish 목소리 + FISH_EMOTION=inline. 대답 안 꾸미는 말 바로 앞에 정해진 한국어 연기 표시
  (FISH_INLINE_CUES)를 많아야 2개 → speech_text(표시 있음)와 content(표시 없음)로 나눈다.
- off: 아무것도 넣지 않는다(지금처럼 목소리 회사가 글을 보고 정한다). ElevenLabs 는 늘 off.

감정 이름은 회사와 무관한 우리 이름이다. MiniMax·Fish 표시로 바꾸는 일은 각 어댑터가 한다.

고인 페르소나 안전선: 슬픔은 차분함까지만 낸다(우는 듯한 소리 금지). 고인 여부를 모르면 고인으로
본다. 화남·두려움·역겨움은 아예 고를 수 없고, 연기 표시 목록에도 울음·흐느낌이 없다.
"""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from infra.external_api.tts import wants_emotion_hint
from infra.external_api.tts.fish import FISH_INLINE_CUES, cue_key, is_fish_voice

EMOTIONS = ("neutral", "calm", "happy", "sad", "surprised")
# Fish(FISH_EMOTION=cue)에서 고를 수 있는 이름. warm·worried 는 Fish 에만 있다(MiniMax 에선 None).
FISH_EMOTIONS = ("neutral", "calm", "warm", "happy", "sad", "surprised", "worried")
# 고인 페르소나에서 바꿔 내는 감정
_DECEASED_CAP = {"sad": "calm"}
# 대답 하나에 남기는 연기 표시 수(inline)
MAX_INLINE_CUES = 2

EMOTION_INSTRUCTION = """[목소리 감정 표시]
대답 맨 앞에 [감정:이름] 을 딱 하나 붙인다. 이름은 neutral, calm, happy, sad, surprised 중 하나다.
- 반가운 소식·칭찬·농담 → happy
- 힘든 얘기·아픈 얘기·그리움 → sad 또는 calm
- 뜻밖의 소식 → surprised
- 걱정해 주거나 달래 줄 때 → calm
- 평범한 안부·일상 → neutral
이 표시는 목소리에만 쓰이고 상대에게는 보이지 않는다. 표시 뒤에는 평소처럼 대답한다."""

# FISH_EMOTION=cue. 예시 문장을 넣지 않는다(반말 예시가 존댓말로 대하는 상대에게 말투를 흔들지 않게).
FISH_CUE_INSTRUCTION = """[목소리 감정 표시]
대답 맨 앞에 [감정:이름] 을 딱 하나 붙인다. 이름은 neutral, calm, warm, happy, sad, surprised, worried 중 하나다.
- 반가운 소식·칭찬·농담 → happy
- 아끼는 마음을 전하거나 달래 줄 때 → warm
- 힘든 얘기·아픈 얘기·그리움 → sad 또는 calm
- 몸이나 일이 걱정될 때 → worried
- 뜻밖의 소식 → surprised
- 평범한 안부·일상 → neutral
이 표시는 목소리에만 쓰이고 상대에게는 보이지 않는다. 표시 뒤에는 평소 말투(높임말 포함) 그대로 대답한다."""

# FISH_EMOTION=inline. 목록은 fish.FISH_INLINE_CUES 와 같다(고치면 둘 다 바뀐다).
FISH_INLINE_INSTRUCTION = f"""[목소리 연기 표시]
느낌이 분명한 곳에만, 그렇게 읽을 말 바로 앞에 아래 표시를 글자 그대로 넣는다. 목록 밖 표시나 다른 괄호는 쓰지 않는다.
{" ".join(FISH_INLINE_CUES)}
- 한 대답에 많아야 2개, 대부분은 0~1개. 문장 가운데도 되지만 대답 맨 끝에는 두지 않는다.
- 표시를 넣어도 대답 글자·말투·높임말은 하나도 바꾸지 않는다.
이 표시는 목소리에만 쓰이고 상대 화면에는 보이지 않는다."""

_INSTRUCTIONS = {"minimax": EMOTION_INSTRUCTION, "cue": FISH_CUE_INSTRUCTION, "inline": FISH_INLINE_INSTRUCTION}

# 모델이 형식을 조금씩 바꿔 낸다: [감정:sad], [sad], (감정: 슬픔), [emotion: happy] ...
# 감정 이름이거나 "감정/emotion" 이 붙은 괄호만 표시로 본다. 다른 괄호(예: (웃음))는 둔다.
_KOREAN = {"보통": "neutral", "평범": "neutral", "차분": "calm", "차분함": "calm", "평온": "calm",
           "다정": "warm", "다정함": "warm", "기쁨": "happy", "행복": "happy", "슬픔": "sad",
           "놀람": "surprised", "걱정": "worried"}
_NAMES = "|".join(sorted({*FISH_EMOTIONS, *_KOREAN}, key=len, reverse=True))
_TAG = re.compile(
    r"[\[(（【]\s*(?:(?:감정|emotion)\s*[:：]?\s*([A-Za-z가-힣]+)|(" + _NAMES + r"))\s*[\])）】]",
    re.IGNORECASE,
)
_SQUARE = re.compile(r"\[[^\[\]\n]*\]")
_WORD = re.compile(r"\w")
_PUNCT = ".,!?…~"
# 표시 앞 글이 낱말·문장부호로 끝나면(빈칸 없이 붙어 있으면) 표시를 뗀 자리에 빈칸 하나를 둔다
_GLUE_BEFORE = re.compile(r"[\w.,!?…~]\Z")  # \Z: 줄바꿈으로 끝나면 붙은 것이 아니다($ 는 끝 줄바꿈 앞에도 맞는다)


def voice_mode(settings, voice_id: str | None) -> str:
    """이 대답에 쓸 음성 표시 방식: off | minimax | cue | inline (기존 경로 generate_persona_response 용).

    Fish 목소리는 FISH_EMOTION, MiniMax 는 VOICE_EMOTION 이 정한다. ElevenLabs 는 늘 off.
    """
    if voice_id and is_fish_voice(voice_id):
        return getattr(settings, "fish_emotion", "off")
    if getattr(settings, "voice_emotion", "off") == "inline" and wants_emotion_hint(voice_id):
        return "minimax"
    return "off"


def fish_mode(fish_emotion: str, voice_id: str | None) -> str:
    """Fish 목소리면 FISH_EMOTION 값, 아니면 off (중간계층 용 — MiniMax 는 지금처럼 감정 없음)."""
    return fish_emotion if voice_id and is_fish_voice(voice_id) else "off"


def voice_instruction(mode: str) -> str | None:
    """방식에 맞는 지침(system 한 개). off 면 None."""
    return _INSTRUCTIONS.get(mode)


def strip_emotion_tags(text: str) -> str:
    """글 어디에 있든 감정 표시를 지운다. 소리로 읽히면 안 된다."""
    return re.sub(r"[ 	]{2,}", " ", _TAG.sub("", text)).strip()


def split_emotion(
    text: str, *, deceased: bool | None, names: tuple[str, ...] = EMOTIONS
) -> tuple[str | None, str]:
    """대답에서 감정 표시를 떼어 (감정, 깨끗한 글) 을 돌려준다.

    맨 앞 표시만 감정으로 쓰고, 다른 곳에 섞여 나온 표시는 지우기만 한다. names 에 없는 이름이거나
    표시가 없으면 감정은 None(목소리 회사가 글을 보고 알아서 정한다).
    """
    head = _TAG.match(text.lstrip())
    name = (head.group(1) or head.group(2)) if head else None
    emotion = _KOREAN.get(name, name.lower()) if name else None
    clean = strip_emotion_tags(text)
    if emotion not in names:
        emotion = None
    if emotion and deceased is not False:  # 모르면(None) 고인으로 본다
        emotion = _DECEASED_CAP.get(emotion, emotion)
    return emotion, clean


def split_cues(text: str) -> tuple[str, list[tuple[int, str]]]:
    """글에서 [ ] 표시를 모두 떼어 (깨끗한 글, [(깨끗한 글에서 표시가 있던 자리, 표시)]) 를 돌려준다.

    목록(FISH_INLINE_CUES)에 있는 표시만 앞에서부터 MAX_INLINE_CUES 개까지 자리를 기억한다.
    목록에 없는 표시와 넘친 표시는 지우기만 한다. 표시 뒤 띄어쓰기도 같이 지운다. 표시가 두 낱말 사이에
    빈칸 없이 붙어 있었으면 빈칸 하나를 남긴다(글자는 그대로, 낱말이 붙지 않게).
    """
    parts: list[str] = []
    cues: list[tuple[int, str]] = []
    size, last = 0, 0
    for m in _SQUARE.finditer(text):
        piece = text[last:m.start()]
        last = m.end()
        while last < len(text) and text[last] in " \t":
            last += 1
        nxt = text[last] if last < len(text) else ""
        if nxt and nxt in _PUNCT:
            piece = piece.rstrip(" \t")  # "그래 [미소]." → "그래." (문장부호 앞 빈칸을 남기지 않는다)
        elif _WORD.match(nxt) and _GLUE_BEFORE.search(piece or next((p for p in reversed(parts) if p), "")):
            # 앞말에 붙은 표시: "아이고[한숨 쉬며] 그랬구나" → "아이고 그랬구나" (두 낱말을 붙이지 않는다)
            piece += " "
        parts.append(piece)
        size += len(piece)
        key = cue_key(m.group(0))
        if key in FISH_INLINE_CUES and len(cues) < MAX_INLINE_CUES:
            cues.append((size, key))
    parts.append(text[last:])
    joined = "".join(parts)
    lead = len(joined) - len(joined.lstrip())
    clean = joined.strip()
    return clean, [(max(pos - lead, 0), cue) for pos, cue in cues]


def place_cues(clean: str, cues: list[tuple[int, str]], final: str) -> str | None:
    """split_cues 가 기억한 표시를 final(가드를 거친 글)에 다시 꽂아 목소리용 글을 만든다.

    final 이 clean 과 다르면(가드가 문장을 지웠으면) 같은 글자가 남은 자리에만 꽂는다. 그 말이 지워졌거나
    표시 뒤에 말이 없으면(맨 끝) 그 표시는 버린다. 남은 표시가 없으면 None(BE 는 content 를 읽는다).
    """
    if not cues or not final:
        return None
    if final == clean:
        blocks = [(0, 0, len(clean))]
    else:
        blocks = SequenceMatcher(None, clean, final, autojunk=False).get_matching_blocks()
    placed: list[tuple[int, str]] = []
    for pos, cue in cues:
        for a, b, size in blocks:
            # 표시 바로 뒤 글자들(2자, 글 끝이면 남은 만큼)이 그대로 남아 있어야 같은 자리다
            if a <= pos < a + size and a + size - pos >= min(2, len(clean) - pos):
                q = b + pos - a
                if _WORD.search(final[q:]):
                    placed.append((q, cue))
                break
    if not placed:
        return None
    out, last = [], 0
    for q, cue in sorted(placed, key=lambda x: x[0]):
        out += [final[last:q], cue, " "]
        last = q
    out.append(final[last:])
    return "".join(out)


@dataclass
class VoiceSplit:
    """대답 글에서 떼어 낸 음성 표시. text 는 표시를 뗀 글(화면·가드용)."""
    text: str
    mode: str = "off"
    emotion: str | None = None
    # inline: text 에서 표시가 있던 자리와 표시
    cues: list[tuple[int, str]] = field(default_factory=list)

    def speech_text(self, final: str | None = None) -> str | None:
        """목소리로 읽을 글(inline 이고 표시가 남을 때만). final 을 주면 그 글(가드 뒤)에 꽂는다."""
        return place_cues(self.text, self.cues, self.text if final is None else final)


def split_voice(text: str, mode: str, *, deceased: bool | None) -> VoiceSplit:
    """방식에 맞게 대답에서 감정·연기 표시를 떼어 낸다. off 면 글을 그대로 둔다(지금과 같다)."""
    if mode == "minimax":
        emotion, clean = split_emotion(text, deceased=deceased)
        return VoiceSplit(text=clean, mode=mode, emotion=emotion)
    if mode == "cue":
        emotion, clean = split_emotion(text, deceased=deceased, names=FISH_EMOTIONS)
        # 감정 표시에 이끌려 [다정하게]·[울면서] 같은 [ ] 표시를 글에 섞어 내도 화면·가드·대화 기록에 남지 않게
        # inline 과 같이 [ ] 표시를 모두 지운다(목소리용 글은 따로 만들지 않는다)
        rest, _ = split_cues(clean)
        return VoiceSplit(text=rest or clean, mode=mode, emotion=emotion)
    if mode == "inline":
        clean, cues = split_cues(strip_emotion_tags(text))
        return VoiceSplit(text=clean or text, mode=mode, cues=cues if clean else [])
    return VoiceSplit(text=text, mode="off")
