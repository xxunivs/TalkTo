"""
통화 중 파이프라인: TTS → 재생 → STT → LLM → 다음 질문.

직전 답변만 LLM 컨텍스트로 사용 (누적 컨텍스트 X).
"""
from __future__ import annotations

import os
import time
import logging
import httpx

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator.call_session import CallSession
    from userbot.call_controller import CallController

log = logging.getLogger("talkto.pipeline")

TTS_URL = os.environ.get("TTS_URL", "http://tts:8001")
STT_URL = os.environ.get("STT_URL", "http://stt:8002")
LLM_URL = os.environ.get("LLM_URL", "http://llm:8003")


async def speak(text: str) -> bytes:
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{TTS_URL}/synthesize", json={"text": text}, timeout=30)
        r.raise_for_status()
        return r.content


async def transcribe(wav_bytes: bytes) -> str:
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{STT_URL}/transcribe",
            files={"audio": ("a.wav", wav_bytes, "audio/wav")},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["text"]


async def next_question(last_answer: str) -> str:
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{LLM_URL}/tail_question",
            json={"last_answer": last_answer},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["question"]


async def run_call_loop(
    session: CallSession,
    controller: CallController,
    opening_question: str,
    max_duration: int,
) -> CallSession:
    """
    Main in-call conversation loop.

    Cycles: TTS -> send audio -> record response -> STT -> generate next question.
    Exits when max_duration exceeded or 2 consecutive empty transcriptions.
    """
    question = opening_question
    start_time = time.monotonic()
    empty_count = 0

    while True:
        elapsed = time.monotonic() - start_time
        remaining = max_duration - elapsed
        if remaining <= 0:
            log.info("Max duration reached (%.0fs), ending loop", elapsed)
            break

        # Step 1: TTS - synthesize question
        log.info("Speaking: %s", question[:80])
        try:
            wav_bytes = await speak(question)
        except Exception:
            log.exception("TTS failed for question")
            break

        session.add_turn("assistant", question)

        # Step 2: Send audio into call
        try:
            await controller.send_audio(session.call_id, wav_bytes)
        except Exception:
            log.exception("Failed to send audio")
            break

        # Step 3: Record response (cap at remaining time, max 30s)
        listen_seconds = min(30, int(remaining))
        try:
            response_wav = await controller.recv_audio(session.call_id, listen_seconds)
        except Exception:
            log.exception("Failed to record audio")
            break

        # Step 4: Transcribe response
        try:
            answer_text = await transcribe(response_wav)
        except Exception:
            log.exception("STT failed")
            break

        log.info("Transcribed: %s", answer_text[:120] if answer_text else "(empty)")

        # Step 5: Add answer to transcript
        session.add_turn("user", answer_text)

        # Check for silence (empty transcription 2x in a row)
        if not answer_text.strip():
            empty_count += 1
            log.info("Empty transcription %d/2", empty_count)
            if empty_count >= 2:
                log.info("Two consecutive empty responses, ending loop")
                break
        else:
            empty_count = 0

        # Step 6: Check remaining time
        elapsed = time.monotonic() - start_time
        if elapsed >= max_duration:
            log.info("Max duration reached after transcription, ending loop")
            break

        # Step 7: Generate next question
        try:
            question = await next_question(answer_text)
        except Exception:
            log.exception("LLM follow-up generation failed")
            break

    log.info("Call loop finished: %d turns in %.0fs", len(session.transcript), time.monotonic() - start_time)
    return session
