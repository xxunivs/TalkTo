"""2단계 민감정보 플래그 (재현율 우선, 구조 위반은 버리지 않고 예외 -> 재시도, 재실패 시 녹음 중단)"""

import logging

from app.external_api.openai_client import analysis_client, analysis_completion, json_reply
from app.schema.transcript import TranscriptSegment
from app.setting import Settings

logger = logging.getLogger(__name__)


class SensitivityValidationError(RuntimeError):
    """플래그 산출 구조 신뢰 불가 (녹음 진행 중단)"""


def run_sensitivity_analysis(
    segments: list[TranscriptSegment],
    *,
    settings: Settings,
) -> dict:

    client = analysis_client(settings, "sensitivity analysis")
    # 부분 실패 정책: 구조 위반이면 1회 재시도, 재실패 시 예외 (녹음 중단)
    last_error: SensitivityValidationError | None = None
    result = None
    for attempt in range(2):
        response = analysis_completion(
            client,
            "sensitivity analysis",
            model=settings.openai_analysis_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SENSITIVITY_SYSTEM_PROMPT},
                {"role": "user", "content": build_sensitivity_user_prompt(segments)},
            ],
        )
        payload = json_reply(response, "sensitivity analysis")
        try:
            result = validate_sensitivity_payload(payload, segments)
            break
        except SensitivityValidationError as error:
            last_error = error
            logger.warning("sensitivity attempt %d invalid: %s", attempt + 1, error)
    if result is None:
        raise last_error

    # 2차 판정은 유형 정의에 명백히 안 맞는 플래그만 제거 (애매하면 유지, 재현율은 1차, 정밀도는 2차)
    if result["sensitivityFlags"]:
        judge_response = analysis_completion(
            client,
            "sensitivity judge",
            model=settings.openai_analysis_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SENSITIVITY_JUDGE_PROMPT},
                {
                    "role": "user",
                    "content": build_sensitivity_judge_prompt(result["sensitivityFlags"], segments),
                },
            ],
        )
        judge_payload = json_reply(judge_response, "sensitivity judge")
        result = apply_judge_decisions(result, judge_payload)
    return result


def apply_judge_decisions(result: dict, judge_payload: dict) -> dict:
    """drop 판정 플래그만 제거 (판정 누락, 형식 오류는 keep)"""
    drops: dict[int, str] = {}
    for decision in judge_payload.get("decisions") or []:
        if not isinstance(decision, dict):
            continue
        index = decision.get("index")
        if isinstance(index, int) and decision.get("verdict") == "drop":
            drops[index] = str(decision.get("reason") or "").strip()

    kept, removed = [], []
    for i, flag in enumerate(result["sensitivityFlags"]):
        if i in drops:
            removed.append({**flag, "judgeReason": drops[i]})
        else:
            kept.append(flag)
    if removed:
        # 판정 이유는 원문을 담을 수 있어 로그에는 유형만
        logger.info(
            "sensitivity judge dropped %d flags: %s",
            len(removed),
            [f["type"] for f in removed],
        )
    return {"sensitivityFlags": kept, "judgeDropped": removed}


def validate_sensitivity_payload(payload: dict, segments: list[TranscriptSegment]) -> dict:
    """구조만 검증, 위반은 예외 (내용 필터링 금지, 오류 문구에 원문 없이 순번, 자료형, 개수만)"""
    valid_ids = {segment.segment_index for segment in segments}
    flags = []
    for n, flag in enumerate(payload.get("sensitivityFlags") or []):
        if not isinstance(flag, dict):
            raise SensitivityValidationError(f"flag {n} is not an object ({type(flag).__name__})")
        flag_type = flag.get("type")
        if flag_type not in SENSITIVITY_TYPES:
            raise SensitivityValidationError(
                f"flag {n} has unknown sensitivity type ({type(flag_type).__name__})"
            )
        description = str(flag.get("description") or "").strip()
        if not description:
            raise SensitivityValidationError(f"flag {n} description is empty")
        source_ids = flag.get("sourceSegmentIds")
        if not isinstance(source_ids, list) or not source_ids:
            raise SensitivityValidationError(f"flag {n} ({flag_type}) has no source ids")
        cleaned_ids = [i for i in source_ids if isinstance(i, int) and i in valid_ids]
        if not cleaned_ids:
            raise SensitivityValidationError(
                f"flag {n} ({flag_type}) cites nonexistent segments ({len(source_ids)} ids)"
            )
        flags.append(
            {
                "type": flag_type,
                "description": description,
                "sourceSegmentIds": sorted(set(cleaned_ids)),
            }
        )
    return {"sensitivityFlags": flags}


# ===== 프롬프트 =====
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


def build_sensitivity_judge_prompt(flags: list[dict], segments: list[TranscriptSegment]) -> str:
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
