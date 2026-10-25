"""가족 공유 API(app/services/sharing.py).

주인: GET·POST /personas/{id}/shares, POST …/shares/confirm, POST …/shares/{shareId}/renew,
PATCH·DELETE …/shares/{shareId}, POST …/shares/stop. 주인 확인은 owned_persona 라 가족·모르는
사람은 같은 404 다. 들어온 가족은 자기 자리만 고치고(PATCH) 나간다(DELETE).

초대 받은 분: POST /invites/preview(로그인 전), /invites/resolve, /invites/accept. 열쇠는 늘 본문에
싣는다(주소 경로·쿼리에 넣으면 접속 기록에 남는다). 주인 이름 말고는 그분 성함·가족 표·미리 적어 둔
내용을 로그인 전에는 보이지 않는다.
"""

from fastapi import APIRouter, Request, Response, status
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, CurrentUser
from app.api.v1.billing import persona_title
from app.api.v1.persona_setup import owned_persona
from app.core import ratelimit
from app.core.config import get_settings
from app.db.models import ORIGIN_IMPORT, Person, Persona, PersonaShare, SetupStep, User
from app.schemas.common import APIModel, Envelope
from app.services import analytics, billing, deletion, gate, intake, legal, mailer, sharing

router = APIRouter(tags=["Family sharing"])
invite_router = APIRouter(prefix="/invites", tags=["Family sharing"])


class ShareCreate(APIModel):
    # 처음 한 번 받는 주인의 확인(family_share …). 모르는 종류는 무시한다.
    agreements: list[str] = Field(default_factory=list, max_length=10)
    # 고른 가족 표 줄 이름(만들기 흐름) 또는 예전 가족 목록 속 이름(가져온 것)
    family_row: str | None = None
    # 미리 적어 둔 그 가족(ShareProfile 일부). 받는 분은 '네, 맞아요'만 누르면 된다.
    prefill: dict | None = None


class ShareConfirm(APIModel):
    # 주인이 체크한 가족 공유 확인(family_share …). 모르는 종류는 무시한다.
    agreements: list[str] = Field(default_factory=list, max_length=10)


class InviteToken(APIModel):
    token: str = Field(max_length=200)


class InviteAccept(APIModel):
    token: str = Field(max_length=200)
    agree: bool = False
    profile: dict | None = None


# ────────────────────────── 주인 ──────────────────────────


def _reason(persona: Persona, ent: billing.Entitlement | None) -> str | None:
    """주인 화면에서 지금 링크를 만들 수 없는 까닭(만들 수 있으면 None)."""
    if persona.setup_step != SetupStep.READY:
        return "not_ready"
    if not sharing.shareable(persona):
        return "unsupported"
    if not get_settings().share_open:
        return "closed"
    if ent is None or not sharing.sharing_allowed(ent):
        return "plan"
    return None


async def _account_names(db: DB, shares: list[PersonaShare]) -> dict[str, str]:
    ids = [s.user_id for s in shares if s.user_id]
    if not ids:
        return {}
    rows = await db.execute(select(User.id, User.name).where(User.id.in_(ids)))
    return dict(rows.all())


@router.get("/personas/{persona_id}/shares", response_model=Envelope[dict])
async def list_shares(persona_id: str, user: CurrentUser, db: DB):
    """주인의 '가족과 함께' 화면. 쓰지 않고 기한이 지난 링크는 먼저 지운다."""
    persona = await owned_persona(db, persona_id, user.id)
    await sharing.drop_expired(db, persona.id)
    await db.commit()
    # 주인이 보는 것이라 요금제 보기와 같이 체험을 열 수 있다(billing._persona_view 와 같다)
    ent = (
        await billing.entitlement(db, user, persona)
        if persona.setup_step == SetupStep.READY
        else None
    )
    kind = sharing.subject_kind(persona)
    need = await sharing.owner_needs(db, user.id, persona)
    labels = legal.SHARE_OWNER_LABELS.get(kind, {})
    shares = await sharing.persona_shares(db, persona.id)
    names = await _account_names(db, shares)
    reason = _reason(persona, ent)
    return Envelope(
        data={
            "allowed": reason is None,
            "reason": reason,
            "prelaunch": not billing.payments_open(user),
            "subjectKind": kind,
            "need": need,
            "consentItems": [{"type": t, "label": labels.get(t, "")} for t in need],
            "cap": get_settings().share_members_per_persona,
            "used": len(shares),
            "familyRows": sharing.owner_family_rows(persona, shares),
            "addressSuggestions": sharing.address_suggestions(persona),
            # 아이의 대화 상대면 관계는 이 목록(직계가족)에서만 고른다(직접 입력 없음)
            "minorScope": sharing.minor_persona(persona),
            "relations": sharing.share_relations(persona),
            "voiceShareable": sharing.voice_shareable(persona),
            "items": [sharing.share_item(s, names.get(s.user_id or "")) for s in shares],
        }
    )


def _ready(persona: Persona) -> None:
    """다 만들었고 함께 쓸 수 있는 대화 상대인가(아니면 share_not_ready·share_unsupported)."""
    if persona.setup_step != SetupStep.READY:
        raise sharing.fail("share_not_ready")
    if not sharing.shareable(persona):
        raise sharing.fail("share_unsupported")


async def _record_agreements(db: DB, user, persona: Persona, agreements: list[str] | None) -> None:
    """주인이 체크한 확인을 남긴다(그분 상태에 맞는 종류만, 모르는 것은 무시). 남겼으면 commit."""
    kinds = set(legal.share_owner_types(sharing.subject_kind(persona)))
    recorded = False
    for kind in dict.fromkeys(agreements or []):
        if kind in kinds:
            await legal.record(
                db, user, legal.persona_type(kind, persona.id), True, persona_id=persona.id
            )
            recorded = True
    if recorded:
        await db.commit()


async def _owner_checks(db: DB, user, persona: Persona, agreements: list[str] | None):
    """링크 만들기·다시 보내기 앞의 확인(순서가 정해져 있다). agreements 는 만들기만 받는다
    (확인만 다시 할 때는 POST …/shares/confirm). 맨 앞은 처리 게이트(멈춤·법정대리인 확인 등)."""
    await gate.require(db, persona, "invite")
    _ready(persona)
    if not get_settings().share_open:
        raise sharing.fail("share_closed")
    await _record_agreements(db, user, persona, agreements)
    need = await sharing.owner_needs(db, user.id, persona)
    if need:
        raise sharing.fail("share_consent_needed", need=need)
    ent = await billing.entitlement(db, user, persona)
    if not sharing.sharing_allowed(ent):
        raise sharing.fail("share_plan")


def _link_props(prefill: dict, persona: Persona, *, renew: bool) -> dict:
    return {
        "prefilled": bool(sharing.public_profile(prefill)),
        "origin": "import" if persona.origin == ORIGIN_IMPORT else "web",
        "renew": renew,
    }


@router.post("/personas/{persona_id}/shares", response_model=Envelope[dict], status_code=201)
async def create_share(persona_id: str, payload: ShareCreate, user: CurrentUser, db: DB):
    """초대 링크 하나(= 가족 한 사람의 자리)를 만든다. 열쇠는 이 응답에만 싣는다."""
    persona = await owned_persona(db, persona_id, user.id)
    ratelimit.SHARE_LINKS.check(user.id)
    await _owner_checks(db, user, persona, payload.agreements)
    prefill = _prefill(persona, payload.prefill, payload.family_row)
    # 같은 대화 상대의 링크 만들기를 한 줄로 세운다(자리 수를 함께 넘지 않게 — billing 과 같은
    # 대화 상대 줄 자물쇠)
    await db.execute(select(Persona.id).where(Persona.id == persona.id).with_for_update())
    await sharing.drop_expired(db, persona.id)
    cap = get_settings().share_members_per_persona
    if len(await sharing.persona_shares(db, persona.id)) >= cap:
        await db.rollback()
        raise sharing.fail("share_full", cap=cap)
    token, digest = sharing.new_token()
    share = PersonaShare(
        persona_id=persona.id, token_hash=digest, expires_at=sharing.link_expiry(), prefill=prefill
    )
    db.add(share)
    await db.commit()
    await analytics.track(
        db,
        "share_link_created",
        user_id=user.id,
        persona_id=persona.id,
        **_link_props(prefill, persona, renew=False),
    )
    return Envelope(data={"share": sharing.share_item(share), "url": sharing.join_url(token)})


# 가족 표 줄 → 미리 적어 둘 칸(이름·관계·그분이 부르던 말)
ROW_FIELDS = (("myName", "name"), ("myRelation", "relation"), ("calledMe", "calledBy"))


def _prefill(persona: Persona, data: dict | None, family_row) -> dict:
    """미리 적어 둔 그 가족. 가족 표 줄을 골랐으면 비운 칸(이름·관계·불리던 말)을 그 줄로 채운다."""
    values = sharing.validate_profile(data or {}, partial=True)
    row_name = sharing.validate_family_row(family_row)
    if row_name and persona.origin != ORIGIN_IMPORT:
        row = next((r for r in sharing.family_rows(persona.answers) if r["name"] == row_name), None)
        if row is not None:
            if row.get("status") == "deceased":
                raise sharing.fail("share_deceased_row")
            for key, source in ROW_FIELDS:
                if not values.get(key) and row.get(source):
                    values[key] = row[source]
    out = {k: v for k, v in values.items() if v}
    # 아이의 대화 상대: 미리 적어 둔 관계도 직계가족만(비워 두면 들어올 때 고른다)
    sharing.check_minor_scope(persona, out.get("myRelation"), required=False)
    if row_name:
        out["familyRow"] = row_name
    return out


@router.post("/personas/{persona_id}/shares/confirm", response_model=Envelope[dict])
async def confirm_sharing(persona_id: str, payload: ShareConfirm, user: CurrentUser, db: DB):
    """주인의 확인만 다시 받는다(링크는 만들지 않는다). 남은 확인 {need} 를 돌려준다.

    그분 상태를 바꾸면 확인이 거둬져 함께 쓰는 가족이 멈춘다. 자리가 다 찼거나 새 링크가 필요 없어도
    여기서 다시 확인하면 가족이 이어서 이야기한다. 잠시 초대를 멈춘 때(share_open 끔)에도 받는다
    — 이미 함께하는 가족은 그대로 쓰기 때문이다. 요금제는 보지 않는다(가족은 따로 plan 으로 멈춘다).
    """
    persona = await owned_persona(db, persona_id, user.id)
    _ready(persona)
    await _record_agreements(db, user, persona, payload.agreements)
    return Envelope(data={"need": await sharing.owner_needs(db, user.id, persona)})


@router.post("/personas/{persona_id}/shares/{share_id}/renew", response_model=Envelope[dict])
async def renew_share(persona_id: str, share_id: str, user: CurrentUser, db: DB):
    """쓰지 않은 링크를 다시 보낸다: 같은 자리에 새 열쇠·새 기한(예전 링크는 더 열리지 않는다)."""
    persona = await owned_persona(db, persona_id, user.id)
    ratelimit.SHARE_LINKS.check(user.id)
    await _owner_checks(db, user, persona, None)
    share = await db.get(PersonaShare, share_id)
    if share is None or share.persona_id != persona.id or share.user_id is not None:
        raise sharing.fail("share_not_found")
    await sharing.drop_expired(db, persona.id, keep=share.id)
    token, digest = sharing.new_token()
    share.token_hash = digest
    share.expires_at = sharing.link_expiry()
    await db.commit()
    await analytics.track(
        db,
        "share_link_created",
        user_id=user.id,
        persona_id=persona.id,
        **_link_props(share.prefill or {}, persona, renew=True),
    )
    return Envelope(data={"share": sharing.share_item(share), "url": sharing.join_url(token)})


async def _share_and_role(db: DB, persona_id: str, share_id: str, user) -> tuple:
    """(자리, 대화 상대, 'owner' | 'member'). 주인은 그 대화 상대의 자리, 가족은 자기 자리만."""
    share = await db.get(PersonaShare, share_id)
    if share is None or share.persona_id != persona_id:
        raise sharing.fail("share_not_found")
    persona = await db.get(Persona, persona_id)
    person = await db.get(Person, persona.person_id) if persona else None
    if person is not None and person.owner_user_id == user.id:
        return share, persona, "owner"
    if share.user_id is not None and share.user_id == user.id:
        return share, persona, "member"
    raise sharing.fail("share_not_found")


def _edits(payload: dict) -> tuple[dict, bool, object]:
    """PATCH 본문: ShareProfile 칸(일부)과 familyRow(주인만). {"profile": {…}} 로 감싸도 받는다."""
    if not isinstance(payload, dict):
        raise sharing.fail("share_not_found")
    data = payload
    for key in ("profile", "prefill"):
        if isinstance(payload.get(key), dict):
            data = payload[key]
            break
    family_row = data.get("familyRow", payload.get("familyRow"))
    has_row = "familyRow" in data or "familyRow" in payload
    return sharing.validate_profile(data, partial=True), has_row, family_row


@router.patch("/personas/{persona_id}/shares/{share_id}", response_model=Envelope[dict])
async def update_share(persona_id: str, share_id: str, payload: dict, user: CurrentUser, db: DB):
    """정보 고치기. 주인은 쓰지 않은 링크의 미리 적은 내용이나 들어온 가족의 정보를, 가족은 자기
    정보를 고친다. 정보가 바뀌면 다음 대화 때 지침을 다시 조립한다."""
    share, persona, role = await _share_and_role(db, persona_id, share_id, user)
    values, has_row, family_row = _edits(payload)
    edited_at = sharing.now().isoformat()
    if role == "owner" and share.user_id is None:
        prefill = {**(share.prefill or {}), **values}
        if has_row:
            prefill.pop("familyRow", None)
            row_name = sharing.validate_family_row(family_row)
            if row_name:
                prefill["familyRow"] = row_name
        sharing.check_minor_scope(persona, prefill.get("myRelation") or None, required=False)
        share.prefill = {k: v for k, v in prefill.items() if v}
    else:
        merged = {**(share.profile or {}), **values}
        checked = sharing.validate_profile(merged, partial=False)
        # 아이의 대화 상대: 관계를 직계가족 목록 안으로 고쳐야 저장된다(고치면 멈춤이 풀린다)
        sharing.check_minor_scope(persona, checked.get("myRelation"))
        profile = dict(checked)
        family = (share.profile or {}).get("familyRow")
        if role == "owner" and has_row:
            family = sharing.validate_family_row(family_row)
        if family:
            profile["familyRow"] = family
        profile["editedBy"] = role
        profile["editedAt"] = edited_at
        share.profile = profile
        share.instructions_base = None
    await db.commit()
    await analytics.track(
        db, "share_profile_updated", user_id=user.id, persona_id=persona_id, by=role
    )
    if role == "member":
        return Envelope(data=sharing.member_me(share))
    names = await _account_names(db, [share])
    return Envelope(data=sharing.share_item(share, names.get(share.user_id or "")))


@router.delete("/personas/{persona_id}/shares/{share_id}", response_model=Envelope[dict])
async def delete_share(persona_id: str, share_id: str, user: CurrentUser, db: DB):
    """주인: 링크 취소·가족 내보내기. 가족: 나가기. 그 가족이 나눈 대화도 지운다."""
    share, _, role = await _share_and_role(db, persona_id, share_id, user)
    result = await sharing.end_share(db, share, by=role, actor_id=user.id)
    data: dict = {"ended": True}
    if result is not None:
        # 지운 대화가 있으면 백업 날짜까지 알린다(WO 7). 나간 가족에게는 메일도(되면)
        data["deletion"] = deletion.response(result)
        if role == "member":
            mailer.send_user(user.email, "[TalkTo] 함께 나눈 대화를 지웠어요", result.message)
    return Envelope(data=data)


@router.post("/personas/{persona_id}/shares/stop", response_model=Envelope[dict])
async def stop_sharing(persona_id: str, user: CurrentUser, db: DB):
    """가족과 함께 쓰기 그만두기: 모든 자리를 끝내고 주인의 확인을 거둔다(다시 하려면 다시 확인)."""
    persona = await owned_persona(db, persona_id, user.id)
    removed, result = await sharing.stop_all(db, persona, user)
    data: dict = {"removed": removed}
    if result is not None:
        data["deletion"] = deletion.response(result)
    return Envelope(data=data)


# ────────────────────────── 초대 받은 분 ──────────────────────────


async def _invite_context(db: DB, share: PersonaShare) -> tuple[Persona, Person, User | None]:
    persona = await db.get(Persona, share.persona_id)
    person = await db.get(Person, persona.person_id)
    return persona, person, await db.get(User, person.owner_user_id)


def _owner_side(persona: Persona, person: Person, owner: User | None) -> dict:
    """주인 쪽에서 본 이름과 호칭(그분 성함은 싣지 않는다). 나 자신 대화 상대면 호칭은 없다."""
    self_persona = intake.is_self(persona.basics or {})
    return {
        "ownerName": sharing.owner_display(persona, owner),
        "ownerTitle": None if self_persona else persona_title(persona, person),
        "isSelf": self_persona,
    }


async def _unavailable(db: DB, persona: Persona, owner: User | None) -> bool:
    if not get_settings().share_open:
        return True
    _, reason = await sharing.pause_reason(db, persona, owner)
    if reason is not None:
        return True
    # 처리 게이트(멈춤·법정대리인 확인 전 등)가 새 가족을 받지 않는 때
    return await gate.block(db, persona, "join", member=True) is not None


@invite_router.post("/preview", response_model=Envelope[dict])
async def preview_invite(payload: InviteToken, request: Request, db: DB):
    """로그인 전에 링크가 어떤 상태인지(누가 초대했는지만).

    대화 상대 id·그분 성함·가족 표·미리 적어 둔 내용은 싣지 않는다.
    """
    ratelimit.INVITES_IP.check(ratelimit.client_ip(request))
    share = await sharing.find_by_token(db, payload.token)
    if share is None:
        return Envelope(data={"state": "invalid"})
    persona, person, owner = await _invite_context(db, share)
    side = _owner_side(persona, person, owner)
    if share.user_id is not None:
        return Envelope(data={"state": "used", **side})
    if sharing.expired(share):
        return Envelope(data={"state": "expired"})
    if await _unavailable(db, persona, owner):
        return Envelope(data={"state": "unavailable"})
    return Envelope(
        data={
            "state": "open",
            **side,
            "addressTerm": (share.prefill or {}).get("addressTerm"),
            "expiresAt": share.expires_at,
        }
    )


@invite_router.post("/resolve", response_model=Envelope[dict])
async def resolve_invite(payload: InviteToken, request: Request, user: CurrentUser, db: DB):
    """로그인(과 계정 동의) 뒤: 이 링크로 무엇을 할지. join 이면 확인 카드에 쓸 것을 싣는다."""
    ratelimit.INVITES_IP.check(ratelimit.client_ip(request))
    share = await sharing.find_by_token(db, payload.token)
    if share is None:
        return Envelope(data={"state": "invalid"})
    persona, person, owner = await _invite_context(db, share)
    side = _owner_side(persona, person, owner) | {"origin": persona.origin}
    if person.owner_user_id == user.id:
        return Envelope(data={"state": "owner", "personaId": persona.id, **side})
    mine = await db.scalar(
        select(PersonaShare).where(
            PersonaShare.persona_id == persona.id, PersonaShare.user_id == user.id
        )
    )
    if mine is not None:
        # 이 링크(책갈피)나 다른 링크로 이미 들어온 분. 기한이 지나도 바로 대화로 간다.
        return Envelope(data={"state": "member", "personaId": persona.id, **side})
    if share.user_id is not None:
        return Envelope(data={"state": "used", **side})
    if sharing.expired(share):
        return Envelope(data={"state": "expired"})
    if await _unavailable(db, persona, owner):
        return Envelope(data={"state": "unavailable"})
    prefill = sharing.public_profile(share.prefill)
    return Envelope(
        data={
            "state": "join",
            **side,
            "addressTerm": prefill.get("addressTerm"),
            "prefill": prefill,
            # 미리 적어 둔 것이 있으면 '만든 분이 적은 것'이라고 알린다(개인정보보호법 제20조)
            "fromOwner": bool(prefill),
            # 아이의 대화 상대면 직계가족(부모·조부모)만 고른다(직접 입력 없음)
            "relations": sharing.share_relations(persona),
            "minorScope": sharing.minor_persona(persona),
            "addressSuggestions": sharing.address_suggestions(persona),
            "consent": {
                "type": legal.SHARE_MEMBER_TYPE,
                "doc": legal.SHARE_DOC,
                "label": legal.SHARE_MEMBER_LABEL,
            },
            "voiceShareable": sharing.voice_shareable(persona),
        }
    )


@invite_router.post("/accept", response_model=Envelope[dict], status_code=201)
async def accept_invite(
    payload: InviteAccept, request: Request, response: Response, user: CurrentUser, db: DB
):
    """함께하기. 가족 동의를 남기고(정보보다 먼저, 같은 트랜잭션) 자리를 잡는다.

    이미 이 대화 상대에 자리가 있으면 링크를 쓰지 않고 200 {already: true}.
    """
    ratelimit.INVITES_IP.check(ratelimit.client_ip(request))
    share = await sharing.find_by_token(db, payload.token)
    if share is None:
        raise sharing.fail("invite_invalid")
    persona, person, owner = await _invite_context(db, share)
    if person.owner_user_id == user.id:
        raise sharing.fail("invite_own")
    mine = await db.scalar(
        select(PersonaShare).where(
            PersonaShare.persona_id == persona.id, PersonaShare.user_id == user.id
        )
    )
    if mine is not None:
        response.status_code = status.HTTP_200_OK
        return Envelope(data={"personaId": persona.id, "shareId": mine.id, "already": True})
    if share.user_id is not None:
        raise sharing.fail("invite_used")
    if sharing.expired(share):
        raise sharing.fail("invite_expired")
    if not get_settings().share_open:
        raise sharing.fail("share_closed")
    _, reason = await sharing.pause_reason(db, persona, owner)
    if reason is not None or await gate.block(db, persona, "join", member=True) is not None:
        raise sharing.fail("invite_unavailable")
    if payload.agree is not True:
        raise sharing.fail("invite_agree_required")
    profile = sharing.validate_profile(payload.profile or {}, partial=False)
    sharing.check_minor_scope(persona, profile.get("myRelation"))
    prefill = share.prefill or {}
    at = sharing.now()
    stored = dict(profile)
    if prefill.get("familyRow"):
        stored["familyRow"] = prefill["familyRow"]
    stored |= {"editedBy": "member", "editedAt": at.isoformat()}
    share_id, persona_id = share.id, persona.id
    # 동의를 먼저 남기고 같은 트랜잭션에서 자리를 잡는다(자리를 못 잡으면 동의도 남지 않는다)
    await legal.record(
        db,
        user,
        legal.persona_type(legal.SHARE_MEMBER_TYPE, persona_id),
        True,
        persona_id=persona_id,
    )
    try:
        claimed = await sharing.claim_seat(db, share_id, user.id, stored, at)
        if not claimed:
            await db.rollback()
            raise sharing.fail("invite_used")
        await db.commit()
    except IntegrityError:
        # 같은 사람이 다른 링크로 방금 들어왔다(대화 상대마다 한 자리)
        await db.rollback()
        mine = await db.scalar(
            select(PersonaShare).where(
                PersonaShare.persona_id == persona_id, PersonaShare.user_id == user.id
            )
        )
        response.status_code = status.HTTP_200_OK
        return Envelope(
            data={"personaId": persona_id, "shareId": mine.id if mine else None, "already": True}
        )
    seat = await db.get(PersonaShare, share_id, populate_existing=True)
    await sharing.prebuild(db, await sharing.chat_access(db, persona_id, user))
    edited = any((profile.get(k) or None) != (prefill.get(k) or None) for k in sharing.PROFILE_KEYS)
    await analytics.track(
        db,
        "share_joined",
        user_id=user.id,
        persona_id=persona_id,
        prefilled=bool(sharing.public_profile(prefill)),
        edited=edited,
    )
    return Envelope(data={"personaId": persona_id, "shareId": seat.id, "already": False})
