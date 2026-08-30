"""화자 식별 — ECAPA-TDNN 음성 임베딩으로 어느 화자가 대상자인지 판별.

reference 임베딩(Intake 음성 샘플 구간에서 1회 생성)과 녹음 속 화자별
임베딩의 코사인 유사도로 대상자 화자 라벨을 정한다. 유사도가 임계값에
못 미치면 확정하지 않는다 (내용 기반 fallback은 2단계에서).

torch/speechbrain은 무거운 선택 의존성이라 모듈 import 시점이 아니라
사용 시점에 로드한다 — 미설치 환경에서도 서버는 뜬다.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.external_service.llm import create_openai_client
from app.data_schema.context import SubjectContext
from app.data_schema.transcript import TranscriptSegment

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


# ===== 내용 기반 대상자 폴백 (ECAPA 성문 참조가 없을 때) =====
# 참조 클립이 없으면 성문 매칭을 못 하므로, 전사문의 호칭·맥락을 LLM으로 읽어
# '어느 화자가 대상자인지' 판정한다. 발화량 최다 추정이 아니라 호칭 방향이 근거.
# 확정 못 하면 None(찍지 않음).

SUBJECT_ID_SYSTEM_PROMPT = """너는 가족 통화 전사문에서 '대상자'가 어느 화자(speaker label)인지 판정하는 분석기다.

대상자는 이 녹음의 주인공(예: 할머니)이다. 판정 규칙:
1. 핵심 근거는 **호칭의 방향**이다. 한 화자가 다른 화자를 대상자의 호칭/이름으로 부르면(예: "할머니, 밥 먹었어?"), **불린 쪽**이 대상자다. 말한 쪽이 아니다.
2. 대상자가 스스로를 부르는 경우("할머니가 해줄게")도 있으니, 반드시 '말한 화자'와 '불린 대상'을 구분한다.
3. 대상자의 시점(자기 생애·건강·가족을 챙기는 말)은 **보조 근거일 뿐, 단독 근거가 될 수 없다.**
4. 발화량(누가 더 많이 말했나)·화제·말투만으로 대상자를 지목하지 마라.
5. **추측 금지.** 전사문에 대상자를 부르는 호칭이나 이름이 실제로 등장하고 그 방향으로 화자를 특정할 수 있을 때만 라벨을 반환한다. 그런 명시적 호칭·이름 근거가 없으면, 아무리 그럴듯해 보여도 subjectSpeakerLabel은 반드시 null이다.

출력은 JSON만: {"subjectSpeakerLabel": "SPK_0 | null", "confidence": "confirmed | inferred", "reason": "근거가 된 호칭·이름과 화자. 근거 없으면 null인 이유"}"""


def _build_user_prompt(
    segments: list[TranscriptSegment], subject_context: SubjectContext | None
) -> str:
    parts: list[str] = []
    subject = subject_context.subject if subject_context else None
    if subject and (subject.name or subject.address_term):
        parts.append(
            f"대상자: {subject.name or '미상'} (가족 내 호칭: {subject.address_term or '미상'})"
        )
    if subject_context and subject_context.family_members:
        members = "; ".join(
            f"{m.name}({m.relation_to_subject or '관계미상'}"
            + (f", 호칭 {'/'.join(m.address_terms)}" if m.address_terms else "")
            + ")"
            for m in subject_context.family_members
        )
        parts.append(f"가족 구성원: {members}")
    labels = sorted({s.speaker_label for s in segments})
    parts.append(f"등장 화자 라벨: {', '.join(labels)}")
    parts.append("전사문:")
    for s in segments:
        parts.append(f"[{s.speaker_label}] {s.transcript_text}")
    return "\n".join(parts)


def identify_subject_by_content(
    segments: list[TranscriptSegment],
    subject_context: SubjectContext | None,
    *,
    settings: Settings,
) -> str | None:
    """전사문 호칭·맥락으로 대상자 화자 라벨을 판정. 실패·미확정·API 오류는 None."""
    if not segments:
        return None
    valid_labels = {s.speaker_label for s in segments}
    try:
        client = create_openai_client(settings, "subject identification")
        response = client.chat.completions.create(
            model=settings.openai_analysis_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SUBJECT_ID_SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(segments, subject_context)},
            ],
        )
        payload = json.loads(response.choices[0].message.content or "{}")
    except Exception as error:  # LLM 오류가 분석을 깨지 않게 (미확정 진행)
        logger.warning("subject identification (content) failed: %s", error)
        return None
    label = payload.get("subjectSpeakerLabel")
    # 환각 방지: 실제 등장한 화자 라벨만 인정.
    if isinstance(label, str) and label in valid_labels:
        return label
    return None
