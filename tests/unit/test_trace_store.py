from __future__ import annotations

from mcp_v2_server.trace_store import TraceStore


def test_trace_store_clamps_non_positive_limits() -> None:
    store = TraceStore(max_keep=0, ttl_sec=0)

    assert store.max_keep == 1
    assert store.ttl_sec == 1

    first = store.create({"value": 1})
    second = store.create({"value": 2})

    assert store.get(first) is None
    assert store.get(second) == {"value": 2}
