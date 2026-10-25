"""페르소나와 브라우저에서 대화 — 로컬 웹 UI (실제 조립+기억주입+응답 서빙 흐름).

실행:
    .venv/bin/python -m evaluation.persona.webchat
    → 브라우저에서 http://localhost:8800 열기

기본은 신금자(오디오 빌드). 다른 인물은 환경변수로:
    CHAT_FIXTURE=... CHAT_PERSONA=... CHAT_MEMORIES=... .venv/bin/python -m evaluation.persona.webchat
"""

import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.core.config import load_settings
from app.pipeline.embeddings.service import embed_texts
from app.pipeline.persona.service import (
    assemble_persona_instructions,
    generate_persona_response,
)
from app.schemas.context import IntakeContext, SubjectContext
from app.schemas.persona import (
    ConversationMessage,
    MemoryContext,
    PersonaAssemblyRequest,
    PersonaContext,
    PersonaResponseRequest,
)

_FIXTURE = Path(os.getenv("CHAT_FIXTURE", "data/fixtures/subject_context_singeumja.json"))
_PERSONA = os.getenv("CHAT_PERSONA", "data/built_singeumja/persona.txt")
_MEMORIES = os.getenv("CHAT_MEMORIES", "data/built_singeumja/memories.json")
_MEMORY_K = int(os.getenv("CHAT_MEMORY_K", "6"))

settings = load_settings(".env")
_payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
subject_context = SubjectContext(**_payload["subjectContext"])
NAME = subject_context.subject.name if subject_context.subject else "대상자"

if _PERSONA and Path(_PERSONA).exists():
    _instructions = Path(_PERSONA).read_text(encoding="utf-8")
else:
    intake_raw = _payload.get("intakeContext")
    _instructions = assemble_persona_instructions(
        PersonaAssemblyRequest(
            subject_context=subject_context,
            intake_context=IntakeContext(**intake_raw) if intake_raw else None,
        )
    ).instructions

if _MEMORIES and Path(_MEMORIES).exists():
    _cards = [c for c in json.loads(Path(_MEMORIES).read_text(encoding="utf-8")) if c.get("content")]
else:
    _cards = [c for c in (_payload.get("intakeContext") or {}).get("memoryCards", []) if c.get("content")]
_vectors = embed_texts([c["content"] for c in _cards], settings=settings) if _cards else []
_persona = PersonaContext(subject_id="webchat", instructions=_instructions, voice_id=None)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


app = FastAPI()


class ChatIn(BaseModel):
    message: str
    history: list[dict] = []


_PAGE = """<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>__NAME__ 대화</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,system-ui,sans-serif;background:#f2f2f7;height:100vh;display:flex;flex-direction:column}
header{background:#fff;padding:14px;text-align:center;font-weight:600;border-bottom:1px solid #ddd}
#log{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px}
.b{max-width:78%;padding:10px 14px;border-radius:18px;line-height:1.45;white-space:pre-wrap;word-break:break-word}
.me{align-self:flex-end;background:#0a84ff;color:#fff;border-bottom-right-radius:5px}
.her{align-self:flex-start;background:#fff;color:#111;border:1px solid #e2e2e2;border-bottom-left-radius:5px}
.think{align-self:flex-start;color:#999;font-style:italic}
footer{display:flex;gap:8px;padding:12px;background:#fff;border-top:1px solid #ddd}
#msg{flex:1;padding:11px 14px;border:1px solid #ccc;border-radius:20px;font-size:16px;outline:none}
button{padding:0 18px;border:0;border-radius:20px;background:#0a84ff;color:#fff;font-size:16px;font-weight:600}
button:disabled{opacity:.4}
</style></head><body>
<header>__NAME__ 님과 대화</header>
<div id=log></div>
<footer><input id=msg placeholder="말을 걸어보세요…" autocomplete=off><button id=send>보내기</button></footer>
<script>
const log=document.getElementById('log'),msg=document.getElementById('msg'),send=document.getElementById('send');
let history=[];
function add(t,cls){const d=document.createElement('div');d.className='b '+cls;d.textContent=t;log.appendChild(d);log.scrollTop=log.scrollHeight;return d}
async function go(){
  const text=msg.value.trim();if(!text)return;
  msg.value='';send.disabled=true;add(text,'me');
  const th=add('…','think');
  try{
    const r=await fetch('/api/chat',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({message:text,history})});
    const j=await r.json();th.remove();
    const reply=j.reply||('(오류: '+(j.detail||JSON.stringify(j))+')');
    add(reply,'her');
    history.push({role:'user',content:text});history.push({role:'assistant',content:reply});
  }catch(e){th.remove();add('(요청 실패: '+e+')','her')}
  send.disabled=false;msg.focus();
}
send.onclick=go;msg.addEventListener('keydown',e=>{if(e.key==='Enter')go()});
add('__NAME__ 님이 기다리고 있어요. 인사해보세요.','her');msg.focus();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _PAGE.replace("__NAME__", NAME)


@app.post("/api/chat")
def chat(inp: ChatIn) -> dict:
    memories: list[MemoryContext] = []
    if _cards:
        qvec = embed_texts([inp.message], settings=settings)[0]
        ranked = sorted(
            zip(_cards, _vectors), key=lambda cv: _cosine(qvec, cv[1]), reverse=True
        )[:_MEMORY_K]
        memories = [
            MemoryContext(id=str(i), title=c.get("title", ""), content=c["content"],
                          tags=c.get("tags", []))
            for i, (c, _) in enumerate(ranked)
        ]
    history = [ConversationMessage(role=h["role"], content=h["content"]) for h in inp.history]
    result = generate_persona_response(
        PersonaResponseRequest(
            message=inp.message, history=history, memories=memories, persona=_persona
        ),
        settings=settings,
    )
    return {"reply": result.content}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("CHAT_PORT", "8800"))
    print(f"\n=== {NAME} 웹챗 (기억 {len(_cards)}건) ===")
    print(f"브라우저에서 열기:  http://localhost:{port}\n")
    uvicorn.run(app, host="127.0.0.1", port=port)
