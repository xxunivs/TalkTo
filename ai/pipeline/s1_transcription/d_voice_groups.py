"""녹음 여러 개의 같은 목소리 묶기 (ECAPA 지문 유사도, 실패 시 method=per_recording)"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.external_api.audio import download_audio
from app.schema.transcript import TranscriptSegment
from app.scratch import scratch_dir
from app.setting import Settings

logger = logging.getLogger(__name__)

# 녹음 사이 기준은 speaker_id_threshold (실측: 같은 사람 0.605~0.948, 다른 사람 0.258~0.417)
# 한 녹음 안 두 화자 병합은 더 엄하게 (다른 사람을 묶으면 엉뚱한 목소리 복제)
SAME_RECORDING_THRESHOLD = 0.7


@dataclass
class SpeakerItem:
    recording_id: str
    speaker_label: str
    speech_ms: int
    embedding: list[float] | None = None


@dataclass
class VoiceCluster:
    members: list[SpeakerItem] = field(default_factory=list)
    centroid: list[float] | None = None

    @property
    def speech_ms(self) -> int:
        return sum(m.speech_ms for m in self.members)


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    norm = (sum(a * a for a in left) ** 0.5) * (sum(b * b for b in right) ** 0.5)
    return dot / norm if norm else 0.0


def _mean(vectors: list[list[float]]) -> list[float]:
    return [sum(values) / len(vectors) for values in zip(*vectors)]


def cluster_speakers(
    items: list[SpeakerItem],
    *,
    threshold: float,
    same_recording_threshold: float = SAME_RECORDING_THRESHOLD,
) -> list[VoiceCluster]:
    """발화 긴 화자부터 가장 비슷한 묶음에 배정 (지문 없는 화자는 단독, 결과는 발화 시간순)"""
    clusters: list[VoiceCluster] = []
    for item in sorted(items, key=lambda i: -i.speech_ms):
        best, best_score = None, -1.0
        if item.embedding is not None:
            for cluster in clusters:
                if cluster.centroid is None:
                    continue
                score = _cosine(item.embedding, cluster.centroid)
                same_recording = any(m.recording_id == item.recording_id for m in cluster.members)
                needed = same_recording_threshold if same_recording else threshold
                if score >= needed and score > best_score:
                    best, best_score = cluster, score
        if best is None:
            best = VoiceCluster()
            clusters.append(best)
        best.members.append(item)
        vectors = [m.embedding for m in best.members if m.embedding is not None]
        best.centroid = _mean(vectors) if vectors else None
    return sorted(clusters, key=lambda c: -c.speech_ms)


def speech_ms_by_label(segments: list[TranscriptSegment]) -> dict[str, int]:
    out: dict[str, int] = {}
    for seg in segments:
        out[seg.speaker_label] = out.get(seg.speaker_label, 0) + max(0, seg.end_ms - seg.start_ms)
    return out


def group_voices(
    recordings: list[tuple[str, str, list[TranscriptSegment]]],
    *,
    settings: Settings,
) -> tuple[list[VoiceCluster], str]:
    """(recording_id, audio_url, segments) 목록 -> 목소리 묶음, 사용한 방법"""
    items: list[SpeakerItem] = []
    for recording_id, _, segments in recordings:
        for label, ms in speech_ms_by_label(segments).items():
            items.append(SpeakerItem(recording_id, label, ms))

    try:
        from pipeline.s1_transcription.b_speaker_id import EMBED_LOCK, shared_embedder
    except ImportError:
        logger.warning("speaker_id 패키지 미설치 — 목소리를 녹음별로만 나눈다")
        return cluster_speakers(items, threshold=2.0), "per_recording"

    embedder = shared_embedder(
        Path(settings.speaker_model_dir) if settings.speaker_model_dir else None
    )
    by_key = {(i.recording_id, i.speaker_label): i for i in items}
    embedded = 0
    failures = 0
    for recording_id, audio_url, segments in recordings:
        path = None
        try:
            # 주소 하나가 만료돼도 나머지 녹음은 묶도록 다운로드도 try 안에
            path = download_audio(
                audio_url,
                timeout_seconds=settings.audio_download_timeout_seconds,
                max_bytes=settings.max_audio_bytes,
                temp_dir=scratch_dir(settings),
            )
            with EMBED_LOCK:
                vectors = embedder.embed_speakers(path, segments)
            for label, vector in vectors.items():
                by_key[(recording_id, label)].embedding = vector
                embedded += 1
        except Exception:  # 한 녹음 실패에도 나머지 진행
            failures += 1
            logger.exception("voice embedding failed for recording %s", recording_id)
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    if not embedded:
        if failures:
            # 대부분 모델 로드 실패 (인터넷 없음, 권한)
            logger.error(
                "voice grouping fell back to per_recording: all %d recordings failed", failures
            )
        return cluster_speakers(items, threshold=2.0), "per_recording"
    return cluster_speakers(items, threshold=settings.speaker_id_threshold), "ecapa"
