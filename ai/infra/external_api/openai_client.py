"""OpenAI 클라이언트 팩토리.

파이프라인 서비스들이 같은 설정(api_key·timeout·retries)으로 클라이언트를
만들도록 한곳에 둔다. 테스트에서 목킹할 때는 이 모듈의 OpenAI를 patch한다
(`patch("infra.external_api.openai_client.OpenAI")`).

시간 한도는 쓰는 곳마다 다르다.
- chat_client: 대화 대답. BE 가 45초에 끊으므로 그 안에 끝나거나 포기한다.
- analysis_client: 녹음 분석·통찰. 긴 녹음은 답 하나에 60초가 넘는다(BE 는 30분까지 기다린다).
- create_openai_client: 그 밖(기본 OPENAI_TIMEOUT_SECONDS·OPENAI_MAX_RETRIES).
"""

import json

import openai
from openai import OpenAI

from infra.error import AnalysisBadOutputError, AnalysisTooLongError
from infra.setting import Settings

# 같은 설정이면 클라이언트(연결 묶음)를 다시 쓴다. 매번 새로 만들면 요청마다 OpenAI 와 새로
# 연결(TLS)을 맺어 1턴이 0.3~2초 더 걸렸다(2026-10-13 측정). 키는 OpenAI 클래스까지 넣어,
# 테스트가 이 모듈의 OpenAI 를 patch 하면 새 클라이언트를 만든다.
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
    """대화 대답용. 시간 한도 × (다시 하기 + 1) 이 BE 의 45초보다 짧다(기본 20초 × 2번)."""
    return create_openai_client(
        settings,
        purpose,
        timeout=settings.openai_chat_timeout_seconds,
        max_retries=settings.openai_chat_max_retries,
    )


def analysis_client(settings: Settings, purpose: str = "") -> OpenAI:
    """녹음 분석·통찰용. 긴 녹음의 답 하나가 기본 60초를 넘겨 매번 실패하던 것을 막는다(기본 300초)."""
    return create_openai_client(
        settings,
        purpose,
        timeout=settings.openai_analysis_timeout_seconds,
        max_retries=settings.openai_analysis_max_retries,
    )


def analysis_completion(client: OpenAI, stage: str, **kwargs):
    """분석 호출 한 번. 시간 안에 못 끝나면 코드가 있는 오류(ANALYSIS_TOO_LONG, 422)로 바꾼다.

    같은 녹음은 다시 해도 같은 결과라, 이름 없는 500 으로 BE 가 같은 일을 되풀이하지 않게 한다.
    """
    try:
        return client.chat.completions.create(**kwargs)
    except openai.APITimeoutError as error:
        raise AnalysisTooLongError(stage) from error


def json_reply(response, stage: str) -> dict:
    """분석 답(JSON 한 덩어리)을 읽는다. 길이 한도에서 잘렸거나 JSON 이 아니면 코드가 있는 오류로."""
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
