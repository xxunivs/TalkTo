"""
AI 서버 — 백엔드가 HTTP로 호출하는 진입점.

실행: uvicorn server:app --port 8000
문서: http://localhost:8000/docs
"""
import os
import sys
import time
import tempfile
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from llm.core import generate_response, local_short_term_enabled
from memory.embeddings import search_memories, get_embedding
from memory.extract import extract_and_save
from memory.importance import evaluate_importance
from voice.stt import transcribe
from voice.tts import synthesize

app = FastAPI(title="TalkTo Persona AI", version="0.1.0")


@app.middleware("http")
async def require_ai_server_token(request: Request, call_next):
    """
    AI 서버 public URL 보호용 shared secret 검사.

    AI_SERVER_TOKEN이 설정된 환경에서만 /ai/* 요청에
    X-AI-Server-Token 헤더를 요구한다. /health는 공개로 둔다.
    """
    expected_token = os.getenv("AI_SERVER_TOKEN")
    if expected_token and request.url.path.startswith("/ai/"):
        actual_token = request.headers.get("x-ai-server-token")
        if actual_token != expected_token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid AI server token."},
            )

    return await call_next(request)


# === 요청/응답 형식 ===

class MemoryItem(BaseModel):
    """백엔드가 보내주는 기억 한 개"""
    id: str = ""
    title: str = ""
    content: str = ""

class ChatRequest(BaseModel):
    message: str
    history: list = Field(default_factory=list)
    memories: Optional[list[MemoryItem]] = None  # 백엔드가 직접 검색한 기억

class ChatResponse(BaseModel):
    content: str
    retrieved_memory_ids: list
    latency_ms: int

class EmbedRequest(BaseModel):
    text: str

class EmbedResponse(BaseModel):
    embedding: list

class MemoryExtractRequest(BaseModel):
    history: list = Field(default_factory=list)
    user_message: str
    assistant_message: str

class MemoryExtractResponse(BaseModel):
    saved: bool
    importance: int
    memory_type: str = "SHORT_TERM"
    category: str = ""
    summary: str = ""
    reason: str = ""

class STTResponse(BaseModel):
    success: bool
    text: str = ""
    error: str = ""

class TTSRequest(BaseModel):
    text: str


# === 엔드포인트 ===

@app.post("/ai/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    """
    텍스트 메시지 → 할머니 답변 생성.

    memories가 있으면 → 백엔드가 검색한 기억 사용
    memories가 없으면 → 내부 JSON에서 검색 (기존 방식)
    """
    start = time.time()

    # 1. 기억 검색
    provided_memories = req.memories
    if provided_memories is not None:
        # 백엔드가 직접 검색해서 보내준 기억 사용.
        # 빈 배열은 "검색 결과 없음"으로 보고 내부 JSON fallback을 하지 않는다.
        memory_ids = [m.id for m in provided_memories]
        memory_text = "\n".join([f"- {m.title}: {m.content}" for m in provided_memories])
    else:
        # 내부 JSON에서 검색 (로컬 테스트용)
        found = search_memories(req.message, top_k=5)
        memory_ids = [m["id"] for m in found]
        memory_text = "\n".join([f"- {m['title']}: {m['content']}" for m in found])

    # 2. 답변 생성 (기억을 프롬프트에 주입)
    reply = generate_response(req.message, req.history, memory_text=memory_text)

    # 3. 로컬 개발에서만 단기기억 추출 (운영은 백엔드 DB가 기억 저장소의 기준)
    if local_short_term_enabled():
        extract_and_save(req.message, reply)

    latency_ms = int((time.time() - start) * 1000)

    return ChatResponse(
        content=reply,
        retrieved_memory_ids=memory_ids,
        latency_ms=latency_ms,
    )


@app.post("/ai/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest):
    """
    텍스트 → 임베딩 벡터 반환.
    백엔드가 기억 생성/수정 시 호출해서 벡터를 DB에 저장.
    """
    embedding = get_embedding(req.text)
    return EmbedResponse(embedding=embedding)


@app.post("/ai/memory/extract", response_model=MemoryExtractResponse)
def extract_memory(req: MemoryExtractRequest):
    """
    최근 대화 맥락 + 현재 대화 한 턴 → 백엔드 DB에 저장할 단기기억 후보 판단.

    이 엔드포인트는 로컬 파일에 저장하지 않고 후보만 반환한다.
    실제 저장 여부와 저장 위치는 백엔드가 결정한다.
    """
    result = evaluate_importance(
        req.user_message,
        req.assistant_message,
        history=req.history,
    )
    importance = int(result.get("importance", 0) or 0)

    return MemoryExtractResponse(
        saved=importance >= 6,
        importance=importance,
        memory_type="SHORT_TERM",
        category=result.get("category", "") or "",
        summary=result.get("summary", "") or "",
        reason=result.get("reason", "") or "",
    )


@app.post("/ai/stt", response_model=STTResponse)
async def stt(audio_file: UploadFile = File(...)):
    """
    음성 파일 → 텍스트 변환 (Whisper API).
    """
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
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
        output_path = tmp.name

    try:
        result = synthesize(req.text, output_path)

        if not result["success"]:
            return {"success": False, "error": result.get("error", "")}

        with open(output_path, "rb") as f:
            audio_bytes = f.read()
    finally:
        if os.path.exists(output_path):
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
