"""외부 데이터(AI Hub) 일반화 검증, 전화망 전사 CER과 합성 용어집 오교정 안전"""

import argparse
import os
import random
import re
from pathlib import Path

from dotenv import load_dotenv

from evaluation.common import REPO_ROOT
from evaluation.metrics import cer

# AI Hub 자료 위치 (환경 변수 AIHUB_ROOT 로 변경)
AIHUB_ROOT = Path(os.environ.get("AIHUB_ROOT") or Path.home() / "aihub_data")
PHONE_BASE = AIHUB_ROOT / "571_전화망/007.저음질_전화망_음성인식_데이터/01.데이터/2.Validation"

# (철자)/(발음) 이중 전사, 골드가 이중 표기면 CER 낮은 쪽 채택
_DUAL = re.compile(r"\(([^()]*)\)\s*/\s*\(([^()]*)\)")
# 잡음, 간투어 태그 (b/ n/ o/ l/ u/ 등)
_TAGS = re.compile(r"\b[bnolu]/")
# 불명료 구간 ((...)), 중단 표시 +, 발음 오류 표시 *
_UNCLEAR = re.compile(r"\(\([^)]*\)\)")


def normalize_gold_variants(text: str) -> tuple[str, str]:
    """AI Hub 표기 제거 후 (철자 해석, 발음 해석)"""

    def strip_common(value: str) -> str:
        value = _UNCLEAR.sub(" ", value)
        value = _TAGS.sub(" ", value)
        value = value.replace("+", " ").replace("*", " ")
        return " ".join(value.split())

    orthographic = strip_common(_DUAL.sub(r"\1", text))
    phonetic = strip_common(_DUAL.sub(r"\2", text))
    return orthographic, phonetic


def gold_cer(gold_raw: str, hypothesis: str) -> float:
    orthographic, phonetic = normalize_gold_variants(gold_raw)
    return min(cer(orthographic, hypothesis), cer(phonetic, hypothesis))


def phone_clip_pairs() -> list[tuple[Path, Path]]:
    pairs = []
    for wav in (PHONE_BASE / "VS_D03").rglob("*.wav"):
        label = PHONE_BASE / "VL_D03" / wav.relative_to(PHONE_BASE / "VS_D03").with_suffix(".txt")
        if label.exists():
            pairs.append((wav, label))
    return sorted(pairs)


def run_phone_cer(clips: int) -> None:
    from app.external_api.stt.elevenlabs_scribe import ElevenLabsScribeProvider

    provider = ElevenLabsScribeProvider(api_key=os.environ["ELEVENLABS_API_KEY"])
    random.seed(42)
    sample = random.sample(phone_clip_pairs(), clips)

    scores = []
    worst: list[tuple[float, str, str]] = []
    for wav, label in sample:
        gold_raw = label.read_text(encoding="utf-8").strip()
        result = provider.transcribe(wav, language="ko", speaker_diarization=False)
        hypothesis = " ".join(s.transcript_text for s in result.segments)
        value = gold_cer(gold_raw, hypothesis)
        scores.append(value)
        worst.append((value, gold_raw, hypothesis))

    scores.sort()
    print(
        f"\n[전화망 8kHz] {clips}클립 CER 평균 {sum(scores) / len(scores):.4f} "
        f"/ 중앙값 {scores[len(scores) // 2]:.4f} / 최대 {scores[-1]:.4f}"
    )
    print("최악 3건:")
    for value, gold_raw, hypothesis in sorted(worst, reverse=True)[:3]:
        print(f"  CER {value:.3f} | 골드: {gold_raw[:60]}")
        print(f"            | 전사: {hypothesis[:60]}")


# 오교정 안전 확인용 합성 용어집, 특정 가족과 무관한 일반 이름과 지명
SYNTHETIC_GLOSSARY = [
    "김하늘",
    "하늘",
    "하늘아",
    "박민준",
    "민준",
    "민준아",
    "이서연",
    "서연",
    "서연아",
    "최준호",
    "준호",
    "준호야",
    "정읍",
    "속초",
    "구미",
    "매실청",
    "장아찌",
]


def run_correction_safety(source: str, utterances: int) -> None:
    from app.schema.transcript import TranscriptSegment
    from app.setting import load_settings
    from pipeline.s1_transcription.c_correction import correct_segments

    if source == "phone":
        labels = [label for _, label in phone_clip_pairs()]
    else:
        raise SystemExit(f"아직 연결 안 된 소스: {source} (다운로드 후 추가)")

    random.seed(7)
    chosen = random.sample(labels, min(utterances, len(labels)))
    segments = []
    for i, label in enumerate(chosen):
        orthographic, _ = normalize_gold_variants(label.read_text(encoding="utf-8").strip())
        if orthographic:
            segments.append(
                TranscriptSegment(
                    segment_index=i,
                    start_ms=i * 1000,
                    end_ms=(i + 1) * 1000,
                    speaker_label="SPK_0",
                    transcript_text=orthographic,
                )
            )

    settings = load_settings(REPO_ROOT / ".env")
    correct_segments(segments, glossary=SYNTHETIC_GLOSSARY, settings=settings)

    corrected = [s for s in segments if s.corrected_text]
    flagged = [s for s in segments if s.needs_review]
    print(
        f"\n[보정 오교정 안전 — {source}] 발화 {len(segments)}건 + 합성 용어집 {len(SYNTHETIC_GLOSSARY)}개"
    )
    print(f"  교정 발생(=원문 훼손 후보): {len(corrected)}건 ← 0이어야 함")
    for s in corrected:
        print(f"    ⚠️ {s.transcript_text[:50]} → {s.corrected_text[:50]}")
    print(f"  리뷰 표시(안전한 보수 동작): {len(flagged)}건")
    for s in flagged[:5]:
        print(f"    · {s.transcript_text[:60]}")


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    phone = sub.add_parser("phone-cer", help="전화망 8kHz 클립 전사 CER")
    phone.add_argument("--clips", type=int, default=30)

    safety = sub.add_parser(
        "correction-safety", help="남의 대화 + 합성 용어집 교정 시 원문 훼손 수"
    )
    safety.add_argument("--source", type=str, default="phone")
    safety.add_argument("--utterances", type=int, default=80)

    args = parser.parse_args()
    if args.command == "phone-cer":
        run_phone_cer(args.clips)
    elif args.command == "correction-safety":
        run_correction_safety(args.source, args.utterances)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
