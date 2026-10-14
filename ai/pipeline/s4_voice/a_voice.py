"""채팅 음성 입출력 — 음성 메시지 STT(OpenAI) + 페르소나 답변 TTS(ElevenLabs·MiniMax).

녹음 분석용 전사(/analysis)와 별개: 여기는 가족이 페르소나에게 말한 짧은 음성 메시지를
받아쓰고(STT), 페르소나 답변을 고인 목소리로 합성(TTS)한다.
"""

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from pydantic import HttpUrl

from infra.setting import Settings
from infra.error import (
    InvalidVoiceSampleError,
    MissingVoiceError,
    TTSProviderError,
)
from infra.external_api.audio import download_audio
from infra.external_api.openai_client import create_openai_client
from infra.external_api.tts import TTSResult, create_tts_provider
from pipeline.s4_voice import c_sample_quality as quality
from pipeline.s4_voice.b_emotion import strip_emotion_tags
from infra.schema.memory import PersistedTranscriptSegment
from infra.schema.voice import (
    SpeechSynthesisRequest,
    VoiceCloneFromTranscriptRequest,
    VoiceCloneRequest,
    VoiceCloneResponse,
    VoiceSampleSegment,
    VoiceTranscriptionResponse,
)

# 다구간 클론 샘플 튜닝값
_CLONE_SAMPLE_MAX_SECONDS = 180.0  # 이어붙인 샘플 총 길이 상한
_CLONE_CANDIDATE_SECONDS = 600.0  # 품질을 재 볼 후보 총 길이(여기서 깨끗한 180초를 고른다)
_CLONE_MIN_SAMPLE_SECONDS = 10.0  # 기준을 넘는 구간이 이보다 적으면 기준을 낮춰서라도 채운다
_CLONE_MIN_SEGMENT_MS = 800  # 이보다 짧은 구간은 버림(잡음·토막 방지)
_CLONE_EDGE_TRIM_MS = 80  # 구간 앞뒤를 조금 깎는다. 화자 경계에 남의 숨·말끝이 붙어 있곤 하다
_CLONE_GAP_SECONDS = 0.3  # 구간 사이 쉼. 딱 붙이면 말이 이어 붙은 소리로 학습한다

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
    def pick(asked: float | None, fallback: float) -> float:
        return fallback if asked is None else asked

    # 감정 표시가 글에 남아 있으면 소리로 읽힌다. 저장된 대답에도 남아 있을 수 있어 여기서 한 번 더 지운다.
    text = strip_emotion_tags(request.text) or request.text
    provider = create_tts_provider(settings, voice_id=voice_id)
    return provider.synthesize(
        text,
        voice_id=voice_id,
        speed=pick(request.speed, settings.elevenlabs_speed),
        stability=pick(request.stability, settings.elevenlabs_stability),
        similarity_boost=pick(request.similarity_boost, settings.elevenlabs_similarity_boost),
        style=pick(request.style, settings.elevenlabs_style),
        emotion=request.emotion,
    )


def _ffmpeg_normalize(
    src: Path, dst: Path, start_ms: int, end_ms: int, *, loudnorm: bool = False
) -> None:
    """구간만 잘라 mono 22050 pcm_s16le wav로 정규화(concat -c copy용 통일 파라미터).

    loudnorm 이면 구간마다 소리 크기를 맞춘다(통화마다 크기가 달라 학습이 흔들리지 않게).
    """
    filters = ["-af", "loudnorm=I=-20:TP=-2:LRA=11"] if loudnorm else []
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-ss", str(start_ms / 1000), "-to", str(end_ms / 1000),
         "-i", str(src), *filters, "-ac", "1", "-ar", "22050", "-c:a", "pcm_s16le", str(dst)],
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
    """대상자 구간들 → 깨끗한 구간만 골라 이어붙인 단일 wav(≤_CLONE_SAMPLE_MAX_SECONDS).

    1) 녹음마다 한 번씩 먼저 다 내려받는다. 2) 구간마다 앞뒤를 조금 깎아 자르고 품질을 잰다
    (c_sample_quality). 3) 기준을 넘는 구간을 점수 순으로 녹음마다 번갈아 고른다.
    4) 고른 구간은 소리 크기를 맞춰 다시 자르고, 짧은 쉼을 끼워 이어붙인다.
    """
    if shutil.which("ffmpeg") is None:  # 배포엔 설치돼 있음(Dockerfile). 로컬/설정 문제.
        raise TTSProviderError("ffmpeg가 필요합니다(다구간 샘플 이어붙이기).")
    workdir = Path(tempfile.mkdtemp(prefix="voice_clone_"))
    try:
        # 서명된 주소는 곧 만료된다. 자르기 전에 녹음마다 한 번씩 먼저 다 받아 둔다.
        sources: dict[str, Path] = {}
        for url in dict.fromkeys(str(seg.audio_url) for seg in segments):
            sources[url] = download_audio(
                url,
                timeout_seconds=settings.audio_download_timeout_seconds,
                max_bytes=settings.max_audio_bytes,
                temp_dir=workdir,
            )
        measured: list[tuple[VoiceSampleSegment, int, int, quality.SegmentQuality]] = []
        for i, seg in enumerate(segments):
            url = str(seg.audio_url)
            start_ms, end_ms = seg.start_ms, seg.end_ms
            if end_ms - start_ms > _CLONE_MIN_SEGMENT_MS + 2 * _CLONE_EDGE_TRIM_MS:
                start_ms, end_ms = start_ms + _CLONE_EDGE_TRIM_MS, end_ms - _CLONE_EDGE_TRIM_MS
            raw = workdir / f"raw_{i}.wav"
            try:
                _ffmpeg_normalize(sources[url], raw, start_ms, end_ms)
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or b"").decode("utf-8", "ignore")[-300:]
                raise InvalidVoiceSampleError(
                    f"구간 {i}(startMs={seg.start_ms}, endMs={seg.end_ms}) 컷 실패: {detail}"
                ) from exc
            # 선언된 구간이 아니라 '실제로 잘린 길이'로 판단 — 오디오 밖 구간은 0초가 된다.
            q = quality.measure_wav(raw)
            raw.unlink(missing_ok=True)
            if q.seconds * 1000 < _CLONE_MIN_SEGMENT_MS:
                continue
            measured.append((seg, start_ms, end_ms, q))
        if not measured:
            raise InvalidVoiceSampleError(
                f"유효한 대상자 구간이 없습니다(자른 뒤 길이가 모두 {_CLONE_MIN_SEGMENT_MS}ms 미만). "
                "startMs/endMs가 해당 오디오 범위 안인지 확인하세요."
            )
        chosen = quality.pick(
            [(str(seg.audio_url), q) for seg, _, _, q in measured],
            budget_seconds=_CLONE_SAMPLE_MAX_SECONDS,
            min_seconds=_CLONE_MIN_SAMPLE_SECONDS,
        )

        gap = workdir / "gap.wav"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
             "-t", str(_CLONE_GAP_SECONDS), "-c:a", "pcm_s16le", str(gap)],
            check=True, capture_output=True,
        )
        parts: list[Path] = []
        for n, i in enumerate(chosen):
            seg, start_ms, end_ms, _ = measured[i]
            part = workdir / f"part_{n}.wav"
            _ffmpeg_normalize(sources[str(seg.audio_url)], part, start_ms, end_ms, loudnorm=True)
            parts += [part, gap]
        listfile = workdir / "list.txt"
        listfile.write_text("".join(f"file '{p}'\n" for p in parts[:-1]), encoding="utf-8")
        out = workdir / "sample.wav"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-f", "concat", "-safe", "0",
             "-i", str(listfile), "-c", "copy", str(out)],
            check=True, capture_output=True,
        )
        for src in sources.values():
            src.unlink(missing_ok=True)
        return out
    except subprocess.CalledProcessError as exc:  # 이어붙이기 단계 실패
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
    provider = create_tts_provider(settings, provider=request.provider)
    if request.samples:
        # 품질을 재 볼 후보를 통화마다 고르게 추린다. 실제로 담을 180초는 품질을 재서 고른다.
        selected = select_clone_samples(
            request.samples, max_seconds=_CLONE_CANDIDATE_SECONDS
        )
        sample_path = _build_multi_sample(selected, settings=settings)
        workdir = sample_path.parent
        try:
            voice_id = provider.clone_voice(request.name, sample_path)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        return VoiceCloneResponse(voice_id=voice_id, provider=provider.name)

    audio_path = download_audio(
        str(request.sample_audio_url),
        timeout_seconds=settings.audio_download_timeout_seconds,
        max_bytes=settings.max_audio_bytes,
    )
    try:
        voice_id = provider.clone_voice(request.name, audio_path)
    finally:
        audio_path.unlink(missing_ok=True)
    return VoiceCloneResponse(voice_id=voice_id, provider=provider.name)
