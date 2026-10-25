"""예전 목소리를 Fish drama-3 로 다시 만들기(voice_retrain)와 Fish 로 보내기 전 동의 확인.

- 동의(overseas_fish)를 받으면 그 사람의 예전 목소리를 하나씩 다시 만든다(VOICE 작업, reason=fish).
- 다 만들면 한 번에 바꾸고 예전 대답 음성을 비운 뒤, 예전 목소리를 그 회사에서 지운다.
- 동의가 없으면(관리자는 빼고) 녹음을 Fish 로 보내지 않는다. 3번 실패하면 더 걸지 않는다.
"""

import asyncio
import shutil
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from legal_gates import pass_subject_consent
from sqlalchemy import delete, select, update
from test_billing import _admin, _ready_persona
from test_persona_setup import BASICS, FakeAI, _signup, fake_ai  # noqa: F401

from app.core.config import get_settings
from app.db.models import (
    AnalyticsEvent,
    BereavedDeclaration,
    Consent,
    ConsentRecord,
    Message,
    Persona,
    PersonaJob,
    PremiumVoice,
    User,
)
from app.db.session import SessionLocal
from app.main import app
from app.services import ai as ai_module
from app.services import gate, jobs, legal, notify, persona_builder, voice_retrain
from app.services.storage import get_storage

API = "/api/v1"

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 필요")


@pytest.fixture(autouse=True)
def _retrain_on(monkeypatch: pytest.MonkeyPatch):
    """다시 만들기는 기본으로 꺼져 있다(먼저 들어 보고 켠다). 이 파일의 시험은 켜고 돈다."""
    monkeypatch.setattr(get_settings(), "voice_retrain_enabled", True)


def _run(coro):
    return asyncio.run(coro)


def _set_voice(pid: str, voice_id: str, provider: str | None) -> None:
    """예전 회사로 만든 목소리인 것처럼 바꾼다."""

    async def go():
        async with SessionLocal() as db:
            await db.execute(
                update(Persona)
                .where(Persona.id == pid)
                .values(voice_id=voice_id, voice_provider=provider)
            )
            await db.commit()

    _run(go())


def _persona(pid: str) -> Persona:
    async def go():
        async with SessionLocal() as db:
            return await db.get(Persona, pid)

    return _run(go())


def _owner_id(email: str) -> str:
    async def go():
        async with SessionLocal() as db:
            return await db.scalar(select(User.id).where(User.email == email))

    return _run(go())


def _revoke_fish(email: str) -> None:
    async def go():
        async with SessionLocal() as db:
            user = await db.scalar(select(User).where(User.email == email))
            await db.execute(
                update(Consent)
                .where(Consent.user_id == user.id, Consent.consent_type == "overseas_fish")
                .values(agreed=False)
            )
            await db.commit()

    _run(go())


def _sweep(owner_id: str | None = None) -> str | None:
    """다시 만들기를 하나 걸고, 건 작업이 끝날 때까지 기다린다."""

    async def go():
        async with SessionLocal() as db:
            started = await voice_retrain.sweep(db, owner_id=owner_id)
        await jobs.wait_all()
        return started

    return _run(go())


def _voice_jobs(pid: str) -> list[PersonaJob]:
    async def go():
        async with SessionLocal() as db:
            return (
                await db.scalars(
                    select(PersonaJob)
                    .where(PersonaJob.persona_id == pid, PersonaJob.kind == "VOICE")
                    .order_by(PersonaJob.created_at)
                )
            ).all()

    return _run(go())


def _wait_job(client: TestClient, h: dict, pid: str, kind: str) -> dict:
    for _ in range(200):
        job = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["jobs"].get(kind)
        if job and job["status"] != "RUNNING":
            return job
        time.sleep(0.1)
    raise AssertionError(f"{kind} did not finish")


def _speak(client: TestClient, h: dict, pid: str) -> str:
    """대답 하나를 받아 목소리로 들어 둔다(대답 음성이 저장된다). 메시지 id."""
    cid = client.post(f"{API}/personas/{pid}/conversation", headers=h).json()["data"][
        "conversationId"
    ]
    sent = client.post(
        f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": "할머니"}
    )
    reply = sent.json()["data"]["assistantMessage"]["id"]
    assert client.post(f"{API}/messages/{reply}/speech", headers=h).status_code == 200
    return reply


def _admin_fish_records(user_id: str) -> int:
    async def go():
        async with SessionLocal() as db:
            rows = await db.scalars(
                select(ConsentRecord).where(
                    ConsentRecord.user_id == user_id,
                    ConsentRecord.consent_type == "overseas_fish",
                    ConsentRecord.channel == "admin",
                )
            )
            return len(rows.all())

    return _run(go())


def _audio_url(message_id: str) -> str | None:
    async def go():
        async with SessionLocal() as db:
            return await db.scalar(select(Message.audio_url).where(Message.id == message_id))

    return _run(go())


@needs_ffmpeg
def test_accepting_fish_consent_retrains_old_voice(fake_ai: FakeAI):  # noqa: F811
    email = "retrain-consent@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _ready_persona(client, h)
        reply = _speak(client, h, pid)
        old_audio = _audio_url(reply)
        assert old_audio and get_storage().exists(old_audio)
        # 예전에 MiniMax 로 만든 목소리였고, 새 동의(overseas_fish)는 아직 받지 않았다
        _set_voice(pid, "TalkTo_old", "minimax")
        _revoke_fish(email)
        assert _sweep(_owner_id(email)) is None
        clones = len(fake_ai.calls["/v1/voice/clone"])

        # 동의하자마자 그 사람의 예전 목소리를 다시 만든다
        accepted = client.post(
            f"{API}/consents/accept", headers=h, json={"types": ["overseas_fish"]}
        )
        assert accepted.status_code == 200, accepted.text
        job = _wait_job(client, h, pid, "VOICE")
        assert job["status"] == "DONE", job
        assert len(fake_ai.calls["/v1/voice/clone"]) == clones + 1
        assert fake_ai.calls["/v1/voice/clone"][-1]["provider"] == "fish"
        persona = _persona(pid)
        assert persona.voice_provider == "fish" and persona.voice_id == fake_ai.voice(clones + 1)
        # 예전 목소리는 바꾼 뒤에 그 회사(MiniMax)에서 지우고, 예전 대답 음성은 비운다
        old = {"voiceId": "TalkTo_old", "provider": "minimax"}
        assert fake_ai.calls["/v1/voice/delete"][-1] == old
        assert _audio_url(reply) is None
        # 예전 목소리로 만든 대답 음성 파일도 지운다(처리방침: 목소리를 다시 만들 때까지)
        assert not get_storage().exists(old_audio)
        # 다시 들으면 새 목소리로, 다른 키에 만든다
        assert client.post(f"{API}/messages/{reply}/speech", headers=h).status_code == 200
        new_audio = _audio_url(reply)
        assert new_audio != old_audio and get_storage().exists(new_audio)
        [retrain] = _voice_jobs(pid)
        # 끊겨도 정리할 수 있게 작업에 남긴 표시(끝나면 done)
        assert retrain.options["reason"] == "fish" and retrain.options["clone"] == "done"
        assert retrain.options["oldVoice"] == ["TalkTo_old", "minimax"]
        assert retrain.options["newVoice"] == [fake_ai.voice(clones + 1), "fish"]

        async def event():
            async with SessionLocal() as db:
                return await db.scalar(
                    select(AnalyticsEvent)
                    .where(AnalyticsEvent.persona_id == pid, AnalyticsEvent.name == "voice_created")
                    .order_by(AnalyticsEvent.created_at.desc())
                )

        props = _run(event()).props
        assert props["provider"] == "fish" and props["reason"] == "fish" and props["remake"]
        assert props["phone"] in (True, False)
        # 이미 Fish 목소리면 다시 만들지 않는다
        assert _sweep(_owner_id(email)) is None


@needs_ffmpeg
def test_old_precise_voice_and_its_first_voice_are_deleted_at_elevenlabs(fake_ai: FakeAI):  # noqa: F811
    email = "retrain-pvc@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _ready_persona(client, h)
        _set_voice(pid, "pvc-1", "elevenlabs_pvc")

        async def add_row():
            async with SessionLocal() as db:
                db.add(
                    PremiumVoice(
                        persona_id=pid,
                        status="READY",
                        voice_id="pvc-1",
                        previous_voice_id="ivc-1",
                        previous_voice_provider="elevenlabs",
                    )
                )
                await db.commit()

        _run(add_row())
        assert _sweep(_owner_id(email)) == pid
        deleted = fake_ai.calls["/v1/voice/delete"]
        # 똑 닮은 목소리(elevenlabs_pvc)는 ElevenLabs 로 지운다(AI 서버가 모르는 이름은 보내지 않게)
        assert {"voiceId": "pvc-1", "provider": "elevenlabs"} in deleted
        assert {"voiceId": "ivc-1", "provider": "elevenlabs"} in deleted
        assert sum(1 for d in deleted if d["voiceId"] == "pvc-1") == 1

        async def row():
            async with SessionLocal() as db:
                return await db.scalar(select(PremiumVoice).where(PremiumVoice.persona_id == pid))

        left = _run(row())
        assert left.status == "REMOVED" and left.voice_id is None
        assert _persona(pid).voice_provider == "fish"
        # 대화 상대를 지울 때는 지금 목소리만 지운다(정리한 기록은 다시 지우지 않는다)
        voice = _persona(pid).voice_id
        before = len(fake_ai.calls["/v1/voice/delete"])
        assert client.delete(f"{API}/personas/{pid}", headers=h).status_code == 200
        assert fake_ai.calls["/v1/voice/delete"][before:] == [
            {"voiceId": voice, "provider": "fish"}
        ]


@needs_ffmpeg
def test_who_gets_retrained(fake_ai: FakeAI):  # noqa: F811
    """동의가 없는 사람·실패가 쌓인 대화 상대는 건너뛴다. 관리자는 동의 화면 없이 간다."""
    email = "retrain-who@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _ready_persona(client, h)
        _set_voice(pid, "voice-old", "elevenlabs")
        owner = _owner_id(email)

        async def check() -> bool:
            async with SessionLocal() as db:
                persona = await db.get(Persona, pid)
                user = await db.get(User, owner)
                return await voice_retrain.eligible(db, persona, user)

        assert _run(check()) is True
        _revoke_fish(email)
        assert _run(check()) is False

        # 관리자 대화 상대는 overseas_fish 기록이 없어도 다시 만든다
        admin = _admin(client)
        admin_pid = _ready_persona(client, admin)
        _set_voice(admin_pid, "voice-admin-old", "minimax")
        admin_id = _owner_id(get_settings().bootstrap_admin_email.lower())
        assert _sweep(admin_id) == admin_pid
        assert _persona(admin_pid).voice_provider == "fish"
        # 동의 화면은 없어도 동의 없이 Fish 로 보낸 기록(channel=admin)은 문서 버전마다 한 번 남긴다
        assert _admin_fish_records(admin_id) == 1

        # 3번 실패하면 더 걸지 않는다(서버를 다시 켜며 끊긴 것은 세지 않는다)
        client.post(f"{API}/consents/accept", headers=h, json={"types": ["overseas_fish"]})
        assert _wait_job(client, h, pid, "VOICE")["status"] == "DONE"
        _set_voice(pid, "voice-old-2", "elevenlabs")

        async def failed(n: int, error: str):
            async with SessionLocal() as db:
                for _ in range(n):
                    db.add(
                        PersonaJob(
                            persona_id=pid,
                            kind="VOICE",
                            status="FAILED",
                            error=error,
                            options={"reason": "fish"},
                        )
                    )
                await db.commit()

        _run(failed(5, jobs.INTERRUPTED))
        # Fish 크레딧·키·장애로 실패한 것도 세지 않는다(충전한 뒤 다시 걸어야 한다)
        _run(failed(5, ai_module.VOICE_DOWN))
        _run(failed(2, "잠시 문제가 생겼어요."))
        assert _run(check()) is True
        _run(failed(1, "잠시 문제가 생겼어요."))
        assert _run(check()) is False
        assert _sweep(owner) is None


@needs_ffmpeg
def test_only_one_retrain_runs_at_a_time(fake_ai: FakeAI):  # noqa: F811
    email = "retrain-one@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _ready_persona(client, h)
        _set_voice(pid, "voice-old", "elevenlabs")

        async def running():
            async with SessionLocal() as db:
                other = await db.scalar(select(Persona.id).where(Persona.id != pid).limit(1))
                db.add(
                    PersonaJob(
                        persona_id=other, kind="VOICE", status="RUNNING", options={"reason": "fish"}
                    )
                )
                await db.commit()
                return other

        other = _run(running())
        assert _sweep(_owner_id(email)) is None

        async def finish():
            async with SessionLocal() as db:
                await db.execute(
                    update(PersonaJob)
                    .where(PersonaJob.persona_id == other, PersonaJob.status == "RUNNING")
                    .values(status="DONE")
                )
                await db.commit()

        _run(finish())
        assert _sweep(_owner_id(email)) == pid


@needs_ffmpeg
def test_no_recordings_reach_fish_without_consent(fake_ai: FakeAI):  # noqa: F811
    """배포 전에 건 작업·다시 시도처럼 동의 게이트를 지나지 않은 일도 Fish 로 보내지 않는다."""
    email = "fish-guard@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _ready_persona(client, h)
        _revoke_fish(email)
        clones = len(fake_ai.calls["/v1/voice/clone"])

        async def go(kind, fn, options=None):
            async with SessionLocal() as db:
                await jobs.start(db, pid, kind, fn, options)
            await jobs.wait_all()

        _run(go("VOICE", persona_builder.run_voice))
        job = _voice_jobs(pid)[-1]
        assert job.status == "FAILED" and job.error == persona_builder.FISH_CONSENT_NEEDED
        _run(go("BUILD", persona_builder.run_build, {"cloneVoice": True}))
        assert len(fake_ai.calls["/v1/voice/clone"]) == clones  # 녹음을 보내지 않았다
        persona = _persona(pid)
        assert persona.voice_id == fake_ai.voice(1) and persona.setup_step == "READY"


@needs_ffmpeg
def test_new_voice_is_deleted_when_the_switch_fails(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    email = "switch-fails@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _ready_persona(client, h)

        async def broken(db, persona_id):
            raise RuntimeError("db went away")

        monkeypatch.setattr(persona_builder, "_clear_reply_audio", broken)
        assert client.post(f"{API}/personas/{pid}/voice", headers=h).status_code == 200
        job = _wait_job(client, h, pid, "VOICE")
        assert job["status"] == "FAILED"
        # 바꾸지 못한 새 목소리(두 번째)는 Fish 에서 지우고, 쓰던 목소리는 그대로
        assert fake_ai.calls["/v1/voice/delete"][-1] == {
            "voiceId": fake_ai.voice(2),
            "provider": "fish",
        }
        persona = _persona(pid)
        assert (persona.voice_id, persona.voice_provider) == (fake_ai.voice(1), "fish")


@needs_ffmpeg
def test_speech_made_with_an_old_voice_is_not_cached(fake_ai: FakeAI):  # noqa: F811
    """대답 음성을 만드는 사이 목소리를 다시 만들었으면, 예전 목소리 소리는 저장하지 않는다."""
    email = "speech-race@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _ready_persona(client, h)
        cid = client.post(f"{API}/personas/{pid}/conversation", headers=h).json()["data"][
            "conversationId"
        ]
        sent = client.post(
            f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": "할머니"}
        )
        reply = sent.json()["data"]["assistantMessage"]["id"]

        async def slow_speech(*, text, voice_id, emotion=None):
            # 회사가 소리를 만드는 사이 다시 만들기가 끝났다
            async with SessionLocal() as db:
                await db.execute(
                    update(Persona).where(Persona.id == pid).values(voice_id="voice-new")
                )
                await db.commit()
            return b"ID3old"

        ai_module.ai_service.speech = slow_speech
        try:
            raced = client.post(f"{API}/messages/{reply}/speech", headers=h)
        finally:
            del ai_module.ai_service.speech  # 클래스의 진짜 speech 로 돌아간다
        assert raced.status_code == 409 and "다시 들어" in raced.json()["detail"]
        assert _audio_url(reply) is None
        again = client.post(f"{API}/messages/{reply}/speech", headers=h)
        assert again.status_code == 200
        assert fake_ai.calls["/v1/voice/speech"][-1]["voiceId"] == "voice-new"


@pytest.fixture
def voice_down(fake_ai: FakeAI, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    """목소리 만들기(/v1/voice/clone)만 Fish 크레딧 문제로 막는다. down 을 비우면 풀린다."""
    down = {"/v1/voice/clone"}
    real = httpx._client.AsyncClient
    credits = {"error": {"code": "VOICE_PROVIDER_CREDITS", "message": "voice provider unavailable"}}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in down:
            fake_ai.calls.setdefault(request.url.path, []).append({})
            return httpx.Response(503, json=credits)
        return fake_ai(request)

    monkeypatch.setattr(
        ai_module.httpx,
        "AsyncClient",
        lambda *a, **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(notify, "error", lambda kind, where: alerts.append((kind, where)))
    for state in (ai_module.provider_state, ai_module.voice_state):
        state.update(code=None, at=None, ok_at=None)
    yield down, alerts
    for state in (ai_module.provider_state, ai_module.voice_state):
        state.update(code=None, at=None, ok_at=None)


@needs_ffmpeg
def test_voice_provider_trouble_alerts_admins_with_voice_text(voice_down):
    down, alerts = voice_down
    email = "voice-down@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        down.clear()  # 만들 때는 정상
        pid = _ready_persona(client, h)
        down.add("/v1/voice/clone")

        assert client.post(f"{API}/personas/{pid}/voice", headers=h).status_code == 200
        job = _wait_job(client, h, pid, "VOICE")
        assert job["status"] == "FAILED" and job["error"] == ai_module.VOICE_DOWN
        # 목소리 회사 문제로 알린다(OpenAI 크레딧 알림이 아니다). 작업 실패는 두 번 알리지 않는다
        assert [kind for kind, _ in alerts] == ["VOICE_PROVIDER_CREDITS"]
        text = alerts[0][1]
        assert "Fish Audio" in text and "크레딧" in text and "OpenAI" not in text
        # 관리자 화면: AI(OpenAI) 경고는 그대로 두고 목소리 경고만 띄운다
        stats = client.get(f"{API}/admin/stats", headers=_admin(client)).json()["data"]
        assert stats["aiProviderTrouble"] is None
        assert stats["voiceProviderTrouble"]["code"] == "VOICE_PROVIDER_CREDITS"
        # 풀리기 전에는 다시 만들기를 새로 걸지 않는다(실패 횟수만 쌓이지 않게)
        _set_voice(pid, "voice-old", "elevenlabs")
        assert _sweep(_owner_id(email)) is None
        # 충전한 뒤 다시 만들면 경고가 사라진다
        down.clear()
        assert client.post(f"{API}/personas/{pid}/voice", headers=h).status_code == 200
        assert _wait_job(client, h, pid, "VOICE")["status"] == "DONE"
        stats = client.get(f"{API}/admin/stats", headers=_admin(client)).json()["data"]
        assert stats["voiceProviderTrouble"] is None


def test_voice_provider_alert_texts():
    seen: list[tuple[str, str]] = []
    real = notify.error
    notify.error = lambda kind, where: seen.append((kind, where))
    try:
        for code in sorted(ai_module.VOICE_PROVIDER_CODES):
            notify.voice_provider(code)
    finally:
        notify.error = real
    assert [kind for kind, _ in seen] == sorted(ai_module.VOICE_PROVIDER_CODES)
    assert all("Fish Audio" in text and "OpenAI" not in text for _, text in seen)
    assert "FISH_AUDIO_API_KEY" in dict(seen)["VOICE_PROVIDER_KEY"]


def test_old_precise_voice_documents_are_deleted_at_start(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        h = _signup(client, "pvc-docs@example.com")
        pid = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]
        storage = get_storage()
        key = f"personas/{pid}/pvc_docs/death_certificate_0_x.jpg"
        storage.put(key, b"\xff\xd8 fake", "image/jpeg")
        storage.put(f"personas/{pid}/recordings/keep.m4a", b"audio", "audio/mp4")

        async def add_row():
            async with SessionLocal() as db:
                db.add(PremiumVoice(persona_id=pid, status="VERIFYING", document_keys=[key]))
                await db.commit()

        _run(add_row())
        assert _run(voice_retrain.drop_pvc_documents()) == 1
        assert not storage.exists(key)
        assert storage.exists(f"personas/{pid}/recordings/keep.m4a")  # 녹음은 그대로
        assert _run(voice_retrain.drop_pvc_documents()) == 0  # 두 번 해도 할 일이 없다


def test_retrain_is_off_by_default():
    """다시 만들면 예전 목소리를 그 회사에서 지워 되돌릴 수 없다. 배포 뒤 먼저 들어 보고 켠다."""
    from app.core.config import Settings

    assert Settings.model_fields["voice_retrain_enabled"].default is False


@needs_ffmpeg
def test_old_voice_replies_do_not_clear_fish_trouble(voice_down):
    """Fish 가 막힌 동안 예전 목소리(MiniMax·ElevenLabs)로 읽기·지우기가 성공해도 풀린 것이 아니다.

    풀린 것으로 보면 다시 만들기가 쉬지 않고 Fish 에 다시 걸어 실패만 쌓인다.
    """
    down, _ = voice_down
    email = "legacy-ok@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        down.clear()
        pid = _ready_persona(client, h)
        down.add("/v1/voice/clone")
        assert client.post(f"{API}/personas/{pid}/voice", headers=h).status_code == 200
        assert _wait_job(client, h, pid, "VOICE")["status"] == "FAILED"
        assert ai_module.voice_provider_trouble()["code"] == "VOICE_PROVIDER_CREDITS"

        _set_voice(pid, "TalkTo_legacy", "minimax")
        _speak(client, h, pid)
        _run(ai_module.ai_service.delete_voice(voice_id="eleven-old", provider="elevenlabs"))
        assert ai_module.voice_provider_trouble()["code"] == "VOICE_PROVIDER_CREDITS"
        assert _sweep(_owner_id(email)) is None
        # Fish 목소리로 읽기가 되면 풀린다
        _set_voice(pid, "fish_abc", "fish")
        _speak(client, h, pid)
        assert ai_module.voice_provider_trouble() is None


@needs_ffmpeg
def test_retrain_waits_until_every_required_consent_is_in(fake_ai: FakeAI):  # noqa: F811
    """overseas_fish 만 받고 다른 필수 동의가 빠졌으면(앱도 못 쓴다) 녹음을 보내지 않는다."""
    email = "retrain-gate@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _ready_persona(client, h)
        _set_voice(pid, "voice-old", "elevenlabs")
        owner = _owner_id(email)
        other = next(t for t in legal.ACCOUNT_TYPES if t != "overseas_fish")

        async def revoke():
            async with SessionLocal() as db:
                await db.execute(
                    update(Consent)
                    .where(Consent.user_id == owner, Consent.consent_type == other)
                    .values(agreed=False)
                )
                await db.commit()

        _run(revoke())
        _revoke_fish(email)
        accepted = client.post(
            f"{API}/consents/accept", headers=h, json={"types": ["overseas_fish"]}
        )
        assert accepted.status_code == 200 and other in accepted.json()["data"]["consentsNeeded"]
        assert _voice_jobs(pid) == []
        assert _sweep(owner) is None
        # 마저 동의하면 다시 만든다
        client.post(f"{API}/consents/accept", headers=h, json={"types": [other]})
        assert _sweep(owner) == pid
        assert _persona(pid).voice_provider == "fish"


def _set_persona(pid: str, **values) -> None:
    async def go():
        async with SessionLocal() as db:
            await db.execute(update(Persona).where(Persona.id == pid).values(**values))
            await db.commit()

    _run(go())


@needs_ffmpeg
def test_processing_gate_decides_who_gets_retrained(fake_ai: FakeAI):  # noqa: F811
    """처리 게이트(2026-10-22 법률 자문)에 막힌 대화 상대는 다시 만들지 않는다: 멈춘 것, 예전
    '대화가 어려우신 분', 그분 동의 녹음이 확인되기 전의 살아 계신 분. 유족 명단을 아직 적지 않은
    이미 만든 고인 대화 상대는 막지 않는다(대표 답 2026-10-23 #3 — 카드만)."""
    email = "retrain-legal-gate@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _ready_persona(client, h)
        _set_voice(pid, "voice-old", "elevenlabs")
        owner = _owner_id(email)
        basics = _persona(pid).basics

        async def check() -> tuple[bool, str | None]:
            async with SessionLocal() as db:
                persona = await db.get(Persona, pid)
                user = await db.get(User, owner)
                found = await gate.block(db, persona, "voice")
                return await voice_retrain.eligible(db, persona, user), found and found.code

        assert _run(check()) == (True, None)

        async def undeclare():
            async with SessionLocal() as db:
                await db.execute(
                    delete(BereavedDeclaration).where(BereavedDeclaration.persona_id == pid)
                )
                await db.commit()

        _run(undeclare())
        assert _run(check()) == (True, None)

        now = datetime.now(UTC)
        later = now + timedelta(days=7)
        _set_persona(pid, hold_reason="objection", hold_at=now, delete_after=later)
        assert _run(check()) == (False, "stopped")
        assert _sweep(owner) is None
        _set_persona(pid, hold_reason=None, hold_at=None, delete_after=None)

        _set_persona(pid, basics=basics | {"status": "unable"})
        assert _run(check()) == (False, "unable")

        # 살아 계신 분: 목소리 만들기 확인(pledge_living)이 있어도 그분 동의 녹음 전에는 안 보낸다
        _set_persona(pid, basics=basics | {"status": "alive", "minor": False})

        async def living_pledge():
            async with SessionLocal() as db:
                user = await db.get(User, owner)
                await legal.record(
                    db,
                    user,
                    legal.persona_type("voice_clone", pid),
                    True,
                    persona_id=pid,
                    doc="pledge_living",
                )
                await db.commit()

        _run(living_pledge())
        assert _run(check()) == (False, "subject_consent")
        assert _sweep(owner) is None
        pass_subject_consent(None, pid)
        assert _run(check()) == (True, None)
        assert _sweep(owner) == pid


@needs_ffmpeg
def test_hand_set_voices_are_left_alone(fake_ai: FakeAI):  # noqa: F811
    """관리자가 손으로 넣은 목소리는 회사를 비워 다시 만들거나 그 회사에서 지우지 않는다.
    다른 대화 상대가 같이 쓰는 목소리도 지우지 않는다."""
    email = "hand-set@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _ready_persona(client, h)
        _set_voice(pid, "voice-old", "elevenlabs")
        patched = client.patch(
            f"{API}/admin/personas/{pid}", headers=_admin(client), json={"voiceId": "shared-1"}
        )
        assert patched.status_code == 200, patched.text
        assert _persona(pid).voice_provider is None
        assert _sweep(_owner_id(email)) is None
        # fish_ 목소리인데 예전 회사 이름이 남아 있어도 다시 만들지 않는다
        _set_voice(pid, "fish_hand", "elevenlabs")
        assert _sweep(_owner_id(email)) is None

        # 같은 목소리를 두 대화 상대가 쓰면, 하나를 지워도 회사의 목소리는 남긴다
        other = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]
        _set_voice(pid, "shared-2", "elevenlabs")
        _set_voice(other, "shared-2", "elevenlabs")
        before = len(fake_ai.calls.get("/v1/voice/delete", []))
        assert client.delete(f"{API}/personas/{other}", headers=h).status_code == 200
        assert fake_ai.calls.get("/v1/voice/delete", [])[before:] == []
        assert client.delete(f"{API}/personas/{pid}", headers=h).status_code == 200
        assert fake_ai.calls["/v1/voice/delete"][before:] == [
            {"voiceId": "shared-2", "provider": "elevenlabs"}
        ]


@needs_ffmpeg
def test_withdrawing_voice_consent_removes_the_voice(fake_ai: FakeAI):  # noqa: F811
    email = "voice-withdraw@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _ready_persona(client, h)
        voice = _persona(pid).voice_id
        reply = _speak(client, h, pid)
        key = _audio_url(reply)
        withdrawn = client.post(
            f"{API}/consents",
            headers=h,
            json={"consentType": f"voice_clone:{pid}", "agreed": False},
        )
        assert withdrawn.status_code == 200, withdrawn.text
        persona = _persona(pid)
        assert persona.voice_id is None and persona.voice_provider is None
        assert fake_ai.calls["/v1/voice/delete"][-1] == {"voiceId": voice, "provider": "fish"}
        assert _audio_url(reply) is None and not get_storage().exists(key)
        speech = client.post(f"{API}/messages/{reply}/speech", headers=h)
        assert speech.status_code == 409
        # 글 대화는 그대로
        cid = client.post(f"{API}/personas/{pid}/conversation", headers=h).json()["data"][
            "conversationId"
        ]
        sent = client.post(
            f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": "할머니"}
        )
        assert sent.status_code == 200


def test_background_retrain_does_not_block_deleting(fake_ai: FakeAI):  # noqa: F811
    """동의를 거두는 길이 탈퇴라서, 뒤에서 도는 다시 만들기가 지우기를 막으면 안 된다."""
    with TestClient(app) as client:
        h = _signup(client, "retrain-delete@example.com")
        pid = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]
        other = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]

        async def running():
            async with SessionLocal() as db:
                db.add(
                    PersonaJob(
                        persona_id=pid, kind="VOICE", status="RUNNING", options={"reason": "fish"}
                    )
                )
                db.add(PersonaJob(persona_id=other, kind="VOICE", status="RUNNING", options={}))
                await db.commit()

        _run(running())
        # 고객이 건 목소리 다시 만들기는 끝난 뒤에 지운다
        assert client.delete(f"{API}/personas/{other}", headers=h).status_code == 409
        assert client.delete(f"{API}/personas/{pid}", headers=h).status_code == 200

        async def finish():
            async with SessionLocal() as db:
                await db.execute(
                    update(PersonaJob)
                    .where(PersonaJob.persona_id == other, PersonaJob.status == "RUNNING")
                    .values(status="DONE")
                )
                await db.commit()

        _run(finish())


def test_interrupted_clones_are_cleaned_at_start(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    """서버를 다시 켜며 끊긴 목소리 만들기가 회사에 남긴 목소리를 지운다(작업에 남긴 표시로)."""
    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(notify, "error", lambda kind, where: alerts.append((kind, where)))
    with TestClient(app) as client:
        h = _signup(client, "clone-cut@example.com")
        pid = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]
        _set_voice(pid, "fish_cut_keep", "fish")
        cases = [
            # 새 목소리를 받고 바꾸기 전에 끊김 → 새 목소리를 지운다
            {"oldVoice": ["fish_cut_keep", "fish"], "newVoice": ["fish_cut_new", "fish"]},
            # 바꾼 뒤(예전 목소리를 지우기 전) 끊김 → 예전 목소리를 지운다
            {"oldVoice": ["TalkTo_cut_old", "minimax"], "newVoice": ["fish_cut_keep", "fish"]},
            # 회사가 답하기 전에 끊김 → 목소리 id 를 몰라 관리자에게 알린다
            {"oldVoice": ["fish_cut_keep", "fish"]},
        ]

        async def add():
            async with SessionLocal() as db:
                for marks in cases:
                    db.add(
                        PersonaJob(
                            persona_id=pid,
                            kind="VOICE",
                            status="FAILED",
                            error=jobs.INTERRUPTED,
                            options={"clone": "started", **marks},
                        )
                    )
                # 끝난 것은 보지 않는다
                db.add(
                    PersonaJob(
                        persona_id=pid,
                        kind="VOICE",
                        status="FAILED",
                        error=jobs.INTERRUPTED,
                        options={"clone": "done", "newVoice": ["fish_done", "fish"]},
                    )
                )
                await db.commit()

        _run(add())
        before = len(fake_ai.calls.get("/v1/voice/delete", []))
        assert _run(persona_builder.clean_interrupted_clones()) >= 3
        deleted = fake_ai.calls["/v1/voice/delete"][before:]
        assert sorted(d["voiceId"] for d in deleted) == ["TalkTo_cut_old", "fish_cut_new"]
        assert [kind for kind, _ in alerts] == [f"VOICE_CLONE_INTERRUPTED {pid[:8]}"]
        assert f"TalkTo_{pid[:8]}" in alerts[0][1]
        assert {j.options["clone"] for j in _voice_jobs(pid)} == {"cleaned", "done"}
        assert _persona(pid).voice_id == "fish_cut_keep"
        assert _run(persona_builder.clean_interrupted_clones()) == 0


def test_unfinished_precise_voices_are_deleted_at_elevenlabs(fake_ai: FakeAI):  # noqa: F811
    """만들다 만 똑 닮은 목소리(녹음 30분~2시간)는 ElevenLabs 에서 지운다.

    대화 상대가 지금 쓰는 것은 Fish 로 다시 만들 때 지운다.
    """
    with TestClient(app) as client:
        h = _signup(client, "pvc-voices@example.com")
        pid = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]
        other = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]
        _set_voice(pid, "ivc-now", "elevenlabs")
        _set_voice(other, "pvc-ready", "elevenlabs_pvc")

        async def add_rows():
            async with SessionLocal() as db:
                db.add(
                    PremiumVoice(
                        persona_id=pid,
                        status="TRAINING",
                        voice_id="pvc-half",
                        previous_voice_id="ivc-now",
                        previous_voice_provider="elevenlabs",
                    )
                )
                db.add(PremiumVoice(persona_id=other, status="READY", voice_id="pvc-ready"))
                await db.commit()

        async def drop() -> int:
            async with SessionLocal() as db:
                return await voice_retrain.drop_pvc_voices(db)

        async def row(persona_id: str) -> PremiumVoice:
            async with SessionLocal() as db:
                return await db.scalar(
                    select(PremiumVoice).where(PremiumVoice.persona_id == persona_id)
                )

        _run(add_rows())
        before = len(fake_ai.calls.get("/v1/voice/delete", []))
        assert _run(drop()) >= 1
        deleted = fake_ai.calls["/v1/voice/delete"][before:]
        assert {"voiceId": "pvc-half", "provider": "elevenlabs"} in deleted
        assert all(d["voiceId"] not in ("pvc-ready", "ivc-now") for d in deleted)
        half = _run(row(pid))
        assert half.voice_id is None and half.previous_voice_id == "ivc-now"
        assert _run(row(other)).voice_id == "pvc-ready"
        assert _run(drop()) == 0
