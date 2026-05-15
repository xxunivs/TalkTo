"""
장기기억 임베딩 생성 + 검색 모듈.

- memories.json을 읽어서 각 chunk를 벡터로 변환
- 사용자 질문과 가장 관련 있는 장기기억을 검색
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

MEMORIES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "memories.json",
)
EMBEDDINGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "memory_embeddings.json",
)
MODEL = "text-embedding-3-small"


KEYWORD_BOOST_RULES = [
    {
        "keywords": ["향이", "외손녀", "공공기관", "영어"],
        "memory_ids": ["family_hyangi"],
        "boost": 0.22,
    },
    {
        "keywords": ["엄마", "아빠", "부모"],
        "memory_ids": ["family_hyangi_parents"],
        "boost": 0.16,
    },
    {
        "keywords": ["이종서 아들", "준혁", "이준혁", "의예과"],
        "memory_ids": ["family_junhyuk", "family_son"],
        "boost": 0.22,
    },
    {
        "keywords": ["할아버지", "이재산", "남편"],
        "memory_ids": ["family_husband"],
        "boost": 0.20,
    },
    {
        "keywords": ["아파", "아프", "허리", "엉덩이", "진통제", "수술"],
        "memory_ids": ["health_back"],
        "boost": 0.24,
    },
    {
        "keywords": ["당뇨", "혈당", "서울대병원", "내과"],
        "memory_ids": ["health_diabetes", "place_hospital"],
        "boost": 0.20,
    },
    {
        "keywords": ["병원", "정형외과", "검사", "진료"],
        "memory_ids": ["place_hospital", "health_diabetes", "health_back"],
        "boost": 0.18,
    },
    {
        "keywords": ["밖에서", "사먹", "사 먹", "파는 음식", "외식", "집밥", "설탕"],
        "memory_ids": ["values_food"],
        "boost": 0.24,
    },
    {
        "keywords": ["도시락"],
        "memory_ids": ["episode_dosirak", "family_hyangi"],
        "boost": 0.18,
    },
    {
        "keywords": ["상 받", "수상", "상품", "회사에서 상"],
        "memory_ids": ["episode_award", "family_hyangi"],
        "boost": 0.18,
    },
    {
        "keywords": ["콩나물국", "콩나물"],
        "memory_ids": ["recipe_kongnamul"],
        "boost": 0.22,
    },
    {
        "keywords": ["불고기"],
        "memory_ids": ["recipe_bulgogi"],
        "boost": 0.22,
    },
    {
        "keywords": ["된장찌개", "된장"],
        "memory_ids": ["recipe_doenjangjjigae"],
        "boost": 0.22,
    },
    {
        "keywords": ["매실청", "매실"],
        "memory_ids": ["recipe_maesil"],
        "boost": 0.22,
    },
    {
        "keywords": ["신림동", "시장"],
        "memory_ids": ["episode_sinrimdong"],
        "boost": 0.22,
    },
    {
        "keywords": ["건강", "운동", "면역력", "건강하려면"],
        "memory_ids": ["values_health"],
        "boost": 0.20,
    },
]


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
    """모든 장기기억 chunk의 임베딩을 생성하고 저장"""
    memories = load_memories()
    print(f"장기기억 {len(memories)}개 임베딩 생성 중...")

    results = []
    for i, item in enumerate(memories):
        # 제목 + 내용을 합쳐서 임베딩
        text = f"{item['title']}: {item['content']}"
        embedding = get_embedding(text)
        results.append({
            "id": item["id"],
            "embedding": embedding,
        })
        print(f"  [{i+1}/{len(memories)}] {item['id']}")

    with open(EMBEDDINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False)

    print(f"완료! {EMBEDDINGS_PATH}에 저장됨")


def cosine_similarity(a, b):
    """두 벡터의 코사인 유사도 계산"""
    a = np.array(a)
    b = np.array(b)
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))


def keyword_boost(query, memory_id):
    """
    짧은 구어체 질문에서 임베딩 검색이 빗나가는 경우를 보정한다.

    예: "향이는 뭐하는 애야?"는 임베딩만 쓰면 향이 부모 기억이 더 높게
    나올 수 있으므로, 명시 키워드가 있으면 해당 기억에 작은 가산점을 준다.
    """
    matched_boost = 0.0
    for rule in KEYWORD_BOOST_RULES:
        if memory_id not in rule["memory_ids"]:
            continue
        if any(keyword in query for keyword in rule["keywords"]):
            matched_boost += rule["boost"]
    return matched_boost


def search_memories(query, top_k=5):
    """
    사용자 질문과 가장 관련 있는 장기기억을 검색.

    매개변수:
        query: 사용자 질문 (예: "할머니 불고기 어떻게 만들어?")
        top_k: 반환할 기억 개수

    반환값:
        관련 장기기억 리스트 [{"id", "title", "content", "score"}, ...]
    """
    # 임베딩 파일 로드
    with open(EMBEDDINGS_PATH, "r", encoding="utf-8") as f:
        embeddings_data = json.load(f)

    # 장기기억 원본 로드
    memories = load_memories()
    item_dict = {m["id"]: m for m in memories}

    # 질문을 벡터로 변환
    query_embedding = get_embedding(query)

    # 각 기억과 유사도 계산 + 키워드 보정
    scores = []
    for item in embeddings_data:
        base_score = cosine_similarity(query_embedding, item["embedding"])
        boost = keyword_boost(query, item["id"])
        final_score = base_score + boost
        scores.append((item["id"], final_score, base_score, boost))

    # 유사도 높은 순 정렬
    scores.sort(key=lambda x: x[1], reverse=True)

    # top_k개 반환
    results = []
    for mem_id, score, base_score, boost in scores[:top_k]:
        mem = item_dict[mem_id]
        results.append({
            "id": mem["id"],
            "title": mem["title"],
            "content": mem["content"],
            "score": round(score, 4),
            "base_score": round(base_score, 4),
            "boost": round(boost, 4),
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
