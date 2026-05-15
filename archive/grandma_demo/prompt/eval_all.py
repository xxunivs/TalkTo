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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from openai import OpenAI
from prompt.system_prompt_A import SYSTEM_PROMPT as PROMPT_A
from prompt.system_prompt_B import SYSTEM_PROMPT as PROMPT_B
from prompt.system_prompt_C import SYSTEM_PROMPT as PROMPT_C
from prompt.eval_questions import EVAL_SET
from memory.embeddings import search_memories

load_dotenv()

MY_KEY = os.getenv("OPENAI_API_KEY")
PROTO_KEY = os.getenv("PROTO_OPENAI_API_KEY")

BASE_MODEL = "gpt-4.1-mini"
FINETUNED_MODEL = "ft:gpt-4.1-mini-2025-04-14:talkto::DeJqGFij"
PROTO_MODEL = "ft:gpt-4.1-mini-2025-04-14:picky::C0g3PIpn"

PROMPTS = {
    "A": {"name": "상세A", "prompt": PROMPT_A},
    "B": {"name": "최소B", "prompt": PROMPT_B},
    "C": {"name": "중간C", "prompt": PROMPT_C},
}

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


def generate(client, model, system_prompt, question):
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
    )
    return response.choices[0].message.content


def print_rag(memories):
    print("RAG:")
    if not memories:
        print("  없음")
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
        print(f"  {idx}. [{score_text}] {memory['id']} | {memory['title']} | {content}")


def run_eval(prompt_key):
    if not MY_KEY:
        raise RuntimeError("OPENAI_API_KEY를 .env에 설정하세요.")

    prompt_info = PROMPTS[prompt_key]
    main_client = OpenAI(api_key=MY_KEY)
    proto_client = OpenAI(api_key=PROTO_KEY) if PROTO_KEY else None

    print("=" * 60)
    print(f"프롬프트: {prompt_key} ({prompt_info['name']})")
    print(f"1. 기본모델 + {prompt_info['name']} + RAG")
    print(f"2. 파인튜닝모델 + {prompt_info['name']} + RAG")
    print("3. 초기 프로토타입")
    if not proto_client:
        print("프로토타입: PROTO_OPENAI_API_KEY 없음 - 결과 생략")
    print("=" * 60)

    for i, item in enumerate(EVAL_SET, 1):
        memories = search_memories(item["질문"], top_k=5)
        rag_prompt = build_rag_prompt(prompt_info["prompt"], memories)

        base_reply = generate(main_client, BASE_MODEL, rag_prompt, item["질문"])
        finetuned_reply = generate(main_client, FINETUNED_MODEL, rag_prompt, item["질문"])
        if proto_client:
            proto_reply = generate(proto_client, PROTO_MODEL, PROTO_PROMPT, item["질문"])
        else:
            proto_reply = "PROTO_OPENAI_API_KEY 없음"

        print(f"\n--- 질문 {i} ---")
        print(f"질문:       {item['질문']}")
        print(f"실제:       {item['실제']}")
        print_rag(memories)
        print(f"기본모델:   {base_reply}")
        print(f"파인튜닝:   {finetuned_reply}")
        print(f"프로토타입: {proto_reply}")
        print(f"평가포인트: {item['포인트']}")

    print("\n" + "=" * 60)
    print("완료!")
    print("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python -m prompt.eval_all [A|B|C]")
        print()
        for key, value in PROMPTS.items():
            print(f"  {key}: {value['name']} 프롬프트로 3-way 비교")
        sys.exit(0)

    selected_prompt = sys.argv[1].upper()
    if selected_prompt not in PROMPTS:
        print("A, B, C 중 선택하세요.")
        sys.exit(1)

    run_eval(selected_prompt)
