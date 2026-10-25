"""가족 공유. 주인이 만든 대화 상대와, 주인이 초대한 가족도 각자 자기 계정으로 이야기한다.

- 주인은 Person.owner_user_id 하나뿐이다. 가족은 PersonaShare 줄(초대 링크 하나 = 자리 하나)로
  들어온다. 가족이 할 수 있는 일은 대화(글·말)·듣기·대답 평가·내 정보 보고 고치기·나가기뿐이다.
  나머지 대화 상대 API 는 owned_persona 그대로라 가족도 모르는 사람과 같은 404 를 받는다.
- 대화방은 Conversation.user_id 로만 읽는다. 주인도 가족이 나눈 대화를 볼 수 없다.
- 결제·대화 횟수는 늘 주인 것이다: 권리(entitlement)는 주인으로 보고 체험은 열지 않는다
  (start_trial=False). 대화 횟수는 대화 상대마다라 가족이 함께 쓴다(UsageEvent.user_id = 말한 사람).
- 가족과 이야기할 때는 그 가족을 '지금 대화하는 사람'으로 놓고 지침을 다시 조립한다(만들기 흐름 —
  주인과 같은 조립기(LLM 없음)에 다른 입력). 가져온 대화 상대는 연결 문단의 한 줄만 바꾼다.
  주인과 이야기할 때 보내는 것은 예전과 한 글자도 다르지 않다.
- 가족에게는 Fish 목소리로만 읽어 준다(member_voice_ok). 예전 목소리(ElevenLabs·MiniMax)는 다시
  만들 때까지만 쓰는 것이라 만든 분만 듣는다(MiniMax 는 가족이 동의한 적도 없다). 가족 공유 안내·
  해외 회사로 보내는 정보에 그렇게 적었다.
- 초대 열쇠는 sha256 만 둔다. 열쇠는 만들 때·다시 보낼 때 응답에 한 번만 싣고, 서버 주소 경로·
  쿼리·통계에는 넣지 않는다(웹 주소는 #/join/<열쇠>).
- (2026-10-22 법률 자문) 만 19세 미만 아이의 대화 상대는 법정대리인과 직계가족(부모·조부모)만 함께
  쓴다(intake.MINOR_SHARE_RELATIONS — 미리 적기·들어오기·고치기에서 막고, 이미 들어와 있는 범위 밖
  가족은 minor_scope 로 멈춘다). 멈춘 대화 상대(holds)는 가족도 stopped 로 멈춘다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import HTTPException, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import (
    ORIGIN_IMPORT,
    Consent,
    Conversation,
    GapQuestion,
    Person,
    Persona,
    PersonaShare,
    SetupStep,
    User,
    UserStatus,
)
from app.services import (
    analytics,
    billing,
    deletion,
    intake,
    legal,
    notices,
    notify,
    persona_builder,
)
from app.services.ai import FISH_VOICE_PREFIX, AIServerError, ai_service
from app.services.storage import get_storage

logger = logging.getLogger(__name__)

# owned_persona 와 같은 글(가족이 아닌 사람에게 무엇이 있는지 알리지 않는다)
NOT_FOUND = "대화 상대를 찾지 못했어요."


# ────────────────────────── 오류 ──────────────────────────


class ShareError(Exception):
    """가족 공유 오류. main.py 가 {"detail": 글, "code": 코드, …extra} 로 바꾼다.

    화면은 code 로 가른다(글은 그대로 보여도 된다).
    """

    def __init__(self, status_code: int, code: str, detail: str, **extra) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.extra = extra


# code → (HTTP, 화면에 보일 글). share_bad_profile·share_paused 는 글이 그때마다 달라 따로 만든다.
ERRORS: dict[str, tuple[int, str]] = {
    "share_not_ready": (409, "아직 만드는 중이에요. 다 만든 뒤에 대화할 수 있어요."),
    "share_unsupported": (409, "이 대화 상대는 아직 가족과 함께 쓸 수 없어요."),
    "share_closed": (409, "잠시 가족 초대를 멈췄어요. 조금 뒤 다시 해 주세요."),
    "share_consent_needed": (409, "가족과 함께 쓰기 전에 확인할 것이 있어요."),
    "share_plan": (402, "가족과 함께 쓰기는 가족 요금제에서 할 수 있어요."),
    "share_full": (
        409,
        "함께 쓸 수 있는 가족은 {cap}명까지예요. "
        "쓰지 않은 링크를 취소하거나 가족을 내보낸 뒤 다시 만들어 주세요.",
    ),
    "share_deceased_row": (400, "돌아가신 분께는 초대 링크를 보낼 수 없어요."),
    "share_not_found": (404, "함께 쓰는 가족을 찾지 못했어요. 화면을 새로고침해 주세요."),
    "share_voice_unavailable": (
        409,
        "예전에 만든 목소리라 함께 쓰는 가족에게는 아직 글로만 드려요. "
        "만든 분이 목소리를 새로 만들면 들을 수 있어요.",
    ),
    "invite_invalid": (
        404,
        "초대 링크를 찾지 못했어요. 받은 링크를 다시 확인하거나 보내 준 가족에게 새 링크를 "
        "부탁해 주세요.",
    ),
    "invite_expired": (410, "기간이 지난 초대 링크예요. 보내 준 가족에게 새 링크를 부탁해 주세요."),
    "invite_used": (
        410,
        "이미 다른 분이 쓴 초대 링크예요. 보내 준 가족에게 내 링크를 따로 부탁해 주세요.",
    ),
    "invite_own": (409, "내가 만든 대화 상대예요. 초대 링크는 함께 쓸 가족에게 보내 주세요."),
    "invite_unavailable": (
        409,
        "지금은 이 대화 상대를 함께 쓸 수 없어요. 보내 준 가족에게 알려 주세요.",
    ),
    "invite_agree_required": (400, "가족 공유 안내에 동의해 주세요."),
    # 만 19세 미만 아이의 대화 상대(2026-10-22 법률 자문 · WO 4 '이용 범위')
    "share_minor_scope": (
        400,
        "아이의 대화 상대는 법정대리인과 직계가족(부모·조부모)만 함께 쓸 수 있어요. "
        "관계를 목록에서 골라 주세요.",
    ),
}


def fail(code: str, **extra) -> ShareError:
    status_code, detail = ERRORS[code]
    return ShareError(status_code, code, detail.format(**extra), **extra)


def _bad(field: str, message: str) -> ShareError:
    return ShareError(400, "share_bad_profile", message, field=field)


# ────────────────────────── 누가 이 대화 상대와 이야기하나 ──────────────────────────


@dataclass
class ChatAccess:
    persona: Persona
    person: Person
    owner: User | None
    share: PersonaShare | None  # None = 주인

    @property
    def member(self) -> bool:
        return self.share is not None


async def chat_access(db: AsyncSession, persona_id: str, user: User) -> ChatAccess:
    """주인이나 들어온 가족이면 ChatAccess, 아니면 owned_persona 와 같은 404."""
    persona = await db.scalar(
        select(Persona).where(Persona.id == persona_id).execution_options(populate_existing=True)
    )
    person = await db.get(Person, persona.person_id) if persona is not None else None
    if persona is None or person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    if person.owner_user_id == user.id:
        return ChatAccess(persona, person, user, None)
    share = await db.scalar(
        select(PersonaShare).where(
            PersonaShare.persona_id == persona.id, PersonaShare.user_id == user.id
        )
    )
    if share is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return ChatAccess(persona, person, await db.get(User, person.owner_user_id), share)


# persona_builder.pledge_doc(그분 상태마다 다른 확인 문서) → 가족 공유 확인을 고르는 종류
SUBJECT_KINDS = {
    "pledge_deceased": "deceased",
    "pledge_living": "alive",
    "pledge_unable": "unable",
    "pledge_self": "self",
}


def subject_kind(persona: Persona) -> str:
    """deceased | alive | unable | self (목소리 확인 문서와 같은 기준)."""
    return SUBJECT_KINDS.get(persona_builder.pledge_doc(persona), "deceased")


def shareable(persona: Persona) -> bool:
    """함께 쓸 수 있는 대화 상대인가: 다 만들었고, 가져온 것이거나 만들기 흐름 지침이 있다."""
    if persona.setup_step != SetupStep.READY:
        return False
    if persona.origin == ORIGIN_IMPORT:
        return True
    return bool(persona.instructions and (persona.basics or {}).get("myName"))


def owner_share_types() -> list[str]:
    """주인이 하는 가족 공유 확인 종류(그분 상태에 따라 이 가운데 몇 개를 받는다)."""
    return [t for t in legal.SHARE_PREFIXES if t != legal.SHARE_MEMBER_TYPE]


async def owner_needs(db: AsyncSession, owner_id: str, persona: Persona) -> list[str]:
    """주인이 아직 하지 않은(또는 거둔) 가족 공유 확인."""
    need = []
    for kind in legal.share_owner_types(subject_kind(persona)):
        if not await legal.has(db, owner_id, legal.persona_type(kind, persona.id)):
            need.append(kind)
    return need


# 가족 공유 동의를 거둔 기록(consent_records.channel)에 남기는 까닭. 스스로 눌러 나간 것은 web 이고,
# 남이 끝냈거나 다른 일로 끝난 것은 무엇으로 끝났는지 남긴다(2년 보관하는 이력만으로 알 수 있게).
END_CHANNELS = {
    "member": "web",  # 가족이 '함께 쓰기 그만두기'를 눌렀다
    "owner": "share_owner",  # 주인이 내보냈다
    "stop": "share_stop",  # 주인이 가족 공유를 그만뒀다
    "account": "share_account",  # 그 가족이 탈퇴했다
}
# 주인이 그분 상태를 바꿔 주인의 확인을 거둔 기록
STATUS_CHANNEL = "share_status"


async def withdraw_owner_consents(
    db: AsyncSession, owner: User, persona_id: str, *, channel: str = "web"
) -> int:
    """주인의 가족 공유 확인을 거둔다(그만두기·그분 상태가 바뀜). commit 은 부르는 쪽. 거둔 수.

    거두면 가족은 '확인 필요(consent)'로 멈추고, 주인이 다시 확인하면(POST …/shares/confirm)
    이어서 이야기한다. channel 은 거둔 까닭(END_CHANNELS·STATUS_CHANNEL)이다.
    """
    n = 0
    for kind in owner_share_types():
        consent_type = legal.persona_type(kind, persona_id)
        if await legal.has(db, owner.id, consent_type):
            await legal.record(
                db, owner, consent_type, False, persona_id=persona_id, channel=channel
            )
            n += 1
    return n


# F3: 결제가 열린 뒤 무료 체험(TRIAL) 동안에도 함께 쓴다. 빼면 체험 중에는 함께 쓰지 못한다.
SHARE_STATUSES = {"PRELAUNCH", "ADMIN", "TRIAL"}


def sharing_allowed(ent) -> bool:
    """이 권리로 가족과 함께 쓸 수 있나(가족 요금제·결제 전·관리자·체험)."""
    if not ent.active:
        return False
    if ent.status in SHARE_STATUSES:
        return True
    # 내리기를 걸어 두어도 다음 결제일까지는 plan 이 family 다(billing._renew_one 이 바꾼다)
    return ent.plan == "family" and ent.status in ("ACTIVE", "CANCELING", "PAST_DUE")


async def pause_reason(
    db: AsyncSession, persona: Persona, owner: User | None
) -> tuple[billing.Entitlement | None, str | None]:
    """가족이 지금 이야기할 수 없는 까닭(없으면 None)과 주인의 권리. 체험은 열지 않는다.

    멈춘 대화 상대(이의 접수 등, app/services/holds.py)는 무엇보다 먼저 stopped 다."""
    if persona.hold_reason:
        return None, "stopped"
    if owner is None or owner.status != UserStatus.ACTIVE:
        return None, "owner"
    if not shareable(persona):
        return None, "unavailable"
    if await owner_needs(db, owner.id, persona):
        return None, "consent"
    ent = await billing.entitlement(db, owner, persona, start_trial=False)
    if not ent.active:
        return ent, "waiting_owner" if ent.status == "WAITING_OWNER" else "ended"
    if not sharing_allowed(ent):
        return ent, "plan"
    return ent, None


async def member_entitlement(
    db: AsyncSession, access: ChatAccess
) -> tuple[billing.Entitlement | None, str | None]:
    """들어온 가족의 권리(주인 것)와 멈춘 까닭. 가족 자신의 동의는 들어올 때 받았다(나가면 줄째
    지워져 다시 보지 않는다).

    아이의 대화 상대에 직계가족(부모·조부모)이 아닌 관계로 들어와 있으면 minor_scope 로 멈춘다
    (자리와 나눈 대화는 그대로 — 관계를 목록 안으로 고치거나 만든 분이 자리를 빼면 풀린다). 대화할
    때마다 다시 본다."""
    ent, reason = await pause_reason(db, access.persona, access.owner)
    if reason is None and out_of_minor_scope(access.persona, access.share):
        return ent, "minor_scope"
    return ent, reason


# ────────────────────────── 만 19세 미만 아이의 대화 상대 ──────────────────────────


def minor_persona(persona: Persona) -> bool:
    """만 19세 미만이라고 답한 대화 상대(나이가 지나도 새 동의 전까지는 같게 본다 — 이 칸만)."""
    return (persona.basics or {}).get("minor") is True


def share_relations(persona: Persona) -> list[str]:
    """가족이 고를 수 있는 '그분과 어떤 사이'. 아이의 대화 상대는 직계가족(부모·조부모)만."""
    if minor_persona(persona):
        return list(intake.MINOR_SHARE_RELATIONS)
    return list(intake.MY_RELATIONS)


def check_minor_scope(persona: Persona, relation: str | None, *, required: bool = True) -> None:
    """아이의 대화 상대면 관계가 직계가족 목록 안이어야 한다(아니면 share_minor_scope 400).
    required=False(미리 적어 두기)면 비운 관계는 넘긴다(들어올 때 꼭 고른다)."""
    if not minor_persona(persona) or (relation is None and not required):
        return
    if relation not in intake.MINOR_SHARE_RELATIONS:
        raise fail("share_minor_scope", field="myRelation")


def out_of_minor_scope(persona: Persona, share: PersonaShare | None) -> bool:
    if share is None or share.user_id is None or not minor_persona(persona):
        return False
    return (share.profile or {}).get("myRelation") not in intake.MINOR_SHARE_RELATIONS


async def pause_minor_scope(db: AsyncSession, persona: Persona) -> int:
    """미성년이 된(PUT /minor) 대화 상대에 범위 밖 관계로 들어와 있는 가족 자리 수. 1개 이상이면
    만든 분께 알린다(minor_scope, 한 번만). 멈춤 자체는 member_entitlement 가 대화마다 판단한다
    (따로 칸을 두지 않는다). commit 은 부르는 쪽."""
    shares = await persona_shares(db, persona.id)
    count = sum(1 for share in shares if out_of_minor_scope(persona, share))
    if count:
        person = await db.get(Person, persona.person_id)
        label = notices.persona_label(persona)
        await notices.create(
            db,
            person.owner_user_id,
            "minor_scope",
            "함께 쓰는 가족을 확인해 주세요",
            f"{label}는 아이의 대화 상대라 법정대리인과 직계가족(부모·조부모)만 함께 쓸 수 있어요. "
            "관계가 맞지 않는 가족은 대화가 멈췄어요. 가족 공유 화면에서 확인해 주세요.",
            persona_id=persona.id,
            action={"label": "가족 공유 보기", "route": f"/p/{persona.id}/family"},
            dedupe_key=f"minorscope:{persona.id}",
        )
    return count


PAUSED_TEXTS = {
    "plan": "지금은 함께 이야기할 수 없어요. "
    "{owner}님이 가족 요금제로 바꾸면 다시 이야기할 수 있어요. 나눈 대화는 그대로 볼 수 있어요.",
    "ended": "지금은 이 대화 상대와 이야기할 수 없어요. "
    "{owner}님이 요금제를 다시 시작하면 이어서 이야기할 수 있어요. "
    "나눈 대화는 그대로 볼 수 있어요.",
    "waiting_owner": "지금은 이야기할 수 없어요. "
    "{owner}님이 TalkTo에 들어와 확인하면 다시 이야기할 수 있어요.",
    "owner": "지금은 이 대화 상대와 이야기할 수 없어요. 나눈 대화는 그대로 볼 수 있어요.",
    "consent": "{owner}님이 가족 공유를 다시 확인하면 이어서 이야기할 수 있어요. "
    "나눈 대화는 그대로 볼 수 있어요.",
    "unavailable": "지금은 함께 이야기할 수 없어요. {owner}님께 알려 주세요. "
    "나눈 대화는 그대로 볼 수 있어요.",
    # 멈춘 대화 상대(이의 접수 등 — app/services/holds.py)
    "stopped": "지금은 이 대화 상대와 이야기할 수 없어요.",
    # 아이의 대화 상대에 직계가족이 아닌 관계로 들어와 있음
    "minor_scope": "아이의 대화 상대는 법정대리인과 직계가족(부모·조부모)만 함께 쓸 수 있어요. "
    "관계가 맞으면 내 정보에서 관계를 고쳐 주세요.",
}


def paused_text(reason: str, owner_name: str, ent: billing.Entitlement | None = None) -> str:
    """멈춘 가족에게 보일 한 문장(요금제 고르기 안내는 넣지 않는다 — 결제는 주인이 한다)."""
    return PAUSED_TEXTS.get(reason, PAUSED_TEXTS["unavailable"]).format(owner=owner_name)


def paused_error(access: ChatAccess, reason: str, ent: billing.Entitlement | None) -> ShareError:
    return ShareError(
        403, "share_paused", paused_text(reason, owner_label(access), ent), reason=reason
    )


def owner_display(persona: Persona, owner: User | None) -> str:
    """가족에게 보일 만든 분의 이름: 만들 때 적은 내 이름 → 계정 이름 → '가족'."""
    for value in ((persona.basics or {}).get("myName"), owner.name if owner else None):
        if value and str(value).strip():
            return str(value).strip()
    return "가족"


def owner_label(access: ChatAccess) -> str:
    return owner_display(access.persona, access.owner)


def member_voice_ok(persona: Persona) -> bool:
    """함께 쓰는 가족에게 대답을 이 목소리로 읽어 드려도 되나. Fish 목소리만 된다.

    예전 목소리(ElevenLabs·MiniMax)는 다시 만들 때까지 만든 분만 듣는다(모듈 설명). AI 서버는 회사
    칸이 아니라 voice_id 앞글자로 회사를 고르므로(AI 레포
    app/external_api/tts provider_for_voice: 'fish_' → Fish, 'TalkTo' → MiniMax, 그 밖 →
    ElevenLabs) 앞글자만 본다(가져온 목소리는 회사 칸이 비어 있기도 하다). 목소리가 없으면 False."""
    return (persona.voice_id or "").startswith(FISH_VOICE_PREFIX)


def voice_shareable(persona: Persona) -> bool:
    """주인·초대 화면의 voiceShareable: 지금 목소리를 가족도 들을 수 있나. 예전 목소리(Fish 가
    아닌 것)일 때만 False 다. 목소리가 아직 없으면 막을 것이 없어 True."""
    return not persona.voice_id or member_voice_ok(persona)


# ────────────────────────── 부르는 말 추천 ──────────────────────────

# 주인이 고른 그분과의 관계(basics.relation)로 그분이 여자분인지 남자분인지 짐작한다(모르면 둘 다).
FEMALE = {"할머니", "외할머니", "어머니", "딸", "누나·언니", "여동생"}
MALE = {"할아버지", "외할아버지", "아버지", "아들", "형·오빠", "남동생"}
# 가족의 관계(intake.MY_RELATIONS) → (여자분일 때, 남자분일 때) 그분을 부르는 말. 말 높이는 짐작하지
# 않는다(화면이 미리 고르지 않는다).
ADDRESS_TERMS: dict[str, tuple[list[str], list[str]]] = {
    "아들": (["엄마", "어머니"], ["아빠", "아버지"]),
    "딸": (["엄마", "어머니"], ["아빠", "아버지"]),
    "손자": (["할머니"], ["할아버지"]),
    "손녀": (["할머니"], ["할아버지"]),
    "외손자": (["외할머니", "할머니"], ["외할아버지", "할아버지"]),
    "외손녀": (["외할머니", "할머니"], ["외할아버지", "할아버지"]),
    "며느리": (["어머님"], ["아버님"]),
    "사위": (["장모님", "어머님"], ["장인어른", "아버님"]),
    "배우자": (["여보", "당신"], ["여보", "당신"]),
    "조카": (["이모", "고모", "큰엄마"], ["삼촌", "큰아버지", "작은아버지"]),
    "형제자매": (["언니", "누나"], ["오빠", "형"]),
}


def address_suggestions(persona: Persona) -> dict[str, list[str]]:
    """가족의 관계마다 그분을 부를 만한 말(초대 화면·참여 화면의 칩)."""
    relation = (persona.basics or {}).get("relation")
    out: dict[str, list[str]] = {}
    for mine in intake.MY_RELATIONS:
        female, male = ADDRESS_TERMS.get(mine, ([], []))
        if relation in FEMALE:
            terms = female
        elif relation in MALE:
            terms = male
        else:
            terms = list(dict.fromkeys([*female, *male]))
        out[mine] = list(terms)
    return out


# ────────────────────────── 가족이 적는 정보(ShareProfile) ──────────────────────────

PROFILE_KEYS = (
    "myName",
    "myRelation",
    "addressTerm",
    "calledMe",
    "speechLevel",
    "selfReference",
    "callsOwner",
)
REQUIRED = ("myName", "myRelation", "addressTerm")
# 칸 → (최대 길이, 비었을 때 글(꼭 받는 칸만), 칸 이름)
TEXT_FIELDS: dict[str, tuple[int, str | None, str]] = {
    "myName": (20, "내 이름을 적어 주세요.", "내 이름"),
    "myRelation": (20, "그분과 어떤 사이인지 골라 주세요.", "관계"),
    "addressTerm": (15, "그분을 부르는 말을 적어 주세요.", "그분을 부르는 말"),
    "calledMe": (15, None, "그분이 나를 부르던 말"),
    "selfReference": (15, None, "그분이 자기를 가리키던 말"),
    "callsOwner": (15, None, "만든 분을 부르는 말"),
}
SPEECH_LEVELS = ("반말", "존댓말")
FAMILY_ROW_MAX = 15


def _one_line(value) -> str:
    return " ".join(str(value or "").split())


def validate_profile(data, *, partial: bool) -> dict:
    """ShareProfile 을 손으로 본다(틀리면 share_bad_profile 400 — 한국어 한 줄과 field).

    partial=True(미리 적어 두기·고치기): 보낸 칸만 보고, 비운 칸은 None 이다.
    partial=False(들어오기): 꼭 받는 셋(내 이름·관계·그분을 부르는 말)이 있어야 하고, 모든 칸을
    돌려준다(없는 칸은 None). 모르는 칸·familyRow·note·toMe 는 버린다.
    """
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise _bad("myName", "입력한 값을 다시 확인해 주세요.")
    out: dict = {}
    for key, (limit, missing, label) in TEXT_FIELDS.items():
        if key not in data:
            if not partial and missing:
                raise _bad(key, missing)
            if not partial:
                out[key] = None
            continue
        value = data[key]
        if value is not None and not isinstance(value, str):
            raise _bad(key, f"{label} 칸을 다시 확인해 주세요.")
        text = _one_line(value)
        if not text:
            if not partial and missing:
                raise _bad(key, missing)
            out[key] = None
            continue
        if len(text) > limit:
            raise _bad(key, f"{label} 칸은 {limit}자까지 적을 수 있어요.")
        out[key] = text
    if out.get("addressTerm") in intake._SELF_WORDS:
        raise _bad("addressTerm", "그분을 부르던 말(예: 엄마, 할머니)을 적어 주세요.")
    if "speechLevel" in data:
        level = data["speechLevel"]
        if level in (None, ""):
            out["speechLevel"] = None
        elif level in SPEECH_LEVELS:
            out["speechLevel"] = level
        else:
            raise _bad("speechLevel", "그분이 나에게 쓰던 말은 반말이나 존댓말로 골라 주세요.")
    elif not partial:
        out["speechLevel"] = None
    return {key: out[key] for key in PROFILE_KEYS if key in out}


def validate_family_row(value) -> str | None:
    """가족 표 줄 이름(만들기 흐름) 또는 예전 가족 목록 속 이름(가져온 것). 15자까지."""
    if value is not None and not isinstance(value, str):
        raise _bad("familyRow", "가족 이름을 다시 확인해 주세요.")
    text = _one_line(value)
    if len(text) > FAMILY_ROW_MAX:
        raise _bad("familyRow", f"가족 이름 칸은 {FAMILY_ROW_MAX}자까지 적을 수 있어요.")
    return text or None


def public_profile(values: dict | None) -> dict:
    """가족에게 보여도 되는 칸만(familyRow·editedBy·editedAt·note·toMe 는 빼고). 빈 칸은 뺀다."""
    values = values or {}
    return {key: values[key] for key in PROFILE_KEYS if values.get(key)}


def owner_profile(values: dict | None) -> dict:
    """주인 화면에 보일 칸(ShareProfile + familyRow). 빈 칸은 뺀다."""
    values = values or {}
    out = public_profile(values)
    if values.get("familyRow"):
        out["familyRow"] = values["familyRow"]
    return out


def family_rows(answers: dict | None) -> list[dict]:
    """가족 표(M2-01) 줄들(intake 와 같은 규칙으로 다듬은 것)."""
    return intake._family_rows((answers or {}).get("M2-01"))


def same_person(a: str | None, b: str | None) -> bool:
    """두 이름을 한 사람으로 보는가. persona_builder.build_prompt_material 이 녹음 속 이름을 가족
    표에 합칠 때와 같은 규칙이다: 같거나, 두 글자 이상인 한쪽이 다른 쪽에 들어 있다
    ('지은' ↔ '홍지은')."""
    if not a or not b:
        return False
    return a == b or (len(a) >= 2 and a in b) or (len(b) >= 2 and b in a)


def picked_row(answers: dict | None, profile: dict) -> dict | None:
    """이 가족이 가족 표의 어느 줄인가. familyRow 로, 없으면 내 이름으로 찾는다.

    '다른 가족'으로 들어오며 이름을 조금 다르게 적었으면('홍철수' ↔ 가족 표 '철수') 관계까지 같은
    살아 계신 분의 줄이 하나뿐일 때만 그 줄로 본다.
    """
    rows = family_rows(answers)
    for name in (profile.get("familyRow"), profile.get("myName")):
        if not name:
            continue
        for row in rows:
            if row["name"] == name:
                return row
    name, relation = profile.get("myName"), profile.get("myRelation")
    if profile.get("familyRow") or not name or not relation:
        return None
    close = [
        row
        for row in rows
        if same_person(row["name"], name)
        and row.get("relation") == relation
        and row.get("status") != "deceased"
    ]
    return close[0] if len(close) == 1 else None


def owner_family_rows(persona: Persona, shares: list[PersonaShare]) -> list[dict]:
    """초대할 사람 고르기(만들기 흐름만): 가족 표의 살아 계신 분. 주인·그분 자신·'나'는 뺀다."""
    if persona.origin == ORIGIN_IMPORT:
        return []
    taken = {
        name
        for share in shares
        for name in ((share.prefill or {}).get("familyRow"), (share.profile or {}).get("familyRow"))
        if name
    }
    out = []
    for row in intake.family_rows(persona.basics or {}, persona.answers or {}):
        if row.get("status") == "deceased":
            continue
        out.append(
            {
                "familyRow": row["name"],
                "myName": row["name"],
                "myRelation": row.get("relation"),
                "calledMe": row.get("calledBy"),
                "taken": row["name"] in taken,
            }
        )
    return out


# ────────────────────────── 초대 열쇠 ──────────────────────────


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> tuple[str, str]:
    """(열쇠, 그 해시). 열쇠는 응답에 한 번만 싣고 해시만 둔다."""
    token = secrets.token_urlsafe(24)
    return token, token_hash(token)


def join_url(token: str) -> str:
    """웹 주소. 요청 주소가 아니라 FRONTEND_URL 로 만든다(열쇠는 # 뒤라 서버 기록에 남지 않는다)."""
    return f"{get_settings().frontend_url.rstrip('/')}/#/join/{token}"


def now() -> datetime:
    return datetime.now(UTC)


def link_expiry(at: datetime | None = None) -> datetime:
    return (at or now()) + timedelta(hours=get_settings().share_link_hours)


def expired(share: PersonaShare, at: datetime | None = None) -> bool:
    return share.user_id is None and billing._aware(share.expires_at) <= (at or now())


async def drop_expired(
    db: AsyncSession, persona_id: str | None = None, *, keep: str | None = None
) -> int:
    """쓰지 않고 기한이 지난 링크를 지운다(목록·만들기·다시 보내기 때, 그리고 하루 한 번).
    commit 은 부르는 쪽. 지운 수."""
    query = delete(PersonaShare).where(
        PersonaShare.user_id.is_(None), PersonaShare.expires_at <= now()
    )
    if persona_id is not None:
        query = query.where(PersonaShare.persona_id == persona_id)
    if keep is not None:
        query = query.where(PersonaShare.id != keep)
    # 세션에 읽어 둔 줄과 견주지 않는다(SQLite 가 돌려준 시각에는 시간대가 없다)
    result = await db.execute(query.execution_options(synchronize_session=False))
    return int(result.rowcount or 0)


async def find_by_token(db: AsyncSession, token: str) -> PersonaShare | None:
    if not token:
        return None
    return await db.scalar(select(PersonaShare).where(PersonaShare.token_hash == token_hash(token)))


async def persona_shares(db: AsyncSession, persona_id: str) -> list[PersonaShare]:
    return list(
        (
            await db.scalars(
                select(PersonaShare)
                .where(PersonaShare.persona_id == persona_id)
                .order_by(PersonaShare.created_at, PersonaShare.id)
            )
        ).all()
    )


async def joined_count(db: AsyncSession, persona_id: str) -> int:
    return int(
        await db.scalar(
            select(func.count(PersonaShare.id)).where(
                PersonaShare.persona_id == persona_id, PersonaShare.user_id.is_not(None)
            )
        )
        or 0
    )


async def joined_counts(db: AsyncSession, persona_ids: list[str]) -> dict[str, int]:
    """대화 상대마다 들어온 가족 수(한 번에 센다)."""
    if not persona_ids:
        return {}
    rows = await db.execute(
        select(PersonaShare.persona_id, func.count(PersonaShare.id))
        .where(PersonaShare.persona_id.in_(persona_ids), PersonaShare.user_id.is_not(None))
        .group_by(PersonaShare.persona_id)
    )
    return {persona_id: int(n) for persona_id, n in rows}


async def claim_seat(
    db: AsyncSession, share_id: str, user_id: str, profile: dict, at: datetime
) -> bool:
    """비어 있고 기한 전인 자리만 잡는다(동시에 둘이 눌러도 한 사람만). commit 은 부르는 쪽."""
    result = await db.execute(
        update(PersonaShare)
        .where(
            PersonaShare.id == share_id,
            PersonaShare.user_id.is_(None),
            PersonaShare.expires_at > at,
        )
        .values(
            user_id=user_id,
            joined_at=at,
            profile=profile,
            instructions=None,
            instructions_base=None,
            updated_at=at,
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


# ────────────────────────── 화면에 보일 모양 ──────────────────────────


def share_item(share: PersonaShare, account_name: str | None = None) -> dict:
    """주인 화면의 한 줄(ShareItem). 열쇠는 싣지 않는다."""
    joined = share.user_id is not None
    values = (share.profile if joined else share.prefill) or {}
    item = {
        "id": share.id,
        "status": "joined" if joined else "open",
        "label": values.get("myName"),
        "createdAt": share.created_at,
        "editedBy": values.get("editedBy") if joined else None,
    }
    if joined:
        item |= {
            "joinedAt": share.joined_at,
            "accountName": account_name,
            "profile": owner_profile(share.profile),
        }
    else:
        item |= {"expiresAt": share.expires_at, "prefill": owner_profile(share.prefill)}
    return item


def member_me(share: PersonaShare) -> dict:
    """가족 자신의 정보(MemberMe). familyRow 는 싣지 않는다."""
    values = share.profile or {}
    return {
        "shareId": share.id,
        **{key: values.get(key) for key in PROFILE_KEYS},
        "joinedAt": share.joined_at,
        "editedBy": values.get("editedBy"),
    }


def member_item(access: ChatAccess) -> dict:
    """가족이 보는 대화 상대 한 줄. 성함(name)·personId·목소리 회사는 싣지 않는다."""
    persona, person, share = access.persona, access.person, access.share
    return {
        "id": persona.id,
        "role": "member",
        "shareId": share.id,
        "addressTerm": (share.profile or {}).get("addressTerm"),
        "relation": None,
        "lifeStatus": person.life_status,
        "step": persona.setup_step,
        "hasVoice": member_voice_ok(persona),
        "origin": persona.origin,
        "ownerName": owner_label(access),
        "updatedAt": persona.updated_at,
    }


def member_detail(access: ChatAccess) -> dict:
    """가족이 보는 대화 상대 상세. 기본 정보·질문 답·녹음·동의는 싣지 않는다.

    isSelf·addressSuggestions·relations·minorScope 는 '나에 대한 정보 고치기' 화면용이다(참여 화면과
    같은 값 — 아이의 대화 상대면 관계는 직계가족 목록에서만)."""
    return member_item(access) | {
        "jobs": {},
        "voicePending": False,
        "me": member_me(access.share),
        "isSelf": intake.is_self(access.persona.basics or {}),
        "addressSuggestions": address_suggestions(access.persona),
        "relations": share_relations(access.persona),
        "minorScope": minor_persona(access.persona),
    }


# ────────────────────────── 가족과 이야기할 지침 ──────────────────────────

# member_* 함수가 바뀌면 올린다(모두 다음 대화 때 다시 조립한다)
MEMBER_PROMPT_VERSION = "1"
# 주인에게만 맞는 답(그분이 주인 앞에서 자기를 가리키던 말·주인에게 쓰던 말 높이·말투)
# P1-07A(그분이 주인을 부르던 말 — 기본 정보 calledMe 가 비었을 때의 만들 때 질문)도 주인 것이다
OWNER_ONLY_ANSWERS = ("M2-01-self", "M2-01-level", "M2-01-tone", "P1-07A")
# AI 서버 PersonaContext.instructions 상한
MAX_INSTRUCTIONS = 50_000
TOO_LONG = "지금은 대답을 만들 수 없어요. 잠시 뒤 다시 해 주세요."
# intake.build_intake 의 '다른 가족의 사생활' 금지와 같은 줄(주인 줄을 더해 가족이 생기면 넣는다)
OTHER_FAMILY_PRIVACY = {
    "topic": "다른 가족의 사생활",
    "grade": "금지",
    "note": "살아 계신 가족의 주소·직장·학교·건강을 먼저 꺼내거나 지어내 말하지 않는다"
    " — 사용자가 가족 걱정을 털어놓으면 들어 준다",
    "sourceQuestion": "M2-01",
}


def member_basics(ob: dict, p: dict, picked: dict | None) -> dict:
    """그분은 그대로, '나'는 이 가족으로 바꾼 basics. 만 19세 미만 표시(minor · birthYm)도 잇는다
    — 빠지면 가족의 조립본에 아이 안전 규칙(basicProfile.minor)이 들어가지 않는다(SPEC §6.2)."""
    return {
        "name": ob["name"],
        "status": ob["status"],  # 나 자신이면 status 는 이미 alive
        # relation 은 화면·예전 조립기용(4층 조립기는 쓰지 않는다)
        "relation": p["addressTerm"],
        "addressTerm": p["addressTerm"],
        "myName": p["myName"],
        "myRelation": p["myRelation"],
        "calledMe": p.get("calledMe") or (picked or {}).get("calledBy"),
        **{key: ob[key] for key in ("minor", "birthYm") if key in ob},
    }


def member_answers(ob: dict, answers: dict, p: dict, picked: dict | None = None) -> dict:
    """주인에게만 맞는 답은 빼고, 이 가족의 줄(picked_row)은 가족 표에서 빼고(듣는 사람 줄이 된다),
    가족 표의 '나에게는'(toMe — 주인이 그 가족을 부르는 말)은 지운다."""
    out = {k: v for k, v in answers.items() if k not in OWNER_ONLY_ANSWERS}
    skip = {
        p["myName"],
        p.get("familyRow"),
        (picked or {}).get("name"),
        ob.get("myName") if not intake.is_self(ob) else None,
    } - {None}
    rows = [
        {k: v for k, v in r.items() if k != "toMe"}
        for r in answers.get("M2-01") or []
        if isinstance(r, dict) and r.get("name") not in skip
    ]
    if rows:
        out["M2-01"] = rows
    else:
        out.pop("M2-01", None)
    if p.get("selfReference"):
        out["M2-01-self"] = p["selfReference"]
    if p.get("speechLevel"):
        out["M2-01-level"] = p["speechLevel"]
    return out


async def assemble_for_member(db: AsyncSession, persona: Persona, p: dict) -> str:
    """주인과 같은 조립(/v1/persona/assembly, LLM 없음)에 이 가족을 '지금 대화하는 사람'으로 넣는다.

    가족 표 맨 앞(isListener)이 이 가족이고, 그다음이 주인(isOwner — 나 자신 대화 상대는 빼고)이다.
    듣는 사람 줄에는 isMember 를 단다(나 자신 대화 상대는 주인 줄이 없어서, AI 서버가 이것으로
    함께 쓰는 가족의 조립본임을 안다. 주인 조립본의 줄에는 isOwner 도 isMember 도 없다).
    주인이 그 가족 줄에 적은 한 줄 메모는 듣는 사람 줄로 옮긴다.
    주인 줄은 녹음 속 이름을 합치기 전에 넣는다. 그래서 녹음이 주인을 짧은 이름으로 부른 줄
    ('지은' ↔ '홍지은')이 따로 생기거나 비슷한 이름의 다른 가족 줄('지은이 아빠')로 가지 않고,
    주인과 이야기할 때 주인(듣는 사람) 줄로 합쳐지는 것과 똑같이 주인 줄로 합쳐진다(녹음이 주인
    줄을 고치지 않는다).
    """
    # '기억이 안 나요'·'따로 없었어요' 같은 표시 값은 빈 답이다(주인 줄에도 AI 로도 가지 않게)
    ob, answers = persona.basics or {}, intake.for_ai(persona.answers)
    picked = picked_row(answers, p)
    owner_rows = []
    if not intake.is_self(ob) and ob.get("myName"):
        owner_rows.append(
            {
                k: v
                for k, v in {
                    "name": ob["myName"],
                    "relation": ob.get("myRelation"),
                    "addressTermTo": intake.called_me(ob, answers),
                    "addressTermFrom": ob.get("addressTerm"),
                    "selfReference": answers.get("M2-01-self"),
                    "speechLevel": answers.get("M2-01-level"),
                    "tone": answers.get("M2-01-tone"),
                    "listenerCalls": p.get("callsOwner"),
                    "talksWithPersona": True,
                    "isOwner": True,
                }.items()
                if v is not None
            }
        )
    sc, ic, examples = persona_builder.build_prompt_material(
        member_basics(ob, p, picked),
        member_answers(ob, answers, p, picked),
        await persona_builder.load_recordings(db, persona.id),
        set(persona.subject_voices or []),
        after_listener=owner_rows,
    )
    fam = ic["familyMap"]  # fam[0] = 이 가족(isListener), 그다음 주인(isOwner, 있으면)
    fam[0]["isMember"] = True
    if picked and picked.get("note"):
        fam[0]["note"] = picked["note"]
    if len(fam) > 1 and not any(
        t.get("topic") == OTHER_FAMILY_PRIVACY["topic"] for t in ic["tabooTopics"]
    ):
        ic["tabooTopics"].append(dict(OTHER_FAMILY_PRIVACY))
    return await ai_service.assemble(
        subjectContext=sc, intakeContext=ic, speechExamples=examples, personaInsights=[]
    )


def instructions_base(persona: Persona, share: PersonaShare) -> str:
    """조립본이 바탕으로 한 것의 해시. 주인 지침·기본 정보·질문 답·고른 목소리·이 가족의 정보 가운데
    하나라도 바뀌면 다음 대화 때 다시 조립한다(민감 동의를 거둬 답이 지워져도 바로 빠진다)."""
    payload = json.dumps(
        [
            MEMBER_PROMPT_VERSION,
            persona.instructions or "",
            persona.basics or {},
            persona.answers or {},
            sorted(persona.subject_voices or []),
            share.profile or {},
        ],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# 가져온 대화 상대의 연결 문단(AI 레포 tools/export_manual_persona.py BRIDGE). 빈 줄이 없고 마지막
# 줄이 '- 지금 대화하는 사람은 …'(persona_import.listener_line)이다.
BRIDGE_HEAD = "# 아래 페르소나 설명과 함께 쓰는 법"
LISTENER_PREFIX = "- 지금 대화하는 사람은 "
_LISTENER_START = LISTENER_PREFIX.removeprefix("- ")


def _named_in(p: dict, text: str) -> bool:
    name = p.get("myName") or ""
    return len(name) >= 2 and name in text


def _quoted(word: str, with_batchim: str, without: str) -> str:
    """'말'과 받침에 맞는 조사('장모님'이라고 · '엄마'라고)."""
    return f"'{word}'{josa(word, with_batchim, without)}"


def member_line(p: dict, *, in_list: bool, owner_name: str | None, where: str = "아래") -> str:
    """가져온 대화 상대에서 '지금 대화하는 사람' 한 줄(이 가족).

    where 는 옛 본문(가족 목록이 든 글)이 이 줄의 어느 쪽에 있는지다. 연결 문단 속이면 '아래',
    연결 문단이 없어 본문 끝에 붙이면(verbatim) '위'.
    """
    row = p.get("familyRow") or p["myName"]
    who = f"'{row}'" + (f"({p['myRelation']})" if p.get("myRelation") else "")
    if in_list:
        s = (
            f"지금 대화하는 사람은 {where} 가족 목록의 {who}이다. "
            "누구인지 다시 확인하지 않아도 된다."
        )
    else:
        s = (
            f"지금 대화하는 사람은 {who}이다. {where} 가족 목록에 없어도 가족이 초대해 함께 "
            "이야기하는 사람이니 낯선 사람으로 대하지 않고, 누구인지 다시 확인하지 않아도 된다. "
            "이 사람과 함께한 옛일은 지어내지 않는다."
        )
    if p.get("calledMe"):
        s += f" 이 사람을 부를 때는 {_quoted(p['calledMe'], '이라고', '라고')} 부른다."
    s += f" 이 사람은 나를 {_quoted(p['addressTerm'], '이라고', '라고')} 부른다."
    if p.get("selfReference"):
        self_word = _quoted(p["selfReference"], "이라고", "라고")
        s += f" 이 사람 앞에서 나를 가리킬 때는 {self_word} 한다."
    if p.get("speechLevel"):
        s += f" 이 사람에게는 {p['speechLevel']}로 말한다."
    if owner_name and owner_name != row:
        s += (
            f" {where} 글에서 '{owner_name}'에게 하던 부르는 말과 "
            f"{_quoted(owner_name, '과', '와')}의 옛일은 '{owner_name}'의 것이니 "
            "이 사람에게 그대로 쓰지 않는다."
        )
    return s


def imported_for_member(text: str, p: dict, owner_name: str | None) -> str:
    """가져온 지침에서 '지금 대화하는 사람' 한 줄만 이 가족으로 바꾼다(옛 본문은 끝 글자까지 같다).

    연결 문단이 없으면(verbatim 가져오기) 끝에 짧은 마디를 붙인다. 주인 줄이 없으면(내 이름 없이
    가져옴) 연결 문단 끝에 이 가족의 줄을 붙인다.
    """
    head = text.find(BRIDGE_HEAD)
    if head == -1:
        # 옛 본문 뒤에 붙으니 가족 목록과 글은 이 줄의 '위'에 있다
        line = member_line(
            p,
            in_list=bool(p.get("familyRow")) or _named_in(p, text),
            owner_name=owner_name,
            where="위",
        )
        return f"{text.rstrip()}\n\n# 지금 대화하는 사람 (이 대화에서는 이것이 앞선다)\n- {line}\n"
    end = text.find("\n\n", head)
    end = len(text) if end == -1 else end
    legacy = text[end:]
    line = member_line(
        p, in_list=bool(p.get("familyRow")) or _named_in(p, legacy), owner_name=owner_name
    )
    kept = [
        row
        for row in text[head:end].split("\n")
        if not row.startswith(LISTENER_PREFIX) and not row.startswith(_LISTENER_START)
    ]
    return text[:head] + "\n".join([*kept, "- " + line]) + legacy


async def member_instructions(db: AsyncSession, access: ChatAccess) -> str:
    """이 가족과 이야기할 때 보낼 지침. 주인 지침으로 대신하는 일은 없다.

    - 가져온 것: 그때그때 한 줄만 바꾼다(저장하지 않는다).
    - 만들기 흐름: 조립본을 자리에 둔다(바탕 해시가 같으면 다시 쓴다). 조립이 실패하면 예전 조립본을
      쓰고, 그것도 없으면 오류를 올린다(부르는 쪽이 되돌리고 잡은 대화 횟수를 돌려준다).
    - AI 서버 주소가 없으면(로컬 개발) 조립하지 않는다(대답도 개발용이다).
    저장은 부르는 쪽(exchange)의 commit 에 실린다.
    """
    persona, share = access.persona, access.share
    profile = share.profile or {}
    if persona.origin == ORIGIN_IMPORT:
        text = imported_for_member(
            persona.instructions or "", profile, (persona.basics or {}).get("myName")
        )
        if len(text) > MAX_INSTRUCTIONS:
            logger.error("member instructions too long for persona %s", persona.id)
            notify.error("SHARE_INSTRUCTIONS_TOO_LONG", f"persona {persona.id[:8]}")
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, TOO_LONG)
        return text
    if not get_settings().ai_server_url:
        return persona.instructions or ""
    base = instructions_base(persona, share)
    if share.instructions and share.instructions_base == base:
        return share.instructions
    try:
        text = await assemble_for_member(db, persona, profile)
    except (AIServerError, httpx.HTTPError):
        if share.instructions:
            logger.warning("member assembly failed; using the last one for share %s", share.id)
            return share.instructions
        raise
    share.instructions, share.instructions_base = text, base
    return text


async def prebuild(db: AsyncSession, access: ChatAccess) -> None:
    """들어온 바로 뒤에 지침을 미리 조립해 둔다(실패해도 첫 대화 때 다시 한다)."""
    try:
        if access.persona.origin == ORIGIN_IMPORT:
            return
        await member_instructions(db, access)
        await db.commit()
    except Exception as error:  # noqa: BLE001 — 미리 해 두는 일이라 들어오기를 깨지 않는다
        await db.rollback()
        logger.warning("member prebuild failed: %s", type(error).__name__)


# ────────────────────────── 기억 제목 ──────────────────────────


def josa(word: str, with_batchim: str, without: str) -> str:
    """받침에 맞는 조사(이/가 · 과/와 · 이라고/라고). 마지막 한글 글자에 받침이 있으면 with_batchim,
    없거나 한글이 없으면 without(AI 레포 render.particle 과 같은 쪽)."""
    for char in reversed(word or ""):
        code = ord(char) - 0xAC00
        if 0 <= code < 11172:
            return with_batchim if code % 28 else without
    return without


def josa_ga(word: str) -> str:
    """'이'/'가'. 마지막 한글 글자에 받침이 있으면 '이'."""
    return josa(word, "이", "가")


# 주인이 적은 기억(질문 답·직접 넣은 것).
# 녹음 속 그분 말(RECORDING)·가져온 기억(IMPORT)은 그대로 둔다.
OWNER_WRITTEN = ("INTAKE", "MANUAL")


def member_memory_title(title: str, source: str | None, owner: str) -> str:
    """가족과 이야기할 때 주인이 적은 기억에 '누가 적었는지'를 붙인다.

    그 기억을 지금 대화하는 가족과 함께한 일처럼 말하지 않게 한다. '(물으면만)' 표시는 제목 어디에
    있어도 지켜진다.
    """
    if source not in OWNER_WRITTEN:
        return title
    return f"{owner}{josa_ga(owner)} 적어 준 이야기 · {title}"


# ────────────────────────── 끝내기 ──────────────────────────


async def end_share(
    db: AsyncSession, share: PersonaShare, *, by: str, actor_id: str | None = None
) -> deletion.DeletionResult | None:
    """자리 하나를 끝낸다(취소·나가기·내보내기·그만두기·탈퇴). commit 한다.

    들어온 가족이면 그 가족이 이 대화 상대와 나눈 대화(메시지·평가는 함께)와 저장소의 대화 음성을
    지우고, 가족 동의의 지금 상태 줄을 지운다(거둔 이력은 남긴다). 대화 횟수 기록(UsageEvent)은
    남겨 내보냈다 다시 불러 횟수를 되살리지 못하게 한다.

    지운 대화가 있으면 지운 일시를 기록하고(WO 7 · kind="share_member") 그 결과를 돌려준다(화면·메일
    문구). 탈퇴(by="account")는 계정 기록이 따로 남아 여기서 남기지 않는다. 지운 것이 없으면 None.
    """
    persona_id, member_id = share.persona_id, share.user_id
    conversation_ids: list[str] = []
    if member_id is not None:
        conversation_ids = list(
            (
                await db.scalars(
                    select(Conversation.id).where(
                        Conversation.persona_id == persona_id, Conversation.user_id == member_id
                    )
                )
            ).all()
        )
        if conversation_ids:
            await db.execute(delete(Conversation).where(Conversation.id.in_(conversation_ids)))
        # '더 알려 주실 기억'에 남긴 답 가운데 만든 분이 아직 확인하지 않은 것은 지우고 질문을
        # 다시 연다(가족 고침(Feedback)은 대화와 함께 지워졌다. 이미 반영한 것은 만든 분이
        # 확인한 것이라 남는다)
        await db.execute(
            update(GapQuestion)
            .where(
                GapQuestion.persona_id == persona_id,
                GapQuestion.answered_by_user_id == member_id,
                GapQuestion.answer_state == "pending_owner",
            )
            .values(
                status="open",
                answer_state=None,
                answer_text=None,
                answered_by_user_id=None,
                answered_at=None,
            )
        )
        kind = legal.persona_type(legal.SHARE_MEMBER_TYPE, persona_id)
        # 누가 끝냈는지 이력에 남긴다(주인이 내보낸 것을 가족이 스스로 거둔 것처럼 적지 않게)
        legal.log(db, member_id, kind, False, persona_id=persona_id, channel=END_CHANNELS[by])
        await db.execute(
            delete(Consent).where(Consent.user_id == member_id, Consent.consent_type == kind)
        )
    await db.delete(share)
    await db.commit()
    storage = get_storage()
    for conversation_id in conversation_ids:
        await asyncio.to_thread(storage.delete_prefix, f"conversations/{conversation_id}")
    result = None
    if member_id is not None and conversation_ids and by != "account":
        result = await deletion.record(
            db,
            kind="share_member",
            user_id=member_id,
            persona_id=persona_id,
            requested_by="owner",
            vendor_ok=None,
            detail={"conversations": len(conversation_ids)},
        )
    if member_id is not None:
        await analytics.track(
            db, "share_ended", user_id=actor_id or member_id, persona_id=persona_id, by=by
        )
    return result


async def leave_all(db: AsyncSession, user: User) -> None:
    """회원 탈퇴: 이 사람이 함께하던 모든 자리에서 나간다(대화·대화 음성까지)."""
    rows = (await db.scalars(select(PersonaShare).where(PersonaShare.user_id == user.id))).all()
    for share in rows:
        await end_share(db, share, by="account", actor_id=user.id)


async def stop_all(
    db: AsyncSession, persona: Persona, owner: User
) -> tuple[int, deletion.DeletionResult | None]:
    """가족과 함께 쓰기 그만두기: 모든 자리(쓰지 않은 링크까지)를 끝내고 주인의 확인을 거둔다.
    (끝낸 자리 수, 가족 대화를 지운 마지막 결과 — 지운 것이 없으면 None)."""
    rows = await persona_shares(db, persona.id)
    last = None
    for share in rows:
        result = await end_share(db, share, by="stop", actor_id=owner.id)
        last = result or last
    await withdraw_owner_consents(db, owner, persona.id, channel=END_CHANNELS["stop"])
    await db.commit()
    return len(rows), last
