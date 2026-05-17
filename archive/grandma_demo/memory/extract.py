"""
단기기억 추출 모듈.
매 대화 턴 후에 중요도를 평가하고, 6 이상이면 단기기억에 저장.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.importance import evaluate_importance
from memory.short_term import add_short_term_memory

IMPORTANCE_THRESHOLD = 6  # 이 점수 이상이면 저장


def extract_and_save(
    user_message: str,
    assistant_message: str,
    history: list | None = None,
) -> dict:
    """
    대화 한 턴을 평가하고, 중요하면 단기기억에 저장.

    매개변수:
        user_message: 사용자가 한 말
        assistant_message: 할머니가 답한 말
        history: 현재 턴 이전의 최근 대화 내역

    반환값:
        {"saved": True, "memory": {...}} — 저장됨
        {"saved": False, "importance": 3, "reason": "..."} — 저장 안 됨
    """
    # 1. 중요도 평가
    eval_result = evaluate_importance(user_message, assistant_message, history=history)

    importance = eval_result.get("importance", 0)

    # 2. 임계값 미만이면 저장 안 함
    if importance < IMPORTANCE_THRESHOLD:
        return {
            "saved": False,
            "importance": importance,
            "reason": eval_result.get("reason", ""),
        }

    # 3. 중요하면 단기기억에 저장
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
        "note": memory,  # Backward-compatible response key.
    }


if __name__ == "__main__":
    # 테스트
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
