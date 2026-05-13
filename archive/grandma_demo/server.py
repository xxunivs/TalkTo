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


@app.post("/ai/voice-chat")
async def voice_chat(
    audio_file: UploadFile = File(...),
    history: str = "[]",
):
    """
    음성 메시지 → STT → 할머니 답변 생성 → TTS 한 번에 처리.
    백엔드가 한 번만 호출하면 됨.

    매개변수:
        audio_file: 사용자 음성 파일
        history: 이전 대화 내역 JSON 문자열 (multipart라서 문자열로 받음)

    반환값:
        JSON {stt_text, content, retrieved_memory_ids, latency_ms, tts_success}
        + TTS 성공 시 audio_bytes 포함
    """
    import json as json_module
    start = time.time()

    # 1. STT: 음성 → 텍스트
    suffix = os.path.splitext(audio_file.filename or "audio.wav")[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await audio_file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        stt_result = transcribe(tmp_path)
    finally:
        os.unlink(tmp_path)

    if not stt_result["success"]:
        return {"success": False, "error": stt_result.get("error", ""), "step": "stt"}

    stt_text = stt_result["text"]

    # 2. 답변 생성: RAG + GPT
    parsed_history = json_module.loads(history) if history else []
    memories = search_memories(stt_text, top_k=5)
    memory_ids = [m["id"] for m in memories]
    reply = generate_response(stt_text, parsed_history)
    extract_and_save(stt_text, reply)

    # 3. TTS: 텍스트 → 할머니 목소리
    output_path = f"/tmp/tts_{int(time.time())}.mp3"
    tts_result = synthesize(reply, output_path)

    latency_ms = int((time.time() - start) * 1000)

    if tts_result["success"]:
        with open(output_path, "rb") as f:
            import base64
            audio_base64 = base64.b64encode(f.read()).decode()
        os.unlink(output_path)
    else:
        audio_base64 = None

    return {
        "success": True,
        "stt_text": stt_text,
        "content": reply,
        "retrieved_memory_ids": memory_ids,
        "latency_ms": latency_ms,
        "tts_success": tts_result["success"],
        "tts_audio_base64": audio_base64,
        "tts_error": tts_result.get("error", "") if not tts_result["success"] else "",
    }


@app.get("/health")
def health():
    """헬스 체크"""
    return {"status": "ok"}
