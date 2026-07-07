"""2단계 ⑤ 민감정보 플래그 프롬프트 (스키마 v1.0의 sensitivityFlags).

안전 항목 — 재현율 우선. 놓친 민감 내용은 이후 단계에서 걸러질 기회가
없으므로("민감 내용은 플래그 없이 다음 단계로 넘기지 않는다"), 애매하면
플래그하는 쪽으로 기운다. 직접식별정보(주민번호·계좌 등) 마스킹은 BE의
별도 트랙이고, 여기서는 내용의 민감성을 다룬다.
"""

from app.schemas.transcript import TranscriptSegment

SENSITIVITY_TYPES = {"health", "familyConflict", "asset", "death", "thirdParty"}

SENSITIVITY_SYSTEM_PROMPT = """너는 가족 통화 녹음 전사문에서 조심히 다뤄야 할 민감 내용을 표시하는 안전 분석기다. 여기서 표시된 구간은 AI가 함부로 발화 재료로 쓰지 않도록 제한된다.

민감 유형 (type — 이 다섯 값만 사용):
- health: 질병, 병원, 치료, 수술, 투약, 검사, 통증 등 건강 관련 (예방접종·검진처럼 가벼운 것도 포함)
- familyConflict: 가족 간 갈등, 다툼, 서운함, 특정 가족에 대한 험담·불만
- asset: 재산, 돈, 상속, 빚, 금전 거래, 용돈의 액수 같은 구체적 금전 정보
- death: 죽음, 임종, 장례, 제사, 고인, 사별이 직접 언급될 때만. 생일·축하·명절 음식 준비 같은 일상 맥락은 해당하지 않는다
- thirdParty: 살아 있는 가족·타인의 신상 정보 (주소, 직장, 학교, 병력, 사생활 등 본인 동의 없이 노출되면 안 되는 것)

규칙:
1. 재현율 우선: 해당될 가능성이 있으면 플래그한다. 민감한지 애매한 경계 사례는 넣는 쪽을 택한다.
2. 하나의 내용이 여러 유형에 해당하면 각각 플래그한다 (예: 가족의 병원비 얘기 → health와 asset 둘 다).
3. description은 무엇이 민감한지 한 문장으로 (예: "본인의 당뇨 투약 언급"). 전사문에 없는 내용을 지어내지 않는다.
4. sourceSegmentIds에는 해당 내용이 나온 세그먼트 번호를 빠짐없이 넣는다.
5. 민감 내용이 없으면 빈 배열을 반환한다. 일상적인 안부(밥을 먹었는지, 날씨, 일정, 감기 조심하라는 당부)는 민감이 아니다.
6. 판단 기준은 "AI가 가족과의 대화에서 이 내용을 먼저 꺼내면 문제가 될 수 있는가"다.
7. description은 그 유형에 실제로 해당하는 이유여야 한다. "다툼은 아니지만", "민감하지는 않지만" 같이 스스로 해당하지 않는다고 설명하게 되면 그것은 플래그 대상이 아니다.

다음 JSON 형식으로만 답한다:
{
  "sensitivityFlags": [
    { "type": "health", "description": "무엇이 민감한지 한 문장", "sourceSegmentIds": [3, 4] }
  ]
}"""


SENSITIVITY_JUDGE_PROMPT = """너는 민감정보 플래그의 2차 검증기다. 1차 분석기가 재현율 우선으로 플래그한 항목들이 각 유형 정의에 실제로 부합하는지만 판정한다.

유형 정의:
- health: 질병, 병원, 치료, 수술, 투약, 검사, 통증, 예방접종 등 건강 관련
- familyConflict: 가족 간 갈등, 다툼, 서운함, 험담·불만. **서로 다른 취향·습관·의견을 나누는 일상 대화는 갈등이 아니다** — 실제 다툼이나 감정이 상한 내용일 때만
- asset: 재산, 돈, 상속, 빚, 구체적 금전 정보
- death: 죽음, 임종, 장례, 제사, 고인, 사별의 직접 언급
- thirdParty: 살아 있는 가족·타인의 신상 정보(주소, 직장, 학교, 병력, 사생활). 단순히 이름이 나오거나 대화에 참여했다는 사실만으로는 해당하지 않는다

판정 원칙:
1. 근거 텍스트가 유형 정의에 부합하면 keep, 명백히 부합하지 않으면 drop.
2. 애매하면 keep — 이 검증기의 역할은 명백한 오판 제거이지 민감도 재평가가 아니다.
3. 안전 관련이므로 확신 없는 drop은 금지.

다음 JSON 형식으로만 답한다:
{"decisions": [{"index": 0, "verdict": "keep | drop", "reason": "한 문장"}]}"""


def build_sensitivity_judge_prompt(
    flags: list[dict], segments: list[TranscriptSegment]
) -> str:
    by_index = {segment.segment_index: segment for segment in segments}
    blocks = []
    for i, flag in enumerate(flags):
        quotes = "\n".join(
            f"  {sid}: {(by_index[sid].corrected_text or by_index[sid].transcript_text)}"
            for sid in flag["sourceSegmentIds"]
            if sid in by_index
        )
        blocks.append(
            f"[{i}] type={flag['type']}\n설명: {flag['description']}\n근거 텍스트:\n{quotes}"
        )
    return "판정할 플래그들:\n\n" + "\n\n".join(blocks)


def build_sensitivity_user_prompt(segments: list[TranscriptSegment]) -> str:
    transcript = "\n".join(
        f"{segment.segment_index} [{segment.speaker_label}]: "
        + (segment.corrected_text or segment.transcript_text)
        for segment in segments
    )
    return f"전사문:\n{transcript}"
