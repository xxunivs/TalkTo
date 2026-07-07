"""3-A 기억 조각 추출 프롬프트 (memory_segments).

전사문에서 검색·회상·페르소나 응답에 쓸 의미 단위 기억 문장을 만든다.
전사문 복사가 아니라 지칭이 해소된 자립적(self-contained) 문장이어야 하고,
모든 기억은 근거 세그먼트로 역추적 가능해야 한다 (공통 품질 원칙).
"""

from app.schemas.context import SubjectContext
from app.schemas.transcript import TranscriptSegment

MEMORY_SYSTEM_PROMPT = """너는 가족 통화 전사문에서 "기억 조각"을 추출하는 분석기다. 기억 조각은 나중에 가족이 검색하거나 대상자 페르소나가 회상할 재료다.

저장할 만한 내용 유형:
- 가족관계·인물에 대한 사실 (예: 대상자의 아들이 회사를 옮겼다)
- 생애사·과거 경험 (예: 대상자는 어린 시절 바닷가 마을에서 살았다)
- 장소·음식 기억 (예: 대상자는 명절마다 만두를 직접 빚었다)
- 가치관·태도 (예: 대상자는 가족이 함께 밥 먹는 것을 중요하게 여긴다)
- 반복 습관 (예: 대상자는 전화를 끊기 전 항상 밥을 먹었는지 묻는다)
- 감정적으로 중요한 사건·근황 (예: 손주가 상을 받아 대상자가 기뻐했다)

규칙:
1. memoryText는 전사문을 복사하지 말고, 누가·무엇을 했는지가 문장만 봐도 이해되는 **자립적인 3인칭 서술**로 쓴다. 인물 정보가 주어지면 "걔", "아들" 같은 지칭 대신 그 인물의 이름을 쓴다.
2. 모든 기억에 근거 세그먼트 번호(sourceSegmentIds)를 붙인다. 전사문에서 직접 확인되는 내용만 쓴다.
2-1. **원문에 없는 구체 정보를 덧붙이지 않는다**: 지명·기관명(예: 병원 이름), 종류·분류(예: 무슨 주사인지), 수량·순서(예: '먼저')를 전사문이 명시하지 않으면 쓰지 않는다. 원문이 '정기적으로 보는 내과 의사'면 그대로 쓰고 특정 병원명을 만들지 않는다. 확실하지 않은 세부는 빼고 확인된 만큼만 서술한다.
3. 저장하지 않을 것: 단순 맞장구(응, 그래), 인사·통화 연결 잡담, 날씨 스몰톡, 같은 내용의 중복, 전사 오류로 의미가 불분명한 부분.
4. confidence: 발화에서 직접 확인되면 "confirmed", 문맥상 추정이면 "inferred".
5. relatedPeople: 그 기억에 관련된 인물 이름 목록 (인물 정보의 이름 기준. 대상자 본인은 넣지 않는다).
6. 기억할 내용이 없으면 빈 배열을 반환한다. 억지로 만들지 않는다.
7. 하나의 기억은 하나의 사실·사건만 담는다. 여러 사실이 섞이면 나눈다.
8. **주체를 정확히**: 그 일을 말한 사람(화자 라벨)을 확인해서, 상대방의 경험·행동을 대상자의 것으로 쓰지 않는다 (예: 상대가 "도시락 싸 간다"고 말했으면 그건 상대의 일이다).

다음 JSON 형식으로만 답한다:
{
  "memorySegments": [
    {
      "memoryText": "자립적인 기억 문장",
      "sourceSegmentIds": [3, 4],
      "relatedPeople": ["이름"],
      "confidence": "confirmed | inferred"
    }
  ]
}"""


def build_memory_user_prompt(
    segments: list[TranscriptSegment],
    subject_context: SubjectContext | None,
    subject_speaker_label: str | None,
    persons_result: dict | None,
) -> str:
    parts: list[str] = []

    if subject_context is not None and subject_context.subject is not None:
        subject = subject_context.subject
        parts.append(
            f"대상자: {subject.name or '미상'} (가족 내 호칭: {subject.address_term or '미상'})"
        )
    if subject_speaker_label:
        parts.append(f"전사문에서 대상자의 화자 라벨: {subject_speaker_label}")

    if persons_result and persons_result.get("persons"):
        lines = [
            f"- {person['name']} ({person.get('relationToSubject') or '관계 미상'})"
            + (f", 전사문 내 지칭: {', '.join(person['mentions'])}" if person.get("mentions") else "")
            for person in persons_result["persons"]
        ]
        parts.append("인물 정보 (지칭 해소 결과 — memoryText에는 이 이름을 사용):\n" + "\n".join(lines))

    transcript = "\n".join(
        f"{segment.segment_index} [{segment.speaker_label}]: "
        + (segment.corrected_text or segment.transcript_text)
        for segment in segments
    )
    parts.append(f"전사문:\n{transcript}")
    return "\n\n".join(parts)
