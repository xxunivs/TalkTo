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
from evaluation.eval_questions import DIALOGUE_EVAL_SET, EVAL_SET
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

    def save(self, prefix, prompt_key):
        os.makedirs(RESULT_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(RESULT_DIR, f"{prefix}_{prompt_key}_{timestamp}.md")
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

def write_eval_header(logger, title, prompt_key, prompt_info, show_rag, show_points, show_memory=None):
    logger.write(f"# {title} - Prompt {prompt_key} ({prompt_info['name']})")
    logger.write("")
    logger.write(f"- 실행시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.write(f"- 기본모델: {BASE_MODEL} + {prompt_info['name']} + RAG")
    logger.write(f"- 파인튜닝모델: {FINETUNED_MODEL} + {prompt_info['name']} + RAG")
    logger.write(f"- 초기 프로토타입: {PROTO_MODEL}")
    logger.write(f"- RAG 상세 출력: {'켜짐' if show_rag else '꺼짐'}")
    logger.write(f"- 평가포인트 출력: {'켜짐' if show_points else '꺼짐'}")
    if show_memory is not None:
        logger.write(f"- 단기기억 디버그 출력: {'켜짐' if show_memory else '꺼짐'}")
    if not PROTO_KEY:
        logger.write("- 프로토타입 결과: PROTO_OPENAI_API_KEY 없음 - 생략")
    logger.write("")
    logger.write("=" * 60)

def run_single_eval(prompt_key, show_rag=False, show_points=False):
    prompt_info = PROMPTS[prompt_key]
    main_client, proto_client = create_clients()
    logger = EvalLogger()

    write_eval_header(
        logger,
        "Single Eval Result",
        prompt_key,
        prompt_info,
        show_rag,
        show_points,
    )

    for i, item in enumerate(EVAL_SET, 1):
        memories = search_memories(item["질문"], top_k=5)
        rag_prompt = build_rag_prompt(prompt_info["prompt"], memories)

        base_reply = generate(main_client, BASE_MODEL, rag_prompt, item["질문"])
        finetuned_reply = generate(main_client, FINETUNED_MODEL, rag_prompt, item["질문"])
        if proto_client:
            proto_reply = generate(proto_client, PROTO_MODEL, PROTO_PROMPT, item["질문"])
        else:
            proto_reply = "PROTO_OPENAI_API_KEY 없음"

        logger.write("")
        logger.write(f"## 질문 {i}")
        logger.write(f"질문:       {item['질문']}")
        logger.write(f"실제:       {item['실제']}")
        if show_rag:
            print_rag(memories, logger)
        logger.write(f"기본모델:   {base_reply}")
        logger.write(f"파인튜닝:   {finetuned_reply}")
        logger.write(f"프로토타입: {proto_reply}")
        if show_points:
            logger.write(f"평가포인트: {item['포인트']}")

    logger.write("")
    logger.write("=" * 60)
    logger.write("완료!")
    saved_path = logger.save("eval", prompt_key)
    logger.write(f"저장: {saved_path}")

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

def format_short_term(short_term_memories):
    if not short_term_memories:
        return "없음"
    return " / ".join([m["content"] for m in short_term_memories])

def run_dialogue_eval(prompt_key, show_rag=False, show_points=False, show_memory=False):
    prompt_info = PROMPTS[prompt_key]
    main_client, proto_client = create_clients()
    logger = EvalLogger()

    write_eval_header(
        logger,
        "Dialogue Eval Result",
        prompt_key,
        prompt_info,
        show_rag,
        show_points,
        show_memory=show_memory,
    )

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
    saved_path = logger.save("dialogue", prompt_key)
    logger.write(f"저장: {saved_path}")

def run_suite(prompt_key, show_rag=False, show_points=False, show_memory=False):
    print("=" * 60)
    print(f"Eval suite 시작: 프롬프트 {prompt_key} ({PROMPTS[prompt_key]['name']})")
    print("1. 단발 질문 평가")
    print("2. 이어지는 대화 평가")
    print("=" * 60)
    print()

    run_single_eval(prompt_key, show_rag=show_rag, show_points=show_points)

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
    print("결과 파일은 evaluation/results/에 각각 저장됐습니다.")
    print("=" * 60)

def print_usage():
    print("사용법: python -m evaluation.eval_run [A|B|C] [--single|--dialogue] [--show-rag] [--show-points] [--show-memory]")
    print()
    for key, value in PROMPTS.items():
        print(f"  {key}: {value['name']}로 평가")

if __name__ == "__main__":
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    show_rag = "--show-rag" in sys.argv
    show_points = "--show-points" in sys.argv
    show_memory = "--show-memory" in sys.argv
    single_only = "--single" in sys.argv
    dialogue_only = "--dialogue" in sys.argv

    if len(args) < 1:
        print_usage()
        sys.exit(0)

    selected_prompt = args[0].upper()
    if selected_prompt not in PROMPTS:
        print("A, B, C 중 선택하세요.")
        sys.exit(1)

    if single_only and dialogue_only:
        print("--single과 --dialogue는 동시에 사용할 수 없습니다.")
        sys.exit(1)

    if single_only:
        run_single_eval(selected_prompt, show_rag=show_rag, show_points=show_points)
    elif dialogue_only:
        run_dialogue_eval(
            selected_prompt,
            show_rag=show_rag,
            show_points=show_points,
            show_memory=show_memory,
        )
    else:
        run_suite(
            selected_prompt,
            show_rag=show_rag,
            show_points=show_points,
            show_memory=show_memory,
        )
