"""목소리 복제 재료 선별 (구간별 SNR, clipping, 음량 측정 후 깨끗한 것만, IVC 는 재료 품질 의존)"""

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

MIN_SNR_DB = 12.0  # 최소 SNR
MAX_CLIP_RATIO = 0.002  # 최대 clipping 표본 비율 (0.2%)
MIN_LEVEL_DB = -45.0  # 최소 음량
_FRAME_SECONDS = 0.02


@dataclass(frozen=True)
class SegmentQuality:
    seconds: float
    snr_db: float
    clip_ratio: float
    level_db: float

    @property
    def ok(self) -> bool:
        return (
            self.snr_db >= MIN_SNR_DB
            and self.clip_ratio <= MAX_CLIP_RATIO
            and self.level_db >= MIN_LEVEL_DB
        )

    @property
    def score(self) -> float:
        """품질 점수 (깨끗하고 길수록 높음, 긴 구간 독식 방지로 길이는 8초 상한)"""
        return min(self.snr_db, 35.0) + 0.5 * min(self.seconds, 8.0) - 2000.0 * self.clip_ratio


def measure(pcm: np.ndarray, sample_rate: int) -> SegmentQuality:
    """-1~1 mono 표본 품질 측정 (SNR: 상위 10% 대 하위 10% 프레임)"""
    seconds = len(pcm) / sample_rate if sample_rate else 0.0
    frame = max(1, int(sample_rate * _FRAME_SECONDS))
    count = len(pcm) // frame
    if count < 5:
        return SegmentQuality(seconds, 0.0, 0.0, -120.0)
    frames = pcm[: count * frame].reshape(count, frame)
    rms = np.sqrt(np.mean(frames**2, axis=1)) + 1e-9
    speech = float(np.percentile(rms, 90))
    noise = float(np.percentile(rms, 10))
    level = float(20 * np.log10(np.sqrt(np.mean(pcm**2)) + 1e-9))
    return SegmentQuality(
        seconds=seconds,
        snr_db=float(20 * np.log10(speech / noise)),
        clip_ratio=float(np.mean(np.abs(pcm) >= 0.985)),
        level_db=level,
    )


def measure_wav(path: Path, *, max_seconds: float | None = None) -> SegmentQuality:
    """16비트 mono wav 품질 측정 (max_seconds 만큼만 읽음, 전체는 44.1kHz 1분에 약 29MB)"""
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        frames = w.getnframes()
        if max_seconds is not None:
            frames = min(frames, int(max_seconds * rate))
        raw = w.readframes(frames)
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return measure(pcm, rate)


def pick(
    candidates: list[tuple[str, SegmentQuality]],
    *,
    budget_seconds: float,
    min_seconds: float,
    keep_order: bool = False,
) -> list[int]:
    """담을 후보 번호 (기준 통과분 부족 시 기준 완화, 녹음별 번갈아, keep_order 면 고른 순서)"""
    good = [i for i, (_, q) in enumerate(candidates) if q.ok]
    if sum(candidates[i][1].seconds for i in good) < min_seconds:
        good = list(range(len(candidates)))
    queues: dict[str, list[int]] = {}
    for i in sorted(good, key=lambda i: candidates[i][1].score, reverse=True):
        queues.setdefault(candidates[i][0], []).append(i)
    chosen: list[int] = []
    total = 0.0
    while total < budget_seconds and any(queues.values()):
        for queue in queues.values():
            if queue and total < budget_seconds:
                i = queue.pop(0)
                chosen.append(i)
                total += candidates[i][1].seconds
    return chosen if keep_order else sorted(chosen)


def pick_seconds(
    candidates: list[tuple[str, SegmentQuality]],
    *,
    budget_seconds: float,
    min_seconds: float,
    min_part_seconds: float,
) -> dict[int, float]:
    """후보별 사용 초 (번호 -> 초, 항상 budget_seconds 이하, Fish 업로드 한도 초과 413 사고 대응)"""
    out: dict[int, float] = {}
    left = budget_seconds
    for i in pick(
        candidates, budget_seconds=budget_seconds, min_seconds=min_seconds, keep_order=True
    ):
        seconds = min(candidates[i][1].seconds, left)
        if seconds < min(min_part_seconds, candidates[i][1].seconds):
            continue
        out[i] = seconds
        left -= seconds
    return out
