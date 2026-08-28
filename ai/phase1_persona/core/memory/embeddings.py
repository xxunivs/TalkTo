import os
import sys
import json
from pathlib import Path
import numpy as np
from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

MEMORIES_PATH = ROOT / "data" / "memories.json"
EMBEDDINGS_PATH = ROOT / "data" / "memory_embeddings.json"
MODEL = "text-embedding-3-small"

KEYWORD_BOOST_RULES = [
    {
        "keywords": ["향이", "외손녀", "공공기관", "영어"],
        "memory_ids": ["family_hyangi"],
        "boost": 0.22,
    },
    {
        "keywords": ["정읍", "큰오빠", "어린 시절", "고향", "새어머니"],
        "memory_ids": ["identity_childhood", "identity_basic"],
        "boost": 0.24,
    },
    {
        "keywords": ["천주교", "성당", "신앙", "기도", "하늘", "평온", "지켜보고", "응원"],
        "memory_ids": ["religion_catholic"],
        "boost": 0.24,
    },
    {
        "keywords": ["돌아가", "돌아가셨", "사망", "임종", "심근경색", "중환자실", "스텐트"],
        "memory_ids": ["death_passing"],
        "boost": 0.36,
    },
    {
        "keywords": ["보고 싶", "그리워", "그립"],
        "memory_ids": ["death_passing", "religion_catholic"],
        "boost": 0.18,
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
        "keywords": ["큰고모", "작은고모", "이신화", "이신경", "딸"],
        "memory_ids": ["family_daughters", "family_big_daughter_ishinhwa", "family_small_daughter_ishingyeong"],
        "boost": 0.22,
    },
    {
        "keywords": ["큰고모부", "작은고모부", "윤보영", "남석찬"],
        "memory_ids": ["family_aunt_husbands"],
        "boost": 0.24,
    },
    {
        "keywords": ["남향", "홍윤현", "국립중앙도서관"],
        "memory_ids": ["family_namhyang"],
        "boost": 0.34,
    },
    {
        "keywords": ["남규하", "남해욱"],
        "memory_ids": ["family_namgyuha"],
        "boost": 0.30,
    },
    {
        "keywords": ["윤선혜", "윤선화", "윤유진"],
        "memory_ids": ["family_yoon_cousins"],
        "boost": 0.30,
    },
    {
        "keywords": ["친척", "큰오빠", "신희용", "신욱", "고모"],
        "memory_ids": ["family_extended_relatives", "identity_childhood"],
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
    {
        "keywords": ["희생", "가족 사랑", "가족에게", "어떤 사람", "자식", "손주", "사랑", "챙겨"],
        "memory_ids": ["values_family_sacrifice", "food_care"],
        "boost": 0.20,
    },
    {
        "keywords": ["정직", "거짓말", "싸우지", "다투지", "화목"],
        "memory_ids": ["values_honesty_harmony"],
        "boost": 0.22,
    },
    {
        "keywords": ["용돈", "반찬", "과일", "죽", "마늘장아찌", "오이소박이", "음식으로"],
        "memory_ids": ["food_care"],
        "boost": 0.22,
    },
    {
        "keywords": ["수내동", "분당", "중앙공원", "이마트", "홈플러스"],
        "memory_ids": ["life_late_places", "life_home_daily_scene"],
        "boost": 0.22,
    },
    {
        "keywords": ["건대", "면목동", "중곡동", "어디 살", "거주", "이사"],
        "memory_ids": ["life_residence_history"],
        "boost": 0.24,
    },
    {
        "keywords": ["담배", "술", "아버지 건강", "이종서 건강"],
        "memory_ids": ["family_father_health_worry"],
        "boost": 0.28,
    },
    {
        "keywords": ["용돈", "절약", "아껴", "돈을 어떻게"],
        "memory_ids": ["values_saving_allowance"],
        "boost": 0.24,
    },
    {
        "keywords": ["전화", "받을 때", "끊을 때", "잘 자", "사랑한다"],
        "memory_ids": ["speech_call_open_close"],
        "boost": 0.22,
    },
    {
        "keywords": ["말투", "방언", "표현", "어투"],
        "memory_ids": ["speech_dialect_expressions", "speech_common_family_phrases", "speech_call_open_close"],
        "boost": 0.24,
    },
    {
        "keywords": ["감정 표현", "담담", "서운", "유머"],
        "memory_ids": ["emotion_style"],
        "boost": 0.22,
    },
    {
        "keywords": ["마지막", "스텐트", "응급실", "중환자실", "심장 통증"],
        "memory_ids": ["health_final_course", "death_passing"],
        "boost": 0.30,
    },
    {
        "keywords": ["마지막 못", "못 봐서", "못 가서", "갔어야", "미안", "죄책감", "후회", "임종 못"],
        "memory_ids": ["memorial_guilt_comfort"],
        "boost": 0.34,
    },
]

def load_memories():
    with open(MEMORIES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def get_embedding(text):
    response = client.embeddings.create(
        model=MODEL,
        input=text,
    )
    return response.data[0].embedding

def build_embeddings():
    memories = load_memories()
    print(f"장기기억 {len(memories)}개 임베딩 생성 중...")

    results = []
    for i, item in enumerate(memories):

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
    a = np.array(a)
    b = np.array(b)
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

def keyword_boost(query, memory_id):
    matched_boost = 0.0
    for rule in KEYWORD_BOOST_RULES:
        if memory_id not in rule["memory_ids"]:
            continue
        if any(keyword in query for keyword in rule["keywords"]):
            matched_boost += rule["boost"]
    return matched_boost

def search_memories(query, top_k=5):

    with open(EMBEDDINGS_PATH, "r", encoding="utf-8") as f:
        embeddings_data = json.load(f)

    memories = load_memories()
    item_dict = {m["id"]: m for m in memories}

    query_embedding = get_embedding(query)

    scores = []
    for item in embeddings_data:
        base_score = cosine_similarity(query_embedding, item["embedding"])
        boost = keyword_boost(query, item["id"])
        final_score = base_score + boost
        scores.append((item["id"], final_score, base_score, boost))

    scores.sort(key=lambda x: x[1], reverse=True)

    results = []
    for mem_id, score, base_score, boost in scores[:top_k]:
        mem = item_dict[mem_id]
        results.append({
            "id": mem["id"],
            "title": mem["title"],
            "content": mem["content"],
            "tags": mem.get("tags", []),
            "score": round(score, 4),
            "base_score": round(base_score, 4),
            "boost": round(boost, 4),
        })

    return results

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--build":
        build_embeddings()
    else:

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
