"""
AI 서버 — 백엔드가 HTTP로 호출하는 진입점.

실행: uvicorn server:app --port 8000
문서: http://localhost:8000/docs
"""
import os
import sys
import time
import tempfile
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from llm.core import generate_response
from memory.embeddings import search_memories
from memory.extract import extract_and_save
from voice.stt import transcribe
from voice.tts import synthesize

app = FastAPI(title="TalkTo Persona AI", version="0.1.0")


# === 요청/응답 형식 ===

class ChatRequest(BaseModel):
    message: str
    history: list = []

class ChatResponse(BaseModel):
    content: str
    retrieved_memory_ids: list
    latency_ms: int

class STTResponse(BaseModel):
    success: bool
    text: str = ""
    error: str = ""

class TTSRequest(BaseModel):
    text: str

class TTSResponse(BaseModel):
    success: bool
    audio_url: str = ""
    error: str = ""


# === 엔드포인트 ===

@app.post("/ai/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    """
    텍스트 메시지 → 할머니 답변 생성.
    기억 검색(RAG) + GPT 호출 + 단기 기억 추출까지 처리.
    """
    start = time.time()

    # 1. 관련 기억 검색
    memories = search_memories(req.message, top_k=5)
    memory_ids = [m["id"] for m in memories]

    # 2. 답변 생성
    reply = generate_response(req.message, req.history)

    # 3. 단기 기억 추출 (중요도 6 이상이면 자동 저장)
    extract_and_save(req.message, reply)

    latency_ms = int((time.time() - start) * 1000)

    return ChatResponse(
        content=reply,
        retrieved_memory_ids=memory_ids,
        latency_ms=latency_ms,
    )


@app.post("/ai/stt", response_model=STTResponse)
async def stt(audio_file: UploadFile = File(...)):
    """
    음성 파일 → 텍스트 변환 (Whisper API).
    """
    # 임시 파일로 저장 후 변환
    suffix = os.path.splitext(audio_file.filename or "audio.wav")[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await audio_file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = transcribe(tmp_path)
        return STTResponse(
            success=result["success"],
            text=result.get("text", ""),
            error=result.get("error", ""),
        )
    finally:
        os.unlink(tmp_path)


@app.post("/ai/tts")
def tts(req: TTSRequest):
    """
    텍스트 → 할머니 목소리 변환 (ElevenLabs API).
    음성 파일(mp3)을 직접 반환. 백엔드가 받아서 저장/signed URL 생성.
    """
    output_path = f"/tmp/tts_{int(time.time())}.mp3"
    result = synthesize(req.text, output_path)

    if not result["success"]:
        return {"success": False, "error": result.get("error", "")}

    with open(output_path, "rb") as f:
        audio_bytes = f.read()
    os.unlink(output_path)

    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={"Content-Disposition": "attachment; filename=tts_output.mp3"},
    )


@app.get("/health")
def health():
    """헬스 체크"""
    return {"status": "ok"}
