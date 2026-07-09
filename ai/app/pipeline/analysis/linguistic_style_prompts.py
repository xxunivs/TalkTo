"""2단계 ④ 언어 스타일·감정 표현 — 대상자의 말투를 근거와 함께 추출.

대상자(고인) 발화에서 반복 말버릇·호칭 패턴·문장 스타일·감정 표현 방식을 뽑는다.
페르소나 말투 few-shot과 speechStyle 슬롯의 재료가 된다. 지어내지 않고 실제 발화에
근거를 붙인다(sentencePatterns는 관찰 일반화라 근거 없음).
"""

from app.schemas.context import SubjectContext
from app.schemas.transcript import TranscriptSegment

LINGUISTIC_STYLE_SYSTEM_PROMPT = """너는 가족 통화 전사문에서 **대상자의 말투(언어 스타일)**를 분석하는 분석기다. 목적은 대상자가 "어떻게 말하는지"를 재현할 재료를 뽑는 것이다 — 무슨 말을 했는지(내용)가 아니라 어떻게 말하는지(형식·습관·감정 표현)에 집중한다.

**오직 대상자의 발화만** 본다(주어진 화자 라벨 기준). 통화 상대의 말투는 뽑지 않는다.

추출 항목:
- recurringPhrases: 대상자가 반복해 쓰는 말버릇·입버릇·감탄사·접속 습관(예: "그러니끼", "아이고", "~잖냐"). 특징적이거나 두 번 이상 나온 것.
- addressTerms: 대상자가 특정 인물을 **부르는 호칭**(호격). 대상자가 실제로 그렇게 부른 근거가 있을 때만(예: "준혁아", "우리 강아지").
- sentencePatterns: 문장 스타일의 일반화(예: "짧은 단문", "접속어 없이 화제 전환", "끝을 흐리는 말끝", "되묻는 습관"). 관찰 요약이라 근거 세그먼트를 붙이지 않는다.
- emotionalExpressions: 감정을 드러내는 특유의 방식(예: 애정을 "밥은 먹었냐"로 표현, 걱정을 "몸조심해라"로). emotion + 실제 표현.

규칙:
1. 지어내지 않는다. 전사문에서 직접 확인되는 것만 쓴다. 없으면 빈 배열/목록.
2. recurringPhrases·addressTerms·emotionalExpressions에는 근거 세그먼트 번호(sourceSegmentIds)를 붙인다. sentencePatterns에는 붙이지 않는다.
3. 대상자 발화가 아닌 것(상대 발화, 전사 오류)에서 뽑지 않는다.
4. 내용 요약·사실이 아니라 **말하는 방식**만 뽑는다(무엇을 좋아하는지는 여기 넣지 않는다).

다음 JSON 형식으로만 답한다:
{
  "linguisticStyle": {
    "recurringPhrases": [{ "phrase": "그러니끼", "sourceSegmentIds": [3, 17] }],
    "addressTerms": [{ "person": "준혁", "term": "준혁아", "sourceSegmentIds": [5] }],
    "sentencePatterns": ["짧은 단문", "접속어 없는 화제 전환"],
    "emotionalExpressions": [{ "emotion": "애정", "expression": "밥은 먹었냐", "sourceSegmentIds": [8] }]
  }
}"""


def build_linguistic_style_user_prompt(
    segments: list[TranscriptSegment],
    subject_context: SubjectContext | None,
    subject_speaker_label: str | None,
) -> str:
    parts: list[str] = []

    if subject_context is not None and subject_context.subject is not None:
        subject = subject_context.subject
        parts.append(
            f"대상자: {subject.name or '미상'} (가족 내 호칭: {subject.address_term or '미상'})"
        )
    if subject_speaker_label:
        parts.append(
            f"전사문에서 대상자의 화자 라벨: {subject_speaker_label} "
            f"— 이 화자의 발화에서만 말투를 뽑는다."
        )

    transcript = "\n".join(
        f"{segment.segment_index} [{segment.speaker_label}]: "
        + (segment.corrected_text or segment.transcript_text)
        for segment in segments
    )
    parts.append(f"전사문:\n{transcript}")
    return "\n\n".join(parts)
