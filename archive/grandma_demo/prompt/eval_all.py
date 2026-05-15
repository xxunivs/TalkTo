"""
프롬프트 버전별 3-way 평가 스크립트.

하나의 프롬프트 버전(A/B/C)을 고른 뒤 아래 세 결과를 같은 질문에서 비교한다.
1. 기본모델 + 선택 프롬프트 + RAG
2. 파인튜닝모델 + 선택 프롬프트 + RAG
3. 초기 프로토타입 모델

사용법:
  python -m prompt.eval_all C
"""
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import dotenv_values, load_dotenv
from openai import OpenAI
from prompt.system_prompt_A import SYSTEM_PROMPT as PROMPT_A
from prompt.system_prompt_B import SYSTEM_PROMPT as PROMPT_B
from prompt.system_prompt_C import SYSTEM_PROMPT as PROMPT_C
from prompt.eval_questions import EVAL_SET
from memory.embeddings import search_memories

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
    "A": {"name": "상세A", "prompt": PROMPT_A},
    "B": {"name": "최소B", "prompt": PROMPT_B},
    "C": {"name": "중간C", "prompt": PROMPT_C},
}

RESULT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "eval_results",
)

PROTO_PROMPT = """당신은 83세의 따뜻하고 다정한 할머니 '신금자'입니다. 자식들과 손자손녀들을 무척 사랑하며, 항상 그들의 건강과 안녕을 걱정합니다. 대화할 때는 할머니의 말투로 이야기합니다. 6달 전에 세상을 떠났으며 평소 독실한 신자이셨고 베푸는 삶을 사셨으며 현재 천국에 계십니다. 할머니는 대화를 무척 좋아하시고, 손자손녀의 이야기에 진심으로 관심을 갖고 계십니다.

행동 가이드라인:
- 짧게 1-2문장으로 답변하되, 대화를 끝내지 말고 질문이나 호기심을 표현하세요.
- 손자손녀의 이야기에 관심을 보이고 가끔은 추가적인 질문도 하세요.
- 매번 똑같은 건강 걱정 패턴을 피하고 다양하게 반응하세요.
- 손자손녀가 힘들어할 때는 따뜻한 위로와 격려의 말을 건네세요.
- 손자손녀가 기뻐하거나 잘한 일에 대해서는 아낌없이 칭찬하고 자랑스러워하는 감정을 표현하세요."""


def build_rag_prompt(system_prompt, memories):
    if not memories:
        return system_prompt

    memory_text = "\n".join([f"- {m['title']}: {m['content']}" for m in memories])
    return system_prompt + f"\n\n[할머니의 관련 장기기억]\n{memory_text}"


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


class EvalLogger:
    def __init__(self):
        self.lines = []

    def write(self, text=""):
        print(text)
        self.lines.append(text)

    def save(self, prompt_key):
        os.makedirs(RESULT_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(RESULT_DIR, f"eval_{prompt_key}_{timestamp}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines) + "\n")
        return path


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


def run_eval(prompt_key, show_rag=False, show_points=False):
    if not MY_KEY:
        raise RuntimeError("OPENAI_API_KEY를 .env에 설정하세요.")

    prompt_info = PROMPTS[prompt_key]
    main_client = OpenAI(api_key=MY_KEY)
    proto_client = OpenAI(api_key=PROTO_KEY) if PROTO_KEY else None
    logger = EvalLogger()

    logger.write(f"# Eval Result - Prompt {prompt_key} ({prompt_info['name']})")
    logger.write("")
    logger.write(f"- 실행시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.write(f"- 기본모델: {BASE_MODEL} + {prompt_info['name']} + RAG")
    logger.write(f"- 파인튜닝모델: {FINETUNED_MODEL} + {prompt_info['name']} + RAG")
    logger.write(f"- 초기 프로토타입: {PROTO_MODEL}")
    logger.write(f"- RAG 상세 출력: {'켜짐' if show_rag else '꺼짐'}")
    logger.write(f"- 평가포인트 출력: {'켜짐' if show_points else '꺼짐'}")
    if not proto_client:
        logger.write("- 프로토타입 결과: PROTO_OPENAI_API_KEY 없음 - 생략")
    logger.write("")
    logger.write("=" * 60)

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
    saved_path = logger.save(prompt_key)
    logger.write(f"저장: {saved_path}")


if __name__ == "__main__":
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    show_rag = "--show-rag" in sys.argv
    show_points = "--show-points" in sys.argv

    if len(args) < 1:
        print("사용법: python -m prompt.eval_all [A|B|C] [--show-rag] [--show-points]")
        print()
        for key, value in PROMPTS.items():
            print(f"  {key}: {value['name']} 프롬프트로 3-way 비교")
        sys.exit(0)

    selected_prompt = args[0].upper()
    if selected_prompt not in PROMPTS:
        print("A, B, C 중 선택하세요.")
        sys.exit(1)

    run_eval(selected_prompt, show_rag=show_rag, show_points=show_points)
