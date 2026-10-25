"""ElevenLabs 계정에 남는 기록 지우기(받아쓰기 기록·대답 읽기 기록).

TalkTo 는 ElevenLabs 의 '기록 남기지 않기(무보관)'를 쓸 수 없다(기업 요금제만). 그래서 쓸 때마다 남는
기록을 다 쓴 뒤 바로 지운다.
- 받아쓰기: 응답의 transcription_id → DELETE /v1/speech-to-text/transcripts/{id}
  (보낸 녹음과 받아쓴 글이 함께 지워진다)
- 대답 읽기(예전 ElevenLabs 목소리만): 응답 머리의 history-item-id → DELETE /v1/history/{id}.
  머리에 없으면 request-id 로 최근 기록에서 찾는다.

지우기는 대답을 늦추지 않게 따로 돈다(백그라운드 스레드). 실패하면 몇 번 다시 하고, 그래도 안 되면 id 만
로그에 남긴다(글·녹음 내용은 남기지 않는다). 지우기가 실패해도 받아쓰기·대답은 그대로 돌려준다.
예전에 쌓인 대답 읽기 기록은 tools/purge_elevenlabs_history.py 로 지운다.
"""

import logging
import threading
import time
from collections.abc import Callable

import httpx

logger = logging.getLogger(__name__)

ELEVENLABS_BASE = "https://api.elevenlabs.io"
TIMEOUT_SECONDS = 20.0
# 다시 해 보기 전에 기다리는 초(첫 시도는 바로)
RETRY_DELAYS = (0.0, 1.0, 4.0)


def _headers(api_key: str) -> dict[str, str]:
    return {"xi-api-key": api_key}


def _delete(api_key: str, path: str) -> bool:
    """지웠거나 이미 없으면 True. 404 는 이미 지운 것으로 본다."""
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
    """대답 읽기 기록 id 를 request-id 로 찾는다(가장 최근 몇 개만 본다)."""
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
    """계정의 대답 읽기 기록 전부(최근 것부터). 지우기 도구가 쓴다."""
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
    """대답을 늦추지 않게 따로 돌린다. 시험에서는 바로 부르도록 바꿔 끼운다."""
    threading.Thread(target=action, daemon=True, name="elevenlabs-cleanup").start()


def forget_transcript(api_key: str, body: dict) -> None:
    """받아쓰기 응답을 받은 뒤 부른다. id 가 없으면 지울 수 없어 로그만 남긴다."""
    transcription_id = body.get("transcription_id")
    if not isinstance(transcription_id, str) or not transcription_id:
        logger.warning("scribe response had no transcription_id; transcript stays in the account")
        return
    in_background(lambda: delete_transcript(api_key, transcription_id))


def forget_speech(api_key: str, *, voice_id: str, headers: object) -> None:
    """예전 ElevenLabs 목소리로 대답을 읽은 뒤 부른다(응답 머리에서 기록 id 를 찾는다)."""
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
                logger.warning(
                    "elevenlabs history item not found for request_id=%s", request_id
                )

        in_background(lookup_and_delete)
        return
    logger.warning("elevenlabs tts response had no history id; history item stays in the account")
