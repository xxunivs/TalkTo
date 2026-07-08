"""4단계 페르소나 응답 서빙 — 조립된 instructions + 런타임 기억 주입 → 응답.

BE가 (a) 저장한 페르소나 조립본(persona.instructions), (b) 이번 질의로 벡터 검색한
관련 기억 후보(memories), (c) 대화 이력(history), (d) 사용자 메시지(message)를 넘기면,
AI가 기억을 프롬프트에 주입해 응답을 생성한다. AI는 stateless — 벡터 검색은 BE 담당.

평가 하네스(evaluation/persona)에서 검증한 조립·기억주입·응답 흐름을 엔드포인트로 옮긴 것.
"""

import logging

from openai import OpenAI

from app.core.config import Settings
from app.pipeline.persona.assembler import assemble_persona_prompt
from app.schemas.persona import (
    PersonaAssemblyRequest,
    PersonaAssemblyResponse,
    PersonaResponseRequest,
    PersonaResponseResult,
)
from app.schemas.transcript import TranscriptSegment

logger = logging.getLogger(__name__)

PROVIDER = "openai"


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
        "아래는 이 대화에 관련된 기억이다. 답변에 자연스럽게 녹이되, "
        "없는 내용을 지어내지 않는다:\n" + "\n".join(lines)
    )


def generate_persona_response(
    request: PersonaResponseRequest, *, settings: Settings
) -> PersonaResponseResult:
    if settings.openai_api_key is None:
        raise RuntimeError("OPENAI_API_KEY is required for persona response")
    client = OpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )

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
