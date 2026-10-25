"""발화 분해(orchestrator/split.py) + 사전 판정·가드 v1.1 의 분해 단서 사용. 가짜 가족 데이터."""
from orchestrator.contracts import (
    Generation,
    MemoryItem,
    MemorySelection,
    PersonaProfile,
    SessionState,
    TurnInput,
)
from orchestrator.guard import GuardV11
from orchestrator.precheck import CoveragePrecheckV11
from orchestrator.split import Splitter, lexicon_from_intake_json, lexicon_from_terms

INTAKE = {"subjectContext": {"familyMembers": [
    {"name": "김민수", "relationToSubject": "손자", "addressTerms": ["할머니"]},
    {"name": "김지호", "relationToSubject": "아들", "addressTerms": ["엄마"]},
    {"name": "며느리", "relationToSubject": "며느리", "addressTerms": ["어머니"]}]}}


class Profiles:
    def __init__(self, prof):
        self.prof = prof

    def profile_for(self, turn):
        return self.prof


def _prof():
    lex = lexicon_from_intake_json(INTAKE)
    return PersonaProfile(subject_name="박순자", address_terms=["할머니", "엄마", "어머니"], family_names=lex.names)


def test_split_conservative():
    sp = Splitter(lexicon_from_terms(["할머니", "엄마"], ["김민수", "김지호"]))
    assert sp.split("할머니 나 민수야. 김치 어떻게 담가?").cue == "김치 어떻게 담가?"
    assert sp.split("김치 어떻게 담가? 나 민수야, 할머니.").cue == "김치 어떻게 담가?"
    assert sp.split("할머니 고향이 어디였지?").cue == "할머니 고향이 어디였지?"  # 화제일 수 있어 남김
    assert sp.split("민수 고등학교 어디 나왔지?").cue == "민수 고등학교 어디 나왔지?"  # 이름이 화제면 안 뗌
    assert sp.split("할머니 잘 지냈어?").cue == ""
    assert sp.split("나 민수야").speaker == "민수"


def test_precheck_v11_skips_greeting_and_uses_cue():
    pre = CoveragePrecheckV11(Profiles(_prof()))
    sel = MemorySelection(memories=[MemoryItem(id="m", text="김치는 11월에 담근다.")], strategy="t")
    st = SessionState(session_id=None, subject_id="s")
    assert pre.check(TurnInput(subject_id="s", message="엄마 나 지호야. 별일 없지?", persona_instructions=""), st, sel).known is None
    r = pre.check(TurnInput(subject_id="s", message="할머니 잘 지냈어? 나 민수야. 김치는 언제 담가?", persona_instructions=""), st, sel)
    assert r.known is True


def test_guard_v11_accepts_vocative_name_and_skips_greeting():
    g = GuardV11(Profiles(_prof()))
    sel = MemorySelection(memories=[MemoryItem(id="a", text="김치는 11월에 담근다.")], strategy="t")
    st = SessionState(session_id=None, subject_id="s")
    turn = TurnInput(subject_id="s", message="할머니, 김치는 언제 담가?", persona_instructions="")
    out = g.apply(Generation(text="[근거: m0] 민수야, 김치는 11월에 담그지.", provider="t", model="t"), turn, st, sel)
    assert out.action == "pass", out.reasons
    greet = TurnInput(subject_id="s", message="어머니 저예요. 식사하셨어요?", persona_instructions="")
    out2 = g.apply(Generation(text="[근거: 없음] 그래, 먹었다.", provider="t", model="t"), greet, st, sel)
    assert out2.action == "pass", out2.reasons
