"""Glossary-based transcript correction pass.

Fixes proper nouns the STT misheard (e.g. "연아" → "윤아") using the family
glossary. Corrections never touch meaning: only glossary terms are corrected,
originals stay untouched, and uncertain cases are flagged needs_review instead
of edited.
"""

import json
import logging
import re

from openai import OpenAI  # 타입 힌트용

from infra.setting import Settings
from infra.external_api.openai_client import create_openai_client
from infra.schema.subject import IntakeContext, SubjectContext
from infra.schema.transcript import TranscriptSegment

logger = logging.getLogger(__name__)

CHUNK_SIZE = 80
# a name fix barely changes length; big deltas mean the model rewrote content
_LENGTH_RATIO_BOUNDS = (0.5, 1.5)

_CHOSEONG = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNGSEONG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONGSEONG = [""] + list("ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ")

_SYSTEM_PROMPT = """너는 한국어 통화 전사 교정기다. 고유명사 목록(가족 이름, 지명, 음식 등)을 참고해 전사문에서 잘못 받아쓴 고유명사만 교정한다.

규칙:
1. 목록의 단어와 발음이 유사하게 잘못 전사된 부분만 해당 단어로 교정한다 (호칭 활용형 포함: 목록에 "윤"이 있으면 "연아"는 "윤아"로).
2. 문맥 조건: 그 자리가 실제로 사람을 부르거나 사람·지명을 가리키는 자리일 때만 교정한다. 발음이 비슷하다는 이유만으로 일반 단어·사투리를 이름으로 바꾸지 않는다.
3. 그 외의 표현, 문법, 어미, 내용은 절대 바꾸지 않는다.
4. 확신이 없으면 교정하지 말고 needsReview 배열에 세그먼트 번호를 넣는다.
5. 원문에 없는 말을 만들지 않는다.

예시 (목록에 "하늘"이 있다고 가정):
- "어, 하널아. 잘 있었냐" → "어, 하늘아. 잘 있었냐" (부르는 자리 + 발음 유사, 교정 O)
- "그려유. 잘 자요." → 교정하지 않는다 ("그려유"는 사투리 표현이지 이름이 아님. 애매하면 needsReview)

다음 JSON 형식으로만 답한다:
{"corrections": [{"segmentIndex": 0, "correctedText": "교정된 전체 문장"}], "needsReview": [3]}
교정할 것이 없으면 {"corrections": [], "needsReview": []}"""


def correct_segments(
    segments: list[TranscriptSegment],
    *,
    glossary: list[str],
    settings: Settings,
) -> list[TranscriptSegment]:
    """Return segments with corrected_text/needs_review filled in place."""
    if not glossary or not segments:
        return segments
    if settings.openai_api_key is None:
        logger.warning("correction pass skipped: OPENAI_API_KEY not set")
        return segments

    client = create_openai_client(settings)
    for start in range(0, len(segments), CHUNK_SIZE):
        chunk = segments[start : start + CHUNK_SIZE]
        try:
            _correct_chunk(client, chunk, glossary=glossary, settings=settings)
        except Exception:
            # 보정은 부가 패스 — 실패해도 전사 자체는 원문으로 진행한다
            logger.exception(
                "correction pass failed for segments %s-%s; returning originals",
                chunk[0].segment_index,
                chunk[-1].segment_index,
            )
    return segments


def _correct_chunk(
    client: OpenAI,
    chunk: list[TranscriptSegment],
    *,
    glossary: list[str],
    settings: Settings,
) -> None:
    lines = "\n".join(
        f"{segment.segment_index}: {segment.transcript_text}" for segment in chunk
    )
    user_prompt = (
        f"고유명사 목록: {', '.join(glossary)}\n\n전사문:\n{lines}"
    )
    response = client.chat.completions.create(
        model=settings.openai_correction_model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    apply_corrections(chunk, payload, max_jamo_ratio=settings.correction_max_jamo_ratio)


def apply_corrections(
    chunk: list[TranscriptSegment],
    payload: dict,
    *,
    max_jamo_ratio: float = 0.45,
) -> None:
    by_index = {segment.segment_index: segment for segment in chunk}

    for correction in payload.get("corrections") or []:
        if not isinstance(correction, dict):
            continue
        segment = by_index.get(correction.get("segmentIndex"))
        corrected = correction.get("correctedText")
        if segment is None or not isinstance(corrected, str) or not corrected.strip():
            continue
        corrected = corrected.strip()
        if corrected == segment.transcript_text:
            continue
        ratio = len(corrected) / max(len(segment.transcript_text), 1)
        if not _LENGTH_RATIO_BOUNDS[0] <= ratio <= _LENGTH_RATIO_BOUNDS[1]:
            segment.needs_review = True
            continue
        if not _phonetically_plausible(
            segment.transcript_text, corrected, max_jamo_ratio
        ):
            segment.needs_review = True
            continue
        segment.corrected_text = corrected

    for index in payload.get("needsReview") or []:
        segment = by_index.get(index)
        if segment is not None:
            segment.needs_review = True


def _phonetically_plausible(
    original: str, corrected: str, max_jamo_ratio: float
) -> bool:
    """모든 교체 단어 쌍이 발음(자모) 수준에서 유사할 때만 True."""
    original_tokens = original.split()
    corrected_tokens = corrected.split()
    if len(original_tokens) != len(corrected_tokens):
        return False
    for before, after in zip(original_tokens, corrected_tokens):
        if before == after:
            continue
        before_jamo = _to_jamo(before.strip(".,?!~…"))
        after_jamo = _to_jamo(after.strip(".,?!~…"))
        longest = max(len(before_jamo), len(after_jamo), 1)
        if _edit_distance(before_jamo, after_jamo) / longest > max_jamo_ratio:
            return False
    return True


def _to_jamo(text: str) -> str:
    parts = []
    for char in text:
        code = ord(char)
        if 0xAC00 <= code <= 0xD7A3:
            offset = code - 0xAC00
            parts.append(_CHOSEONG[offset // 588])
            parts.append(_JUNGSEONG[(offset % 588) // 28])
            parts.append(_JONGSEONG[offset % 28])
        else:
            parts.append(char)
    return "".join(parts)


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i] + [0] * len(right)
        for j, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
        previous = current
    return previous[-1]


# ===== 용어집 파생 (교정·화자식별 재료) =====
# 용어의 원천은 사용자가 앱에 입력한 데이터(프로필, 가족 구성원, 가족 용어집,
# Intake 이름 리스트). 이 함수들은 그 구조화 데이터를 평평한 단어 목록으로
# 조합만 한다. 특정 가족에 대한 하드코딩은 없다.

_TRIM = ".,?!~… "
# 성+이름 형태의 한글 실명 (2~4자) — 성을 뗀 이름만으로 부르는 게 일반적이라
# 호칭 보정에는 이름 변형도 필요하다 (이정우 → 정우, 남윤 → 윤)
_KOREAN_FULL_NAME = re.compile(r"^[가-힣]{2,4}$")


def _has_batchim(char: str) -> bool:
    code = ord(char)
    return 0xAC00 <= code <= 0xD7A3 and (code - 0xAC00) % 28 != 0


def _vocative(name: str) -> str:
    """이름을 부르는 형태 (한국어 호격: 받침 있으면 '아', 없으면 '야')."""
    return name + ("아" if _has_batchim(name[-1]) else "야")


def _with_given_name(full_name: str) -> list[str]:
    if _KOREAN_FULL_NAME.fullmatch(full_name):
        given = full_name[1:]
        return [full_name, given, _vocative(given)]
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
