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
    default_manual_id: str | None
    log_level: str
    adaptive_tuning: bool
    adaptive_stats_path: Path
    adaptive_min_recall: float
    adaptive_candidate_low_base: int
    adaptive_file_bias_base: float
    vault_scan_default_chunk_lines: int
    vault_scan_max_chunk_lines: int
    coverage_min_ratio: float
    marginal_gain_min: float
    trace_max_keep: int
    trace_ttl_sec: int
    allow_file_scope: bool
    hard_max_sections: int
    hard_max_chars: int
    default_max_stage: int

    @classmethod
    def from_env(cls) -> "Config":
        workspace_root = Path(os.getenv("WORKSPACE_ROOT", ".")).expanduser().resolve()
        manuals_root = Path(os.getenv("MANUALS_ROOT", str(workspace_root / "manuals"))).expanduser().resolve()
        vault_root = Path(os.getenv("VAULT_ROOT", str(workspace_root / "vault"))).expanduser().resolve()
        default_manual_id = os.getenv("DEFAULT_MANUAL_ID")
        default_max_stage = _env_int("DEFAULT_MAX_STAGE", 4)
        if default_max_stage not in {3, 4}:
            raise ValueError("DEFAULT_MAX_STAGE must be 3 or 4")

        return cls(
            workspace_root=workspace_root,
            manuals_root=manuals_root,
            vault_root=vault_root,
            default_manual_id=default_manual_id,
            log_level=os.getenv("LOG_LEVEL", "info"),
            adaptive_tuning=_env_bool("ADAPTIVE_TUNING", True),
            adaptive_stats_path=Path(
                os.getenv("ADAPTIVE_STATS_PATH", str(vault_root / ".system" / "adaptive_stats.jsonl"))
            ).expanduser(),
            adaptive_min_recall=_env_float("ADAPTIVE_MIN_RECALL", 0.90),
            adaptive_candidate_low_base=_env_int("ADAPTIVE_CANDIDATE_LOW_BASE", 3),
            adaptive_file_bias_base=_env_float("ADAPTIVE_FILE_BIAS_BASE", 0.80),
            vault_scan_default_chunk_lines=_env_int("VAULT_SCAN_DEFAULT_CHUNK_LINES", 80),
            vault_scan_max_chunk_lines=_env_int("VAULT_SCAN_MAX_CHUNK_LINES", 200),
            coverage_min_ratio=_env_float("COVERAGE_MIN_RATIO", 0.90),
            marginal_gain_min=_env_float("MARGINAL_GAIN_MIN", 0.02),
            trace_max_keep=_env_int("TRACE_MAX_KEEP", 100),
            trace_ttl_sec=_env_int("TRACE_TTL_SEC", 1800),
            allow_file_scope=_env_bool("ALLOW_FILE_SCOPE", False),
            hard_max_sections=_env_int("HARD_MAX_SECTIONS", 20),
            hard_max_chars=_env_int("HARD_MAX_CHARS", 12000),
            default_max_stage=default_max_stage,
        )
