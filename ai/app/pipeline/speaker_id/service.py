"""화자 식별 — ECAPA-TDNN 음성 임베딩으로 어느 화자가 대상자인지 판별.

reference 임베딩(Intake 음성 샘플 구간에서 1회 생성)과 녹음 속 화자별
임베딩의 코사인 유사도로 대상자 화자 라벨을 정한다. 유사도가 임계값에
못 미치면 확정하지 않는다 (내용 기반 fallback은 2단계에서).

torch/speechbrain은 무거운 선택 의존성이라 모듈 import 시점이 아니라
사용 시점에 로드한다 — 미설치 환경에서도 서버는 뜬다.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from app.schemas.transcript import TranscriptSegment

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
# 화자당 임베딩에 사용할 최대 발화 길이 — ECAPA는 수십 초면 충분히 안정적
MAX_SPEECH_SECONDS = 60.0
# 이보다 짧은 세그먼트는 잡음·맞장구일 가능성이 높아 임베딩에서 제외
MIN_SEGMENT_MS = 800


@dataclass
class SpeakerMatch:
    speaker_label: str
    similarity: float


class SpeakerEmbedder:
    """ECAPA-TDNN 임베딩 (SpeechBrain spkrec-ecapa-voxceleb, Apache-2.0, CPU)."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir
        self._model = None

    def _load(self):
        if self._model is None:
            from speechbrain.inference.speaker import EncoderClassifier

            self._model = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir=str(self._cache_dir) if self._cache_dir else None,
                run_opts={"device": "cpu"},
            )
        return self._model

    def embed_waveform(self, waveform) -> list[float]:
        """mono 16kHz 텐서(1, samples) → 임베딩 벡터."""
        import torch

        model = self._load()
        with torch.no_grad():
            embedding = model.encode_batch(waveform).squeeze()
        return embedding.tolist()

    def embed_clip(
        self, audio_path: Path, *, start_ms: int | None = None, end_ms: int | None = None
    ) -> list[float]:
        waveform = load_mono_16k(audio_path)
        if start_ms is not None or end_ms is not None:
            waveform = slice_ms(waveform, start_ms or 0, end_ms)
        return self.embed_waveform(waveform)

    def embed_speakers(
        self, audio_path: Path, segments: list[TranscriptSegment]
    ) -> dict[str, list[float]]:
        """녹음 하나에서 화자 라벨별 임베딩 생성."""
        import torch

        waveform = load_mono_16k(audio_path)
        clips: dict[str, list] = {}
        budgets: dict[str, float] = {}
        for segment in segments:
            if segment.end_ms - segment.start_ms < MIN_SEGMENT_MS:
                continue
            used = budgets.get(segment.speaker_label, 0.0)
            if used >= MAX_SPEECH_SECONDS:
                continue
            clip = slice_ms(waveform, segment.start_ms, segment.end_ms)
            clips.setdefault(segment.speaker_label, []).append(clip)
            budgets[segment.speaker_label] = used + clip.shape[1] / SAMPLE_RATE

        return {
            label: self.embed_waveform(torch.cat(parts, dim=1))
            for label, parts in clips.items()
        }


def identify_subject(
    speaker_embeddings: dict[str, list[float]],
    reference_embedding: list[float],
    *,
    threshold: float,
) -> SpeakerMatch | None:
    """reference와 가장 유사한 화자를 찾고, 임계값 미달이면 확정하지 않는다."""
    best: SpeakerMatch | None = None
    for label, embedding in speaker_embeddings.items():
        similarity = cosine_similarity(embedding, reference_embedding)
        if best is None or similarity > best.similarity:
            best = SpeakerMatch(speaker_label=label, similarity=similarity)
    if best is None or best.similarity < threshold:
        if best is not None:
            logger.info(
                "subject speaker not confirmed: best %s similarity %.3f < %.3f",
                best.speaker_label,
                best.similarity,
                threshold,
            )
        return None
    return best


def cosine_similarity(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = sum(a * a for a in left) ** 0.5
    norm_right = sum(b * b for b in right) ** 0.5
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)


def load_mono_16k(audio_path: Path):
    """오디오 → mono 16kHz 텐서(1, samples).

    torchaudio.load는 버전에 따라 torchcodec을 요구해 컨테이너에서 실패한다(실측:
    "TorchCodec is required for load_with_torchcodec"). 그래서 ffmpeg으로 16kHz mono
    float32 PCM으로 디코딩해 numpy→torch로 직접 읽는다 — torchaudio 백엔드/torchcodec
    의존 없이 m4a/mp3/wav 등 어떤 포맷이든 안전.
    """
    import subprocess

    import torch

    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-i", str(audio_path), "-ac", "1", "-ar",
         str(SAMPLE_RATE), "-f", "f32le", "-"],
        check=True,
        capture_output=True,
    )
    # numpy 브릿지를 피해 바이트에서 바로 텐서 생성(numpy 버전 이슈 무관).
    return torch.frombuffer(bytearray(result.stdout), dtype=torch.float32).unsqueeze(0)


def slice_ms(waveform, start_ms: int, end_ms: int | None):
    start = int(start_ms * SAMPLE_RATE / 1000)
    end = int(end_ms * SAMPLE_RATE / 1000) if end_ms is not None else waveform.shape[1]
    return waveform[:, start:end]
