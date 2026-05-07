import os
import sys
from dotenv import load_dotenv
from openai import OpenAI

# prompt/ 폴더를 import 할 수 있도록 프로젝트 루트 경로 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prompt.system_prompt import SYSTEM_PROMPT

# .env 파일에서 API 키 읽어오기
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def generate_response(message: str, history: list = None) -> str:
    """
    백엔드가 호출하는 핵심 함수.

    매개변수:
        message: 사용자가 지금 보낸 메시지
                 예: "할머니야 안녕"

        history: 이전 대화 내역 리스트 (백엔드가 저장해두고 넘겨줌)
                 처음 대화할 때는 빈 리스트 []
                 대화가 쌓이면 이런 형태:
                 [
                     {"role": "user", "content": "할머니야 안녕"},
                     {"role": "assistant", "content": "어 잘 있었어? 밥은 먹었니?"},
                     {"role": "user", "content": "응 먹었어"},
                     {"role": "assistant", "content": "뭐 먹었는데?"},
                 ]
                 이걸 GPT한테 같이 보내야 "아까 뭐라고 했었는데"가 이어짐

    반환값:
        할머니 답변 텍스트 (str)
        예: "그래 잘 먹었으면 됐다. 뭐 먹었는데?"
    """
    if history is None:
        history = []

    # GPT한테 보낼 메시지를 순서대로 조립
    messages = []

    # 1. 시스템 프롬프트: "너는 할머니야" (GPT의 역할 설정)
    messages.append({"role": "system", "content": SYSTEM_PROMPT})

    # 2. 이전 대화 내역: 맥락을 이어가기 위해 이전 대화를 붙임
    messages.extend(history)

    # 3. 지금 사용자가 보낸 메시지
    messages.append({"role": "user", "content": message})

    # GPT API 호출 → 할머니 답변 받기
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
    )

    # 응답에서 텍스트만 꺼내서 반환
    return response.choices[0].message.content


# 터미널에서 직접 실행할 때만 동작하는 테스트용 대화 루프
# 백엔드가 호출할 때는 위의 generate_response() 함수만 사용
if __name__ == "__main__":
    history = []
    print("할머니와 대화를 시작합니다. (종료: q)")
    while True:
        user_input = input("\n나: ")
        if user_input == "q":
            break

        # 할머니 답변 생성
        reply = generate_response(user_input, history)
        print(f"할머니: {reply}")

        # 대화 내역에 추가 (다음 호출 때 맥락 유지용)
        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": reply})
