"""페르소나와 터미널에서 직접 대화 — 실제 서빙 흐름(조립 + 기억 top-k 주입 + 응답 생성).

BE UI 없이 fixture만으로 대화 체험. /assembly로 페르소나 프롬프트를 만들고, 매 입력마다
memoryCards를 임베딩 검색해 관련 기억을 주입한 뒤 /responses 로직으로 답한다.

Usage:
    python -m evaluation.persona.chat --fixture data/fixtures/subject_context_singeumja.json
    # 미리 조립된 프롬프트를 쓰려면:
    python -m evaluation.persona.chat --fixture <fx> --persona evaluation/persona/results/persona_xxx.txt
종료: 빈 줄 입력 또는 Ctrl-D.
"""

import argparse
import json
from pathlib import Path

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


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--persona", type=Path, default=None,
                        help="미리 조립된 프롬프트 파일(주면 fresh 조립 대신 이걸 사용)")
    parser.add_argument("--memory-k", type=int, default=6)
    parser.add_argument("--speech-examples", type=int, default=0,
                        help="fixture _speechExamples 중 앞 N개를 말투 few-shot으로")
    args = parser.parse_args()

    settings = load_settings(".env")
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
            )
        )
        instructions = assembled.instructions
        name = assembled.subject_name

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
                zip(cards, vectors), key=lambda cv: _cosine(qvec, cv[1]), reverse=True
            )[: args.memory_k]
            memories = [
                MemoryContext(
                    id=str(i), title=card.get("title", ""),
                    content=card["content"], tags=card.get("tags", []),
                )
                for i, (card, _) in enumerate(ranked)
            ]

        result = generate_persona_response(
            PersonaResponseRequest(
                message=message, history=history, memories=memories, persona=persona
            ),
            settings=settings,
        )
        print(f"{name}> {result.content}\n")
        history.append(ConversationMessage(role="user", content=message))
        history.append(ConversationMessage(role="assistant", content=result.content))

    print("\n(대화 종료)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
