"""ffmpeg 로 하는 오디오 처리. 영상에서 소리만 뽑고, 목소리 미리듣기 클립을 만든다.

녹음 원본(영상 포함)은 소리를 뽑은 뒤 지운다. 뒤 단계(전사·화자 묶기·목소리 복제)는 모두
여기서 만든 mono 44.1kHz AAC(m4a) 하나만 본다. 영상은 크고, 얼굴이 담긴 자료라 오래 두지 않는다.

큰 녹음을 메모리에 통째로 올리지 않는다(파일 경로로 다룬다: prepare·inspect·phone_band·
cut_preview). 녹음 길이는 max_recording_minutes 까지만 받는다: 파일 크기만 보면 압축이 센
파일이 몇십 시간짜리가 되어, 바꾼 소리가 메모리·디스크·전사 비용을 끝없이 키운다(2026-10 AI
서버가 긴 녹음으로 메모리가 넘쳐 꺼진 일과 같은 종류). ffmpeg 일은 서버 전체에서 동시에
SLOTS 개까지만 돈다(slot()).
"""

import asyncio
import logging
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# 올릴 수 있는 확장자. 영상은 소리만 뽑는다.
AUDIO_SUFFIXES = {".wav", ".m4a", ".mp3", ".webm", ".ogg", ".flac", ".aac", ".opus", ".amr", ".3gp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".wmv", ".mpeg", ".mpg"}
UPLOAD_SUFFIXES = AUDIO_SUFFIXES | VIDEO_SUFFIXES
# 소리 파일을 저장할 때 붙이는 Content-Type. 브라우저가 보낸 값은 믿지 않는다.
AUDIO_CONTENT_TYPES = {
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".opus": "audio/ogg",
    ".amr": "audio/amr",
    ".3gp": "audio/3gpp",
}

PREVIEW_SECONDS = 15
PREVIEW_MIN_SEGMENT_MS = 1500
# 통화 녹음 판별: 말소리가 가장 센 1~3kHz 에 비해 4.5~7kHz 가 이만큼(dB) 이상 작으면 통화로 본다.
# 전화망은 300~3400Hz 만 남긴다. 할머니 통화(wav·m4a·영상)는 -41~-42dB, 일반 녹음은 -8~-24dB
# 였다(2026-10 측정). 필터를 여러 번 겹쳐 가파르게 자른다.
PHONE_BAND_DB = -33.0
_HIGH_BAND = "highpass=f=4500," * 4 + "lowpass=f=7000," * 2
_SPEECH_BAND = "highpass=f=1000," * 2 + "lowpass=f=3000," * 2
# 통화 녹음인지는 앞 10분만 듣고 정한다(몇 시간짜리를 처음부터 끝까지 두 번 걸러 듣지 않게)
PHONE_BAND_SECONDS = 600
# 바꾼 m4a 의 비트레이트. 목소리 복제 재료라 낮추지 않는다.
AUDIO_BITRATE_KBPS = 128

# ffmpeg 한 번에 기다리는 시간(초). 몇 시간짜리 녹음도 이 안에 끝난다. 넘으면 멈춘다.
FFMPEG_TIMEOUT_SECONDS = 900
# 입력마다 붙인다. 파일 안에 적힌 주소(http·concat 목록 등)를 따라가 바깥이나 다른 파일을 열지 않게.
INPUT_WHITELIST = ["-protocol_whitelist", "file,pipe"]
# 서버 전체에서 동시에 도는 ffmpeg 일(녹음 바꾸기·미리듣기·통화 판별·목소리 메시지 길이 재기).
# 서버는 2 vCPU 를 AI 서버와 나눠 쓴다.
SLOTS = 2
_slot: tuple[asyncio.AbstractEventLoop, asyncio.Semaphore] | None = None


class AudioError(ValueError):
    """사용자에게 그대로 보여줄 오디오 문제."""


def slot() -> asyncio.Semaphore:
    """ffmpeg 일 자리. `async with audio.slot():` 안에서 파일을 받고·바꾸고·올린다.

    이벤트 루프마다 하나를 둔다(시험은 시험마다 루프가 새로 뜬다). 다시 들어가지 않는다:
    자리를 잡은 채 아래의 async 함수(extract_audio 등)를 부르지 않는다.
    """
    global _slot
    loop = asyncio.get_running_loop()
    if _slot is None or _slot[0] is not loop:
        _slot = (loop, asyncio.Semaphore(SLOTS))
    return _slot[1]


def max_recording_seconds() -> int:
    return max(60, get_settings().max_recording_minutes * 60)


def _limit_text(seconds: int) -> str:
    minutes = seconds // 60
    if minutes % 60 == 0:
        return f"{minutes // 60}시간"
    return f"{minutes}분"


def too_long_message() -> str:
    limit = _limit_text(max_recording_seconds())
    return f"녹음이 너무 길어요. 녹음 하나는 {limit}까지 올릴 수 있어요. 나눠서 올려 주세요."


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        logger.error("ffmpeg/ffprobe not found on PATH — recordings cannot be processed")
        raise AudioError("지금은 녹음을 살펴볼 수 없어요. 잠시 뒤 다시 해 주세요.")


def _run(args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            args, capture_output=True, check=False, timeout=FFMPEG_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired as error:
        logger.warning("%s timed out after %ss", args[0], FFMPEG_TIMEOUT_SECONDS)
        raise AudioError(
            "녹음을 살펴보는 데 너무 오래 걸려 멈췄어요. 파일을 나눠서 다시 올려 주세요."
        ) from error


def _duration_ms(path: Path) -> int:
    """파일에 적힌 길이(ms). 적혀 있지 않으면(브라우저 녹음 webm 등) 0."""
    out = (
        _run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                *INPUT_WHITELIST,
                "-i",
                str(path),
            ]
        )
        .stdout.decode()
        .strip()
    )
    try:
        return int(float(out) * 1000)
    except ValueError:
        return 0


def _extract_file(src: Path, dst: Path) -> int:
    """음성·영상 파일 → mono 44.1kHz AAC(dst). 길이(ms)를 돌려준다. 너무 길면 AudioError.

    파일에 적힌 길이는 비었거나 틀릴 수 있어(만든 사람이 마음대로 적는다) 먼저 보고 거른 뒤,
    바꿀 때도 상한(-t)까지만 바꾸고 바꾼 소리의 길이로 다시 본다.
    """
    _require_ffmpeg()
    limit = max_recording_seconds()
    if _duration_ms(src) > limit * 1000:
        raise AudioError(too_long_message())
    result = _run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            *INPUT_WHITELIST,
            "-i",
            str(src),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:a",
            "aac",
            "-b:a",
            f"{AUDIO_BITRATE_KBPS}k",
            "-t",
            str(limit + 1),
            str(dst),
        ]
    )
    if result.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        raise AudioError(
            "이 파일에서 소리를 찾지 못했어요. 소리가 있는 음성·영상 파일인지 확인해 주세요."
        )
    # 비트레이트로 본 크기의 1.5배를 넘으면(길이를 속인 파일 등) 받지 않는다
    if dst.stat().st_size > (limit + 1) * AUDIO_BITRATE_KBPS * 1000 // 8 * 3 // 2:
        raise AudioError(too_long_message())
    duration = _duration_ms(dst)
    if duration > limit * 1000:
        raise AudioError(too_long_message())
    if duration < 1000:
        raise AudioError("소리가 너무 짧아요. 1초 넘게 소리가 담긴 파일을 올려 주세요.")
    return duration


def _rms_db(path: Path, af: str) -> float:
    result = _run(
        [
            "ffmpeg",
            "-nostdin",
            "-t",
            str(PHONE_BAND_SECONDS),
            *INPUT_WHITELIST,
            "-i",
            str(path),
            "-af",
            f"{af}astats=measure_perchannel=none:measure_overall=RMS_level",
            "-f",
            "null",
            "-",
        ]
    )
    found = re.findall(
        r"RMS level dB:\s*(-?inf|-?\d+(?:\.\d+)?)", result.stderr.decode("utf-8", "ignore")
    )
    if not found:
        raise AudioError("이 녹음을 읽지 못했어요. 다른 파일로 올려 주세요.")
    value = found[-1]
    return float("-inf") if "inf" in value else float(value)


def phone_band(path: Path) -> bool:
    """통화 녹음(전화망을 거쳐 4kHz 위가 잘린 소리)인가. 막는 호출이다(to_thread 로)."""
    _require_ffmpeg()
    speech = _rms_db(path, _SPEECH_BAND)
    high = _rms_db(path, _HIGH_BAND)
    if speech == float("-inf"):
        return False
    return high - speech < PHONE_BAND_DB


def prepare(src: Path, dst: Path) -> tuple[int, bool]:
    """원본(src) → m4a(dst). (길이 ms, 통화 녹음인가). 막는 호출이다(slot 안에서 to_thread 로)."""
    duration = _extract_file(src, dst)
    return duration, phone_band(dst)


def inspect(path: Path) -> tuple[int, bool]:
    """이미 바꿔 둔 m4a 의 (길이 ms, 통화 녹음인가). 막는 호출이다."""
    _require_ffmpeg()
    duration = _duration_ms(path)
    if duration < 1000:
        raise AudioError("소리가 너무 짧아요. 1초 넘게 소리가 담긴 파일을 올려 주세요.")
    return duration, phone_band(path)


_TIME = re.compile(r"time=(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


def voice_seconds(content: bytes, suffix: str, cap: int) -> float | None:
    """목소리 메시지를 cap+1 초까지 풀어 보고 길이(초)를 잰다. 재지 못하면 None.

    브라우저 녹음(webm)은 파일에 길이가 적혀 있지 않아 실제로 풀어서 잰다. 막는 호출이다.
    """
    if shutil.which("ffmpeg") is None:
        return None
    with tempfile.TemporaryDirectory(prefix="talkto_voice_") as tmp:
        src = Path(tmp) / f"voice{suffix}"
        src.write_bytes(content)
        result = _run(
            [
                "ffmpeg",
                "-nostdin",
                *INPUT_WHITELIST,
                "-i",
                str(src),
                "-vn",
                "-t",
                str(cap + 1),
                "-f",
                "null",
                "-",
            ]
        )
    if result.returncode != 0:
        return None
    found = _TIME.findall(result.stderr.decode("utf-8", "ignore"))
    if not found:
        return None
    hours, minutes, seconds = found[-1]
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


Fetch = Callable[[str, Path], None]


def cut_preview(fetch: Fetch, clips: list[tuple[str, int, int]]) -> bytes:
    """여러 녹음의 구간을 이어붙인 짧은 mp3. fetch(녹음 id, 받을 경로)로 녹음을 하나씩 받는다.

    녹음은 한 번에 하나만 디스크에 두고, 자른 뒤 바로 지운다(메모리에 올리지 않는다). 막는
    호출이다(slot 안에서 to_thread 로).
    """
    _require_ffmpeg()
    with tempfile.TemporaryDirectory(prefix="talkto_preview_") as tmp:
        root = Path(tmp)
        order = list(dict.fromkeys(key for key, _, _ in clips))
        parts: dict[int, Path] = {}
        for key in order:
            src = root / "source.m4a"
            fetch(key, src)
            for i, (clip_key, start_ms, end_ms) in enumerate(clips):
                if clip_key != key:
                    continue
                part = root / f"part_{i}.wav"
                result = _run(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-y",
                        "-ss",
                        str(start_ms / 1000),
                        "-to",
                        str(end_ms / 1000),
                        *INPUT_WHITELIST,
                        "-i",
                        str(src),
                        "-ac",
                        "1",
                        "-ar",
                        "22050",
                        "-c:a",
                        "pcm_s16le",
                        str(part),
                    ]
                )
                if result.returncode == 0 and part.exists() and part.stat().st_size > 1000:
                    parts[i] = part
            src.unlink(missing_ok=True)
        if not parts:
            raise AudioError("미리 들을 소리가 없어요.")
        listfile = root / "list.txt"
        listfile.write_text(
            "".join(f"file '{parts[i].as_posix()}'\n" for i in sorted(parts)), encoding="utf-8"
        )
        out = root / "preview.mp3"
        result = _run(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                *INPUT_WHITELIST,
                "-i",
                str(listfile),
                "-c:a",
                "libmp3lame",
                "-b:a",
                "96k",
                str(out),
            ]
        )
        if result.returncode != 0:
            raise AudioError("미리듣기를 만들지 못했어요.")
        return out.read_bytes()


# ── 바이트로 주고받는 예전 모양(작은 파일·시험용). 자리(slot)를 잡고 돈다. ──


def _extract(content: bytes, suffix: str) -> tuple[bytes, int]:
    with tempfile.TemporaryDirectory(prefix="talkto_audio_") as tmp:
        src = Path(tmp) / f"source{suffix}"
        dst = Path(tmp) / "audio.m4a"
        src.write_bytes(content)
        duration = _extract_file(src, dst)
        return dst.read_bytes(), duration


def _phone_band(content: bytes) -> bool:
    with tempfile.TemporaryDirectory(prefix="talkto_band_") as tmp:
        src = Path(tmp) / "audio.m4a"
        src.write_bytes(content)
        return phone_band(src)


async def is_phone_band(content: bytes) -> bool:
    """통화 녹음(전화망을 거쳐 4kHz 위가 잘린 소리)인가."""
    async with slot():
        return await asyncio.to_thread(_phone_band, content)


async def extract_audio(content: bytes, suffix: str) -> tuple[bytes, int]:
    """음성·영상 파일 → (m4a 바이트, 길이 ms)."""
    async with slot():
        return await asyncio.to_thread(_extract, content, suffix)


async def build_preview(sources: dict[str, bytes], clips: list[tuple[str, int, int]]) -> bytes:
    """여러 녹음의 구간을 이어붙인 짧은 mp3. sources = {녹음 id: m4a 바이트}."""

    def fetch(key: str, path: Path) -> None:
        path.write_bytes(sources[key])

    async with slot():
        return await asyncio.to_thread(cut_preview, fetch, clips)
