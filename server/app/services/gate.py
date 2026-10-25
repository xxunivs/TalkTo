"""하나뿐인 처리 게이트(2026-10-22 법률 자문 · SPEC §2.2 · §11).

대화 상대마다 '지금 이 일을 해도 되나'를 한곳에서 정한다: 멈춤(hold) · 대화가 어려우신 분(unable)
· 유족 확인 · 법정대리인 확인 · 그분 동의 녹음. 판단 순서와 글은 SPEC §11.1 표가 정답이다
(위에서부터 처음 맞는 줄).

  1 멈춤(hold_reason)                       → 모든 일
  2 대화가 어려우신 분(unable, 예전 상태)    → 녹음 처리·목소리·만들기
  3 돌아가신 분, 유족 선언 없음              → 아직 다 만들지 않은 것의 만들기·목소리만
    (대표 답 2026-10-23: 이미 다 만든 고인 대화 상대는 아무것도 막지 않고 카드만 보인다. 살아
    계신 분·나 자신에서 돌아가신 분으로 바꾼 것은 새 고인 대화 상대라 녹음 처리·목소리·만들기를
    선언 뒤로 미룬다 — BEREAVED_REQUIRED)
  4 살아 계신 아이, 법정대리인 확인 전       → 녹음 처리·목소리·만들기·새 초대·들어오기
  5 살아 계신 분, 그분 동의 녹음 통과 전     → 녹음 처리·목소리·만들기(다 만들기 전이면 글도)

관리자 계정·관리자 소유 대화 상대도 똑같이 막는다(동의 게이트 우회는 계정 동의에만 해당).
막히면 GateError → main.py 가 {"detail": 글, "code": 코드, **extra} 로 바꾼다(만든 분 409).
가족(member)에게 막힐 때는 sharing.ShareError(403, "share_paused", 글, reason=코드)를 그대로 쓴다
(화면의 멈춤 카드가 그대로 처리한다).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Persona, SetupStep
from app.services import ages, intake, subject_consent

Action = Literal["audio", "voice", "build", "text", "chat", "speech", "invite", "join"]

# 그분 녹음을 다루는 일(받아쓰기·살펴보기·목소리·만들기)
VENDOR_ACTIONS = ("audio", "voice", "build")

# basics 표시: 돌아가신 분이 아니던 대화 상대(살아 계신 분·나 자신)를 돌아가신 분으로 바꿨다.
# 다 만든 것이어도 새 고인 대화 상대처럼 유족 선언을 먼저 받는다(#3 — 그분 동의 녹음을 피하는
# 길을 닫는다). PUT /personas/{id} 가 붙이고 이어 간다(mark_new_deceased)
BEREAVED_REQUIRED = "bereavedRequired"

# 멈춤(#1)의 글은 holds 에 둔다(알림·카드와 같은 글).
# 가족이 멈춤 밖의 까닭(법정대리인 확인 전 등)으로 들어오지 못할 때
UNAVAILABLE_MEMBER = "지금은 이 대화 상대를 함께 쓸 수 없어요. 보내 준 가족에게 알려 주세요."
UNABLE = "이 대화 상대는 새 녹음 처리와 목소리 다시 만들기를 멈췄어요."
BEREAVED_BUILD = "돌아가신 분의 신청 자격과 유족 명단을 먼저 적어 주세요."
GUARDIAN = "먼저 법정대리인 확인을 마쳐 주세요."
NOT_GUARDIAN = (
    "아이의 대화 상대는 그 아이의 부모님만 만들 수 있어요. 이 대화 상대는 새 녹음 처리와 목소리 "
    "다시 만들기를 멈췄어요. 궁금한 점은 고객센터로 알려 주세요."
)
SUBJECT = "그분 동의 녹음이 확인되어야 그분 녹음을 살펴보거나 목소리를 만들 수 있어요."
# 배포 때 돌던 만들기·다시 시도가 작업 안에서 막혔을 때도 같은 글(SPEC §5.10)
SUBJECT_BUILD = "그분 동의 녹음이 확인되어야 이어서 만들 수 있어요."
SUBJECT_TEXT = "그분 동의 녹음이 확인된 뒤에 넣을 수 있어요."


@dataclass(frozen=True)
class Block:
    code: str
    message: str


class GateError(Exception):
    """처리 게이트에 막혔다. main.py 가 status_code 와 {"detail", "code", **extra} 로 바꾼다."""

    def __init__(self, status_code: int, code: str, detail: str, **extra) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.extra = extra


def month_day(value: datetime | date | None) -> str | None:
    """한국 날짜 'M월 D일'(알림·카드 글). 없으면 None."""
    if value is None:
        return None
    day = ages.today_kst(value) if isinstance(value, datetime) else value
    return f"{day.month}월 {day.day}일"


def stopped_message(persona: Persona, *, member: bool = False) -> str:
    """#1 멈춤의 글(만든 분: 지우는 날, 가족: 이야기할 수 없다는 것만)."""
    # holds 는 지울 때 persona_delete(→ persona_builder → 이 파일)를 쓰므로 쓸 때 가져온다
    from app.services import holds

    return holds.MEMBER_BLOCKED if member else holds.blocked_message(persona)


def living_other(basics: dict | None) -> bool:
    """살아 계신 분이고 나 자신이 아님(그분 동의 녹음이 필요한 대화 상대)."""
    basics = basics or {}
    return basics.get("status") == "alive" and not intake.is_self(basics)


def deceased_other(basics: dict | None) -> bool:
    """돌아가신 분이고 나 자신이 아님(유족 선언을 받는 대화 상대)."""
    basics = basics or {}
    return basics.get("status") == "deceased" and not intake.is_self(basics)


def mark_new_deceased(old: dict | None, basics: dict) -> dict:
    """정보 고치기: 돌아가신 분이 아니던 대화 상대를 돌아가신 분으로 바꾸면 BEREAVED_REQUIRED 를
    붙이고, 앞서 붙인 것은 이어 간다(validate_basics 는 정해진 칸만 돌려준다). 돌아가신 분이
    아니게 되면 뗀다."""
    old = old or {}
    basics = {key: value for key, value in basics.items() if key != BEREAVED_REQUIRED}
    if not deceased_other(basics):
        return basics
    if deceased_other(old) and old.get(BEREAVED_REQUIRED) is not True:
        return basics
    return basics | {BEREAVED_REQUIRED: True}


def ready(persona: Persona) -> bool:
    return persona.setup_step == SetupStep.READY


async def _minor_state(db: AsyncSession, persona: Persona):
    # minors 는 holds → persona_delete → persona_builder(이 파일을 쓴다)로 이어질 수 있어 쓸 때
    # 가져온다(가져오는 차례가 꼬이지 않게)
    from app.services import minors

    return await minors.state(db, persona)


async def _declared(db: AsyncSession, persona: Persona) -> bool:
    from app.services import bereaved

    return await bereaved.get(db, persona.id) is not None


async def guardian_block(db: AsyncSession, persona: Persona) -> Block | None:
    """#4: 아이의 대화 상대인데 법정대리인 확인을 마치지 않았으면 그 까닭. 부모가 아닌 분이
    예전에 만든 것(notGuardian)은 확인을 마칠 길이 없어 그 글로 알린다."""
    basics = persona.basics or {}
    if not living_other(basics) or basics.get("minor") is not True:
        return None
    state = await _minor_state(db, persona)
    if state is None or state.guardian_ok:
        return None
    return Block("guardian", NOT_GUARDIAN if basics.get("notGuardian") else GUARDIAN)


async def block(
    db: AsyncSession, persona: Persona, action: Action, *, member: bool = False
) -> Block | None:
    """막는 까닭(없으면 None). SPEC §11.1 표 — 위에서부터 처음 맞는 줄."""
    basics = persona.basics or {}
    # 1. 멈춤: 모든 일
    if persona.hold_reason:
        return Block("stopped", stopped_message(persona, member=member))
    status = basics.get("status")
    # 2. 대화가 어려우신 분(예전 상태): 녹음 처리·목소리만 멈춘다(대화·답 고치기·가족은 그대로)
    if status == "unable":
        return Block("unable", UNABLE) if action in VENDOR_ACTIONS else None
    # 3. 돌아가신 분: 새로 만드는 것만 유족 선언을 먼저 받는다. 이미 다 만든 것은 막지 않는다.
    #    돌아가신 분으로 바꾼 것은 녹음 처리도 선언 뒤에(살아 계셨던 분의 녹음이 그대로 나가지
    #    않게). 대화는 그대로다
    if deceased_other(basics):
        if basics.get(BEREAVED_REQUIRED) is True:
            guarded: tuple[str, ...] = VENDOR_ACTIONS
        else:
            guarded = () if ready(persona) else ("build", "voice")
        if action in guarded and not await _declared(db, persona):
            return Block("bereaved", BEREAVED_BUILD)
        return None
    if not living_other(basics):
        return None
    # 4. 살아 계신 아이: 법정대리인 확인이 먼저
    if action in (*VENDOR_ACTIONS, "invite", "join"):
        found = await guardian_block(db, persona)
        if found is not None:
            return found
    # 5. 그분 동의 녹음: 통과 전에는 그분 녹음을 다루지 않는다. 다 만들기 전이면 글도 해외로
    #    보내지 않는다(기억 인터뷰 기억 넣기). 다 만든 것의 대화·글 처리는 그대로(WO 3-8)
    if action in VENDOR_ACTIONS or (action == "text" and not ready(persona)):
        mode = ages.consent_mode(basics, ages.today_kst())
        if not await subject_consent.passed(db, persona, mode):
            if action == "text":
                return Block("subject_consent", SUBJECT_TEXT)
            return Block("subject_consent", SUBJECT_BUILD if action == "build" else SUBJECT)
    return None


async def require(
    db: AsyncSession, persona: Persona, action: Action, *, member: bool = False
) -> None:
    """막히면 만든 분은 GateError(409), 가족은 sharing.ShareError(403 share_paused, reason=코드)."""
    found = await block(db, persona, action, member=member)
    if found is None:
        return
    if member:
        # sharing 이 persona_builder 를 거쳐 이 파일을 쓰므로 여기서 가져온다
        from app.services.sharing import ShareError

        text = found.message if found.code == "stopped" else UNAVAILABLE_MEMBER
        raise ShareError(403, "share_paused", text, reason=found.code)
    raise GateError(409, found.code, found.message)


async def check_job(db: AsyncSession, persona_id: str, action: Action) -> None:
    """작업 안에서 다시 본다(배포 전에 건 작업·다시 시도·뒤에서 도는 일, 그리고 작업 중간에 걸린
    멈춤). 막히면 ValueError(글) → 작업 FAILED, 화면에 그 글. 다른 요청이 고친 멈춤을 보려고
    대화 상대 줄을 다시 읽는다."""
    persona = await db.get(Persona, persona_id, populate_existing=True)
    if persona is None:
        return
    found = await block(db, persona, action)
    if found is not None:
        raise ValueError(found.message)


# ────────────────────────── 만든 분 카드(SPEC §11.2) ──────────────────────────

# 대표 답(2026-10-23 #3): 이미 만든 고인 대화 상대는 막지 않으므로 '이어서 대화할 수 있어요'
# 대신 대화가 그대로 된다고 알린다
BEREAVED_TITLE = "신청 자격과 유족 명단을 적어 주세요"
BEREAVED_BODY = (
    "돌아가신 분의 대화 상대는 이제 고인과의 관계와 고인의 배우자·자녀(안 계시면 부모) 명단을 "
    "적고 확인해 주셔야 해요. 대화는 지금처럼 하실 수 있어요. 사망진단서 같은 서류는 받지 않아요."
)
# 돌아가신 분으로 바꾼 것(BEREAVED_REQUIRED)은 녹음 처리·목소리를 선언 뒤로 미루므로 그것을 알린다
BEREAVED_BODY_CHANGED = (
    "돌아가신 분의 대화 상대는 고인과의 관계와 고인의 배우자·자녀(안 계시면 부모) 명단을 적고 "
    "확인해 주셔야 그분 녹음을 살펴보거나 목소리를 만들 수 있어요. {chat}사망진단서 같은 서류는 "
    "받지 않아요."
)
BEREAVED_CHAT = "대화는 지금처럼 하실 수 있어요. "
GUARDIAN_TITLE = "법정대리인 확인을 마쳐 주세요"
GUARDIAN_BODIES = {
    "minor_child": "만 14세 미만 아이의 대화 상대는 법정대리인 동의와 휴대전화 확인 문자가 "
    "필요해요.",
    "minor_teen": "아이의 대화 상대는 만드는 분이 법정대리인(부모)인지 먼저 확인해야 해요.",
}
CONSENT_TITLE_NEW = "먼저 그분 동의 녹음을 해 주세요"
CONSENT_TITLE_READY = "그분 동의 녹음을 받아 주세요"
CONSENT_TITLE_AGAIN = "아이 본인의 동의 녹음이 필요해요"
CONSENT_BODY_NEW = (
    "앱이 보여 주는 대본대로 그분이 직접 말씀하시는 모습을 한 번에 녹음해요. TalkTo가 확인하기 "
    "전에는 그분 녹음을 글로 옮기거나 해외 회사로 보내지 않아요. 녹음은 미리 올려 두셔도 돼요."
)
CONSENT_BODY_READY = (
    "살아 계신 분의 대화 상대는 이제 그분이 직접 말씀하시는 동의 녹음이 있어야 녹음을 더 익히거나 "
    "목소리를 다시 만들 수 있어요. 대화는 지금처럼 하실 수 있어요."
)
CONSENT_BODY_AGAIN = "{date}까지 받지 못하면 대화가 멈추고 지워져요."
REVIEW_TITLE = "그분 동의 녹음을 확인하고 있어요"
REVIEW_BODY = "보통 1~2일 걸려요. 확인되면 알림으로 알려 드릴게요."
HOLD_TITLE = "확인할 것이 있어요"
HOLD_BODY = "TalkTo가 따로 연락드릴게요."
UNABLE_TITLE = "새 녹음 처리와 목소리 다시 만들기를 멈췄어요"
UNABLE_BODY = (
    "스스로 설명을 듣고 허락하시기 어려운 분의 대화 상대는 더 만들지 않기로 했어요. 이 대화 상대는 "
    "지우지 않고 대화도 그대로 하실 수 있지만, 녹음을 더 익히거나 목소리를 다시 만들 수는 없어요."
)


def _card(
    kind: str,
    title: str,
    body: str,
    *,
    blocking: bool = False,
    route: str | None = None,
    **extra,
) -> dict:
    return {
        "kind": kind,
        "blocking": blocking,
        "title": title,
        "body": body,
        "route": route,
        **{key: value for key, value in extra.items() if value is not None},
    }


def stopped_card(persona: Persona) -> dict:
    """멈춤 카드(막음). 글은 멈춤 알림과 같다(holds.stopped_body — 까닭마다)."""
    from app.services import holds

    return _card(
        "stopped",
        holds.STOPPED_TITLE,
        holds.stopped_body(persona),
        blocking=True,
        deleteAt=holds.view(persona)["deleteAfter"],
    )


async def _consent_card(db: AsyncSession, persona: Persona, *, kind: str) -> dict | None:
    """그분 동의 녹음 카드(통과 전). 검수 중·보류면 그 카드, 아니면 녹음해 달라는 카드."""
    route = f"/p/{persona.id}/consent"
    latest = await subject_consent.latest_relevant(db, persona)
    status = latest.status if latest is not None else "none"
    if status == "submitted":
        return _card("subject_consent_review", REVIEW_TITLE, REVIEW_BODY, route=route)
    if status == "hold":
        return _card("subject_consent_hold", HOLD_TITLE, HOLD_BODY, route=route)
    state = await _minor_state(db, persona) if (persona.basics or {}).get("minor") else None
    if state is not None and state.reconsent_kind and state.due_at is not None:
        from app.services import minors

        title = CONSENT_TITLE_AGAIN
        # 알림과 같은 날(기한 0시의 앞날 = 받아야 하는 마지막 날)
        body = CONSENT_BODY_AGAIN.format(date=month_day(minors.last_day(state.due_at)))
    elif ready(persona):
        title, body = CONSENT_TITLE_READY, CONSENT_BODY_READY
    else:
        title, body = CONSENT_TITLE_NEW, CONSENT_BODY_NEW
    if status == "rerequest":
        body = (latest.note_to_owner or "").strip() or subject_consent.REREQUEST_HEAD
    return _card(
        kind,
        title,
        body,
        route=route,
        dueAt=state.due_at if state is not None and state.reconsent_kind else None,
    )


async def actions(db: AsyncSession, persona: Persona) -> list[dict]:
    """만든 분 카드(§11.2): {kind, blocking, title, body, route, dueAt?, deleteAt?}. 할 일이 없으면
    빈 목록. 멈췄으면 멈춤 카드 하나만."""
    if persona.hold_reason:
        return [stopped_card(persona)]
    basics = persona.basics or {}
    status = basics.get("status")
    if status == "unable":
        return [_card("unable_frozen", UNABLE_TITLE, UNABLE_BODY)]
    if deceased_other(basics):
        # 만들기 전의 새 고인 대화 상대는 카드 없이 '만들기 전 확인'에서 받는다
        changed = basics.get(BEREAVED_REQUIRED) is True
        if (ready(persona) or changed) and not await _declared(db, persona):
            if changed:
                body = BEREAVED_BODY_CHANGED.format(chat=BEREAVED_CHAT if ready(persona) else "")
            else:
                body = BEREAVED_BODY
            return [_card("bereaved", BEREAVED_TITLE, body, route=f"/p/{persona.id}/bereaved")]
        return []
    if not living_other(basics):
        return []
    if "minor" not in basics:
        # 예전 대화 상대: 카드 단추가 먼저 '그분은 만 19세 미만인가요?'를 묻는다
        card = await _consent_card(db, persona, kind="minor_question")
        return [card] if card is not None else []
    found = await guardian_block(db, persona)
    if found is not None:
        if basics.get("notGuardian"):
            return [_card("guardian", GUARDIAN_TITLE, NOT_GUARDIAN)]
        mode = ages.consent_mode(basics, ages.today_kst()) or "minor_teen"
        body = GUARDIAN_BODIES.get(mode, GUARDIAN_BODIES["minor_teen"])
        return [_card("guardian", GUARDIAN_TITLE, body, route=f"/p/{persona.id}/guardian")]
    mode = ages.consent_mode(basics, ages.today_kst())
    if await subject_consent.passed(db, persona, mode):
        return []
    card = await _consent_card(db, persona, kind="subject_consent")
    return [card] if card is not None else []


async def summary(db: AsyncSession, persona: Persona) -> dict:
    """상세의 gates 칸(§11.3): {audio, voice, build, chat, invite: bool(true = 됨), code, message}.
    code·message 는 처음 막힌 일의 것(없으면 None)."""
    out: dict = {}
    first: Block | None = None
    for action in ("audio", "voice", "build", "chat", "invite"):
        found = await block(db, persona, action)
        out[action] = found is None
        if first is None and found is not None:
            first = found
    out["code"] = first.code if first else None
    out["message"] = first.message if first else None
    return out
