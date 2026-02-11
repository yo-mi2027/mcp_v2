from __future__ import annotations

import time
from pathlib import PureWindowsPath

import pytest

from mcp_v2_server.adaptive_stats import AdaptiveStatsWriter
from mcp_v2_server.config import Config
from mcp_v2_server.errors import ToolError
from mcp_v2_server.path_guard import _is_subpath_casefold, normalize_relative_path
from mcp_v2_server.tools_manual import manual_find, manual_hits, manual_ls, manual_read, manual_scan, manual_toc


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


def test_manual_toc_groups_headings_by_path(state) -> None:
    out = manual_toc(state, manual_id="m1")
    rules = next(x for x in out["items"] if x["path"] == "rules.md")
    assert len(rules["headings"]) >= 3
    assert all("title" in h and "line_start" in h for h in rules["headings"])


def test_manual_ls_navigates_one_level_by_id(state) -> None:
    root = manual_ls(state, id="manuals")
    assert root["id"] == "manuals"
    assert {item["name"] for item in root["items"]} == {"m1", "m2"}
    m1_node = next(item for item in root["items"] if item["name"] == "m1")
    assert m1_node["kind"] == "dir"

    manual_dir = state.config.manuals_root / "m1"
    (manual_dir / "sub").mkdir(parents=True, exist_ok=True)
    (manual_dir / "sub" / "more.md").write_text("# more\n", encoding="utf-8")
    second = manual_ls(state, id=m1_node["id"])
    assert second["id"] == m1_node["id"]
    names = {item["name"] for item in second["items"]}
    assert "rules.md" in names
    assert "policy.json" in names
    assert "sub" in names

    sub_node = next(item for item in second["items"] if item["name"] == "sub")
    assert sub_node["kind"] == "dir"
    third = manual_ls(state, id=sub_node["id"])
    assert {item["name"] for item in third["items"]} == {"more.md"}


def test_manual_ls_rejects_file_id_expansion(state) -> None:
    root = manual_ls(state, id="manuals")
    m1_node = next(item for item in root["items"] if item["name"] == "m1")
    second = manual_ls(state, id=m1_node["id"])
    file_node = next(item for item in second["items"] if item["name"] == "rules.md")
    with pytest.raises(ToolError) as e:
        manual_ls(state, id=file_node["id"])
    assert e.value.code == "invalid_parameter"


def test_manual_read_json_section_scope_is_invalid(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_read(
            state,
            ref={"target": "manual", "manual_id": "m1", "path": "policy.json"},
            scope="section",
        )
    assert e.value.code == "invalid_scope"


def test_manual_read_md_defaults_to_section_scope(state) -> None:
    out = manual_read(
        state,
        ref={"target": "manual", "manual_id": "m1", "path": "rules.md", "start_line": 3},
    )
    assert out["applied"]["scope"] == "section"
    assert "## 例外" in out["text"]


def test_manual_read_infers_manual_target_when_missing(state) -> None:
    out = manual_read(
        state,
        ref={"manual_id": "m1", "path": "rules.md", "start_line": 3},
    )
    assert out["applied"]["scope"] == "section"
    assert "## 例外" in out["text"]


def test_manual_read_section_repeated_request_uses_scan_fallback(state) -> None:
    manual_dir = state.config.manuals_root / "m1"
    (manual_dir / "multi.md").write_text(
        "# 章\n## ①\nA\n## ②\nB\n## ③\nC\n",
        encoding="utf-8",
    )
    first = manual_read(
        state,
        ref={"target": "manual", "manual_id": "m1", "path": "multi.md", "start_line": 2},
        scope="section",
    )
    second = manual_read(
        state,
        ref={"target": "manual", "manual_id": "m1", "path": "multi.md", "start_line": 2},
        scope="section",
    )
    assert first["applied"]["mode"] == "read"
    assert second["applied"]["mode"] == "scan_fallback"
    assert "## ②" in second["text"]


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


def test_manual_scan_accepts_start_line_without_cursor(state) -> None:
    out = manual_scan(state, manual_id="m1", path="rules.md", start_line=3, chunk_lines=2)
    assert out["applied_range"] == {"start_line": 3, "end_line": 4}


def test_manual_scan_truncates_by_max_chars(state) -> None:
    out = manual_scan(state, manual_id="m1", path="rules.md", chunk_lines=20, limits={"max_chars": 5})
    assert out["truncated"] is True
    assert out["truncated_reason"] == "max_chars"
    assert len(out["text"]) == 5


def test_manual_scan_rejects_non_integer_chunk_lines(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_scan(state, manual_id="m1", path="rules.md", chunk_lines="abc")
    assert e.value.code == "invalid_parameter"


def test_manual_read_rejects_negative_max_chars(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_read(
            state,
            ref={"target": "manual", "manual_id": "m1", "path": "rules.md", "start_line": 3},
            limits={"max_chars": -1},
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


def test_manual_find_rejects_non_integer_budget_time_ms(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_find(state, query="対象外", budget={"time_ms": "abc"})
    assert e.value.code == "invalid_parameter"


def test_manual_find_rejects_negative_budget_max_candidates(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_find(state, query="対象外", budget={"max_candidates": -1})
    assert e.value.code == "invalid_parameter"


def test_manual_find_stage3_still_returns_loose_signal(state) -> None:
    out = manual_find(state, query="対 象外", manual_id="m1", max_stage=3)
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates")
    assert any("loose" in (item["ref"] or {}).get("signals", []) for item in hits["items"])


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
    unscanned = manual_hits(state, trace_id=out["trace_id"], kind="unscanned")

    assert out["summary"]["candidates"] == 0
    assert hits["total"] == 0
    assert any(item["reason"] == "stage_cap" for item in unscanned["items"])


def test_manual_find_stage4_expands_exceptions_for_exceptions_intent(state) -> None:
    manual_dir = state.config.manuals_root / "m3"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "exceptions_only.md").write_text("# 補足\nこの場合は対象外です。\n", encoding="utf-8")

    out = manual_find(state, query="無関係語", manual_id="m3", intent="exceptions", max_stage=4)
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates")

    assert out["summary"]["candidates"] >= 1
    assert any("exceptions" in (item["ref"] or {}).get("signals", []) for item in hits["items"])


def test_manual_find_only_unscanned_prioritizes_without_strict_filtering(state) -> None:
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
        f'{(item["ref"] or {}).get("manual_id") or hits.get("manual_id")}:{item["ref"]["path"]}'
        for item in hits["items"]
    }
    assert any(key in unscanned_keys for key in followup_keys)
    assert any(key not in unscanned_keys for key in followup_keys)


def test_manual_find_unscanned_collects_remaining_scope_after_cutoff(state) -> None:
    out = manual_find(state, query="対象外", budget={"max_candidates": 1})
    unscanned = manual_hits(state, trace_id=out["trace_id"], kind="unscanned")
    keys = {f'{item["manual_id"]}:{item["path"]}' for item in unscanned["items"]}
    assert "m1:rules.md" in keys
    assert "m2:appendix.md" in keys


def test_manual_find_summary_uses_minimal_fields(state) -> None:
    out = manual_find(state, query="対象外")
    assert set(out["summary"].keys()) == {
        "scanned_files",
        "scanned_nodes",
        "candidates",
        "file_bias_ratio",
        "conflict_count",
        "gap_count",
        "integration_status",
    }


def test_manual_hits_candidates_compacts_redundant_fields(state) -> None:
    out = manual_find(state, query="対象外", manual_id="m1")
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates", limit=5)
    assert hits.get("manual_id") == "m1"
    assert hits["total"] >= 1
    first = hits["items"][0]
    assert "manual_id" not in first["ref"]
    assert "target" not in first["ref"]
    assert "json_path" not in first["ref"]
    assert "reason" not in first
    assert "conflict_with" not in first
    assert "gap_hint" not in first


def test_manual_find_returns_claim_graph_when_requested(state) -> None:
    out = manual_find(state, query="対象外", intent="exceptions", include_claim_graph=True)
    assert "claim_graph" in out
    assert "claims" in out["claim_graph"]
    assert "evidences" in out["claim_graph"]
    assert "edges" in out["claim_graph"]
    assert "facets" in out["claim_graph"]


def test_manual_find_omits_claim_graph_by_default(state) -> None:
    out = manual_find(state, query="対象外", intent="exceptions")
    assert "claim_graph" not in out


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


def test_manual_find_stage_cap_marks_unscanned_sections(state) -> None:
    out = manual_find(state, query="zzz", manual_id="m2", max_stage=3)
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


def test_manual_hits_rejects_non_integer_offset(state) -> None:
    out = manual_find(state, query="対象外")
    with pytest.raises(ToolError) as e:
        manual_hits(state, trace_id=out["trace_id"], offset="abc")
    assert e.value.code == "invalid_parameter"


def test_manual_hits_rejects_negative_limit(state) -> None:
    out = manual_find(state, query="対象外")
    with pytest.raises(ToolError) as e:
        manual_hits(state, trace_id=out["trace_id"], limit=-1)
    assert e.value.code == "invalid_parameter"


def test_config_default_adaptive_stats_path_under_system(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("VAULT_ROOT", raising=False)
    monkeypatch.delenv("ADAPTIVE_STATS_PATH", raising=False)
    cfg = Config.from_env()
    assert cfg.adaptive_stats_path == (cfg.vault_root / ".system" / "adaptive_stats.jsonl")


def test_config_rejects_invalid_default_max_stage(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("DEFAULT_MAX_STAGE", "2")
    with pytest.raises(ValueError, match="DEFAULT_MAX_STAGE must be 3 or 4"):
        Config.from_env()


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
