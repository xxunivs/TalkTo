"""기억 조각 텍스트 -> 벡터 (text-embedding-3-small, 모델과 차원은 env, 입력은 redacted 텍스트)"""

import json
import logging

from app.error import AppError
from app.external_api.openai_client import create_openai_client
from app.schema.embedding import (
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingResult,
    QueryEmbeddingRequest,
    QueryEmbeddingResponse,
)
from app.setting import Settings

logger = logging.getLogger(__name__)

PROVIDER = "openai"

# 배치 크기 (OpenAI 요청당 2,048개, 약 30만 토큰 한도, 한 번에 보내면 400 으로 마지막 단계 실패한 이력)
# 글자 상한은 한글 토큰 비율을 넉넉히 본 값
EMBED_BATCH_ITEMS = 256
EMBED_BATCH_CHARS = 60_000
# 대화 중 기억 찾기(/v1/embeddings/query): 질문 재작성(8초) + 임베딩(15초 x 2회) < BE 45초
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
    """글별 벡터 (순서 유지, 배치 분할, interactive 면 대화용 시간 한도)"""
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
        if len(got) != len(batch):  # 개수 불일치 시 엉뚱한 기억에 벡터가 붙음
            raise AppError(
                code="EMBEDDING_COUNT_MISMATCH",
                message=f"Embedding provider returned {len(got)} vectors for {len(batch)} inputs.",
                status_code=502,
            )
        vectors.extend(got)
    return vectors


def generate_embeddings(request: EmbeddingRequest, *, settings: Settings) -> EmbeddingResponse:
    """memory segment 배치 벡터화 (계약 엔드포인트)"""
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


# 세션 14 E6/E2 ask_rewrite 프롬프트 (D7: 실제 통화 92문항 근거 적중 51% -> 74%)
# 이름, 호칭만 일반화, ask 는 측정 프롬프트와 동일하게 두려고 남김 (미사용)
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
REWRITE_HISTORY = 4  # 최근 메시지 수 (E6 의 history[-4:])


def rewrite_query(request: QueryEmbeddingRequest, *, settings: Settings) -> str | None:
    """앞 대화 반영한 완결 질문 한 문장 (실패 시 None, 원문 검색)"""
    hist = "\n".join(
        f"{'가족' if m.role == 'user' else '대상자'}: {m.content}"
        for m in request.history[-REWRITE_HISTORY:]
    )
    prompt = QUERY_REWRITE_PROMPT.format(history=hist, msg=request.text)
    try:
        # 대화 중이라 짧게 1회 (실패 시 원문)
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
    except Exception as e:  # noqa: BLE001 (API 오류, JSON 깨짐 모두 원문으로)
        logger.warning("query rewrite failed, using original text: %s", type(e).__name__)
        return None
    if not isinstance(rewritten, str) or not rewritten.strip():
        logger.warning("query rewrite returned no text, using original text")
        return None
    return rewritten.strip()


def generate_query_embedding(
    request: QueryEmbeddingRequest, *, settings: Settings
) -> QueryEmbeddingResponse:
    """기억 검색 질문 벡터 (기억과 같은 모델, QUERY_REWRITE=on 이면 재작성 질문 사용)"""
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
