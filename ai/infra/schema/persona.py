from typing import Literal

from pydantic import Field, field_validator, model_serializer

from infra.schema.field_rule import ApiModel
from infra.schema.subject import IntakeContext, SubjectContext


class ConversationMessage(ApiModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


def drop_empty_turns(value: object) -> object:
    """앞 대화의 빈 글 제거 (검증 전, 저장된 빈 대답으로 이후 턴이 422 로 막힌 이력)"""
    if not isinstance(value, list):
        return value

    def blank(item: object) -> bool:
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
        return isinstance(content, str) and not content.strip()

    return [item for item in value if not blank(item)]


class MemoryContext(ApiModel):
    id: str
    title: str = Field(default="", max_length=500)
    content: str = Field(min_length=1, max_length=50_000)
    tags: list[str] = Field(default_factory=list)
    # 중간계층 기억 선택용 (선택, 없으면 장기)
    memory_type: Literal["LONG_TERM", "SHORT_TERM"] | None = None


class PersonaContext(ApiModel):
    subject_id: str
    instructions: str = Field(min_length=1, max_length=50_000)
    voice_id: str | None = None
    # 돌아가신 대상자 여부 (감정 안전선용, None 이면 True 로 간주)
    deceased: bool | None = None
    # 가져온 수동 지침 (중간계층 생략: 등록부 없이 만든 지침이라 사전 점검이 있는 기억도 모른다로 판정)
    fixed_prompt: bool = False
    # 만 19세 미만 생존 대화 상대 (BE 는 true 일 때만 전송, deceased 면 미사용)
    # 두 경로 모두 매 턴 미성년 규칙, 입력 검사, 대답 검사 (pipeline/safety/minor_guard.py)
    minor: bool = False


class StyleExample(ApiModel):
    """가족이 고친 말투 한 쌍 (예전 대답 -> 그분이라면 했을 말)"""

    original: str = Field(min_length=1, max_length=2_000)
    corrected: str = Field(min_length=1, max_length=300)


class PersonaResponseRequest(ApiModel):
    message: str = Field(min_length=1, max_length=20_000)
    history: list[ConversationMessage] = Field(default_factory=list)
    memories: list[MemoryContext] = Field(default_factory=list)
    # 총 기억 수 (선택, 기억 부족 지시와 사전 판정 생략 판단용, 없으면 받은 기억 수로 추정)
    memory_total: int | None = Field(default=None, ge=0, le=1_000_000)
    persona: PersonaContext
    # 중간계층 전용 (선택)
    session_id: str | None = Field(default=None, max_length=200)
    # 현재 화자 힌트 (앱에서 고른 가족 프로필 등)
    speaker_hint: str | None = Field(default=None, max_length=200)
    # 기억 빈틈 표시 요청 (대답 끝 [기억없음:주제] -> memoryGap, pipeline/s3_synthesis/e_memory_gap.py)
    flag_gaps: bool = False
    # 가족이 고친 말투 (최근 10개, 두 경로 모두 말투 교정 절 추가)
    style_examples: list[StyleExample] = Field(default_factory=list, max_length=10)

    _drop_empty_history = field_validator("history", mode="before")(drop_empty_turns)


class MemoryGap(ApiModel):
    """이번 대답의 기억 빈틈 (BE 의 대화 후 가족 질문 후보)"""

    # 모델이 붙인 주제 (빈 글이어도 빈틈은 존재)
    topic: str = Field(default="", max_length=60)


class PersonaResponseResult(ApiModel):
    content: str
    retrieved_memory_ids: list[str] = Field(default_factory=list)
    # 대답 음성 감정 (VOICE_EMOTION=inline, FISH_EMOTION=cue 일 때만, None 이면 제공사 판단)
    emotion: str | None = None
    # 음성용 글 (FISH_EMOTION=inline 에서 연기 표시가 남았을 때만, content + 연기 표시)
    # 화면, 대화 기록, 기억 추출은 content 만 사용
    speech_text: str | None = None
    # 기억 빈틈 (flagGaps 일 때만, 대답 끝 표시 또는 중간계층 사전 판정에서 생성)
    # 표시는 content, speechText 에서 이미 제거
    memory_gap: MemoryGap | None = None
    provider: str
    model: str

    @model_serializer(mode="wrap")
    def _omit_empty_speech_text(self, handler):
        # speechText, memoryGap 이 None 이면 칸 생략 (FISH_EMOTION=off 응답을 예전과 동일하게)
        data = handler(self)
        if isinstance(data, dict):
            for key in ("speechText", "speech_text", "memoryGap", "memory_gap"):
                if key in data and data[key] is None:
                    del data[key]
        return data


class GapCandidate(ApiModel):
    """대화 한 번의 기억 빈틈 후보 (대화 후 BE 전송)"""

    user_message: str = Field(min_length=1, max_length=4_000)
    reply: str = Field(default="", max_length=4_000)
    # 대답 모델이 붙인 주제 (signal=model, 없으면 빈 글)
    topic: str = Field(default="", max_length=60)
    # model = 대답 모델 표시, low_recall = 기억 검색 결과 없음, feedback = 가족의 내용 오류 신고
    signal: Literal["model", "low_recall", "feedback"] = "model"
    # 내용 오류 신고에 가족이 적은 한 줄 (선택)
    correction: str | None = Field(default=None, max_length=300)


class OpenGapQuestion(ApiModel):
    id: str = Field(min_length=1, max_length=64)
    topic: str = Field(default="", max_length=60)
    question: str = Field(min_length=1, max_length=300)


class GapQuestionRequest(ApiModel):
    candidate: GapCandidate
    # 열린 질문 (중복 생성 방지, BE 는 10개까지)
    open_questions: list[OpenGapQuestion] = Field(default_factory=list, max_length=20)
    # 질문에 쓸 그분 호칭 (없으면 그분)
    subject_label: str | None = Field(default=None, max_length=40)
    # 본인 페르소나 여부 (만든 분 = 그분)
    subject_self: bool = False
    # 돌아가신 분 여부 (질문 시제, None 이면 과거형)
    deceased: bool | None = None
    # 공유 가족 대화에서 나온 후보 (만든 분이 질문을 보므로 그 가족의 이름, 날짜, 사건, 사정 제외)
    from_member: bool = False


class GapQuestionResponse(ApiModel):
    # new = 새 질문, merge = 열린 질문 mergeInto 와 동일, skip = 물을 것 없음
    action: Literal["new", "merge", "skip"]
    question: str | None = None
    topic: str | None = None
    merge_into: str | None = None
    provider: str
    model: str


class MemoryCandidateRequest(ApiModel):
    user_message: str = Field(min_length=1, max_length=20_000)
    assistant_message: str = Field(min_length=1, max_length=20_000)
    history: list[ConversationMessage] = Field(default_factory=list)

    _drop_empty_history = field_validator("history", mode="before")(drop_empty_turns)


class MemoryCandidate(ApiModel):
    summary: str
    category: str | None = None
    importance: int = Field(ge=1, le=10)
    confidence: float = Field(ge=0, le=1)
    # 저장 추천 (importance 임계값 기준, BE 는 따르되 기존 기억 중복만 제거)
    should_store: bool


class MemoryCandidateResponse(ApiModel):
    candidates: list[MemoryCandidate]
    provider: str
    model: str


class PersonaAssemblyRequest(ApiModel):
    subject_context: SubjectContext
    intake_context: IntakeContext | None = None
    # 대상자 실제 발화 (말투 few-shot, BE 가 전사에서 추림)
    speech_examples: list[str] = Field(default_factory=list, max_length=50)
    # reflection 통찰 문자열 (성향, 가치관 슬롯에 병합)
    persona_insights: list[str] = Field(default_factory=list, max_length=50)
    # 사인, 임종 질문 설정 (4층 조립 전용): None = 설문 그대로, false = 말하지 않음, true = 물으면 담담하게
    death_talk: bool | None = None


class PersonaAssemblyResponse(ApiModel):
    instructions: str
    subject_name: str
