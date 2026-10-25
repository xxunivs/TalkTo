"""외부 데이터(AI Hub) 일반화 랩 — 우리가 안 본 조건에서의 검증.

축별 용도:
- 전사 CER: 저음질 전화망(음질), 노인 자유대화(고령), 방언 2인(방언)
- 보정 오교정 안전: 남의 대화 + 용어집을 끼웠을 때 원문 훼손 0건 확인
  (교정 "성공률"은 그 가족 용어집이 있어야 해서 자체/베타 데이터 몫)

AI Hub 전사 표기 규칙(이중 전사 (철자)/(발음), 잡음 태그 b/ o/ 등)을
정규화한 뒤 채점한다. 골드가 이중 표기일 때는 두 해석 중 CER이 낮은
쪽을 쓴다 (STT가 어느 쪽으로 써도 정답 취급).

Usage:
    python -m evaluation.transcription.external_cer phone-cer [--clips 30]
    python -m evaluation.transcription.external_cer correction-safety --source phone [--utterances 80]
"""

import argparse
import os
import random
import re
from pathlib import Path

from dotenv import load_dotenv

from evaluation.metrics import cer
from evaluation.common import REPO_ROOT

AIHUB_ROOT = Path(__file__).resolve().parents[1] / "data" / "external"
PHONE_BASE = (
    AIHUB_ROOT
    / "571_전화망/007.저음질_전화망_음성인식_데이터/01.데이터/2.Validation"
)

# (철자)/(발음) 이중 전사
_DUAL = re.compile(r"\(([^()]*)\)\s*/\s*\(([^()]*)\)")
# 잡음·간투어 태그: b/ n/ o/ l/ u/ 등
_TAGS = re.compile(r"\b[bnolu]/")
# 불명료 구간 ((...)), 중단 표시 +, 발음 오류 표시 *
_UNCLEAR = re.compile(r"\(\([^)]*\)\)")


def normalize_gold_variants(text: str) -> tuple[str, str]:
    """AI Hub 표기 제거 → (철자 해석, 발음 해석) 두 가지 반환."""

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
    from app.providers.stt.elevenlabs_scribe import ElevenLabsScribeProvider

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
    print(f"\n[전화망 8kHz] {clips}클립 CER 평균 {sum(scores)/len(scores):.4f} "
          f"/ 중앙값 {scores[len(scores)//2]:.4f} / 최대 {scores[-1]:.4f}")
    print("최악 3건:")
    for value, gold_raw, hypothesis in sorted(worst, reverse=True)[:3]:
        print(f"  CER {value:.3f} | 골드: {gold_raw[:60]}")
        print(f"            | 전사: {hypothesis[:60]}")


# 오교정 안전 테스트용 합성 용어집 — 특정 가족과 무관한 일반 이름·지명
SYNTHETIC_GLOSSARY = [
    "김하늘", "하늘", "하늘아", "박민준", "민준", "민준아",
    "이서연", "서연", "서연아", "최준호", "준호", "준호야",
    "정읍", "속초", "구미", "매실청", "장아찌",
]


def run_correction_safety(source: str, utterances: int) -> None:
    from app.core.config import load_settings
    from app.schemas.transcript import TranscriptSegment
    from app.pipeline.correction.service import correct_segments

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
                    segment_index=i, start_ms=i * 1000, end_ms=(i + 1) * 1000,
                    speaker_label="SPK_0", transcript_text=orthographic,
                )
            )

    settings = load_settings(REPO_ROOT / ".env")
    correct_segments(segments, glossary=SYNTHETIC_GLOSSARY, settings=settings)

    corrected = [s for s in segments if s.corrected_text]
    flagged = [s for s in segments if s.needs_review]
    print(f"\n[보정 오교정 안전 — {source}] 발화 {len(segments)}건 + 합성 용어집 {len(SYNTHETIC_GLOSSARY)}개")
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

    phone = sub.add_parser("phone-cer")
    phone.add_argument("--clips", type=int, default=30)

    safety = sub.add_parser("correction-safety")
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
