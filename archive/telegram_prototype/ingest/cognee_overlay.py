"""
Overlay 레이어 저장: 맥락/일시 상태.

규칙:
- 통화 시점의 기분, 최근 이벤트, 일시적 계획 등
- 시간에 따라 퇴색/덮어쓰기될 수 있음 (TTL 혹은 recency weight)
"""
import os
import asyncio
from datetime import datetime, timezone
import cognee

DATASET = os.environ.get("COGNEE_DATASET_OVERLAY", "talkto_overlay")


async def save(facts: list[str], call_id: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = f"[call:{call_id} @ {ts}] Overlay layer facts:\n" + "\n".join(f"- {f}" for f in facts)
    await cognee.add(body, dataset_name=DATASET)
    await cognee.cognify(datasets=[DATASET])


def save_sync(facts: list[str], call_id: str) -> None:
    asyncio.run(save(facts, call_id))
