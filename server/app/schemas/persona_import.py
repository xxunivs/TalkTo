"""페르소나 가져오기 묶음(bundle)과 관리자 가져오기 요청의 모양.

손으로 만든 페르소나(AI 레포 output/<subject>/ 의 옛 프롬프트·장기기억)를 관리자 화면 '페르소나
가져오기'(/admin/import)로 운영 계정에 넣는다. 묶음은 AI 레포의 로컬 도구가 만들고(git 레포 밖에
둔다 — 가족 자료라 커밋하지 않고, 가져온 뒤 지운다), 관리자가 화면에서 올리면 이 서버가 받는다.
처리는 app/services/persona_import.py, API 는 app/api/v1/admin_import.py.

묶음 JSON (version 1). 모르는 칸은 받지 않는다(source 만 출처 기록이라 모르는 칸을 버린다)::

    {
      "format": "talkto-persona-bundle",
      "version": 1,
      "source": {
        "subject": "<output 폴더 이름>",
        "promptSha256": "<prompt.legacy 의 sha256(UTF-8, 줄바꿈 LF), 소문자 hex 64자>",
        "rulesSha256": "<선택: prompt.rules 의 sha256(같은 방식). 있으면 맞는지 본다>",
        "builtAt": "<만든 때, ISO 8601>",
        "promptRev": "<선택: disk 또는 git 커밋>",
        "promptBlob": "<선택: 원본 파일 git hash-object>",
        "toolCommit": "<선택: 묶음을 만든 AI 레포 HEAD>"
      },
      "persona": {"name": "…", "relation": "할머니", "addressTerm": "할머니", "status": "deceased"},
      "prompt": {
        "legacy": "<옛 프롬프트 원문. 한 글자도 바꾸지 않는다(줄바꿈만 LF)>",
        "rules": "<선택: 지금 L1 공통 규칙(layered, 비공개·고인·가족 있음, family_list='아래 가족
                   목록'). 사망 규칙 자리는 {{death_rule}} 한 줄로 비워 둔다>",
        "deathRules": {"deny": "…", "brief": "…{{death_answer}}…", "ask_only": "…{{death_answer}}…",
                       "open": "…"},
        "bridge": "<선택: 짧은 연결 문단. '지금 대화하는 사람' 자리는 {{listener_line}} 한 줄>"
      },
      "memories": [
        {"legacyId": "<옛 id>", "category": "<옛 분류>", "title": "<옛 제목, 표시 없이>",
         "content": "<옛 본문>", "sensitive": false}
      ],
      "probeEmbeddings": [{"legacyId": "<memories 의 id>", "embedding": [0.01, …]}],
      "voice": {"voiceId": "fish_<Fish 모델 id>"}
    }

- guarded 로 가져오려면 rules 에 지금 L1 의 안전 규칙(109 안내·대화 설정 비공개·조언 금지·
  마크다운 금지·'(물으면만)' 기억 규칙)이 다 있어야 한다(persona_import.GUARDRAILS).
  오래됐거나 손으로 줄인 rules 는 400 이다.
- rules·deathRules·bridge 는 guarded 로 가져올 때만 쓴다(verbatim 은 legacy 만). rules 의
  {{death_rule}} 은 관리자가 고른 사망 정책의 deathRules 한 줄로, {{death_answer}} 는 관리자가
  적은 한마디로 채운다(비우면 그 예시 구절을 빼고 지어내지 않는다). bridge 의 {{listener_line}} 은
  myName 이 있으면 '지금 대화하는 사람' 한 줄로, 없으면 빈 줄로 바꾼다(옛 프롬프트의 확인 흐름).
- sensitive 표시(' (물으면만)')는 이 서버가 제목에 붙인다. 묶음 title 에는 붙이지 않는다.
- probeEmbeddings: 옛 로컬 벡터 몇 개(5개까지). 그 기억의 "title: content"(원래 제목)를 지금
  AI 서버로 임베딩해 견준다. 같은 임베딩 모델인지 보는 참고값일 뿐이다(저장하지 않는다).
- 임베딩은 묶음에 싣지 않는다. 이 서버가 본문(content)만 지금 AI 서버로 다시 만든다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from app.schemas.common import APIModel

BUNDLE_FORMAT = "talkto-persona-bundle"
BUNDLE_VERSION = 1
DEATH_POLICIES = ("deny", "brief", "ask_only", "open")
# 묶음 글에 남겨 두는 자리표시(이 서버가 채운다)
DEATH_RULE_SLOT = "{{death_rule}}"
DEATH_ANSWER_SLOT = "{{death_answer}}"
LISTENER_SLOT = "{{listener_line}}"
# AI 서버 /v1/persona/responses 의 지침 상한(AI 레포 infra/schema/persona.py)
MAX_INSTRUCTIONS = 50_000
MAX_MEMORIES = 200
MAX_PROBES = 5


class _Strict(APIModel):
    model_config = ConfigDict(extra="forbid")


class BundleSource(APIModel):
    """출처 기록(내용 없음). 모르는 칸은 버린다."""

    model_config = ConfigDict(extra="ignore")

    subject: str = Field(min_length=1, max_length=100)
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # 선택: prompt.rules 의 해시. 있으면 rules 가 만든 때 그대로인지 본다.
    rules_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    built_at: datetime
    prompt_rev: str | None = Field(default=None, max_length=64)
    prompt_blob: str | None = Field(default=None, max_length=64)
    tool_commit: str | None = Field(default=None, max_length=64)


class BundlePersona(_Strict):
    name: str = Field(min_length=1, max_length=40)
    relation: str = Field(min_length=1, max_length=40)
    address_term: str | None = Field(default=None, max_length=30)
    # 지금은 돌아가신 분만 가져온다(안전 규칙·목소리 확인 문서가 고인 기준)
    status: Literal["deceased"]


class BundlePrompt(_Strict):
    legacy: str = Field(min_length=1, max_length=MAX_INSTRUCTIONS)
    rules: str | None = Field(default=None, max_length=30_000)
    death_rules: dict[Literal["deny", "brief", "ask_only", "open"], str] | None = None
    bridge: str | None = Field(default=None, max_length=3_000)

    @model_validator(mode="after")
    def _short_death_rules(self):
        for line in (self.death_rules or {}).values():
            if len(line) > 2_000:
                raise ValueError("deathRules line is too long")
        return self


class BundleMemory(_Strict):
    legacy_id: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=30)
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=5_000)
    sensitive: bool = False


class ProbeEmbedding(_Strict):
    legacy_id: str = Field(min_length=1, max_length=100)
    embedding: list[float] = Field(min_length=1, max_length=4_096)


class BundleVoice(_Strict):
    voice_id: str = Field(pattern=r"^fish_[A-Za-z0-9]{8,64}$")


class PersonaBundle(_Strict):
    format: Literal["talkto-persona-bundle"]
    version: Literal[1]
    source: BundleSource
    persona: BundlePersona
    prompt: BundlePrompt
    memories: list[BundleMemory] = Field(min_length=1, max_length=MAX_MEMORIES)
    probe_embeddings: list[ProbeEmbedding] = Field(default_factory=list, max_length=MAX_PROBES)
    voice: BundleVoice | None = None

    @model_validator(mode="after")
    def _ids(self):
        ids = [m.legacy_id for m in self.memories]
        if len(set(ids)) != len(ids):
            raise ValueError("memories[].legacyId must be unique")
        unknown = [p.legacy_id for p in self.probe_embeddings if p.legacy_id not in set(ids)]
        if unknown:
            raise ValueError("probeEmbeddings[].legacyId must name a memory")
        return self


# ────────────────────────── 관리자 요청 ──────────────────────────


class ImportPromptOptions(_Strict):
    """지침을 어떻게 짤지. 묶음을 다시 만들지 않고 화면에서 바꾼다."""

    # guarded = L1 공통 규칙 + 연결 문단 + 옛 본문 그대로 / verbatim = 옛 본문만
    mode: Literal["guarded", "verbatim"] = "guarded"
    death_policy: Literal["deny", "brief", "ask_only", "open"] = "ask_only"
    # 사인·경위를 물을 때의 한마디. 비우면 넣지 않는다(지어내지 않는다).
    death_answer: str | None = Field(default=None, max_length=80)


class ImportOverrides(_Strict):
    """기본 정보. 비운 칸은 묶음 값을 쓴다(myName·myRelation·calledMe 는 묶음에 없다).

    받는 길이는 만들기 흐름(PersonaCreate)과 같다. 저장하는 길이(이름·관계·내 이름 20자, 호칭·나를
    부르던 말 15자)를 넘으면 자르지 않고 400 이다(persona_import.BASICS_LIMITS).
    """

    name: str | None = Field(default=None, max_length=40)
    relation: str | None = Field(default=None, max_length=40)
    address_term: str | None = Field(default=None, max_length=30)
    my_name: str | None = Field(default=None, max_length=40)
    my_relation: str | None = Field(default=None, max_length=40)
    called_me: str | None = Field(default=None, max_length=30)


class ImportVoiceOptions(_Strict):
    # after_owner_pledge = 주인이 앱에서 '돌아가신 분 — 유족 확인'을 하면 붙인다(D4).
    # 관리자가 대신 확인하는 길은 두지 않는다.
    attach: Literal["none", "after_owner_pledge"] = "after_owner_pledge"
    # 미리 들어보기: 운영 AI 서버로 짧은 한 문장을 합성해 본다(이 모델을 쓸 수 있는지)
    probe: bool = False


class PersonaImportRequest(_Strict):
    target_user_id: str = Field(min_length=1, max_length=36)
    # 이 회사로 이은 계정만 받는다(관리자 계정을 잘못 고르지 않게). null 이면 보지 않는다.
    require_provider: Literal["kakao", "google"] | None = "kakao"
    # true 면 아무것도 쓰지 않고 미리보기만 돌려준다
    dry_run: bool = True
    bundle: PersonaBundle
    prompt: ImportPromptOptions = Field(default_factory=ImportPromptOptions)
    overrides: ImportOverrides = Field(default_factory=ImportOverrides)
    voice: ImportVoiceOptions = Field(default_factory=ImportVoiceOptions)
    # 기억이 실제로 찾아지는지 볼 시험 질문(G1: 차원이 어긋나면 하나도 안 찾힌다)
    check_query: str = Field(default="고향 이야기 해 주세요", min_length=1, max_length=200)
