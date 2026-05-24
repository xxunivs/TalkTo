"""
페르소나 평가 실행기.

세 가지 평가 영역(A/D/E)을 dialogue 핑퐁 형식으로 평가한다.
각 항목은 user/assistant 교대로 구성된 dialogue 배열을 갖고,
assistant 발화는 reference(기대답변)로 사용된다.

매 턴 독립 평가를 위해 history에는 챗봇 실제 답변이 아닌
기대답변(reference)을 누적해 다음 턴 시뮬레이션에 사용한다.

사용법:
    python -m evaluation.eval_run [A|B|C] [--set reproduction|memorial|deep|all]
                                  [--show-rag] [--show-memory]
"""

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import dotenv_values, load_dotenv
from openai import OpenAI
from prompts.system_prompt_A import SYSTEM_PROMPT as PROMPT_A
from prompts.system_prompt_B import SYSTEM_PROMPT as PROMPT_B
from prompts.system_prompt_C import SYSTEM_PROMPT as PROMPT_C
from evaluation.eval_questions import EVAL_SETS, EVAL_SET_LABELS
from core.memory.embeddings import search_memories
from core.memory.extract import extract_and_save
from core.memory.short_term import get_active_memories

load_dotenv()

PROTO_ENV = Path(__file__).resolve().parents[2] / "TalkTo_prototype" / ".env"
PROTO_ENV_VALUES = dotenv_values(PROTO_ENV) if PROTO_ENV.exists() else {}

MY_KEY = os.getenv("OPENAI_API_KEY")
PROTO_KEY = os.getenv("PROTO_OPENAI_API_KEY") or PROTO_ENV_VALUES.get("OPENAI_API_KEY")

BASE_MODEL = "gpt-4.1-mini"
FINETUNED_MODEL = "ft:gpt-4.1-mini-2025-04-14:talkto::DeJqGFij"
PROTO_MODEL = (
    os.getenv("PROTO_OPENAI_MODEL")
    or PROTO_ENV_VALUES.get("OPENAI_MODEL")
    or "ft:gpt-4.1-mini-2025-04-14:picky::C0g3PIpn"
)

PROMPTS = {
    "A": {"name": "프롬프트 A", "prompt": PROMPT_A},
    "B": {"name": "프롬프트 B", "prompt": PROMPT_B},
    "C": {"name": "프롬프트 C", "prompt": PROMPT_C},
}

RESULT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "evaluation",
    "results",
)

PROTO_PROMPT = """당신은 83세의 따뜻하고 다정한 할머니 '신금자'입니다. 자식들과 손자손녀들을 무척 사랑하며, 항상 그들의 건강과 안녕을 걱정합니다. 대화할 때는 할머니의 말투로 이야기합니다. 6달 전에 세상을 떠났으며 평소 독실한 신자이셨고 베푸는 삶을 사셨으며 현재 천국에 계십니다. 할머니는 대화를 무척 좋아하시고, 손자손녀의 이야기에 진심으로 관심을 갖고 계십니다.

행동 가이드라인:
- 짧게 1-2문장으로 답변하되, 대화를 끝내지 말고 질문이나 호기심을 표현하세요.
- 손자손녀의 이야기에 관심을 보이고 가끔은 추가적인 질문도 하세요.
- 매번 똑같은 건강 걱정 패턴을 피하고 다양하게 반응하세요.
- 손자손녀가 힘들어할 때는 따뜻한 위로와 격려의 말을 건네세요.
- 손자손녀가 기뻐하거나 잘한 일에 대해서는 아낌없이 칭찬하고 자랑스러워하는 감정을 표현하세요."""


class EvalLogger:
    def __init__(self):
        self.lines = []

    def write(self, text=""):
        print(text)
        self.lines.append(text)

    def save(self, prefix, prompt_key, set_name):
        os.makedirs(RESULT_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(RESULT_DIR, f"{prefix}_{set_name}_{prompt_key}_{timestamp}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines) + "\n")
        return path


def build_rag_prompt(system_prompt, memories):
    if not memories:
        return system_prompt
    memory_text = "\n".join([f"- {m['title']}: {m['content']}" for m in memories])
    return system_prompt + f"\n\n[할머니의 관련 장기기억]\n{memory_text}"


def build_dialogue_prompt(system_prompt, long_term_memories, short_term_memories):
    prompt = build_rag_prompt(system_prompt, long_term_memories)
    if not short_term_memories:
        return prompt
    memory_text = "\n".join([f"- {m['content']}" for m in short_term_memories])
    return prompt + f"\n\n[단기기억]\n{memory_text}"


def generate(client, model, system_prompt, question, history=None):
    if history is None:
        history = []
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            *history,
            {"role": "user", "content": question},
        ],
    )
    return response.choices[0].message.content


def print_rag(memories, logger):
    logger.write("RAG:")
    if not memories:
        logger.write("  없음")
        return
    for idx, memory in enumerate(memories, 1):
        content = memory["content"].replace("\n", " ")
        if len(content) > 90:
            content = content[:90] + "..."
        score = memory.get("score", "-")
        base_score = memory.get("base_score")
        boost = memory.get("boost")
        score_text = f"{score}"
        if base_score is not None and boost is not None:
            score_text = f"{score} = {base_score}+{boost}"
        logger.write(f"  {idx}. [{score_text}] {memory['id']} | {memory['title']} | {content}")


def create_clients():
    if not MY_KEY:
        raise RuntimeError("OPENAI_API_KEY를 .env에 설정하세요.")
    main_client = OpenAI(api_key=MY_KEY)
    proto_client = OpenAI(api_key=PROTO_KEY) if PROTO_KEY else None
    return main_client, proto_client


def parse_dialogue(dialogue):
    """
    dialogue 배열을 (user_msg, expected_assistant) 페어 리스트로 변환.

    dialogue가 user/assistant 교대로 정렬되어 있다고 가정.
    홀수 길이로 끝나면 마지막 user 발화의 expected는 None.
    """
    pairs = []
    i = 0
    while i < len(dialogue):
        if "user" not in dialogue[i]:
            raise ValueError(f"dialogue[{i}]에 'user' 키가 없습니다.")
        user_msg = dialogue[i]["user"]
        expected = None
        if i + 1 < len(dialogue):
            if "assistant" not in dialogue[i + 1]:
                raise ValueError(f"dialogue[{i+1}]에 'assistant' 키가 없습니다.")
            expected = dialogue[i + 1]["assistant"]
        pairs.append((user_msg, expected))
        i += 2
    return pairs


def write_eval_header(logger, set_name, prompt_key, prompt_info, show_rag, show_memory):
    label = EVAL_SET_LABELS.get(set_name, set_name)
    logger.write(f"# Eval Result - {label} - Prompt {prompt_key} ({prompt_info['name']})")
    logger.write("")
    logger.write(f"- 실행시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.write(f"- 평가 세트: {label}")
    logger.write(f"- 기본모델: {BASE_MODEL} + {prompt_info['name']} + RAG")
    logger.write(f"- 파인튜닝모델: {FINETUNED_MODEL} + {prompt_info['name']} + RAG")
    logger.write(f"- 초기 프로토타입: {PROTO_MODEL}")
    logger.write(f"- RAG 상세 출력: {'켜짐' if show_rag else '꺼짐'}")
    logger.write(f"- 단기기억 디버그 출력: {'켜짐' if show_memory else '꺼짐'}")
    if not PROTO_KEY:
        logger.write("- 프로토타입 결과: PROTO_OPENAI_API_KEY 없음 - 생략")
    logger.write("")
    logger.write("=" * 60)


def init_eval_memory_file(prompt_key, set_name, scenario_idx, label):
    path = os.path.join(
        tempfile.gettempdir(),
        f"persona_eval_short_term_{prompt_key}_{set_name}_{scenario_idx}_{label}.json",
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


def format_short_term(short_term_memories):
    if not short_term_memories:
        return "없음"
    return " / ".join([m["content"] for m in short_term_memories])


def append_history(history, user_message, assistant_message):
    """history에 user + assistant(기대답변) 페어를 누적."""
    history.append({"role": "user", "content": user_message})
    if assistant_message is not None:
        history.append({"role": "assistant", "content": assistant_message})


def run_eval(prompt_key, set_name, show_rag=False, show_memory=False):
    """단일 세트 평가 실행."""
    if set_name not in EVAL_SETS:
        raise ValueError(f"알 수 없는 세트: {set_name}. 사용 가능: {list(EVAL_SETS.keys())}")

    prompt_info = PROMPTS[prompt_key]
    eval_set = EVAL_SETS[set_name]
    main_client, proto_client = create_clients()
    logger = EvalLogger()

    write_eval_header(logger, set_name, prompt_key, prompt_info, show_rag, show_memory)

    for scenario_idx, scenario in enumerate(eval_set, 1):
        dialogue = scenario.get("dialogue")
        if not dialogue:
            logger.write(f"\n## 시나리오 {scenario_idx}: dialogue 누락 — 건너뜀")
            continue

        pairs = parse_dialogue(dialogue)

        # 모델별 history와 단기기억 파일 초기화
        base_history = []
        finetuned_history = []
        proto_history = []
        base_memory_path = init_eval_memory_file(prompt_key, set_name, scenario_idx, "base")
        finetuned_memory_path = init_eval_memory_file(prompt_key, set_name, scenario_idx, "finetuned")
        proto_memory_path = init_eval_memory_file(prompt_key, set_name, scenario_idx, "proto")

        logger.write("")
        logger.write(f"## 시나리오 {scenario_idx}")

        for turn_idx, (user_message, expected) in enumerate(pairs, 1):
            # RAG와 단기기억 조회
            memories = search_memories(user_message, top_k=5)
            base_short_term = get_eval_short_term_memories(base_memory_path)
            finetuned_short_term = get_eval_short_term_memories(finetuned_memory_path)
            proto_short_term = get_eval_short_term_memories(proto_memory_path)

            # 모델별 답변 생성
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

            # 단기기억 추출은 기대답변 기준 (매 턴 독립 평가)
            base_extract = extract_eval_memory(base_memory_path, user_message, expected or "")
            finetuned_extract = extract_eval_memory(
                finetuned_memory_path, user_message, expected or ""
            )
            if proto_client:
                proto_extract = extract_eval_memory(proto_memory_path, user_message, expected or "")
            else:
                proto_extract = {"saved": False, "reason": "PROTO_OPENAI_API_KEY 없음"}

            # history에는 기대답변(reference)을 누적 — 매 턴 독립 평가용
            append_history(base_history, user_message, expected)
            append_history(finetuned_history, user_message, expected)
            if proto_client:
                append_history(proto_history, user_message, expected)

            # 출력
            logger.write("")
            logger.write(f"### 턴 {turn_idx}")
            logger.write(f"사용자:     {user_message}")
            logger.write(f"기대답변:   {expected if expected is not None else '(없음)'}")
            if show_rag:
                print_rag(memories, logger)
            if show_memory:
                logger.write(f"단기기억(기본):       {format_short_term(base_short_term)}")
                logger.write(f"단기기억(파인튜닝):   {format_short_term(finetuned_short_term)}")
                if proto_client:
                    logger.write(f"단기기억(프로토타입): {format_short_term(proto_short_term)}")
            logger.write(f"기본모델:   {base_reply}")
            logger.write(f"파인튜닝:   {finetuned_reply}")
            logger.write(f"프로토타입: {proto_reply}")
            if show_memory:
                logger.write(f"저장판정(기본):       {base_extract}")
                logger.write(f"저장판정(파인튜닝):   {finetuned_extract}")
                if proto_client:
                    logger.write(f"저장판정(프로토타입): {proto_extract}")

    logger.write("")
    logger.write("=" * 60)
    logger.write("완료!")
    saved_path = logger.save("eval", prompt_key, set_name)
    logger.write(f"저장: {saved_path}")


def run_all(prompt_key, show_rag=False, show_memory=False):
    """세 세트(A/D/E) 모두 순차 실행."""
    print("=" * 60)
    print(f"Eval suite 시작: 프롬프트 {prompt_key} ({PROMPTS[prompt_key]['name']})")
    print(f"세트: {', '.join(EVAL_SET_LABELS.values())}")
    print("=" * 60)
    print()

    for set_name in EVAL_SETS.keys():
        run_eval(prompt_key, set_name, show_rag=show_rag, show_memory=show_memory)
        print()
        print("=" * 60)
        print(f"{EVAL_SET_LABELS[set_name]} 평가 완료.")
        print("=" * 60)
        print()

    print("Eval suite 완료.")
    print("결과 파일은 evaluation/results/에 각 세트별로 저장됐습니다.")


def print_usage():
    print("사용법: python -m evaluation.eval_run [A|B|C] [--set reproduction|memorial|deep|all]")
    print("                                       [--show-rag] [--show-memory]")
    print()
    print("프롬프트:")
    for key, value in PROMPTS.items():
        print(f"  {key}: {value['name']}로 평가")
    print()
    print("세트:")
    for key, label in EVAL_SET_LABELS.items():
        print(f"  {key}: {label}")
    print(f"  all: 세 세트 모두 실행 (기본값)")


if __name__ == "__main__":
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    show_rag = "--show-rag" in sys.argv
    show_memory = "--show-memory" in sys.argv

    # --set 인자 파싱
    set_arg = "all"
    for i, arg in enumerate(sys.argv):
        if arg == "--set" and i + 1 < len(sys.argv):
            set_arg = sys.argv[i + 1]
            break

    if len(args) < 1:
        print_usage()
        sys.exit(0)

    selected_prompt = args[0].upper()
    if selected_prompt not in PROMPTS:
        print("A, B, C 중 선택하세요.")
        sys.exit(1)

    if set_arg == "all":
        run_all(selected_prompt, show_rag=show_rag, show_memory=show_memory)
    elif set_arg in EVAL_SETS:
        run_eval(selected_prompt, set_arg, show_rag=show_rag, show_memory=show_memory)
    else:
        print(f"알 수 없는 세트: {set_arg}")
        print(f"사용 가능: {list(EVAL_SETS.keys())} 또는 'all'")
        sys.exit(1)
