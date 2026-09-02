# 참고용 (서비스에서 안 씀). 단기기억을 로컬 JSON 에 저장 (strength, recall_count, archived 망각 모델)
# 프로덕션에서는 BE 가 기억 저장을 소유하고, phase1 배포에서도 꺼져 있었다.

import os
import sys
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DEFAULT_SHORT_TERM_PATH = str(ROOT / "data" / "short_term_memories.json")

def get_short_term_path() -> str:
    return os.getenv("PERSONA_SHORT_TERM_PATH", DEFAULT_SHORT_TERM_PATH)

def load_short_term_memories() -> list:
    with open(get_short_term_path(), "r", encoding="utf-8") as f:
        return json.load(f)

def save_short_term_memories(memories: list):
    with open(get_short_term_path(), "w", encoding="utf-8") as f:
        json.dump(memories, f, ensure_ascii=False, indent=2)

def add_short_term_memory(content: str, category: str, importance: int) -> dict:
    memories = load_short_term_memories()

    new_id = f"ST-{len(memories) + 1:04d}"
    new_memory = {
        "id": new_id,
        "content": content,
        "category": category,
        "importance": importance,
        "strength": 1.0,
        "recall_count": 0,
        "created_at": datetime.now().isoformat(),
        "last_recalled_at": None,
        "archived": False,
    }

    memories.append(new_memory)
    save_short_term_memories(memories)
    return new_memory

def get_active_memories() -> list:
    memories = load_short_term_memories()
    return [m for m in memories if not m["archived"]]

def on_memory_recalled(memory_id: str):
    memories = load_short_term_memories()
    for m in memories:
        if m["id"] == memory_id:
            m["strength"] = min(m["strength"] + 1.0, 10.0)
            m["recall_count"] += 1
            m["last_recalled_at"] = datetime.now().isoformat()
            break
    save_short_term_memories(memories)

if __name__ == "__main__":

    mem = add_short_term_memory(
        content="향이가 오늘 회사에서 스트레스 받았다고 했다",
        category="가족",
        importance=7,
    )
    print(f"추가됨: {mem}")

    all_mems = get_active_memories()
    print(f"\n활성 단기기억 {len(all_mems)}개:")
    for m in all_mems:
        print(f"  [{m['id']}] {m['content']}")
