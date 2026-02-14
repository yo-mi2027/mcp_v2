from __future__ import annotations

from mcp_v2_eval.eval_manual_find import evaluate_manual_find


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
    assert "tokens_per_query" in out["metrics"]


def test_eval_gate_detects_failure(state) -> None:
    thresholds = {
        "hit_rate@5": {"op": ">=", "value": 1.01},
    }
    out = evaluate_manual_find(state, _sample_cases(), top_k=5, thresholds=thresholds)
    assert out["pass_fail"]["all_passed"] is False
    assert any(check["metric"] == "hit_rate@5" and check["passed"] is False for check in out["pass_fail"]["checks"])
