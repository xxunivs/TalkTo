"""Cognee API 인증 + 공통 호출 헬퍼."""
import os
import io
import logging
import httpx

log = logging.getLogger("talkto.cognee_client")

COGNEE_API = os.environ.get("COGNEE_API_URL", "http://cognee-api:8000")
COGNEE_USER = os.environ.get("COGNEE_USER", "xxunivs@gmail.com")
COGNEE_PASS = os.environ.get("COGNEE_PASS", "7899@rladnwn")

_token: str | None = None


def _login() -> str:
    global _token
    if _token:
        return _token
    with httpx.Client(base_url=COGNEE_API, timeout=30) as c:
        r = c.post(
            "/api/v1/auth/login",
            data={"username": COGNEE_USER, "password": COGNEE_PASS},
        )
        r.raise_for_status()
        _token = r.json()["access_token"]
        log.info("Cognee login OK")
    return _token


def add_and_cognify(text: str, dataset: str) -> None:
    token = _login()
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=COGNEE_API, timeout=120, headers=headers) as c:
        r = c.post(
            "/api/v1/add",
            files={"data": ("facts.txt", io.BytesIO(text.encode("utf-8")), "text/plain")},
            data={"datasetName": dataset},
        )
        r.raise_for_status()
        log.info("Added to cognee dataset '%s'", dataset)

        r = c.post(
            "/api/v1/cognify",
            json={"datasets": [dataset], "runInBackground": True},
        )
        r.raise_for_status()
        log.info("Cognify triggered (background) for dataset '%s'", dataset)
