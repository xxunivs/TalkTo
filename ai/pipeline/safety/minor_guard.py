"""만 19세 미만 대화 상대 안전 검사 (법률 자문 2026-10-21, WO 4, LLM 미호출, 이미 조립된 지침과 fixedPrompt 에도 매 턴 적용)"""

from __future__ import annotations

import re

from app.schema.persona import PersonaResponseResult
from orchestrator.guard import CRISIS_LINE
from orchestrator.text import listener_speech_level
from pipeline.s3_synthesis.common_rules import RULES_DIR, render_block_file

MINOR_NOTE = "\n".join(render_block_file(RULES_DIR / "minor_safety.md", {"minor"}))

SEXUAL = re.compile(
    r"성관계|섹스|야동|음란|포르노|자위(?![대권])|애무|알몸|나체"
    r"|성적인\s*(?:말|얘기|이야기|행동|농담|사진|관계|접촉)|야한\s*(?:사진|얘기|이야기|거|것|짓)|몸매"
    r"|가슴\s*(?:만져|만지|보여\s*줘)|벗은\s*(?:몸|사진)|(?<![a-z])(?:sex|porn|nude)",
    re.IGNORECASE,
)
# 조사 뒤 는, 도와 띄어쓰기 변형 포함 ('엄마한테는 말하지 마')
# 챙기는 말 제외 ('엄마한테 말하지 그랬어', '왜 말하지 않았어', '비밀번호')
GROOMING = re.compile(
    r"(?:엄마|아빠|부모님|어른)\s*(?:한테|께|에게)(?:는|도)?\s*(?:말\s*하지(?!\s*(?:그랬|않았|못))|비밀(?!\s*번호))"
    r"|둘이서만\s*만나|비밀로\s*(?:하자|해)|몰래\s*만나"
)
# 대답에만 적용 (제외: '죽여준다', '마약김밥', '물총으로', '폭탄 세일', '그네에서 뛰어내리면 다쳐')
# 차단 유지: '죽여 줄게', '옥상에서 뛰어내리자', '뛰어내려 봐'
DANGER = re.compile(
    r"죽(?:여(?!\s*(?:준다|주네|주는|주더라|줬))|일|이자)|칼로\s*(?:찌|찔|베|벨)|때려\s*죽|폭탄(?!\s*(?:세일|머리))"
    r"|(?<!물)총으로|자살\s*방법|목을\s*매"
    r"|(?:옥상|창문|다리|난간)에서\s*뛰어내[리려]|뛰어내리(?:자|라)\b|뛰어내려\s*(?:봐|보자)"
    r"|약을\s*(?:많이|한꺼번에)|술\s*(?:마시자|마셔\s*봐)|담배\s*(?:피우자|피워\s*봐)"
    r"|마약(?!\s*(?:김밥|떡볶이|옥수수|계란|빵|토스트|베개))|가출하자"
)
# 벌레, 게임 괴물 죽이자는 평범한 말 ('모기 죽이자', '좀비 죽여') DANGER 검사 전 제거
PEST_KILL = re.compile(r"(?:모기|파리|벌레|개미|좀비|몬스터)(?:를|을|도|는|부터|만)?\s*죽")

# 말 높이별 대체 글 (가족 표 '지금 대화하는 사람' 줄이 존댓말이면 True)
FALLBACK = {
    True: "그 이야기는 잘 모르겠어요. 우리 다른 이야기 해요.",
    False: "그 얘기는 잘 모르겠어. 우리 다른 얘기 하자.",
}
# 입력 검사로 모델 미호출 대답의 provider, model (추적용, BE 미사용)
SKIPPED = "minor_guard"


def is_minor(minor: bool | None, deceased: bool | None) -> bool:
    """미성년 규칙 적용 여부 (떠나신 자녀는 제외, deceased 미상이면 적용)"""
    return bool(minor) and not deceased


def is_polite(instructions: str) -> bool:
    return listener_speech_level(instructions) == "존댓말"


def input_blocked(message: str) -> bool:
    """가족 말의 성적 내용, 비밀 만남 여부 (True 면 LLM 미호출)"""
    return bool(SEXUAL.search(message or "") or GROOMING.search(message or ""))


def safe_history(history: list) -> list:
    """입력 검사에 걸린 가족 말과 바로 뒤 대체 글을 뺀 history (BE 는 막힌 말도 history 로 보냄)"""
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
    """대답에서 걸린 종류 (sexual, grooming, danger, 비면 통과)"""
    text = text or ""
    return [
        name
        for name, hit in (
            ("sexual", SEXUAL.search(text)),
            ("grooming", GROOMING.search(text)),
            ("danger", DANGER.search(PEST_KILL.sub(" ", text))),
        )
        if hit
    ]


def blocking_hits(text: str, *, safety_turn: bool) -> list[str]:
    """대답 교체 사유 (위기 턴, 상담 번호 포함이면 성적 내용만: '뛰어내리면 안 돼' 같은 위기 대응 보존)"""
    hits = reply_hits(text)
    if safety_turn or CRISIS_LINE.search(text or ""):
        return [h for h in hits if h == "sexual"]
    return hits


def filter_reply(text: str, *, polite: bool, safety_turn: bool) -> tuple[str, bool]:
    """(글, 교체 여부) (걸리면 대답 전체를 FALLBACK 으로)"""
    if not blocking_hits(text, safety_turn=safety_turn):
        return text, False
    return FALLBACK[polite], True


def blocked_result(instructions: str) -> PersonaResponseResult:
    """입력 검사에 걸린 턴의 대답 (모델 미호출, 감정, 목소리 글, 기억 빈틈, 사용 기억 없음)"""
    return PersonaResponseResult(
        content=FALLBACK[is_polite(instructions)],
        retrieved_memory_ids=[],
        provider=SKIPPED,
        model=SKIPPED,
    )
