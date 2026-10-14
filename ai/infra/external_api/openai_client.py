"""OpenAI 클라이언트 팩토리.

파이프라인 서비스들이 같은 설정(api_key·timeout·retries)으로 클라이언트를
만들도록 한곳에 둔다. 테스트에서 목킹할 때는 이 모듈의 OpenAI를 patch한다
(`patch("infra.external_api.openai_client.OpenAI")`).
"""

from openai import OpenAI

from infra.setting import Settings


# 같은 설정이면 클라이언트(연결 묶음)를 다시 쓴다. 매번 새로 만들면 요청마다 OpenAI 와 새로
# 연결(TLS)을 맺어 1턴이 0.3~2초 더 걸렸다(2026-10-13 측정). 키는 OpenAI 클래스까지 넣어,
# 테스트가 이 모듈의 OpenAI 를 patch 하면 새 클라이언트를 만든다.
_clients: dict[tuple, OpenAI] = {}


def create_openai_client(settings: Settings, purpose: str = "") -> OpenAI:
    if settings.openai_api_key is None:
        detail = f" for {purpose}" if purpose else ""
        raise RuntimeError(f"OPENAI_API_KEY is required{detail}")
    key = (
        id(OpenAI),
        settings.openai_api_key.get_secret_value(),
        settings.openai_timeout_seconds,
        settings.openai_max_retries,
    )
    client = _clients.get(key)
    if client is None:
        client = OpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
        )
        _clients[key] = client
    return client
