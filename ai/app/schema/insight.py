"""reflection 계약: 누적 기억 -> 상위 통찰 (근거 기억 id 역추적, 페르소나 성향과 가치관 재료)"""

from pydantic import Field

from app.schema.field_rule import ApiModel
from app.schema.subject import SubjectContext


class ReflectionMemoryInput(ApiModel):
    id: str = Field(min_length=1, max_length=200)  # BE 기억 식별자 (근거 역추적용)
    memory_text: str = Field(min_length=1, max_length=20_000)
    tags: list[str] = Field(default_factory=list)
    importance_score: int | None = Field(default=None, ge=1, le=10)


class ReflectionRequest(ApiModel):
    subject_context: SubjectContext | None = None
    memories: list[ReflectionMemoryInput] = Field(min_length=1)


class Reflection(ApiModel):
    insight: str  # 여러 기억을 가로지르는 상위 서술 (성향, 가치관, 반복 주제)
    category: str  # 가치관 | 성향 | 반복주제 | 관계 | 생애서사
    evidence_memory_ids: list[str] = Field(min_length=1)  # 근거 기억 id (요청에 존재)
    importance: int = Field(ge=1, le=10)


class ReflectionResponse(ApiModel):
    reflections: list[Reflection] = Field(default_factory=list)
    provider: str
    model: str
