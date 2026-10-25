from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UserRole(StrEnum):
    USER = "USER"
    ADMIN = "ADMIN"


class UserStatus(StrEnum):
    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    # 회원 탈퇴. 계정 줄은 결제 기록 보존 때문에 남기고 알아볼 수 없게 바꾼다.
    DELETED = "DELETED"


class LifeStatus(StrEnum):
    LIVING = "LIVING"
    DECEASED = "DECEASED"
    UNKNOWN = "UNKNOWN"


class SetupStep(StrEnum):
    """페르소나 만들기 단계. 녹음 올리기 → 화자 고르기 → 질문 → 만들기 → 대화."""

    UPLOAD = "UPLOAD"
    SPEAKERS = "SPEAKERS"
    QUESTIONS = "QUESTIONS"
    BUILDING = "BUILDING"
    READY = "READY"


class JobStatus(StrEnum):
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=UserRole.USER)
    status: Mapped[str] = mapped_column(String(20), default=UserStatus.ACTIVE)
    refresh_token_version: Mapped[int] = mapped_column(Integer, default=0)
    # (예전) 계정 단위 무료 체험. 지금은 페르소나마다 체험한다(Persona.trial_ends_at).
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 무료 체험을 시작한 페르소나 수(지운 것도 센다). 계정마다 상한이 있다.
    trials_used: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    # 초기 가족 요금(첫 100가족·12개월)을 받은 가족이면 그 끝.
    # 그때까지 이 가족의 모든 페르소나를 초기 요금으로 결제한다.
    beta_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 초기 가족 요금이 끝난 뒤 정식 요금으로 이어 가겠다고 동의한 때. 없으면 갱신하지 않고 끝낸다.
    regular_price_consent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# 소셜 로그인이 쓸 만한 이메일을 주지 않으면 users.email 에 이 주소를 넣는다.
# .invalid 는 실제로 없는 도메인이라 비밀번호 로그인·가입과 겹치지 않는다. 화면에는 null 로 낸다.
SOCIAL_PLACEHOLDER_EMAIL_SUFFIX = "@social.talkto.invalid"


class SocialAccount(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """카카오·구글 계정 하나를 우리 사용자에 잇는다. 한 사람이 둘 다 이을 수 있다."""

    __tablename__ = "social_accounts"
    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_social_account_provider_user"),
    )

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))  # kakao / google
    # 그 회사가 주는 바뀌지 않는 번호(카카오 id, 구글 sub). 이메일은 바뀔 수 있어 이것으로 찾는다.
    provider_user_id: Mapped[str] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)


class LoginTicket(Base):
    """소셜 로그인 뒤 웹이 토큰과 바꿔 가는 한 번짜리 표.

    토큰을 주소에 바로 실으면 방문 기록·로그에 남는다. 그래서 짧게 사는 표만 주소에 싣는다.
    """

    __tablename__ = "login_tickets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Person(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "people"

    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    relationship_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 내가 그분을 부르는 호칭 (예: 할머니)
    address_term: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 돌아가신 분인지는 이 칸으로 정한다. 프롬프트 글자로 짐작하지 않는다.
    # 모르면 UNKNOWN 이고, 안전 규칙은 UNKNOWN 을 고인과 같이 다룬다.
    life_status: Mapped[str] = mapped_column(String(20), default=LifeStatus.UNKNOWN)


class Consent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "consents"
    __table_args__ = (UniqueConstraint("user_id", "consent_type", name="uq_consent_user_type"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # 계정 필수 종류(terms …) 또는 대화 상대별 종류(voice_clone:{persona_id} …)
    consent_type: Mapped[str] = mapped_column(String(80))
    agreed: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[str] = mapped_column(String(20), default="1.0")
    agreed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConsentRecord(Base, UUIDPrimaryKeyMixin):
    """동의 이력. 덮어쓰지 않고 쌓는다(동의 증빙). 대화 상대·계정을 지워도 남긴다."""

    __tablename__ = "consent_records"

    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    consent_type: Mapped[str] = mapped_column(String(80))
    # 대화 상대별 동의면 그 대화 상대. 지워도 기록은 남기려고 외래 키를 걸지 않는다.
    persona_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    agreed: Mapped[bool] = mapped_column(Boolean)
    version: Mapped[str] = mapped_column(String(20))
    # 그때 보여 준 문서(app/static/legal 의 key)와 그 본문 해시
    doc: Mapped[str | None] = mapped_column(String(40), nullable=True)
    text_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    channel: Mapped[str] = mapped_column(String(20), default="web")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


class Persona(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "personas"

    person_id: Mapped[str] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    speaking_style: Mapped[str | None] = mapped_column(Text, nullable=True)
    voice_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 목소리를 만든 곳: fish(지금, Fish drama-3). 다시 만들기 전의 예전 목소리는
    # minimax(통화 녹음) / elevenlabs(일반 녹음) / elevenlabs_pvc(똑 닮은 목소리)
    voice_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # 만들기 단계. 예전에 직접 넣은 페르소나는 바로 대화할 수 있게 READY 로 둔다.
    setup_step: Mapped[str] = mapped_column(String(20), default=SetupStep.READY)
    # 처음에 받은 이름·관계·호칭·내 이름 (JSON)
    basics: Mapped[dict] = mapped_column(JSON, default=dict)
    # 핵심 질문 답 (JSON). 문항 번호가 키다.
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    # AI 서버 /v1/persona/assembly 가 조립한 지침. 있으면 대화에 이것을 쓴다.
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 사람이 고른 그분 목소리(VoiceGroup.voice_key 목록). 한 사람이 둘로 나뉘었으면 여럿.
    subject_voices: Mapped[list[str]] = mapped_column(JSON, default=list)
    # 이 페르소나의 무료 체험이 끝나는 때. 다 만든 뒤 처음 볼 때 정한다.
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 다 만든 때(처음 READY 가 된 때). 결제 전 대화 횟수는 이날부터 30일마다 다시 센다.
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 어디서 왔나. None = 만들기 흐름, "import" = 관리자가 손으로 만든 페르소나를 가져옴
    # (app/services/persona_import.py). 가져온 것은 지침·IMPORT 기억을 다시 만들지 않는다.
    origin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 멈춘 까닭(2026-10-22 법률 자문, app/services/holds.py): objection · prior_wish ·
    # other_parent · subject_withdrew · subject_refused · minor_consent_missing.
    # 있으면 대화·처리·초대가 모두 멈춘다(gate). 비면 멈추지 않은 것
    hold_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    hold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 이때가 지나면 holds.sweep 이 기존 삭제 경로(persona_delete)로 지운다
    delete_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# Persona.origin 값
ORIGIN_IMPORT = "import"


class PersonaShare(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """가족 공유 한 자리. 주인이 만든 초대 링크 하나가, 들어온 가족 한 사람의 자리가 된다.

    주인은 넣지 않는다(주인은 Person.owner_user_id). user_id 가 비면 아직 쓰지 않은 링크다.
    대화 상대를 지우면 FK CASCADE 로 함께 지워진다(관계(relationship)는 두지 않는다).
    상태는 칸으로 두지 않고 정한다: user_id 가 있으면 joined, 없고 기한 전이면 open, 지났으면
    expired.
    취소·나가기·내보내기·그만두기는 줄을 지운다(이력은 consent_records 와 통계 수로 남는다).
    """

    __tablename__ = "persona_shares"
    __table_args__ = (UniqueConstraint("persona_id", "user_id", name="uq_persona_share_user"),)

    persona_id: Mapped[str] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), index=True
    )
    # 초대 열쇠의 sha256(hex). 열쇠 자체는 만들 때·다시 보낼 때 한 번만 돌려준다.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # 들어온 뒤에는 보지 않는다
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 주인이 미리 적어 둔 그 가족(ShareProfile 일부 + familyRow)
    prefill: Mapped[dict] = mapped_column(JSON, default=dict)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 들어온 가족(ShareProfile + familyRow·editedBy·editedAt)
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    # 만들기 흐름 대화 상대만: 이 가족과 대화할 때 쓰는 조립본(캐시)과 그 바탕의 해시
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    instructions_base: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Conversation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "conversations"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    persona_id: Mapped[str] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(20), default="TEXT")
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at"
    )


class Message(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "messages"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    # 대화 횟수를 다 써서 글로만 준 대답(목소리로 만들지 않는다)
    text_only: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    audio_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieved_memory_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 대답 음성에 실을 감정(neutral, calm, warm, happy, sad, surprised, worried).
    # 없으면 목소리 회사가 정한다.
    emotion: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 목소리로 읽을 글(AI 서버 speechText — Fish FISH_EMOTION=inline). content 와 글자는
    # 같고 연기 표시([웃으며] 등)만 들어 있다. 읽기에만 쓰고 화면·대화 기록에는 content 를
    # 쓴다. 없으면 content 를 읽는다.
    speech_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Feedback(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "feedback"
    __table_args__ = (UniqueConstraint("message_id", "user_id", name="uq_feedback_message_user"),)

    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    rating: Mapped[str] = mapped_column(String(20))
    # 말풍선 ⋯ 고르기(FEEDBACK_TAGS: LIKE·STYLE·CONTENT·TOO_LONG·VOICE). 예전 화면은 [] 를 보낸다
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    comment: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    # 아래는 2026-10-21 '대화 고치기'(app/services/corrections.py). 예전 줄은 비어 있다.
    # 말투·내용이 다를 때 "그분이라면 어떻게 말하셨을까요?" 한 줄(300자).
    # 글로만 쓴다(목소리로 읽지 않는다)
    correction: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # applied 반영함 · pending_owner 함께 쓰는 가족의 고침(만든 분 확인 전) ·
    # rejected 만든 분이 뺌 · none 반영할 것 없음(평가만·너무 길어요·목소리)
    state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 어느 대화 상대의 대답인가(만든 분 확인 목록·관리자 목록용)
    persona_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # '내용이 틀려요' 고침으로 만든 기억(source=FEEDBACK)
    memory_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 반영한 때와 만든 분이 정한 때(가족 고침)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConversationFeedback(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """대화 전체에 남긴 한 줄(대화 화면 위 작은 단추). 관리자만 본다."""

    __tablename__ = "conversation_feedback"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    persona_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    comment: Mapped[str] = mapped_column(String(300))
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)


class StyleExample(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """가족이 고쳐 준 말투 한 쌍(예전 대답 → 그분이라면 했을 말). 대화마다 최근 10개를 AI 에 보낸다
    (styleExamples). 글 예시로만 쓰고 목소리로 읽지 않는다. 대화 상대를 지우면 함께 지워진다."""

    __tablename__ = "style_examples"

    persona_id: Mapped[str] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), index=True
    )
    feedback_id: Mapped[str | None] = mapped_column(
        ForeignKey("feedback.id", ondelete="SET NULL"), nullable=True, index=True
    )
    original: Mapped[str] = mapped_column(Text)
    corrected: Mapped[str] = mapped_column(String(300))
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class GapQuestion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """'더 알려 주실 기억' 질문 하나(app/services/gaps.py). 대화 상대가 몰랐던 것을 대화가 끝난 뒤
    가족에게 묻는다. 대화 중에는 띄우지 않는다(대답 뒤 작업이 만들고, 화면은 나갈 때·대화 상대
    화면에서만 보여 준다). 대화 상대마다 열린 질문은 10개까지다."""

    __tablename__ = "gap_questions"

    persona_id: Mapped[str] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), index=True
    )
    # 같은 것을 다시 묻지 않게 묶는 짧은 주제(예: 즐겨 부른 노래)
    topic: Mapped[str] = mapped_column(String(60), default="")
    # TalkTo 가 가족에게 묻는 말(대화 상대가 묻는 말이 아니다)
    question: Mapped[str] = mapped_column(String(200))
    # open 열림 · answered 답함(가족 답은 만든 분 확인 전이어도) · dismissed '잘 모르겠어요'
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    # model 대답 모델이 표시 · low_recall 기억 검색이 빔 · feedback '내용이 틀려요'
    signal: Mapped[str] = mapped_column(String(20))
    source_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    # 만든 분이 '가족에게 물어보기'를 눌렀다(함께 쓰는 가족에게도 보인다)
    ask_family: Mapped[bool] = mapped_column(Boolean, default=False)
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    answered_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # applied 기억에 넣음 · pending_owner 가족 답(만든 분 확인 전) · rejected 만든 분이 뺌
    answer_state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 답으로 만든 기억(source=GAP)
    memory_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 같은 빈틈이 또 나온 횟수(합친 것까지)
    hits: Mapped[int] = mapped_column(Integer, default=1)
    # '잘 모르겠어요'를 누른 가족(그 가족에게만 숨긴다). 만든 분은 status=dismissed
    skipped_by: Mapped[list[str]] = mapped_column(JSON, default=list)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Memory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "memories"

    person_id: Mapped[str] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), index=True)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(120))
    memory_type: Mapped[str] = mapped_column(String(30), default="EPISODE")
    related_people: Mapped[list[str]] = mapped_column(JSON, default=list)
    related_period: Mapped[str | None] = mapped_column(String(50), nullable=True)
    body_markdown: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    confidence_score: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    embedding_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    # MANUAL = 사람이 직접 / RECORDING = 녹음 분석 / INTAKE = 질문 답 /
    # IMPORT = 관리자가 가져온 페르소나의 기억(다시 만들기가 지우지 않는다) /
    # GAP = '더 알려 주실 기억' 답 / FEEDBACK = '내용이 틀려요' 고침
    # (GAP·FEEDBACK 도 다시 만들기가 지우지 않는다)
    source: Mapped[str] = mapped_column(String(20), default="MANUAL")
    recording_id: Mapped[str | None] = mapped_column(
        ForeignKey("recordings.id", ondelete="SET NULL"), nullable=True
    )
    # 지금은 JSON 으로 둔다. RDS 로 옮길 때 pgvector 칸으로 바꾼다.
    embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    # 가족이 '내용이 틀려요'라고 한 대답에 쓰인 기억(끌 후보).
    # 지우거나 숨기지 않는다 — 관리자가 본다
    flagged_wrong: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class Recording(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """페르소나를 만들려고 올린 녹음 하나."""

    __tablename__ = "recordings"

    persona_id: Mapped[str] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), index=True
    )
    original_name: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    # 원본(영상 포함)에서 소리만 뽑아 storage_key 를 m4a 로 바꿨는지
    audio_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 통화 녹음(4kHz 위가 잘린 소리)인가. 소리를 뽑을 때 잰다.
    phone_band: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # UPLOADED / TRANSCRIBED / FAILED
    status: Mapped[str] = mapped_column(String(20), default="UPLOADED")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 대화 내용으로 짐작한 그분 화자(이 녹음 안의 라벨). 목소리 추천에 쓴다.
    suggested_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # /v1/analysis/recording 결과 (기억·말투·민감·요약)
    analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    segments: Mapped[list[TranscriptSegment]] = relationship(
        cascade="all, delete-orphan", order_by="TranscriptSegment.segment_index"
    )


class TranscriptSegment(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "transcript_segments"

    recording_id: Mapped[str] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE"), index=True
    )
    segment_index: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    # 전사가 이 녹음 안에서 나눈 화자 (SPK_0 …). 녹음마다 따로 붙는다.
    speaker_label: Mapped[str] = mapped_column(String(120))
    # 녹음 사이에 묶은 목소리 (VOICE_1 …). 같은 사람이면 녹음이 달라도 같다.
    voice_key: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    transcript_text: Mapped[str] = mapped_column(Text)
    corrected_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def text(self) -> str:
        return (self.corrected_text or self.transcript_text).strip()


class VoiceGroup(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """녹음 여러 개에 걸쳐 묶은 한 사람의 목소리. 화자 고르기 화면의 카드 하나."""

    __tablename__ = "voice_groups"

    persona_id: Mapped[str] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), index=True
    )
    voice_key: Mapped[str] = mapped_column(String(40))
    position: Mapped[int] = mapped_column(Integer)
    speech_ms: Mapped[int] = mapped_column(Integer)
    # [{"recordingId": …, "speakerLabel": …, "speechMs": …}]
    members: Mapped[list[dict]] = mapped_column(JSON, default=list)
    preview_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 대화 내용으로 보아 그분일 것 같은 목소리
    suggested: Mapped[bool] = mapped_column(Boolean, default=False)


class PersonaJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """오래 걸리는 일(전사·분석·만들기) 한 번. 화면은 이 표를 몇 초마다 읽는다."""

    __tablename__ = "persona_jobs"

    persona_id: Mapped[str] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))  # TRANSCRIBE / ANALYZE / BUILD
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.RUNNING)
    detail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # 화면의 진행 막대(0~100). 단계마다 작업이 적는다. 끝나면 100.
    percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    options: Mapped[dict] = mapped_column(JSON, default=dict)


class ErrorLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "error_logs"

    level: Mapped[str] = mapped_column(String(20), default="ERROR")
    path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Subscription(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """페르소나 하나의 구독(결제는 페르소나마다 받는다). 체험은 Persona.trial_ends_at 으로 본다.

    카드 번호는 받지 않는다. 결제 회사가 준 빌링키(카드를 대신하는 열쇠)만 둔다.
    페르소나를 지우면 구독도 지워진다(더 결제하지 않는다). 결제 기록(Payment)은 남는다.
    """

    __tablename__ = "persona_subscriptions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    persona_id: Mapped[str] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), unique=True, index=True
    )
    plan: Mapped[str] = mapped_column(String(20))  # memories / family
    # ACTIVE 결제됨 · PAST_DUE 갱신 결제 실패(며칠 기다린다) · CANCELED 끝남
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    provider: Mapped[str] = mapped_column(String(20))  # toss / mock
    customer_key: Mapped[str] = mapped_column(String(64))
    billing_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    card_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    current_period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # 해지를 눌렀으면 이번 기간이 끝날 때 멈춘다(남은 기간은 그대로 쓴다)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    # 낮은 요금제로 바꾸면 다음 기간부터 적용한다
    next_plan: Mapped[str | None] = mapped_column(String(20), nullable=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    # 매달 결제하는 날(한국 날짜). 31일에 시작하면 짧은 달은 그달 마지막 날, 다음 달은 다시 31일.
    billing_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 갱신 결제를 하는 동안 잡아 두는 자물쇠. 두 곳에서 같은 구독을 동시에 결제하지 않게.
    renew_lock_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Payment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """결제 한 번(첫 결제·갱신·요금제 올리기)."""

    __tablename__ = "payments"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # 어느 페르소나의 결제인가. 페르소나를 지워도 기록은 남기려고 외래 키를 걸지 않는다.
    persona_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    plan: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(20))
    order_id: Mapped[str] = mapped_column(String(64), unique=True)
    amount: Mapped[int] = mapped_column(Integer)
    # DONE / FAILED / UNKNOWN(연결이 끊겨 결과를 모름 — 같은 주문 번호로 결제 회사에 물어 정한다)
    status: Mapped[str] = mapped_column(String(20))
    # first(첫 달) / upgrade(올리기 차액) / renew(갱신)
    kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payment_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    receipt_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 아래는 청약철회·환불 판단(2026-10-22 법률 자문). 예전 줄은 비어 있다.
    # 이 결제로 시작한 이용 기간의 결제 후 첫 대화(초대받은 가족의 대화도 센다)
    first_chat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_chat_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    first_chat_member: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # kind=first 결제를 만들 때 그 대화 상대가 무료 체험을 실제로 썼는지(스냅숏)
    trial_used: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # 계약 내용(메일 또는 앱 알림)을 남긴 때
    contract_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 관리자가 토스 상점관리자에서 손으로 환불한 뒤 적은 때·금액·까닭
    # (no_chat_7d · no_trial_7d · mismatch · prorated · other + 메모)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refund_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    refund_note: Mapped[str | None] = mapped_column(String(300), nullable=True)


class UsageEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """대화 한 번(말하고 대답 듣기). 요금제의 '한 달 대화 N번'을 센다.

    한 달 = 결제일부터 다음 결제일 전까지(무료 체험은 체험 기간). 그 기간 안의 줄 수를 센다.
    페르소나·대화를 지워도 남는다(지우고 다시 만들어 횟수를 되살리지 못하게).
    """

    __tablename__ = "usage_events"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="conversation")
    persona_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)


class PersonaMaterial(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """사용자가 더한 글 자료(카카오톡 대화·문자·메일·블로그·일기·메모 등).

    넣을지 말지는 사용자 선택이다. 지금은 모아 두기만 하고, 기억 체계와 지침 만들기가 바뀌면
    include 가 켜진 자료를 기억 DB·지침 재료로 쓴다. 내용은 저장소(storage_key)에 UTF-8 글로 둔다.
    """

    __tablename__ = "persona_materials"

    persona_id: Mapped[str] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), index=True
    )
    # kakaotalk / sms / email / blog / diary / sns / other
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(10))  # file / text(붙여넣기)
    filename: Mapped[str | None] = mapped_column(String(200), nullable=True)
    storage_key: Mapped[str] = mapped_column(String(300))
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    preview: Mapped[str | None] = mapped_column(String(300), nullable=True)
    include: Mapped[bool] = mapped_column(Boolean, default=True)


class PremiumVoice(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """예전 똑 닮은 목소리(ElevenLabs PVC) 기록. 2026-10-17 에 없앴고 이제 만들지 않는다.

    남은 줄은 증빙과 정리에 쓴다: 목소리를 Fish 로 다시 만들거나 대화 상대를 지울 때 여기 남은
    PVC 와 처음 목소리(previous_voice_id)를 회사에서 지우고 REMOVED 로 둔다. 확인 서류는 서버가
    켤 때 지운다(voice_retrain.drop_pvc_documents).
    """

    __tablename__ = "premium_voices"

    persona_id: Mapped[str] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), unique=True, index=True
    )
    # UPLOADING → VERIFYING(확인 서류 심사) → TRAINING → READY / FAILED / REMOVED
    status: Mapped[str] = mapped_column(String(20), default="UPLOADING")
    voice_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject_seconds: Mapped[int] = mapped_column(Integer, default=0)
    document_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    detail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_voice_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    previous_voice_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 재료를 다 올린 때. 다시 시도할 때 같은 PVC 에 재료를 두 번 올리지 않게.
    samples_uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CheckoutSession(Base, TimestampMixin):
    """결제 창 한 번. 주소에 싣는 짧은 표라 한 번만 쓴다(새로고침해도 두 번 결제되지 않게)."""

    __tablename__ = "checkout_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    persona_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    plan: Mapped[str] = mapped_column(String(20))
    # 결제 창에 보여 준 값과 초기 요금인지. 카드를 받고 돌아오면 이 값 그대로 결제한다.
    quoted_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quoted_beta: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    customer_key: Mapped[str] = mapped_column(String(64))
    return_to: Mapped[str] = mapped_column(String(2000))
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN / DONE / FAILED
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SubscriptionRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """결제가 열리기 전의 구독 신청(대화 상대마다 하나). 돈은 나가지 않는다.

    결제가 열리면 신청한 분께 알리고, 그 대화 상대가 결제에 성공하면 CONVERTED 로 바꾼다.
    대화 상대를 지우거나 회원 탈퇴하면 함께 지워진다.
    """

    __tablename__ = "subscription_requests"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    persona_id: Mapped[str] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), unique=True, index=True
    )
    plan: Mapped[str] = mapped_column(String(20))  # memories / family (예전 premium 은 취소함)
    # REQUESTED 신청함 · CANCELED 취소함 · CONVERTED 결제함
    status: Mapped[str] = mapped_column(String(20), default="REQUESTED")
    # 예전 똑 닮은 목소리 신청 때 받던 인터뷰 연락처. 이제 받지 않고, 남은 값은 서버가 켤 때
    # 지운다(db.session._retire_premium). 칸만 남겨 둔다.
    contact_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    contact_time: Mapped[str | None] = mapped_column(String(60), nullable=True)


class AnalyticsEvent(Base, UUIDPrimaryKeyMixin):
    """사용 통계 이벤트 하나(개발자 화면 /admin/stats). 1년 보관한다.

    대화 내용·녹음·질문 답·이름·이메일은 넣지 않는다(props 는 작은 값만). 회원 탈퇴하면
    user_id 를 비운다. 대화 상대를 지워도 남기려고 persona_id 에는 외래 키를 걸지 않는다.
    """

    __tablename__ = "analytics_events"

    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    persona_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(50), index=True)
    props: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


class InterviewRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """기억 인터뷰 신청 하나(app/services/interview.py). 대표가 만든 분(주인)과 전화·영상으로
    이야기를 나누고, 들은 내용을 관리자가 글로 넣는다. 그분(대상자)은 인터뷰하지 않는다.

    연락처·이름·희망 일정은 끝나거나(done) 취소한(cancelled) 뒤 30일이 지나면 하루 정리가 지운다
    (wiped_at). 녹음 파일은 받지 않는다. 대화 상대를 지우면 함께 지워진다.
    """

    __tablename__ = "interview_requests"

    persona_id: Mapped[str] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), index=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # 인터뷰 받을 분(만든 분). 30일 정리 뒤에는 비어 있다
    name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    relation: Mapped[str | None] = mapped_column(String(40), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 바라는 요일(월…일)과 시간 한 줄
    days: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    time_text: Mapped[str | None] = mapped_column(String(60), nullable=True)
    method: Mapped[str] = mapped_column(String(10))  # phone / video
    # 통화 녹음 동의(선택). 녹음은 서비스에 올리지 않는다
    recording_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    # requested 신청함 · scheduled 일정 잡음 · done 끝남 · cancelled 취소함
    status: Mapped[str] = mapped_column(String(20), default="requested", index=True)
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 끝나거나 취소한 때(30일 정리의 기준). 다시 열면 비운다
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 이름·연락처·일정을 지운 때
    wiped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InterviewEntry(Base, UUIDPrimaryKeyMixin):
    """기억 인터뷰에서 들은 답 하나(질문 번호별). 같은 답을 persona.answers 에도 합친다.

    무엇을 인터뷰로 넣었는지 남기는 기록이다(source=interview). 신청 없이 대화 상대에 바로 넣으면
    request_id 가 비어 있다. 만든 분이 빼 달라고 하면 관리자가 이 기록 하나를 지우고, 이 답이 더한
    부분(added)만 persona.answers 에서 뺀다(interview.remove_answer).
    """

    __tablename__ = "interview_entries"

    request_id: Mapped[str | None] = mapped_column(
        ForeignKey("interview_requests.id", ondelete="SET NULL"), nullable=True, index=True
    )
    persona_id: Mapped[str] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="interview")
    # 이 답이 persona.answers 에 실제로 더한 것: 목록 질문은 새로 생긴 줄들, 글·고르는 질문은
    # [넣은 글](덧붙인 덩어리 또는 바꾼 답). 이미 들어 있어 바뀐 게 없으면 [].
    # 없으면(None) 넣은 글 전체를 더한 것으로 본다.
    added: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


# ──────────── 2026-10-22 법률 자문 반영(동의 녹음·미성년·유족·멈춤·알림·지운 기록) ────────────


class SubjectConsentRecording(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """살아 계신 분(나 자신 아님) 본인의 동의 녹음 한 번(시도). app/services/subject_consent.py.

    녹음은 서울 저장소(personas/{pid}/subject_consent/)에만 두고 어떤 해외 회사로도 보내지 않는다
    (받아쓰기·목소리 만들기에 쓰지 않는다). Recording 표에 넣지 않는 것도 그래서다(받아쓰기 작업이
    집어 가지 않게). 다시 녹음해도 앞선 줄을 지우지 않는다. 대화 상대를 지우면 함께 지워진다.
    """

    __tablename__ = "subject_consent_recordings"
    __table_args__ = (
        UniqueConstraint("persona_id", "attempt", name="uq_subject_consent_recording_attempt"),
    )

    persona_id: Mapped[str] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), index=True
    )
    # 녹음한 만든 분
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # 이 대화 상대에서 몇 번째(1부터)
    attempt: Mapped[int] = mapped_column(Integer)
    # adult · minor_teen · minor_child (발급 때 정함, ages.consent_mode)
    mode: Mapped[str] = mapped_column(String(20))
    # 녹음할 때 보인 대본(subject_consent_script · subject_consent_child)과 그 판·해시
    script_doc: Mapped[str] = mapped_column(String(40))
    script_version: Mapped[str] = mapped_column(String(20))
    script_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 확인 단어 두 개를 공백 하나로 이은 것(예: 사과 바다). 서버가 발급한다
    words: Mapped[str] = mapped_column(String(40))
    words_issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 발급 + 60분. 이 뒤에 올리면 받지 않는다
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # issued · submitted · interrupted · superseded · expired · passed · rerequest · hold · refused
    status: Mapped[str] = mapped_column(String(20), default="issued", index=True)
    # 녹음 중 '그분이(아이가) 원하지 않으세요'를 눌렀음
    refused_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    storage_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 서버가 잰 길이(audio.voice_seconds)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 받은 파일 그대로의 해시(편집 여부 확인용). 변환하지 않고 저장한다
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # {"steps": [{"step": 1, "atMs": 0}, …], "format": "webm", "interrupted": bool, "reason": str}
    # 브라우저 종류(UA)는 남기지 않는다(처리방침에 없는 항목)
    client_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    # 검수자가 이 판정에서 '한 번 더 녹음 허락'을 눌렀음(녹음 상한이 하나 늘어난다)
    extra_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 검수 10항목 {"1": true, …, "10": true}
    checklist: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reviewer_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 만든 분께 보일 말(500자)과 내부 메모(1000자)
    note_to_owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class PersonaMinorCheck(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """만 19세 미만 대화 상대의 시스템 상태(만든 분이 적은 값은 Persona.basics 의 minor·birthYm·
    guardianBasis). 법정대리인 확인 문자와 14·19세 재동의. app/services/minors.py · sms.py.

    휴대전화 번호는 가린 것만 남긴다. manual(대표가 손으로 보냄)일 때만 보내기 전까지 전체 번호와
    확인 번호를 두고, 대표가 '보냈어요'를 누르면 바로 비운다.
    """

    __tablename__ = "persona_minor_checks"

    persona_id: Mapped[str] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), unique=True, index=True
    )
    sms_phone_masked: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # sha256(code + persona_id + JWT_SECRET)
    sms_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sms_code_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 이 번호 확인 시도 수(넘으면 새로 받기)와 오늘 보낸 수
    sms_attempts: Mapped[int] = mapped_column(Integer, default=0)
    sms_sent_count: Mapped[int] = mapped_column(Integer, default=0)
    sms_last_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sms_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sms_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sms_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # manual 공급자만: 대표가 문자를 보낼 때까지 둔 번호(숫자만)와 확인 번호. 보내면 비운다
    sms_phone_pending: Mapped[str | None] = mapped_column(String(11), nullable=True)
    sms_code_pending: Mapped[str | None] = mapped_column(String(6), nullable=True)
    sms_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    turned14_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    turned19_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 14 · 19. 새 동의가 필요해지는 때(KST 그달의 다음 달 1일)와 기한(+6개월), 받은 때
    reconsent_kind: Mapped[str | None] = mapped_column(String(4), nullable=True)
    reconsent_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reconsent_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reconsent_done_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 보낸 미리 알림 표시 {"d30": iso, "d7": iso}
    reminders: Mapped[dict] = mapped_column(JSON, default=dict)


class BereavedDeclaration(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """돌아가신 분 대화 상대의 신청 자격과 유족 명단(제3자 정보). app/services/bereaved.py.

    대화 상대를 지우면 함께 지워진다. 고쳐 쓰면 덮기 전 선언을 history 에 쌓는다(이의가 왔을 때
    그때 명단에 누가 있었는지 지우지 않으려고).
    """

    __tablename__ = "bereaved_declarations"

    persona_id: Mapped[str] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), unique=True, index=True
    )
    # spouse · child · grandchild · parent · other
    applicant_relation: Mapped[str] = mapped_column(String(20))
    # other 일 때만: 동의해 준 유족의 이름과 관계(spouse · child · grandchild · parent)
    consenting_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    consenting_relation: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # [{"name": …, "relation": spouse|son|daughter|mother|father, "contact": … | null}] 12줄까지
    members: Mapped[list[dict]] = mapped_column(JSON, default=list)
    # "적을 분이 없어요(모두 돌아가셨어요)"
    none_left: Mapped[bool] = mapped_column(Boolean, default=False)
    declared_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    declared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 그때 pledge_deceased 버전
    pledge_version: Mapped[str] = mapped_column(String(20))
    history: Mapped[list[dict]] = mapped_column(JSON, default=list)


class PersonaObjection(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """이의 접수 기록(유족·다른 쪽 부모·그분 본인). 대화 상대를 지워도 남기려고 외래 키를 걸지
    않는다. 결과가 정해진 지(outcome_at) 3년 지나면 하루 정리가 지운다."""

    __tablename__ = "persona_objections"

    persona_id: Mapped[str] = mapped_column(String(36), index=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # objection · prior_wish · other_parent · subject_withdrew
    reason: Mapped[str] = mapped_column(String(30))
    from_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    from_relation: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 관리자 메모(1000자). 고인·사별 이야기는 적지 않는다
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    admin_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # deleted · cancelled
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OwnerNotice(Base, UUIDPrimaryKeyMixin):
    """앱 안 알림 하나(app/services/notices.py). 푸시·문자는 보내지 않는다.

    TalkTo 가 말하는 글이다. 대화 상대가 말하거나 보낸 것처럼 쓰지 않는다(대화 상대는
    notices.persona_label 로만 가리킨다). 읽고 180일 지나면 하루 정리가 지운다. 단 kind=contract
    (계약 내용 교부)는 읽어도 두고 탈퇴 때 지운다. 탈퇴는 계정 줄을 지우지 않으므로 CASCADE 가
    돌지 않는다 — 탈퇴 처리가 따로 지운다.
    """

    __tablename__ = "owner_notices"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="uq_owner_notice_user_dedupe"),
    )

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # 지운 대화 상대의 알림도 남기려고 외래 키를 걸지 않는다
    persona_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)
    # {"label": "…", "route": "/p/{id}/consent"}
    action: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 같은 일을 두 번 알리지 않게(예: minor14:{pid}). 비면 겹침을 보지 않는다
    dedupe_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeletionRecord(Base, UUIDPrimaryKeyMixin):
    """지운 일시 기록(내용 없이 개수만). app/services/deletion.py. 지운 지 5년 지나면 하루 정리가
    지운다. 화면·메일의 '서버 백업에는 ○월 ○일까지'는 backup_until 로 쓴다."""

    __tablename__ = "deletion_records"

    # persona · account · voice_consent · sensitive_consent · share_member(공유를 끝내 지운 가족
    # 대화) 또는 멈춘 까닭(objection 등)
    kind: Mapped[str] = mapped_column(String(30))
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    persona_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # owner · admin · system
    requested_by: Mapped[str] = mapped_column(String(10))
    done_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # done_at + deletion_backup_days(7, RDS 백업 보관 기간)
    backup_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 목소리 회사에서 지웠나(지울 목소리가 없으면 None)
    vendor_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # 개수만({"personas": 2})
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
