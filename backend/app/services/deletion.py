"""지운 일시 기록과 지울 때 알림 문구(2026-10-22 법률 자문 · WO 7 · SPEC §9).

'삭제 완료'만 알리면 서버 백업에 남은 것과 어긋나므로, 실제로 지운 곳만 '지웠어요'라고 쓰고 백업이
저절로 지워지는 날(done_at + deletion_backup_days, KST)을 함께 알린다.

- WO 원문의 "맡긴 회사의 운영 시스템에서 지웠어요"는 쓰지 않는다. 대답을 만드는 회사가 받은 글을
  최대 30일 두는 것(overseas.md · 처리방침 5번)을 TalkTo 가 지울 수 없어, 그렇게 알리면 사실과
  다른 고지가 된다(변호사 의견 p.14 · SPEC §20 Q5 · 대표 답 2). TalkTo 서버와 목소리 회사의 만든
  목소리만 '지웠어요'라고 쓰고, 나머지는 '해외 회사로 보내는 정보'로 넘긴다.
- 기록(deletion_records)에는 내용 없이 언제·무엇을·누가만 남긴다(개수만). 5년 뒤 하루 정리가 지운다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import DeletionRecord

# 한국 날짜로 알린다(billing.KST 와 같은 +9시간 고정)
KST = timezone(timedelta(hours=9))
# 기록 보관(처리방침: 지운 일시 기록은 5년)
RECORD_YEARS = 5

_TAIL = (
    "서버 백업에는 {date}까지 남았다가 저절로 지워지고, 해외 회사에 남는 자료는 "
    "'해외 회사로 보내는 정보'에 적은 대로예요."
)
# 목소리 회사에서 만든 목소리까지 지웠다(vendor_ok=True)
VENDOR_DONE = "TalkTo 서버에서 지웠고, 목소리를 만든 회사에서도 만든 목소리를 지웠어요. " + _TAIL
# 지울 목소리가 없었다(vendor_ok=None)
SERVER_ONLY = "TalkTo 서버에서 지웠어요. " + _TAIL
# 목소리 회사에서 지우지 못했다(vendor_ok=False — 운영 알림을 보고 손으로 지운다)
VENDOR_FAILED = (
    "TalkTo 서버에서 지웠어요. 목소리를 만든 회사에서는 아직 지우지 못해 TalkTo가 따로 확인해 "
    "지울게요. " + _TAIL
)
# 종교·건강 동의 거두기(해외 회사에 지워 달라고 하는 것이 없다)
SENSITIVE = "동의를 거두고 적은 답을 TalkTo 서버에서 지웠어요. " + _TAIL


@dataclass
class DeletionResult:
    done_at: datetime
    backup_until: datetime
    # 목소리 회사에서 지웠나(지울 목소리가 없으면 None)
    vendor_ok: bool | None
    message: str


def _aware(value: datetime) -> datetime:
    """SQLite 는 시간대를 지워 돌려준다. 저장할 때 UTC 로 넣으므로 UTC 로 읽는다."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def scope_of(kind: str) -> str:
    """기록 종류 → 알림 문구 종류. 멈춘 까닭(objection 등)으로 지운 것도 대화 상대 지우기다."""
    return {"account": "account", "voice_consent": "voice", "sensitive_consent": "sensitive"}.get(
        kind, "persona"
    )


def backup_date(result: DeletionResult) -> str:
    """'서버 백업에는 ○월 ○일까지'의 날짜(한국 날짜)."""
    k = _aware(result.backup_until).astimezone(KST)
    return f"{k.month}월 {k.day}일"


def message(result: DeletionResult, *, scope: str) -> str:
    """화면·메일에 보일 문장(SPEC §9.2 최종). scope = persona · account · voice · sensitive."""
    date = backup_date(result)
    if scope == "sensitive":
        return SENSITIVE.format(date=date)
    if result.vendor_ok is True:
        return VENDOR_DONE.format(date=date)
    if result.vendor_ok is False:
        return VENDOR_FAILED.format(date=date)
    return SERVER_ONLY.format(date=date)


async def record(
    db: AsyncSession,
    *,
    kind: str,
    user_id: str | None,
    persona_id: str | None,
    requested_by: str,
    vendor_ok: bool | None,
    detail: dict | None = None,
    at: datetime | None = None,
) -> DeletionResult:
    """지운 일시를 deletion_records 에 한 줄 남기고 화면·메일 문구를 만든다. commit 한다(부르는 쪽이
    하던 변경도 함께). kind = persona · account · voice_consent · sensitive_consent · share_member
    (가족 공유를 그만두거나 가족이 나가 그 가족의 대화를 지움 — 문구는 대화 상대 지우기와 같다) ·
    멈춘 까닭. requested_by = owner(이용자 본인 — 나간 가족도) · admin · system.
    detail 은 개수만({"personas": 2})."""
    done = at or datetime.now(UTC)
    until = done + timedelta(days=get_settings().deletion_backup_days)
    db.add(
        DeletionRecord(
            kind=kind[:30],
            user_id=user_id,
            persona_id=persona_id,
            requested_by=requested_by[:10],
            done_at=done,
            backup_until=until,
            vendor_ok=vendor_ok,
            detail=dict(detail or {}),
        )
    )
    await db.commit()
    result = DeletionResult(done, until, vendor_ok, "")
    result.message = message(result, scope=scope_of(kind))
    return result


def response(result: DeletionResult) -> dict:
    """지우기 API 응답(SPEC §9.3). backupUntil 은 한국 날짜(YYYY-MM-DD)."""
    return {
        "deleted": True,
        "doneAt": _aware(result.done_at).isoformat(),
        "backupUntil": _aware(result.backup_until).astimezone(KST).date().isoformat(),
        "vendorOk": result.vendor_ok,
        "message": result.message,
    }


def combine(results: list[DeletionResult]) -> bool | None:
    """여러 번 지운 결과(탈퇴 때 대화 상대마다)의 목소리 회사 삭제. 하나라도 실패면 False, 지운 것이
    있으면 True, 지울 목소리가 하나도 없었으면 None."""
    flags = [r.vendor_ok for r in results if r.vendor_ok is not None]
    if not flags:
        return None
    return all(flags)


def _years_before(at: datetime, years: int) -> datetime:
    try:
        return at.replace(year=at.year - years)
    except ValueError:  # 2월 29일
        return at.replace(year=at.year - years, day=28)


async def cleanup(db: AsyncSession, now: datetime) -> int:
    """지운 지 5년 지난 기록을 지운다. 지운 수. commit 한다."""
    result = await db.execute(
        delete(DeletionRecord).where(DeletionRecord.done_at < _years_before(now, RECORD_YEARS))
    )
    await db.commit()
    return int(result.rowcount or 0)
