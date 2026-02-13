from __future__ import annotations

import copy
import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class CacheLookupResult:
    hit: bool
    mode: str
    score: float | None = None
    value: dict[str, Any] | None = None


@dataclass
class SemanticCacheEntry:
    created_at: float
    key: str
    scope_key: str
    normalized_query: str
    vector: list[float] | None
    payload: dict[str, Any]
    manuals_fingerprint: str


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float] | None: ...


class SemanticCache(Protocol):
    def lookup_exact(
        self,
        *,
        scope_key: str,
        normalized_query: str,
        manuals_fingerprint: str,
    ) -> CacheLookupResult: ...

    def lookup_semantic(
        self,
        *,
        scope_key: str,
        normalized_query: str,
        manuals_fingerprint: str,
        sim_threshold: float,
    ) -> CacheLookupResult: ...

    def put(
        self,
        *,
        scope_key: str,
        normalized_query: str,
        manuals_fingerprint: str,
        payload: dict[str, Any],
    ) -> None: ...

    def cleanup(self) -> None: ...


class NoopEmbeddingProvider:
    def embed(self, text: str) -> list[float] | None:
        return None


def embedding_provider_from_name(name: str) -> EmbeddingProvider:
    normalized = (name or "none").strip().lower()
    if normalized == "none":
        return NoopEmbeddingProvider()
    raise ValueError(f"unsupported SEM_CACHE_EMBEDDING_PROVIDER: {name}")


class NoopSemanticCacheStore:
    def lookup_exact(
        self,
        *,
        scope_key: str,
        normalized_query: str,
        manuals_fingerprint: str,
    ) -> CacheLookupResult:
        return CacheLookupResult(hit=False, mode="miss")

    def lookup_semantic(
        self,
        *,
        scope_key: str,
        normalized_query: str,
        manuals_fingerprint: str,
        sim_threshold: float,
    ) -> CacheLookupResult:
        return CacheLookupResult(hit=False, mode="miss")

    def put(
        self,
        *,
        scope_key: str,
        normalized_query: str,
        manuals_fingerprint: str,
        payload: dict[str, Any],
    ) -> None:
        return None

    def cleanup(self) -> None:
        return None


class SemanticCacheStore:
    def __init__(
        self,
        *,
        max_keep: int,
        ttl_sec: int,
        embedding_provider: EmbeddingProvider | None = None,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self.max_keep = max(1, int(max_keep))
        self.ttl_sec = max(1, int(ttl_sec))
        self.embedding_provider = embedding_provider or NoopEmbeddingProvider()
        self._now_fn = now_fn or time.time
        self._items: OrderedDict[str, SemanticCacheEntry] = OrderedDict()

    @property
    def size(self) -> int:
        self.cleanup()
        return len(self._items)

    def _key(self, *, scope_key: str, normalized_query: str, manuals_fingerprint: str) -> str:
        return "\x1f".join([scope_key, manuals_fingerprint, normalized_query])

    def _cleanup_ttl(self, now: float) -> None:
        expired = [key for key, item in self._items.items() if now - item.created_at > self.ttl_sec]
        for key in expired:
            self._items.pop(key, None)

    def _cleanup_size(self) -> None:
        while len(self._items) > self.max_keep:
            self._items.popitem(last=False)

    def cleanup(self) -> None:
        now = self._now_fn()
        self._cleanup_ttl(now)
        self._cleanup_size()

    def lookup_exact(
        self,
        *,
        scope_key: str,
        normalized_query: str,
        manuals_fingerprint: str,
    ) -> CacheLookupResult:
        self.cleanup()
        key = self._key(
            scope_key=scope_key,
            normalized_query=normalized_query,
            manuals_fingerprint=manuals_fingerprint,
        )
        item = self._items.get(key)
        if item is None:
            return CacheLookupResult(hit=False, mode="miss")
        self._items.move_to_end(key)
        return CacheLookupResult(hit=True, mode="exact", score=1.0, value=copy.deepcopy(item.payload))

    def lookup_semantic(
        self,
        *,
        scope_key: str,
        normalized_query: str,
        manuals_fingerprint: str,
        sim_threshold: float,
    ) -> CacheLookupResult:
        self.cleanup()
        query_vec = self.embedding_provider.embed(normalized_query)
        if not query_vec:
            return CacheLookupResult(hit=False, mode="miss")

        best_key: str | None = None
        best_score = -1.0
        for key, item in self._items.items():
            if item.scope_key != scope_key:
                continue
            if item.manuals_fingerprint != manuals_fingerprint:
                continue
            if not item.vector:
                continue
            score = _cosine_similarity(query_vec, item.vector)
            if score > best_score:
                best_score = score
                best_key = key

        if best_key is None or best_score < sim_threshold:
            return CacheLookupResult(hit=False, mode="miss")

        hit_item = self._items[best_key]
        self._items.move_to_end(best_key)
        return CacheLookupResult(
            hit=True,
            mode="semantic",
            score=best_score,
            value=copy.deepcopy(hit_item.payload),
        )

    def put(
        self,
        *,
        scope_key: str,
        normalized_query: str,
        manuals_fingerprint: str,
        payload: dict[str, Any],
    ) -> None:
        now = self._now_fn()
        self._cleanup_ttl(now)
        key = self._key(
            scope_key=scope_key,
            normalized_query=normalized_query,
            manuals_fingerprint=manuals_fingerprint,
        )
        item = SemanticCacheEntry(
            created_at=now,
            key=key,
            scope_key=scope_key,
            normalized_query=normalized_query,
            vector=self.embedding_provider.embed(normalized_query),
            payload=copy.deepcopy(payload),
            manuals_fingerprint=manuals_fingerprint,
        )
        self._items[key] = item
        self._items.move_to_end(key)
        self._cleanup_size()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return -1.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return -1.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))
