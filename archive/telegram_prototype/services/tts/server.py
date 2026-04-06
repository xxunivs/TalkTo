"""
Kokoro TTS HTTP server (Korean).

POST /synthesize {"text": "...", "voice": "ko_kr_default"}
  -> audio/wav (16kHz mono, 16-bit PCM)
"""
import io
import logging

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

logger = logging.getLogger(__name__)

app = FastAPI(title="talkto-tts")

# Load Kokoro pipeline once at startup; allow health check even if import fails.
_pipeline = None
_load_error: str | None = None

try:
    from kokoro import KPipeline
    _pipeline = KPipeline(lang_code="k")
    logger.info("Kokoro TTS pipeline loaded (lang_code='k')")
except Exception as exc:
    _load_error = str(exc)
    logger.error("Failed to load Kokoro TTS pipeline: %s", exc)

KOKORO_SAMPLE_RATE = 24_000
TARGET_SAMPLE_RATE = 16_000

# Map friendly voice names to Kokoro voice IDs.
_VOICE_MAP: dict[str, str] = {
    "ko_kr_default": "af_heart",
    "af_heart": "af_heart",
}
_DEFAULT_VOICE = "af_heart"


class SynthesizeReq(BaseModel):
    text: str
    voice: str = "ko_kr_default"


def _resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Downsample audio array using linear interpolation (CPU-only, no scipy)."""
    if orig_sr == target_sr:
        return audio
    ratio = target_sr / orig_sr
    n_out = int(len(audio) * ratio)
    x_orig = np.linspace(0, len(audio) - 1, len(audio))
    x_new = np.linspace(0, len(audio) - 1, n_out)
    return np.interp(x_new, x_orig, audio).astype(np.float32)


def _to_wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    """Encode float32 audio as 16-bit PCM WAV bytes."""
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, subtype="PCM_16", format="WAV")
    buf.seek(0)
    return buf.read()


@app.post("/synthesize")
def synthesize(req: SynthesizeReq) -> Response:
    if _pipeline is None:
        raise HTTPException(
            status_code=503,
            detail=f"TTS pipeline not available: {_load_error}",
        )

    voice = _VOICE_MAP.get(req.voice, _DEFAULT_VOICE)

    chunks: list[np.ndarray] = []
    try:
        for _gs, _ps, audio in _pipeline(req.text, voice=voice):
            if audio is not None and len(audio) > 0:
                chunks.append(np.asarray(audio, dtype=np.float32))
    except Exception as exc:
        logger.exception("Kokoro inference failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not chunks:
        raise HTTPException(status_code=500, detail="TTS produced no audio output")

    combined = np.concatenate(chunks)
    resampled = _resample(combined, KOKORO_SAMPLE_RATE, TARGET_SAMPLE_RATE)
    wav_bytes = _to_wav_bytes(resampled, TARGET_SAMPLE_RATE)

    return Response(content=wav_bytes, media_type="audio/wav")


@app.get("/health")
def health() -> dict:
    return {
        "ok": _pipeline is not None,
        "model": "kokoro",
        "error": _load_error,
    }
