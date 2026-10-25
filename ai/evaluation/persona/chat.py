"""페르소나 터미널 대화, 서빙 흐름(조립 + 기억 top-k 주입 + 응답) 재현 (빈 줄 또는 Ctrl-D 로 종료)"""

import argparse
import json
from pathlib import Path

from app.schema.persona import (
    ConversationMessage,
    MemoryContext,
    PersonaAssemblyRequest,
    PersonaContext,
    PersonaResponseRequest,
    PersonaResponseResult,
)
from app.schema.subject import IntakeContext, SubjectContext
from app.setting import Settings, load_settings
from evaluation.common import REPO_ROOT
from orchestrator.service import orchestrate_persona_response
from pipeline.s3_synthesis.c_build_system_prompt import (
    assemble_persona_instructions,
    generate_persona_response,
)
from pipeline.s3_synthesis.d_build_memory_embedding import embed_texts


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def respond(request: PersonaResponseRequest, settings: Settings) -> PersonaResponseResult:
    """/v1/persona/responses 와 같은 경로 선택 (PERSONA_ORCHESTRATOR 켜지면 중간계층)"""
    if settings.persona_orchestrator and not request.persona.fixed_prompt:
        return orchestrate_persona_response(request, settings=settings)
    return generate_persona_response(request, settings=settings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument(
        "--persona",
        type=Path,
        default=None,
        help="미리 조립된 프롬프트 파일(주면 fresh 조립 대신 이걸 사용)",
    )
    parser.add_argument(
        "--memories",
        type=Path,
        default=None,
        help="기억 카드 JSON 목록(title, content, tags). 주면 fixture 카드 대신 사용",
    )
    parser.add_argument("--memory-k", type=int, default=6)
    parser.add_argument(
        "--speech-examples",
        type=int,
        default=0,
        help="fixture _speechExamples 중 앞 N개를 말투 few-shot으로",
    )
    args = parser.parse_args()

    settings = load_settings(REPO_ROOT / ".env")
    payload = json.loads(args.fixture.read_text(encoding="utf-8"))
    subject_context = SubjectContext(**payload["subjectContext"])
    intake_raw = payload.get("intakeContext")
    intake_context = IntakeContext(**intake_raw) if intake_raw else None
    speech_examples = (payload.get("_speechExamples") or [])[: args.speech_examples]

    if args.persona:
        instructions = args.persona.read_text(encoding="utf-8")
        name = subject_context.subject.name if subject_context.subject else "대상자"
    else:
        assembled = assemble_persona_instructions(
            PersonaAssemblyRequest(
                subject_context=subject_context,
                intake_context=intake_context,
                speech_examples=speech_examples,
            ),
            settings,
        )
        instructions = assembled.instructions
        name = assembled.subject_name

    if args.memories:
        cards = [
            c for c in json.loads(args.memories.read_text(encoding="utf-8")) if c.get("content")
        ]
    else:
        cards = [c for c in (intake_raw or {}).get("memoryCards", []) if c.get("content")]
    vectors = embed_texts([c["content"] for c in cards], settings=settings) if cards else []
    persona = PersonaContext(subject_id="local-chat", instructions=instructions, voice_id=None)

    print(f"\n=== {name} 페르소나와 대화 (기억카드 {len(cards)}개, top-{args.memory_k} 주입) ===")
    print("종료: 빈 줄 또는 Ctrl-D\n")
    history: list[ConversationMessage] = []
    while True:
        try:
            message = input("나> ").strip()
        except EOFError:
            break
        if not message:
            break

        memories: list[MemoryContext] = []
        if cards:
            qvec = embed_texts([message], settings=settings)[0]
            ranked = sorted(
                zip(cards, vectors, strict=True), key=lambda cv: _cosine(qvec, cv[1]), reverse=True
            )[: args.memory_k]
            memories = [
                MemoryContext(
                    id=str(i),
                    title=card.get("title", ""),
                    content=card["content"],
                    tags=card.get("tags", []),
                )
                for i, (card, _) in enumerate(ranked)
            ]

        result = respond(
            PersonaResponseRequest(
                message=message, history=history, memories=memories, persona=persona
            ),
            settings,
        )
        print(f"{name}> {result.content}\n")
        history.append(ConversationMessage(role="user", content=message))
        history.append(ConversationMessage(role="assistant", content=result.content))

    print("\n(대화 종료)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
