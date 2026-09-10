"""subjectContext / intakeContext — 계약 2의 컨텍스트 입력 (시점별 2소스).

BE가 저장된 프로필·가족 용어집·Intake를 이 모양으로 변환해 분석 요청에
실어 보낸다. intakeContext의 섹션 상세는 설문지 v1.0 문항 키 기준 부록에서
확정 예정이라, 아직 정의되지 않은 키는 거부하지 않고 무시한다.
"""

from pydantic import ConfigDict, Field

from infra.schema.field_rule import ApiModel


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


class SituationalReaction(ContextModel):
    situation: str
    response: str  # 대상자가 할 법한 답 (스크립트)
    avoid: str | None = None  # 피해야 할 답


class FamilyNote(ContextModel):
    """M2-01 가족 표 한 행 + M2-02 걱정."""

    name: str
    relation: str | None = None
    tone: str | None = None  # M2-01 대화할 때 톤
    address_term_to: str | None = None  # M2-01 그분이 이 사람을 부르는 호칭
    address_term_from: str | None = None  # M2-01 이 사람이 그분을 부르는 호칭
    self_reference: str | None = None  # M2-01 그분이 자기를 부르는 말
    speech_level: str | None = None  # M2-01 반말 / 존댓말
    talks_with_persona: bool | None = None  # M2-01 이 사람도 페르소나와 대화하나요
    worries: list[str] = Field(default_factory=list)  # M2-02 걱정하시던 것


class SpeechStyle(ContextModel):
    """말투 문항 (C-01·C-02, M3 계열). 서술 한 줄로 뭉치지 않고 문항별로 둔다."""

    length: str | None = None  # C-01 말의 길이
    manner: str | None = None  # C-02 이야기하는 방식
    when_angry: str | None = None  # M3-03 화가 나면
    interjections: list[str] = Field(default_factory=list)  # M3-04 감탄사
    dialect: str | None = None  # M3-05 사투리·억양
    dialect_example: str | None = None  # M3-05 예시 문장
    avoid_styles: list[str] = Field(default_factory=list)  # M3-06 쓰면 어색할 말투
    sentence_endings: list[str] = Field(default_factory=list)  # M3-07 말끝
    call_open: str | None = None  # M3-01 전화 첫마디
    call_close: str | None = None  # M3-02 전화 끝말
    summary: str | None = None  # 위 값을 사람이 읽는 한 줄로 (선택)


class SpeechExample(ContextModel):
    """실제 표현 (C-09, M4-04, M4-06). 추정은 인용하지 않고 방향으로만 쓴다."""

    text: str
    situation: str | None = None  # C-09 언제 쓰는 말인가요
    listener: str | None = None  # C-09 누구에게
    inferred: bool = False  # 실제로 들은 말이 아니면 True
    source_question: str | None = None


class Personality(ContextModel):
    """성격·가치관 (C-03~C-07, M4-01·M4-02·M4-05, T2-03·T2-04)."""

    one_line: str | None = None  # C-05
    introduction: str | None = None  # C-06
    traits: list[str] = Field(default_factory=list)  # M4-01
    values: list[str] = Field(default_factory=list)  # M4-02
    affection_style: list[str] = Field(default_factory=list)  # C-07
    comfort_style: str | None = None  # C-03 힘들어할 때 태도
    advice_style: list[str] = Field(default_factory=list)  # C-04 고민을 들으면
    conflict_style: str | None = None  # M4-05 갈등이 생기면
    remembered_as: str | None = None  # T2-03 어떤 사람으로 기억되고 싶은지
    wish_listener_feels: str | None = None  # T2-04 목소리를 들을 때 느꼈으면 하는 것


class TabooTopic(ContextModel):
    """주제별 등급 (M9-01) 과 금기 (C-10, M9-03, T2-02, M5-07)."""

    topic: str
    grade: str | None = None  # 자유 / 물으면만 / 짧게 넘김 / 금지
    note: str | None = None  # 단서
    instead: str | None = None  # C-10 대신 어떻게 하면 좋을까요
    source_question: str | None = None


class IntakeContext(ContextModel):
    """Persona Intake 의 A(프롬프트) 문항이 들어오는 자리.

    문항 분류(docs/Persona_Intake_문항분류.md) 기준으로 A 와 A·B 50문항을
    빠짐없이 담는다. B 문항은 여기가 아니라 기억(memory segments) 으로 간다.

    speech_style·personality·taboo_topics 는 예전 계약이 문자열/문자열 목록이라
    그 형태로도 들어올 수 있다. 조립기가 둘 다 읽는다.
    """

    basic_profile: dict = Field(default_factory=dict)  # P1-02·04·05, T1-02, C-05
    family_map: list[FamilyNote] = Field(default_factory=list)  # M2-01, M2-02
    speech_style: SpeechStyle | str | None = None  # C-01·C-02, M3 계열
    speech_examples: list[SpeechExample] = Field(default_factory=list)  # C-09, M4-04·06
    personality: Personality | str | None = None  # C-03~C-07, M4-01·02·05, T2-03·04
    situational_reactions: list[SituationalReaction] = Field(default_factory=list)  # M8
    taboo_topics: list[TabooTopic] | list[str] = Field(default_factory=list)  # C-10, M9
    visitor_tone: str | None = None  # P1-07C 방문자를 대하는 느낌
    visitor_context: str | None = None  # P1-07D 왜 만들었고 방문자가 뭘 궁금해할지
    audience: str | None = None  # P1-07 누가 대화하는지
    memory_cards: list[dict] = Field(default_factory=list)  # 예전 계약 호환
    stt_hints: SttHints | None = None
