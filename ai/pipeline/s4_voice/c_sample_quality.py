"""목소리 복제 재료 고르기: 그분 구간마다 소리를 재서 깨끗한 것만 남긴다.

IVC(즉시 복제)는 넣는 소리를 그대로 따라 하므로 재료 품질이 결과를 크게 좌우한다(실험:
같은 목소리라도 정리한 재료와 날 재료의 닮음 차이가 컸다). 길이만 보고 고르면 잡음이 많거나
소리가 찢어진 구간, 숨소리만 긴 구간도 들어간다. 그래서 구간마다
  - 신호 대 잡음(SNR): 말하는 부분(상위 10% 프레임)과 쉬는 부분(하위 10% 프레임)의 크기 차
  - 찢어짐(clipping): 최대치에 닿은 표본 비율
  - 크기: 너무 작게 녹음된 구간
을 재서 기준에 못 미치는 구간을 빼고, 점수 높은 순으로 녹음마다 번갈아 담는다.
"""

from dataclasses import dataclass
from pathlib import Path
import wave

import numpy as np

MIN_SNR_DB = 12.0  # 이보다 잡음이 크면 뺀다
MAX_CLIP_RATIO = 0.002  # 표본 0.2% 넘게 찢어졌으면 뺀다
MIN_LEVEL_DB = -45.0  # 이보다 작게 녹음된 구간은 뺀다
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
        """깨끗할수록, 조금 길수록(문장이 온전할수록) 높다. 아주 긴 구간이 독식하지 않게 8초에서 멈춘다."""
        return min(self.snr_db, 35.0) + 0.5 * min(self.seconds, 8.0) - 2000.0 * self.clip_ratio


def measure(pcm: np.ndarray, sample_rate: int) -> SegmentQuality:
    """-1~1 사이 mono 표본으로 품질을 잰다."""
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
    """16비트 mono wav(_ffmpeg_normalize 결과)를 읽어 잰다.

    max_seconds 를 주면 앞에서 그만큼만 읽는다. 다 읽으면 길이만큼 메모리를 쓴다(44.1kHz 에서
    1분에 약 29MB, 표본·제곱·절댓값을 따로 들기 때문).
    """
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
    """(녹음 주소, 품질) 목록에서 담을 후보의 번호를 고른다.

    원래 순서대로 돌려준다(keep_order 면 고른 순서대로).

    기준을 넘는 구간만 쓰되, 그것만으로 min_seconds 가 안 되면 기준을 낮춰 점수 높은 것부터 쓴다
    (통화 녹음처럼 전부 잡음이 있는 경우에도 복제는 되게). 녹음마다 번갈아 담아 한 통화가
    다 차지하지 않게 한다.
    """
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
    """pick 과 같이 고르되, 후보마다 몇 초를 쓸지까지 정한다(번호 → 초).

    pick 은 담은 길이가 예산에 닿을 때까지 후보를 '통째로' 담아, 마지막 하나가 길면(혼자 몇 분씩
    이어 말한 구간) 예산을 훌쩍 넘었다. 그 wav 가 Fish Audio 의 올리기 한도(30~40MB, 이 형식으로
    약 6분)를 넘어 413 으로 목소리를 못 만들었다(2026-10-18). 넘치는 후보는 남은 예산만큼만 쓰고,
    그 남은 몫이 min_part_seconds 보다 짧으면 뺀다. 그래서 쓰는 말소리는 늘 budget_seconds 이하다.
    """
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
