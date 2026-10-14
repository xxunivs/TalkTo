"""이미 만든 답(results/answers_*.jsonl)에 언어 누출 가드(orchestrator/lang_guard.py)를 돌려 본다. OpenAI 호출 없음.

보려는 것
  1) 지금 답에 맥락에 없던 문자 체계·영어 낱말·추론 문장이 얼마나 섞여 나오나(조건별 비율)
  2) 가드가 잘못 거는 경우가 있나 — 걸린 답을 전부 예시로 남겨 사람이 확인한다
맥락 = 문항의 가족 말(여러 턴이면 전부) + 앞 대화 + 그 문항에 넣은 기억 + 그 조건의 지시문.
원본 세트(orig, 실제 통화 질문)와 확장 세트(ext, 만든 문항)를 따로 센다.

    python experiments/prompt_compare/lang_scan.py [--glob "answers_*_<묶음>.jsonl"]
결과: results/lang_scan_<시각>.md(요약) / .jsonl(걸린 답 전부)
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import compare as C  # noqa: E402

from orchestrator.lang_guard import findings  # noqa: E402


def prompt_for(variant: str) -> str:
    """답 파일의 조건 이름 → 그 조건이 쓴 지시문."""
    f = C.VAR / f"{variant}.txt"
    if f.exists():
        return f.read_text(encoding="utf-8")
    try:
        import batch
        name = batch.CONDS.get(variant.replace("-at-", "@"), {}).get("prompt")
    except ImportError:
        name = None
    f = C.VAR / f"{name}.txt" if name else None
    return f.read_text(encoding="utf-8") if f and f.exists() else ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default="answers_*.jsonl")
    a = ap.parse_args()
    items = {r["iid"]: r for r in C.rj(C.DATA / "sets.jsonl")}
    by_id = C.memories()
    files = sorted(p for p in C.RES.glob(a.glob) if ".judged" not in p.name)
    if not files:
        raise SystemExit(f"답 파일 없음: {C.RES / a.glob}")
    counts: dict[tuple[str, str], Counter] = defaultdict(Counter)
    kinds: dict[str, Counter] = defaultdict(Counter)
    hits_rows = []
    prompts: dict[str, str] = {}
    for f in files:
        for r in C.rj(f):
            if "error" in r or r["iid"] not in items:
                continue
            it = items[r["iid"]]
            variant = r.get("variant", f.stem)
            prompt = prompts.setdefault(variant, prompt_for(variant))
            mem = " ".join(f"{by_id[i]['title']} {by_id[i]['body']}" for i in it.get("mem_ids", []) if i in by_id)
            hist = " ".join(h["content"] for h in it.get("history", []))
            dialogue = "\n".join([*it["turns"], hist])
            context = "\n".join([dialogue, mem, prompt])
            key = (f.stem.removeprefix("answers_"), it["set"])
            for k, ans in enumerate(r.get("answers") or []):
                hit = findings(ans, context, dialogue)
                counts[key]["answers"] += 1
                if hit:
                    counts[key]["hit"] += 1
                    for h in hit:
                        kinds[key[1]][h.split(":", 1)[0]] += 1
                    hits_rows.append({"file": f.name, "iid": r["iid"], "set": it["set"], "turn": k, "answer": ans,
                                      "findings": hit, "no_prompt": not prompt})
    stamp = C.stamp()
    C.wj(C.RES / f"lang_scan_{stamp}.jsonl", hits_rows)
    L = [f"# 언어 누출 스캔 ({stamp})", "", f"답 파일 {len(files)}개. 지시문을 못 찾은 조건은 맥락에서 지시문이 빠진다(no_prompt).",
         "", "| 파일(조건_반복) | 세트 | 답 | 걸린 답 | 비율 |", "|---|---|---|---|---|"]
    for (name, st), c in sorted(counts.items()):
        L.append(f"| {name} | {st} | {c['answers']} | {c['hit']} | {c['hit'] / max(1, c['answers']) * 100:.1f}% |")
    tot = {st: sum(c["answers"] for (n, s), c in counts.items() if s == st) for st in ("orig", "ext")}
    hit = {st: sum(c["hit"] for (n, s), c in counts.items() if s == st) for st in ("orig", "ext")}
    L += ["", "## 합계", ""] + [f"- {st}: {hit[st]}/{tot[st]} ({hit[st] / max(1, tot[st]) * 100:.2f}%), 종류 {dict(kinds[st])}"
                               for st in ("orig", "ext")]
    L += ["", "## 걸린 답 (사람 확인용)", ""]
    for h in hits_rows:
        L.append(f"- [{h['set']}] {h['file']} {h['iid']}#{h['turn']} {h['findings']}: {h['answer'][:160]!r}")
    out = C.RES / f"lang_scan_{stamp}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:8 + len(counts) + 4]))
    print(f"→ {out}")


if __name__ == "__main__":
    main()
