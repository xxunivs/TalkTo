"""평가 공용 헬퍼, 저장된 전사 결과와 녹음 경로 로딩"""

import json
import re
from datetime import datetime
from pathlib import Path

from app.schema.transcript import TranscriptSegment

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "evaluation" / "transcription" / "results"
GOLD_DIR = REPO_ROOT / "evaluation" / "transcription" / "gold"
AUDIO_DIR = REPO_ROOT / "input" / "신금자" / "음성"

# 결과 이름 "할머니 목소리 N" -> 녹음 파일 "통화NN" (길이 대조로 확인한 대응)
_RESULT_STEM = re.compile(r"^할머니 목소리 (\d+)$")


def natural_key(name: str | Path) -> tuple:
    stem = Path(name).stem
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", stem))


def result_stems() -> list[str]:
    return sorted((path.stem for path in RESULTS_DIR.glob("*.json")), key=natural_key)


def load_segments(stem: str) -> list[TranscriptSegment]:
    body = json.loads((RESULTS_DIR / f"{stem}.json").read_text(encoding="utf-8"))
    return [TranscriptSegment(**segment) for segment in body["segments"]]


def audio_path(stem: str) -> Path:
    """결과 이름에 대응하는 원본 녹음 경로 (확장자 무관)"""
    match = _RESULT_STEM.match(stem)
    name = f"통화{int(match.group(1)):02d}" if match else stem
    found = sorted(AUDIO_DIR.glob(f"{name}.*"))
    if not found:
        raise FileNotFoundError(f"녹음 없음: {AUDIO_DIR / name}.*")
    return found[0]


def write_summary_md(path: Path, title: str, lines: list[str]) -> Path:
    """채점 요약 md 저장, 반환은 저장 경로"""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = [f"# {title}", "", f"실행: {datetime.now():%Y-%m-%d %H:%M}", ""]
    path.write_text("\n".join(header + lines) + "\n", encoding="utf-8")
    return path
