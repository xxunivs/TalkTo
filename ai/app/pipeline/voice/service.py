"""채팅 음성 입출력 — 음성 메시지 STT(OpenAI) + 페르소나 답변 TTS(ElevenLabs).

녹음 분석용 전사(/analysis)와 별개: 여기는 가족이 페르소나에게 말한 짧은 음성 메시지를
받아쓰고(STT), 페르소나 답변을 고인 목소리로 합성(TTS)한다. MVP 검증 로직 이식.
"""

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from pydantic import HttpUrl

from app.core.config import Settings
from app.core.errors import (
    InvalidVoiceSampleError,
    MissingVoiceError,
    TTSProviderError,
)
from app.pipeline.transcription.audio import download_audio
from app.providers.llm import create_openai_client
from app.providers.tts import TTSResult, create_tts_provider
from app.schemas.memory import PersistedTranscriptSegment
from app.schemas.voice import (
    SpeechSynthesisRequest,
    VoiceCloneFromTranscriptRequest,
    VoiceCloneRequest,
    VoiceCloneResponse,
    VoiceSampleSegment,
    VoiceTranscriptionResponse,
)

# 다구간 클론 샘플 튜닝값
_CLONE_SAMPLE_MAX_SECONDS = 180.0  # 이어붙인 샘플 총 길이 상한
_CLONE_MIN_SEGMENT_MS = 800  # 이보다 짧은 구간은 버림(잡음·토막 방지)

# BE가 persona.voiceId 초기값으로 넣는 자리표시자. 실제 음성 id가 아니라서 그대로
# 프로바이더에 넘기면 실패한다. 값이 없는 것으로 보고 서버 기본 voiceId로 폴백한다.
# (BE가 클론 voiceId를 persona.voiceId에 저장하기 시작하면 이 처리는 무해해진다.)
_PLACEHOLDER_VOICE_IDS = frozenset({"default-voice"})


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
    requested = (request.voice_id or "").strip()
    if requested in _PLACEHOLDER_VOICE_IDS:
        requested = ""
    voice_id = requested or settings.elevenlabs_default_voice_id
    if not voice_id:
        raise MissingVoiceError(
            "voiceId가 필요합니다 (요청의 voiceId 또는 ELEVENLABS_DEFAULT_VOICE_ID)."
        )
    speed = request.speed if request.speed is not None else settings.elevenlabs_speed
    provider = create_tts_provider(settings)
    return provider.synthesize(request.text, voice_id=voice_id, speed=speed)


def _ffmpeg_normalize(src: Path, dst: Path, start_ms: int, end_ms: int) -> None:
    """구간만 잘라 mono 22050 pcm_s16le wav로 정규화(concat -c copy용 통일 파라미터)."""
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-ss", str(start_ms / 1000), "-to", str(end_ms / 1000),
         "-i", str(src), "-ac", "1", "-ar", "22050", "-c:a", "pcm_s16le", str(dst)],
        check=True, capture_output=True,
    )


def _audio_seconds(path: Path) -> float:
    """자른 뒤 실제 길이. 구간이 오디오 밖이면 ffmpeg가 0초 파일을 만들므로 여기서 걸러낸다."""
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def _build_multi_sample(segments: list[VoiceSampleSegment], *, settings: Settings) -> Path:
    """대상자 구간들을 내려받아 잘라 이어붙인 단일 wav(≤_CLONE_SAMPLE_MAX_SECONDS)."""
    if shutil.which("ffmpeg") is None:  # 배포엔 설치돼 있음(Dockerfile). 로컬/설정 문제.
        raise TTSProviderError("ffmpeg가 필요합니다(다구간 샘플 이어붙이기).")
    workdir = Path(tempfile.mkdtemp(prefix="voice_clone_"))
    parts: list[Path] = []
    budget = 0.0
    try:
        for i, seg in enumerate(segments):
            if budget >= _CLONE_SAMPLE_MAX_SECONDS:
                break
            src = download_audio(
                str(seg.audio_url),
                timeout_seconds=settings.audio_download_timeout_seconds,
                max_bytes=settings.max_audio_bytes,
                temp_dir=workdir,
            )
            part = workdir / f"part_{i}.wav"
            try:
                _ffmpeg_normalize(src, part, seg.start_ms, seg.end_ms)
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or b"").decode("utf-8", "ignore")[-300:]
                raise InvalidVoiceSampleError(
                    f"구간 {i}(startMs={seg.start_ms}, endMs={seg.end_ms}) 컷 실패: {detail}"
                ) from exc
            finally:
                src.unlink(missing_ok=True)
            # 선언된 구간이 아니라 '실제로 잘린 길이'로 판단 — 오디오 밖 구간은 0초가 된다.
            seconds = _audio_seconds(part)
            if seconds * 1000 < _CLONE_MIN_SEGMENT_MS:
                part.unlink(missing_ok=True)
                continue
            parts.append(part)
            budget += seconds
        if not parts:
            raise InvalidVoiceSampleError(
                f"유효한 대상자 구간이 없습니다(자른 뒤 길이가 모두 {_CLONE_MIN_SEGMENT_MS}ms 미만). "
                "startMs/endMs가 해당 오디오 범위 안인지 확인하세요."
            )
        listfile = workdir / "list.txt"
        listfile.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
        out = workdir / "sample.wav"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-f", "concat", "-safe", "0",
             "-i", str(listfile), "-c", "copy", str(out)],
            check=True, capture_output=True,
        )
        return out
    except subprocess.CalledProcessError as exc:  # concat 단계 실패
        shutil.rmtree(workdir, ignore_errors=True)
        detail = (exc.stderr or b"").decode("utf-8", "ignore")[-500:]
        raise InvalidVoiceSampleError(f"샘플 이어붙이기 실패: {detail}") from exc
    except BaseException:  # 그 외 실패(구간 무효·다운로드 등) — 임시 폴더 정리 후 재발생
        shutil.rmtree(workdir, ignore_errors=True)
        raise


def collect_subject_samples(
    segments: list[PersistedTranscriptSegment],
    subject_speaker_label: str,
    audio_url: HttpUrl,
) -> list[VoiceSampleSegment]:
    """전사 구간 중 대상자 화자 구간만 골라 클론 샘플 구간으로 변환(시간순).

    상대 화자가 섞이면 엉뚱한 목소리로 학습되므로 라벨이 정확히 일치하는 구간만 취한다.
    실제 컷·길이 상한(_CLONE_SAMPLE_MAX_SECONDS)·짧은 토막 버림(_CLONE_MIN_SEGMENT_MS)은
    다운스트림(_build_multi_sample)이 처리하므로 여기선 필터·매핑만 한다.
    """
    samples: list[VoiceSampleSegment] = []
    for seg in sorted(segments, key=lambda s: s.start_ms):
        if seg.speaker_label != subject_speaker_label:
            continue
        if seg.end_ms <= seg.start_ms:  # 무효 구간 방어
            continue
        samples.append(
            VoiceSampleSegment(
                audio_url=audio_url, start_ms=seg.start_ms, end_ms=seg.end_ms
            )
        )
    return samples


def select_clone_samples(
    samples: list[VoiceSampleSegment], *, max_seconds: float
) -> list[VoiceSampleSegment]:
    """누적 풀이 상한을 넘으면 통화 간 고르게 + 긴 구간 우선으로 예산 안에서 선별한다.

    긴 구간 우선만 쓰면 말 많은 한 통화가 예산을 독식해 목소리 다양성이 준다. 그래서
    녹음(audio_url)별로 묶어 각자 긴 구간 우선 정렬 후, 녹음들을 라운드로빈으로 돌며 한
    구간씩 담아 여러 통화가 고르게 들어가게 한다. 상한 이하면 전부 반환. 실제 컷 길이
    기준 최종 상한은 _build_multi_sample이 다시 적용한다.
    """
    budget_ms = int(max_seconds * 1000)
    by_recording: dict[str, list[VoiceSampleSegment]] = {}
    for seg in samples:
        by_recording.setdefault(str(seg.audio_url), []).append(seg)
    for segs in by_recording.values():
        segs.sort(key=lambda s: s.end_ms - s.start_ms, reverse=True)

    selected: list[VoiceSampleSegment] = []
    total_ms = 0
    queues = [segs for segs in by_recording.values()]
    while total_ms < budget_ms and any(queues):
        for queue in queues:
            if total_ms >= budget_ms:
                break
            if queue:
                seg = queue.pop(0)
                selected.append(seg)
                total_ms += seg.end_ms - seg.start_ms
    return selected


def clone_voice_from_transcript(
    request: VoiceCloneFromTranscriptRequest, *, settings: Settings
) -> VoiceCloneResponse:
    """전사 결과 → 대상자 구간 자동 수집 → 클론(IVC) → voiceId.

    화자 식별로 확정된 subjectSpeakerLabel 구간을 모아 한 목소리로 학습한다. 라벨
    미확정(None)이면 엉뚱한 화자를 클론할 위험이 있어 진행하지 않는다(발화량 폴백 없음).
    """
    if not request.subject_speaker_label:
        raise InvalidVoiceSampleError(
            "대상자 화자 라벨(subjectSpeakerLabel)이 없어 클론 재료를 모을 수 없습니다. "
            "참조 클립으로 화자 식별을 먼저 수행하세요."
        )
    samples = collect_subject_samples(
        request.transcript_segments,
        request.subject_speaker_label,
        request.audio_url,
    )
    if not samples:
        raise InvalidVoiceSampleError(
            f"대상자 라벨 '{request.subject_speaker_label}'에 해당하는 구간이 없습니다."
        )
    clone_request = VoiceCloneRequest(name=request.name, samples=samples)
    return clone_voice_from_sample(clone_request, settings=settings)


def clone_voice_from_sample(
    request: VoiceCloneRequest, *, settings: Settings
) -> VoiceCloneResponse:
    """고인 목소리 샘플 → 클론 음성 등록 → voiceId. BE는 이 값을 persona.voiceId에 저장.

    두 방식: (1) sampleAudioUrl 단일 클립, (2) samples 다구간 — 화자 식별로 전 통화에서
    모은 대상자 구간들을 잘라 이어붙여 한 목소리로 학습(샘플 길이·다양성 확보).
    """
    provider = create_tts_provider(settings)
    if request.samples:
        # 누적 풀이 상한을 넘으면 긴 구간 우선으로 선별(앞 통화 편중 방지).
        selected = select_clone_samples(
            request.samples, max_seconds=_CLONE_SAMPLE_MAX_SECONDS
        )
        sample_path = _build_multi_sample(selected, settings=settings)
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
