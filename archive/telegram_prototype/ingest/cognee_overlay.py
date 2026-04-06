"""
Overlay 레이어 저장: 맥락/일시 상태.

규칙:
- 통화 시점의 기분, 최근 이벤트, 일시적 계획 등
- 시간에 따라 퇴색/덮어쓰기될 수 있음 (TTL 혹은 recency weight)

기존 cognee-api (http://cognee-api:8000) 를 HTTP로 호출.
"""
import os
import io
import logging
from datetime import datetime, timezone
import httpx

log = logging.getLogger("talkto.cognee_overlay")

COGNEE_API = os.environ.get("COGNEE_API_URL", "http://cognee-api:8000")
DATASET = os.environ.get("COGNEE_DATASET_OVERLAY", "talkto_overlay")


def save_sync(facts: list[str], call_id: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = f"[call:{call_id} @ {ts}] Overlay layer facts:\n" + "\n".join(f"- {f}" for f in facts)
    _add_and_cognify(body)


def _add_and_cognify(text: str) -> None:
    with httpx.Client(base_url=COGNEE_API, timeout=120) as c:
        r = c.post(
            "/api/v1/add",
            files={"data": ("facts.txt", io.BytesIO(text.encode("utf-8")), "text/plain")},
            data={"datasetName": DATASET},
        )
        r.raise_for_status()
        log.info("Added to cognee dataset '%s'", DATASET)

        r = c.post(
            "/api/v1/cognify",
            json={"datasets": [DATASET]},
        )
        r.raise_for_status()
        log.info("Cognify triggered for dataset '%s'", DATASET)
