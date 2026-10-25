"""픽스처(subjectContext, intakeContext, _speechExamples) -> 페르소나 system prompt 조립 저장"""

import argparse
import json
from pathlib import Path

from app.schema.subject import IntakeContext, SubjectContext
from pipeline.s3_synthesis.c_build_system_prompt import _speech_segments, assemble_persona_prompt

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "evaluation" / "persona" / "results"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    payload = json.loads(args.fixture.read_text(encoding="utf-8"))
    subject = SubjectContext(**payload["subjectContext"])
    intake = IntakeContext(**payload["intakeContext"]) if payload.get("intakeContext") else None
    examples = payload.get("_speechExamples", [])

    prompt = assemble_persona_prompt(
        subject_context=subject,
        persons_results=[],
        sensitivity_results=[],
        segments_by_recording=[_speech_segments(examples)] if examples else [],
        subject_labels=["S"] if examples else [],
        intake_context=intake,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = args.out or (OUT_DIR / f"persona_{args.fixture.stem.replace('subject_context_', '')}.txt")
    out.write_text(prompt, encoding="utf-8")
    try:
        shown = out.relative_to(REPO_ROOT)
    except ValueError:  # 저장소 밖 --out 경로
        shown = out
    print(f"조립 완료 {len(prompt)}자 → {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
