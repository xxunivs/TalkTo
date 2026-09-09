"""평가 랩 공용 헬퍼 — 저장된 전사 결과와 컨텍스트 픽스처 로딩."""

import json
import re
from pathlib import Path

from app.schemas.context import IntakeContext, SubjectContext
from app.schemas.transcript import TranscriptSegment

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "evaluation" / "transcription" / "results"
FIXTURE_PATH = REPO_ROOT / "data" / "fixtures" / "subject_context_singeumja.json"
GOLD_DIR = REPO_ROOT / "evaluation" / "transcription" / "gold"
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


def write_summary_md(path: Path, title: str, lines: list[str]) -> Path:
    """채점 lab의 요약 결과를 md로 저장(로그뿐 아니라 파일로 지속). 반환: 저장 경로.

    RESULTS_DIR 이하는 우리 데이터 파생이라 gitignore — 로컬 지속·프라이버시 안전.
    """
    from datetime import datetime

    path.parent.mkdir(parents=True, exist_ok=True)
    header = [f"# {title}", "", f"실행: {datetime.now():%Y-%m-%d %H:%M}", ""]
    path.write_text("\n".join(header + lines) + "\n", encoding="utf-8")
    return path
