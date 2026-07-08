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
from app.schemas.context import IntakeContext, SubjectContext
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
    intake_context: IntakeContext | None = None,
    retrieved_memories: list[str] | None = None,
) -> str:
    subject_name = (
        subject_context.subject.name if subject_context.subject else "대상자"
    )
    address = subject_context.subject.address_term if subject_context.subject else None

    identity = f"{subject_name}"
    if address:
        identity += f". 가족들은 '{address}'라고 부른다."
    if intake_context is not None:
        bp = intake_context.basic_profile or {}
        if bp.get("oneLine"):
            identity += f" {bp['oneLine']}"
        # familyStatusNow 상시 주입도 과공유를 일으켜 제외
        # 사인·경위(deathContext)는 프롬프트에 넣지 않는다 — 배경으로 넣어도 직접
        # 물으면 단정적으로 새어나와(F 안전 게이트 미달) 페르소나가 알 필요도 없다.
        if bp.get("status") == "사망":
            identity += (
                "\n(사후 페르소나다. 사망·임종·사인·경위는 어떤 경우에도 입에 올리지 않는다."
                " 직접 물어도 '그런 얘기는 자세히 말하지 말자, 마음만 아프다' 하고 안부·가족 챙김으로 넘긴다.)"
            )

    # Intake 가족톤을 1차 소스로, 파이프라인 persons 병합을 보강으로 합침
    tone_by_name: dict[str, tuple[str | None, str | None]] = {}
    if intake_context is not None:
        for note in intake_context.family_map:
            tone_by_name[note.name] = (note.relation, note.tone)
    merged = {p["name"]: p["relation"] for p in merge_persons(persons_results)}
    ordered_names = list(tone_by_name) + [n for n in merged if n not in tone_by_name]
    family_lines = []
    for name in ordered_names:
        rel, tone = tone_by_name.get(name, (None, None))
        rel = rel or merged.get(name)
        line = f"- {name}" + (f" ({rel})" if rel else "")
        if tone:
            line += f": {tone}"
        family_lines.append(line)
    family = "\n".join(family_lines) or "- (아직 파악된 가족 정보 없음)"

    examples = collect_speech_examples(segments_by_recording, subject_labels)
    speech_examples = "\n".join(f'- "{e}"' for e in examples) or "- (예시 없음)"

    taboo = "\n".join(f"- {t}" for t in aggregate_taboo(sensitivity_results)) or "- (없음)"

    # memoryCards 상시 주입은 과공유(안전 게이트 회귀)를 일으켜 제외 —
    # 기억은 런타임 RAG로 질의 관련된 것만 주입한다
    memories = (
        "\n".join(f"- {m}" for m in retrieved_memories)
        if retrieved_memories else "(이 대화 관련 기억이 런타임에 주입됨)"
    )

    speech_style = (
        intake_context.speech_style
        if intake_context and intake_context.speech_style
        else "짧고 담담한 단문이 기본. 요리법·추억은 길게 풀어도 자연스럽다."
    )
    personality = (
        intake_context.personality
        if intake_context and intake_context.personality
        else "(파악된 성격 정보 없음)"
    )
    if intake_context and intake_context.situational_reactions:
        sit_lines = []
        for r in intake_context.situational_reactions:
            line = f'- "{r.situation}" → {r.response}'
            if r.avoid:
                line += f" (피할 것: {r.avoid})"
            sit_lines.append(line)
        situational = "\n".join(sit_lines)
    else:
        situational = "(별도 지침 없음 — 위 원칙을 따른다)"
    # Intake 금기를 taboo에 합침
    if intake_context and intake_context.taboo_topics:
        taboo = "\n".join(f"- {t}" for t in intake_context.taboo_topics) + (
            f"\n{taboo}" if "없음" not in taboo else ""
        )

    return PERSONA_TEMPLATE.format(
        subject_name=subject_name,
        identity=identity,
        personality=personality,
        family=family,
        speech_style=speech_style,
        speech_examples=speech_examples,
        situational=situational,
        taboo=taboo,
        fixed_rules=FIXED_SAFETY_RULES,
        memories=memories,
    )
