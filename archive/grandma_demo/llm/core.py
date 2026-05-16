import os
import sys
from dotenv import load_dotenv
from openai import OpenAI

# 프로젝트 루트 경로 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prompt.system_prompt import SYSTEM_PROMPT
from memory.embeddings import search_memories
from memory.short_term import get_active_memories, on_memory_recalled
from memory.extract import extract_and_save

# .env 파일에서 API 키 읽어오기
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

DEFAULT_CHAT_MODEL = "gpt-4.1-mini"


def get_chat_model() -> str:
    """운영 배포에서는 환경변수로 기본/파인튜닝 모델을 교체한다."""
    return os.getenv("OPENAI_CHAT_MODEL", DEFAULT_CHAT_MODEL)


def local_short_term_enabled() -> bool:
    """
    AI 서버 로컬 파일에 단기기억을 저장/조회할지 여부.

    운영에서는 백엔드가 기억 저장소의 기준이므로 기본값을 끈다.
    """
    value = os.getenv("PERSONA_ENABLE_LOCAL_SHORT_TERM", "false").lower()
    return value in {"1", "true", "yes", "on"}


def generate_response(message: str, history: list = None, memory_text: str = None) -> str:
    """
    백엔드가 호출하는 핵심 함수.

    매개변수:
        message: 사용자가 지금 보낸 메시지
        history: 이전 대화 내역 리스트 (백엔드가 넘겨줌)
        memory_text: 장기기억 텍스트 (server.py에서 조립해서 전달. 없으면 내부 검색)

    반환값:
        할머니 답변 텍스트 (str)
    """
    if history is None:
        history = []

    # 1. 장기기억 / 단기기억 텍스트 준비
    if memory_text is None:
        # 내부 JSON에서 검색 (로컬 테스트용)
        long_term = search_memories(message, top_k=5)

        memory_parts = []
        if long_term:
            lt_text = "\n".join([f"- {m['title']}: {m['content']}" for m in long_term])
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
        # server.py에서 조립한 장기기억 텍스트 사용
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

    # 2. 프롬프트 조립
    messages = []
    messages.append({"role": "system", "content": SYSTEM_PROMPT + memory_section})
    messages.extend(history)
    messages.append({"role": "user", "content": message})

    # 4. GPT 호출
    response = client.chat.completions.create(
        model=get_chat_model(),
        messages=messages,
    )

    return response.choices[0].message.content


# 터미널에서 직접 실행할 때만 동작하는 테스트용 대화 루프
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

        # 단기기억 추출 (중요도 6 이상이면 자동 저장)
        if local_short_term_enabled():
            mem_result = extract_and_save(user_input, reply)
            if mem_result["saved"]:
                print(f"  [단기기억 저장] {mem_result['memory']['content']}")

        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": reply})
