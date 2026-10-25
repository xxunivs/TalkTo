"""OpenAI 클라이언트 팩토리 (용도별 시간 한도, 테스트는 이 모듈의 OpenAI 를 patch)"""

import json

import openai
from openai import OpenAI

from app.error import AnalysisBadOutputError, AnalysisTooLongError
from app.setting import Settings

# 같은 설정이면 클라이언트 재사용 (매번 새 TLS 연결 시 1턴 0.3~2초 증가, 2026-10-13 측정)
# 키에 OpenAI 클래스 포함: 테스트 patch 시 새 클라이언트 생성
_clients: dict[tuple, OpenAI] = {}


def create_openai_client(
    settings: Settings,
    purpose: str = "",
    *,
    timeout: float | None = None,
    max_retries: int | None = None,
) -> OpenAI:
    if settings.openai_api_key is None:
        detail = f" for {purpose}" if purpose else ""
        raise RuntimeError(f"OPENAI_API_KEY is required{detail}")
    timeout = settings.openai_timeout_seconds if timeout is None else timeout
    max_retries = settings.openai_max_retries if max_retries is None else max_retries
    key = (
        id(OpenAI),
        settings.openai_api_key.get_secret_value(),
        timeout,
        max_retries,
    )
    client = _clients.get(key)
    if client is None:
        client = OpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=timeout,
            max_retries=max_retries,
        )
        _clients[key] = client
    return client


def chat_client(settings: Settings, purpose: str = "") -> OpenAI:
    """대화 대답용 클라이언트 (시간 한도 x (재시도 + 1) < BE 45초, 기본 20초 x 2)"""
    return create_openai_client(
        settings,
        purpose,
        timeout=settings.openai_chat_timeout_seconds,
        max_retries=settings.openai_chat_max_retries,
    )


def analysis_client(settings: Settings, purpose: str = "") -> OpenAI:
    """녹음 분석, 통찰용 클라이언트 (긴 녹음은 답 하나에 60초 초과, 기본 300초)"""
    return create_openai_client(
        settings,
        purpose,
        timeout=settings.openai_analysis_timeout_seconds,
        max_retries=settings.openai_analysis_max_retries,
    )


def analysis_completion(client: OpenAI, stage: str, **kwargs):
    """분석 호출 1회 (시간 초과 -> ANALYSIS_TOO_LONG 422, BE 의 무의미한 재시도 방지)"""
    try:
        return client.chat.completions.create(**kwargs)
    except openai.APITimeoutError as error:
        raise AnalysisTooLongError(stage) from error


def json_reply(response, stage: str) -> dict:
    """분석 답 JSON 파싱 (길이 잘림 -> TOO_LONG, 형식 위반 -> BAD_OUTPUT)"""
    choice = response.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        raise AnalysisTooLongError(stage)
    try:
        payload = json.loads(choice.message.content or "{}")
    except json.JSONDecodeError as error:
        raise AnalysisBadOutputError(stage) from error
    if not isinstance(payload, dict):
        raise AnalysisBadOutputError(stage)
    return payload
