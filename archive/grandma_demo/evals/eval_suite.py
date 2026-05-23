import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evals.evaluation import PROMPTS, run_suite

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
