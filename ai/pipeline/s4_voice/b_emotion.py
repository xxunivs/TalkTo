"""대답 음성 감정 (대답 LLM 이 같은 호출에서 선택, 별도 호출 시 첫 소리 약 1.3초 지연)"""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from infra.external_api.tts import wants_emotion_hint
from infra.external_api.tts.fish import FISH_INLINE_CUES, cue_key, is_fish_voice

EMOTIONS = ("neutral", "calm", "happy", "sad", "surprised")
# Fish(FISH_EMOTION=cue) 감정 이름 (warm, worried 는 Fish 전용, MiniMax 에선 None)
FISH_EMOTIONS = ("neutral", "calm", "warm", "happy", "sad", "surprised", "worried")
# 사망 대상자 페르소나의 감정 치환 (우는 소리 방지)
_DECEASED_CAP = {"sad": "calm"}
# 대답당 연기 표시 상한 (inline)
MAX_INLINE_CUES = 2

EMOTION_INSTRUCTION = """[목소리 감정 표시]
대답 맨 앞에 [감정:이름] 을 딱 하나 붙인다. 이름은 neutral, calm, happy, sad, surprised 중 하나다.
- 반가운 소식·칭찬·농담 → happy
- 힘든 얘기·아픈 얘기·그리움 → sad 또는 calm
- 뜻밖의 소식 → surprised
- 걱정해 주거나 달래 줄 때 → calm
- 평범한 안부·일상 → neutral
이 표시는 목소리에만 쓰이고 상대에게는 보이지 않는다. 표시 뒤에는 평소처럼 대답한다."""

# FISH_EMOTION=cue (예시 문장 없음: 반말 예시가 존댓말 상대 말투를 흔들지 않도록)
FISH_CUE_INSTRUCTION = """[목소리 감정 표시]
대답 맨 앞에 [감정:이름] 을 딱 하나 붙인다. 이름은 neutral, calm, warm, happy, sad, surprised, worried 중 하나다.
- 반가운 소식·칭찬·농담 → happy
- 아끼는 마음을 전하거나 달래 줄 때 → warm
- 힘든 얘기·아픈 얘기·그리움 → sad 또는 calm
- 몸이나 일이 걱정될 때 → worried
- 뜻밖의 소식 → surprised
- 평범한 안부·일상 → neutral
이 표시는 목소리에만 쓰이고 상대에게는 보이지 않는다. 표시 뒤에는 평소 말투(높임말 포함) 그대로 대답한다."""

# FISH_EMOTION=inline (목록은 fish.FISH_INLINE_CUES 공유)
FISH_INLINE_INSTRUCTION = f"""[목소리 연기 표시]
느낌이 분명한 곳에만, 그렇게 읽을 말 바로 앞에 아래 표시를 글자 그대로 넣는다. 목록 밖 표시나 다른 괄호는 쓰지 않는다.
{" ".join(FISH_INLINE_CUES)}
- 한 대답에 많아야 2개, 대부분은 0~1개. 문장 가운데도 되지만 대답 맨 끝에는 두지 않는다.
- 표시를 넣어도 대답 글자·말투·높임말은 하나도 바꾸지 않는다.
이 표시는 목소리에만 쓰이고 상대 화면에는 보이지 않는다."""

_INSTRUCTIONS = {
    "minimax": EMOTION_INSTRUCTION,
    "cue": FISH_CUE_INSTRUCTION,
    "inline": FISH_INLINE_INSTRUCTION,
}

# 감정 표시 형식 변형 허용 ([감정:sad], [sad], (감정: 슬픔), [emotion: happy])
# 감정 이름이나 감정/emotion 이 붙은 괄호만 표시, 다른 괄호는 유지
_KOREAN = {
    "보통": "neutral",
    "평범": "neutral",
    "차분": "calm",
    "차분함": "calm",
    "평온": "calm",
    "다정": "warm",
    "다정함": "warm",
    "기쁨": "happy",
    "행복": "happy",
    "슬픔": "sad",
    "놀람": "surprised",
    "걱정": "worried",
}
_NAMES = "|".join(sorted({*FISH_EMOTIONS, *_KOREAN}, key=len, reverse=True))
_TAG = re.compile(
    r"[\[(（【]\s*(?:(?:감정|emotion)\s*[:：]?\s*([A-Za-z가-힣]+)|(" + _NAMES + r"))\s*[\])）】]",
    re.IGNORECASE,
)
_SQUARE = re.compile(r"\[[^\[\]\n]*\]")
_WORD = re.compile(r"\w")
_PUNCT = ".,!?…~"
# 표시 앞 글이 낱말, 문장부호로 붙어 끝나면 뗀 자리에 빈칸 하나
_GLUE_BEFORE = re.compile(r"[\w.,!?…~]\Z")  # \Z 사용 ($ 는 끝 줄바꿈 앞에도 매치)


def voice_mode(settings, voice_id: str | None) -> str:
    """음성 표시 방식 off, minimax, cue, inline (Fish: FISH_EMOTION, MiniMax: VOICE_EMOTION, ElevenLabs: off)"""
    if voice_id and is_fish_voice(voice_id):
        return getattr(settings, "fish_emotion", "off")
    if getattr(settings, "voice_emotion", "off") == "inline" and wants_emotion_hint(voice_id):
        return "minimax"
    return "off"


def fish_mode(fish_emotion: str, voice_id: str | None) -> str:
    """Fish 목소리면 FISH_EMOTION, 아니면 off (중간계층용)"""
    return fish_emotion if voice_id and is_fish_voice(voice_id) else "off"


def voice_instruction(mode: str) -> str | None:
    """방식별 지침 (off 면 None)"""
    return _INSTRUCTIONS.get(mode)


def strip_emotion_tags(text: str) -> str:
    """글 전체 감정 표시 제거"""
    return re.sub(r"[ 	]{2,}", " ", _TAG.sub("", text)).strip()


def split_emotion(
    text: str, *, deceased: bool | None, names: tuple[str, ...] = EMOTIONS
) -> tuple[str | None, str]:
    """감정 표시 분리 -> (감정, 깨끗한 글) (맨 앞 표시만 감정, names 밖이면 None)"""
    head = _TAG.match(text.lstrip())
    name = (head.group(1) or head.group(2)) if head else None
    emotion = _KOREAN.get(name, name.lower()) if name else None
    clean = strip_emotion_tags(text)
    if emotion not in names:
        emotion = None
    if emotion and deceased is not False:  # None 은 사망으로 간주
        emotion = _DECEASED_CAP.get(emotion, emotion)
    return emotion, clean


def split_cues(text: str) -> tuple[str, list[tuple[int, str]]]:
    """[ ] 표시 전부 제거 -> (깨끗한 글, 목록 내 표시 위치) (MAX_INLINE_CUES 개까지 기억)"""
    parts: list[str] = []
    cues: list[tuple[int, str]] = []
    size, last = 0, 0
    for m in _SQUARE.finditer(text):
        piece = text[last : m.start()]
        last = m.end()
        while last < len(text) and text[last] in " \t":
            last += 1
        nxt = text[last] if last < len(text) else ""
        if nxt and nxt in _PUNCT:
            piece = piece.rstrip(" \t")  # "그래 [미소]." -> "그래."
        elif _WORD.match(nxt) and _GLUE_BEFORE.search(
            piece or next((p for p in reversed(parts) if p), "")
        ):
            # "아이고[한숨 쉬며] 그랬구나" -> "아이고 그랬구나"
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
    """기억한 표시를 가드 거친 글에 재삽입 -> 목소리용 글 (남은 글자 자리만, 없으면 None)"""
    if not cues or not final:
        return None
    if final == clean:
        blocks = [(0, 0, len(clean))]
    else:
        blocks = SequenceMatcher(None, clean, final, autojunk=False).get_matching_blocks()
    placed: list[tuple[int, str]] = []
    for pos, cue in cues:
        for a, b, size in blocks:
            # 표시 뒤 2자(글 끝이면 남은 만큼)가 남아 있어야 같은 자리
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
    """대답에서 분리한 음성 표시 (text 는 화면, 가드용 글)"""

    text: str
    mode: str = "off"
    emotion: str | None = None
    # inline 표시 위치와 표시
    cues: list[tuple[int, str]] = field(default_factory=list)

    def speech_text(self, final: str | None = None) -> str | None:
        """목소리용 글 (inline 이고 표시가 남을 때만, final 은 가드 뒤 글)"""
        return place_cues(self.text, self.cues, self.text if final is None else final)


def split_voice(text: str, mode: str, *, deceased: bool | None) -> VoiceSplit:
    """방식별 감정, 연기 표시 분리 (off 면 글 그대로)"""
    if mode == "minimax":
        emotion, clean = split_emotion(text, deceased=deceased)
        return VoiceSplit(text=clean, mode=mode, emotion=emotion)
    if mode == "cue":
        emotion, clean = split_emotion(text, deceased=deceased, names=FISH_EMOTIONS)
        # 섞여 나온 [ ] 표시도 전부 제거 (화면, 가드, 기록 노출 방지)
        rest, _ = split_cues(clean)
        return VoiceSplit(text=rest or clean, mode=mode, emotion=emotion)
    if mode == "inline":
        clean, cues = split_cues(strip_emotion_tags(text))
        return VoiceSplit(text=clean or text, mode=mode, cues=cues if clean else [])
    return VoiceSplit(text=text, mode="off")
