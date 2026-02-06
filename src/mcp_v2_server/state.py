from __future__ import annotations

from dataclasses import dataclass

from .adaptive_stats import AdaptiveStatsWriter
from .config import Config
from .logging_jsonl import JsonlLogger
from .trace_store import TraceStore


@dataclass
class AppState:
    config: Config
    logger: JsonlLogger
    traces: TraceStore
    adaptive_stats: AdaptiveStatsWriter


def create_state(config: Config | None = None) -> AppState:
    cfg = config or Config.from_env()
    return AppState(
        config=cfg,
        logger=JsonlLogger(level=cfg.log_level),
        traces=TraceStore(max_keep=cfg.trace_max_keep, ttl_sec=cfg.trace_ttl_sec),
        adaptive_stats=AdaptiveStatsWriter(cfg.adaptive_stats_path),
    )
