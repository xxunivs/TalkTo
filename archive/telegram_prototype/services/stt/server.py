"""
Faster-Whisper STT HTTP 서버 (small, CPU, int8).

POST /transcribe (multipart/form-data: audio=@file.wav)
  -> {"text": "...", "language": "ko", "duration": 12.3}
"""
import os
import tempfile
from fastapi import FastAPI, UploadFile, File
from faster_whisper import WhisperModel

MODEL_NAME = os.environ.get("WHISPER_MODEL", "small")
DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
COMPUTE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")

app = FastAPI(title="talkto-stt")
model = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE)


@app.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)) -> dict:
    raw = await audio.read()
    if not raw:
        return {"text": "", "language": "ko", "duration": 0.0}

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name

    try:
        segments, info = model.transcribe(tmp_path, language="ko")
        texts = []
        duration = 0.0
        for segment in segments:
            texts.append(segment.text.strip())
            duration = max(duration, segment.end)
        text = " ".join(t for t in texts if t)
    finally:
        os.unlink(tmp_path)

    return {"text": text, "language": "ko", "duration": duration}


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model": MODEL_NAME}
