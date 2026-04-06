"""
통화 중 파이프라인: TTS → 재생 → STT → LLM → 다음 질문.

직전 답변만 LLM 컨텍스트로 사용 (누적 컨텍스트 X).
"""
from __future__ import annotations

import os
import logging
import httpx

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


# Note: run_call_loop() removed — conversation loop is now in userbot/main.py
