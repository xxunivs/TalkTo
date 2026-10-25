"""AI 회사(OpenAI) 크레딧이 떨어지면: 고객에게는 차분한 문장, 관리자 화면에는 경고, 답은 그대로."""

import time

import httpx
import pytest
from fastapi.testclient import TestClient
from test_billing import _admin, _ready_persona
from test_persona_setup import BASICS, FakeAI, _signup, fake_ai  # noqa: F401

from app.main import app
from app.services import ai as ai_module

API = "/api/v1"
QUOTA = {"error": {"code": "AI_PROVIDER_QUOTA", "message": "AI provider unavailable"}}


@pytest.fixture
def provider_down(fake_ai: FakeAI, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    """대화·지침 조립(OpenAI 를 쓰는 곳)만 503 으로 막는다. 막힌 경로는 down 에서 고친다."""
    down = {"/v1/persona/assembly", "/v1/persona/responses"}
    real = httpx._client.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in down:
            return httpx.Response(503, json=QUOTA)
        return fake_ai(request)

    monkeypatch.setattr(
        ai_module.httpx,
        "AsyncClient",
        lambda *a, **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    ai_module.provider_state.update(code=None, at=None, ok_at=None)
    yield down
    ai_module.provider_state.update(code=None, at=None, ok_at=None)


def _job(client, h, pid, kind):
    for _ in range(200):
        job = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["jobs"].get(kind)
        if job and job["status"] != "RUNNING":
            return job
        time.sleep(0.1)
    raise AssertionError(f"{kind} did not finish")


def test_quota_stops_learning_and_chat_and_warns_admin(provider_down):
    with TestClient(app) as client:
        h = _signup(client, "provider-down@example.com")
        down = set(provider_down)
        provider_down.clear()  # 만들 때는 정상
        pid = _ready_persona(client, h)
        provider_down.update(down)

        detail = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]
        answers = {**detail["answers"], "M3-01": "우리 강아지니?"}
        saved = client.put(
            f"{API}/personas/{pid}", headers=h, json={"basics": BASICS, "answers": answers}
        )
        assert saved.status_code == 200 and saved.json()["data"]["updating"] is True
        job = _job(client, h, pid, "UPDATE")
        assert job["status"] == "FAILED" and job["error"] == ai_module.AI_DOWN
        # 답은 그대로 저장돼 있다
        kept = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["answers"]
        assert kept["M3-01"] == "우리 강아지니?"

        cid = client.post(f"{API}/personas/{pid}/conversation", headers=h).json()["data"][
            "conversationId"
        ]
        sent = client.post(
            f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": "할머니"}
        )
        assert sent.status_code == 503 and sent.json()["detail"] == ai_module.AI_DOWN

        stats = client.get(f"{API}/admin/stats", headers=_admin(client)).json()["data"]
        assert stats["aiProviderTrouble"]["code"] == "AI_PROVIDER_QUOTA"

        # 충전한 뒤 다시 익히면 경고가 사라진다
        provider_down.clear()
        # '다시 익히기' = 같은 답을 다시 저장(앞서 실패했으면 다시 익힌다)
        again = client.put(
            f"{API}/personas/{pid}", headers=h, json={"basics": BASICS, "answers": kept}
        )
        assert again.status_code == 200 and again.json()["data"]["updating"] is True
        assert _job(client, h, pid, "UPDATE")["status"] == "DONE"
        stats = client.get(f"{API}/admin/stats", headers=_admin(client)).json()["data"]
        assert stats["aiProviderTrouble"] is None
