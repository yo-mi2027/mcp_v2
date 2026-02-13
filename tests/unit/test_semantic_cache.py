from __future__ import annotations

import pytest

from mcp_v2_server.semantic_cache import (
    NoopEmbeddingProvider,
    SemanticCacheStore,
    embedding_provider_from_name,
)


def test_semantic_cache_exact_hit_and_miss() -> None:
    store = SemanticCacheStore(max_keep=10, ttl_sec=60, embedding_provider=NoopEmbeddingProvider())
    store.put(
        scope_key="m1|expand=true|max=200",
        normalized_query="対象外",
        manuals_fingerprint="fp1",
        payload={"trace_id": "t1"},
    )

    hit = store.lookup_exact(
        scope_key="m1|expand=true|max=200",
        normalized_query="対象外",
        manuals_fingerprint="fp1",
    )
    miss = store.lookup_exact(
        scope_key="m1|expand=true|max=200",
        normalized_query="対象外",
        manuals_fingerprint="fp2",
    )

    assert hit.hit is True
    assert hit.mode == "exact"
    assert hit.value == {"trace_id": "t1"}
    assert miss.hit is False
    assert miss.mode == "miss"


def test_semantic_cache_ttl_expiry() -> None:
    now = {"value": 1000.0}
    store = SemanticCacheStore(
        max_keep=10,
        ttl_sec=1,
        embedding_provider=NoopEmbeddingProvider(),
        now_fn=lambda: now["value"],
    )
    store.put(
        scope_key="m1",
        normalized_query="対象外",
        manuals_fingerprint="fp1",
        payload={"trace_id": "t1"},
    )
    now["value"] = 1002.0

    out = store.lookup_exact(
        scope_key="m1",
        normalized_query="対象外",
        manuals_fingerprint="fp1",
    )

    assert out.hit is False
    assert store.size == 0


def test_semantic_cache_lru_eviction() -> None:
    store = SemanticCacheStore(max_keep=2, ttl_sec=60, embedding_provider=NoopEmbeddingProvider())
    store.put(scope_key="m1", normalized_query="q1", manuals_fingerprint="fp1", payload={"v": 1})
    store.put(scope_key="m1", normalized_query="q2", manuals_fingerprint="fp1", payload={"v": 2})
    _ = store.lookup_exact(scope_key="m1", normalized_query="q1", manuals_fingerprint="fp1")
    store.put(scope_key="m1", normalized_query="q3", manuals_fingerprint="fp1", payload={"v": 3})

    miss = store.lookup_exact(scope_key="m1", normalized_query="q2", manuals_fingerprint="fp1")
    hit = store.lookup_exact(scope_key="m1", normalized_query="q1", manuals_fingerprint="fp1")

    assert miss.hit is False
    assert hit.hit is True
    assert store.size == 2


def test_semantic_cache_semantic_lookup_is_miss_when_embedding_disabled() -> None:
    store = SemanticCacheStore(max_keep=10, ttl_sec=60, embedding_provider=NoopEmbeddingProvider())
    store.put(
        scope_key="m1",
        normalized_query="対象外",
        manuals_fingerprint="fp1",
        payload={"trace_id": "t1"},
    )

    out = store.lookup_semantic(
        scope_key="m1",
        normalized_query="対象外の条件",
        manuals_fingerprint="fp1",
        sim_threshold=0.9,
    )

    assert out.hit is False
    assert out.mode == "miss"


def test_embedding_provider_from_name_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError):
        embedding_provider_from_name("unknown")
