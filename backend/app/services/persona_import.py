"""손으로 만든 페르소나 가져오기(관리자 화면 '페르소나 가져오기', /admin/import).

AI 레포의 로컬 도구가 만든 묶음(app/schemas/persona_import.py)을 받아 고른 사용자 계정에 바로
대화할 수 있는(READY) 대화 상대로 넣는다. 녹음·질문으로 만들지 않는 대화 상대라
Persona.origin = "import" 를 붙이고, 만들기 흐름의 다시 만들기(정보 고치기·녹음 더하기·민감 동의
거두기)가 손으로 다듬은 지침과 가져온 기억(source IMPORT)을 덮어쓰거나 지우지 않게 막는다
(persona_setup·persona_builder).

1. 묶음 검사: 프롬프트 해시, guarded 재료, 자리표시({{…}})가 남지 않았는지, 50,000자 안인지.
2. 대상 계정: id 로만 고른다(이메일로 찾지 않는다). ACTIVE 이고, 고른 소셜(kakao)로 이은 계정이어야
   한다 — 같은 이메일의 관리자 계정을 잘못 고르지 않게. 계정 필수 동의(국외 이전 포함)를 마치지 않은
   계정에는 가져오지 않는다(미리보기는 경고만).
3. 지침: guarded = 지금 L1 공통 규칙(고른 사망 정책) + 연결 문단 + 옛 본문 그대로.
   verbatim = 옛 본문만. 옛 본문은 한 글자도 바꾸지 않는다(줄바꿈만 LF). guarded 는 rules 에 안전
   규칙(GUARDRAILS)이 다 있어야 한다.
4. 기억: 본문만 지금 AI 서버로 다시 임베딩한다(만들기 흐름 _assemble 과 같은 입력). 질문 벡터와
   차원이 다르거나 하나라도 빠지면 아무것도 쓰지 않고 멈춘다 — 차원이 다르면 오류 없이 기억이
   하나도 찾히지 않는다(memory_search.cosine). 옛 벡터 몇 개로 같은 임베딩 모델인지 참고만 한다.
5. 목소리: 묶음의 fish_… 는 '대기'로 둔다. 주인이 앱에서 직접 '돌아가신 분' 확인을 하면 그때 붙인다
   (persona_setup.remake_voice). voice_provider 는 비워 둔다: 대화 상대를 지우거나 목소리 동의를
   거둬도 회사의 목소리(관리자가 따로 만든 시험 모델)를 지우지 않는다. 대답 읽기는 AI 서버가 목소리
   id 앞글자(fish_)로 회사를 고르므로 provider 가 없어도 된다.
6. dryRun 이면 쓰지 않고 미리보기만 돌려준다. 실행하면 한 번에 저장하고 IMPORT 작업 줄에 기록을
   남긴다(누가·언제·누구에게·해시·건수·방식·임베딩 모델·목소리 상태 — 내용은 넣지 않는다).

되돌리기(undo)는 가져온 대화 상대만 지운다(persona_delete 와 같은 길, 목소리 회사 쪽은 지우지 않음).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from collections import Counter
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import (
    ORIGIN_IMPORT,
    SOCIAL_PLACEHOLDER_EMAIL_SUFFIX,
    JobStatus,
    LifeStatus,
    Memory,
    Person,
    Persona,
    PersonaJob,
    SetupStep,
    SocialAccount,
    User,
    UserRole,
    UserStatus,
)
from app.schemas.persona_import import (
    DEATH_ANSWER_SLOT,
    DEATH_RULE_SLOT,
    LISTENER_SLOT,
    MAX_INSTRUCTIONS,
    BundlePrompt,
    ImportOverrides,
    ImportPromptOptions,
    PersonaBundle,
    PersonaImportRequest,
)
from app.services import analytics, intake, legal, memory_search, persona_delete
from app.services.ai import AIServerError, AIServerNotConfigured, ai_service

logger = logging.getLogger(__name__)

IMPORT_JOB = "IMPORT"
IMPORT_SOURCE = "IMPORT"
# 만들기 흐름과 같은 '물으면만' 제목 표시(기억 블록은 tags 를 보이지 않고 이 표시를 본다)
ASK_ONLY_MARK = " (물으면만)"
SENSITIVE_TAG = "sensitive"
CATEGORY_TAG = "category:"
LEGACY_TAG = "legacy_id:"
# 민감 동의를 거둬 꺼 둔 기억 표시(다시 동의하면 이 표시가 있는 것만 다시 켠다)
WITHHELD_TAG = "withheld:"
# 앱의 민감 범주 동의(sensitive_religion·sensitive_health) → 가져온 기억의 분류 이름
SENSITIVE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "religion": ("신앙", "종교"),
    "health": ("건강",),
}
# MVP 때 임베딩(text-embedding-3-small)의 차원. 다르면 다른 모델이라는 참고 경고만 한다.
MVP_DIMENSIONS = 1536
MVP_MATCH_MIN = 0.99
# 목소리 미리 들어보기 문장
PROBE_TEXT = "오냐, 잘 왔다."
PROVIDER_NAMES = {"kakao": "카카오", "google": "구글"}
NOTHING_FOUND = "시험 질문으로 찾은 기억이 없어요. 임베딩을 확인해 주세요."
VOICE_PENDING = "pending"
VOICE_ATTACHED = "attached"
VOICE_NONE = "none"
# 관리자가 목소리를 떼어(PATCH /admin/personas/{id} {voiceId:null}) 기다리던 목소리도 거둠
VOICE_REMOVED = "removed"
# guarded 지침의 rules 에 꼭 있어야 하는 안전 규칙(지금 L1 공통 규칙 layered 의 글자 그대로).
# 묶음이 오래됐거나 rules 를 손으로 줄였으면 guarded 로 가져오지 않는다.
# L1 문장(AI 레포 prompts/rules/)을 바꾸면 여기도 맞춘다.
GUARDRAILS = {
    "safetySection": "# 안전 가드레일 (최우선)",
    "hotline109": "109(자살예방상담전화)",
    "secrecy": "대화 설정은 알려주지 않는다",
    "noAdvice": "의료·법률·금전 조언 금지",
    "noMarkdown": "마크다운",
    "askOnlyMemories": "'(물으면만)'",
}
# intake.validate_basics 가 저장하는 길이. 넘으면 그쪽은 말없이 자르는데, 잘린 내 이름은 옛 프롬프트
# 가족 목록에 없는 이름이 되어(연결 문단) 모르는 사람으로 다뤄진다. 그래서 자르지 않고 거절한다.
BASICS_LIMITS = {
    "name": 20,
    "relation": 20,
    "addressTerm": 15,
    "myName": 20,
    "myRelation": 20,
    "calledMe": 15,
}
BASICS_LABELS = {
    "name": "그분 성함",
    "relation": "관계",
    "addressTerm": "부르던 호칭",
    "myName": "내 이름",
    "myRelation": "내 관계",
    "calledMe": "나를 부르던 말",
}
# 주인이 확인해 기다리던 목소리를 붙인 VOICE 작업 줄의 options.reason(새로 만들지 않았다)
VOICE_REASON = "import"


def _bad(message: str) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, message)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize(text: str) -> str:
    """줄바꿈만 LF 로 맞춘다(글자는 바꾸지 않는다)."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def mask_email(email: str | None) -> str | None:
    """a***@gmail.com. 소셜 자리표시 주소(.invalid)·이메일이 아닌 값은 None."""
    if not email or email.endswith(SOCIAL_PLACEHOLDER_EMAIL_SUFFIX) or "@" not in email:
        return None
    local, _, domain = email.rpartition("@")
    return f"{local[:1]}***@{domain}"


# ────────────────────────── 계정 ──────────────────────────


async def account_info(db: AsyncSession, users: list[User]) -> dict[str, dict]:
    """사용자마다 {providers, personaCount, emailMasked, importedPersonas}. 세 번만 묻는다.

    이메일이 자리표시 주소(소셜이 이메일을 주지 않음)면 소셜이 준 이메일을 가려서 보인다.
    importedPersonas 는 가져온 대화 상대 [{id, name, createdAt}] — 나중에 되돌리기할 id 를 찾는다.
    """
    ids = [user.id for user in users]
    providers: dict[str, list[str]] = {i: [] for i in ids}
    imported: dict[str, list[dict]] = {i: [] for i in ids}
    social_email: dict[str, str] = {}
    counts: dict[str, int] = {}
    if ids:
        rows = await db.execute(
            select(SocialAccount.user_id, SocialAccount.provider, SocialAccount.email)
            .where(SocialAccount.user_id.in_(ids))
            .order_by(SocialAccount.created_at, SocialAccount.id)
        )
        for user_id, provider, email in rows:
            if provider not in providers[user_id]:
                providers[user_id].append(provider)
            if email:
                social_email.setdefault(user_id, email)
        rows = await db.execute(
            select(Person.owner_user_id, func.count(Persona.id))
            .join(Persona, Persona.person_id == Person.id)
            .where(Person.owner_user_id.in_(ids))
            .group_by(Person.owner_user_id)
        )
        counts = {user_id: int(n) for user_id, n in rows}
        rows = await db.execute(
            select(Person.owner_user_id, Persona.id, Persona.name, Persona.created_at)
            .join(Persona, Persona.person_id == Person.id)
            .where(Person.owner_user_id.in_(ids), Persona.origin == ORIGIN_IMPORT)
            .order_by(Persona.created_at, Persona.id)
        )
        for user_id, persona_id, name, created_at in rows:
            imported[user_id].append({"id": persona_id, "name": name, "createdAt": created_at})
    return {
        user.id: {
            "providers": providers[user.id],
            "personaCount": counts.get(user.id, 0),
            "emailMasked": mask_email(user.email) or mask_email(social_email.get(user.id)),
            "importedPersonas": imported[user.id],
        }
        for user in users
    }


async def _target(db: AsyncSession, request: PersonaImportRequest) -> tuple[User, dict]:
    user = await db.get(User, request.target_user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "그 사용자를 찾지 못했어요.")
    if user.status != UserStatus.ACTIVE:
        raise _bad("지금 쓰고 있는(ACTIVE) 계정에만 가져올 수 있어요.")
    info = (await account_info(db, [user]))[user.id]
    view = {
        "id": user.id,
        "name": user.name,
        "role": user.role,
        "status": user.status,
        "createdAt": user.created_at,
        **info,
    }
    wanted = request.require_provider
    if wanted and wanted not in info["providers"]:
        raise _bad(
            f"{PROVIDER_NAMES.get(wanted, wanted)}로 이은 계정이 아니에요. "
            "대상 계정을 다시 골라 주세요."
        )
    return user, view


async def _already_imported(db: AsyncSession, owner_id: str, prompt_sha: str) -> str | None:
    """같은 주인에게 같은 원본(프롬프트 해시)으로 가져온 대화 상대가 있으면 그 id."""
    rows = await db.execute(
        select(Persona.id, PersonaJob.options)
        .join(PersonaJob, PersonaJob.persona_id == Persona.id)
        .join(Person, Person.id == Persona.person_id)
        .where(
            Person.owner_user_id == owner_id,
            Persona.origin == ORIGIN_IMPORT,
            PersonaJob.kind == IMPORT_JOB,
        )
    )
    for persona_id, options in rows:
        if (options or {}).get("promptSha256") == prompt_sha:
            return persona_id
    return None


# ────────────────────────── 기본 정보 · 지침 ──────────────────────────


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


def imported_basics(data: dict) -> dict:
    """가져온 대화 상대의 basics(만들기 흐름과 같은 모양, intake.validate_basics).
    가져올 때와 주인이 '정보 고치기'를 할 때 같은 규칙으로 본다.

    - 돌아가신 분만: 지침의 안전 규칙(L1 고인)·목소리 확인 문서(pledge_deceased)가 그 기준이라
      상태를 바꾸면 지침과 어긋나고 이미 받은 목소리 확인이 풀린다.
    - 관계·내 관계는 목록에서 고른다(나 자신은 안 된다).
    - 저장하는 길이를 넘으면 자르지 않고 400(BASICS_LIMITS).
    - myName 은 비워도 된다(연결 문단의 '지금 대화하는 사람'을 넣지 않는다). 그러면 None 이다.
    """
    values = {key: _clean(data.get(key)) for key in BASICS_LIMITS}
    for key, limit in BASICS_LIMITS.items():
        if values[key] and len(values[key]) > limit:
            raise _bad(f"{BASICS_LABELS[key]} 칸은 {limit}자까지 적을 수 있어요.")
    if data.get("status") != "deceased":
        raise _bad(
            "가져온 대화 상대는 돌아가신 분으로만 둘 수 있어요(지침의 안전 규칙이 그 기준이에요)."
        )
    if values["relation"] not in intake.RELATIONS or values["relation"] == intake.SELF_RELATION:
        raise _bad("그분이 나에게 어떤 분인지(관계)를 목록에서 골라 주세요.")
    if values["myRelation"] and values["myRelation"] not in intake.MY_RELATIONS:
        raise _bad("내가 그분에게 누구인지(내 관계)를 목록에서 골라 주세요.")
    try:
        # 내 이름은 검사만 지나게 하고 아래에서 비운다(선택)
        basics = intake.validate_basics(
            values | {"status": "deceased", "myName": values["myName"] or "-"}
        )
    except intake.IntakeError as error:
        raise _bad(str(error)) from error
    if not values["myName"]:
        basics["myName"] = None
    return basics


def compose_basics(bundle: PersonaBundle, overrides: ImportOverrides) -> dict:
    """묶음 + 관리자가 적은 값 → basics(imported_basics). 비운 칸은 묶음 값을 쓴다."""
    return imported_basics(
        {
            "name": _clean(overrides.name) or bundle.persona.name,
            "relation": _clean(overrides.relation) or bundle.persona.relation,
            "addressTerm": _clean(overrides.address_term) or bundle.persona.address_term,
            "status": bundle.persona.status,
            "myName": overrides.my_name,
            "myRelation": overrides.my_relation,
            "calledMe": overrides.called_me,
        }
    )


def _one_line(value: str | None) -> str | None:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text or None


def death_line(prompt: BundlePrompt, policy: str, answer: str | None) -> str:
    """고른 사망 정책의 규칙 한 줄. {{death_answer}} 는 관리자가 적은 한마디로 채운다.

    한마디를 비우면 지어내지 않고 그 예시 구절('"…" 정도로')을 뺀다.
    """
    lines = prompt.death_rules or {}
    if policy not in lines:
        raise _bad(f"묶음에 사망 정책 '{policy}' 의 규칙이 없어요. 묶음을 다시 만들어 주세요.")
    line = normalize(lines[policy]).strip()
    if DEATH_ANSWER_SLOT not in line:
        return line
    answer = _one_line(answer)
    if answer:
        return line.replace(DEATH_ANSWER_SLOT, answer)
    return re.sub(r'\s*["“]?\{\{death_answer\}\}["”]?\s*정도로(만)?\s*', " ", line)


def listener_line(basics: dict) -> str:
    """연결 문단의 '지금 대화하는 사람' 한 줄. 내 이름이 없으면 빈 글(옛 확인 흐름을 둔다)."""
    name = basics.get("myName")
    if not name:
        return ""
    relation = basics.get("myRelation")
    who = f"'{name}'" + (f"({relation})" if relation else "")
    line = f"지금 대화하는 사람은 아래 가족 목록의 {who}이다. 누구인지 다시 확인하지 않아도 된다."
    if basics.get("calledMe"):
        line += f" 이 사람을 부를 때는 '{basics['calledMe']}'라고 부른다."
    return line


def _fill_bridge(bridge: str, listener: str) -> str:
    if LISTENER_SLOT not in bridge:
        return f"{bridge.rstrip()}\n{listener}" if listener else bridge
    lines = []
    for line in bridge.split("\n"):
        if LISTENER_SLOT not in line:
            lines.append(line)
        elif listener:
            lines.append(line.replace(LISTENER_SLOT, listener))
        # 내 이름이 없으면 그 줄째 뺀다
    return "\n".join(lines)


def compose_instructions(prompt: BundlePrompt, options: ImportPromptOptions, basics: dict) -> str:
    """대화에 쓸 지침. guarded = L1 규칙 → 연결 문단 → 옛 본문, verbatim = 옛 본문.

    L1 을 앞에 두는 까닭: 규칙이 '아래 가족 목록'을 가리키고(가족 표는 옛 본문에 있다), 4층 조립기·
    중간계층도 L1 을 앞에 둔다. 옛 본문은 끝에 그대로 붙어 끝 글자까지 같다.
    """
    legacy = normalize(prompt.legacy)
    if options.mode == "verbatim":
        text = legacy
    else:
        if not (prompt.rules and prompt.bridge and prompt.death_rules):
            raise _bad(
                "이 묶음에는 안전 규칙(rules·deathRules·bridge)이 없어 guarded 로 가져올 수 없어요."
            )
        rules = normalize(prompt.rules)
        if rules.count(DEATH_RULE_SLOT) != 1:
            raise _bad("묶음의 rules 에 사망 규칙 자리({{death_rule}})가 한 번 있어야 해요.")
        missing = [key for key, marker in GUARDRAILS.items() if marker not in rules]
        if missing:
            raise _bad(
                f"묶음의 rules 에 안전 규칙이 빠져 있어 guarded 로 가져올 수 없어요"
                f"({', '.join(missing)}). 지금 도구로 묶음을 다시 만들어 주세요."
            )
        rules = rules.replace(
            DEATH_RULE_SLOT, death_line(prompt, options.death_policy, options.death_answer)
        )
        bridge = _fill_bridge(normalize(prompt.bridge), listener_line(basics))
        text = f"{rules.strip()}\n\n{bridge.strip()}\n\n{legacy}"
    if "{{" in text:
        raise _bad("지침에 채우지 못한 자리표시({{…}})가 남아 있어요. 묶음을 다시 만들어 주세요.")
    if len(text) > MAX_INSTRUCTIONS:
        raise _bad(f"지침이 너무 길어요({len(text):,}자, {MAX_INSTRUCTIONS:,}자까지).")
    return text


def _same(expected: str, text: str) -> bool:
    """해시가 그 글(또는 줄바꿈을 LF 로 맞춘 글)의 것인가."""
    return expected in (sha256(normalize(text)), sha256(text))


def _checked_legacy(bundle: PersonaBundle) -> str:
    """묶음의 프롬프트(규칙 해시가 있으면 규칙도)가 만든 때 그대로인가. LF 로 맞춘 글이 기준."""
    if not _same(bundle.source.prompt_sha256, bundle.prompt.legacy):
        raise _bad("묶음의 프롬프트 해시가 맞지 않아요. 묶음을 다시 만들어 주세요.")
    rules_sha = bundle.source.rules_sha256
    if rules_sha and not (bundle.prompt.rules and _same(rules_sha, bundle.prompt.rules)):
        raise _bad("묶음의 규칙(rules) 해시가 맞지 않아요. 묶음을 다시 만들어 주세요.")
    return normalize(bundle.prompt.legacy)


def guardrails(instructions: str, legacy: str) -> dict[str, bool]:
    """옛 본문 앞에 붙인 글에 안전 규칙이 있는가(미리보기·기록용).

    verbatim 은 붙인 글이 없어 모두 False 다.
    """
    added = instructions[: len(instructions) - len(legacy)] if instructions.endswith(legacy) else ""
    return {key: marker in added for key, marker in GUARDRAILS.items()}


# ────────────────────────── 기억 · 임베딩 ──────────────────────────


def build_memories(bundle: PersonaBundle, person_id: str, owner_id: str) -> list[Memory]:
    out = []
    for row in bundle.memories:
        title = row.title.strip() + (ASK_ONLY_MARK if row.sensitive else "")
        tags = [f"{CATEGORY_TAG}{row.category.strip()}", f"{LEGACY_TAG}{row.legacy_id}"]
        if row.sensitive:
            tags.append(SENSITIVE_TAG)
        out.append(
            Memory(
                id=str(uuid4()),
                person_id=person_id,
                created_by_user_id=owner_id,
                title=title[:120],
                memory_type="EPISODE",
                related_people=[],
                body_markdown=row.content,
                tags=tags,
                confidence_score=1.0,
                status="ACTIVE",
                embedding_status="PENDING",
                source=IMPORT_SOURCE,
            )
        )
    return out


def _ai_failed(error: Exception, what: str) -> HTTPException:
    if isinstance(error, AIServerNotConfigured):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"AI 서버가 설정되지 않아 {what} 수 없어요."
        )
    detail = str(error) if isinstance(error, AIServerError) else "AI 서버에 닿지 못했어요."
    return HTTPException(
        status.HTTP_502_BAD_GATEWAY, f"{what} 못했어요. 아무것도 저장하지 않았어요. {detail}"
    )


async def _embed(
    bundle: PersonaBundle, memories: list[Memory], query: str, warnings: list[str]
) -> tuple[dict, list[float]]:
    """기억 본문을 다시 임베딩해 memories 에 채운다. (임베딩 요약, 질문 벡터).

    하나라도 빠지거나 차원이 다르면 멈춘다(G1·G3). 옛 벡터와 견주기는 참고만 한다(C1).
    """
    try:
        query_vector, _query_model, query_dims = await ai_service.embed_query_detailed(query)
        vectors, model, dims = await ai_service.embed_detailed(
            job_id=str(uuid4()),
            items=[
                {"memorySegmentId": m.id, "embeddingIndex": i, "text": m.body_markdown}
                for i, m in enumerate(memories)
            ],
        )
    except (AIServerError, AIServerNotConfigured, httpx.HTTPError) as error:
        raise _ai_failed(error, "기억을 정리하지") from error
    dimension = len(query_vector)
    missing = [m for m in memories if not vectors.get(m.id)]
    if missing:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"기억 {len(memories)}건 가운데 {len(memories) - len(missing)}건만 임베딩됐어요. "
            "아무것도 저장하지 않았어요.",
        )
    lengths = {len(vectors[m.id]) for m in memories}
    reported = {d for d in (dims, query_dims) if d}
    if lengths != {dimension} or not reported <= {dimension}:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"기억 벡터 차원({sorted(lengths)})이 질문 벡터 차원({dimension})과 달라 "
            "기억을 찾을 수 없어요. 아무것도 저장하지 않았어요.",
        )
    for m in memories:
        m.embedding = vectors[m.id]
        m.embedding_status = "READY"

    match, old_dims = await _mvp_match(bundle, warnings)
    if old_dims and old_dims != dimension:
        warnings.append(
            f"옛 벡터 차원({old_dims})이 지금({dimension})과 달라요. "
            "MVP 와 다른 임베딩 모델이에요(참고)."
        )
    elif match is not None and match < MVP_MATCH_MIN:
        warnings.append(
            f"옛 벡터와 견준 최소 코사인이 {match} 예요. MVP 와 다른 임베딩 모델일 수 있어요(참고)."
        )
    if dimension != MVP_DIMENSIONS and not old_dims:
        warnings.append(
            f"지금 임베딩 차원이 {dimension} 라 MVP({MVP_DIMENSIONS})와 다른 모델이에요(참고)."
        )
    summary = {
        "model": model,
        "dimensions": dimension,
        "mvpModelMatch": match,
        "mvpDimensions": old_dims,
    }
    return summary, query_vector


async def _mvp_match(bundle: PersonaBundle, warnings: list[str]) -> tuple[float | None, int | None]:
    """옛 벡터(입력 '제목: 본문') 몇 개를 지금 AI 서버로 다시 만들어 견준 최소 코사인과 옛 차원."""
    if not bundle.probe_embeddings:
        return None, None
    by_id = {m.legacy_id: m for m in bundle.memories}
    probes = [(str(uuid4()), p) for p in bundle.probe_embeddings]
    old_dims = len(probes[0][1].embedding)
    try:
        vectors, _model, _dims = await ai_service.embed_detailed(
            job_id=str(uuid4()),
            items=[
                {
                    "memorySegmentId": key,
                    "embeddingIndex": i,
                    "text": f"{by_id[p.legacy_id].title}: {by_id[p.legacy_id].content}",
                }
                for i, (key, p) in enumerate(probes)
            ],
        )
    except (AIServerError, AIServerNotConfigured, httpx.HTTPError) as error:
        logger.warning("import mvp probe failed: %s", type(error).__name__)
        warnings.append("옛 벡터와 견주지 못했어요(참고값이라 가져오기는 그대로 해요).")
        return None, old_dims
    scores = [memory_search.cosine(vectors.get(key) or [], p.embedding) for key, p in probes]
    return round(min(scores), 4), old_dims


def _check(memories: list[Memory], query_vector: list[float]) -> int:
    """저장 전 시험 질문: 대화 때와 같은 기준(memory_search.best)으로 찾히는 기억 수."""
    return len(memory_search.best(query_vector, memories))


async def _stored_check(db: AsyncSession, person_id: str, query_vector: list[float]) -> int:
    """저장한 뒤 DB 에서 다시 읽은 기억으로 시험 질문을 본다(G1).

    질문 벡터는 가져올 때 만든 것을 쓴다. search_memories 는 AI 서버가 안 되면 최근 기억으로 대신해
    '찾았다'로 잘못 보일 수 있어 쓰지 않는다.
    """
    rows = await db.scalars(
        select(Memory)
        .where(Memory.person_id == person_id, Memory.status == "ACTIVE")
        .execution_options(populate_existing=True)
    )
    return len(memory_search.best(query_vector, list(rows)))


# ────────────────────────── 목소리 ──────────────────────────


async def _probe_voice(voice_id: str) -> dict:
    """운영 AI 서버로 짧은 한 문장을 읽어 본다. 목소리 회사 문제로 알리지 않는다(alert=False)."""
    try:
        audio = await ai_service.speech(text=PROBE_TEXT, voice_id=voice_id, alert=False)
    except (AIServerError, AIServerNotConfigured, httpx.HTTPError) as error:
        logger.warning("import voice probe failed: %s", type(error).__name__)
        return {
            "ok": False,
            "status": getattr(error, "status_code", None),
            "code": getattr(error, "code", None),
        }
    return {"ok": True, "contentType": "audio/mpeg", "audio": base64.b64encode(audio).decode()}


async def _voice_plan(bundle: PersonaBundle, request: PersonaImportRequest, warnings: list[str]):
    """(붙일 방식, 미리 들어보기 결과)."""
    voice_id = bundle.voice.voice_id if bundle.voice else None
    attach = request.voice.attach
    if attach != VOICE_NONE and not voice_id:
        warnings.append("묶음에 목소리가 없어 글 대화만 해요.")
        attach = VOICE_NONE
    probe = None
    if request.voice.probe and voice_id:
        probe = await _probe_voice(voice_id)
        if not probe["ok"] and attach != VOICE_NONE:
            attach = VOICE_NONE
            warnings.append(
                "운영 목소리 회사(Fish) 계정에서 이 목소리를 읽지 못해 목소리는 붙이지 않아요."
            )
    elif attach != VOICE_NONE:
        warnings.append("목소리를 미리 들어보지 않았어요. 붙이기 전에 들어 보길 권해요.")
    return attach, probe


# ────────────────────────── 가져오기 ──────────────────────────


def _bundle_sha(bundle: PersonaBundle) -> str:
    data = bundle.model_dump(mode="json", by_alias=True)
    return sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _sensitive_counts(bundle: PersonaBundle) -> dict[str, int]:
    out = {}
    for category, names in SENSITIVE_CATEGORIES.items():
        out[category] = sum(1 for m in bundle.memories if m.category.strip() in names)
    return out


async def run(db: AsyncSession, admin: User, request: PersonaImportRequest) -> dict:
    """미리보기(dryRun) 또는 가져오기. 돌려주는 값에는 내용(지침·기억 본문)을 싣지 않는다."""
    bundle = request.bundle
    warnings: list[str] = []
    legacy = _checked_legacy(bundle)
    user, target = await _target(db, request)
    if user.role == UserRole.ADMIN:
        warnings.append("관리자 계정이에요. 가족이 쓰는 계정이 맞는지 확인해 주세요.")
    # 주인이 계정 필수 동의(국외 이전 포함)를 마치기 전에는 가족 자료를 그 계정에 넣지 않는다(G12).
    # 미리보기는 알리기만 한다.
    if await legal.missing_required(db, user):
        if not request.dry_run:
            raise _bad(
                "이 계정은 아직 필수 동의를 마치지 않았어요. 주인이 한 번 로그인해 동의를 마친 뒤 "
                "가져와 주세요. 아무것도 저장하지 않았어요."
            )
        warnings.append(
            "이 계정은 아직 필수 동의를 마치지 않았어요. "
            "주인이 한 번 로그인해 동의를 마쳐야 가져올 수 있어요(지금은 미리보기만 돼요)."
        )
    prompt_sha = bundle.source.prompt_sha256
    existing = await _already_imported(db, user.id, prompt_sha)
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "이 계정에 같은 묶음을 이미 가져왔어요. 다시 가져오려면 먼저 되돌리기를 해 주세요.",
        )
    cap = get_settings().prelaunch_personas_per_account
    if cap is not None and target["personaCount"] >= cap:
        warnings.append(
            f"이 계정의 대화 상대가 {target['personaCount']}명이라 결제 전 상한({cap}명)에 "
            "닿았어요(가져오기는 막지 않아요)."
        )

    options = request.prompt
    basics = compose_basics(bundle, request.overrides)
    instructions = compose_instructions(bundle.prompt, options, basics)
    guarded = options.mode == "guarded"
    # 고른 사망 정책 줄에 한마디 자리가 있을 때만 한마디를 쓴다(deny·open 은 쓰지 않는다)
    answer_slot = guarded and DEATH_ANSWER_SLOT in (bundle.prompt.death_rules or {}).get(
        options.death_policy, ""
    )
    answer = _one_line(options.death_answer)
    death_answer_set = bool(answer_slot and answer)
    rules_sha = sha256(normalize(bundle.prompt.rules)) if guarded and bundle.prompt.rules else None
    applied = guardrails(instructions, legacy)
    if not guarded:
        warnings.append(
            "verbatim: 지금 안전 규칙(109 안내·지침 비공개·조언 금지·마크다운 금지)이 붙지 않아요."
        )
    else:
        if answer_slot and not answer:
            warnings.append("사망 질문의 한마디를 비워 두어 그 예시 구절을 뺐어요(지어내지 않음).")
        if answer and not answer_slot:
            warnings.append(
                f"사망 정책 '{options.death_policy}' 는 한마디를 쓰지 않아요(넣지 않았어요)."
            )
        if not basics.get("myName"):
            warnings.append(
                "지금 대화하는 사람(내 이름)을 비워 두어 옛 프롬프트의 확인 흐름을 그대로 써요."
            )
    sensitive = sum(1 for m in bundle.memories if m.sensitive)
    by_category = _sensitive_counts(bundle)
    if any(by_category.values()):
        warnings.append(
            f"종교 {by_category['religion']}건·건강 {by_category['health']}건 기억이 켜진 채로 "
            "들어가요. 주인이 앱의 '종교·건강 이야기 동의'에서 거두면 꺼지고, 다시 동의하면 "
            "켜져요. 지침 본문 속 문장은 그대로 남아요."
        )

    person_id = str(uuid4())
    memories = build_memories(bundle, person_id, user.id)
    embedding, query_vector = await _embed(bundle, memories, request.check_query, warnings)
    attach, probe = await _voice_plan(bundle, request, warnings)
    voice_id = bundle.voice.voice_id if bundle.voice and attach != VOICE_NONE else None
    found = _check(memories, query_vector)

    result = {
        "dryRun": request.dry_run,
        "personaId": None,
        "target": target,
        "basics": basics,
        "prompt": {
            "mode": options.mode,
            "deathPolicy": options.death_policy if guarded else None,
            "deathAnswerSet": death_answer_set,
            "chars": len(instructions),
            "sha256": sha256(instructions),
            "legacyChars": len(legacy),
            "legacySha256": sha256(legacy),
            "legacyUnchanged": instructions.endswith(legacy),
            # 옛 본문 앞에 붙인 안전 규칙(verbatim 이면 null·0·모두 false)
            "rulesSha256": rules_sha,
            "rulesChars": len(normalize(bundle.prompt.rules)) if rules_sha else 0,
            "guardrails": applied,
        },
        "memories": {
            "total": len(memories),
            "ready": sum(1 for m in memories if m.embedding_status == "READY"),
            "sensitive": sensitive,
            "religion": by_category["religion"],
            "health": by_category["health"],
            "categories": dict(Counter(m.category.strip() for m in bundle.memories)),
        },
        "embedding": embedding,
        "voice": {
            "attach": attach,
            "status": VOICE_PENDING if voice_id else VOICE_NONE,
            "hasVoiceId": bool(bundle.voice),
            "probe": probe,
        },
        "check": {"query": request.check_query, "found": found},
        "warnings": warnings,
    }
    if found == 0:
        warnings.append(NOTHING_FOUND)
    if request.dry_run:
        return result

    # 미리보기와 실행 사이(임베딩을 기다리는 동안) 같은 묶음이 먼저 들어갔으면 멈춘다
    if await _already_imported(db, user.id, prompt_sha):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "이 계정에 같은 묶음을 방금 가져왔어요. 화면을 새로고침해 주세요.",
        )
    now = datetime.now(UTC)
    person = Person(
        id=person_id,
        owner_user_id=user.id,
        name=basics["name"],
        relationship_label=basics["relation"],
        address_term=basics["addressTerm"],
        life_status=LifeStatus.DECEASED,
    )
    db.add(person)
    await db.flush()
    persona = Persona(
        person_id=person.id,
        name=basics["name"],
        status="READY",
        setup_step=SetupStep.READY,
        basics=basics,
        answers={},
        instructions=instructions,
        subject_voices=[],
        ready_at=now,
        trial_ends_at=None,
        is_active=False,
        origin=ORIGIN_IMPORT,
    )
    db.add(persona)
    await db.flush()
    db.add_all(memories)
    source = bundle.source
    bundle_sha = _bundle_sha(bundle)
    voice_state = VOICE_PENDING if voice_id else VOICE_NONE
    db.add(
        PersonaJob(
            persona_id=persona.id,
            kind=IMPORT_JOB,
            status=JobStatus.DONE,
            percent=100,
            detail="완료 · 가져옴",
            # 관리자 작업 기록(내용 없음). voiceId 는 주인이 확인하면 붙일 목소리.
            # 대화 상대를 지우면(되돌리기 포함) 함께 지워진다. 남는 기록은 persona_imported.
            options={
                "by": admin.id,
                "at": now.isoformat(),
                "targetUserId": user.id,
                "promptSha256": prompt_sha,
                "bundleSha256": bundle_sha,
                "builtAt": source.built_at.isoformat(),
                "promptRev": source.prompt_rev,
                "promptBlob": source.prompt_blob,
                "toolCommit": source.tool_commit,
                "memories": len(memories),
                "sensitive": sensitive,
                "promptMode": options.mode,
                "deathPolicy": options.death_policy if guarded else None,
                "deathAnswerSet": death_answer_set,
                "instructionsSha256": result["prompt"]["sha256"],
                "rulesSha256": rules_sha,
                "guardrails": applied,
                "embeddingModel": embedding["model"],
                "embeddingDimensions": embedding["dimensions"],
                "mvpModelMatch": embedding["mvpModelMatch"],
                "voice": voice_state,
                "voiceId": voice_id,
            },
        )
    )
    await db.commit()
    # 대화 상대를 지워도 남는 관리자 작업 기록(analytics_events 는 대화 상대에 외래 키가 없다).
    # 대상 계정은 user_id 칸에 남는다(탈퇴하면 forget_user 가 끊는다 — 일부러 props 에 넣지 않는다).
    await analytics.track(
        db,
        "persona_imported",
        user_id=user.id,
        persona_id=persona.id,
        admin=admin.id,
        memories=len(memories),
        sensitive=sensitive,
        promptMode=options.mode,
        deathPolicy=options.death_policy if guarded else None,
        voice=voice_state,
        promptSha256=prompt_sha,
        bundleSha256=bundle_sha,
        instructionsSha256=result["prompt"]["sha256"],
        embeddingModel=embedding["model"],
        embeddingDimensions=embedding["dimensions"],
    )
    # 저장한 뒤 DB 에서 다시 읽어 한 번 더 본다(G1)
    found = await _stored_check(db, person.id, query_vector)
    result["personaId"] = persona.id
    result["check"]["found"] = found
    if found == 0 and NOTHING_FOUND not in warnings:
        warnings.append(NOTHING_FOUND)
    logger.info(
        "persona imported %s for user %s (%s memories, %s)",
        persona.id,
        user.id,
        len(memories),
        options.mode,
    )
    return result


# ────────────────────────── 가져온 뒤 ──────────────────────────


async def import_record(db: AsyncSession, persona_id: str) -> PersonaJob | None:
    """가져오기 기록(IMPORT 작업 줄). 가져온 대화 상대가 아니면 None."""
    return await db.scalar(
        select(PersonaJob)
        .where(PersonaJob.persona_id == persona_id, PersonaJob.kind == IMPORT_JOB)
        .order_by(PersonaJob.created_at.desc())
        .limit(1)
    )


def pending_voice(persona: Persona, record: PersonaJob | None) -> str | None:
    """주인이 확인하면 붙일 목소리. 이미 목소리가 있거나 가져온 목소리가 없으면 None.

    붙인 뒤 목소리 동의를 거둬 목소리가 지워졌으면 다시 '대기'다(다시 확인하면 다시 붙는다).
    """
    if persona.origin != ORIGIN_IMPORT or persona.voice_id or record is None:
        return None
    return (record.options or {}).get("voiceId") or None


def mark_voice_attached(record: PersonaJob) -> None:
    record.options = {
        **(record.options or {}),
        "voice": VOICE_ATTACHED,
        "voiceAttachedAt": datetime.now(UTC).isoformat(),
    }


async def cancel_voice(db: AsyncSession, persona: Persona, admin_id: str) -> bool:
    """관리자가 목소리를 뗄 때(PATCH /admin/personas/{id} {voiceId:null}) 기다리던 목소리도 거둔다.

    거두지 않으면 주인이 '목소리 붙이기'로 다시 확인해 같은 목소리가 다시 붙는다
    (plan §8 '목소리만'). 회사 쪽 목소리는 지우지 않는다. commit 은 부르는 쪽이 한다.
    거둔 게 있으면 True.
    """
    if persona.origin != ORIGIN_IMPORT:
        return False
    record = await import_record(db, persona.id)
    if record is None or not (record.options or {}).get("voiceId"):
        return False
    record.options = {
        **record.options,
        "voice": VOICE_REMOVED,
        "voiceId": None,
        "voiceRemovedAt": datetime.now(UTC).isoformat(),
        "voiceRemovedBy": admin_id,
    }
    return True


async def sensitive_states(db: AsyncSession, persona: Persona) -> dict[str, str]:
    """가져온 기억의 민감 분류(종교·건강)를 대화에 쓰는가. 범주마다 'on' | 'off' | 'none'.

    off = 주인이 그 동의를 거둬 꺼 둠(withheld 표시), none = 그 분류 기억이 없음.
    동의(consents)만으로는 '아직 고르지 않아 켜 둔 채'와 '거둬서 꺼 둠'을 가를 수 없어 화면이
    이 값을 본다.
    """
    rows = await db.scalars(
        select(Memory.tags).where(
            Memory.person_id == persona.person_id, Memory.source == IMPORT_SOURCE
        )
    )
    tag_sets = [set(tags or []) for tags in rows]
    out: dict[str, str] = {}
    for category, names in SENSITIVE_CATEGORIES.items():
        wanted = {f"{CATEGORY_TAG}{name}" for name in names}
        mine = [tags for tags in tag_sets if wanted & tags]
        if not mine:
            out[category] = "none"
        elif any(f"{WITHHELD_TAG}{category}" in tags for tags in mine):
            out[category] = "off"
        else:
            out[category] = "on"
    return out


async def set_sensitive_memories(
    db: AsyncSession, persona: Persona, category: str, agreed: bool
) -> int:
    """민감 범주 동의(종교·건강)를 거두면 그 분류의 가져온 기억을 끄고, 다시 동의하면 켠다.

    가져온 대화 상대는 질문 답이 없어서 거둬도 지울 답이 없다. 대신 기억을 끈다(INACTIVE — 대화에
    넣지 않는다). 다시 켤 때는 여기서 끈 것만 켠다(withheld 표시). 지침 본문 속 문장은 그대로다.
    commit 은 부르는 쪽이 한다. 바꾼 기억 수를 돌려준다.
    """
    wanted = {f"{CATEGORY_TAG}{name}" for name in SENSITIVE_CATEGORIES.get(category, ())}
    mark = f"{WITHHELD_TAG}{category}"
    rows = await db.scalars(
        select(Memory).where(Memory.person_id == persona.person_id, Memory.source == IMPORT_SOURCE)
    )
    changed = 0
    for memory in rows:
        tags = list(memory.tags or [])
        if not wanted & set(tags):
            continue
        if not agreed and memory.status == "ACTIVE":
            memory.status = "INACTIVE"
            memory.tags = [*tags, mark]
            changed += 1
        elif agreed and mark in tags:
            memory.status = "ACTIVE"
            memory.tags = [t for t in tags if t != mark]
            changed += 1
    return changed


async def undo(db: AsyncSession, admin: User, persona_id: str) -> dict:
    """가져온 대화 상대를 지운다(사람·기억·대화·작업까지, persona_delete 와 같은 길).

    만들기 흐름으로 만든 대화 상대는 받지 않는다. voice_provider 가 비어 있어 목소리 회사의 목소리는
    지우지 않는다. 동의 이력·통계는 남는다. IMPORT 기록 줄도 함께 지워지므로 그 해시를
    persona_import_undone 이벤트에 옮겨 둔다.
    """
    persona = await db.get(Persona, persona_id)
    if persona is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "대화 상대를 찾지 못했어요.")
    if persona.origin != ORIGIN_IMPORT:
        raise HTTPException(status.HTTP_409_CONFLICT, "가져온 대화 상대만 되돌릴 수 있어요.")
    owner_id = await db.scalar(select(Person.owner_user_id).where(Person.id == persona.person_id))
    record = await import_record(db, persona.id)
    audit = {
        key: (record.options or {}).get(key) if record else None
        for key in ("promptSha256", "bundleSha256", "instructionsSha256")
    }
    message, _ = await persona_delete.claim(db, persona)
    if message:
        raise HTTPException(status.HTTP_409_CONFLICT, message)
    await persona_delete.remove(db, persona, requested_by="admin")
    await analytics.track(
        db,
        "persona_import_undone",
        user_id=owner_id,
        persona_id=persona_id,
        admin=admin.id,
        **audit,
    )
    logger.info("persona import %s undone", persona_id)
    return {"deleted": True, "personaId": persona_id}
