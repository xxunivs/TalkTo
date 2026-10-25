"""중간계층 경로의 '놓침'이 어디서 나오는지 가른다. OpenAI 호출 없음(사전 판정만 다시 돌린다).

중간계층 조건(layered@orch, ops@orch)의 fact 문항마다 사전 판정(coverage_v11)을 다시 돌려
known=False(= "모른다·되묻기" 지시가 붙음)였는지 보고, 채점 결과·가드 동작과 엇갈려 센다.
    놓침 = 사전 판정 지시 때문 / 가드 되묻기 때문 / 둘 다 아님(모델이 스스로 모른다고 함)
근거 기억은 모든 문항에서 프롬프트에 들어가 있으므로(검색 문제 아님), 놓침은 판정·가드의 낱말 비교에서 나온다.

    python experiments/prompt_compare/precheck_replay.py --batch <묶음 id>
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import compare as C  # noqa: E402


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True)
    batch = ap.parse_args().batch
    from orchestrator.contracts import MemoryItem, SessionState, Turn, TurnInput
    items = {r["iid"]: r for r in C.rj(C.DATA / "sets.jsonl")}
    by_id = C.memories()
    lines = ["# 중간계층 놓침 원인 (사전 판정 재생)", "",
             "| 조건 | 판정 | 가드 | 사전 판정 known=False | 문항 |", "|---|---|---|---|---|"]
    for cond, rules, prompt in (("layered-at-orch", "layered", "auto_layered_nocommon"), ("ops-at-orch", "legacy", "ops")):
        orch = C.build_orch(C.LLM(None, True), rules)
        text = (C.VAR / f"{prompt}.txt").read_text(encoding="utf-8")
        cnt: Counter = Counter()
        for f in sorted(C.RES.glob(f"answers_{cond}_r*_{batch}.judged.jsonl")):
            for r in C.rj(f):
                if r.get("type") != "fact" or "error" in r:
                    continue
                it = items[r["iid"]]
                mems = [MemoryItem(id=i, title=by_id[i]["title"], text=by_id[i]["body"], memory_type=by_id[i]["mtype"])
                        for i in it["mem_ids"] if i in by_id]
                hist = [Turn(role=h["role"], content=h["content"]) for h in it["history"]]
                turn = TurnInput(subject_id="s", message=it["turns"][0], persona_instructions=text, history=hist,
                                 be_memories=mems)
                run = orch.run(turn, until="precheck", state=SessionState(session_id=None, subject_id="s"))
                blocked = run.precheck.known is False
                cnt[(r["verdict"], (r.get("guard_actions") or ["-"])[0], blocked)] += 1
        for (v, g, b), n in sorted(cnt.items()):
            lines.append(f"| {cond} | {v} | {g} | {'예' if b else '아니오'} | {n} |")
    out = C.RES / f"precheck_replay_{C.stamp()}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"→ {out}")


if __name__ == "__main__":
    main()
