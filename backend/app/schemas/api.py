from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import EmailStr, Field, field_validator, model_validator

from app.db.models import SOCIAL_PLACEHOLDER_EMAIL_SUFFIX
from app.schemas.common import APIModel


class LoginRequest(APIModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class SignupRequest(APIModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=100)
    # 가입 화면에서 체크한 계정 필수 동의(age14, terms …). 빠지면 첫 로그인 때 동의 화면으로 간다.
    agreements: list[str] = Field(default_factory=list, max_length=30)


class RefreshRequest(APIModel):
    refresh_token: str


class TokenPair(APIModel):
    bearer_token: str
    refresh_token: str
    token_type: str = "bearer"


class OAuthTicketExchange(APIModel):
    ticket: str = Field(min_length=1, max_length=200)


class AuthProviders(APIModel):
    """로그인 화면이 어떤 버튼을 보여줄지."""

    kakao: bool
    google: bool
    signup: bool


class UserView(APIModel):
    id: str
    # 내보내는 쪽은 EmailStr 로 막지 않는다. 소셜 가입자의 자리 표시 주소(.invalid)는 null 로 낸다.
    email: str | None
    name: str
    role: str
    status: str
    # 처음 이은 소셜 로그인(kakao / google). 이메일로만 가입했으면 null.
    provider: str | None = None
    created_at: datetime

    @field_validator("email", mode="before")
    @classmethod
    def hide_placeholder_email(cls, value: str | None) -> str | None:
        if isinstance(value, str) and value.endswith(SOCIAL_PLACEHOLDER_EMAIL_SUFFIX):
            return None
        return value


class ImportedPersonaRef(APIModel):
    """관리자가 가져온 대화 상대 하나(되돌리기에 쓸 id)."""

    id: str
    name: str
    created_at: datetime


class AdminUserView(APIModel):
    """관리자 사용자 목록·검색 한 줄. 페르소나 가져오기의 대상 계정 고르기에도 쓴다.

    이메일은 가린 값만 싣는다(전체 주소는 응답에도 넣지 않는다 — 대상 고르기는 이름·가입일·소셜로).
    """

    id: str
    name: str
    role: str
    status: str
    # 처음 이은 소셜 로그인(kakao / google). 이메일로만 가입했으면 null.
    provider: str | None = None
    # 이은 소셜 로그인 전부(이은 순서)
    providers: list[str] = Field(default_factory=list)
    persona_count: int = 0
    # a***@gmail.com. 자리표시 주소면 소셜이 준 이메일을 가린 값(없으면 null).
    email_masked: str | None = None
    # 관리자가 가져온 대화 상대(origin=import). 되돌리기(DELETE /admin/personas/{id}/import)에 쓴다.
    imported_personas: list[ImportedPersonaRef] = Field(default_factory=list)
    created_at: datetime


class MeView(UserView):
    # 아직 받지 않은 계정 필수 동의. 비어 있지 않으면 화면이 동의 화면으로 보낸다.
    consents_needed: list[str] = Field(default_factory=list)


class AccountDelete(APIModel):
    confirm: str = Field(max_length=20)


class InvitationCreate(APIModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=100)
    role: str = "USER"


class UserUpdate(APIModel):
    role: str | None = None
    status: str | None = None


class ConsentAccept(APIModel):
    types: list[str] = Field(default_factory=list, max_length=30)


class ConsentView(APIModel):
    consent_type: str
    agreed: bool
    version: str
    agreed_at: datetime | None


class ConsentSaved(ConsentView):
    # /consents/accept 와 같은 남은 계정 필수 동의
    consents_needed: list[str] = Field(default_factory=list)


class ConsentSave(APIModel):
    consent_type: str = Field(min_length=1, max_length=80)
    agreed: bool
    version: str = "1.0"


class ConversationCreate(APIModel):
    persona_id: str
    channel: str = Field(pattern="^(TEXT|VOICE)$")
    title: str | None = Field(default=None, max_length=120)


class ConversationView(APIModel):
    id: str
    persona_id: str
    channel: str
    title: str | None
    started_at: datetime
    last_message_at: datetime | None


class MessageView(APIModel):
    id: str
    role: str
    content: str
    audio_url: str | None
    retrieved_memory_ids: list[str]
    latency_ms: int | None
    created_at: datetime
    text_only: bool | None = None


class ConversationDetail(ConversationView):
    messages: list[MessageView]


class TextMessageCreate(APIModel):
    content: str = Field(min_length=1, max_length=4000)


class MessageExchange(APIModel):
    user_message: MessageView
    assistant_message: MessageView
    stt_text: str | None = None
    # 이번 대화를 센 뒤 남은 대화 횟수(None = 제한 없음)
    conversations_left: int | None = None
    # 대화 횟수를 다 써서 글로만 대답했다(목소리로 들려주지 않는다)
    text_only: bool = False


class PersonaView(APIModel):
    id: str
    person_id: str
    name: str
    description: str | None
    speaking_style: str | None
    voice_id: str | None
    status: str
    is_active: bool


class ActivePersonaView(APIModel):
    """고객이 보는 대화 상대. 대화에 쓰는 설명·말투(지침 재료)는 내보내지 않는다."""

    id: str
    person_id: str
    name: str
    voice_id: str | None
    status: str
    is_active: bool


class PersonaUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    speaking_style: str | None = None
    voice_id: str | None = None
    status: str | None = None
    is_active: bool | None = None


class FeedbackRating(StrEnum):
    UP = "UP"
    NEUTRAL = "NEUTRAL"
    DOWN = "DOWN"


FeedbackTag = Annotated[str, Field(min_length=1, max_length=40)]


class FeedbackCreate(APIModel):
    rating: FeedbackRating
    # 말풍선 ⋯ 고르기: LIKE·STYLE·CONTENT·TOO_LONG·VOICE (app/services/corrections.py).
    # 예전 화면은 []
    tags: list[FeedbackTag] = Field(default_factory=list, max_length=10)
    comment: str | None = Field(default=None, max_length=500)
    # STYLE·CONTENT 에 붙이는 "그분이라면 어떻게 말하셨을까요?" 한 줄(글 예시로만 쓴다)
    correction: str | None = Field(default=None, max_length=300)


class FeedbackView(APIModel):
    id: str
    message_id: str
    user_id: str
    rating: str
    tags: list[str]
    comment: str | None
    reviewed: bool
    created_at: datetime
    correction: str | None = None
    # applied · pending_owner(가족 고침, 만든 분 확인 전) · rejected · none (예전 줄은 null)
    state: str | None = None


class AdminFeedbackView(FeedbackView):
    """관리자 평가 목록 한 줄. 대답 글과 대화 상대(성함 대신 호칭)를 붙인다."""

    persona_id: str | None = None
    persona_title: str | None = None
    # 평가한 대답(그분 쪽 말풍선)
    reply: str | None = None
    # 함께 쓰는 가족이 남긴 것
    member: bool = False
    applied_at: datetime | None = None
    decided_at: datetime | None = None


class ReviewedUpdate(APIModel):
    reviewed: bool


class ConversationFeedbackCreate(APIModel):
    comment: str = Field(min_length=1, max_length=300)


class ConversationFeedbackView(APIModel):
    id: str
    conversation_id: str
    persona_id: str | None
    comment: str
    reviewed: bool
    created_at: datetime


class AdminConversationFeedbackView(ConversationFeedbackView):
    user_id: str
    persona_title: str | None = None
    member: bool = False


class GapAnswer(APIModel):
    # 빈 글·2,000자 넘음은 gaps.clean_answer 가 한국어 400 으로 알린다
    text: str = Field(default="", max_length=4000)


class GapAskFamily(APIModel):
    ask: bool = True


class CorrectionDecision(APIModel):
    """만든 분이 확인 목록에서 본 가족 고침(고친 말·고른 것). 가족이 그 사이에 바꿨으면 409 라,
    본 적 없는 글이 반영되지 않는다. 보내지 않는 예전 화면은 지금 것을 그대로 반영한다."""

    correction: str | None = Field(default=None, max_length=300)
    tags: list[str] = Field(default_factory=list, max_length=10)


class GapDecision(APIModel):
    """만든 분이 확인 목록에서 본 가족 답(답 글·답한 때 — 목록의 answerText·answeredAt 그대로).
    그 사이에 다른 답으로 바뀌었으면 409 라(가족이 나가 질문이 다시 열린 뒤 다른 가족이 답함 등),
    본 적 없는 답이 기억이 되지 않는다. 보내지 않는 예전 화면은 지금 것을 그대로 확인한다."""

    # 저장된 답은 gaps.ANSWER_MAX(2,000자) 안이다
    answer_text: str | None = Field(default=None, max_length=2000)
    answered_at: datetime | None = None


# 기억 글 길이 상한. AI 서버 MemoryContext.content(최대 50,000자)보다 짧게: 넘으면 그 기억이 실린
# 대화마다 AI 서버가 거절한다. 칸 길이는 DB 열(memory_type 30, related_period 50)과 같다.
MEMORY_BODY_MAX = 20_000
MemoryTag = Annotated[str, Field(min_length=1, max_length=50)]
RelatedPerson = Annotated[str, Field(min_length=1, max_length=100)]


class MemoryCreate(APIModel):
    person_id: str
    title: str = Field(min_length=1, max_length=120)
    memory_type: str = Field(default="EPISODE", min_length=1, max_length=30)
    related_people: list[RelatedPerson] = Field(default_factory=list, max_length=20)
    related_period: str | None = Field(default=None, max_length=50)
    body_markdown: str = Field(min_length=1, max_length=MEMORY_BODY_MAX)
    tags: list[MemoryTag] = Field(default_factory=list, max_length=10)
    confidence_score: float = Field(default=1.0, ge=0, le=1)


class MemoryUpdate(APIModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    memory_type: str | None = Field(default=None, min_length=1, max_length=30)
    related_people: list[RelatedPerson] | None = Field(default=None, max_length=20)
    related_period: str | None = Field(default=None, max_length=50)
    body_markdown: str | None = Field(default=None, min_length=1, max_length=MEMORY_BODY_MAX)
    tags: list[MemoryTag] | None = Field(default=None, max_length=10)
    confidence_score: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("At least one field is required")
        return self


class MemoryView(APIModel):
    id: str
    person_id: str
    title: str
    memory_type: str
    related_people: list[str]
    related_period: str | None
    body_markdown: str
    tags: list[str]
    confidence_score: float
    status: str
    embedding_status: str
    created_at: datetime
    updated_at: datetime


class DashboardOverview(APIModel):
    total_users: int
    active_users: int
    total_conversations: int
    total_messages: int
    total_memories: int
    negative_feedback: int


class DailyMetric(APIModel):
    date: str
    conversations: int
    messages: int
    feedback: int
