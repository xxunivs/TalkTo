"""OpenAI 클라이언트 팩토리.

여러 파이프라인 서비스가 같은 설정(api_key·timeout·retries)으로 클라이언트를
만들던 중복을 한곳으로 모은다. 테스트에서 목킹할 때는 이 모듈의 OpenAI를 patch한다
(`patch("app.providers.llm.OpenAI")`).
"""

from openai import OpenAI

from app.core.config import Settings


def create_openai_client(settings: Settings, purpose: str = "") -> OpenAI:
    if settings.openai_api_key is None:
        detail = f" for {purpose}" if purpose else ""
        raise RuntimeError(f"OPENAI_API_KEY is required{detail}")
    return OpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )
