"""3-A 기억 조각 추출 프롬프트 (memory_segments).

전사문에서 검색·회상·페르소나 응답에 쓸 의미 단위 기억 문장을 만든다.
전사문 복사가 아니라 지칭이 해소된 자립적(self-contained) 문장이어야 하고,
모든 기억은 근거 세그먼트로 역추적 가능해야 한다 (공통 품질 원칙).
"""

from app.schemas.context import SubjectContext
from app.schemas.transcript import TranscriptSegment

MEMORY_SYSTEM_PROMPT = """너는 가족 통화 전사문에서 "기억 조각"을 추출하는 분석기다. 기억 조각은 나중에 가족이 검색하거나 대상자 페르소나가 회상할 재료다. **기억할 만한 내용은 빠짐없이 뽑는 것이 중요하다** — 사소해 보여도 음식·조언·근황·인사가 담기면 검색·회상의 재료가 된다.

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
2-0. **근거를 빠짐없이 인용한다**: 기억 문장의 각 정보 조각이 나온 세그먼트를 모두 넣는다. 예를 들어 "미역국을 못 먹었지만 케이크는 먹었다"면 미역국 언급 세그먼트와 케이크 언급 세그먼트를 둘 다 넣는다. 핵심 세그먼트 하나만 넣고 나머지를 빠뜨리지 않는다. (근거는 나중에 원본 재생·검증에 쓰이므로 누락되면 그 기억을 확인할 수 없다.)
2-1. **원문에 없는 구체 정보를 덧붙이지 않는다**: 지명·기관명(예: 병원 이름), 종류·분류(예: 무슨 주사인지), 수량·순서(예: '먼저')를 전사문이 명시하지 않으면 쓰지 않는다. 원문이 '정기적으로 보는 내과 의사'면 그대로 쓰고 특정 병원명을 만들지 않는다. 확실하지 않은 세부는 빼고 확인된 만큼만 서술한다.
3. 저장하지 않을 것은 최소한으로: 순수 맞장구(응, 그래), 통화 연결용 형식적 인사(여보세요 등), 순수 날씨 스몰톡, 같은 내용의 중복, 전사 오류로 의미 불명. **그 외 화제가 있는 발화는 웬만하면 기억으로 뽑는다.** 특히 놓치기 쉬운 것: 조리법·보관법·재료 고르는 법, 건강 조언·당부(예방주사 후 주의 등), 음식·선물 나눔, 인물 근황(직장·성취 등), 의미 있는 인사(새해 복·사랑한다).
4. confidence: 발화에서 직접 확인되면 "confirmed", 문맥상 추정이면 "inferred".
5. relatedPeople: 그 기억에 관련된 인물 이름 목록 (인물 정보의 이름 기준. 대상자 본인은 넣지 않는다).
6. 기억할 내용이 없으면 빈 배열을 반환한다. 억지로 만들지 않는다.
6-1. importance: 그 기억이 대상자를 이해하고 회상하는 데 얼마나 중요한지 1~10. 생애사·핵심 가치관·강한 감정·반복 습관은 높게(7~10), 일회성 사소한 사실은 낮게(1~4). 페르소나 reflection 트리거·Memories 필터에 쓰인다(검색 순위에는 쓰지 않는다).
6-2. tags: 아래 통제 어휘에서만 고른다 (여러 개 가능, 없으면 빈 배열).
   주제: 음식요리 / 건강병원 / 가족안부 / 명절기념일 / 날씨계절 / 신앙 / 일·학교 / 추억 / 장소고향
   생애시기: 유년 / 젊은시절 / 중년 / 노년 / 최근
   감정: 애정 / 그리움 / 걱정 / 기쁨 / 슬픔 / 유머
7. 하나의 기억은 하나의 사실·사건·조언만 담는다. 한 발화에 여러 포인트가 있으면(예: 조리법 + 보관법 + 재료 고르는 법) 각각 별도 기억으로 나눈다. 뭉뚱그려 하나로 합치지 않는다.
8. **주체를 정확히**: 그 일을 말한 사람(화자 라벨)을 확인해서, 상대방의 경험·행동을 대상자의 것으로 쓰지 않는다 (예: 상대가 "도시락 싸 간다"고 말했으면 그건 상대의 일이다).
9. **통화 상대(대상자가 아닌 화자)의 이름을 확정하는 조건은 단 하나**: 대상자가 그 상대를 특정 이름·호칭으로 **직접 부른(호격)** 근거가 전사문에 있을 때만이다. 그런 근거가 없으면 상대는 반드시 "상대"라고만 쓴다.
   - 대상자가 상대를 "너/니/네가"로만 부르고, 전사문의 이름들은 전부 제3자를 가리키는 언급이면 → 상대는 "상대"다. 언급된 그 이름을 상대에게 붙이지 않는다.
   - 예: 대상자가 "애들은 안 먹는다고. 준혁이도. 그래서 너도"라고 말하면, 준혁은 제3자이고 통화 상대(너)는 준혁이 아니다.
10. **상대의 일을 대상자의 일로 바꾸지 않는다**: 통화 상대의 경험·행동·의견은 상대가 이름 미확정이어도 "상대는 …"으로 쓴다. 이름을 못 붙인다고 해서 그 내용을 대상자(신금자)에게 옮기지 않는다.
   - 예: 상대가 "회사에서 도시락 싸 먹어"라고 말하면 → "상대는 회사에서 도시락을 싸서 먹는다" (신금자가 아니다. 신금자는 그 이야기를 들은 것이다.)
   - 누가 한 일인지 화자 라벨로 판단한다: 대상자 화자의 발화면 대상자의 일, 상대 화자의 발화면 상대의 일.
11. relatedPeople에는 이름이 확정된 인물만 넣는다. 통화 상대가 "상대"로 남으면 relatedPeople는 비운다.

다음 JSON 형식으로만 답한다:
{
  "memorySegments": [
    {
      "memoryText": "자립적인 기억 문장",
      "sourceSegmentIds": [3, 4],
      "relatedPeople": ["이름"],
      "confidence": "confirmed | inferred",
      "importance": 7,
      "tags": ["음식요리", "최근"]
    }
  ]
}"""


def build_memory_user_prompt(
    segments: list[TranscriptSegment],
    subject_context: SubjectContext | None,
    subject_speaker_label: str | None,
    persons_result: dict | None,
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
            f"규칙 9~11에서 '상대'로만 남기던 통화 상대를 이 이름으로 확정해, 그 상대의 발화·경험은 "
            f"'{conversation_partner_name}은(는) …'으로 귀속하고 relatedPeople에도 이 이름을 넣는다. "
            f"전사문에서 제3자로만 언급되는 다른 이름은 이 상대에 갖다 붙이지 않는다."
        )

    if persons_result and persons_result.get("persons"):
        lines = [
            f"- {person['name']} ({person.get('relationToSubject') or '관계 미상'})"
            + (f", 전사문 내 지칭: {', '.join(person['mentions'])}" if person.get("mentions") else "")
            for person in persons_result["persons"]
        ]
        parts.append("전사문에 언급된 인물 (제3자 언급일 수 있음 — 통화 상대와 동일인이라는 근거가 없으면 상대에 갖다 붙이지 말 것):\n" + "\n".join(lines))

    transcript = "\n".join(
        f"{segment.segment_index} [{segment.speaker_label}]: "
        + (segment.corrected_text or segment.transcript_text)
        for segment in segments
    )
    parts.append(f"전사문:\n{transcript}")
    return "\n\n".join(parts)
