"""채팅 음성 입출력 (음성 메시지 STT: OpenAI, 답변 TTS: Fish Audio, 예전 목소리는 ElevenLabs, MiniMax)"""

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from pydantic import HttpUrl

from infra.error import (
    InvalidVoiceSampleError,
    MissingVoiceError,
    TTSProviderError,
)
from infra.external_api.audio import download_audio
from infra.external_api.openai_client import create_openai_client
from infra.external_api.tts import TTSResult, create_clone_provider, create_tts_provider
from infra.external_api.tts.fish import strip_fish_cues
from infra.schema.memory import PersistedTranscriptSegment
from infra.schema.voice import (
    SpeechSynthesisRequest,
    VoiceCloneFromTranscriptRequest,
    VoiceCloneRequest,
    VoiceCloneResponse,
    VoiceSampleSegment,
    VoiceTranscriptionResponse,
)
from infra.scratch import scratch_dir
from infra.setting import Settings
from pipeline.s4_voice import c_sample_quality as quality
from pipeline.s4_voice.b_emotion import strip_emotion_tags

# 다구간 클론 샘플 튜닝값
# 이어붙인 샘플 총 길이 상한 (Fish 즉시 복제는 깨끗한 1분 안팎이면 충분, 청취 비교 기준)
_CLONE_SAMPLE_MAX_SECONDS = 60.0
_CLONE_CANDIDATE_SECONDS = 600.0  # 품질 측정 후보 총 길이 (여기서 깨끗한 60초 선택)
_CLONE_SAMPLE_RATE = 44100  # 업로드 wav 44.1kHz mono 16비트 (넓은 대역 고음 보존)
_CLONE_MIN_SAMPLE_SECONDS = 10.0  # 기준 통과분이 이보다 적으면 기준 완화
_CLONE_MIN_SEGMENT_MS = 800  # 최소 구간 길이 (잡음, 토막 방지)
_CLONE_EDGE_TRIM_MS = 80  # 구간 앞뒤 트림 (화자 경계의 남의 숨, 말끝 제거)
_CLONE_GAP_SECONDS = 0.3  # 구간 사이 쉼 (붙이면 이어 붙은 소리로 학습)

# BE persona.voiceId 초기 자리표시자 (실제 id 아님, 서버 기본 voiceId 로 폴백)
_PLACEHOLDER_VOICE_IDS = frozenset({"default-voice"})

# 음성 메시지 STT 시간 한도: BE 60초(ai.TRANSCRIBE_TIMEOUT) 안에 끝나도록 25초 x 2회
VOICE_STT_TIMEOUT_SECONDS = 25.0
VOICE_STT_MAX_RETRIES = 1


def transcribe_voice_message(
    audio: bytes,
    filename: str,
    *,
    language: str,
    settings: Settings,
) -> VoiceTranscriptionResponse:
    client = create_openai_client(
        settings,
        "voice message stt",
        timeout=VOICE_STT_TIMEOUT_SECONDS,
        max_retries=VOICE_STT_MAX_RETRIES,
    )
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


def synthesize_speech(request: SpeechSynthesisRequest, *, settings: Settings) -> TTSResult:
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

    # 감정 표시 재제거 (저장된 대답에 남은 표시가 소리로 읽히지 않도록, Fish 는 fish_plain_text 추가 제거)
    text = strip_emotion_tags(request.text) or request.text
    provider = create_tts_provider(settings, voice_id=voice_id)
    if provider.name != "fish" and getattr(settings, "fish_emotion", "off") != "off":
        # Fish 연기 표시는 타사 TTS 가 소리로 읽음 (FISH_EMOTION=off 면 표시가 없어 글 그대로)
        text = strip_fish_cues(text) or text
    # 속도, 결 값은 예전 ElevenLabs, MiniMax 용 (Fish 미사용)
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
    """구간 컷 -> mono 44100 pcm_s16le wav (concat -c copy 용, loudnorm 이면 음량 통일)"""
    filters = ["-af", "loudnorm=I=-20:TP=-2:LRA=11"] if loudnorm else []
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-ss",
            str(start_ms / 1000),
            "-to",
            str(end_ms / 1000),
            "-i",
            str(src),
            *filters,
            "-ac",
            "1",
            "-ar",
            str(_CLONE_SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            str(dst),
        ],
        check=True,
        capture_output=True,
    )


def _audio_seconds(path: Path) -> float:
    """컷 후 실제 길이 (오디오 밖 구간은 ffmpeg 가 0초 파일 생성)"""
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def _build_multi_sample(segments: list[VoiceSampleSegment], *, settings: Settings) -> Path:
    """대상자 구간 -> 깨끗한 구간만 이어붙인 단일 wav (최대 _CLONE_SAMPLE_MAX_SECONDS)"""
    if shutil.which("ffmpeg") is None:  # 배포는 Dockerfile 에 설치, 로컬 설정 문제
        raise TTSProviderError("ffmpeg가 필요합니다(다구간 샘플 이어붙이기).")
    # 서버 시작 시 비우는 임시 폴더 (강제 종료 잔여 녹음 방지, infra/scratch.py)
    workdir = Path(tempfile.mkdtemp(prefix="voice_clone_", dir=scratch_dir(settings)))
    try:
        # 서명 URL 만료 대비: 컷 전에 녹음별 1회 선다운로드
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
            # 측정도 앞 60초까지만 (통째 컷 시 10분에 약 290MB, 긴 구간 하나로 2.5GB 컨테이너 OOM 위험)
            end_ms = min(end_ms, start_ms + int(_CLONE_SAMPLE_MAX_SECONDS * 1000))
            raw = workdir / f"raw_{i}.wav"
            try:
                _ffmpeg_normalize(sources[url], raw, start_ms, end_ms)
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or b"").decode("utf-8", "ignore")[-300:]
                raise InvalidVoiceSampleError(
                    f"구간 {i}(startMs={seg.start_ms}, endMs={seg.end_ms}) 컷 실패: {detail}"
                ) from exc
            # 선언 구간이 아닌 실제 컷 길이로 판단 (오디오 밖 구간은 0초)
            q = quality.measure_wav(raw, max_seconds=_CLONE_SAMPLE_MAX_SECONDS)
            raw.unlink(missing_ok=True)
            if q.seconds * 1000 < _CLONE_MIN_SEGMENT_MS:
                continue
            measured.append((seg, start_ms, end_ms, q))
        if not measured:
            raise InvalidVoiceSampleError(
                f"유효한 대상자 구간이 없습니다(자른 뒤 길이가 모두 {_CLONE_MIN_SEGMENT_MS}ms 미만). "
                "startMs/endMs가 해당 오디오 범위 안인지 확인하세요."
            )
        # 후보별 사용 초 결정 (긴 구간 하나가 Fish 업로드 한도를 넘지 않도록)
        use = quality.pick_seconds(
            [(str(seg.audio_url), q) for seg, _, _, q in measured],
            budget_seconds=_CLONE_SAMPLE_MAX_SECONDS,
            min_seconds=_CLONE_MIN_SAMPLE_SECONDS,
            min_part_seconds=_CLONE_MIN_SEGMENT_MS / 1000,
        )

        gap = workdir / "gap.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=r={_CLONE_SAMPLE_RATE}:cl=mono",
                "-t",
                str(_CLONE_GAP_SECONDS),
                "-c:a",
                "pcm_s16le",
                str(gap),
            ],
            check=True,
            capture_output=True,
        )
        parts: list[Path] = []
        for n, i in enumerate(sorted(use)):
            seg, start_ms, end_ms, _ = measured[i]
            end_ms = min(end_ms, start_ms + int(use[i] * 1000))
            part = workdir / f"part_{n}.wav"
            _ffmpeg_normalize(sources[str(seg.audio_url)], part, start_ms, end_ms, loudnorm=True)
            parts += [part, gap]
        listfile = workdir / "list.txt"
        listfile.write_text("".join(f"file '{p}'\n" for p in parts[:-1]), encoding="utf-8")
        out = workdir / "sample.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listfile),
                "-c",
                "copy",
                str(out),
            ],
            check=True,
            capture_output=True,
        )
        for src in sources.values():
            src.unlink(missing_ok=True)
        return out
    except subprocess.CalledProcessError as exc:  # 이어붙이기 단계 실패
        shutil.rmtree(workdir, ignore_errors=True)
        detail = (exc.stderr or b"").decode("utf-8", "ignore")[-500:]
        raise InvalidVoiceSampleError(f"샘플 이어붙이기 실패: {detail}") from exc
    except BaseException:  # 그 외 실패: 임시 폴더 정리 후 재발생
        shutil.rmtree(workdir, ignore_errors=True)
        raise


def collect_subject_samples(
    segments: list[PersistedTranscriptSegment],
    subject_speaker_label: str,
    audio_url: HttpUrl,
) -> list[VoiceSampleSegment]:
    """전사 구간 중 대상자 라벨 정확 일치 구간 -> 클론 샘플 구간 (시간순, 컷과 상한은 _build_multi_sample)"""
    samples: list[VoiceSampleSegment] = []
    for seg in sorted(segments, key=lambda s: s.start_ms):
        if seg.speaker_label != subject_speaker_label:
            continue
        if seg.end_ms <= seg.start_ms:  # 무효 구간 제외
            continue
        samples.append(
            VoiceSampleSegment(audio_url=audio_url, start_ms=seg.start_ms, end_ms=seg.end_ms)
        )
    return samples


def select_clone_samples(
    samples: list[VoiceSampleSegment], *, max_seconds: float
) -> list[VoiceSampleSegment]:
    """예산 내 샘플 선별 (녹음별 긴 구간 우선 + 라운드로빈, 한 통화 독식 방지)"""
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
    """전사 결과 -> 대상자 구간 수집 -> 클론(IVC) -> voiceId (라벨 미확정이면 중단, 발화량 폴백 없음)"""
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
    """대상자 목소리 샘플 -> 클론 등록 -> voiceId (단일 클립 또는 다구간, 항상 Fish Audio)"""
    provider = create_clone_provider(settings)
    if request.samples:
        # 통화별 균등 후보 추림 (실제 60초는 품질 측정 후 선택)
        selected = select_clone_samples(request.samples, max_seconds=_CLONE_CANDIDATE_SECONDS)
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
        temp_dir=scratch_dir(settings),
    )
    try:
        voice_id = provider.clone_voice(request.name, audio_path)
    finally:
        audio_path.unlink(missing_ok=True)
    return VoiceCloneResponse(voice_id=voice_id, provider=provider.name)
