"""처리 게이트(app/services/gate.py · SPEC §11 · 2026-10-22 법률 자문).

- 살아 계신 분(나 자신 아님)은 그분 동의 녹음이 통과해야 녹음 처리·목소리·만들기가 열린다(다 만든
  것은 대화·답 고치기는 그대로). 작업 안에서도 다시 본다(배포 전에 건 작업·다시 시도).
- 멈춘 대화 상대(이의 접수 등)는 대화·대답 듣기·처리·다시 익히기가 모두 멈춘다(가족은 403).
- 이미 다 만든 고인 대화 상대는 유족 명단이 없어도 아무것도 막지 않고 카드만(대표 답 #3). 새로
  만드는 고인 대화 상대는 만들기 전에 유족 명단을 받는다.
- 그분 동의 녹음은 어떤 해외 회사로도 가지 않는다(ai.py 가 막는다).
"""

import asyncio
import inspect
import shutil
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from legal_gates import declare_bereaved, pass_subject_consent
from sqlalchemy import select
from test_persona_setup import ANSWERS, BASICS, FakeAI, _wav, fake_ai  # noqa: F401
from test_sharing import (  # noqa: F401
    OWNER_BASICS,
    _family,
    _persona,
    _room,
    _say,
    _set,
    _signup,
    settings,
)

from app.core import body_limit
from app.db.models import ConsentRecord, Memory, Persona, PersonaJob, SetupStep, User
from app.db.session import SessionLocal
from app.main import app
from app.services import ai as ai_module
from app.services import (
    gate,
    interview,
    jobs,
    legal,
    persona_builder,
    subject_consent,
    voice_retrain,
)

API = "/api/v1"
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 필요")

# 살아 계신 어머니(나 자신 아님, 만 19세 미만 아님)
LIVING = {
    "name": "박영자",
    "relation": "어머니",
    "addressTerm": "엄마",
    "status": "alive",
    "myName": "김민수",
    "myRelation": "아들",
    "calledMe": "민수야",
    "minor": False,
}


def _run(coro):
    return asyncio.run(coro)


def _wait_job(client: TestClient, h: dict, pid: str, kind: str, timeout: float = 20) -> dict:
    """작업이 끝날 때까지(DONE·FAILED 모두) 기다린다."""
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["jobs"].get(kind)
        if job and job["status"] != "RUNNING":
            return job
        time.sleep(0.1)
    raise AssertionError(f"{kind} did not finish")


def _create(client: TestClient, h: dict, basics: dict) -> str:
    created = client.post(f"{API}/personas", headers=h, json=basics)
    assert created.status_code == 201, created.text
    return created.json()["data"]["id"]


def _upload(client: TestClient, h: dict, pid: str, name: str = "통화01.wav"):
    return client.post(
        f"{API}/personas/{pid}/recordings",
        headers=h,
        data={"pledge": "true"},
        files=[("files", (name, _wav(), "audio/wav"))],
    )


def _detail(client: TestClient, h: dict, pid: str) -> dict:
    response = client.get(f"{API}/personas/{pid}", headers=h)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _hold(pid: str, reason: str = "objection", days: int = 7) -> None:
    now = datetime.now(UTC)
    _set(Persona, pid, hold_reason=reason, hold_at=now, delete_after=now + timedelta(days=days))


async def _block(pid: str, action: str, *, member: bool = False):
    async with SessionLocal() as db:
        persona = await db.get(Persona, pid)
        return await gate.block(db, persona, action, member=member)


# ────────────────────────── 살아 계신 분 ──────────────────────────


@needs_ffmpeg
def test_living_persona_waits_for_subject_consent(fake_ai: FakeAI):  # noqa: F811
    """녹음은 미리 올려 둘 수 있지만(서울 저장만) 받아쓰기·살펴보기·만들기는 그분 동의 녹음이
    통과한 뒤에만. 통과 전에는 AI 서버(해외 회사)를 한 번도 부르지 않는다."""
    with TestClient(app) as client:
        h, _, _ = _signup(client, "민수")
        pid = _create(client, h, LIVING)
        assert _upload(client, h, pid).status_code == 200
        blocked = client.post(f"{API}/personas/{pid}/transcribe", headers=h)
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "subject_consent"
        assert blocked.json()["detail"] == gate.SUBJECT
        assert not fake_ai.calls

        detail = _detail(client, h, pid)
        assert detail["gates"]["audio"] is False and detail["gates"]["build"] is False
        assert detail["gates"]["chat"] is True and detail["gates"]["code"] == "subject_consent"
        assert detail["subjectConsent"]["required"] is True
        assert detail["subjectConsent"]["status"] == "none"
        assert detail["subjectConsent"]["canStart"] is True
        card = detail["actions"][0]
        assert card["kind"] == "subject_consent" and card["blocking"] is False
        assert card["title"] == gate.CONSENT_TITLE_NEW and card["route"] == f"/p/{pid}/consent"
        listed = client.get(f"{API}/personas", headers=h).json()["data"][0]
        assert listed["actions"] == [{"kind": "subject_consent", "blocking": False}]
        assert listed["hold"] is None

        pass_subject_consent(client, pid)
        assert _detail(client, h, pid)["actions"] == []
        assert client.post(f"{API}/personas/{pid}/transcribe", headers=h).status_code == 200
        assert _wait_job(client, h, pid, "TRANSCRIBE")["status"] == "DONE"
        chosen = client.post(
            f"{API}/personas/{pid}/speakers", headers=h, json={"voiceKeys": ["VOICE_1"]}
        )
        assert chosen.status_code == 200, chosen.text
        assert _wait_job(client, h, pid, "ANALYZE")["status"] == "DONE"
        built = client.post(
            f"{API}/personas/{pid}/build",
            headers=h,
            json={"answers": ANSWERS, "cloneVoice": True, "voiceConsent": True},
        )
        assert built.status_code == 200, built.text
        assert _wait_job(client, h, pid, "BUILD")["status"] == "DONE"
        assert _detail(client, h, pid)["step"] == "READY"


def test_ready_living_persona_keeps_talking_but_cannot_reprocess(fake_ai: FakeAI):  # noqa: F811
    """이미 만든 살아 계신 분(배포 전): 대화·답 고치기는 그대로, 녹음 더하기·목소리 다시 만들기는
    그분 동의 녹음 전까지 멈춘다. 미성년 여부를 아직 답하지 않았으면 카드가 그것부터 묻는다."""
    old = {k: v for k, v in LIVING.items() if k != "minor"}
    with TestClient(app) as client:
        h, owner_id, _ = _signup(client, "민수")
        pid = _persona(owner_id, basics=old, voice_id="fish_old", voice_provider="fish")
        _set(Persona, pid, subject_voices=["VOICE_1"])
        cid = _room(client, h, pid)
        assert _say(client, h, cid).status_code == 200
        for path in ("voice", "recordings/apply"):
            blocked = client.post(f"{API}/personas/{pid}/{path}", headers=h)
            assert blocked.status_code == 409, path
            assert blocked.json()["code"] == "subject_consent"
        detail = _detail(client, h, pid)
        assert detail["gates"]["voice"] is False and detail["gates"]["chat"] is True
        assert detail["subjectConsent"]["blockCode"] == "minor_unknown"
        card = detail["actions"][0]
        assert card["kind"] == "minor_question" and card["title"] == gate.CONSENT_TITLE_READY
        assert card["body"] == gate.CONSENT_BODY_READY
        # 미성년 여부를 답한 뒤에는 동의 녹음 카드
        _set(Persona, pid, basics=LIVING)
        assert _detail(client, h, pid)["actions"][0]["kind"] == "subject_consent"
        # 답 고치기(글 — 다 만든 대화 상대는 그대로 된다)
        saved = client.put(
            f"{API}/personas/{pid}",
            headers=h,
            json={"basics": LIVING | {"calledMe": "우리 아들"}},
        )
        assert saved.status_code == 200, saved.text


def test_answers_autosave_before_consent(fake_ai: FakeAI):  # noqa: F811
    """만드는 중의 질문 답·기본 정보 저장은 그분 동의 녹음 전에도 된다(해외 회사를 안 부른다)."""
    with TestClient(app) as client:
        h, _, _ = _signup(client, "민수")
        pid = _create(client, h, LIVING)
        _set(Persona, pid, setup_step=SetupStep.QUESTIONS, subject_voices=["VOICE_1"])
        saved = client.put(
            f"{API}/personas/{pid}", headers=h, json={"basics": LIVING, "answers": ANSWERS}
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["data"]["updating"] is False
        draft = client.put(f"{API}/personas/{pid}/answers", headers=h, json={"C-01": "짧게"})
        assert draft.status_code == 200
        assert not fake_ai.calls


def test_speakers_and_build_wait_for_subject_consent(fake_ai: FakeAI):  # noqa: F811
    """받아쓰기를 마친 뒤(배포 전에 받아쓴 것 등)라도 목소리 고르기(살펴보기)와 만들기는 그분 동의
    녹음이 확인되기 전에는 409 subject_consent(만들기는 이어서 만들 수 있다는 글)."""
    with TestClient(app) as client:
        h, _, _ = _signup(client, "민수")
        pid = _create(client, h, LIVING)
        _set(Persona, pid, setup_step=SetupStep.SPEAKERS)
        chosen = client.post(
            f"{API}/personas/{pid}/speakers", headers=h, json={"voiceKeys": ["VOICE_1"]}
        )
        assert chosen.status_code == 409 and chosen.json()["code"] == "subject_consent"
        _set(Persona, pid, setup_step=SetupStep.QUESTIONS, subject_voices=["VOICE_1"])
        built = client.post(
            f"{API}/personas/{pid}/build",
            headers=h,
            json={"answers": ANSWERS, "cloneVoice": True, "voiceConsent": True},
        )
        assert built.status_code == 409 and built.json()["code"] == "subject_consent"
        assert built.json()["detail"] == gate.SUBJECT_BUILD
        assert not fake_ai.calls


async def _start_job(pid: str, kind: str, fn, options: dict | None = None) -> None:
    async with SessionLocal() as db:
        await jobs.start(db, pid, kind, fn, options)
    await jobs.wait_all()


async def _job(pid: str, kind: str) -> PersonaJob:
    async with SessionLocal() as db:
        return await jobs.latest(db, pid, kind)


def test_jobs_check_the_gate_themselves(fake_ai: FakeAI):  # noqa: F811
    """배포 전에 건 작업·다시 시도처럼 화면을 거치지 않은 작업도 처음에 게이트를 본다. 막히면
    한국어 글로 실패하고 AI 서버(해외 회사)를 부르지 않는다."""
    with TestClient(app) as client:
        h, _, _ = _signup(client, "민수")
        pid = _create(client, h, LIVING)
        _upload(client, h, pid)
        _set(Persona, pid, setup_step=SetupStep.BUILDING, subject_voices=["VOICE_1"])
        _run(_start_job(pid, "BUILD", persona_builder.run_build, {"cloneVoice": True}))
        build = _run(_job(pid, "BUILD"))
        assert build.status == "FAILED" and build.error == gate.SUBJECT_BUILD
        _run(_start_job(pid, "TRANSCRIBE", persona_builder.run_transcribe))
        assert _run(_job(pid, "TRANSCRIBE")).error == gate.SUBJECT
        _set(Persona, pid, setup_step=SetupStep.READY)
        _run(_start_job(pid, "VOICE", persona_builder.run_voice))
        assert _run(_job(pid, "VOICE")).error == gate.SUBJECT
        assert not fake_ai.calls


def test_hold_during_build_stops_before_the_voice_vendor(
    fake_ai: FakeAI,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
):
    """만드는 중에 멈춤(이의 접수)이 걸리면 목소리를 만들기 전에 멈춘다(Fish 를 부르지 않는다)."""
    real = persona_builder._assemble

    async def assemble_then_hold(db, job, persona, *args, **kwargs):
        await real(db, job, persona, *args, **kwargs)
        async with SessionLocal() as other:
            row = await other.get(Persona, persona.id)
            now = datetime.now(UTC)
            row.hold_reason, row.hold_at = "objection", now
            row.delete_after = now + timedelta(days=7)
            await other.commit()

    monkeypatch.setattr(persona_builder, "_assemble", assemble_then_hold)
    with TestClient(app) as client:
        h, _, _ = _signup(client, "지은")
        pid = _create(client, h, BASICS)
        declare_bereaved(client, pid)
        _set(Persona, pid, setup_step=SetupStep.BUILDING, subject_voices=["VOICE_1"])
        _run(_start_job(pid, "BUILD", persona_builder.run_build, {"cloneVoice": True}))
        build = _run(_job(pid, "BUILD"))
        assert build.status == "FAILED" and "멈췄어요" in build.error
        assert "/v1/voice/clone" not in fake_ai.calls


# ────────────────────────── 멈춤 ──────────────────────────


def test_hold_stops_owner_and_family(fake_ai: FakeAI):  # noqa: F811
    """멈춘 대화 상대: 만든 분 대화·대답 듣기·다시 익히기는 409 stopped, 가족은 403 share_paused
    (reason stopped). 나눈 대화 보기와 지우기는 된다."""
    with TestClient(app) as client:
        fam = _family(client)
        owner_room = _room(client, fam.owner, fam.pid)
        member_room = _room(client, fam.member, fam.pid)
        sent = _say(client, fam.owner, owner_room)
        assert sent.status_code == 200, sent.text
        reply_id = sent.json()["data"]["assistantMessage"]["id"]
        _hold(fam.pid)

        blocked = _say(client, fam.owner, owner_room)
        assert blocked.status_code == 409 and blocked.json()["code"] == "stopped"
        assert "지워요" in blocked.json()["detail"]
        speech = client.post(f"{API}/messages/{reply_id}/speech", headers=fam.owner)
        assert speech.status_code == 409 and speech.json()["code"] == "stopped"
        member = _say(client, fam.member, member_room)
        assert member.status_code == 403
        assert member.json()["code"] == "share_paused" and member.json()["reason"] == "stopped"
        assert member.json()["detail"] == "지금은 이 대화 상대와 이야기할 수 없어요."
        changed = client.put(
            f"{API}/personas/{fam.pid}",
            headers=fam.owner,
            json={"basics": OWNER_BASICS | {"calledMe": "강아지"}},
        )
        assert changed.status_code == 409 and changed.json()["code"] == "stopped"

        detail = _detail(client, fam.owner, fam.pid)
        assert detail["hold"]["reason"] == "objection"
        assert [a["kind"] for a in detail["actions"]] == ["stopped"]
        assert detail["actions"][0]["blocking"] is True
        assert not any(detail["gates"][k] for k in ("audio", "voice", "build", "chat", "invite"))
        assert _detail(client, fam.member, fam.pid)["stopped"] is True
        listed = client.get(f"{API}/personas", headers=fam.owner).json()["data"][0]
        assert listed["actions"] == [{"kind": "stopped", "blocking": True}]
        history = client.get(f"{API}/conversations/{owner_room}", headers=fam.owner)
        assert history.status_code == 200
        assert client.delete(f"{API}/personas/{fam.pid}", headers=fam.owner).status_code == 200


def test_held_persona_gets_no_interview_memory_or_rebuild(fake_ai: FakeAI):  # noqa: F811
    """관리자 기억 넣기·다시 만들기도 멈춘 대화 상대에는 하지 않는다."""
    with TestClient(app) as client:
        _, owner_id, _ = _signup(client, "지은")
        pid = _persona(owner_id)
        _hold(pid)

        async def add(db):
            persona = await db.get(Persona, pid)
            await interview.add_memory(db, persona, text="시장 이야기", title=None)

        with pytest.raises(interview.InterviewConflict):
            _run(_with_db(add, consent=owner_id, pid=pid))
        assert "/v1/embeddings" not in fake_ai.calls


def _ai_counts(fake_ai: FakeAI) -> dict[str, int]:  # noqa: F811
    return {path: len(calls) for path, calls in fake_ai.calls.items()}


def test_held_persona_applies_no_family_correction(fake_ai: FakeAI):  # noqa: F811
    """멈춘 대화 상대는 가족 고침 반영(기억 임베딩·빈틈 질문 — 해외 회사)도 하지 않는다(#1)."""
    with TestClient(app) as client:
        fam = _family(client)
        cid = _room(client, fam.member, fam.pid)
        reply = _say(client, fam.member, cid).json()["data"]["assistantMessage"]["id"]
        content = client.post(
            f"{API}/messages/{reply}/feedback",
            headers=fam.member,
            json={"rating": "DOWN", "tags": ["CONTENT"], "correction": "남원이지."},
        )
        assert content.json()["data"]["state"] == "pending_owner"
        _hold(fam.pid)
        before = _ai_counts(fake_ai)
        url = f"{API}/personas/{fam.pid}/corrections/{content.json()['data']['id']}/approve"
        approved = client.post(url, headers=fam.owner)
        assert approved.status_code == 409 and approved.json()["code"] == "stopped"
        assert _ai_counts(fake_ai) == before


def test_held_persona_applies_no_owner_correction(fake_ai: FakeAI):  # noqa: F811
    """멈춘 대화 상대에 만든 분이 '내용이 틀려요'로 고쳐도 기억 임베딩(해외 회사)을 하지 않는다."""
    with TestClient(app) as client:
        h, owner_id, _ = _signup(client, "지은")
        pid = _persona(owner_id)
        reply = _say(client, h, _room(client, h, pid)).json()["data"]["assistantMessage"]["id"]
        _hold(pid)
        before = _ai_counts(fake_ai)
        fixed = client.post(
            f"{API}/messages/{reply}/feedback",
            headers=h,
            json={"rating": "DOWN", "tags": ["CONTENT"], "correction": "남원이지."},
        )
        assert fixed.status_code == 409 and fixed.json()["code"] == "stopped"
        assert _ai_counts(fake_ai) == before


async def _with_db(fn, *, consent: str | None = None, pid: str | None = None):
    async with SessionLocal() as db:
        if consent is not None:
            # 기억 인터뷰 내용을 넣는 데 동의한 만든 분(require_interview_consent)
            owner = await db.get(User, consent)
            await legal.record(db, owner, legal.persona_type("interview_use", pid), True)
            await db.commit()
        return await fn(db)


# ────────────────────────── 기억 인터뷰(글) ──────────────────────────


def test_interview_memory_waits_for_consent_until_ready(fake_ai: FakeAI):  # noqa: F811
    """만드는 중인 살아 계신 분: 그분 동의 녹음 전에는 기억 넣기(바로 OpenAI 임베딩)를 막는다.
    다 만든 대화 상대는 넣는다(WO 3-8)."""
    with TestClient(app) as client:
        h, owner_id, _ = _signup(client, "민수")
        pid = _create(client, h, LIVING)

        async def add(db):
            persona = await db.get(Persona, pid)
            return await interview.add_memory(db, persona, text="시장 이야기", title=None)

        with pytest.raises(interview.InterviewConflict) as error:
            _run(_with_db(add, consent=owner_id, pid=pid))
        assert str(error.value) == gate.SUBJECT_TEXT
        assert "/v1/embeddings" not in fake_ai.calls
        _set(Persona, pid, setup_step=SetupStep.READY)
        memory = _run(_with_db(add))
        assert isinstance(memory, Memory) and fake_ai.calls["/v1/embeddings"]


# ────────────────────────── 돌아가신 분(대표 답 #3) ──────────────────────────


def test_existing_deceased_persona_gets_a_card_only(fake_ai: FakeAI):  # noqa: F811
    """유족 명단이 없는 이미 만든 고인 대화 상대: 대화·목소리 다시 만들기·초대 모두 그대로(막지
    않음), 만든 분에게 막지 않는 카드만. 적고 나면 카드가 사라진다."""
    with TestClient(app) as client:
        h, owner_id, _ = _signup(client, "지은")
        pid = _persona(owner_id)
        assert _say(client, h, _room(client, h, pid)).status_code == 200
        for action in ("audio", "voice", "build", "chat", "speech", "invite"):
            assert _run(_block(pid, action)) is None, action
        detail = _detail(client, h, pid)
        assert detail["bereavedDeclared"] is False
        card = detail["actions"][0]
        assert card == {
            "kind": "bereaved",
            "blocking": False,
            "title": gate.BEREAVED_TITLE,
            "body": gate.BEREAVED_BODY,
            "route": f"/p/{pid}/bereaved",
        }
        declare_bereaved(client, pid)
        detail = _detail(client, h, pid)
        assert detail["actions"] == [] and detail["bereavedDeclared"] is True


def test_new_deceased_persona_declares_before_build():
    """새로 만드는 고인 대화 상대는 만들기(목소리 포함) 전에 유족 명단이 있어야 한다. 받아쓰기는
    그 전에도 된다."""
    with TestClient(app) as client:
        h, _, _ = _signup(client, "지은")
        pid = _create(client, h, BASICS)
        assert _run(_block(pid, "audio")) is None
        for action in ("build", "voice"):
            found = _run(_block(pid, action))
            assert found is not None and found.code == "bereaved", action
            assert found.message == gate.BEREAVED_BUILD
        assert (
            _detail(client, h, pid)["actions"] == []
        )  # 카드는 다 만든 것에만(만들기 전 확인 화면)
        declare_bereaved(client, pid)
        assert _run(_block(pid, "build")) is None


async def _voice_pledge(owner_id: str, pid: str, doc: str) -> None:
    async with SessionLocal() as db:
        owner = await db.get(User, owner_id)
        await legal.record(
            db, owner, legal.persona_type("voice_clone", pid), True, persona_id=pid, doc=doc
        )
        await db.commit()


async def _retrain(pid: str, owner_id: str) -> tuple[bool, str | None]:
    async with SessionLocal() as db:
        persona = await db.get(Persona, pid)
        owner = await db.get(User, owner_id)
        found = await gate.block(db, persona, "voice")
        return await voice_retrain.eligible(db, persona, owner), found and found.code


def test_living_persona_turned_deceased_declares_first(fake_ai: FakeAI):  # noqa: F811
    """다 만든 살아 계신 분을 돌아가신 분으로 바꾸면 새 고인 대화 상대다(대표 답 #3 은 이미 고인이던
    것만): 유족 명단 전에는 녹음 더하기·목소리 다시 만들기·Fish 다시 만들기를 하지 않고(그분 동의
    녹음을 피하지 못하게) 대화는 그대로다. 표시는 다음 고치기에도 남고, 명단을 적으면 열린다."""
    with TestClient(app) as client:
        h, owner_id, _ = _signup(client, "민수")
        pid = _persona(owner_id, basics=LIVING, voice_id="voice-old", voice_provider="elevenlabs")
        _set(Persona, pid, subject_voices=["VOICE_1"])
        # 바꾼 뒤의 목소리 확인 문서(pledge_deceased)까지 있어도 게이트가 막는다
        _run(_voice_pledge(owner_id, pid, "pledge_deceased"))
        for path in ("voice", "recordings/apply"):
            assert client.post(f"{API}/personas/{pid}/{path}", headers=h).json()["code"] == (
                "subject_consent"
            )

        moved = client.put(
            f"{API}/personas/{pid}", headers=h, json={"basics": LIVING | {"status": "deceased"}}
        )
        assert moved.status_code == 200, moved.text
        _wait_job(client, h, pid, "UPDATE")
        for path in ("voice", "recordings/apply"):
            blocked = client.post(f"{API}/personas/{pid}/{path}", headers=h)
            assert blocked.status_code == 409, path
            assert blocked.json()["code"] == "bereaved"
            assert blocked.json()["detail"] == gate.BEREAVED_BUILD
        assert _run(_retrain(pid, owner_id)) == (False, "bereaved")
        assert _say(client, h, _room(client, h, pid)).status_code == 200
        detail = _detail(client, h, pid)
        gates = [detail["gates"][k] for k in ("audio", "voice", "build", "chat", "invite")]
        assert gates == [False, False, False, True, True]
        assert detail["actions"] == [
            {
                "kind": "bereaved",
                "blocking": False,
                "title": gate.BEREAVED_TITLE,
                "body": gate.BEREAVED_BODY_CHANGED.format(chat=gate.BEREAVED_CHAT),
                "route": f"/p/{pid}/bereaved",
            }
        ]
        # 다시 고쳐도 표시는 남는다(validate_basics 는 정해진 칸만 돌려준다)
        again = client.put(
            f"{API}/personas/{pid}",
            headers=h,
            json={"basics": LIVING | {"status": "deceased", "calledMe": "우리 아들"}},
        )
        assert again.status_code == 200, again.text
        _wait_job(client, h, pid, "UPDATE")
        assert _run(_block(pid, "audio")).code == "bereaved"

        declare_bereaved(client, pid)
        assert _run(_retrain(pid, owner_id)) == (True, None)
        assert _detail(client, h, pid)["actions"] == []
        # 게이트를 지났다(새로 올린 녹음이 없어 400)
        applied = client.post(f"{API}/personas/{pid}/recordings/apply", headers=h)
        assert applied.status_code == 400 and applied.json()["detail"] == "새로 올린 녹음이 없어요."
        voiced = client.post(f"{API}/personas/{pid}/voice", headers=h, json={"voiceConsent": True})
        assert voiced.status_code == 200, voiced.text
        _wait_job(client, h, pid, "VOICE")


def test_turning_deceased_marks_a_new_deceased_persona():
    """표시(BEREAVED_REQUIRED)는 돌아가신 분이 아니던 것(살아 계신 분·나 자신)을 바꿀 때만 붙고
    이어 가며, 돌아가신 분이 아니게 되면 떨어진다. 이미 고인이던 것에는 붙지 않는다."""
    mark = gate.BEREAVED_REQUIRED
    me = {"name": "김민수", "relation": "나 자신", "status": "alive"}
    assert gate.mark_new_deceased(LIVING, OWNER_BASICS)[mark] is True
    assert gate.mark_new_deceased(me, OWNER_BASICS)[mark] is True
    assert mark not in gate.mark_new_deceased(OWNER_BASICS, OWNER_BASICS | {"calledMe": "강아지"})
    marked = OWNER_BASICS | {mark: True}
    assert gate.mark_new_deceased(marked, OWNER_BASICS)[mark] is True
    assert mark not in gate.mark_new_deceased(marked, LIVING)
    # 화면이 보낸 표시는 받지 않는다(고인이던 것에 붙여 막거나, 바꾼 것에서 떼어 열지 못하게)
    assert mark not in gate.mark_new_deceased(OWNER_BASICS, OWNER_BASICS | {mark: True})


# ────────────────────────── 그 밖 ──────────────────────────


def test_unable_persona_keeps_talking_but_stops_processing():
    """예전 '대화가 어려우신 분': 대화는 그대로, 녹음 처리·목소리·만들기는 멈춘다(안내 카드)."""
    unable = OWNER_BASICS | {"status": "unable"}
    with TestClient(app) as client:
        h, owner_id, _ = _signup(client, "지은")
        pid = _persona(owner_id, basics=unable)
        for action in ("audio", "voice", "build"):
            assert _run(_block(pid, action)).code == "unable"
        for action in ("chat", "text", "invite"):
            assert _run(_block(pid, action)) is None
        card = _detail(client, h, pid)["actions"][0]
        assert card["kind"] == "unable_frozen" and card["blocking"] is False


def test_intake_meta_lists_the_new_choices():
    with TestClient(app) as client:
        meta = client.get(f"{API}/intake/meta").json()["data"]
        assert "unable" not in meta["statuses"]
        assert meta["minorRelations"] == {
            "relation": ["아들", "딸"],
            "myRelation": ["어머니", "아버지"],
        }
        assert set(meta["guardianBases"]) == {"both_agreed", "sole_custody"}
        assert isinstance(meta["under14Open"], bool)
        assert "spouse" in meta["applicantRelations"] and "son" in meta["bereavedRelations"]
        assert "spouse" in meta["consentingRelations"]
        assert "할머니" in meta["minorShareRelations"]


def test_other_person_cannot_become_myself():
    """다른 분의 대화 상대를 '나 자신'으로 바꿔 그분 동의 녹음을 피하지 못한다."""
    with TestClient(app) as client:
        h, _, _ = _signup(client, "민수")
        pid = _create(client, h, LIVING)
        moved = client.put(
            f"{API}/personas/{pid}",
            headers=h,
            json={"basics": LIVING | {"relation": "나 자신"}},
        )
        assert moved.status_code == 400


def test_refused_subject_locks_status_and_relation():
    """그분 동의 녹음에서 원하지 않으신 대화 상대는 상태·관계를 바꿀 수 없다."""
    with TestClient(app) as client:
        h, _, _ = _signup(client, "민수")
        pid = _create(client, h, LIVING)
        row_id = pass_subject_consent(client, pid)
        from app.db.models import SubjectConsentRecording

        _set(SubjectConsentRecording, row_id, status="refused")
        for change in ({"status": "deceased"}, {"relation": "아버지"}):
            moved = client.put(f"{API}/personas/{pid}", headers=h, json={"basics": LIVING | change})
            assert moved.status_code == 400 and "바꿀 수 없어요" in moved.json()["detail"]
        same = client.put(
            f"{API}/personas/{pid}", headers=h, json={"basics": LIVING | {"calledMe": "아들"}}
        )
        assert same.status_code == 200


TEEN = {
    "name": "김하늘",
    "relation": "아들",
    "addressTerm": "하늘이",
    "status": "alive",
    "myName": "박지수",
    "myRelation": "어머니",
    "calledMe": "엄마",
    "minor": True,
    "birthYm": "2010-01",
    "guardianBasis": "both_agreed",
    "guardianAttest": True,
}


def test_minor_flag_stays_and_reaches_the_ai_only_when_true(fake_ai: FakeAI):  # noqa: F811
    """아이의 대화 상대: 화면이 minor 칸을 빼고 고쳐도 그대로 남고, 대화 요청에는 minor=true 만
    싣는다(어른은 칸을 싣지 않는다 — 예전 AI 서버가 모르는 칸을 거절해서)."""
    with TestClient(app) as client:
        h, _, _ = _signup(client, "지수")
        pid = _create(client, h, TEEN)
        edited = {k: v for k, v in TEEN.items() if k not in ("minor", "birthYm", "guardianAttest")}
        saved = client.put(f"{API}/personas/{pid}", headers=h, json={"basics": edited})
        assert saved.status_code == 200, saved.text
        detail = _detail(client, h, pid)
        assert detail["basics"]["minor"] is True and detail["basics"]["birthYm"] == "2010-01"
        assert detail["minor"]["mode"] == "minor_teen" and detail["minor"]["guardianOk"] is True
        _set(Persona, pid, setup_step=SetupStep.READY, status="READY", instructions="(시험 지침)")
        assert _say(client, h, _room(client, h, pid)).status_code == 200
        assert fake_ai.calls["/v1/persona/responses"][-1]["persona"]["minor"] is True

        adult = _create(client, h, LIVING)
        _set(Persona, adult, setup_step=SetupStep.READY, status="READY", instructions="(시험 지침)")
        assert _say(client, h, _room(client, h, adult)).status_code == 200
        assert "minor" not in fake_ai.calls["/v1/persona/responses"][-1]["persona"]


def test_subject_consent_audio_never_reaches_the_ai_server(fake_ai: FakeAI):  # noqa: F811
    """AI 서버로 가는 어떤 요청에도 그분 동의 녹음 주소가 들어가면 보내지 않는다."""
    url = "https://media.example/personas/p1/subject_consent/01-r1.webm?sig=x"

    async def send():
        await ai_module.ai_service.transcribe_recording(
            jobId="j", recordingId="r", audioUrl=url, audioMimeType="audio/webm"
        )

    with pytest.raises(RuntimeError, match="must never leave TalkTo"):
        _run(send())
    assert not fake_ai.calls

    async def clone():
        await ai_module.ai_service.clone_voice(
            name="x", samples=[{"audioUrl": url}], provider="fish"
        )

    with pytest.raises(RuntimeError):
        _run(clone())
    assert not fake_ai.calls


def test_subject_consent_module_does_not_import_the_ai_client():
    source = inspect.getsource(subject_consent)
    assert "ai_service" not in source and "app.services.ai" not in source


def test_consent_audio_body_limit():
    path = "/api/v1/personas/p1/subject-consent/r1/audio"
    assert body_limit.kind_of(path) == "consent_audio"
    assert body_limit.limit_for(path) == 40 * body_limit.MiB
    assert body_limit.too_large_message(path) == "녹음이 너무 길어요. 15분 안으로 녹음해 주세요."
    # 다른 경로는 그대로
    assert body_limit.kind_of("/api/v1/personas/p1/subject-consent/start") is None


def test_gate_summary_and_order():
    """멈춤이 다른 까닭보다 먼저다(§11.1 위에서부터)."""
    with TestClient(app) as client:
        h, owner_id, _ = _signup(client, "민수")
        pid = _persona(owner_id, basics=OWNER_BASICS | {"status": "unable"})
        _hold(pid)
        assert _run(_block(pid, "audio")).code == "stopped"
        assert _run(_block(pid, "chat", member=True)).message == (
            "지금은 이 대화 상대와 이야기할 수 없어요."
        )
        records = _run(_records(owner_id))
        assert records == []


async def _records(user_id: str) -> list:
    async with SessionLocal() as db:
        return list(
            (
                await db.scalars(
                    select(ConsentRecord).where(
                        ConsentRecord.user_id == user_id,
                        ConsentRecord.consent_type.like("subject_consent:%"),
                    )
                )
            ).all()
        )
