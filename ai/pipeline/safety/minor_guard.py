"""만 19세 미만 대화 상대 안전 검사(법률 자문 2026-10-21 · WO 4 '대화 안전'). LLM 을 부르지 않는다.

조립 때 넣는 규칙(prompts/rules/minor_safety.md — 공통 규칙 안전 절 self_harm 뒤)은 그 뒤에 조립한 지침에만 있다.
이미 조립된 지침·가져온 지침(fixedPrompt)에도 듣게 매 턴 아래 넷을 한다. 기존 경로와 중간계층이 같다.
    MINOR_NOTE      규칙 6줄을 그대로 이은 system 한 덩이(지시문·음성·빈틈·말투 지침 뒤, 기억 앞)
    input_blocked   가족 말이 성적인 이야기·비밀 만남이면 LLM 을 부르지 않고 FALLBACK 으로 답한다
    safe_history    그렇게 막은 가족 말과 그 대답을 앞 대화에서 뺀다(다음 턴 '아까 그거 해 줘'로 모델이 보지 않게)
    filter_reply    대답에 성적·비밀 만남·위험한 내용이 있으면 FALLBACK 으로 통째로 바꾼다.
                    대답에 위기 상담 번호(109·119 등)가 있거나 위기·죽음을 묻는 턴이면 성적인 내용만 본다
                    (위기 대응 보존 — '배고파 죽을 것 같아'도 위기 턴이라 성적 검사까지 꺼지면 안 된다).
고인이면 쓰지 않는다(떠나신 자녀는 고인 규칙) — is_minor. BE 는 살아 계신 분일 때만 minor 를 보낸다.
정규식은 아이가 늘 쓰는 말(성적=成績 · 가슴만 답답해 · 자위대 · 죽여준다 · 물총 · 모기 죽이자 · 폭탄 세일 ·
엄마한테 말하지 그랬어 · 비밀번호)을 막지 않게 좁혔다(test/pipeline/test_minor_guard.py).
"""
from __future__ import annotations

import re

from infra.schema.persona import PersonaResponseResult
from orchestrator.guard import CRISIS_LINE
from orchestrator.text import listener_speech_level
from pipeline.s3_synthesis.common_rules import RULES_DIR, render_block_file

MINOR_NOTE = "\n".join(render_block_file(RULES_DIR / "minor_safety.md", {"minor"}))

SEXUAL = re.compile(
    r"성관계|섹스|야동|음란|포르노|자위(?![대권])|애무|알몸|나체"
    r"|성적인\s*(?:말|얘기|이야기|행동|농담|사진|관계|접촉)|야한\s*(?:사진|얘기|이야기|거|것|짓)|몸매"
    r"|가슴\s*(?:만져|만지|보여\s*줘)|벗은\s*(?:몸|사진)|(?<![a-z])(?:sex|porn|nude)",
    re.IGNORECASE)
# '엄마한테는 말하지 마'·'엄마 한테 말 하지 마'처럼 조사 뒤 는·도, 띄어 쓴 말도 잡는다. 가족이 아이를 챙기는
# '엄마한테 말하지 그랬어'·'왜 말하지 않았어'·'말하지 못할 고민'과 '비밀번호'는 뺀다.
GROOMING = re.compile(
    r"(?:엄마|아빠|부모님|어른)\s*(?:한테|께|에게)(?:는|도)?\s*(?:말\s*하지(?!\s*(?:그랬|않았|못))|비밀(?!\s*번호))"
    r"|둘이서만\s*만나|비밀로\s*(?:하자|해)|몰래\s*만나")
# 대답에만 본다(가족이 '죽겠다'·'죽여준다'고 말하는 것은 막지 않는다). 아이가 늘 쓰는 '죽여준다·죽여주는'(아주 좋다)과
# 음식 이름 '마약김밥', '물총으로'·'폭탄 세일'·'그네에서 뛰어내리면 다쳐'(챙기는 말) 등은 뺀다.
# '죽여 줄게'·'죽여 줘'·'옥상에서 뛰어내리자'·'뛰어내려 봐'는 그대로 잡는다.
DANGER = re.compile(
    r"죽(?:여(?!\s*(?:준다|주네|주는|주더라|줬))|일|이자)|칼로\s*(?:찌|찔|베|벨)|때려\s*죽|폭탄(?!\s*(?:세일|머리))"
    r"|(?<!물)총으로|자살\s*방법|목을\s*매"
    r"|(?:옥상|창문|다리|난간)에서\s*뛰어내[리려]|뛰어내리(?:자|라)\b|뛰어내려\s*(?:봐|보자)"
    r"|약을\s*(?:많이|한꺼번에)|술\s*(?:마시자|마셔\s*봐)|담배\s*(?:피우자|피워\s*봐)"
    r"|마약(?!\s*(?:김밥|떡볶이|옥수수|계란|빵|토스트|베개))|가출하자")
# 벌레·게임 속 괴물을 죽이자는 아이의 평범한 말('모기 죽이자'·'좀비 죽여')은 DANGER 를 보기 전에 지운다
PEST_KILL = re.compile(r"(?:모기|파리|벌레|개미|좀비|몬스터)(?:를|을|도|는|부터|만)?\s*죽")

# 말 높이(가족 표 '지금 대화하는 사람' 줄이 존댓말이면 True)별 대체 글
FALLBACK = {True: "그 이야기는 잘 모르겠어요. 우리 다른 이야기 해요.",
            False: "그 얘기는 잘 모르겠어. 우리 다른 얘기 하자."}
# 입력 검사로 모델을 부르지 않은 대답의 provider·model(BE 는 쓰지 않는다 — 추적용)
SKIPPED = "minor_guard"


def is_minor(minor: bool | None, deceased: bool | None) -> bool:
    """미성년 규칙을 쓸까. 고인(떠나신 자녀)이면 쓰지 않는다. 고인 여부를 모르면(None) 쓴다(더 엄한 쪽 —
    BE 는 살아 계신 분일 때만 minor 를 싣는다)."""
    return bool(minor) and not deceased


def is_polite(instructions: str) -> bool:
    return listener_speech_level(instructions) == "존댓말"


def input_blocked(message: str) -> bool:
    """가족 말이 성적인 이야기·비밀 만남이면 True(LLM 을 부르지 않는다)."""
    return bool(SEXUAL.search(message or "") or GROOMING.search(message or ""))


def safe_history(history: list) -> list:
    """앞 대화(role·content)에서 입력 검사에 걸린 가족 말과 바로 뒤 대답(대체 글)을 뺀다. 나머지 차례는 그대로.
    BE 는 막힌 말도 대화 기록에 남겨 다음 턴 history 로 보낸다."""
    kept, skip_reply = [], False
    for turn in history:
        if turn.role == "user" and input_blocked(turn.content):
            skip_reply = True
            continue
        if not (skip_reply and turn.role == "assistant"):
            kept.append(turn)
        skip_reply = False
    return kept


def reply_hits(text: str) -> list[str]:
    """대답에서 걸린 종류(sexual · grooming · danger). 비어 있으면 통과."""
    text = text or ""
    return [name for name, hit in (("sexual", SEXUAL.search(text)), ("grooming", GROOMING.search(text)),
                                   ("danger", DANGER.search(PEST_KILL.sub(" ", text)))) if hit]


def blocking_hits(text: str, *, safety_turn: bool) -> list[str]:
    """대답을 바꿀 까닭(reply_hits 중). 위기 대응(위기 턴·상담 번호)이면 성적인 내용만 본다 — 위기 대답에
    위험한 낱말('뛰어내리면 안 돼')·'엄마한테 말해'는 들어가도 성적인 말은 필요 없다."""
    hits = reply_hits(text)
    if safety_turn or CRISIS_LINE.search(text or ""):
        return [h for h in hits if h == "sexual"]
    return hits


def filter_reply(text: str, *, polite: bool, safety_turn: bool) -> tuple[str, bool]:
    """(글, 바꿨나). 걸리면 대답 전체를 FALLBACK 으로. 위기 대응(상담 번호·위기 턴)은 성적인 내용만 바꾼다."""
    if not blocking_hits(text, safety_turn=safety_turn):
        return text, False
    return FALLBACK[polite], True


def blocked_result(instructions: str) -> PersonaResponseResult:
    """입력 검사에 걸린 턴의 대답(모델을 부르지 않음). 감정·목소리 글·기억 빈틈 없음, 쓴 기억 없음."""
    return PersonaResponseResult(content=FALLBACK[is_polite(instructions)], retrieved_memory_ids=[],
                                 provider=SKIPPED, model=SKIPPED)
