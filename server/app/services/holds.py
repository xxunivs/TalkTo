"""대화 상대 멈춤과 자동 삭제(2026-10-22 법률 자문 · SPEC §7.4).

이의 접수(고인의 가족·다른 쪽 부모·그분 본인)·그분이 동의 녹음에서 원하지 않음·아이 본인 동의 기한
넘김 → 바로 멈추고(Persona.hold_reason) 7일 뒤 기존 삭제 경로(persona_delete.claim → remove)로
지운다. 멈추면 만든 분·가족의 대화, 모든 처리·목소리·초대가 막힌다(gate #1, 가족은 sharing
pause_reason 'stopped').

지우는 일은 되돌릴 수 없어 안전장치를 둔다: 멈출 때와 지우기 하루 전에 운영 알림(건수만), 지우기
직전에 줄을 다시 읽어 그사이 풀었거나 미뤘는지·동의 녹음이 통과했는지 보고, 한 번에
holds_max_deletes_per_sweep 개까지만 지운다(남으면 운영 알림). persona_auto_delete=False 면 지우지
않고 알림만(비상 스위치). 이의는 시스템이 건 멈춤보다 앞선다: 시스템 멈춤 중에 이의가 오면 까닭을
이의로 바꾸고(take_objection), 열린 이의가 있으면 동의 녹음 통과로 저절로 풀지 않는다
(clear_for_consent — 관리자가 직접 푼다).

알림 글은 TalkTo 가 말하는 문장이다(notices.persona_label). place · clear · extend 의 commit 은
부르는 쪽이다. persona_delete 는 지울 때만 부른다(그쪽이 persona_builder 를 끌어와 import 가 꼬이지
않게).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import (
    Person,
    Persona,
    PersonaObjection,
    SetupStep,
    SubjectConsentRecording,
)
from app.services import ages, notices, notify, subject_consent

logger = logging.getLogger(__name__)

# 관리자가 '이의 접수'로 거는 까닭(그 밖은 시스템이 건다: subject_refused · minor_consent_missing)
OBJECTION_REASONS = ("objection", "prior_wish", "other_parent", "subject_withdrew")
# 시스템이 거는 까닭. 이 멈춤 중에 이의가 오면 까닭을 이의로 바꾼다(그 뒤 동의 녹음 통과·판정
# 바뀜으로 풀리면 이의가 소리 없이 되돌려진다 — take_objection)
SYSTEM_REASONS = ("subject_refused", "minor_consent_missing")
REFUND = "결제한 기간이 남아 있으면 남은 날만큼 돌려드려요."
# 만든 분 카드·알림 본문(SPEC §11.2 stopped). {date} = 지우는 날
STOPPED_BODIES = {
    "objection": "고인의 가족께서 이의를 보내오셔서 대화를 멈췄어요. {date}에 지워요. "
    + REFUND
    + " 궁금한 점은 고객센터로 알려 주세요.",
    "prior_wish": "고인께서 생전에 원하지 않으셨다는 것이 확인되어 대화를 멈췄어요. "
    "{date}에 지워요. " + REFUND,
    "other_parent": "아이의 다른 쪽 부모님께서 반대 의견을 보내오셔서 대화를 멈췄어요. "
    "{date}에 지워요. " + REFUND,
    "subject_withdrew": "그분께서 그만두길 원하셔서 대화를 멈췄어요. {date}에 지워요. " + REFUND,
    "subject_refused": "그분이 동의 녹음에서 원하지 않으셔서 이 대화 상대는 만들 수 없어요. "
    "올리신 녹음과 자료는 {date}에 지워요. 지금 바로 지우실 수도 있어요.",
    "minor_consent_missing": "아이 본인의 동의를 기한까지 받지 못해 대화를 멈췄어요. "
    "{date}에 지워요. 그 전에 동의 녹음이 확인되면 다시 쓸 수 있어요.",
}
STOPPED_TITLE = "이 대화 상대를 멈췄어요"
# 게이트 #1(만든 분)과 가족에게 보이는 글
BLOCKED = "이 대화 상대는 지금 멈췄어요. {date}에 지워요. 자세한 내용은 알림을 확인해 주세요."
MEMBER_BLOCKED = "지금은 이 대화 상대와 이야기할 수 없어요."
# 같은 운영 알림을 되풀이하지 않는 간격(10분마다 돈다)
ALERT_EVERY = timedelta(hours=6)

_alerted: dict[str, datetime] = {}
# 지우기 하루 전 알림을 보낸 대화 상대 → 그때의 지우는 때(미루면 다시 알린다)
_warned: dict[str, datetime] = {}


def _aware(value: datetime | None) -> datetime | None:
    """SQLite 는 시간대를 빼고 돌려준다. 모두 UTC 로 저장하므로 UTC 로 본다."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _now() -> datetime:
    return datetime.now(UTC)


def month_day(value: datetime | date) -> str:
    """화면·알림의 'M월 D일'(한국 날짜)."""
    day = value if not isinstance(value, datetime) else _aware(value).astimezone(ages.KST).date()
    return f"{day.month}월 {day.day}일"


def title(persona: Persona) -> str:
    """관리자 목록의 호칭(호칭 → 관계 → '그분'). 성함은 따로 칸에만."""
    basics = persona.basics or {}
    for value in (basics.get("addressTerm"), basics.get("relation")):
        if value and str(value).strip():
            return str(value).strip()
    return "그분"


def view(persona: Persona) -> dict | None:
    """상세·목록의 hold 칸: {reason, holdAt, deleteAfter} 또는 None."""
    if not persona.hold_reason:
        return None
    return {
        "reason": persona.hold_reason,
        "holdAt": _aware(persona.hold_at),
        "deleteAfter": _aware(persona.delete_after),
    }


def _delete_day(persona: Persona) -> str:
    return month_day(persona.delete_after or _now())


def stopped_body(persona: Persona) -> str:
    """만든 분 카드·알림 본문(까닭마다). 그분이 원하지 않은 대화 상대가 이미 다 만든 것이었으면
    남은 날 환불 문장을 붙인다."""
    template = STOPPED_BODIES.get(persona.hold_reason or "", BLOCKED)
    body = template.format(date=_delete_day(persona))
    if persona.hold_reason == "subject_refused" and persona.setup_step == SetupStep.READY:
        body += " " + REFUND
    return body


def blocked_message(persona: Persona) -> str:
    """게이트 #1 의 만든 분 글(409 stopped)."""
    return BLOCKED.format(date=_delete_day(persona))


async def _owner(db: AsyncSession, persona: Persona) -> str | None:
    return await db.scalar(select(Person.owner_user_id).where(Person.id == persona.person_id))


def _alert(key: str, text: str, now: datetime) -> None:
    last = _alerted.get(key)
    if last is not None and now - last < ALERT_EVERY:
        return
    _alerted[key] = now
    notify.send(text, channel="alerts")


# ────────────────────────── 멈추기·풀기·미루기 ──────────────────────────


async def place(
    db: AsyncSession,
    persona: Persona,
    reason: str,
    *,
    by_user_id: str | None = None,
    days: int | None = None,
) -> None:
    """멈춘다: hold_reason · hold_at · delete_after(= 지금 + days 또는 hold_delete_days)
    + 만든 분 알림 stopped(메일도) + 운영 알림 PERSONA_HOLD(건수만). commit 은 부르는 쪽."""
    now = _now()
    persona.hold_reason = reason
    persona.hold_at = now
    persona.delete_after = now + timedelta(days=days or get_settings().hold_delete_days)
    await _tell_stopped(db, persona, now)
    logger.info("persona %s held (%s) by %s", persona.id[:8], reason, (by_user_id or "system")[:8])
    notify.send(
        "⏸️ 대화 상대 1개를 멈췄어요(PERSONA_HOLD) — 7일 뒤 지워요. 잘못 멈췄으면 관리자 "
        "'멈춘 대화 상대'에서 풀어 주세요",
        channel="alerts",
    )


async def _tell_stopped(db: AsyncSession, persona: Persona, now: datetime) -> None:
    """만든 분 알림 stopped(메일도). 글은 지금 까닭의 것."""
    uid = await _owner(db, persona)
    if uid is None:
        return
    label = notices.persona_label(persona)
    await notices.create(
        db,
        uid,
        "stopped",
        f"{label}를 멈췄어요",
        stopped_body(persona),
        persona_id=persona.id,
        action={"label": "자세히 보기", "route": f"/p/{persona.id}"},
        dedupe_key=f"stopped:{persona.id}:{int(now.timestamp())}",
        mail=True,
    )


async def take_objection(
    db: AsyncSession,
    persona: Persona,
    reason: str,
    *,
    by_user_id: str | None = None,
    days: int | None = None,
) -> str:
    """이의 접수로 멈춘다(SPEC §7.4). 이의 기록은 부르는 쪽이 남긴다. commit 은 부르는 쪽.

    - 멈추지 않았으면 place → "placed".
    - 시스템이 건 멈춤(SYSTEM_REASONS)이면 까닭을 이의로 바꾸고 지우는 날은 더 이른 쪽(지금 +
      days)으로, 만든 분께 바뀐 까닭을 다시 알린다 → "replaced". 그대로 두면 뒤에 동의 녹음이
      통과하거나 판정이 바뀌어 풀릴 때 이의까지 되돌려진다(WO 4 — 다른 쪽 부모의 이의는 멈추고
      지운다).
    - 이미 이의로 멈췄으면 그대로(지우는 날도) → "kept"."""
    if not persona.hold_reason:
        await place(db, persona, reason, by_user_id=by_user_id, days=days)
        return "placed"
    if persona.hold_reason not in SYSTEM_REASONS:
        return "kept"
    now = _now()
    latest = now + timedelta(days=days or get_settings().hold_delete_days)
    current = _aware(persona.delete_after)
    old = persona.hold_reason
    persona.hold_reason = reason
    persona.delete_after = min(current, latest) if current is not None else latest
    await _tell_stopped(db, persona, now)
    logger.info(
        "persona %s hold %s → %s by %s", persona.id[:8], old, reason, (by_user_id or "system")[:8]
    )
    return "replaced"


async def objection_open(db: AsyncSession, persona_id: str) -> bool:
    """아직 처리하지 않은(outcome 없음) 이의 기록이 있나."""
    found = await db.scalar(
        select(PersonaObjection.id)
        .where(PersonaObjection.persona_id == persona_id, PersonaObjection.outcome.is_(None))
        .limit(1)
    )
    return found is not None


async def clear_for_consent(
    db: AsyncSession,
    persona: Persona,
    *,
    by_user_id: str | None = None,
    note: str | None = None,
) -> bool:
    """아이 본인 동의 기한 넘김(minor_consent_missing)으로 멈춘 것을 동의 녹음 통과로 푼다(검수
    통과 · holds.sweep). 열린 이의가 있으면 풀지 않고 운영 알림만 남긴다(이의는 관리자가 직접 풀어야
    한다 — 자동으로 풀면 이의가 소리 없이 되돌려진다). 풀었으면 True. commit 은 부르는 쪽."""
    if persona.hold_reason != "minor_consent_missing":
        return False
    if await objection_open(db, persona.id):
        _alert(
            f"objection_open:{persona.id}",
            "⚠️ 동의 녹음이 확인됐지만 열린 이의가 있어 멈춘 대화 상대 1개를 풀지 않았어요 — 관리자 "
            "'멈춘 대화 상대'에서 확인해 주세요",
            _now(),
        )
        return False
    await clear(db, persona, by_user_id=by_user_id, note=note)
    return True


async def _close_objections(
    db: AsyncSession, persona_id: str, outcome: str, now: datetime, note: str | None = None
) -> None:
    rows = (
        await db.scalars(
            select(PersonaObjection).where(
                PersonaObjection.persona_id == persona_id, PersonaObjection.outcome.is_(None)
            )
        )
    ).all()
    for row in rows:
        row.outcome, row.outcome_at = outcome, now
        if note:
            row.note = (f"{row.note}\n{note}" if row.note else note)[:1000]


async def clear(
    db: AsyncSession,
    persona: Persona,
    *,
    by_user_id: str | None = None,
    note: str | None = None,
) -> None:
    """멈춤을 푼다(이의 철회·착오·동의 녹음 통과): 세 칸을 비우고 열린 이의 기록은 cancelled,
    만든 분 알림 resumed(메일도). 멈춘 것이 아니면 아무것도 하지 않는다. commit 은 부르는 쪽."""
    if not persona.hold_reason:
        return
    now = _now()
    persona.hold_reason = persona.hold_at = persona.delete_after = None
    _warned.pop(persona.id, None)
    await _close_objections(db, persona.id, "cancelled", now, note)
    uid = await _owner(db, persona)
    if uid is not None:
        label = notices.persona_label(persona)
        await notices.create(
            db,
            uid,
            "resumed",
            f"{label}를 다시 쓸 수 있어요",
            f"확인을 마쳐 {label}를 다시 쓸 수 있어요.",
            persona_id=persona.id,
            action={"label": "자세히 보기", "route": f"/p/{persona.id}"},
            dedupe_key=f"resumed:{persona.id}:{int(now.timestamp())}",
            mail=True,
        )
    logger.info("persona %s hold cleared by %s", persona.id[:8], (by_user_id or "system")[:8])


async def extend(
    db: AsyncSession,
    persona: Persona,
    days: int,
    *,
    by_user_id: str | None = None,
    note: str | None = None,
) -> None:
    """지우는 날을 days 일 미룬다. 멈춘 것이 아니면 ValueError. commit 은 부르는 쪽."""
    if not persona.hold_reason or persona.delete_after is None:
        raise ValueError("멈춘 대화 상대가 아니에요.")
    persona.delete_after = _aware(persona.delete_after) + timedelta(days=days)
    if note:
        rows = (
            await db.scalars(
                select(PersonaObjection).where(
                    PersonaObjection.persona_id == persona.id, PersonaObjection.outcome.is_(None)
                )
            )
        ).all()
        for row in rows:
            row.note = (f"{row.note}\n{note}" if row.note else note)[:1000]
    logger.info("persona %s delete moved %s days by %s", persona.id[:8], days, by_user_id)


# ────────────────────────── 자동 삭제 ──────────────────────────


async def _warn_tomorrow(db: AsyncSession, now: datetime) -> None:
    """지우기 하루 전 운영 알림(건수만) — 잘못 멈춘 것을 지우기 전에 알아챌 수 있게."""
    rows = (
        await db.execute(
            select(Persona.id, Persona.delete_after).where(
                Persona.hold_reason.is_not(None),
                Persona.delete_after > now,
                Persona.delete_after <= now + timedelta(days=1),
            )
        )
    ).all()
    fresh = [(pid, _aware(at)) for pid, at in rows if _warned.get(pid) != _aware(at)]
    if not fresh:
        return
    for pid, at in fresh:
        _warned[pid] = at
    notify.send(
        f"⏰ 내일 지울 대화 상대 {len(fresh)}개(PERSONA_DELETE_TOMORROW) — 잘못 멈췄으면 관리자 "
        "'멈춘 대화 상대'에서 풀거나 미뤄 주세요",
        channel="alerts",
    )


async def _still_due(db: AsyncSession, persona_id: str, now: datetime) -> Persona | None:
    """지우기 직전에 줄을 잠그고 다시 읽는다. 그사이 풀었거나 미뤘으면 None."""
    persona = await db.scalar(
        select(Persona)
        .where(Persona.id == persona_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if persona is None or not persona.hold_reason or persona.delete_after is None:
        return None
    if _aware(persona.delete_after) > now:
        return None
    return persona


async def _keep(db: AsyncSession, persona: Persona, now: datetime) -> str | None:
    """까닭이 사라졌으면 지우지 않는다(그 까닭, 아니면 None).

    열린 이의가 있으면 그 이의대로 지운다(동의 녹음 통과·판정 바뀜으로 이의를 되돌리지 않는다 —
    지금은 이의가 오면 까닭을 이의로 바꾸지만 그 전 줄을 위해 한 번 더 본다). 아이 본인 동의 기한
    넘김: 그사이 동의 녹음이 통과했으면 풀어 준다. 그분이 원하지 않음: 그 '원하지 않음' 판정이 아직
    그대로인지 본다(바뀌었으면 관리자가 보게 둔다)."""
    if await objection_open(db, persona.id):
        return None
    if persona.hold_reason == "minor_consent_missing":
        mode = ages.consent_mode(persona.basics or {}, ages.today_kst(now))
        if await subject_consent.passed(db, persona, mode):
            await clear_for_consent(db, persona, note="동의 녹음 통과")
            return "passed"
    if persona.hold_reason == "subject_refused":
        refused = await db.scalar(
            select(SubjectConsentRecording.id)
            .where(
                SubjectConsentRecording.persona_id == persona.id,
                SubjectConsentRecording.status == "refused",
            )
            .limit(1)
        )
        if refused is None:
            return "refusal_changed"
    return None


async def _delete_one(db: AsyncSession, persona_id: str, now: datetime) -> bool:
    """한 대화 상대를 기존 삭제 경로로 지운다. 지웠으면 True(못 지우면 다음 번에 다시)."""
    from app.services import persona_delete

    persona = await _still_due(db, persona_id, now)
    if persona is None:
        await db.rollback()
        return False
    kept = await _keep(db, persona, now)
    if kept is not None:
        await db.commit()
        if kept == "refusal_changed":
            _alert(
                f"refusal:{persona_id}",
                "⚠️ 멈춘 대화 상대 1개의 '원하지 않음' 판정이 바뀌어 지우지 않았어요 — 관리자 "
                "'멈춘 대화 상대'에서 확인해 주세요",
                now,
            )
        return False
    reason = persona.hold_reason
    uid = await _owner(db, persona)
    label = notices.persona_label(persona)
    message, _ = await persona_delete.claim(db, persona)
    if message:
        # 작업이 돌거나 결제를 처리하는 중이다(자물쇠는 claim 이 풀었다). 다음 번에 다시 한다
        await db.rollback()
        return False
    result = await persona_delete.remove(db, persona, requested_by="system", kind=reason)
    await db.execute(
        update(PersonaObjection)
        .where(PersonaObjection.persona_id == persona_id, PersonaObjection.outcome.is_(None))
        .values(outcome="deleted", outcome_at=now)
        .execution_options(synchronize_session=False)
    )
    if uid is not None:
        await notices.create(
            db,
            uid,
            "deleted",
            f"{label}를 지웠어요",
            f"알려 드린 대로 {label}를 지웠어요. {result.message}",
            persona_id=persona_id,
            dedupe_key=f"deleted:{persona_id}",
            mail=True,
        )
    await db.commit()
    _warned.pop(persona_id, None)
    logger.info("held persona %s deleted (%s)", persona_id[:8], reason)
    notify.send("🗑️ 멈춘 대화 상대 1개를 지웠어요(PERSONA_AUTO_DELETED)", channel="alerts")
    return True


async def sweep(db: AsyncSession, now: datetime) -> int:
    """10분마다: 지울 때가 된 멈춘 대화 상대를 지운다(한 번에 holds_max_deletes_per_sweep 개까지).
    지운 수."""
    settings = get_settings()
    try:
        await _warn_tomorrow(db, now)
    except Exception:  # 알림이 실패해도 지우기는 한다(지우기 전 알림은 멈출 때 이미 보냈다)
        logger.exception("hold warning failed")
    due = list(
        (
            await db.scalars(
                select(Persona.id)
                .where(Persona.hold_reason.is_not(None), Persona.delete_after <= now)
                .order_by(Persona.delete_after)
            )
        ).all()
    )
    if not due:
        return 0
    if not settings.persona_auto_delete:
        _alert(
            "auto_delete_off",
            f"🗑️ 지울 때가 된 대화 상대 {len(due)}개 — 자동 삭제가 꺼져 있어요"
            "(PERSONA_AUTO_DELETE=false). 관리자 '멈춘 대화 상대'에서 확인해 주세요",
            now,
        )
        return 0
    limit = max(settings.holds_max_deletes_per_sweep, 0)
    deleted = tried = 0
    for persona_id in due:
        if deleted >= limit:
            break
        tried += 1
        try:
            if await _delete_one(db, persona_id, now):
                deleted += 1
        except Exception:  # 하나가 실패해도 다음 것은 본다(다음 번에 다시 한다)
            logger.exception("held persona delete failed %s", persona_id[:8])
            await db.rollback()
    left = len(due) - tried
    if left > 0:
        _alert(
            "more_due",
            f"⚠️ 지울 때가 된 대화 상대가 {left}개 더 있어요 — 확인해 주세요"
            f"(한 번에 {limit}개까지만 지워요)",
            now,
        )
    return deleted
