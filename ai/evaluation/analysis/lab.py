"""2단계 분석 오프라인 랩 — 저장된 전사 13건으로 항목별 프롬프트 품질 확인.

Usage:
    python -m evaluation.analysis_lab persons [--only "할머니 목소리 8,할머니 목소리 10"] [--model gpt-4.1-mini]

입력: evaluation/e2e/results/*.json (전사) + evaluation/fixtures/ (컨텍스트).
대상자 화자 라벨은 골드 텍스트 정렬(speaker_id_lab과 동일)로 자동 도출.
출력: 파일별 인물 표 + 코드 검증 위반 수. 결과는 화면 + results/analysis/ 저장.
"""

import argparse
import json

from app.core.config import load_settings
from app.pipeline.analysis.persons import run_persons_analysis
from app.pipeline.analysis.sensitivity import run_sensitivity_analysis
from evaluation.common import (
    REPO_ROOT,
    RESULTS_DIR,
    load_context_fixture,
    load_segments,
    result_stems,
)
from evaluation.speaker_id.lab import gold_speaker_texts, subject_label_by_text

OUT_DIR = RESULTS_DIR / "analysis"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("item", choices=["persons", "sensitivity"])
    parser.add_argument("--only", type=str, default=None)
    parser.add_argument("--model", type=str, default=None)
    args = parser.parse_args()

    settings = load_settings(REPO_ROOT / ".env")
    if args.model:
        settings = settings.model_copy(update={"openai_analysis_model": args.model})
    print(f"모델: {settings.openai_analysis_model}")

    stems = result_stems()
    if args.only:
        wanted = {name.strip() for name in args.only.split(",")}
        stems = [stem for stem in stems if stem in wanted]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    subject_context, _ = load_context_fixture()

    for stem in stems:
        segments = load_segments(stem)
        if args.item == "sensitivity":
            result = run_sensitivity_analysis(segments, settings=settings)
            (OUT_DIR / f"{stem}.sensitivity.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            flags = result["sensitivityFlags"]
            print(f"\n=== {stem} (플래그 {len(flags)}건) ===")
            for flag in flags:
                print(
                    f"  [{flag['type']}] {flag['description']} "
                    f"(세그 {flag['sourceSegmentIds']})"
                )
            continue

        subject_label = subject_label_by_text(segments, gold_speaker_texts(stem))
        result = run_persons_analysis(
            segments,
            subject_context=subject_context,
            subject_speaker_label=subject_label,
            settings=settings,
        )
        (OUT_DIR / f"{stem}.persons.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        dropped = result["validationDropped"]
        print(f"\n=== {stem} (대상자: {subject_label}, 검증 제거: {sum(dropped.values())}) ===")
        for person in result["persons"]:
            facts = "; ".join(fact["fact"] for fact in person["facts"]) or "-"
            print(
                f"  {person['name']} [{person['relationToSubject'] or '?'}] "
                f"({person['confidence']}) 지칭: {', '.join(person['mentions']) or '-'}"
            )
            if facts != "-":
                print(f"    사실: {facts}")
            for relation in person["relationsToOthers"]:
                print(f"    관계: {relation['name']} = {relation['relation']}")
        for mention in result["unresolvedMentions"]:
            print(f"  [미해결] {mention['mention']} — {mention['context']}")
    print(f"\n결과 저장: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
