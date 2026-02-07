from __future__ import annotations

import time
from pathlib import PureWindowsPath

import pytest

from mcp_v2_server.adaptive_stats import AdaptiveStatsWriter
from mcp_v2_server.config import Config
from mcp_v2_server.errors import ToolError
from mcp_v2_server.path_guard import _is_subpath_casefold, normalize_relative_path
from mcp_v2_server.tools_manual import manual_find, manual_hits, manual_read, manual_scan, manual_toc


def test_normalize_relative_path_rejects_absolute() -> None:
    with pytest.raises(ToolError) as e:
        normalize_relative_path("/abs/path.md")
    assert e.value.code == "invalid_path"


def test_normalize_relative_path_rejects_parent() -> None:
    with pytest.raises(ToolError) as e:
        normalize_relative_path("../x.md")
    assert e.value.code == "invalid_path"


def test_normalize_relative_path_rejects_windows_absolute_drive() -> None:
    with pytest.raises(ToolError) as e:
        normalize_relative_path(r"C:\abs\path.md")
    assert e.value.code == "invalid_path"


def test_normalize_relative_path_normalizes_windows_separators() -> None:
    assert normalize_relative_path(r"daily\2026-02-07.md") == "daily/2026-02-07.md"


def test_is_subpath_casefold_supports_windows_paths() -> None:
    root = PureWindowsPath(r"C:\ws\vault\daily")
    child = PureWindowsPath(r"C:\ws\vault\daily\2026-02-07.md")
    outside = PureWindowsPath(r"C:\ws\vault\daily2\2026-02-07.md")
    assert _is_subpath_casefold(child, root) is True
    assert _is_subpath_casefold(outside, root) is False


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


def test_manual_read_infers_manual_target_when_missing(state) -> None:
    out = manual_read(
        state,
        ref={"manual_id": "m1", "path": "rules.md", "start_line": 3},
    )
    assert out["applied"]["scope"] == "snippet"
    assert "## 例外" in out["text"]


def test_manual_read_rejects_non_manual_target(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_read(
            state,
            ref={"target": "vault", "manual_id": "m1", "path": "rules.md", "start_line": 3},
        )
    assert e.value.code == "invalid_parameter"


def test_manual_read_rejects_invalid_scope(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_read(
            state,
            ref={"target": "manual", "manual_id": "m1", "path": "rules.md", "start_line": 3},
            scope="bad_scope",
        )
    assert e.value.code == "invalid_parameter"


def test_manual_scan_chunk_lines_range(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_scan(state, manual_id="m1", path="rules.md", chunk_lines=999)
    assert e.value.code == "invalid_parameter"


def test_manual_scan_paginates_until_eof(state) -> None:
    first = manual_scan(state, manual_id="m1", path="rules.md", chunk_lines=2)
    assert first["applied_range"] == {"start_line": 1, "end_line": 2}
    assert first["eof"] is False
    assert first["truncated_reason"] == "chunk_end"
    assert first["next_cursor"]["start_line"] == 3

    second = manual_scan(
        state,
        manual_id="m1",
        path="rules.md",
        cursor=first["next_cursor"],
        chunk_lines=20,
    )
    assert second["eof"] is True
    assert second["truncated_reason"] == "none"
    assert second["next_cursor"]["start_line"] is None


def test_manual_scan_truncates_by_max_chars(state) -> None:
    out = manual_scan(state, manual_id="m1", path="rules.md", chunk_lines=20, limits={"max_chars": 5})
    assert out["truncated"] is True
    assert out["truncated_reason"] == "max_chars"
    assert len(out["text"]) == 5


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


def test_manual_find_does_not_match_reference_words_only(state) -> None:
    manual_dir = state.config.manuals_root / "m3"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "ref_only.md").write_text("# 補足\n別表を参照。\n", encoding="utf-8")

    out = manual_find(state, query="無関係語", manual_id="m3", max_stage=3)
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates")

    assert out["summary"]["candidates"] == 0
    assert hits["total"] == 0


def test_manual_find_stage3_does_not_expand_exceptions_only_candidates(state) -> None:
    manual_dir = state.config.manuals_root / "m3"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "exceptions_only.md").write_text("# 補足\nこの場合は対象外です。\n", encoding="utf-8")

    out = manual_find(state, query="無関係語", manual_id="m3", intent="exceptions", max_stage=3)
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates")

    assert out["summary"]["candidates"] == 0
    assert hits["total"] == 0
    assert out["summary"]["cutoff_reason"] == "stage_cap"


def test_manual_find_stage4_expands_exceptions_for_exceptions_intent(state) -> None:
    manual_dir = state.config.manuals_root / "m3"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "exceptions_only.md").write_text("# 補足\nこの場合は対象外です。\n", encoding="utf-8")

    out = manual_find(state, query="無関係語", manual_id="m3", intent="exceptions", max_stage=4)
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates")

    assert out["summary"]["candidates"] >= 1
    assert any("exceptions" in item["signals"] for item in hits["items"])
    assert "exceptions_manual_expanded" in out["summary"]["escalation_reasons"]


def test_manual_find_only_unscanned_restricts_initial_scope(state) -> None:
    initial = manual_find(state, query="対象外", manual_id="m1", budget={"max_candidates": 1})
    initial_unscanned = manual_hits(state, trace_id=initial["trace_id"], kind="unscanned")
    unscanned_keys = {
        f'{item["manual_id"]}:{item["path"]}'
        for item in initial_unscanned["items"]
    }
    followup = manual_find(
        state,
        query="対象外",
        only_unscanned_from_trace_id=initial["trace_id"],
    )
    hits = manual_hits(state, trace_id=followup["trace_id"], kind="candidates")
    assert hits["total"] >= 1
    followup_keys = {
        f'{item["ref"]["manual_id"]}:{item["ref"]["path"]}'
        for item in hits["items"]
    }
    assert followup_keys.issubset(unscanned_keys)
    assert "prioritized_unscanned_sections" in followup["summary"]["escalation_reasons"]


def test_manual_find_summary_has_integrated_candidates(state) -> None:
    out = manual_find(state, query="対象外")
    assert "integrated_candidates" in out["summary"]


def test_manual_find_returns_claim_graph(state) -> None:
    out = manual_find(state, query="対象外", intent="exceptions")
    assert "claim_graph" in out
    assert "claims" in out["claim_graph"]
    assert "evidences" in out["claim_graph"]
    assert "edges" in out["claim_graph"]
    assert "facets" in out["claim_graph"]
    assert "claim_count" in out["summary"]


def test_manual_hits_supports_claim_graph_kinds(state) -> None:
    out = manual_find(state, query="対象外", intent="exceptions")
    trace_id = out["trace_id"]
    claims = manual_hits(state, trace_id=trace_id, kind="claims")
    evidences = manual_hits(state, trace_id=trace_id, kind="evidences")
    edges = manual_hits(state, trace_id=trace_id, kind="edges")
    assert claims["total"] >= 1
    assert evidences["total"] >= 1
    assert edges["total"] >= 1


def test_manual_find_conflict_count_matches_conflict_hits(state) -> None:
    out = manual_find(state, query="対象外", intent="exceptions", manual_id="m1", max_stage=4)
    conflicts = manual_hits(state, trace_id=out["trace_id"], kind="conflicts")
    assert out["summary"]["conflict_count"] == conflicts["total"]


def test_manual_find_gap_count_matches_gap_hits(state) -> None:
    out = manual_find(state, query="対象外の条件と手順を教えて", manual_id="m1", max_stage=4)
    gaps = manual_hits(state, trace_id=out["trace_id"], kind="gaps")
    assert out["summary"]["gap_count"] == gaps["total"]


def test_manual_find_builds_multiple_claims_from_multi_facet_query(state) -> None:
    out = manual_find(state, query="対象外の条件と手順を教えて", manual_id="m1", max_stage=4)
    claims = manual_hits(state, trace_id=out["trace_id"], kind="claims")
    facets = {item["facet"] for item in claims["items"]}
    assert claims["total"] >= 2
    assert "exceptions" in facets
    assert "procedure" in facets
    assert out["summary"]["claim_count"] == claims["total"]


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


def test_config_default_adaptive_stats_path_under_system(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("VAULT_ROOT", raising=False)
    monkeypatch.delenv("ADAPTIVE_STATS_PATH", raising=False)
    cfg = Config.from_env()
    assert cfg.adaptive_stats_path == (cfg.vault_root / ".system" / "adaptive_stats.jsonl")


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
