"""
Master 레이어 저장: 안정된 페르소나 속성.

규칙:
- 잘 변하지 않는 사실 (이름, 직업, 가치관, 장기 목표 등)
- 같은 사실이 여러 통화에서 반복되면 신뢰도 증가
"""
import os
import asyncio
import cognee

DATASET = os.environ.get("COGNEE_DATASET_MASTER", "talkto_master")


async def save(facts: list[str], call_id: str) -> None:
    body = f"[call:{call_id}] Master layer facts:\n" + "\n".join(f"- {f}" for f in facts)
    await cognee.add(body, dataset_name=DATASET)
    await cognee.cognify(datasets=[DATASET])


def save_sync(facts: list[str], call_id: str) -> None:
    asyncio.run(save(facts, call_id))
