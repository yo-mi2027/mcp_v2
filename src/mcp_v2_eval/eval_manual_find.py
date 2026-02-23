from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp_v2_server.errors import ToolError
from mcp_v2_server.normalization import normalize_text
from mcp_v2_server.state import AppState
from mcp_v2_server.tools_manual import manual_find, manual_hits

DEFAULT_BUDGET_TIME_MS = 60000
DEFAULT_BUDGET_MAX_CANDIDATES = 200


def default_thresholds(top_k: int = 5) -> dict[str, dict[str, Any]]:
    return {
        f"hit_rate@{top_k}": {"op": ">=", "value": 0.80},
        f"recall@{top_k}": {"op": ">=", "value": 0.80},
        f"mrr@{top_k}": {"op": ">=", "value": 0.60},
        f"precision@{top_k}": {"op": ">=", "value": 0.50},
        "gap_rate": {"op": "<=", "value": 0.25},
        "conflict_rate": {"op": "<=", "value": 0.20},
        "p95_latency_ms": {"op": "<=", "value": 1200},
        "error_rate": {"op": "==", "value": 0.0},
    }


def load_eval_cases(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8")
    for idx, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid jsonl at line {idx}: {e.msg}") from e
        if not isinstance(payload, dict):
            raise ValueError(f"line {idx} must be an object")
        query = payload.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"line {idx} requires non-empty string query")
        expected_paths = payload.get("expected_paths")
        if not isinstance(expected_paths, list) or not expected_paths or not all(isinstance(p, str) and p for p in expected_paths):
            raise ValueError(f"line {idx} requires expected_paths as non-empty string list")
        manual_id = payload.get("manual_id")
        if not isinstance(manual_id, str) or not manual_id.strip():
            raise ValueError(f"line {idx} manual_id requires non-empty string")
        forbidden_paths = payload.get("forbidden_paths", [])
        if not isinstance(forbidden_paths, list) or not all(isinstance(p, str) for p in forbidden_paths):
            raise ValueError(f"line {idx} forbidden_paths must be string list")
        facet = payload.get("facet", "unknown")
        if not isinstance(facet, str):
            raise ValueError(f"line {idx} facet must be string")
        case_id = payload.get("case_id")
        if case_id is None:
            case_id = f"case_{idx:03d}"
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"line {idx} case_id must be non-empty string")
        rows.append(
            {
                "case_id": case_id,
                "facet": facet,
                "query": query,
                "manual_id": manual_id.strip(),
                "expected_paths": expected_paths,
                "forbidden_paths": forbidden_paths,
            }
        )
    if not rows:
        raise ValueError("dataset is empty")
    return rows


def _percentile(values: list[float], p: int) -> float:
    if not values:
        return math.inf
    ordered = sorted(values)
    rank = max(0, math.ceil((p / 100) * len(ordered)) - 1)
    return float(ordered[rank])


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _to_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    raise ValueError("threshold value must be numeric")


def _judge_threshold(metric_value: float, rule: dict[str, Any]) -> bool:
    op = str(rule.get("op", "")).strip()
    threshold_value = _to_float(rule.get("value"))
    if op == ">=":
        return metric_value >= threshold_value
    if op == "<=":
        return metric_value <= threshold_value
    if op == "==":
        return metric_value == threshold_value
    raise ValueError(f"unsupported threshold op: {op}")


def _estimate_case_tokens(summary: dict[str, Any], hit_items: list[dict[str, Any]]) -> int:
    chars = len(json.dumps(summary, ensure_ascii=False))
    for item in hit_items:
        ref = item.get("ref") or {}
        chars += len(str(ref.get("path") or ""))
        chars += 24
    return max(1, (chars + 3) // 4)


def _required_terms_for_case(case: dict[str, Any]) -> list[str]:
    raw = case.get("required_terms")
    if isinstance(raw, list):
        out: list[str] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, str):
                continue
            normalized = normalize_text(item)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
            if len(out) >= 2:
                break
        if out:
            return out
    query = str(case.get("query") or "").strip()
    return [query] if query else ["required"]


def evaluate_manual_find(
    state: AppState,
    cases: list[dict[str, Any]],
    *,
    top_k: int = 5,
    expand_scope: bool = True,
    include_claim_graph: bool = False,
    budget_time_ms: int = DEFAULT_BUDGET_TIME_MS,
    budget_max_candidates: int = DEFAULT_BUDGET_MAX_CANDIDATES,
    thresholds: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if int(top_k) < 1:
        raise ValueError("top_k must be >= 1")
    if int(budget_time_ms) < 1:
        raise ValueError("budget_time_ms must be >= 1")
    if int(budget_max_candidates) < 1:
        raise ValueError("budget_max_candidates must be >= 1")

    metrics_thresholds = dict(default_thresholds(top_k))
    if thresholds:
        metrics_thresholds.update(thresholds)

    rows: list[dict[str, Any]] = []

    for case in cases:
        started = time.monotonic()
        case_id = str(case.get("case_id") or "")
        query = str(case.get("query") or "")
        manual_id = str(case.get("manual_id") or "").strip()
        if not manual_id:
            raise ValueError(f"case {case_id or '<unknown>'} requires non-empty manual_id")
        expected_paths = {str(p) for p in case.get("expected_paths") or []}
        forbidden_paths = {str(p) for p in case.get("forbidden_paths") or []}
        try:
            found = manual_find(
                state,
                query=query,
                manual_id=manual_id,
                expand_scope=expand_scope,
                required_terms=_required_terms_for_case(case),
                budget={"time_ms": int(budget_time_ms), "max_candidates": int(budget_max_candidates)},
                include_claim_graph=include_claim_graph,
                record_adaptive_stats=False,
            )
            trace_id = str(found["trace_id"])
            summary = found.get("summary") or {}
            applied = found.get("applied") if isinstance(found.get("applied"), dict) else {}
            applied_expand_scope = bool(applied.get("expand_scope")) if "expand_scope" in applied else False
            sem_cache_hit = bool(applied.get("sem_cache_hit")) if "sem_cache_hit" in applied else False
            sem_cache_mode = str(applied.get("sem_cache_mode") or "unknown")
            sem_cache_score_raw = applied.get("sem_cache_score")
            sem_cache_score = float(sem_cache_score_raw) if isinstance(sem_cache_score_raw, (int, float)) else None
            sem_cache_latency_saved_raw = applied.get("sem_cache_latency_saved_ms")
            sem_cache_latency_saved_ms = (
                max(0, int(sem_cache_latency_saved_raw))
                if isinstance(sem_cache_latency_saved_raw, (int, float))
                else None
            )
            hits = manual_hits(state, trace_id=trace_id, kind="candidates", offset=0, limit=top_k)
            hit_items = [item for item in (hits.get("items") or []) if isinstance(item, dict)]
            top_paths: list[str] = []
            for item in hit_items:
                ref = item.get("ref") or {}
                path = ref.get("path")
                if isinstance(path, str) and path and path not in top_paths:
                    top_paths.append(path)
            relevant_count = sum(1 for path in top_paths if path in expected_paths)
            hit = relevant_count > 0
            recall = (relevant_count / len(expected_paths)) if expected_paths else 0.0
            first_hit_rank: int | None = None
            for idx, item in enumerate(hit_items, start=1):
                ref = item.get("ref") or {}
                path = ref.get("path")
                if isinstance(path, str) and path in expected_paths:
                    first_hit_rank = idx
                    break
            reciprocal_rank = (1.0 / float(first_hit_rank)) if first_hit_rank is not None else 0.0
            precision = relevant_count / float(top_k)
            precision_retrieved = (relevant_count / len(top_paths)) if top_paths else 0.0
            forbidden_hit = any(path in forbidden_paths for path in top_paths) if forbidden_paths else False
            latency_ms = int((time.monotonic() - started) * 1000)
            est_tokens = _estimate_case_tokens(summary, hit_items)
            integration_status = str(summary.get("integration_status") or "unknown")
            rows.append(
                {
                    "case_id": case_id,
                    "facet": case.get("facet"),
                    "query": query,
                    "manual_id": manual_id,
                    "ok": True,
                    "trace_id": trace_id,
                    "latency_ms": latency_ms,
                    "top_paths": top_paths,
                    "expected_paths": sorted(expected_paths),
                    "forbidden_paths": sorted(forbidden_paths),
                    "hit": hit,
                    "recall": round(recall, 4),
                    "reciprocal_rank": round(reciprocal_rank, 4),
                    "precision": round(precision, 4),
                    "precision_retrieved": round(precision_retrieved, 4),
                    "gap": int(summary.get("gap_count", 0)) > 0,
                    "conflict": int(summary.get("conflict_count", 0)) > 0,
                    "integration_status": integration_status,
                    "needs_followup": integration_status == "needs_followup",
                    "blocked": integration_status == "blocked",
                    "forbidden_hit": forbidden_hit,
                    "requested_expand_scope": bool(expand_scope),
                    "applied_expand_scope": applied_expand_scope,
                    "sem_cache_hit": sem_cache_hit,
                    "sem_cache_mode": sem_cache_mode,
                    "sem_cache_score": round(sem_cache_score, 4) if sem_cache_score is not None else None,
                    "sem_cache_latency_saved_ms": sem_cache_latency_saved_ms,
                    "est_tokens": est_tokens,
                    "error": None,
                }
            )
        except ToolError as e:
            rows.append(
                {
                    "case_id": case_id,
                    "facet": case.get("facet"),
                    "query": query,
                    "manual_id": manual_id,
                    "ok": False,
                    "trace_id": None,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "top_paths": [],
                    "expected_paths": sorted(expected_paths),
                    "forbidden_paths": sorted(forbidden_paths),
                    "hit": False,
                    "recall": 0.0,
                    "reciprocal_rank": 0.0,
                    "precision": 0.0,
                    "precision_retrieved": 0.0,
                    "gap": False,
                    "conflict": False,
                    "integration_status": None,
                    "needs_followup": False,
                    "blocked": False,
                    "forbidden_hit": False,
                    "requested_expand_scope": bool(expand_scope),
                    "applied_expand_scope": None,
                    "sem_cache_hit": False,
                    "sem_cache_mode": "error",
                    "sem_cache_score": None,
                    "sem_cache_latency_saved_ms": None,
                    "est_tokens": 0,
                    "error": {"code": e.code, "message": e.message},
                }
            )
        except Exception as e:  # pragma: no cover - defensive fallback
            rows.append(
                {
                    "case_id": case_id,
                    "facet": case.get("facet"),
                    "query": query,
                    "manual_id": manual_id,
                    "ok": False,
                    "trace_id": None,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "top_paths": [],
                    "expected_paths": sorted(expected_paths),
                    "forbidden_paths": sorted(forbidden_paths),
                    "hit": False,
                    "recall": 0.0,
                    "reciprocal_rank": 0.0,
                    "precision": 0.0,
                    "precision_retrieved": 0.0,
                    "gap": False,
                    "conflict": False,
                    "integration_status": None,
                    "needs_followup": False,
                    "blocked": False,
                    "forbidden_hit": False,
                    "requested_expand_scope": bool(expand_scope),
                    "applied_expand_scope": None,
                    "sem_cache_hit": False,
                    "sem_cache_mode": "error",
                    "sem_cache_score": None,
                    "sem_cache_latency_saved_ms": None,
                    "est_tokens": 0,
                    "error": {"code": "unknown", "message": str(e)},
                }
            )

    total_cases = len(rows)
    if total_cases == 0:
        raise ValueError("no eval cases")
    hit_key = f"hit_rate@{top_k}"
    recall_key = f"recall@{top_k}"
    precision_key = f"precision@{top_k}"
    mrr_key = f"mrr@{top_k}"
    case_latencies = [float(row["latency_ms"]) for row in rows]
    case_tokens = [float(row.get("est_tokens") or 0.0) for row in rows]
    sem_cache_latency_saved_values = [float(row.get("sem_cache_latency_saved_ms") or 0.0) for row in rows]
    metrics = {
        hit_key: round(sum(1 for row in rows if row["hit"]) / total_cases, 4),
        recall_key: round(sum(float(row["recall"]) for row in rows) / total_cases, 4),
        mrr_key: round(sum(float(row["reciprocal_rank"]) for row in rows) / total_cases, 4),
        precision_key: round(sum(float(row["precision"]) for row in rows) / total_cases, 4),
        "precision@retrieved": round(sum(float(row["precision_retrieved"]) for row in rows) / total_cases, 4),
        "gap_rate": round(sum(1 for row in rows if row["gap"]) / total_cases, 4),
        "conflict_rate": round(sum(1 for row in rows if row["conflict"]) / total_cases, 4),
        "needs_followup_rate": round(sum(1 for row in rows if row.get("needs_followup")) / total_cases, 4),
        "blocked_rate": round(sum(1 for row in rows if row.get("blocked")) / total_cases, 4),
        "p95_latency_ms": round(_percentile(case_latencies, 95), 2),
        "tokens_per_query": round(sum(case_tokens) / total_cases, 2),
        "sem_cache_hit_rate": round(sum(1 for row in rows if row.get("sem_cache_hit")) / total_cases, 4),
        "sem_cache_exact_hit_rate": round(
            sum(1 for row in rows if row.get("sem_cache_mode") == "exact") / total_cases, 4
        ),
        "sem_cache_semantic_hit_rate": round(
            sum(1 for row in rows if row.get("sem_cache_mode") == "semantic") / total_cases, 4
        ),
        "sem_cache_guard_revalidate_rate": round(
            sum(1 for row in rows if row.get("sem_cache_mode") == "guard_revalidate") / total_cases, 4
        ),
        "sem_cache_est_latency_saved_ms_total": round(sum(sem_cache_latency_saved_values), 2),
        "sem_cache_est_latency_saved_ms_per_query": round(sum(sem_cache_latency_saved_values) / total_cases, 2),
        "error_rate": round(sum(1 for row in rows if not row["ok"]) / total_cases, 4),
        "forbidden_hit_rate": round(sum(1 for row in rows if row["forbidden_hit"]) / total_cases, 4),
    }
    check_rows: list[dict[str, Any]] = []
    for key, rule in metrics_thresholds.items():
        metric_value = float(metrics.get(key, math.nan))
        passed = _judge_threshold(metric_value, rule)
        check_rows.append(
            {
                "metric": key,
                "value": metric_value,
                "op": rule["op"],
                "threshold": _to_float(rule["value"]),
                "passed": passed,
            }
        )
    all_passed = all(row["passed"] for row in check_rows)
    threshold_failures = [row for row in check_rows if not row["passed"]]
    failed_cases = [row for row in rows if (not row["ok"]) or (not row["hit"]) or row["forbidden_hit"]]
    return {
        "metrics": metrics,
        "thresholds": metrics_thresholds,
        "pass_fail": {"all_passed": all_passed, "checks": check_rows, "threshold_failures": threshold_failures},
        "cases": rows,
        "failed_cases": failed_cases,
    }


def build_eval_report(
    dataset_path: Path,
    *,
    results: dict[str, Any],
    top_k: int,
    expand_scope: bool,
    include_claim_graph: bool,
    budget_time_ms: int,
    budget_max_candidates: int,
    manual_find_claim_graph_enabled: bool | None = None,
) -> dict[str, Any]:
    applied_values = {
        row.get("applied_expand_scope")
        for row in (results.get("cases") or [])
        if isinstance(row, dict) and row.get("applied_expand_scope") is not None
    }
    if len(applied_values) == 1:
        applied_expand_scope: bool | list[bool] | None = bool(next(iter(applied_values)))
    elif applied_values:
        applied_expand_scope = sorted(bool(v) for v in applied_values)
    else:
        applied_expand_scope = None
    find_options: dict[str, Any] = {
        "expand_scope": bool(expand_scope),
        "requested_expand_scope": bool(expand_scope),
        "applied_expand_scope": applied_expand_scope,
        "include_claim_graph": include_claim_graph,
        "budget": {"time_ms": budget_time_ms, "max_candidates": budget_max_candidates},
    }
    if manual_find_claim_graph_enabled is not None:
        find_options["manual_find_claim_graph_enabled"] = bool(manual_find_claim_graph_enabled)

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dataset_path": dataset_path.as_posix(),
        "dataset_hash": _hash_file(dataset_path),
        "top_k": top_k,
        "find_options": find_options,
        "metrics": results["metrics"],
        "thresholds": results["thresholds"],
        "pass_fail": results["pass_fail"],
        "failed_cases": results["failed_cases"],
        "case_count": len(results["cases"]),
    }


def write_eval_report(report: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    dated_path = out_dir / f"{stamp}.json"
    latest_path = out_dir / "latest.json"
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    dated_path.write_text(payload, encoding="utf-8")
    latest_path.write_text(payload, encoding="utf-8")
    return dated_path, latest_path
