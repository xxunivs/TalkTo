from typing import Literal

from pydantic import Field, field_validator, model_serializer

from infra.schema.field_rule import ApiModel
from infra.schema.subject import IntakeContext, SubjectContext


class ConversationMessage(ApiModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


def drop_empty_turns(value: object) -> object:
    """앞 대화에서 글이 빈 줄을 뺀다(검증 전에).

    예전에 빈 대답이 BE 에 저장된 대화는 그 줄이 최근 12줄 안에 남아 다음 턴이 모두 422 로 막혔다.
    빈 줄은 대화에 아무것도 더하지 않으므로 빼고 진행한다(그런 대화가 다시 이어진다).
    """
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
    # 만 19세 미만 살아 계신 대화 상대(BE 는 true 일 때만 싣는다 — 예전 BE 요청도 그대로 받는다).
    # 켜지면 두 경로 모두 매 턴 미성년 규칙 system·입력 검사·대답 검사를 한다(pipeline/safety/minor_guard.py).
    # 고인(deceased)이면 쓰지 않는다.
    minor: bool = False


class StyleExample(ApiModel):
    """가족이 고쳐 준 말투 한 쌍(BE StyleExample). 예전 대답 → 그분이라면 했을 말."""

    original: str = Field(min_length=1, max_length=2_000)
    corrected: str = Field(min_length=1, max_length=300)


class PersonaResponseRequest(ApiModel):
    message: str = Field(min_length=1, max_length=20_000)
    history: list[ConversationMessage] = Field(default_factory=list)
    memories: list[MemoryContext] = Field(default_factory=list)
    # 이 페르소나의 총 기억 수(memoryTotal, 선택). 검색 결과(memories)만 보내는 BE 가 이 값을 주면
    # '기억이 적을 때' 지시·사전 판정 건너뛰기를 정확히 켠다. 없으면 받은 기억 수로 짐작(pipeline/persona_prompt/low_memory.py).
    memory_total: int | None = Field(default=None, ge=0, le=1_000_000)
    persona: PersonaContext
    # 중간계층용(선택). 기존 경로는 쓰지 않는다.
    session_id: str | None = Field(default=None, max_length=200)
    # 앱에서 고른 가족 프로필 등 "지금 누가 말하나" 힌트
    speaker_hint: str | None = Field(default=None, max_length=200)
    # 기억 빈틈 표시를 받을까(BE '더 알려 주실 기억'). 켜면 같은 호출에서 대답 끝에 [기억없음:주제] 를 붙이게 하고
    # 떼어 내 memoryGap 으로 돌려준다(pipeline/s3_synthesis/e_memory_gap.py). 꺼져 있으면 예전과 같은 메시지다.
    flag_gaps: bool = False
    # 가족이 고쳐 준 말투(최근 10개). 있으면 '말투를 이렇게 고쳐 달라고 했어요' 절을 넣는다(두 경로 모두).
    style_examples: list[StyleExample] = Field(default_factory=list, max_length=10)

    _drop_empty_history = field_validator("history", mode="before")(drop_empty_turns)


class MemoryGap(ApiModel):
    """이번 대답에서 그분 기억에 없던 것(BE 가 대화가 끝난 뒤 가족에게 물을 질문 후보로 쓴다)."""

    # 모델이 붙인 주제(없으면 빈 글 — 빈틈은 있었다)
    topic: str = Field(default="", max_length=60)


class PersonaResponseResult(ApiModel):
    content: str
    retrieved_memory_ids: list[str] = Field(default_factory=list)
    # 대답 음성에 실을 감정(MiniMax VOICE_EMOTION=inline, Fish FISH_EMOTION=cue 일 때만). None 이면 목소리 회사가 정한다.
    emotion: str | None = None
    # 목소리로 읽을 글(Fish FISH_EMOTION=inline 이고 연기 표시가 남았을 때만). content 와 글자는 같고
    # 정해진 연기 표시([웃으며] 등)만 들어 있다. BE 는 이것을 저장했다가 읽기에 쓰고, 없으면 content 를 읽는다.
    # 화면·대화 기록·기억 추출에는 content(표시 없음)만 쓴다.
    speech_text: str | None = None
    # 기억 빈틈(요청 flagGaps 일 때만). 대답 끝 [기억없음:주제] 표시나 중간계층 사전 판정('기억에 없다')에서 온다.
    # 표시는 content·speechText 에서 이미 떼어 냈다(화면·가드·대화 기록·기억에 가지 않는다).
    memory_gap: MemoryGap | None = None
    provider: str
    model: str

    @model_serializer(mode="wrap")
    def _omit_empty_speech_text(self, handler):
        # speechText·memoryGap 이 없으면 칸을 아예 빼서 FISH_EMOTION=off 응답이 예전과 글자까지 같게 둔다
        data = handler(self)
        if isinstance(data, dict):
            for key in ("speechText", "speech_text", "memoryGap", "memory_gap"):
                if key in data and data[key] is None:
                    del data[key]
        return data


class GapCandidate(ApiModel):
    """대화 한 번에서 나온 기억 빈틈 후보(BE 가 대화 뒤에 보낸다)."""

    user_message: str = Field(min_length=1, max_length=4_000)
    reply: str = Field(default="", max_length=4_000)
    # 대답 모델이 붙인 주제(signal=model). 없으면 빈 글
    topic: str = Field(default="", max_length=60)
    # model = 대답 모델 표시 · low_recall = 기억 검색이 비었음 · feedback = 가족이 '내용이 틀려요'
    signal: Literal["model", "low_recall", "feedback"] = "model"
    # '내용이 틀려요'에 가족이 적은 한 줄(있으면)
    correction: str | None = Field(default=None, max_length=300)


class OpenGapQuestion(ApiModel):
    id: str = Field(min_length=1, max_length=64)
    topic: str = Field(default="", max_length=60)
    question: str = Field(min_length=1, max_length=300)


class GapQuestionRequest(ApiModel):
    candidate: GapCandidate
    # 이 대화 상대에 아직 열려 있는 질문(같은 것을 또 만들지 않게). BE 는 10개까지만 연다.
    open_questions: list[OpenGapQuestion] = Field(default_factory=list, max_length=20)
    # 질문에 쓸 그분 호칭(예: 할머니). 없으면 '그분'
    subject_label: str | None = Field(default=None, max_length=40)
    # 나 자신으로 만든 대화 상대(만든 분 = 그분)
    subject_self: bool = False
    # 돌아가신 분인가(질문 시제). 모르면 None → 지난 일로 묻는다
    deceased: bool | None = None
    # 후보가 함께 쓰는 가족의 대화·고침에서 왔다(질문은 만든 분이 본다). 그 가족이 한 말의 이름·날짜·사건·
    # 그 가족 자신의 사정은 질문·주제에 넣지 않고, 대답 모델이 붙인 주제로 대신 채우지 않는다.
    from_member: bool = False


class GapQuestionResponse(ApiModel):
    # new = 새 질문 · merge = 열린 질문 mergeInto 와 같은 것 · skip = 물을 것이 아님
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
