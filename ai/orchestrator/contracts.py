"""중간계층 단계별 입출력 계약 (필드 변경 시 CONTRACT.md, test/orchestrator 함께 수정)"""

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
    # user 턴 화자 (프로필 이름 또는 id, 모르면 None)
    speaker: str | None = None


MemoryType = Literal["LONG_TERM", "SHORT_TERM", "DELTA"]


class MemoryItem(Contract):
    id: str
    text: str
    title: str = ""
    memory_type: MemoryType = "LONG_TERM"
    # 운영 DB status(ACTIVE 등) 또는 active, conflict, retracted
    status: str = "active"
    tags: list[str] = Field(default_factory=list)
    # ADD-only 필드 (DB 에 없으면 None)
    source: str | None = None
    told_by: str | None = None
    relation: str | None = None
    conflict_group: str | None = None


class StyleFix(Contract):
    """가족이 고쳐 준 말투 한 쌍 (BE styleExamples)"""

    original: str
    corrected: str


class TurnInput(Contract):
    """BE 요청 -> 중간계층 한 턴 입력"""

    subject_id: str
    message: str
    persona_instructions: str
    history: list[Turn] = Field(default_factory=list)
    # BE 검색 기억 (현재 top-5, 사용 여부는 선택기 결정)
    be_memories: list[MemoryItem] = Field(default_factory=list)
    # 페르소나 총 기억 수 (BE memoryTotal, None 이면 받은 수로 추정, low_memory.memory_count)
    memory_total: int | None = None
    session_id: str | None = None
    speaker_hint: str | None = None
    voice_id: str | None = None
    # BE persona.deceased: 음성 감정 안전선(None 이면 사망으로 간주), 등록부 없을 때 공통 규칙 변형
    # (PersonaRegistry.profile_for 에서 지시문 추정보다 우선)
    deceased: bool | None = None
    # BE flagGaps: 켜면 빈틈 표시 지침 추가, 결과에 memory_gap 포함
    flag_gaps: bool = False
    # 가족이 고쳐 준 말투 (BE styleExamples, 최근 10개)
    style_examples: list[StyleFix] = Field(default_factory=list)
    # 만 19세 미만 생존 대화 상대 (BE persona.minor, 사망 대상자면 False)
    # 켜면 미성년 규칙 추가, 입력 검사(service), 대답 검사(MinorGuard) 작동
    minor: bool = False


class Replacement(Contract):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: str = Field(alias="from")
    to: str = ""


class RestrictedFacts(Contract):
    """제한 정보 (사인, 임종 경위 등, 설명: orchestrator/restricted.py)"""

    # deny | brief | ask_only | open (intake M9-01, T1-02 답이 있으면 덮어씀, orchestrator/intake_policy.py)
    policy: Literal["deny", "brief", "ask_only", "open"] = "deny"
    brief_answer: str | None = None
    policy_source: str | None = None  # "restricted.json" | "M9-01" | "T1-02"
    policy_note: str | None = None  # M9-01 단서 (설문 작성자 원문)
    facts: dict = Field(default_factory=dict)
    replace: list[Replacement] = Field(default_factory=list)
    memory_ids: list[str] = Field(default_factory=list)
    leak_terms: list[str] = Field(default_factory=list)
    note: str | None = None


class Shot(Contract):
    """말투 few-shot 한 쌍 (실제 통화, user = 가족 말, persona = 대상자 말)"""

    user: str
    persona: str


class PersonaProfile(Contract):
    """페르소나 메타: 공통 규칙 변형, 말투 재료 (source: registry | inferred | default)"""

    subject_name: str | None = None
    public: bool = False
    deceased: bool = False
    has_family: bool = True
    source: str = "default"
    # few-shot 머리말 대상자 호칭 (기본 subject_name)
    self_label: str | None = None
    style_card: str | None = None
    shots: list[Shot] = Field(default_factory=list)
    # 제한 정보 (사인 등, 없으면 None)
    restricted: RestrictedFacts | None = None
    # 발화 분해 사전 (intake.json familyMembers): 가족이 그분을 부르는 말, 가족 이름
    address_terms: list[str] = Field(default_factory=list)
    family_names: list[str] = Field(default_factory=list)
    # 등록부 spec.json (있으면 런타임 절을 가족 표 기준으로 생성)
    persona_spec: PersonaSpec | None = None


# ---------------------------------------------------------------- 단계 출력


class SessionState(Contract):
    session_id: str | None
    subject_id: str
    turn_index: int = 0
    speaker: str | None = None
    recent_turns: list[Turn] = Field(default_factory=list)
    # 챙김, 애정 표현 등 세션 카운터 (가드 R3, 챙김 제한용)
    counters: dict[str, int] = Field(default_factory=dict)
    # 이번 세션에 가족이 알려 준 새 기억 (델타 블록, ADD-only)
    delta_memories: list[MemoryItem] = Field(default_factory=list)


class MemorySelection(Contract):
    memories: list[MemoryItem]
    delta: list[MemoryItem] = Field(default_factory=list)
    strategy: str


class PrecheckResult(Contract):
    # None = 판정 안 함, False = DB 근거 없음 -> directive 로 모른다, 되묻기 지시
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
    # 근거 헤더로 확인한 인용 기억 id (None = 헤더 없음, 인용 정보 모름)
    cited_memory_ids: list[str] | None = None
    # 세션 카운터 증가분 (예: {"affection": 1})
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
    # 대답 음성 표시 (FISH_EMOTION, orchestrator/voice.py, off 면 둘 다 None)
    emotion: str | None = None
    speech_text: str | None = None
    # 기억 빈틈 주제 (turn.flag_gaps 일 때만): 대답 끝 [기억없음:주제] 표시, 없으면 사전 판정 기억 없음 화제어
    # (주제 모르면 빈 글, 빈틈 없으면 None)
    memory_gap: str | None = None
    trace: list[StageTrace] = Field(default_factory=list)


# ---------------------------------------------------------------- 단계 Protocol


class SessionStore(Protocol):
    impl: str

    def load(self, turn: TurnInput) -> SessionState: ...

    def save(self, state: SessionState) -> None: ...


class MemoryStore(Protocol):
    """대상자 기억 원본 (BE 요청, 파일, DB 공통 모양)"""

    impl: str

    def list_for_subject(self, turn: TurnInput) -> list[MemoryItem]: ...


class MemorySelector(Protocol):
    impl: str

    def select(
        self, turn: TurnInput, state: SessionState, store: MemoryStore
    ) -> MemorySelection: ...


class Precheck(Protocol):
    impl: str

    def check(
        self, turn: TurnInput, state: SessionState, selection: MemorySelection
    ) -> PrecheckResult: ...


class PersonaProfileStore(Protocol):
    impl: str

    def profile_for(self, turn: TurnInput) -> PersonaProfile: ...


class Assembler(Protocol):
    impl: str

    def assemble(
        self,
        turn: TurnInput,
        state: SessionState,
        selection: MemorySelection,
        precheck: PrecheckResult,
    ) -> PromptBundle: ...


class LLM(Protocol):
    impl: str

    def generate(self, messages: list[dict[str, str]]) -> Generation: ...


class Guard(Protocol):
    impl: str

    def apply(
        self,
        generation: Generation,
        turn: TurnInput,
        state: SessionState,
        selection: MemorySelection,
    ) -> GuardResult: ...


class PostTurn(Protocol):
    """턴 뒤 처리 (응답 전 부분은 세션 갱신 정도, 무거운 추출은 비동기)"""

    impl: str

    def after(self, turn: TurnInput, state: SessionState, guard: GuardResult) -> SessionState: ...
