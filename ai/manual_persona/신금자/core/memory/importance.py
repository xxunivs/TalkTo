import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

EVAL_PROMPT = """
다음은 할머니와 손녀의 대화 한 턴이다.
최근 대화 맥락을 참고해서, 현재 턴에서 나중에 기억해둘 만한 새로운 정보가 있는지 평가해줘.
최근 대화는 맥락 파악용이고, 저장 판단과 요약은 현재 턴에서 새로 확정되거나 구체화된 정보 중심으로 해줘.

[평가 기준]
- 감정적 중요도 (30%): 감정적으로 의미 있는 사건인가?
- 관계 중요도 (25%): 가족/인간관계에 대한 새로운 정보인가?
- 개인정보 중요도 (15%): 직장, 학교, 건강 등 개인 정보인가?
- 기억 가치 (20%): 다음 대화에서 언급할 만한 가치가 있는가?
- 연속성 중요도 (10%): 이전 대화와 연결되는 맥락인가?

[응답 형식 — 반드시 JSON으로만 답해]
{
  "importance": 1~10 사이 정수,
  "category": "가족" | "건강" | "직장" | "일상" | "감정" | "기타",
  "summary": "기억할 내용 한 줄 요약 (중요도 6 미만이면 빈 문자열)",
  "reason": "판단 이유 한 줄"
}

중요도 6 미만이면 기억할 필요 없음.
"응", "그래", "알았어" 같은 단순 맞장구는 중요도 1.
""".strip()

def format_history_for_importance(history: list | None) -> str:
    if not history:
        return "(없음)"

    lines = []
    for item in history[-10:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role", "")
        content = item.get("content", "")
        if not content:
            continue
        speaker = "손녀" if role == "user" else "할머니" if role == "assistant" else role
        lines.append(f"{speaker}: {content}")

    return "\n".join(lines) if lines else "(없음)"

def evaluate_importance(
    user_message: str,
    assistant_message: str,
    history: list | None = None,
) -> dict:
    try:
        conversation = (
            f"[최근 대화]\n{format_history_for_importance(history)}\n\n"
            f"[현재 턴]\n손녀: {user_message}\n할머니: {assistant_message}"
        )

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": EVAL_PROMPT},
                {"role": "user", "content": conversation},
            ],
            temperature=0,
        )

        result = json.loads(response.choices[0].message.content)
        return result

    except Exception as e:
        return {
            "importance": 0,
            "category": "",
            "summary": "",
            "reason": f"평가 실패: {str(e)}",
        }

if __name__ == "__main__":

    test_cases = [
        ("나 회사 그만뒀어", "어? 왜? 무슨 일 있었어?"),
        ("응", "그래."),
        ("할머니 나 오늘 남자친구 생겼어!", "어? 진짜? 어떤 사람인데?"),
        ("오늘 날씨 좀 춥네", "어. 따뜻하게 입어."),
        ("나 다음 달에 이사해", "어디로 가는데?"),
    ]

    for user_msg, assistant_msg in test_cases:
        result = evaluate_importance(user_msg, assistant_msg)
        print(f"\n손녀: {user_msg}")
        print(f"할머니: {assistant_msg}")
        print(f"→ 중요도: {result['importance']}, 카테고리: {result['category']}")
        print(f"  요약: {result['summary']}")
        print(f"  이유: {result['reason']}")
