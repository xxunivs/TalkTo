"""픽스처(계약2 JSON) → 페르소나 system prompt 조립·저장.

lab.py가 쓰는 미리 조립된 persona .txt를 어느 인물이든 재현 가능하게 만든다.
말투 예시는 픽스처의 `_speechExamples`(실오디오 근거 발화)를 세그먼트로 넣어 주입.

Usage:
    python -m evaluation.persona.build --fixture data/fixtures/subject_context_choiyoungja_gangwon.json
"""

import argparse
import json
from pathlib import Path

from app.pipeline.persona.assembler import assemble_persona_prompt
from app.schemas.context import IntakeContext, SubjectContext
from app.schemas.transcript import TranscriptSegment

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "evaluation" / "persona" / "results"


def speech_segments(examples: list[str]) -> list[TranscriptSegment]:
    return [
        TranscriptSegment(
            segment_index=i, start_ms=i * 1000, end_ms=i * 1000 + 500,
            speaker_label="S", transcript_text=text,
        )
        for i, text in enumerate(examples)
    ]


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
        segments_by_recording=[speech_segments(examples)] if examples else [],
        subject_labels=["S"] if examples else [],
        intake_context=intake,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = args.out or (OUT_DIR / f"persona_{args.fixture.stem.replace('subject_context_', '')}.txt")
    out.write_text(prompt, encoding="utf-8")
    print(f"조립 완료 {len(prompt)}자 → {out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
