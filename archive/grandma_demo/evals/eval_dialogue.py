"""
프롬프트 버전별 대화형 3-way 평가 스크립트.

하나의 프롬프트 버전(A/B/C)을 고른 뒤, 여러 턴으로 이어지는 시나리오에서
기본모델+RAG, 파인튜닝모델+RAG, 초기 프로토타입을 비교한다.

사용법:
  python -m evals.eval_dialogue C
"""
import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from openai import OpenAI
from evals.eval_all import (
    BASE_MODEL,
    FINETUNED_MODEL,
    PROTO_MODEL,
    PROMPTS,
    PROTO_PROMPT,
    RESULT_DIR,
    build_rag_prompt,
    generate,
    print_rag,
    EvalLogger,
)
from evals.eval_questions import DIALOGUE_EVAL_SET
from core.memory.embeddings import search_memories
from core.memory.extract import extract_and_save
from core.memory.short_term import get_active_memories

load_dotenv()

MY_KEY = os.getenv("OPENAI_API_KEY")
PROTO_KEY = os.getenv("PROTO_OPENAI_API_KEY")


def append_history(history, user_message, assistant_message):
    history.append({"role": "user", "content": user_message})
    history.append({"role": "assistant", "content": assistant_message})


def init_eval_memory_file(prompt_key, scenario_idx, label):
    path = os.path.join(
        tempfile.gettempdir(),
        f"persona_eval_short_term_{prompt_key}_{scenario_idx}_{label}.json",
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write("[]")
    return path


def with_short_term_path(path, callback):
    previous = os.environ.get("PERSONA_SHORT_TERM_PATH")
    os.environ["PERSONA_SHORT_TERM_PATH"] = path
    try:
        return callback()
    finally:
        if previous is None:
            os.environ.pop("PERSONA_SHORT_TERM_PATH", None)
        else:
            os.environ["PERSONA_SHORT_TERM_PATH"] = previous


def get_eval_short_term_memories(path):
    return with_short_term_path(path, get_active_memories)


def extract_eval_memory(path, user_message, assistant_message):
    return with_short_term_path(
        path,
        lambda: extract_and_save(user_message, assistant_message),
    )


def build_dialogue_prompt(system_prompt, long_term_memories, short_term_memories):
    prompt = build_rag_prompt(system_prompt, long_term_memories)
    if not short_term_memories:
        return prompt

    memory_text = "\n".join([f"- {m['content']}" for m in short_term_memories])
    return prompt + f"\n\n[단기기억]\n{memory_text}"


def format_short_term(short_term_memories):
    if not short_term_memories:
        return "없음"
    return " / ".join([m["content"] for m in short_term_memories])


def save_dialogue_result(logger, prompt_key):
    os.makedirs(RESULT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(RESULT_DIR, f"dialogue_{prompt_key}_{timestamp}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(logger.lines) + "\n")
    return path


def run_dialogue_eval(prompt_key, show_rag=False, show_points=False, show_memory=False):
    if not MY_KEY:
        raise RuntimeError("OPENAI_API_KEY를 .env에 설정하세요.")

    prompt_info = PROMPTS[prompt_key]
    main_client = OpenAI(api_key=MY_KEY)
    proto_client = OpenAI(api_key=PROTO_KEY) if PROTO_KEY else None
    logger = EvalLogger()

    logger.write(f"# Dialogue Eval Result - Prompt {prompt_key} ({prompt_info['name']})")
    logger.write("")
    logger.write(f"- 실행시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.write(f"- 기본모델: {BASE_MODEL} + {prompt_info['name']} + RAG")
    logger.write(f"- 파인튜닝모델: {FINETUNED_MODEL} + {prompt_info['name']} + RAG")
    logger.write(f"- 초기 프로토타입: {PROTO_MODEL}")
    logger.write(f"- RAG 상세 출력: {'켜짐' if show_rag else '꺼짐'}")
    logger.write(f"- 평가포인트 출력: {'켜짐' if show_points else '꺼짐'}")
    logger.write(f"- 단기기억 디버그 출력: {'켜짐' if show_memory else '꺼짐'}")
    if not proto_client:
        logger.write("- 프로토타입 결과: PROTO_OPENAI_API_KEY 없음 - 생략")
    logger.write("")
    logger.write("=" * 60)

    for scenario_idx, scenario in enumerate(DIALOGUE_EVAL_SET, 1):
        base_history = []
        finetuned_history = []
        proto_history = []
        base_memory_path = init_eval_memory_file(prompt_key, scenario_idx, "base")
        finetuned_memory_path = init_eval_memory_file(prompt_key, scenario_idx, "finetuned")
        proto_memory_path = init_eval_memory_file(prompt_key, scenario_idx, "proto")

        logger.write("")
        logger.write(f"## 시나리오 {scenario_idx}: {scenario['name']}")
        if show_points:
            logger.write(f"평가포인트: {scenario['point']}")

        for turn_idx, user_message in enumerate(scenario["turns"], 1):
            memories = search_memories(user_message, top_k=5)
            base_short_term = get_eval_short_term_memories(base_memory_path)
            finetuned_short_term = get_eval_short_term_memories(finetuned_memory_path)
            proto_short_term = get_eval_short_term_memories(proto_memory_path)

            base_reply = generate(
                main_client,
                BASE_MODEL,
                build_dialogue_prompt(prompt_info["prompt"], memories, base_short_term),
                user_message,
                history=base_history,
            )
            finetuned_reply = generate(
                main_client,
                FINETUNED_MODEL,
                build_dialogue_prompt(prompt_info["prompt"], memories, finetuned_short_term),
                user_message,
                history=finetuned_history,
            )
            if proto_client:
                proto_reply = generate(
                    proto_client,
                    PROTO_MODEL,
                    PROTO_PROMPT,
                    user_message,
                    history=proto_history,
                )
            else:
                proto_reply = "PROTO_OPENAI_API_KEY 없음"

            base_extract = extract_eval_memory(base_memory_path, user_message, base_reply)
            finetuned_extract = extract_eval_memory(
                finetuned_memory_path,
                user_message,
                finetuned_reply,
            )
            if proto_client:
                proto_extract = extract_eval_memory(proto_memory_path, user_message, proto_reply)
            else:
                proto_extract = {"saved": False, "reason": "PROTO_OPENAI_API_KEY 없음"}

            append_history(base_history, user_message, base_reply)
            append_history(finetuned_history, user_message, finetuned_reply)
            if proto_client:
                append_history(proto_history, user_message, proto_reply)

            logger.write("")
            logger.write(f"### 턴 {turn_idx}")
            logger.write(f"사용자:     {user_message}")
            if show_rag:
                print_rag(memories, logger)
            if show_memory:
                logger.write(f"단기기억(기본): {format_short_term(base_short_term)}")
                logger.write(f"단기기억(파인튜닝): {format_short_term(finetuned_short_term)}")
                if proto_client:
                    logger.write(f"단기기억(프로토타입): {format_short_term(proto_short_term)}")
            logger.write(f"기본모델:   {base_reply}")
            logger.write(f"파인튜닝:   {finetuned_reply}")
            logger.write(f"프로토타입: {proto_reply}")
            if show_memory:
                logger.write(f"저장판정(기본): {base_extract}")
                logger.write(f"저장판정(파인튜닝): {finetuned_extract}")
                if proto_client:
                    logger.write(f"저장판정(프로토타입): {proto_extract}")

    logger.write("")
    logger.write("=" * 60)
    logger.write("완료!")
    saved_path = save_dialogue_result(logger, prompt_key)
    logger.write(f"저장: {saved_path}")


if __name__ == "__main__":
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    show_rag = "--show-rag" in sys.argv
    show_points = "--show-points" in sys.argv
    show_memory = "--show-memory" in sys.argv

    if len(args) < 1:
        print("사용법: python -m evals.eval_dialogue [A|B|C] [--show-rag] [--show-points] [--show-memory]")
        print()
        for key, value in PROMPTS.items():
            print(f"  {key}: {value['name']} 프롬프트로 대화형 3-way 비교")
        sys.exit(0)

    selected_prompt = args[0].upper()
    if selected_prompt not in PROMPTS:
        print("A, B, C 중 선택하세요.")
        sys.exit(1)

    run_dialogue_eval(
        selected_prompt,
        show_rag=show_rag,
        show_points=show_points,
        show_memory=show_memory,
    )
