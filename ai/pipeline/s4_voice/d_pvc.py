"""Premium 정밀 목소리(PVC) 재료 만들기 + ElevenLabs PVC 호출.

PVC 는 30분~3시간의 한 사람 목소리로 학습한다. BE 가 넘긴 그분 구간들을 녹음마다 한 번만 내려받아
자르고, 소리를 재서(c_sample_quality) 깨끗한 구간만 남긴 뒤, ElevenLabs 권장대로
  - mp3 192kbps, 30분이 넘지 않게 나눠서
  - 크기 -20 LUFS 안팎(권장 -23~-18 dB RMS), 최고점 -3 dB
로 이어 붙여 올린다.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from infra.error import InvalidVoiceSampleError, ProviderNotConfiguredError, TTSProviderError
from infra.external_api.audio import download_audio
from infra.external_api.tts.elevenlabs_pvc import ElevenLabsPVC
from infra.schema.voice import (
    PvcCreateRequest,
    PvcSamplesRequest,
    PvcSamplesResponse,
    PvcStatusResponse,
    PvcVerificationRequest,
)
from infra.setting import Settings
from pipeline.s4_voice import c_sample_quality as quality

logger = logging.getLogger(__name__)

_EDGE_TRIM_MS = 80
_MIN_SEGMENT_MS = 1500
_GAP_SECONDS = 0.3
_CHUNK_SECONDS = 25 * 60  # ElevenLabs: 아주 긴 소리는 30분 안팎으로 나눠 올린다
_MAX_SECONDS = 3 * 3600
_MIN_SECONDS = 30 * 60  # 기준을 넘는 구간이 이보다 적으면 기준을 낮춰서라도 채운다
_RATE = 44100


def pvc_client(settings: Settings) -> ElevenLabsPVC:
    if settings.elevenlabs_api_key is None:
        raise ProviderNotConfiguredError("ELEVENLABS_API_KEY 가 있어야 정밀 목소리를 만들 수 있다.")
    return ElevenLabsPVC(settings.elevenlabs_api_key.get_secret_value())


def _cut(src: Path, dst: Path, start_ms: int, end_ms: int, *, loudnorm: bool) -> None:
    filters = ["-af", "loudnorm=I=-20:TP=-3:LRA=11"] if loudnorm else []
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-ss", str(start_ms / 1000), "-to", str(end_ms / 1000),
         "-i", str(src), *filters, "-ac", "1", "-ar", str(_RATE), "-c:a", "pcm_s16le", str(dst)],
        check=True, capture_output=True,
    )


def build_pvc_files(request: PvcSamplesRequest, *, settings: Settings, workdir: Path) -> tuple[list[Path], float]:
    """(올릴 mp3 파일들, 총 초). workdir 은 부르는 쪽이 지운다."""
    if shutil.which("ffmpeg") is None:
        raise TTSProviderError("ffmpeg 가 필요합니다(정밀 목소리 재료 만들기).")
    # 서명된 주소는 곧 만료된다. 자르기 전에 녹음마다 한 번씩 먼저 다 받아 둔다.
    sources: dict[str, Path] = {}
    for url in dict.fromkeys(str(seg.audio_url) for seg in request.samples):
        sources[url] = download_audio(
            url,
            timeout_seconds=settings.audio_download_timeout_seconds,
            max_bytes=settings.max_audio_bytes,
            temp_dir=workdir,
        )
    measured: list[tuple[str, int, int, quality.SegmentQuality]] = []
    for i, seg in enumerate(request.samples):
        url = str(seg.audio_url)
        start_ms, end_ms = seg.start_ms, seg.end_ms
        if end_ms - start_ms < _MIN_SEGMENT_MS:
            continue
        start_ms, end_ms = start_ms + _EDGE_TRIM_MS, end_ms - _EDGE_TRIM_MS
        raw = workdir / f"raw_{i}.wav"
        try:
            _cut(sources[url], raw, start_ms, end_ms, loudnorm=False)
        except subprocess.CalledProcessError:
            continue
        q = quality.measure_wav(raw)
        raw.unlink(missing_ok=True)
        if q.seconds * 1000 >= _MIN_SEGMENT_MS - 2 * _EDGE_TRIM_MS:
            measured.append((url, start_ms, end_ms, q))
    if not measured:
        raise InvalidVoiceSampleError("정밀 목소리 재료로 쓸 구간이 없습니다.")
    chosen = quality.pick(
        [(url, q) for url, _, _, q in measured],
        budget_seconds=_MAX_SECONDS,
        min_seconds=_MIN_SECONDS,
    )

    gap = workdir / "gap.wav"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", f"anullsrc=r={_RATE}:cl=mono",
         "-t", str(_GAP_SECONDS), "-c:a", "pcm_s16le", str(gap)],
        check=True, capture_output=True,
    )
    chunks: list[list[Path]] = [[]]
    chunk_seconds = 0.0
    total = 0.0
    for n, i in enumerate(chosen):
        url, start_ms, end_ms, q = measured[i]
        if chunk_seconds + q.seconds > _CHUNK_SECONDS and chunks[-1]:
            chunks.append([])
            chunk_seconds = 0.0
        part = workdir / f"part_{n}.wav"
        _cut(sources[url], part, start_ms, end_ms, loudnorm=True)
        chunks[-1] += [part, gap]
        chunk_seconds += q.seconds + _GAP_SECONDS
        total += q.seconds
    files: list[Path] = []
    for c, parts in enumerate(chunks):
        listfile = workdir / f"list_{c}.txt"
        listfile.write_text("".join(f"file '{p}'\n" for p in parts[:-1]), encoding="utf-8")
        out = workdir / f"pvc_{c + 1:02d}.mp3"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
             "-c:a", "libmp3lame", "-b:a", "192k", str(out)],
            check=True, capture_output=True,
        )
        files.append(out)
    for src in sources.values():
        src.unlink(missing_ok=True)
    return files, total


def create(request: PvcCreateRequest, *, settings: Settings) -> str:
    return pvc_client(settings).create(
        request.name, language=request.language, description="TalkTo persona voice"
    )


def add_samples(request: PvcSamplesRequest, *, settings: Settings) -> PvcSamplesResponse:
    workdir = Path(tempfile.mkdtemp(prefix="pvc_"))
    try:
        files, total = build_pvc_files(request, settings=settings, workdir=workdir)
        pvc_client(settings).add_samples(request.voice_id, files)
        return PvcSamplesResponse(uploaded_seconds=int(total), files=len(files))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def request_verification(request: PvcVerificationRequest, *, settings: Settings) -> None:
    documents = []
    workdir = Path(tempfile.mkdtemp(prefix="pvc_docs_"))
    try:
        for doc in request.documents:
            path = download_audio(
                str(doc.url),
                timeout_seconds=settings.audio_download_timeout_seconds,
                max_bytes=25 * 1024 * 1024,
                temp_dir=workdir,
            )
            name = doc.filename
            ctype = "application/pdf" if name.lower().endswith(".pdf") else "image/" + (
                name.rsplit(".", 1)[-1].lower().replace("jpg", "jpeg")
            )
            documents.append((name, path.read_bytes(), ctype))
        pvc_client(settings).request_verification(request.voice_id, documents, request.note)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def status(voice_id: str, *, settings: Settings) -> PvcStatusResponse:
    return PvcStatusResponse(**pvc_client(settings).status(voice_id))


def train(voice_id: str, *, settings: Settings) -> str:
    return pvc_client(settings).train(voice_id)


def delete(voice_id: str, *, settings: Settings) -> None:
    pvc_client(settings).delete(voice_id)
