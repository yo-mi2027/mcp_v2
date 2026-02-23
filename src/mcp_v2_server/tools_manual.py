from __future__ import annotations

import hashlib
import time
import base64
import math
import copy
import re
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import ToolError, ensure
from .manual_index import (
    MdNode,
    discover_manual_ids,
    list_manual_files,
    parse_markdown_toc,
)
from .normalization import normalize_text, split_terms
from .path_guard import normalize_relative_path, resolve_inside_root
from .sparse_index import SparseIndex, bm25_scores
from .state import AppState

EXCEPTION_WORDS = [
    "注意",
    "留意",
    "対象外",
    "除外",
    "不適用",
    "支払われない",
]
NORMALIZED_EXCEPTION_WORDS = [normalize_text(w) for w in EXCEPTION_WORDS]

FACET_ORDER = ["definition", "procedure", "eligibility", "exceptions", "compare", "unknown"]
FACET_HINTS_RAW: dict[str, list[str]] = {
    "definition": ["定義", "とは", "意味", "概要", "基本"],
    "procedure": ["手順", "フロー", "手続き", "ステップ", "方法"],
    "eligibility": ["条件", "要件", "対象", "可否", "適用", "支払", "日数", "回数", "上限", "金額", "限度", "無制限"],
    "exceptions": ["例外", "対象外", "除外", "不適用", "ただし", "但し"],
    "compare": ["比較", "違い", "差分", "優先", "どちら", "対比", "vs"],
}
FACET_HINTS = {
    key: [normalize_text(word) for word in words]
    for key, words in FACET_HINTS_RAW.items()
}
SCAN_MAX_CHARS = 12000
READ_MAX_SECTIONS = 20
READ_MAX_CHARS = 12000
MANUAL_IO_MAX_CHARS_MIN = 256
MANUAL_IO_MAX_CHARS_MAX = 50000
TOC_DEFAULT_MAX_FILES = 20
TOC_MAX_FILES_WITHOUT_HEADINGS = 200
TOC_MAX_FILES_WITHOUT_PREFIX = 50
TOC_MAX_FILES_DEEP = 50
TOC_SCOPE_HARD_LIMIT = 200
TOC_DEFAULT_MAX_HEADINGS_PER_FILE = 20
TOC_MAX_HEADINGS_PER_FILE = 200
NUMBER_PATTERN = re.compile(r"\d+")
NOISE_PATH_TERMS = ("目次", "toc", "index")
NUMBER_CONTEXT_TERMS = {normalize_text(term) for term in ("手術番号", "附番", "別表", "番号")}
PROXIMITY_WINDOW_CHARS = 80
LEXICAL_SPLIT_RE = re.compile(r"[・/(),、。:：;；\[\]{}「」『』【】]+")
LEXICAL_SCRIPT_CHUNK_RE = re.compile(r"[a-z]+[0-9]+|[a-z]+|[0-9]+|[ぁ-んァ-ヶー一-龯々〆ヵヶ]+")
LEXICAL_CJK_ONLY_RE = re.compile(r"^[ぁ-んァ-ヶー一-龯々〆ヵヶ]+$")
CJK_COMPOUND_SUFFIXES = tuple(
    normalize_text(term)
    for term in (
        "給付金",
        "手術",
        "入院",
        "通院",
        "退院",
        "特約",
        "保険",
        "規定",
        "通算",
        "条件",
        "番号",
        "支払",
    )
)
CODE_TOKEN_RE = re.compile(r"^[a-z]{1,4}\d{2,6}[a-z]?$")
PRF_TERM_SHAPE_RE = re.compile(r"^[a-z0-9ぁ-んァ-ヶー一-龯々〆ヵヶ]+$")
PRF_TOP_DOCS = 6
PRF_MAX_TERMS = 4
PRF_TERM_MAX_DF_RATIO = 0.60
PRF_TERM_WEIGHT = 0.40
CODE_EXACT_BONUS = 1.60
CLAIM_GRAPH_STRONG_SUPPORT_MIN_AVG_CONFIDENCE = 0.75
CLAIM_GRAPH_STRONG_SUPPORT_MAX_EXTRA_FOLLOWUPS = 1
CLAIM_GRAPH_SEARCH_GAP_EXCLUDED_FACETS = frozenset({"compare"})
MANUAL_FIND_DYNAMIC_CUTOFF_MAX_CANDIDATES = 50
MANUAL_FIND_DYNAMIC_CUTOFF_MIN_KEEP = 8
MANUAL_FIND_DYNAMIC_CUTOFF_MIN_SCORE_RATIO = 0.25
MANUAL_FIND_DYNAMIC_CUTOFF_MIN_COVERAGE = 0.50
MANUAL_FIND_FILE_DIVERSITY_PENALTY_MIN = 0.35
MANUAL_FIND_FILE_DIVERSITY_PENALTY_TOP_RATIO = 0.05
MANUAL_FIND_SCAN_CAP_MIN_CANDIDATES = 50
MANUAL_FIND_SCAN_CAP_BUDGET_MULTIPLIER = 20
QUERY_DECOMP_COMPARE_DIFF_RE = re.compile(r"^\s*(?P<left>.+?)\s*と\s*(?P<right>.+?)\s*の違い\s*$")
QUERY_DECOMP_COMPARE_KEYWORD_RE = re.compile(
    r"^\s*(?P<left>.+?)\s*と\s*(?P<right>.+?)\s*(?:を|の)?\s*(?:比較|差|どっち|どちら)(?:.*)?$"
)
QUERY_DECOMP_COMPARE_RE = re.compile(r"^\s*(?P<left>.+?)\s*と\s*(?P<right>.+?)\s*$")
QUERY_DECOMP_VS_RE = re.compile(r"^\s*(?P<left>.+?)\s*(?:vs|VS|Vs|v\.s\.|ＶＳ|ｖｓ)\s*(?P<right>.+?)\s*$")
QUERY_DECOMP_CASE_RE = re.compile(r"^\s*(?P<left>.+?)\s*の場合の\s*(?P<right>.+?)\s*$")
KANJI_CHAR_RE = re.compile(r"[一-龯々〆ヵヶ]")
HIRAGANA_CHAR_RE = re.compile(r"[ぁ-ん]")
OKURIGANA_TRIM_SUFFIXES = ("い", "み", "り", "き", "し")
REQUIRED_TERMS_MAX_ITEMS = 2
REQUIRED_TERM_RRF_K = 60
REQUIRED_TERM_RRF_BASE_WEIGHT = 0.70
REQUIRED_TERM_RRF_AND_WEIGHT = 1.10
REQUIRED_TERM_RRF_SINGLE_WEIGHT = 1.00
REQUIRED_TERM_RRF_IMBALANCE_RATIO = 0.70
REQUIRED_TERM_RRF_BASE_WEIGHT_IMBALANCED = 0.45
REQUIRED_TERM_MATCH_BONUS = 2.40
REQUIRED_TERM_DF_MIN_RATIO = 0.02
REQUIRED_TERM_DF_MAX_RATIO = 0.85
REQUIRED_TERM_DF_COMMON_MIN_DOCS = 20
GATE_FUSION_RRF_K = 60
GATE_FUSION_BASE_WEIGHT = 0.65
GATE_FUSION_G0_WEIGHT = 1.00
GATE_FUSION_REQUIRED_WEIGHT_MIN = 0.35
GATE_FUSION_REQUIRED_WEIGHT_MAX = 1.35
GATE_FUSION_TOP_K = 10
GATE_FUSION_LOW_RATIO = 0.20
GATE_FUSION_HIGH_RATIO = 0.75
REQUIRED_EFFECT_TOP_K = 5
EXHAUSTIVE_QUERY_HINTS = tuple(
    normalize_text(term)
    for term in (
        "網羅",
        "漏れなく",
        "すべて",
        "全て",
        "全部",
        "全件",
        "包括的",
    )
)
LEXICAL_TOKEN_HIT_COUNT_CAP = 20
LEXICAL_DEFINITION_TITLE_BONUS = 0.90
RELAXED_MIN_MATCHED_TOKENS = 2
RELAXED_MIN_TOKEN_HIT_SUM = 3
RELAXED_STRONG_SIGNALS = {"phrase", "code_exact", "proximity", "number_context"}
DEFINITION_TITLE_HINTS = tuple(normalize_text(term) for term in ("定義", "基本", "支払事由", "概要"))
ELIGIBILITY_QUERY_HINTS = tuple(normalize_text(term) for term in ("条件", "要件", "支払", "給付金", "事由"))
CLAIM_GRAPH_STRONG_SIGNALS = {
    "phrase",
    "anchor",
    "number_context",
    "proximity",
    "code_exact",
    "required_term",
    "required_term_and",
}


def _is_exhaustive_query(query: str) -> bool:
    normalized_query = normalize_text(query)
    if not normalized_query:
        return False
    return any(hint in normalized_query for hint in EXHAUSTIVE_QUERY_HINTS)


def _parse_int_param(
    value: Any,
    *,
    name: str,
    default: int,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    raw = default if value is None else value
    if isinstance(raw, bool):
        raise ToolError("invalid_parameter", f"{name} must be an integer")
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        raise ToolError("invalid_parameter", f"{name} must be an integer")
    if min_value is not None and parsed < min_value:
        raise ToolError("invalid_parameter", f"{name} must be >= {min_value}")
    if max_value is not None and parsed > max_value:
        raise ToolError("invalid_parameter", f"{name} must be <= {max_value}")
    return parsed


def _parse_bool_param(value: Any, *, name: str, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ToolError("invalid_parameter", f"{name} must be boolean")


def _require_non_empty_string(value: Any, *, name: str) -> str:
    if not isinstance(value, str):
        raise ToolError("invalid_parameter", f"{name} must be string")
    stripped = value.strip()
    if not stripped:
        raise ToolError("invalid_parameter", f"{name} is required")
    return stripped


def _require_manual_id(value: Any, *, name: str = "manual_id") -> str:
    manual_id = _require_non_empty_string(value, name=name)
    canonical = manual_id.replace("\\", "/")
    parts = [part for part in PurePosixPath(canonical).parts if part not in {"", "."}]
    ensure(
        len(parts) == 1 and parts[0] != "..",
        "invalid_parameter",
        f"{name} must be a single manual directory id",
    )
    return manual_id


def _parse_required_terms_param(value: Any, *, name: str = "required_terms") -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ToolError("invalid_parameter", f"{name} must be a string array")

    normalized_terms: list[str] = []
    seen: set[str] = set()
    for idx, item in enumerate(value):
        term = _require_non_empty_string(item, name=f"{name}[{idx}]")
        normalized = normalize_text(term)
        if not normalized:
            raise ToolError("invalid_parameter", f"{name}[{idx}] must not be empty after normalization")
        if normalized in seen:
            continue
        seen.add(normalized)
        normalized_terms.append(normalized)
    if len(normalized_terms) > REQUIRED_TERMS_MAX_ITEMS:
        raise ToolError("invalid_parameter", f"{name} must contain at most {REQUIRED_TERMS_MAX_ITEMS} items")
    return normalized_terms


def _cacheable_query(query: str) -> str:
    normalized = normalize_text(query)
    if normalized:
        return normalized
    return query.strip().lower()


def _manual_find_scope_key(
    *,
    manual_id: str | None,
    max_candidates: int,
    budget_time_ms: int,
    required_terms: list[str] | None = None,
) -> str:
    scope_manual_id = manual_id or "*"
    required = ",".join(required_terms or [])
    return (
        f"manual_id={scope_manual_id}"
        f"|max_candidates={max_candidates}"
        f"|budget_time_ms={budget_time_ms}"
        f"|required_terms={required}"
    )


def _manuals_fingerprint(state: AppState, manual_ids: list[str]) -> str:
    digest = hashlib.sha256()
    for mid in sorted(set(manual_ids)):
        digest.update(mid.encode("utf-8"))
        digest.update(b"\0")
        manual_root = state.config.manuals_root / mid
        rows = list_manual_files(state.config.manuals_root, manual_id=mid)
        for row in rows:
            digest.update(row.path.encode("utf-8"))
            digest.update(b"\0")
            full_path = manual_root / row.path
            try:
                stat = full_path.stat()
                digest.update(str(stat.st_mtime_ns).encode("ascii"))
                digest.update(b":")
                digest.update(str(stat.st_size).encode("ascii"))
            except Exception:
                digest.update(b"missing")
            digest.update(b"\0")
    return digest.hexdigest()


def _compact_next_actions(next_actions: Any) -> list[dict[str, Any]]:
    del next_actions
    # Public compact responses intentionally avoid planner hints. LLM callers are
    # expected to choose the next tool call directly.
    return []


def _compact_manual_find_output(
    *,
    trace_id: str,
    summary: Any,
    applied: dict[str, Any],
    next_actions: Any,
) -> dict[str, Any]:
    summary_obj = summary if isinstance(summary, dict) else {}
    candidates_raw = summary_obj.get("candidates")
    candidates = int(candidates_raw) if isinstance(candidates_raw, int) else 0
    status = applied.get("required_effect_status")
    if not isinstance(status, str) or not status:
        status = "required_effective"
    failure_reason = applied.get("required_failure_reason")
    if not isinstance(failure_reason, str) or not failure_reason:
        failure_reason = None
    return {
        "trace_id": trace_id,
        "candidates": candidates,
        "status": status,
        "failure_reason": failure_reason,
        "next_actions": _compact_next_actions(next_actions),
    }


def _out_from_trace_payload(
    *,
    trace_id: str,
    trace_payload: dict[str, Any],
    include_claim_graph: bool,
    compact: bool,
) -> dict[str, Any]:
    applied = trace_payload.get("applied")
    if not isinstance(applied, dict):
        requested_required_terms = trace_payload.get("requested_required_terms")
        if not isinstance(requested_required_terms, list):
            requested_required_terms = trace_payload.get("required_terms") or []
        required_terms_df_filtered = trace_payload.get("required_terms_df_filtered")
        if not isinstance(required_terms_df_filtered, list):
            required_terms_df_filtered = []
        applied = {
            "manual_id": trace_payload.get("manual_id"),
            "requested_expand_scope": None,
            "expand_scope": False,
            "required_terms_source": trace_payload.get("required_terms_source") or "user",
            "required_terms_decision_reason": trace_payload.get("required_terms_decision_reason"),
            "requested_required_terms": requested_required_terms,
            "required_terms": trace_payload.get("required_terms") or [],
            "required_terms_df_filtered": required_terms_df_filtered,
            "required_terms_relaxed": bool(trace_payload.get("required_terms_relaxed", False)),
            "required_terms_relax_reason": trace_payload.get("required_terms_relax_reason"),
            "required_effect_status": trace_payload.get("required_effect_status") or "required_effective",
            "required_failure_reason": trace_payload.get("required_failure_reason"),
            "required_strict_candidates": int(trace_payload.get("required_strict_candidates") or 0),
            "required_filtered_candidates": int(trace_payload.get("required_filtered_candidates") or 0),
            "required_terms_match_stats": trace_payload.get("required_terms_match_stats") or [],
            "required_terms_missing": trace_payload.get("required_terms_missing") or [],
            "required_top_k": int(trace_payload.get("required_top_k") or 0),
            "required_top_hits": int(trace_payload.get("required_top_hits") or 0),
            "selected_gate": trace_payload.get("selected_gate") or "single",
            "gate_selection_reason": trace_payload.get("gate_selection_reason"),
        }
    else:
        applied = dict(applied)
        if "requested_required_terms" not in applied:
            requested = trace_payload.get("requested_required_terms")
            if not isinstance(requested, list):
                requested = trace_payload.get("required_terms") or []
            applied["requested_required_terms"] = requested if isinstance(requested, list) else []
        if "required_terms_df_filtered" not in applied:
            dropped = trace_payload.get("required_terms_df_filtered")
            applied["required_terms_df_filtered"] = dropped if isinstance(dropped, list) else []
        if "required_terms_relaxed" not in applied:
            applied["required_terms_relaxed"] = bool(trace_payload.get("required_terms_relaxed", False))
        if "required_terms_relax_reason" not in applied:
            reason = trace_payload.get("required_terms_relax_reason")
            applied["required_terms_relax_reason"] = reason if isinstance(reason, str) and reason else None
        if "required_effect_status" not in applied:
            status = trace_payload.get("required_effect_status")
            applied["required_effect_status"] = status if isinstance(status, str) and status else "required_effective"
        if "required_failure_reason" not in applied:
            reason = trace_payload.get("required_failure_reason")
            applied["required_failure_reason"] = reason if isinstance(reason, str) and reason else None
        if "required_strict_candidates" not in applied:
            applied["required_strict_candidates"] = int(trace_payload.get("required_strict_candidates") or 0)
        if "required_filtered_candidates" not in applied:
            applied["required_filtered_candidates"] = int(trace_payload.get("required_filtered_candidates") or 0)
        if "required_terms_match_stats" not in applied:
            stats = trace_payload.get("required_terms_match_stats")
            applied["required_terms_match_stats"] = stats if isinstance(stats, list) else []
        if "required_terms_missing" not in applied:
            missing = trace_payload.get("required_terms_missing")
            applied["required_terms_missing"] = missing if isinstance(missing, list) else []
        if "required_top_k" not in applied:
            applied["required_top_k"] = int(trace_payload.get("required_top_k") or 0)
        if "required_top_hits" not in applied:
            applied["required_top_hits"] = int(trace_payload.get("required_top_hits") or 0)
        if "required_terms_source" not in applied:
            source = trace_payload.get("required_terms_source")
            applied["required_terms_source"] = source if source == "user" else "user"
        if "required_terms_decision_reason" not in applied:
            reason = trace_payload.get("required_terms_decision_reason")
            applied["required_terms_decision_reason"] = reason if isinstance(reason, str) and reason else None
        if "selected_gate" not in applied:
            gate = trace_payload.get("selected_gate")
            applied["selected_gate"] = gate if isinstance(gate, str) and gate else "single"
        if "gate_selection_reason" not in applied:
            reason = trace_payload.get("gate_selection_reason")
            applied["gate_selection_reason"] = reason if isinstance(reason, str) and reason else None
    summary = trace_payload.get("summary") or {}
    next_actions = trace_payload.get("next_actions") or []
    out: dict[str, Any]
    if compact:
        out = _compact_manual_find_output(
            trace_id=trace_id,
            summary=summary,
            applied=applied,
            next_actions=next_actions,
        )
    else:
        out = {
            "trace_id": trace_id,
            "summary": summary,
            "next_actions": next_actions,
            "applied": applied,
        }
    if not compact:
        selected_gate_out = trace_payload.get("selected_gate")
        if not isinstance(selected_gate_out, str):
            selected_gate_out = applied.get("selected_gate")
        if isinstance(selected_gate_out, str):
            out["selected_gate"] = selected_gate_out
        gate_selection_reason_out = trace_payload.get("gate_selection_reason")
        if not isinstance(gate_selection_reason_out, str):
            gate_selection_reason_out = applied.get("gate_selection_reason")
        if isinstance(gate_selection_reason_out, str):
            out["gate_selection_reason"] = gate_selection_reason_out
    if include_claim_graph and not compact:
        out["claim_graph"] = (trace_payload.get("claim_graph") or {})
    return out


def _cached_trace_payload_and_source_latency(value: Any) -> tuple[dict[str, Any] | None, int | None]:
    if not isinstance(value, dict):
        return None, None
    trace_payload = value.get("trace_payload")
    if not isinstance(trace_payload, dict):
        return None, None
    source_latency_raw = value.get("source_latency_ms")
    source_latency_ms: int | None = None
    if isinstance(source_latency_raw, (int, float)) and not isinstance(source_latency_raw, bool):
        source_latency_ms = max(0, int(source_latency_raw))
    return trace_payload, source_latency_ms


def _apply_cached_request_overrides(
    *,
    trace_payload: dict[str, Any],
    requested_expand_scope: bool | None,
) -> dict[str, Any]:
    patched = copy.deepcopy(trace_payload)
    patched["requested_expand_scope"] = requested_expand_scope
    applied = patched.get("applied")
    if isinstance(applied, dict):
        applied["requested_expand_scope"] = requested_expand_scope
        patched["applied"] = applied
    return patched


def _apply_sem_cache_diagnostics_to_trace_payload(
    *,
    trace_payload: dict[str, Any],
    sem_cache_used: bool,
    sem_cache_hit: bool,
    sem_cache_mode: str,
    sem_cache_score: float | None,
    sem_cache_latency_saved_ms: int | None,
) -> dict[str, Any]:
    patched = copy.deepcopy(trace_payload)
    applied = patched.get("applied")
    if not isinstance(applied, dict):
        return patched
    applied["sem_cache_used"] = sem_cache_used
    applied["sem_cache_hit"] = sem_cache_hit
    applied["sem_cache_mode"] = sem_cache_mode
    applied["sem_cache_score"] = round(sem_cache_score, 4) if sem_cache_score is not None else None
    applied["sem_cache_latency_saved_ms"] = sem_cache_latency_saved_ms
    patched["applied"] = applied
    return patched


def _cached_summary_is_acceptable(state: AppState, summary: dict[str, Any]) -> bool:
    try:
        gap_count = int(summary.get("gap_count", 0))
    except (TypeError, ValueError):
        gap_count = 0
    try:
        conflict_count = int(summary.get("conflict_count", 0))
    except (TypeError, ValueError):
        conflict_count = 0
    if state.config.sem_cache_max_summary_gap >= 0 and gap_count > state.config.sem_cache_max_summary_gap:
        return False
    if state.config.sem_cache_max_summary_conflict >= 0 and conflict_count > state.config.sem_cache_max_summary_conflict:
        return False
    return True


MANUAL_FIND_INLINE_HITS_LIMIT_DEFAULT = 5
MANUAL_FIND_INLINE_HITS_LIMIT_MAX = 5


def _parse_manual_find_inline_hits_param(inline_hits: Any) -> dict[str, Any] | None:
    if inline_hits is None:
        return None
    if not isinstance(inline_hits, dict):
        raise ToolError("invalid_parameter", "inline_hits must be object")
    limit = _parse_int_param(
        inline_hits.get("limit"),
        name="inline_hits.limit",
        default=MANUAL_FIND_INLINE_HITS_LIMIT_DEFAULT,
        min_value=1,
    )
    return {
        "kind": "integrated_top",
        "offset": 0,
        "limit": min(limit, MANUAL_FIND_INLINE_HITS_LIMIT_MAX),
    }


def _attach_manual_find_inline_hits(
    *,
    state: AppState,
    out: dict[str, Any],
    trace_id: str,
    inline_hits_spec: dict[str, Any] | None,
) -> dict[str, Any]:
    if inline_hits_spec is None:
        return out
    inline_hits = manual_hits(
        state,
        trace_id=trace_id,
        kind=str(inline_hits_spec.get("kind") or "integrated_top"),
        offset=int(inline_hits_spec.get("offset") or 0),
        limit=int(inline_hits_spec.get("limit") or MANUAL_FIND_INLINE_HITS_LIMIT_DEFAULT),
        compact=True,
    )
    out_with_inline = dict(out)
    out_with_inline["inline_hits"] = inline_hits
    return out_with_inline


def _record_manual_find_stats(
    state: AppState,
    *,
    query: str,
    summary: dict[str, Any],
    scanned_files: int,
    scanned_nodes: int,
    candidates_count: int,
    warnings: int,
    max_stage_applied: int,
    scope_expanded: bool,
    cutoff_reason: str | None,
    unscanned_sections_count: int,
    candidate_low_threshold: int,
    file_bias_threshold: float,
    sem_cache_hit: bool,
    sem_cache_mode: str,
    sem_cache_score: float | None,
    latency_saved_ms: int | None,
    scoring_mode: str = "heuristic",
    index_rebuilt: bool = False,
    index_docs: int | None = None,
) -> None:
    chars_in = len(query)
    chars_out = len(str(summary))
    added_est_tokens = chars_out // 4
    marginal_gain = (candidates_count / added_est_tokens) if added_est_tokens > 0 else None
    state.adaptive_stats.append(
        {
            "ts": int(time.time() * 1000),
            "query_hash": hashlib.sha256(query.encode("utf-8")).hexdigest()[:16],
            "scanned_files": scanned_files,
            "scanned_nodes": scanned_nodes,
            "candidates": candidates_count,
            "warnings": warnings,
            "max_stage_applied": max_stage_applied,
            "scope_expanded": scope_expanded,
            "cutoff_reason": cutoff_reason,
            "unscanned_sections_count": unscanned_sections_count,
            "est_tokens": (chars_in + chars_out + 3) // 4,
            "est_tokens_in": (chars_in + 3) // 4,
            "est_tokens_out": (chars_out + 3) // 4,
            "added_evidence_count": candidates_count,
            "added_est_tokens": added_est_tokens,
            "marginal_gain": round(marginal_gain, 4) if marginal_gain is not None else None,
            "candidate_low_threshold": candidate_low_threshold,
            "file_bias_threshold": file_bias_threshold,
            "sem_cache_hit": sem_cache_hit,
            "sem_cache_mode": sem_cache_mode,
            "sem_cache_score": round(sem_cache_score, 4) if sem_cache_score is not None else None,
            "latency_saved_ms": latency_saved_ms,
            "scoring_mode": scoring_mode,
            "index_rebuilt": index_rebuilt,
            "index_docs": index_docs,
        }
    )


def _trim_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _encode_node_segment(value: str) -> str:
    raw = value.encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_node_segment(value: str) -> str:
    padded = value + ("=" * ((4 - (len(value) % 4)) % 4))
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except Exception:
        raise ToolError("invalid_parameter", "invalid id")


def _manual_root_id(manual_id: str) -> str:
    return manual_id


def _manual_dir_id(manual_id: str, relative_dir: str) -> str:
    return f"dir::{manual_id}::{_encode_node_segment(relative_dir)}"


def _manual_file_id(manual_id: str, relative_path: str) -> str:
    return f"file::{manual_id}::{_encode_node_segment(relative_path)}"


def _parse_manual_ls_id(id_value: Any) -> tuple[str, str, str | None]:
    if not isinstance(id_value, str):
        raise ToolError("invalid_parameter", "id must be string")
    id_value = id_value.strip()
    if not id_value:
        raise ToolError("invalid_parameter", "id must be string")
    if id_value == "manuals":
        return "manuals", "", None
    if id_value.startswith("dir::"):
        parts = id_value.split("::", 2)
        ensure(len(parts) == 3, "invalid_parameter", "invalid id")
        head, manual_id, encoded = parts
        ensure(head == "dir" and bool(manual_id) and bool(encoded), "invalid_parameter", "invalid id")
        manual_id = _require_manual_id(manual_id, name="id")
        relative_dir = _decode_node_segment(encoded)
        return "dir", manual_id, normalize_relative_path(relative_dir)
    if id_value.startswith("file::"):
        parts = id_value.split("::", 2)
        ensure(len(parts) == 3, "invalid_parameter", "invalid id")
        head, manual_id, encoded = parts
        ensure(head == "file" and bool(manual_id) and bool(encoded), "invalid_parameter", "invalid id")
        manual_id = _require_manual_id(manual_id, name="id")
        relative_path = _decode_node_segment(encoded)
        return "file", manual_id, normalize_relative_path(relative_path)
    # Plain manual id (ex: "m1") for top-level manual nodes.
    return "manual", _require_manual_id(id_value, name="id"), ""


def manual_ls(state: AppState, id: str | None = None) -> dict[str, Any]:
    applied_id = id or "manuals"
    node_kind, manual_id, relative = _parse_manual_ls_id(applied_id)

    if node_kind == "manuals":
        manual_ids = discover_manual_ids(state.config.manuals_root)
        state.manual_root_ids = set(manual_ids)
        return {
            "id": "manuals",
            "items": [
                {"id": _manual_root_id(mid), "name": mid, "kind": "dir"}
                for mid in manual_ids
            ],
        }

    ensure(_manual_exists(state.config.manuals_root, manual_id), "not_found", "manual_id not found", {"manual_id": manual_id})
    manual_root = state.config.manuals_root / manual_id

    if node_kind == "file":
        raise ToolError("invalid_parameter", "file id cannot be expanded")

    base_dir = manual_root if not relative else resolve_inside_root(manual_root, relative, must_exist=True)
    ensure(base_dir.is_dir(), "not_found", "directory not found")

    items: list[dict[str, Any]] = []
    for child in sorted(base_dir.iterdir(), key=lambda p: p.name):
        if child.is_symlink():
            continue
        child_name = child.name
        child_rel = child_name if not relative else f"{relative}/{child_name}"
        if child.is_dir():
            items.append(
                {
                    "id": _manual_dir_id(manual_id, child_rel),
                    "name": child_name,
                    "kind": "dir",
                }
            )
            continue
        if not child.is_file():
            continue
        suffix = child.suffix.casefold()
        if suffix not in {".md", ".json"}:
            continue
        items.append(
            {
                "id": _manual_file_id(manual_id, child_rel),
                "name": child_name,
                "kind": "file",
                "path": child_rel,
                "file_type": suffix[1:],
            }
        )

    return {
        "id": applied_id,
        "items": items,
    }


def _manual_exists(root: Path, manual_id: str) -> bool:
    return (root / manual_id).is_dir()


def _manual_ls_next_hint(candidate_ids: set[str]) -> str:
    if not candidate_ids:
        return " Next call manual_ls(id=...) with one of manual_ls(id='manuals').items[].id."
    preview = sorted(candidate_ids)[:3]
    examples = " | ".join(f"manual_ls(id='{mid}')" for mid in preview)
    if len(candidate_ids) > len(preview):
        examples = f"{examples} | ..."
    return f" Next allowed call: {examples}."


def _ensure_not_manuals_root_id(state: AppState, manual_id: str) -> None:
    if manual_id != "manuals":
        return
    hint = _manual_ls_next_hint(state.manual_root_ids)
    details = {"candidate_ids": sorted(state.manual_root_ids)} if state.manual_root_ids else None
    raise ToolError(
        "invalid_parameter",
        "manual_id='manuals' is a root id; use a manual directory id from manual_ls(id='manuals').items[].id."
        f"{hint}",
        details,
    )


def _normalize_toc_cursor(cursor: Any) -> dict[str, Any]:
    if cursor is None:
        return {}
    if isinstance(cursor, dict):
        return cursor
    if isinstance(cursor, (int, str)):
        return {"offset": _parse_int_param(cursor, name="cursor", default=0, min_value=0)}
    raise ToolError("invalid_parameter", "cursor must be an object (offset) or an integer/string offset")


def manual_toc(
    state: AppState,
    manual_id: str,
    path_prefix: str | None = None,
    max_files: int | None = None,
    cursor: dict[str, Any] | int | str | None = None,
    depth: str | None = None,
    max_headings_per_file: int | None = None,
) -> dict[str, Any]:
    applied_manual_id = _require_manual_id(manual_id, name="manual_id")
    _ensure_not_manuals_root_id(state, applied_manual_id)
    ensure(
        _manual_exists(state.config.manuals_root, applied_manual_id),
        "not_found",
        "manual_id not found",
        {"manual_id": applied_manual_id},
    )

    applied_path_prefix = normalize_relative_path(path_prefix) if isinstance(path_prefix, str) and path_prefix.strip() else ""
    if depth is None:
        applied_depth = "shallow"
    else:
        ensure(isinstance(depth, str), "invalid_parameter", "depth must be shallow or deep")
        applied_depth = depth.strip().lower()
        ensure(applied_depth in {"shallow", "deep"}, "invalid_parameter", "depth must be shallow or deep")
    applied_include_headings = applied_depth == "deep"
    if applied_include_headings:
        ensure(bool(applied_path_prefix), "invalid_parameter", "path_prefix is required when depth=deep")
    max_limit = TOC_MAX_FILES_DEEP if applied_include_headings else TOC_MAX_FILES_WITHOUT_HEADINGS
    default_max_files = min(TOC_DEFAULT_MAX_FILES, max_limit)
    applied_max_files = _parse_int_param(
        max_files,
        name="max_files",
        default=default_max_files,
        min_value=1,
        max_value=max_limit,
    )
    if not applied_path_prefix and applied_max_files > TOC_MAX_FILES_WITHOUT_PREFIX:
        raise ToolError(
            "invalid_parameter",
            f"max_files must be <= {TOC_MAX_FILES_WITHOUT_PREFIX} when path_prefix is empty",
        )
    applied_max_headings_per_file = _parse_int_param(
        max_headings_per_file,
        name="max_headings_per_file",
        default=TOC_DEFAULT_MAX_HEADINGS_PER_FILE,
        min_value=1,
        max_value=TOC_MAX_HEADINGS_PER_FILE,
    )
    cursor_obj = _normalize_toc_cursor(cursor)
    offset = _parse_int_param(cursor_obj.get("offset"), name="cursor.offset", default=0, min_value=0)

    items: list[dict[str, Any]] = []
    files = sorted(list_manual_files(state.config.manuals_root, manual_id=applied_manual_id), key=lambda row: row.path)
    if applied_path_prefix:
        prefix = f"{applied_path_prefix}/"
        files = [row for row in files if row.path == applied_path_prefix or row.path.startswith(prefix)]
    ensure(
        len(files) <= TOC_SCOPE_HARD_LIMIT,
        "needs_narrow_scope",
        f"toc scope too large: {len(files)} files (limit={TOC_SCOPE_HARD_LIMIT}); narrow path_prefix",
    )
    ensure(offset <= len(files), "invalid_parameter", "cursor.offset out of range")
    page = files[offset : offset + applied_max_files]
    for row in page:
        file_path = resolve_inside_root(state.config.manuals_root / applied_manual_id, row.path, must_exist=True)
        headings: list[dict[str, Any]] = []
        if applied_include_headings:
            text = file_path.read_text(encoding="utf-8")
            if row.file_type == "md":
                for node in parse_markdown_toc(row.path, text)[:applied_max_headings_per_file]:
                    headings.append({"title": node.title, "line_start": node.line_start})
            else:
                headings.append({"title": Path(row.path).name, "line_start": 1})
        items.append({"path": row.path, "headings": headings})
    next_offset = offset + len(page)
    next_cursor = {"offset": next_offset} if next_offset < len(files) else None
    return {
        "items": items,
        "total_files": len(files),
        "next_cursor": next_cursor,
        "applied": {
            "manual_id": applied_manual_id,
            "path_prefix": applied_path_prefix,
            "depth": applied_depth,
            "max_files": applied_max_files,
            "include_headings": applied_include_headings,
            "max_headings_per_file": applied_max_headings_per_file,
            "offset": offset,
        },
    }


def _find_md_node(nodes: list[MdNode], start_line: Any) -> MdNode:
    if start_line is None:
        return nodes[0]
    parsed_start_line = _parse_int_param(start_line, name="ref.start_line", default=1, min_value=1)
    for node in nodes:
        if node.line_start == parsed_start_line:
            return node
    raise ToolError("not_found", "section not found for ref.start_line", {"start_line": parsed_start_line})


def _char_offset_from_line(text: str, line_no: int) -> int:
    if line_no <= 1:
        return 0
    offset = 0
    for _ in range(1, line_no):
        next_break = text.find("\n", offset)
        if next_break < 0:
            raise ToolError("invalid_parameter", "start_line out of range")
        offset = next_break + 1
    return offset


def _line_from_char_offset(text: str, offset: int) -> int:
    if not text:
        return 1
    bounded = min(max(0, offset), len(text))
    return text.count("\n", 0, bounded) + 1


def _normalize_scan_cursor(cursor: Any) -> dict[str, Any]:
    if cursor is None:
        return {}
    if isinstance(cursor, dict):
        return cursor
    if isinstance(cursor, (int, str)):
        return {
            "char_offset": _parse_int_param(
                cursor,
                name="cursor",
                default=0,
                min_value=0,
            )
        }
    raise ToolError(
        "invalid_parameter",
        "cursor must be an object (char_offset/start_line) or an integer/string char_offset",
    )


def manual_read(
    state: AppState,
    ref: dict[str, Any],
    scope: str | None = None,
    allow_file: bool | None = None,
    expand: dict[str, Any] | None = None,
    max_chars: int | None = None,
) -> dict[str, Any]:
    ensure(isinstance(ref, dict), "invalid_parameter", "ref must be object")
    ref = dict(ref)
    ref.pop("target", None)
    manual_id = _require_manual_id(ref.get("manual_id"), name="ref.manual_id")
    _ensure_not_manuals_root_id(state, manual_id)
    ensure(_manual_exists(state.config.manuals_root, manual_id), "not_found", "manual_id not found", {"manual_id": manual_id})
    path_value = ref.get("path")
    if not isinstance(path_value, str):
        raise ToolError("invalid_path", "ref.path must be a string")
    relative_path = normalize_relative_path(path_value)
    full_path = resolve_inside_root(state.config.manuals_root / manual_id, relative_path, must_exist=True)
    ensure(full_path.exists() and full_path.is_file(), "not_found", "manual file not found", {"path": relative_path})

    suffix = full_path.suffix.casefold()
    if scope not in {None, "section"}:
        raise ToolError("invalid_parameter", "manual_read scope is fixed to section")
    if allow_file is not None:
        raise ToolError("invalid_parameter", "allow_file is not supported; manual_read is section-only")
    if expand is not None:
        raise ToolError("invalid_parameter", "expand is not supported; manual_read is section-only")

    text = full_path.read_text(encoding="utf-8")
    applied_scope = "section"
    applied_max_chars = _parse_int_param(
        max_chars,
        name="max_chars",
        default=READ_MAX_CHARS,
        min_value=MANUAL_IO_MAX_CHARS_MIN,
        max_value=MANUAL_IO_MAX_CHARS_MAX,
    )
    truncated = False
    output = ""
    applied_mode = "read"

    if suffix == ".json":
        raise ToolError("invalid_scope", "manual_read supports markdown sections only; use manual_scan for json")
    else:
        lines = text.splitlines()
        nodes = parse_markdown_toc(relative_path, text)
        target = _find_md_node(nodes, ref.get("start_line"))
        key = f"{manual_id}:{relative_path}"
        section_start = target.line_start
        section_end = target.line_end
        progress = state.read_progress.get(key)
        overlap = bool(
            progress
            and progress.get("last_section_start") is not None
            and progress.get("last_section_end") is not None
            and not (
                section_end < int(progress["last_section_start"] or 1)
                or section_start > int(progress["last_section_end"] or 1)
            )
        )
        if overlap:
            fallback_start = int(progress.get("next_scan_start") or (section_end + 1))
            if fallback_start <= len(lines):
                scan = manual_scan(
                    state,
                    manual_id=manual_id,
                    path=relative_path,
                    start_line=fallback_start,
                    max_chars=applied_max_chars,
                )
                output = str(scan.get("text") or "")
                truncated = bool(scan.get("truncated"))
                applied_mode = "scan_fallback"
                applied_range = scan.get("applied_range") or {}
                eof = bool(scan.get("eof"))
                next_scan_start = (len(lines) + 1) if eof else (int(applied_range.get("end_line") or section_end) + 1)
                state.read_progress[key] = {
                    "last_section_start": section_start,
                    "last_section_end": section_end,
                    "next_scan_start": int(next_scan_start),
                }
            else:
                output = "\n".join(lines[target.line_start - 1 : target.line_end])
        else:
            output = "\n".join(lines[target.line_start - 1 : target.line_end])
            state.read_progress[key] = {
                "last_section_start": section_start,
                "last_section_end": section_end,
                "next_scan_start": section_end + 1,
            }

        if applied_mode == "read":
            output, truncated = _trim_text(output, applied_max_chars)

    return {
        "text": output,
        "truncated": truncated,
        "applied": {
            "scope": applied_scope,
            "max_sections": None,
            "max_chars": applied_max_chars,
            "mode": applied_mode,
        },
    }


def manual_scan(
    state: AppState,
    manual_id: str,
    path: str,
    start_line: int | None = None,
    cursor: dict[str, Any] | int | str | None = None,
    max_chars: int | None = None,
) -> dict[str, Any]:
    applied_manual_id = _require_manual_id(manual_id, name="manual_id")
    _ensure_not_manuals_root_id(state, applied_manual_id)
    ensure(
        _manual_exists(state.config.manuals_root, applied_manual_id),
        "not_found",
        "manual_id not found",
        {"manual_id": applied_manual_id},
    )
    relative_path = normalize_relative_path(path)
    full_path = resolve_inside_root(state.config.manuals_root / applied_manual_id, relative_path, must_exist=True)
    ensure(full_path.exists() and full_path.is_file(), "not_found", "manual file not found", {"path": relative_path})

    text = full_path.read_text(encoding="utf-8")
    applied_max_chars = _parse_int_param(
        max_chars,
        name="max_chars",
        default=SCAN_MAX_CHARS,
        min_value=MANUAL_IO_MAX_CHARS_MIN,
        max_value=MANUAL_IO_MAX_CHARS_MAX,
    )
    normalized_cursor = _normalize_scan_cursor(cursor)
    if start_line is not None:
        parsed_start_line = _parse_int_param(
            start_line,
            name="start_line",
            default=1,
            min_value=1,
        )
        applied_start_offset = _char_offset_from_line(text, parsed_start_line)
    elif normalized_cursor.get("char_offset") is not None:
        applied_start_offset = _parse_int_param(
            normalized_cursor.get("char_offset"),
            name="cursor.char_offset",
            default=0,
            min_value=0,
        )
    elif normalized_cursor.get("start_line") is not None:
        parsed_start_line = _parse_int_param(
            normalized_cursor.get("start_line"),
            name="cursor.start_line",
            default=1,
            min_value=1,
        )
        applied_start_offset = _char_offset_from_line(text, parsed_start_line)
    else:
        applied_start_offset = 0

    ensure(applied_start_offset <= len(text), "invalid_parameter", "cursor.char_offset out of range")
    end_offset = min(len(text), applied_start_offset + applied_max_chars)
    chunk_text = text[applied_start_offset:end_offset]

    start_line_no = _line_from_char_offset(text, applied_start_offset)
    if end_offset <= applied_start_offset:
        end_line_no = start_line_no
    else:
        end_line_no = _line_from_char_offset(text, end_offset - 1)

    truncated_reason = "none" if end_offset >= len(text) else "max_chars"
    eof = end_offset >= len(text)

    return {
        "manual_id": applied_manual_id,
        "path": relative_path,
        "text": chunk_text,
        "applied_range": {"start_line": start_line_no, "end_line": end_line_no},
        "next_cursor": {"char_offset": None if eof else end_offset},
        "eof": eof,
        "truncated": truncated_reason != "none",
        "truncated_reason": truncated_reason,
        "applied": {"max_chars": applied_max_chars},
    }


def _candidate_key(item: dict[str, Any]) -> str:
    ref = item["ref"]
    return f'{ref["manual_id"]}|{ref["path"]}|{ref.get("start_line") or 1}'


def _infer_claim_facets(query: str, candidates: list[dict[str, Any]]) -> list[str]:
    query_norm = normalize_text(query)
    raw_query = query.strip()
    ordered: list[str] = []

    def add(facet: str) -> None:
        if facet in FACET_ORDER and facet not in ordered:
            ordered.append(facet)

    for facet, hints in FACET_HINTS.items():
        if any(hint in query_norm for hint in hints):
            add(facet)

    if (
        QUERY_DECOMP_COMPARE_DIFF_RE.match(raw_query) is not None
        or QUERY_DECOMP_COMPARE_KEYWORD_RE.match(raw_query) is not None
        or QUERY_DECOMP_VS_RE.match(raw_query) is not None
    ):
        add("compare")

    if any("exceptions" in (item.get("signals") or []) for item in candidates):
        add("exceptions")

    if not ordered:
        add("unknown")

    return [facet for facet in FACET_ORDER if facet in ordered] or ["unknown"]


def _claim_texts_for_facet(query: str, facet: str) -> list[str]:
    base = query.strip()
    out: list[str] = []

    def add(text: str) -> None:
        value = text.strip()
        if value and value not in out:
            out.append(value)

    add(base)
    sub_queries = _query_decomp_subqueries(base, max_sub_queries=3)
    intents = [item.strip() for item in sub_queries[1:] if isinstance(item, str) and item.strip()]
    if facet == "compare":
        for intent in intents[:2]:
            add(intent)
        if len(intents) >= 2:
            add(f"{intents[0]} と {intents[1]}")
    elif facet in {"procedure", "eligibility", "exceptions"} and len(intents) >= 2:
        for intent in intents[:2]:
            add(intent)

    if not out:
        return [base] if base else []
    return out[:3]


def _claim_terms(text: str) -> set[str]:
    query_terms = split_terms(text)
    expanded_terms, _ = _expand_lexical_query_terms(query_terms)
    out = {
        normalized
        for normalized in (normalize_text(term) for term in expanded_terms)
        if normalized
    }
    if out:
        return out
    normalized_text = normalize_text(text)
    return {normalized_text} if normalized_text else set()


def _candidate_terms(candidate: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    matched_tokens = candidate.get("matched_tokens")
    if isinstance(matched_tokens, list):
        for token in matched_tokens:
            normalized = normalize_text(str(token))
            if normalized:
                out.add(normalized)
    token_hits = candidate.get("token_hits")
    if isinstance(token_hits, dict):
        for token in token_hits.keys():
            normalized = normalize_text(str(token))
            if normalized:
                out.add(normalized)
    return out


def _claim_coverage(candidate_terms: set[str], claim_terms: set[str]) -> float:
    if not claim_terms:
        return 0.0
    matched = len(candidate_terms.intersection(claim_terms))
    return matched / max(1, len(claim_terms))


def _facet_match_score(
    *,
    facet: str,
    query_norm: str,
    candidate_terms: set[str],
    signals: set[str],
    claim_coverage: float,
) -> float:
    hints = FACET_HINTS.get(facet, [])
    query_hint_hit = any(hint in query_norm for hint in hints)
    hint_hits = 0
    for hint in hints:
        if any(hint in term for term in candidate_terms):
            hint_hits += 1

    score = 0.0
    if query_hint_hit:
        score += 0.12
    if hint_hits > 0:
        score += 0.28 + min(0.20, 0.08 * float(hint_hits - 1))
    score += min(0.35, claim_coverage * 0.55)
    if facet == "exceptions" and "exceptions" in signals:
        score += 0.30
    if facet == "compare" and claim_coverage >= 0.40:
        score += 0.12
    if facet == "unknown":
        score = max(score, 0.18 + (claim_coverage * 0.45))
    return min(1.0, score)


def _candidate_has_facet_hint(candidate_terms: set[str], facet: str) -> bool:
    hints = FACET_HINTS.get(facet, [])
    if not hints or not candidate_terms:
        return False
    for hint in hints:
        if any(hint in term for term in candidate_terms):
            return True
    return False


def _candidate_score_norms(candidates: list[dict[str, Any]]) -> list[float]:
    if not candidates:
        return []
    raw_scores: list[float] = []
    for item in candidates:
        score_raw = item.get("score")
        if isinstance(score_raw, (int, float)) and not isinstance(score_raw, bool):
            raw_scores.append(float(score_raw))
        else:
            raw_scores.append(float(_candidate_rank_score(item)))
    min_score = min(raw_scores)
    max_score = max(raw_scores)
    if max_score <= min_score:
        return [1.0 if max_score > 0 else 0.0 for _ in raw_scores]
    denom = max_score - min_score
    return [max(0.0, min(1.0, (score - min_score) / denom)) for score in raw_scores]


def _relation_for_facet(
    *,
    facet: str,
    candidate: dict[str, Any],
    candidate_terms: set[str],
    facet_match_score: float,
    claim_coverage: float,
    score_norm: float,
) -> tuple[str, float] | None:
    signals = set(candidate.get("signals") or [])
    strong_hit = bool(signals.intersection(CLAIM_GRAPH_STRONG_SIGNALS))
    lexical_hit = bool("exact" in signals or strong_hit)
    has_exception = "exceptions" in signals
    compare_hint_hit = _candidate_has_facet_hint(candidate_terms, "compare")

    if not lexical_hit and facet_match_score < 0.20 and claim_coverage < 0.20:
        return None

    if facet == "exceptions":
        if has_exception and facet_match_score >= 0.30:
            relation = "supports"
        elif lexical_hit and claim_coverage >= 0.45:
            relation = "contradicts"
        elif facet_match_score >= 0.20 or claim_coverage >= 0.25:
            relation = "requires_followup"
        else:
            return None
    elif facet == "eligibility":
        if has_exception and claim_coverage >= 0.30:
            relation = "contradicts"
        elif lexical_hit and facet_match_score >= 0.26:
            relation = "supports"
        elif facet_match_score >= 0.18 or claim_coverage >= 0.25:
            relation = "requires_followup"
        else:
            return None
    elif facet == "compare":
        if lexical_hit and claim_coverage >= 0.45 and facet_match_score >= 0.30 and compare_hint_hit:
            relation = "supports"
        elif lexical_hit and claim_coverage >= 0.35 and facet_match_score >= 0.25:
            relation = "requires_followup"
        elif claim_coverage >= 0.20:
            relation = "requires_followup"
        else:
            return None
    elif facet == "unknown":
        if lexical_hit and score_norm >= 0.50 and claim_coverage >= 0.15:
            relation = "supports"
        elif claim_coverage >= 0.10 and score_norm >= 0.20:
            relation = "requires_followup"
        else:
            return None
    else:
        if lexical_hit and facet_match_score >= 0.24:
            relation = "supports"
        elif claim_coverage >= 0.20:
            relation = "requires_followup"
        else:
            return None

    base_conf = 0.25 + (0.35 * score_norm) + (0.30 * facet_match_score) + (0.10 * claim_coverage)
    if relation == "supports":
        confidence = base_conf + (0.08 if strong_hit else 0.02)
    elif relation == "contradicts":
        confidence = base_conf + 0.03
    else:
        confidence = 0.20 + (0.25 * facet_match_score) + (0.20 * claim_coverage) + (0.15 * score_norm)
    return relation, round(max(0.05, min(0.98, confidence)), 4)


def _build_claim_graph(
    *,
    query: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    facets = _infer_claim_facets(query, candidates)
    query_norm = normalize_text(query)
    score_norms = _candidate_score_norms(candidates)
    claims: list[dict[str, Any]] = []
    claim_terms_by_id: dict[str, set[str]] = {}
    claim_index = 0
    for facet in facets:
        for claim_text in _claim_texts_for_facet(query, facet):
            claim_index += 1
            claim_id = f"claim:{facet}:{claim_index}"
            claims.append(
                {
                    "claim_id": claim_id,
                    "facet": facet,
                    "text": f"{claim_text} [{facet}]",
                    "status": "unresolved",
                    "confidence": 0.0,
                }
            )
            claim_terms_by_id[claim_id] = _claim_terms(claim_text)

    evidences: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    claim_stats: dict[str, dict[str, float]] = {
        claim["claim_id"]: {
            "supports": 0,
            "contradicts": 0,
            "followups": 0,
            "support_conf_sum": 0.0,
            "contradict_conf_sum": 0.0,
            "followup_conf_sum": 0.0,
        }
        for claim in claims
    }

    for idx, candidate in enumerate(candidates, start=1):
        ref = candidate["ref"]
        evidence_id = f"ev:{idx}"
        score = float(candidate.get("score") or 0.0)
        score_norm = score_norms[idx - 1] if idx - 1 < len(score_norms) else 0.0
        signals = sorted(set(candidate.get("signals") or []))
        signal_set = set(signals)
        candidate_term_set = _candidate_terms(candidate)
        digest_input = f'{ref.get("manual_id")}|{ref.get("path")}|{ref.get("start_line") or 1}|{",".join(signals)}|{score}'
        evidences.append(
            {
                "evidence_id": evidence_id,
                "ref": {
                    "target": "manual",
                    "manual_id": ref.get("manual_id"),
                    "path": ref.get("path"),
                    "start_line": ref.get("start_line") or 1,
                },
                "signals": signals,
                "score": round(score, 4),
                "snippet_digest": hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:16],
            }
        )

        for claim in claims:
            claim_id = claim["claim_id"]
            facet = claim["facet"]
            claim_term_set = claim_terms_by_id.get(claim_id) or set()
            coverage = _claim_coverage(candidate_term_set, claim_term_set)
            facet_score = _facet_match_score(
                facet=facet,
                query_norm=query_norm,
                candidate_terms=candidate_term_set,
                signals=signal_set,
                claim_coverage=coverage,
            )
            relation_row = _relation_for_facet(
                facet=facet,
                candidate=candidate,
                candidate_terms=candidate_term_set,
                facet_match_score=facet_score,
                claim_coverage=coverage,
                score_norm=score_norm,
            )
            if relation_row is None:
                continue
            relation, edge_confidence = relation_row
            edges.append(
                {
                    "from_claim_id": claim_id,
                    "to_evidence_id": evidence_id,
                    "relation": relation,
                    "confidence": round(edge_confidence, 4),
                }
            )
            stats = claim_stats[claim_id]
            if relation == "supports":
                stats["supports"] += 1
                stats["support_conf_sum"] += edge_confidence
            elif relation == "contradicts":
                stats["contradicts"] += 1
                stats["contradict_conf_sum"] += edge_confidence
            else:
                stats["followups"] += 1
                stats["followup_conf_sum"] += edge_confidence

    for claim in claims:
        stats = claim_stats[claim["claim_id"]]
        supports = int(stats["supports"])
        contradicts = int(stats["contradicts"])
        followups = int(stats["followups"])
        avg_support = (stats["support_conf_sum"] / supports) if supports > 0 else 0.0
        total_edges = supports + contradicts + followups
        if supports > 0 and contradicts > 0:
            status = "conflicted"
        elif supports > 0 and followups <= supports:
            status = "supported"
        elif (
            supports > 0
            and contradicts == 0
            and avg_support >= CLAIM_GRAPH_STRONG_SUPPORT_MIN_AVG_CONFIDENCE
            and followups <= (supports + CLAIM_GRAPH_STRONG_SUPPORT_MAX_EXTRA_FOLLOWUPS)
        ):
            status = "supported"
        else:
            status = "unresolved"
        if total_edges <= 0:
            confidence = 0.0
        else:
            support_ratio = supports / total_edges
            contradict_ratio = contradicts / total_edges
            followup_ratio = followups / total_edges
            confidence = (
                (avg_support * 0.55)
                + (support_ratio * 0.45)
                - (contradict_ratio * 0.35)
                - (followup_ratio * 0.20)
            )
            if status == "conflicted":
                confidence *= 0.85
            elif status == "unresolved":
                confidence *= 0.75
        claim["status"] = status
        claim["confidence"] = round(max(0.0, min(1.0, confidence)), 4)

    facet_rows: list[dict[str, Any]] = []
    for facet in facets:
        rows = [claim for claim in claims if claim["facet"] == facet]
        supported = sum(1 for claim in rows if claim["status"] == "supported")
        conflicted = sum(1 for claim in rows if claim["status"] == "conflicted")
        unresolved = sum(1 for claim in rows if claim["status"] == "unresolved")
        coverage_status = "covered" if supported > 0 else ("partial" if (conflicted > 0 or unresolved > 0) else "missing")
        facet_rows.append(
            {
                "facet": facet,
                "claim_count": len(rows),
                "supported_count": supported,
                "conflicted_count": conflicted,
                "unresolved_count": unresolved,
                "coverage_status": coverage_status,
            }
        )

    return {
        "claims": claims,
        "evidences": evidences,
        "edges": edges,
        "facets": facet_rows,
    }


def _build_summary(
    claim_graph: dict[str, Any],
    candidates: list[dict[str, Any]],
    scanned_files: int,
    scanned_nodes: int,
    candidate_low_threshold: int,
    file_bias_threshold: float,
) -> dict[str, Any]:
    signal_counts = Counter()
    file_counts = Counter()
    for c in candidates:
        for s in c["signals"]:
            signal_counts[s] += 1
        file_counts[c["path"]] += 1
    total = len(candidates)
    file_bias = (max(file_counts.values()) / total) if total else 0.0
    exception_hits = signal_counts.get("exceptions", 0)
    claims = claim_graph.get("claims", [])
    edges = claim_graph.get("edges", [])
    unresolved_search_gap_claim_ids = {
        str(claim.get("claim_id") or "")
        for claim in claims
        if (
            isinstance(claim, dict)
            and claim.get("status") == "unresolved"
            and str(claim.get("claim_id") or "")
            and str(claim.get("facet") or "") not in CLAIM_GRAPH_SEARCH_GAP_EXCLUDED_FACETS
        )
    }
    conflicted_claim_count = sum(1 for c in claims if c.get("status") == "conflicted")
    unresolved_claim_count = len(unresolved_search_gap_claim_ids)
    contradict_claim_count = len({e.get("from_claim_id") for e in edges if e.get("relation") == "contradicts"})
    followup_claim_count = len(
        {
            claim_id
            for e in edges
            if e.get("relation") == "requires_followup"
            for claim_id in [str(e.get("from_claim_id") or "")]
            if claim_id and claim_id in unresolved_search_gap_claim_ids
        }
    )

    heuristic_gap_count = 0
    if (
        total == 0
        or total < candidate_low_threshold
        or (total >= 5 and file_bias >= file_bias_threshold)
    ):
        heuristic_gap_count = 1

    gap_count = max(heuristic_gap_count, unresolved_claim_count, followup_claim_count)
    conflict_count = max(conflicted_claim_count, contradict_claim_count)

    sufficiency_score = min(1.0, total / 5.0) * (1.0 - min(file_bias, 1.0) * 0.2)
    status = "ready" if (sufficiency_score >= 0.6 and gap_count == 0 and conflict_count == 0) else "needs_followup"
    if total == 0:
        status = "blocked"
    summary: dict[str, Any] = {
        "scanned_files": scanned_files,
        "scanned_nodes": scanned_nodes,
        "candidates": total,
        "file_bias_ratio": round(file_bias, 4),
        "conflict_count": conflict_count,
        "gap_count": gap_count,
        "integration_status": status,
    }
    return summary


def _build_retrieval_summary(
    *,
    candidates: list[dict[str, Any]],
    scanned_files: int,
    scanned_nodes: int,
    candidate_low_threshold: int,
    file_bias_threshold: float,
) -> dict[str, Any]:
    total, file_bias, _exception_hits = _candidate_metrics(candidates)
    heuristic_gap_count = 0
    if (
        total == 0
        or total < candidate_low_threshold
        or (total >= 5 and file_bias >= file_bias_threshold)
    ):
        heuristic_gap_count = 1
    sufficiency_score = min(1.0, total / 5.0) * (1.0 - min(file_bias, 1.0) * 0.2)
    status = "ready" if (sufficiency_score >= 0.6 and heuristic_gap_count == 0) else "needs_followup"
    if total == 0:
        status = "blocked"
    return {
        "scanned_files": scanned_files,
        "scanned_nodes": scanned_nodes,
        "candidates": total,
        "file_bias_ratio": round(file_bias, 4),
        "conflict_count": 0,
        "gap_count": heuristic_gap_count,
        "integration_status": status,
    }


def _claim_coverage_ratio(claim_graph: dict[str, Any]) -> float:
    claims = claim_graph.get("claims") or []
    if not isinstance(claims, list) or not claims:
        return 0.0
    supported = sum(1 for claim in claims if claim.get("status") == "supported")
    return supported / len(claims)


def _claim_coverage_ratio_for_search_gaps(claim_graph: dict[str, Any]) -> float:
    claims = claim_graph.get("claims") or []
    if not isinstance(claims, list):
        return 0.0
    filtered = [
        claim
        for claim in claims
        if isinstance(claim, dict) and str(claim.get("facet") or "") not in CLAIM_GRAPH_SEARCH_GAP_EXCLUDED_FACETS
    ]
    if not filtered:
        return 1.0
    supported = sum(1 for claim in filtered if claim.get("status") == "supported")
    return supported / len(filtered)


def _candidate_metrics(candidates: list[dict[str, Any]]) -> tuple[int, float, int]:
    total = len(candidates)
    if total == 0:
        return 0, 0.0, 0
    file_counts = Counter(item["path"] for item in candidates)
    file_bias = max(file_counts.values()) / total
    exception_hits = sum(1 for item in candidates if "exceptions" in item["signals"])
    return total, file_bias, exception_hits


def _should_expand_scope(
    *,
    total: int,
    file_bias: float,
    exception_hits: int,
    candidate_low_threshold: int,
    file_bias_threshold: float,
) -> bool:
    del exception_hits
    return (
        total == 0
        or total < candidate_low_threshold
        or (total >= 5 and file_bias >= file_bias_threshold)
    )


def _is_noise_path(path: str) -> bool:
    normalized = normalize_text(Path(path).name)
    return any(term in normalized for term in NOISE_PATH_TERMS)


def _split_cjk_compound_piece(piece: str) -> list[str]:
    if not piece or not LEXICAL_CJK_ONLY_RE.fullmatch(piece) or len(piece) < 4:
        return []

    out: list[str] = []
    seen: set[str] = set()

    def add(token: str) -> None:
        value = token.strip()
        if not value or value in seen:
            return
        seen.add(value)
        out.append(value)

    def split_suffixes(value: str) -> None:
        add(value)
        if len(value) < 3:
            return
        for suffix in CJK_COMPOUND_SUFFIXES:
            if len(value) <= len(suffix):
                continue
            if not value.endswith(suffix):
                continue
            prefix = value[: len(value) - len(suffix)]
            if prefix:
                split_suffixes(prefix)
            add(suffix)
            break

    split_suffixes(piece)
    return out


def _file_query_relevance_score(path: str, normalized_titles: set[str], lexical_terms: list[str]) -> float:
    normalized_path = normalize_text(path)
    if not normalized_path:
        return 0.0
    score = 0.0
    for term in set(lexical_terms):
        if len(term) < 2:
            continue
        if term in normalized_path:
            score += 2.0
        if normalized_titles and any(term in title for title in normalized_titles):
            score += 1.0
    return score


def _segment_query_term(term: str) -> list[str]:
    if not term:
        return []
    queue: list[str] = [term]
    # Split once on the linking particle to expose sub-intents in short noun phrases too.
    if "の" in term:
        split_parts = [part for part in term.split("の") if len(part) >= 2]
        if split_parts:
            queue = split_parts

    out: list[str] = []
    seen: set[str] = set()

    def add(token: str) -> None:
        value = token.strip()
        if not value or value in seen:
            return
        seen.add(value)
        out.append(value)

    for chunk in queue:
        parts = [part for part in LEXICAL_SPLIT_RE.split(chunk) if part]
        if not parts:
            parts = [chunk]
        for part in parts:
            script_chunks = [piece for piece in LEXICAL_SCRIPT_CHUNK_RE.findall(part) if piece]
            if not script_chunks:
                add(part)
                continue
            for piece in script_chunks:
                add(piece)
                for token in _split_cjk_compound_piece(piece):
                    add(token)

    return out


def _expand_lexical_query_terms(query_terms: list[str]) -> tuple[list[str], list[set[str]]]:
    seen: set[str] = set()
    ordered: list[str] = []
    coverage_groups: list[set[str]] = []

    def add(value: str, group: set[str]) -> None:
        token = value.strip()
        if not token:
            return
        group.add(token)
        if token not in seen:
            seen.add(token)
            ordered.append(token)

    for term in query_terms:
        candidate = normalize_text(term)
        if not candidate:
            continue
        variants = _segment_query_term(candidate)
        if not variants:
            variants = [candidate]
        group: set[str] = set()
        for variant in variants:
            add(variant, group)
            for number in NUMBER_PATTERN.findall(variant):
                add(number, group)
        if group:
            coverage_groups.append(group)

    if not ordered:
        fallback = [normalize_text(term) for term in query_terms if normalize_text(term)]
        for item in fallback:
            if item not in seen:
                seen.add(item)
                ordered.append(item)
            coverage_groups.append({item})

    return ordered, coverage_groups


def _is_kanji_char(ch: str) -> bool:
    return bool(ch) and bool(KANJI_CHAR_RE.fullmatch(ch))


def _is_hiragana_char(ch: str) -> bool:
    return bool(ch) and bool(HIRAGANA_CHAR_RE.fullmatch(ch))


def _expand_okurigana_variants(term: str) -> list[str]:
    normalized = normalize_text(term)
    if not normalized:
        return []

    out: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        item = value.strip()
        if not item or item in seen:
            return
        seen.add(item)
        out.append(item)

    add(normalized)
    if (
        len(normalized) >= 2
        and normalized[-1] in OKURIGANA_TRIM_SUFFIXES
        and _is_kanji_char(normalized[-2])
    ):
        add(normalized[:-1])

    # Example: 申し込み -> 申込み -> 申込
    for idx, ch in enumerate(normalized):
        if ch != "し":
            continue
        if idx == 0 or idx >= len(normalized) - 1:
            continue
        if not _is_kanji_char(normalized[idx - 1]):
            continue
        if not _is_hiragana_char(normalized[idx + 1]):
            continue
        removed = normalized[:idx] + normalized[idx + 1 :]
        add(removed)
        if len(removed) >= 2 and removed[-1] in OKURIGANA_TRIM_SUFFIXES and _is_kanji_char(removed[-2]):
            add(removed[:-1])
        break
    return out


def _required_term_pattern_groups(required_terms: list[str]) -> list[list[str]]:
    out: list[list[str]] = []
    for term in required_terms:
        variants = _expand_okurigana_variants(term)
        if variants:
            out.append(variants)
    return out


def _matches_required_term_groups(normalized_text: str, pattern_groups: list[list[str]]) -> bool:
    if not pattern_groups:
        return True
    for group in pattern_groups:
        if not any(pattern in normalized_text for pattern in group):
            return False
    return True


def _required_term_passes(required_terms: list[str]) -> list[tuple[str, list[str], float]]:
    if len(required_terms) <= 1:
        return [("single", list(required_terms), REQUIRED_TERM_RRF_SINGLE_WEIGHT)]
    left, right = required_terms[0], required_terms[1]
    return [
        ("and", [left, right], REQUIRED_TERM_RRF_AND_WEIGHT),
        ("single_a", [left], REQUIRED_TERM_RRF_SINGLE_WEIGHT),
        ("single_b", [right], REQUIRED_TERM_RRF_SINGLE_WEIGHT),
    ]


def _required_term_doc_freq(sparse_index: SparseIndex, pattern_group: list[str]) -> int:
    if sparse_index.total_docs <= 0 or not pattern_group:
        return 0
    doc_freq = 0
    for doc in sparse_index.docs:
        normalized_text = doc.normalized_text
        if not normalized_text:
            continue
        if any(pattern in normalized_text for pattern in pattern_group):
            doc_freq += 1
    return doc_freq


def _filter_required_terms_by_df(
    *,
    required_terms: list[str],
    sparse_index: SparseIndex,
) -> tuple[list[str], list[dict[str, Any]]]:
    if not required_terms or sparse_index.total_docs <= 0:
        return list(required_terms), []

    total_docs = max(1, int(sparse_index.total_docs))
    pattern_groups = _required_term_pattern_groups(required_terms)
    kept: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    for idx, term in enumerate(required_terms):
        patterns = pattern_groups[idx] if idx < len(pattern_groups) and pattern_groups[idx] else [term]
        doc_freq = _required_term_doc_freq(sparse_index, patterns)
        doc_freq_ratio = doc_freq / float(total_docs)
        reason: str | None = None
        dropped = False
        if doc_freq > 0 and doc_freq_ratio < REQUIRED_TERM_DF_MIN_RATIO:
            reason = "too_rare"
        elif total_docs >= REQUIRED_TERM_DF_COMMON_MIN_DOCS and doc_freq_ratio > REQUIRED_TERM_DF_MAX_RATIO:
            reason = "too_common"
            dropped = True
        if not dropped:
            kept.append(term)
        if reason is None:
            continue
        diagnostics.append(
            {
                "term": term,
                "doc_freq": int(doc_freq),
                "doc_freq_ratio": round(doc_freq_ratio, 4),
                "reason": reason,
                "dropped": bool(dropped),
            }
        )
    return kept, diagnostics


def _required_term_match_stats(
    *,
    required_terms: list[str],
    sparse_index: SparseIndex,
) -> list[dict[str, Any]]:
    if not required_terms:
        return []
    total_docs = max(1, int(sparse_index.total_docs))
    pattern_groups = _required_term_pattern_groups(required_terms)
    stats: list[dict[str, Any]] = []
    for idx, term in enumerate(required_terms):
        patterns = pattern_groups[idx] if idx < len(pattern_groups) and pattern_groups[idx] else [term]
        doc_freq = _required_term_doc_freq(sparse_index, patterns)
        stats.append(
            {
                "term": term,
                "matched_docs": int(doc_freq),
                "matched_doc_ratio": round(float(doc_freq) / float(total_docs), 4),
            }
        )
    return stats


def _required_match_count_from_pass_label(pass_label: str) -> int:
    label = str(pass_label or "")
    if "and" in label:
        return 2
    return 1


def _match_coverage_ratio(matched_terms: set[str], coverage_groups: list[set[str]]) -> float:
    if not coverage_groups:
        return 0.0
    matched_groups = 0
    for group in coverage_groups:
        if not group:
            continue
        if matched_terms.intersection(group):
            matched_groups += 1
    return matched_groups / max(1, len(coverage_groups))


def _is_code_like_term(term: str) -> bool:
    return bool(CODE_TOKEN_RE.fullmatch(term))


def _compile_code_pattern(term: str) -> re.Pattern[str]:
    match = re.fullmatch(r"([a-z]+)(\d+)([a-z]?)", term)
    if match is None:
        return re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])")
    prefix, number, suffix = match.groups()
    suffix_part = re.escape(suffix) if suffix else ""
    # Accept mild separators between alpha and numeric blocks (K867, K-867, K 867).
    return re.compile(
        rf"(?<![a-z0-9]){re.escape(prefix)}[\s\-_]*{re.escape(number)}{suffix_part}(?![a-z0-9])"
    )


def _prf_expand_terms(
    *,
    sparse_index: SparseIndex,
    query_terms: set[str],
    missing_terms: set[str],
) -> list[str]:
    if not query_terms or not missing_terms or sparse_index.total_docs <= 0:
        return []
    seed_terms = {term for term in query_terms if len(term) >= 2}
    if not seed_terms:
        return []
    bm25 = bm25_scores(sparse_index, query_terms=seed_terms)
    if not bm25:
        return []
    ranked = sorted(
        ((doc_id, score) for doc_id, score in bm25.items() if score > 0.0),
        key=lambda row: (
            -float(row[1]),
            str(sparse_index.docs[int(row[0])].path),
            int(sparse_index.docs[int(row[0])].start_line),
        ),
    )[:PRF_TOP_DOCS]
    if not ranked:
        return []

    total_docs = max(1, sparse_index.total_docs)
    candidate_gain: dict[str, float] = {}
    for doc_id, score in ranked:
        doc = sparse_index.docs[int(doc_id)]
        base = float(score)
        if base <= 0.0:
            continue
        for term, tf in doc.term_freq.items():
            if term in seed_terms or len(term) < 2:
                continue
            if _is_code_like_term(term):
                continue
            if term.isdigit():
                continue
            if not PRF_TERM_SHAPE_RE.fullmatch(term):
                continue
            if len(term) <= 2:
                continue
            df = int(sparse_index.doc_freq.get(term, 0))
            if df <= 0:
                continue
            if (float(df) / float(total_docs)) > PRF_TERM_MAX_DF_RATIO:
                continue
            idf = _idf(total_docs, df)
            tfw = _bm25_tf_weight(int(tf), doc_len=int(doc.doc_len), avg_doc_len=float(sparse_index.avg_doc_len))
            gain = base * idf * tfw
            if gain <= 0.0:
                continue
            candidate_gain[term] = candidate_gain.get(term, 0.0) + gain

    expanded = sorted(candidate_gain.items(), key=lambda row: (-row[1], row[0]))
    return [term for term, _ in expanded[:PRF_MAX_TERMS]]


def _idf(total_docs: int, doc_freq: int) -> float:
    return math.log((float(total_docs) + 1.0) / (float(doc_freq) + 1.0)) + 1.0


def _bm25_tf_weight(
    term_freq: int,
    *,
    doc_len: int,
    avg_doc_len: float,
    k1: float = 1.2,
    b: float = 0.75,
) -> float:
    tf = max(0.0, float(term_freq))
    if tf <= 0.0:
        return 0.0
    doc_len_f = max(1.0, float(doc_len))
    avgdl = max(1.0, float(avg_doc_len))
    denom = tf + (k1 * (1.0 - b + b * (doc_len_f / avgdl)))
    if denom <= 0.0:
        return 0.0
    return (tf * (k1 + 1.0)) / denom


def _candidate_rank_score(item: dict[str, Any]) -> float:
    raw = item.get("_rank_score")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    score = item.get("score")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        return float(score)
    return 0.0


def _set_candidate_component_scores(
    item: dict[str, Any],
    *,
    score_lexical: float | None,
    score_fused: float | None,
) -> None:
    if score_lexical is not None:
        item["score_lexical"] = round(float(score_lexical), 4)
    if score_fused is not None:
        item["score_fused"] = round(float(score_fused), 4)


def _annotate_lexical_candidate_scores(items: list[dict[str, Any]]) -> None:
    for item in items:
        score = _candidate_rank_score(item)
        _set_candidate_component_scores(
            item,
            score_lexical=score,
            score_fused=score,
        )


def _min_max_normalize(value: float, *, min_value: float, max_value: float) -> float:
    denom = max_value - min_value
    if abs(denom) <= 1e-9:
        return 0.5
    normalized = (value - min_value) / denom
    return max(0.0, min(1.0, float(normalized)))


def _blend_query_decomp_rrf_score(
    *,
    base_score: float,
    rrf_score: float,
    base_min: float,
    base_max: float,
    rrf_min: float,
    rrf_max: float,
    base_weight: float,
) -> tuple[float, float, float]:
    alpha = max(0.0, min(1.0, float(base_weight)))
    base_norm = _min_max_normalize(base_score, min_value=base_min, max_value=base_max)
    rrf_norm = _min_max_normalize(rrf_score, min_value=rrf_min, max_value=rrf_max)
    blended_norm = (alpha * base_norm) + ((1.0 - alpha) * rrf_norm)

    base_range = base_max - base_min
    if base_range > 1e-9:
        merged_score = base_min + (blended_norm * base_range)
        return float(merged_score), float(base_norm), float(rrf_norm)

    rrf_range = rrf_max - rrf_min
    if rrf_range > 1e-9:
        spread = max(0.5, abs(base_score) * 0.05)
        merged_score = base_score + ((rrf_norm - 0.5) * spread)
        return float(merged_score), float(base_norm), float(rrf_norm)

    return float(base_score), float(base_norm), float(rrf_norm)


def _candidate_tie_break_key(item: dict[str, Any]) -> tuple[float, int, float, float, str, int]:
    coverage_raw = item.get("match_coverage")
    coverage = (
        max(0.0, float(coverage_raw))
        if isinstance(coverage_raw, (int, float)) and not isinstance(coverage_raw, bool)
        else 0.0
    )
    matched_tokens = item.get("matched_tokens")
    matched_count = len(matched_tokens) if isinstance(matched_tokens, list) else 0

    token_hit_sum = 0.0
    token_hit_max = 0.0
    token_hits = item.get("token_hits")
    if isinstance(token_hits, dict):
        for value in token_hits.values():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                hit = max(0.0, float(value))
                token_hit_sum += hit
                token_hit_max = max(token_hit_max, hit)

    return (
        -coverage,
        -matched_count,
        -token_hit_sum,
        -token_hit_max,
        str(item.get("path") or ""),
        int(item.get("start_line") or 1),
    )


def _candidate_sort_key(item: dict[str, Any]) -> tuple[float, float, int, float, float, str, int]:
    return (-_candidate_rank_score(item), *_candidate_tie_break_key(item))


def _prefer_candidate(candidate: dict[str, Any], prev: dict[str, Any]) -> bool:
    return _candidate_sort_key(candidate) < _candidate_sort_key(prev)


def _upsert_candidate_with_file_cap(
    *,
    candidates: dict[str, dict[str, Any]],
    file_candidate_keys: dict[tuple[str, str], set[str]],
    file_key: tuple[str, str],
    candidate_row: dict[str, Any],
    per_file_cap: int,
) -> bool:
    key = _candidate_key(candidate_row)
    existing = candidates.get(key)
    if existing is not None and not _prefer_candidate(candidate_row, existing):
        return False

    keys_for_file = file_candidate_keys.setdefault(file_key, set())
    if existing is None and key not in keys_for_file and len(keys_for_file) >= per_file_cap:
        valid_keys = [item_key for item_key in keys_for_file if item_key in candidates]
        if not valid_keys:
            keys_for_file.clear()
            valid_keys = []
        if not valid_keys:
            candidates[key] = candidate_row
            keys_for_file.add(key)
            return True
        worst_key = max(valid_keys, key=lambda item_key: _candidate_sort_key(candidates[item_key]))
        worst_row = candidates.get(worst_key)
        if worst_row is not None and not _prefer_candidate(candidate_row, worst_row):
            return False
        keys_for_file.remove(worst_key)
        candidates.pop(worst_key, None)

    candidates[key] = candidate_row
    keys_for_file.add(key)
    return True


def _effective_scan_hard_cap(configured_cap: int, requested_max_candidates: int) -> int:
    configured = max(1, int(configured_cap))
    requested = max(1, int(requested_max_candidates))
    budget_scaled = max(
        MANUAL_FIND_SCAN_CAP_MIN_CANDIDATES,
        requested * MANUAL_FIND_SCAN_CAP_BUDGET_MULTIPLIER,
    )
    return min(configured, budget_scaled)


def _term_positions(text: str, term: str, *, limit: int = 8) -> list[int]:
    if not term:
        return []
    out: list[int] = []
    start = 0
    while len(out) < limit:
        idx = text.find(term, start)
        if idx < 0:
            break
        out.append(idx)
        start = idx + max(1, len(term))
    return out


def _min_distance(a: list[int], b: list[int]) -> int | None:
    if not a or not b:
        return None
    best: int | None = None
    for left in a:
        for right in b:
            dist = abs(left - right)
            if best is None or dist < best:
                best = dist
    return best


def _compact_match_text(text: str) -> str:
    return (
        text.replace(" ", "")
        .replace("-", "")
        .replace("・", "")
        .replace("/", "")
        .replace("(", "")
        .replace(")", "")
    )


def _apply_dynamic_candidate_cutoff(
    candidates: list[dict[str, Any]],
    *,
    requested_max_candidates: int,
) -> tuple[list[dict[str, Any]], bool]:
    if not candidates:
        return candidates, False

    hard_cap = max(1, min(int(requested_max_candidates), MANUAL_FIND_DYNAMIC_CUTOFF_MAX_CANDIDATES))
    capped = list(candidates[:hard_cap])
    applied = len(candidates) > len(capped)
    if len(capped) <= MANUAL_FIND_DYNAMIC_CUTOFF_MIN_KEEP:
        return capped, applied

    top_score = _candidate_rank_score(capped[0])
    if top_score <= 0:
        return capped, applied

    dynamic_limit = len(capped)
    for idx in range(MANUAL_FIND_DYNAMIC_CUTOFF_MIN_KEEP, len(capped)):
        row = capped[idx]
        row_score = _candidate_rank_score(row)
        score_ratio = row_score / max(1e-9, top_score)
        coverage_raw = row.get("match_coverage")
        coverage = float(coverage_raw) if isinstance(coverage_raw, (int, float)) and not isinstance(coverage_raw, bool) else 0.0
        if score_ratio < MANUAL_FIND_DYNAMIC_CUTOFF_MIN_SCORE_RATIO and coverage < MANUAL_FIND_DYNAMIC_CUTOFF_MIN_COVERAGE:
            dynamic_limit = idx
            break

    if dynamic_limit < len(capped):
        return capped[:dynamic_limit], True
    return capped, applied


def _candidate_token_hit_sum(item: dict[str, Any]) -> int:
    token_hits = item.get("token_hits")
    if not isinstance(token_hits, dict):
        return 0
    total = 0
    for value in token_hits.values():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += max(0, int(value))
    return total


def _is_strong_relaxed_candidate(item: dict[str, Any]) -> bool:
    matched_tokens = item.get("matched_tokens")
    matched_count = len(matched_tokens) if isinstance(matched_tokens, list) else 0
    if matched_count >= RELAXED_MIN_MATCHED_TOKENS:
        return True
    if _candidate_token_hit_sum(item) >= RELAXED_MIN_TOKEN_HIT_SUM:
        return True
    signals = set(item.get("signals") or [])
    if signals.intersection(RELAXED_STRONG_SIGNALS):
        return True
    return False


def _filter_relaxed_candidates(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    if not candidates:
        return candidates, False
    kept = [item for item in candidates if _is_strong_relaxed_candidate(item)]
    if kept:
        return kept, len(kept) < len(candidates)
    return [], bool(candidates)


def _relaxed_candidates_are_weak(candidates: list[dict[str, Any]]) -> bool:
    if not candidates:
        return True
    strong = sum(1 for item in candidates if _is_strong_relaxed_candidate(item))
    if strong <= 0:
        return True
    return strong <= max(1, len(candidates) // 4)


def _required_term_candidates_are_weak(candidates: list[dict[str, Any]], *, top_k: int = 3) -> bool:
    if not candidates:
        return True
    applied_top_k = max(1, int(top_k))
    top_rows = candidates[:applied_top_k]
    strong = 0
    for item in top_rows:
        raw = item.get("_required_match_count")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 1
        if value >= 2:
            strong += 1
    return strong <= 0


def _apply_file_diversity_rerank(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if len(candidates) <= 2:
        return candidates

    peak_score = max((_candidate_rank_score(item) for item in candidates), default=0.0)
    penalty_unit = max(
        MANUAL_FIND_FILE_DIVERSITY_PENALTY_MIN,
        peak_score * MANUAL_FIND_FILE_DIVERSITY_PENALTY_TOP_RATIO,
    )
    remaining = [dict(item) for item in candidates]
    selected: list[dict[str, Any]] = []
    path_counts: dict[str, int] = {}

    while remaining:
        best_idx = 0
        best_adjusted = float("-inf")
        best_penalty = 0.0
        best_sort_key: tuple[float, float, int, float, float, str, int] | None = None
        for idx, item in enumerate(remaining):
            path = str(item.get("path") or "")
            seen = path_counts.get(path, 0)
            penalty = float(seen) * penalty_unit
            adjusted = _candidate_rank_score(item) - penalty
            sort_key = (-adjusted, *_candidate_tie_break_key(item))
            if best_sort_key is None or sort_key < best_sort_key:
                best_idx = idx
                best_adjusted = adjusted
                best_penalty = penalty
                best_sort_key = sort_key

        chosen = remaining.pop(best_idx)
        chosen["_rank_score"] = float(best_adjusted)
        chosen["score"] = round(float(best_adjusted), 4)
        if best_penalty > 0.0:
            rank_explain = list(chosen.get("rank_explain") or [])
            rank_explain.append(f"file_diversity=-{round(best_penalty, 4)}")
            chosen["rank_explain"] = rank_explain
        selected.append(chosen)
        selected_path = str(chosen.get("path") or "")
        path_counts[selected_path] = path_counts.get(selected_path, 0) + 1

    return selected


def _default_scan_next_action(
    manual_id: str | None,
    candidates: list[dict[str, Any]],
    *,
    manuals_root: Path | None,
) -> dict[str, Any] | None:
    if not manual_id:
        return None
    for item in candidates:
        path = str(item.get("path") or "")
        if not path:
            continue
        params: dict[str, Any] = {"manual_id": manual_id, "path": path}
        start_line = item.get("start_line")
        if isinstance(start_line, int) and start_line >= 1:
            params["start_line"] = start_line
        return {"type": "manual_scan", "confidence": 0.7, "params": params}
    if manuals_root is not None:
        for row in list_manual_files(manuals_root, manual_id=manual_id):
            return {"type": "manual_scan", "confidence": 0.7, "params": {"manual_id": manual_id, "path": row.path}}
    return None


def _plan_next_actions(
    summary: dict[str, Any],
    query: str,
    max_stage: int,
    *,
    manual_id: str | None,
    candidates: list[dict[str, Any]],
    manuals_root: Path | None,
) -> list[dict[str, Any]]:
    del max_stage
    if _is_exhaustive_query(query):
        scan_action = _default_scan_next_action(manual_id, candidates, manuals_root=manuals_root)
        if scan_action is not None:
            return [scan_action]
    if summary["conflict_count"] > 0:
        return [{"type": "manual_read", "confidence": 0.7, "params": {"scope": "section"}}]
    return [{"type": "manual_hits", "confidence": 0.7, "params": {"kind": "integrated_top", "offset": 0}}]


def _validate_next_actions(actions: Any) -> list[dict[str, Any]]:
    if not isinstance(actions, list):
        raise ToolError("invalid_parameter", "next_actions must be a list")
    validated: list[dict[str, Any]] = []
    for item in actions:
        if not isinstance(item, dict):
            raise ToolError("invalid_parameter", "next_actions item must be an object")
        action_type = item.get("type")
        confidence = item.get("confidence")
        params = item.get("params")
        if action_type not in {"manual_hits", "manual_read", "manual_find", "manual_scan"}:
            raise ToolError("invalid_parameter", "next_actions.type is invalid")
        if confidence is not None and not isinstance(confidence, (int, float)):
            raise ToolError("invalid_parameter", "next_actions.confidence must be a number or null")
        if isinstance(confidence, (int, float)) and not (0.0 <= float(confidence) <= 1.0):
            raise ToolError("invalid_parameter", "next_actions.confidence must be between 0 and 1")
        if params is not None and not isinstance(params, dict):
            raise ToolError("invalid_parameter", "next_actions.params must be an object or null")
        validated.append({"type": action_type, "confidence": confidence, "params": params})
    return validated


def _plan_next_actions_with_planner(
    state: AppState,
    summary: dict[str, Any],
    query: str,
    max_stage: int,
    *,
    manual_id: str | None,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    planner = state.next_actions_planner
    if planner is None:
        return _plan_next_actions(
            summary,
            query,
            max_stage,
            manual_id=manual_id,
            candidates=candidates,
            manuals_root=state.config.manuals_root,
        )
    try:
        raw_actions = planner(
            {
                "summary": summary,
                "query": query,
                "manual_id": manual_id,
                "candidate_paths": [str(item.get("path") or "") for item in candidates if item.get("path")],
            }
        )
        return _validate_next_actions(raw_actions)
    except Exception:
        return _plan_next_actions(
            summary,
            query,
            max_stage,
            manual_id=manual_id,
            candidates=candidates,
            manuals_root=state.config.manuals_root,
        )


def _run_find_pass(
    state: AppState,
    manual_ids: list[str],
    query: str,
    max_stage: int,
    budget_time_ms: int,
    max_candidates: int,
    required_terms: list[str] | None = None,
    prioritize_paths: dict[str, set[str]] | None = None,
    allowed_paths: dict[str, set[str]] | None = None,
) -> tuple[list[dict[str, Any]], int, int, int, str | None, list[dict[str, Any]], bool, int]:
    del max_stage
    start = time.monotonic()
    candidates: dict[str, dict[str, Any]] = {}
    warnings = 0
    scanned_files = 0
    scanned_nodes = 0
    cutoff_reason: str | None = None
    unscanned_sections: list[dict[str, Any]] = []
    scanned_doc_ids: set[int] = set()

    base_terms = split_terms(query)
    lexical_terms, coverage_groups = _expand_lexical_query_terms(base_terms)
    query_term_set = set(lexical_terms)
    term_weights: dict[str, float] = {term: 1.0 for term in lexical_terms}
    base_code_terms = {term for term in query_term_set if _is_code_like_term(term)}
    code_term_patterns = {term: _compile_code_pattern(term) for term in sorted(base_code_terms)}
    normalized_phrase_terms = [normalize_text(term) for term in base_terms if len(normalize_text(term)) >= 4]
    sparse_query_coverage_weight = max(0.0, float(state.config.sparse_query_coverage_weight))
    coverage_weight = max(0.0, float(state.config.lexical_coverage_weight))
    phrase_weight = max(0.0, float(state.config.lexical_phrase_weight))
    number_context_bonus_weight = max(0.0, float(state.config.lexical_number_context_bonus))
    proximity_bonus_near = max(0.0, float(state.config.lexical_proximity_bonus_near))
    proximity_bonus_far = max(0.0, float(state.config.lexical_proximity_bonus_far))
    length_penalty_weight = max(0.0, float(state.config.lexical_length_penalty_weight))

    manuals_fp = _manuals_fingerprint(state, manual_ids)
    sparse_index, index_rebuilt = state.sparse_index.get_or_build(manual_ids=manual_ids, fingerprint=manuals_fp)
    index_docs = sparse_index.total_docs
    total_docs = max(1, sparse_index.total_docs)
    avg_doc_len = max(1.0, float(sparse_index.avg_doc_len))
    unresolved_group_terms: set[str] = set()
    for group in coverage_groups:
        if not group:
            continue
        if any(sparse_index.postings.get(term) for term in group):
            continue
        unresolved_group_terms.update(group)
    unresolved_for_prf = {
        term
        for term in unresolved_group_terms
        if len(term) >= 3 and not term.isdigit() and not _is_code_like_term(term)
    }
    missing_terms = {term for term in query_term_set if term in unresolved_for_prf and not sparse_index.postings.get(term)}
    feedback_terms: list[str] = []
    feedback_term_set: set[str] = set()
    if missing_terms:
        feedback_terms = _prf_expand_terms(
            sparse_index=sparse_index,
            query_terms=query_term_set,
            missing_terms=missing_terms,
        )
        for term in feedback_terms:
            if term in term_weights:
                continue
            term_weights[term] = PRF_TERM_WEIGHT
            lexical_terms.append(term)
        feedback_term_set = set(feedback_terms)
        query_term_set = set(lexical_terms)
    term_doc_freq = {term: 0 for term in query_term_set}
    if query_term_set:
        for doc in sparse_index.docs:
            text = doc.normalized_text
            if not text:
                continue
            for term in query_term_set:
                if term in text:
                    term_doc_freq[term] = term_doc_freq.get(term, 0) + 1

    scan_hard_cap = _effective_scan_hard_cap(
        int(state.config.manual_find_scan_hard_cap),
        max_candidates,
    )
    applied_required_terms = list(required_terms or [])
    required_pattern_groups = _required_term_pattern_groups(applied_required_terms)
    required_terms_added_to_query: list[str] = []
    if applied_required_terms:
        for term in applied_required_terms:
            if term in query_term_set:
                continue
            query_term_set.add(term)
            lexical_terms.append(term)
            term_weights[term] = max(1.05, float(term_weights.get(term, 1.0)))
            required_terms_added_to_query.append(term)
    if required_terms_added_to_query:
        for term in required_terms_added_to_query:
            term_doc_freq.setdefault(term, 0)
        for doc in sparse_index.docs:
            text = doc.normalized_text
            if not text:
                continue
            for term in required_terms_added_to_query:
                if term in text:
                    term_doc_freq[term] = term_doc_freq.get(term, 0) + 1
    per_file_cap = max(1, min(int(state.config.manual_find_per_file_candidate_cap), max_candidates))
    prescan_enabled = bool(state.config.manual_find_file_prescan_enabled)
    file_candidate_keys: dict[tuple[str, str], set[str]] = {}
    file_title_terms: dict[tuple[str, str], set[str]] = {}
    for doc in sparse_index.docs:
        key = (doc.manual_id, doc.path)
        file_title_terms.setdefault(key, set()).add(doc.normalized_title)

    files_by_manual: dict[str, list[Any]] = {}
    for manual_id in manual_ids:
        files = list_manual_files(state.config.manuals_root, manual_id=manual_id)
        if allowed_paths is not None:
            files = [row for row in files if row.path in allowed_paths.get(manual_id, set())]
        if prescan_enabled:
            preferred = prioritize_paths.get(manual_id, set()) if prioritize_paths else set()
            files.sort(
                key=lambda r: (
                    r.path not in preferred,
                    -_file_query_relevance_score(
                        r.path,
                        file_title_terms.get((manual_id, r.path), set()),
                        lexical_terms,
                    ),
                    r.path,
                )
            )
        elif prioritize_paths and manual_id in prioritize_paths:
            preferred = prioritize_paths[manual_id]
            files.sort(key=lambda r: (r.path not in preferred, r.path))
        files_by_manual[manual_id] = files

    seen_unscanned: set[tuple[str, str]] = set()

    def append_remaining_unscanned(start_manual_idx: int, start_file_idx: int, reason: str) -> None:
        for mi in range(start_manual_idx, len(manual_ids)):
            mid = manual_ids[mi]
            rows = files_by_manual.get(mid, [])
            from_idx = start_file_idx if mi == start_manual_idx else 0
            for row in rows[from_idx:]:
                key = (mid, row.path)
                if key in seen_unscanned:
                    continue
                seen_unscanned.add(key)
                unscanned_sections.append({"manual_id": mid, "path": row.path, "reason": reason})

    for manual_idx, manual_id in enumerate(manual_ids):
        files = files_by_manual.get(manual_id, [])
        for row_idx, row in enumerate(files):
            elapsed_ms = int((time.monotonic() - start) * 1000)
            if elapsed_ms > budget_time_ms:
                cutoff_reason = "time_budget"
                append_remaining_unscanned(manual_idx, row_idx, "time_budget")
                break
            if len(candidates) >= scan_hard_cap:
                cutoff_reason = "candidate_cap"
                append_remaining_unscanned(manual_idx, row_idx, "candidate_cap")
                break
            if _is_noise_path(row.path):
                continue
            scanned_files += 1
            doc_ids = sparse_index.docs_by_file.get((manual_id, row.path), [])
            if row.file_type == "md":
                scanned_nodes += len(doc_ids)
            elif doc_ids:
                scanned_nodes += 1
            else:
                # Index build may skip unreadable files; treat as warning on query pass.
                warnings += 1
                continue

            for doc_id in doc_ids:
                scanned_doc_ids.add(doc_id)
                doc = sparse_index.docs[doc_id]
                normalized_text = doc.normalized_text
                if not normalized_text:
                    continue
                if required_pattern_groups and not _matches_required_term_groups(normalized_text, required_pattern_groups):
                    continue
                compact_text = _compact_match_text(normalized_text)
                token_hits: dict[str, int] = {}
                for term in lexical_terms:
                    raw_count = normalized_text.count(term)
                    if raw_count <= 0:
                        compact_term = _compact_match_text(term)
                        if compact_term:
                            raw_count = compact_text.count(compact_term)
                    count = min(int(raw_count), LEXICAL_TOKEN_HIT_COUNT_CAP)
                    if count > 0:
                        token_hits[term] = count
                if not token_hits:
                    continue

                matched_terms = set(token_hits.keys())
                base_score = 0.0
                for term, count in token_hits.items():
                    term_weight = max(0.0, float(term_weights.get(term, 1.0)))
                    base_score += (
                        term_weight
                        * _idf(total_docs, term_doc_freq.get(term, 0))
                        * _bm25_tf_weight(
                        int(count),
                        doc_len=int(doc.doc_len),
                        avg_doc_len=avg_doc_len,
                    )
                    )

                match_coverage_ratio = _match_coverage_ratio(matched_terms, coverage_groups)
                sparse_coverage_bonus = match_coverage_ratio * sparse_query_coverage_weight
                coverage_bonus = match_coverage_ratio * coverage_weight
                phrase_bonus = 0.0
                for phrase in normalized_phrase_terms:
                    if phrase and phrase in normalized_text:
                        phrase_bonus += phrase_weight * _idf(total_docs, term_doc_freq.get(phrase, 0))

                number_terms = {term for term in matched_terms if NUMBER_PATTERN.search(term)}
                context_present = bool(number_terms) and any(ctx in normalized_text for ctx in NUMBER_CONTEXT_TERMS)
                number_context_bonus = number_context_bonus_weight if context_present else 0.0

                anchor_terms = [term for term in matched_terms if len(term) >= 4 and not NUMBER_PATTERN.search(term)]
                proximity_bonus = 0.0
                if number_terms and anchor_terms:
                    number_positions: list[int] = []
                    for term in sorted(number_terms):
                        number_positions.extend(_term_positions(normalized_text, term, limit=4))
                    anchor_positions: list[int] = []
                    for term in anchor_terms[:3]:
                        anchor_positions.extend(_term_positions(normalized_text, term, limit=4))
                    min_distance = _min_distance(anchor_positions, number_positions)
                    if min_distance is not None and min_distance <= PROXIMITY_WINDOW_CHARS:
                        proximity_bonus = proximity_bonus_near if min_distance <= 40 else proximity_bonus_far

                code_exact_hits = 0
                if code_term_patterns:
                    for code_term, pattern in code_term_patterns.items():
                        if code_term in matched_terms and pattern.search(normalized_text):
                            code_exact_hits += 1
                code_exact_bonus = float(code_exact_hits) * CODE_EXACT_BONUS
                prf_support_hits = len(matched_terms.intersection(feedback_term_set))
                prf_support_bonus = float(min(2, prf_support_hits)) * PRF_TERM_WEIGHT
                definition_title_bonus = 0.0
                if (
                    any(hint in doc.normalized_title for hint in DEFINITION_TITLE_HINTS)
                    and any(hint in query_term_set for hint in ELIGIBILITY_QUERY_HINTS)
                ):
                    definition_title_bonus = LEXICAL_DEFINITION_TITLE_BONUS

                length_penalty = max(0.0, (len(normalized_text) - 3000) / 3000.0) * length_penalty_weight
                score = (
                    base_score
                    + sparse_coverage_bonus
                    + coverage_bonus
                    + phrase_bonus
                    + number_context_bonus
                    + proximity_bonus
                    + code_exact_bonus
                    + prf_support_bonus
                    + definition_title_bonus
                    - length_penalty
                )
                if score <= 0:
                    continue

                signals: set[str] = {"exact"}
                if required_pattern_groups:
                    signals.add("required_term")
                    if len(required_pattern_groups) > 1:
                        signals.add("required_term_and")
                if phrase_bonus > 0:
                    signals.add("phrase")
                if anchor_terms:
                    signals.add("anchor")
                if context_present:
                    signals.add("number_context")
                if proximity_bonus > 0:
                    signals.add("proximity")
                if code_exact_hits > 0:
                    signals.add("code_exact")
                if prf_support_hits > 0:
                    signals.add("prf")
                if definition_title_bonus > 0:
                    signals.add("definition_title")
                if any(word in normalized_text for word in NORMALIZED_EXCEPTION_WORDS) and any(
                    term in FACET_HINTS["exceptions"] or term in NORMALIZED_EXCEPTION_WORDS
                    for term in matched_terms
                ):
                    signals.add("exceptions")

                matched_tokens = sorted(matched_terms, key=lambda term: (-token_hits.get(term, 0), term))
                rank_explain: list[str] = []
                rank_explain.append(f"base={round(base_score, 4)}")
                if required_pattern_groups:
                    rank_explain.append(f"required_terms={len(required_pattern_groups)}")
                if sparse_coverage_bonus > 0:
                    rank_explain.append(f"sparse_coverage={round(sparse_coverage_bonus, 4)}")
                rank_explain.append(f"coverage={round(coverage_bonus, 4)}")
                if phrase_bonus > 0:
                    rank_explain.append(f"phrase={round(phrase_bonus, 4)}")
                if number_context_bonus > 0:
                    rank_explain.append(f"number_context={round(number_context_bonus, 4)}")
                if proximity_bonus > 0:
                    rank_explain.append(f"proximity={round(proximity_bonus, 4)}")
                if code_exact_bonus > 0:
                    rank_explain.append(f"code_exact={round(code_exact_bonus, 4)}")
                if prf_support_bonus > 0:
                    rank_explain.append(f"prf_support={round(prf_support_bonus, 4)}")
                if definition_title_bonus > 0:
                    rank_explain.append(f"definition_title={round(definition_title_bonus, 4)}")
                if length_penalty > 0:
                    rank_explain.append(f"length_penalty={round(length_penalty, 4)}")

                item = {
                    "ref": {
                        "target": "manual",
                        "manual_id": manual_id,
                        "path": row.path,
                        "start_line": doc.start_line,
                        "json_path": None,
                        "title": doc.title,
                        "signals": sorted(signals),
                    },
                    "path": row.path,
                    "start_line": doc.start_line,
                    "reason": None,
                    "signals": sorted(signals),
                    "_rank_score": float(score),
                    "score": round(score, 4),
                    "conflict_with": [],
                    "gap_hint": None,
                    "matched_tokens": matched_tokens,
                    "token_hits": {term: token_hits.get(term, 0) for term in matched_tokens},
                    "match_coverage": round(match_coverage_ratio, 4),
                    "rank_explain": rank_explain,
                }
                if applied_required_terms:
                    item["required_terms"] = list(applied_required_terms)
                inserted = _upsert_candidate_with_file_cap(
                    candidates=candidates,
                    file_candidate_keys=file_candidate_keys,
                    file_key=(manual_id, row.path),
                    candidate_row=item,
                    per_file_cap=per_file_cap,
                )
                if inserted and len(candidates) >= scan_hard_cap:
                    cutoff_reason = "candidate_cap"
                    append_remaining_unscanned(manual_idx, row_idx, "candidate_cap")
                    break
            if cutoff_reason:
                break
        if cutoff_reason:
            break

    ordered_primary = sorted(
        candidates.values(),
        key=_candidate_sort_key,
    )
    exploration_enabled = bool(state.config.manual_find_exploration_enabled)
    exploration_ratio = max(0.0, min(1.0, float(state.config.manual_find_exploration_ratio)))
    exploration_min_candidates = max(0, int(state.config.manual_find_exploration_min_candidates))
    exploration_score_scale = max(0.0, float(state.config.manual_find_exploration_score_scale))
    exploration_pool: list[dict[str, Any]] = []
    if (
        exploration_enabled
        and exploration_ratio > 0.0
        and exploration_score_scale > 0.0
        and scanned_doc_ids
        and query_term_set
    ):
        sparse_scores = bm25_scores(sparse_index, query_terms=query_term_set)
        ranked_doc_ids = sorted(
            ((doc_id, score) for doc_id, score in sparse_scores.items() if score > 0 and doc_id in scanned_doc_ids),
            key=lambda row: (
                -float(row[1]),
                str(sparse_index.docs[int(row[0])].path),
                int(sparse_index.docs[int(row[0])].start_line),
            ),
        )
        seen_keys = set(candidates.keys())
        for doc_id, raw_score in ranked_doc_ids:
            doc = sparse_index.docs[doc_id]
            if _is_noise_path(doc.path):
                continue
            normalized_text = doc.normalized_text
            if not normalized_text:
                continue
            if required_pattern_groups and not _matches_required_term_groups(normalized_text, required_pattern_groups):
                continue
            compact_text = _compact_match_text(normalized_text)
            token_hits: dict[str, int] = {}
            for term in lexical_terms:
                raw_count = normalized_text.count(term)
                if raw_count <= 0:
                    compact_term = _compact_match_text(term)
                    if compact_term:
                        raw_count = compact_text.count(compact_term)
                count = min(int(raw_count), LEXICAL_TOKEN_HIT_COUNT_CAP)
                if count > 0:
                    token_hits[term] = count
            if not token_hits:
                continue
            matched_terms = set(token_hits.keys())
            code_exact_hits = 0
            if code_term_patterns:
                for code_term, pattern in code_term_patterns.items():
                    if code_term in matched_terms and pattern.search(normalized_text):
                        code_exact_hits += 1
            code_exact_bonus = float(code_exact_hits) * CODE_EXACT_BONUS
            scaled_score = round((float(raw_score) * exploration_score_scale) + code_exact_bonus, 4)
            if scaled_score <= 0.0:
                continue
            signals: set[str] = {"exploration", "exact"}
            if required_pattern_groups:
                signals.add("required_term")
                if len(required_pattern_groups) > 1:
                    signals.add("required_term_and")
            if code_exact_hits > 0:
                signals.add("code_exact")
            if any(word in normalized_text for word in NORMALIZED_EXCEPTION_WORDS) and any(
                term in FACET_HINTS["exceptions"] or term in NORMALIZED_EXCEPTION_WORDS for term in matched_terms
            ):
                signals.add("exceptions")
            matched_tokens = sorted(matched_terms, key=lambda term: (-token_hits.get(term, 0), term))
            exploration_rank_explain = [
                f"exploration_bm25={round(float(raw_score), 4)}",
                f"exploration_scale={round(exploration_score_scale, 4)}",
                f"code_exact={round(code_exact_bonus, 4)}",
            ]
            if required_pattern_groups:
                exploration_rank_explain.insert(0, f"required_terms={len(required_pattern_groups)}")
            item = {
                "ref": {
                    "target": "manual",
                    "manual_id": doc.manual_id,
                    "path": doc.path,
                    "start_line": doc.start_line,
                    "json_path": None,
                    "title": doc.title,
                    "signals": sorted(signals),
                },
                "path": doc.path,
                "start_line": doc.start_line,
                "reason": None,
                "signals": sorted(signals),
                "_rank_score": float(scaled_score),
                "score": scaled_score,
                "conflict_with": [],
                "gap_hint": None,
                "matched_tokens": matched_tokens,
                "token_hits": {term: token_hits.get(term, 0) for term in matched_tokens},
                "match_coverage": round(_match_coverage_ratio(matched_terms, coverage_groups), 4),
                "rank_explain": exploration_rank_explain,
            }
            if applied_required_terms:
                item["required_terms"] = list(applied_required_terms)
            key = _candidate_key(item)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            exploration_pool.append(item)

    if exploration_pool:
        exploration_quota = min(
            max_candidates,
            max(exploration_min_candidates, int(math.ceil(max_candidates * exploration_ratio))),
        )
        primary_quota = max(0, max_candidates - exploration_quota)
        exploration_sorted = sorted(
            exploration_pool,
            key=_candidate_sort_key,
        )
        combined = ordered_primary[:primary_quota] + exploration_sorted[:exploration_quota]
        if len(combined) < max_candidates:
            combined.extend(ordered_primary[primary_quota : primary_quota + (max_candidates - len(combined))])
        if len(combined) < max_candidates:
            combined.extend(exploration_sorted[exploration_quota : exploration_quota + (max_candidates - len(combined))])
        dedup: dict[str, dict[str, Any]] = {}
        for item in combined:
            key = _candidate_key(item)
            prev = dedup.get(key)
            if prev is None or _prefer_candidate(item, prev):
                dedup[key] = item
        ordered = sorted(
            dedup.values(),
            key=_candidate_sort_key,
        )
    else:
        ordered = ordered_primary

    ordered = ordered[:max_candidates]
    _annotate_lexical_candidate_scores(ordered)
    for item in ordered:
        score_fused = item.get("score_fused")
        if not isinstance(score_fused, (int, float)) or isinstance(score_fused, bool):
            score_fused = _candidate_rank_score(item)
        item["score"] = round(float(score_fused), 4)
    return ordered, scanned_files, scanned_nodes, warnings, cutoff_reason, unscanned_sections, index_rebuilt, index_docs


def _query_decomp_subqueries(query: str, *, max_sub_queries: int) -> list[str]:
    cap = max(1, int(max_sub_queries))
    base = query.strip()
    if not base:
        return []
    out = [base]
    match = QUERY_DECOMP_COMPARE_DIFF_RE.match(base)
    if match is not None:
        left = (match.group("left") or "").strip()
        right = (match.group("right") or "").strip()
        for sub in (left, right):
            if sub and sub not in out:
                out.append(sub)
            if len(out) >= cap:
                break
        return out[:cap]

    match = QUERY_DECOMP_COMPARE_KEYWORD_RE.match(base)
    if match is not None:
        left = (match.group("left") or "").strip()
        right = (match.group("right") or "").strip()
        for sub in (left, right):
            if sub and sub not in out:
                out.append(sub)
            if len(out) >= cap:
                break
        return out[:cap]

    match = QUERY_DECOMP_VS_RE.match(base)
    if match is not None:
        left = (match.group("left") or "").strip()
        right = (match.group("right") or "").strip()
        for sub in (left, right):
            if sub and sub not in out:
                out.append(sub)
            if len(out) >= cap:
                break
        return out[:cap]

    match = QUERY_DECOMP_CASE_RE.match(base)
    if match is not None:
        left = (match.group("left") or "").strip()
        right = (match.group("right") or "").strip()
        for sub in (f"{left} {right}".strip(), right):
            if sub and sub not in out:
                out.append(sub)
            if len(out) >= cap:
                break
        return out[:cap]

    match = QUERY_DECOMP_COMPARE_RE.match(base)
    if match is not None:
        left = (match.group("left") or "").strip()
        right = (match.group("right") or "").strip()
        for sub in (left, right):
            if sub and sub not in out:
                out.append(sub)
            if len(out) >= cap:
                break
    return out[:cap]


def _run_find_pass_with_query_decomp_rrf(
    state: AppState,
    *,
    manual_ids: list[str],
    query: str,
    max_stage: int,
    budget_time_ms: int,
    max_candidates: int,
    required_terms: list[str] | None = None,
    prioritize_paths: dict[str, set[str]] | None = None,
    allowed_paths: dict[str, set[str]] | None = None,
) -> tuple[list[dict[str, Any]], int, int, int, str | None, list[dict[str, Any]], bool, int, bool]:
    sub_queries = _query_decomp_subqueries(
        query,
        max_sub_queries=state.config.manual_find_query_decomp_max_sub_queries,
    )
    if len(sub_queries) <= 1:
        rows = _run_find_pass(
            state=state,
            manual_ids=manual_ids,
            query=query,
            max_stage=max_stage,
            budget_time_ms=budget_time_ms,
            max_candidates=max_candidates,
            required_terms=required_terms,
            prioritize_paths=prioritize_paths,
            allowed_paths=allowed_paths,
        )
        return (*rows, False)

    sub_budget_time_ms = max(1, budget_time_ms // len(sub_queries))
    sub_max_candidates = max(1, min(max_candidates, (max_candidates // len(sub_queries)) + 5))
    rrf_k = max(1, int(state.config.manual_find_query_decomp_rrf_k))

    merged_rows: dict[str, dict[str, Any]] = {}
    rrf_scores: dict[str, float] = {}
    scanned_files = 0
    scanned_nodes = 0
    warnings = 0
    cutoff_reason: str | None = None
    unscanned_sections: list[dict[str, Any]] = []
    index_rebuilt = False
    index_docs = 0

    for sub_query in sub_queries:
        try:
            (
                sub_candidates,
                sub_scanned_files,
                sub_scanned_nodes,
                sub_warnings,
                sub_cutoff_reason,
                sub_unscanned,
                sub_index_rebuilt,
                sub_index_docs,
            ) = _run_find_pass(
                state=state,
                manual_ids=manual_ids,
                query=sub_query,
                max_stage=max_stage,
                budget_time_ms=sub_budget_time_ms,
                max_candidates=sub_max_candidates,
                required_terms=required_terms,
                prioritize_paths=prioritize_paths,
                allowed_paths=allowed_paths,
            )
        except Exception:
            warnings += 1
            continue
        scanned_files += sub_scanned_files
        scanned_nodes += sub_scanned_nodes
        warnings += sub_warnings
        unscanned_sections.extend(sub_unscanned)
        index_rebuilt = index_rebuilt or sub_index_rebuilt
        index_docs = max(index_docs, sub_index_docs)
        if cutoff_reason is None and sub_cutoff_reason is not None:
            cutoff_reason = sub_cutoff_reason

        for rank, item in enumerate(sub_candidates, start=1):
            key = _candidate_key(item)
            prev = merged_rows.get(key)
            if prev is None or _prefer_candidate(item, prev):
                merged_rows[key] = dict(item)
            rrf_scores[key] = rrf_scores.get(key, 0.0) + (1.0 / float(rrf_k + rank))

    if not merged_rows:
        rows = _run_find_pass(
            state=state,
            manual_ids=manual_ids,
            query=query,
            max_stage=max_stage,
            budget_time_ms=budget_time_ms,
            max_candidates=max_candidates,
            required_terms=required_terms,
            prioritize_paths=prioritize_paths,
            allowed_paths=allowed_paths,
        )
        return (*rows, False)

    rows_for_sort: list[dict[str, Any]] = []
    base_scores_by_key: dict[str, float] = {}
    for key, item in merged_rows.items():
        base_scores_by_key[key] = _candidate_rank_score(item)
    base_min = min(base_scores_by_key.values(), default=0.0)
    base_max = max(base_scores_by_key.values(), default=0.0)
    rrf_min = min(rrf_scores.values(), default=0.0)
    rrf_max = max(rrf_scores.values(), default=0.0)
    base_weight = float(state.config.manual_find_query_decomp_base_weight)
    for key, item in merged_rows.items():
        rrf = float(rrf_scores.get(key, 0.0))
        base_score = float(base_scores_by_key.get(key, 0.0))
        merged_score, base_norm, rrf_norm = _blend_query_decomp_rrf_score(
            base_score=base_score,
            rrf_score=rrf,
            base_min=base_min,
            base_max=base_max,
            rrf_min=rrf_min,
            rrf_max=rrf_max,
            base_weight=base_weight,
        )
        item["_rank_score"] = merged_score
        item["score"] = round(merged_score, 4)
        _set_candidate_component_scores(
            item,
            score_lexical=merged_score,
            score_fused=merged_score,
        )
        signals = set(item.get("signals") or [])
        signals.add("query_decomp_rrf")
        item["signals"] = sorted(signals)
        ref = dict(item.get("ref") or {})
        ref_signals = set(ref.get("signals") or [])
        ref_signals.add("query_decomp_rrf")
        ref["signals"] = sorted(ref_signals)
        item["ref"] = ref
        rank_explain = list(item.get("rank_explain") or [])
        rank_explain.append(f"rrf={round(rrf, 6)}")
        rank_explain.append(f"base_norm={round(base_norm, 4)}")
        rank_explain.append(f"rrf_norm={round(rrf_norm, 4)}")
        rank_explain.append(f"query_decomp_alpha={round(max(0.0, min(1.0, base_weight)), 4)}")
        item["rank_explain"] = rank_explain
        rows_for_sort.append(item)

    rows_for_sort.sort(key=_candidate_sort_key)
    ordered = rows_for_sort[:max_candidates]
    return (
        ordered,
        scanned_files,
        scanned_nodes,
        warnings,
        cutoff_reason,
        unscanned_sections,
        index_rebuilt,
        index_docs,
        True,
    )


def _run_find_pass_lexical_single(
    state: AppState,
    *,
    manual_ids: list[str],
    query: str,
    max_stage: int,
    budget_time_ms: int,
    max_candidates: int,
    required_terms: list[str] | None = None,
    allow_query_decomp: bool,
    prioritize_paths: dict[str, set[str]] | None = None,
    allowed_paths: dict[str, set[str]] | None = None,
) -> tuple[list[dict[str, Any]], int, int, int, str | None, list[dict[str, Any]], bool, int, bool, str, str | None]:
    query_decomp_applied = False
    if allow_query_decomp and state.config.manual_find_query_decomp_enabled:
        (
            rows,
            scanned_files,
            scanned_nodes,
            warnings,
            cutoff_reason,
            unscanned_sections,
            index_rebuilt,
            index_docs,
            query_decomp_applied,
        ) = _run_find_pass_with_query_decomp_rrf(
            state=state,
            manual_ids=manual_ids,
            query=query,
            max_stage=max_stage,
            budget_time_ms=budget_time_ms,
            max_candidates=max_candidates,
            required_terms=required_terms,
            prioritize_paths=prioritize_paths,
            allowed_paths=allowed_paths,
        )
    else:
        (
            rows,
            scanned_files,
            scanned_nodes,
            warnings,
            cutoff_reason,
            unscanned_sections,
            index_rebuilt,
            index_docs,
        ) = _run_find_pass(
            state=state,
            manual_ids=manual_ids,
            query=query,
            max_stage=max_stage,
            budget_time_ms=budget_time_ms,
            max_candidates=max_candidates,
            required_terms=required_terms,
            prioritize_paths=prioritize_paths,
            allowed_paths=allowed_paths,
        )
    return (
        rows,
        scanned_files,
        scanned_nodes,
        warnings,
        cutoff_reason,
        unscanned_sections,
        index_rebuilt,
        index_docs,
        query_decomp_applied,
        "query_decomp_rrf" if query_decomp_applied else "lexical",
        None,
    )


def _merge_required_term_pass_rows(
    *,
    pass_rows: list[tuple[str, float, list[dict[str, Any]]]],
    max_candidates: int,
) -> list[dict[str, Any]]:
    merged_rows: dict[str, dict[str, Any]] = {}
    rrf_scores: dict[str, float] = {}
    pass_labels_by_key: dict[str, str] = {}
    required_match_count_by_key: dict[str, int] = {}
    single_pass_labels_by_key: dict[str, set[str]] = {}
    rrf_k = max(1, int(REQUIRED_TERM_RRF_K))

    pass_counts = [len(rows) for _, _, rows in pass_rows]
    total_pass_rows = sum(pass_counts)
    dominant_pass_ratio = (max(pass_counts) / total_pass_rows) if total_pass_rows > 0 else 0.0
    base_weight = float(REQUIRED_TERM_RRF_BASE_WEIGHT)
    if dominant_pass_ratio >= REQUIRED_TERM_RRF_IMBALANCE_RATIO:
        base_weight = min(base_weight, float(REQUIRED_TERM_RRF_BASE_WEIGHT_IMBALANCED))

    for pass_label, pass_weight, rows in pass_rows:
        pass_match_count = _required_match_count_from_pass_label(pass_label)
        for rank, item in enumerate(rows, start=1):
            row = dict(item)
            key = _candidate_key(row)
            prev = merged_rows.get(key)
            if prev is None or _prefer_candidate(row, prev):
                merged_rows[key] = row
                pass_labels_by_key[key] = pass_label
            required_match_count_by_key[key] = max(required_match_count_by_key.get(key, 1), pass_match_count)
            if "single_a" in pass_label or "single_b" in pass_label:
                labels = single_pass_labels_by_key.setdefault(key, set())
                labels.add(pass_label)
            rrf_scores[key] = rrf_scores.get(key, 0.0) + (float(pass_weight) / float(rrf_k + rank))

    if not merged_rows:
        return []

    base_scores_by_key = {key: _candidate_rank_score(item) for key, item in merged_rows.items()}
    base_min = min(base_scores_by_key.values(), default=0.0)
    base_max = max(base_scores_by_key.values(), default=0.0)
    rrf_min = min(rrf_scores.values(), default=0.0)
    rrf_max = max(rrf_scores.values(), default=0.0)

    rows_for_sort: list[dict[str, Any]] = []
    for key, item in merged_rows.items():
        rrf = float(rrf_scores.get(key, 0.0))
        base_score = float(base_scores_by_key.get(key, 0.0))
        merged_score, base_norm, rrf_norm = _blend_query_decomp_rrf_score(
            base_score=base_score,
            rrf_score=rrf,
            base_min=base_min,
            base_max=base_max,
            rrf_min=rrf_min,
            rrf_max=rrf_max,
            base_weight=base_weight,
        )
        required_match_count = max(1, int(required_match_count_by_key.get(key, 1)))
        labels_for_key = single_pass_labels_by_key.get(key, set())
        if required_match_count < 2 and any("single_a" in label for label in labels_for_key) and any(
            "single_b" in label for label in labels_for_key
        ):
            required_match_count = 2
        required_match_bonus = REQUIRED_TERM_MATCH_BONUS if required_match_count >= 2 else 0.0
        merged_score += required_match_bonus
        item["_required_match_count"] = required_match_count
        item["_rank_score"] = merged_score
        item["score"] = round(merged_score, 4)
        _set_candidate_component_scores(
            item,
            score_lexical=merged_score,
            score_fused=merged_score,
        )
        signals = set(item.get("signals") or [])
        signals.add("required_terms_rrf")
        item["signals"] = sorted(signals)
        ref = dict(item.get("ref") or {})
        ref_signals = set(ref.get("signals") or [])
        ref_signals.add("required_terms_rrf")
        ref["signals"] = sorted(ref_signals)
        item["ref"] = ref
        rank_explain = list(item.get("rank_explain") or [])
        rank_explain.append(f"required_pass={pass_labels_by_key.get(key, 'single')}")
        rank_explain.append(f"required_match_count={required_match_count}")
        if required_match_bonus > 0.0:
            rank_explain.append(f"required_match_bonus={round(required_match_bonus, 4)}")
        rank_explain.append(f"required_rrf={round(rrf, 6)}")
        rank_explain.append(f"required_base_norm={round(base_norm, 4)}")
        rank_explain.append(f"required_rrf_norm={round(rrf_norm, 4)}")
        rank_explain.append(f"required_rrf_alpha={round(max(0.0, min(1.0, base_weight)), 4)}")
        item["rank_explain"] = rank_explain
        rows_for_sort.append(item)

    rows_for_sort.sort(key=_candidate_sort_key)
    return rows_for_sort[:max_candidates]


def _merge_required_unscanned_sections(pass_unscanned: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for rows in pass_unscanned:
        for row in rows:
            manual_id = str(row.get("manual_id") or "")
            path = str(row.get("path") or "")
            reason = str(row.get("reason") or "")
            key = (manual_id, path, reason)
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
    return out


def _run_find_pass_lexical(
    state: AppState,
    *,
    manual_ids: list[str],
    query: str,
    max_stage: int,
    budget_time_ms: int,
    max_candidates: int,
    required_terms: list[str] | None = None,
    allow_query_decomp: bool,
    prioritize_paths: dict[str, set[str]] | None = None,
    allowed_paths: dict[str, set[str]] | None = None,
) -> tuple[list[dict[str, Any]], int, int, int, str | None, list[dict[str, Any]], bool, int, bool, str, str | None]:
    applied_required_terms = list(required_terms or [])
    if len(applied_required_terms) <= 1:
        return _run_find_pass_lexical_single(
            state=state,
            manual_ids=manual_ids,
            query=query,
            max_stage=max_stage,
            budget_time_ms=budget_time_ms,
            max_candidates=max_candidates,
            required_terms=applied_required_terms,
            allow_query_decomp=allow_query_decomp,
            prioritize_paths=prioritize_paths,
            allowed_paths=allowed_paths,
        )

    pass_plan = _required_term_passes(applied_required_terms[:REQUIRED_TERMS_MAX_ITEMS])
    pass_rows: list[tuple[str, float, list[dict[str, Any]]]] = []
    pass_scanned_files: list[int] = []
    pass_scanned_nodes: list[int] = []
    pass_warnings: list[int] = []
    pass_cutoff_reasons: list[str | None] = []
    pass_unscanned: list[list[dict[str, Any]]] = []
    query_decomp_applied = False
    index_rebuilt = False
    index_docs = 0

    for pass_label, pass_terms, pass_weight in pass_plan:
        (
            rows,
            scanned_files,
            scanned_nodes,
            warnings,
            cutoff_reason,
            unscanned_sections,
            pass_index_rebuilt,
            pass_index_docs,
            pass_query_decomp_applied,
            _pass_scoring_mode,
            _pass_fallback_reason,
        ) = _run_find_pass_lexical_single(
            state=state,
            manual_ids=manual_ids,
            query=query,
            max_stage=max_stage,
            budget_time_ms=budget_time_ms,
            max_candidates=max_candidates,
            required_terms=pass_terms,
            allow_query_decomp=allow_query_decomp,
            prioritize_paths=prioritize_paths,
            allowed_paths=allowed_paths,
        )
        pass_rows.append((pass_label, pass_weight, rows))
        pass_scanned_files.append(scanned_files)
        pass_scanned_nodes.append(scanned_nodes)
        pass_warnings.append(warnings)
        pass_cutoff_reasons.append(cutoff_reason)
        pass_unscanned.append(unscanned_sections)
        query_decomp_applied = query_decomp_applied or pass_query_decomp_applied
        index_rebuilt = index_rebuilt or pass_index_rebuilt
        index_docs = max(index_docs, pass_index_docs)

    merged_rows = _merge_required_term_pass_rows(pass_rows=pass_rows, max_candidates=max_candidates)
    return (
        merged_rows,
        max(pass_scanned_files) if pass_scanned_files else 0,
        max(pass_scanned_nodes) if pass_scanned_nodes else 0,
        max(pass_warnings) if pass_warnings else 0,
        next((reason for reason in pass_cutoff_reasons if reason is not None), None),
        _merge_required_unscanned_sections(pass_unscanned),
        index_rebuilt,
        index_docs,
        query_decomp_applied,
        "required_terms_rrf",
        None,
    )


def _run_find_gate_pass(
    *,
    state: AppState,
    manual_ids: list[str],
    query: str,
    max_stage: int,
    budget_time_ms: int,
    max_candidates: int,
    required_terms: list[str],
    prioritize_paths: dict[str, set[str]] | None,
    allowed_paths: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    (
        candidates,
        scanned_files,
        scanned_nodes,
        warnings,
        cutoff_reason,
        unscanned,
        index_rebuilt,
        index_docs,
        query_decomp_applied,
        scoring_mode,
        _pass_fallback_reason,
    ) = _run_find_pass_lexical(
        state=state,
        manual_ids=manual_ids,
        query=query,
        max_stage=max_stage,
        budget_time_ms=budget_time_ms,
        max_candidates=max_candidates,
        required_terms=required_terms,
        allow_query_decomp=True,
        prioritize_paths=prioritize_paths,
        allowed_paths=allowed_paths,
    )
    total, file_bias, exception_hits = _candidate_metrics(candidates)
    top_score = max((_candidate_rank_score(item) for item in candidates), default=0.0)
    return {
        "required_terms": list(required_terms),
        "candidates": candidates,
        "scanned_files": scanned_files,
        "scanned_nodes": scanned_nodes,
        "warnings": warnings,
        "cutoff_reason": cutoff_reason,
        "unscanned": unscanned,
        "index_rebuilt": index_rebuilt,
        "index_docs": index_docs,
        "query_decomp_applied": query_decomp_applied,
        "scoring_mode": scoring_mode,
        "candidates_count": int(total),
        "file_bias_ratio": float(file_bias),
        "exception_hits": int(exception_hits),
        "top_score": float(top_score),
    }


def _gate_overlap_ratio(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    *,
    top_k: int,
) -> float:
    if top_k <= 0:
        return 0.0
    left_keys = {_candidate_key(item) for item in left[:top_k]}
    right_keys = {_candidate_key(item) for item in right[:top_k]}
    if not left_keys or not right_keys:
        return 0.0
    denom = max(1, min(len(left_keys), len(right_keys)))
    return float(len(left_keys & right_keys) / float(denom))


def _required_gate_fusion_weight(
    *,
    g0_run: dict[str, Any] | None,
    required_run: dict[str, Any],
) -> tuple[float, str]:
    required_count = int(required_run.get("candidates_count") or 0)
    if required_count <= 0:
        return 0.0, "required_gate_empty"
    if g0_run is None:
        return 1.0, "required_only"
    g0_count = int(g0_run.get("candidates_count") or 0)
    g0_candidates = list(g0_run.get("candidates") or [])
    required_candidates = list(required_run.get("candidates") or [])
    if g0_count <= 0:
        return 1.10, "g0_empty_required_promoted"

    ratio = float(required_count / max(1, g0_count))
    overlap = _gate_overlap_ratio(
        g0_candidates,
        required_candidates,
        top_k=max(1, int(GATE_FUSION_TOP_K)),
    )
    novelty = max(0.0, 1.0 - overlap)
    weight = 1.0
    reasons: list[str] = []
    if ratio < GATE_FUSION_LOW_RATIO:
        weight -= 0.25
        reasons.append("low_required_ratio")
    elif ratio >= GATE_FUSION_HIGH_RATIO:
        weight += 0.15
        reasons.append("high_required_ratio")
    if overlap >= 0.90:
        weight -= 0.20
        reasons.append("high_overlap")
    elif novelty >= 0.50:
        weight += 0.10
        reasons.append("high_novelty")
    weight = max(
        float(GATE_FUSION_REQUIRED_WEIGHT_MIN),
        min(float(GATE_FUSION_REQUIRED_WEIGHT_MAX), float(weight)),
    )
    if not reasons:
        reasons.append("balanced")
    return weight, "+".join(reasons)


def _diagnose_required_effect_status(
    *,
    requested_required_terms: list[str],
    required_terms_missing: list[str],
    applied_required_terms: list[str],
    strict_required_candidates: int,
    filtered_required_candidates: int,
    g0_candidates: int,
) -> tuple[str, str | None]:
    if not requested_required_terms:
        return "required_effective", None

    missing = [term for term in required_terms_missing if term]
    if missing and len(missing) >= len(requested_required_terms):
        return "required_none_matched", f"required_terms_not_found_in_manual_scope:{','.join(missing)}"

    strict_count = max(0, int(strict_required_candidates))
    filtered_count = max(0, int(filtered_required_candidates))
    g0_count = max(0, int(g0_candidates))
    terms_changed = list(requested_required_terms) != list(applied_required_terms)

    if missing:
        return "term_dropped_or_weakened", f"some_required_terms_not_found_in_manual_scope:{','.join(missing)}"
    if strict_count <= 0 and filtered_count > 0:
        return "term_dropped_or_weakened", "strict_required_zero_candidates_but_filtered_gate_has_hits"
    if strict_count <= 0 and filtered_count <= 0:
        if g0_count > 0:
            return "required_none_matched", "required_terms_zero_candidates_in_manual_scope"
        return "required_none_matched", "no_candidates_across_g0_and_greq"
    if terms_changed and filtered_count <= 0:
        return "term_dropped_or_weakened", "filtered_required_zero_after_df_guard"
    if terms_changed and filtered_count < strict_count:
        return "term_dropped_or_weakened", "filtered_required_weaker_than_strict_after_df_guard"
    return "required_effective", None


def _required_signal_hits_in_top_candidates(
    candidates: list[dict[str, Any]],
    *,
    top_k: int,
) -> int:
    if top_k <= 0 or not candidates:
        return 0
    hits = 0
    for item in candidates[:top_k]:
        signals = set(item.get("signals") or [])
        ref = item.get("ref")
        if isinstance(ref, dict):
            signals.update(ref.get("signals") or [])
        if "required_term" in signals or "required_term_and" in signals:
            hits += 1
    return hits


def _merge_gate_unscanned_sections(gate_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _merge_required_unscanned_sections([list(row.get("unscanned") or []) for row in gate_runs])


def _enforce_gate_unique_top_candidates(
    *,
    selected_rows: list[dict[str, Any]],
    all_rows: list[dict[str, Any]],
    gate_runs: list[dict[str, Any]],
    max_candidates: int,
) -> tuple[list[dict[str, Any]], set[str], list[dict[str, Any]]]:
    if max_candidates <= 0:
        return [], set(), []
    selected = list(selected_rows[:max_candidates])
    selected_keys = {_candidate_key(item) for item in selected}
    all_by_key = {_candidate_key(item): item for item in all_rows}
    guaranteed_keys: set[str] = set()
    injected_events: list[dict[str, Any]] = []

    for gate_run in gate_runs:
        gate_label = str(gate_run.get("gate") or "")
        gate_candidates = list(gate_run.get("candidates") or [])
        if not gate_label or not gate_candidates:
            continue
        top_row = gate_candidates[0]
        top_key = _candidate_key(top_row)
        guaranteed_keys.add(top_key)
        if top_key in selected_keys:
            injected_events.append({"gate": gate_label, "candidate_key": top_key, "injected": False})
            continue

        replacement_key: str | None = None
        if len(selected) >= max_candidates:
            replace_index: int | None = None
            for idx in range(len(selected) - 1, -1, -1):
                row_key = _candidate_key(selected[idx])
                if row_key not in guaranteed_keys:
                    replace_index = idx
                    replacement_key = row_key
                    break
            if replace_index is None:
                injected_events.append(
                    {
                        "gate": gate_label,
                        "candidate_key": top_key,
                        "injected": False,
                        "reason": "max_candidates_all_guaranteed",
                    }
                )
                continue
            selected.pop(replace_index)
            selected_keys.discard(replacement_key or "")

        selected.append(dict(all_by_key.get(top_key, top_row)))
        selected_keys.add(top_key)
        event: dict[str, Any] = {"gate": gate_label, "candidate_key": top_key, "injected": True}
        if replacement_key:
            event["replaced_candidate_key"] = replacement_key
        injected_events.append(event)

    selected.sort(key=_candidate_sort_key)
    return selected[:max_candidates], guaranteed_keys, injected_events


def _fuse_gate_runs_rrf(
    *,
    gate_runs: list[dict[str, Any]],
    max_candidates: int,
) -> tuple[
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
    str,
    str,
    bool,
    str | None,
    str,
    list[dict[str, Any]],
    set[str],
]:
    ensure(bool(gate_runs), "conflict", "gate_runs is empty")
    applied_max_candidates = max(1, int(max_candidates))
    merged_rows: dict[str, dict[str, Any]] = {}
    rrf_scores: dict[str, float] = {}
    contributions: dict[str, list[dict[str, Any]]] = {}
    gate_weight_map: dict[str, dict[str, Any]] = {}
    g0_run = next((row for row in gate_runs if str(row.get("gate") or "") == "g0"), None)

    for gate_run in gate_runs:
        gate_label = str(gate_run.get("gate") or "")
        if not gate_label:
            continue
        if gate_label == "g0":
            weight = float(GATE_FUSION_G0_WEIGHT)
            reason = "baseline"
        else:
            weight, reason = _required_gate_fusion_weight(g0_run=g0_run, required_run=gate_run)
        gate_weight_map[gate_label] = {"weight": round(float(weight), 4), "reason": reason}
        if weight <= 0.0:
            continue
        for rank, item in enumerate(list(gate_run.get("candidates") or []), start=1):
            row = dict(item)
            key = _candidate_key(row)
            prev = merged_rows.get(key)
            if prev is None or _prefer_candidate(row, prev):
                merged_rows[key] = row
            contribution = float(weight) / float(max(1, int(GATE_FUSION_RRF_K)) + rank)
            rrf_scores[key] = rrf_scores.get(key, 0.0) + contribution
            contributions.setdefault(key, []).append(
                {
                    "gate": gate_label,
                    "rank": int(rank),
                    "weight": round(float(weight), 4),
                    "rrf": round(float(contribution), 8),
                }
            )

    if not merged_rows:
        base_fallback = g0_run or gate_runs[0]
        fallback_candidates = list(base_fallback.get("candidates") or [])[:applied_max_candidates]
        return (
            fallback_candidates,
            gate_weight_map,
            [],
            "g0",
            "required_gate_empty_fallback_to_g0",
            True,
            "zero_candidates_with_required_terms",
            str(base_fallback.get("scoring_mode") or "lexical"),
            _merge_gate_unscanned_sections(gate_runs),
            set(),
        )

    base_scores = {key: _candidate_rank_score(row) for key, row in merged_rows.items()}
    base_min = min(base_scores.values(), default=0.0)
    base_max = max(base_scores.values(), default=0.0)
    rrf_min = min(rrf_scores.values(), default=0.0)
    rrf_max = max(rrf_scores.values(), default=0.0)

    rows_for_sort: list[dict[str, Any]] = []
    for key, row in merged_rows.items():
        base_score = float(base_scores.get(key, 0.0))
        rrf_score = float(rrf_scores.get(key, 0.0))
        merged_score, base_norm, rrf_norm = _blend_query_decomp_rrf_score(
            base_score=base_score,
            rrf_score=rrf_score,
            base_min=base_min,
            base_max=base_max,
            rrf_min=rrf_min,
            rrf_max=rrf_max,
            base_weight=float(GATE_FUSION_BASE_WEIGHT),
        )
        row["_rank_score"] = merged_score
        row["score"] = round(float(merged_score), 4)
        _set_candidate_component_scores(
            row,
            score_lexical=merged_score,
            score_fused=merged_score,
        )
        signals = set(row.get("signals") or [])
        signals.add("gate_rrf")
        row["signals"] = sorted(signals)
        ref = dict(row.get("ref") or {})
        ref_signals = set(ref.get("signals") or [])
        ref_signals.add("gate_rrf")
        ref["signals"] = sorted(ref_signals)
        row["ref"] = ref
        rank_explain = list(row.get("rank_explain") or [])
        rank_explain.append(f"gate_rrf={round(rrf_score, 8)}")
        rank_explain.append(f"gate_base_norm={round(base_norm, 4)}")
        rank_explain.append(f"gate_rrf_norm={round(rrf_norm, 4)}")
        row["rank_explain"] = rank_explain
        rows_for_sort.append(row)

    rows_for_sort.sort(key=_candidate_sort_key)
    selected_rows = rows_for_sort[:applied_max_candidates]
    selected_rows, guaranteed_keys, guarantee_events = _enforce_gate_unique_top_candidates(
        selected_rows=selected_rows,
        all_rows=rows_for_sort,
        gate_runs=[row for row in gate_runs if int(row.get("candidates_count") or 0) > 0],
        max_candidates=applied_max_candidates,
    )

    fusion_debug_rows: list[dict[str, Any]] = []
    guaranteed_events_by_key: dict[str, dict[str, Any]] = {}
    for event in guarantee_events:
        key = str(event.get("candidate_key") or "")
        if key and key not in guaranteed_events_by_key:
            guaranteed_events_by_key[key] = event
    for rank, row in enumerate(selected_rows, start=1):
        key = _candidate_key(row)
        fusion_debug_rows.append(
            {
                "candidate_key": key,
                "rank": int(rank),
                "score": round(float(_candidate_rank_score(row)), 4),
                "ref": row.get("ref"),
                "contributions": list(contributions.get(key) or []),
                "guaranteed_top": key in guaranteed_keys,
                "guaranteed_injected": bool((guaranteed_events_by_key.get(key) or {}).get("injected") is True),
            }
        )

    required_runs = [row for row in gate_runs if str(row.get("gate") or "") != "g0"]
    required_has_hits = any(int(row.get("candidates_count") or 0) > 0 for row in required_runs)
    selected_gate = "g_req" if required_has_hits else "g0"
    gate_selection_reason = (
        "rrf_fusion_with_required_gate" if required_has_hits else "required_gate_empty_fallback_to_g0"
    )
    required_relaxed = bool(required_runs) and not required_has_hits
    required_relax_reason = "zero_candidates_with_required_terms" if required_relaxed else None
    scoring_mode = "gate_rrf" if len(gate_runs) > 1 else str(gate_runs[0].get("scoring_mode") or "lexical")

    return (
        selected_rows,
        gate_weight_map,
        fusion_debug_rows,
        selected_gate,
        gate_selection_reason,
        required_relaxed,
        required_relax_reason,
        scoring_mode,
        _merge_gate_unscanned_sections(gate_runs),
        guaranteed_keys,
    )


def manual_find(
    state: AppState,
    query: str,
    manual_id: str | None = None,
    expand_scope: bool | None = None,
    required_terms: list[str] | None = None,
    only_unscanned_from_trace_id: str | None = None,
    budget: dict[str, Any] | None = None,
    include_claim_graph: bool | None = None,
    use_cache: bool | None = None,
    inline_hits: dict[str, Any] | None = None,
    compact: bool | None = None,
    record_adaptive_stats: bool = True,
) -> dict[str, Any]:
    started_at = time.monotonic()
    query = _require_non_empty_string(query, name="query")
    applied_manual_id = _require_manual_id(manual_id, name="manual_id")
    _ensure_not_manuals_root_id(state, applied_manual_id)
    ensure(expand_scope is None or isinstance(expand_scope, bool), "invalid_parameter", "expand_scope must be boolean")
    applied_only_unscanned_trace_id: str | None = None
    if only_unscanned_from_trace_id is not None:
        applied_only_unscanned_trace_id = _require_non_empty_string(
            only_unscanned_from_trace_id,
            name="only_unscanned_from_trace_id",
        )
    if required_terms is None:
        raise ToolError("invalid_parameter", "required_terms is required")
    required_terms_source = "user"
    required_terms_decision_reason: str | None = "provided_by_caller"
    applied_required_terms = _parse_required_terms_param(required_terms)
    if not applied_required_terms:
        raise ToolError("invalid_parameter", "required_terms must contain at least 1 item")
    requested_required_terms = list(applied_required_terms)
    required_terms_df_filtered: list[dict[str, Any]] = []
    applied_required_terms_relaxed = False
    required_terms_relax_reason: str | None = None
    required_effect_status = "required_effective"
    required_failure_reason: str | None = None
    required_strict_candidates = 0
    required_filtered_candidates = 0
    required_terms_match_stats: list[dict[str, Any]] = []
    required_terms_missing: list[str] = []
    required_top_k = 0
    required_top_hits = 0
    selected_gate = "single"
    gate_selection_reason = "single_pass"
    gate_runs: list[dict[str, Any]] = []
    gate_eval_runs: list[dict[str, Any]] = []
    fusion_debug_rows: list[dict[str, Any]] = []
    fused_gate_top_keys: set[str] = set()
    requested_expand_scope = expand_scope if expand_scope is not None else None
    applied_expand_scope = False
    applied_include_claim_graph = _parse_bool_param(
        include_claim_graph,
        name="include_claim_graph",
        default=False,
    )
    applied_use_cache = _parse_bool_param(
        use_cache,
        name="use_cache",
        default=state.config.sem_cache_enabled,
    )
    applied_compact = _parse_bool_param(
        compact,
        name="compact",
        default=False,
    )
    applied_inline_hits = _parse_manual_find_inline_hits_param(inline_hits)
    applied_max_stage = 3

    if budget is None:
        budget_obj: dict[str, Any] = {}
    elif isinstance(budget, dict):
        budget_obj = budget
    else:
        raise ToolError("invalid_parameter", "budget must be object")
    budget_time_ms = _parse_int_param(budget_obj.get("time_ms"), name="budget.time_ms", default=60000, min_value=1)
    max_candidates = _parse_int_param(
        budget_obj.get("max_candidates"),
        name="budget.max_candidates",
        default=200,
        min_value=1,
    )
    candidate_low_threshold, file_bias_threshold = state.adaptive_stats.manual_find_thresholds(
        base_candidate_low=state.config.adaptive_candidate_low_base,
        base_file_bias=state.config.adaptive_file_bias_base,
        adaptive_tuning=state.config.adaptive_tuning,
        min_recall=state.config.adaptive_min_recall,
    )

    selected_manual_ids = [applied_manual_id]
    cache_manual_ids = list(selected_manual_ids)
    required_terms_index: SparseIndex | None = None
    prioritize_paths: dict[str, set[str]] | None = None
    escalation_reasons: list[str] = []
    use_semantic_cache = (
        applied_use_cache
        and not bool(applied_only_unscanned_trace_id)
        and not applied_compact
        and not applied_include_claim_graph
    )
    cache_scope_key: str | None = None
    cache_query: str | None = None
    manuals_fp_lookup: str | None = None
    sem_cache_hit = False
    sem_cache_mode = "miss" if use_semantic_cache else "bypass"
    sem_cache_score: float | None = None
    latency_saved_ms: int | None = None
    ensure(
        _manual_exists(state.config.manuals_root, applied_manual_id),
        "not_found",
        "manual_id not found",
        {"manual_id": applied_manual_id},
    )
    if applied_required_terms:
        if required_terms_index is None:
            required_terms_fp = _manuals_fingerprint(state, selected_manual_ids)
            required_terms_index, _ = state.sparse_index.get_or_build(
                manual_ids=selected_manual_ids,
                fingerprint=required_terms_fp,
            )
        required_terms_match_stats = _required_term_match_stats(
            required_terms=requested_required_terms,
            sparse_index=required_terms_index,
        )
        required_terms_missing = [
            str(item.get("term") or "")
            for item in required_terms_match_stats
            if int(item.get("matched_docs") or 0) <= 0 and str(item.get("term") or "")
        ]
        if required_terms_missing:
            escalation_reasons.append("required_terms_missing_from_manual_scope")
        applied_required_terms, required_terms_df_filtered = _filter_required_terms_by_df(
            required_terms=applied_required_terms,
            sparse_index=required_terms_index,
        )
        if required_terms_df_filtered:
            escalation_reasons.append("required_terms_df_filtered")

    if applied_only_unscanned_trace_id:
        trace = state.traces.get(applied_only_unscanned_trace_id)
        if trace is None:
            raise ToolError("not_found", "trace_id not found", {"trace_id": applied_only_unscanned_trace_id})
        targets = trace.get("unscanned_sections", [])
        prioritize_paths = {}
        for item in targets:
            m = item.get("manual_id")
            p = item.get("path")
            if not m or not p:
                continue
            if applied_manual_id and m != applied_manual_id:
                continue
            prioritize_paths.setdefault(m, set()).add(p)
        if prioritize_paths:
            escalation_reasons.append("prioritized_unscanned_sections")

    if use_semantic_cache:
        # Cache scope must preserve caller intent (requested terms),
        # otherwise different requested terms that collapse after DF guard
        # can incorrectly share one cached payload.
        cache_required_terms = requested_required_terms
        cache_scope_key = _manual_find_scope_key(
            manual_id=applied_manual_id,
            max_candidates=max_candidates,
            budget_time_ms=budget_time_ms,
            required_terms=cache_required_terms,
        )
        cache_query = _cacheable_query(query)
        manuals_fp_lookup = _manuals_fingerprint(state, cache_manual_ids)
        exact_cached = state.semantic_cache.lookup_exact(
            scope_key=cache_scope_key,
            normalized_query=cache_query,
            manuals_fingerprint=manuals_fp_lookup,
        )
        if exact_cached.hit:
            cached_trace_payload, source_latency_ms = _cached_trace_payload_and_source_latency(exact_cached.value)
            if cached_trace_payload is not None:
                cached_trace_payload = _apply_cached_request_overrides(
                    trace_payload=cached_trace_payload,
                    requested_expand_scope=requested_expand_scope,
                )
                cached_summary = cached_trace_payload.get("summary")
                if isinstance(cached_summary, dict) and _cached_summary_is_acceptable(state, cached_summary):
                    sem_cache_hit = True
                    sem_cache_mode = "exact"
                    sem_cache_score = exact_cached.score
                    if source_latency_ms is not None:
                        elapsed_ms = int((time.monotonic() - started_at) * 1000)
                        latency_saved_ms = max(0, source_latency_ms - elapsed_ms)
                    cached_trace_payload = _apply_sem_cache_diagnostics_to_trace_payload(
                        trace_payload=cached_trace_payload,
                        sem_cache_used=use_semantic_cache,
                        sem_cache_hit=sem_cache_hit,
                        sem_cache_mode=sem_cache_mode,
                        sem_cache_score=sem_cache_score,
                        sem_cache_latency_saved_ms=latency_saved_ms,
                    )
                    if record_adaptive_stats:
                        cached_candidates = cached_trace_payload.get("candidates")
                        cached_unscanned = cached_trace_payload.get("unscanned_sections")
                        _record_manual_find_stats(
                            state,
                            query=query,
                            summary=cached_summary,
                            scanned_files=0,
                            scanned_nodes=0,
                            candidates_count=len(cached_candidates) if isinstance(cached_candidates, list) else 0,
                            warnings=0,
                            max_stage_applied=applied_max_stage,
                            scope_expanded=False,
                            cutoff_reason=None,
                            unscanned_sections_count=len(cached_unscanned) if isinstance(cached_unscanned, list) else 0,
                            candidate_low_threshold=candidate_low_threshold,
                            file_bias_threshold=file_bias_threshold,
                            sem_cache_hit=sem_cache_hit,
                            sem_cache_mode=sem_cache_mode,
                            sem_cache_score=sem_cache_score,
                            latency_saved_ms=latency_saved_ms,
                            scoring_mode="cache",
                        )
                    trace_id = state.traces.create(cached_trace_payload)
                    out = _out_from_trace_payload(
                        trace_id=trace_id,
                        trace_payload=cached_trace_payload,
                        include_claim_graph=applied_include_claim_graph,
                        compact=applied_compact,
                    )
                    return _attach_manual_find_inline_hits(
                        state=state,
                        out=out,
                        trace_id=trace_id,
                        inline_hits_spec=applied_inline_hits,
                    )
                sem_cache_mode = "guard_revalidate"

        semantic_cached = state.semantic_cache.lookup_semantic(
            scope_key=cache_scope_key,
            normalized_query=cache_query,
            manuals_fingerprint=manuals_fp_lookup,
            sim_threshold=state.config.sem_cache_sim_threshold,
        )
        if semantic_cached.hit:
            cached_trace_payload, source_latency_ms = _cached_trace_payload_and_source_latency(semantic_cached.value)
            if cached_trace_payload is not None:
                cached_trace_payload = _apply_cached_request_overrides(
                    trace_payload=cached_trace_payload,
                    requested_expand_scope=requested_expand_scope,
                )
                cached_summary = cached_trace_payload.get("summary")
                if isinstance(cached_summary, dict) and _cached_summary_is_acceptable(state, cached_summary):
                    sem_cache_hit = True
                    sem_cache_mode = "semantic"
                    sem_cache_score = semantic_cached.score
                    if source_latency_ms is not None:
                        elapsed_ms = int((time.monotonic() - started_at) * 1000)
                        latency_saved_ms = max(0, source_latency_ms - elapsed_ms)
                    cached_trace_payload = _apply_sem_cache_diagnostics_to_trace_payload(
                        trace_payload=cached_trace_payload,
                        sem_cache_used=use_semantic_cache,
                        sem_cache_hit=sem_cache_hit,
                        sem_cache_mode=sem_cache_mode,
                        sem_cache_score=sem_cache_score,
                        sem_cache_latency_saved_ms=latency_saved_ms,
                    )
                    if record_adaptive_stats:
                        cached_candidates = cached_trace_payload.get("candidates")
                        cached_unscanned = cached_trace_payload.get("unscanned_sections")
                        _record_manual_find_stats(
                            state,
                            query=query,
                            summary=cached_summary,
                            scanned_files=0,
                            scanned_nodes=0,
                            candidates_count=len(cached_candidates) if isinstance(cached_candidates, list) else 0,
                            warnings=0,
                            max_stage_applied=applied_max_stage,
                            scope_expanded=False,
                            cutoff_reason=None,
                            unscanned_sections_count=len(cached_unscanned) if isinstance(cached_unscanned, list) else 0,
                            candidate_low_threshold=candidate_low_threshold,
                            file_bias_threshold=file_bias_threshold,
                            sem_cache_hit=sem_cache_hit,
                            sem_cache_mode=sem_cache_mode,
                            sem_cache_score=sem_cache_score,
                            latency_saved_ms=latency_saved_ms,
                            scoring_mode="cache",
                        )
                    trace_id = state.traces.create(cached_trace_payload)
                    out = _out_from_trace_payload(
                        trace_id=trace_id,
                        trace_payload=cached_trace_payload,
                        include_claim_graph=applied_include_claim_graph,
                        compact=applied_compact,
                    )
                    return _attach_manual_find_inline_hits(
                        state=state,
                        out=out,
                        trace_id=trace_id,
                        inline_hits_spec=applied_inline_hits,
                    )
                sem_cache_mode = "guard_revalidate"

    applied_required_terms_for_gate = list(applied_required_terms[:REQUIRED_TERMS_MAX_ITEMS])
    requested_required_terms_for_gate = list(requested_required_terms[:REQUIRED_TERMS_MAX_ITEMS])
    strict_gate_diag_result: dict[str, Any] | None = None
    gate_plan: list[tuple[str, list[str]]] = [("g0", [])]
    if applied_required_terms_for_gate:
        gate_plan.append(("g_req", list(applied_required_terms_for_gate)))

    for gate_label, gate_required_terms in gate_plan:
        gate_result = _run_find_gate_pass(
            state=state,
            manual_ids=selected_manual_ids,
            query=query,
            max_stage=applied_max_stage,
            budget_time_ms=budget_time_ms,
            max_candidates=max_candidates,
            required_terms=gate_required_terms,
            prioritize_paths=prioritize_paths,
            allowed_paths=None,
        )
        gate_result["gate"] = gate_label
        gate_eval_runs.append(gate_result)

    if (
        requested_required_terms_for_gate
        and requested_required_terms_for_gate != applied_required_terms_for_gate
    ):
        strict_gate_diag_result = _run_find_gate_pass(
            state=state,
            manual_ids=selected_manual_ids,
            query=query,
            max_stage=applied_max_stage,
            budget_time_ms=budget_time_ms,
            max_candidates=max_candidates,
            required_terms=requested_required_terms_for_gate,
            prioritize_paths=prioritize_paths,
            allowed_paths=None,
        )

    g0_gate_eval = next((row for row in gate_eval_runs if str(row.get("gate") or "") == "g0"), None)
    g_req_gate_eval = next((row for row in gate_eval_runs if str(row.get("gate") or "") == "g_req"), None)
    g0_candidates = int((g0_gate_eval or {}).get("candidates_count") or 0)
    required_filtered_candidates = int((g_req_gate_eval or {}).get("candidates_count") or 0)
    if strict_gate_diag_result is not None:
        required_strict_candidates = int(strict_gate_diag_result.get("candidates_count") or 0)
    elif requested_required_terms_for_gate == applied_required_terms_for_gate:
        required_strict_candidates = int(required_filtered_candidates)
    required_effect_status, required_failure_reason = _diagnose_required_effect_status(
        requested_required_terms=requested_required_terms_for_gate,
        required_terms_missing=required_terms_missing,
        applied_required_terms=applied_required_terms_for_gate,
        strict_required_candidates=required_strict_candidates,
        filtered_required_candidates=required_filtered_candidates,
        g0_candidates=g0_candidates,
    )
    if required_effect_status != "required_effective":
        escalation_reasons.append(f"required_effect_{required_effect_status}")

    (
        candidates,
        gate_weight_map,
        fusion_debug_rows,
        selected_gate,
        gate_selection_reason,
        fused_required_relaxed,
        fused_required_relax_reason,
        scoring_mode,
        unscanned,
        fused_gate_top_keys,
    ) = _fuse_gate_runs_rrf(
        gate_runs=gate_eval_runs,
        max_candidates=max_candidates,
    )
    scanned_files = max((int(row.get("scanned_files") or 0) for row in gate_eval_runs), default=0)
    scanned_nodes = max((int(row.get("scanned_nodes") or 0) for row in gate_eval_runs), default=0)
    warnings = max((int(row.get("warnings") or 0) for row in gate_eval_runs), default=0)
    cutoff_reason = next((row.get("cutoff_reason") for row in gate_eval_runs if row.get("cutoff_reason") is not None), None)
    index_rebuilt = any(bool(row.get("index_rebuilt") or False) for row in gate_eval_runs)
    index_docs = max((int(row.get("index_docs") or 0) for row in gate_eval_runs), default=0)
    query_decomp_applied = any(bool(row.get("query_decomp_applied") or False) for row in gate_eval_runs)

    for gate_result in gate_eval_runs:
        gate_label = str(gate_result.get("gate") or "")
        weight_meta = gate_weight_map.get(gate_label, {"weight": 0.0, "reason": "none"})
        weight_value = float(weight_meta.get("weight") or 0.0)
        has_candidates = int(gate_result.get("candidates_count") or 0) > 0
        gate_runs.append(
            {
                "gate": gate_label,
                "required_terms": list(gate_result.get("required_terms") or []),
                "candidates_count": int(gate_result.get("candidates_count") or 0),
                "file_bias_ratio": round(float(gate_result.get("file_bias_ratio") or 0.0), 4),
                "exception_hits": int(gate_result.get("exception_hits") or 0),
                "top_score": round(float(gate_result.get("top_score") or 0.0), 4),
                "scoring_mode": str(gate_result.get("scoring_mode") or "lexical"),
                "cutoff_reason": gate_result.get("cutoff_reason"),
                "weight": round(weight_value, 4),
                "weight_reason": str(weight_meta.get("reason") or "none"),
                "selected": bool(weight_value > 0.0 and (gate_label == "g0" or has_candidates)),
            }
        )

    if fused_required_relaxed:
        applied_required_terms_relaxed = True
        required_terms_relax_reason = fused_required_relax_reason
        escalation_reasons.append("required_terms_relaxed_after_gate_fallback")

    if (
        applied_required_terms_relaxed
        and len(applied_required_terms) >= 2
        and required_terms_relax_reason == "zero_candidates_with_required_terms"
        and candidates
    ):
        candidates, relaxed_filtered = _filter_relaxed_candidates(candidates)
        if relaxed_filtered:
            escalation_reasons.append("relaxed_noise_filtered")

    total, file_bias, exception_hits = _candidate_metrics(candidates)
    should_expand = _should_expand_scope(
        total=total,
        file_bias=file_bias,
        exception_hits=exception_hits,
        candidate_low_threshold=candidate_low_threshold,
        file_bias_threshold=file_bias_threshold,
    )
    if applied_manual_id and should_expand:
        if total == 0:
            escalation_reasons.append("zero_candidates")
        if total < candidate_low_threshold:
            escalation_reasons.append("low_candidates")
        if total >= 5 and file_bias >= file_bias_threshold:
            escalation_reasons.append("file_bias")
        cutoff_reason = cutoff_reason or "stage_cap"
        escalation_reasons.append("stage_cap")
        pending_scope_ids = [mid for mid in discover_manual_ids(state.config.manuals_root) if mid != applied_manual_id]
        for extra_id in pending_scope_ids:
            for row in list_manual_files(state.config.manuals_root, manual_id=extra_id):
                unscanned.append({"manual_id": extra_id, "path": row.path, "reason": "stage_cap"})
    candidates = _apply_file_diversity_rerank(candidates)
    pre_cutoff_candidates = list(candidates)
    candidates, dynamic_cutoff_applied = _apply_dynamic_candidate_cutoff(
        candidates,
        requested_max_candidates=max_candidates,
    )
    if fused_gate_top_keys and candidates:
        candidates, _unused_guaranteed, final_injected_events = _enforce_gate_unique_top_candidates(
            selected_rows=candidates,
            all_rows=pre_cutoff_candidates,
            gate_runs=[row for row in gate_eval_runs if int(row.get("candidates_count") or 0) > 0],
            max_candidates=max(1, len(candidates)),
        )
        if any(bool(item.get("injected")) for item in final_injected_events):
            escalation_reasons.append("gate_unique_top_injected_after_cutoff")
            injected_keys = {
                str(item.get("candidate_key") or "")
                for item in final_injected_events
                if bool(item.get("injected")) and str(item.get("candidate_key") or "")
            }
            fusion_debug_by_key = {
                str(item.get("candidate_key") or ""): dict(item)
                for item in fusion_debug_rows
                if str(item.get("candidate_key") or "")
            }
            for key in injected_keys:
                row = fusion_debug_by_key.get(key)
                if row is None:
                    fusion_debug_by_key[key] = {
                        "candidate_key": key,
                        "contributions": [],
                        "guaranteed_top": key in fused_gate_top_keys,
                        "guaranteed_injected": True,
                    }
                else:
                    row["guaranteed_injected"] = True
            fusion_debug_rows = list(fusion_debug_by_key.values())
    if dynamic_cutoff_applied and cutoff_reason is None:
        cutoff_reason = "dynamic_cutoff"
    if fusion_debug_rows:
        fusion_debug_by_key = {
            str(item.get("candidate_key") or ""): dict(item)
            for item in fusion_debug_rows
            if str(item.get("candidate_key") or "")
        }
        rebuilt_debug_rows: list[dict[str, Any]] = []
        for rank, item in enumerate(candidates, start=1):
            key = _candidate_key(item)
            base_debug = dict(fusion_debug_by_key.get(key) or {})
            base_debug["candidate_key"] = key
            base_debug["rank"] = int(rank)
            base_debug["score"] = round(float(_candidate_rank_score(item)), 4)
            base_debug["ref"] = item.get("ref")
            base_debug["guaranteed_top"] = bool(base_debug.get("guaranteed_top") or (key in fused_gate_top_keys))
            base_debug["guaranteed_injected"] = bool(base_debug.get("guaranteed_injected") is True)
            if "contributions" not in base_debug or not isinstance(base_debug.get("contributions"), list):
                base_debug["contributions"] = []
            rebuilt_debug_rows.append(base_debug)
        fusion_debug_rows = rebuilt_debug_rows
    required_top_k = min(max(0, int(REQUIRED_EFFECT_TOP_K)), len(candidates))
    required_top_hits = _required_signal_hits_in_top_candidates(candidates, top_k=required_top_k)
    if requested_required_terms_for_gate and required_effect_status == "required_effective" and required_top_k > 0:
        if required_top_hits <= 0:
            required_effect_status = "required_fallback"
            required_failure_reason = f"required_terms_not_in_top_{required_top_k}"
            escalation_reasons.append("required_effect_required_fallback")
    for item in candidates:
        final_score = _candidate_rank_score(item)
        item["score"] = round(final_score, 4)
        item["score_lexical"] = round(final_score, 4)
        item["score_fused"] = round(final_score, 4)
    claim_graph_enabled = (
        bool(state.config.manual_find_claim_graph_enabled)
        and not applied_compact
        and applied_include_claim_graph
    )
    if claim_graph_enabled:
        claim_graph = _build_claim_graph(
            query=query,
            candidates=candidates,
        )
    else:
        claim_graph = {
            "claims": [],
            "evidences": [],
            "edges": [],
            "facets": [],
        }
    summary = _build_retrieval_summary(
        candidates=candidates,
        scanned_files=scanned_files,
        scanned_nodes=scanned_nodes,
        candidate_low_threshold=candidate_low_threshold,
        file_bias_threshold=file_bias_threshold,
    )
    summary_token_estimate = max(1, len(str(summary)) // 4)
    marginal_gain = len(candidates) / summary_token_estimate
    if marginal_gain < state.config.marginal_gain_min and summary["integration_status"] == "ready":
        summary["integration_status"] = "needs_followup"
        escalation_reasons.append("low_marginal_gain")
    if applied_compact:
        next_actions = []
    else:
        next_actions = _plan_next_actions_with_planner(
            state=state,
            summary=summary,
            query=query,
            max_stage=applied_max_stage,
            manual_id=applied_manual_id,
            candidates=candidates,
        )
        if required_effect_status in {"term_dropped_or_weakened", "required_none_matched"}:
            rewrite_retry_action = {
                "type": "manual_find",
                "confidence": 0.6,
                "params": {
                    "query": query,
                    "manual_id": applied_manual_id,
                    "required_terms": requested_required_terms_for_gate,
                    "expand_scope": False,
                    "use_cache": False,
                },
            }
            if not any(
                item.get("type") == "manual_find"
                and isinstance(item.get("params"), dict)
                and (item.get("params") or {}).get("manual_id") == applied_manual_id
                and (item.get("params") or {}).get("query") == query
                for item in next_actions
                if isinstance(item, dict)
            ):
                next_actions.append(rewrite_retry_action)
    evidences_by_id = {item["evidence_id"]: item for item in claim_graph.get("evidences", [])}
    conflict_by_claim: dict[str, dict[str, Any]] = {}
    gap_rows: list[dict[str, Any]] = []
    while len(gap_rows) < summary["gap_count"]:
        gap_rows.append(
            {
                "ref": None,
                "path": None,
                "start_line": None,
                "reason": "gap",
                "signals": [],
                "score": None,
                "conflict_with": [],
                "gap_hint": "no candidates matched the current query scope",
            }
        )

    trace_payload = {
        "query": query,
        "manual_id": applied_manual_id,
        "applied": {
            "manual_id": applied_manual_id,
            "requested_expand_scope": requested_expand_scope,
            "expand_scope": applied_expand_scope,
            "required_terms_source": required_terms_source,
            "required_terms_decision_reason": required_terms_decision_reason,
            "requested_required_terms": requested_required_terms,
            "required_terms": applied_required_terms,
            "required_terms_df_filtered": required_terms_df_filtered,
            "required_terms_relaxed": applied_required_terms_relaxed,
            "required_terms_relax_reason": required_terms_relax_reason,
            "required_effect_status": required_effect_status,
            "required_failure_reason": required_failure_reason,
            "required_strict_candidates": required_strict_candidates,
            "required_filtered_candidates": required_filtered_candidates,
            "required_terms_match_stats": required_terms_match_stats,
            "required_terms_missing": required_terms_missing,
            "required_top_k": required_top_k,
            "required_top_hits": required_top_hits,
            "selected_gate": selected_gate,
            "gate_selection_reason": gate_selection_reason,
        },
        "gate_runs": gate_runs,
        "fusion_debug": fusion_debug_rows,
        "claim_graph": claim_graph,
        "summary": summary,
        "next_actions": next_actions,
        "candidates": candidates,
        "unscanned_sections": [
            {
                "manual_id": item.get("manual_id") or applied_manual_id,
                "path": item["path"],
                "start_line": None,
                "reason": item.get("reason") or "time_budget",
                "signals": [],
                "score": None,
                "ref": None,
                "conflict_with": [],
                "gap_hint": None,
            }
            for item in unscanned
        ],
        "conflicts": [
            {
                "ref": (evidences_by_id.get(edge["to_evidence_id"]) or {}).get("ref"),
                "path": ((evidences_by_id.get(edge["to_evidence_id"]) or {}).get("ref") or {}).get("path"),
                "start_line": ((evidences_by_id.get(edge["to_evidence_id"]) or {}).get("ref") or {}).get("start_line"),
                "reason": "claim_conflict",
                "signals": (evidences_by_id.get(edge["to_evidence_id"]) or {}).get("signals") or [],
                "score": (evidences_by_id.get(edge["to_evidence_id"]) or {}).get("score"),
                "conflict_with": [edge["from_claim_id"]],
                "gap_hint": None,
            }
            for edge in conflict_by_claim.values()
        ],
        "gaps": gap_rows,
        "integrated_top": [
            {**item, "reason": "ranked_by_integration"}
            for item in candidates
        ],
        "escalation_reasons": sorted(set(escalation_reasons)),
        "cutoff_reason": cutoff_reason,
    }
    trace_id = state.traces.create(trace_payload)
    if record_adaptive_stats:
        _record_manual_find_stats(
            state,
            query=query,
            summary=summary,
            scanned_files=scanned_files,
            scanned_nodes=scanned_nodes,
            candidates_count=len(candidates),
            warnings=warnings,
            max_stage_applied=applied_max_stage,
            cutoff_reason=cutoff_reason,
            unscanned_sections_count=len(unscanned),
            candidate_low_threshold=candidate_low_threshold,
            file_bias_threshold=file_bias_threshold,
            sem_cache_hit=sem_cache_hit,
            sem_cache_mode=sem_cache_mode,
            sem_cache_score=sem_cache_score,
            latency_saved_ms=latency_saved_ms,
            scoring_mode=scoring_mode,
            index_rebuilt=index_rebuilt,
            index_docs=index_docs,
            scope_expanded=applied_expand_scope,
        )

    if use_semantic_cache and cache_scope_key and cache_query:
        manuals_fp_put = manuals_fp_lookup or _manuals_fingerprint(state, cache_manual_ids)
        source_latency_ms = int((time.monotonic() - started_at) * 1000)
        state.semantic_cache.put(
            scope_key=cache_scope_key,
            normalized_query=cache_query,
            manuals_fingerprint=manuals_fp_put,
            payload={"trace_payload": trace_payload, "source_latency_ms": source_latency_ms},
        )

    applied_out = {
        "manual_id": applied_manual_id,
        "requested_expand_scope": requested_expand_scope,
        "expand_scope": applied_expand_scope,
        "required_terms_source": required_terms_source,
        "required_terms_decision_reason": required_terms_decision_reason,
        "requested_required_terms": requested_required_terms,
        "required_terms": applied_required_terms,
        "required_terms_df_filtered": required_terms_df_filtered,
        "required_terms_relaxed": applied_required_terms_relaxed,
        "required_terms_relax_reason": required_terms_relax_reason,
        "required_effect_status": required_effect_status,
        "required_failure_reason": required_failure_reason,
        "required_strict_candidates": required_strict_candidates,
        "required_filtered_candidates": required_filtered_candidates,
        "required_terms_match_stats": required_terms_match_stats,
        "required_terms_missing": required_terms_missing,
        "required_top_k": required_top_k,
        "required_top_hits": required_top_hits,
        "selected_gate": selected_gate,
        "gate_selection_reason": gate_selection_reason,
        "sem_cache_used": use_semantic_cache,
        "sem_cache_hit": sem_cache_hit,
        "sem_cache_mode": sem_cache_mode,
        "sem_cache_score": round(sem_cache_score, 4) if sem_cache_score is not None else None,
        "sem_cache_latency_saved_ms": latency_saved_ms,
    }
    if applied_compact:
        out = _compact_manual_find_output(
            trace_id=trace_id,
            summary=summary,
            applied=applied_out,
            next_actions=next_actions,
        )
    else:
        out = {
            "trace_id": trace_id,
            "summary": summary,
            "next_actions": next_actions,
            "selected_gate": selected_gate,
            "gate_selection_reason": gate_selection_reason,
            "applied": applied_out,
        }
        if applied_include_claim_graph:
            out["claim_graph"] = claim_graph
    return _attach_manual_find_inline_hits(
        state=state,
        out=out,
        trace_id=trace_id,
        inline_hits_spec=applied_inline_hits,
    )


def manual_hits(
    state: AppState,
    trace_id: str,
    kind: str | None = None,
    offset: int | None = None,
    limit: int | None = None,
    compact: bool | None = None,
) -> dict[str, Any]:
    applied_trace_id = _require_non_empty_string(trace_id, name="trace_id")
    payload = state.traces.get(applied_trace_id)
    if payload is None:
        raise ToolError("not_found", "trace_id not found", {"trace_id": applied_trace_id})
    if kind is None:
        applied_kind = "candidates"
    else:
        if not isinstance(kind, str):
            raise ToolError("invalid_parameter", "kind must be string")
        applied_kind = kind
    ensure(
        applied_kind in {
            "candidates",
            "unscanned",
            "conflicts",
            "gaps",
            "integrated_top",
            "claims",
            "evidences",
            "edges",
            "gate_runs",
            "fusion_debug",
        },
        "invalid_parameter",
        "invalid kind",
    )
    applied_offset = _parse_int_param(offset, name="offset", default=0, min_value=0)
    applied_limit = _parse_int_param(limit, name="limit", default=50, min_value=1)
    applied_compact = _parse_bool_param(compact, name="compact", default=False)

    key_map = {
        "candidates": "candidates",
        "unscanned": "unscanned_sections",
        "conflicts": "conflicts",
        "gaps": "gaps",
        "integrated_top": "integrated_top",
        "claims": "claim_graph.claims",
        "evidences": "claim_graph.evidences",
        "edges": "claim_graph.edges",
        "gate_runs": "gate_runs",
        "fusion_debug": "fusion_debug",
    }
    mapped_key = key_map[applied_kind]
    if "." in mapped_key:
        parent, child = mapped_key.split(".", 1)
        rows = (payload.get(parent) or {}).get(child, [])
    else:
        rows = payload.get(mapped_key, [])
    shared_manual_id: str | None = None
    if applied_kind in {"candidates", "integrated_top"}:
        manual_ids = {
            str(((item.get("ref") or {}).get("manual_id")))
            for item in rows
            if (item.get("ref") or {}).get("manual_id")
        }
        shared_manual_id = next(iter(manual_ids)) if len(manual_ids) == 1 else None
    if applied_compact and applied_kind in {"candidates", "integrated_top"}:
        compact_rows: list[dict[str, Any]] = []
        for item in rows:
            ref = dict(item.get("ref") or {})
            compact_ref: dict[str, Any] = {}
            if applied_kind == "integrated_top":
                if ref.get("manual_id"):
                    compact_ref["manual_id"] = ref["manual_id"]
            elif not shared_manual_id and ref.get("manual_id"):
                compact_ref["manual_id"] = ref["manual_id"]
            if ref.get("path"):
                compact_ref["path"] = ref["path"]
            if ref.get("start_line") is not None:
                compact_ref["start_line"] = ref["start_line"]
            compact_item: dict[str, Any] = {"ref": compact_ref}
            if applied_kind == "integrated_top":
                title = ref.get("title")
                if isinstance(title, str) and title:
                    compact_item["title"] = title
            score = item.get("score")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                compact_item["score"] = round(float(score), 4)
            matched_tokens = item.get("matched_tokens")
            if isinstance(matched_tokens, list) and matched_tokens:
                compact_item["matched_tokens"] = matched_tokens
            compact_rows.append(compact_item)
        rows = compact_rows
    elif applied_kind == "candidates":
        compact_rows = []
        for item in rows:
            ref = dict(item.get("ref") or {})
            compact_ref: dict[str, Any] = {}
            if not shared_manual_id and ref.get("manual_id"):
                compact_ref["manual_id"] = ref["manual_id"]
            if ref.get("path"):
                compact_ref["path"] = ref["path"]
            if ref.get("start_line") is not None:
                compact_ref["start_line"] = ref["start_line"]
            if ref.get("signals"):
                compact_ref["signals"] = ref["signals"]

            compact_item: dict[str, Any] = {"ref": compact_ref}
            score = item.get("score")
            if score is not None:
                compact_item["score"] = score
            score_lexical = item.get("score_lexical")
            if isinstance(score_lexical, (int, float)) and not isinstance(score_lexical, bool):
                compact_item["score_lexical"] = round(float(score_lexical), 4)
            score_fused = item.get("score_fused")
            if isinstance(score_fused, (int, float)) and not isinstance(score_fused, bool):
                compact_item["score_fused"] = round(float(score_fused), 4)
            matched_tokens = item.get("matched_tokens")
            if isinstance(matched_tokens, list) and matched_tokens:
                compact_item["matched_tokens"] = matched_tokens
            token_hits = item.get("token_hits")
            if isinstance(token_hits, dict) and token_hits:
                compact_item["token_hits"] = token_hits
            match_coverage = item.get("match_coverage")
            if isinstance(match_coverage, (int, float)) and not isinstance(match_coverage, bool):
                compact_item["match_coverage"] = round(float(match_coverage), 4)
            rank_explain = item.get("rank_explain")
            if isinstance(rank_explain, list) and rank_explain:
                compact_item["rank_explain"] = rank_explain
            reason = item.get("reason")
            if reason is not None:
                compact_item["reason"] = reason
            conflict_with = item.get("conflict_with") or []
            if conflict_with:
                compact_item["conflict_with"] = conflict_with
            gap_hint = item.get("gap_hint")
            if gap_hint is not None:
                compact_item["gap_hint"] = gap_hint
            compact_rows.append(compact_item)
        rows = compact_rows
    sliced = rows[applied_offset : applied_offset + applied_limit]
    out = {
        "trace_id": applied_trace_id,
        "kind": applied_kind,
        "offset": applied_offset,
        "limit": applied_limit,
        "total": len(rows),
        "items": sliced,
    }
    if applied_kind == "candidates" and shared_manual_id:
        out["manual_id"] = shared_manual_id
    return out
