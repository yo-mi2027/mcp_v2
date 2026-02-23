from __future__ import annotations

from pathlib import Path

from mcp_v2_eval.eval_manual_find import build_eval_report, evaluate_manual_find


def _sample_cases() -> list[dict[str, object]]:
    return [
        {
            "case_id": "c1",
            "facet": "exceptions",
            "query": "対象外",
            "manual_id": "m1",
            "expected_paths": ["rules.md"],
            "forbidden_paths": [],
        },
        {
            "case_id": "c2",
            "facet": "definition",
            "query": "支払い",
            "manual_id": "m1",
            "expected_paths": ["policy.json"],
            "forbidden_paths": [],
        },
        {
            "case_id": "c3",
            "facet": "procedure",
            "query": "フロー",
            "manual_id": "m2",
            "expected_paths": ["appendix.md"],
            "forbidden_paths": [],
        },
    ]


def test_eval_gate_passes_with_reasonable_thresholds(state) -> None:
    thresholds = {
        "hit_rate@1": {"op": ">=", "value": 0.66},
        "precision@1": {"op": ">=", "value": 0.66},
        "gap_rate": {"op": "<=", "value": 1.0},
        "conflict_rate": {"op": "<=", "value": 1.0},
        "p95_latency_ms": {"op": "<=", "value": 5000},
        "error_rate": {"op": "==", "value": 0.0},
    }
    out = evaluate_manual_find(state, _sample_cases(), top_k=1, thresholds=thresholds)
    assert out["pass_fail"]["all_passed"] is True
    assert out["metrics"]["error_rate"] == 0.0
    assert "recall@1" in out["metrics"]
    assert "mrr@1" in out["metrics"]
    assert "tokens_per_query" in out["metrics"]
    assert "sem_cache_hit_rate" in out["metrics"]
    assert "sem_cache_exact_hit_rate" in out["metrics"]
    assert "sem_cache_semantic_hit_rate" in out["metrics"]
    assert "sem_cache_est_latency_saved_ms_per_query" in out["metrics"]
    assert "needs_followup_rate" in out["metrics"]
    assert "blocked_rate" in out["metrics"]


def test_eval_gate_detects_failure(state) -> None:
    thresholds = {
        "hit_rate@5": {"op": ">=", "value": 1.01},
    }
    out = evaluate_manual_find(state, _sample_cases(), top_k=5, thresholds=thresholds)
    assert out["pass_fail"]["all_passed"] is False
    assert any(check["metric"] == "hit_rate@5" and check["passed"] is False for check in out["pass_fail"]["checks"])


def test_eval_gate_reports_expand_scope_application(state) -> None:
    out = evaluate_manual_find(state, _sample_cases(), top_k=1)
    assert out["cases"]
    first = out["cases"][0]
    assert first["requested_expand_scope"] is True
    assert isinstance(first["applied_expand_scope"], bool)

    report = build_eval_report(
        Path("evals/manual_find_gold.jsonl"),
        results=out,
        top_k=1,
        expand_scope=True,
        include_claim_graph=False,
        budget_time_ms=60000,
        budget_max_candidates=200,
    )
    find_options = report["find_options"]
    assert find_options["requested_expand_scope"] is True
    assert isinstance(find_options["applied_expand_scope"], bool)
