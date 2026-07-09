"""채팅 음성 입출력 — 음성 메시지 STT(OpenAI) + 페르소나 답변 TTS(ElevenLabs).

녹음 분석용 전사(/analysis)와 별개: 여기는 가족이 페르소나에게 말한 짧은 음성 메시지를
받아쓰고(STT), 페르소나 답변을 고인 목소리로 합성(TTS)한다. MVP 검증 로직 이식.
"""

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.config import Settings
from app.core.errors import MissingVoiceError, TTSProviderError
from app.pipeline.transcription.audio import download_audio
from app.providers.llm import create_openai_client
from app.providers.tts import create_tts_provider
from app.providers.tts.interface import TTSResult
from app.schemas.voice import (
    SpeechSynthesisRequest,
    VoiceCloneRequest,
    VoiceCloneResponse,
    VoiceSampleSegment,
    VoiceTranscriptionResponse,
)

# 다구간 클론 샘플 튜닝값
_CLONE_SAMPLE_MAX_SECONDS = 180.0  # 이어붙인 샘플 총 길이 상한
_CLONE_MIN_SEGMENT_MS = 800  # 이보다 짧은 구간은 버림(잡음·토막 방지)


def transcribe_voice_message(
    audio: bytes,
    filename: str,
    *,
    language: str,
    settings: Settings,
) -> VoiceTranscriptionResponse:
    client = create_openai_client(settings, "voice message stt")
    buffer = io.BytesIO(audio)
    buffer.name = filename or "audio.wav"
    response = client.audio.transcriptions.create(
        model=settings.openai_stt_model,
        file=buffer,
        language=language,
    )
    return VoiceTranscriptionResponse(
        text=(response.text or "").strip(),
        provider="openai",
        model=settings.openai_stt_model,
    )


def synthesize_speech(
    request: SpeechSynthesisRequest, *, settings: Settings
) -> TTSResult:
    voice_id = request.voice_id or settings.elevenlabs_default_voice_id
    if not voice_id:
        raise MissingVoiceError(
            "voiceId가 필요합니다 (요청의 voiceId 또는 ELEVENLABS_DEFAULT_VOICE_ID)."
        )
    speed = request.speed if request.speed is not None else settings.elevenlabs_speed
    provider = create_tts_provider(settings)
    return provider.synthesize(request.text, voice_id=voice_id, speed=speed)


def _ffmpeg_normalize(src: Path, dst: Path, start_ms: int | None, end_ms: int | None) -> None:
    """구간(있으면)만 잘라 mono 22050 pcm_s16le wav로 정규화(concat -c copy용 통일 파라미터)."""
    cmd = ["ffmpeg", "-nostdin", "-y"]
    if start_ms is not None:
        cmd += ["-ss", str(start_ms / 1000), "-to", str(end_ms / 1000)]
    cmd += ["-i", str(src), "-ac", "1", "-ar", "22050", "-c:a", "pcm_s16le", str(dst)]
    subprocess.run(cmd, check=True, capture_output=True)


def _build_multi_sample(segments: list[VoiceSampleSegment], *, settings: Settings) -> Path:
    """대상자 구간들을 내려받아 잘라 이어붙인 단일 wav(≤_CLONE_SAMPLE_MAX_SECONDS)."""
    if shutil.which("ffmpeg") is None:  # 배포엔 설치돼 있음(Dockerfile). 로컬 방어.
        raise TTSProviderError("ffmpeg가 필요합니다(다구간 샘플 이어붙이기).")
    workdir = Path(tempfile.mkdtemp(prefix="voice_clone_"))
    parts: list[Path] = []
    budget = 0.0
    try:
        for i, seg in enumerate(segments):
            if budget >= _CLONE_SAMPLE_MAX_SECONDS:
                break
            if seg.start_ms is not None and (seg.end_ms - seg.start_ms) < _CLONE_MIN_SEGMENT_MS:
                continue
            src = download_audio(
                str(seg.audio_url),
                timeout_seconds=settings.audio_download_timeout_seconds,
                max_bytes=settings.max_audio_bytes,
                temp_dir=workdir,
            )
            part = workdir / f"part_{i}.wav"
            try:
                _ffmpeg_normalize(src, part, seg.start_ms, seg.end_ms)
            finally:
                src.unlink(missing_ok=True)
            parts.append(part)
            dur = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(part)],
                capture_output=True, text=True,
            ).stdout.strip()
            budget += float(dur) if dur else 0.0
        if not parts:
            raise TTSProviderError("유효한 대상자 구간이 없습니다(모두 너무 짧거나 비어 있음).")
        listfile = workdir / "list.txt"
        listfile.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
        out = workdir / "sample.wav"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-f", "concat", "-safe", "0",
             "-i", str(listfile), "-c", "copy", str(out)],
            check=True, capture_output=True,
        )
        return out
    except subprocess.CalledProcessError as exc:  # noqa: TRY302 - 컨텍스트 붙여 재발생
        detail = (exc.stderr or b"").decode("utf-8", "ignore")[-500:]
        raise TTSProviderError(f"샘플 이어붙이기 실패: {detail}") from exc


def clone_voice_from_sample(
    request: VoiceCloneRequest, *, settings: Settings
) -> VoiceCloneResponse:
    """고인 목소리 샘플 → 클론 음성 등록 → voiceId. BE는 이 값을 persona.voiceId에 저장.

    두 방식: (1) sampleAudioUrl 단일 클립, (2) samples 다구간 — 화자 식별로 전 통화에서
    모은 대상자 구간들을 잘라 이어붙여 한 목소리로 학습(샘플 길이·다양성 확보).
    """
    provider = create_tts_provider(settings)
    if request.samples:
        sample_path = _build_multi_sample(request.samples, settings=settings)
        workdir = sample_path.parent
        try:
            voice_id = provider.clone_voice(request.name, sample_path)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        return VoiceCloneResponse(voice_id=voice_id, provider="elevenlabs")

    audio_path = download_audio(
        str(request.sample_audio_url),
        timeout_seconds=settings.audio_download_timeout_seconds,
        max_bytes=settings.max_audio_bytes,
    )
    try:
        voice_id = provider.clone_voice(request.name, audio_path)
    finally:
        audio_path.unlink(missing_ok=True)
    return VoiceCloneResponse(voice_id=voice_id, provider="elevenlabs")
