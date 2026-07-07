"""2단계 ⑤ 민감정보 플래그 — 안전 항목이라 실패 정책이 다르다.

인물 분석과 달리 재현율이 최우선이므로 코드 게이트는 구조(enum·근거 실존)만
검증하고 내용을 걸러내지 않는다. 구조 위반이 하나라도 있으면 조용히 버리는
대신 SensitivityValidationError를 던진다 — 상위에서 재시도하고, 그래도
실패하면 해당 녹음 전체를 중단한다 (부분 실패 정책).
"""

import json
import logging

from openai import OpenAI

from app.core.config import Settings
from app.pipeline.analysis.sensitivity_prompts import (
    SENSITIVITY_JUDGE_PROMPT,
    SENSITIVITY_SYSTEM_PROMPT,
    SENSITIVITY_TYPES,
    build_sensitivity_judge_prompt,
    build_sensitivity_user_prompt,
)
from app.schemas.transcript import TranscriptSegment

logger = logging.getLogger(__name__)


class SensitivityValidationError(RuntimeError):
    """플래그 산출 구조가 신뢰 불가 — 이 녹음은 진행하면 안 된다."""


def run_sensitivity_analysis(
    segments: list[TranscriptSegment],
    *,
    settings: Settings,
) -> dict:
    if settings.openai_api_key is None:
        raise RuntimeError("OPENAI_API_KEY is required for sensitivity analysis")

    client = OpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )
    response = client.chat.completions.create(
        model=settings.openai_analysis_model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SENSITIVITY_SYSTEM_PROMPT},
            {"role": "user", "content": build_sensitivity_user_prompt(segments)},
        ],
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    result = validate_sensitivity_payload(payload, segments)

    # R5 이중 체크: 2차 판정이 "유형 정의에 명백히 부합하지 않는" 플래그만
    # 제거한다. 애매하면 유지 — 재현율은 1차, 정밀도는 2차가 담당
    if result["sensitivityFlags"]:
        judge_response = client.chat.completions.create(
            model=settings.openai_analysis_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SENSITIVITY_JUDGE_PROMPT},
                {
                    "role": "user",
                    "content": build_sensitivity_judge_prompt(
                        result["sensitivityFlags"], segments
                    ),
                },
            ],
        )
        judge_payload = json.loads(judge_response.choices[0].message.content or "{}")
        result = apply_judge_decisions(result, judge_payload)
    return result


def apply_judge_decisions(result: dict, judge_payload: dict) -> dict:
    """drop 판정된 플래그만 제거. 판정 누락·형식 오류는 keep으로 처리(보수적)."""
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
        logger.info(
            "sensitivity judge dropped %d flags: %s",
            len(removed),
            [f"{f['type']}:{f['judgeReason']}" for f in removed],
        )
    return {"sensitivityFlags": kept, "judgeDropped": removed}


def validate_sensitivity_payload(
    payload: dict, segments: list[TranscriptSegment]
) -> dict:
    """구조 검증만 한다 — 내용 필터링은 안전 항목에선 금지.

    enum 밖의 type이나 실존하지 않는 근거는 "덜 플래그된 것"이 아니라
    "산출을 신뢰할 수 없는 것"이므로 예외로 올린다.
    """
    valid_ids = {segment.segment_index for segment in segments}
    flags = []
    for flag in payload.get("sensitivityFlags") or []:
        if not isinstance(flag, dict):
            raise SensitivityValidationError(f"flag is not an object: {flag!r}")
        flag_type = flag.get("type")
        if flag_type not in SENSITIVITY_TYPES:
            raise SensitivityValidationError(f"unknown sensitivity type: {flag_type!r}")
        description = str(flag.get("description") or "").strip()
        if not description:
            raise SensitivityValidationError("flag description is empty")
        source_ids = flag.get("sourceSegmentIds")
        if not isinstance(source_ids, list) or not source_ids:
            raise SensitivityValidationError(f"flag has no source ids: {description}")
        cleaned_ids = [i for i in source_ids if isinstance(i, int) and i in valid_ids]
        if not cleaned_ids:
            raise SensitivityValidationError(
                f"flag cites nonexistent segments: {description} ({source_ids})"
            )
        flags.append(
            {
                "type": flag_type,
                "description": description,
                "sourceSegmentIds": sorted(set(cleaned_ids)),
            }
        )
    return {"sensitivityFlags": flags}
