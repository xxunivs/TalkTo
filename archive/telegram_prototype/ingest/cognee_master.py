"""
Master 레이어 저장: 안정된 페르소나 속성.

규칙:
- 잘 변하지 않는 사실 (이름, 직업, 가치관, 장기 목표 등)
- 같은 사실이 여러 통화에서 반복되면 신뢰도 증가

기존 cognee-api (http://cognee-api:8000) 를 HTTP로 호출.
"""
import os
import io
import logging
import httpx

log = logging.getLogger("talkto.cognee_master")

COGNEE_API = os.environ.get("COGNEE_API_URL", "http://cognee-api:8000")
DATASET = os.environ.get("COGNEE_DATASET_MASTER", "talkto_master")


def save_sync(facts: list[str], call_id: str) -> None:
    body = f"[call:{call_id}] Master layer facts:\n" + "\n".join(f"- {f}" for f in facts)
    _add_and_cognify(body)


def _add_and_cognify(text: str) -> None:
    with httpx.Client(base_url=COGNEE_API, timeout=120) as c:
        # POST /api/v1/add — multipart with text as file
        r = c.post(
            "/api/v1/add",
            files={"data": ("facts.txt", io.BytesIO(text.encode("utf-8")), "text/plain")},
            data={"datasetName": DATASET},
        )
        r.raise_for_status()
        log.info("Added to cognee dataset '%s'", DATASET)

        # POST /api/v1/cognify
        r = c.post(
            "/api/v1/cognify",
            json={"datasets": [DATASET]},
        )
        r.raise_for_status()
        log.info("Cognify triggered for dataset '%s'", DATASET)
