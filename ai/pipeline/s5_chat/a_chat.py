"""s5 대화 (로컬 테스트기) — 코드 안에서 페르소나와 실제로 대화해보는 harness.

BE(persona-runtime.service.ts)의 검색+응답 로직을 로컬 파일 기준으로 복제한다.
배포 서빙은 BE가 담당하고, 이건 대화 품질을 로컬에서 확인·튜닝하기 위한 도구다.
파라미터를 BE와 동일하게 맞춰 프로덕션과 같은 결과를 본다.

실행:
    python -m pipeline.s5_chat.a_chat --subject 신금자

읽는 파일 (output/<subject>/):
    s3_synthesis/b_system_prompt.txt      페르소나 지침 (필수)
    s2_extraction/*_memory.json           기억 조각 (설문·전사·따로 넣은 것)
    s3_synthesis/c_memory_embedding.json  기억 임베딩 (id → vector)
    s4_voice/voice_id.txt                 클론 voice_id (--tts 때 사용)
쓰는 파일:
    s5_chat/chat_YYMMDD_HHMM.json         대화 로그 (질문, 응답, 사용된 기억)

BE와 다른 점을 켜려면(프로덕션과 어긋남에 유의):
    --keep-history   이전 대화를 프롬프트에 넣어 멀티턴 (BE는 지금 history 비움)
"""

import argparse
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

from infra.setting import Settings, load_settings
from infra.schema.persona import (
    ConversationMessage,
    MemoryContext,
    PersonaContext,
    PersonaResponseRequest,
)
from pipeline.s3_synthesis.b_build_system_prompt import generate_persona_response
from pipeline.s3_synthesis.c_build_memory_embedding import embed_texts

logger = logging.getLogger(__name__)

# ===== BE persona-runtime 상수 (동일하게 유지 — 프로덕션 파리티) =====
RUNTIME_MEMORY_LIMIT = 5      # 주입할 기억 상위 개수
LOW_CONFIDENCE_THRESHOLD = 0.18  # 이 유사도 미만이면 버림
CANDIDATE_LIMIT = 500         # 후보 임베딩 상한
# 위기 신호 게이트 — 매칭되면 RAG 없이 안전 응답으로 대체
BLOCKED_TOPIC_PATTERN = re.compile(
    r"\b(suicide|self-harm|kill|weapon|violence)\b|자살|자해|살해|무기|폭력",
    re.IGNORECASE,
)
BLOCKED_REPLY = (
    "그런 얘기는 하지 말자. 지금 혼자 있지 말고 가까운 사람한테 바로 연락해라. "
    "너무 힘들면 119나 응급실 도움도 꼭 받고."
)


# ===== 검색 (BE retrieveRuntimeMemories 복제) =====
def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = sum(a * a for a in left) ** 0.5
    norm_right = sum(b * b for b in right) ** 0.5
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)


def retrieve_memories(
    query: str,
    memories: list[dict],
    embeddings: dict[str, list[float]],
    *,
    settings: Settings,
    top_k: int,
    threshold: float,
) -> list[dict]:
    """질문과 유사한 기억 top-k. 임베딩이 없으면 앞쪽 N개로 폴백(BE는 최근 N개)."""
    if not memories:
        return []
    if not embeddings:
        return memories[:top_k]
    query_embedding = embed_texts([query], settings=settings)[0]
    scored: list[tuple[float, dict]] = []
    for memory in memories[:CANDIDATE_LIMIT]:
        vector = embeddings.get(memory["id"])
        if not vector:
            continue
        scored.append((_cosine_similarity(query_embedding, vector), memory))
    scored = [pair for pair in scored if pair[0] >= threshold]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [memory for _, memory in scored[:top_k]]


# ===== 로컬 파일 로더 (파이프라인 output 포맷에 관대하게) =====
def load_persona_instructions(subject_dir: Path) -> str:
    path = subject_dir / "s3_synthesis" / "b_system_prompt.txt"
    if not path.exists():
        raise FileNotFoundError(
            f"페르소나 지침이 없다: {path}\n"
            f"  → 파이프라인 s3(b_persona)까지 돌려 이 파일을 먼저 만들어야 대화할 수 있다."
        )
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"페르소나 지침이 비어 있다: {path}")
    return text


def _first(record: dict, *keys, default=None):
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return default


def load_memories(subject_dir: Path) -> list[dict]:
    """기억 조각 → [{id, title, text, tags}]. 없으면 빈 목록(기억 없이 대화)."""
    # 기억은 출처가 여럿이다. 설문·전사·따로 넣은 것을 모두 모은다.
    found = sorted((subject_dir / "s2_extraction").glob("*_memory.json"))
    if not found:
        logger.warning(
            "기억 파일 없음(%s) — 기억 없이 페르소나만으로 대화한다.",
            subject_dir / "s2_extraction" / "*_memory.json")
        return []
    items: list = []
    for path in found:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            items.extend(raw)
        elif isinstance(raw, dict):
            items.extend(raw.get("memorySegments") or raw.get("memory_segments") or [])
    memories: list[dict] = []
    for i, item in enumerate(items):
        text = _first(item, "memoryText", "memory_text", "content", "text")
        if not text:
            continue
        mid = _first(item, "id", "memorySegmentId", "memory_segment_id", "segmentIndex", "segment_index", default=i)
        memories.append(
            {
                "id": str(mid),
                "title": str(_first(item, "title", default="") or ""),
                "text": str(text),
                "tags": item.get("tags") or [],
            }
        )
    return memories


def load_embeddings(subject_dir: Path) -> dict[str, list[float]]:
    """id → 벡터. 없으면 빈 dict(폴백 검색)."""
    path = subject_dir / "s3_synthesis" / "c_memory_embedding.json"
    if not path.exists():
        logger.warning("임베딩 파일 없음(%s) — 유사도 검색 대신 앞쪽 기억으로 폴백.", path)
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "embeddings" in raw:
        rows = raw["embeddings"]
    elif isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict):
        # 이미 {id: vector} 형태
        return {str(k): v for k, v in raw.items() if isinstance(v, list)}
    else:
        rows = []
    result: dict[str, list[float]] = {}
    for row in rows:
        mid = _first(row, "memorySegmentId", "memory_segment_id", "id", "segmentIndex", "segment_index")
        vector = _first(row, "embedding", "vector")
        if mid is not None and isinstance(vector, list):
            result[str(mid)] = vector
    return result


def load_voice_id(subject_dir: Path) -> str | None:
    path = subject_dir / "s4_voice" / "voice_id.txt"
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        return value or None
    return None


# ===== 응답 =====
def answer(
    message: str,
    *,
    instructions: str,
    subject: str,
    voice_id: str | None,
    memories: list[dict],
    embeddings: dict[str, list[float]],
    history: list[ConversationMessage],
    settings: Settings,
    top_k: int,
    threshold: float,
) -> dict:
    """한 턴 응답. 반환: {text, safety, used_memory_ids, retrieved}."""
    if BLOCKED_TOPIC_PATTERN.search(message):
        return {"text": BLOCKED_REPLY, "safety": "blocked", "used_memory_ids": [], "retrieved": []}

    retrieved = retrieve_memories(
        message, memories, embeddings,
        settings=settings, top_k=top_k, threshold=threshold,
    )
    request = PersonaResponseRequest(
        message=message,
        history=history,
        memories=[
            MemoryContext(id=m["id"], title=m["title"], content=m["text"], tags=m["tags"])
            for m in retrieved
        ],
        persona=PersonaContext(
            subject_id=subject, instructions=instructions, voice_id=voice_id
        ),
    )
    result = generate_persona_response(request, settings=settings)
    return {
        "text": result.content,
        "safety": "allowed",
        "used_memory_ids": result.retrieved_memory_ids,
        "retrieved": [{"id": m["id"], "text": m["text"]} for m in retrieved],
    }


def _maybe_synthesize(text: str, voice_id: str | None, chat_dir: Path, turn: int, settings: Settings) -> str | None:
    """--tts: 응답을 대상자 목소리로 합성해 mp3 저장. 실패해도 대화는 계속."""
    from infra.schema.voice import SpeechSynthesisRequest
    from pipeline.s4_voice.a_voice import synthesize_speech

    try:
        tts = synthesize_speech(
            SpeechSynthesisRequest(text=text, voice_id=voice_id), settings=settings
        )
    except Exception as error:  # 음성 합성 실패가 대화를 깨지 않게
        logger.warning("음성 합성 실패: %s", error)
        return None
    out = chat_dir / f"reply_{turn:03d}.mp3"
    out.write_bytes(tts.audio)
    return str(out)


def run_repl(args: argparse.Namespace) -> int:
    settings = load_settings()
    if settings.openai_api_key is None:
        print("OPENAI_API_KEY가 없다 (.env 확인) — 대화하려면 필요하다.", file=sys.stderr)
        return 1

    subject_dir = Path(args.root) / args.subject
    if not subject_dir.exists():
        print(f"대상자 폴더가 없다: {subject_dir}", file=sys.stderr)
        return 1

    try:
        instructions = load_persona_instructions(subject_dir)
        memories = load_memories(subject_dir)
        embeddings = load_embeddings(subject_dir)
        voice_id = load_voice_id(subject_dir)
    except (FileNotFoundError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1

    chat_dir = subject_dir / "s5_chat"
    chat_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now()
    log_path = chat_dir / started.strftime("chat_%y%m%d_%H%M.json")

    print(f"[{args.subject}] 대화 시작. 기억 {len(memories)}개, 임베딩 {len(embeddings)}개"
          f"{', voice_id 있음' if voice_id else ''}. (exit/quit 종료)")
    if args.keep_history:
        print("  * --keep-history: 멀티턴 (BE 프로덕션과 어긋남, 로컬 튜닝용)")

    history: list[ConversationMessage] = []
    turns: list[dict] = []

    def save_log() -> None:
        log_path.write_text(
            json.dumps(
                {
                    "subject": args.subject,
                    "startedAt": started.isoformat(timespec="seconds"),
                    "params": {
                        "topK": args.top_k,
                        "threshold": args.threshold,
                        "candidateLimit": CANDIDATE_LIMIT,
                        "keepHistory": args.keep_history,
                    },
                    "turns": turns,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    try:
        turn = 0
        while True:
            try:
                message = input("나 > ").strip()
            except EOFError:
                break
            if not message:
                continue
            if message.lower() in {"exit", "quit", "종료"}:
                break

            reply = answer(
                message,
                instructions=instructions, subject=args.subject, voice_id=voice_id,
                memories=memories, embeddings=embeddings, history=history,
                settings=settings, top_k=args.top_k, threshold=args.threshold,
            )
            print(f"{args.subject} > {reply['text']}")
            if reply["retrieved"]:
                print(f"    (기억 {len(reply['retrieved'])}개 참고)")

            audio_path = None
            if args.tts and reply["safety"] == "allowed":
                audio_path = _maybe_synthesize(reply["text"], voice_id, chat_dir, turn, settings)

            turns.append(
                {
                    "user": message,
                    "assistant": reply["text"],
                    "safety": reply["safety"],
                    "usedMemoryIds": reply["used_memory_ids"],
                    "retrieved": reply["retrieved"],
                    **({"audio": audio_path} if audio_path else {}),
                }
            )
            if args.keep_history:
                history = history + [
                    ConversationMessage(role="user", content=message),
                    ConversationMessage(role="assistant", content=reply["text"]),
                ]
            save_log()
            turn += 1
    finally:
        save_log()
        print(f"\n대화 로그 저장: {log_path} ({len(turns)}턴)")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="s5 로컬 대화 테스트기 (BE persona-runtime 복제)")
    parser.add_argument("--subject", required=True, help="대상자 폴더명 (예: 신금자)")
    parser.add_argument("--root", default="output", help="산출물 루트 (기본 output)")
    parser.add_argument("--top-k", type=int, default=RUNTIME_MEMORY_LIMIT, help="주입할 기억 개수")
    parser.add_argument("--threshold", type=float, default=LOW_CONFIDENCE_THRESHOLD, help="유사도 임계값")
    parser.add_argument("--keep-history", action="store_true", help="멀티턴(이전 대화 주입, BE와 어긋남)")
    parser.add_argument("--tts", action="store_true", help="응답을 대상자 목소리 mp3로 합성")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    sys.exit(run_repl(parser.parse_args()))


if __name__ == "__main__":
    main()
