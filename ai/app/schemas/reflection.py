"""4단계 reflection 계약 — 누적된 기억 조각에서 상위 통찰(성향·가치관·반복주제)을 도출.

개별 사실(3-A memory)을 넘어, 여러 기억을 가로질러 대상자를 설명하는 통찰을 만든다
(Generative Agents reflection). 각 통찰은 근거가 된 기억 id로 역추적 가능해야 하며,
주어진 기억 밖 내용을 지어내지 않는다. 페르소나 성향·가치관 슬롯의 재료(personaMaterials).

BE는 누적 importance가 임계값을 넘거나 수동 재빌드 시 이 엔드포인트를 호출한다.
"""

from pydantic import Field

from app.schemas.common import ApiModel
from app.schemas.context import SubjectContext


class ReflectionMemoryInput(ApiModel):
    id: str = Field(min_length=1, max_length=200)  # BE의 기억 식별자(근거 역추적용)
    memory_text: str = Field(min_length=1, max_length=20_000)
    tags: list[str] = Field(default_factory=list)
    importance_score: int | None = Field(default=None, ge=1, le=10)


class ReflectionRequest(ApiModel):
    subject_context: SubjectContext | None = None
    memories: list[ReflectionMemoryInput] = Field(min_length=1)


class Reflection(ApiModel):
    insight: str  # 여러 기억을 가로지르는 상위 서술(성향·가치관·반복주제)
    category: str  # 가치관 | 성향 | 반복주제 | 관계 | 생애서사
    evidence_memory_ids: list[str] = Field(min_length=1)  # 근거 기억 id(요청에 존재)
    importance: int = Field(ge=1, le=10)


class ReflectionResponse(ApiModel):
    reflections: list[Reflection] = Field(default_factory=list)
    provider: str
    model: str
