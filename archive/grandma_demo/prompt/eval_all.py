"""
9가지 조합 평가 스크립트.
인자로 조합 번호(1~9)를 받아서 실행.

사용법:
  python -m prompt.eval_all 1   # 기본GPT + 상세A + RAG없음
  python -m prompt.eval_all 2   # 기본GPT + 최소B + RAG없음
  ...
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from openai import OpenAI
from prompt.system_prompt_A import SYSTEM_PROMPT as PROMPT_A
from prompt.system_prompt_B import SYSTEM_PROMPT as PROMPT_B
from prompt.eval_questions import EVAL_SET
from memory.embeddings import search_memories

load_dotenv()

# API 키
MY_KEY = os.getenv("OPENAI_API_KEY")
PROTO_KEY = "sk-REDACTED"

# 대표 프롬프트
PROTO_PROMPT = """당신은 83세의 따뜻하고 다정한 할머니 '신금자'입니다. 자식들과 손자손녀들을 무척 사랑하며, 항상 그들의 건강과 안녕을 걱정합니다. 대화할 때는 할머니의 말투로 이야기합니다. 6달 전에 세상을 떠났으며 평소 독실한 신자이셨고 베푸는 삶을 사셨으며 현재 천국에 계십니다. 할머니는 대화를 무척 좋아하시고, 손자손녀의 이야기에 진심으로 관심을 갖고 계십니다.

행동 가이드라인:
- 짧게 1-2문장으로 답변하되, 대화를 끝내지 말고 질문이나 호기심을 표현하세요.
- 손자손녀의 이야기에 관심을 보이고 가끔은 추가적인 질문도 하세요.
- 매번 똑같은 건강 걱정 패턴을 피하고 다양하게 반응하세요.
- 손자손녀가 힘들어할 때는 따뜻한 위로와 격려의 말을 건네세요.
- 손자손녀가 기뻐하거나 잘한 일에 대해서는 아낌없이 칭찬하고 자랑스러워하는 감정을 표현하세요."""

CONFIGS = {
    1: {"name": "기본GPT + 상세A + RAG없음", "key": MY_KEY, "model": "gpt-4.1-mini", "prompt": PROMPT_A, "rag": False},
    2: {"name": "기본GPT + 최소B + RAG없음", "key": MY_KEY, "model": "gpt-4.1-mini", "prompt": PROMPT_B, "rag": False},
    3: {"name": "기본GPT + 상세A + RAG있음", "key": MY_KEY, "model": "gpt-4.1-mini", "prompt": PROMPT_A, "rag": True},
    4: {"name": "기본GPT + 최소B + RAG있음", "key": MY_KEY, "model": "gpt-4.1-mini", "prompt": PROMPT_B, "rag": True},
    5: {"name": "파인튜닝 + 상세A + RAG없음", "key": MY_KEY, "model": "ft:gpt-4.1-mini-2025-04-14:talkto::DeJqGFij", "prompt": PROMPT_A, "rag": False},
    6: {"name": "파인튜닝 + 최소B + RAG없음", "key": MY_KEY, "model": "ft:gpt-4.1-mini-2025-04-14:talkto::DeJqGFij", "prompt": PROMPT_B, "rag": False},
    7: {"name": "파인튜닝 + 상세A + RAG있음", "key": MY_KEY, "model": "ft:gpt-4.1-mini-2025-04-14:talkto::DeJqGFij", "prompt": PROMPT_A, "rag": True},
    8: {"name": "파인튜닝 + 최소B + RAG있음", "key": MY_KEY, "model": "ft:gpt-4.1-mini-2025-04-14:talkto::DeJqGFij", "prompt": PROMPT_B, "rag": True},
    9: {"name": "대표 prototype", "key": PROTO_KEY, "model": "ft:gpt-4.1-mini-2025-04-14:picky::C0g3PIpn", "prompt": PROTO_PROMPT, "rag": False},
}


def run_eval(config_num):
    cfg = CONFIGS[config_num]
    client = OpenAI(api_key=cfg["key"])

    print("=" * 60)
    print(f"[{config_num}] {cfg['name']}")
    print(f"모델: {cfg['model']}")
    print(f"RAG: {'있음 (top_k=5)' if cfg['rag'] else '없음'}")
    print("=" * 60)

    for i, item in enumerate(EVAL_SET, 1):
        prompt = cfg["prompt"]

        if cfg["rag"]:
            memories = search_memories(item["질문"], top_k=5)
            if memories:
                memory_text = "\n".join([f"- {m['title']}: {m['content']}" for m in memories])
                prompt = prompt + f"\n\n[할머니의 관련 기억]\n{memory_text}"

        response = client.chat.completions.create(
            model=cfg["model"],
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": item["질문"]},
            ],
        )
        ai_reply = response.choices[0].message.content

        print(f"\n--- 질문 {i} ---")
        print(f"질문:     {item['질문']}")
        print(f"실제:     {item['실제']}")
        print(f"AI:       {ai_reply}")
        print(f"평가포인트: {item['포인트']}")

    print("\n" + "=" * 60)
    print("완료!")
    print("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python -m prompt.eval_all [번호]")
        print()
        for num, cfg in CONFIGS.items():
            print(f"  {num}: {cfg['name']}")
        sys.exit(0)

    config_num = int(sys.argv[1])
    if config_num not in CONFIGS:
        print(f"1~9 중 선택하세요.")
        sys.exit(1)

    run_eval(config_num)
