"""대상자 목소리 자동 클론 — 통화에서 대상자 화자 구간을 추출해 ElevenLabs 클론 생성.

각 인물의 통화 오디오 → 전사(화자분리) → 대상자(발화 최다) 구간만 뽑아 이어붙인
샘플 → ElevenLabs add-voice → voiceId. data/<이름>/voice_id.txt에 저장.

Usage:
    python -m evaluation.persona.clone_personas --names 최영자 이순덕 정말순 서정숙 김분남
"""

import argparse
import subprocess
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from app.core.config import load_settings
from app.providers.tts.elevenlabs_tts import ElevenLabsTTSProvider

REPO = Path(__file__).resolve().parents[2]
SAMPLE_MAX_SECONDS = 90.0
MIN_SEG_MS = 1200


def _subject_segments(clip: Path, api_key: str):
    from app.providers.stt.elevenlabs_scribe import ElevenLabsScribeProvider
    segs = ElevenLabsScribeProvider(api_key=api_key).transcribe(
        clip, language="ko", speaker_diarization=True).segments
    counts: dict[str, int] = {}
    for s in segs:
        counts[s.speaker_label] = counts.get(s.speaker_label, 0) + 1
    subject = max(counts, key=counts.get) if counts else None
    return [s for s in segs if s.speaker_label == subject], subject


def _build_sample(clip: Path, segments) -> Path:
    """대상자 구간들을 잘라 이어붙인 wav 샘플(최대 SAMPLE_MAX_SECONDS)."""
    parts, budget = [], 0.0
    tmpdir = Path(tempfile.mkdtemp())
    for i, seg in enumerate(segments):
        dur = (seg.end_ms - seg.start_ms) / 1000
        if dur * 1000 < MIN_SEG_MS:
            continue
        if budget >= SAMPLE_MAX_SECONDS:
            break
        part = tmpdir / f"p{i}.wav"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-i", str(clip), "-ss", str(seg.start_ms / 1000),
             "-to", str(seg.end_ms / 1000), "-ac", "1", "-ar", "22050", str(part)],
            check=True, capture_output=True)
        parts.append(part)
        budget += dur
    listfile = tmpdir / "list.txt"
    listfile.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
    out = tmpdir / "sample.wav"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
         "-c", "copy", str(out)], check=True, capture_output=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--names", nargs="+", required=True)
    args = parser.parse_args()

    load_dotenv(REPO / ".env")
    settings = load_settings(REPO / ".env")
    api_key = settings.elevenlabs_api_key.get_secret_value()
    provider = ElevenLabsTTSProvider(api_key=api_key, model_id=settings.elevenlabs_model)

    for name in args.names:
        audio_dir = REPO / "data" / name / "통화녹음"
        clips = sorted(audio_dir.glob("*.wav"), key=lambda p: p.stat().st_size, reverse=True)
        if not clips:
            print(f"{name}: 오디오 없음 — 건너뜀")
            continue
        clip = clips[0]  # 가장 긴 통화
        segments, subject = _subject_segments(clip, api_key)
        if not segments:
            print(f"{name}: 대상자 구간 없음 — 건너뜀")
            continue
        sample = _build_sample(clip, segments)
        dur = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of",
             "csv=p=0", str(sample)], capture_output=True, text=True).stdout.strip()
        voice_id = provider.clone_voice(f"TalkTo_{name}", sample)
        (REPO / "data" / name / "voice_id.txt").write_text(voice_id, encoding="utf-8")
        print(f"{name}: 샘플 {dur}s(화자 {subject}) → voiceId {voice_id}")

    print("\n완료 — 각 data/<이름>/voice_id.txt 저장")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
