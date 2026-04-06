"""
통화 종료 직후 실행: 전체 transcript → Qwen 분석 → Master/Overlay 분기 저장.
"""
import os
import json
import logging
import httpx
from pathlib import Path

from ingest import cognee_master, cognee_overlay

log = logging.getLogger("talkto.post_call")

LLM_URL = os.environ.get("LLM_URL", "http://llm:8003")


def run(call_dir: Path) -> dict:
    transcript_path = call_dir / "transcript.json"
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))

    with httpx.Client() as c:
        r = c.post(
            f"{LLM_URL}/analyze_call",
            json={"transcript": transcript},
            timeout=120,
        )
        r.raise_for_status()
        analysis = r.json()

    (call_dir / "analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Master/Overlay routing: save facts to cognee knowledge graph layers
    call_id = call_dir.name

    facts_master = analysis.get("facts_master", [])
    if facts_master:
        log.info("Saving %d facts to master layer for call %s", len(facts_master), call_id)
        cognee_master.save_sync(facts_master, call_id)
        log.info("Master layer saved: %s", facts_master)

    facts_overlay = analysis.get("facts_overlay", [])
    if facts_overlay:
        log.info("Saving %d facts to overlay layer for call %s", len(facts_overlay), call_id)
        cognee_overlay.save_sync(facts_overlay, call_id)
        log.info("Overlay layer saved: %s", facts_overlay)

    return analysis
