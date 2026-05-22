"""
말투 평가 실행 스크립트.
10개 질문을 AI한테 던지고, 실제 할머니 답변과 나란히 비교 출력.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.response.generator import generate_response
from evals.eval_questions import EVAL_SET


def run_eval():
    print("=" * 60)
    print("할머니 말투 평가 (실제 vs AI)")
    print("=" * 60)

    for i, item in enumerate(EVAL_SET, 1):
        ai_reply = generate_response(item["질문"])

        print(f"\n--- 질문 {i} ---")
        print(f"질문:     {item['질문']}")
        print(f"실제:     {item['실제']}")
        print(f"AI:       {ai_reply}")
        print(f"평가포인트: {item['포인트']}")

    print("\n" + "=" * 60)
    print("비교 완료! 위 결과를 보고 프롬프트를 수정하세요.")
    print("=" * 60)


if __name__ == "__main__":
    run_eval()
