"""대답 음성의 감정 — 대답을 만드는 LLM 이 같은 호출에서 함께 고른다(VOICE_EMOTION=inline).

감정만 고르는 LLM 을 따로 부르지 않는다. 따로 부르면 첫 소리가 약 1.3초 늦어진다.
대신 대답 맨 앞에 [감정:이름] 을 하나 붙이게 하고, 여기서 떼어 낸다. 화면·대화 기록·
기억 추출에는 떼어 낸 글만 간다.

이름은 회사와 무관한 우리 이름이다. MiniMax·ElevenLabs 이름으로 바꾸는 일은 각 어댑터가 한다.

고인 페르소나 안전선: 슬픔은 차분함까지만 낸다(우는 듯한 소리 금지). 고인 여부를 모르면 고인으로
본다. 화남·두려움·역겨움은 아예 고를 수 없다.
"""

import re

EMOTIONS = ("neutral", "calm", "happy", "sad", "surprised")
# 고인 페르소나에서 바꿔 내는 감정
_DECEASED_CAP = {"sad": "calm"}

EMOTION_INSTRUCTION = """[목소리 감정 표시]
대답 맨 앞에 [감정:이름] 을 딱 하나 붙인다. 이름은 neutral, calm, happy, sad, surprised 중 하나다.
- 반가운 소식·칭찬·농담 → happy
- 힘든 얘기·아픈 얘기·그리움 → sad 또는 calm
- 뜻밖의 소식 → surprised
- 걱정해 주거나 달래 줄 때 → calm
- 평범한 안부·일상 → neutral
이 표시는 목소리에만 쓰이고 상대에게는 보이지 않는다. 표시 뒤에는 평소처럼 대답한다."""

# 모델이 형식을 조금씩 바꿔 낸다: [감정:sad], [sad], (감정: 슬픔), [emotion: happy] ...
# 감정 이름이거나 "감정/emotion" 이 붙은 괄호만 표시로 본다. 다른 괄호(예: (웃음))는 둔다.
_KOREAN = {"보통": "neutral", "평범": "neutral", "차분": "calm", "차분함": "calm", "평온": "calm",
           "기쁨": "happy", "행복": "happy", "슬픔": "sad", "놀람": "surprised"}
_NAMES = "|".join(sorted({*EMOTIONS, *_KOREAN}, key=len, reverse=True))
_TAG = re.compile(
    r"[\[(（【]\s*(?:(?:감정|emotion)\s*[:：]?\s*([A-Za-z가-힣]+)|(" + _NAMES + r"))\s*[\])）】]",
    re.IGNORECASE,
)


def strip_emotion_tags(text: str) -> str:
    """글 어디에 있든 감정 표시를 지운다. 소리로 읽히면 안 된다."""
    return re.sub(r"[ 	]{2,}", " ", _TAG.sub("", text)).strip()


def split_emotion(text: str, *, deceased: bool | None) -> tuple[str | None, str]:
    """대답에서 감정 표시를 떼어 (감정, 깨끗한 글) 을 돌려준다.

    맨 앞 표시만 감정으로 쓰고, 다른 곳에 섞여 나온 표시는 지우기만 한다. 모르는 이름이거나
    표시가 없으면 감정은 None(목소리 회사가 글을 보고 알아서 정한다).
    """
    head = _TAG.match(text.lstrip())
    name = (head.group(1) or head.group(2)) if head else None
    emotion = _KOREAN.get(name, name.lower()) if name else None
    clean = strip_emotion_tags(text)
    if emotion not in EMOTIONS:
        emotion = None
    if emotion and deceased is not False:  # 모르면(None) 고인으로 본다
        emotion = _DECEASED_CAP.get(emotion, emotion)
    return emotion, clean
