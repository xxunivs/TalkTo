"""Overlay 레이어 저장: 맥락/일시 상태."""
import os
import logging
from datetime import datetime, timezone
from ingest.cognee_client import add_and_cognify

log = logging.getLogger("talkto.cognee_overlay")
DATASET = os.environ.get("COGNEE_DATASET_OVERLAY", "talkto_overlay")


def save_sync(facts: list[str], call_id: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = f"[call:{call_id} @ {ts}] Overlay layer facts:\n" + "\n".join(f"- {f}" for f in facts)
    add_and_cognify(body, DATASET)
    log.info("Overlay saved: %d facts for %s", len(facts), call_id)
