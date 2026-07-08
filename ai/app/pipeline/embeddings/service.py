"""3-B 임베딩 생성 — 기억 조각 텍스트 → 벡터 (text-embedding-3-small).

검색·페르소나 RAG의 입력. 모델·차원은 env 주입(BE embeddings.dimensions
호환). 입력은 redacted 기준(외부 API 전송, 명세 MASK-002·003).
"""

import logging

from app.core.config import Settings
from app.providers.llm import create_openai_client

logger = logging.getLogger(__name__)


def embed_texts(texts: list[str], *, settings: Settings) -> list[list[float]]:
    if settings.openai_api_key is None:
        raise RuntimeError("OPENAI_API_KEY is required for embeddings")
    if not texts:
        return []

    client = create_openai_client(settings)
    response = client.embeddings.create(
        model=settings.openai_embedding_model,
        input=texts,
        dimensions=settings.openai_embedding_dimensions,
    )
    return [item.embedding for item in response.data]
