from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app


def test_health_and_login():
    settings = get_settings()
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": settings.bootstrap_admin_email,
                "password": settings.bootstrap_admin_password,
            },
        )
        assert response.status_code == 200
        assert response.json()["data"]["bearerToken"]


def test_protected_route_requires_token():
    with TestClient(app) as client:
        assert client.get("/api/v1/auth/me").status_code == 401


def test_logout_revokes_access_and_refresh_tokens():
    settings = get_settings()
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={
                "email": settings.bootstrap_admin_email,
                "password": settings.bootstrap_admin_password,
            },
        )
        tokens = login.json()["data"]
        headers = {"Authorization": f"Bearer {tokens['bearerToken']}"}

        assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
        assert (
            client.post(
                "/api/v1/auth/refresh",
                json={"refreshToken": tokens["refreshToken"]},
            ).status_code
            == 401
        )


def test_primary_persona_conversation_memory_flow():
    settings = get_settings()
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={
                "email": settings.bootstrap_admin_email,
                "password": settings.bootstrap_admin_password,
            },
        )
        headers = {"Authorization": f"Bearer {login.json()['data']['bearerToken']}"}

        persona_response = client.get("/api/v1/personas/active", headers=headers)
        assert persona_response.status_code == 200
        persona = persona_response.json()["data"]

        conversation_response = client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"personaId": persona["id"], "channel": "TEXT", "title": "API test"},
        )
        assert conversation_response.status_code == 201
        conversation_id = conversation_response.json()["data"]["id"]

        message_response = client.post(
            f"/api/v1/conversations/{conversation_id}/messages/text",
            headers=headers,
            json={"content": "안녕하세요"},
        )
        assert message_response.status_code == 200
        assistant_id = message_response.json()["data"]["assistantMessage"]["id"]

        assert (
            client.post(
                f"/api/v1/messages/{assistant_id}/feedback",
                headers=headers,
                json={"rating": "UP", "tags": ["NATURAL"]},
            ).status_code
            == 200
        )

        memory_response = client.post(
            "/api/v1/memories",
            headers=headers,
            json={
                "personId": persona["personId"],
                "title": "테스트 기억",
                "bodyMarkdown": "함께 나눈 기억",
            },
        )
        assert memory_response.status_code == 201
        assert client.get("/api/v1/admin/metrics/overview", headers=headers).status_code == 200
        assert client.get("/api/v1/admin/metrics/daily", headers=headers).status_code == 200
