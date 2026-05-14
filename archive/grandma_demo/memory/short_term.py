"""
단기기억 관리 모듈.
대화 중 새로 알게 된 정보를 저장하고 검색.
"""
import os
import sys
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SHORT_TERM_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "short_term_memories.json"
)

"""
단기기억 스키마:
{
    "id": "st_001",                    — 고유 ID
    "content": "향이가 회사를 그만뒀다",  — 기억 내용
    "category": "가족",                 — 카테고리
    "importance": 8,                   — 중요도 (1~10)
    "strength": 1.0,                   — 강도 (회상될수록 증가, 시간 지나면 감소)
    "recall_count": 0,                 — 회상 횟수
    "created_at": "2026-05-12T15:30:00", — 생성 시각
    "last_recalled_at": null,          — 마지막 회상 시각
    "archived": false                  — 잊혀졌는지 (strength 0.1 미만이면 true)
}
"""


def load_short_term_memories() -> list:
    """단기기억 전체 로드"""
    with open(SHORT_TERM_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_short_term_memories(memories: list):
    """단기기억 전체 저장"""
    with open(SHORT_TERM_PATH, "w", encoding="utf-8") as f:
        json.dump(memories, f, ensure_ascii=False, indent=2)


def add_short_term_memory(content: str, category: str, importance: int) -> dict:
    """
    새 단기기억 추가.

    매개변수:
        content: 기억 내용 (예: "향이가 회사를 그만뒀다")
        category: 카테고리 (예: "가족", "일상", "감정")
        importance: 중요도 1~10

    반환값:
        추가된 단기기억 dict
    """
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
    """archived되지 않은 활성 단기기억만 반환"""
    memories = load_short_term_memories()
    return [m for m in memories if not m["archived"]]


def on_memory_recalled(memory_id: str):
    """단기기억이 프롬프트에 포함됐을 때 강도 +1, 회상 횟수 +1"""
    memories = load_short_term_memories()
    for m in memories:
        if m["id"] == memory_id:
            m["strength"] = min(m["strength"] + 1.0, 10.0)
            m["recall_count"] += 1
            m["last_recalled_at"] = datetime.now().isoformat()
            break
    save_short_term_memories(memories)


if __name__ == "__main__":
    # 테스트: 단기기억 추가
    mem = add_short_term_memory(
        content="향이가 오늘 회사에서 스트레스 받았다고 했다",
        category="가족",
        importance=7,
    )
    print(f"추가됨: {mem}")

    # 전체 조회
    all_mems = get_active_memories()
    print(f"\n활성 단기기억 {len(all_mems)}개:")
    for m in all_mems:
        print(f"  [{m['id']}] {m['content']}")
