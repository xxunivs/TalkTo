"""ElevenLabs 받아쓰기, 대답 읽기 기록 즉시 삭제 (무보관은 기업 요금제 전용, 백그라운드 재시도)"""

import logging
import threading
import time
from collections.abc import Callable

import httpx

logger = logging.getLogger(__name__)

ELEVENLABS_BASE = "https://api.elevenlabs.io"
TIMEOUT_SECONDS = 20.0
# 재시도 전 대기 초 (첫 시도는 즉시)
RETRY_DELAYS = (0.0, 1.0, 4.0)


def _headers(api_key: str) -> dict[str, str]:
    return {"xi-api-key": api_key}


def _delete(api_key: str, path: str) -> bool:
    """삭제 성공 여부 (404 는 이미 삭제로 간주)"""
    try:
        response = httpx.delete(
            f"{ELEVENLABS_BASE}{path}", headers=_headers(api_key), timeout=TIMEOUT_SECONDS
        )
    except httpx.HTTPError:
        return False
    return response.status_code < 300 or response.status_code == 404


def _retry(action: Callable[[], bool]) -> bool:
    for delay in RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        if action():
            return True
    return False


def delete_transcript(api_key: str, transcription_id: str) -> bool:
    ok = _retry(lambda: _delete(api_key, f"/v1/speech-to-text/transcripts/{transcription_id}"))
    if ok:
        logger.info("elevenlabs transcript deleted id=%s", transcription_id)
    else:
        logger.warning("elevenlabs transcript NOT deleted id=%s", transcription_id)
    return ok


def delete_history_item(api_key: str, history_item_id: str) -> bool:
    ok = _retry(lambda: _delete(api_key, f"/v1/history/{history_item_id}"))
    if ok:
        logger.info("elevenlabs history item deleted id=%s", history_item_id)
    else:
        logger.warning("elevenlabs history item NOT deleted id=%s", history_item_id)
    return ok


def find_history_item(api_key: str, *, voice_id: str, request_id: str) -> str | None:
    """request-id -> 대답 읽기 기록 id (최근 20개만 조회)"""
    try:
        response = httpx.get(
            f"{ELEVENLABS_BASE}/v1/history",
            headers=_headers(api_key),
            params={"voice_id": voice_id, "page_size": 20},
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    for item in response.json().get("history") or []:
        if item.get("request_id") == request_id and isinstance(item.get("history_item_id"), str):
            return item["history_item_id"]
    return None


def list_history(api_key: str, *, page_size: int = 1000) -> list[dict]:
    """계정의 대답 읽기 기록 전체 (최근순, 삭제 도구용)"""
    items: list[dict] = []
    after: str | None = None
    while True:
        params: dict = {"page_size": page_size}
        if after:
            params["start_after_history_item_id"] = after
        response = httpx.get(
            f"{ELEVENLABS_BASE}/v1/history",
            headers=_headers(api_key),
            params=params,
            timeout=60.0,
        )
        if response.status_code != 200:
            raise RuntimeError(f"ElevenLabs history list error {response.status_code}")
        body = response.json()
        items.extend(body.get("history") or [])
        after = body.get("last_history_item_id")
        if not body.get("has_more") or not after:
            return items


def in_background(action: Callable[[], object]) -> None:
    """백그라운드 스레드 실행 (테스트에서 즉시 실행으로 교체)"""
    threading.Thread(target=action, daemon=True, name="elevenlabs-cleanup").start()


def forget_transcript(api_key: str, body: dict) -> None:
    """받아쓰기 기록 삭제 예약 (transcription_id 없으면 경고 로그만)"""
    transcription_id = body.get("transcription_id")
    if not isinstance(transcription_id, str) or not transcription_id:
        logger.warning("scribe response had no transcription_id; transcript stays in the account")
        return
    in_background(lambda: delete_transcript(api_key, transcription_id))


def forget_speech(api_key: str, *, voice_id: str, headers: object) -> None:
    """예전 ElevenLabs 목소리 대답 읽기 기록 삭제 예약 (응답 헤더의 기록 id, 없으면 request-id 조회)"""
    get = getattr(headers, "get", None)
    history_id = get("history-item-id") if get else None
    request_id = get("request-id") if get else None
    if isinstance(history_id, str) and history_id:
        in_background(lambda: delete_history_item(api_key, history_id))
        return
    if isinstance(request_id, str) and request_id:

        def lookup_and_delete() -> None:
            found = None
            for delay in RETRY_DELAYS:
                if delay:
                    time.sleep(delay)
                found = find_history_item(api_key, voice_id=voice_id, request_id=request_id)
                if found:
                    break
            if found:
                delete_history_item(api_key, found)
            else:
                logger.warning("elevenlabs history item not found for request_id=%s", request_id)

        in_background(lookup_and_delete)
        return
    logger.warning("elevenlabs tts response had no history id; history item stays in the account")
