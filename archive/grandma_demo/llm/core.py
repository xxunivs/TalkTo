import os
import sys
from dotenv import load_dotenv
from openai import OpenAI

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prompt.system_prompt import SYSTEM_PROMPT

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def generate_response(message: str, history: list = None) -> str:
    if history is None:
        history = []

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": message})

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
    )

    return response.choices[0].message.content


if __name__ == "__main__":
    history = []
    print("할머니와 대화를 시작합니다. (종료: q)")
    while True:
        user_input = input("\n나: ")
        if user_input == "q":
            break
        reply = generate_response(user_input, history)
        print(f"할머니: {reply}")
        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": reply})
