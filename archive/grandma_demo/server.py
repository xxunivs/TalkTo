import os
import sys
import time
import tempfile
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.response.generator import generate_response, local_short_term_enabled
from core.memory.embeddings import search_memories, get_embedding
from core.memory.extract import extract_and_save
from core.memory.importance import evaluate_importance
from core.voice.stt import transcribe
from core.voice.tts import synthesize

app = FastAPI(title="TalkTo Persona AI", version="0.1.0")

@app.middleware("http")
async def require_ai_server_token(request: Request, call_next):
    expected_token = os.getenv("AI_SERVER_TOKEN")
    if expected_token and request.url.path.startswith("/ai/"):
        actual_token = request.headers.get("x-ai-server-token")
        if actual_token != expected_token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid AI server token."},
            )

    return await call_next(request)

class MemoryItem(BaseModel):
    id: str = ""
    title: str = ""
    content: str = ""

class ChatRequest(BaseModel):
    message: str
    history: list = Field(default_factory=list)
    memories: Optional[list[MemoryItem]] = None

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

@app.post("/ai/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    start = time.time()

    provided_memories = req.memories
    if provided_memories is not None:

        memory_ids = [m.id for m in provided_memories]
        memory_text = "\n".join([f"- {m.title}: {m.content}" for m in provided_memories])
    else:

        found = search_memories(req.message, top_k=5)
        memory_ids = [m["id"] for m in found]
        memory_text = "\n".join([f"- {m['title']}: {m['content']}" for m in found])

    reply = generate_response(req.message, req.history, memory_text=memory_text)

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
    embedding = get_embedding(req.text)
    return EmbedResponse(embedding=embedding)

@app.post("/ai/memory/extract", response_model=MemoryExtractResponse)
def extract_memory(req: MemoryExtractRequest):
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
    return {"status": "ok"}
