"""말로 답하기(POST /personas/{id}/transcriptions): 녹음 → 글. 녹음은 어디에도 남지 않는다."""

import io
import logging
import os
import shutil
import tempfile
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from test_sharing import _family, _set
from test_sharing import _signup as _share_signup

from app.core import ratelimit
from app.core.config import Settings, get_settings
from app.db.models import Persona
from app.main import app
from app.services import ai as ai_module
from app.services import audio

API = "/api/v1"
PATH = "/v1/voice/answer-transcriptions"


def _wav(seconds: float = 2.0, rate: int = 8000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x00\x01" * int(seconds * rate))
    return buffer.getvalue()


class FakeScribe:
    """AI 서버 대역(말로 답하기 받아쓰기만). 받은 요청과 정한 답."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.answer = httpx.Response(200, json={"text": " 밥은 먹었냐  늘 그러셨어요 "})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        assert request.url.path == PATH
        assert request.headers.get("X-AI-Server-Token") == "shared-secret"
        return self.answer


@pytest.fixture
def scribe(monkeypatch: pytest.MonkeyPatch) -> FakeScribe:
    fake = FakeScribe()
    real_client = httpx.AsyncClient

    def configured() -> Settings:
        return get_settings().model_copy(
            update={"ai_server_url": "https://ai.example.test", "ai_server_token": "shared-secret"}
        )

    monkeypatch.setattr(ai_module, "get_settings", configured)
    monkeypatch.setattr(
        ai_module.httpx,
        "AsyncClient",
        lambda *a, **kw: real_client(transport=httpx.MockTransport(fake), **kw),
    )
    return fake


def _media_files() -> set[str]:
    root = Path(os.environ["LOCAL_MEDIA_DIR"])
    return {str(p) for p in root.rglob("*") if p.is_file()} if root.exists() else set()


def _send(client: TestClient, h: dict, pid: str, content: bytes, name: str = "answer.webm"):
    return client.post(
        f"{API}/personas/{pid}/transcriptions",
        headers=h,
        files={"audio": (name, content, "audio/webm")},
    )


def test_owner_and_member_get_text_and_nothing_is_kept(scribe: FakeScribe, monkeypatch):
    """주인과 함께 쓰는 가족은 글을 받고, 남은 404. 저장소에도 임시 폴더에도 녹음이 남지 않는다."""
    made: list[str] = []
    real_tempdir = tempfile.TemporaryDirectory

    def tracked(*args, **kwargs):
        handle = real_tempdir(*args, **kwargs)
        made.append(handle.name)
        return handle

    monkeypatch.setattr(audio.tempfile, "TemporaryDirectory", tracked)
    with TestClient(app) as client:
        fam = _family(client)
        before = _media_files()
        sent = _send(client, fam.owner, fam.pid, _wav(), "answer.wav")
        assert sent.status_code == 200, sent.text
        assert sent.json()["data"] == {"text": "밥은 먹었냐 늘 그러셨어요"}
        # 받아쓰기는 AI 서버의 말로 답하기 길로 녹음 파일만 간다(한국어는 AI 서버가 정한다)
        body = scribe.requests[-1].content
        assert b'name="audio_file"; filename="answer.wav"' in body
        member = _send(client, fam.member, fam.pid, _wav())
        assert member.status_code == 200, member.text

        other, _, _ = _share_signup(client, "남")
        assert _send(client, other, fam.pid, _wav()).status_code == 404
        assert _send(client, other, "no-such-persona", _wav()).status_code == 404
        assert len(scribe.requests) == 2  # 남의 요청은 받아쓰지 않는다

        # 녹음은 저장소에 올리지 않는다(대화 상대 저장소가 그대로다)
        assert _media_files() == before
    # 길이를 재느라 만든 임시 폴더는 바로 지웠다
    if shutil.which("ffmpeg"):
        assert made
    assert all(not Path(name).exists() for name in made)


def test_held_persona_takes_no_spoken_answers(scribe: FakeScribe):
    """멈춘 대화 상대(게이트 #1)는 말로 적기도 받지 않는다: 만든 분 409 stopped, 가족 403
    share_paused(reason stopped). 받아쓰기로 보내지 않는다."""
    with TestClient(app) as client:
        fam = _family(client)
        now = datetime.now(UTC)
        _set(
            Persona,
            fam.pid,
            hold_reason="objection",
            hold_at=now,
            delete_after=now + timedelta(days=7),
        )
        owner = _send(client, fam.owner, fam.pid, _wav())
        assert owner.status_code == 409 and owner.json()["code"] == "stopped"
        member = _send(client, fam.member, fam.pid, _wav())
        assert member.status_code == 403, member.text
        assert member.json()["code"] == "share_paused" and member.json()["reason"] == "stopped"
    assert scribe.requests == []


def test_limits_and_no_speech(scribe: FakeScribe, monkeypatch):
    with TestClient(app) as client:
        fam = _family(client)
        # 6MB 를 넘으면 받아쓰지 않는다
        big = _send(client, fam.owner, fam.pid, b"\x00" * (6 * 1024 * 1024 + 10))
        assert big.status_code == 400 and "3분" in big.json()["detail"]
        assert _send(client, fam.owner, fam.pid, b"").status_code == 400
        if shutil.which("ffmpeg"):
            # 185초(3분에서 멈춘 녹음의 여유)를 넘으면 받아쓰지 않는다
            long = _send(client, fam.owner, fam.pid, _wav(186), "long.wav")
            assert long.status_code == 400 and "3분" in long.json()["detail"]
            # 풀지 못하는(길이를 모르는) 녹음도 받아쓰지 않는다
            junk = _send(client, fam.owner, fam.pid, b"not audio at all" * 10, "junk.webm")
            assert junk.status_code == 400 and "확인하지 못했어요" in junk.json()["detail"]
        assert not scribe.requests

        # 말소리가 없으면 422 no_speech(AI 서버가 알려 주거나 빈 글이 오거나)
        scribe.answer = httpx.Response(
            422, json={"error": {"code": "NO_SPEECH", "message": "no speech"}}
        )
        quiet = _send(client, fam.owner, fam.pid, _wav())
        assert quiet.status_code == 422
        assert quiet.json() == {
            "detail": "말씀을 알아듣지 못했어요. 다시 한번 말씀해 주세요.",
            "code": "no_speech",
        }
        scribe.answer = httpx.Response(200, json={"text": "   "})
        assert _send(client, fam.owner, fam.pid, _wav()).json()["code"] == "no_speech"

        # 다른 AI 서버 오류는 차분한 문장(503)
        scribe.answer = httpx.Response(502, json={"error": {"code": "STT_PROVIDER_ERROR"}})
        failed = _send(client, fam.owner, fam.pid, _wav())
        assert failed.status_code == 503

        # 마구 보내면 429
        monkeypatch.setattr(ratelimit.TRANSCRIPTIONS, "limit", 1)
        ratelimit.TRANSCRIPTIONS.reset()
        scribe.answer = httpx.Response(200, json={"text": "네"})
        assert _send(client, fam.owner, fam.pid, _wav()).status_code == 200
        blocked = _send(client, fam.owner, fam.pid, _wav())
        # 언제 다시 되는지 알린다(10분 창이라 '10분 뒤')
        assert blocked.status_code == 429
        assert "10분 뒤에 다시 쓸 수 있어요" in blocked.json()["detail"]
        assert "글로 적어 주세요" in blocked.json()["detail"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="녹음 길이는 ffmpeg 로 잰다")
def test_three_minute_auto_stop_is_accepted(scribe: FakeScribe):
    """화면은 3분에서 멈추지만 멈추기까지 조금 걸려 녹음은 180초를 조금 넘는다: 받아쓴다."""
    with TestClient(app) as client:
        fam = _family(client)
        for seconds in (180.5, 182):
            # 3분 녹음(16kHz mono wav, 가장 큰 형식)도 6MB 안이다
            clip = _wav(seconds, 16000)
            assert len(clip) < 6 * 1024 * 1024
            sent = _send(client, fam.owner, fam.pid, clip, "answer.wav")
            assert sent.status_code == 200, sent.text
        assert len(scribe.requests) == 2  # AI 서버로 넘어갔다
        too_long = _send(client, fam.owner, fam.pid, _wav(186, 16000), "answer.wav")
        assert too_long.status_code == 400 and "3분" in too_long.json()["detail"]
        assert len(scribe.requests) == 2


def test_one_at_a_time_slots_and_daily_cap(scribe: FakeScribe, monkeypatch):
    """한 사람은 한 번에 하나만(429), 서버 전체는 SLOTS 개까지(503), 하루 상한(429)."""
    from app.api.v1 import transcriptions

    with TestClient(app) as client:
        fam = _family(client)
        # 앞서 보낸 것을 아직 받아쓰는 중이면 429
        monkeypatch.setattr(transcriptions, "_in_flight", {fam.owner_id})
        busy = _send(client, fam.owner, fam.pid, _wav())
        assert busy.status_code == 429 and "아직" in busy.json()["detail"]
        # 가족(다른 사람)은 그대로 된다
        assert _send(client, fam.member, fam.pid, _wav()).status_code == 200
        monkeypatch.setattr(transcriptions, "_in_flight", set())
        # 서버 전체 자리가 차면 503
        monkeypatch.setattr(transcriptions, "_active", transcriptions.SLOTS)
        full = _send(client, fam.owner, fam.pid, _wav())
        assert full.status_code == 503
        monkeypatch.setattr(transcriptions, "_active", 0)
        assert len(scribe.requests) == 1
        # 끝나면 자리를 돌려준다
        assert _send(client, fam.owner, fam.pid, _wav()).status_code == 200
        assert transcriptions._active == 0 and not transcriptions._in_flight

        # 하루 상한
        monkeypatch.setattr(ratelimit.TRANSCRIPTIONS_DAILY, "limit", 1)
        ratelimit.TRANSCRIPTIONS_DAILY.reset()
        assert _send(client, fam.owner, fam.pid, _wav()).status_code == 200
        assert _send(client, fam.owner, fam.pid, _wav()).status_code == 429


def test_same_ip_daily_cap_counts_every_account(scribe: FakeScribe, monkeypatch):
    """같은 IP 는 계정을 바꿔도 함께 센다(429). 다른 IP 는 그대로 된다."""
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    monkeypatch.setattr(ratelimit.TRANSCRIPTIONS_IP, "limit", 2)
    with TestClient(app) as client:
        fam = _family(client)
        assert _send(client, fam.owner, fam.pid, _wav()).status_code == 200
        assert _send(client, fam.member, fam.pid, _wav()).status_code == 200
        # 계정마다는 한 번씩뿐이지만 같은 IP 라 막힌다
        for h in (fam.owner, fam.member):
            blocked = _send(client, h, fam.pid, _wav())
            assert blocked.status_code == 429 and "Retry-After" in blocked.headers
        assert len(scribe.requests) == 2
        # 다른 IP(CloudFront 가 붙인 주소)에서는 된다
        elsewhere = fam.owner | {"CloudFront-Viewer-Address": "203.0.113.7:443"}
        assert _send(client, elsewhere, fam.pid, _wav()).status_code == 200
        assert len(scribe.requests) == 3


def test_server_daily_seconds_budget(scribe: FakeScribe, monkeypatch, caplog):
    """서버 전체 최근 24시간 받아쓴 길이를 다 쓰면 누구든 503(BUSY). 누가 걸렸는지는 안 남긴다."""
    from app.api.v1 import transcriptions

    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "transcription_daily_seconds", 1)
    with TestClient(app) as client:
        fam = _family(client)
        assert _send(client, fam.owner, fam.pid, _wav(2.0)).status_code == 200
        # 잰 길이(재지 못하면 받는 상한)를 더했다
        used = ratelimit.TRANSCRIPTION_SECONDS.used()
        if shutil.which("ffmpeg"):
            assert used == pytest.approx(2.0, abs=0.1)
        else:
            assert used == transcriptions.ACCEPT_SECONDS
        caplog.set_level(logging.WARNING, logger="app.core.ratelimit")
        # 다 썼으면 주인도 가족도(다른 IP 여도) 자리가 찬 것과 같게 503
        elsewhere = fam.member | {"CloudFront-Viewer-Address": "203.0.113.7:443"}
        for h in (fam.owner, elsewhere):
            full = _send(client, h, fam.pid, _wav())
            assert full.status_code == 503
            assert full.json()["detail"] == transcriptions.BUSY
        assert len(scribe.requests) == 1
        assert transcriptions._active == 0 and not transcriptions._in_flight
        assert "budget used up: transcription_seconds" in caplog.text
        for who in (fam.owner_id, fam.member_id, fam.owner_email, fam.member_email, "203.0.113"):
            assert who not in caplog.text
        # 0 이하면 끈다
        monkeypatch.setattr(settings, "transcription_daily_seconds", 0)
        assert _send(client, fam.owner, fam.pid, _wav()).status_code == 200
        # 요청 횟수 제한을 끄면 막지 않는다
        monkeypatch.setattr(settings, "transcription_daily_seconds", 1)
        monkeypatch.setattr(settings, "rate_limit_enabled", False)
        assert _send(client, fam.owner, fam.pid, _wav()).status_code == 200


def test_budget_rolls_over_24_hours(monkeypatch):
    """최근 24시간에 쓴 것만 센다. 요청 횟수 제한을 끄면 세지도 막지도 않는다."""
    hour = ratelimit.HOUR
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    budget = ratelimit.Budget("test", 24 * hour)
    budget.spend(100, now=0)
    budget.spend(50, now=10 * hour)
    assert budget.used_up(150, now=11 * hour)
    assert not budget.used_up(151, now=11 * hour)
    assert not budget.used_up(0, now=11 * hour)  # 0 이하면 끈다
    # 첫 것이 24시간을 지나면 빠진다
    assert budget.used(now=24 * hour + 1) == 50
    assert not budget.used_up(100, now=24 * hour + 1)
    assert budget.used(now=34 * hour + 1) == 0
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", False)
    budget.spend(1000, now=35 * hour)
    assert not budget.used_up(1, now=35 * hour)
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    assert budget.used(now=35 * hour) == 0
    # 시험마다 비우는 reset_all 이 서버 전체 받아쓰기 길이도 비운다
    ratelimit.TRANSCRIPTION_SECONDS.spend(10)
    assert ratelimit.TRANSCRIPTION_SECONDS.used() == 10
    ratelimit.reset_all()
    assert ratelimit.TRANSCRIPTION_SECONDS.used() == 0
    assert ratelimit.TRANSCRIPTIONS_IP in ratelimit.ALL


def test_exactly_six_mib_is_accepted(scribe: FakeScribe):
    """6MiB(6,291,456바이트) 녹음까지는 받아쓴다(넘는 것만 400). 48kHz 라 1분 남짓이다."""
    limit = 6 * 1024 * 1024
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(48000)
        out.writeframes(b"\x00\x01" * ((limit - 44) // 2))  # 머리 44바이트 + 소리
    clip = buffer.getvalue()
    assert len(clip) == limit
    with TestClient(app) as client:
        fam = _family(client)
        sent = _send(client, fam.owner, fam.pid, clip, "answer.wav")
        assert sent.status_code == 200, sent.text
        assert len(scribe.requests) == 1
        over = _send(client, fam.owner, fam.pid, clip + b"\x00", "answer.wav")
        assert over.status_code == 400 and len(scribe.requests) == 1


def test_body_limit_allows_six_megabytes():
    from app.core import body_limit

    path = f"{API}/personas/p/transcriptions"
    assert body_limit.limit_for(path) > 6 * 1024 * 1024
    assert "3분" in body_limit.too_large_message(path)


def test_limits_fit_a_whole_creation_by_voice():
    # 만들 때 질문을 다 말로 답해도 걸리지 않게(2026-10-25 30번/10분에 걸린 일)
    assert ratelimit.TRANSCRIPTIONS.limit >= 60 and ratelimit.TRANSCRIPTIONS.window == 10 * 60
    assert ratelimit.TRANSCRIPTIONS_DAILY.limit >= 200
    assert ratelimit.TRANSCRIPTIONS_IP.limit >= ratelimit.TRANSCRIPTIONS_DAILY.limit


def test_wait_text():
    assert [ratelimit.wait_text(s) for s in (0, 30, 60, 61, 599, 3600, 5400, 86399)] == [
        "1분",
        "1분",
        "1분",
        "2분",
        "10분",
        "1시간",
        "1시간 30분",
        "24시간",
    ]
