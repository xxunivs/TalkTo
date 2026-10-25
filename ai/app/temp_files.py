"""임시 파일 폴더(<임시 폴더>/talkto-ai) 관리 (OOM 강제 종료 시 finally 미실행 대비: 시작 시 비움, 일꾼 1개일 때만)"""

import logging
import os
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

SCRATCH_NAME = "talkto-ai"


def scratch_root(settings) -> Path:
    """임시 파일 폴더 경로 (생성 없음, TEMP_DIR 또는 시스템 임시 폴더 아래)"""
    base = getattr(settings, "temp_dir", None)
    if not isinstance(base, (str, os.PathLike)) or not str(base).strip():
        base = tempfile.gettempdir()
    return Path(base) / SCRATCH_NAME


def scratch_dir(settings) -> Path:
    """임시 파일 폴더 (없으면 생성, 내려받기와 작업 폴더 전용)"""
    root = scratch_root(settings)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def _workers() -> int:
    try:
        return int(os.environ.get("WEB_CONCURRENCY") or "1")
    except ValueError:
        return 1


def reset_scratch_dir(settings) -> int:
    """지난 프로세스의 임시 파일 삭제 -> 삭제 개수 (파일 이름은 로그에 남기지 않음)"""
    root = scratch_root(settings)
    if _workers() > 1:
        logger.warning("scratch cleanup skipped: more than one worker (WEB_CONCURRENCY)")
        return 0
    if root.is_symlink():  # 심볼릭 링크를 따라가 다른 경로를 지우는 것 방지
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
