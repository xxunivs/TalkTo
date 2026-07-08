"""3-B 임베딩 생성 — 기억 조각 텍스트 → 벡터 (text-embedding-3-small).

검색·페르소나 RAG의 입력. 모델·차원은 env 주입(BE embeddings.dimensions
호환). 입력은 redacted 기준(외부 API 전송, 명세 MASK-002·003).
"""

import logging

from app.core.config import Settings
from app.providers.llm import create_openai_client
from app.schemas.embeddings import EmbeddingRequest, EmbeddingResponse, EmbeddingResult

logger = logging.getLogger(__name__)

PROVIDER = "openai"


def embed_texts(texts: list[str], *, settings: Settings) -> list[list[float]]:
    if not texts:
        return []

    client = create_openai_client(settings, "embeddings")
    response = client.embeddings.create(
        model=settings.openai_embedding_model,
        input=texts,
        dimensions=settings.openai_embedding_dimensions,
    )
    return [item.embedding for item in response.data]


def generate_embeddings(
    request: EmbeddingRequest, *, settings: Settings
) -> EmbeddingResponse:
    """계약 엔드포인트 — memory segment 텍스트들을 배치 벡터화해 항목별로 되돌린다."""
    vectors = embed_texts([item.text for item in request.items], settings=settings)
    results = [
        EmbeddingResult(
            memory_segment_id=item.memory_segment_id,
            embedding_index=item.embedding_index,
            provider=PROVIDER,
            model=settings.openai_embedding_model,
            dimensions=settings.openai_embedding_dimensions,
            embedding=vector,
        )
        for item, vector in zip(request.items, vectors)
    ]
    return EmbeddingResponse(embeddings=results)
