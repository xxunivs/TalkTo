"""돌아가신 분 대화 상대의 신청 자격과 유족 명단(2026-10-22 법률 자문 · WO 5 · SPEC §7).

유족 대표 한 사람의 동의로 다른 유족의 청구권이 사라지지 않으므로, 만든 분이 고인과의 관계와 고인의
배우자·자녀(이들이 없으면 부모) 명단을 적고, 명단의 분들께 알렸고 반대가 없다는 확약
(pledge_deceased)을 한다. 사망진단서 같은 증빙은 받지 않는다.

- 명단(제3자 정보)은 그 대화 상대를 지울 때까지 둔다(CASCADE). 연락처는 선택이고, 있으면 대표가
  제작 사실을 직접 알릴 수 있게 관리자 화면에 문자·메일 문안을 둔다(templates — 자동 발송 없음).
- 고쳐 쓰면 덮기 전 선언을 history 에 쌓는다(이의가 왔을 때 그때 명단에 누가 있었는지 지우지 않게).
  멈춘(hold) 대화 상대는 고치지 못한다(API 가 막는다 — 이의를 보낸 분을 명단에서 빼는 일을 막는다).
- 새로 만드는 고인 대화 상대는 만들기(build) 전에 받는다. 이미 만든 것은 막지 않고 안내 카드만
  (대표 답 2026-10-23 · gate).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import BereavedDeclaration, Persona, User
from app.services import intake, legal

PLEDGE_DOC = "pledge_deceased"
NAME_MAX = 20
CONTACT_MAX = 60
MEMBERS_MAX = 12
HISTORY_MAX = 10

APPLICANT_REQUIRED = "고인과 어떤 관계인지 골라 주세요."
CONSENTING_REQUIRED = (
    "동의해 준 유족(배우자·자녀·손주·부모 가운데 한 분)의 이름과 관계를 적어 주세요."
)
MEMBERS_REQUIRED = "고인의 배우자·자녀(안 계시면 부모)를 적거나 '적을 분이 없어요'를 골라 주세요."
NAME_TOO_LONG = f"이름은 {NAME_MAX}자까지 적어 주세요."
CONTACT_TOO_LONG = f"연락처는 {CONTACT_MAX}자까지 적어 주세요."
TOO_MANY = f"{MEMBERS_MAX}분까지 적을 수 있어요."
PLEDGE_REQUIRED = "확인할 내용에 체크해 주세요."


class BereavedError(ValueError):
    """화면에 그대로 보일 입력 오류(400)."""


async def get(db: AsyncSession, persona_id: str) -> BereavedDeclaration | None:
    """유족 선언이 있으면 그 줄(적을 분이 없어요 또는 명단 한 줄 이상), 없으면 None."""
    row = await db.scalar(
        select(BereavedDeclaration).where(BereavedDeclaration.persona_id == persona_id)
    )
    if row is None or not (row.none_left or row.members):
        return None
    return row


def _text(value) -> str:
    return " ".join(str(value).split()) if isinstance(value, str) else ""


def _members(value) -> list[dict]:
    """명단 줄들. 이름도 연락처도 없는 빈 줄은 버린다(화면의 빈 줄)."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise BereavedError(MEMBERS_REQUIRED)
    rows = []
    for item in value:
        if not isinstance(item, dict):
            raise BereavedError(MEMBERS_REQUIRED)
        name, contact = _text(item.get("name")), _text(item.get("contact"))
        if not name and not contact:
            continue
        if not name or item.get("relation") not in intake.BEREAVED_RELATIONS:
            raise BereavedError(MEMBERS_REQUIRED)
        if len(name) > NAME_MAX:
            raise BereavedError(NAME_TOO_LONG)
        if len(contact) > CONTACT_MAX:
            raise BereavedError(CONTACT_TOO_LONG)
        rows.append({"name": name, "relation": item["relation"], "contact": contact or None})
    if len(rows) > MEMBERS_MAX:
        raise BereavedError(TOO_MANY)
    return rows


def validate(payload: dict) -> dict:
    """PUT /personas/{id}/bereaved 본문을 본다(SPEC §7.2). 틀리면 BereavedError(글)."""
    applicant = payload.get("applicantRelation")
    if applicant not in intake.APPLICANT_RELATIONS:
        raise BereavedError(APPLICANT_REQUIRED)
    consenting_name = consenting_relation = None
    if applicant == "other":
        consenting_name = _text(payload.get("consentingName"))
        consenting_relation = payload.get("consentingRelation")
        if not consenting_name or consenting_relation not in intake.CONSENTING_RELATIONS:
            raise BereavedError(CONSENTING_REQUIRED)
        if len(consenting_name) > NAME_MAX:
            raise BereavedError(NAME_TOO_LONG)
    members = _members(payload.get("members"))
    none_left = payload.get("noneLeft") is True
    if none_left == bool(members):
        # 적을 분이 없다고 했으면 명단은 비어 있고, 아니면 한 분 이상
        raise BereavedError(MEMBERS_REQUIRED)
    if payload.get("pledge") is not True:
        raise BereavedError(PLEDGE_REQUIRED)
    return {
        "applicant_relation": applicant,
        "consenting_name": consenting_name,
        "consenting_relation": consenting_relation,
        "members": members,
        "none_left": none_left,
    }


def _snapshot(row: BereavedDeclaration) -> dict:
    declared = row.declared_at
    return {
        "applicantRelation": row.applicant_relation,
        "consentingName": row.consenting_name,
        "consentingRelation": row.consenting_relation,
        "members": list(row.members or []),
        "noneLeft": bool(row.none_left),
        "declaredBy": row.declared_by,
        "declaredAt": declared.isoformat() if declared else None,
        "pledgeVersion": row.pledge_version,
    }


async def save(
    db: AsyncSession, user: User, persona: Persona, payload: dict
) -> BereavedDeclaration:
    """신청 자격과 유족 명단을 적고 확약을 남긴다(PUT /personas/{id}/bereaved). 덮어쓰면 앞 선언은
    history 에 쌓는다. 틀리면 BereavedError. commit 은 부르는 쪽."""
    values = validate(payload)
    row = await db.scalar(
        select(BereavedDeclaration).where(BereavedDeclaration.persona_id == persona.id)
    )
    if row is None:
        row = BereavedDeclaration(persona_id=persona.id, history=[])
        db.add(row)
    elif row.declared_at is not None:
        row.history = [*(row.history or []), _snapshot(row)][-HISTORY_MAX:]
    for key, value in values.items():
        setattr(row, key, value)
    row.declared_by = user.id
    row.declared_at = datetime.now(UTC)
    row.pledge_version = legal.version_of(PLEDGE_DOC)
    await legal.record(
        db,
        user,
        legal.persona_type("bereaved_pledge", persona.id),
        True,
        persona_id=persona.id,
        doc=PLEDGE_DOC,
    )
    return row


def view(row: BereavedDeclaration | None) -> dict | None:
    """만든 분·관리자가 보는 선언(만든 분이 적은 연락처 포함)."""
    if row is None:
        return None
    declared = row.declared_at
    if declared is not None and declared.tzinfo is None:
        declared = declared.replace(tzinfo=UTC)
    return {
        "applicantRelation": row.applicant_relation,
        "consentingName": row.consenting_name,
        "consentingRelation": row.consenting_relation,
        "members": [dict(m) for m in row.members or []],
        "noneLeft": bool(row.none_left),
        "declaredAt": declared,
        "pledgeVersion": row.pledge_version,
    }


# ────────────────────────── 직접 알림 문안(관리자 수동) ──────────────────────────


def _value(text: str | None) -> str:
    return text.strip() if text and text.strip() else legal.PENDING


def templates(persona: Persona, decl: BereavedDeclaration, owner: User | None) -> list[dict]:
    """명단에 연락처가 있는 분마다 대표가 손으로 보낼 문자·메일 문안(SPEC §7.5). 자동 발송 없음.

    연락처에 @ 가 있으면 메일, 아니면 문자로 본다. 값은 사업자 정보(설정)와 만든 분이 적은 것으로
    채운다. 메일 본문은 문자 본문의 '이 문자에 답하시거나'를 '이 메일에 답하시거나'로 바꿔 쓴다.
    """
    info = legal.business()
    basics = persona.basics or {}
    deceased = basics.get("name") or persona.name
    applicant = intake.APPLICANT_RELATIONS.get(decl.applicant_relation, "가족")
    maker = basics.get("myName") or (owner.name if owner else None) or "만든 분"
    email, phone = _value(info["email"]), _value(info["phone"])
    out = []
    for index, member in enumerate(decl.members or []):
        contact = (member.get("contact") or "").strip()
        if not contact:
            continue
        name = member.get("name") or "유족"
        text = (
            f"[TalkTo] 안녕하세요, {name}님. TalkTo(톡투) 대표 {_value(info['owner'])}이에요. "
            f"고 {deceased}님의 {applicant} {maker}님께서 고인의 목소리와 이야기로 TalkTo에서 "
            f"AI 대화 상대를 만드셨고, 신청하실 때 {name}님께 알렸다고 적어 주셨어요. "
            f"원하지 않으시면 이 문자에 답하시거나 {email}, {phone}로 알려 주세요. "
            "알려 주시면 바로 대화를 멈추고 7일 안에 지워요."
        )
        item = {"memberIndex": index, "name": name, "contact": contact}
        if "@" in contact:
            body = text.replace("이 문자에 답하시거나", "이 메일에 답하시거나")
            body += (
                "\n\nTalkTo는 신청하신 분과 그분이 초대한 가족만 이 대화 상대와 이야기할 수 있게 "
                "하고, 고인의 이름으로 먼저 연락하지 않아요. 개인정보 처리방침: "
                f"{get_settings().frontend_url.rstrip('/')}/#/legal/privacy_policy\n"
                f"{_value(info['name'])} · 대표 {_value(info['owner'])} · {email} · {phone}"
            )
            item |= {
                "channel": "email",
                "subject": f"[TalkTo] 고 {deceased}님의 AI 대화 상대를 만든 사실을 알려 드려요",
                "text": body,
            }
        else:
            item |= {"channel": "sms", "text": text}
        out.append(item)
    return out
