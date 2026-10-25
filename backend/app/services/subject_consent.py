"""살아 계신 분(나 자신 아님) 본인의 '그분 동의 녹음'(2026-10-22 법률 자문 · WO 3 · SPEC §5).

만든 분이 앱 안에서 한 번에 녹음하고(멈춤·편집 없음), TalkTo 대표가 듣고 10항목으로 검수한다.
통과 전에는 그분 녹음을 글로 옮기거나 해외 회사로 보내지 않는다(gate).

- 녹음을 시작하면(issue) 서버가 쉬운 낱말 두 개(확인 단어)를 발급한다. 60분 안에 올려야 한다.
- 올린 녹음은 변환하지 않고 받은 그대로 둔다(sha256 로 편집 여부를 본다). 다시 녹음해도 앞선
  줄·파일을 지우지 않고 몇 번째인지 센다. 검수 전에 새로 올리면 앞의 것은 superseded.
- 중간에 멈춘 녹음(화면이 가려짐·마이크 오류·그만두기)은 interrupted 로 남긴다(검수 대상 아님).
- 검수 결과(통과·재요청·보류·원하지 않음)는 만든 분께 앱 알림(+메일)으로 알린다. 원하지 않음은
  대화 상대를 멈추고 7일 뒤 지운다(holds). 통과는 녹음할 때 보인 대본의 판·해시로 기록한다.

**이 녹음은 어떤 해외 회사로도 보내지 않는다**: 서울 저장소(personas/{pid}/subject_consent/)에만
두고, 받아쓰기·목소리 만들기에 쓰지 않는다(Recording 표에 넣지 않는다 — 받아쓰기 작업이 집어 가지
않게). 그래서 이 파일은 AI 서버 모듈을 import 하지 않는다(시험이 소스를 확인한다). 대화 상대를
지우면 줄(CASCADE)과 파일(persona_delete 의 personas/{pid} 접두 지우기)이 함께 지워진다.

게이트·멈춤(gate·holds·minors)은 그쪽이 이 파일을 쓰므로 함수 안에서 가져온다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import (
    Person,
    Persona,
    PersonaMinorCheck,
    Recording,
    SubjectConsentRecording,
    User,
)
from app.services import ages, audio, legal, mailer, notices, notify
from app.services.storage import get_storage

logger = logging.getLogger(__name__)

# 동의 녹음 종류(ages.consent_mode) → 녹음할 때 보이는 대본 문서
SCRIPT_DOCS = {
    "adult": "subject_consent_script",
    "minor_teen": "subject_consent_child",
    "minor_child": "subject_consent_child",
}
# 관리자 목록의 나이 구간(녹음할 때의 종류로)
AGE_BANDS = {
    "adult": "만 19세 이상",
    "minor_teen": "만 14~18세",
    "minor_child": "만 14세 미만",
}

# 확인 단어(두 글자 쉬운 명사, 죽음·병·슬픔과 닿는 낱말은 넣지 않는다). 둘을 골라 보인다
WORDS = (
    "사과 바다 구름 나무 기차 연필 우산 고래 딸기 하늘 시계 모자 가방 의자 사탕 토끼 거울 바람 "
    "노을 호박 감자 당근 수박 포도 오이 참외 자두 국수 만두 김밥 두부 우유 단추 장갑 양말 신발 "
    "안경 열쇠 풍선 공책 지도 편지 나비 참새 오리 사자 기린 여우 하마 염소 거북 꿀벌 개미 들판 "
    "강물 호수 바위 모래 파도 단풍 냄비 접시 수건 이불 베개 창문 대문 시소 그네 버스 택시 소풍 "
    "생일 봄날 가을 여름 겨울 아침 저녁 햇살"
).split()

# 확인 단어를 받은 뒤 올려야 하는 시간
WORDS_TTL = timedelta(minutes=60)
# 녹음 길이를 잴 때 상한(초)에 더 두는 여유(화면이 15분에 끊어도 인코더 끝이 조금 넘는다)
LENGTH_SLACK = 5
# 한 대화 상대에서 끊긴 녹음·하루에 시작한 녹음 상한(검수로 보낸 녹음 상한은 설정)
MAX_INTERRUPTED = 10
MAX_STARTS_PER_DAY = 10
# 관리자 듣기 서명 주소 유효 시간(초). 내려받기 단추는 두지 않는다
LISTEN_TTL = 15 * 60

# 검수로 보낸 녹음(올린 뒤의 모든 상태)
SUBMITTED_STATES = ("submitted", "superseded", "passed", "rerequest", "hold", "refused")
# 화면에 보일 '지금 상태'를 정하는 줄
RELEVANT_STATES = ("passed", "submitted", "rerequest", "hold", "refused", "issued")
VERDICTS = {"pass": "passed", "rerequest": "rerequest", "hold": "hold", "refused": "refused"}
# 받는 녹음 형식(확장자) → 저장할 때 붙이는 Content-Type. 브라우저가 보낸 값은 믿지 않는다
FORMATS = {
    "webm": "audio/webm",
    "ogg": "audio/ogg",
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "aac": "audio/aac",
    "wav": "audio/wav",
}

NOT_REQUIRED = "이 대화 상대는 그분 동의 녹음이 필요 없어요."
MINOR_UNKNOWN = "먼저 그분이 만 19세 미만인지 알려 주세요."
ALREADY_PASSED = "그분 동의 녹음이 이미 확인됐어요."
# 14·19세 재동의: 그 나이가 된 다음 달 1일(reconsent_from)부터 새 나이로 녹음한다
RECONSENT_NOT_YET = "{date}부터 녹음할 수 있어요."
IN_REVIEW_HOLD = "TalkTo가 확인하고 있어요. 따로 연락드릴게요."
TOO_MANY = "여러 번 다시 녹음하셨어요. 고객센터로 알려 주시면 도와드릴게요."
EXPIRED = "확인 단어가 바뀌었어요. 처음부터 다시 녹음해 주세요."
NO_LENGTH = "녹음 길이를 확인하지 못했어요. 다시 녹음해 주세요."
TOO_LONG = "녹음이 너무 길어요. 15분 안으로 녹음해 주세요."
BAD_FORMAT = "이 형식의 녹음은 받을 수 없어요. 다시 녹음해 주세요."
NOT_FOUND = "녹음을 찾지 못했어요. 화면을 새로고침해 주세요."
NOT_REVIEWABLE = "지금은 확인할 수 없는 녹음이에요. 화면을 새로고침해 주세요."
ALL_CHECKS = "10개 항목이 모두 통과해야 해요."
MODE_CHANGED = "녹음한 뒤 나이 구간이 바뀌었어요. 새로 녹음을 받아 주세요."
NO_RECORDINGS = "비교할 이야기 녹음이 아직 없어요. 녹음을 올리신 뒤 확인해요."
REFUSED_NEEDS = (
    "원하지 않음으로 정하려면 녹음 중 '원하지 않으세요'를 눌렀거나 내부 메모가 있어야 해요."
)

# ────────────────────────── 검수 10항목(베타/31 원문) ──────────────────────────

REREQUEST_HEAD = (
    "녹음 잘 받았어요. 번거로우시겠지만 한 번 더 부탁드려요. 그분 목소리를 다루는 일이라 이 "
    "부분은 꼭 확인이 필요해요."
)
_DATE_NAME = (
    "오늘 날짜와 성함을 그분이 직접 말씀하시는 부분이 필요해요. 대신 말씀하시거나 미리 알려 드리지 "
    "말아 주세요."
)
# 항목마다 제목, 불통과 예시, 재요청 때 만든 분께 보낼 문장(관리자 화면이 실패 항목에 맞춰 미리
# 채운다 — 관리자가 고칠 수 있다). 6번(유도·압박)은 보류라 재요청 문장이 없다
CHECKLIST = [
    {
        "key": "1",
        "title": "본인 목소리인가",
        "detail": "이야기 녹음의 화자와 동일한가",
        "failExample": "낭독자와 이야기 화자가 다름",
        "rerequest": "동의 녹음 속 목소리가 올리신 녹음 속 그분과 같은 분인지 확인하기 어려웠어요.",
    },
    {
        "key": "2",
        "title": "날짜를 본인이 말했는가",
        "failExample": '신청자가 "오늘 9월 17일이잖아" 하고 대신 말함',
        "rerequest": _DATE_NAME,
    },
    {
        "key": "3",
        "title": "성함을 본인이 말했는가",
        "failExample": "신청자가 대신 답함",
        "rerequest": _DATE_NAME,
    },
    {
        "key": "4",
        "title": '"괜찮아요?"에 본인이 답했는가',
        "detail": "가장 중요",
        "failExample": '무응답 / 신청자가 "괜찮대요" 하고 넘어감',
        "rerequest": "'괜찮으세요?'라고 여쭤보시고 그분이 직접 대답하시는 부분까지 녹음해 주세요.",
    },
    {
        "key": "5",
        "title": "낭독을 본인이 했는가",
        "failExample": "신청자가 대신 읽음 → 즉시 불통과",
        "rerequest": "낭독은 그분이 직접 하셔야 해요. 한 줄씩 읽어 드리고 그분이 따라 읽으시게 해 "
        "주세요.",
    },
    {
        "key": "6",
        "title": "유도·압박 정황이 없는가",
        "failExample": '"그냥 해, 빨리", 반복 종용, 짜증 섞인 톤',
        "rerequest": None,
    },
    {
        "key": "7",
        "title": "철회 안내에 반응했는가",
        "failExample": "마지막 확인에 아무 반응 없음",
        "rerequest": "마지막에 '나중에 싫어지시면 말씀만 하세요'라고 알려 드리고 그분이 대답하시는 "
        "부분이 필요해요.",
    },
    {
        "key": "8",
        "title": "첫머리에 확인 단어가 있고, 끊김·편집 없는 한 파일인가",
        "failExample": "단어 없음 · 다른 날 단어 · 잘린 흔적 → 재요청",
        "rerequest": "녹음 첫머리에 화면에 나온 확인 단어를 말씀해 주시고, 처음부터 끝까지 한 번에 "
        "녹음해 주세요.",
    },
    {
        "key": "9",
        "title": "해외 회사로 보낸다는 설명에 본인이 괜찮다고 답했는가",
        "detail": "국외 이전 별도 동의 · 동의문 §7",
        "failExample": "설명 없음 / 신청자가 대신 답함 → 불통과",
        "rerequest": "미국 회사로 보낸다는 설명을 듣고 그분이 직접 괜찮다고 하시는 부분이 "
        "필요해요.",
    },
    {
        "key": "10",
        "title": "이해 확인 질문 3개에 본인이 스스로 답했는가",
        "detail": '① "뭘 만드는 거라고 했죠?" ② "싫어지면 어떻게 하면 돼요?" ③ "싫으시면 안 '
        '하셔도 돼요. 정말 괜찮으세요?"',
        "failExample": "답 없음 · 신청자 말을 그대로 따라 함 → 불통과",
        "rerequest": "이해 확인 질문 세 가지에 그분이 스스로 답하시는 부분이 필요해요. 대신 "
        "답하거나 따라 말하게 하지 말아 주세요.",
    },
]
CHECK_KEYS = [item["key"] for item in CHECKLIST]
# 하나라도 불통과면 동의가 성립하지 않는 항목(통과로 정할 수 없다)
ESSENTIAL = ("4", "5", "9", "10")
# 판정 안내(관리자 화면 고정 글)
GUIDE = [
    "10개 전부 통과 → 통과",
    "1~3, 7번 일부 미흡 → 재요청",
    "4·5·9·10번 하나라도 불통과 → 재요청 필수(동의 불성립)",
    "6번 유도·압박 정황 → 보류(만든 분께 전화로 확인, 그분이 원치 않으시면 원하지 않음)",
    "인지 상태가 의심스러우면 만들지 않음(원하지 않음으로 처리)",
    "의학적 판단이 아니라, 본인 의사를 표현하실 수 있는가만 봅니다.",
    "목소리가 아이로 들리는데 만 19세 이상으로 답한 대화 상대면 보류(만든 분께 확인)",
]

# 검수 결과 알림(SPEC §10.2). 대화 상대는 notices.persona_label 로만 가리킨다
PASSED_TITLE = "그분 동의 녹음이 확인됐어요"
PASSED_BODY = (
    "{label}의 그분 동의 녹음이 확인됐어요. 이제 올리신 녹음을 살펴보고 대화 상대를 만들 수 있어요."
)
RETRY_TITLE = "그분 동의 녹음을 한 번 더 부탁드려요"
HOLD_TITLE = "그분 동의 녹음을 확인하고 있어요"
HOLD_BODY = "{label}의 그분 동의 녹음에서 확인할 것이 있어 TalkTo가 따로 연락드릴게요."
# 운영 알림(개인정보 없이)
OPS_TEXT = "동의 녹음 검수 대기 1건"


def _utc(value: datetime | None) -> datetime | None:
    """SQLite 는 시간대를 빼고 돌려준다. 모두 UTC 로 저장하므로 UTC 로 본다."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _error(status_code: int, code: str, message: str):
    # gate 가 이 파일을 쓰므로 여기서 가져온다
    from app.services.gate import GateError

    return GateError(status_code, code, message)


def mode_of(persona: Persona, today=None) -> str | None:
    """지금 받아야 하는 동의 녹음의 종류. 미성년 여부를 아직 답하지 않은 예전 대화 상대는 None."""
    basics = persona.basics or {}
    if "minor" not in basics and basics.get("status") == "alive":
        return None
    return ages.consent_mode(basics, today or ages.today_kst())


def _living_other(persona: Persona) -> bool:
    from app.services.gate import living_other

    return living_other(persona.basics)


async def rows_of(db: AsyncSession, persona_id: str) -> list[SubjectConsentRecording]:
    """이 대화 상대의 모든 시도(처음부터)."""
    return list(
        (
            await db.scalars(
                select(SubjectConsentRecording)
                .where(SubjectConsentRecording.persona_id == persona_id)
                .order_by(SubjectConsentRecording.attempt)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


def _expire(rows: list[SubjectConsentRecording], now: datetime) -> bool:
    """60분 안에 올리지 않은 발급 줄을 expired 로(게으르게). 바꿨으면 True(commit 은 부르는 쪽)."""
    changed = False
    for row in rows:
        if row.status == "issued" and _utc(row.expires_at) < now:
            row.status = "expired"
            changed = True
    return changed


async def _reconsent_from(
    db: AsyncSession, persona_id: str, now: datetime | None = None
) -> datetime | None:
    """재동의가 시작된 때(그 나이가 된 다음 달 1일). 시작 전이면 None — 그때까지는 지금 나이의
    통과를 그대로 센다(시작하면 나이 구간이 바뀌어 예전 통과는 저절로 빠진다)."""
    since = _utc(
        await db.scalar(
            select(PersonaMinorCheck.reconsent_from).where(
                PersonaMinorCheck.persona_id == persona_id
            )
        )
    )
    if since is not None and (now or datetime.now(UTC)) < since:
        return None
    return since


async def _reconsent_waiting(db: AsyncSession, persona_id: str, now: datetime) -> datetime | None:
    """14·19세 재동의를 알렸지만 아직 시작 전(그 나이가 된 다음 달 1일 전)이면 그 시작 때."""
    check = await db.scalar(
        select(PersonaMinorCheck).where(PersonaMinorCheck.persona_id == persona_id)
    )
    if check is None or not check.reconsent_kind or check.reconsent_done_at is not None:
        return None
    start = _utc(check.reconsent_from)
    return start if start is not None and now < start else None


def _counts(row: SubjectConsentRecording, mode: str | None, since: datetime | None) -> bool:
    """이 통과 줄이 지금 mode 의 통과로 세지나(재동의가 걸려 있으면 그 뒤에 검수한 것만)."""
    if row.status != "passed" or mode is None or row.mode != mode:
        return False
    return since is None or (row.reviewed_at is not None and _utc(row.reviewed_at) >= since)


async def passed(db: AsyncSession, persona: Persona, mode: str | None) -> bool:
    """그 mode 의 통과한 동의 녹음이 있나. 재동의가 걸려 있으면(persona_minor_checks.reconsent_from)
    그 뒤에 검수한 것만 센다. 나이가 바뀌어 mode 가 달라지면 예전 통과는 저절로 모자라게 된다."""
    if mode is None:
        return False
    rows = (
        await db.scalars(
            select(SubjectConsentRecording).where(
                SubjectConsentRecording.persona_id == persona.id,
                SubjectConsentRecording.status == "passed",
                SubjectConsentRecording.mode == mode,
            )
        )
    ).all()
    if not rows:
        return False
    since = await _reconsent_from(db, persona.id)
    return any(_counts(row, mode, since) for row in rows)


def _relevant(
    rows: list[SubjectConsentRecording], mode: str | None, since: datetime | None, now: datetime
) -> SubjectConsentRecording | None:
    """화면에 보일 '지금 상태'를 정하는 가장 최근 줄. 지금 세지 않는 예전 통과(나이가 바뀜·재동의
    전)가 가장 최근이면 None(아직 받지 않은 것)."""
    for row in reversed(rows):
        if row.status not in RELEVANT_STATES:
            continue
        if row.status == "issued" and _utc(row.expires_at) < now:
            continue
        if row.status == "passed" and not _counts(row, mode, since):
            return None
        return row
    return None


async def latest_relevant(db: AsyncSession, persona: Persona) -> SubjectConsentRecording | None:
    """gate 카드용: 지금 상태를 정하는 줄(없으면 None)."""
    rows = await rows_of(db, persona.id)
    since = await _reconsent_from(db, persona.id)
    return _relevant(rows, mode_of(persona), since, datetime.now(UTC))


def _same_mode(rows: list[SubjectConsentRecording], mode: str | None):
    return [row for row in rows if row.mode == mode]


def submitted_count(rows: list[SubjectConsentRecording], mode: str | None) -> int:
    """지금 mode 로 검수에 보낸 녹음 수(14·19세 재동의는 새로 센다)."""
    return sum(1 for row in _same_mode(rows, mode) if row.status in SUBMITTED_STATES)


def max_attempts(rows: list[SubjectConsentRecording], mode: str | None) -> int:
    """검수로 보낼 수 있는 녹음 수: 설정 + 관리자가 '한 번 더 허락'한 줄 수."""
    extra = sum(1 for row in _same_mode(rows, mode) if row.extra_allowed)
    return get_settings().subject_consent_max_attempts + extra


def _too_many(rows: list[SubjectConsentRecording], mode: str | None, now: datetime) -> bool:
    if submitted_count(rows, mode) >= max_attempts(rows, mode):
        return True
    same = _same_mode(rows, mode)
    if sum(1 for row in same if row.status == "interrupted") >= MAX_INTERRUPTED:
        return True
    today = ages.today_kst(now)
    started = sum(1 for row in rows if ages.today_kst(_utc(row.created_at) or now) == today)
    return started >= MAX_STARTS_PER_DAY


async def _guardian_missing(db: AsyncSession, persona: Persona):
    """아이의 대화 상대에서 법정대리인 확인이 덜 됐으면 MinorState, 아니면 None."""
    if (persona.basics or {}).get("minor") is not True:
        return None
    from app.services import minors

    state = await minors.state(db, persona)
    return state if state is not None and not state.guardian_ok else None


async def start_block(
    db: AsyncSession,
    persona: Persona,
    rows: list[SubjectConsentRecording],
    now: datetime,
) -> tuple[str, str] | None:
    """지금 녹음을 시작할 수 없는 까닭(code, 글). 시작할 수 있으면 None."""
    from app.services import gate

    if not _living_other(persona):
        return "not_required", NOT_REQUIRED
    # 아이 본인 동의 기한을 넘겨 멈춘 것은 녹음을 받는다(통과하면 되살린다). 다른 멈춤은 막는다
    if persona.hold_reason and persona.hold_reason != "minor_consent_missing":
        return "stopped", gate.stopped_message(persona)
    mode = mode_of(persona, ages.today_kst(now))
    if mode is None:
        return "minor_unknown", MINOR_UNKNOWN
    missing = await _guardian_missing(db, persona)
    if missing is not None:
        guardian = await gate.guardian_block(db, persona)
        return "guardian", guardian.message if guardian is not None else gate.GUARDIAN
    # 재동의 시작 전에 옛 나이로 녹음하면 시작하는 날 세지 않게 된다(다시 녹음해야 한다)
    waiting = await _reconsent_waiting(db, persona.id, now)
    if waiting is not None:
        return "reconsent_not_yet", RECONSENT_NOT_YET.format(date=gate.month_day(waiting))
    since = await _reconsent_from(db, persona.id, now)
    if any(_counts(row, mode, since) for row in rows):
        return "already_passed", ALREADY_PASSED
    latest = _relevant(rows, mode, since, now)
    if latest is not None and latest.status == "hold":
        return "in_review_hold", IN_REVIEW_HOLD
    if _too_many(rows, mode, now):
        return "too_many", TOO_MANY
    return None


def _recording_view(row: SubjectConsentRecording | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "attempt": row.attempt,
        "status": row.status,
        "submittedAt": row.submitted_at,
        "reviewedAt": row.reviewed_at,
        "noteToOwner": row.note_to_owner,
    }


async def status_for(db: AsyncSession, persona: Persona) -> dict:
    """GET /personas/{id}/subject-consent 모양(SPEC §5.6-1). 60분 지난 발급 줄은 여기서 정리한다."""
    from app.services import sms

    now = datetime.now(UTC)
    rows = await rows_of(db, persona.id)
    if _expire(rows, now):
        await db.commit()
    required = _living_other(persona)
    mode = mode_of(persona, ages.today_kst(now)) if required else None
    since = await _reconsent_from(db, persona.id, now)
    latest = _relevant(rows, mode, since, now) if required else None
    status = latest.status if latest is not None else "none"
    blocked = await start_block(db, persona, rows, now)
    guardian = None
    if required and (persona.basics or {}).get("minor") is True:
        missing = await _guardian_missing(db, persona)
        guardian = {
            "needed": missing is not None,
            "missing": list(missing.missing) if missing is not None else [],
            "under14Open": sms.under14_open(),
        }
    return {
        "required": required,
        "mode": mode,
        "status": status,
        "attempts": len(rows),
        "submittedCount": submitted_count(rows, mode),
        "maxAttempts": max_attempts(rows, mode),
        "latest": _recording_view(rows[-1] if rows else None),
        "scriptDoc": SCRIPT_DOCS.get(mode) if mode else None,
        "canStart": blocked is None,
        "blockCode": blocked[0] if blocked else None,
        "blockMessage": blocked[1] if blocked else None,
        "guardian": guardian,
    }


def _new_words(rows: list[SubjectConsentRecording]) -> list[str]:
    """앞선 시도와 겹치지 않는 낱말 두 개(같은 쌍이면 다시 뽑는다)."""
    used = {frozenset((row.words or "").split()) for row in rows}
    pick = secrets.SystemRandom()
    for _ in range(50):
        words = pick.sample(WORDS, 2)
        if frozenset(words) not in used:
            return words
    return words


async def issue(db: AsyncSession, user: User, persona: Persona) -> SubjectConsentRecording:
    """확인 단어를 발급하고 새 시도 줄을 만든다(§5.4 · §5.6-2). 못 하면 GateError(409, 까닭).

    같은 대화 상대의 아직 올리지 않은 발급 줄은 expired 로 둔다. 몇 번째인지(attempt)가 겹치지 않게
    대화 상대 줄을 잠근 채 센다.
    """
    await db.execute(select(Persona.id).where(Persona.id == persona.id).with_for_update())
    now = datetime.now(UTC)
    rows = await rows_of(db, persona.id)
    _expire(rows, now)
    blocked = await start_block(db, persona, rows, now)
    if blocked is not None:
        await db.commit()
        raise _error(409, *blocked)
    for row in rows:
        if row.status == "issued":
            row.status = "expired"
    mode = mode_of(persona, ages.today_kst(now))
    doc = SCRIPT_DOCS[mode]
    row = SubjectConsentRecording(
        persona_id=persona.id,
        user_id=user.id,
        attempt=max((r.attempt for r in rows), default=0) + 1,
        mode=mode,
        script_doc=doc,
        script_version=legal.version_of(doc),
        script_sha256=legal.text_sha256(doc),
        words=" ".join(_new_words(rows)),
        words_issued_at=now,
        expires_at=now + WORDS_TTL,
        status="issued",
        refused_flag=False,
        extra_allowed=False,
        client_meta={},
    )
    db.add(row)
    await db.commit()
    return row


def storage_key(row: SubjectConsentRecording, suffix: str) -> str:
    """personas/{pid}/subject_consent/{몇 번째:02d}-{id}.{형식}(이야기 녹음·남긴 글과 다른 접두)."""
    return f"personas/{row.persona_id}/subject_consent/{row.attempt:02d}-{row.id}.{suffix}"


def audio_format(filename: str | None, fmt: str | None) -> str | None:
    """받는 형식(확장자, 점 없이). 파일 이름이 먼저, 없으면 화면이 알려 준 형식. 모르면 None."""
    for value in (Path(filename or "").suffix.lstrip("."), fmt or ""):
        value = value.strip().lower()
        if value in FORMATS:
            return value
    return None


def parse_steps(raw: str | None) -> list[dict]:
    """녹음하는 동안 넘긴 대본 단계 [{"step": n, "atMs": ms}]. 읽지 못하는 것은 빼고 50개까지."""
    try:
        value = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    steps = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        step, at = item.get("step"), item.get("atMs")
        if isinstance(step, int) and isinstance(at, int) and 0 <= step <= 50 and at >= 0:
            steps.append({"step": step, "atMs": at})
    return steps[:50]


def _ops_notice() -> None:
    where = "관리자 화면 '동의 녹음 검수'에서 확인해 주세요"
    notify.send(f"🎙️ {OPS_TEXT} — {where}")
    mailer.send_ops(f"[TalkTo] {OPS_TEXT}", f"{OPS_TEXT}. {where}.")


async def store_audio(
    db: AsyncSession,
    user: User,
    persona: Persona,
    record_id: str,
    *,
    content: bytes,
    filename: str | None = None,
    fmt: str | None = None,
    steps: list[dict] | None = None,
    interrupted: bool = False,
    refused: bool = False,
    reason: str | None = None,
) -> SubjectConsentRecording:
    """받은 녹음을 변환하지 않고 서울 저장소에 둔다(§5.6-3). 끝까지 녹음했거나 '원하지 않으세요'를
    눌렀으면 submitted(앞의 검수 전 녹음은 superseded), 중간에 멈췄으면 interrupted.

    응답을 받지 못해 같은 파일을 다시 보내면(화면이 3번까지 다시 보낸다) 같은 줄을 그대로 준다.
    """
    from app.services import gate

    now = datetime.now(UTC)
    row = await db.get(SubjectConsentRecording, record_id, populate_existing=True)
    if row is None or row.persona_id != persona.id:
        raise _error(404, "not_found", NOT_FOUND)
    sha = hashlib.sha256(content).hexdigest()
    if row.status != "issued":
        if row.sha256 == sha:
            return row
        raise _error(409, "expired", EXPIRED)
    if _utc(row.expires_at) < now:
        row.status = "expired"
        await db.commit()
        raise _error(409, "expired", EXPIRED)
    if persona.hold_reason and persona.hold_reason != "minor_consent_missing":
        raise _error(409, "stopped", gate.stopped_message(persona))
    suffix = audio_format(filename, fmt)
    if suffix is None:
        raise _error(400, "bad_format", BAD_FORMAT)
    settings = get_settings()
    if len(content) > settings.subject_consent_max_mb * 1024 * 1024:
        raise _error(413, "too_long", TOO_LONG)
    cap = settings.subject_consent_max_seconds + LENGTH_SLACK
    seconds = (
        await asyncio.to_thread(audio.voice_seconds, content, f".{suffix}", cap)
        if content
        else None
    )
    if seconds is None or seconds < 1:
        raise _error(400, "no_length", NO_LENGTH)
    if seconds > cap:
        raise _error(400, "too_long", TOO_LONG)
    key = storage_key(row, suffix)
    # 저장소로 올리는 동안 트랜잭션을 열어 두지 않는다
    await db.commit()
    storage = get_storage()
    await asyncio.to_thread(storage.put, key, content, FORMATS[suffix])
    await db.execute(select(Persona.id).where(Persona.id == persona.id).with_for_update())
    await db.refresh(row)
    if row.status != "issued":
        # 그사이 다른 요청이 같은 줄을 올렸다(같은 파일이면 그것을 준다)
        await db.commit()
        if row.sha256 != sha:
            await asyncio.to_thread(storage.delete_prefix, key)
            raise _error(409, "expired", EXPIRED)
        return row
    row.storage_key = key
    row.content_type = FORMATS[suffix]
    row.size_bytes = len(content)
    row.duration_ms = int(seconds * 1000)
    row.sha256 = sha
    row.submitted_at = now
    row.refused_flag = bool(refused)
    row.client_meta = {
        "steps": list(steps or []),
        "format": suffix,
        "interrupted": bool(interrupted),
        **({"reason": reason[:20]} if reason else {}),
    }
    submitted = refused or not interrupted
    row.status = "submitted" if submitted else "interrupted"
    if submitted:
        await db.execute(
            update(SubjectConsentRecording)
            .where(
                SubjectConsentRecording.persona_id == persona.id,
                SubjectConsentRecording.status == "submitted",
                SubjectConsentRecording.id != row.id,
            )
            .values(status="superseded")
        )
    await db.commit()
    if submitted:
        _ops_notice()
    return row


async def abandon(
    db: AsyncSession, persona: Persona, record_id: str, reason: str
) -> SubjectConsentRecording:
    """올리지 못하고 끊긴 시도(§5.6-4): 발급 줄이면 interrupted(소리 없음). 그 밖이면 그대로."""
    row = await db.get(SubjectConsentRecording, record_id, populate_existing=True)
    if row is None or row.persona_id != persona.id:
        raise _error(404, "not_found", NOT_FOUND)
    if row.status == "issued":
        row.status = "interrupted"
        row.client_meta = {**(row.client_meta or {}), "interrupted": True, "reason": reason}
        await db.commit()
    return row


# ────────────────────────── 관리자 검수 ──────────────────────────


async def owner_of(db: AsyncSession, persona: Persona) -> User | None:
    return await db.scalar(
        select(User)
        .join(Person, Person.owner_user_id == User.id)
        .where(Person.id == persona.person_id)
    )


async def has_story_recordings(db: AsyncSession, persona_id: str) -> bool:
    """비교할 이야기 녹음(서울 저장)이 있나. 1번 '본인 목소리인가'를 보려면 있어야 한다."""
    found = await db.scalar(select(Recording.id).where(Recording.persona_id == persona_id).limit(1))
    return found is not None


def _verdict_error(
    row: SubjectConsentRecording, checks: dict[str, bool], verdict: str, admin_note: str | None
):
    """판정 규칙(§5.7). 어기면 GateError(400), 맞으면 None."""
    # 4·5·9·10 불통과(동의 불성립)·6 불통과(보류)도 여기서 통과가 막힌다
    if verdict == "pass" and not all(checks.values()):
        return _error(400, "checklist", ALL_CHECKS)
    if verdict == "refused" and not (row.refused_flag or (admin_note or "").strip()):
        return _error(400, "refused_reason", REFUSED_NEEDS)
    return None


async def review(
    db: AsyncSession,
    admin: User,
    record_id: str,
    *,
    checklist: dict,
    verdict: str,
    note_to_owner: str | None = None,
    admin_note: str | None = None,
    allow_more: bool = False,
) -> SubjectConsentRecording:
    """관리자 검수(§5.7). submitted·hold 줄만. 통과는 녹음할 때 보인 대본의 판·해시로 동의 기록을
    남기고, 원하지 않음은 대화 상대를 멈춘다(7일 뒤 지움). 결과는 만든 분께 알린다(앱 알림 + 메일).
    """
    from app.services import holds

    if verdict not in VERDICTS:
        raise _error(400, "verdict", "판정을 골라 주세요.")
    row = await db.get(SubjectConsentRecording, record_id, populate_existing=True)
    if row is None:
        raise _error(404, "not_found", NOT_FOUND)
    if row.status not in ("submitted", "hold"):
        raise _error(409, "not_reviewable", NOT_REVIEWABLE)
    persona = await db.get(Persona, row.persona_id, populate_existing=True)
    if persona is None:
        raise _error(404, "not_found", NOT_FOUND)
    checks = {key: checklist.get(key) is True for key in CHECK_KEYS}
    problem = _verdict_error(row, checks, verdict, admin_note)
    if problem is not None:
        raise problem
    if verdict == "pass":
        if row.mode != mode_of(persona):
            raise _error(400, "mode_changed", MODE_CHANGED)
        if not await has_story_recordings(db, persona.id):
            raise _error(409, "no_recordings", NO_RECORDINGS)
    now = datetime.now(UTC)
    row.checklist = checks
    row.status = VERDICTS[verdict]
    row.reviewer_user_id = admin.id
    row.reviewed_at = now
    row.note_to_owner = (note_to_owner or "").strip()[:500] or None
    row.admin_note = (admin_note or "").strip()[:1000] or None
    if verdict == "rerequest" and allow_more:
        row.extra_allowed = True
    owner = await owner_of(db, persona)
    label = notices.persona_label(persona)
    dedupe = f"subject_consent:{row.id}:{verdict}"
    if verdict == "pass":
        # 검수하는 날의 대본이 아니라 녹음할 때 보인 대본의 판·해시(그 사이 대본을 고쳐도 맞게)
        legal.log(
            db,
            owner.id if owner else None,
            legal.persona_type("subject_consent", persona.id),
            True,
            persona_id=persona.id,
            doc=row.script_doc,
            version=row.script_version,
            channel="admin_review",
            sha256=row.script_sha256,
        )
        check = await db.scalar(
            select(PersonaMinorCheck).where(PersonaMinorCheck.persona_id == persona.id)
        )
        if check is not None and check.reconsent_kind and check.reconsent_done_at is None:
            # 재동의 시작 뒤 새 나이로 받은 통과만 표시한다(minors.sweep·passed 와 같게)
            since = _utc(check.reconsent_from)
            if since is not None and now >= since and _counts(row, mode_of(persona), since):
                check.reconsent_done_at = now
        # 아이 본인 동의 기한을 넘겨 멈췄던 것: 지우기 전에 동의가 확인됐으니 되살린다(열린 이의가
        # 있으면 풀지 않는다 — holds.clear_for_consent)
        await holds.clear_for_consent(
            db, persona, by_user_id=admin.id, note="subject_consent_passed"
        )
        if owner is not None:
            await notices.create(
                db,
                owner.id,
                "subject_consent_passed",
                PASSED_TITLE,
                PASSED_BODY.format(label=label),
                persona_id=persona.id,
                action={"label": "이어서 만들기", "route": f"/p/{persona.id}"},
                dedupe_key=dedupe,
                mail=True,
            )
    elif verdict == "rerequest" and owner is not None:
        await notices.create(
            db,
            owner.id,
            "subject_consent_rerequest",
            RETRY_TITLE,
            row.note_to_owner or REREQUEST_HEAD,
            persona_id=persona.id,
            action={"label": "다시 녹음하기", "route": f"/p/{persona.id}/consent"},
            dedupe_key=dedupe,
            mail=True,
        )
    elif verdict == "hold" and owner is not None:
        await notices.create(
            db,
            owner.id,
            "subject_consent_hold",
            HOLD_TITLE,
            HOLD_BODY.format(label=label),
            persona_id=persona.id,
            dedupe_key=dedupe,
            mail=True,
        )
    elif verdict == "refused":
        # 멈춤 알림(stopped)은 holds 가 보낸다
        await holds.place(db, persona, "subject_refused", by_user_id=admin.id)
    await db.commit()
    return row


def listen_url(row: SubjectConsentRecording) -> str | None:
    """관리자 듣기 서명 주소(15분). 파일이 없으면 None."""
    if not row.storage_key:
        return None
    return get_storage().signed_url(row.storage_key, LISTEN_TTL)


def gap_seconds(row: SubjectConsentRecording) -> float | None:
    """확인 단어를 받은 뒤 올리기까지 녹음 길이보다 더 걸린 초(8번 판단 보조)."""
    if row.submitted_at is None or row.duration_ms is None:
        return None
    spent = (_utc(row.submitted_at) - _utc(row.words_issued_at)).total_seconds()
    return round(spent - row.duration_ms / 1000, 1)
