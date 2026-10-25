"""화자 식별 캘리브레이션 — 골드 13건으로 ECAPA 유사도 임계값 실측.

정답 매핑은 손으로 만들지 않는다: 골드 전사의 화자 라벨(할머니:/손녀: 등)과
Scribe 화자 라벨(SPK_n)을 텍스트 유사도(CER)로 자동 정렬해 "이 파일에서
할머니 = SPK_?"를 도출한다. reference 임베딩은 지정 파일의 대상자 발화로
만들어 Intake 음성 샘플(voiceSampleRef) 등록을 시뮬레이션한다.

Usage:
    python -m evaluation.speaker_id.lab [--reference "할머니 목소리 1"]

Requires: pip install speechbrain torchaudio torch (선택 의존성),
evaluation/e2e/results/*.json (전사 결과), evaluation/bakeoff/gold/*.txt.
"""

import argparse
import re

from app.schemas.transcript import TranscriptSegment
from app.pipeline.speaker_id.service import SpeakerEmbedder, cosine_similarity
from evaluation.metrics import cer
from evaluation.common import AUDIO_DIR, GOLD_DIR, load_segments, result_stems

SUBJECT_GOLD_LABEL = "할머니"
# reference 클립 길이(초) — Intake 음성 샘플 구간을 흉내 낸다
REFERENCE_CLIP_SECONDS = 20.0


def gold_speaker_texts(stem: str) -> dict[str, str]:
    """골드 전사를 화자 라벨별 텍스트로 분리."""
    texts: dict[str, list[str]] = {}
    raw = (GOLD_DIR / f"{stem}.txt").read_text(encoding="utf-8")
    for line in raw.splitlines():
        match = re.match(r"^([^:\n]{1,10}):\s*(.*)$", line)
        if match:
            texts.setdefault(match.group(1).strip(), []).append(match.group(2))
    return {label: " ".join(lines) for label, lines in texts.items()}


def subject_label_by_text(
    segments: list[TranscriptSegment], gold_texts: dict[str, str]
) -> str | None:
    """골드 문장별 최근접 세그먼트의 화자 라벨을 투표해 '할머니 = SPK_?' 도출.

    화자 전체 텍스트끼리 CER을 비교하면 골드가 잘린 파일(10번)에서 길이
    차이에 속는다 — 문장 단위 투표는 존재하는 문장만 세므로 강건하다.
    """
    subject_text = gold_texts.get(SUBJECT_GOLD_LABEL)
    if not subject_text:
        return None

    votes: dict[str, int] = {}
    for line in subject_text.split("."):
        line = line.strip()
        if len(line) < 5:
            continue
        best_label, best_cer = None, None
        for segment in segments:
            value = cer(line, segment.transcript_text)
            if best_cer is None or value < best_cer:
                best_label, best_cer = segment.speaker_label, value
        if best_label is not None:
            votes[best_label] = votes.get(best_label, 0) + 1
    if not votes:
        return None
    return max(votes, key=votes.get)


def reference_embedding_from(
    embedder: SpeakerEmbedder, stem: str, subject_label: str
) -> list[float]:
    """대상자 발화 앞부분 ~20초로 reference 임베딩 생성 (voiceSampleRef 시뮬레이션)."""
    import torch

    from app.pipeline.speaker_id.service import SAMPLE_RATE, load_mono_16k, slice_ms

    segments = load_segments(stem)
    waveform = load_mono_16k(AUDIO_DIR / f"{stem}.wav")
    clips, total = [], 0.0
    for segment in segments:
        if segment.speaker_label != subject_label:
            continue
        clip = slice_ms(waveform, segment.start_ms, segment.end_ms)
        clips.append(clip)
        total += clip.shape[1] / SAMPLE_RATE
        if total >= REFERENCE_CLIP_SECONDS:
            break
    return embedder.embed_waveform(torch.cat(clips, dim=1))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=str, default="할머니 목소리 1")
    args = parser.parse_args()

    stems = result_stems()
    embedder = SpeakerEmbedder()

    # 1) 정답 매핑
    truth: dict[str, str] = {}
    for stem in stems:
        label = subject_label_by_text(load_segments(stem), gold_speaker_texts(stem))
        if label:
            truth[stem] = label
    print(f"정답 매핑(텍스트 정렬): {len(truth)}건")

    # 2) reference 등록
    reference = reference_embedding_from(embedder, args.reference, truth[args.reference])
    print(f"reference: {args.reference} / {truth[args.reference]} (~{REFERENCE_CLIP_SECONDS:.0f}s)")

    # 3) 파일별 화자 임베딩 → 유사도
    same_sims, diff_sims, rows = [], [], []
    for stem in stems:
        embeddings = embedder.embed_speakers(
            AUDIO_DIR / f"{stem}.wav", load_segments(stem)
        )
        sims = {
            label: cosine_similarity(embedding, reference)
            for label, embedding in embeddings.items()
        }
        subject = truth[stem]
        predicted = max(sims, key=sims.get)
        for label, value in sims.items():
            (same_sims if label == subject else diff_sims).append(value)
        rows.append((stem, subject, predicted, sims))

    # 4) 리포트
    print("\n| 파일 | 정답 | 예측 | 정답 유사도 | 타화자 유사도 | 판정 |")
    print("|---|---|---|---|---|---|")
    correct = 0
    for stem, subject, predicted, sims in rows:
        ok = predicted == subject
        correct += ok
        other = max((v for k, v in sims.items() if k != subject), default=float("nan"))
        print(
            f"| {stem} | {subject} | {predicted} | {sims.get(subject, float('nan')):.3f} "
            f"| {other:.3f} | {'✅' if ok else '❌'} |"
        )
    print(f"\n식별 정확도: {correct}/{len(rows)}")
    if same_sims and diff_sims:
        low, high = min(same_sims), max(diff_sims)
        print(f"대상자 유사도: {low:.3f} ~ {max(same_sims):.3f}")
        print(f"타화자 유사도: {min(diff_sims):.3f} ~ {high:.3f}")
        print(f"분리 여유(margin): {low - high:+.3f} → 임계값 후보: {(low + high) / 2:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
