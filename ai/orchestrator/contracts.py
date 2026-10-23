"""중간계층 단계별 입출력 계약.

규칙
- 단계는 아래 Protocol 하나를 구현하고, 정해진 계약 모델만 주고받는다.
- 단계는 서로를 직접 부르지 않는다. 순서와 연결은 pipeline.PersonaOrchestrator 만 안다.
- 모든 구현은 `impl` 이름을 가진다(추적 로그·실험 조건 이름으로 씀).
- 계약 필드를 바꾸면 CONTRACT.md 와 test/orchestrator 를 같이 고친다.
"""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from pipeline.persona_prompt.spec import PersonaSpec


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 공통 데이터

class Turn(Contract):
    role: Literal["user", "assistant"]
    content: str
    # user 턴이면 누가 말했나(화자 프로필 이름·id). 모르면 None.
    speaker: str | None = None


MemoryType = Literal["LONG_TERM", "SHORT_TERM", "DELTA"]


class MemoryItem(Contract):
    id: str
    text: str
    title: str = ""
    memory_type: MemoryType = "LONG_TERM"
    # 운영 DB status(ACTIVE …) 또는 active/conflict/retracted
    status: str = "active"
    tags: list[str] = Field(default_factory=list)
    # ADD-only 필드. DB 에 없으면 None.
    source: str | None = None
    told_by: str | None = None
    relation: str | None = None
    conflict_group: str | None = None


class StyleFix(Contract):
    """가족이 고쳐 준 말투 한 쌍(BE styleExamples). 조립기가 '말투를 이렇게 고쳐 달라고 했어요' 절로 넣는다."""
    original: str
    corrected: str


class TurnInput(Contract):
    """BE 요청을 중간계층 형식으로 옮긴 것. 한 턴의 입력 전부."""
    subject_id: str
    message: str
    persona_instructions: str
    history: list[Turn] = Field(default_factory=list)
    # BE 가 검색해 보낸 기억(지금은 top-5). 선택기가 쓸지 말지 정한다.
    be_memories: list[MemoryItem] = Field(default_factory=list)
    session_id: str | None = None
    speaker_hint: str | None = None
    voice_id: str | None = None
    # 고인 페르소나인가(BE persona.deceased). 음성 감정 안전선(모르면(None) 고인으로 본다)과, 등록부가 없을 때
    # 공통 규칙 변형(사후·사망 정책, PersonaRegistry.profile_for: 값이 있으면 지시문 추정보다 우선)에 쓴다.
    deceased: bool | None = None
    # 기억 빈틈 표시를 받을까(BE flagGaps). 켜면 조립기가 빈틈 표시 지침을 넣고, 결과에 memory_gap 을 싣는다.
    flag_gaps: bool = False
    # 가족이 고쳐 준 말투(BE styleExamples, 최근 10개)
    style_examples: list[StyleFix] = Field(default_factory=list)
    # 만 19세 미만 살아 계신 대화 상대(BE persona.minor, 고인이면 False). 조립기가 미성년 규칙을 넣고,
    # 입력 검사(service)·대답 검사(MinorGuard)가 켜진다.
    minor: bool = False


class Replacement(Contract):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: str = Field(alias="from")
    to: str = ""


class RestrictedFacts(Contract):
    """제한 정보(사인·임종 경위 등). 설명: orchestrator/restricted.py"""
    # deny | brief | ask_only | open. intake(M9-01·T1-02)에 답이 있으면 그 값으로 덮인다(orchestrator/intake_policy.py).
    policy: Literal["deny", "brief", "ask_only", "open"] = "deny"
    brief_answer: str | None = None
    policy_source: str | None = None  # "restricted.json" | "M9-01" | "T1-02"
    policy_note: str | None = None  # M9-01 단서(설문 작성자가 적은 말)
    facts: dict = Field(default_factory=dict)
    replace: list[Replacement] = Field(default_factory=list)
    memory_ids: list[str] = Field(default_factory=list)
    leak_terms: list[str] = Field(default_factory=list)
    note: str | None = None


class Shot(Contract):
    """말투 few-shot 한 쌍(실제 통화). user = 가족 말, persona = 대상자 말."""
    user: str
    persona: str


class PersonaProfile(Contract):
    """페르소나 메타. 공통 규칙 변형·말투 재료를 정한다(조립기가 씀).

    source: registry(prompts/personas 에 등록) | inferred(지시문에서 추정) | default
    """
    subject_name: str | None = None
    public: bool = False
    deceased: bool = False
    has_family: bool = True
    source: str = "default"
    # few-shot 머리말에 쓸 대상자 호칭(기본 subject_name)
    self_label: str | None = None
    style_card: str | None = None
    shots: list[Shot] = Field(default_factory=list)
    # 제한 정보(사인 등). 없으면 None.
    restricted: RestrictedFacts | None = None
    # 발화 분해 사전(intake.json familyMembers 에서): 가족이 그분을 부르는 말 / 가족 이름
    address_terms: list[str] = Field(default_factory=list)
    family_names: list[str] = Field(default_factory=list)
    # 등록부의 spec.json(페르소나 명세). 있으면 런타임 절(지금 상대·이미 한 말)을 가족 표 기준으로 만든다.
    persona_spec: PersonaSpec | None = None


# ---------------------------------------------------------------- 단계 출력

class SessionState(Contract):
    session_id: str | None
    subject_id: str
    turn_index: int = 0
    speaker: str | None = None
    recent_turns: list[Turn] = Field(default_factory=list)
    # 챙김·애정 표현 등 세션 카운터(가드 R3·챙김 제한이 씀)
    counters: dict[str, int] = Field(default_factory=dict)
    # 이번 세션에서 가족이 알려 준 새 기억(델타 블록, ADD-only)
    delta_memories: list[MemoryItem] = Field(default_factory=list)


class MemorySelection(Contract):
    memories: list[MemoryItem]
    delta: list[MemoryItem] = Field(default_factory=list)
    strategy: str


class PrecheckResult(Contract):
    # None = 판정하지 않음. False = DB 에 근거 없음 → directive 로 "모른다·되묻기" 지시
    known: bool | None = None
    coverage: float | None = None
    topic_terms: list[str] = Field(default_factory=list)
    directive: str | None = None


class PromptSection(Contract):
    name: str
    role: Literal["system", "user", "assistant"]
    content: str


class PromptBundle(Contract):
    sections: list[PromptSection]

    def messages(self) -> list[dict[str, str]]:
        return [{"role": s.role, "content": s.content} for s in self.sections]


class Generation(Contract):
    text: str
    provider: str
    model: str
    usage: dict[str, int] = Field(default_factory=dict)
    latency_s: float | None = None
    ttft_s: float | None = None


class GuardResult(Contract):
    text: str
    action: Literal["pass", "trim", "ask_back", "redact"] = "pass"
    reasons: list[str] = Field(default_factory=list)
    # 근거 헤더로 확인한 인용 기억 id. None = 헤더 없음(인용 정보 모름)
    cited_memory_ids: list[str] | None = None
    # 세션 카운터 증가분(예: {"affection": 1})
    counter_delta: dict[str, int] = Field(default_factory=dict)


class StageTrace(Contract):
    stage: str
    impl: str
    ms: float
    summary: dict = Field(default_factory=dict)


class TurnResult(Contract):
    content: str
    retrieved_memory_ids: list[str]
    provider: str
    model: str
    # 대답 음성 표시(FISH_EMOTION, orchestrator/voice.py). off 면 둘 다 None.
    emotion: str | None = None
    speech_text: str | None = None
    # 기억 빈틈 주제(turn.flag_gaps 일 때만). 대답 끝 [기억없음:주제] 표시, 없으면 사전 판정의 '기억에 없다' 화제어.
    # 주제를 모르면 빈 글, 빈틈이 없으면 None.
    memory_gap: str | None = None
    trace: list[StageTrace] = Field(default_factory=list)


# ---------------------------------------------------------------- 단계 Protocol

class SessionStore(Protocol):
    impl: str

    def load(self, turn: TurnInput) -> SessionState: ...

    def save(self, state: SessionState) -> None: ...


class MemoryStore(Protocol):
    """대상자 기억 원본. BE 요청·파일·DB 어디서 읽든 같은 모양으로 준다."""
    impl: str

    def list_for_subject(self, turn: TurnInput) -> list[MemoryItem]: ...


class MemorySelector(Protocol):
    impl: str

    def select(self, turn: TurnInput, state: SessionState, store: MemoryStore) -> MemorySelection: ...


class Precheck(Protocol):
    impl: str

    def check(self, turn: TurnInput, state: SessionState, selection: MemorySelection) -> PrecheckResult: ...


class PersonaProfileStore(Protocol):
    impl: str

    def profile_for(self, turn: TurnInput) -> PersonaProfile: ...


class Assembler(Protocol):
    impl: str

    def assemble(
        self, turn: TurnInput, state: SessionState, selection: MemorySelection, precheck: PrecheckResult
    ) -> PromptBundle: ...


class LLM(Protocol):
    impl: str

    def generate(self, messages: list[dict[str, str]]) -> Generation: ...


class Guard(Protocol):
    impl: str

    def apply(
        self, generation: Generation, turn: TurnInput, state: SessionState, selection: MemorySelection
    ) -> GuardResult: ...


class PostTurn(Protocol):
    """턴 뒤 처리. 응답 전에 도는 부분은 빨라야 한다(세션 갱신 정도). 무거운 추출은 비동기로."""
    impl: str

    def after(self, turn: TurnInput, state: SessionState, guard: GuardResult) -> SessionState: ...
