"""③ 사전 판정.

NoopPrecheck: 판정 안 함(기본).
CoveragePrecheck(ORCHESTRATOR_PRECHECK=coverage): 질문이면 화제어(형태소)가 기억 문서 하나에 얼마나 모여 있는지
  (최대 커버율)를 보고, 임계 미만이면 "모른다·되묻기" 지시를 넣는다. 임계 0.6 은 실측으로 정한 값.
  - 문서 = 선택된 기억 + 델타 기억 + 페르소나 지시문의 '기본 정보' 절(m-profile)
  - 동의어 사전(prompts/topic_synonyms.json): 화제어 대신 기억에 쓰인 말도 맞은 것으로 친다(고향↔태어났다)
  - 질문이 아니면(근황 알림·잡담) 판정하지 않는다 — 가족이 새 소식을 말할 때 "모른다"가 붙으면 안 된다
  - 화제어가 없으면(인사 등) 판정하지 않는다
  - 기억이 하나도 없거나 min_memories(ORCHESTRATOR_PRECHECK_MIN_MEMORIES, 30)보다 적으면 판정하지 않는다
    (기억 수는 BE memoryTotal, 없으면 받은 수로 짐작 — pipeline/persona_prompt/low_memory.memory_count)
CoveragePrecheckV11(coverage_v11): v1 의 오판 원인을 고친 판정(아래 클래스 설명).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from orchestrator.contracts import MemoryItem, MemorySelection, PrecheckResult, SessionState, TurnInput
from orchestrator.persona import subject_name_from
from orchestrator.restricted import redact_instructions
from orchestrator.text import listener_speech_level

SYNONYMS_PATH = Path(__file__).resolve().parents[1] / "prompts" / "topic_synonyms.json"

_QUESTION_WORDS = re.compile(r"(뭐|뭘|무슨|무엇|어디|언제|누구|누가|몇|어떻게|어떡|어땠|왜|얼마|어느|기억\s?(나|해|하))")
_QUESTION_END = re.compile(r"(니|냐|나요|까|까요|죠|지요|어요|아요|예요|에요|래|대|지|나)\s*[?？]?\s*$")
# 앞머리 호칭("할머니,")과 자기소개("나 도윤이야.")는 화제가 아니다
_VOCATIVE = re.compile(r"^\s*(할머니|할머이|할매|할아버지|엄마|어머니|어머님|아빠|아버지|아버님|여보|당신)\s*[,.!~]*\s*")
_SELF_INTRO = re.compile(r"^\s*나\s*[,]?\s*[가-힣]{1,4}?(이야|야|이에요|예요|에요|이)\s*[.!,~]+\s*")
_PROFILE_HEAD = re.compile(r"^#+[^\n]*기본\s*정보[^\n]*\n(.*?)(?=^#|\Z)", re.M | re.S)
# 기억을 묻는 말이 아닌 관계 말: 그분 안부("편안하게 잘 계시죠", "거기는 어때"), 인사, 마음("보고 싶어요"),
# 자기 근황("저 잘 지내요"). is_relational() 이 '이 말만 있는 질문'을 가려 사전 판정·가드 R1a·R1t 를 건너뛴다.
# 화제가 남는 질문("잘 계시죠? 근데 제주도 갔던 거 기억나?")은 떼기 전 원래 화제어로 전과 똑같이 판정한다.
# (2026-10-17 실사용: 고인 안부에 "글쎄, 그건 기억이 잘 안 나네"가 나가던 문제)
# 반복은 {0,6}·{1,3} 으로 묶는다 — 무제한 \S*·+ 를 겹치면 "잘잘잘…?" 같은 입력에서 지수 시간이 걸린다.
_RELATIONAL = re.compile(
    r"(?:(?:편안|편히|평안|건강|무탈|잘|괜찮)\S{0,6}?\s*){1,3}"
    r"(?:계시|계세|계셔|계십|지내|지냈|있|사시|사세|사셔|하시|하세|하셔)\S{0,6}(?:\s+(?:있|계시|계세|계셔)\S{0,6})?"
    r"|어떻게\s*(?:지내|계시|계세|계셔|사시|사세)\S{0,6}"
    r"|(?:거기|그곳|그쪽|하늘나라|하늘|저세상|저승)\S{0,3}\s*(?:생활\S{0,3}\s*)?"
    r"(?:어때|어떠세|어떠니|어떻니|괜찮아|괜찮으|괜찮니|괜찮지|편안해|편안하|편해|좋아|좋으|좋니|좋지|좋냐)\S{0,4}"
    r"|어디\s*(?:아프|아픈|아프신)\S{0,6}(?:\s*(?:데|곳)\S{0,3}\s*(?:없|는|은)\S{0,4})?"
    r"|아프(?:신)?\s*(?:데|곳)\S{0,3}\s*(?:없|는|은)\S{0,4}|안\s*아프\S{0,6}|아프지\s*않\S{0,6}"
    r"|보고\s*싶\S{0,6}|그리워(?:요|라|서|하지|하지마)?|그립(?:다|네|구나|지|고)\S{0,3}"
    r"|사랑(?:해|합|한다|하고)\S{0,4}|고마워\S{0,3}|고맙(?:다|습|네|구나|고)\S{0,4}"
    r"|감사(?:합|해|하|드)\S{0,6}|미안(?:해|하|합)\S{0,6}|죄송(?:해|하|합)\S{0,6}"
    r"|안녕(?:하|히)\S{0,6}|안녕(?=$|[\s.,!?~…])|여보세요\S{0,3}|오랜만\S{0,6}|반가워\S{0,3}|반갑\S{0,6}"
    r"|별일\s*없\S{0,6}|밥\s*(?:은|잘|좀)?\s*(?:먹었|드셨|먹)\S{0,6}|식사\s*(?:하셨|했|하)\S{0,6}")


# 위기(자해·자살 생각)와 그분의 죽음·임종을 묻는 말. 기억을 묻는 질문이 아니다.
# '-어요'로 끝나 질문으로 잡히면 사전 판정이 "기억에 없다, 되물어라"를 넣고 가드가 위기 대답(109·119 안내 포함)을
# "글쎄, 그건 기억이 잘 안 나네. 네가 얘기 좀 해 줄래?"로 갈아 끼웠다(고인에게 죽음 경위를 되묻는 셈).
# 이런 말은 사전 판정·가드의 지어내기 검사(R1)를 건너뛰고 공통 규칙(위기 대응·사망 정책)을 따른다.
SAFETY = re.compile(
    r"죽고\s?싶|죽을\s?것\s?같|죽어\s?버리|사라지고\s?싶|없어지고\s?싶|살기\s?싫|살고\s?싶지\s?않"
    r"|자살|자해|목숨|돌아가셨|돌아가신|임종|장례|세상을\s?떠|마지막\s?(?:순간|모습)")


def is_safety_turn(text: str) -> bool:
    return bool(SAFETY.search(text or ""))


def _keep_if_adnominal(m: re.Match) -> str:
    """'잘 사시던 데', '잘하시던 거'처럼 '-던' 관형형은 화제를 꾸미는 말이라 떼지 않는다."""
    return m.group(0) if "던" in m.group(0) else " "


def strip_relational(text: str) -> str:
    """관계 말(안부·인사·마음·자기 근황)을 지운 나머지. 화제어 판단에만 쓴다(LLM 에 가는 원문은 그대로)."""
    return re.sub(r"\s+", " ", _RELATIONAL.sub(_keep_if_adnominal, text)).strip()


def is_relational(text: str, topic_terms) -> bool:
    """안부·인사·마음 말'만' 있는 질문인가. topic_terms(text) -> list 는 호출 쪽 판정의 화제어 함수.
    - 뗄 관계 말이 하나도 없던 질문("누구였어?")은 해당 없음(전과 같은 경로로 간다).
    - 떼고 남은 말에 의문사(뭐·어디·누구·언제·몇·기억나…)가 있으면 기억을 묻는 질문으로 본다
      ('데·거·분' 같은 의존명사는 화제어로 안 잡혀서 "고마운 분 누구였어?"가 비어 보이는 것을 막는다)."""
    rest = strip_relational(text)
    if rest == re.sub(r"\s+", " ", text).strip():
        return False
    if _QUESTION_WORDS.search(rest):
        return False
    return not topic_terms(rest)


class NoopPrecheck:
    impl = "noop_v0"

    def check(self, turn: TurnInput, state: SessionState, selection: MemorySelection) -> PrecheckResult:
        return PrecheckResult()


def is_question(text: str) -> bool:
    t = text.strip()
    if "?" in t or "？" in t:
        return True
    last = re.split(r"[.!\n]", t.rstrip(".! \n"))[-1]
    return bool(_QUESTION_WORDS.search(last) or _QUESTION_END.search(last))


def strip_preamble(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = _VOCATIVE.sub("", text, count=1)
        text = _SELF_INTRO.sub("", text, count=1)
    return text.strip()


def profile_section(instructions: str) -> str:
    m = _PROFILE_HEAD.search(instructions)
    return m.group(1).strip() if m else ""


def load_synonyms(path: Path = SYNONYMS_PATH) -> dict[str, tuple[str, ...]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: tuple(v) for k, v in raw.items() if not k.startswith("_")}


def _directive(terms: list[str], public: bool, polite: bool = False, deceased: bool = False) -> str:
    """polite: 가족 표의 '지금 대화하는 사람' 줄이 존댓말(orchestrator.text.listener_speech_level).

    deceased: 고인 페르소나는 '기억이 안 난다'·'모르겠다'고 말하지 않는다(잠금 2, 대표 지시 2026-10-21).
    지어내지 않는 것은 같고, 잊은 듯 말하는 대신 가족에게서 그 이야기를 듣고 싶어 하며 되묻는다.
    """
    topic = "·".join(terms[:4])
    if deceased and not public:
        ask = ("\"그 이야기 한번 들려주실래요? 듣고 싶네요\"처럼 존댓말로" if polite
               else "\"그 얘기 네가 한번 해 줄래? 듣고 싶구나\"처럼")
        return ("# 이번 질문에 대해 (코드 판정)\n"
                f"가족이 물은 것({topic})은 기억에 없다. 지어내지 말고, '기억이 안 난다'·'잊어버렸다'·"
                f"'모르겠다'처럼 잊은 듯 말하지도 말고, 그 이야기를 가족에게서 듣고 싶다는 마음으로 {ask} "
                "다정하게 되물어라. 장소·날짜·이름 같은 세부를 만들지 않는다.")
    if public:
        return ("# 이번 질문에 대해 (코드 판정)\n"
                f"방문자가 물은 것({topic})은 기억에 없다. 지어내지 말고 \"그건 제가 잘 모르겠네요\"처럼 "
                "모른다고 짧게 말한 뒤, 방문자에게 어떤 이야기인지 되물어라. 장소·날짜·이름 같은 세부를 만들지 않는다.")
    if polite:
        return ("# 이번 질문에 대해 (코드 판정)\n"
                f"가족이 물은 것({topic})은 기억에 없다. 지어내지 말고 \"글쎄요, 그건 기억이 잘 안 나네요\"처럼 "
                "모른다고 짧게 말한 뒤, \"이야기 좀 해 주실래요?\"처럼 존댓말로 가족에게 되물어라. "
                "장소·날짜·이름 같은 세부를 만들지 않는다.")
    return ("# 이번 질문에 대해 (코드 판정)\n"
            f"가족이 물은 것({topic})은 기억에 없다. 지어내지 말고 \"글쎄, 그건 기억이 잘 안 나네\"처럼 "
            "모른다고 짧게 말한 뒤, \"네가 얘기 좀 해 줄래?\"처럼 가족에게 되물어라. "
            "장소·날짜·이름 같은 세부를 만들지 않는다.")


class CoveragePrecheck:
    impl = "coverage_v1"

    def __init__(self, profiles=None, *, threshold: float = 0.6,
                 synonyms: dict[str, tuple[str, ...]] | None = None, min_memories: int = 0) -> None:
        from orchestrator.text import morph_set  # kiwipiepy 를 이 구현을 쓸 때만 요구
        self._morph_set = morph_set
        self.profiles = profiles
        self.threshold = threshold
        # 기억이 이 수보다 적으면 판정하지 않는다(ORCHESTRATOR_PRECHECK_MIN_MEMORIES). 기억이 몇 개뿐이면 거의 모든
        # 질문이 임계를 못 넘어 매 턴 '모른다' 지시가 붙고 대화가 닫힌다(10/18 실사용, 4층 레포 세션 19).
        self.min_memories = min_memories
        self.synonyms = load_synonyms() if synonyms is None else synonyms

    def low_memory(self, turn: TurnInput, selection: MemorySelection) -> bool:
        """기억이 min_memories 보다 적어 판정을 건너뛸 턴인가. 기억이 하나도 없으면 문턱과 상관없이 건너뛴다."""
        from pipeline.persona_prompt.low_memory import memory_count
        n_sent = sent_memories(turn, selection)
        if n_sent == 0:
            return True
        n = memory_count(n_sent, turn.memory_total)
        return n is not None and n < self.min_memories

    def topic_terms(self, text: str, extra_stop: set[str]) -> list[str]:
        from orchestrator.text import GENERIC_STOP
        stop = GENERIC_STOP | extra_stop
        return sorted({t for t in self._morph_set(text) if t not in stop})

    def _docs(self, turn: TurnInput, selection: MemorySelection) -> list[tuple[str, frozenset[str]]]:
        docs = []
        for m in list(selection.memories) + list(selection.delta):
            docs.append((m.id, self._morph_set(_doc_text(m))))
        instructions = turn.persona_instructions
        if self.profiles is not None:
            instructions, _ = redact_instructions(instructions, self.profiles.profile_for(turn).restricted)
        prof = profile_section(instructions)
        if prof:
            docs.append(("m-profile", self._morph_set(prof)))
        return docs

    def _covered(self, term: str, doc: frozenset[str]) -> bool:
        return term in doc or any(s in doc for s in self.synonyms.get(term, ()))

    def check(self, turn: TurnInput, state: SessionState, selection: MemorySelection) -> PrecheckResult:
        if is_safety_turn(turn.message):  # 위기·죽음 말에는 "모른다·되묻기"를 넣지 않는다
            return PrecheckResult()
        body = strip_preamble(turn.message)
        if not body or not is_question(body):
            return PrecheckResult()
        if self.low_memory(turn, selection):
            return PrecheckResult()
        names = {n for n in (subject_name_from(turn.persona_instructions), state.speaker, turn.speaker_hint) if n}
        terms = self.topic_terms(body, names)
        if not terms or is_relational(body, lambda t: self.topic_terms(t, names)):  # 화제어 없음·안부뿐 → 판정 안 함
            return PrecheckResult(topic_terms=[])
        best, best_id = 0.0, None
        for doc_id, doc in self._docs(turn, selection):
            cover = sum(self._covered(t, doc) for t in terms) / len(terms)
            if cover > best:
                best, best_id = cover, doc_id
        known = best >= self.threshold
        public = bool(self.profiles and self.profiles.profile_for(turn).public)
        return PrecheckResult(known=known, coverage=round(best, 3), topic_terms=terms,
                              directive=None if known else _directive(
                                  terms, public, polite=_polite(turn),
                                  deceased=_is_deceased(turn, self.profiles)))


def _is_deceased(turn: TurnInput, profiles) -> bool:
    """고인 페르소나인가. BE 가 보낸 persona.deceased 가 먼저이고, 없으면 프로필 추정."""
    if turn.deceased is not None:
        return bool(turn.deceased)
    return bool(profiles and profiles.profile_for(turn).deceased)


def _polite(turn: TurnInput) -> bool:
    """지금 대화하는 사람에게 존댓말을 쓰는가(가족 표 그 사람 줄). 반말·모름이면 예전 되묻기 그대로."""
    return listener_speech_level(turn.persona_instructions) == "존댓말"


def sent_memories(turn: TurnInput, selection: MemorySelection) -> int:
    """이번 턴에 받은 기억 수(BE 가 보낸 것과 선택된 것 중 많은 쪽 + 델타). 제한 정보로 뺀 기억 때문에
    top-5 를 다 받은 페르소나가 '기억이 적다'로 잘못 보이지 않게 BE 가 보낸 수도 본다."""
    return max(len(turn.be_memories), len(selection.memories)) + len(selection.delta)


def _doc_text(m: MemoryItem) -> str:
    return (m.title + " " if m.title else "") + m.text


# ---------------------------------------------------------------- v1.1 (ORCHESTRATOR_PRECHECK=coverage_v11)
LIGHT_V11 = frozenset({"하", "있", "되", "어떻", "가", "오", "보", "알", "말하", "그렇", "이렇", "기억나", "맨날", "자주", "늘",
                       "사람", "좀", "막", "그", "적"})
ADDRESS_V11 = frozenset({"엄마", "어머니", "어머님", "아빠", "아버지", "아버님", "여보", "할매", "할머님"})
# 기억 원문에 대신 쓰이는 말(부분 문자열로 찾는다). topic_synonyms.json(v1 보정값)은 건드리지 않으려고 따로 둔다.
SYN_V11 = {
    "나오": ("졸업", "나왔", "나온"), "대학": ("학과", "학부", "대학"), "대학교": ("학과", "학부", "대학"),
    "고등학교": ("고등", "과학고", "여고", "부고"), "잔소리": ("뭐라고", "하던 말", "자주 하던", "끊어라", "하지 마"),
    "일하": ("근무", "직장", "회사"), "일": ("근무", "직장", "회사"), "다니": ("재학", "근무", "다녔", "다니"),
}
_MERGE_TAGS = ("NNG", "NNP", "MAG", "XR")


class CoveragePrecheckV11(CoveragePrecheck):
    """v1 오판 원인을 고친 판정.
    1) 이름이 한 글자 형태소로 쪼개짐(외자·두 글자 이름이 한 글자씩 갈라지는 경우) → 붙어 있던 한 글자 조각을 한 낱말로 다시 합친다.
    2) 이름 표기 차이(정우 vs 이정우)·합성어(대학 ⊂ 한빛대학교) → 기억 원문 부분 문자열로도 맞은 것으로 친다(가드 v1 규칙과 같음).
    3) 가벼운 말(하·있·알·맨날)·가족이 그분을 부르는 말(엄마·어머니)은 화제어에서 뺀다.
    4) SYN_V11: 기억에 대신 쓰이는 말(나오↔졸업, 잔소리↔하던 말 …).
    5) 등록부에 가족 사전(intake.json)이 있으면 원문 대신 발화 분해 단서(orchestrator/split.py)로 판정한다.
       단서가 비면(인사·군말뿐) 판정하지 않는다("별일 없지?"에 '모름'이 붙지 않게).
    주의: 이 고침은 특정 평가 세트의 오판을 보고 만들었다 → 새 질문으로 다시 확인해야 한다.
    """
    impl = "coverage_v1.1"

    def __init__(self, profiles=None, *, threshold: float = 0.6, synonyms=None, extra_synonyms=None,
                 address_words=ADDRESS_V11, min_memories: int = 0) -> None:
        super().__init__(profiles, threshold=threshold, synonyms=synonyms, min_memories=min_memories)
        self.extra = SYN_V11 if extra_synonyms is None else extra_synonyms
        self.address = frozenset(address_words)
        self._splitters: dict = {}

    def question_text(self, turn: TurnInput) -> tuple[str, frozenset[str]]:
        """(판정할 질문 텍스트, 더 뺄 부르는 말). 가족 사전이 있으면 분해 단서, 없으면 앞머리 고정 제거."""
        prof = self.profiles.profile_for(turn) if self.profiles is not None else None
        if prof is not None and (prof.address_terms or prof.family_names):
            key = (tuple(prof.address_terms), tuple(prof.family_names))
            sp = self._splitters.get(key)
            if sp is None:
                from orchestrator.split import Splitter, lexicon_from_terms
                sp = self._splitters[key] = Splitter(lexicon_from_terms(prof.address_terms, prof.family_names))
            return sp.split(turn.message).cue, frozenset(sp.lex.addresses)
        return strip_preamble(turn.message), frozenset()

    def topic_terms(self, text: str, extra_stop: set[str]) -> list[str]:
        from orchestrator.text import GENERIC_STOP, KEEP_TAGS, _get_kiwi, _lock
        kiwi = _get_kiwi()
        with _lock:
            toks = kiwi.tokenize(text)
        forms: list[str] = []
        prev = None
        for t in toks:
            if not t.tag.startswith(KEEP_TAGS):
                prev = None
                continue
            single = len(t.form) == 1 and t.tag.startswith(_MERGE_TAGS)
            if single and prev is not None and prev[1] and t.start == prev[2]:
                forms[-1] += t.form
                prev = (forms[-1], True, t.start + t.len)
                continue
            forms.append(t.form)
            prev = (t.form, single, t.start + t.len)
        stop = GENERIC_STOP | LIGHT_V11 | self.address | extra_stop
        return sorted({f for f in forms if f not in stop})

    def _covered_raw(self, term: str, doc: frozenset[str], raw: str) -> bool:
        if self._covered(term, doc):
            return True
        if len(term) >= 2 and term in raw:
            return True
        return any(s in raw for s in self.extra.get(term, ()))

    def check(self, turn: TurnInput, state: SessionState, selection: MemorySelection) -> PrecheckResult:
        if is_safety_turn(turn.message):  # 위기·죽음 말에는 "모른다·되묻기"를 넣지 않는다
            return PrecheckResult()
        body, addr = self.question_text(turn)
        if not body or not is_question(body):
            return PrecheckResult()
        if self.low_memory(turn, selection):
            return PrecheckResult()
        names = {n for n in (subject_name_from(turn.persona_instructions), state.speaker, turn.speaker_hint) if n}
        terms = self.topic_terms(body, names | addr)
        if not terms or is_relational(body, lambda t: self.topic_terms(t, names | addr)):  # 화제어 없음·안부뿐 → 판정 안 함
            return PrecheckResult(topic_terms=[])
        raws = [_doc_text(m) for m in list(selection.memories) + list(selection.delta)]
        instructions = turn.persona_instructions
        if self.profiles is not None:
            instructions, _ = redact_instructions(instructions, self.profiles.profile_for(turn).restricted)
        prof = profile_section(instructions)
        if prof:
            raws.append(prof)
        best = 0.0
        for raw in raws:
            doc = self._morph_set(raw)
            best = max(best, sum(self._covered_raw(t, doc, raw) for t in terms) / len(terms))
        known = best >= self.threshold
        public = bool(self.profiles and self.profiles.profile_for(turn).public)
        return PrecheckResult(known=known, coverage=round(best, 3), topic_terms=terms,
                              directive=None if known else _directive(
                                  terms, public, polite=_polite(turn),
                                  deceased=_is_deceased(turn, self.profiles)))
