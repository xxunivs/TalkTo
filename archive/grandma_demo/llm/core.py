import os
from dotenv import load_dotenv
from openai import OpenAI
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
    print(generate_response("할머니야, 안녕"))
