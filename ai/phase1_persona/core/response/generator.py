import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from prompts.system_prompt import SYSTEM_PROMPT
from core.memory.embeddings import search_memories
from core.memory.short_term import get_active_memories, on_memory_recalled
from core.memory.extract import extract_and_save

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

DEFAULT_CHAT_MODEL = "gpt-4.1-mini"

def get_chat_model() -> str:
    return os.getenv("OPENAI_CHAT_MODEL", DEFAULT_CHAT_MODEL)

def local_short_term_enabled() -> bool:
    value = os.getenv("PERSONA_ENABLE_LOCAL_SHORT_TERM", "false").lower()
    return value in {"1", "true", "yes", "on"}

def format_memory(title: str, content: str, tags: list = None) -> str:
    tag_text = f" [tags: {', '.join(tags)}]" if tags else ""
    return f"- {title}{tag_text}: {content}"

def generate_response(message: str, history: list = None, memory_text: str = None) -> str:
    if history is None:
        history = []

    if memory_text is None:

        long_term = search_memories(message, top_k=5)

        memory_parts = []
        if long_term:
            lt_text = "\n".join([
                format_memory(m["title"], m["content"], m.get("tags", []))
                for m in long_term
            ])
            memory_parts.append(f"[장기기억]\n{lt_text}")

        if local_short_term_enabled():
            short_term = get_active_memories()
        else:
            short_term = []
        if short_term:
            st_text = "\n".join([f"- {m['content']}" for m in short_term])
            memory_parts.append(f"[단기기억]\n{st_text}")
            for m in short_term:
                on_memory_recalled(m["id"])

        memory_section = "\n\n" + "\n\n".join(memory_parts) if memory_parts else ""
    else:

        memory_parts = []
        if memory_text.strip():
            memory_parts.append(f"[장기기억]\n{memory_text}")

        if local_short_term_enabled():
            short_term = get_active_memories()
        else:
            short_term = []
        if short_term:
            st_text = "\n".join([f"- {m['content']}" for m in short_term])
            memory_parts.append(f"[단기기억]\n{st_text}")
            for m in short_term:
                on_memory_recalled(m["id"])

        memory_section = "\n\n" + "\n\n".join(memory_parts) if memory_parts else ""

    messages = []
    messages.append({"role": "system", "content": SYSTEM_PROMPT + memory_section})
    messages.extend(history)
    messages.append({"role": "user", "content": message})

    response = client.chat.completions.create(
        model=get_chat_model(),
        messages=messages,
    )

    return response.choices[0].message.content

if __name__ == "__main__":
    os.environ.setdefault("PERSONA_ENABLE_LOCAL_SHORT_TERM", "true")
    history = []
    print("할머니와 대화를 시작합니다. (종료: q)")
    while True:
        user_input = input("\n나: ")
        if user_input == "q":
            break

        reply = generate_response(user_input, history)
        print(f"할머니: {reply}")

        if local_short_term_enabled():
            mem_result = extract_and_save(user_input, reply)
            if mem_result["saved"]:
                print(f"  [단기기억 저장] {mem_result['memory']['content']}")

        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": reply})
