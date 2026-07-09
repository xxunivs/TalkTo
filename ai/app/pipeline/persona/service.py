"""4단계 페르소나 응답 서빙 — 조립된 instructions + 런타임 기억 주입 → 응답.

BE가 (a) 저장한 페르소나 조립본(persona.instructions), (b) 이번 질의로 벡터 검색한
관련 기억 후보(memories), (c) 대화 이력(history), (d) 사용자 메시지(message)를 넘기면,
AI가 기억을 프롬프트에 주입해 응답을 생성한다. AI는 stateless — 벡터 검색은 BE 담당.

평가 하네스(evaluation/persona)에서 검증한 조립·기억주입·응답 흐름을 엔드포인트로 옮긴 것.
"""

import json
import logging

from app.core.config import Settings
from app.pipeline.persona.assembler import assemble_persona_prompt
from app.providers.llm import create_openai_client
from app.schemas.persona import (
    MemoryCandidate,
    MemoryCandidateRequest,
    MemoryCandidateResponse,
    PersonaAssemblyRequest,
    PersonaAssemblyResponse,
    PersonaResponseRequest,
    PersonaResponseResult,
)
from app.schemas.transcript import TranscriptSegment

logger = logging.getLogger(__name__)

PROVIDER = "openai"

MEMORY_CANDIDATE_PROMPT = """대화의 마지막 주고받음에서 '앞으로도 기억할 만한 새로운 사실'만 뽑는다.

- 대상: 사용자가 말한 지속되는 근황·변화(취직·이사·결혼·출산·시험·건강·새 관계 등).
- 제외: 인사·감정 표현("보고 싶어")·일시적 상태·이미 뻔한 것·페르소나(어르신) 자신의 말.
- 없으면 빈 목록.

각 후보 필드:
- summary: 한 문장(누가 무엇). 사용자가 실제로 말한 것만, 지어내지 않는다.
- category: 짧은 분류(예: 직장·가족·건강·경조사·이사).
- importance: 1~10 정수(클수록 중요).
- confidence: 0~1(사용자 발화에 얼마나 분명히 나왔는가).

JSON으로만: {"candidates": [{"summary": "...", "category": "...", "importance": 5, "confidence": 0.8}]}"""


def _speech_segments(examples: list[str]) -> list[TranscriptSegment]:
    """말투 few-shot용 예시 발화를 대상자(화자 S) 세그먼트로 감싼다."""
    return [
        TranscriptSegment(
            segment_index=i, start_ms=i * 1000, end_ms=i * 1000 + 500,
            speaker_label="S", transcript_text=text,
        )
        for i, text in enumerate(examples)
        if text.strip()
    ]


def assemble_persona_instructions(
    request: PersonaAssemblyRequest,
) -> PersonaAssemblyResponse:
    """subjectContext + intakeContext + 발화 예시 → 페르소나 system 프롬프트.

    LLM 미사용(순수 조립) — 비용·지연 없음. 산출물을 BE가 저장했다가 채팅 시
    /responses의 persona.instructions로 넘긴다. 기억은 조립 시 넣지 않고 런타임 주입.
    """
    segments = _speech_segments(request.speech_examples)
    instructions = assemble_persona_prompt(
        subject_context=request.subject_context,
        persons_results=[],
        sensitivity_results=[],
        segments_by_recording=[segments] if segments else [],
        subject_labels=["S"] if segments else [],
        intake_context=request.intake_context,
    )
    subject = request.subject_context.subject
    name = (subject.name if subject and subject.name else "대상자")
    return PersonaAssemblyResponse(instructions=instructions, subject_name=name)


def build_memory_block(memories) -> str:
    """검색된 기억 후보를 프롬프트에 주입할 텍스트 블록으로. 없으면 빈 문자열."""
    if not memories:
        return ""
    lines = []
    for memory in memories:
        title = (memory.title or "").strip()
        head = f"[{title}] " if title else ""
        lines.append(f"- {head}{memory.content.strip()}")
    return (
        "참고 기억(대화 화제가 닿으면 지명·햇수 등 구체 사실을 그대로 녹이고, 인사·짧은 "
        "안부엔 억지로 꺼내지 않는다. 없는 내용은 지어내지 않는다):\n" + "\n".join(lines)
    )


def generate_persona_response(
    request: PersonaResponseRequest, *, settings: Settings
) -> PersonaResponseResult:
    client = create_openai_client(settings, "persona response")

    messages = [{"role": "system", "content": request.persona.instructions}]
    memory_block = build_memory_block(request.memories)
    if memory_block:
        messages.append({"role": "system", "content": memory_block})
    for turn in request.history:
        messages.append({"role": turn.role, "content": turn.content})
    messages.append({"role": "user", "content": request.message})

    response = client.chat.completions.create(
        model=settings.openai_chat_model,
        messages=messages,
    )
    content = (response.choices[0].message.content or "").strip()

    # 첫 컷: 주입한 기억 전부를 '사용됨'으로 반환. 실제 인용된 것만 추리는 정교화는 이후.
    return PersonaResponseResult(
        content=content,
        retrieved_memory_ids=[memory.id for memory in request.memories],
        provider=PROVIDER,
        model=settings.openai_chat_model,
    )


_STORE_IMPORTANCE_THRESHOLD = 6  # 옛 MVP와 동일: importance >= 6이면 저장 추천


def extract_memory_candidates(
    request: MemoryCandidateRequest, *, settings: Settings
) -> MemoryCandidateResponse:
    """채팅 턴에서 앞으로 저장할 만한 새 기억 후보를 뽑는다.

    저장 판단(shouldStore)은 AI가 importance 임계값으로 내린다(옛 MVP 동작 유지).
    BE는 shouldStore를 따르되, stateless인 AI가 못 하는 기존 기억과의 중복 제거만 한다.
    """
    client = create_openai_client(settings, "memory candidate extraction")

    convo = "\n".join(f"{m.role}: {m.content}" for m in request.history)
    convo += f"\nuser: {request.user_message}\nassistant: {request.assistant_message}"

    response = client.chat.completions.create(
        model=settings.openai_analysis_model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": MEMORY_CANDIDATE_PROMPT},
            {"role": "user", "content": convo},
        ],
    )
    payload = json.loads(response.choices[0].message.content or "{}")

    candidates = []
    for item in payload.get("candidates") or []:
        summary = str(item.get("summary") or "").strip()
        if not summary:
            continue
        # 코드가 범위를 강제(LLM이 벗어난 값을 줘도 스키마 검증에서 터지지 않게)
        try:
            importance = int(item.get("importance", 5))
        except (TypeError, ValueError):
            importance = 5
        importance = max(1, min(10, importance))
        try:
            confidence = float(item.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        category = (str(item.get("category")).strip() or None) if item.get("category") else None
        candidates.append(
            MemoryCandidate(
                summary=summary,
                category=category,
                importance=importance,
                confidence=confidence,
                should_store=importance >= _STORE_IMPORTANCE_THRESHOLD,
            )
        )

    return MemoryCandidateResponse(
        candidates=candidates,
        provider=PROVIDER,
        model=settings.openai_analysis_model,
    )
