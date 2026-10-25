"""presigned URL -> 녹음 임시 파일 (오류 메시지, 로그에 URL 금지: 쿼리에 자격 증명 포함)"""

import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from infra.error import AudioDownloadFailedError, AudioUrlExpiredError

_MIME_SUFFIXES = {
    "audio/m4a": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/flac": ".flac",
}


def _suffix_for(url: str, mime_type: str | None) -> str:
    if mime_type:
        normalized = mime_type.split(";")[0].strip().lower()
        if normalized in _MIME_SUFFIXES:
            return _MIME_SUFFIXES[normalized]
    suffix = Path(urlparse(url).path).suffix
    return suffix if suffix else ".m4a"


def download_audio(
    url: str,
    *,
    mime_type: str | None = None,
    timeout_seconds: float = 120.0,
    max_bytes: int | None = None,
    temp_dir: Path | None = None,
) -> Path:
    target = tempfile.NamedTemporaryFile(
        delete=False, suffix=_suffix_for(url, mime_type), dir=temp_dir
    )
    path = Path(target.name)
    try:
        try:
            written = 0
            with httpx.stream(
                "GET", url, timeout=timeout_seconds, follow_redirects=True
            ) as response:
                if response.status_code in (400, 401, 403):
                    raise AudioUrlExpiredError()
                if response.status_code != 200:
                    raise AudioDownloadFailedError(f"storage returned {response.status_code}")
                for chunk in response.iter_bytes():
                    written += len(chunk)
                    if max_bytes is not None and written > max_bytes:
                        raise AudioDownloadFailedError(f"audio exceeds the {max_bytes} byte limit")
                    target.write(chunk)
            if written == 0:
                raise AudioDownloadFailedError("storage returned an empty body")
        except httpx.HTTPError as error:
            raise AudioDownloadFailedError(type(error).__name__) from error
        target.close()  # 마지막 버퍼 기록 중 디스크 부족도 아래에서 삭제
    except BaseException:
        # 모든 실패(디스크, 중단 포함)에서 받다 만 임시 파일 삭제
        target.close()
        path.unlink(missing_ok=True)
        raise
    return path
