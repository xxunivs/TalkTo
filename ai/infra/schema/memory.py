from typing import Literal
from uuid import UUID

from pydantic import Field

from infra.schema.field_rule import ApiModel


class PersistedTranscriptSegment(ApiModel):
    id: UUID
    segment_index: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    speaker_label: str = Field(default="unknown", max_length=120)
    transcript_text: str = Field(min_length=1, max_length=20_000)


class IntakeMemory(ApiModel):
    """Persona Intake 의 B(기억DB) 문항에서 만든 기억 한 조각.

    녹음에서 뽑는 MemorySegment 와 달리 전사 위치가 없다. 대신 어느 문항에서
    나왔는지와 사용 범위를 들고 있다. 기억DB 시드(별도 정리한 지식)도 같은
    형태로 넣되 source_question 을 비운다.
    """

    id: str
    memory_text: str = Field(min_length=1, max_length=20_000)
    tags: list[str] = Field(default_factory=list)
    importance_score: int = Field(default=5, ge=1, le=10)
    related_people: list[str] = Field(default_factory=list)
    confidence: Literal["confirmed", "inferred"] = "confirmed"
    source_question: str | None = None  # 예: M5-02. 기억DB 시드는 비운다
    period: str | None = None  # M5 시기, M1-01 연도 등
    emotion: str | None = None  # M5·M7 감정
    # 자유 = 먼저 꺼내도 됨 / 물으면만 / 회상 참고만 = 검색은 되되 발화 금지
    usage_scope: Literal["자유", "물으면만", "회상 참고만"] = "자유"


class MemorySegment(ApiModel):
    segment_index: int = Field(ge=0)
    source_transcript_segment_ids: list[UUID] = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    speaker_label: str = Field(default="unknown", max_length=120)
    memory_text: str = Field(min_length=1, max_length=20_000)
    confidence: Literal["confirmed", "inferred"]
    importance_score: int = Field(ge=1, le=10)
    tags: list[str] = Field(default_factory=list)
    related_people: list[str] = Field(default_factory=list)
    sensitivity_flags: list[str] = Field(default_factory=list)
