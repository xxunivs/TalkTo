"""말로 답하기: 질문 답·기억 더 채우기·고쳐 쓰기 칸의 🎤 녹음을 글로 옮겨 돌려준다.

돌려준 글은 칸에 들어가고, 사람이 고친 뒤 저장한다(여기서는 아무것도 저장하지 않는다).
녹음은 남기지 않는다: 메모리에서 길이를 재고(재는 동안만 임시 폴더, 바로 지운다) AI 서버로 보낸 뒤
버린다. 저장소(S3)에 올리지 않고, 목소리 만들기에도 쓰지 않는다. AI 서버는 ElevenLabs 로 받아쓰고
그곳의 받아쓰기 기록(녹음·글)을 바로 지운다(AI 레포 elevenlabs_records.forget_transcript).

대화 상대의 주인과 함께 쓰는 가족만 쓸 수 있다(가족은 기억 채우기·고쳐 쓰기에 쓴다). 남은 404.
받아쓰기에 돈이 들어 한 사람은 한 번에 하나만(429), 서버 전체에서 동시에 SLOTS 개까지만(503),
하루에 TRANSCRIPTIONS_DAILY 번까지 받는다. 새 계정을 마구 만들어 돌려 쓰지 못하게 같은 IP 도
하루 TRANSCRIPTIONS_IP 번까지(429), 서버 전체는 최근 24시간 transcription_daily_seconds 초까지(503)
받아쓴다. 길이를 재지 못한 녹음은 보내지 않는다(ffmpeg 가 있을 때).
"""

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse

from app.api.deps import DB, CurrentUser
from app.core import ratelimit
from app.core.config import get_settings
from app.schemas.common import Envelope
from app.services import audio, gate, sharing
from app.services.ai import NoSpeechError, ai_service

router = APIRouter(tags=["Transcriptions"])
logger = logging.getLogger(__name__)

# 한 번에 받는 녹음: 6MB, 3분. 화면은 3분(MAX_SECONDS)에서 녹음을 멈춘다. 화면은 녹음이 시작된
# 뒤부터 세고 멈추는 데도 조금 걸려 3분에서 멈춘 녹음은 늘 180초를 조금 넘는다. 그래서 받는 쪽은
# ACCEPT_SECONDS(185초)까지 받는다(넘는 것만 '3분까지'로 돌려보낸다).
MAX_BYTES = 6 * 1024 * 1024
MAX_SECONDS = 180
ACCEPT_SECONDS = 185
# 돌려주는 글의 상한(3분 말은 보통 1,500자 안팎이다)
MAX_CHARS = 5_000
# 서버 전체에서 동시에 받아쓰는 녹음 수(AI 서버·ffmpeg 를 한 사람이 다 쓰지 않게)
SLOTS = 2

NO_SPEECH = "말씀을 알아듣지 못했어요. 다시 한번 말씀해 주세요."
NO_SPEECH_CODE = "no_speech"
TOO_LONG = "3분까지 말씀하실 수 있어요. 나눠서 말씀해 주세요."
TOO_LARGE = "녹음이 너무 커요. 3분 안으로 나눠서 말씀해 주세요."
EMPTY = "녹음된 소리가 없어요. 다시 한번 말씀해 주세요."
UNREADABLE = "녹음을 확인하지 못했어요. 다시 한번 말씀해 주세요."
BUSY_USER = "앞서 하신 말씀을 아직 글로 옮기고 있어요. 끝난 뒤에 다시 해 주세요."
BUSY = "지금 말씀을 글로 옮기는 일이 많아요. 잠시 뒤 다시 해 주세요."
# 횟수 상한에 걸렸을 때(언제 다시 되는지 알린다. {wait} = '7분' 같은 남은 때)
TOO_MANY_SOON = (
    "말로 답하기를 짧은 시간에 많이 쓰셨어요. {wait} 뒤에 다시 쓸 수 있어요. "
    "그동안은 글로 적어 주세요."
)
TOO_MANY_TODAY = (
    "오늘은 말로 답하기를 많이 쓰셨어요. {wait} 뒤에 다시 쓸 수 있어요. 그동안은 글로 적어 주세요."
)

# 지금 받아쓰는 사람(한 사람에 하나씩)과 받아쓰는 수. 서버 한 대·이벤트 루프 하나라 잠금 없이 센다.
_in_flight: set[str] = set()
_active = 0


def _size(upload: UploadFile) -> int:
    """받아 둔 파일 크기(읽지 않고 잰다)."""
    handle = upload.file
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    handle.seek(0)
    return size


def _no_speech() -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": NO_SPEECH, "code": NO_SPEECH_CODE},
    )


@router.post("/personas/{persona_id}/transcriptions", response_model=Envelope[dict])
async def transcribe_answer(
    persona_id: str,
    request: Request,
    user: CurrentUser,
    db: DB,
    audio_file: Annotated[UploadFile, File(alias="audio")],
):
    """말로 답한 녹음(multipart audio: webm·m4a·wav) → {text}. 녹음은 남기지 않는다."""
    global _active
    # 계정마다 세기 전에 IP 로도 센다(한 곳에서 새 계정을 여럿 만들어 돌려 쓰지 못하게)
    ratelimit.TRANSCRIPTIONS_IP.check(ratelimit.client_ip(request), TOO_MANY_SOON)
    ratelimit.TRANSCRIPTIONS.check(user.id, TOO_MANY_SOON)
    ratelimit.TRANSCRIPTIONS_DAILY.check(user.id, TOO_MANY_TODAY)
    # 주인이나 함께 쓰는 가족만(남은 대화 상대가 있는지도 모르게 404)
    access = await sharing.chat_access(db, persona_id, user)
    # 멈춘 대화 상대는 말로 적기도 받지 않는다(게이트 #1). 그 밖에는 그분 동의 녹음 전에도 연다
    # (SPEC §20 Q9 — 그분 녹음이 아니고 남기지 않는다)
    await gate.require(db, access.persona, "chat", member=access.member)
    # 받아쓰는 동안 연결을 트랜잭션 속에 붙잡아 두지 않는다
    await db.commit()
    user_id = user.id
    if user_id in _in_flight:
        await audio_file.close()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, BUSY_USER)
    # 서버 전체 하루 받아쓰기 길이를 다 썼어도 자리가 찬 것과 같게 503. 길이는 재고 나서 더하므로
    # 동시에 받던 것만큼(SLOTS × ACCEPT_SECONDS)은 넘을 수 있다.
    budget = get_settings().transcription_daily_seconds
    if _active >= SLOTS or ratelimit.TRANSCRIPTION_SECONDS.used_up(budget):
        await audio_file.close()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, BUSY)
    _in_flight.add(user_id)
    _active += 1
    try:
        return await _transcribe(audio_file)
    finally:
        _active -= 1
        _in_flight.discard(user_id)


async def _transcribe(audio_file: UploadFile):
    try:
        size = _size(audio_file)
        if size > MAX_BYTES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, TOO_LARGE)
        if not size:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, EMPTY)
        # 확장자·Content-Type 은 보낸 값을 믿지 않고 아는 소리 형식으로만 본다
        suffix = Path(audio_file.filename or "").suffix.lower()
        if suffix == ".mp4":  # 사파리 녹음(aac)은 .mp4 로 오기도 한다
            suffix = ".m4a"
        if suffix not in audio.AUDIO_SUFFIXES:
            suffix = ".webm"
        content_type = audio.AUDIO_CONTENT_TYPES.get(suffix, "application/octet-stream")
        content = await audio_file.read()
    finally:
        # 받아 둔 임시 파일(큰 본문이면 디스크)은 바로 닫아 지운다
        await audio_file.close()
    try:
        # 길이 재기(ffmpeg)는 서버 전체의 ffmpeg 자리 안에서. ACCEPT_SECONDS+1 초까지만 풀어 본다.
        async with audio.slot():
            seconds = await asyncio.to_thread(
                audio.voice_seconds, content, suffix, ACCEPT_SECONDS
            )
        if seconds is None and shutil.which("ffmpeg"):
            # 풀지 못한 녹음은 길이를 모른다: 3분 상한을 넘을 수 있어 받아쓰지 않는다
            raise HTTPException(status.HTTP_400_BAD_REQUEST, UNREADABLE)
        if seconds is not None and seconds > ACCEPT_SECONDS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, TOO_LONG)
        # 보내기 전에 서버 전체 하루 길이에 더한다(길이를 모르면 받는 상한만큼)
        ratelimit.TRANSCRIPTION_SECONDS.spend(ACCEPT_SECONDS if seconds is None else seconds)
        try:
            text = await ai_service.transcribe_answer(f"answer{suffix}", content, content_type)
        except NoSpeechError:
            return _no_speech()
    finally:
        del content
    text = " ".join((text or "").split())
    if not text:
        return _no_speech()
    return Envelope(data={"text": text[:MAX_CHARS]})
