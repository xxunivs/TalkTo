from typing import Literal

from pydantic import Field

from infra.schema.field_rule import ApiModel
from infra.schema.subject import IntakeContext, SubjectContext


class ConversationMessage(ApiModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class MemoryContext(ApiModel):
    id: str
    title: str = Field(default="", max_length=500)
    content: str = Field(min_length=1, max_length=50_000)
    tags: list[str] = Field(default_factory=list)
    # 중간계층 기억 선택용(선택). LONG_TERM | SHORT_TERM. 없으면 장기로 본다.
    memory_type: Literal["LONG_TERM", "SHORT_TERM"] | None = None


class PersonaContext(ApiModel):
    subject_id: str
    instructions: str = Field(min_length=1, max_length=50_000)
    voice_id: str | None = None
    # 고인 페르소나인가. 감정 안전선에 쓴다. 모르면(None) 고인으로 본다.
    deceased: bool | None = None
    # 손으로 만들어 가져온 지침(BE 페르소나 가져오기). 중간계층(사전 점검·근거 확인)을 거치지 않고
    # 지침 + 참고 기억 + 대화로만 대답한다. 그 지침은 등록부·가족 사전 없이 만들어져, 사전 점검이
    # 있는 기억도 '기억에 없다'로 판정해 모른다고 답하게 만들었다(2026-10-17 할머니 MVP 복원).
    fixed_prompt: bool = False


class PersonaResponseRequest(ApiModel):
    message: str = Field(min_length=1, max_length=20_000)
    history: list[ConversationMessage] = Field(default_factory=list)
    memories: list[MemoryContext] = Field(default_factory=list)
    persona: PersonaContext
    # 중간계층용(선택). 기존 경로는 쓰지 않는다.
    session_id: str | None = Field(default=None, max_length=200)
    # 앱에서 고른 가족 프로필 등 "지금 누가 말하나" 힌트
    speaker_hint: str | None = Field(default=None, max_length=200)


class PersonaResponseResult(ApiModel):
    content: str
    retrieved_memory_ids: list[str] = Field(default_factory=list)
    # 대답 음성에 실을 감정(VOICE_EMOTION=inline 일 때만). None 이면 목소리 회사가 정한다.
    emotion: str | None = None
    provider: str
    model: str


class MemoryCandidateRequest(ApiModel):
    user_message: str = Field(min_length=1, max_length=20_000)
    assistant_message: str = Field(min_length=1, max_length=20_000)
    history: list[ConversationMessage] = Field(default_factory=list)


class MemoryCandidate(ApiModel):
    summary: str
    category: str | None = None
    importance: int = Field(ge=1, le=10)
    confidence: float = Field(ge=0, le=1)
    # AI의 저장 추천(importance 임계값 기준). BE는 이 값을 따르되 기존 기억과 중복만 제거.
    should_store: bool


class MemoryCandidateResponse(ApiModel):
    candidates: list[MemoryCandidate]
    provider: str
    model: str


class PersonaAssemblyRequest(ApiModel):
    subject_context: SubjectContext
    intake_context: IntakeContext | None = None
    # 대상자 본인의 짧고 담백한 실제 발화 (말투 few-shot). BE가 전사에서 추려 보냄.
    speech_examples: list[str] = Field(default_factory=list, max_length=50)
    # 4단계 reflection 통찰(insight 문자열). 성향·가치관 슬롯에 합쳐진다. BE가 저장했다 전달.
    persona_insights: list[str] = Field(default_factory=list, max_length=50)
    # 사인·임종 질문 설정(4층 조립에서만 씀): None = 설문(M9-01·T1-02) 그대로, false = 끔(말하지 않음), true = 켬(물으면 담담하게)
    death_talk: bool | None = None


class PersonaAssemblyResponse(ApiModel):
    instructions: str
    subject_name: str
