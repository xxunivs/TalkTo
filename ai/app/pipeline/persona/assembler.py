"""4단계 조립기 v0 — 파이프라인 산출물 → system prompt 슬롯 채우기.

노션 설계: 고정 템플릿 + 슬롯을 3단계 산출물로 채움 (genagents: 구조화 프로필
+ 원문 발화 주입). 슬롯 재료:
- 가족: 여러 녹음의 persons 병합 (이름·관계, confidence 높은 것 우선)
- 말투 예시: 대상자 화자의 실제 발화 (원문 주입 — Stanford 재현 연구 근거)
- 금기: sensitivityFlags 유형 집계
- 기억: 런타임에 RAG로 주입 (조립 시엔 자리표시)
"""

from collections import Counter

from app.pipeline.persona.template import (
    FIXED_SAFETY_RULES,
    PERSONA_TEMPLATE,
)
from app.schemas.context import SubjectContext
from app.schemas.transcript import TranscriptSegment

_SENSITIVITY_LABELS = {
    "health": "건강·병에 대한 구체적 얘기(병명·병원비 등)",
    "familyConflict": "가족 간 갈등·서운함",
    "asset": "돈·재산·상속",
    "death": "죽음·장례",
    "thirdParty": "다른 가족의 사생활(직장·주소·건강 등)",
}


def merge_persons(persons_results: list[dict]) -> list[dict]:
    """여러 녹음의 persons를 이름 기준 병합. confidence·언급 빈도로 관계 확정."""
    by_name: dict[str, dict] = {}
    for result in persons_results:
        for person in result.get("persons", []):
            name = person["name"]
            entry = by_name.setdefault(
                name, {"name": name, "relations": Counter(), "mentions": set()}
            )
            if person.get("relationToSubject"):
                weight = 2 if person.get("confidence") == "confirmed" else 1
                entry["relations"][person["relationToSubject"]] += weight
            entry["mentions"].update(person.get("mentions", []))
    merged = []
    for entry in by_name.values():
        relation = entry["relations"].most_common(1)[0][0] if entry["relations"] else None
        merged.append({"name": entry["name"], "relation": relation})
    return merged


def collect_speech_examples(
    segments_by_recording: list[list[TranscriptSegment]],
    subject_labels: list[str | None],
    limit: int = 8,
) -> list[str]:
    """대상자 화자의 실제 발화 중 짧고 담백한 것들 (말투 few-shot)."""
    examples: list[str] = []
    for segments, label in zip(segments_by_recording, subject_labels):
        for segment in segments:
            if label and segment.speaker_label != label:
                continue
            text = (segment.corrected_text or segment.transcript_text).strip()
            if 6 <= len(text) <= 40 and text not in examples:
                examples.append(text)
    return examples[:limit]


def aggregate_taboo(sensitivity_results: list[dict]) -> list[str]:
    types: set[str] = set()
    for result in sensitivity_results:
        for flag in result.get("sensitivityFlags", []):
            types.add(flag["type"])
    return [_SENSITIVITY_LABELS[t] for t in _SENSITIVITY_LABELS if t in types]


def assemble_persona_prompt(
    *,
    subject_context: SubjectContext,
    persons_results: list[dict],
    sensitivity_results: list[dict],
    segments_by_recording: list[list[TranscriptSegment]],
    subject_labels: list[str | None],
    retrieved_memories: list[str] | None = None,
) -> str:
    subject_name = (
        subject_context.subject.name if subject_context.subject else "대상자"
    )
    address = subject_context.subject.address_term if subject_context.subject else None

    identity = f"{subject_name}"
    if address:
        identity += f". 가족들은 '{address}'라고 부른다."

    family_lines = []
    for person in merge_persons(persons_results):
        rel = f" ({person['relation']})" if person["relation"] else ""
        family_lines.append(f"- {person['name']}{rel}")
    family = "\n".join(family_lines) or "- (아직 파악된 가족 정보 없음)"

    examples = collect_speech_examples(segments_by_recording, subject_labels)
    speech_examples = "\n".join(f'- "{e}"' for e in examples) or "- (예시 없음)"

    taboo = "\n".join(f"- {t}" for t in aggregate_taboo(sensitivity_results)) or "- (없음)"

    memories = (
        "\n".join(f"- {m}" for m in retrieved_memories)
        if retrieved_memories
        else "(런타임에 관련 기억이 주입됨)"
    )

    return PERSONA_TEMPLATE.format(
        subject_name=subject_name,
        identity=identity,
        family=family,
        speech_style="짧고 담담한 단문이 기본. 요리법·추억은 길게 풀어도 자연스럽다.",
        speech_examples=speech_examples,
        taboo=taboo,
        fixed_rules=FIXED_SAFETY_RULES,
        memories=memories,
    )
