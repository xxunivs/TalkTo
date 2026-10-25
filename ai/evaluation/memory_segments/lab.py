"""3-A 기억 조각 오프라인 랩 — 전사+2단계 산출물로 추출 품질 확인.

Usage:
    python -m evaluation.memory_segments.lab [--only "할머니 목소리 8"] [--model ...]

입력: evaluation/transcription/results/*.json (전사),
      evaluation/transcription/results/analysis/*.persons.json / *.sensitivity.json
      (2단계 산출 — 없으면 해당 파일 건너뜀)
"""

import argparse
import difflib
import json

from app.core.config import load_settings
from app.pipeline.memory_segments.service import extract_memory_segments
from evaluation.common import (
    REPO_ROOT,
    RESULTS_DIR,
    load_context_fixture,
    load_segments,
    result_stems,
)
from evaluation.speaker_id.lab import gold_speaker_texts, subject_label_by_text

ANALYSIS_DIR = RESULTS_DIR / "analysis"
OUT_DIR = RESULTS_DIR / "memory"


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def near_duplicates(memories: list[dict]) -> list[tuple[str, str]]:
    pairs = []
    for i in range(len(memories)):
        for j in range(i + 1, len(memories)):
            a, b = memories[i]["memoryText"], memories[j]["memoryText"]
            if difflib.SequenceMatcher(None, a, b).ratio() >= 0.85:
                pairs.append((a, b))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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
        persons = load_json(ANALYSIS_DIR / f"{stem}.persons.json")
        sensitivity = load_json(ANALYSIS_DIR / f"{stem}.sensitivity.json")
        subject_label = subject_label_by_text(segments, gold_speaker_texts(stem))

        result = extract_memory_segments(
            segments,
            subject_context=subject_context,
            subject_speaker_label=subject_label,
            persons_result=persons,
            sensitivity_result=sensitivity,
            settings=settings,
        )
        (OUT_DIR / f"{stem}.memory.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        memories = result["memorySegments"]
        dropped = result["validationDropped"]
        print(f"\n=== {stem} (기억 {len(memories)}건, 검증 제거 {sum(dropped.values())}) ===")
        for memory in memories:
            flags = f" 🚩{','.join(memory['sensitivityFlags'])}" if memory["sensitivityFlags"] else ""
            people = f" 👤{','.join(memory['relatedPeople'])}" if memory["relatedPeople"] else ""
            print(f"  [{memory['confidence'][:4]}] {memory['memoryText']}{people}{flags}")
        for a, b in near_duplicates(memories):
            print(f"  ⚠️ 유사 중복: {a[:40]} ↔ {b[:40]}")
    print(f"\n결과 저장: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
