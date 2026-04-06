"""
Kokoro TTS HTTP server (Korean, ONNX).

POST /synthesize {"text": "...", "voice": "ko_kr_default"}
  -> audio/wav (24kHz mono, 16-bit PCM)
"""
import io
import logging

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

logger = logging.getLogger(__name__)

app = FastAPI(title="talkto-tts")

_kokoro = None
_load_error: str | None = None

try:
    from kokoro_onnx import Kokoro
    _kokoro = Kokoro("/models/kokoro/kokoro-v1.0.onnx", "/models/kokoro/voices-v1.0.bin")
    logger.info("Kokoro ONNX loaded")
except Exception as exc:
    _load_error = str(exc)
    logger.error("Failed to load Kokoro ONNX: %s", exc)

_VOICE_MAP = {
    "ko_kr_default": "af_heart",
}


class SynthesizeReq(BaseModel):
    text: str
    voice: str = "ko_kr_default"


@app.post("/synthesize")
def synthesize(req: SynthesizeReq) -> Response:
    if _kokoro is None:
        raise HTTPException(status_code=503, detail=f"TTS not available: {_load_error}")

    voice = _VOICE_MAP.get(req.voice, req.voice)

    try:
        audio, sample_rate = _kokoro.create(req.text, voice=voice, lang="ko")
    except Exception as exc:
        logger.exception("Kokoro inference failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if audio is None or len(audio) == 0:
        raise HTTPException(status_code=500, detail="TTS produced no audio")

    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, subtype="PCM_16", format="WAV")
    buf.seek(0)

    return Response(content=buf.read(), media_type="audio/wav")


@app.get("/health")
def health() -> dict:
    return {"ok": _kokoro is not None, "model": "kokoro-onnx", "error": _load_error}
