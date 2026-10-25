"""법률 자문 반영(2026-10-22) 게이트를 시험에서 미리 통과시키는 도우미(SPEC §17.0-1).

돌아가신 분은 유족 선언 없이 만들 수 없고, 살아 계신 분(나 자신 아님)은 그분 동의 녹음이 통과해야
녹음 처리·목소리 만들기가 열리고, 만 19세 미만 아이는 법정대리인 확인이 먼저다. 다른 것을 시험하는
파일은 이 도우미로 그 조건을 채운다. API(L2·L3)가 아직 없어도 되게 새 표에 줄을 직접 넣는다.

- declare_bereaved: 돌아가신 분 대화 상대의 신청 자격과 유족 명단(기본: 적을 분이 없어요)
- pass_subject_consent: 그분 동의 녹음 통과 줄(관리자 검수를 마친 것처럼)
- guardian_ok: 법정대리인 확인 + 만 14세 미만 동의 둘 + 확인 문자 통과

첫 인자에 AsyncSession 을 주면 코루틴을 돌려준다(await 해서 쓴다, commit 까지 한다). TestClient 나
None 을 주면 따로 세션을 열어 바로 넣는다(동기 시험용 — 돌고 있는 이벤트 루프 밖에서만). 모두 만든
줄의 id 를 돌려준다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    BereavedDeclaration,
    Person,
    Persona,
    PersonaMinorCheck,
    SubjectConsentRecording,
    User,
)
from app.db.session import SessionLocal
from app.services import legal
from app.services.subject_consent import SCRIPT_DOCS

CHECKLIST_ALL = {str(n): True for n in range(1, 11)}


def _dispatch(db_or_client, make):
    if isinstance(db_or_client, AsyncSession):
        return make(db_or_client)

    async def go():
        async with SessionLocal() as db:
            return await make(db)

    return asyncio.run(go())


async def _owner(db: AsyncSession, persona_id: str) -> tuple[Persona, User]:
    persona = await db.get(Persona, persona_id)
    assert persona is not None, f"persona {persona_id} not found"
    person = await db.get(Person, persona.person_id)
    owner = await db.get(User, person.owner_user_id)
    return persona, owner


def declare_bereaved(
    db_or_client,
    persona_id: str,
    *,
    none_left: bool = True,
    members: list[dict] | None = None,
    applicant_relation: str = "grandchild",
):
    """유족 선언(bereaved_declarations)과 확약 기록(bereaved_pledge:{pid}). 이미 있으면 덮는다."""

    async def make(db: AsyncSession) -> str:
        _, owner = await _owner(db, persona_id)
        row = await db.scalar(
            select(BereavedDeclaration).where(BereavedDeclaration.persona_id == persona_id)
        )
        if row is None:
            row = BereavedDeclaration(persona_id=persona_id)
            db.add(row)
        row.applicant_relation = applicant_relation
        row.members = list(members or [])
        row.none_left = none_left and not members
        row.declared_by = owner.id
        row.declared_at = datetime.now(UTC)
        row.pledge_version = legal.version_of("pledge_deceased")
        await legal.record(
            db,
            owner,
            legal.persona_type("bereaved_pledge", persona_id),
            True,
            persona_id=persona_id,
            doc="pledge_deceased",
        )
        await db.commit()
        return row.id

    return _dispatch(db_or_client, make)


def pass_subject_consent(
    db_or_client, persona_id: str, mode: str = "adult", *, at: datetime | None = None
):
    """그분 동의 녹음 통과 줄(status=passed, 10항목 모두 통과)과 통과 기록(subject_consent:{pid}).
    at = 검수한 때(재동의 시험에서 reconsent_from 앞뒤로 둔다). 기본은 지금."""

    async def make(db: AsyncSession) -> str:
        _, owner = await _owner(db, persona_id)
        reviewed = at or datetime.now(UTC)
        attempt = (
            await db.scalar(
                select(func.max(SubjectConsentRecording.attempt)).where(
                    SubjectConsentRecording.persona_id == persona_id
                )
            )
            or 0
        ) + 1
        doc = SCRIPT_DOCS[mode]
        row = SubjectConsentRecording(
            persona_id=persona_id,
            user_id=owner.id,
            attempt=attempt,
            mode=mode,
            script_doc=doc,
            script_version=legal.version_of(doc),
            script_sha256=legal.text_sha256(doc),
            words="사과 바다",
            words_issued_at=reviewed - timedelta(minutes=20),
            expires_at=reviewed + timedelta(minutes=40),
            status="passed",
            client_meta={"steps": [], "format": "webm"},
            submitted_at=reviewed - timedelta(minutes=5),
            checklist=dict(CHECKLIST_ALL),
            reviewed_at=reviewed,
        )
        db.add(row)
        legal.log(
            db,
            owner.id,
            legal.persona_type("subject_consent", persona_id),
            True,
            persona_id=persona_id,
            doc=doc,
            version=row.script_version,
            channel="admin_review",
        )
        await db.commit()
        return row.id

    return _dispatch(db_or_client, make)


def guardian_ok(db_or_client, persona_id: str):
    """법정대리인 확인을 모두 마친 것처럼: guardian_attest · guardian_consent · guardian_overseas
    기록(문서 guardian_consent)과 확인 문자 통과(persona_minor_checks.sms_verified_at). 만 14~18세
    아이에게도 그대로 써도 된다(남는 기록은 해가 없다)."""

    async def make(db: AsyncSession) -> str:
        _, owner = await _owner(db, persona_id)
        for prefix in ("guardian_attest", "guardian_consent", "guardian_overseas"):
            await legal.record(
                db,
                owner,
                legal.persona_type(prefix, persona_id),
                True,
                persona_id=persona_id,
                doc="guardian_consent",
            )
        legal.log(
            db,
            owner.id,
            legal.persona_type("guardian_sms", persona_id),
            True,
            persona_id=persona_id,
            doc="guardian_consent",
            channel="sms",
        )
        check = await db.scalar(
            select(PersonaMinorCheck).where(PersonaMinorCheck.persona_id == persona_id)
        )
        if check is None:
            check = PersonaMinorCheck(persona_id=persona_id)
            db.add(check)
        check.sms_phone_masked = "010-****-0000"
        check.sms_provider = "log"
        check.sms_verified_at = datetime.now(UTC)
        await db.commit()
        return check.id

    return _dispatch(db_or_client, make)
