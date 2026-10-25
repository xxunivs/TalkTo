"""만 19세 미만 대화 상대(2026-10-22 법률 자문 · WO 4 · SPEC §6).

만든 분이 적은 값(minor · birthYm · guardianBasis · notGuardian)은 Persona.basics, 시스템 상태(확인
문자·14·19세 재동의)는 persona_minor_checks 에 둔다. 나이 셈은 app/services/ages.py.

- 법정대리인 확인(guardian_ok): guardian_attest:{pid} 기록이 있고, 만 14세 미만이면 법정대리인
  동의 둘(guardian_consent · guardian_overseas)과 확인 문자 통과까지. 부모가 아닌 분이 예전에 만든
  아이(notGuardian)는 늘 모자라다(게이트가 처리·목소리·새 초대를 막는다 — 대화는 그대로).
- 확인 문자: 번호는 가린 것만 남기고 확인 번호는 해시만 둔다. manual(대표가 손으로 보냄)일 때만
  대표가 '보냈어요'를 누를 때까지 전체 번호와 확인 번호를 둔다(관리자 대기열).
- 나이가 바뀔 때(sweep, 하루 한 번): 14·19세가 되는 달에 알리고, 그다음 달 1일부터 6개월 안에 새
  동의 녹음이 통과하지 않으면 멈춘다(지우지 않는다 — 지우기는 holds.sweep 이 7일 뒤 기존 삭제
  경로로). 되돌릴 수 없는 일로 이어지므로 안전장치를 둔다: 기한 미리 알림(d30 · d7)을 실제로 남긴
  뒤에만, 멈추기 직전에 통과를 다시 보고, 한 번에 멈추는 수가 많으면 멈추지 않고 운영 알림만.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import LifeStatus, Person, Persona, PersonaMinorCheck, User
from app.services import ages, holds, legal, notices, notify, sms, subject_consent

logger = logging.getLogger(__name__)

GUARDIAN_DOC = "guardian_consent"
# 부모가 아닌 분이 예전에 만든 아이의 대화 상대(카드·법정대리인 화면 글)
NOT_GUARDIAN_TEXT = (
    "아이의 대화 상대는 그 아이의 부모님만 만들 수 있어요. 이 대화 상대는 새 녹음 처리와 목소리 "
    "다시 만들기를 멈췄어요. 궁금한 점은 고객센터로 알려 주세요."
)
# 확인 문자: 다시 받기 간격·하루 상한·확인 번호 틀림 상한
RESEND_AFTER = timedelta(seconds=60)
DAILY_SENDS = 5
MAX_TRIES = 5
# 나이 셈 실수로 한꺼번에 멈추는 사고를 막는다(넘으면 멈추지 않고 운영 알림만)
MAX_HOLDS_PER_SWEEP = 3
# 기한 미리 알림을 남긴 뒤 멈추기까지 적어도 이만큼(7일 전 알림이 하루 안팎 늦게 갈 수 있다)
NOTICE_LEAD = timedelta(days=6)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite 는 시간대를 빼고 돌려준다. 모두 UTC 로 저장하므로 UTC 로 본다."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _kst_start(day: date) -> datetime:
    """그날 한국 시간 0시(UTC 로 저장)."""
    return datetime(day.year, day.month, day.day, tzinfo=ages.KST).astimezone(UTC)


def _add_months(day: date, months: int) -> date:
    years, month = divmod(day.month - 1 + months, 12)
    # 늘 1일이라 짧은 달 걱정이 없다(reconsent_from 은 그달 1일)
    return date(day.year + years, month + 1, day.day)


def last_day(due_at: datetime) -> date:
    """기한(그날 0시)의 바로 앞날 = 받아야 하는 마지막 날. 2026-10-01 0시 → 9월 30일."""
    return (_aware(due_at).astimezone(ages.KST) - timedelta(seconds=1)).date()


# ────────────────────────── 지금 상태 ──────────────────────────


@dataclass
class MinorState:
    """만 19세 미만 대화 상대의 지금 상태(gate · 상세 minor 칸).

    guardian_ok: guardian_attest:{pid} 기록이 있고, mode 가 minor_child 이면 guardian_consent ·
    guardian_overseas 기록과 sms_verified_at 까지 있음. missing 은 빠진 것(attest · consent ·
    overseas · sms). 만 19세가 지나 mode 가 adult 면 법정대리인은 더 보지 않는다(본인 동의 녹음을
    다시 받는다 — subject_consent).
    """

    age: int
    mode: str
    guardian_ok: bool
    missing: list[str] = field(default_factory=list)
    reconsent_kind: str | None = None
    reconsent_from: datetime | None = None
    due_at: datetime | None = None
    not_guardian: bool = False


def is_minor(persona: Persona) -> bool:
    """만 19세 미만이라고 답한 대화 상대(가족 공유 범위·관리자 목록 기준 — 이 칸만 본다)."""
    return (persona.basics or {}).get("minor") is True


async def owner_id(db: AsyncSession, persona: Persona) -> str | None:
    return await db.scalar(select(Person.owner_user_id).where(Person.id == persona.person_id))


async def check_row(
    db: AsyncSession, persona_id: str, *, create: bool = False
) -> PersonaMinorCheck | None:
    row = await db.scalar(
        select(PersonaMinorCheck).where(PersonaMinorCheck.persona_id == persona_id)
    )
    if row is None and create:
        row = PersonaMinorCheck(persona_id=persona_id, reminders={})
        db.add(row)
        await db.flush()
    return row


def _age(basics: dict, today: date) -> int:
    try:
        return max(ages.age_on(basics.get("birthYm") or "", today), 0)
    except ValueError:
        return 0


async def state(
    db: AsyncSession, persona: Persona, *, today: date | None = None
) -> MinorState | None:
    """미성년이라고 답한 살아 계신 분(나 자신 아님)의 대화 상대면 그 상태, 아니면 None(돌아가신
    자녀는 고인 규칙을 따른다 — None)."""
    basics = persona.basics or {}
    if basics.get("minor") is not True:
        return None
    today = today or ages.today_kst()
    mode = ages.consent_mode(basics, today)
    if mode is None:
        return None
    not_guardian = basics.get("notGuardian") is True
    check = await check_row(db, persona.id)
    missing: list[str] = []
    if mode != "adult":
        uid = await owner_id(db, persona)

        async def has(prefix: str) -> bool:
            return uid is not None and await legal.has(
                db, uid, legal.persona_type(prefix, persona.id)
            )

        if not_guardian or not await has("guardian_attest"):
            missing.append("attest")
        if mode == "minor_child" and not not_guardian:
            for key, prefix in (("consent", "guardian_consent"), ("overseas", "guardian_overseas")):
                if not await has(prefix):
                    missing.append(key)
            if check is None or check.sms_verified_at is None:
                missing.append("sms")
    pending = check is not None and check.reconsent_kind and check.reconsent_done_at is None
    return MinorState(
        age=_age(basics, today),
        mode=mode,
        guardian_ok=not missing,
        missing=missing,
        reconsent_kind=check.reconsent_kind if pending else None,
        reconsent_from=_aware(check.reconsent_from) if pending else None,
        due_at=_aware(check.reconsent_due_at) if pending else None,
        not_guardian=not_guardian,
    )


def view(found: MinorState | None) -> dict | None:
    """상세의 minor 칸(SPEC §11.3)."""
    if found is None:
        return None
    return {
        "age": found.age,
        "mode": found.mode,
        "guardianOk": found.guardian_ok,
        "missing": list(found.missing),
        "notGuardian": found.not_guardian,
        "reconsentKind": found.reconsent_kind,
        "reconsentFrom": found.reconsent_from,
        "dueAt": found.due_at,
    }


async def record_attest(db: AsyncSession, user: User, persona: Persona) -> None:
    """만드는 분이 아이의 법정대리인(부모)이라고 확인한 기록. 상태 줄도 이때 만든다(나이 바뀜
    sweep 은 이 줄을 만든 때를 '지켜보기 시작한 때'로 본다 — 만들 때 이미 그 나이였는지). commit 은
    부르는 쪽."""
    await check_row(db, persona.id, create=True)
    await legal.record(
        db,
        user,
        legal.persona_type("guardian_attest", persona.id),
        True,
        persona_id=persona.id,
        doc=GUARDIAN_DOC,
    )


# ────────────────────────── 법정대리인 확인 문자 ──────────────────────────


class GuardianError(Exception):
    """법정대리인 확인 오류. API 가 {"detail", "code"} 로 바꾼다."""

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


async def guardian_view(db: AsyncSession, persona: Persona) -> dict:
    """GET /personas/{id}/guardian(SPEC §6.3)."""
    found = await state(db, persona)
    check = await check_row(db, persona.id)
    waiting = check is not None and check.sms_code_hash and check.sms_verified_at is None
    return {
        "needed": found is not None and not found.guardian_ok,
        "mode": found.mode if found else None,
        "guardianOk": found.guardian_ok if found else None,
        "missing": list(found.missing) if found else [],
        "notGuardian": found.not_guardian if found else False,
        "notGuardianText": NOT_GUARDIAN_TEXT if found and found.not_guardian else None,
        "phoneMasked": check.sms_phone_masked if check else None,
        "smsVerifiedAt": _aware(check.sms_verified_at) if check else None,
        # manual: TalkTo 가 문자를 보내기 전(대기열에 있음)
        "smsQueued": bool(check and check.sms_phone_pending),
        "codeExpiresAt": _aware(check.sms_code_expires_at) if waiting else None,
        "under14Open": sms.under14_open(),
        "labels": legal.GUARDIAN_LABELS,
    }


async def _owner_has(db: AsyncSession, persona: Persona, prefix: str) -> bool:
    uid = await owner_id(db, persona)
    return uid is not None and await legal.has(db, uid, legal.persona_type(prefix, persona.id))


async def request_code(db: AsyncSession, persona: Persona, phone, now: datetime) -> dict:
    """확인 문자 요청(POST …/guardian/sms). 만 14세 미만·법정대리인 동의 둘 뒤에만. commit 은
    부르는 쪽.

    manual 은 보내지 않고 대기열에 둔다({queued: true}). 번호는 가린 것만 남긴다(manual 은 대표가
    보낼 때까지만 전체 번호).
    """
    found = await state(db, persona)
    if found is None or found.mode != "minor_child":
        raise GuardianError(
            409, "not_needed", "확인 문자는 만 14세 미만 아이의 대화 상대에서만 받아요."
        )
    if found.not_guardian:
        raise GuardianError(409, "not_guardian", NOT_GUARDIAN_TEXT)
    digits = sms.phone_digits(phone)
    if digits is None:
        raise GuardianError(400, "bad_phone", "휴대전화 번호를 확인해 주세요.")
    if not (
        await _owner_has(db, persona, "guardian_consent")
        and await _owner_has(db, persona, "guardian_overseas")
    ):
        raise GuardianError(409, "guardian_consent", "먼저 법정대리인 동의에 체크해 주세요.")
    provider = sms.get_provider()
    if provider is None:
        raise GuardianError(409, "sms_unavailable", "지금은 확인 문자를 보낼 수 없어요.")
    check = await check_row(db, persona.id, create=True)
    if check.sms_verified_at is not None:
        raise GuardianError(409, "sms_verified", "확인 문자 확인을 이미 마쳤어요.")
    last = _aware(check.sms_requested_at)
    if last is not None and now - last < RESEND_AFTER:
        raise GuardianError(409, "sms_wait", "1분 뒤에 다시 받아 주세요.")
    if last is None or ages.today_kst(last) != ages.today_kst(now):
        check.sms_sent_count = 0
    if (check.sms_sent_count or 0) >= DAILY_SENDS:
        raise GuardianError(
            409, "sms_daily", "오늘은 확인 문자를 더 받을 수 없어요. 내일 다시 해 주세요."
        )
    manual = provider.name == sms.MANUAL
    code = f"{secrets.randbelow(10**6):06d}"
    hours = get_settings().sms_manual_code_hours
    expires = now + (timedelta(hours=hours) if manual else timedelta(minutes=sms.CODE_MINUTES))
    check.sms_code_hash = sms.code_hash(code, persona.id)
    check.sms_code_expires_at = expires
    check.sms_attempts = 0
    check.sms_phone_masked = sms.mask_phone(digits)
    check.sms_provider = provider.name
    check.sms_requested_at = now
    check.sms_sent_count = (check.sms_sent_count or 0) + 1
    if manual:
        # 대표가 관리자 화면에서 이 번호로 직접 보낸다('보냈어요'를 누르면 번호·확인 번호를 지운다)
        check.sms_phone_pending, check.sms_code_pending = digits, code
        check.sms_message_id = None
        notify.send(
            "📱 법정대리인 확인 문자 1건을 보내 주세요(GUARDIAN_SMS_PENDING) — 관리자 화면 "
            "'확인 문자 보내기'에서 확인해 주세요",
            channel="alerts",
        )
        return {"queued": True, "expiresAt": expires, "phoneMasked": check.sms_phone_masked}
    try:
        result = await provider.send(digits, sms.message(code, sms.valid_for(False)))
    except sms.SmsError as error:
        logger.warning("guardian sms failed: %s", type(error).__name__)
        raise GuardianError(
            502, "sms_failed", "확인 문자를 보내지 못했어요. 잠시 뒤 다시 해 주세요."
        ) from error
    check.sms_phone_pending = check.sms_code_pending = None
    check.sms_message_id = result.message_id
    check.sms_last_sent_at = now
    return {"queued": False, "expiresAt": expires, "phoneMasked": check.sms_phone_masked}


async def verify_code(db: AsyncSession, user: User, persona: Persona, code, now: datetime) -> None:
    """확인 번호 넣기(POST …/guardian/sms/verify). 맞으면 sms_verified_at 과 기록. commit 은 부르는
    쪽(틀린 횟수도 남겨야 하니 틀려도 commit 한다 — GuardianError 를 받은 API 가 commit 한다)."""
    check = await check_row(db, persona.id)
    if check is None or not check.sms_code_hash:
        raise GuardianError(409, "sms_none", "먼저 확인 문자를 받아 주세요.")
    renew = GuardianError(400, "sms_renew", "확인 번호를 새로 받아 주세요.")
    expires = _aware(check.sms_code_expires_at)
    if (check.sms_attempts or 0) >= MAX_TRIES or expires is None or expires <= now:
        raise renew
    text = str(code or "").strip()
    if not secrets.compare_digest(sms.code_hash(text, persona.id), check.sms_code_hash):
        check.sms_attempts = (check.sms_attempts or 0) + 1
        if check.sms_attempts >= MAX_TRIES:
            raise renew
        raise GuardianError(400, "sms_wrong", "확인 번호가 맞지 않아요.")
    check.sms_verified_at = now
    # 한 번 쓴 확인 번호는 다시 쓰지 못하게 지운다(manual 대기열에 남았으면 그것도)
    check.sms_code_hash = None
    check.sms_code_expires_at = None
    check.sms_phone_pending = check.sms_code_pending = None
    legal.log(
        db,
        user.id,
        legal.persona_type("guardian_sms", persona.id),
        True,
        persona_id=persona.id,
        doc=GUARDIAN_DOC,
        channel="sms",
    )


async def sms_queue(db: AsyncSession, now: datetime) -> list[dict]:
    """manual 대기열(관리자 '확인 문자 보내기'): 대표가 보낼 번호·확인 번호·문자 글. 전체 번호는
    이 화면에서만 보인다. 오래된 요청부터."""
    rows = (
        await db.execute(
            select(PersonaMinorCheck, Persona)
            .join(Persona, Persona.id == PersonaMinorCheck.persona_id)
            .where(PersonaMinorCheck.sms_phone_pending.is_not(None))
            .order_by(PersonaMinorCheck.sms_requested_at)
        )
    ).all()
    out = []
    for check, persona in rows:
        expires = _aware(check.sms_code_expires_at)
        out.append(
            {
                "personaId": persona.id,
                "title": holds.title(persona),
                "phone": check.sms_phone_pending,
                "code": check.sms_code_pending,
                "text": sms.message(check.sms_code_pending or "", sms.valid_for(True)),
                "requestedAt": _aware(check.sms_requested_at),
                "expiresAt": expires,
                "expired": expires is None or expires <= now,
            }
        )
    return out


async def mark_sent(db: AsyncSession, persona: Persona, now: datetime) -> bool:
    """대표가 manual 확인 문자를 보냈다: 전체 번호·확인 번호를 지우고(가린 번호·해시는 남김) 만든
    분께 알린다. 보낼 것이 없었으면 False. commit 은 부르는 쪽."""
    check = await check_row(db, persona.id)
    if check is None or not check.sms_phone_pending:
        return False
    check.sms_phone_pending = check.sms_code_pending = None
    check.sms_last_sent_at = now
    check.sms_provider = sms.MANUAL
    uid = await owner_id(db, persona)
    if uid is not None:
        label = notices.persona_label(persona)
        await notices.create(
            db,
            uid,
            "guardian_sms_sent",
            "확인 문자를 보냈어요",
            f"{label}의 법정대리인 확인 문자를 보냈어요. 받으신 확인 번호를 넣어 주세요.",
            persona_id=persona.id,
            action={"label": "확인 번호 넣기", "route": f"/p/{persona.id}/guardian"},
        )
    return True


# ────────────────────────── 나이가 바뀔 때 ──────────────────────────

# (나이, 알린 때 칸, 알림 종류)
TURNS = (
    (19, "turned19_notified_at", "minor_turns19"),
    (14, "turned14_notified_at", "minor_turns14"),
)


def _turn_texts(kind: str, label: str, due: datetime) -> tuple[str, str]:
    deadline = holds.month_day(last_day(due))
    if kind == "minor_turns14":
        return (
            "아이 본인의 동의 녹음이 필요해요",
            f"아이가 만 14세가 되는 달이에요. {label}를 계속 쓰려면 아이 본인의 동의 녹음이 "
            f"필요해요. {deadline}까지 받지 못하면 대화가 멈추고 지워져요.",
        )
    return (
        "본인 동의를 다시 받아 주세요",
        f"아이가 만 19세가 되는 달이에요. {label}를 계속 쓰려면 본인의 동의 녹음을 다시 받아야 "
        f"해요. {deadline}까지 받지 못하면 대화가 멈추고 지워져요.",
    )


def _consent_action(persona: Persona) -> dict:
    return {"label": "동의 녹음 하기", "route": f"/p/{persona.id}/consent"}


async def _turning(
    db: AsyncSession,
    persona: Persona,
    uid: str,
    check: PersonaMinorCheck,
    ym: str,
    today: date,
    now: datetime,
) -> int:
    """14·19세가 되는 달이면 한 번 알리고 재동의 기한을 정한다. 알렸으면 1.

    지켜보기 시작한 때(check.created_at — 만들거나 미성년이라고 답한 무렵)에 이미 그 나이였으면 그
    나이의 동의를 받은 것이라 알리지 않는다."""
    since = ages.today_kst(check.created_at or now)
    for years, notified, kind in TURNS:
        if today < ages.turning_month(ym, years):
            continue
        if getattr(check, notified) is not None or ages.age_on(ym, since) >= years:
            return 0
        start = ages.reconsent_from(ym, years)
        due = _kst_start(_add_months(start, get_settings().minor_reconsent_months))
        check.reconsent_kind = str(years)
        check.reconsent_from = _kst_start(start)
        check.reconsent_due_at = due
        check.reconsent_done_at = None
        check.reminders = {}
        setattr(check, notified, now)
        title, body = _turn_texts(kind, notices.persona_label(persona), due)
        await notices.create(
            db,
            uid,
            kind,
            title,
            body,
            persona_id=persona.id,
            action=_consent_action(persona),
            dedupe_key=f"minor{years}:{persona.id}",
            mail=True,
        )
        return 1
    return 0


async def _remind(
    db: AsyncSession,
    persona: Persona,
    uid: str,
    check: PersonaMinorCheck,
    today: date,
    now: datetime,
) -> int:
    """기한 30일 전·7일 전 미리 알림(각 한 번). 7일도 안 남았는데 7일 전 알림을 아직 못 했으면
    (서버가 멈춰 있었거나 늦게 답한 경우) 알려 드리지 않은 채 멈추지 않게 기한을 지금부터 7일 뒤로
    미루고 알린다. 알렸으면 1."""
    due = _aware(check.reconsent_due_at)
    reminders = dict(check.reminders or {})
    left = due - now
    key = None
    if "d7" not in reminders and left <= timedelta(days=7):
        key, kind = "d7", "minor_due_d7"
        if left < NOTICE_LEAD:
            due = now + timedelta(days=7)
            check.reconsent_due_at = due
        # 30일 전 알림 자리가 지났으면 이 알림이 그것을 대신한다
        reminders.setdefault("d30", now.isoformat())
    elif "d30" not in reminders and left <= timedelta(days=30):
        key, kind = "d30", "minor_due_d30"
    if key is None:
        return 0
    reminders[key] = now.isoformat()
    check.reminders = reminders
    days = max((_aware(due).astimezone(ages.KST).date() - today).days, 1)
    label = notices.persona_label(persona)
    await notices.create(
        db,
        uid,
        kind,
        f"동의 녹음 기한이 {days}일 남았어요",
        f"{label}의 동의 녹음을 {holds.month_day(last_day(due))}까지 받아 주세요. "
        "받지 못하면 대화가 멈추고 지워져요.",
        persona_id=persona.id,
        action=_consent_action(persona),
        dedupe_key=f"minor{key}:{check.reconsent_kind}:{persona.id}",
        mail=True,
    )
    return 1


def _reminded(check: PersonaMinorCheck, now: datetime) -> bool:
    """멈춰도 되나: 30일 전·7일 전 알림을 실제로 남겼고, 7일 전 알림 뒤로 충분히 지났다."""
    reminders = check.reminders or {}
    if "d30" not in reminders or "d7" not in reminders:
        return False
    try:
        sent = _aware(datetime.fromisoformat(reminders["d7"]))
    except (TypeError, ValueError):
        return False
    return now - sent >= NOTICE_LEAD


def _watched(basics: dict, today: date) -> bool:
    """나이 바뀜을 지켜볼 대화 상대인가: 만 19세 미만이라고 답한 살아 계신 분(나 자신 아님).

    부모가 아닌 분이 예전에 만든 아이(notGuardian)는 뺀다. 법정대리인 확인도 아이 본인 동의 녹음도
    받을 길이 없어(게이트 #4) 알려도 할 수 없는 일을 시키게 되고, 기한을 넘기면 멈추고 지우게 된다.
    지우지 않고 대화는 그대로, 관리자 목록(minor_not_guardian)에서 대표가 정한다(SPEC §6.4 ·
    §20 Q14)."""
    if basics.get("minor") is not True or basics.get("notGuardian") is True:
        return False
    return ages.consent_mode(basics, today) is not None


async def _sweep_one(
    db: AsyncSession, persona: Persona, uid: str, today: date, now: datetime
) -> tuple[int, bool]:
    """한 대화 상대의 나이 바뀜 일. (한 일의 수, 기한을 넘겨 멈출 차례인지)."""
    basics = persona.basics or {}
    mode = ages.consent_mode(basics, today)
    ym = basics.get("birthYm") or ""
    try:
        ages.parse_ym(ym)
    except ValueError:
        logger.warning("minor persona %s has no readable birth month", persona.id[:8])
        return 0, False
    check = await check_row(db, persona.id, create=True)
    done = await _turning(db, persona, uid, check, ym, today, now)
    if not check.reconsent_kind:
        return done, False
    if check.reconsent_done_at is not None:
        # 검수 통과 때 찍힌 표시도 지금 나이의 통과로 세지는지 다시 본다(passed 만 믿는다 —
        # 게이트·카드와 같게). 재동의 시작(그다음 달 1일) 전에 옛 나이로 받은 녹음은 세지 않으니,
        # 시작 뒤에도 통과가 없으면 표시를 지우고 다시 알린다
        start = _aware(check.reconsent_from)
        if (start is not None and now < start) or await subject_consent.passed(db, persona, mode):
            return done, False
        logger.warning("minor persona %s reconsent mark has no counted pass", persona.id[:8])
        check.reconsent_done_at = None
    if check.reconsent_due_at is None:
        # 손으로 고친 줄 등 기한이 없으면 멈추지 않고 건너뛴다(다른 대화 상대는 그대로 본다)
        logger.warning("minor persona %s reconsent has no due date", persona.id[:8])
        return done, False
    if await subject_consent.passed(db, persona, mode):
        check.reconsent_done_at = now
        return done + 1, False
    done += await _remind(db, persona, uid, check, today, now)
    return done, _aware(check.reconsent_due_at) <= now and _reminded(check, now)


async def sweep(db: AsyncSession, now: datetime) -> int:
    """하루 한 번: 14·19세가 되는 달 알림, 기한 미리 알림, 기한을 넘기면 멈춤(SPEC §6.5).
    한 일의 수.

    대상: _watched, 멈추지 않은 것. 지우지는 않는다. 대화 상대마다 따로 commit 한다(하나가 실패해도
    그날의 다른 알림은 남고, 실패한 것은 다음 날 다시 — holds.sweep 과 같게. SQLite 는 SAVEPOINT 를
    바르게 다루지 않아 commit · rollback 으로 가른다).
    """
    today = ages.today_kst(now)
    rows = (
        await db.execute(
            select(Persona.id, Persona.basics, Person.owner_user_id)
            .join(Person, Person.id == Persona.person_id)
            .where(Person.life_status == LifeStatus.LIVING, Persona.hold_reason.is_(None))
        )
    ).all()
    watched = [(pid, uid) for pid, basics, uid in rows if _watched(basics or {}, today)]
    done = 0
    overdue: list[str] = []
    for persona_id, uid in watched:
        try:
            # rollback 뒤에도 쓸 수 있게 줄마다 새로 읽는다
            persona = await db.get(Persona, persona_id, populate_existing=True)
            if persona is None or persona.hold_reason:
                continue
            did, late = await _sweep_one(db, persona, uid, today, now)
            await db.commit()
        except Exception:  # 하나가 실패해도 다른 대화 상대는 본다(다음 날 다시 한다)
            logger.exception("minor sweep failed %s", persona_id[:8])
            await db.rollback()
            continue
        done += did
        if late:
            overdue.append(persona_id)
    if len(overdue) > MAX_HOLDS_PER_SWEEP:
        logger.error("minor reconsent overdue for %s personas — not stopping", len(overdue))
        notify.send(
            f"⚠️ 아이 본인 동의 기한을 넘긴 대화 상대가 {len(overdue)}개라 멈추지 않았어요"
            "(MINOR_HOLD_GUARD) — 나이 셈이 맞는지 확인해 주세요",
            channel="alerts",
        )
        overdue = []
    for persona_id in overdue:
        try:
            persona = await db.get(Persona, persona_id, populate_existing=True)
            if persona is None or persona.hold_reason:
                continue
            # 멈추기 직전에 통과를 다시 본다(그사이 검수가 끝났을 수 있다)
            mode = ages.consent_mode(persona.basics or {}, today)
            if await subject_consent.passed(db, persona, mode):
                continue
            await holds.place(db, persona, "minor_consent_missing")
            await db.commit()
        except Exception:
            logger.exception("minor hold failed %s", persona_id[:8])
            await db.rollback()
            continue
        done += 1
    return done
