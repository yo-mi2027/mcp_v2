#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from mcp_v2_server.config import Config
from mcp_v2_eval.eval_manual_find import (
    DEFAULT_BUDGET_MAX_CANDIDATES,
    DEFAULT_BUDGET_TIME_MS,
    build_eval_report,
    default_thresholds,
    evaluate_manual_find,
    load_eval_cases,
    write_eval_report,
)
from mcp_v2_server.state import create_state


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate manual_find quality with JSONL gold cases.")
    parser.add_argument("--dataset", default="evals/manual_find_gold.jsonl", help="JSONL path for eval dataset")
    parser.add_argument("--out-dir", default="vault/.system/evals", help="Directory for report JSON")
    parser.add_argument("--top-k", type=int, default=5, help="Top-k for hit_rate/precision metrics")
    parser.add_argument("--budget-time-ms", type=int, default=DEFAULT_BUDGET_TIME_MS)
    parser.add_argument("--budget-max-candidates", type=int, default=DEFAULT_BUDGET_MAX_CANDIDATES)
    parser.add_argument("--max-cases", type=int, default=0, help="Run only first N cases (0 means all)")
    parser.add_argument("--include-claim-graph", action="store_true")
    parser.add_argument("--no-expand-scope", action="store_true")
    parser.add_argument("--enforce-thresholds", action="store_true", help="Exit 1 when thresholds fail")
    parser.add_argument("--hit-rate-min", type=float, default=None)
    parser.add_argument("--precision-min", type=float, default=None)
    parser.add_argument("--gap-rate-max", type=float, default=None)
    parser.add_argument("--conflict-rate-max", type=float, default=None)
    parser.add_argument("--p95-latency-max-ms", type=float, default=None)
    parser.add_argument("--error-rate-max", type=float, default=None)
    parser.add_argument(
        "--compare-sem-cache",
        action="store_true",
        help="Run two evaluations (SEM_CACHE off/on) and emit a comparison report.",
    )
    parser.add_argument(
        "--compare-late-rerank",
        action="store_true",
        help="Run two evaluations (late rerank off/on) and emit a comparison report.",
    )
    return parser.parse_args()


def _apply_threshold_overrides(thresholds: dict[str, dict[str, Any]], args: argparse.Namespace, top_k: int) -> None:
    hit_key = f"hit_rate@{top_k}"
    precision_key = f"precision@{top_k}"
    if args.hit_rate_min is not None:
        thresholds[hit_key] = {"op": ">=", "value": float(args.hit_rate_min)}
    if args.precision_min is not None:
        thresholds[precision_key] = {"op": ">=", "value": float(args.precision_min)}
    if args.gap_rate_max is not None:
        thresholds["gap_rate"] = {"op": "<=", "value": float(args.gap_rate_max)}
    if args.conflict_rate_max is not None:
        thresholds["conflict_rate"] = {"op": "<=", "value": float(args.conflict_rate_max)}
    if args.p95_latency_max_ms is not None:
        thresholds["p95_latency_ms"] = {"op": "<=", "value": float(args.p95_latency_max_ms)}
    if args.error_rate_max is not None:
        thresholds["error_rate"] = {"op": "<=", "value": float(args.error_rate_max)}


def _run_once(
    *,
    cfg: Config,
    cases: list[dict[str, Any]],
    top_k: int,
    expand_scope: bool,
    include_claim_graph: bool,
    budget_time_ms: int,
    budget_max_candidates: int,
    thresholds: dict[str, dict[str, Any]],
    dataset_path: Path,
) -> dict[str, Any]:
    state = create_state(cfg)
    results = evaluate_manual_find(
        state,
        cases,
        top_k=top_k,
        expand_scope=expand_scope,
        include_claim_graph=include_claim_graph,
        budget_time_ms=budget_time_ms,
        budget_max_candidates=budget_max_candidates,
        thresholds=thresholds,
    )
    return build_eval_report(
        dataset_path,
        results=results,
        top_k=top_k,
        expand_scope=expand_scope,
        include_claim_graph=include_claim_graph,
        budget_time_ms=budget_time_ms,
        budget_max_candidates=budget_max_candidates,
    )


def _metric_delta(base: dict[str, Any], target: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key in sorted(set(base.keys()) & set(target.keys())):
        lhs = base.get(key)
        rhs = target.get(key)
        if isinstance(lhs, (int, float)) and isinstance(rhs, (int, float)):
            out[key] = round(float(rhs) - float(lhs), 4)
    return out


def main() -> int:
    args = _parse_args()
    dataset_path = Path(args.dataset).expanduser()
    out_dir = Path(args.out_dir).expanduser()
    top_k = max(1, int(args.top_k))
    thresholds = default_thresholds(top_k)
    _apply_threshold_overrides(thresholds, args, top_k)

    cases = load_eval_cases(dataset_path)
    if args.max_cases > 0:
        cases = cases[: args.max_cases]

    base_cfg = Config.from_env()
    expand_scope = not args.no_expand_scope
    include_claim_graph = bool(args.include_claim_graph)
    budget_time_ms = int(args.budget_time_ms)
    budget_max_candidates = int(args.budget_max_candidates)
    if args.compare_sem_cache and args.compare_late_rerank:
        print("choose either --compare-sem-cache or --compare-late-rerank", file=sys.stderr)
        return 2

    if args.compare_sem_cache:
        baseline_report = _run_once(
            cfg=replace(base_cfg, sem_cache_enabled=False),
            cases=cases,
            top_k=top_k,
            expand_scope=expand_scope,
            include_claim_graph=include_claim_graph,
            budget_time_ms=budget_time_ms,
            budget_max_candidates=budget_max_candidates,
            thresholds=thresholds,
            dataset_path=dataset_path,
        )
        sem_cache_report = _run_once(
            cfg=replace(base_cfg, sem_cache_enabled=True),
            cases=cases,
            top_k=top_k,
            expand_scope=expand_scope,
            include_claim_graph=include_claim_graph,
            budget_time_ms=budget_time_ms,
            budget_max_candidates=budget_max_candidates,
            thresholds=thresholds,
            dataset_path=dataset_path,
        )
        delta = _metric_delta(baseline_report["metrics"], sem_cache_report["metrics"])
        report = {
            "mode": "sem_cache_compare",
            "baseline": baseline_report,
            "with_sem_cache": sem_cache_report,
            "metrics_delta": delta,
            "comparison_summary": {
                "baseline_passed": bool(baseline_report["pass_fail"]["all_passed"]),
                "with_sem_cache_passed": bool(sem_cache_report["pass_fail"]["all_passed"]),
                "p95_latency_ms_delta": delta.get("p95_latency_ms"),
                f"hit_rate@{top_k}_delta": delta.get(f"hit_rate@{top_k}"),
                f"precision@{top_k}_delta": delta.get(f"precision@{top_k}"),
            },
        }
    elif args.compare_late_rerank:
        baseline_report = _run_once(
            cfg=replace(base_cfg, late_rerank_enabled=False),
            cases=cases,
            top_k=top_k,
            expand_scope=expand_scope,
            include_claim_graph=include_claim_graph,
            budget_time_ms=budget_time_ms,
            budget_max_candidates=budget_max_candidates,
            thresholds=thresholds,
            dataset_path=dataset_path,
        )
        rerank_report = _run_once(
            cfg=replace(base_cfg, late_rerank_enabled=True),
            cases=cases,
            top_k=top_k,
            expand_scope=expand_scope,
            include_claim_graph=include_claim_graph,
            budget_time_ms=budget_time_ms,
            budget_max_candidates=budget_max_candidates,
            thresholds=thresholds,
            dataset_path=dataset_path,
        )
        delta = _metric_delta(baseline_report["metrics"], rerank_report["metrics"])
        report = {
            "mode": "late_rerank_compare",
            "baseline": baseline_report,
            "with_late_rerank": rerank_report,
            "metrics_delta": delta,
            "comparison_summary": {
                "baseline_passed": bool(baseline_report["pass_fail"]["all_passed"]),
                "with_late_rerank_passed": bool(rerank_report["pass_fail"]["all_passed"]),
                "p95_latency_ms_delta": delta.get("p95_latency_ms"),
                "tokens_per_query_delta": delta.get("tokens_per_query"),
                f"hit_rate@{top_k}_delta": delta.get(f"hit_rate@{top_k}"),
                f"precision@{top_k}_delta": delta.get(f"precision@{top_k}"),
            },
        }
    else:
        report = _run_once(
            cfg=base_cfg,
            cases=cases,
            top_k=top_k,
            expand_scope=expand_scope,
            include_claim_graph=include_claim_graph,
            budget_time_ms=budget_time_ms,
            budget_max_candidates=budget_max_candidates,
            thresholds=thresholds,
            dataset_path=dataset_path,
        )

    dated_path, latest_path = write_eval_report(report, out_dir)
    if args.compare_sem_cache or args.compare_late_rerank:
        print(json.dumps(report["comparison_summary"], ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"metrics": report["metrics"], "pass_fail": report["pass_fail"]}, ensure_ascii=False, indent=2))
    print(f"wrote report: {dated_path.as_posix()}")
    print(f"updated latest: {latest_path.as_posix()}")

    if args.enforce_thresholds:
        if args.compare_sem_cache:
            baseline_pass = bool(report["baseline"]["pass_fail"]["all_passed"])
            sem_cache_pass = bool(report["with_sem_cache"]["pass_fail"]["all_passed"])
            if not (baseline_pass and sem_cache_pass):
                return 1
        elif args.compare_late_rerank:
            baseline_pass = bool(report["baseline"]["pass_fail"]["all_passed"])
            rerank_pass = bool(report["with_late_rerank"]["pass_fail"]["all_passed"])
            if not (baseline_pass and rerank_pass):
                return 1
        elif not report["pass_fail"]["all_passed"]:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
