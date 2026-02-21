from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .adaptive_stats import AdaptiveStatsWriter
from .config import Config
from .logging_jsonl import JsonlLogger
from .semantic_cache import (
    SemanticCache,
    SemanticCacheStore,
    NoopSemanticCacheStore,
    embedding_provider_from_name,
)
from .sparse_index import SparseIndexStore
from .trace_store import TraceStore


@dataclass
class AppState:
    config: Config
    logger: JsonlLogger
    traces: TraceStore
    adaptive_stats: AdaptiveStatsWriter
    semantic_cache: SemanticCache
    sparse_index: SparseIndexStore
    read_progress: dict[str, dict[str, int | None]] = field(default_factory=dict)
    manual_root_ids: set[str] = field(default_factory=set)
    manual_ls_seen: bool = False
    next_actions_planner: Callable[[dict[str, Any]], Any] | None = None


def create_state(config: Config | None = None) -> AppState:
    cfg = config or Config.from_env()
    if cfg.sem_cache_enabled:
        semantic_cache: SemanticCache = SemanticCacheStore(
            max_keep=cfg.sem_cache_max_keep,
            ttl_sec=cfg.sem_cache_ttl_sec,
            embedding_provider=embedding_provider_from_name(cfg.sem_cache_embedding_provider),
        )
    else:
        semantic_cache = NoopSemanticCacheStore()
    return AppState(
        config=cfg,
        logger=JsonlLogger(level=cfg.log_level),
        traces=TraceStore(max_keep=cfg.trace_max_keep, ttl_sec=cfg.trace_ttl_sec),
        adaptive_stats=AdaptiveStatsWriter(cfg.adaptive_stats_path),
        semantic_cache=semantic_cache,
        sparse_index=SparseIndexStore(cfg.manuals_root),
    )
