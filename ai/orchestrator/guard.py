"""⑥ 사후 가드. LLM 을 다시 부르지 않는다(코드 판정만).

PassGuard: 아무것도 안 함(기본).
GuardV1(ORCHESTRATOR_GUARD=v1) — 규칙은 서로 독립인 순수 함수, 아래 순서로 적용한다.
  0. 헤더   첫 줄 "[근거: m3,m10]"/"[근거: 없음]" 을 떼고(TTS 로 새지 않게) 인용 기억을 찾는다.
            id 는 m{0부터 순번}, 선택된 기억 → 델타 순(header_block 참고).
  1. 누출   restricted.leak_terms 가 든 문장을 지운다. 다 지워지면 brief_answer(있으면) 또는 되묻기.
  1-1. 안전  위기·죽음을 묻는 말(precheck.SAFETY)이거나 답에 위기 상담 번호(109·119 등)가 있으면 여기서 그대로 통과(2~4 안 함).
  2. 지어내기(질문일 때만, 답이 이미 '모른다·되묻기'면 통과)  ※ 화제 임계 0.5 는 사전 판정(0.6)과 따로 실측으로 정한 값
            R1a 헤더가 [근거: 없음] / 없는 id → 되묻기
            R1t 인용 기억(+프로필)이 질문 화제어를 못 덮음(커버율 < 임계) → 되묻기   ← 엉뚱한 id 를 인용하며 지어내는 경우
            R1b 답의 숫자·고유명사가 근거(인용 기억, 헤더 없으면 넣은 기억 전부)+질문+페르소나 지시문에 없음 → 되묻기
            안부·인사·마음만 있는 질문(precheck.strip_relational 뒤 화제어 없음)은 R1a·R1t 를 건너뛰고 R1b 만 본다
            (근거 = 넣은 기억 전부 + 말하는 사람 이름). 넣은 기억이 하나도 없으면 R1a 를 건너뛴다([근거: 없음]이 맞는 헤더).
  3. 애정   '사랑' 문장은 가족이 먼저 사랑을 말했거나(되받기) 마무리 인사일 때만, 세션당 1번(실제 통화도 마무리에 한 번 정도).
            그 밖엔 문장 삭제.
  4. 챙김   밥·건강 챙김 문장은 세션당 care_limit 번까지(모델은 절반 넘는 턴에 넣는다). 넘으면 그 문장 삭제(다른 문장이 남을 때만).
  고인   비공개 고인 페르소나면 잊은 듯 말한 문장(forgot)을 지운다(잠금 2). 지어내기 검사 앞에서 한 번, 모든 반환에서 한 번.
         다 지워지면 고인 되묻기. 위기·죽음 턴은 되묻기로 바꾸지 않고(남는 문장이 있을 때만 지운다), 상담 번호 문장은 지우지 않는다.
         지어내기 검사는 이야기를 청하는 되묻기(TELL_ME)일 때만 건너뛴다('글쎄·몰라'로는 건너뛰지 않는다).
되묻기 문구는 세 단계다(ask_back_text): 질문 화제어를 넣어 구체적으로 되묻고 → 같은 세션에서 연속이면 짧게 →
세 번째부터는 상대 근황으로 돌린다(취조처럼 들리지 않고, 기억이 적은 페르소나에서 대화가 닫히지 않게).
화제어는 살아 계신 분·공개 본인만 넣고, 가족 말에 아픈 말·성·위험·위기 말(SENSITIVE_TOPIC 등)이 있으면 넣지 않는다.
가족 표의 '지금 대화하는 사람' 줄이 존댓말이면 높인 되묻기(family_polite)를 쓴다(가드 문구는 모델을 거치지 않는다).
GuardV11(ORCHESTRATOR_GUARD=v11): v1 의 오차단을 줄인 판정(아래 클래스 설명).
"""

from __future__ import annotations

import re

from orchestrator.contracts import (
    Generation,
    GuardResult,
    MemoryItem,
    MemorySelection,
    SessionState,
    TurnInput,
)
from orchestrator.persona import subject_name_from
from orchestrator.precheck import is_question, is_safety_turn, profile_section, strip_preamble
from orchestrator.restricted import redact_instructions
from orchestrator.text import listener_speech_level


class PassGuard:
    impl = "pass_v0"

    def apply(
        self,
        generation: Generation,
        turn: TurnInput,
        state: SessionState,
        selection: MemorySelection,
    ) -> GuardResult:
        return GuardResult(text=generation.text)


# ---------------------------------------------------------------- 헤더 (조립기와 같이 씀)

HEADER_RULE = (
    '# 답변 형식 (꼭 지킬 것)\n첫 줄에 이 답의 근거가 된 참고 기억 id 를 "[근거: m12,m40]" 처럼 쓴다. '
    '참고 기억에 근거가 없으면 "[근거: 없음]" 이라고 쓴다. 둘째 줄부터 평소처럼 답한다. 근거가 없으면 지어내지 말고 되물어라.'
)
HEADER = re.compile(r"^\s*\[근거:\s*([^\]]*)\]\s*")


def labeled(selection: MemorySelection) -> list[MemoryItem]:
    return list(selection.memories) + list(selection.delta)


def header_block(selection: MemorySelection) -> str:
    """줄마다 (m번호) id 를 붙인 참고 기억 블록. 번호는 parse_header 와 같다."""
    items = labeled(selection)
    if not items:
        return ""
    lines = [
        f"- (m{i}) " + (f"[{m.title}] " if m.title else "") + m.text.strip()
        for i, m in enumerate(items)
    ]
    return (
        "참고 기억(각 줄 앞 (m번호)가 기억 id. 대화 화제가 닿으면 구체 사실을 그대로 녹이고, 없는 내용은 지어내지 않는다):\n"
        + "\n".join(lines)
    )


def parse_header(
    text: str, selection: MemorySelection
) -> tuple[str, list[MemoryItem] | None, bool]:
    """(본문, 인용 기억(None=헤더 없음), 잘못된 id 있음)."""
    m = HEADER.match(text)
    if not m:
        return text, None, False
    body = text[m.end() :].strip()
    raw = [x.strip() for x in m.group(1).split(",") if x.strip()]
    if not raw or raw == ["없음"]:
        return body, [], False
    items, bad = labeled(selection), False
    cited = []
    for x in raw:
        if re.fullmatch(r"m\d+", x) and int(x[1:]) < len(items):
            cited.append(items[int(x[1:])])
        else:
            bad = True
    return body, cited, bad


# ---------------------------------------------------------------- 규칙 재료

DONT_KNOW = re.compile(
    r"(몰라|모르겠|모른다|기억이\s?(잘\s?)?안|생각이\s?(잘\s?)?안\s?나|가물가물|글쎄|잊어버|까먹|얘기\s?(좀\s?)?해\s?줄래|말해\s?줄래"
    # 고인 페르소나의 되묻기(잊은 듯 말하지 않고 가족에게 듣고 싶어 한다: ASK_BACK *_deceased)
    r"|얘기[^.?!\n]{0,8}해\s?줄래|이야기[^.?!\n]{0,10}(들려|해)\s?주실래|들려\s?줄래)"
)
# DONT_KNOW 중 이야기를 청하는 되묻기만. 고인은 이 말일 때만 지어내기 검사를 건너뛴다('글쎄·몰라' 뒤에 지어낸 말이 숨지 않게)
TELL_ME = re.compile(
    r"얘기\s?(좀\s?)?해\s?줄래|말해\s?줄래|얘기[^.?!\n]{0,8}해\s?줄래|이야기[^.?!\n]{0,10}(들려|해)\s?주실래"
    r"|들려\s?줄래"
)
AFFECTION = re.compile(r"사랑")
USER_LOVE = re.compile(r"사랑|보고\s?싶")
CLOSING = re.compile(
    r"(끊(어|을게|는다|자|을께)|들어가(세요|요|라|볼게)|잘\s?(자|있어|지내)|또\s?(전화|연락)|안녕히|다음에\s?(또|전화)|이만)"
)
CARE = re.compile(
    r"(밥|끼니|챙겨\s?먹|건강\s?(잘\s?)?챙|건강이\s?제일|몸\s?(조심|건강|챙|잘\s?돌)|아프지\s?마|감기\s?조심|푹\s?쉬|잘\s?먹어|무리하지\s?마)"
)
NUM = re.compile(r"\d+")
# 위기 상담 번호(자살예방 109·옛 1393, 정신건강 위기상담 1577-0199, 응급 119)
CRISIS_LINE = re.compile(r"(?<!\d)(?:109|119|1393|1577-0199)(?!\d)")
SENT = re.compile(r"(?<=[.!?…~])\s+|\n+")
# 되묻기 문구 (연속 되묻기 0 / 1 / 2 이상). 예전엔 두 단계였고 첫 문구가 "네가 얘기 좀 해 줄래?"였는데,
# 기억이 적은 페르소나에서는 매 턴 이 문구와 "그건 잘 모르겠다"가 번갈아 나와 대화가 닫혔다(10/18 실사용, 4층 레포 세션 19).
# 지금은 첫 단계에 질문 화제어를 넣어 구체적으로 되묻고(ASK_BACK_TOPIC, 화제어가 없으면 [0]),
# 세 번째부터는 상대 근황으로 돌린다.
ASK_BACK = {
    "family": (
        "글쎄, 그건 기억이 잘 안 나네. 그게 언제 적 일이야?",
        "그건 잘 모르겠다.",
        "그건 잘 모르겠고, 너는 요즘 어떻게 지내?",
    ),
    "family_polite": (
        "글쎄요, 그건 기억이 잘 안 나네요. 그게 언제 적 일이에요?",
        "그건 잘 모르겠어요.",
        "그건 잘 모르겠고, 요즘은 어떻게 지내세요?",
    ),
    "public": (
        "그건 제가 잘 모르겠네요. 언제 적 이야기예요?",
        "그 부분은 제가 잘 모르겠어요.",
        "그건 제가 모르겠고, 요즘은 어떻게 지내세요?",
    ),
    # 고인 페르소나는 '기억이 안 난다'·'모르겠다'고 말하지 않는다(잠금 2, 대표 지시 2026-10-21).
    # 지어내지도 않고, 가족에게서 그 이야기를 듣고 싶어 하며 되묻는다. 세 번째도 모른다는 말 없이 근황으로 돌린다.
    "family_deceased": (
        "그 얘기 네가 한번 해 줄래? 듣고 싶구나.",
        "그래, 네 얘기 더 들려줄래?",
        "그 얘기는 다음에 또 들려줄래? 너는 요즘 어떻게 지내?",
    ),
    "family_polite_deceased": (
        "그 이야기 한번 들려주실래요? 듣고 싶네요.",
        "이야기 더 들려주실래요?",
        "그 이야기는 다음에 또 들려주실래요? 요즘은 어떻게 지내세요?",
    ),
}
# 첫 되묻기에 질문 화제어(명사 최대 2개)를 넣은 꼴. {topic} 은 GuardV1.ask_back_topic.
# 고인(*_deceased)은 화제어를 넣지 않고 늘 ASK_BACK[kind][0] — 가드 문구는 모델을 거치지 않아, 가족 말의 병명·
# 자세한 말을 고인이 되받아 "○○ 얘기 해 줄래? 듣고 싶구나"로 청하게 되면 사망 정책에 어긋난다(10/24 리뷰).
ASK_BACK_TOPIC = {
    "family": "글쎄, {topic} 얘기는 기억이 잘 안 나네. 그게 언제 적 일이야?",
    "family_polite": "글쎄요, {topic} 이야기는 기억이 잘 안 나네요. 그게 언제 적 일이에요?",
    "public": "{topic} 이야기는 제가 잘 모르겠네요. 언제 적 이야기예요?",
}


def ask_back_text(kind: str, streak: int, topic: str = "") -> str:
    """되묻기 문구. streak = 같은 세션에서 연속 되묻기 횟수. 화제어가 없거나 고인이면 첫 단계는 화제 없는 문구."""
    if streak >= 2:
        return ASK_BACK[kind][2]
    if streak >= 1:
        return ASK_BACK[kind][1]
    tpl = ASK_BACK_TOPIC.get(kind)
    return tpl.format(topic=topic) if topic and tpl else ASK_BACK[kind][0]


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT.split(text) if s and s.strip()] or [text]


# 고인 페르소나가 잊은 듯 말하는 말(잠금 2). 내가 기억이 안 난다·잊었다·모르겠다는 말만 좁게 잡는다
# (기억에 없다·깜빡했다·가물거린다도. BE app/services/forgetting.py 와 같은 말을 잡는다).
# 안 걸리는 말: '잊지 마라'·'잊어버리지 마라'(챙김), '기억이 안 나면'·'안 나도'·'잊었으면'(조건·바람),
# '안 잊었다'·'못 잊었지'(기억한다), '네 생각이 안 나는 날이 없다'·'잊었을 리가'·'잊었던 일'(그리움),
# '보고 싶은지 모르겠다'·'어쩔 줄을 모르겠다'(감탄), '아무·다른 생각이 안 나더라'(마음), '잘 모르겠으면'(조건).
# 맨 '몰라·모른다'는 넣지 않는다(no_advice 의 "그건 내가 잘 모른다" 같은 약 이야기 피하기까지 지워진다).
_ADV = (
    r"(?:잘|하나도|전혀|통|영|도통|도무지|이젠|이제는|이제|요즘|요새|다)"  # 둘까지(이젠 잘 안 나)
)
_NA = r"안\s?(?:나(?!면|도|는\s?(?:날|때|적))|난(?!다면|다고|\s?(?:날|때|적))|났|납)"  # 안 나·난다·났어·납니다
FORGOT = re.compile(
    rf"기억(?:이|도)?\s?(?:{_ADV}\s?){{0,2}}{_NA}|기억(?:이|도)?\s?(?:잘\s?)?나지\s?않(?!는\s?(?:날|때|적))"
    r"|기억(?:을|도)?\s?(?:잘\s?)?못\s?하겠|기억이\s?가물|가물\s?(?:가물|거)|기억(?:에|이)\s?없(?!는|으면|다면)"
    r"|(?<!안)(?<!안\s)(?<!못)(?<!못\s)(?:잊어\s?버렸|잊어먹었|잊었|까먹었|까먹어\s?버렸|깜[빡박]\s?했|다\s?잊어서)"
    r"(?!을\s?(?:리|까)|으면|던|다고|다면)"
    rf"|(?<!아무)(?<!아무\s)(?<!다른\s)(?<!딴\s)생각이\s?(?:{_ADV}\s?){{0,2}}{_NA}"
    r"|(?<!지)(?<!지\s)(?<!줄\s)(?<!줄을\s)(?<!바\s)(?<!바를\s)(?:잘\s?)?모르겠(?!으면|나요?[?？])"
)  # '내가 모르겠나?'는 안다는 말
# 걸린 말이 묻는 꼴이면("까먹었니?", "기억 안 나지?", "잊었어?", "내가 너를 잊었겠니?") 잊은 말이 아니다
_ASKS = re.compile(r"[^\s,.!…~]*?(?:[?？]|(?:니|냐)(?=$|[\s,.!…~]))")
# 단, 혼잣말로 묻는 꼴("잊어버렸나?", "안 나네?", "왜 이렇게 기억이 안 나지?")은 잊은 말이다
_WONDER = re.compile(r"(?:나|네|는가)요?[?？]")
_WHY = re.compile(r"왜\s?(?:이렇게|이리|자꾸)")
# 걸린 말 앞 마디(쉼표부터)가 듣는 사람 이야기면("너 또 까먹었구나", "넌 모르겠지만") 잊은 말이 아니다.
# 단, 듣는 사람이 한 말을 내가 잊은 꼴("네가 말한 거 다 까먹었다", "네가 해 준 얘기는 기억이 안 나네")은 잊은 말이다
_YOU = re.compile(r"(?<![가-힣])(?:너|넌|너는|너도|네가|니가|자네|당신)(?![가-힣])")
_ME = re.compile(
    r"(?<![가-힣])(?:난|나는|나도|내가|저는|저도|제가)(?![가-힣])"
)  # 맨 '나'는 '안 나'와 겹쳐 뺀다
_TOLD = re.compile(
    r"(?:한|준|던|낸)\s?(?:거|건|걸|것|얘기|이야기|말|일)(?:[은는을를도]|(?![가-힣]))"
)


def forgot(sentence: str) -> bool:
    """페르소나 자신이 기억이 안 난다·잊었다·모르겠다고 하는 문장인가. 묻는 말·듣는 사람 이야기·상담 번호 문장은 아니다."""
    if CRISIS_LINE.search(sentence):
        return False
    for m in FORGOT.finditer(sentence):
        head = sentence[
            max(sentence.rfind(",", 0, m.start()), sentence.rfind("，", 0, m.start()))
            + 1 : m.start()
        ]
        tail = sentence[m.end() :]
        if _ASKS.match(tail) and not (_WONDER.match(tail) or _WHY.search(head)):
            continue
        you = _YOU.search(head)
        if you and not _ME.search(head) and not _TOLD.search(head, you.end()):
            continue
        return True
    return False


def says_forgot(text: str) -> bool:
    return any(forgot(s) for s in sentences(text))


def drop_forgetting(text: str, fallback: str) -> str:
    """잊은 듯 말한 문장을 지운다. 다 지워지면 fallback. 지울 것이 없으면 글을 한 글자도 바꾸지 않는다."""
    sents = sentences(text)
    kept = [s for s in sents if not forgot(s)]
    if len(kept) == len(sents):
        return text
    return " ".join(kept) if kept else fallback


def _deceased(turn: TurnInput, prof) -> bool:
    """고인 페르소나인가. BE 가 보낸 persona.deceased 가 먼저이고, 없으면 프로필 추정."""
    if turn.deceased is not None:
        return bool(turn.deceased)
    return bool(getattr(prof, "deceased", False))


# 지어내기(R1b) 근거로 함께 보는 앞 대화 줄 수(가족 말만 쓴다. BE 는 최근 12줄을 보낸다)
HEARD_TURNS = 12
# 화제 확인(R1t)에서 빼는 말: 가벼운 동사·빈도어(질문 화제가 아님). 오차단 사례를 보고 정했다.
TOPIC_LIGHT = frozenset(
    {
        "하",
        "있",
        "되",
        "어떻",
        "가",
        "오",
        "보",
        "알",
        "말하",
        "그렇",
        "이렇",
        "기억나",
        "맨날",
        "자주",
        "늘",
    }
)
# 가족 호칭 ↔ 기억에 쓰인 말(가드 전용, 사전 판정 사전은 그대로)
TOPIC_ALIASES = {"아빠": ("아버지",), "엄마": ("어머니",)}
# 되묻기 문구의 화제어에서 빼는 명사: 때·호칭·인사·묻는 말·막연한 말(화제가 아니라 "글쎄, 그때 얘기는…"·
# "얼마 얘기는…"처럼 어색해진다)
TOPIC_PHRASE_STOP = frozenset(
    {
        "저번",
        "이번",
        "그때",
        "요즘",
        "오늘",
        "어제",
        "옛날",
        "안녕",
        "할머니",
        "할아버지",
        "할매",
        "할배",
        "엄마",
        "아빠",
        "어머니",
        "아버지",
        "어머님",
        "아버님",
        "여보",
        "당신",
        "우리",
        "얘기",
        "이야기",
        "얼마",
        "얼마큼",
        "무엇",
        "언제",
        "어디",
        "누구",
        "정도",
        "이유",
        "때문",
        "사람",
        "생각",
        "마음",
        "기분",
        "느낌",
    }
)
# 가족 말(또는 화제어)에 이런 말이 있으면 되묻기에 화제어를 넣지 않는다(화제 없는 [0] 문구). 가드 문구는 모델을 거치지
# 않아 가족 말의 명사가 그대로 대답이 된다 — 병·돈·법·부부 사이처럼 아픈 말, 성·위험한 말(SEXUAL·GROOMING·DANGER,
# 위기·죽음 SAFETY 는 ask_back_topic 에서 같이 본다). 넘쳐 걸러도 예전 문구가 될 뿐이라 넓게 잡는다(10/24 리뷰).
_JOSA = r"(?:이|가|을|를|은|는|도|에|만|으로|로|과|와|이랑|하고|때문)?(?![가-힣])"
SENSITIVE_TOPIC = re.compile(
    r"병원|병실|병동|입원|퇴원|수술|투병|투석|항암|중환자|응급실|진단|질환|질병|치매|알츠하이머|파킨슨|뇌졸중|중풍|뇌출혈"
    r"|뇌경색|심장마비|심근경색|당뇨|고혈압|요양|간병|호스피스|우울|공황|정신과|정신병|장애|마비|종양|백혈병|아프|아팠|아픈|앓"
    rf"|[가-힣]{{1,3}}암{_JOSA}|(?<![가-힣])(?:암|병|약|빚|돈){_JOSA}"
    r"|낙태|임신|중절|유산|피임|성병|성폭행|성추행|강간|추행"
    r"|바람\s?(?:을|이)?\s?(?:피|폈|핀|났|나서)|바람둥이|불륜|외도|애인|내연|이혼|별거|재혼"
    r"|감옥|교도소|구치소|수감|징역|감방|전과|범죄|체포|구속|재판|소송|고소|경찰|벌금|사기"
    r"|채무|대출|사채|파산|부도|보증|월급|연봉|재산|상속|유언|증여|보험|연금|통장|계좌|비밀번호"
    r"|폭력|폭행|학대|때렸|때리|맞았|도박|노름|중독|술주정"
    r"|죽음|사망|죽었|죽은|사고|부고|유골|납골|영정"
)


def proper_nouns(text: str) -> list[str]:
    from orchestrator.text import proper_nouns as nnp

    return nnp(text)


def unsupported_tokens(body: str, base: str) -> list[str]:
    out = [n for n in NUM.findall(body) if n not in base]
    out += [p for p in proper_nouns(body) if p not in base]
    return sorted(set(out))


class GuardV1:
    impl = "guard_v1"

    def __init__(self, profiles, *, topic_threshold: float = 0.5, care_limit: int = 2) -> None:
        from orchestrator.precheck import CoveragePrecheck

        self.profiles = profiles
        self.cov = CoveragePrecheck(
            profiles, threshold=topic_threshold
        )  # 화제어·동의어 판정 재사용
        self.topic_threshold = topic_threshold
        self.care_limit = care_limit

    # 훅(GuardV11 이 바꾼다) --------------------------------------------
    def question_text(self, turn: TurnInput) -> str:
        return strip_preamble(turn.message)

    def aliases(self, term: str) -> tuple[str, ...]:
        return TOPIC_ALIASES.get(term, ())

    def extra_ground(self, turn: TurnInput) -> str:
        return ""

    def ask_back_topic(self, turn: TurnInput, state: SessionState, leak_terms=()) -> str:
        """되묻기 문구에 넣을 질문 화제어: 질문의 명사 중 사람 이름·가벼운 말·가릴 말(leak_terms)을 뺀 것, 나온 순서로
        최대 2개. 명사만 쓴다(어간 '끓이' 같은 건 말이 안 된다). 없으면 빈 문자열.
        가족 말에 아픈 말·성·위험·위기 말이 있으면 화제어를 하나도 넣지 않는다(빈 문자열 → 화제 없는 되묻기)."""
        from orchestrator.precheck import SAFETY
        from orchestrator.text import GENERIC_STOP, nouns

        # 이 모듈을 import 하는 쪽이라 여기서 import 한다(순환)
        from pipeline.safety.minor_guard import DANGER, GROOMING, SEXUAL

        names = {
            n
            for n in (
                subject_name_from(turn.persona_instructions),
                state.speaker,
                turn.speaker_hint,
            )
            if n
        }
        try:
            question = self.question_text(turn)
            if any(
                p.search(t)
                for p in (SENSITIVE_TOPIC, SAFETY, SEXUAL, GROOMING, DANGER)
                for t in (turn.message or "", question)
            ):
                return ""
            stop = GENERIC_STOP | TOPIC_LIGHT | TOPIC_PHRASE_STOP | names
            terms = [
                t
                for t in nouns(question)
                if t not in stop
                and not SENSITIVE_TOPIC.search(t)
                and not any(t in x or x in t for x in leak_terms if x)
            ]
        except Exception:  # noqa: BLE001 — 문구는 덧붙임일 뿐, 화제어 추출 실패로 가드가 죽으면 안 된다
            return ""
        return " ".join(list(dict.fromkeys(terms))[:2])

    def has_topic(self, turn: TurnInput, state: SessionState) -> bool:
        """안부·인사·마음 말'만' 있는 질문이 아닌가. 원래 화제어가 없던 질문도 True(전처럼 R1a 를 본다)."""
        from orchestrator.precheck import is_relational

        names = {
            n
            for n in (
                subject_name_from(turn.persona_instructions),
                state.speaker,
                turn.speaker_hint,
            )
            if n
        }
        return not is_relational(
            self.question_text(turn),
            lambda x: [t for t in self.cov.topic_terms(x, names) if t not in TOPIC_LIGHT],
        )

    def topic_covered(
        self, turn: TurnInput, state: SessionState, cited: list[MemoryItem], profile: str
    ) -> bool | None:
        body = self.question_text(turn)
        names = {
            n
            for n in (
                subject_name_from(turn.persona_instructions),
                state.speaker,
                turn.speaker_hint,
            )
            if n
        }
        terms = [t for t in self.cov.topic_terms(body, names) if t not in TOPIC_LIGHT]
        if not terms:
            return None
        # 인용 기억 전부(+프로필)를 한 덩어리로 본다 — 여러 기억을 묶어 답하는 게 정상이라서
        raw = " ".join((m.title + " " if m.title else "") + m.text for m in cited) + (
            " " + profile if profile else ""
        )
        doc = self.cov._morph_set(raw)

        def hit(t: str) -> bool:
            if self.cov._covered(t, doc):
                return True
            if len(t) >= 2 and t in raw:  # 합성어 속(병원 ⊂ 서울대병원)
                return True
            return any(a in raw for a in self.aliases(t))

        return sum(hit(t) for t in terms) / len(terms) >= self.topic_threshold

    def apply(
        self,
        generation: Generation,
        turn: TurnInput,
        state: SessionState,
        selection: MemorySelection,
    ) -> GuardResult:
        prof = self.profiles.profile_for(turn)
        restricted = prof.restricted
        kind = (
            "public"
            if prof.public
            else (
                "family_polite"
                if listener_speech_level(turn.persona_instructions) == "존댓말"
                else "family"
            )
        )
        if kind != "public" and _deceased(turn, prof):
            kind += "_deceased"
        deceased = kind.endswith("_deceased")
        streak = state.counters.get("ask_back_streak", 0)
        reasons: list[str] = []
        delta: dict[str, int] = {}
        made: list[str] = []

        def ask_back() -> str:
            """되묻기 문구(쓸 때만 화제어를 뽑는다). 고인은 화제어를 넣지 않는다(ASK_BACK_TOPIC 참고).
            고인 문구는 잊은 말이 되지 않게 한 번 더 본다(done 의 drop_forgetting 도 거친다)."""
            if not made:
                topic = (
                    ""
                    if deceased
                    else self.ask_back_topic(
                        turn, state, restricted.leak_terms if restricted else ()
                    )
                )
                text = ask_back_text(kind, streak, topic)
                made.append(ASK_BACK[kind][0] if deceased and says_forgot(text) else text)
            return made[0]

        body, cited, bad_id = parse_header(generation.text, selection)
        cited_ids = None if cited is None else [m.id for m in cited]
        # 기억이 하나도 없으면 '[근거: 없음]'(·없는 id)은 맞는 헤더다 — 그때는 R1a 로 되묻지 않고 R1b 만 본다(세션 19)
        no_memories = not labeled(selection)

        def done(text: str, action: str) -> GuardResult:
            if deceased:  # 고인은 잊은 듯 말하지 않는다(잠금 2). 모든 반환 경로(가림 답·위기 턴 포함)에서 거른다
                kept = drop_forgetting(text, "")
                if kept != text:
                    if kept:
                        reasons.append("forgot_drop")
                        text, action = kept, ("trim" if action == "pass" else action)
                    elif "safety_skip" in reasons:  # 위기·죽음 대답은 되묻기로 바꾸지 않는다
                        reasons.append("forgot_kept_safety")
                    else:
                        reasons.append("forgot_drop")
                        text, action = ask_back(), "ask_back"
            if action == "ask_back":
                delta["ask_back_streak"] = 1
            elif streak:
                delta["ask_back_streak"] = -streak
            return GuardResult(
                text=text,
                action=action,
                reasons=reasons,
                cited_memory_ids=cited_ids,
                counter_delta=delta,
            )

        # 1. 누출
        if restricted and restricted.leak_terms:
            sents = sentences(body)
            kept = [s for s in sents if not any(t in s for t in restricted.leak_terms)]
            if len(kept) < len(sents):
                reasons.append("leak")
                if not kept:
                    return done(restricted.brief_answer or ask_back(), "redact")
                body = " ".join(kept)

        # 위기(자해 생각)·그분의 죽음을 묻는 말이거나 대답에 위기 상담 번호가 있으면 지어내기·문장 삭제를 하지 않는다.
        # 위기 대답이 "기억이 안 나네, 얘기해 줄래?"로 바뀌거나 상담 번호 문장이 지워지면 안 된다(누출 검사는 위에서 했다).
        if is_safety_turn(turn.message) or CRISIS_LINE.search(body):
            reasons.append("safety_skip")
            return done(body, "trim" if "leak" in reasons else "pass")

        # 고인: 잊은 듯 말한 문장을 먼저 지운다. '기억이 안 나네'가 DONT_KNOW 에 걸려 나머지 말이 지어내기 검사를 건너뛰지 않게
        if deceased:
            kept = drop_forgetting(body, "")
            if kept != body:
                reasons.append("forgot_drop")
                if not kept:
                    return done(ask_back(), "ask_back")
                body = kept

        # 2. 지어내기 (질문일 때만). 고인은 이야기를 청하는 되묻기일 때만 건너뛴다(TELL_ME)
        msg = self.question_text(turn)
        if msg and is_question(msg) and not (TELL_ME if deceased else DONT_KNOW).search(body):
            instructions, _ = redact_instructions(turn.persona_instructions, restricted)
            profile = profile_section(instructions)
            # 안부·인사·마음만 있는 질문("편안하게 잘 계시죠?")은 기억을 묻는 게 아니라 근거 기억이 없는 게 정상이다.
            # 근거 없음(R1a)·화제 확인(R1t)으로 답을 갈아 끼우지 않고, 숫자·고유명사를 지어냈는지(R1b)만 본다.
            relational = not self.has_topic(turn, state)
            if relational:
                reasons.append("relational_skip_r1a_r1t")
            if not relational and not no_memories and cited is not None and (bad_id or not cited):
                reasons.append("R1a_bad_id" if bad_id else "R1a_no_ground")
                return done(ask_back(), "ask_back")
            if not relational and cited:
                covered = self.topic_covered(turn, state, cited, profile)
                if covered is False:
                    reasons.append("R1t_topic_not_in_cited")
                    return done(ask_back(), "ask_back")
            if relational:  # 인용이 없어도 넣은 기억 전부 + 말하는 사람 이름을 근거로 본다
                ground = labeled(selection)
                speakers = " ".join(n for n in (state.speaker, turn.speaker_hint) if n)
            else:
                ground = cited if cited is not None else labeled(selection)
                speakers = ""
            # 가족이 앞 대화에서 직접 말한 것(부산으로 이사 간다 등)도 근거다 — 들은 걸 되받는 걸 지어내기로 보지 않는다.
            # 페르소나가 앞서 한 말은 넣지 않는다(그때 지어낸 말이 근거가 되지 않게)
            heard = " ".join(t.content for t in turn.history[-HEARD_TURNS:] if t.role == "user")
            base = (
                " ".join(f"{m.title} {m.text}" for m in ground)
                + " "
                + turn.message
                + " "
                + instructions
                + " "
                + self.extra_ground(turn)
                + " "
                + speakers
                + " "
                + heard
            )
            bad = unsupported_tokens(body, base)
            if bad:
                reasons.append("R1b_unsupported:" + ",".join(bad))
                return done(ask_back(), "ask_back")

        # 3·4. 문장 단위 삭제
        love_ok = bool(USER_LOVE.search(turn.message) or CLOSING.search(turn.message))
        love_used = state.counters.get("affection", 0)
        care_used = state.counters.get("care", 0)
        sents, kept = sentences(body), []
        for s in sents:
            if AFFECTION.search(s):
                if love_ok and love_used == 0:
                    love_used += 1
                    delta["affection"] = delta.get("affection", 0) + 1
                else:
                    reasons.append("R3_affection_drop")
                    continue
            if CARE.search(s):
                if care_used >= self.care_limit:
                    reasons.append("care_drop")
                    continue
                care_used += 1
                delta["care"] = delta.get("care", 0) + 1
            kept.append(s)
        if (
            not kept
        ):  # 다 지워지면 원래 답의 첫 문장만이라도(빈 답보다 낫다), 카운터는 올리지 않는다
            reasons.append("all_dropped_keep_first")
            kept = sents[:1]
        text = " ".join(kept) if len(kept) < len(sents) else body
        trimmed = len(kept) < len(sents) or "leak" in reasons or "forgot_drop" in reasons
        return done(text, "trim" if trimmed else "pass")


class GuardV11(GuardV1):
    """v1.1(ORCHESTRATOR_GUARD=v11). v1 이 멀쩡한 답을 되묻기로 바꾸던 오차단을 줄인다.
    - 질문 텍스트 = 발화 분해 단서(가족 사전 있을 때). 단서가 비면(인사만) 지어내기 검사(R1)를 하지 않는다.
    - 화제 확인은 사전 판정 v1.1 의 화제어(이름 조각 합치기·가벼운 말/부르는 말 빼기)와 동의어(SYN_V11)를 쓴다.
    - R1b: 가족 이름과 부르는 꼴(서연아·정우야·도윤이)은 근거 없는 고유명사로 보지 않는다.
    """

    impl = "guard_v1.1"

    def __init__(self, profiles, *, topic_threshold: float = 0.5, care_limit: int = 2) -> None:
        super().__init__(profiles, topic_threshold=topic_threshold, care_limit=care_limit)
        from orchestrator.precheck import CoveragePrecheckV11

        self.cov = CoveragePrecheckV11(profiles, threshold=topic_threshold)

    def question_text(self, turn: TurnInput) -> str:
        return self.cov.question_text(turn)[0]

    def aliases(self, term: str) -> tuple[str, ...]:
        from orchestrator.precheck import SYN_V11

        return TOPIC_ALIASES.get(term, ()) + SYN_V11.get(term, ())

    def extra_ground(self, turn: TurnInput) -> str:
        prof = self.profiles.profile_for(turn) if self.profiles is not None else None
        if prof is None:
            return ""
        forms = []
        for n in prof.family_names:
            forms += [n, n + "아", n + "야", n + "이", n + "이야"]
        return " ".join(forms + list(prof.address_terms))
