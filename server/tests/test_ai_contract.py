import json

import httpx
import pytest

from app.core.config import Settings
from app.services import ai as ai_module


def configured_settings() -> Settings:
    return Settings(
        _env_file=None,
        ai_server_url="https://ai.example.test/",
        ai_server_token="shared-secret",
    )


@pytest.mark.asyncio
async def test_chat_matches_ai_server_contract(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "content": "밥은 먹었냐?",
                "retrievedMemoryIds": ["memory-1"],
                "provider": "openai",
                "model": "test-model",
            },
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr(ai_module, "get_settings", configured_settings)
    monkeypatch.setattr(ai_module.httpx, "AsyncClient", client_factory)

    content, memory_ids, latency_ms, _emotion, speech_text = await ai_module.ai_service.chat(
        message="안녕하세요",
        history=[{"role": "user", "content": "보고 싶어요"}],
        memories=[
            {
                "id": "memory-1",
                "title": "저녁 식사",
                "content": "함께 저녁을 먹었다.",
                "tags": ["가족"],
            }
        ],
        persona={
            "subject_id": "person-1",
            "name": "할머니",
            "description": "따뜻하고 다정하다.",
            "speaking_style": "짧고 담백하게 말한다.",
            "voice_id": "voice-1",
        },
    )

    request = captured["request"]
    assert isinstance(request, httpx.Request)
    assert request.url == "https://ai.example.test/v1/persona/responses"
    assert request.headers["X-AI-Server-Token"] == "shared-secret"
    assert captured["json"] == {
        "message": "안녕하세요",
        "history": [{"role": "user", "content": "보고 싶어요"}],
        "memories": [
            {
                "id": "memory-1",
                "title": "저녁 식사",
                "content": "함께 저녁을 먹었다.",
                "tags": ["가족"],
            }
        ],
        "persona": {
            "subjectId": "person-1",
            "instructions": "이름: 할머니\n설명: 따뜻하고 다정하다.\n말투: 짧고 담백하게 말한다.",
            "voiceId": "voice-1",
            "deceased": None,
        },
    }
    assert content == "밥은 먹었냐?"
    assert memory_ids == ["memory-1"]
    assert isinstance(latency_ms, int)
    assert speech_text is None  # 예전 AI 서버(또는 FISH_EMOTION 꺼짐)는 speechText 를 보내지 않는다


@pytest.mark.asyncio
async def test_chat_reads_optional_speech_text(monkeypatch: pytest.MonkeyPatch):
    """AI PersonaResponseResult.speech_text(speechText, 선택). Fish FISH_EMOTION=inline 일 때만
    온다.

    있으면 그대로, 없거나 null·빈 글이면 None(대답을 그대로 읽는다). 감정(emotion)도 같이 읽는다.
    """
    answers = [
        {"speechText": "[웃으며] 밥은 먹었냐?", "emotion": None},
        {"speechText": None, "emotion": "warm"},
        {"speechText": ""},
        {},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        extra = answers.pop(0)
        return httpx.Response(
            200,
            json={"content": "밥은 먹었냐?", "retrievedMemoryIds": [], "provider": "x",
                  "model": "y", **extra},
        )  # fmt: skip

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(ai_module, "get_settings", configured_settings)
    monkeypatch.setattr(
        ai_module.httpx, "AsyncClient", lambda *a, **kw: real_client(transport=transport, **kw)
    )
    persona = {"subject_id": "person-1", "name": "할머니", "instructions": "지침"}
    got = []
    for _ in range(4):
        content, _ids, _ms, emotion, speech_text = await ai_module.ai_service.chat(
            message="할머니", history=[], memories=[], persona=persona
        )
        assert content == "밥은 먹었냐?"  # 화면 글에는 연기 표시가 없다
        got.append((emotion, speech_text))
    assert got == [
        (None, "[웃으며] 밥은 먹었냐?"),
        ("warm", None),
        (None, None),
        (None, None),
    ]


@pytest.mark.asyncio
async def test_chat_sends_speaker_hint_only_when_given(monkeypatch: pytest.MonkeyPatch):
    """함께 쓰는 가족이 말할 때만 speakerHint(AI PersonaResponseRequest.speaker_hint)를 싣는다.

    주인이 말할 때는 싣지 않아 요청이 예전과 같다(위 시험).
    """
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"content": "왔구나.", "retrievedMemoryIds": [], "provider": "x", "model": "y"},
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr(ai_module, "get_settings", configured_settings)
    monkeypatch.setattr(ai_module.httpx, "AsyncClient", client_factory)
    persona = {"subject_id": "person-1", "name": "할머니", "instructions": "가족 지침"}
    for hint in ("철수", None, ""):
        await ai_module.ai_service.chat(
            message="엄마", history=[], memories=[], persona=persona, speaker_hint=hint
        )
    assert bodies[0]["speakerHint"] == "철수"
    assert "persona" in bodies[0] and "speakerHint" not in bodies[0]["persona"]
    assert "speakerHint" not in bodies[1] and "speakerHint" not in bodies[2]
    assert bodies[1] == bodies[2]


@pytest.mark.asyncio
async def test_reply_sends_memory_total_when_counted(monkeypatch: pytest.MonkeyPatch):
    """memoryTotal(AI PersonaResponseRequest.memory_total, 선택): 그분 기억 총수. 0 도 정수로
    싣는다(기억이 하나도 없는 페르소나). None 이면 칸을 빼서 예전 요청과 같다(위 시험)."""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"content": "왔구나.", "retrievedMemoryIds": [], "provider": "x", "model": "y"},
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(ai_module, "get_settings", configured_settings)
    monkeypatch.setattr(
        ai_module.httpx, "AsyncClient", lambda *a, **kw: real_client(transport=transport, **kw)
    )
    persona = {"subject_id": "person-1", "name": "할머니", "instructions": "지침"}
    history = [
        {"role": "user", "content": "할머니 나 왔어"},
        {"role": "assistant", "content": "그래, 왔구나."},
    ]
    for total in (0, 7, None):
        await ai_module.ai_service.reply(
            message="밥 먹었어", history=history, memories=[], persona=persona, memory_total=total
        )
    await ai_module.ai_service.chat(
        message="밥 먹었어", history=history, memories=[], persona=persona, memory_total=3
    )
    assert bodies[0]["memoryTotal"] == 0 and type(bodies[0]["memoryTotal"]) is int
    assert bodies[1]["memoryTotal"] == 7
    assert "memoryTotal" not in bodies[2]
    assert bodies[3]["memoryTotal"] == 3  # chat() 도 넘긴다
    # 맨 위 칸이다(persona 안이 아니다). 앞 대화는 그대로 간다
    assert all("memoryTotal" not in body["persona"] for body in bodies)
    assert all(body["history"] == history for body in bodies)


@pytest.mark.asyncio
async def test_transcription_matches_ai_server_multipart_contract(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        captured["body"] = await request.aread()
        return httpx.Response(
            200,
            json={"text": "안녕하세요", "provider": "openai", "model": "whisper-1"},
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr(ai_module, "get_settings", configured_settings)
    monkeypatch.setattr(ai_module.httpx, "AsyncClient", client_factory)

    text = await ai_module.ai_service.transcribe("message.wav", b"RIFFaudio", "audio/wav")

    request = captured["request"]
    body = captured["body"]
    assert isinstance(request, httpx.Request)
    assert isinstance(body, bytes)
    assert request.url == "https://ai.example.test/v1/voice/transcriptions"
    assert request.headers["X-AI-Server-Token"] == "shared-secret"
    assert b'name="audio_file"; filename="message.wav"' in body
    assert b"Content-Type: audio/wav" in body
    assert b'name="language"' in body
    assert b"ko" in body
    assert text == "안녕하세요"


@pytest.mark.asyncio
async def test_query_embedding_sends_recent_history_only_when_there_is_some(
    monkeypatch: pytest.MonkeyPatch,
):
    """기억 검색 질문에는 앞 대화 최근 4개를 붙인다(AI 서버 QUERY_REWRITE 가 볼 것).

    앞 대화가 없으면 history 칸을 아예 보내지 않아 예전 요청과 같다.
    응답에 rewrittenQuery 가 와도 된다.
    """
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://ai.example.test/v1/embeddings/query"
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "provider": "openai",
                "model": "text-embedding-3-small",
                "dimensions": 2,
                "embedding": [0.1, 0.2],
                **({"rewrittenQuery": "된장찌개에 된장 얼마나 넣어?"} if len(bodies) > 1 else {}),
            },
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(ai_module, "get_settings", configured_settings)
    monkeypatch.setattr(
        ai_module.httpx, "AsyncClient", lambda *a, **kw: real_client(transport=transport, **kw)
    )

    assert await ai_module.ai_service.embed_query("고향이 어디야?") == [0.1, 0.2]
    history = [
        {"role": "user", "content": "할머니 된장찌개 어떻게 끓여?"},
        {"role": "assistant", "content": "멸치 육수 내고 된장 풀면 되지."},
        {"role": "user", "content": "두부도 넣어?"},
        {"role": "assistant", "content": "   "},  # 빈 말은 AI 서버가 거부하니 뺀다
        {"role": "assistant", "content": "그럼, 두부 넣지."},
        {"role": "user", "content": "호박은?"},
    ]
    vector, model, dims = await ai_module.ai_service.embed_query_detailed(
        "얼마나 넣어?", history=history
    )
    assert (vector, model, dims) == ([0.1, 0.2], "text-embedding-3-small", 2)
    assert bodies[0] == {"text": "고향이 어디야?"}
    assert bodies[1] == {
        "text": "얼마나 넣어?",
        "history": [
            {"role": "assistant", "content": "멸치 육수 내고 된장 풀면 되지."},
            {"role": "user", "content": "두부도 넣어?"},
            {"role": "assistant", "content": "그럼, 두부 넣지."},
            {"role": "user", "content": "호박은?"},
        ],
    }
    await ai_module.ai_service.embed_query("안녕", history=[])
    assert bodies[2] == {"text": "안녕"}
