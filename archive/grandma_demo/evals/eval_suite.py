"""
단발 평가와 대화형 평가를 한 번에 실행하는 스크립트.

사용법:
  python -m evals.eval_suite C
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evals.eval_all import PROMPTS, run_eval
from evals.eval_dialogue import run_dialogue_eval


def run_suite(prompt_key, show_rag=False, show_points=False, show_memory=False):
    print("=" * 60)
    print(f"Eval suite 시작: 프롬프트 {prompt_key} ({PROMPTS[prompt_key]['name']})")
    print("1. 단발 질문 평가")
    print("2. 이어지는 대화 평가")
    print("=" * 60)
    print()

    run_eval(prompt_key, show_rag=show_rag, show_points=show_points)

    print()
    print("=" * 60)
    print("단발 질문 평가 완료. 이어지는 대화 평가를 시작합니다.")
    print("=" * 60)
    print()

    run_dialogue_eval(
        prompt_key,
        show_rag=show_rag,
        show_points=show_points,
        show_memory=show_memory,
    )

    print()
    print("=" * 60)
    print("Eval suite 완료.")
    print("결과 파일은 evals/results/에 각각 저장됐습니다.")
    print("=" * 60)


if __name__ == "__main__":
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    show_rag = "--show-rag" in sys.argv
    show_points = "--show-points" in sys.argv
    show_memory = "--show-memory" in sys.argv

    if len(args) < 1:
        print("사용법: python -m evals.eval_suite [A|B|C] [--show-rag] [--show-points] [--show-memory]")
        print()
        for key, value in PROMPTS.items():
            print(f"  {key}: {value['name']} 프롬프트로 단발+대화형 평가")
        sys.exit(0)

    selected_prompt = args[0].upper()
    if selected_prompt not in PROMPTS:
        print("A, B, C 중 선택하세요.")
        sys.exit(1)

    run_suite(
        selected_prompt,
        show_rag=show_rag,
        show_points=show_points,
        show_memory=show_memory,
    )
