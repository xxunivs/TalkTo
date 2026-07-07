"""평가 랩 공용 헬퍼 — 저장된 전사 결과와 컨텍스트 픽스처 로딩."""

import json
import re
from pathlib import Path

from app.schemas.context import IntakeContext, SubjectContext
from app.schemas.transcript import TranscriptSegment

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "evaluation" / "e2e" / "results"
FIXTURE_PATH = REPO_ROOT / "evaluation" / "fixtures" / "subject_context_singeumja.json"
GOLD_DIR = REPO_ROOT / "evaluation" / "bakeoff" / "gold"
AUDIO_DIR = REPO_ROOT / "data" / "voice_raw"


def natural_key(name: str | Path) -> tuple:
    stem = Path(name).stem
    return tuple(
        int(part) if part.isdigit() else part for part in re.split(r"(\d+)", stem)
    )


def result_stems() -> list[str]:
    return sorted((path.stem for path in RESULTS_DIR.glob("*.json")), key=natural_key)


def load_segments(stem: str) -> list[TranscriptSegment]:
    body = json.loads((RESULTS_DIR / f"{stem}.json").read_text(encoding="utf-8"))
    return [TranscriptSegment(**segment) for segment in body["segments"]]


def load_context_fixture() -> tuple[SubjectContext, IntakeContext | None]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    subject = SubjectContext(**payload["subjectContext"])
    intake = (
        IntakeContext(**payload["intakeContext"]) if payload.get("intakeContext") else None
    )
    return subject, intake
