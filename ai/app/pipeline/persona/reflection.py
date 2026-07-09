"""4단계 reflection — 누적 기억에서 상위 통찰 도출 (LLM 제안 + 근거 검증).

Generative Agents 방식: 개별 관찰(기억)을 가로질러 대상자를 설명하는 통찰을 만들고,
각 통찰에 근거 기억을 연결한다. 근거 없는 통찰은 버린다(R1 준용). 기억을 M1·M2…
번호로 제시해 LLM이 번호로 인용하게 하고(긴 id 인용 오류 방지), 검증에서 실제 id로 되돌린다.
"""

import json
import logging

from app.core.config import Settings
from app.providers.llm import create_openai_client
from app.schemas.reflection import (
    Reflection,
    ReflectionRequest,
    ReflectionResponse,
)

logger = logging.getLogger(__name__)

_CATEGORIES = {"가치관", "성향", "반복주제", "관계", "생애서사"}

REFLECTION_SYSTEM_PROMPT = """너는 대상자(고인)에 대한 **기억 조각들을 가로질러 상위 통찰**을 뽑는 분석기다. 목적은 개별 사실이 아니라, 여러 기억에서 반복·수렴하는 대상자의 성향·가치관·삶의 패턴을 요약해 페르소나의 '성격·가치관' 재료를 만드는 것이다.

기억은 M1, M2 … 번호로 주어진다. 각 통찰은 근거가 된 기억 번호들을 함께 낸다.

규칙:
1. **개별 기억을 다시 쓰지 않는다.** 한 기억만으로 나오는 단일 사실은 통찰이 아니다. **두 개 이상의 기억이 뒷받침하는**, 여러 기억을 묶어야 보이는 상위 서술만 통찰로 만든다(예: 여러 요리·나눔 기억 → "음식을 나누는 데서 정을 느낀다").
2. **주어진 기억 밖 내용을 지어내지 않는다.** 근거 기억에서 실제로 확인되는 것만 일반화한다. 억지로 만들지 말고, 통찰이 없으면 빈 배열.
3. evidence: 그 통찰을 뒷받침하는 기억 번호(정수) 목록. **반드시 2개 이상.**
4. category는 다음 중 하나: 가치관 / 성향 / 반복주제 / 관계 / 생애서사.
5. importance: 그 통찰이 대상자를 이해하는 데 얼마나 핵심인지 1~10.
6. 통찰은 짧고 단정한 3인칭 서술 한 문장으로 쓴다.

다음 JSON 형식으로만 답한다:
{
  "reflections": [
    { "insight": "가족이 함께 밥 먹는 것을 무엇보다 중요하게 여긴다.",
      "category": "가치관", "evidence": [1, 5, 9], "importance": 8 }
  ]
}"""


def _build_user_prompt(request: ReflectionRequest) -> str:
    parts: list[str] = []
    if request.subject_context and request.subject_context.subject:
        subject = request.subject_context.subject
        parts.append(f"대상자: {subject.name or '미상'}")

    lines = []
    for index, memory in enumerate(request.memories, start=1):
        tag_hint = f" [{', '.join(memory.tags)}]" if memory.tags else ""
        lines.append(f"M{index}: {memory.memory_text}{tag_hint}")
    parts.append("기억 목록:\n" + "\n".join(lines))
    return "\n\n".join(parts)


def run_reflection(
    request: ReflectionRequest, *, settings: Settings
) -> ReflectionResponse:
    client = create_openai_client(settings, "persona reflection")
    response = client.chat.completions.create(
        model=settings.openai_analysis_model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(request)},
        ],
    )
    payload = json.loads(response.choices[0].message.content or "{}")

    id_by_number = {i: memory.id for i, memory in enumerate(request.memories, start=1)}
    reflections: list[Reflection] = []
    dropped = 0
    for item in payload.get("reflections") or []:
        if not isinstance(item, dict):
            dropped += 1
            continue
        insight = str(item.get("insight") or "").strip()
        category = str(item.get("category") or "").strip()
        evidence_ids = [
            id_by_number[n]
            for n in (item.get("evidence") or [])
            if isinstance(n, int) and n in id_by_number
        ]
        # 근거 2개 미만·통제 어휘 밖 카테고리·빈 통찰은 신뢰 불가로 버린다
        if not insight or category not in _CATEGORIES or len(set(evidence_ids)) < 2:
            dropped += 1
            continue
        raw_importance = item.get("importance")
        importance = (
            min(10, max(1, int(raw_importance)))
            if isinstance(raw_importance, (int, float))
            else 5
        )
        reflections.append(
            Reflection(
                insight=insight,
                category=category,
                evidence_memory_ids=sorted(set(evidence_ids)),
                importance=importance,
            )
        )

    if dropped:
        logger.info("reflection dropped %d unsupported insights", dropped)
    return ReflectionResponse(
        reflections=reflections,
        provider="openai",
        model=settings.openai_analysis_model,
    )
