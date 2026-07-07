"""3-C 파생 산출물 프롬프트 — 전체 요약 (녹음 1건 → 2~3문장).

Archive 목록/상세용. 요약은 전사문에서 확인되는 내용만(S1 근거율), 핵심 주제를
담되(S2), Archive 목록에 보이므로 민감플래그 내용을 직접 노출하지 않는다(S4).
태그는 3-A 기억들의 통제 어휘 태그를 녹음 단위로 집계(코드) — LLM 불필요.
"""

from app.schemas.transcript import TranscriptSegment

SUMMARY_SYSTEM_PROMPT = """너는 가족 통화 녹음을 Archive 목록에 보여줄 짧은 요약으로 만드는 분석기다.

규칙:
1. 2~3문장으로. 이 통화가 전반적으로 무엇에 관한 내용인지 보여준다.
2. 전사문에서 확인되는 내용만 쓴다. 없는 내용을 지어내지 않는다.
3. 핵심 주제(음식·건강·근황·추억 등)를 중심으로. 사소한 잡담은 뺀다.
4. 민감한 내용(구체적 병명·병원비·가족 갈등·재산)은 요약에 직접 노출하지 않는다. "건강 이야기를 나눴다" 수준으로만 (Archive 목록은 다른 가족도 볼 수 있다).
5. 담백한 서술체. 과장·감정 수식 없이.

다음 JSON으로만 답한다:
{"summary": "2~3문장 요약"}"""


def build_summary_user_prompt(segments: list[TranscriptSegment]) -> str:
    transcript = "\n".join(
        (segment.corrected_text or segment.transcript_text) for segment in segments
    )
    return f"전사문:\n{transcript}"
