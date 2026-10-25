"""⑥ 사후 가드 (코드 판정만, LLM 미호출): PassGuard, GuardV1, GuardV11"""

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


# ---------------------------------------------------------------- 헤더 (조립기 공용)

HEADER_RULE = (
    '# 답변 형식 (꼭 지킬 것)\n첫 줄에 이 답의 근거가 된 참고 기억 id 를 "[근거: m12,m40]" 처럼 쓴다. '
    '참고 기억에 근거가 없으면 "[근거: 없음]" 이라고 쓴다. 둘째 줄부터 평소처럼 답한다. 근거가 없으면 지어내지 말고 되물어라.'
)
HEADER = re.compile(r"^\s*\[근거:\s*([^\]]*)\]\s*")


def labeled(selection: MemorySelection) -> list[MemoryItem]:
    return list(selection.memories) + list(selection.delta)


def header_block(selection: MemorySelection) -> str:
    """(m번호) id 붙인 참고 기억 블록 (번호는 parse_header 와 동일, 선택 기억 -> 델타 순)"""
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
    """(본문, 인용 기억(None=헤더 없음), 잘못된 id 여부)"""
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
    # 사망 대상자 되묻기 (잊은 듯 말하지 않고 가족 이야기를 청함, ASK_BACK *_deceased)
    r"|얘기[^.?!\n]{0,8}해\s?줄래|이야기[^.?!\n]{0,10}(들려|해)\s?주실래|들려\s?줄래)"
)
# DONT_KNOW 중 이야기를 청하는 되묻기만 (사망 대상자는 이때만 지어내기 검사 생략, 글쎄, 몰라 뒤 지어낸 말 방지)
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
# 위기 상담 번호 (자살예방 109, 옛 1393, 정신건강 위기상담 1577-0199, 응급 119)
CRISIS_LINE = re.compile(r"(?<!\d)(?:109|119|1393|1577-0199)(?!\d)")
SENT = re.compile(r"(?<=[.!?…~])\s+|\n+")
# 되묻기 문구 (연속 되묻기 0 / 1 / 2 이상): 첫 단계는 화제어로 구체적으로(ASK_BACK_TOPIC), 셋째부터 근황 전환
# (예전 두 단계 문구는 기억 적은 페르소나에서 모른다와 번갈아 나와 대화 단절, 10/18 실사용)
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
    # 사망 대상자: 기억 안 남, 모르겠다 표현 금지 (잠금 2, 2026-10-21 대표 지시)
    # 지어내지 않고 가족 이야기를 청하는 되묻기, 셋째도 모른다는 말 없이 근황 전환
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
# 첫 되묻기에 질문 화제어(명사 최대 2개)를 넣은 꼴 ({topic} 은 GuardV1.ask_back_topic)
# 사망 대상자는 늘 ASK_BACK[kind][0] (모델 미경유 문구라 병명 등을 되받아 청하면 사망 정책 위반, 10/24 리뷰)
ASK_BACK_TOPIC = {
    "family": "글쎄, {topic} 얘기는 기억이 잘 안 나네. 그게 언제 적 일이야?",
    "family_polite": "글쎄요, {topic} 이야기는 기억이 잘 안 나네요. 그게 언제 적 일이에요?",
    "public": "{topic} 이야기는 제가 잘 모르겠네요. 언제 적 이야기예요?",
}


def ask_back_text(kind: str, streak: int, topic: str = "") -> str:
    """되묻기 문구 (streak = 같은 세션 연속 되묻기 횟수, 화제어 없거나 사망 대상자면 첫 단계는 화제 없는 문구)"""
    if streak >= 2:
        return ASK_BACK[kind][2]
    if streak >= 1:
        return ASK_BACK[kind][1]
    tpl = ASK_BACK_TOPIC.get(kind)
    return tpl.format(topic=topic) if topic and tpl else ASK_BACK[kind][0]


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT.split(text) if s and s.strip()] or [text]


# 사망 대상자가 잊은 듯 말하는 말 (잠금 2, 기억 안 남, 잊음, 모르겠다만 좁게, BE app/services/forgetting.py 와 같은 범위)
# 제외: 챙김(잊지 마라), 조건, 바람, 기억함(안 잊었다), 그리움, 감탄, 맨 몰라 (no_advice 약 이야기 회피 문장 보호)
# 부사 둘까지 (이젠 잘 안 나)
_ADV = r"(?:잘|하나도|전혀|통|영|도통|도무지|이젠|이제는|이제|요즘|요새|다)"
_NA = r"안\s?(?:나(?!면|도|는\s?(?:날|때|적))|난(?!다면|다고|\s?(?:날|때|적))|났|납)"  # 안 나, 난다, 났어, 납니다
FORGOT = re.compile(
    rf"기억(?:이|도)?\s?(?:{_ADV}\s?){{0,2}}{_NA}|기억(?:이|도)?\s?(?:잘\s?)?나지\s?않(?!는\s?(?:날|때|적))"
    r"|기억(?:을|도)?\s?(?:잘\s?)?못\s?하겠|기억이\s?가물|가물\s?(?:가물|거)|기억(?:에|이)\s?없(?!는|으면|다면)"
    r"|(?<!안)(?<!안\s)(?<!못)(?<!못\s)(?:잊어\s?버렸|잊어먹었|잊었|까먹었|까먹어\s?버렸|깜[빡박]\s?했|다\s?잊어서)"
    r"(?!을\s?(?:리|까)|으면|던|다고|다면)"
    rf"|(?<!아무)(?<!아무\s)(?<!다른\s)(?<!딴\s)생각이\s?(?:{_ADV}\s?){{0,2}}{_NA}"
    r"|(?<!지)(?<!지\s)(?<!줄\s)(?<!줄을\s)(?<!바\s)(?<!바를\s)(?:잘\s?)?모르겠(?!으면|나요?[?？])"
)  # '내가 모르겠나?'는 안다는 말
# 걸린 말이 묻는 꼴이면 제외 ("까먹었니?", "기억 안 나지?", "잊었어?", "내가 너를 잊었겠니?")
_ASKS = re.compile(r"[^\s,.!…~]*?(?:[?？]|(?:니|냐)(?=$|[\s,.!…~]))")
# 단, 혼잣말로 묻는 꼴은 포함 ("잊어버렸나?", "안 나네?", "왜 이렇게 기억이 안 나지?")
_WONDER = re.compile(r"(?:나|네|는가)요?[?？]")
_WHY = re.compile(r"왜\s?(?:이렇게|이리|자꾸)")
# 앞 마디(쉼표부터)가 듣는 사람 이야기면 제외 ("너 또 까먹었구나", "넌 모르겠지만")
# 단, 듣는 사람이 한 말을 내가 잊은 꼴은 포함 ("네가 말한 거 다 까먹었다")
_YOU = re.compile(r"(?<![가-힣])(?:너|넌|너는|너도|네가|니가|자네|당신)(?![가-힣])")
# 맨 '나' 제외 ('안 나'와 겹침)
_ME = re.compile(r"(?<![가-힣])(?:난|나는|나도|내가|저는|저도|제가)(?![가-힣])")
_TOLD = re.compile(
    r"(?:한|준|던|낸)\s?(?:거|건|걸|것|얘기|이야기|말|일)(?:[은는을를도]|(?![가-힣]))"
)


def forgot(sentence: str) -> bool:
    """페르소나 자신의 기억 안 남, 잊음, 모르겠다 문장 여부 (묻는 말, 듣는 사람 이야기, 상담 번호 문장 제외)"""
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
    """잊은 듯 말한 문장 삭제 (다 지워지면 fallback, 지울 것 없으면 원문 그대로)"""
    sents = sentences(text)
    kept = [s for s in sents if not forgot(s)]
    if len(kept) == len(sents):
        return text
    return " ".join(kept) if kept else fallback


def _deceased(turn: TurnInput, prof) -> bool:
    """사망 대상자 페르소나 여부 (BE persona.deceased 우선, 없으면 프로필 추정)"""
    if turn.deceased is not None:
        return bool(turn.deceased)
    return bool(getattr(prof, "deceased", False))


# 지어내기(R1b) 근거로 보는 앞 대화 줄 수 (가족 말만, BE 는 최근 12줄 전송)
HEARD_TURNS = 12
# 화제 확인(R1t) 제외어: 가벼운 동사, 빈도어 (오차단 사례 기반)
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
# 가족 호칭 <-> 기억 속 표현 (가드 전용, 사전 판정 사전과 별개)
TOPIC_ALIASES = {"아빠": ("아버지",), "엄마": ("어머니",)}
# 되묻기 화제어 제외 명사: 때, 호칭, 인사, 묻는 말, 막연한 말 ("그때 얘기는", "얼마 얘기는" 같은 어색한 문구 방지)
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
# 가족 말이나 화제어에 아픈 말(병, 돈, 법, 부부 사이)이 있으면 화제어 생략 (모델 미경유 문구라 그대로 노출)
# 성, 위험, 위기 말은 ask_back_topic 에서 함께 판정, 과잉 차단은 예전 문구일 뿐이라 넓게 (10/24 리뷰)
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
    """헤더, 누출, 안전 통과, 지어내기(R1a, R1t, R1b), 애정, 챙김 순 규칙 (화제 임계 0.5 는 사전 판정 0.6 과 별도 실측)"""

    impl = "guard_v1"

    def __init__(self, profiles, *, topic_threshold: float = 0.5, care_limit: int = 2) -> None:
        from orchestrator.precheck import CoveragePrecheck

        self.profiles = profiles
        # 화제어, 동의어 판정 재사용
        self.cov = CoveragePrecheck(profiles, threshold=topic_threshold)
        self.topic_threshold = topic_threshold
        self.care_limit = care_limit

    # 훅 (GuardV11 재정의) --------------------------------------------
    def question_text(self, turn: TurnInput) -> str:
        return strip_preamble(turn.message)

    def aliases(self, term: str) -> tuple[str, ...]:
        return TOPIC_ALIASES.get(term, ())

    def extra_ground(self, turn: TurnInput) -> str:
        return ""

    def ask_back_topic(self, turn: TurnInput, state: SessionState, leak_terms=()) -> str:
        """되묻기 문구용 질문 화제어 (명사만, 이름, 가벼운 말, leak_terms 제외, 최대 2개, 민감한 가족 말이면 빈 문자열)"""
        from orchestrator.precheck import SAFETY
        from orchestrator.text import GENERIC_STOP, nouns

        # 순환 import 회피
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
        except Exception:  # noqa: BLE001 (화제어 추출 실패로 가드 중단 방지)
            return ""
        return " ".join(list(dict.fromkeys(terms))[:2])

    def has_topic(self, turn: TurnInput, state: SessionState) -> bool:
        """안부, 인사, 마음 말만 있는 질문이 아닌지 (원래 화제어 없던 질문도 True)"""
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
        # 인용 기억 전부(+프로필)를 한 덩어리로 판정 (여러 기억을 묶어 답하는 게 정상)
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
            """되묻기 문구 (쓸 때만 화제어 추출, 사망 대상자는 화제어 없이 잊은 말 여부 재확인)"""
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
        # 기억 0개면 [근거: 없음], 없는 id 도 맞는 헤더 -> R1a 대신 R1b 만 (세션 19)
        no_memories = not labeled(selection)

        def done(text: str, action: str) -> GuardResult:
            # 사망 대상자 잊은 듯 말하기 금지 (잠금 2): 모든 반환 경로(가림 답, 위기 턴 포함)에서 거름
            if deceased:
                kept = drop_forgetting(text, "")
                if kept != text:
                    if kept:
                        reasons.append("forgot_drop")
                        text, action = kept, ("trim" if action == "pass" else action)
                    elif "safety_skip" in reasons:  # 위기, 죽음 대답은 되묻기로 교체 안 함
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

        # 위기(자해 생각), 그분의 죽음을 묻는 말이나 위기 상담 번호가 든 대답: 지어내기 검사, 문장 삭제 생략
        # (위기 대답의 되묻기 교체, 상담 번호 문장 삭제 방지, 누출 검사는 위에서 완료)
        if is_safety_turn(turn.message) or CRISIS_LINE.search(body):
            reasons.append("safety_skip")
            return done(body, "trim" if "leak" in reasons else "pass")

        # 사망 대상자: 잊은 듯 말한 문장 먼저 삭제 (DONT_KNOW 에 걸려 지어내기 검사를 피하지 않게)
        if deceased:
            kept = drop_forgetting(body, "")
            if kept != body:
                reasons.append("forgot_drop")
                if not kept:
                    return done(ask_back(), "ask_back")
                body = kept

        # 2. 지어내기 (질문일 때만, 사망 대상자는 이야기를 청하는 되묻기 TELL_ME 일 때만 생략)
        msg = self.question_text(turn)
        if msg and is_question(msg) and not (TELL_ME if deceased else DONT_KNOW).search(body):
            instructions, _ = redact_instructions(turn.persona_instructions, restricted)
            profile = profile_section(instructions)
            # 안부, 인사, 마음만 있는 질문("편안하게 잘 계시죠?")은 근거 기억 없음이 정상
            # -> R1a, R1t 로 교체하지 않고 숫자, 고유명사 지어내기(R1b)만 확인
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
            if relational:  # 인용 없어도 넣은 기억 전부 + 말하는 사람 이름을 근거로
                ground = labeled(selection)
                speakers = " ".join(n for n in (state.speaker, turn.speaker_hint) if n)
            else:
                ground = cited if cited is not None else labeled(selection)
                speakers = ""
            # 가족이 앞 대화에서 직접 말한 것도 근거 (들은 말 되받기는 지어내기 아님)
            # 페르소나의 앞선 말은 제외 (그때 지어낸 말이 근거가 되지 않게)
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

        # 3, 4. 문장 단위 삭제 (애정은 되받기나 마무리 인사일 때 세션당 1번, 챙김은 care_limit 번까지)
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
        # 다 지워지면 첫 문장만이라도 유지 (빈 답 방지), 카운터 미증가
        if not kept:
            reasons.append("all_dropped_keep_first")
            kept = sents[:1]
        text = " ".join(kept) if len(kept) < len(sents) else body
        trimmed = len(kept) < len(sents) or "leak" in reasons or "forgot_drop" in reasons
        return done(text, "trim" if trimmed else "pass")


class GuardV11(GuardV1):
    """v1.1 (ORCHESTRATOR_GUARD=v11): 발화 분해 단서, v1.1 화제어와 동의어, 가족 이름 호격 허용으로 v1 오차단 감소"""

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
