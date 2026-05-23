import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.memory.importance import evaluate_importance
from core.memory.short_term import add_short_term_memory

IMPORTANCE_THRESHOLD = 6

def extract_and_save(
    user_message: str,
    assistant_message: str,
    history: list | None = None,
) -> dict:

    eval_result = evaluate_importance(user_message, assistant_message, history=history)

    importance = eval_result.get("importance", 0)

    if importance < IMPORTANCE_THRESHOLD:
        return {
            "saved": False,
            "importance": importance,
            "reason": eval_result.get("reason", ""),
        }

    summary = eval_result.get("summary", "")
    category = eval_result.get("category", "기타")

    if not summary:
        summary = f"손녀가 '{user_message}'라고 말함"

    memory = add_short_term_memory(
        content=summary,
        category=category,
        importance=importance,
    )

    return {
        "saved": True,
        "memory": memory,
        "note": memory,
    }

if __name__ == "__main__":

    test_cases = [
        ("나 회사 그만뒀어", "어? 왜? 무슨 일 있었어?"),
        ("응", "그래."),
        ("나 다음 달에 이사해", "어디로 가는데?"),
    ]

    for user_msg, assistant_msg in test_cases:
        result = extract_and_save(user_msg, assistant_msg)
        print(f"\n손녀: {user_msg}")
        if result["saved"]:
            print(f"→ 저장됨! [{result['memory']['importance']}점] {result['memory']['content']}")
        else:
            print(f"→ 저장 안 됨 [{result['importance']}점] {result['reason']}")
