"""2단계 심층 분석 — 항목별 분리 호출 (P0: ① 인물·관계).

LLM 산출은 그대로 믿지 않는다: 근거 세그먼트 실존(R1), confidence enum,
구조 검증을 코드로 강제하고, 위반 항목은 버리거나 unresolved로 강등한다.
"""

import json
import logging

from app.external_service.llm import create_openai_client

from app.core.config import Settings
from app.data_schema.context import SubjectContext
from app.data_schema.transcript import TranscriptSegment

logger = logging.getLogger(__name__)

_CONFIDENCE_VALUES = {"confirmed", "inferred"}


def run_persons_analysis(
    segments: list[TranscriptSegment],
    *,
    subject_context: SubjectContext | None,
    subject_speaker_label: str | None,
    settings: Settings,
    conversation_partner_name: str | None = None,
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
                    segments, subject_context, subject_speaker_label,
                    conversation_partner_name,
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


# ===== 프롬프트 =====
PERSONS_SYSTEM_PROMPT = """너는 가족 통화 녹음 전사문에서 인물과 관계를 추출하는 분석기다. 대상자(전사문의 주인공, 예: 할머니)의 시점에서 등장 인물을 정리한다.

추출 규칙:
1. 전사문에 실제로 언급된 인물만 추출한다. 언급되지 않은 사람을 가족 정보에서 가져와 채우지 않는다.
2. **대상자 본인은 persons에도 unresolvedMentions에도 넣지 않는다** (본인 정보는 다른 분석 항목이 담당). 대상자가 스스로를 부르는 표현("할머니가 해줄게"의 "할머니" 등)은 인물 추출 대상이 아니다.
3. name에는 실제 이름 또는 가족 호칭(할아버지, 큰고모 등)만 쓴다. "SPK_1", "대화 상대", "상대방" 같은 라벨·자리표시자를 이름으로 쓰지 않는다 — 누구인지 모르면 persons가 아니라 unresolvedMentions에 넣는다.
3-1. **가족 정보의 인물과 매칭되면 name은 가족 정보의 정식 이름으로 쓴다** (예: 전사문에 "할아버지"로만 나와도 가족 정보의 배우자 "박철수"로 확인되면 name은 "박철수"). 전사 표기는 mentions에 남긴다.
4. 대화 상대(청자) 식별: **대상자가 상대를 부르는 이름·호칭**(예: 목록에 "하늘"이 있는데 전사문에 "하늘아", "하늘이니?" 같은 부름이 있음)이 전사문에 있고 가족 정보와 대조될 때만 상대를 인물로 확정한다. 상대가 대상자를 부르는 호칭("할머니", "엄마")은 상대의 세대를 짐작하게 할 뿐 신원 확정 근거가 아니다 — 이 경우 상대를 인물로 만들지 말고 생략한다. 상대의 발화 내용(근황 등)만으로 상대가 가족 중 누구라고 추측하지 않는다.
4-1. **호칭·관계어는 말한 사람 기준으로 해소한다.** 대화 상대가 말한 "엄마"는 그 상대의 엄마이고, 대상자가 말한 "네 엄마"는 상대의 엄마다. 누가 말했는지(화자 라벨)를 확인하고, 상대가 누구인지 먼저 판단한 뒤 그 기준으로 가족 정보와 대조한다. 상대가 누구인지 모르면 상대 기준 호칭들은 해소하지 말고 unresolvedMentions로 보낸다.
5. STT 오전사 감안: 전사문의 표기가 **사람을 부르거나 가리키는 자리**이고 가족 정보의 이름·호칭과 발음이 비슷하면 같은 인물로 본다 (예: 가족 정보에 손녀 "김하늘"이 있으면 전사문의 "하널아"는 "하늘아"의 오전사 가능 → 김하늘의 mention, confidence는 inferred). 일반 단어(음식, 사물 등)를 발음이 비슷하다는 이유로 이름으로 해석하지 않는다. 발음이 비슷하지 않으면 새 인물로 만들지 말고 unresolvedMentions로.
5-1. **mentions에는 전사문에 실제로 나온 표기를 그대로 쓴다** (예: 전사문이 "하널아"면 mentions도 "하널아"). 전사문에 없는 표현을 mentions에 넣지 않는다.
6. "여보세요", "하이루" 같은 인사말·감탄사는 인물도 지칭도 아니다.
7. 모든 항목에 근거 세그먼트 번호(sourceSegmentIds)를 붙인다. 근거를 댈 수 없으면 그 항목은 제외한다.
8. confidence: 발화에서 직접 확인되면 "confirmed", 문맥으로 추정하면 "inferred".
9. 지칭 해소: "걔", "네 엄마", "그 양반" 같은 표현이 가족 정보와 문맥으로 누구인지 분명하면 해당 인물의 mentions에 넣는다. 분명하지 않으면 절대 확정하지 말고 unresolvedMentions로 분리한다.
9-1. mentions에는 이름·호칭·특정인을 가리키는 표현만 넣는다. "너", "네가", "니가" 같은 2인칭 대명사와 "어" 같은 호응은 넣지 않는다.
9-2. **unresolvedMentions 자격**: 사람을 가리키는 것이 분명한데 신원만 불확실한 표현만 넣는다 (예: "그 양반", "숙모", 모르는 이름). 다음은 넣지 않는다 — 일반 대명사(나, 너, 그거, 저기), 감탄사·호응(어, 응), 사물·장소 지시어, "사람"·"애들"·"친구" 같은 불특정 일반 표현, 대상자 본인 지칭.
10. 관계(relationToSubject)는 대상자 기준 **단일 값**이다 (예: 아들, 손녀, 며느리). "A or B" 같은 복수 후보를 쓰지 말고, 하나로 확정할 수 없으면 null로 두고 confidence를 inferred로 한다.
11. facts: 그 인물에 대해 전사문에서 알 수 있는 사실(직장, 근황, 사건 등)을 짧은 문장으로. 누구에 대한 사실인지 애매하면(청자 얘기인지 제3자 얘기인지 등) 그 인물에 붙이지 말고 제외한다.
12. relationsToOthers: 인물끼리의 관계가 전사문에서 드러나면 기록한다 (예: A는 B의 배우자).
13. 해당 없는 배열은 빈 배열로 둔다. 억지로 채우지 않는다.

다음 JSON 형식으로만 답한다:
{
  "persons": [
    {
      "name": "이름 또는 대표 호칭",
      "relationToSubject": "아들 | 손녀 | ... | null",
      "mentions": ["전사문에 등장한 표현들"],
      "confidence": "confirmed | inferred",
      "sourceSegmentIds": [0, 3],
      "relationsToOthers": [
        { "name": "상대", "relation": "관계", "sourceSegmentIds": [5], "confidence": "confirmed" }
      ],
      "facts": [
        { "fact": "사실 문장", "sourceSegmentIds": [7], "confidence": "inferred" }
      ]
    }
  ],
  "unresolvedMentions": [
    { "mention": "그 양반", "context": "누구인지 불확실한 이유", "sourceSegmentIds": [12] }
  ]
}"""


def build_persons_user_prompt(
    segments: list[TranscriptSegment],
    subject_context: SubjectContext | None,
    subject_speaker_label: str | None,
    conversation_partner_name: str | None = None,
) -> str:
    parts: list[str] = []

    if subject_context is not None and subject_context.subject is not None:
        subject = subject_context.subject
        parts.append(
            f"대상자: {subject.name or '미상'} (가족 내 호칭: {subject.address_term or '미상'})"
        )
    if subject_speaker_label:
        parts.append(f"전사문에서 대상자의 화자 라벨: {subject_speaker_label}")
    if conversation_partner_name:
        parts.append(
            f"통화 상대(확정): 이 녹음에서 대상자와 통화한 상대는 '{conversation_partner_name}'이다. "
            f"대상자가 아닌 화자를 이 사람으로 확정해 persons에 포함하고(호격 근거가 없어도 이 값이 주어졌으므로 확정), "
            f"그 화자의 발화를 이 상대에게 귀속한다. 단, 전사문에서 제3자로만 언급되는 다른 이름을 이 상대에 갖다 붙이지 않는다."
        )

    if subject_context is not None and subject_context.family_members:
        lines = [
            f"- {member.name} ({member.relation_to_subject or '관계 미상'})"
            + (f", 대상자가 부르는 호칭: {', '.join(member.address_terms)}" if member.address_terms else "")
            for member in subject_context.family_members
        ]
        parts.append("가족 정보 (지칭 해소 힌트 — 언급 안 된 사람은 추출 금지):\n" + "\n".join(lines))

    transcript = "\n".join(
        f"{segment.segment_index} [{segment.speaker_label}]: "
        + (segment.corrected_text or segment.transcript_text)
        for segment in segments
    )
    parts.append(f"전사문:\n{transcript}")
    return "\n\n".join(parts)
