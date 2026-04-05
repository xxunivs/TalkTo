#!/usr/bin/env python3
"""
dataset_univs 지식그래프를 터미널에서 조회하는 CLI 도구.

사용법:
  python3 view_knowledge_graph.py                    # 전체 요약
  python3 view_knowledge_graph.py "관심사"            # 특정 쿼리
  python3 view_knowledge_graph.py --nodes             # 그래프 노드 목록
"""

import os
import sys
import asyncio

from dotenv import load_dotenv
load_dotenv("/home/commit/cognee-mcp/.env.univs")
os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "false")

import litellm
litellm.drop_params = True

import cognee
from cognee.api.v1.search import SearchType

DATASET_NAME = "dataset_univs"


def configure_cognee():
    llm_config = {
        "llm_api_key": os.environ.get("LLM_API_KEY"),
        "llm_provider": os.environ.get("LLM_PROVIDER", "openai"),
        "llm_model": os.environ.get("LLM_MODEL"),
        "llm_instructor_mode": os.environ.get("LLM_INSTRUCTOR_MODE", "json_mode"),
    }
    endpoint = os.environ.get("LLM_ENDPOINT")
    if endpoint:
        llm_config["llm_endpoint"] = endpoint
    cognee.config.set_llm_config(llm_config)
    cognee.config.system_root_directory(os.environ.get("COGNEE_SYSTEM_DIR", "/home/commit/.cognee_system_univs"))
    cognee.config.data_root_directory(os.environ.get("COGNEE_DATA_DIR", "/home/commit/.cognee_data_univs"))


def show_nodes():
    """Kuzu 그래프에서 노드/엣지 직접 조회"""
    import kuzu
    db_path = os.environ.get("COGNEE_SYSTEM_DIR", "/home/commit/.cognee_system_univs") + "/databases/cognee_graph_kuzu"
    db = kuzu.Database(db_path, read_only=True)
    conn = kuzu.Connection(db)

    print("=== 노드 (Entities) ===\n")
    try:
        result = conn.execute("MATCH (n:Node) RETURN n.id, n.name, n.type, n.properties LIMIT 50")
        while result.has_next():
            row = result.get_next()
            name = row[1] or row[0]
            ntype = row[2] or ""
            print(f"  [{ntype}] {name}")
    except Exception as e:
        print(f"  노드 조회 오류: {e}")

    print("\n=== 관계 (Edges) ===\n")
    try:
        result = conn.execute("MATCH (a:Node)-[r:EDGE]->(b:Node) RETURN a.name, r.relationship_name, b.name LIMIT 50")
        while result.has_next():
            row = result.get_next()
            print(f"  {row[0]} --[{row[1]}]--> {row[2]}")
    except Exception as e:
        print(f"  관계 조회 오류: {e}")


async def search_graph(query: str):
    configure_cognee()
    print(f"=== 검색: \"{query}\" ===\n")
    results = await cognee.search(
        query,
        query_type=SearchType.GRAPH_COMPLETION,
        datasets=[DATASET_NAME],
    )
    if not results:
        print("  결과 없음")
        return
    for i, r in enumerate(results[:10]):
        print(f"[{i+1}] {str(r)}")
        print()


async def full_summary():
    configure_cognee()
    queries = [
        "이 사용자는 어떤 사람인가? 성격, 관심사, 취미, 직업 등 전체 요약",
        "최근 대화 주제들",
    ]
    for q in queries:
        print(f"=== {q} ===\n")
        results = await cognee.search(q, query_type=SearchType.GRAPH_COMPLETION, datasets=[DATASET_NAME])
        if results:
            for r in results[:3]:
                print(f"  {str(r)[:300]}")
        else:
            print("  결과 없음")
        print()


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)

    if "--nodes" in sys.argv:
        show_nodes()
    elif len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        asyncio.run(search_graph(sys.argv[1]))
    else:
        show_nodes()
        print()
        asyncio.run(full_summary())
