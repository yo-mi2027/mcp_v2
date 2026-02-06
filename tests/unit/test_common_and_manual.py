from __future__ import annotations

import time

import pytest

from mcp_v2_server.adaptive_stats import AdaptiveStatsWriter
from mcp_v2_server.config import Config
from mcp_v2_server.errors import ToolError
from mcp_v2_server.path_guard import normalize_relative_path
from mcp_v2_server.tools_manual import manual_find, manual_hits, manual_read, manual_toc


def test_normalize_relative_path_rejects_absolute() -> None:
    with pytest.raises(ToolError) as e:
        normalize_relative_path("/abs/path.md")
    assert e.value.code == "invalid_path"


def test_normalize_relative_path_rejects_parent() -> None:
    with pytest.raises(ToolError) as e:
        normalize_relative_path("../x.md")
    assert e.value.code == "invalid_path"


def test_manual_toc_has_parent_relations(state) -> None:
    out = manual_toc(state, manual_id="m1")
    rules = [x for x in out["items"] if x["path"] == "rules.md"]
    assert len(rules) >= 3
    by_id = {item["node_id"]: item for item in rules}
    child = next(x for x in rules if x["level"] == 2)
    assert by_id[child["parent_id"]]["level"] == 1


def test_manual_read_json_section_scope_is_invalid(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_read(
            state,
            ref={"target": "manual", "manual_id": "m1", "path": "policy.json"},
            scope="section",
        )
    assert e.value.code == "invalid_scope"


def test_manual_read_md_defaults_to_snippet_scope(state) -> None:
    out = manual_read(
        state,
        ref={"target": "manual", "manual_id": "m1", "path": "rules.md", "start_line": 3},
    )
    assert out["applied"]["scope"] == "snippet"
    assert "## 例外" in out["text"]


def test_manual_read_rejects_invalid_scope(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_read(
            state,
            ref={"target": "manual", "manual_id": "m1", "path": "rules.md", "start_line": 3},
            scope="bad_scope",
        )
    assert e.value.code == "invalid_parameter"


def test_manual_find_rejects_invalid_max_stage(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_find(state, query="対象外", max_stage=2)
    assert e.value.code == "invalid_parameter"


def test_manual_find_rejects_invalid_intent(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_find(state, query="対象外", intent="bad_intent")
    assert e.value.code == "invalid_parameter"


def test_manual_find_stage3_still_returns_loose_signal(state) -> None:
    out = manual_find(state, query="対 象外", manual_id="m1", max_stage=3)
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates")
    assert any("loose" in item["signals"] for item in hits["items"])


def test_manual_find_only_unscanned_restricts_initial_scope(state) -> None:
    initial = manual_find(state, query="対象外", manual_id="m1", budget={"max_candidates": 1})
    followup = manual_find(
        state,
        query="対象外",
        only_unscanned_from_trace_id=initial["trace_id"],
    )
    hits = manual_hits(state, trace_id=followup["trace_id"], kind="candidates")
    assert hits["total"] >= 1
    paths = {item["ref"]["path"] for item in hits["items"]}
    assert "policy.json" in paths
    assert "rules.md" not in paths
    assert "prioritized_unscanned_sections" in followup["summary"]["escalation_reasons"]


def test_manual_find_summary_has_integrated_candidates(state) -> None:
    out = manual_find(state, query="対象外")
    assert "integrated_candidates" in out["summary"]


def test_manual_find_stage_cap_marks_unscanned_sections(state) -> None:
    out = manual_find(state, query="zzz", manual_id="m2", max_stage=3)
    assert out["summary"]["cutoff_reason"] == "stage_cap"
    assert "stage_cap" in out["summary"]["escalation_reasons"]
    unscanned = manual_hits(state, trace_id=out["trace_id"], kind="unscanned")
    assert any(item["reason"] == "stage_cap" for item in unscanned["items"])


def test_manual_hits_not_found_after_ttl(state) -> None:
    out = manual_find(state, query="対象外")
    trace_id = out["trace_id"]
    assert manual_hits(state, trace_id=trace_id)["total"] >= 1
    time.sleep(1.2)
    with pytest.raises(ToolError) as e:
        manual_hits(state, trace_id=trace_id)
    assert e.value.code == "not_found"


def test_config_artifacts_dir_is_fixed(monkeypatch) -> None:
    monkeypatch.setenv("ARTIFACTS_DIR", "custom_artifacts")
    cfg = Config.from_env()
    assert cfg.artifacts_dir == "artifacts"


def test_adaptive_thresholds_rollback_on_recall_drop(tmp_path) -> None:
    writer = AdaptiveStatsWriter(tmp_path / "adaptive_stats.jsonl")
    now_ms = int(time.time() * 1000)

    for i in range(100):
        writer.append(
            {
                "ts": now_ms - 1000 + i,
                "candidates": 3,
                "cutoff_reason": None,
                "candidate_low_threshold": 5,
                "file_bias_threshold": 0.86,
            }
        )
    for i in range(100):
        writer.append(
            {
                "ts": now_ms + i,
                "candidates": 0,
                "cutoff_reason": None,
                "candidate_low_threshold": 5,
                "file_bias_threshold": 0.86,
            }
        )

    candidate_low, file_bias = writer.manual_find_thresholds(
        base_candidate_low=3,
        base_file_bias=0.80,
        adaptive_tuning=True,
    )
    assert candidate_low == 3
    assert file_bias == 0.80


def test_adaptive_thresholds_change_at_most_once_per_24h(tmp_path) -> None:
    writer = AdaptiveStatsWriter(tmp_path / "adaptive_stats.jsonl")
    now_ms = int(time.time() * 1000)

    # First call can adapt when no change has been applied within 24h.
    writer.append(
        {
            "ts": now_ms - (60 * 1000),
            "candidates": 0,
            "cutoff_reason": "time_budget",
            "candidate_low_threshold": 4,
            "file_bias_threshold": 0.8,
        }
    )
    first_candidate_low, first_file_bias = writer.manual_find_thresholds(
        base_candidate_low=3,
        base_file_bias=0.80,
        adaptive_tuning=True,
    )
    assert first_candidate_low == 3
    assert first_file_bias == 0.77

    # Persist adapted values with a recent timestamp.
    writer.append(
        {
            "ts": now_ms,
            "candidates": 0,
            "cutoff_reason": "time_budget",
            "candidate_low_threshold": first_candidate_low,
            "file_bias_threshold": first_file_bias,
        }
    )
    second_candidate_low, second_file_bias = writer.manual_find_thresholds(
        base_candidate_low=3,
        base_file_bias=0.80,
        adaptive_tuning=True,
    )
    assert second_candidate_low == first_candidate_low
    assert second_file_bias == first_file_bias
