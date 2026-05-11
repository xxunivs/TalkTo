"""
대표 prototype 파인튜닝 모델 성능 테스트.
대표 API 키 + 대표 파인튜닝 모델로 eval 질문을 돌려본다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import OpenAI
from prompt.eval_questions import EVAL_SET

# 대표 API 키 + 모델
client = OpenAI(api_key="sk-REDACTED")
MODEL = "ft:gpt-4.1-mini-2025-04-14:picky::C0g3PIpn"

# 대표가 쓰던 시스템 프롬프트 (prototype 할머니.json에서 가져옴)
SYSTEM_PROMPT = """당신은 83세의 따뜻하고 다정한 할머니 '신금자'입니다. 자식들과 손자손녀들을 무척 사랑하며, 항상 그들의 건강과 안녕을 걱정합니다. 대화할 때는 할머니의 말투로 이야기합니다. 6달 전에 세상을 떠났으며 평소 독실한 신자이셨고 베푸는 삶을 사셨으며 현재 천국에 계십니다. 할머니는 대화를 무척 좋아하시고, 손자손녀의 이야기에 진심으로 관심을 갖고 계십니다.

행동 가이드라인:
- 짧게 1-2문장으로 답변하되, 대화를 끝내지 말고 질문이나 호기심을 표현하세요.
- 손자손녀의 이야기에 관심을 보이고 가끔은 추가적인 질문도 하세요.
- 매번 똑같은 건강 걱정 패턴을 피하고 다양하게 반응하세요.
- 손자손녀가 힘들어할 때는 따뜻한 위로와 격려의 말을 건네세요.
- 손자손녀가 기뻐하거나 잘한 일에 대해서는 아낌없이 칭찬하고 자랑스러워하는 감정을 표현하세요."""


def run_eval():
    print("=" * 60)
    print("대표 prototype 파인튜닝 모델 평가")
    print(f"모델: {MODEL}")
    print("=" * 60)

    for i, item in enumerate(EVAL_SET, 1):
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
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
    print("비교 완료!")
    print("=" * 60)


if __name__ == "__main__":
    run_eval()
