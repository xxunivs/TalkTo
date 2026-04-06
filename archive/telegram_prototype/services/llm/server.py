"""
Qwen LLM HTTP 서버 (꼬리질문 + 감정분석).

POST /tail_question {"last_answer": "...", "persona_hint": "..."}
  -> {"question": "..."}

POST /analyze_call {"transcript": [...]}
  -> {"emotion": "...", "topics": [...], "summary": "...", "facts_master": [...], "facts_overlay": [...]}

GET /health
  -> {"ok": True}
"""
import glob
import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="talkto-llm")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
QWEN_MODEL_PATH = os.environ.get("QWEN_MODEL_PATH", "/models/qwen")
QWEN_CONTEXT_SIZE = int(os.environ.get("QWEN_CONTEXT_SIZE", "4096"))
N_THREADS = max(1, (os.cpu_count() or 4) // 2)

PROMPTS_DIR = Path(__file__).parent / "prompts"

# ---------------------------------------------------------------------------
# Module-level state (populated at startup)
# ---------------------------------------------------------------------------
_model = None
_tail_question_template: str = ""
_emotion_analysis_template: str = ""


def _load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    logger.warning("Prompt file not found: %s", path)
    return ""


def _find_gguf(model_dir: str) -> str | None:
    pattern = os.path.join(model_dir, "*.gguf")
    matches = glob.glob(pattern)
    if not matches:
        return None
    return matches[0]


@app.on_event("startup")
def startup() -> None:
    global _model, _tail_question_template, _emotion_analysis_template

    _tail_question_template = _load_prompt("tail_question.txt")
    _emotion_analysis_template = _load_prompt("emotion_analysis.txt")

    gguf_path = _find_gguf(QWEN_MODEL_PATH)
    if gguf_path is None:
        logger.error(
            "No .gguf file found in %s — LLM endpoints will return 503.", QWEN_MODEL_PATH
        )
        return

    try:
        from llama_cpp import Llama  # type: ignore

        logger.info("Loading model from %s (n_ctx=%d, n_threads=%d)", gguf_path, QWEN_CONTEXT_SIZE, N_THREADS)
        _model = Llama(
            model_path=gguf_path,
            n_ctx=QWEN_CONTEXT_SIZE,
            n_threads=N_THREADS,
            n_gpu_layers=0,  # CPU-only
            verbose=False,
        )
        logger.info("Model loaded successfully.")
    except Exception as exc:
        logger.exception("Failed to load model: %s", exc)


def _require_model():
    if _model is None:
        raise HTTPException(status_code=503, detail="LLM model not available")


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------
class TailReq(BaseModel):
    last_answer: str
    persona_hint: str = ""


class AnalyzeReq(BaseModel):
    transcript: list  # [{"role": "user"|"bot", "text": "..."}]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.post("/tail_question")
def tail_question(req: TailReq) -> dict:
    _require_model()

    prompt = _tail_question_template.format(last_answer=req.last_answer)

    response = _model.create_chat_completion(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=64,
    )

    question_text = response["choices"][0]["message"]["content"].strip()
    return {"question": question_text}


@app.post("/analyze_call")
def analyze_call(req: AnalyzeReq) -> dict:
    _require_model()

    transcript_lines = "\n".join(
        f"{entry.get('role', 'unknown')}: {entry.get('text', '')}"
        for entry in req.transcript
    )
    prompt = _emotion_analysis_template.format(transcript=transcript_lines)

    response = _model.create_chat_completion(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=512,
    )

    raw = response["choices"][0]["message"]["content"].strip()

    # Extract JSON block if wrapped in markdown fences
    if "```" in raw:
        parts = raw.split("```")
        for part in parts[1::2]:
            candidate = part.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            try:
                parsed = json.loads(candidate)
                break
            except json.JSONDecodeError:
                continue
        else:
            parsed = None
    else:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None

    if parsed is None:
        logger.warning("JSON parse failed for analyze_call; returning fallback structure.")
        parsed = {
            "emotion": "unknown",
            "topics": [],
            "summary": raw,
            "facts_master": [],
            "facts_overlay": [],
        }

    return {
        "emotion": parsed.get("emotion", "unknown"),
        "topics": parsed.get("topics", []),
        "summary": parsed.get("summary", ""),
        "facts_master": parsed.get("facts_master", []),
        "facts_overlay": parsed.get("facts_overlay", []),
    }


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model_loaded": _model is not None}
