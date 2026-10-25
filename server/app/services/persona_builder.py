"""녹음(음성·영상) → 목소리 고르기 → 핵심 질문 → 페르소나.

단계마다 AI 서버를 부르고 결과를 DB 에 둔다. AI 서버는 아무것도 저장하지 않는다(stateless).
단계 순서는 AI 레포 pipeline/run_pipeline.py 와 같다.

    전사          /v1/analysis/transcriptions   (s1_a·s1_c)
    화자 추천     /v1/analysis/subject-speaker  (s1_b)
    목소리 묶기   /v1/analysis/voice-groups     (녹음 사이 같은 사람, ECAPA)
    녹음 분석     /v1/analysis/recording        (s2_a·b·c·e·f)
    통찰          /v1/persona/reflection        (s3_b)
    지침 조립     /v1/persona/assembly          (s3_c)
    임베딩        /v1/embeddings                (s3_d)
    목소리 복제   /v1/voice/clone               (s4, 고른 경우만)
"""

import asyncio
import hashlib
import logging
import tempfile
import time
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.db.models import (
    ORIGIN_IMPORT,
    ConsentRecord,
    Conversation,
    JobStatus,
    Memory,
    Message,
    Person,
    Persona,
    PersonaJob,
    PremiumVoice,
    Recording,
    SetupStep,
    TranscriptSegment,
    User,
    UserRole,
    VoiceGroup,
)
from app.db.session import SessionLocal
from app.services import analytics, audio, gate, intake, jobs, legal, notify
from app.services.ai import PROVIDER_CODES, TRANSPORT_CODES, AIServerError, ai_service
from app.services.storage import get_storage

logger = logging.getLogger(__name__)

# AI 서버 RecordingSafetyFlag.type → 금기 주제 이름. 사람이 정하기 전까지 "물으면만".
SAFETY_TOPIC = {
    "health": "건강·병",
    "asset": "재산·돈",
    "familyConflict": "가족 갈등",
    "death": "죽음",
    "thirdParty": "다른 가족의 사생활",
}
# 목소리 복제 재료: AI 레포 pipeline/s4_voice/a_voice.py 와 같은 기준
# 목소리 복제 후보. AI 서버가 소리를 재서 이 가운데 깨끗한 1분쯤을 골라 Fish 로 보낸다.
CLONE_CANDIDATE_SECONDS = 600
CLONE_MIN_SEGMENT_MS = 1500  # 짧은 맞장구·토막은 목소리 결을 담지 못한다
CLONE_MIN_SECONDS = 10
CLONE_MAX_SAMPLES = 100  # AI 서버가 한 번에 받는 구간 수
CLONE_CROSSTALK_MS = 200  # 이만큼 붙어서 남이 말하면 섞였을 수 있다
# 웃음·울음은 목소리 결과 다르게 학습된다(실험에서 웃음 구간을 빼야 닮았다)
CLONE_SKIP_TEXT = ("(웃음)", "(웃는", "ㅋㅋ", "ㅎㅎ", "하하하", "(울음)", "(흐느")
# 녹음을 AI 서버가 내려받을 서명 주소의 유효 시간. 긴 녹음 전사보다 길게.
AUDIO_URL_TTL = 3 * 3600
ANALYZE_WAIT_SECONDS = 3600
# 관리자가 가져온 대화 상대(origin=import)에 다시 만들기가 걸렸을 때 남기는 글
# (아무것도 바꾸지 않는다)
IMPORTED_SKIP = "완료 · 가져온 대화 상대는 다시 만들지 않아요"


def recording_key(persona_id: str, recording_id: str, suffix: str) -> str:
    return f"personas/{persona_id}/recordings/{recording_id}{suffix}"


async def load_recordings(db: AsyncSession, persona_id: str) -> list[Recording]:
    return list(
        (
            await db.scalars(
                select(Recording)
                .where(Recording.persona_id == persona_id)
                .options(selectinload(Recording.segments))
                .order_by(Recording.created_at)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


SUBJECT_LABEL = "SUBJECT"


def _segment_payload(
    segments: list[TranscriptSegment], subject_voices: set[str] | None = None
) -> list[dict]:
    """AI 서버로 보낼 전사 구간. 목소리를 묶었으면 화자 라벨을 목소리 키로 바꿔 보낸다.

    그분으로 고른 목소리는 모두 SUBJECT 하나로 보낸다. 한 사람이 둘로 나뉘어 있어도
    AI 서버는 대상자 라벨 하나만 받기 때문이다.
    """
    out = []
    for seg in segments:
        if not seg.text:
            continue
        label = seg.voice_key or seg.speaker_label
        if subject_voices and seg.voice_key in subject_voices:
            label = SUBJECT_LABEL
        out.append(
            {
                "id": seg.id,
                "segmentIndex": seg.segment_index,
                "startMs": seg.start_ms,
                "endMs": max(seg.end_ms, seg.start_ms),
                "speakerLabel": label,
                "transcriptText": seg.text,
            }
        )
    return out


def _is_subject(seg: TranscriptSegment, subject_voices: set[str]) -> bool:
    return seg.voice_key is not None and seg.voice_key in subject_voices


async def load_voices(db: AsyncSession, persona_id: str) -> list[VoiceGroup]:
    return list(
        (
            await db.scalars(
                select(VoiceGroup)
                .where(VoiceGroup.persona_id == persona_id)
                .order_by(VoiceGroup.position)
            )
        ).all()
    )


# ────────────────────────── 1. 소리 뽑기 + 전사 + 목소리 묶기 ──────────────────────────


MISSING_FILE = "이 녹음 파일을 찾지 못했어요. 지우고 다시 올려 주세요."


async def _prepare_audio(db: AsyncSession, rec: Recording) -> None:
    """원본(음성·영상)에서 소리만 뽑아 m4a 로 바꾸고 원본은 지운다.

    파일은 메모리에 올리지 않고 임시 폴더로 받고·바꾸고·올린다(서버 전체에서 audio.SLOTS 개만).
    바꾼 m4a 를 저장소와 DB 에 다 남긴(commit) 뒤에 원본을 지운다: 그 사이 서버가 꺼져도(배포·
    재시작) 녹음을 잃지 않는다. 원본이 없고 바꾼 m4a 만 남았으면(예전 순서에서 끊긴 경우) 그것으로
    잇는다. 둘 다 없으면 이 녹음만 실패로 둔다(AudioError).
    """
    storage = get_storage()
    original = rec.storage_key
    name = original.rsplit("/", 1)[-1]
    suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    target = recording_key(rec.persona_id, rec.id, ".m4a")
    async with audio.slot():
        with tempfile.TemporaryDirectory(prefix="talkto_prepare_") as tmp:
            src = Path(tmp) / f"source{suffix}"
            dst = Path(tmp) / "audio.m4a"
            if await asyncio.to_thread(storage.exists, original):
                await asyncio.to_thread(storage.download, original, src)
                duration, phone = await asyncio.to_thread(audio.prepare, src, dst)
                await asyncio.to_thread(storage.upload, target, dst, "audio/mp4")
            elif original != target and await asyncio.to_thread(storage.exists, target):
                logger.warning("recording %s: original missing, resuming from converted", rec.id)
                await asyncio.to_thread(storage.download, target, dst)
                duration, phone = await asyncio.to_thread(audio.inspect, dst)
            else:
                raise audio.AudioError(MISSING_FILE)
            size = dst.stat().st_size
    rec.storage_key = target
    rec.content_type, rec.size_bytes = "audio/mp4", size
    rec.duration_ms, rec.audio_ready = duration, True
    rec.phone_band = phone
    await db.commit()
    if original != target:
        try:
            await asyncio.to_thread(storage.delete_prefix, original)
        except Exception as error:  # noqa: BLE001 — 남은 원본은 대화 상대를 지울 때 함께 지운다
            logger.warning("original recording delete failed: %s", type(error).__name__)


FISH = "fish"


def voice_provider() -> str:
    """새 목소리는 모두 Fish Audio drama-3 로 만든다(2026-10-17, 통화 녹음도 같다).

    예전에는 통화 녹음이면 minimax, 아니면 elevenlabs 였다. 그 목소리는 voice_retrain 이 하나씩
    Fish 로 다시 만든다. 다른 회사로 되돌리는 스위치는 두지 않는다: 새로 가입한 분은 그 회사로
    보내는 데 동의하지 않았다(개인정보보호법 제28조의8).
    """
    return FISH


async def mostly_phone(db: AsyncSession, recordings: list[Recording], subject: set[str]) -> bool:
    """그분 목소리가 주로 통화 녹음에서 나왔나(통계 voice_created{phone} 로만 본다).

    이 기능 전에 올린 녹음은 여기서 통화 녹음인지 재어 둔다(화면의 녹음 표시에도 쓴다).
    """
    phone_ms = total_ms = 0
    storage = get_storage()
    for rec in recordings:
        mine = sum(s.end_ms - s.start_ms for s in rec.segments if _is_subject(s, subject))
        if not mine:
            continue
        if rec.phone_band is None and rec.audio_ready:  # 이 기능 전에 올린 녹음
            try:
                async with audio.slot():
                    with tempfile.TemporaryDirectory(prefix="talkto_band_") as tmp:
                        path = Path(tmp) / "audio.m4a"
                        await asyncio.to_thread(storage.download, rec.storage_key, path)
                        rec.phone_band = await asyncio.to_thread(audio.phone_band, path)
            except audio.AudioError:
                rec.phone_band = False
        total_ms += mine
        phone_ms += mine if rec.phone_band else 0
    await db.commit()
    return bool(total_ms and phone_ms * 2 > total_ms)


# 목소리 만들기 직전 확인 문서. 그분 상태(basics.status)마다 다르다.
PLEDGE_DOCS = {
    "deceased": "pledge_deceased",
    "alive": "pledge_living",
    "unable": "pledge_unable",
}


def pledge_doc(persona: Persona) -> str:
    basics = persona.basics or {}
    if basics.get("relation") == intake.SELF_RELATION:
        return "pledge_self"  # 내 목소리로 만드는 나 자신
    return PLEDGE_DOCS.get(basics.get("status"), "pledge_deceased")


async def voice_consented(db: AsyncSession, user_id: str, persona: Persona) -> bool:
    """목소리 만들기 확인이 있나. 그분 상태를 바꿔 확인 문서가 달라졌으면 없는 것으로 본다."""
    kind = legal.persona_type("voice_clone", persona.id)
    if not await legal.has(db, user_id, kind):
        return False
    return await legal.last_doc(db, user_id, kind) == pledge_doc(persona)


# 그분 녹음·대답 글을 Fish Audio(미국)로 보내는 계정 동의
FISH_CONSENT = "overseas_fish"
# 관리자(TalkTo 직원)라 동의 화면 없이 보낸 기록의 channel
ADMIN_CHANNEL = "admin"
FISH_CONSENT_NEEDED = (
    "그분 목소리를 만드는 해외 회사로 녹음을 보내는 데 동의해 주셔야 목소리를 만들 수 있어요."
)


async def _owner(db: AsyncSession, persona: Persona) -> User | None:
    return await db.scalar(
        select(User)
        .join(Person, Person.owner_user_id == User.id)
        .where(Person.id == persona.person_id)
    )


async def fish_allowed(db: AsyncSession, persona: Persona) -> bool:
    """그분 녹음을 Fish Audio 로 보내도 되나: 주인이 overseas_fish 에 동의했거나 관리자다.

    화면에서 온 요청은 동의 게이트를 지나 늘 동의가 있다. 뒤에서 도는 일(배포 전에 건 작업·
    다시 시도·voice_retrain)은 게이트를 지나지 않아 Fish 를 부르기 전에 여기서 따로 본다.
    관리자는 TalkTo 직원이라 게이트 없이 간다(deps.py 와 같다).
    """
    owner = await _owner(db, persona)
    if owner is None:
        return False
    return owner.role == UserRole.ADMIN or await legal.has(db, owner.id, FISH_CONSENT)


async def _note_admin_fish(db: AsyncSession, persona: Persona) -> None:
    """관리자 대화 상대의 녹음을 동의 화면 없이 Fish 로 보낸다는 기록(channel=admin)을 남긴다.

    관리자는 overseas_fish 동의 화면을 보지 않는다(fish_allowed). 그래도 누가 언제 어느 문서
    버전 아래 보냈는지는 동의 이력에 남긴다(문서 버전마다 한 번). commit 은 부르는 쪽이 한다.
    """
    owner = await _owner(db, persona)
    if owner is None or owner.role != UserRole.ADMIN:
        return
    if await legal.has(db, owner.id, FISH_CONSENT):
        return
    version = legal.current_version(FISH_CONSENT)
    logged = await db.scalar(
        select(ConsentRecord.id)
        .where(
            ConsentRecord.user_id == owner.id,
            ConsentRecord.consent_type == FISH_CONSENT,
            ConsentRecord.channel == ADMIN_CHANNEL,
            ConsentRecord.version == version,
        )
        .limit(1)
    )
    if logged is None:
        legal.log(db, owner.id, FISH_CONSENT, True, channel=ADMIN_CHANNEL)


Span = tuple[float, float]


def _at(span: Span, fraction: float) -> float:
    """진행 막대에서 이 단계가 차지하는 구간(span) 안의 위치(%)."""
    lo, hi = span
    return lo + (hi - lo) * max(0.0, min(1.0, fraction))


BEAT_SECONDS = 6


def _job_writer(job_id: str):
    """작업 진행(문구·진행률)을 따로 연 세션으로 적는 함수. 작업이 쓰는 세션은 건드리지 않는다."""

    async def write(step: str, percent: float) -> None:
        async with SessionLocal() as s:
            current = await s.scalar(select(PersonaJob.percent).where(PersonaJob.id == job_id))
            await s.execute(
                update(PersonaJob)
                .where(PersonaJob.id == job_id, PersonaJob.status == JobStatus.RUNNING)
                .values(detail=step[:300], percent=jobs.next_percent(current, percent))
            )
            await s.commit()

    return write


@asynccontextmanager
async def _creep(write, step: str, lo: float, hi: float, expect_seconds: float):
    """AI 호출 하나(녹음 옮기기·살펴보기)를 기다리는 동안 막대를 lo 에서 hi 쪽으로 천천히 채운다.

    녹음이 하나면 한 번의 긴 호출이라 막대가 멈춘 것처럼 보여서다. 걸린 시간을 expect_seconds 와
    견줘 채우고, 끝까지 가지는 않는다(90%). write 는 작업 세션과 다른 세션으로 적는다(_job_writer):
    같은 세션을 함께 쓰면 그 사이 바꾼 값이 저장되지 않거나, 적다 실패하면 작업까지 멈춘다.
    적다가 실패해도 본 일은 그대로 간다. 빠져나올 때는 적던 것이 끝난 뒤에 돌아온다.
    """
    stop = asyncio.Event()

    async def beat() -> None:
        started = time.monotonic()
        while True:
            try:
                await asyncio.wait_for(stop.wait(), BEAT_SECONDS)
                return
            except TimeoutError:
                pass
            done = min(0.9, (time.monotonic() - started) / max(1.0, expect_seconds))
            try:
                await write(step, lo + (hi - lo) * done)
            except Exception as error:  # noqa: BLE001 — 막대는 덤이다
                logger.warning("progress heartbeat failed: %s", error)
                return

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        stop.set()
        await task


NO_SPEECH = "이 녹음에서는 말소리를 찾지 못했어요."
# AI 서버 오류 코드(AI 레포 infra/error.py) 가운데 그 녹음 탓인 것
NO_SPEECH_CODES = {"EMPTY_TRANSCRIPT", "AUDIO_TOO_SHORT"}


def _recording_problem(error: AIServerError) -> str | None:
    """전사 오류가 그 녹음 탓이면 녹음에 남길 말. 아니면 None(작업을 멈춘다)."""
    if error.code in NO_SPEECH_CODES:
        return NO_SPEECH
    if error.code == "AUDIO_TOO_LARGE":
        return audio.too_long_message()
    return None


async def _transcribe_pending(
    db: AsyncSession,
    job: PersonaJob,
    persona: Persona,
    progress,
    *,
    retry_failed: bool = True,
    span: Span = (0, 90),
) -> list[Recording]:
    """아직 글자로 옮기지 않은 녹음만 옮기고, 말소리를 찾은 녹음 전부를 돌려준다.

    retry_failed=False 면 앞서 실패한 녹음은 건너뛴다(같은 파일은 또 실패하고 전사 값만 든다).
    진행 막대는 녹음 하나마다 span 을 나눠 채운다(옮기기가 가장 오래 걸린다).

    그분 녹음을 해외 회사(받아쓰기)로 보내기 전에 게이트를 다시 본다(배포 전에 건 작업·다시
    시도도 막는다 — 살아 계신 분은 그분 동의 녹음이 통과해야 한다).
    """
    await gate.check_job(db, persona.id, "audio")
    context = intake.subject_context(persona.basics, persona.answers)
    recordings = await load_recordings(db, persona.id)
    todo = [
        r for r in recordings if r.status == "UPLOADED" or (retry_failed and r.status == "FAILED")
    ]
    storage = get_storage()

    n = len(todo)
    for i, rec in enumerate(todo, start=1):
        if not rec.audio_ready:
            await progress(f"녹음을 준비하는 중 ({i}/{n})", _at(span, (i - 1) / n))
            try:
                await _prepare_audio(db, rec)
            except audio.AudioError as error:
                rec.status, rec.error = "FAILED", str(error)
                await db.commit()
                continue
        step = f"녹음을 듣고 누가 무슨 말을 했는지 적는 중 ({i}/{n})"
        await progress(step, _at(span, (i - 0.8) / n))
        audio_url = storage.signed_url(rec.storage_key, AUDIO_URL_TTL)
        expect = max(40.0, (rec.duration_ms or 120_000) / 1000 * 0.5)
        try:
            async with _creep(
                _job_writer(job.id), step, _at(span, (i - 0.8) / n), _at(span, i / n), expect
            ):
                result = await ai_service.transcribe_recording(
                    jobId=job.id,
                    recordingId=rec.id,
                    audioUrl=audio_url,
                    audioMimeType=rec.content_type,
                    mode="full",
                    language="ko",
                    speakerDiarization=True,
                    subjectContext=context,
                )
        except AIServerError as error:
            # 이 녹음 탓인 것(말소리가 없음·너무 짧음·너무 김)만 그 녹음을 빼고 간다. 내려받기가
            # 잠깐 실패했거나 주소가 지난 것(같은 422)은 녹음 탓이 아니라 작업을 멈추고 녹음은
            # 그대로 둔다(다시 누르면 다시 옮긴다).
            reason = _recording_problem(error)
            if reason is None:
                raise
            rec.status, rec.error = "FAILED", reason
            await db.commit()
            continue
        await db.execute(delete(TranscriptSegment).where(TranscriptSegment.recording_id == rec.id))
        kept = 0
        for row in result["segments"]:
            text = (row.get("transcriptText") or "").strip()
            if not text:
                continue
            kept += 1
            db.add(
                TranscriptSegment(
                    recording_id=rec.id,
                    segment_index=row["segmentIndex"],
                    start_ms=row["startMs"],
                    end_ms=row["endMs"],
                    speaker_label=row.get("speakerLabel") or "unknown",
                    transcript_text=text,
                    corrected_text=row.get("correctedText"),
                )
            )
        if not kept:
            # 글자가 하나도 없으면 옮긴 녹음(익힌 녹음)으로 두지 않는다(실패로 두어야 뺄 수 있다)
            rec.status, rec.error = "FAILED", NO_SPEECH
            await db.commit()
            continue
        rec.status, rec.error = "TRANSCRIBED", None
        rec.suggested_label = result.get("subjectSpeakerLabel")
        await db.commit()
        await progress(step, _at(span, i / n))

    recordings = await load_recordings(db, persona.id)
    done = [r for r in recordings if r.status == "TRANSCRIBED" and r.segments]
    if not done:
        raise ValueError("말소리를 찾은 녹음이 없어요. 다른 녹음을 올려 주세요.")

    await progress("녹음에서 그분 목소리를 찾는 중", span[1])
    for rec in done:
        if not rec.suggested_label:
            rec.suggested_label = await ai_service.suggest_subject_speaker(
                transcriptSegments=_segment_payload(rec.segments), subjectContext=context
            )
    await db.commit()
    return done


async def run_transcribe(db: AsyncSession, job: PersonaJob, progress) -> None:
    persona = await db.get(Persona, job.persona_id)
    done = await _transcribe_pending(db, job, persona, progress, span=(0, 85))
    await progress("녹음 속 목소리를 사람별로 나누는 중", 90)
    await _group_voices(db, persona, done)
    # 목소리를 다시 묶었으니 예전 분석(그분 구간 기준)은 버리고, 고른 뒤 다시 분석한다
    for rec in done:
        rec.analysis = None
    persona.setup_step = SetupStep.SPEAKERS
    await db.commit()


async def _group_voices(db: AsyncSession, persona: Persona, recordings: list[Recording]) -> None:
    """AI 서버에 목소리를 묶게 하고, 묶음마다 미리듣기 클립을 만든다.

    미리듣기는 묶음마다 필요한 녹음만 하나씩 디스크로 받아 자른다(녹음 전체를 메모리에 모아 두지
    않는다). 새 묶음을 DB 에 넣고 추천 목소리를 적는 일(대화 상대 줄을 잡는다)은 미리듣기를 다
    만든 뒤에 한다: 다 만든 대화 상대에 녹음을 더하는 동안(EXTEND) 그 줄을 오래 잡으면 가족의
    대화 보내기(대화 횟수 잡기)가 기다린다.
    """
    storage = get_storage()
    # 다시 묶을 때(녹음을 더 올린 뒤) 예전 묶음 키가 남아 있으면 그게 화자 라벨로 나가서,
    # 돌아온 묶음과 원래 화자 라벨이 맞지 않는다. 전사 때의 화자 라벨로 보낸다.
    for rec in recordings:
        for seg in rec.segments:
            seg.voice_key = None
    result = await ai_service.group_voices(
        recordings=[
            {
                "recordingId": rec.id,
                "audioUrl": storage.signed_url(rec.storage_key, AUDIO_URL_TTL),
                "transcriptSegments": _segment_payload(rec.segments),
            }
            for rec in recordings
        ]
    )
    await db.execute(delete(VoiceGroup).where(VoiceGroup.persona_id == persona.id))
    await asyncio.to_thread(storage.delete_prefix, f"personas/{persona.id}/voices")

    by_id = {rec.id: rec for rec in recordings}
    key_of: dict[tuple[str, str], str] = {}
    groups: list[VoiceGroup] = []
    votes: list[int] = []
    for position, row in enumerate(result["groups"]):
        voice_key = f"VOICE_{position + 1}"
        members = [m for m in row["members"] if m["recordingId"] in by_id]
        for m in members:
            key_of[(m["recordingId"], m["speakerLabel"])] = voice_key
        # 대화 내용으로 짐작한 화자가 이 목소리에 있으면, 그 화자가 말한 시간만큼 표를 준다
        votes.append(
            sum(
                m["speechMs"]
                for m in members
                if by_id[m["recordingId"]].suggested_label == m["speakerLabel"]
            )
        )
        groups.append(
            VoiceGroup(
                persona_id=persona.id,
                voice_key=voice_key,
                position=position,
                speech_ms=row["speechMs"],
                members=members,
                suggested=False,
            )
        )
    if votes and max(votes) > 0:
        groups[votes.index(max(votes))].suggested = True

    for rec in recordings:
        for seg in rec.segments:
            seg.voice_key = key_of.get((rec.id, seg.speaker_label))

    def fetch(rec_id: str, path: Path) -> None:
        storage.download(by_id[rec_id].storage_key, path)

    for group in groups:
        segments = [
            (rec.id, seg)
            for rec in recordings
            for seg in rec.segments
            if seg.voice_key == group.voice_key
            and seg.end_ms - seg.start_ms >= audio.PREVIEW_MIN_SEGMENT_MS
        ]
        segments.sort(key=lambda pair: pair[1].start_ms - pair[1].end_ms)  # 긴 구간부터
        clips, total = [], 0
        for rec_id, seg in segments:
            if total >= audio.PREVIEW_SECONDS * 1000:
                break
            end = min(seg.end_ms, seg.start_ms + 6000)  # 한 구간이 미리듣기를 다 차지하지 않게
            clips.append((rec_id, seg.start_ms, end))
            total += end - seg.start_ms
        if not clips:
            continue
        clips.sort(key=lambda c: (c[0], c[1]))  # 들을 때는 녹음별 시간 순서대로
        try:
            async with audio.slot():
                preview = await asyncio.to_thread(audio.cut_preview, fetch, clips)
        except audio.AudioError:
            continue
        key = f"personas/{persona.id}/voices/{group.voice_key}.mp3"
        await asyncio.to_thread(storage.put, key, preview, "audio/mpeg")
        group.preview_key = key

    db.add_all(groups)
    # 추천 목소리를 미리 골라 둔다. 확정은 사람이 한다.
    persona.subject_voices = [g.voice_key for g in groups if g.suggested]
    await db.flush()


def voice_overview(voices: list[VoiceGroup], recordings: list[Recording]) -> list[dict]:
    """화자 고르기 화면에 보여줄 목소리 카드."""
    storage = get_storage()
    names = {rec.id: rec.original_name for rec in recordings}
    out = []
    for group in voices:
        lines = [
            seg.text
            for rec in recordings
            for seg in rec.segments
            if seg.voice_key == group.voice_key and 4 <= len(seg.text) <= 60
        ]
        recording_ids = list(dict.fromkeys(m["recordingId"] for m in group.members))
        out.append(
            {
                "voiceKey": group.voice_key,
                "title": f"목소리 {group.position + 1}",
                "speechSeconds": round(group.speech_ms / 1000),
                "recordings": [names[r] for r in recording_ids if r in names],
                "previewUrl": storage.signed_url(group.preview_key) if group.preview_key else None,
                "lines": _evenly(lines, 3),
                "suggested": group.suggested,
            }
        )
    return out


# ────────────────────────── 2. 녹음 분석 (질문에 답하는 동안) ──────────────────────────


async def run_analyze(db: AsyncSession, job: PersonaJob, progress) -> None:
    persona = await db.get(Persona, job.persona_id)
    # 대화 상대 줄을 잡은 채 돌아온다. 작업 끝(DONE)을 적는 commit 이 함께 풀어, 그 사이에 다시
    # 고르기가 '아직 살펴보는 중'으로 보고 새 살펴보기를 걸지 않는 틈이 없다.
    await _analyze(db, job, persona, progress, only_new=False)


# 고른 목소리는 그대로인데 살펴보지 못한 녹음이 남아 다시 도는 횟수 상한(끝없이 돌지 않게)
MAX_LEFTOVER_PASSES = 3


async def _analyze(
    db: AsyncSession,
    job: PersonaJob,
    persona: Persona,
    progress,
    *,
    only_new: bool,
    span: Span = (0, 100),
) -> None:
    """그분 목소리로 고른 구간을 녹음마다 살펴본다.

    살펴보는 동안 목소리를 다시 고르거나 녹음 올리기로 돌아가도 된다(choose_voices·back_to_upload
    가 기다리게 하지 않는다). 녹음 하나를 마칠 때마다 고른 목소리와 목소리 묶음이 그대로인지 보고,
    바뀌었으면 그 결과는 버리고 새로 고른 목소리로 다시 한다. 목소리 키(VOICE_1 …)는 다시 묶으면
    같은 이름이 다른 묶음을 가리킬 수 있어 묶음(VoiceGroup id)까지 견준다. 확인과 저장은 대화 상대
    줄을 잠근 채 해서, 고르기가 그 사이에 끼어 예전 목소리 결과가 남지 않게 한다.

    끝내기 전에 잠근 채로 살펴보지 못한 녹음(그분 구간이 있는데 분석이 없는 것)이 남았는지 보고,
    남았으면 그것만 마저 한다. 돌아올 때는 대화 상대 줄을 잠근 채다: 부르는 쪽이 바로 commit 한다
    (run_analyze 는 작업 끝 commit 이, 나머지는 직접).

    받아쓴 글을 해외 회사(OpenAI)로 보내기 전에 게이트를 다시 본다.
    """
    await gate.check_job(db, persona.id, "audio")
    context = intake.subject_context(persona.basics, persona.answers)
    state = await _voice_state(db, persona.id)
    leftover_passes = 0
    while True:
        subject = state[0]
        targets = [
            r
            for r in await load_recordings(db, persona.id)
            if r.status == "TRANSCRIBED"
            and any(_is_subject(s, subject) for s in r.segments)
            and not (only_new and r.analysis)
        ]
        n = len(targets)
        changed = False
        for i, rec in enumerate(targets, start=1):
            step = f"말투와 기억을 익히는 중 ({i}/{n})"
            await progress(step, _at(span, (i - 0.9) / n))
            segments = _segment_payload(rec.segments, subject)
            expect = max(40.0, len(segments) * 0.6)
            async with _creep(
                _job_writer(job.id), step, _at(span, (i - 0.9) / n), _at(span, i / n), expect
            ):
                analysis = await ai_service.analyze_recording(
                    jobId=job.id,
                    recordingId=rec.id,
                    transcriptSegments=segments,
                    subjectContext=context,
                    subjectSpeakerLabel=SUBJECT_LABEL,
                )
            if await _voice_state(db, persona.id, lock=True) != state:
                await db.commit()  # 잠금만 푼다(바꾼 것 없음)
                changed = True
                break
            rec.analysis = analysis
            await db.commit()
            await progress(step, _at(span, i / n))
        now = await _voice_state(db, persona.id, lock=True)
        if not changed and now == state:
            if not await _unanalysed(db, persona.id, subject):
                return  # 잠근 채 돌아간다(위 설명)
            leftover_passes += 1
            if leftover_passes > MAX_LEFTOVER_PASSES:
                logger.warning("recordings left unanalysed for %s", persona.id)
                return
            logger.info("unanalysed recordings left for %s; analysing them", persona.id)
        else:
            logger.info("subject voices changed during analysis of %s; starting over", persona.id)
            await progress("다시 고른 목소리로 살펴보는 중")
        await db.commit()  # 잠금을 푼다
        state = now
        # 바뀐 목소리로 다시 할 때 예전 결과는 고르기·다시 묶기가 이미 비웠다. 빈 것만 한다.
        only_new = True


async def _chosen_voices(db: AsyncSession, persona_id: str, *, lock: bool = False) -> set[str]:
    """지금 DB 에 있는 '그분 목소리'(다른 요청이 바꿨을 수 있어 새로 읽는다)."""
    query = select(Persona.subject_voices).where(Persona.id == persona_id)
    if lock:
        query = query.with_for_update()
    return set(await db.scalar(query) or [])


async def _voice_state(
    db: AsyncSession, persona_id: str, *, lock: bool = False
) -> tuple[set[str], frozenset[str]]:
    """(고른 목소리, 지금 목소리 묶음 id). 다시 묶으면 묶음 id 가 모두 바뀐다."""
    subject = await _chosen_voices(db, persona_id, lock=lock)
    groups = frozenset(
        (await db.scalars(select(VoiceGroup.id).where(VoiceGroup.persona_id == persona_id))).all()
    )
    return subject, groups


async def _unanalysed(db: AsyncSession, persona_id: str, subject: set[str]) -> bool:
    """그분 구간이 있는데 아직 살펴보지 않은(분석이 없는) 녹음이 있나."""
    return any(
        r.status == "TRANSCRIBED"
        and r.analysis is None
        and any(_is_subject(s, subject) for s in r.segments)
        for r in await load_recordings(db, persona_id)
    )


def _subject_lines(recordings: list[Recording], subject: set[str]) -> list[str]:
    """그분이 직접 한 짧은 말. 말투 예시와 '자주 하시던 말' 후보로 쓴다."""
    out: list[str] = []
    for rec in recordings:
        for seg in rec.segments:
            if _is_subject(seg, subject) and 6 <= len(seg.text) <= 40 and seg.text not in out:
                out.append(seg.text)
    return out


def phrase_suggestions(recordings: list[Recording], subject: set[str]) -> list[str]:
    out: list[str] = []
    for rec in recordings:
        style = (rec.analysis or {}).get("speechStyle") or {}
        for row in style.get("recurringPhrases", []):
            phrase = (row.get("phrase") or "").strip()
            if phrase and phrase not in out:
                out.append(phrase)
    for text in _subject_lines(recordings, subject):
        if len(out) >= 12:
            break
        if len(text) <= 25 and text not in out:
            out.append(text)
    return out[:12]


def _evenly(items: list[str], limit: int) -> list[str]:
    if len(items) <= limit:
        return items
    step = len(items) / limit
    return [items[int(i * step)] for i in range(limit)]


def build_prompt_material(
    basics: dict,
    answers: dict,
    recordings: list[Recording],
    subject: set[str],
    *,
    after_listener: list[dict] | None = None,
) -> tuple[dict, dict, list[str]]:
    """질문 답 + 녹음 분석 → (subjectContext, intakeContext, 말투 예시).

    AI 레포 run_pipeline.Ctx.prompt_material 과 같은 규칙: 같은 칸에 둘 다 있으면 답이
    이긴다(사람이 직접 쓴 것). 녹음은 답이 비운 칸만 채운다.

    after_listener 는 가족 표에서 듣는 사람 바로 뒤에 둘 줄이다(가족 공유: 대화 상대를 만든 주인 줄,
    isOwner). 녹음 속 이름을 합치기 전에 넣어, 녹음이 주인을 부른 말이 다른 가족 줄로 가지 않게
    한다. 주인 줄은 듣는 사람 줄처럼 녹음이 고치지 않는다. 주인과 이야기할 때는 넘기지 않는다.
    """
    # '기억이 안 나요'·'따로 없었어요' 같은 표시 값은 여기서부터 빈 답이다(AI 로 가지 않는다)
    answers = intake.for_ai(answers)
    payload = intake.build_intake(basics, answers)
    subject_context, intake_context = payload["subjectContext"], payload["intakeContext"]
    if after_listener:
        intake_context["familyMap"][1:1] = after_listener
    analyses = [r.analysis for r in recordings if r.analysis]

    # 되풀이한 짧은 말은 감탄사로, 문장 특징은 말투 요약으로 (d_merge_recording_prompt 와 같은 기준)
    counts: Counter[str] = Counter()
    patterns: list[str] = []
    called: dict[str, str] = {}
    taboo: dict[str, dict] = {}
    for analysis in analyses:
        style = analysis.get("speechStyle") or {}
        for row in style.get("recurringPhrases", []):
            phrase = (row.get("phrase") or "").strip()
            if phrase:
                counts[phrase] += max(1, len(row.get("sourceTranscriptSegmentIds") or []))
        for pattern in style.get("sentencePatterns", []):
            if pattern not in patterns:
                patterns.append(pattern)
        for row in style.get("addressTerms", []):
            if row.get("person") and row.get("term"):
                called.setdefault(row["person"], row["term"])
        for flag in analysis.get("safetyFlags", []):
            topic = SAFETY_TOPIC.get(flag.get("type"), flag.get("type") or "기타")
            taboo.setdefault(
                topic, {"topic": topic, "grade": "물으면만", "note": flag.get("description")}
            )

    speech_style = intake_context["speechStyle"]
    # 직접 적은 감탄사(M3-04)가 있으면 그것을, 없으면 녹음에서 되풀이한 짧은 말을 쓴다
    speech_style["interjections"] = (
        speech_style.get("interjections")
        or [p for p, n in counts.most_common() if len(p) <= 6 and n >= 3][:10]
    )
    if patterns:
        speech_style["summary"] = " / ".join(patterns)

    for person, term in called.items():
        same = next(
            (
                row
                for row in intake_context["familyMap"]
                if row["name"] == person
                or (len(person) >= 2 and person in row["name"])
                or (len(row["name"]) >= 2 and row["name"] in person)
            ),
            None,
        )
        if same is None:
            intake_context["familyMap"].append({"name": person, "addressTermTo": term})
        elif not (same.get("addressTermTo") or same.get("isListener") or same.get("isOwner")):
            same["addressTermTo"] = term
    topics = {row["topic"] for row in intake_context["tabooTopics"]}
    intake_context["tabooTopics"] += [row for t, row in taboo.items() if t not in topics]

    # 직접 적은 '자주 하시던 말'(C-09·M4-04)을 앞에 두고 녹음 예시로 채운다. 조립기가 8개로 추린다.
    typed = list(dict.fromkeys([*answers.get("C-09", []), *answers.get("M4-04", [])]))
    heard = [t for t in _evenly(_subject_lines(recordings, subject), 40) if t not in typed]
    examples = typed[:8] + heard[: max(0, 8 - len(typed))]
    return subject_context, intake_context, examples


# ────────────────────────── 3. 페르소나 만들기 ──────────────────────────


async def _wait_for_analysis(
    db: AsyncSession, persona_id: str, progress, span: Span = (0, 30)
) -> None:
    waited = 0
    while True:
        job = await jobs.latest(db, persona_id, "ANALYZE")
        if job is None or job.status == JobStatus.DONE:
            return
        if job.status == JobStatus.FAILED:
            # job.error 는 이미 고객에게 보여도 되는 문장이다(jobs.friendly_error)
            raise ValueError(f"녹음을 살펴보지 못했어요. {job.error or '다시 해 주세요.'}")
        await progress(
            f"녹음을 마저 살펴보는 중 · {job.detail}" if job.detail else "녹음을 마저 살펴보는 중",
            _at(span, (job.percent or 0) / 100),
        )
        await asyncio.sleep(2)
        waited += 2
        if waited > ANALYZE_WAIT_SECONDS:
            raise ValueError("녹음을 살펴보는 데 너무 오래 걸려요. 다시 해 주세요.")


def _clean_subject_segments(rec: Recording, subject: set[str]) -> tuple[list, list]:
    """(깨끗한 그분 구간, 그분 구간 전부). 깨끗한 것: 충분히 길고, 남의 말이 겹치거나 바로
    붙어 있지 않고, 웃음·울음 표시가 없는 구간."""
    mine, others = [], []
    for s in rec.segments:
        (mine if _is_subject(s, subject) else others).append(s)
    mine = [s for s in mine if s.end_ms - s.start_ms >= CLONE_MIN_SEGMENT_MS]

    def clean(seg) -> bool:
        text = seg.corrected_text or seg.transcript_text or ""
        if any(mark in text for mark in CLONE_SKIP_TEXT):
            return False
        return not any(
            o.start_ms < seg.end_ms + CLONE_CROSSTALK_MS
            and o.end_ms > seg.start_ms - CLONE_CROSSTALK_MS
            for o in others
        )

    return [s for s in mine if clean(s)], mine


def _clone_samples(recordings: list[Recording], subject: set[str]) -> list[dict]:
    """복제 후보 구간을 녹음마다 긴 것부터 번갈아 담는다(한 통화가 다 차지하지 않게).

    남의 목소리가 섞였을 만한 구간은 뺀다. 소리 품질(잡음·찢어짐)은 AI 서버가 실제 소리를 재서
    다시 고르므로 여기서는 후보를 넉넉히(CLONE_CANDIDATE_SECONDS) 보낸다. 깨끗한 구간만으로
    10초가 안 되면(통화 내내 말이 겹치는 등) 그분 구간 전부를 후보로 쓴다.
    """
    storage = get_storage()
    picked = [(rec, *_clean_subject_segments(rec, subject)) for rec in recordings]
    clean_ms = sum(s.end_ms - s.start_ms for _, clean, _ in picked for s in clean)
    use_clean = clean_ms >= CLONE_MIN_SECONDS * 1000
    queues = []
    for rec, clean, everything in picked:
        segs = sorted(clean if use_clean else everything, key=lambda s: s.start_ms - s.end_ms)
        if segs:
            queues.append((storage.signed_url(rec.storage_key, AUDIO_URL_TTL), segs))
    samples: list[dict] = []
    budget_ms = 0
    while budget_ms < CLONE_CANDIDATE_SECONDS * 1000 and len(samples) < CLONE_MAX_SAMPLES:
        added = False
        for url, queue in queues:
            if (
                queue
                and budget_ms < CLONE_CANDIDATE_SECONDS * 1000
                and len(samples) < CLONE_MAX_SAMPLES
            ):
                seg = queue.pop(0)
                samples.append({"audioUrl": url, "startMs": seg.start_ms, "endMs": seg.end_ms})
                budget_ms += seg.end_ms - seg.start_ms
                added = True
        if not added:
            break
    if budget_ms < CLONE_MIN_SECONDS * 1000:
        raise ValueError(
            "그분 목소리가 너무 짧아 목소리를 만들 수 없어요. "
            f"그분 말씀이 {CLONE_MIN_SECONDS}초 넘게 담긴 녹음을 더 올려 주세요."
        )
    return samples


# 목소리를 둔 곳(Persona.voice_provider) → AI 서버 /v1/voice/delete 의 provider.
# 예전 똑 닮은 목소리(elevenlabs_pvc)는 ElevenLabs 에 있어 ElevenLabs 로 지운다.
# 모르는 값(None 포함)은 지우지 않는다: 관리자가 손으로 넣은 목소리일 수 있다
# (PATCH /admin/personas 로 목소리를 바꾸면 voice_provider 를 비운다).
VENDOR_PROVIDERS = {
    "fish": "fish",
    "elevenlabs": "elevenlabs",
    "minimax": "minimax",
    "elevenlabs_pvc": "elevenlabs",
}
# options.reason: 예전 목소리를 Fish drama-3 로 다시 만드는 VOICE 작업(voice_retrain 이 건다)
RETRAIN_REASON = "fish"


async def voice_in_use(voice_id: str) -> bool:
    """이 목소리를 쓰는 대화 상대가 있나(바꾸거나 지운 commit 뒤에, 따로 연 세션으로 본다)."""
    async with SessionLocal() as s:
        found = await s.scalar(select(Persona.id).where(Persona.voice_id == voice_id).limit(1))
    return found is not None


async def delete_vendor_voice(voice_id: str | None, provider: str | None) -> bool:
    """목소리를 만든 회사에서도 지운다. 실패해도 하던 일(지우기·다시 만들기)은 막지 않는다.

    다른 대화 상대가 아직 쓰는 목소리(관리자가 같은 목소리를 넣은 경우)는 지우지 않는다. 지우지
    못하면 관리자에게 알린다(목소리 id 만 보낸다 — 회사 화면에서 손으로 지운다).
    True = 지웠거나 지울 것이 없음, False = 지우지 못함(지운 뒤 알림 글이 달라진다 — deletion).
    """
    if not voice_id or provider not in VENDOR_PROVIDERS:
        return True
    if await voice_in_use(voice_id):
        logger.info("vendor voice %s kept: another persona still uses it", voice_id)
        return True
    vendor = VENDOR_PROVIDERS[provider]
    try:
        await ai_service.delete_voice(voice_id=voice_id, provider=vendor)
    except Exception as error:  # noqa: BLE001 — 남은 목소리는 알림을 보고 손으로 지운다
        logger.warning("vendor voice delete failed %s (%s): %s", voice_id, provider, error)
        notify.error(
            f"VOICE_DELETE_FAILED {voice_id}",
            f"{vendor} 에서 목소리를 지우지 못했어요. 그 회사 화면에서 손으로 지워 주세요",
        )
        return False
    return True


async def pvc_leftovers(db: AsyncSession, persona_id: str) -> list[tuple[str, str | None]]:
    """예전 똑 닮은 목소리(ElevenLabs PVC, 이제 만들지 않는다) 기록에 남은 회사 쪽 목소리.

    만들다 만 PVC 와, PVC 로 바꾸며 둔 처음 목소리. 정리한 기록(REMOVED)은 건너뛴다.
    """
    row = await db.scalar(select(PremiumVoice).where(PremiumVoice.persona_id == persona_id))
    if row is None or row.status == "REMOVED":
        return []
    found = [(row.voice_id, "elevenlabs_pvc"), (row.previous_voice_id, row.previous_voice_provider)]
    return [(voice_id, provider) for voice_id, provider in found if voice_id]


def reply_audio_key(conversation_id: str, message_id: str, voice_id: str) -> str:
    """대답 음성을 둘 저장소 키. 목소리마다 키가 달라서, 목소리를 바꾼 뒤 예전 목소리로 만든 파일을
    지워도(_drop_reply_audio) 그사이 새 목소리로 다시 만든 파일은 남는다."""
    tag = hashlib.sha256(voice_id.encode()).hexdigest()[:10]
    return f"conversations/{conversation_id}/speech/{message_id}-{tag}.mp3"


async def _reply_audio_keys(db: AsyncSession, persona_id: str) -> list[str]:
    """저장해 둔 대답 음성 파일의 저장소 키(예전 주소 모양은 빼고)."""
    conversation_ids = select(Conversation.id).where(Conversation.persona_id == persona_id)
    keys = await db.scalars(
        select(Message.audio_url).where(
            Message.conversation_id.in_(conversation_ids),
            Message.role == "assistant",
            Message.audio_url.like("conversations/%"),
        )
    )
    return list(keys)


async def _clear_reply_audio(db: AsyncSession, persona_id: str) -> None:
    """예전 목소리로 만들어 둔 대답 음성은 버린다. 다시 들으면 새 목소리로 만든다.

    (사용자 음성 메시지는 user 줄이라 건드리지 않는다). commit 은 부르는 쪽이 한다. 파일은 commit
    뒤에 _drop_reply_audio 로 지운다.
    """
    conversation_ids = select(Conversation.id).where(Conversation.persona_id == persona_id)
    await db.execute(
        update(Message)
        .where(Message.conversation_id.in_(conversation_ids), Message.role == "assistant")
        .values(audio_url=None)
    )


def _delete_keys(keys: list[str]) -> None:
    storage = get_storage()
    for key in keys:
        try:
            storage.delete_prefix(key)
        except Exception as error:  # noqa: BLE001 — 남은 파일은 대화 상대를 지울 때 함께 지운다
            logger.warning("reply audio delete failed: %s", type(error).__name__)


async def _drop_reply_audio(keys: list[str]) -> None:
    """바꾸기 전 목소리로 만든 대답 음성 파일을 저장소에서 지운다(바꾼 commit 뒤에 부른다).

    처리방침의 '목소리를 다시 만들 때까지'를 지킨다. 파일이 많을 수 있어 따로 돌린다.
    """
    if keys:
        await asyncio.to_thread(_delete_keys, keys)


async def remove_voice(db: AsyncSession, persona: Persona) -> bool | None:
    """그분 목소리를 지운다(목소리 만들기 확인을 거뒀을 때). 글 대화는 그대로다. commit 한다.

    그 목소리로 만든 대답 음성과, 목소리를 만든 회사의 목소리(예전 똑 닮은 목소리 기록에 남은
    것까지)도 지운다. 회사 쪽 결과를 돌려준다(persona_delete.delete_voices 와 같은 뜻 — 지울
    것이 없었으면 None).
    """
    # persona_delete 가 이 파일을 가져오므로 여기서 가져온다
    from app.services.persona_delete import delete_voices

    leftovers = await pvc_leftovers(db, persona.id)
    vendor = [(persona.voice_id, persona.voice_provider), *leftovers]
    old_audio = await _reply_audio_keys(db, persona.id)
    persona.voice_id = persona.voice_provider = None
    await _clear_reply_audio(db, persona.id)
    if leftovers:
        await db.execute(
            update(PremiumVoice)
            .where(PremiumVoice.persona_id == persona.id)
            .values(status="REMOVED", voice_id=None, detail="목소리 만들기 확인을 거둬 정리했어요")
        )
    await db.commit()
    await _drop_reply_audio(old_audio)
    return await delete_voices(vendor)


def _vendor_name(persona: Persona) -> str:
    # 목소리 회사에는 그분 성함 대신 알아볼 수 없는 이름을 보낸다
    return f"TalkTo_{persona.id[:8]}"


# 목소리 회사가 고를 깨끗한 구간이 모자랐다(AI 서버 INVALID_VOICE_SAMPLE)
UNCLEAR_SAMPLES = (
    "그분 목소리가 또렷하게 담긴 부분이 모자라 목소리를 만들지 못했어요. "
    "그분 말씀이 또렷한 녹음을 더 올려 주세요."
)


def _clone_error(persona_id: str, error: Exception) -> Exception:
    """목소리 만들기(clone) 실패를 정리한다. 고객에게 보일 까닭이 있으면 그 오류로 바꾼다.

    AI 서버와 연결이 끊기거나 시간이 넘었으면(AI 서버를 다시 켬 등) 목소리 회사는 목소리를 이미
    만들었을 수 있다. 목소리 id 를 몰라 지우지 못하니 관리자에게 알린다(회사 화면에서 이름
    TalkTo_<대화 상대 id 앞 8자> 로 찾아 지운다).
    """
    if isinstance(error, AIServerError):
        if error.code in TRANSPORT_CODES:
            notify.error(
                f"VOICE_CLONE_INTERRUPTED {persona_id[:8]}",
                "AI 서버와 연결이 끊겨 목소리 만들기가 멈췄어요. Fish Audio 에 이름이 "
                f"TalkTo_{persona_id[:8]} 로 시작하는 목소리가 남았으면 지워 주세요",
            )
        if error.code == "INVALID_VOICE_SAMPLE":
            return ValueError(UNCLEAR_SAMPLES)
    return error


async def _mark_clone(db: AsyncSession, job: PersonaJob, **marks) -> None:
    """목소리 회사에 새 목소리를 만드는 중이라는 표시를 작업(options)에 남긴다. commit 한다.

    clone: started → done. oldVoice·newVoice: [목소리 id, 회사]. 서버가 꺼져 작업이 끊기면
    (jobs.fail_interrupted) clean_interrupted_clones 가 이 표시를 보고 회사에 남은 목소리를 지운다.
    """
    job.options = {**(job.options or {}), **marks}
    await db.commit()


async def clean_interrupted_clones() -> int:
    """서버를 다시 켜며 끊긴 목소리 만들기가 회사에 남긴 목소리를 지운다. 살펴본 작업 수.

    - 새 목소리를 받고 바꾸기 전에 끊겼으면: 아무도 쓰지 않는 새 목소리를 지운다.
    - 바꾼 뒤(예전 목소리를 지우기 전)에 끊겼으면: 예전 목소리를 지운다.
    - 회사가 답하기 전에 끊겼으면: 목소리 id 를 몰라 관리자에게 알린다(회사 화면에서 이름
      TalkTo_<대화 상대 id 앞 8자> 로 찾아 지운다).

    켤 때 한 번 부른다(main.periodic, AI 서버가 뜬 뒤). 본 작업은 clone=cleaned 로 바꿔 다시 보지
    않는다.
    """
    async with SessionLocal() as db:
        rows = (
            await db.scalars(
                select(PersonaJob).where(
                    PersonaJob.kind.in_(("BUILD", "VOICE")),
                    PersonaJob.status == JobStatus.FAILED,
                    PersonaJob.error == jobs.INTERRUPTED,
                )
            )
        ).all()
        seen = 0
        for job in rows:
            options = job.options or {}
            if options.get("clone") != "started":
                continue
            persona = await db.get(Persona, job.persona_id)
            current = persona.voice_id if persona else None
            new, old = options.get("newVoice"), options.get("oldVoice")
            if not new:
                notify.error(
                    f"VOICE_CLONE_INTERRUPTED {job.persona_id[:8]}",
                    "서버를 다시 켜며 목소리 만들기가 끊겼어요. Fish Audio 에 이름이 "
                    f"TalkTo_{job.persona_id[:8]} 로 시작하는 목소리가 남았으면 지워 주세요",
                )
            elif new[0] != current:
                await delete_vendor_voice(*new)
            elif old and old[0] and old[0] != current:
                await delete_vendor_voice(*old)
            job.options = {**options, "clone": "cleaned"}
            await db.commit()  # 하나씩 저장한다(목소리 회사를 부르는 동안 쓰기를 열어 두지 않게)
            seen += 1
    if seen:
        logger.info("checked %s interrupted voice clones", seen)
    return seen


async def run_voice(db: AsyncSession, job: PersonaJob, progress) -> None:
    """만든 페르소나의 목소리만 다시 복제한다(목소리 회사를 바꿨을 때, 복제에 실패했을 때).

    options.reason == "fish" 는 예전 목소리를 Fish drama-3 로 다시 만드는 일이다(voice_retrain).
    새 목소리를 다 만들 때까지는 예전 목소리로 대답한다. 바꾸기(목소리·대답 음성 버리기·예전
    똑 닮은 목소리 기록 정리)는 한 번에 저장하고, 그 뒤에 예전 대답 음성 파일과 예전 목소리를
    지운다. 새 목소리를 받은 뒤 무엇이 실패하면(끊김 포함) 새 목소리를 회사에서 지운다.
    """
    await gate.check_job(db, job.persona_id, "voice")
    persona = await db.get(Persona, job.persona_id, populate_existing=True)
    await progress("그분 목소리를 고르는 중", 10)
    recordings = await load_recordings(db, persona.id)
    subject = set(persona.subject_voices or [])
    provider = voice_provider()
    if not await fish_allowed(db, persona):
        raise ValueError(FISH_CONSENT_NEEDED)
    phone = await mostly_phone(db, recordings, subject)
    old_voice = (persona.voice_id, persona.voice_provider)
    await progress("그분 목소리를 다시 만드는 중", 30)
    samples = _clone_samples(recordings, subject)
    # 목소리 회사로 보내기 바로 앞에서 한 번 더(그사이 멈춤·이의 접수가 걸렸을 수 있다)
    await gate.check_job(db, persona.id, "voice")
    await _note_admin_fish(db, persona)
    await _mark_clone(db, job, clone="started", oldVoice=list(old_voice))
    try:
        async with _creep(_job_writer(job.id), "그분 목소리를 다시 만드는 중", 30, 85, 60):
            cloned = await ai_service.clone_voice(
                name=_vendor_name(persona), samples=samples, provider=provider
            )
    except AIServerError as error:
        reason = _clone_error(persona.id, error)
        if reason is error:
            raise
        raise reason from error
    leftovers: list[tuple[str, str | None]] = []
    try:
        await _mark_clone(db, job, newVoice=list(cloned))
        await progress("새 목소리로 바꾸는 중", 90)
        leftovers = await pvc_leftovers(db, persona.id)
        old_audio = await _reply_audio_keys(db, persona.id)
        persona.voice_id, persona.voice_provider = cloned
        await _clear_reply_audio(db, persona.id)
        if leftovers:
            await db.execute(
                update(PremiumVoice)
                .where(PremiumVoice.persona_id == persona.id)
                .values(status="REMOVED", voice_id=None, detail="목소리를 새로 만들며 정리했어요")
            )
        await db.commit()
    except BaseException:  # 서버가 꺼지며 끊긴 것(CancelledError)까지
        await db.rollback()
        # 바꾸지 못했으니 아무도 쓰지 않는 새 목소리다. 회사에 남기지 않는다.
        await delete_vendor_voice(*cloned)
        raise
    await _drop_reply_audio(old_audio)
    reason = (job.options or {}).get("reason")
    await analytics.track_persona(
        db,
        "voice_created",
        persona.id,
        provider=persona.voice_provider,
        remake=True,
        phone=phone,
        **({"reason": reason} if reason else {}),
    )
    # 새 목소리로 바꾼 뒤에 예전 목소리를 회사에서 지운다
    done = {persona.voice_id}
    for voice_id, vendor in [old_voice, *leftovers]:
        if voice_id and voice_id not in done:
            done.add(voice_id)
            await delete_vendor_voice(voice_id, vendor)
    await _mark_clone(db, job, clone="done")


async def _reflect(
    subject_context: dict, reflected: list[Memory], importance: dict[str, int]
) -> list[str]:
    """성격 정리(통찰). 덤이라 실패해도 만들기를 멈추지 않는다. AI 회사 문제(크레딧·키)는 다음
    단계도 어차피 막히니 그대로 올린다(관리자 경고·차분한 문장)."""
    try:
        return await ai_service.reflect(
            subjectContext=subject_context,
            memories=[
                {
                    "id": m.id,
                    "memoryText": m.body_markdown,
                    "tags": m.tags,
                    "importanceScore": min(10, max(1, importance[m.id])),
                }
                for m in reflected
            ],
        )
    except AIServerError as error:
        if error.code in PROVIDER_CODES:
            raise
        logger.warning("reflection skipped: %s", error.code or error.status_code)
        return []
    except (KeyError, TypeError, ValueError) as error:
        logger.warning("reflection skipped: %s", type(error).__name__)
        return []


async def _assemble(
    db: AsyncSession,
    job: PersonaJob,
    persona: Persona,
    person: Person,
    recordings: list[Recording],
    subject: set[str],
    progress,
    span: Span = (0, 100),
) -> None:
    """기억(녹음 분석 + 질문 답) → 통찰 → 지침 → 임베딩.

    AI 를 부르는 동안에는 DB 를 건드리지 않고, 다 만든 뒤에 예전 기억을 한 번에 바꾼다.
    다 만든 페르소나를 고칠 때(run_update) 그동안에도 예전 지침·기억으로 대화할 수 있게.

    관리자가 가져온 대화 상대(origin=import)는 아무것도 바꾸지 않는다: 손으로 다듬은 지침을 녹음·
    질문 조립본으로 덮어쓰지 않는다(화면 API 가 먼저 막고, 여기는 이중 안전이다).
    """
    if persona.origin == ORIGIN_IMPORT:
        logger.warning("assemble skipped for imported persona %s", persona.id)
        return
    await progress("기억을 한데 모으는 중", _at(span, 0.05))
    importance: dict[str, int] = {}
    memories: list[Memory] = []
    for rec in recordings:
        for row in (rec.analysis or {}).get("memorySegments", []):
            tags = row.get("tags") or []
            memory = Memory(
                id=str(uuid4()),
                person_id=person.id,
                created_by_user_id=person.owner_user_id,
                title=(tags[0] if tags else "녹음 속 기억")[:120],
                memory_type="EPISODE",
                related_people=row.get("relatedPeople") or [],
                body_markdown=row["memoryText"],
                tags=tags[:10],
                confidence_score=1.0 if row.get("confidence") == "confirmed" else 0.6,
                source="RECORDING",
                recording_id=rec.id,
            )
            memories.append(memory)
            importance[memory.id] = int(row.get("importanceScore") or 5)
    asked_only: set[str] = set()
    for row in intake.build_intake_memories(persona.basics, persona.answers):
        # '물으면만' 기억(종교·힘들었던 시기 등)은 제목에 표시해 둔다. 대화에서 AI 가 그 표시를
        # 보고 먼저 꺼내지 않는다(build_memory_block).
        ask = row.get("usageScope") == "물으면만"
        memory = Memory(
            id=str(uuid4()),
            person_id=person.id,
            created_by_user_id=person.owner_user_id,
            title=intake.memory_title(row["tags"][0], row.get("usageScope")),
            memory_type="EPISODE",
            related_people=row["relatedPeople"],
            body_markdown=row["memoryText"],
            tags=row["tags"],
            source="INTAKE",
        )
        memories.append(memory)
        importance[memory.id] = row["importanceScore"]
        if ask:
            asked_only.add(memory.id)

    subject_context, intake_context, examples = build_prompt_material(
        persona.basics, persona.answers, recordings, subject
    )
    insights: list[str] = []
    # '물으면만' 기억은 성격 정리(지침에 늘 들어간다)에 넣지 않는다 — 넣으면 묻지 않아도 나온다
    reflected = [m for m in memories if m.id not in asked_only]
    # 성격 정리는 AI 서버가 예전 조립기(legacy)일 때만 지침에 쓰인다(layered 는 버린다). 꺼 두면
    # 부르지 않는다(PERSONA_REFLECTION).
    if reflected and get_settings().persona_reflection:
        await progress("성격과 가치관을 정리하는 중", _at(span, 0.25))
        insights = await _reflect(subject_context, reflected, importance)

    await progress("그분처럼 말하는 법을 정리하는 중", _at(span, 0.55))
    instructions = await ai_service.assemble(
        subjectContext=subject_context,
        intakeContext=intake_context,
        speechExamples=examples,
        personaInsights=insights[:50],
    )

    if memories:
        await progress("기억을 찾기 쉽게 정리하는 중", _at(span, 0.85))
        vectors = await ai_service.embed(
            job_id=job.id,
            items=[
                {"memorySegmentId": m.id, "embeddingIndex": i, "text": m.body_markdown}
                for i, m in enumerate(memories)
            ],
        )
        for m in memories:
            m.embedding = vectors.get(m.id)
            m.embedding_status = "READY" if m.embedding else "FAILED"

    # 다 만든 뒤 한 번에 바꾼다. 앞서 만든 기억은 지운다(직접 넣은 기억은 둔다).
    await db.execute(
        delete(Memory).where(
            Memory.person_id == person.id, Memory.source.in_(["RECORDING", "INTAKE"])
        )
    )
    db.add_all(memories)
    persona.instructions = instructions
    await db.commit()


async def run_build(db: AsyncSession, job: PersonaJob, progress) -> None:
    """질문 답·녹음 분석으로 지침·기억을 만들고(원하면) 목소리를 만든다.

    해외 회사를 부르기 전마다 게이트를 본다: 처음(분석이 OpenAI 를 부르기 전), 조립 앞, 목소리
    만들기 앞. 만드는 중에 멈춤·이의 접수가 걸리면 다음 회사 호출 전에 멈춘다.
    """
    await gate.check_job(db, job.persona_id, "build")
    await _wait_for_analysis(db, job.persona_id, progress, span=(0, 30))
    persona = await db.get(Persona, job.persona_id, populate_existing=True)
    # 살펴보기가 끝났어도 그사이 다시 고르기·다시 묶기로 비워진 녹음이 남을 수 있다. 빠진 것만
    # 마저 살펴본다(다 살펴봤으면 아무것도 부르지 않는다). 만드는 동안은 목소리를 바꿀 수 없다.
    await _analyze(db, job, persona, progress, only_new=True, span=(28, 30))
    await db.commit()
    person = await db.get(Person, persona.person_id)
    recordings = await load_recordings(db, persona.id)
    subject = set(persona.subject_voices or [])
    clone = bool(job.options.get("cloneVoice"))
    await gate.check_job(db, persona.id, "build")
    await _assemble(
        db, job, persona, person, recordings, subject, progress, span=(30, 85 if clone else 98)
    )

    note = ""
    # 목소리를 만들지 못한 까닭(고객에게 보여도 되는 한 문장). 대화 상대 상세의 voiceIssue.
    voice_error: str | None = None
    old_voice = (persona.voice_id, persona.voice_provider)
    cloned: tuple[str, str] | None = None
    phone = False
    if job.options.get("cloneVoice"):
        provider = voice_provider()
        await progress("그분 목소리를 만드는 중", 88)
        # 목소리 회사로 보내기 바로 앞에서 한 번 더(조립하는 사이 멈춤이 걸렸을 수 있다)
        await gate.check_job(db, persona.id, "voice")
        if not await fish_allowed(db, persona):
            # 배포 전에 건 작업·다시 시도처럼 동의 게이트를 지나지 않은 경우. 녹음은 보내지 않는다.
            logger.warning("voice clone skipped for persona %s: no overseas_fish", persona.id)
            note = " · 목소리는 해외 회사로 보내는 동의를 받은 뒤 만들 수 있어요"
            voice_error = FISH_CONSENT_NEEDED
        else:
            phone = await mostly_phone(db, recordings, subject)
            await _note_admin_fish(db, persona)
            await _mark_clone(db, job, clone="started", oldVoice=list(old_voice))
            try:
                cloned = await ai_service.clone_voice(
                    name=_vendor_name(persona),
                    samples=_clone_samples(recordings, subject),
                    provider=provider,
                )
            except (AIServerError, ValueError) as error:  # 목소리가 없어도 글로는 대화할 수 있다
                # 원문은 로그로만. 고객에게는 차분한 까닭 한 문장(voiceIssue)과 '목소리 만들기'.
                logger.warning("voice clone failed for persona %s: %s", persona.id, error)
                note = " · 목소리는 만들지 못했어요. 글로는 바로 대화할 수 있어요"
                voice_error = jobs.friendly_error(_clone_error(persona.id, error))

    try:
        if voice_error:
            job.options = {**(job.options or {}), "voiceError": voice_error[:300]}
        if cloned is not None:
            await _mark_clone(db, job, newVoice=list(cloned))
            persona.voice_id, persona.voice_provider = cloned
        persona.setup_step = SetupStep.READY
        persona.status = "READY"
        # 결제 전 대화 횟수는 처음 다 만든 날부터 30일마다 다시 센다(다시 만들어도 그날은 그대로)
        if persona.ready_at is None:
            persona.ready_at = datetime.now(UTC)
        job.detail = f"완료{note}"[:300]
        await db.commit()
    except BaseException:  # 서버가 꺼지며 끊긴 것(CancelledError)까지
        if cloned is not None:
            await db.rollback()
            # 저장하지 못한 새 목소리는 아무도 쓰지 않는다. 회사에 남기지 않는다.
            await delete_vendor_voice(*cloned)
        raise
    if cloned is not None:
        await analytics.track_persona(
            db,
            "voice_created",
            persona.id,
            provider=persona.voice_provider,
            remake=False,
            phone=phone,
        )
    if old_voice[0] and old_voice[0] != persona.voice_id:
        await delete_vendor_voice(*old_voice)
    if (job.options or {}).get("clone") == "started":
        await _mark_clone(db, job, clone="done")


async def run_update(db: AsyncSession, job: PersonaJob, progress) -> None:
    """다 만든 페르소나의 정보·답을 고친 뒤 지침과 기억만 다시 만든다. 목소리·녹음 분석은 그대로."""
    await gate.check_job(db, job.persona_id, "text")
    persona = await db.get(Persona, job.persona_id, populate_existing=True)
    if persona.origin == ORIGIN_IMPORT:
        job.detail = IMPORTED_SKIP
        return
    person = await db.get(Person, persona.person_id)
    recordings = await load_recordings(db, persona.id)
    subject = set(persona.subject_voices or [])
    await _assemble(db, job, persona, person, recordings, subject, progress, span=(0, 98))


async def run_extend(db: AsyncSession, job: PersonaJob, progress) -> None:
    """다 만든 페르소나에 녹음을 더한다. 대화는 그동안에도 예전 지침으로 계속된다.

    새 녹음만 옮기고, 목소리를 다시 묶은 뒤, 예전에 그분으로 고른 화자가 들어간 묶음을 그대로
    그분으로 이어 받는다(사람이 다시 고르지 않게). 예전 녹음에서 그분이 아니라고 한 화자는 다시
    묶여도 그분이 아니다. 새 녹음만 분석하고 지침·기억을 다시 만든다.
    """
    await gate.check_job(db, job.persona_id, "audio")
    persona = await db.get(Persona, job.persona_id, populate_existing=True)
    if persona.origin == ORIGIN_IMPORT:
        job.detail = IMPORTED_SKIP
        return
    person = await db.get(Person, persona.person_id)
    subject = set(persona.subject_voices or [])
    old = await load_recordings(db, persona.id)
    before = {(r.id, s.speaker_label) for r in old for s in r.segments if _is_subject(s, subject)}
    left_out = {(r.id, s.speaker_label) for r in old for s in r.segments} - before
    new_ids = {r.id for r in old if r.status == "UPLOADED"}
    done = await _transcribe_pending(db, job, persona, progress, retry_failed=False, span=(0, 40))
    await progress("녹음 속 목소리를 사람별로 나누는 중", 45)
    await _group_voices(db, persona, done)
    groups = await load_voices(db, persona.id)
    carried = [
        g.voice_key
        for g in groups
        if any((m["recordingId"], m["speakerLabel"]) in before for m in g.members)
    ]
    if carried:
        persona.subject_voices = carried
        for rec in done:
            for seg in rec.segments:
                if seg.voice_key in carried and (rec.id, seg.speaker_label) in left_out:
                    seg.voice_key = None
    # 다시 묶은 것을 바로 저장한다(대화 상대 줄을 잡은 채 다음 일로 가지 않게 — 그동안 가족의 대화
    # 보내기가 기다린다)
    await db.commit()
    carried_set = set(carried)
    covered = {m["recordingId"] for g in groups if g.voice_key in carried_set for m in g.members}
    missing = [r for r in done if r.id in new_ids and r.id not in covered]
    await _analyze(db, job, persona, progress, only_new=True, span=(45, 70))
    await db.commit()  # _analyze 는 대화 상대 줄을 잡은 채 돌아온다
    # 반영하는 사이 정보 고치기가 끝났을 수 있다. 최신 정보로 지침을 만든다.
    persona = await db.get(Persona, job.persona_id, populate_existing=True)
    # 조립(OpenAI) 앞에서 한 번 더(그사이 멈춤·이의 접수가 걸렸을 수 있다)
    await gate.check_job(db, persona.id, "audio")
    recordings = await load_recordings(db, persona.id)
    await _assemble(
        db,
        job,
        persona,
        person,
        recordings,
        set(persona.subject_voices or []),
        progress,
        span=(70, 98),
    )
    if missing:
        # 새 녹음에서 그분 목소리를 찾지 못했다. 조용히 '완료'라고만 하지 않는다.
        job.detail = (
            f"완료 · 새 녹음 {len(missing)}개에서는 그분 목소리를 찾지 못해 그 녹음은 "
            "익히지 못했어요"
        )[:300]
        await db.commit()
