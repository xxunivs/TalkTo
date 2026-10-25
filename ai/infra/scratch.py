"""AI 서버의 임시 파일을 한 폴더(<임시 폴더>/talkto-ai)에 모으고, 서버를 켤 때 비운다.

내려받은 녹음, 풀어 놓은 PCM(<녹음>.f32), 목소리 재료 작업 폴더(voice_clone_*)는 파이썬
finally 에서 지운다. 그런데 메모리가 넘쳐 커널이 프로세스를 강제로 끝내면(OOM, 2026-10-18)
finally 가 돌지 않는다. 도커는 같은 컨테이너를 다시 띄우므로(--restart unless-stopped) 가족 녹음이
다음 배포까지 디스크에 남았다(대화 상대 지우기·보관 기간 약속 밖). 그래서 모든 임시 파일을 이
폴더에 두고, 서버가 뜰 때 지난 프로세스가 남긴 것을 지운다.

일꾼이 하나일 때만 지운다(WEB_CONCURRENCY, 기본 1). 여럿이면 다른 일꾼이 쓰는 중인 파일을 지울 수 있다.
"""

import logging
import os
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

SCRATCH_NAME = "talkto-ai"


def scratch_root(settings) -> Path:
    """임시 파일 폴더 경로(만들지 않는다). TEMP_DIR 이 있으면 그 아래, 없으면 시스템 임시 폴더 아래."""
    base = getattr(settings, "temp_dir", None)
    if not isinstance(base, (str, os.PathLike)) or not str(base).strip():
        base = tempfile.gettempdir()
    return Path(base) / SCRATCH_NAME


def scratch_dir(settings) -> Path:
    """임시 파일 폴더(없으면 만든다). 내려받기·작업 폴더는 늘 여기에 둔다."""
    root = scratch_root(settings)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def _workers() -> int:
    try:
        return int(os.environ.get("WEB_CONCURRENCY") or "1")
    except ValueError:
        return 1


def reset_scratch_dir(settings) -> int:
    """지난 프로세스가 남긴 임시 파일을 지우고 지운 개수를 돌려준다(파일 이름은 남기지 않는다)."""
    root = scratch_root(settings)
    if _workers() > 1:
        logger.warning("scratch cleanup skipped: more than one worker (WEB_CONCURRENCY)")
        return 0
    if root.is_symlink():  # 남이 만든 바로가기를 따라가 다른 곳을 지우지 않는다
        logger.error("scratch cleanup skipped: %s is a symlink", SCRATCH_NAME)
        return 0
    removed = 0
    if root.is_dir():
        for entry in root.iterdir():
            try:
                if entry.is_dir() and not entry.is_symlink():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
                removed += 1
            except OSError as error:
                logger.warning(
                    "scratch cleanup could not remove an entry: %s", type(error).__name__
                )
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if removed:
        logger.warning("scratch cleanup removed %d leftover temp entries", removed)
    return removed
