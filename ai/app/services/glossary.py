"""보정·화자식별용 용어 파생 — subjectContext/intakeContext에서 자동 추출.

용어의 원천은 사용자가 앱에 입력한 데이터(프로필, 가족 구성원, 가족 용어집,
Intake 이름 리스트)이고, 이 모듈은 그 구조화 데이터를 평평한 단어 목록으로
조합만 한다. 특정 가족에 대한 하드코딩은 없다.
"""

import re

from app.schemas.context import IntakeContext, SubjectContext

_TRIM = ".,?!~… "
# 성+이름 형태의 한글 실명 (2~4자) — 성을 뗀 이름만으로 부르는 게 일반적이라
# 호칭 보정에는 이름 변형도 필요하다 (이종서 → 종서, 남향 → 향)
_KOREAN_FULL_NAME = re.compile(r"^[가-힣]{2,4}$")


def _with_given_name(full_name: str) -> list[str]:
    if _KOREAN_FULL_NAME.fullmatch(full_name):
        return [full_name, full_name[1:]]
    return [full_name]


def build_glossary(
    subject_context: SubjectContext | None,
    intake_context: IntakeContext | None = None,
) -> list[str]:
    terms: list[str] = []

    if subject_context is not None:
        if subject_context.subject and subject_context.subject.name:
            terms.extend(_with_given_name(subject_context.subject.name))
        for member in subject_context.family_members:
            terms.extend(_with_given_name(member.name))
            terms.extend(member.address_terms)
        terms.extend(subject_context.glossary_terms)

    if intake_context is not None and intake_context.stt_hints is not None:
        for name in intake_context.stt_hints.names:
            terms.extend(_with_given_name(name))

    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        cleaned = term.strip(_TRIM)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result
