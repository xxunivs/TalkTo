"""페르소나 명세: 생성기 -> 렌더러 중간 형태 (칸마다 출처 source)"""

from __future__ import annotations

from pydantic import BaseModel, Field

DEATH_POLICIES = ("deny", "brief", "ask_only", "open")
DEATH_WORDS = ("사망", "임종", "장례", "죽음")


def is_death_topic(topic: str) -> bool:
    """사망, 임종 주제 판별 (주제 목록 대신 L1 사망 정책 모듈 담당)"""
    return any(w in topic for w in DEATH_WORDS)


class Relation(BaseModel):
    name: str
    relation: str | None = None
    calls_them: str | None = None  # 그분이 이 사람을 부르는 말
    self_ref: str | None = None  # 이 사람 앞에서 그분이 자기를 가리키는 말(할머니가, 엄마가, 내가)
    they_call: str | None = None  # 이 사람이 그분을 부르는 말
    speech_level: str | None = None  # 반말, 존댓말
    tone: str | None = None
    worries: list[str] = Field(default_factory=list)
    talks_with_persona: bool | None = None
    is_listener: bool | None = None  # 지금 페르소나와 대화하는 사람(웹앱 전송)
    is_owner: bool | None = None  # 이 대화 상대를 만든 가족(가족 공유 시 웹앱 전송)
    is_member: bool | None = None  # 가족 공유로 들어온 가족(듣는 사람 줄, 웹앱 전송)
    listener_calls: str | None = None  # 대화하는 사람이 이 사람을 부르는 말(아빠, 고모)
    deceased: bool | None = None
    note: str | None = None  # 가족이 적은 한 줄 메모
    source: str = "intake:M2-01"


class Topic(BaseModel):
    topic: str
    grade: str  # 자유, 물으면만, 짧게 넘김, 금지
    note: str | None = None
    source: str | None = None


class Situation(BaseModel):
    situation: str
    response: str
    avoid: str | None = None
    source: str | None = None


class Quote(BaseModel):
    text: str
    situation: str | None = None
    listener: str | None = None
    source: str | None = None


class Shot(BaseModel):
    user: str  # 가족이 한 말
    persona: str  # 바로 뒤 그분의 답
    source: str | None = None  # call:통화03#45


class Character(BaseModel):
    one_line: str | None = None
    introduction: str | None = None
    traits: list[str] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)
    affection_style: list[str] = Field(default_factory=list)
    comfort_style: str | None = None
    advice_style: list[str] = Field(default_factory=list)
    conflict_style: str | None = None
    remembered_as: str | None = None
    wish_listener_feels: str | None = None


class Style(BaseModel):
    length: str | None = None
    manner: str | None = None
    when_angry: str | None = None
    interjections: list[str] = Field(default_factory=list)
    dialect: str | None = None
    dialect_example: str | None = None
    avoid_styles: list[str] = Field(default_factory=list)
    sentence_endings: list[str] = Field(default_factory=list)
    call_open: str | None = None
    call_close: str | None = None  # 통화를 끝낼 때만 쓰는 말
    words: list[str] = Field(default_factory=list)  # 실제로 쓰던 어휘, 말버릇(인터뷰, 전사 근거)
    summary: str | None = None  # 전사 LLM 추출의 문장 특징 요약
    card: str | None = None  # 전사 통계 말투 카드(G3)
    quotes: list[Quote] = Field(default_factory=list)  # 설문에 적힌 실제 표현(C-09 등)
    shots: list[Shot] = Field(default_factory=list)  # 실제 통화 주고받기(G3)


class PersonaSpec(BaseModel):
    version: int = 1
    name: str
    public: bool = False
    deceased: bool = False
    # 만 19세 미만 생존 대화 상대(BE basicProfile.minor): L1 안전 절 미성년 규칙, 사망이면 False
    minor: bool = False
    period: str | None = None
    family_calls_subject: str | None = (
        None  # 가족 모두가 그분을 같은 말로 부를 때만(아니면 가족 표에 사람마다)
    )
    visitor_address: str | None = None  # 공개: 방문자를 부르는 말(P1-07B)
    visitor_tone: str | None = None
    visitor_context: str | None = None
    death_policy: str = "deny"
    death_answer: str | None = None
    death_policy_source: str | None = None
    character: Character = Field(default_factory=Character)
    situations: list[Situation] = Field(default_factory=list)
    topics: list[Topic] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    style: Style = Field(default_factory=Style)
    materials: list[str] = Field(default_factory=list)  # 쓰인 재료: intake, transcript, interview
    notes: list[str] = Field(default_factory=list)  # 생성기가 내린 판단 기록(사람 확인용)

    def add_material(self, kind: str) -> None:
        if kind not in self.materials:
            self.materials.append(kind)

    def relation_for(self, name: str) -> Relation | None:
        """이름이 같거나 한쪽이 다른 쪽을 포함하면 같은 사람 (도윤, 이도윤)"""
        name = name.strip()
        for r in self.relations:
            if r.name == name or (len(name) >= 2 and (name in r.name or r.name in name)):
                return r
        return None
