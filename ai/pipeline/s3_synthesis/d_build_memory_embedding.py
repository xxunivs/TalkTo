"""임베딩 생성 — 기억 조각 텍스트 → 벡터 (text-embedding-3-small).

검색·페르소나 RAG의 입력. 모델·차원은 env 주입(BE embeddings.dimensions
호환). 외부 API로 나가므로 입력은 redacted 텍스트를 쓴다.
"""

import json
import logging

from infra.setting import Settings
from infra.external_api.openai_client import create_openai_client
from infra.schema.embedding import (
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingResult,
    QueryEmbeddingRequest,
    QueryEmbeddingResponse,
)

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


# 세션 14 E6/E2① ask_rewrite 프롬프트(D7: 원래 검색기 실제 통화 92문항 근거 적중 51%→74%).
# 바꾼 것은 실험 페르소나의 이름·호칭 세 군데뿐(→ "어르신"·"대상자"). ask 는 받기만 하고 쓰지 않는다
# (잰 프롬프트와 같게 두려고 남김).
QUERY_REWRITE_PROMPT = """너는 통화 대화를 보고 판정만 한다. 대상자는 어르신이고, 가족이 대상자(AI)에게 말을 건다.

[앞 대화]
{history}

[가족의 이번 말]
{msg}

두 가지를 JSON 한 줄로만 답하라.
1) "ask": 이번 말이 대상자의 기억·경험·지식을 묻는 질문이면 true. 맞장구, 방금 들은 말을 되묻는 확인, 자기 근황 말하기, 인사, 혼잣말, 감탄이면 false.
2) "rewritten": ask 와 상관없이 항상 쓴다. 이번 말이 앞 대화 없이는 무엇을 묻는지 모르는 말(예: 앞 대화가 된장찌개 이야기이고 이번 말이 "얼마나 넣어?")이면, 앞 대화에서 빠진 화제·대상만 채워 혼자 읽어도 뜻이 통하는 한 문장으로 바꾼다(예: "된장찌개에 된장 얼마나 넣어?"). 앞 대화에 없는 내용은 더하지 않는다. 이미 혼자 뜻이 통하는 말이면 이번 말을 글자 그대로 쓴다.
형식: {{"ask": true, "rewritten": "..."}}
"""
REWRITE_HISTORY = 4  # 최근 메시지 수(E6 의 history[-4:])


def rewrite_query(request: QueryEmbeddingRequest, *, settings: Settings) -> str | None:
    """앞 대화로 질문을 완결된 한 문장으로. 실패하면 None(원문으로 검색). 검색 한 번을 막느니 원문이 낫다."""
    hist = "\n".join(f"{'가족' if m.role == 'user' else '대상자'}: {m.content}"
                     for m in request.history[-REWRITE_HISTORY:])
    prompt = QUERY_REWRITE_PROMPT.format(history=hist, msg=request.text)
    try:
        client = create_openai_client(settings, "query rewrite")
        response = client.chat.completions.create(
            model=settings.openai_analysis_model, messages=[{"role": "user", "content": prompt}])
        text = (response.choices[0].message.content or "").strip().strip("`").removeprefix("json").strip()
        rewritten = json.loads(text[text.find("{"):text.rfind("}") + 1]).get("rewritten")
    except Exception as e:  # noqa: BLE001 — API 오류·JSON 깨짐 모두 원문으로
        logger.warning("query rewrite failed, using original text: %s", type(e).__name__)
        return None
    if not isinstance(rewritten, str) or not rewritten.strip():
        logger.warning("query rewrite returned no text, using original text")
        return None
    return rewritten.strip()


def generate_query_embedding(
    request: QueryEmbeddingRequest, *, settings: Settings
) -> QueryEmbeddingResponse:
    """기억을 찾을 질문 하나를 벡터로. 기억 벡터화와 같은 모델을 써야 서로 견줄 수 있다.
    QUERY_REWRITE=on 이고 앞 대화가 있으면 다시 쓴 질문을 임베딩한다(답 생성에는 원문이 그대로 간다)."""
    rewritten = None
    if settings.query_rewrite == "on" and request.history:
        rewritten = rewrite_query(request, settings=settings)
    vector = embed_texts([rewritten or request.text], settings=settings)[0]
    return QueryEmbeddingResponse(
        provider=PROVIDER,
        model=settings.openai_embedding_model,
        dimensions=settings.openai_embedding_dimensions,
        embedding=vector,
        rewritten_query=rewritten,
    )
