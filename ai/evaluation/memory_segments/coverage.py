"""3-A 기억 커버리지 채점 — 중요한 화제를 빠짐없이 뽑았나 (R2와 별개 축).

R2(faithfulness)는 "뽑은 기억이 사실인가"(정밀도), 커버리지는 "기억할 만한
화제를 다 뽑았나"(재현). 골드 주제는 손으로 정하지 않는다: judge 모델이
전사문에서 '기억·회상·페르소나에 쓸 만한 핵심 화제'를 자동 추출해 기준선을
만들고, 각 화제가 기억 조각으로 덮였는지 다시 judge가 판정한다.

노션 평가 계획의 '커버리지' 축. judge=OPENAI_JUDGE_MODEL.

Usage:
    python -m evaluation.memory_segments.coverage [--only ...]
"""

import argparse
import json

from openai import OpenAI

from app.core.config import load_settings
from evaluation.common import REPO_ROOT, RESULTS_DIR, load_segments, result_stems

MEMORY_DIR = RESULTS_DIR / "memory"

TOPICS_PROMPT = """너는 가족 통화 전사문에서 '기억할 만한 핵심 화제'를 뽑는 분석기다.
나중에 가족이 검색하거나 대상자 페르소나가 회상할 가치가 있는 화제만 고른다.

포함: 음식·요리법·음식 선물, 건강·병원, 가족·인물 근황, 생애·과거 경험, 장소,
가치관·조언, 의미 있는 인사(새해 복·사랑한다 등), 반복 습관.
제외: 단순 맞장구, 통화 연결용 잡담, 날씨 스몰톡.

각 화제는 한 구절로. 다음 JSON으로만 답한다:
{"topics": ["종갓집 김치를 주문해 먹음", "당뇨로 요리가 힘들어짐", ...]}"""

COVERAGE_PROMPT = """너는 기억 조각들이 주어진 핵심 화제를 덮는지 판정하는 채점기다.
각 화제에 대해, 그 화제를 다루는 기억 조각이 목록에 있으면 covered, 없으면 missed.

다음 JSON으로만 답한다:
{"results": [{"topic": "화제", "verdict": "covered|missed"}]}"""


def judge_json(client, model, system, user):
    r = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return json.loads(r.choices[0].message.content or "{}")


def transcript_text(stem):
    return "\n".join(
        (s.corrected_text or s.transcript_text) for s in load_segments(stem)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", type=str, default=None)
    args = parser.parse_args()

    settings = load_settings(REPO_ROOT / ".env")
    client = OpenAI(api_key=settings.openai_api_key.get_secret_value())
    model = settings.openai_judge_model
    print(f"judge 모델: {model}")

    stems = [s for s in result_stems() if (MEMORY_DIR / f"{s}.memory.json").exists()]
    if args.only:
        wanted = {n.strip() for n in args.only.split(",")}
        stems = [s for s in stems if s in wanted]

    grand_covered, grand_total, all_missed = 0, 0, []
    for stem in stems:
        topics = judge_json(client, model, TOPICS_PROMPT, transcript_text(stem)).get("topics", [])
        if not topics:
            continue
        memories = json.loads((MEMORY_DIR / f"{stem}.memory.json").read_text(encoding="utf-8"))
        mem_lines = "\n".join(f"- {m['memoryText']}" for m in memories["memorySegments"])
        user = "핵심 화제:\n" + "\n".join(f"- {t}" for t in topics) + f"\n\n기억 조각:\n{mem_lines}"
        results = judge_json(client, model, COVERAGE_PROMPT, user).get("results", [])
        covered = sum(1 for r in results if r.get("verdict") == "covered")
        total = len(results) or len(topics)
        grand_covered += covered
        grand_total += total
        missed = [r["topic"] for r in results if r.get("verdict") == "missed"]
        all_missed.extend((stem, t) for t in missed)
        print(f"  {stem}: 화제 {total}개, 커버 {covered} ({covered/total:.0%})")

    rate = grand_covered / grand_total if grand_total else 0
    print(f"\n[커버리지] 총 화제 {grand_total}개, 커버 {grand_covered} = {rate:.1%}")
    if all_missed:
        print("놓친 화제:")
        for stem, t in all_missed[:20]:
            print(f"  · [{stem}] {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
