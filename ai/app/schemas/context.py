"""subjectContext / intakeContext — 계약 2의 컨텍스트 입력 (시점별 2소스).

BE가 저장된 프로필·가족 용어집·Intake를 이 모양으로 변환해 분석 요청에
실어 보낸다. intakeContext의 섹션 상세는 설문지 v1.0 문항 키 기준 부록에서
확정 예정이라, 아직 정의되지 않은 키는 거부하지 않고 무시한다.
"""

from pydantic import ConfigDict, Field

from app.schemas.common import ApiModel


class ContextModel(ApiModel):
    model_config = ConfigDict(
        alias_generator=ApiModel.model_config["alias_generator"],
        populate_by_name=True,
        extra="ignore",
    )


class SubjectInfo(ContextModel):
    address_term: str | None = None
    name: str | None = None


class FamilyMember(ContextModel):
    name: str
    relation_to_subject: str | None = None
    address_terms: list[str] = Field(default_factory=list)


class SubjectContext(ContextModel):
    subject: SubjectInfo | None = None
    family_members: list[FamilyMember] = Field(default_factory=list)
    glossary_terms: list[str] = Field(default_factory=list)


class VoiceSampleRef(ContextModel):
    document_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None


class SttHints(ContextModel):
    names: list[str] = Field(default_factory=list)
    voice_sample_ref: VoiceSampleRef | None = None


class IntakeContext(ContextModel):
    stt_hints: SttHints | None = None
