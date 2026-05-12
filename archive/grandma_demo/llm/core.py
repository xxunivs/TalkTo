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


def generate_response(message: str, history: list = None) -> str:
    """
    백엔드가 호출하는 핵심 함수.
    사용자 메시지에 관련된 기억을 검색해서 GPT한테 같이 넘긴다.

    매개변수:
        message: 사용자가 지금 보낸 메시지
        history: 이전 대화 내역 리스트 (백엔드가 넘겨줌)

    반환값:
        할머니 답변 텍스트 (str)
    """
    if history is None:
        history = []

    # 1. 장기 기억 검색 (top 5)
    long_term = search_memories(message, top_k=5)

    # 2. 단기 기억 전부 (중요도 6 이상만 저장된 것들)
    short_term = get_active_memories()

    # 3. 기억을 텍스트로 변환
    memory_parts = []
    if long_term:
        lt_text = "\n".join([f"- {m['title']}: {m['content']}" for m in long_term])
        memory_parts.append(f"[장기 기억]\n{lt_text}")
    if short_term:
        st_text = "\n".join([f"- {m['content']}" for m in short_term])
        memory_parts.append(f"[최근 대화에서 알게 된 것]\n{st_text}")
        # 회상된 단기 기억 강도 갱신
        for m in short_term:
            on_memory_recalled(m["id"])

    memory_section = "\n\n" + "\n\n".join(memory_parts) if memory_parts else ""

    # 3. 프롬프트 조립
    messages = []
    messages.append({"role": "system", "content": SYSTEM_PROMPT + memory_section})
    messages.extend(history)
    messages.append({"role": "user", "content": message})

    # 4. GPT 호출
    response = client.chat.completions.create(
        model="gpt-4.1-mini",  # RAG 테스트용: 파인튜닝 없이 기본 모델
        messages=messages,
    )

    return response.choices[0].message.content


# 터미널에서 직접 실행할 때만 동작하는 테스트용 대화 루프
if __name__ == "__main__":
    history = []
    print("할머니와 대화를 시작합니다. (종료: q)")
    while True:
        user_input = input("\n나: ")
        if user_input == "q":
            break

        reply = generate_response(user_input, history)
        print(f"할머니: {reply}")

        # 단기 기억 추출 (중요도 6 이상이면 자동 저장)
        mem_result = extract_and_save(user_input, reply)
        if mem_result["saved"]:
            print(f"  [기억 저장] {mem_result['memory']['content']}")

        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": reply})
