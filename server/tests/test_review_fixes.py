"""2026-10-18 전체 점검에서 고친 것들(녹음 길이·메모리, 업로드, 목소리 메시지, 작업 경합, 로그인).

각 시험 위의 주석에 점검 항목 id 를 적는다(be-authz-1 등).
"""

import asyncio
import logging
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from legal_gates import declare_bereaved
from sqlalchemy import func, select, update
from test_billing import _admin, _ready_persona
from test_intake_more import _agree
from test_persona_setup import (  # noqa: F401
    ANSWERS,
    BASICS,
    FakeAI,
    _hold_analysis,
    _signup,
    _to_speakers,
    _wait,
    _wav,
    fake_ai,
)

from app import main as main_module
from app.api.v1 import conversations
from app.core import ratelimit
from app.core.config import Settings, get_settings
from app.core.security import create_refresh_token, create_token, decode_token
from app.db import session as session_module
from app.db.models import (
    JobStatus,
    Memory,
    Message,
    Person,
    Persona,
    PersonaJob,
    Recording,
    TranscriptSegment,
    UsageEvent,
    User,
    UserRole,
    UserStatus,
)
from app.db.session import SessionLocal
from app.main import app
from app.services import ai as ai_module
from app.services import audio, intake, jobs, memory_search, persona_builder
from app.services.storage import LocalStorage, check_prefix

API = "/api/v1"
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 필요")
Handler = Callable[[httpx.Request], httpx.Response]


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def ai(fake_ai: FakeAI, monkeypatch: pytest.MonkeyPatch) -> dict[str, Handler]:  # noqa: F811
    """가짜 AI 서버 앞에 끼운다. overrides[경로] 를 두면 그 경로만 다르게 답한다
    (오류·빈 대답·끊김)."""
    overrides: dict[str, Handler] = {}
    real = httpx._client.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        override = overrides.get(request.url.path)
        if override is not None:
            fake_ai.calls.setdefault(request.url.path, []).append({})
            return override(request)
        return fake_ai(request)

    monkeypatch.setattr(
        ai_module.httpx,
        "AsyncClient",
        lambda *a, **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    return overrides


def _error(status: int, code: str) -> Handler:
    return lambda request: httpx.Response(status, json={"error": {"code": code, "message": code}})


def _final_job(client: TestClient, h: dict, pid: str, kind: str, timeout: float = 20) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["jobs"].get(kind)
        if job and job["status"] != "RUNNING":
            return job
        time.sleep(0.1)
    raise AssertionError(f"{kind} did not finish")


def _recordings(pid: str) -> list[Recording]:
    async def go():
        async with SessionLocal() as db:
            rows = await db.scalars(
                select(Recording).where(Recording.persona_id == pid).order_by(Recording.created_at)
            )
            return list(rows.all())

    return _run(go())


def _usage(pid: str) -> int:
    async def go():
        async with SessionLocal() as db:
            return await db.scalar(
                select(func.count(UsageEvent.id)).where(UsageEvent.persona_id == pid)
            )

    return _run(go())


def _conversation(client: TestClient, h: dict, pid: str) -> str:
    return client.post(f"{API}/personas/{pid}/conversation", headers=h).json()["data"][
        "conversationId"
    ]


def _webm(seconds: int) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "v.webm"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi"]
            + ["-i", f"sine=frequency=440:duration={seconds}", "-c:a", "libopus", str(out)],
            check=True,
        )
        return out.read_bytes()


# ────────────────────────── 녹음 길이·메모리 (be-authz-1, be-input-1, be-flow-2) ──────────────────


@needs_ffmpeg
async def test_long_recording_is_refused_and_encode_is_capped(monkeypatch):
    monkeypatch.setattr(audio, "max_recording_seconds", lambda: 1)
    commands: list[list[str]] = []
    real = subprocess.run

    def spy(args, **kwargs):
        commands.append(list(args))
        return real(args, **kwargs)

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        wav.write_bytes(_wav(3.0))
        monkeypatch.setattr(audio.subprocess, "run", spy)
        with pytest.raises(audio.AudioError, match="녹음이 너무 길어요"):
            audio.prepare(wav, Path(tmp) / "out.m4a")
    monkeypatch.setattr(audio, "max_recording_seconds", lambda: 3600)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        wav.write_bytes(_wav(3.0))
        commands.clear()
        duration, phone = audio.prepare(wav, Path(tmp) / "out.m4a")
    assert 2900 <= duration <= 3100 and phone in (True, False)
    encode = next(c for c in commands if c[0] == "ffmpeg" and "aac" in c)
    assert encode[encode.index("-t") + 1] == "3601"  # 파일에 적힌 길이를 믿지 않고 바꿀 때도 자른다
    bands = [c for c in commands if "-af" in c]
    assert bands and all(c[c.index("-t") + 1] == str(audio.PHONE_BAND_SECONDS) for c in bands)
    assert audio.too_long_message().startswith("녹음이 너무 길어요")


@needs_ffmpeg
def test_too_long_recording_fails_alone_and_the_rest_goes_on(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    monkeypatch.setattr(audio, "max_recording_seconds", lambda: 10)
    with TestClient(app) as client:
        h = _signup(client, f"long-{uuid4().hex[:6]}@example.com")
        pid = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]
        up = client.post(
            f"{API}/personas/{pid}/recordings",
            headers=h,
            data={"pledge": "true"},
            files=[
                ("files", ("짧은.wav", _wav(5.0), "audio/wav")),
                ("files", ("긴.wav", _wav(25.0), "audio/wav")),
            ],
        )
        assert up.status_code == 200, up.text
        assert client.post(f"{API}/personas/{pid}/transcribe", headers=h).status_code == 200
        _wait(client, h, pid, "TRANSCRIBE")
        recordings = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["recordings"]
    by_name = {r["originalName"]: r for r in recordings}
    assert by_name["짧은.wav"]["status"] == "TRANSCRIBED"
    assert by_name["긴.wav"]["status"] == "FAILED"
    assert by_name["긴.wav"]["error"].startswith("녹음이 너무 길어요")


@needs_ffmpeg
def test_voice_previews_never_load_whole_recordings_into_memory(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    """미리듣기는 녹음을 하나씩 파일로 받아 자른다(storage.get 으로 통째로 올리지 않는다)."""

    def no_get(self, key):
        raise AssertionError(f"whole recording loaded: {key}")

    monkeypatch.setattr(LocalStorage, "get", no_get)
    with TestClient(app) as client:
        h = _signup(client, f"preview-{uuid4().hex[:6]}@example.com")
        pid = _to_speakers(client, h)
        detail = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]
    assert detail["step"] == "SPEAKERS"
    assert all(v["previewUrl"] for v in detail["voices"])
    assert all(r["status"] == "TRANSCRIBED" for r in detail["recordings"])


# ────────────────────────── 원본 지우기 순서 (be-flow-3) ──────────────────────────


@needs_ffmpeg
def test_recording_survives_a_crash_between_convert_and_save(fake_ai: FakeAI):  # noqa: F811
    """바꾼 m4a 만 남고 원본이 지워진 채 끊겼으면 그 m4a 로 잇는다. 둘 다 없으면 그 녹음만 실패."""
    with TestClient(app) as client:
        h = _signup(client, f"crash-{uuid4().hex[:6]}@example.com")
        pid = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]
        client.post(
            f"{API}/personas/{pid}/recordings",
            headers=h,
            data={"pledge": "true"},
            files=[("files", ("통화.wav", _wav(), "audio/wav"))],
        )
        assert client.post(f"{API}/personas/{pid}/transcribe", headers=h).status_code == 200
        _wait(client, h, pid, "TRANSCRIBE")
        rec = _recordings(pid)[0]
        assert rec.storage_key.endswith(".m4a") and rec.audio_ready
        lost = persona_builder.recording_key(pid, str(uuid4()), ".wav")

        async def crash():
            async with SessionLocal() as db:
                # 예전 순서에서 끊긴 모양: DB 는 원본(.wav)을 가리키는데 원본은 이미 지워졌다
                await db.execute(
                    update(Recording)
                    .where(Recording.id == rec.id)
                    .values(
                        storage_key=persona_builder.recording_key(pid, rec.id, ".wav"),
                        audio_ready=False,
                        status="UPLOADED",
                    )
                )
                db.add(
                    Recording(
                        persona_id=pid,
                        original_name="없는.wav",
                        storage_key=lost,
                        content_type="audio/wav",
                        size_bytes=10,
                    )
                )
                await db.commit()

        _run(crash())
        assert client.post(f"{API}/personas/{pid}/back-to-upload", headers=h).status_code == 200
        assert client.post(f"{API}/personas/{pid}/transcribe", headers=h).status_code == 200
        _wait(client, h, pid, "TRANSCRIBE")
    rows = {r.original_name: r for r in _recordings(pid)}
    assert rows["통화.wav"].status == "TRANSCRIBED" and rows["통화.wav"].audio_ready
    assert rows["통화.wav"].storage_key.endswith(".m4a")
    assert rows["없는.wav"].status == "FAILED"
    assert rows["없는.wav"].error == persona_builder.MISSING_FILE


# ────────────────────────── 전사 오류 코드 (contracts-2, be-flow-8) ──────────────────────────


@needs_ffmpeg
def test_only_speechless_errors_fail_the_recording(ai):
    with TestClient(app) as client:
        h = _signup(client, f"codes-{uuid4().hex[:6]}@example.com")
        pid = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]
        client.post(
            f"{API}/personas/{pid}/recordings",
            headers=h,
            data={"pledge": "true"},
            files=[("files", ("통화.wav", _wav(), "audio/wav"))],
        )
        path = "/v1/analysis/transcriptions"
        # 내려받기가 잠깐 실패(같은 422)는 녹음 탓이 아니다: 작업만 멈추고 녹음은 다시 옮길 수 있게
        ai[path] = _error(422, "AUDIO_DOWNLOAD_FAILED")
        client.post(f"{API}/personas/{pid}/transcribe", headers=h)
        job = _final_job(client, h, pid, "TRANSCRIBE")
        assert job["status"] == "FAILED"
        assert _recordings(pid)[0].status == "UPLOADED"
        # 말소리가 없으면 그 녹음만 실패
        ai[path] = _error(422, "EMPTY_TRANSCRIPT")
        client.post(f"{API}/personas/{pid}/transcribe", headers=h)
        _final_job(client, h, pid, "TRANSCRIBE")
        rec = _recordings(pid)[0]
        assert rec.status == "FAILED" and rec.error == persona_builder.NO_SPEECH
        # 글자가 하나도 없는 전사도 옮긴 녹음으로 두지 않는다
        ai[path] = lambda request: httpx.Response(
            200,
            json={
                "segments": [
                    {
                        "segmentIndex": 0,
                        "startMs": 0,
                        "endMs": 1000,
                        "speakerLabel": "SPK_0",
                        "transcriptText": "  ",
                    }
                ],
                "provider": "fake",
                "model": "fake",
            },
        )
        client.post(f"{API}/personas/{pid}/transcribe", headers=h)
        _final_job(client, h, pid, "TRANSCRIBE")
        rec = _recordings(pid)[0]
        assert rec.status == "FAILED" and rec.error == persona_builder.NO_SPEECH


# ────────────────────────── 다시 고르기·다시 묶기 중 살펴보기 (be-flow-1) ──────────────────────


def _analyses(pid: str) -> list:
    return [r.analysis for r in _recordings(pid)]


@needs_ffmpeg
def test_regroup_with_the_same_voice_key_reanalyses_everything(
    fake_ai: FakeAI,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
):
    """살펴보는 사이 녹음을 더해 다시 묶고 같은 이름(VOICE_1)을 골라도 빠지는 녹음이 없다."""
    release, seen = _hold_analysis(monkeypatch)
    with TestClient(app) as client:
        h = _signup(client, f"regroup-{uuid4().hex[:6]}@example.com")
        pid = _to_speakers(client, h)
        choose = f"{API}/personas/{pid}/speakers"
        assert client.post(choose, headers=h, json={"voiceKeys": ["VOICE_1"]}).status_code == 200
        assert client.post(f"{API}/personas/{pid}/back-to-upload", headers=h).status_code == 200
        more = client.post(
            f"{API}/personas/{pid}/recordings",
            headers=h,
            files=[("files", ("통화03.wav", _wav(), "audio/wav"))],
        )
        assert more.status_code == 200, more.text
        assert client.post(f"{API}/personas/{pid}/transcribe", headers=h).status_code == 200
        _wait(client, h, pid, "TRANSCRIBE")
        again = client.post(choose, headers=h, json={"voiceKeys": ["VOICE_1"]})
        assert again.status_code == 200, again.text
        release.set()
        _wait(client, h, pid, "ANALYZE")
    analyses = _analyses(pid)
    assert len(analyses) == 3 and all(analyses)


@needs_ffmpeg
def test_build_analyses_recordings_left_without_analysis(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        h = _signup(client, f"leftover-{uuid4().hex[:6]}@example.com")
        pid = _to_speakers(client, h)
        client.post(f"{API}/personas/{pid}/speakers", headers=h, json={"voiceKeys": ["VOICE_1"]})
        _wait(client, h, pid, "ANALYZE")
        before = len(fake_ai.calls["/v1/analysis/recording"])
        first = _recordings(pid)[0]

        async def lose():
            async with SessionLocal() as db:
                await db.execute(
                    update(Recording).where(Recording.id == first.id).values(analysis=None)
                )
                await db.commit()

        _run(lose())
        declare_bereaved(client, pid)  # 돌아가신 분은 만들기 전에 유족 명단(2026-10-22 법률 자문)
        built = client.post(
            f"{API}/personas/{pid}/build", headers=h, json={"answers": ANSWERS}
        )
        assert built.status_code == 200, built.text
        _wait(client, h, pid, "BUILD")
    assert len(fake_ai.calls["/v1/analysis/recording"]) == before + 1
    assert all(_analyses(pid))


# ────────────────────────── 작업 걸기 경합·끝 적기 (be-input-6, be-flow-9, be-flow-10) ──────────


async def _persona_rows() -> str:
    async with SessionLocal() as db:
        user = User(
            email=f"rows-{uuid4().hex[:8]}@example.com",
            name="x",
            password_hash="x",
            role=UserRole.USER,
            status=UserStatus.ACTIVE,
        )
        db.add(user)
        await db.flush()
        person = Person(owner_user_id=user.id, name="김순자", relationship_label="할머니")
        db.add(person)
        await db.flush()
        persona = Persona(person_id=person.id, name="김순자", status="DRAFT")
        db.add(persona)
        await db.commit()
        return persona.id


async def test_job_start_locks_the_persona_row_before_checking():
    persona_id = await _persona_rows()
    statements = []
    async with SessionLocal() as db:
        real = db.execute

        async def spy(statement, *args, **kwargs):
            statements.append(statement)
            return await real(statement, *args, **kwargs)

        db.execute = spy

        async def noop(_db, _job, _progress):
            await asyncio.sleep(0.2)

        await jobs.start(db, persona_id, "UPDATE", noop)
        with pytest.raises(HTTPException) as busy:
            await jobs.start(db, persona_id, "UPDATE", noop)
        assert busy.value.status_code == 409
    await jobs.wait_all()
    locked = [s for s in statements if getattr(s, "_for_update_arg", None) is not None]
    assert len(locked) == 2


async def test_job_result_write_failure_does_not_leave_it_running():
    persona_id = await _persona_rows()
    async with SessionLocal() as db:
        job = PersonaJob(persona_id=persona_id, kind="UPDATE", detail="준비하는 중", options={})
        db.add(job)
        await db.commit()
        job_id = job.id

    async def sabotage(db, _job, _progress):
        async def broken():
            raise ConnectionError("db gone")

        db.commit = broken

    await jobs._run(job_id, sabotage)
    async with SessionLocal() as db:
        row = await db.get(PersonaJob, job_id)
        assert row.status == JobStatus.FAILED and row.error == jobs.INTERRUPTED


# ────────────────────────── AI 오류 계약 (be-input-9, be-chat-06, contracts-4, be-flow-4) ─────


def _configured() -> Settings:
    return Settings(
        _env_file=None, ai_server_url="https://ai.example.test/", ai_server_token="shared-secret"
    )


async def test_ai_transport_errors_become_calm_ai_errors(monkeypatch):
    real = httpx.AsyncClient
    failure: list[Exception] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if failure:
            raise failure[0]
        return httpx.Response(503, json={"error": {"code": "AI_PROVIDER_QUOTA"}})

    monkeypatch.setattr(ai_module, "get_settings", _configured)
    monkeypatch.setattr(
        ai_module.httpx,
        "AsyncClient",
        lambda *a, **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    ai_module.provider_state.update(code=None, at=None, ok_at=None)
    try:
        # 말로 보낸 녹음도 다른 호출처럼 AI 회사 문제를 알아본다(예전에는 500 이 났다)
        with pytest.raises(ai_module.AIServerError) as quota:
            await ai_module.ai_service.transcribe("v.webm", b"x", "audio/webm")
        assert str(quota.value) == ai_module.AI_DOWN
        assert ai_module.provider_trouble()["code"] == "AI_PROVIDER_QUOTA"

        failure.append(httpx.ConnectError("refused"))
        with pytest.raises(ai_module.AIServerError) as lost:
            await ai_module.ai_service.assemble(subjectContext={})
        assert lost.value.code == ai_module.UNREACHABLE_CODE
        assert str(lost.value) == ai_module.AI_UNREACHABLE

        failure[0] = httpx.ReadTimeout("slow")
        with pytest.raises(ai_module.AIServerError) as slow:
            await ai_module.ai_service.chat(
                message="안녕",
                history=[],
                memories=[],
                persona={"subject_id": "s", "name": "n", "instructions": "x"},
                timeout=3,
            )
        assert slow.value.code == ai_module.TIMEOUT_CODE
        assert jobs.friendly_error(slow.value) == jobs.TIMED_OUT
    finally:
        ai_module.provider_state.update(code=None, at=None, ok_at=None)


# ────────────────────────── 빈 대답 (be-chat-02, contracts-1) ──────────────────────────


@needs_ffmpeg
def test_empty_reply_is_not_saved_and_old_empty_rows_do_not_break_the_room(ai, fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        h = _signup(client, f"empty-{uuid4().hex[:6]}@example.com")
        pid = _ready_persona(client, h)
        cid = _conversation(client, h, pid)
        used = _usage(pid)
        ai["/v1/persona/responses"] = lambda request: httpx.Response(
            200, json={"content": "   ", "retrievedMemoryIds": [], "provider": "f", "model": "f"}
        )
        sent = client.post(
            f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": "할머니"}
        )
        assert sent.status_code == 503 and sent.json()["detail"] == ai_module.AI_TROUBLE
        assert client.get(f"{API}/conversations/{cid}", headers=h).json()["data"]["messages"] == []
        assert _usage(pid) == used  # 대답을 못 받은 대화는 세지 않는다

        async def old_empty_row():
            async with SessionLocal() as db:
                db.add(Message(conversation_id=cid, role="assistant", content=""))
                await db.commit()

        _run(old_empty_row())
        del ai["/v1/persona/responses"]
        ok = client.post(
            f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": "할머니"}
        )
        assert ok.status_code == 200, ok.text
    history = fake_ai.calls["/v1/persona/responses"][-1]["history"]
    assert all(m["content"].strip() for m in history)


# ────────────────────────── 목소리 메시지 (be-input-3, be-chat-03, be-chat-04, be-chat-11) ──────


@needs_ffmpeg
def test_voice_message_limits(ai, monkeypatch):
    stt: dict[str, str] = {"text": "할머니 보고 싶어요"}
    ai["/v1/voice/transcriptions"] = lambda request: httpx.Response(200, json=dict(stt))
    voice = _webm(7)
    media = Path(get_settings().local_media_dir)
    with TestClient(app) as client:
        h = _signup(client, f"voice-{uuid4().hex[:6]}@example.com")
        pid = _ready_persona(client, h)
        cid = _conversation(client, h, pid)
        url = f"{API}/conversations/{cid}/messages/voice"

        def send():
            return client.post(url, headers=h, files={"file": ("v.webm", voice, "audio/webm")})

        ok = send()
        assert ok.status_code == 200, ok.text
        used = _usage(pid)

        # 길이 상한(초)을 넘으면 글로 옮기기 전에 돌려보낸다
        monkeypatch.setattr(get_settings(), "max_voice_message_seconds", 3)
        long = send()
        assert long.status_code == 413 and "3초" in long.json()["detail"]
        monkeypatch.setattr(get_settings(), "max_voice_message_seconds", 300)

        # 서버 전체에서 글로 옮기는 자리가 다 찼다
        monkeypatch.setattr(conversations, "_stt_active", conversations.STT_SLOTS)
        busy = send()
        assert busy.status_code == 503 and busy.json()["detail"] == conversations.VOICE_BUSY
        monkeypatch.setattr(conversations, "_stt_active", 0)

        # 한 사람이 앞의 녹음 대답을 받기 전에 또 보냈다
        me = client.get(f"{API}/auth/me", headers=h).json()["data"]["id"]
        conversations._voice_senders.add(me)
        try:
            twice = send()
        finally:
            conversations._voice_senders.discard(me)
        assert twice.status_code == 429

        # 옮긴 글이 너무 길다
        stt["text"] = "가" * (conversations.STT_MAX_CHARS + 1)
        assert send().status_code == 413
        stt["text"] = "할머니"

        # 대답을 받지 못하면 저장한 말 녹음도 지운다
        ai["/v1/persona/responses"] = _error(502, "EMPTY_REPLY")
        failed = send()
        assert failed.status_code == 503
        del ai["/v1/persona/responses"]

        # AI 서버에 닿지 못하면 차분한 503(예전에는 500)
        def lost(request):
            raise httpx.ConnectError("down", request=request)

        ai["/v1/voice/transcriptions"] = lost
        down = send()
        assert down.status_code == 503 and down.json()["detail"] == ai_module.AI_UNREACHABLE
        assert _usage(pid) == used  # 실패한 것은 하나도 세지 않았다
        saved = list((media / "conversations" / cid / "voice").glob("*"))
    assert len(saved) == 1  # 처음 성공한 것 하나만 남았다
    assert conversations._stt_active == 0 and not conversations._voice_senders


def test_voice_message_caps_fit_the_body_limit():
    from app.core import body_limit
    from app.services import voice_retrain

    assert conversations.VOICE_MAX_BYTES < body_limit.VOICE_MESSAGE_LIMIT
    # 화면의 가장 큰 형식(wav 16kHz mono 16bit)으로도 길이 상한만큼은 담긴다
    wav_bytes = get_settings().max_voice_message_seconds * 16000 * 2
    assert wav_bytes < conversations.VOICE_MAX_BYTES
    # AI 서버가 다시 켜지며 끊긴 것은 다시 만들기 실패로 세지 않는다(be-flow-4)
    assert ai_module.AI_UNREACHABLE in voice_retrain.NOT_COUNTED


@needs_ffmpeg
def test_same_reply_is_not_spoken_twice(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    """자동 재생과 '듣기'가 함께 오면 목소리 회사에는 한 번만 보낸다(뒤의 것은 기다린다)."""
    with TestClient(app) as client:
        h = _signup(client, f"speak-{uuid4().hex[:6]}@example.com")
        pid = _ready_persona(client, h)
        cid = _conversation(client, h, pid)
        sent = client.post(
            f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": "할머니"}
        )
        reply = sent.json()["data"]["assistantMessage"]["id"]
        before = len(fake_ai.calls.get("/v1/voice/speech", []))
        pending = asyncio.Event()
        pending.set()  # 앞의 요청이 끝났는데 저장하지 못했다
        monkeypatch.setitem(conversations._speaking, reply, pending)
        waited = client.post(f"{API}/messages/{reply}/speech", headers=h)
        assert waited.status_code == 502
        assert len(fake_ai.calls.get("/v1/voice/speech", [])) == before
        monkeypatch.delitem(conversations._speaking, reply)
        spoken = client.post(f"{API}/messages/{reply}/speech", headers=h)
        assert spoken.status_code == 200, spoken.text
        assert len(fake_ai.calls["/v1/voice/speech"]) == before + 1
    assert reply not in conversations._speaking


# ────────────────────────── 녹음 올리기 (be-authz-2, be-input-2) ──────────────────────────


@needs_ffmpeg
def test_recordings_are_streamed_not_read_into_memory(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    from starlette.datastructures import UploadFile

    async def no_read(self, size=-1):
        raise AssertionError("upload read into memory")

    monkeypatch.setattr(UploadFile, "read", no_read)
    content = _wav(3.0)
    with TestClient(app) as client:
        h = _signup(client, f"stream-{uuid4().hex[:6]}@example.com")
        pid = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]
        up = client.post(
            f"{API}/personas/{pid}/recordings",
            headers=h,
            data={"pledge": "true"},
            files=[("files", ("통화.wav", content, "audio/wav"))],
        )
        assert up.status_code == 200, up.text
        assert up.json()["data"][0]["removable"] is True
        empty = client.post(
            f"{API}/personas/{pid}/recordings",
            headers=h,
            files=[("files", ("빈.wav", b"", "audio/wav"))],
        )
        assert empty.status_code == 400
    rec = _recordings(pid)[0]
    stored = Path(get_settings().local_media_dir) / rec.storage_key
    assert stored.read_bytes() == content and rec.size_bytes == len(content)


# ────────────────────────── 아무것도 익히지 않은 녹음 빼기 (be-flow-8) ──────────────────────────


@needs_ffmpeg
def test_ready_persona_can_drop_a_recording_that_taught_nothing(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        h = _signup(client, f"drop-{uuid4().hex[:6]}@example.com")
        pid = _ready_persona(client, h)
        learned = _recordings(pid)[0]
        stray_id = str(uuid4())

        async def stray():
            async with SessionLocal() as db:
                db.add(
                    Recording(
                        id=stray_id,
                        persona_id=pid,
                        original_name="남의통화.m4a",
                        storage_key=persona_builder.recording_key(pid, stray_id, ".m4a"),
                        content_type="audio/mp4",
                        size_bytes=10,
                        status="TRANSCRIBED",
                        audio_ready=True,
                    )
                )
                db.add(
                    TranscriptSegment(
                        recording_id=stray_id,
                        segment_index=0,
                        start_ms=0,
                        end_ms=4000,
                        speaker_label="SPK_9",
                        transcript_text="모르는 사람 말",
                        voice_key="VOICE_9",
                    )
                )
                await db.commit()

        _run(stray())
        rows = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["recordings"]
        removable = {r["id"]: r["removable"] for r in rows}
        assert removable == {learned.id: False, stray_id: True}
        kept = client.delete(f"{API}/personas/{pid}/recordings/{learned.id}", headers=h)
        assert kept.status_code == 409
        dropped = client.delete(f"{API}/personas/{pid}/recordings/{stray_id}", headers=h)
        assert dropped.status_code == 200, dropped.text
    assert [r.id for r in _recordings(pid)] == [learned.id]


# ────────────────────────── 민감 동의 거두기 (be-chat-01) ──────────────────────────


def _memory_titles(pid: str) -> set[str]:
    async def go():
        async with SessionLocal() as db:
            persona = await db.get(Persona, pid)
            rows = await db.scalars(
                select(Memory.title).where(Memory.person_id == persona.person_id)
            )
            return set(rows.all())

    return _run(go())


@needs_ffmpeg
def test_withdrawing_religion_consent_drops_its_memories_at_once(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    with TestClient(app) as client:
        h = _signup(client, f"religion-{uuid4().hex[:6]}@example.com")
        pid = _ready_persona(client, h)
        _agree(client, h, pid, "sensitive_religion")
        answers = {
            **client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["answers"],
            "M1-09": "불교",
        }
        saved = client.put(
            f"{API}/personas/{pid}", headers=h, json={"basics": BASICS, "answers": answers}
        )
        assert saved.status_code == 200, saved.text
        _wait(client, h, pid, "UPDATE")
        assert "종교 (물으면만)" in _memory_titles(pid)

        async def slow_update(db, job, progress):
            await asyncio.sleep(0.3)

        monkeypatch.setattr(persona_builder, "run_update", slow_update)
        withdrawn = client.post(
            f"{API}/personas/{pid}/consents",
            headers=h,
            json={"type": "sensitive_religion", "agreed": False},
        )
        assert withdrawn.status_code == 200, withdrawn.text
        assert withdrawn.json()["data"]["updating"] is True
        # 다시 익히기를 기다리지 않고 바로 지웠다(그동안 대화에 실리지 않게)
        assert "종교 (물으면만)" not in _memory_titles(pid)
        _wait(client, h, pid, "UPDATE")


def test_sensitive_memory_titles_match_the_built_memories():
    assert intake.sensitive_memory_titles("religion") == [
        "종교 (물으면만)",
        "기도하거나 의지하던 습관 (물으면만)",
        "신앙이나 죽음에 대한 생각 (물으면만)",
    ]
    assert intake.sensitive_memory_titles("health") == []  # 건강은 기억이 아니라 지침에만 간다
    rows = intake.build_intake_memories(BASICS, {"M1-09": "불교"})
    titles = [intake.memory_title(r["tags"][0], r["usageScope"]) for r in rows]
    assert "종교 (물으면만)" in titles


# ────────────────────────── 목소리를 만들지 못한 까닭 (be-flow-7, be-flow-4) ──────────────────


def _build(client: TestClient, h: dict, pid: str) -> None:
    declare_bereaved(client, pid)  # 돌아가신 분은 만들기 전에 유족 명단(2026-10-22 법률 자문)
    built = client.post(
        f"{API}/personas/{pid}/build",
        headers=h,
        json={"answers": ANSWERS, "cloneVoice": True, "voiceConsent": True},
    )
    assert built.status_code == 200, built.text
    _wait(client, h, pid, "BUILD")


@needs_ffmpeg
def test_failed_clone_during_build_is_shown_and_lost_clones_are_reported(ai, monkeypatch):
    alerts: list[str] = []
    monkeypatch.setattr(persona_builder.notify, "error", lambda kind, *a, **k: alerts.append(kind))

    def lost(request):
        raise httpx.RemoteProtocolError("AI restarted", request=request)

    ai["/v1/voice/clone"] = lost
    with TestClient(app) as client:
        h = _signup(client, f"clone-{uuid4().hex[:6]}@example.com")
        pid = _to_speakers(client, h)
        client.post(f"{API}/personas/{pid}/speakers", headers=h, json={"voiceKeys": ["VOICE_1"]})
        _wait(client, h, pid, "ANALYZE")
        _build(client, h, pid)
        detail = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]
    assert detail["step"] == "READY" and not detail["hasVoice"]
    assert detail["voiceIssue"] == ai_module.AI_UNREACHABLE
    # 목소리 회사가 목소리를 이미 만들었을 수 있어 관리자에게 알린다
    assert any(kind.startswith("VOICE_CLONE_INTERRUPTED") for kind in alerts)


@needs_ffmpeg
def test_unclear_samples_get_a_clear_reason_when_remaking(ai):
    with TestClient(app) as client:
        h = _signup(client, f"unclear-{uuid4().hex[:6]}@example.com")
        pid = _ready_persona(client, h)
        assert client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["voiceIssue"] is None
        ai["/v1/voice/clone"] = _error(422, "INVALID_VOICE_SAMPLE")
        assert client.post(f"{API}/personas/{pid}/voice", headers=h).status_code == 200
        job = _final_job(client, h, pid, "VOICE")
    assert job["status"] == "FAILED" and job["error"] == persona_builder.UNCLEAR_SAMPLES


# ────────────────────────── 작업 걸기 횟수 (be-input-8) ──────────────────────────


@needs_ffmpeg
def test_job_starts_are_rate_limited(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    with TestClient(app) as client:
        h = _signup(client, f"jobs-{uuid4().hex[:6]}@example.com")
        pid = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]
        client.post(
            f"{API}/personas/{pid}/recordings",
            headers=h,
            data={"pledge": "true"},
            files=[("files", ("통화.wav", _wav(), "audio/wav"))],
        )
        monkeypatch.setattr(ratelimit.JOB_STARTS, "limit", 0)
        assert client.post(f"{API}/personas/{pid}/transcribe", headers=h).status_code == 429
        assert client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["jobs"] == {}
        # 관리자는 세지 않는다
        admin = _admin(client)
        mine = client.post(f"{API}/personas", headers=admin, json=BASICS).json()["data"]["id"]
        client.post(
            f"{API}/personas/{mine}/recordings",
            headers=admin,
            data={"pledge": "true"},
            files=[("files", ("통화.wav", _wav(), "audio/wav"))],
        )
        assert client.post(f"{API}/personas/{mine}/transcribe", headers=admin).status_code == 200
        _wait(client, admin, mine, "TRANSCRIBE")


# ────────────────────────── 기억 API (be-input-5, be-chat-10, contracts-6) ──────────────────


def test_memories_api_is_admin_only_and_bounded():
    with TestClient(app) as client:
        user = _signup(client, f"mem-{uuid4().hex[:6]}@example.com")
        assert client.get(f"{API}/memories", headers=user).status_code == 403
        admin = _admin(client)
        person_id = client.get(f"{API}/personas/active", headers=admin).json()["data"]["personId"]
        base = {"personId": person_id, "title": "기억", "bodyMarkdown": "함께 나눈 기억"}
        too_long = client.post(
            f"{API}/memories", headers=admin, json=base | {"bodyMarkdown": "가" * 20_001}
        )
        assert too_long.status_code == 422
        bad_type = client.post(
            f"{API}/memories", headers=admin, json=base | {"memoryType": "x" * 31}
        )
        assert bad_type.status_code == 422
        made = client.post(f"{API}/memories", headers=admin, json=base)
        assert made.status_code == 201, made.text
        memory_id = made.json()["data"]["id"]

        async def embed():
            async with SessionLocal() as db:
                await db.execute(
                    update(Memory).where(Memory.id == memory_id).values(embedding=[1.0, 0.0])
                )
                await db.commit()

        _run(embed())
        empty = client.patch(
            f"{API}/memories/{memory_id}", headers=admin, json={"bodyMarkdown": ""}
        )
        assert empty.status_code == 422
        edited = client.patch(
            f"{API}/memories/{memory_id}", headers=admin, json={"bodyMarkdown": "고친 기억"}
        )
        assert edited.status_code == 200, edited.text

        async def vector():
            async with SessionLocal() as db:
                return await db.scalar(select(Memory.embedding).where(Memory.id == memory_id))

        assert _run(vector()) is None  # 예전 글의 벡터로 찾히지 않는다


def test_memory_scoring_matches_plain_cosine():
    class M:
        def __init__(self, embedding):
            self.embedding = embedding

    query = [0.3, 0.1, 0.9]
    rows = [M([0.3, 0.1, 0.8]), M([1.0, 0.0, 0.0]), M([0.0, 0.0, 0.0]), M([0.1, 0.2]), M(None)]
    expected = sorted(
        (m for m in rows if m.embedding and memory_search.cosine(query, m.embedding) >= 0.18),
        key=lambda m: -memory_search.cosine(query, m.embedding),
    )
    assert memory_search.best(query, rows) == expected[: memory_search.TOP_K]
    assert memory_search.best([0.0, 0.0, 0.0], rows) == []


# ────────────────────────── 로그인 (be-authz-3, be-authz-4, be-authz-7) ──────────────────────


def _tokens(client: TestClient, email: str, password: str = "password123") -> dict:
    response = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_refresh_keeps_the_first_login_time_and_stops_after_max_age():
    with TestClient(app) as client:
        email = f"session-{uuid4().hex[:6]}@example.com"
        _signup(client, email)
        first = _tokens(client, email)
        claims = decode_token(first["refreshToken"], "refresh")
        renewed = client.post(f"{API}/auth/refresh", json={"refreshToken": first["refreshToken"]})
        assert renewed.status_code == 200, renewed.text
        again = decode_token(renewed.json()["data"]["refreshToken"], "refresh")
        assert again["auth_time"] == claims["auth_time"]  # 새로 받아도 처음 로그인한 때 그대로

        days = get_settings().max_session_days
        old = create_refresh_token(
            claims["sub"], claims["ver"], auth_time=time.time() - (days + 1) * 86400
        )
        expired = client.post(f"{API}/auth/refresh", json={"refreshToken": old})
        assert expired.status_code == 401
        # 이 기능 전에 받은 토큰(auth_time 없음)은 받은 때부터 센다
        legacy = create_token(claims["sub"], "refresh", claims["ver"], timedelta(days=14))
        assert client.post(f"{API}/auth/refresh", json={"refreshToken": legacy}).status_code == 200


def test_password_change_signs_out_other_devices():
    with TestClient(app) as client:
        email = f"pw-{uuid4().hex[:6]}@example.com"
        _signup(client, email)
        device_a = _tokens(client, email)
        device_b = _tokens(client, email)
        h = {"Authorization": f"Bearer {device_a['bearerToken']}"}
        url = f"{API}/auth/password"
        wrong = client.post(
            url, headers=h, json={"currentPassword": "nope-nope", "newPassword": "newpass123"}
        )
        assert wrong.status_code == 400
        short = client.post(
            url, headers=h, json={"currentPassword": "password123", "newPassword": "short"}
        )
        assert short.status_code == 422
        changed = client.post(
            url, headers=h, json={"currentPassword": "password123", "newPassword": "newpass123"}
        )
        assert changed.status_code == 200, changed.text
        fresh = changed.json()["data"]
        stale = client.post(f"{API}/auth/refresh", json={"refreshToken": device_b["refreshToken"]})
        assert stale.status_code == 401
        me = client.get(
            f"{API}/auth/me", headers={"Authorization": f"Bearer {fresh['bearerToken']}"}
        )
        assert me.status_code == 200
        assert client.post(
            f"{API}/auth/login", json={"email": email, "password": "password123"}
        ).status_code == 401
        _tokens(client, email, "newpass123")


# ────────────────────────── 저장소·로그·DB 풀 (INFRA-4, INFRA-8, be-chat-07) ──────────────────


def test_delete_prefix_refuses_bucket_wide_prefixes(tmp_path):
    for bad in ("", "/", "personas", "personas/", "conversations//x", "other/x", "../x"):
        with pytest.raises(ValueError):
            check_prefix(bad)
    assert check_prefix("personas/p1/recordings/r.m4a") == "personas/p1/recordings/r.m4a"
    storage = LocalStorage(get_settings().model_copy(update={"local_media_dir": tmp_path}))
    with pytest.raises(ValueError):
        storage.delete_prefix("")
    source = tmp_path / "in.bin"
    source.write_bytes(b"abc" * 1000)
    with source.open("rb") as handle:
        storage.put_file("personas/p1/a.bin", handle, "application/octet-stream")
    storage.upload("personas/p1/b.bin", source)
    out = tmp_path / "out.bin"
    storage.download("personas/p1/a.bin", out)
    assert out.read_bytes() == source.read_bytes() == storage.get("personas/p1/b.bin")
    storage.delete_prefix("personas/p1")
    assert not storage.exists("personas/p1/a.bin")


def test_access_log_drops_query_strings():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:1", "GET", "/api/v1/auth/oauth/kakao/callback?code=SECRET&state=S", "1.1", 302),
        None,
    )
    assert main_module.AccessLogPrivacy().filter(record)
    assert "SECRET" not in record.getMessage()
    assert "/api/v1/auth/oauth/kakao/callback" in record.getMessage()
    access = logging.getLogger("uvicorn.access")
    assert any(isinstance(f, main_module.AccessLogPrivacy) for f in access.filters)


def test_database_pool_is_sized_for_postgres_only():
    assert session_module.pool_options("sqlite+aiosqlite:///x.db") == {}
    sized = session_module.pool_options("postgresql+asyncpg://u:p@h/db")
    assert sized["pool_size"] >= 10 and sized["max_overflow"] >= 10
