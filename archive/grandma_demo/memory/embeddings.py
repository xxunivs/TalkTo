"""
기억 chunk 임베딩 생성 + 검색 모듈.

- memories.json을 읽어서 각 chunk를 벡터로 변환
- 사용자 질문과 가장 관련 있는 기억을 검색
"""
import os
import sys
import json
import numpy as np
from dotenv import load_dotenv
from openai import OpenAI

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

MEMORIES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "memories.json")
EMBEDDINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "memory_embeddings.json")
MODEL = "text-embedding-3-small"


def load_memories():
    """memories.json 읽기"""
    with open(MEMORIES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_embedding(text):
    """텍스트를 벡터로 변환"""
    response = client.embeddings.create(
        model=MODEL,
        input=text,
    )
    return response.data[0].embedding


def build_embeddings():
    """모든 기억 chunk의 임베딩을 생성하고 저장"""
    memories = load_memories()
    print(f"기억 {len(memories)}개 임베딩 생성 중...")

    results = []
    for i, mem in enumerate(memories):
        # 제목 + 내용을 합쳐서 임베딩
        text = f"{mem['title']}: {mem['content']}"
        embedding = get_embedding(text)
        results.append({
            "id": mem["id"],
            "embedding": embedding,
        })
        print(f"  [{i+1}/{len(memories)}] {mem['id']}")

    with open(EMBEDDINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False)

    print(f"완료! {EMBEDDINGS_PATH}에 저장됨")


def cosine_similarity(a, b):
    """두 벡터의 코사인 유사도 계산"""
    a = np.array(a)
    b = np.array(b)
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))


def search_memories(query, top_k=5):
    """
    사용자 질문과 가장 관련 있는 기억을 검색.

    매개변수:
        query: 사용자 질문 (예: "할머니 불고기 어떻게 만들어?")
        top_k: 반환할 기억 개수

    반환값:
        관련 기억 리스트 [{"id", "title", "content", "score"}, ...]
    """
    # 임베딩 파일 로드
    with open(EMBEDDINGS_PATH, "r", encoding="utf-8") as f:
        embeddings_data = json.load(f)

    # 기억 원본 로드
    memories = load_memories()
    mem_dict = {m["id"]: m for m in memories}

    # 질문을 벡터로 변환
    query_embedding = get_embedding(query)

    # 각 기억과 유사도 계산
    scores = []
    for item in embeddings_data:
        score = cosine_similarity(query_embedding, item["embedding"])
        scores.append((item["id"], score))

    # 유사도 높은 순 정렬
    scores.sort(key=lambda x: x[1], reverse=True)

    # top_k개 반환
    results = []
    for mem_id, score in scores[:top_k]:
        mem = mem_dict[mem_id]
        results.append({
            "id": mem["id"],
            "title": mem["title"],
            "content": mem["content"],
            "score": round(score, 4),
        })

    return results


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--build":
        build_embeddings()
    else:
        # 검색 테스트
        test_queries = [
            "할머니 불고기 어떻게 만들어?",
            "이종서 아들은 뭐해?",
            "할머니 어디 아파?",
            "콩나물국 끓이는 법",
            "할머니 매실청 만드는 법",
            "할머니 된장찌개 어떻게 해?",
            "향이는 뭐하는 애야?",
            "할아버지는 요즘 뭐하셔?",
            "할머니 건강하려면 어떻게 해야 돼?",
            "할머니 신림동 기억나?",
        ]
        for query in test_queries:
            print(f"\n질문: {query}")
            results = search_memories(query, top_k=5)
            for r in results:
                print(f"  [{r['score']}] {r['title']}: {r['content'][:50]}...")
