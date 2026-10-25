"""임베딩 생성 — 기억 조각 텍스트 → 벡터 (text-embedding-3-small).

검색·페르소나 RAG의 입력. 모델·차원은 env 주입(BE embeddings.dimensions
호환). 외부 API로 나가므로 입력은 redacted 텍스트를 쓴다.
"""

import json
import logging

from infra.error import AppError
from infra.external_api.openai_client import create_openai_client
from infra.schema.embedding import (
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingResult,
    QueryEmbeddingRequest,
    QueryEmbeddingResponse,
)
from infra.setting import Settings

logger = logging.getLogger(__name__)

PROVIDER = "openai"

# 한 번에 보내는 양. OpenAI 는 요청 하나에 2,048개·약 30만 토큰까지 받는다. 기억이 많은 대화 상대(긴 녹음
# 여러 개)는 한 번에 다 보내면 400 으로 거절돼, 녹음 분석까지 다 마친 뒤 마지막 단계에서 실패했다.
# 글자 상한은 한글이 글자당 토큰을 더 쓰는 경우를 넉넉히 본 값이다.
EMBED_BATCH_ITEMS = 256
EMBED_BATCH_CHARS = 60_000
# 대화 중 기억 찾기(/v1/embeddings/query). BE 는 45초에 끊는다: 질문 다시 쓰기(8초) + 임베딩(15초 × 2번) < 45초.
QUERY_EMBED_TIMEOUT_SECONDS = 15.0
QUERY_EMBED_MAX_RETRIES = 1
REWRITE_TIMEOUT_SECONDS = 8.0


def _batches(texts: list[str]) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    size = 0
    for text in texts:
        if current and (len(current) >= EMBED_BATCH_ITEMS or size + len(text) > EMBED_BATCH_CHARS):
            batches.append(current)
            current, size = [], 0
        current.append(text)
        size += len(text)
    if current:
        batches.append(current)
    return batches


def embed_texts(
    texts: list[str], *, settings: Settings, interactive: bool = False
) -> list[list[float]]:
    """글마다 벡터 하나(순서 그대로). 많으면 나눠 보낸다. interactive 면 대화 중 시간 한도를 쓴다."""
    if not texts:
        return []

    if interactive:
        client = create_openai_client(
            settings,
            "query embedding",
            timeout=QUERY_EMBED_TIMEOUT_SECONDS,
            max_retries=QUERY_EMBED_MAX_RETRIES,
        )
    else:
        client = create_openai_client(settings, "embeddings")
    vectors: list[list[float]] = []
    for batch in _batches(texts):
        response = client.embeddings.create(
            model=settings.openai_embedding_model,
            input=batch,
            dimensions=settings.openai_embedding_dimensions,
        )
        got = [item.embedding for item in response.data]
        if len(got) != len(batch):  # 개수가 어긋나면 벡터가 엉뚱한 기억에 붙는다
            raise AppError(
                code="EMBEDDING_COUNT_MISMATCH",
                message=f"Embedding provider returned {len(got)} vectors for {len(batch)} inputs.",
                status_code=502,
            )
        vectors.extend(got)
    return vectors


def generate_embeddings(request: EmbeddingRequest, *, settings: Settings) -> EmbeddingResponse:
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
    hist = "\n".join(
        f"{'가족' if m.role == 'user' else '대상자'}: {m.content}"
        for m in request.history[-REWRITE_HISTORY:]
    )
    prompt = QUERY_REWRITE_PROMPT.format(history=hist, msg=request.text)
    try:
        # 대화 중이라 짧게 한 번만. 못 끝내면 원문으로 찾는다(아래 except)
        client = create_openai_client(
            settings, "query rewrite", timeout=REWRITE_TIMEOUT_SECONDS, max_retries=0
        )
        response = client.chat.completions.create(
            model=settings.openai_analysis_model, messages=[{"role": "user", "content": prompt}]
        )
        text = (
            (response.choices[0].message.content or "")
            .strip()
            .strip("`")
            .removeprefix("json")
            .strip()
        )
        rewritten = json.loads(text[text.find("{") : text.rfind("}") + 1]).get("rewritten")
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
    vector = embed_texts([rewritten or request.text], settings=settings, interactive=True)[0]
    return QueryEmbeddingResponse(
        provider=PROVIDER,
        model=settings.openai_embedding_model,
        dimensions=settings.openai_embedding_dimensions,
        embedding=vector,
        rewritten_query=rewritten,
    )
