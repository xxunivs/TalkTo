"""3-C 파생 산출물 랩 — 요약 생성 + 태그 집계, 채점(S/T 룰).

S1 근거율·S4 민감노출은 judge, S3 형식(2~3문장)·T1 어휘·T3 개수는 코드.
Usage: python -m evaluation.enrichment.lab [--only ...]
"""

import argparse
import json

from openai import OpenAI

from app.core.config import load_settings
from app.pipeline.enrichment.service import (
    aggregate_tags,
    sentence_count,
    summarize_recording,
)
from app.pipeline.memory_segments.service import _ALLOWED_TAGS
from evaluation.common import REPO_ROOT, RESULTS_DIR, load_segments, result_stems

MEMORY_DIR = RESULTS_DIR / "memory"
OUT_DIR = RESULTS_DIR / "enrichment"

SUMMARY_JUDGE = """너는 통화 요약을 채점한다. 아래 전사문을 근거로 두 가지를 판정:
1. grounded: 요약의 모든 내용이 전사문에서 확인되는가 (지어낸 내용 없음).
2. sensitive_leak: 구체적 병명·병원비·가족 갈등·재산 같은 민감 내용이 요약에 직접 노출됐는가.
JSON으로만: {"grounded": true/false, "sensitive_leak": true/false, "reason": "..."}"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", type=str, default=None)
    args = parser.parse_args()

    settings = load_settings(REPO_ROOT / ".env")
    client = OpenAI(api_key=settings.openai_api_key.get_secret_value())
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    stems = [s for s in result_stems() if (MEMORY_DIR / f"{s}.memory.json").exists()
             and not s.startswith("외부")]
    if args.only:
        wanted = {n.strip() for n in args.only.split(",")}
        stems = [s for s in stems if s in wanted]

    s3_ok = s1_ok = s4_ok = t1_ok = t3_ok = total = 0
    for stem in stems:
        segments = load_segments(stem)
        summary = summarize_recording(segments, settings=settings)
        memory = json.loads((MEMORY_DIR / f"{stem}.memory.json").read_text(encoding="utf-8"))
        tags = aggregate_tags(memory)
        (OUT_DIR / f"{stem}.enrichment.json").write_text(
            json.dumps({"summary": summary, "tags": tags}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        total += 1

        # S3 형식(2~3문장), T1 어휘, T3 개수(3~7) — 코드
        nsent = sentence_count(summary)
        s3 = 2 <= nsent <= 3
        t1 = all(t in _ALLOWED_TAGS for t in tags)
        t3 = 3 <= len(tags) <= 7
        # S1·S4 — judge
        transcript = "\n".join((s.corrected_text or s.transcript_text) for s in segments)
        r = client.chat.completions.create(
            model=settings.openai_judge_model, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": SUMMARY_JUDGE},
                      {"role": "user", "content": f"전사문:\n{transcript}\n\n요약:\n{summary}"}])
        j = json.loads(r.choices[0].message.content or "{}")
        s1 = bool(j.get("grounded")); s4 = not j.get("sensitive_leak")

        s3_ok += s3; s1_ok += s1; s4_ok += s4; t1_ok += t1; t3_ok += t3
        print(f"\n[{stem}] 태그{len(tags)}: {', '.join(tags)}")
        print(f"  요약({nsent}문장): {summary}")
        marks = f"S1{'✅' if s1 else '❌'} S3{'✅' if s3 else '❌'} S4{'✅' if s4 else '❌'} T1{'✅' if t1 else '❌'} T3{'✅' if t3 else '❌'}"
        print(f"  {marks}")

    print(f"\n=== 채점 ({total}건) ===")
    print(f"  S1 근거율(지어냄 0): {s1_ok}/{total}   S3 형식(2~3문장): {s3_ok}/{total}   S4 민감 미노출: {s4_ok}/{total}")
    print(f"  T1 어휘 준수: {t1_ok}/{total}   T3 개수(3~7): {t3_ok}/{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
