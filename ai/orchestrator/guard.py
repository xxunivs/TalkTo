"""⑥ 사후 가드. LLM 을 다시 부르지 않는다(코드 판정만).

PassGuard: 아무것도 안 함(기본).
GuardV1(ORCHESTRATOR_GUARD=v1) — 규칙은 서로 독립인 순수 함수, 아래 순서로 적용한다.
  0. 헤더   첫 줄 "[근거: m3,m10]"/"[근거: 없음]" 을 떼고(TTS 로 새지 않게) 인용 기억을 찾는다.
            id 는 m{0부터 순번}, 선택된 기억 → 델타 순(header_block 참고).
  1. 누출   restricted.leak_terms 가 든 문장을 지운다. 다 지워지면 brief_answer(있으면) 또는 되묻기.
  2. 지어내기(질문일 때만, 답이 이미 '모른다·되묻기'면 통과)  ※ 화제 임계 0.5 는 사전 판정(0.6)과 따로 실측으로 정한 값
            R1a 헤더가 [근거: 없음] / 없는 id → 되묻기
            R1t 인용 기억(+프로필)이 질문 화제어를 못 덮음(커버율 < 임계) → 되묻기   ← 엉뚱한 id 를 인용하며 지어내는 경우
            R1b 답의 숫자·고유명사가 근거(인용 기억, 헤더 없으면 넣은 기억 전부)+질문+페르소나 지시문에 없음 → 되묻기
  3. 애정   '사랑' 문장은 가족이 먼저 사랑을 말했거나(되받기) 마무리 인사일 때만, 세션당 1번(실제 통화도 마무리에 한 번 정도).
            그 밖엔 문장 삭제.
  4. 챙김   밥·건강 챙김 문장은 세션당 care_limit 번까지(모델은 절반 넘는 턴에 넣는다). 넘으면 그 문장 삭제(다른 문장이 남을 때만).
되묻기 문구는 같은 세션에서 연속이면 짧은 '모름'으로 바꾼다(취조처럼 들리지 않게).
GuardV11(ORCHESTRATOR_GUARD=v11): v1 의 오차단을 줄인 판정(아래 클래스 설명).
"""
from __future__ import annotations

import re

from orchestrator.contracts import Generation, GuardResult, MemoryItem, MemorySelection, SessionState, TurnInput
from orchestrator.persona import subject_name_from
from orchestrator.precheck import is_question, profile_section, strip_preamble
from orchestrator.restricted import redact_instructions


class PassGuard:
    impl = "pass_v0"

    def apply(self, generation: Generation, turn: TurnInput, state: SessionState,
              selection: MemorySelection) -> GuardResult:
        return GuardResult(text=generation.text)


# ---------------------------------------------------------------- 헤더 (조립기와 같이 씀)

HEADER_RULE = ("# 답변 형식 (꼭 지킬 것)\n첫 줄에 이 답의 근거가 된 참고 기억 id 를 \"[근거: m12,m40]\" 처럼 쓴다. "
               "참고 기억에 근거가 없으면 \"[근거: 없음]\" 이라고 쓴다. 둘째 줄부터 평소처럼 답한다. 근거가 없으면 지어내지 말고 되물어라.")
HEADER = re.compile(r"^\s*\[근거:\s*([^\]]*)\]\s*")


def labeled(selection: MemorySelection) -> list[MemoryItem]:
    return list(selection.memories) + list(selection.delta)


def header_block(selection: MemorySelection) -> str:
    """줄마다 (m번호) id 를 붙인 참고 기억 블록. 번호는 parse_header 와 같다."""
    items = labeled(selection)
    if not items:
        return ""
    lines = [f"- (m{i}) " + (f"[{m.title}] " if m.title else "") + m.text.strip() for i, m in enumerate(items)]
    return ("참고 기억(각 줄 앞 (m번호)가 기억 id. 대화 화제가 닿으면 구체 사실을 그대로 녹이고, 없는 내용은 지어내지 않는다):\n"
            + "\n".join(lines))


def parse_header(text: str, selection: MemorySelection) -> tuple[str, list[MemoryItem] | None, bool]:
    """(본문, 인용 기억(None=헤더 없음), 잘못된 id 있음)."""
    m = HEADER.match(text)
    if not m:
        return text, None, False
    body = text[m.end():].strip()
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

DONT_KNOW = re.compile(r"(몰라|모르겠|모른다|기억이\s?(잘\s?)?안|생각이\s?(잘\s?)?안\s?나|가물가물|글쎄|잊어버|까먹|얘기\s?(좀\s?)?해\s?줄래|말해\s?줄래)")
AFFECTION = re.compile(r"사랑")
USER_LOVE = re.compile(r"사랑|보고\s?싶")
CLOSING = re.compile(r"(끊(어|을게|는다|자|을께)|들어가(세요|요|라|볼게)|잘\s?(자|있어|지내)|또\s?(전화|연락)|안녕히|다음에\s?(또|전화)|이만)")
CARE = re.compile(r"(밥|끼니|챙겨\s?먹|건강\s?(잘\s?)?챙|건강이\s?제일|몸\s?(조심|건강|챙|잘\s?돌)|아프지\s?마|감기\s?조심|푹\s?쉬|잘\s?먹어|무리하지\s?마)")
NUM = re.compile(r"\d+")
SENT = re.compile(r"(?<=[.!?…~])\s+|\n+")
ASK_BACK = {
    "family": ("글쎄, 그건 기억이 잘 안 나네. 네가 얘기 좀 해 줄래?", "그건 잘 모르겠다."),
    "public": ("그건 제가 잘 모르겠네요. 어떤 이야기인지 조금 더 말씀해 주실래요?", "그 부분은 제가 잘 모르겠어요."),
}


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT.split(text) if s and s.strip()] or [text]


# 화제 확인(R1t)에서 빼는 말: 가벼운 동사·빈도어(질문 화제가 아님). 오차단 사례를 보고 정했다.
TOPIC_LIGHT = frozenset({"하", "있", "되", "어떻", "가", "오", "보", "알", "말하", "그렇", "이렇", "기억나", "맨날", "자주", "늘"})
# 가족 호칭 ↔ 기억에 쓰인 말(가드 전용, 사전 판정 사전은 그대로)
TOPIC_ALIASES = {"아빠": ("아버지",), "엄마": ("어머니",)}


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
        self.cov = CoveragePrecheck(profiles, threshold=topic_threshold)  # 화제어·동의어 판정 재사용
        self.topic_threshold = topic_threshold
        self.care_limit = care_limit

    # 훅(GuardV11 이 바꾼다) --------------------------------------------
    def question_text(self, turn: TurnInput) -> str:
        return strip_preamble(turn.message)

    def aliases(self, term: str) -> tuple[str, ...]:
        return TOPIC_ALIASES.get(term, ())

    def extra_ground(self, turn: TurnInput) -> str:
        return ""

    def topic_covered(self, turn: TurnInput, state: SessionState, cited: list[MemoryItem], profile: str) -> bool | None:
        body = self.question_text(turn)
        names = {n for n in (subject_name_from(turn.persona_instructions), state.speaker, turn.speaker_hint) if n}
        terms = [t for t in self.cov.topic_terms(body, names) if t not in TOPIC_LIGHT]
        if not terms:
            return None
        # 인용 기억 전부(+프로필)를 한 덩어리로 본다 — 여러 기억을 묶어 답하는 게 정상이라서
        raw = " ".join((m.title + " " if m.title else "") + m.text for m in cited) + (" " + profile if profile else "")
        doc = self.cov._morph_set(raw)

        def hit(t: str) -> bool:
            if self.cov._covered(t, doc):
                return True
            if len(t) >= 2 and t in raw:  # 합성어 속(병원 ⊂ 서울대병원)
                return True
            return any(a in raw for a in self.aliases(t))
        return sum(hit(t) for t in terms) / len(terms) >= self.topic_threshold

    def apply(self, generation: Generation, turn: TurnInput, state: SessionState,
              selection: MemorySelection) -> GuardResult:
        prof = self.profiles.profile_for(turn)
        restricted = prof.restricted
        kind = "public" if prof.public else "family"
        streak = state.counters.get("ask_back_streak", 0)
        ask_back = ASK_BACK[kind][1 if streak >= 1 else 0]
        reasons: list[str] = []
        delta: dict[str, int] = {}

        body, cited, bad_id = parse_header(generation.text, selection)
        cited_ids = None if cited is None else [m.id for m in cited]

        def done(text: str, action: str) -> GuardResult:
            if action == "ask_back":
                delta["ask_back_streak"] = 1
            elif streak:
                delta["ask_back_streak"] = -streak
            return GuardResult(text=text, action=action, reasons=reasons, cited_memory_ids=cited_ids,
                               counter_delta=delta)

        # 1. 누출
        if restricted and restricted.leak_terms:
            sents = sentences(body)
            kept = [s for s in sents if not any(t in s for t in restricted.leak_terms)]
            if len(kept) < len(sents):
                reasons.append("leak")
                if not kept:
                    return done(restricted.brief_answer or ask_back, "redact")
                body = " ".join(kept)

        # 2. 지어내기 (질문일 때만)
        msg = self.question_text(turn)
        if msg and is_question(msg) and not DONT_KNOW.search(body):
            instructions, _ = redact_instructions(turn.persona_instructions, restricted)
            profile = profile_section(instructions)
            if cited is not None and (bad_id or not cited):
                reasons.append("R1a_bad_id" if bad_id else "R1a_no_ground")
                return done(ask_back, "ask_back")
            if cited:
                covered = self.topic_covered(turn, state, cited, profile)
                if covered is False:
                    reasons.append("R1t_topic_not_in_cited")
                    return done(ask_back, "ask_back")
            ground = cited if cited is not None else labeled(selection)
            base = (" ".join(f"{m.title} {m.text}" for m in ground) + " " + turn.message + " " + instructions
                    + " " + self.extra_ground(turn))
            bad = unsupported_tokens(body, base)
            if bad:
                reasons.append("R1b_unsupported:" + ",".join(bad))
                return done(ask_back, "ask_back")

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
        if not kept:  # 다 지워지면 원래 답의 첫 문장만이라도(빈 답보다 낫다), 카운터는 올리지 않는다
            reasons.append("all_dropped_keep_first")
            kept = sents[:1]
        text = " ".join(kept) if len(kept) < len(sents) else body
        trimmed = len(kept) < len(sents) or "leak" in reasons
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
