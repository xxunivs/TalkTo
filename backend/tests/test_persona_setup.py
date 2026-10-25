"""가입 → 녹음 올리기 → 화자 고르기 → 질문 → 만들기 → 대화.

AI 서버는 가짜(httpx MockTransport)로 바꾸고, 보낸 요청 모양을 같이 확인한다.
"""

import asyncio
import io
import json
import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from legal_gates import declare_bereaved
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.db.models import Memory, Persona
from app.db.session import SessionLocal
from app.main import app
from app.services import ai as ai_module

DIALOG = [
    ("SPK_0", "여보세요, 어 그래 길동이니?"),
    ("SPK_1", "응 할머니, 저예요. 밥 드셨어요?"),
    ("SPK_0", "먹었지. 너는 밥은 먹었냐? 늦게 자지 말고."),
    ("SPK_1", "네, 오늘은 일찍 잘게요."),
    ("SPK_0", "그래 잘했다. 날 추운데 옷 따뜻하게 입고 다니고."),
]


def _video(seconds: int = 25) -> bytes:
    """소리가 든 작은 mp4. 영상에서 소리만 뽑는지 본다."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "v.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c=black:s=64x64:d={seconds}",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:duration={seconds}",
                "-shortest",
                "-c:v",
                "mpeg4",
                "-c:a",
                "aac",
                str(out),
            ],
            check=True,
        )
        return out.read_bytes()


def _wav(seconds: float = 25.0) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * int(8000 * seconds))
    return buf.getvalue()


# AI 서버 목소리 계약(AI 레포 infra/schema/voice.py). 모르는 필드는 받지 않고(extra=forbid),
# provider 는 이 값들만 받는다. 어긋나면 진짜 AI 서버처럼 422 를 준다.
VOICE_CONTRACT = {
    "/v1/voice/clone": {"name", "sampleAudioUrl", "samples", "provider"},
    "/v1/voice/speech": {
        "text",
        "voiceId",
        "speed",
        "stability",
        "similarityBoost",
        "style",
        "emotion",
    },
    "/v1/voice/delete": {"voiceId", "provider"},
}
CLONE_PROVIDERS = {"fish", "elevenlabs", "minimax"}  # VoiceCloneRequest.provider
DELETE_PROVIDERS = {"fish", "elevenlabs", "minimax"}  # VoiceDeleteRequest.provider


def _contract_error(path: str, body: dict) -> str | None:
    fields = VOICE_CONTRACT.get(path)
    if fields is None:
        return None
    extra = set(body) - fields
    if extra:
        return f"extra fields {sorted(extra)}"
    provider = body.get("provider")
    allowed = CLONE_PROVIDERS if path == "/v1/voice/clone" else DELETE_PROVIDERS
    if provider is not None and provider not in allowed:
        return f"provider {provider!r}"
    return None


class FakeAI:
    """AI 서버 대역. 경로마다 정해 둔 답을 주고 받은 요청을 모아 둔다.

    목소리는 진짜 AI 서버처럼 늘 Fish 로 만든다(보낸 provider 와 상관없이 "fish"를 돌려준다).
    목소리 id 는 시험마다 다르다(voice(n)): 시험들이 DB 를 함께 써서, 같은 id 면 다른 시험의 대화
    상대가 아직 쓰는 목소리로 보고 지우지 않는다(persona_builder.voice_in_use).
    """

    def __init__(self) -> None:
        self.calls: dict[str, list[dict]] = {}
        self.tag = uuid4().hex[:6]
        # 대답에 실을 목소리용 글(AI speechText, FISH_EMOTION=inline).
        # None 이면 칸을 보내지 않는다(예전 AI 서버)
        self.speech_text: str | None = None

    def voice(self, n: int) -> str:
        """n 번째로 만든 목소리의 id."""
        return f"voice-{n}-{self.tag}"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content or b"{}")
        self.calls.setdefault(path, []).append(body)
        assert request.headers.get("X-AI-Server-Token") == "shared-secret"
        problem = _contract_error(path, body)
        if problem:
            return httpx.Response(422, json={"detail": problem})
        if path == "/v1/analysis/transcriptions":
            segments, t = [], 0
            for i, (label, text) in enumerate(DIALOG):
                segments.append(
                    {
                        "segmentIndex": i,
                        "startMs": t,
                        "endMs": t + 4000,
                        "speakerLabel": label,
                        "transcriptText": text,
                    }
                )
                t += 4500
            return httpx.Response(
                200,
                json={
                    "segments": segments,
                    "provider": "fake",
                    "model": "fake",
                    "subjectSpeakerLabel": None,
                },
            )
        if path == "/v1/analysis/subject-speaker":
            return httpx.Response(200, json={"subjectSpeakerLabel": "SPK_0"})
        if path == "/v1/analysis/voice-groups":
            # 목소리 지문이 녹음 사이에서 같은 사람을 찾았다고 치고, 라벨별로 묶는다
            groups: dict[str, list[dict]] = {}
            for rec in body["recordings"]:
                ms: dict[str, int] = {}
                for seg in rec["transcriptSegments"]:
                    ms[seg["speakerLabel"]] = (
                        ms.get(seg["speakerLabel"], 0) + seg["endMs"] - seg["startMs"]
                    )
                for label, total in ms.items():
                    groups.setdefault(label, []).append(
                        {
                            "recordingId": rec["recordingId"],
                            "speakerLabel": label,
                            "speechMs": total,
                        }
                    )
            ordered = sorted(groups.values(), key=lambda m: -sum(x["speechMs"] for x in m))
            return httpx.Response(
                200,
                json={
                    "method": "ecapa",
                    "groups": [
                        {"groupIndex": i, "speechMs": sum(x["speechMs"] for x in m), "members": m}
                        for i, m in enumerate(ordered)
                    ],
                },
            )
        if path == "/v1/analysis/recording":
            first = body["transcriptSegments"][0]["id"]
            return httpx.Response(
                200,
                json={
                    "memorySegments": [
                        {
                            "segmentIndex": 0,
                            "sourceTranscriptSegmentIds": [first],
                            "startMs": 0,
                            "endMs": 4000,
                            "speakerLabel": "SPK_0",
                            "memoryText": "손주가 늦게 자는 것을 걱정한다.",
                            "confidence": "confirmed",
                            "importanceScore": 7,
                            "tags": ["가족"],
                            "relatedPeople": ["홍길동"],
                            "sensitivityFlags": [],
                        }
                    ],
                    "summary": "안부 통화",
                    "tags": ["가족"],
                    "speechStyle": {
                        "recurringPhrases": [
                            {"phrase": "밥은 먹었냐", "sourceTranscriptSegmentIds": [first]}
                        ],
                        "addressTerms": [
                            {"person": "홍길동", "term": "길동이", "sourceTranscriptSegmentIds": []}
                        ],
                        "sentencePatterns": ["짧은 반말 단문"],
                        "emotionalExpressions": [],
                    },
                    "safetyFlags": [
                        {
                            "type": "health",
                            "description": "당뇨 이야기",
                            "sourceTranscriptSegmentIds": [],
                        }
                    ],
                    "provider": "fake",
                    "model": "fake",
                },
            )
        if path == "/v1/persona/reflection":
            ids = [m["id"] for m in body["memories"]]
            return httpx.Response(
                200,
                json={
                    "reflections": [
                        {
                            "insight": "가족의 건강을 늘 먼저 챙긴다.",
                            "category": "가치관",
                            "evidenceMemoryIds": ids,
                            "importance": 7,
                        }
                    ],
                    "provider": "fake",
                    "model": "fake",
                },
            )
        if path == "/v1/persona/assembly":
            # 받은 재료를 그대로 지침으로 돌려줘서 테스트가 무엇이 들어갔는지 볼 수 있게 한다
            return httpx.Response(
                200,
                json={
                    "instructions": json.dumps(body, ensure_ascii=False),
                    "subjectName": "김순자",
                },
            )
        if path == "/v1/embeddings":
            return httpx.Response(
                200,
                json={
                    "embeddings": [
                        {
                            "memorySegmentId": item["memorySegmentId"],
                            "embeddingIndex": item["embeddingIndex"],
                            "provider": "fake",
                            "model": "fake",
                            "dimensions": 3,
                            "embedding": [1.0, 0.0, 0.0],
                        }
                        for item in body["items"]
                    ]
                },
            )
        if path == "/v1/embeddings/query":
            return httpx.Response(
                200,
                json={
                    "provider": "fake",
                    "model": "fake",
                    "dimensions": 3,
                    "embedding": [1.0, 0.1, 0.0],
                },
            )
        if path == "/v1/persona/responses":
            return httpx.Response(
                200,
                json={
                    "content": "그래, 밥은 먹었냐?",
                    "retrievedMemoryIds": [m["id"] for m in body["memories"]],
                    "emotion": "calm",
                    **({"speechText": self.speech_text} if self.speech_text else {}),
                    "provider": "fake",
                    "model": "fake",
                },
            )
        if path == "/v1/voice/clone":
            n = len(self.calls["/v1/voice/clone"])
            return httpx.Response(200, json={"voiceId": self.voice(n), "provider": "fish"})
        if path == "/v1/voice/speech":
            return httpx.Response(200, content=b"ID3fake", headers={"content-type": "audio/mpeg"})
        if path == "/v1/voice/delete":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"error": {"code": "nope", "message": path}})


@pytest.fixture
def fake_ai(monkeypatch: pytest.MonkeyPatch) -> FakeAI:
    fake = FakeAI()
    transport = httpx.MockTransport(fake)
    real_client = httpx.AsyncClient

    def configured() -> Settings:
        return get_settings().model_copy(
            update={"ai_server_url": "https://ai.example.test", "ai_server_token": "shared-secret"}
        )

    monkeypatch.setattr(ai_module, "get_settings", configured)
    monkeypatch.setattr(
        ai_module.httpx, "AsyncClient", lambda *a, **kw: real_client(transport=transport, **kw)
    )
    return fake


# 가입 화면의 계정 필수 동의(전부 체크)
AGREEMENTS = [
    "age14",
    "service",
    "terms",
    "privacy",
    "voice_data",
    "voice_synthesis",
    "overseas",
    "overseas_elevenlabs",
    "overseas_fish",
]


def _signup(client: TestClient, email: str) -> dict:
    response = client.post(
        "/api/v1/auth/signup",
        json={
            "email": email,
            "password": "password123",
            "name": "홍길동",
            "agreements": AGREEMENTS,
        },
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['data']['bearerToken']}"}


def _memory_sources(pid: str) -> set[str]:
    """대화에 쓰는 기억의 출처(지침·기억 보기는 고객 API 에 없어 DB 에서 읽는다)."""

    async def go():
        async with SessionLocal() as db:
            persona = await db.get(Persona, pid)
            rows = await db.scalars(
                select(Memory).where(
                    Memory.person_id == persona.person_id, Memory.status == "ACTIVE"
                )
            )
            return {m.source for m in rows}

    return asyncio.run(go())


def _wait(client: TestClient, headers: dict, pid: str, kind: str, timeout: float = 20) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        job = (
            client.get(f"/api/v1/personas/{pid}", headers=headers).json()["data"]["jobs"].get(kind)
        )
        if job and job["status"] != "RUNNING":
            assert job["status"] == "DONE", job
            assert job["percent"] == 100, job  # 끝나면 진행 막대는 100%
            return job
        time.sleep(0.1)
    raise AssertionError(f"{kind} did not finish")


BASICS = {
    "name": "김순자",
    "relation": "할머니",
    "addressTerm": "할머니",
    "status": "deceased",
    "myName": "홍길동",
    "myRelation": "손자",
    "calledMe": "길동아",
}
ANSWERS = {
    # 2026-10-21 개편 뒤 가족·고인의 필수 답(P1-07A 는 BASICS.calledMe 가 대신하고,
    # 조심할 이야기의 임종 등급은 서버가 미리 채운다)
    "M2-01-level": "반말",
    "M2-01-self": "할머니가",
    "M3-01": "길동이니?",
    "M3-02": "조심히 가라",
    "C-09": ["몸조심해라"],
    "C-01": "한두 문장으로 짧게",
    "C-02": "말수가 적고, 말보다 행동(끼니·안부 챙김)으로 표현한다",
    "C-07": ["음식·밥을 챙겨서"],
    "C-03": "말없이 곁에 있어준다",
    "M4-01": ["다정함", "걱정이 많음"],
    "M6-08": "된장찌개",
    "M7-01": "수능 날 아침 찹쌀떡을 쪄주셨다",
    # 예전 필수(개편 뒤에는 선택)
    "C-05": "가족을 위해 희생하며 사랑으로 살아오신 분",
    "C-10": ["돈·재산·상속 이야기"],
    "unknown-field": "버려져야 함",
}


def test_signup_rules():
    with TestClient(app) as client:
        _signup(client, "rules@example.com")
        dup = client.post(
            "/api/v1/auth/signup",
            json={"email": "rules@example.com", "password": "password123", "name": "x"},
        )
        assert dup.status_code == 409
        short = client.post(
            "/api/v1/auth/signup",
            json={"email": "short@example.com", "password": "1234", "name": "x"},
        )
        assert short.status_code == 422


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 필요")
def test_full_persona_flow(fake_ai: FakeAI):
    with TestClient(app) as client:
        h = _signup(client, "flow@example.com")
        assert "C-01" in {
            q["id"] for q in client.get("/api/v1/intake/meta").json()["data"]["questions"]
        }

        created = client.post("/api/v1/personas", headers=h, json=BASICS)
        assert created.status_code == 201, created.text
        pid = created.json()["data"]["id"]
        assert created.json()["data"]["lifeStatus"] == "DECEASED"

        # 녹음 올리기
        bad = client.post(
            f"/api/v1/personas/{pid}/recordings",
            headers=h,
            data={"pledge": "true"},
            files=[("files", ("memo.txt", b"hi", "text/plain"))],
        )
        assert bad.status_code == 400
        up = client.post(
            f"/api/v1/personas/{pid}/recordings",
            headers=h,
            data={"pledge": "true"},
            files=[
                ("files", ("통화01.wav", _wav(), "audio/wav")),
                ("files", ("통화02.wav", _wav(), "audio/wav")),
                ("files", ("설날_영상.mp4", _video(), "video/mp4")),
                ("files", ("지울것.wav", _wav(), "audio/wav")),
            ],
        )
        assert up.status_code == 200, up.text
        recs = up.json()["data"]
        client.delete(f"/api/v1/personas/{pid}/recordings/{recs[3]['id']}", headers=h)

        # 소리 뽑기 → 전사 → 목소리 묶기
        assert client.post(f"/api/v1/personas/{pid}/transcribe", headers=h).status_code == 200
        _wait(client, h, pid, "TRANSCRIBE")
        detail = client.get(f"/api/v1/personas/{pid}", headers=h).json()["data"]
        assert detail["step"] == "SPEAKERS"
        assert [r["status"] for r in detail["recordings"]] == ["TRANSCRIBED"] * 3
        # 녹음이 3개여도 목소리는 2개. 대화 내용으로 짐작한 그분 목소리를 미리 골라 둔다
        voices = detail["voices"]
        assert [v["voiceKey"] for v in voices] == ["VOICE_1", "VOICE_2"]
        assert voices[0]["suggested"] and len(voices[0]["recordings"]) == 3
        assert detail["subjectVoices"] == ["VOICE_1"]
        assert voices[0]["lines"]
        # AI 서버가 받는 녹음은 영상이 아니라 뽑아낸 소리(m4a)다
        sent = fake_ai.calls["/v1/analysis/transcriptions"]
        assert all(call["audioMimeType"] == "audio/mp4" for call in sent)
        got = client.get(sent[2]["audioUrl"].replace("http://localhost:8000", ""))
        assert got.status_code == 200 and got.content[4:8] == b"ftyp"  # mp4 컨테이너의 소리 파일
        # 목소리마다 미리듣기 클립이 있고, 서명 주소로만 열린다
        preview = voices[0]["previewUrl"].replace("http://localhost:8000", "")
        heard = client.get(preview)
        assert heard.status_code == 200 and len(heard.content) > 1000
        assert client.get(preview.replace("sig=", "sig=0")).status_code == 403

        # 목소리 고르기 → 분석이 뒤에서 돈다
        assert (
            client.post(
                f"/api/v1/personas/{pid}/speakers", headers=h, json={"voiceKeys": []}
            ).status_code
            == 400
        )
        assert (
            client.post(
                f"/api/v1/personas/{pid}/speakers", headers=h, json={"voiceKeys": ["VOICE_9"]}
            ).status_code
            == 400
        )
        assert (
            client.post(
                f"/api/v1/personas/{pid}/speakers", headers=h, json={"voiceKeys": ["VOICE_1"]}
            ).status_code
            == 200
        )
        _wait(client, h, pid, "ANALYZE")
        analysed = fake_ai.calls["/v1/analysis/recording"]
        assert len(analysed) == 3 and analysed[0]["subjectSpeakerLabel"] == "SUBJECT"
        labels = {seg["speakerLabel"] for seg in analysed[0]["transcriptSegments"]}
        assert labels == {"SUBJECT", "VOICE_2"}  # 다른 사람도 녹음 사이에서 같은 이름
        phrases = client.get(f"/api/v1/personas/{pid}/phrase-suggestions", headers=h).json()["data"]
        assert phrases[0] == "밥은 먹었냐"

        # 질문 → 만들기
        assert (
            client.put(
                f"/api/v1/personas/{pid}/answers", headers=h, json={"C-01": "한두 문장으로 짧게"}
            ).status_code
            == 200
        )
        # 돌아가신 분은 신청 자격·유족 명단을 먼저 적어야 만든다(2026-10-22 법률 자문)
        undeclared = client.post(
            f"/api/v1/personas/{pid}/build",
            headers=h,
            json={"answers": ANSWERS, "cloneVoice": True, "voiceConsent": True},
        )
        assert undeclared.status_code == 409 and undeclared.json()["code"] == "bereaved"
        declare_bereaved(client, pid)
        missing = client.post(
            f"/api/v1/personas/{pid}/build",
            headers=h,
            json={"answers": {"C-01": "한두 문장으로 짧게"}},
        )
        assert missing.status_code == 400
        # 기본 목소리는 서류 없이 동의만 받는다. 동의가 없으면 만들지 않는다
        no_consent = client.post(
            f"/api/v1/personas/{pid}/build",
            headers=h,
            json={"answers": ANSWERS, "cloneVoice": True},
        )
        assert no_consent.status_code == 400 and "동의" in no_consent.json()["detail"]
        built = client.post(
            f"/api/v1/personas/{pid}/build",
            headers=h,
            json={"answers": ANSWERS, "cloneVoice": True, "voiceConsent": True},
        )
        assert built.status_code == 200, built.text
        job = _wait(client, h, pid, "BUILD")
        assert job["detail"] == "완료"
        detail = client.get(f"/api/v1/personas/{pid}", headers=h).json()["data"]
        assert detail["step"] == "READY" and detail["hasVoice"]
        assert "unknown-field" not in detail["answers"]

        assembly = fake_ai.calls["/v1/persona/assembly"][0]
        intake_ctx = assembly["intakeContext"]
        assert intake_ctx["basicProfile"]["status"] == "사망"
        assert assembly["speechExamples"][0] == "몸조심해라"  # 직접 적은 말이 앞
        assert {
            "topic": "돈·재산·상속 이야기",
            "grade": "금지",
            "sourceQuestion": "C-10",
        } in intake_ctx["tabooTopics"]
        assert any(
            t["topic"] == "건강·병" and t["grade"] == "물으면만" for t in intake_ctx["tabooTopics"]
        )
        # 성격 정리는 운영 조립기(layered)가 쓰지 않아 부르지 않는다(PERSONA_REFLECTION 기본 꺼짐)
        assert assembly["personaInsights"] == []
        assert "/v1/persona/reflection" not in fake_ai.calls
        # 새 목소리는 늘 Fish drama-3 로 만든다(통화 녹음이어도 같다)
        assert fake_ai.calls["/v1/voice/clone"][0]["provider"] == "fish"
        assert (
            client.get(f"/api/v1/personas/{pid}", headers=h).json()["data"]["voiceProvider"]
            == "fish"
        )
        samples = fake_ai.calls["/v1/voice/clone"][0]["samples"]
        assert len(samples) == 9  # 녹음 3개의 그분 구간 3개씩 (손자 목소리는 섞이지 않는다)

        assert _memory_sources(pid) == {"RECORDING", "INTAKE"}

        # 대화
        conv = client.post(f"/api/v1/personas/{pid}/conversation", headers=h).json()["data"]
        again = client.post(f"/api/v1/personas/{pid}/conversation", headers=h).json()["data"]
        assert conv == again
        cid = conv["conversationId"]
        for text in ("할머니 보고 싶어요", "오늘 일찍 잘게요"):
            sent = client.post(
                f"/api/v1/conversations/{cid}/messages/text", headers=h, json={"content": text}
            )
            assert sent.status_code == 200, sent.text
        last = fake_ai.calls["/v1/persona/responses"][-1]
        assert last["persona"]["instructions"] == json.dumps(assembly, ensure_ascii=False)
        assert "fixedPrompt" not in last["persona"]  # 만든 페르소나는 중간계층을 그대로 쓴다
        assert [m["content"] for m in last["history"]] == [
            "할머니 보고 싶어요",
            "그래, 밥은 먹었냐?",
        ]
        # 녹음 3개 + 적어준 답 2개(함께한 장면 M7-01·떠오르는 것 M6-08), 벡터 검색으로 고름
        assert len(last["memories"]) == 5
        # 기억 검색 질문에도 앞 대화를 붙인다(AI 서버 QUERY_REWRITE).
        # 첫 말은 앞 대화가 없어 예전 요청 그대로다
        queries = fake_ai.calls["/v1/embeddings/query"]
        assert queries[-2] == {"text": "할머니 보고 싶어요"}
        assert queries[-1] == {"text": "오늘 일찍 잘게요", "history": last["history"]}

        reply_id = sent.json()["data"]["assistantMessage"]["id"]
        speech = client.post(f"/api/v1/messages/{reply_id}/speech", headers=h)
        assert speech.status_code == 200
        audio = client.get(speech.json()["data"]["audioUrl"].replace("http://localhost:8000", ""))
        assert audio.content == b"ID3fake"
        # 대답 LLM 이 고른 감정이 음성까지 가고, 고인 여부도 넘어간다
        assert last["persona"]["deceased"] is True
        assert fake_ai.calls["/v1/voice/speech"][-1]["emotion"] == "calm"
        assert fake_ai.calls["/v1/voice/speech"][-1]["voiceId"] == fake_ai.voice(1)

        # 목소리만 다시 만들기: 새 목소리로 바뀌고, 예전 목소리로 만든 대답 음성은 다시 만든다
        assert client.post(f"/api/v1/personas/{pid}/voice", headers=h).status_code == 200
        _wait(client, h, pid, "VOICE")
        assert len(fake_ai.calls["/v1/voice/clone"]) == 2
        assert fake_ai.calls["/v1/voice/clone"][1]["provider"] == "fish"
        assert client.post(f"/api/v1/messages/{reply_id}/speech", headers=h).status_code == 200
        assert fake_ai.calls["/v1/voice/speech"][-1]["voiceId"] == fake_ai.voice(2)
        assert len(fake_ai.calls["/v1/voice/speech"]) == 2
        # 예전 목소리는 바꾼 뒤에 Fish 에서 지운다
        assert fake_ai.calls["/v1/voice/delete"][-1] == {
            "voiceId": fake_ai.voice(1),
            "provider": "fish",
        }

        # 정보 고치기: 지침과 기억을 뒤에서 다시 만들고, 고인 여부는 바로 바뀐다.
        edited = BASICS | {"name": "김영희", "status": "alive", "calledMe": "우리 강아지"}
        answers = {k: v for k, v in ANSWERS.items() if k != "unknown-field"}
        answers["C-09"] = ["밥 잘 챙겨 먹어라"]
        # 다 만든 페르소나는 꼭 필요한 답을 비울 수 없다
        bad = client.put(
            f"/api/v1/personas/{pid}",
            headers=h,
            json={"basics": edited, "answers": {"C-09": ["x"]}},
        )
        assert bad.status_code == 400
        saved = client.put(
            f"/api/v1/personas/{pid}", headers=h, json={"basics": edited, "answers": answers}
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["data"]["updating"] is True
        assert saved.json()["data"]["name"] == "김영희"
        _wait(client, h, pid, "UPDATE")
        assert len(fake_ai.calls["/v1/persona/assembly"]) == 2
        rebuilt = fake_ai.calls["/v1/persona/assembly"][1]
        assert rebuilt["speechExamples"][0] == "밥 잘 챙겨 먹어라"
        assert "김영희" in json.dumps(rebuilt["subjectContext"], ensure_ascii=False)
        assert len(fake_ai.calls["/v1/voice/clone"]) == 2  # 목소리는 다시 만들지 않는다
        detail = client.get(f"/api/v1/personas/{pid}", headers=h).json()["data"]
        assert detail["step"] == "READY" and detail["lifeStatus"] == "LIVING"
        assert detail["basics"]["calledMe"] == "우리 강아지"
        assert _memory_sources(pid) == {"RECORDING", "INTAKE"}
        # 같은 내용을 다시 저장하면 다시 만들지 않는다
        same = client.put(
            f"/api/v1/personas/{pid}", headers=h, json={"basics": edited, "answers": answers}
        )
        assert same.json()["data"]["updating"] is False
        # 대화는 새 지침으로, 고인이 아닌 분으로
        client.post(
            f"/api/v1/conversations/{cid}/messages/text", headers=h, json={"content": "엄마 나야"}
        )
        last = fake_ai.calls["/v1/persona/responses"][-1]
        assert last["persona"]["instructions"] == json.dumps(rebuilt, ensure_ascii=False)
        assert last["persona"]["deceased"] is False

        # 남의 페르소나는 안 보인다
        other = _signup(client, "other@example.com")
        assert client.get(f"/api/v1/personas/{pid}", headers=other).status_code == 404
        assert client.post(f"/api/v1/messages/{reply_id}/speech", headers=other).status_code == 404
        assert client.get("/api/v1/personas", headers=other).json()["data"] == []

        removed = client.delete(f"/api/v1/personas/{pid}", headers=h)
        assert removed.status_code == 200
        # 지운 뒤 알림: 실제로 지운 곳만 '지웠어요', 서버 백업이 지워지는 날(SPEC §9)
        assert removed.json()["data"]["deleted"] is True
        assert "서버 백업에는" in removed.json()["data"]["message"]
        assert client.get(f"/api/v1/personas/{pid}", headers=h).status_code == 404
        assert client.get(f"/api/v1/conversations/{cid}", headers=h).status_code == 404


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 필요")
def test_speech_never_falls_back_to_another_voice(fake_ai: FakeAI):
    """목소리를 복제하지 않은 페르소나는 소리를 만들지 않는다(기본 목소리로 새지 않게)."""
    with TestClient(app) as client:
        h = _signup(client, "novoice@example.com")
        persona = client.get("/api/v1/personas", headers=h).json()["data"]
        assert persona == []
        pid = client.post("/api/v1/personas", headers=h, json=BASICS).json()["data"]["id"]
        client.post(
            f"/api/v1/personas/{pid}/recordings",
            headers=h,
            data={"pledge": "true"},
            files=[("files", ("a.wav", _wav(), "audio/wav"))],
        )
        client.post(f"/api/v1/personas/{pid}/transcribe", headers=h)
        _wait(client, h, pid, "TRANSCRIBE")
        client.post(f"/api/v1/personas/{pid}/speakers", headers=h, json={"voiceKeys": ["VOICE_1"]})
        declare_bereaved(client, pid)
        client.post(f"/api/v1/personas/{pid}/build", headers=h, json={"answers": ANSWERS})
        _wait(client, h, pid, "BUILD")
        cid = client.post(f"/api/v1/personas/{pid}/conversation", headers=h).json()["data"][
            "conversationId"
        ]
        reply = client.post(
            f"/api/v1/conversations/{cid}/messages/text", headers=h, json={"content": "안녕"}
        ).json()["data"]["assistantMessage"]["id"]
        assert client.post(f"/api/v1/messages/{reply}/speech", headers=h).status_code == 409
        assert "/v1/voice/speech" not in fake_ai.calls


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 필요")
async def test_phone_band_detection():
    """전화망처럼 300~3400Hz 만 남은 소리는 통화 녹음으로, 넓은 대역 소리는 일반 녹음으로 본다."""
    from app.services import audio

    def noise(af: str) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "n.wav"
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-y",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "anoisesrc=d=5:c=pink:r=44100:a=0.3",
                    "-af",
                    af,
                    str(out),
                ],
                check=True,
            )
            return out.read_bytes()

    phone, _ = await audio.extract_audio(
        noise("highpass=f=300,lowpass=f=3400,lowpass=f=3400,aresample=8000"), ".wav"
    )
    wide, _ = await audio.extract_audio(noise("anull"), ".wav")
    assert await audio.is_phone_band(phone) is True
    assert await audio.is_phone_band(wide) is False


def test_edit_basics_before_ready(fake_ai: FakeAI):
    """만들기 중간에도 기본 정보를 고칠 수 있다. 아직 지침이 없으니 다시 만들 일은 없다."""
    with TestClient(app) as client:
        h = _signup(client, "edit-early@example.com")
        pid = client.post("/api/v1/personas", headers=h, json=BASICS).json()["data"]["id"]
        saved = client.put(
            f"/api/v1/personas/{pid}",
            headers=h,
            json={
                "basics": BASICS | {"relation": "외할머니", "addressTerm": ""},
                "answers": ANSWERS,
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["data"]["updating"] is False
        detail = client.get(f"/api/v1/personas/{pid}", headers=h).json()["data"]
        assert detail["relation"] == "외할머니"
        assert detail["addressTerm"] == "외할머니"  # 호칭을 비우면 관계로
        # 녹음 올리는 단계에서는 답을 받지 않는다(서버가 미리 채운 조심할 이야기 등급만 보인다)
        assert detail["answers"] == {"M9-01-death": "물으면만"}
        # 부르던 말(calledMe)이 기본 정보에 있어 P1-07A 는 꼭 받을 질문에서 빠진다
        assert "P1-07A" not in detail["requiredQuestions"]
        assert detail["basics"]["intakeVersion"] == 2  # 정보를 고쳐도 질문 차례는 그대로
        empty = client.put(
            f"/api/v1/personas/{pid}", headers=h, json={"basics": BASICS | {"name": " "}}
        )
        assert empty.status_code == 400
        other = _signup(client, "edit-other@example.com")
        assert (
            client.put(
                f"/api/v1/personas/{pid}", headers=other, json={"basics": BASICS}
            ).status_code
            == 404
        )


def test_clone_candidates_skip_crosstalk_and_laughter(monkeypatch: pytest.MonkeyPatch):
    """남의 말이 겹치거나 바로 붙은 구간, 웃음 구간, 짧은 토막은 복제 후보에서 뺀다."""
    from types import SimpleNamespace

    from app.services import persona_builder

    def seg(start, end, voice, text="말씀"):
        return SimpleNamespace(
            start_ms=start, end_ms=end, voice_key=voice, transcript_text=text, corrected_text=None
        )

    rec = SimpleNamespace(
        storage_key="k",
        segments=[
            seg(0, 6000, "VOICE_1"),  # 깨끗
            seg(6500, 9000, "VOICE_2"),  # 손자
            seg(9050, 14000, "VOICE_1"),  # 손자 말에 바로 붙음
            seg(20000, 26000, "VOICE_1", "(웃음) 아이고"),  # 웃음
            seg(30000, 31000, "VOICE_1"),  # 너무 짧음
            seg(40000, 47000, "VOICE_1"),  # 깨끗
        ],
    )
    storage = SimpleNamespace(signed_url=lambda key, ttl: f"https://media/{key}")
    monkeypatch.setattr(persona_builder, "get_storage", lambda: storage)
    samples = persona_builder._clone_samples([rec], {"VOICE_1"})
    assert [(s["startMs"], s["endMs"]) for s in samples] == [(40000, 47000), (0, 6000)]

    # 깨끗한 구간만으로 10초가 안 되면 그분 구간 전부를 쓴다
    rec.segments = rec.segments[:3]
    samples = persona_builder._clone_samples([rec], {"VOICE_1"})
    assert {(s["startMs"], s["endMs"]) for s in samples} == {(0, 6000), (9050, 14000)}


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 필요")
def test_back_to_upload_adds_recordings_without_retranscribing(fake_ai: FakeAI):
    """목소리 고르기에서 녹음을 더 올리러 돌아가면, 새 녹음만 전사하고 목소리를 다시 묶는다."""
    with TestClient(app) as client:
        h = _signup(client, "more@example.com")
        pid = client.post("/api/v1/personas", headers=h, json=BASICS).json()["data"]["id"]
        client.post(
            f"/api/v1/personas/{pid}/recordings",
            headers=h,
            data={"pledge": "true"},
            files=[("files", ("통화01.wav", _wav(), "audio/wav"))],
        )
        # 녹음 올리는 단계에서는 돌아갈 곳이 없다
        assert client.post(f"/api/v1/personas/{pid}/back-to-upload", headers=h).status_code == 409
        client.post(f"/api/v1/personas/{pid}/transcribe", headers=h)
        _wait(client, h, pid, "TRANSCRIBE")
        assert len(fake_ai.calls["/v1/analysis/transcriptions"]) == 1

        back = client.post(f"/api/v1/personas/{pid}/back-to-upload", headers=h)
        assert back.status_code == 200, back.text
        detail = client.get(f"/api/v1/personas/{pid}", headers=h).json()["data"]
        assert detail["step"] == "UPLOAD" and len(detail["recordings"]) == 1

        client.post(
            f"/api/v1/personas/{pid}/recordings",
            headers=h,
            data={"pledge": "true"},
            files=[("files", ("통화02.wav", _wav(), "audio/wav"))],
        )
        client.post(f"/api/v1/personas/{pid}/transcribe", headers=h)
        _wait(client, h, pid, "TRANSCRIBE")
        assert len(fake_ai.calls["/v1/analysis/transcriptions"]) == 2  # 새 녹음 하나만 더
        detail = client.get(f"/api/v1/personas/{pid}", headers=h).json()["data"]
        assert detail["step"] == "SPEAKERS"
        assert len(detail["voices"][0]["recordings"]) == 2  # 두 녹음의 같은 목소리를 다시 묶음
        assert detail["subjectVoices"] == ["VOICE_1"]


def test_remaking_voice_asks_for_consent_once(fake_ai: FakeAI):  # noqa: F811
    """예전에 목소리 없이 만든 페르소나도 '목소리 만들기' 때 동의를 받는다(거둔 동의도 다시)."""
    with TestClient(app) as client:
        h = _signup(client, "remake-consent@example.com")
        pid = client.post("/api/v1/personas", headers=h, json=BASICS).json()["data"]["id"]
        client.post(
            f"/api/v1/personas/{pid}/recordings",
            headers=h,
            data={"pledge": "true"},
            files=[("files", ("통화01.wav", _wav(), "audio/wav"))],
        )
        client.post(f"/api/v1/personas/{pid}/transcribe", headers=h)
        _wait(client, h, pid, "TRANSCRIBE")
        client.post(f"/api/v1/personas/{pid}/speakers", headers=h, json={"voiceKeys": ["VOICE_1"]})
        _wait(client, h, pid, "ANALYZE")
        # 예전처럼 목소리 없이 만든 페르소나(동의 기록 없음)
        declare_bereaved(client, pid)
        built = client.post(f"/api/v1/personas/{pid}/build", headers=h, json={"answers": ANSWERS})
        assert built.status_code == 200, built.text
        _wait(client, h, pid, "BUILD")
        detail = client.get(f"/api/v1/personas/{pid}", headers=h).json()["data"]
        assert detail["voiceConsented"] is False and not detail["hasVoice"]
        refused = client.post(f"/api/v1/personas/{pid}/voice", headers=h)
        assert refused.status_code == 400 and "동의" in refused.json()["detail"]
        # 동의를 거둔 줄이 있어도(같은 종류는 한 줄) 다시 동의하면 된다
        client.post(
            "/api/v1/consents",
            headers=h,
            json={"consentType": f"voice_clone:{pid}", "agreed": False, "version": "1.0"},
        )
        ok = client.post(f"/api/v1/personas/{pid}/voice", headers=h, json={"voiceConsent": True})
        assert ok.status_code == 200, ok.text
        _wait(client, h, pid, "VOICE")
        detail = client.get(f"/api/v1/personas/{pid}", headers=h).json()["data"]
        assert detail["voiceConsented"] is True and detail["hasVoice"]
        # 한 번 동의했으면 다시 묻지 않는다
        assert client.post(f"/api/v1/personas/{pid}/voice", headers=h).status_code == 200


def _hold_analysis(monkeypatch: pytest.MonkeyPatch):
    """녹음 살펴보기(analyze_recording)를 풀어 줄 때까지 붙잡아 둔다. 보낸 화자 이름을 모은다."""
    import threading

    from app.services import persona_builder

    release = threading.Event()
    seen: list[set[str]] = []
    real = persona_builder.ai_service.analyze_recording

    async def held(**kwargs):
        seen.append({seg["speakerLabel"] for seg in kwargs["transcriptSegments"]})
        while not release.is_set():
            await asyncio.sleep(0.02)
        return await real(**kwargs)

    monkeypatch.setattr(persona_builder.ai_service, "analyze_recording", held)
    return release, seen


def _to_speakers(client: TestClient, h: dict) -> str:
    pid = client.post("/api/v1/personas", headers=h, json=BASICS).json()["data"]["id"]
    client.post(
        f"/api/v1/personas/{pid}/recordings",
        headers=h,
        data={"pledge": "true"},
        files=[("files", ("통화01.wav", _wav(), "audio/wav")),
               ("files", ("통화02.wav", _wav(), "audio/wav"))],
    )
    assert client.post(f"/api/v1/personas/{pid}/transcribe", headers=h).status_code == 200
    _wait(client, h, pid, "TRANSCRIBE")
    return pid


def _analyses(pid: str) -> list:
    async def go():
        async with SessionLocal() as db:
            from app.db.models import Recording

            rows = await db.scalars(select(Recording).where(Recording.persona_id == pid))
            return [r.analysis for r in rows]

    return asyncio.run(go())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 필요")
def test_choose_voice_again_while_analysing(fake_ai: FakeAI, monkeypatch: pytest.MonkeyPatch):
    """골랐다가 돌아와 다시 골라도 '살펴보는 중'이라고 막지 않는다(2026-10-18 넘어가지 못했다).

    같은 목소리면 그대로 넘어가고, 다른 목소리면 돌던 살펴보기가 예전 결과를 버리고 새 목소리로
    다시 한다.
    """
    release, seen = _hold_analysis(monkeypatch)
    with TestClient(app) as client:
        h = _signup(client, f"again-{uuid4().hex[:6]}@example.com")
        pid = _to_speakers(client, h)
        choose = f"/api/v1/personas/{pid}/speakers"
        back = f"/api/v1/personas/{pid}/back-to-speakers"
        assert client.post(choose, headers=h, json={"voiceKeys": ["VOICE_1"]}).status_code == 200
        assert client.post(back, headers=h).status_code == 200
        again = client.post(choose, headers=h, json={"voiceKeys": ["VOICE_1"]})
        assert again.status_code == 200, again.text
        detail = client.get(f"/api/v1/personas/{pid}", headers=h).json()["data"]
        assert detail["step"] == "QUESTIONS" and detail["jobs"]["ANALYZE"]["status"] == "RUNNING"

        # 이번에는 다른 목소리로 바꾼다. 새 일을 따로 세우지 않고, 돌던 일이 알아채고 다시 한다
        assert client.post(back, headers=h).status_code == 200
        other = client.post(choose, headers=h, json={"voiceKeys": ["VOICE_2"]})
        assert other.status_code == 200, other.text
        release.set()
        _wait(client, h, pid, "ANALYZE")

        assert seen[0] == {"SUBJECT", "VOICE_2"}  # 처음 고른 VOICE_1 이 그분
        redo = seen[1:]
        assert redo and all(labels == {"SUBJECT", "VOICE_1"} for labels in redo)
        analyses = _analyses(pid)
        assert len(redo) == len(analyses) and all(analyses)
        detail = client.get(f"/api/v1/personas/{pid}", headers=h).json()["data"]
        assert detail["subjectVoices"] == ["VOICE_2"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 필요")
def test_back_to_upload_while_analysing(fake_ai: FakeAI, monkeypatch: pytest.MonkeyPatch):
    """살펴보는 중에도 녹음 올리기로 돌아갈 수 있다. 돌던 살펴보기는 결과를 남기지 않고 끝난다."""
    release, seen = _hold_analysis(monkeypatch)
    with TestClient(app) as client:
        h = _signup(client, f"backup-{uuid4().hex[:6]}@example.com")
        pid = _to_speakers(client, h)
        client.post(f"/api/v1/personas/{pid}/speakers", headers=h, json={"voiceKeys": ["VOICE_1"]})
        back = client.post(f"/api/v1/personas/{pid}/back-to-upload", headers=h)
        assert back.status_code == 200, back.text
        release.set()
        _wait(client, h, pid, "ANALYZE")
        assert len(seen) == 1
        assert not any(_analyses(pid))
        assert client.get(f"/api/v1/personas/{pid}", headers=h).json()["data"]["step"] == "UPLOAD"
