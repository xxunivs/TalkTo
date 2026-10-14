"""녹음 여러 개에 걸쳐 같은 사람의 목소리를 하나로 묶는다.

전사(Scribe)는 녹음마다 화자를 따로 나눈다. 통화01 의 SPK_0 과 통화02 의 SPK_1 이 같은
사람이어도 알 수 없다. 화자마다 ECAPA 목소리 지문(b_speaker_id.SpeakerEmbedder)을 만들어
서로 비슷한 것끼리 묶으면, 사람이 녹음마다 고르지 않고 "목소리" 하나만 고르면 된다.

녹음 사이 임계값은 화자 식별과 같은 settings.speaker_id_threshold 다(실통화 실측: 같은 사람
0.605~0.948, 다른 사람 0.258~0.417). 한 녹음 안에서 둘로 나뉜 화자는 더 비슷해야 합친다. 다른 사람을
같은 사람으로 묶는 것이 더 위험해서다(엉뚱한 목소리가 복제된다).

torch·speechbrain 이 없거나 지문을 못 만들면 녹음마다 화자를 따로 둔다(method=per_recording).
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from infra.external_api.audio import download_audio
from infra.schema.transcript import TranscriptSegment
from infra.setting import Settings

logger = logging.getLogger(__name__)

# 한 녹음 안에서 나뉜 두 화자를 같은 사람으로 볼 유사도. 녹음 사이(threshold)보다 엄하게.
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
    """말을 많이 한 화자부터 가장 비슷한 묶음에 넣는다. 모자라면 새 묶음을 만든다.

    지문이 없는 화자(말이 너무 짧음)는 늘 혼자 둔다. 결과는 말한 시간이 긴 순서.
    """
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
    """(recording_id, audio_url, segments) 목록 → 목소리 묶음과 쓴 방법."""
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
            # 내려받기도 안에 둔다. 주소 하나가 만료돼도 나머지 녹음은 묶는다.
            path = download_audio(
                audio_url,
                timeout_seconds=settings.audio_download_timeout_seconds,
                max_bytes=settings.max_audio_bytes,
                temp_dir=settings.temp_dir,
            )
            with EMBED_LOCK:
                vectors = embedder.embed_speakers(path, segments)
            for label, vector in vectors.items():
                by_key[(recording_id, label)].embedding = vector
                embedded += 1
        except Exception:  # 한 녹음이 실패해도 나머지는 묶는다
            failures += 1
            logger.exception("voice embedding failed for recording %s", recording_id)
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    if not embedded:
        if failures:
            # 모델을 못 불러온 경우가 대부분이다(인터넷 없음·권한). 녹음마다 따로 나누는 것으로 버틴다.
            logger.error(
                "voice grouping fell back to per_recording: all %d recordings failed", failures
            )
        return cluster_speakers(items, threshold=2.0), "per_recording"
    return cluster_speakers(items, threshold=settings.speaker_id_threshold), "ecapa"
