"""프롬프트 개선 실험 묶음 — 조건 여러 개 × 반복, 끊겨도 이어서, 끝나면 반복 평균·흔들림·조건 간 차이 신뢰구간.

    .venv\\Scripts\\python.exe evaluation\\prompt_compare\\batch.py cost                 # 비용 추정(OpenAI 없음)
    .venv\\Scripts\\python.exe evaluation\\prompt_compare\\batch.py all                  # 인터뷰 후보 → 변형 → 실행·판정 → 요약
    .venv\\Scripts\\python.exe evaluation\\prompt_compare\\batch.py all --batch <id>     # 끊긴 묶음 이어서(끝난 실행은 건너뜀)
    .venv\\Scripts\\python.exe evaluation\\prompt_compare\\batch.py summarize --batch <id>
    --tiers 1,2,3  --reps-scale 1.0  --mock

모델: 생성·인터뷰 추출 = .env OPENAI_CHAT_MODEL(gpt-6-luna). 판정 = .env OPENAI_JUDGE_MODEL(gpt-6-luna, 비용 때문에 생성과 같은 모델)
      → *.judged55.jsonl. 같은 답을 luna 로도 판정해(*.judged.jsonl) 두 판정기의 일치도를 같이 본다(싸다).
      이미 생성된 답은 다시 만들지 않고, 빠진 판정만 채운다 → 같은 묶음 이름으로 다시 실행하면 이어진다.

조건(무엇을 보려는가)
  1단 기준선 (3회 반복 — 한 번 돌린 결과의 흔들림 폭을 잰다)
    ops                운영 수제
    auto_now           지금 파이프라인 자동
    layered            4층 자동(주고받기 30 + 말투 카드)
  2단 개선 후보·절제 (2회)
    layered_shots0     주고받기 없음            → 주고받기가 말투·사실에 주는 효과
    layered_shots60    주고받기 60개            → 늘리면 더 닮나
    layered_nocard     말투 카드 없음           → 카드 효과
    layered_msgshots   주고받기 30개를 system 글 대신 앞선 대화 메시지로 → 말투를 더 따라 하나
    ops_L1             운영 수제 + 4층 공통 규칙 → 4층의 이점이 공통 규칙에서 오는지
    layered_ivauto     4층 + 가족 인터뷰 후보(자동 추출, 사람 확인 전) → 인터뷰 재료 효과(며느리 호칭 등)
    layered_low        4층, 추론 강도 low      → 말투·속도·비용
  3단 중간계층 경로 (2회) — 운영 후보 설정(structured + 제한 기억 빼기 + 사전 판정 v11 + 가드 v11)
    ops@orch           운영 수제 + legacy 공통 규칙
    layered@orch       4층(L2~L4) + 4층 공통 규칙(L1)
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))
import compare as C  # noqa: E402

DATA, VAR, RES = HERE / "data", HERE / "variants", HERE / "results"

CONDS = {
    "ops": dict(tier=1, reps=3, prompt="ops"),
    "auto_now": dict(tier=1, reps=3, prompt="auto_now"),
    "layered": dict(tier=1, reps=3, prompt="auto_layered"),
    "layered_shots0": dict(tier=2, reps=2, prompt="auto_layered_shots0"),
    "layered_shots60": dict(tier=2, reps=2, prompt="auto_layered_shots60"),
    "layered_nocard": dict(tier=2, reps=2, prompt="auto_layered_nocard"),
    "layered_msgshots": dict(tier=2, reps=2, prompt="auto_layered_shots0", shot_msgs=True),
    "ops_L1": dict(tier=2, reps=2, prompt="ops_L1"),
    "layered_ivauto": dict(tier=2, reps=2, prompt="auto_layered_ivauto"),
    "layered_low": dict(tier=2, reps=2, prompt="auto_layered", effort="low"),
    "ops@orch": dict(tier=3, reps=2, prompt="ops", runtime="orch", rules="legacy"),
    "layered@orch": dict(tier=3, reps=2, prompt="auto_layered_nocommon", runtime="orch", rules="layered"),
}
BASELINES = ("layered", "ops")


def safe(name: str) -> str:
    return name.replace("@", "-at-")


ONLY: set[str] = set()


def plan(tiers: set[int], scale: float) -> list[tuple[str, int]]:
    out = []
    for name, c in CONDS.items():
        if c["tier"] in tiers and (not ONLY or name in ONLY):
            out += [(name, k) for k in range(1, max(1, round(c["reps"] * scale)) + 1)]
    return out


def judge_model() -> str:
    from app.setting import load_settings
    return load_settings(REPO / ".env").openai_judge_model


def ensure_interview(mock: bool) -> None:
    """가족 인터뷰 → 후보(자동, 사람 확인 전). 있으면 건너뜀. LLM 1회."""
    out = DATA / "interview_candidates_auto.json"
    if out.exists():
        return
    from pipeline.persona_prompt.from_interview import extract_candidates
    if mock:  # 가짜 후보를 진짜 파일 자리에 남기지 않는다
        print("인터뷰 후보: --mock 이라 건너뜀(layered_ivauto 는 빠짐)")
        return
    from subject import subject
    src = next((REPO / "input" / subject()["name"]).glob("가족인터뷰_*.txt"), None)
    if src is None:
        print("인터뷰 후보: 가족 인터뷰 파일이 없어 건너뜀(layered_ivauto 는 빠짐)")
        return
    from app.external_api.openai_client import create_openai_client
    from app.setting import load_settings
    s = load_settings(REPO / ".env")
    cands = extract_candidates(src.read_text(encoding="utf-8-sig"),
                               client=create_openai_client(s, "interview extraction"), model=s.openai_analysis_model)
    out.write_text(json.dumps(cands, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"인터뷰 후보: { {k: len(v) for k, v in cands.items() if isinstance(v, list)} }, 근거 검증 탈락 {cands['dropped']}")


def run_all(batch: str, tiers: set[int], scale: float, mock: bool, workers: int, prices: dict,
            judge55: str = "none") -> None:
    if 2 in tiers:
        ensure_interview(mock)
    import prepare
    prepare.main()
    rows = C.items("orig,ext", None)
    gen = C.LLM(None, mock)
    judge = C.LLM(None, mock, temperature=0)
    shots = json.loads((DATA / "shots_auto_layered.json").read_text(encoding="utf-8"))
    todo = plan(tiers, scale)
    print(f"묶음 {batch}: 실행 {len(todo)}개(조건×반복), 문항 {len(rows)}개씩, 생성 {gen.model}, "
          f"판정 {judge.model}" + (f" + 주 판정 {'mock' if mock else judge_model()}({judge55})" if judge55 != "none" else ""))
    t0 = time.perf_counter()
    j55 = C.LLM(None if mock else judge_model(), mock) if judge55 != "none" else None
    for i, (name, k) in enumerate(todo, 1):
        c = CONDS[name]
        tag = f"r{k}_{batch}" + ("_MOCK" if mock else "")
        out_name = safe(name)
        ans = RES / f"answers_{out_name}_{tag}.jsonl"
        if not ans.exists():
            vf = VAR / f"{c['prompt']}.txt"
            if not vf.exists():
                print(f"[{i}/{len(todo)}] {name}: 변형 파일 없음({vf.name}) — 건너뜀")
                continue
            g = C.LLM(None, mock, effort=c.get("effort")) if c.get("effort") else gen
            C.run({name: vf.read_text(encoding="utf-8")}, rows, g, workers, tag,
                  shot_msgs=shots if c.get("shot_msgs") else None, runtime=c.get("runtime", "plain"),
                  rules=c.get("rules", "legacy"), out_names={name: out_name})
        for suffix, jl in ((".judged.jsonl", judge), (".judged55.jsonl", j55)):
            if suffix == ".judged55.jsonl" and (judge55 == "none" or (judge55 == "r1" and k != 1)):
                continue
            if not ans.with_name(ans.stem + suffix).exists():
                C.judge([ans], jl, workers, suffix=suffix)
        print(f"[{i}/{len(todo)}] {name} r{k} 끝 (누적 {(time.perf_counter() - t0) / 60:.1f}분)", flush=True)
    summarize(batch, mock, prices)
    summarize(batch, mock, prices, "judged")


# ------------------------------------------------------------------ 요약
KEYS = [("fact_correct", "fact 정답", True), ("fact_fabricated", "fact 지어냄", False), ("loo_ok", "loo 모름 처리", True),
        ("absent_ok", "absent(3) 모름", True), ("probe_pass", "탐침 통과", True), ("style_js", "말투 거리", False),
        ("len_ratio", "길이 배율", None), ("multi_care_turns", "6턴 챙김", False), ("multi_love", "6턴 사랑", None)]
GROUPS = [("probe_R", "R 관계"), ("probe_D", "D 사후"), ("probe_S", "S 안전"), ("probe_U", "U 모름"), ("probe_F", "F 형식"),
          ("probe_M", "M 여러 턴")]


def _fmt(v, key) -> str:
    if v is None:
        return "-"
    return f"{v:.3f}" if key in ("style_js",) else (f"{v:.2f}" if key in ("len_ratio", "multi_care_turns", "multi_love") else f"{v * 100:.0f}%")


def item_scores(rows: list[dict]) -> dict[str, float]:
    """문항별 성공(0/1). fact·absent·loo = 판정 correct, probe = pass."""
    out = {}
    for r in rows:
        if "error" in r:
            continue
        out[r["iid"]] = float(r.get("pass")) if r["type"] == "probe" else float(r.get("verdict") == "correct")
    return out


def boot_diff(a: dict[str, list[float]], b: dict[str, list[float]], ids: list[str], n: int = 2000) -> tuple[float, float, float]:
    """문항을 다시 뽑는 부트스트랩. 각 문항은 반복 평균. (차이, 2.5%, 97.5%)"""
    ids = [i for i in ids if i in a and i in b]
    if not ids:
        return (float("nan"),) * 3
    d = [sum(a[i]) / len(a[i]) - sum(b[i]) / len(b[i]) for i in ids]
    rng = random.Random(0)
    bs = sorted(sum(d[rng.randrange(len(d))] for _ in d) / len(d) for _ in range(n))
    return sum(d) / len(d), bs[int(0.025 * n)], bs[int(0.975 * n)]


def agreement(batch: str, suffix: str) -> list[str]:
    """같은 답에 대한 luna 판정(.judged) vs 주 판정기(.judged55) 일치."""
    from collections import defaultdict
    pairs = defaultdict(list)
    for f55 in RES.glob(f"answers_*_r*_{batch}{suffix}.judged55.jsonl"):
        f1 = f55.with_name(f55.name.replace(".judged55.jsonl", ".judged.jsonl"))
        if not f1.exists():
            continue
        a = {r["iid"]: r for r in C.rj(f1) if "error" not in r}
        for r in C.rj(f55):
            if "error" in r or r["iid"] not in a:
                continue
            x = a[r["iid"]]
            key = (lambda q: bool(q.get("pass"))) if r["type"] == "probe" else (lambda q: q.get("verdict") == "correct")
            pairs[r["type"]].append((key(x), key(r)))
    if not pairs:
        return []
    L = ["", "## 판정기 일치 (luna vs 주 판정기, 같은 답) — 성공/실패 기준", "", "| 문항 종류 | 쌍 | 일치 | luna 만 성공 | 주 판정기만 성공 |", "|---|---|---|---|---|"]
    for t, ps in pairs.items():
        L.append(f"| {t} | {len(ps)} | {sum(a == b for a, b in ps) / len(ps) * 100:.0f}% | {sum(a and not b for a, b in ps)} | {sum(b and not a for a, b in ps)} |")
    return L


def summarize(batch: str, mock: bool = False, prices: dict | None = None, judged: str | None = None) -> Path:
    suffix = "_MOCK" if mock else ""
    if judged is None:  # 주 판정기(.env OPENAI_JUDGE_MODEL) 결과가 있으면 그걸로
        judged = "judged55" if any(RES.glob(f"answers_*_r*_{batch}{suffix}.judged55.jsonl")) else "judged"
    sets = {r["iid"]: r for r in C.rj(DATA / "sets.jsonl")}
    per: dict[str, list[dict]] = {}
    items: dict[str, dict[str, list[float]]] = {}
    for name in CONDS:
        files = sorted(RES.glob(f"answers_{safe(name)}_r*_{batch}{suffix}.{judged}.jsonl"))
        for f in files:
            rows = C.rj(f)
            per.setdefault(name, []).append(C.metrics(rows))
            for iid, v in item_scores(rows).items():
                items.setdefault(name, {}).setdefault(iid, []).append(v)
    L = [f"# 프롬프트 개선 실험 묶음 {batch} (판정: {judged})", "",
         "값은 반복 평균, 괄호는 반복 최소~최대(반복 1회면 괄호 없음). 원본 = 실제 통화 가족 질문, 확장 = 기억을 빼서 만든 질문·탐침.", "",
         "| 조건 | 반복 | " + " | ".join(k[1] for k in KEYS) + " |", "|---|---|" + "---|" * len(KEYS)]
    for name, ms in per.items():
        cells = []
        for key, _, _ in KEYS:
            vals = [m[key] for m in ms if m.get(key) is not None]
            if not vals:
                cells.append("-")
                continue
            mean = sum(vals) / len(vals)
            cells.append(_fmt(mean, key) + (f" ({_fmt(min(vals), key)}~{_fmt(max(vals), key)})" if len(vals) > 1 else ""))
        L.append(f"| {name} | {len(ms)} | " + " | ".join(cells) + " |")
    L += ["", "## 탐침 그룹별 통과(반복 평균)", "", "| 조건 | " + " | ".join(g[1] for g in GROUPS) + " |", "|---|" + "---|" * len(GROUPS)]
    for name, ms in per.items():
        L.append(f"| {name} | " + " | ".join(_fmt(sum(m[k] for m in ms) / len(ms), k) for k, _ in GROUPS) + " |")
    groups = {"원본 fact": [i for i, s in sets.items() if s["type"] == "fact"],
              "확장 loo": [i for i, s in sets.items() if s["type"] == "loo"],
              "확장 탐침": [i for i, s in sets.items() if s["type"] == "probe"]}
    for base in BASELINES:
        if base not in items:
            continue
        L += ["", f"## {base} 대비 차이 (문항 부트스트랩 95% 구간, 0 을 안 걸치면 차이가 있다고 본다)", "",
              "| 조건 | " + " | ".join(groups) + " |", "|---|" + "---|" * len(groups)]
        for name in per:
            if name == base:
                continue
            cells = []
            for ids in groups.values():
                d, lo, hi = boot_diff(items.get(name, {}), items[base], ids)
                mark = " **" if (lo > 0 or hi < 0) else ""
                cells.append(f"{d * 100:+.1f}%p [{lo * 100:+.1f}, {hi * 100:+.1f}]{mark}")
            L.append(f"| {name} | " + " | ".join(cells) + " |")
    L += agreement(batch, suffix)
    if prices:
        jin, jout = (prices["j_in"], prices["j_out"]) if judged == "judged55" else (prices["in"], prices["out"])
        gen = sum(((m["tokens_in"] - m["tokens_cached"]) * prices["in"] + m["tokens_cached"] * prices["cached"]
                   + m["tokens_out"] * prices["out"]) / 1e6 for ms in per.values() for m in ms)
        jud = sum((m["judge_in"] * jin + m["judge_out"] * jout) / 1e6 for ms in per.values() for m in ms)
        L += ["", f"추정 비용: 생성 ${gen:.2f} + 판정({judged}) ${jud:.2f} = 약 {(gen + jud) * C.USD_KRW:,.0f}원 "
              f"(생성 luna 입력 $0.10·캐시 $0.01·출력 $0.50, 판정 입력 ${jin}·출력 ${jout} /1M 가정)"]
    text = "\n".join(L) + "\n"
    out = RES / f"batch_{batch}{suffix}_{judged}.md"
    out.write_text(text, encoding="utf-8")
    (RES / f"batch_{batch}{suffix}_{judged}.json").write_text(json.dumps(per, ensure_ascii=False, indent=1), encoding="utf-8")
    print(text)
    print("저장:", out)
    return out


def cost(tiers: set[int], scale: float, prices: dict) -> None:
    import prepare
    rows = C.items("orig,ext", None)
    todo = plan(tiers, scale)
    calls = sum(len(r["turns"]) for r in rows)
    by_id = C.memories()
    per_cond = {}
    for name, _ in todo:
        c = CONDS[name]
        vf = VAR / f"{c['prompt']}.txt"
        plen = len(vf.read_text(encoding="utf-8")) if vf.exists() else 8000
        chars = sum((plen + len(C.memory_block(r["mem_ids"], by_id)) + sum(len(h["content"]) for h in r["history"]) + 80 * k)
                    for r in rows for k, _ in enumerate(r["turns"]))
        per_cond.setdefault(name, 0)
        per_cond[name] += 1
        tin = chars * C.TOK_PER_CHAR
        usd_hi = (tin * prices["in"] + calls * 400 * prices["out"]) / 1e6 + len(rows) * (1500 * prices["j_in"] + 400 * prices["j_out"]) / 1e6
        usd_lo = (tin * (0.4 * prices["in"] + 0.6 * prices["cached"]) + calls * 400 * prices["out"]) / 1e6 \
            + len(rows) * (1500 * prices["j_in"] + 400 * prices["j_out"]) / 1e6
        per_cond[name + "_usd"] = per_cond.get(name + "_usd", (0, 0))
        a, b = per_cond[name + "_usd"]
        per_cond[name + "_usd"] = (a + usd_lo, b + usd_hi)
    lo = sum(v[0] for k, v in per_cond.items() if k.endswith("_usd"))
    hi = sum(v[1] for k, v in per_cond.items() if k.endswith("_usd"))
    print(f"실행 {len(todo)}개 × 문항 {len(rows)}개(생성 {calls}회 + 판정 {len(rows)}회씩) + 인터뷰 추출 1회")
    for name in CONDS:
        if name in per_cond:
            a, b = per_cond[name + "_usd"]
            print(f"  {name:18} {per_cond[name]}회  ${a:.2f}~${b:.2f}")
    print(f"합계 약 ${lo:.2f}~${hi:.2f} (약 {lo * C.USD_KRW:,.0f}~{hi * C.USD_KRW:,.0f}원). 1차 실행 실측: 변형 하나 약 $0.18~0.20")
    print(f"시간: 실행 하나 약 3분(생성 1.5분 + 판정 1.5분) → 약 {len(todo) * 3}분")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["cost", "all", "summarize"])
    ap.add_argument("--batch", default=time.strftime("%y%m%d_%H%M"))
    ap.add_argument("--tiers", default="1,2,3")
    ap.add_argument("--reps-scale", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=20, help="동시 호출 수")
    ap.add_argument("--judge55", choices=["none", "r1", "all"], default="none",
                    help="주 판정(.env OPENAI_JUDGE_MODEL): none / r1(반복 1회차만) / all")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--only", help="이 조건만(쉼표로), 예: layered")
    ap.add_argument("--judged", choices=["judged", "judged55"], help="요약에 쓸 판정(기본: 주 판정 결과가 있으면 그것)")
    a = ap.parse_args()
    # 판정 가격은 주 판정기 기준. 지금은 gpt-6-luna(가정값 입력 $0.10·출력 $0.50 /1M, 확인 전)
    prices = {"in": 0.10, "cached": 0.01, "out": 0.50, "j_in": 0.10, "j_out": 0.50}
    tiers = {int(t) for t in a.tiers.split(",") if t.strip()}
    if a.only:
        ONLY.update(x.strip() for x in a.only.split(",") if x.strip())
    if a.cmd == "cost":
        return cost(tiers, a.reps_scale, prices)
    if a.cmd == "summarize":
        summarize(a.batch, a.mock, prices, a.judged)
        if a.judged is None:
            summarize(a.batch, a.mock, prices, "judged")  # luna 판정 표도
        return
    run_all(a.batch, tiers, a.reps_scale, a.mock, a.workers, prices, a.judge55)


if __name__ == "__main__":
    main()
