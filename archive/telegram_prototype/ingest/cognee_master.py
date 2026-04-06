"""Master 레이어 저장: 안정된 페르소나 속성."""
import os
import logging
from ingest.cognee_client import add_and_cognify

log = logging.getLogger("talkto.cognee_master")
DATASET = os.environ.get("COGNEE_DATASET_MASTER", "talkto_master")


def save_sync(facts: list[str], call_id: str) -> None:
    body = f"[call:{call_id}] Master layer facts:\n" + "\n".join(f"- {f}" for f in facts)
    add_and_cognify(body, DATASET)
    log.info("Master saved: %d facts for %s", len(facts), call_id)
