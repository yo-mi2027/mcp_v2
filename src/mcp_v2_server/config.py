from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    workspace_root: Path
    manuals_root: Path
    vault_root: Path
    log_level: str
    adaptive_tuning: bool
    adaptive_stats_path: Path
    adaptive_min_recall: float
    adaptive_candidate_low_base: int
    adaptive_file_bias_base: float
    coverage_min_ratio: float
    marginal_gain_min: float
    corrective_enabled: bool
    corrective_coverage_min: float
    corrective_margin_min: float
    corrective_min_candidates: int
    corrective_on_conflict: bool
    sparse_query_coverage_weight: float
    lexical_coverage_weight: float
    lexical_phrase_weight: float
    lexical_number_context_bonus: float
    lexical_proximity_bonus_near: float
    lexical_proximity_bonus_far: float
    lexical_length_penalty_weight: float
    manual_find_exploration_enabled: bool
    manual_find_exploration_ratio: float
    manual_find_exploration_min_candidates: int
    manual_find_exploration_score_scale: float
    manual_find_stage4_enabled: bool
    manual_find_stage4_neighbor_limit: int
    manual_find_stage4_budget_time_ms: int
    manual_find_stage4_score_penalty: float
    manual_find_query_decomp_enabled: bool
    manual_find_query_decomp_max_sub_queries: int
    manual_find_query_decomp_rrf_k: int
    manual_find_query_decomp_base_weight: float
    manual_find_scan_hard_cap: int
    manual_find_per_file_candidate_cap: int
    manual_find_file_prescan_enabled: bool
    late_rerank_enabled: bool
    late_rerank_top_n: int
    late_rerank_weight: float
    trace_max_keep: int
    trace_ttl_sec: int
    allow_file_scope: bool
    sem_cache_enabled: bool
    sem_cache_ttl_sec: int
    sem_cache_max_keep: int
    sem_cache_sim_threshold: float
    sem_cache_embedding_provider: str
    sem_cache_max_summary_gap: int
    sem_cache_max_summary_conflict: int

    @classmethod
    def from_env(cls) -> "Config":
        workspace_root = Path(os.getenv("WORKSPACE_ROOT", ".")).expanduser().resolve()
        manuals_root = Path(os.getenv("MANUALS_ROOT", str(workspace_root / "manuals"))).expanduser().resolve()
        vault_root = Path(os.getenv("VAULT_ROOT", str(workspace_root / "vault"))).expanduser().resolve()

        return cls(
            workspace_root=workspace_root,
            manuals_root=manuals_root,
            vault_root=vault_root,
            log_level=os.getenv("LOG_LEVEL", "info"),
            adaptive_tuning=_env_bool("ADAPTIVE_TUNING", True),
            adaptive_stats_path=Path(
                os.getenv("ADAPTIVE_STATS_PATH", str(vault_root / ".system" / "adaptive_stats.jsonl"))
            ).expanduser(),
            adaptive_min_recall=_env_float("ADAPTIVE_MIN_RECALL", 0.90),
            adaptive_candidate_low_base=_env_int("ADAPTIVE_CANDIDATE_LOW_BASE", 3),
            adaptive_file_bias_base=_env_float("ADAPTIVE_FILE_BIAS_BASE", 0.80),
            coverage_min_ratio=_env_float("COVERAGE_MIN_RATIO", 0.90),
            marginal_gain_min=_env_float("MARGINAL_GAIN_MIN", 0.02),
            corrective_enabled=_env_bool("CORRECTIVE_ENABLED", False),
            corrective_coverage_min=_env_float("CORRECTIVE_COVERAGE_MIN", 0.90),
            corrective_margin_min=_env_float("CORRECTIVE_MARGIN_MIN", 0.15),
            corrective_min_candidates=_env_int("CORRECTIVE_MIN_CANDIDATES", 3),
            corrective_on_conflict=_env_bool("CORRECTIVE_ON_CONFLICT", True),
            sparse_query_coverage_weight=_env_float("SPARSE_QUERY_COVERAGE_WEIGHT", 0.35),
            lexical_coverage_weight=_env_float("LEXICAL_COVERAGE_WEIGHT", 0.50),
            lexical_phrase_weight=_env_float("LEXICAL_PHRASE_WEIGHT", 0.50),
            lexical_number_context_bonus=_env_float("LEXICAL_NUMBER_CONTEXT_BONUS", 0.80),
            lexical_proximity_bonus_near=_env_float("LEXICAL_PROXIMITY_BONUS_NEAR", 1.00),
            lexical_proximity_bonus_far=_env_float("LEXICAL_PROXIMITY_BONUS_FAR", 0.50),
            lexical_length_penalty_weight=_env_float("LEXICAL_LENGTH_PENALTY_WEIGHT", 0.20),
            manual_find_exploration_enabled=_env_bool("MANUAL_FIND_EXPLORATION_ENABLED", True),
            manual_find_exploration_ratio=_env_float("MANUAL_FIND_EXPLORATION_RATIO", 0.20),
            manual_find_exploration_min_candidates=_env_int("MANUAL_FIND_EXPLORATION_MIN_CANDIDATES", 2),
            manual_find_exploration_score_scale=_env_float("MANUAL_FIND_EXPLORATION_SCORE_SCALE", 0.35),
            manual_find_stage4_enabled=_env_bool("MANUAL_FIND_STAGE4_ENABLED", True),
            manual_find_stage4_neighbor_limit=_env_int("MANUAL_FIND_STAGE4_NEIGHBOR_LIMIT", 2),
            manual_find_stage4_budget_time_ms=_env_int("MANUAL_FIND_STAGE4_BUDGET_TIME_MS", 15000),
            manual_find_stage4_score_penalty=_env_float("MANUAL_FIND_STAGE4_SCORE_PENALTY", 0.15),
            manual_find_query_decomp_enabled=_env_bool("MANUAL_FIND_QUERY_DECOMP_ENABLED", True),
            manual_find_query_decomp_max_sub_queries=_env_int("MANUAL_FIND_QUERY_DECOMP_MAX_SUB_QUERIES", 3),
            manual_find_query_decomp_rrf_k=_env_int("MANUAL_FIND_QUERY_DECOMP_RRF_K", 60),
            manual_find_query_decomp_base_weight=_env_float("MANUAL_FIND_QUERY_DECOMP_BASE_WEIGHT", 0.30),
            manual_find_scan_hard_cap=_env_int("MANUAL_FIND_SCAN_HARD_CAP", 5000),
            manual_find_per_file_candidate_cap=_env_int("MANUAL_FIND_PER_FILE_CANDIDATE_CAP", 8),
            manual_find_file_prescan_enabled=_env_bool("MANUAL_FIND_FILE_PRESCAN_ENABLED", True),
            late_rerank_enabled=_env_bool("LATE_RERANK_ENABLED", False),
            late_rerank_top_n=_env_int("LATE_RERANK_TOP_N", 50),
            late_rerank_weight=_env_float("LATE_RERANK_WEIGHT", 0.60),
            trace_max_keep=_env_int("TRACE_MAX_KEEP", 100),
            trace_ttl_sec=_env_int("TRACE_TTL_SEC", 1800),
            allow_file_scope=_env_bool("ALLOW_FILE_SCOPE", False),
            sem_cache_enabled=_env_bool("SEM_CACHE_ENABLED", False),
            sem_cache_ttl_sec=_env_int("SEM_CACHE_TTL_SEC", 1800),
            sem_cache_max_keep=_env_int("SEM_CACHE_MAX_KEEP", 500),
            sem_cache_sim_threshold=_env_float("SEM_CACHE_SIM_THRESHOLD", 0.92),
            sem_cache_embedding_provider=os.getenv("SEM_CACHE_EMBEDDING_PROVIDER", "none"),
            sem_cache_max_summary_gap=_env_int("SEM_CACHE_MAX_SUMMARY_GAP", -1),
            sem_cache_max_summary_conflict=_env_int("SEM_CACHE_MAX_SUMMARY_CONFLICT", -1),
        )
