"""기억 검색 Recall@k, 기억 전체 임베딩 후 질의별 top-k 코사인 검색"""

import argparse
import json
import math

from app.setting import load_settings
from evaluation.common import REPO_ROOT, RESULTS_DIR, write_summary_md
from evaluation.embeddings.queries import QUERIES
from pipeline.s3_synthesis.d_build_memory_embedding import embed_texts

MEMORY_DIR = RESULTS_DIR / "memory"


def load_all_memories() -> list[dict]:
    """정답 녹음들의 기억 전체 (stem, text, importance, tags)"""
    items = []
    for stem in sorted({stem for _, stem in QUERIES}):
        body = json.loads((MEMORY_DIR / f"{stem}.memory.json").read_text(encoding="utf-8"))
        for seg in body["memorySegments"]:
            items.append(
                {
                    "stem": stem,
                    "text": seg["memoryText"],
                    "importance": seg.get("importanceScore"),
                    "tags": set(seg.get("tags") or []),
                }
            )
    return items


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()

    settings = load_settings(REPO_ROOT / ".env")
    memories = load_all_memories()
    print(f"기억 {len(memories)}건 임베딩 중...")
    mem_vectors = embed_texts([m["text"] for m in memories], settings=settings)
    query_vectors = embed_texts([q for q, _ in QUERIES], settings=settings)

    hits = 0
    rows = []
    print(f"\n[검색 Recall@{args.k}] (P0 코사인 — 재정렬은 실험 결과 검색 악화로 미채택)")
    for (query, answer_stem), qvec in zip(QUERIES, query_vectors, strict=True):
        scored = [
            (cosine(qvec, mvec), mem) for mem, mvec in zip(memories, mem_vectors, strict=True)
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        topk = scored[: args.k]
        hit = any(mem["stem"] == answer_stem for _, mem in topk)
        hits += hit
        top_score, top_mem = topk[0]
        top_stem = top_mem["stem"]
        mark = "✅" if hit else "❌"
        rows.append((mark, query, answer_stem, top_stem, top_score))
        print(f"  {mark} {query}  → 정답 {answer_stem} / top1 {top_stem}({top_score:.2f})")

    recall = hits / len(QUERIES)
    print(f"\nRecall@{args.k} = {hits}/{len(QUERIES)} = {recall:.1%} (목표 ≥ 0.8)")
    print("판정:", "합격" if recall >= 0.8 else "미달")

    lines = [
        f"**Recall@{args.k} = {hits}/{len(QUERIES)} = {recall:.1%}** "
        f"(목표 ≥ 0.8 → {'합격' if recall >= 0.8 else '미달'})",
        "",
        "| | 질의 | 정답 | top1 | 점수 |",
        "|---|---|---|---|---|",
        *[f"| {m} | {q} | {a} | {t} | {s:.2f} |" for m, q, a, t, s in rows],
    ]
    out = write_summary_md(
        RESULTS_DIR / "embeddings_summary.md", f"3-B 임베딩 검색 Recall@{args.k}", lines
    )
    print(f"→ 저장: {out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
