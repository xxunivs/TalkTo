"""Offline lab for the transcript correction pass (backlog 3, spec ANL-005).

Feeds saved transcription results (evaluation/e2e/results/*.json) straight into
the correction service — no STT rerun, so inputs are fixed and model/prompt
changes are comparable. Canary cases come from real mis-transcriptions found
in our recordings.

Usage:
    python -m evaluation.correction_lab [model ...]   # default: gpt-4.1-mini

Requires OPENAI_API_KEY in .env and saved results from
evaluation.e2e.run_e2e_transcription.
"""

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

from app.core.config import load_settings
from app.schemas.transcript import TranscriptSegment
from app.services.correction import correct_segments

RESULTS = REPO_ROOT / "evaluation" / "e2e" / "results"
GLOSSARY = ["향", "향아", "규하", "해욱", "준혁", "채민", "종서", "신경", "금자", "정읍"]

# (파일, 원문, 기대) — "교정:향아" = 향아로 교정돼야 함 / "불변" = 건드리면 안 됨
# 둘 다 실녹음에서 관찰된 사례: 이름 오전사(10번), 사투리 "그려유" 오인(7번)
CANARIES = [
    ("할머니 목소리 10", "어, 영아.", "교정:향아"),
    ("할머니 목소리 7", "구려유. 할머니도 잘 자.", "불변"),
]


def load_segments(stem: str) -> list[TranscriptSegment]:
    body = json.loads((RESULTS / f"{stem}.json").read_text(encoding="utf-8"))
    return [TranscriptSegment(**segment) for segment in body["segments"]]


def run(model: str) -> None:
    settings = load_settings(REPO_ROOT / ".env")
    settings = settings.model_copy(update={"openai_correction_model": model})

    total_corrections = 0
    verdicts = []
    for stem in sorted({canary[0] for canary in CANARIES}):
        segments = load_segments(stem)
        for segment in segments:
            segment.corrected_text, segment.needs_review = None, False
        correct_segments(segments, glossary=GLOSSARY, settings=settings)
        corrected = [s for s in segments if s.corrected_text]
        total_corrections += len(corrected)
        for file_stem, original, expectation in CANARIES:
            if file_stem != stem:
                continue
            target = next(s for s in segments if s.transcript_text == original)
            if expectation == "불변":
                ok = target.corrected_text is None
                got = "불변 유지" if ok else f"오교정→{target.corrected_text}"
            else:
                want = expectation.split(":")[1]
                ok = target.corrected_text is not None and want in target.corrected_text
                got = target.corrected_text or ("리뷰만" if target.needs_review else "미교정")
            verdicts.append((original, got, ok))
        for segment in corrected:
            print(
                f"    [{stem} {segment.segment_index}] "
                f"{segment.transcript_text} → {segment.corrected_text}"
            )

    passed = sum(1 for *_, ok in verdicts if ok)
    print(f"  캔어리 {passed}/{len(verdicts)} | 총 교정 수 {total_corrections}")
    for original, got, ok in verdicts:
        print(f"  {'✅' if ok else '❌'} '{original}' → {got}")


if __name__ == "__main__":
    for model_name in sys.argv[1:] or ["gpt-4.1-mini"]:
        print(f"\n===== model: {model_name} =====")
        run(model_name)
