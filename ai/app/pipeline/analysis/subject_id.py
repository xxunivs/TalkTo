"""대상자 화자 식별 — 의미 기반 폴백 (ECAPA 성문 참조가 없을 때).

참조 클립이 없으면 성문 매칭을 못 하므로, 전사문의 호칭·맥락을 LLM으로 읽어 '어느 화자가
대상자(고인)인지'를 판정한다. 기존의 '발화량 최다 화자' 추정을 대체한다 — 고인·상대 중
누가 더 말했는지는 신원 근거가 아니기 때문. 확정 못 하면 None(찍지 않음).
"""

import json
import logging

from app.core.config import Settings
from app.providers.llm import create_openai_client
from app.schemas.context import SubjectContext
from app.schemas.transcript import TranscriptSegment

logger = logging.getLogger(__name__)

SUBJECT_ID_SYSTEM_PROMPT = """너는 가족 통화 전사문에서 '대상자'가 어느 화자(speaker label)인지 판정하는 분석기다.

대상자는 이 녹음의 주인공(예: 할머니)이다. 판정 규칙:
1. 핵심 근거는 **호칭의 방향**이다. 한 화자가 다른 화자를 대상자의 호칭/이름으로 부르면(예: "할머니, 밥 먹었어?"), **불린 쪽**이 대상자다. 말한 쪽이 아니다.
2. 대상자가 스스로를 부르는 경우("할머니가 해줄게")도 있으니, 반드시 '말한 화자'와 '불린 대상'을 구분한다.
3. 대상자의 시점(자기 생애·건강·가족을 챙기는 말)은 **보조 근거일 뿐, 단독 근거가 될 수 없다.**
4. 발화량(누가 더 많이 말했나)·화제·말투만으로 대상자를 지목하지 마라.
5. **추측 금지.** 전사문에 대상자를 부르는 호칭이나 이름이 실제로 등장하고 그 방향으로 화자를 특정할 수 있을 때만 라벨을 반환한다. 그런 명시적 호칭·이름 근거가 없으면, 아무리 그럴듯해 보여도 subjectSpeakerLabel은 반드시 null이다.

출력은 JSON만: {"subjectSpeakerLabel": "SPK_0 | null", "confidence": "confirmed | inferred", "reason": "근거가 된 호칭·이름과 화자. 근거 없으면 null인 이유"}"""


def _build_user_prompt(
    segments: list[TranscriptSegment], subject_context: SubjectContext | None
) -> str:
    parts: list[str] = []
    subject = subject_context.subject if subject_context else None
    if subject and (subject.name or subject.address_term):
        parts.append(
            f"대상자: {subject.name or '미상'} (가족 내 호칭: {subject.address_term or '미상'})"
        )
    if subject_context and subject_context.family_members:
        members = "; ".join(
            f"{m.name}({m.relation_to_subject or '관계미상'}"
            + (f", 호칭 {'/'.join(m.address_terms)}" if m.address_terms else "")
            + ")"
            for m in subject_context.family_members
        )
        parts.append(f"가족 구성원: {members}")
    labels = sorted({s.speaker_label for s in segments})
    parts.append(f"등장 화자 라벨: {', '.join(labels)}")
    parts.append("전사문:")
    for s in segments:
        parts.append(f"[{s.speaker_label}] {s.transcript_text}")
    return "\n".join(parts)


def identify_subject_by_content(
    segments: list[TranscriptSegment],
    subject_context: SubjectContext | None,
    *,
    settings: Settings,
) -> str | None:
    """전사문 호칭·맥락으로 대상자 화자 라벨을 판정. 실패·미확정·API 오류는 None."""
    if not segments:
        return None
    valid_labels = {s.speaker_label for s in segments}
    try:
        client = create_openai_client(settings, "subject identification")
        response = client.chat.completions.create(
            model=settings.openai_analysis_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SUBJECT_ID_SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(segments, subject_context)},
            ],
        )
        payload = json.loads(response.choices[0].message.content or "{}")
    except Exception as error:  # LLM 오류가 분석을 깨지 않게 (미확정 진행)
        logger.warning("subject identification (content) failed: %s", error)
        return None
    label = payload.get("subjectSpeakerLabel")
    # 환각 방지: 실제 등장한 화자 라벨만 인정.
    if isinstance(label, str) and label in valid_labels:
        return label
    return None
