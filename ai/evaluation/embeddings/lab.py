"""3-B 검색 랩 — 기억 전체 임베딩 → 질의별 top-5 → Recall@5.

P0(코사인만) vs P1(재정렬: 유사도 + 중요도 + 태그겹침) 비교.
importance·tags는 3-A가 아직 안 뽑으므로 P1은 옵션(있으면 사용).
pgvector 실측은 별도(--pgvector, Docker 필요) — 기본은 in-memory 코사인.

Usage:
    python -m evaluation.embeddings.lab            # P0 Recall@5
    python -m evaluation.embeddings.lab --rerank   # P1 비교(중요도·태그 있을 때)
"""

import argparse
import json
import math
from pathlib import Path

from app.core.config import load_settings
from app.pipeline.embeddings.service import embed_texts
from evaluation.common import REPO_ROOT, RESULTS_DIR
from evaluation.embeddings.queries import QUERIES

MEMORY_DIR = RESULTS_DIR / "memory"


def load_all_memories() -> list[dict]:
    """정답 녹음들의 기억 전체를 (stem, index, text, meta)로 로드."""
    items = []
    for stem in sorted({stem for _, stem in QUERIES}):
        body = json.loads((MEMORY_DIR / f"{stem}.memory.json").read_text(encoding="utf-8"))
        for i, seg in enumerate(body["memorySegments"]):
            items.append(
                {
                    "stem": stem,
                    "text": seg["memoryText"],
                    "importance": seg.get("importanceScore"),
                    "tags": set(seg.get("tags") or []),
                }
            )
    return items


_QUERY_TAG_HINTS = {
    "음식요리": ["양념", "김치", "국", "끓", "만들", "요리", "도시락", "죽", "찌개"],
    "건강병원": ["병원", "주사", "예방", "아프", "약", "건강"],
    "명절기념일": ["새해", "생일", "명절"],
    "날씨계절": ["추운", "눈", "겨울", "날씨"],
    "가족안부": ["인사", "안부"],
}


def infer_query_tags(query: str) -> set:
    tags = set()
    for tag, hints in _QUERY_TAG_HINTS.items():
        if any(h in query for h in hints):
            tags.add(tag)
    return tags


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rerank", action="store_true")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--w1", type=float, default=0.15)  # importance 가중
    parser.add_argument("--w2", type=float, default=0.10)  # 태그겹침 가중
    args = parser.parse_args()

    settings = load_settings(REPO_ROOT / ".env")
    memories = load_all_memories()
    print(f"기억 {len(memories)}건 임베딩 중...")
    mem_vectors = embed_texts([m["text"] for m in memories], settings=settings)
    query_vectors = embed_texts([q for q, _ in QUERIES], settings=settings)
    query_tag_list = [infer_query_tags(q) for q, _ in QUERIES]

    hits = 0
    print(f"\n[검색 Recall@{args.k}]  {'(P1 재정렬)' if args.rerank else '(P0 코사인)'}")
    for (query, answer_stem), qvec, query_tags in zip(QUERIES, query_vectors, query_tag_list):
        scored = []
        for mem, mvec in zip(memories, mem_vectors):
            sim = cosine(qvec, mvec)
            score = sim
            if args.rerank:
                imp = (mem["importance"] or 5) / 10
                overlap = (len(mem["tags"] & query_tags) / len(query_tags)) if query_tags else 0.0
                score = sim + args.w1 * imp + args.w2 * overlap
            scored.append((score, sim, mem))
        scored.sort(key=lambda x: x[0], reverse=True)
        topk = scored[: args.k]
        hit = any(mem["stem"] == answer_stem for _, _, mem in topk)
        hits += hit
        top_stem = topk[0][2]["stem"]
        mark = "✅" if hit else "❌"
        print(f"  {mark} {query}  → 정답 {answer_stem} / top1 {top_stem}({topk[0][1]:.2f})")

    recall = hits / len(QUERIES)
    print(f"\nRecall@{args.k} = {hits}/{len(QUERIES)} = {recall:.1%} (목표 ≥ 0.8)")
    print("판정:", "합격" if recall >= 0.8 else "미달")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
