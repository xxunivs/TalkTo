"""② 기억 원본(MemoryStore) + 기억 선택(MemorySelector)"""

from __future__ import annotations

import ast
import json
import logging
import math
from pathlib import Path
from typing import Protocol

from orchestrator.contracts import MemoryItem, MemorySelection, MemoryStore, SessionState, TurnInput

log = logging.getLogger(__name__)


class RequestMemoryStore:
    impl = "request"

    def list_for_subject(self, turn: TurnInput) -> list[MemoryItem]:
        return list(turn.be_memories)


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value.startswith("["):
        try:
            return [str(v) for v in ast.literal_eval(value)]
        except (ValueError, SyntaxError):
            return []
    return []


def memory_from_export_row(row: dict) -> MemoryItem:
    """Neon SQL 편집기 export 한 행 -> MemoryItem (행: {"j": json 문자열} 또는 json 자체)"""
    r = json.loads(row["j"]) if "j" in row else row
    return MemoryItem(
        id=str(r["id"]),
        text=str(r.get("body") or r.get("text") or ""),
        title=str(r.get("title") or ""),
        memory_type=r.get("mtype") or r.get("memory_type") or "LONG_TERM",
        status=str(r.get("status") or "active"),
        tags=_as_list(r.get("tags")),
    )


class ExportFileMemoryStore:
    """Neon memories JSON 파일 원본 (파일 하나 = 대상자 한 명, default 는 모든 subject 공용)"""

    impl = "export_file"

    def __init__(
        self,
        files: dict[str, Path] | None = None,
        *,
        default: Path | None = None,
        statuses: tuple[str, ...] = ("ACTIVE", "active"),
    ) -> None:
        self.files = {k: Path(v) for k, v in (files or {}).items()}
        self.default = Path(default) if default else None
        self.statuses = statuses
        self._cache: dict[Path, list[MemoryItem]] = {}

    def _load(self, path: Path) -> list[MemoryItem]:
        if path not in self._cache:
            rows = json.loads(path.read_text(encoding="utf-8"))
            items = [memory_from_export_row(r) for r in rows]
            self._cache[path] = [m for m in items if m.status in self.statuses and m.text.strip()]
        return self._cache[path]

    def list_for_subject(self, turn: TurnInput) -> list[MemoryItem]:
        path = self.files.get(turn.subject_id, self.default)
        if path is None:
            raise KeyError(f"no memory export for subject {turn.subject_id}")
        return list(self._load(path))


class PassThroughSelector:
    impl = "passthrough_v0"

    def select(self, turn: TurnInput, state: SessionState, store: MemoryStore) -> MemorySelection:
        return MemorySelection(
            memories=store.list_for_subject(turn),
            delta=list(state.delta_memories),
            strategy=f"passthrough:{store.impl}",
        )


# ---------------------------------------------------------------- 장기 전부 + 단기 분해 검색


class Embedder(Protocol):
    """텍스트 -> 벡터 (기억 벡터와 같은 모델 필요, 운영: text-embedding-3-small)"""

    impl: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    """AI 서버 기존 임베딩 함수 래퍼 (pipeline d_build_memory_embedding.embed_texts)"""

    impl = "openai"

    def __init__(self, settings) -> None:
        self.settings = settings

    def embed(self, texts: list[str]) -> list[list[float]]:
        from pipeline.s3_synthesis.d_build_memory_embedding import embed_texts

        return embed_texts(texts, settings=self.settings)


class CachedEmbedder:
    """같은 텍스트 1회 임베딩 캐시 (단기 후보 본문은 턴마다 거의 같음, 프로세스 메모리, 개수 상한)"""

    def __init__(self, inner: Embedder, max_items: int = 5000) -> None:
        self.inner = inner
        self.max_items = max_items
        self.impl = f"{inner.impl}+cache"
        self._cache: dict[str, list[float]] = {}

    def embed(self, texts: list[str]) -> list[list[float]]:
        todo = list(dict.fromkeys(t for t in texts if t not in self._cache))
        if todo:
            for t, v in zip(todo, self.inner.embed(todo)):
                if len(self._cache) >= self.max_items:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[t] = v
        return [self._cache[t] for t in texts]


class FileEmbedder:
    """오프라인 재현용 {텍스트: 벡터} json 캐시 (없는 텍스트는 KeyError)"""

    impl = "file"

    def __init__(self, *paths: Path) -> None:
        self.table: dict[str, list[float]] = {}
        for p in paths:
            self.table.update(json.loads(Path(p).read_text(encoding="utf-8")))

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.table[t] for t in texts]


class MemoryVectors(Protocol):
    """기억 -> 벡터 (못 구한 기억은 검색 대상 제외)"""

    impl: str

    def vectors_for(self, items: list[MemoryItem]) -> dict[str, list[float]]: ...


class EmbedOnDemandVectors:
    """BE 가 본문만 보낼 때 본문 임베딩 (운영 memory_embeddings 와 같게 제목 제외)"""

    def __init__(self, embedder: Embedder) -> None:
        self.embedder = embedder
        self.impl = f"embed:{embedder.impl}"

    def vectors_for(self, items: list[MemoryItem]) -> dict[str, list[float]]:
        items = [m for m in items if m.text.strip()]
        vecs = self.embedder.embed([m.text for m in items]) if items else []
        return {m.id: v for m, v in zip(items, vecs)}


class ExportVectors:
    """운영 memory_embeddings export -> memoryId 별 벡터 (오프라인 재현용)"""

    impl = "export"

    def __init__(self, *paths: Path) -> None:
        self.table: dict[str, list[float]] = {}
        for p in paths:
            for row in json.loads(Path(p).read_text(encoding="utf-8")):
                r = json.loads(row["j"]) if "j" in row else row
                if r.get("embedding"):
                    self.table.setdefault(str(r["memoryId"]), r["embedding"])

    def vectors_for(self, items: list[MemoryItem]) -> dict[str, list[float]]:
        return {m.id: self.table[m.id] for m in items if m.id in self.table}


def _cos(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


class TieredSelector:
    """장기 기억 전부(받은 순서 고정, 캐시) + 단기는 분해 단서로 임계 이상 top-k (memoryType 없으면 PassThrough 와 동일)"""

    impl = "tiered_v1"

    def __init__(
        self,
        profiles,
        embedder: Embedder,
        vectors: MemoryVectors,
        *,
        st_top_k: int = 5,
        threshold: float = 0.18,
    ) -> None:
        self.profiles = profiles
        self.embedder = embedder
        self.vectors = vectors
        self.st_top_k = st_top_k
        self.threshold = threshold
        self._splitters: dict = {}

    def cue(self, turn: TurnInput) -> str:
        prof = self.profiles.profile_for(turn) if self.profiles is not None else None
        if prof is None or not (prof.address_terms or prof.family_names):
            return turn.message.strip()
        key = (tuple(prof.address_terms), tuple(prof.family_names))
        sp = self._splitters.get(key)
        if sp is None:
            from orchestrator.split import Splitter, lexicon_from_terms

            sp = self._splitters[key] = Splitter(
                lexicon_from_terms(prof.address_terms, prof.family_names)
            )
        return sp.split(turn.message).cue

    def rank_short(self, cue: str, short: list[MemoryItem]) -> list[MemoryItem]:
        qv = self.embedder.embed([cue])[0]
        vecs = self.vectors.vectors_for(short)
        scored = [
            (s, i, m)
            for i, m in enumerate(short)
            if m.id in vecs
            for s in [_cos(qv, vecs[m.id])]
            if s >= self.threshold
        ]
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [m for _, _, m in scored[: self.st_top_k]]

    def select(self, turn: TurnInput, state: SessionState, store: MemoryStore) -> MemorySelection:
        items = store.list_for_subject(turn)
        long_ = [m for m in items if m.memory_type == "LONG_TERM"]
        short = [m for m in items if m.memory_type == "SHORT_TERM"]
        tag = ""
        picked: list[MemoryItem] = []
        cue = self.cue(turn) if short else ""
        if short and not cue:
            tag = "|nocue"
        elif short:
            try:
                picked = self.rank_short(cue, short)
            except Exception:  # 임베딩 실패 시 받은 순서(BE 유사도 순)로 대체, 응답 차단 방지
                log.exception(
                    "tiered selector: short-term ranking failed, falling back to given order"
                )
                picked, tag = short[: self.st_top_k], "|embed-fail"
        return MemorySelection(
            memories=long_ + picked,
            delta=list(state.delta_memories),
            strategy=f"tiered:lt{len(long_)}+st{len(picked)}/{len(short)}{tag}",
        )
