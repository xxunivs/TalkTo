"""2단계 심층 분석 — 항목별 분리 호출 (P0: ① 인물·관계).

LLM 산출은 그대로 믿지 않는다: 근거 세그먼트 실존(R1), confidence enum,
구조 검증을 코드로 강제하고, 위반 항목은 버리거나 unresolved로 강등한다.
"""

import json
import logging

from app.providers.llm import create_openai_client

from app.core.config import Settings
from app.pipeline.analysis.persons_prompts import (
    PERSONS_SYSTEM_PROMPT,
    build_persons_user_prompt,
)
from app.schemas.context import SubjectContext
from app.schemas.transcript import TranscriptSegment

logger = logging.getLogger(__name__)

_CONFIDENCE_VALUES = {"confirmed", "inferred"}


def run_persons_analysis(
    segments: list[TranscriptSegment],
    *,
    subject_context: SubjectContext | None,
    subject_speaker_label: str | None,
    settings: Settings,
) -> dict:

    client = create_openai_client(settings, "persons analysis")
    response = client.chat.completions.create(
        model=settings.openai_analysis_model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": PERSONS_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_persons_user_prompt(
                    segments, subject_context, subject_speaker_label
                ),
            },
        ],
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    subject = subject_context.subject if subject_context is not None else None
    return validate_persons_payload(
        payload,
        segments,
        subject_name=subject.name if subject else None,
        subject_address_term=subject.address_term if subject else None,
    )


_SELF_RELATIONS = {"본인", "self", "대상자", "자기 자신"}
# 1·2인칭 대명사와 호응 — 지칭 근거가 될 수 없다 (3인칭 지칭 해소 "걔" 등은 허용)
_PRONOUN_MENTIONS = {
    "나", "내", "내가", "나도", "저", "저희", "우리",
    "너", "네", "네가", "니가", "너도", "널", "당신", "나야", "저야",
    "너네", "너네들", "너희", "너희들",
    "어", "응", "예", "그래",
}
# 특정 개인을 가리키지 않는 일반 표현 — unresolved 자격 없음
_GENERIC_MENTIONS = {"사람", "사람들", "애들", "친구", "친구들", "가족", "식구"}


def validate_persons_payload(
    payload: dict,
    segments: list[TranscriptSegment],
    *,
    subject_name: str | None = None,
    subject_address_term: str | None = None,
) -> dict:
    """코드 검증: 근거 실존·mention 실존·본인 제외·enum·구조. 위반은 제거하고 기록한다."""
    valid_ids = {segment.segment_index for segment in segments}
    transcript_text = " ".join(
        segment.transcript_text + " " + (segment.corrected_text or "")
        for segment in segments
    )
    dropped = {"persons": 0, "relations": 0, "facts": 0, "unresolved": 0, "mentions": 0}

    def clean_source_ids(value) -> list[int] | None:
        if not isinstance(value, list) or not value:
            return None
        ids = [item for item in value if isinstance(item, int) and item in valid_ids]
        return ids or None

    persons = []
    for person in payload.get("persons") or []:
        if not isinstance(person, dict) or not str(person.get("name") or "").strip():
            dropped["persons"] += 1
            continue
        # 대상자 본인·자리표시자 이름 제외 (프롬프트 지시가 뚫려도 코드로 강제)
        name = str(person["name"]).strip()
        relation = str(person.get("relationToSubject") or "").strip().lower()
        placeholder_tokens = ("SPK", "화자", "상대", "청자", "speaker")
        if (
            name in {subject_name, subject_address_term} - {None}
            or relation in _SELF_RELATIONS
            or any(token in name or token in name.upper() for token in placeholder_tokens)
        ):
            dropped["persons"] += 1
            continue
        source_ids = clean_source_ids(person.get("sourceSegmentIds"))
        if source_ids is None or person.get("confidence") not in _CONFIDENCE_VALUES:
            dropped["persons"] += 1
            continue

        relations = []
        for relation_entry in person.get("relationsToOthers") or []:
            relation_ids = (
                clean_source_ids(relation_entry.get("sourceSegmentIds"))
                if isinstance(relation_entry, dict)
                else None
            )
            if (
                relation_ids is None
                or relation_entry.get("confidence") not in _CONFIDENCE_VALUES
                or not str(relation_entry.get("name") or "").strip()
            ):
                dropped["relations"] += 1
                continue
            relations.append({**relation_entry, "sourceSegmentIds": relation_ids})

        facts = []
        for fact in person.get("facts") or []:
            fact_ids = (
                clean_source_ids(fact.get("sourceSegmentIds"))
                if isinstance(fact, dict)
                else None
            )
            if (
                fact_ids is None
                or fact.get("confidence") not in _CONFIDENCE_VALUES
                or not str(fact.get("fact") or "").strip()
            ):
                dropped["facts"] += 1
                continue
            facts.append({**fact, "sourceSegmentIds": fact_ids})

        # mention 게이트: ① 전사문에 실존(힌트 베끼기 차단) ② 1·2인칭 대명사
        # 금지("너"만으로 상대 단정 차단) ③ 지칭 형태여야 함(쉼표 포함·긴 발화
        # 인용구 차단) ④ 대상자 호칭은 타인의 지칭이 될 수 없음.
        # 유효 mention이 하나도 안 남는 인물은 통째로 신뢰하지 않는다
        mentions = []
        for mention in person.get("mentions") or []:
            cleaned = str(mention).strip().rstrip("?.!~")
            if not cleaned:
                continue
            if (
                "," in cleaned
                or len(cleaned) > 10
                or cleaned in _PRONOUN_MENTIONS
                or cleaned.rstrip("은는이가도") in _PRONOUN_MENTIONS
                or cleaned in {subject_name, subject_address_term} - {None}
            ):
                dropped["mentions"] += 1
                continue
            if cleaned in transcript_text:
                mentions.append(cleaned)
            else:
                dropped["mentions"] += 1
        if not mentions:
            dropped["persons"] += 1
            continue
        persons.append(
            {
                "name": str(person["name"]).strip(),
                "relationToSubject": person.get("relationToSubject") or None,
                "mentions": mentions,
                "confidence": person["confidence"],
                "sourceSegmentIds": source_ids,
                "relationsToOthers": relations,
                "facts": facts,
            }
        )

    # 같은 지칭이 여러 인물에 배정되면 근거 불충분 — 전원에게서 제거
    mention_owners: dict[str, int] = {}
    for person in persons:
        for mention in person["mentions"]:
            mention_owners[mention] = mention_owners.get(mention, 0) + 1
    duplicated = {mention for mention, count in mention_owners.items() if count > 1}
    if duplicated:
        survivors = []
        for person in persons:
            kept = [m for m in person["mentions"] if m not in duplicated]
            dropped["mentions"] += len(person["mentions"]) - len(kept)
            person["mentions"] = kept
            if kept:
                survivors.append(person)
            else:
                dropped["persons"] += 1
        persons = survivors

    unresolved = []
    for mention in payload.get("unresolvedMentions") or []:
        mention_ids = (
            clean_source_ids(mention.get("sourceSegmentIds"))
            if isinstance(mention, dict)
            else None
        )
        text = str(mention.get("mention") or "").strip().rstrip("?.!,~") if isinstance(mention, dict) else ""
        if (
            mention_ids is None
            or not text
            or text in _PRONOUN_MENTIONS
            or text in _GENERIC_MENTIONS
            or text in {subject_name, subject_address_term} - {None}
        ):
            dropped["unresolved"] += 1
            continue
        unresolved.append(
            {
                "mention": str(mention["mention"]).strip(),
                "context": str(mention.get("context") or "").strip(),
                "sourceSegmentIds": mention_ids,
            }
        )

    if any(dropped.values()):
        logger.warning("persons validation dropped items: %s", dropped)
    return {
        "persons": persons,
        "unresolvedMentions": unresolved,
        "validationDropped": dropped,
    }
