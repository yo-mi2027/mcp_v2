from __future__ import annotations

import json
import time
from pathlib import PureWindowsPath

import pytest

import mcp_v2_server.tools_manual as tools_manual_module
from mcp_v2_server.adaptive_stats import AdaptiveStatsWriter
from mcp_v2_server.config import Config
from mcp_v2_server.errors import ToolError
from mcp_v2_server.path_guard import _is_subpath_casefold, normalize_relative_path
from mcp_v2_server.semantic_cache import SemanticCacheStore
from mcp_v2_server.state import create_state
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
    assert m1_node["id"] == "m1"

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
            ref={"manual_id": "m1", "path": "policy.json"},
            scope="section",
        )
    assert e.value.code == "invalid_scope"


def test_manual_read_md_defaults_to_section_scope(state) -> None:
    out = manual_read(
        state,
        ref={"manual_id": "m1", "path": "rules.md", "start_line": 3},
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
        ref={"manual_id": "m1", "path": "multi.md", "start_line": 2},
        scope="section",
    )
    second = manual_read(
        state,
        ref={"manual_id": "m1", "path": "multi.md", "start_line": 2},
        scope="section",
    )
    assert first["applied"]["mode"] == "read"
    assert second["applied"]["mode"] == "scan_fallback"
    assert "## ②" in second["text"]


def test_manual_read_ignores_ref_target_hint(state) -> None:
    out = manual_read(
        state,
        ref={"target": "vault", "manual_id": "m1", "path": "rules.md", "start_line": 3},
    )
    assert out["applied"]["scope"] == "section"


def test_manual_read_rejects_invalid_scope(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_read(
            state,
            ref={"manual_id": "m1", "path": "rules.md", "start_line": 3},
            scope="bad_scope",
        )
    assert e.value.code == "invalid_parameter"


def test_manual_read_file_scope_forbidden_when_global_disabled(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_read(
            state,
            ref={"manual_id": "m1", "path": "rules.md"},
            scope="file",
            allow_file=True,
        )
    assert e.value.code == "forbidden"


def test_manual_read_file_scope_requires_allow_file_when_global_enabled(state, monkeypatch) -> None:
    monkeypatch.setenv("ALLOW_FILE_SCOPE", "true")
    local_state = create_state(Config.from_env())
    with pytest.raises(ToolError) as e:
        manual_read(
            local_state,
            ref={"manual_id": "m1", "path": "rules.md"},
            scope="file",
            allow_file=False,
        )
    assert e.value.code == "forbidden"


def test_manual_read_file_scope_succeeds_when_enabled_and_allow_file_true(state, monkeypatch) -> None:
    monkeypatch.setenv("ALLOW_FILE_SCOPE", "true")
    local_state = create_state(Config.from_env())
    out = manual_read(
        local_state,
        ref={"manual_id": "m1", "path": "rules.md"},
        scope="file",
        allow_file=True,
    )
    assert out["applied"]["scope"] == "file"
    assert "## 例外" in out["text"]


def test_manual_read_rejects_non_boolean_allow_file(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_read(
            state,
            ref={"manual_id": "m1", "path": "rules.md"},
            scope="file",
            allow_file="false",
        )
    assert e.value.code == "invalid_parameter"


def test_manual_scan_uses_fixed_max_chars(state) -> None:
    out = manual_scan(state, manual_id="m1", path="rules.md")
    assert out["applied"]["max_chars"] == 12000


def test_manual_scan_paginates_until_eof(state) -> None:
    text = ("a" * 12050) + "\nend\n"
    (state.config.manuals_root / "m1" / "long.md").write_text(text, encoding="utf-8")
    first = manual_scan(state, manual_id="m1", path="long.md")
    assert first["applied_range"] == {"start_line": 1, "end_line": 1}
    assert first["eof"] is False
    assert first["truncated_reason"] == "max_chars"
    assert first["next_cursor"]["char_offset"] == 12000

    second = manual_scan(
        state,
        manual_id="m1",
        path="long.md",
        cursor=first["next_cursor"],
    )
    assert second["eof"] is True
    assert second["truncated_reason"] == "none"
    assert second["next_cursor"]["char_offset"] is None
    assert len(first["text"]) + len(second["text"]) == len(text)


def test_manual_scan_accepts_string_cursor_char_offset(state) -> None:
    text = ("a" * 12050) + "\nend\n"
    (state.config.manuals_root / "m1" / "long_cursor_string.md").write_text(text, encoding="utf-8")
    first = manual_scan(state, manual_id="m1", path="long_cursor_string.md")
    second = manual_scan(
        state,
        manual_id="m1",
        path="long_cursor_string.md",
        cursor=str(first["next_cursor"]["char_offset"]),
    )
    assert second["eof"] is True
    assert second["next_cursor"]["char_offset"] is None
    assert second["applied_range"]["start_line"] == 1


def test_manual_scan_accepts_integer_cursor_char_offset(state) -> None:
    text = ("a" * 12050) + "\nend\n"
    (state.config.manuals_root / "m1" / "long_cursor_int.md").write_text(text, encoding="utf-8")
    first = manual_scan(state, manual_id="m1", path="long_cursor_int.md")
    second = manual_scan(
        state,
        manual_id="m1",
        path="long_cursor_int.md",
        cursor=first["next_cursor"]["char_offset"],
    )
    assert second["eof"] is True
    assert second["next_cursor"]["char_offset"] is None
    assert second["applied_range"]["start_line"] == 1


def test_manual_scan_accepts_start_line_without_cursor(state) -> None:
    out = manual_scan(state, manual_id="m1", path="rules.md", start_line=3)
    assert out["applied_range"] == {"start_line": 3, "end_line": 6}


def test_manual_scan_start_line_takes_precedence_over_cursor_char_offset(state) -> None:
    out = manual_scan(
        state,
        manual_id="m1",
        path="rules.md",
        start_line=3,
        cursor={"char_offset": 0, "start_line": 1},
    )
    assert out["applied_range"]["start_line"] == 3


def test_manual_find_rejects_non_boolean_expand_scope(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_find(state, query="対象外", expand_scope="yes")
    assert e.value.code == "invalid_parameter"


def test_manual_find_rejects_non_integer_budget_time_ms(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_find(state, query="対象外", budget={"time_ms": "abc"})
    assert e.value.code == "invalid_parameter"


def test_manual_find_rejects_negative_budget_max_candidates(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_find(state, query="対象外", budget={"max_candidates": -1})
    assert e.value.code == "invalid_parameter"


def test_manual_find_rejects_non_boolean_include_claim_graph(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_find(state, query="対象外", include_claim_graph="true")
    assert e.value.code == "invalid_parameter"


def test_manual_find_stage3_still_returns_loose_signal(state) -> None:
    out = manual_find(state, query="対 象外", manual_id="m1", expand_scope=False)
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates")
    assert any("loose" in (item["ref"] or {}).get("signals", []) for item in hits["items"])


def test_manual_find_does_not_candidateize_heading_only_match(state) -> None:
    manual_dir = state.config.manuals_root / "m4"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "heading_only.md").write_text("# 対象外\n", encoding="utf-8")

    out = manual_find(state, query="対象外", manual_id="m4", expand_scope=False)
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates")

    assert out["summary"]["candidates"] == 0
    assert hits["total"] == 0


def test_manual_find_adds_heading_focus_signal_on_top_groups(state) -> None:
    manual_dir = state.config.manuals_root / "m5"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "focus.md").write_text(
        "# 高スコア\n対象外です。\n\n# 低スコア\n対-象-外です。\n",
        encoding="utf-8",
    )

    out = manual_find(state, query="対象外", manual_id="m5", expand_scope=False)
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates")

    assert hits["total"] == 2
    assert "heading_focus" in ((hits["items"][0].get("ref") or {}).get("signals") or [])


def test_manual_find_does_not_match_reference_words_only(state) -> None:
    manual_dir = state.config.manuals_root / "m3"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "ref_only.md").write_text("# 補足\n別表を参照。\n", encoding="utf-8")

    out = manual_find(state, query="無関係語", manual_id="m3", expand_scope=False)
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates")

    assert out["summary"]["candidates"] == 0
    assert hits["total"] == 0


def test_manual_find_stage3_does_not_expand_exceptions_only_candidates(state) -> None:
    manual_dir = state.config.manuals_root / "m3"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "exceptions_only.md").write_text("# 補足\nこの場合は対象外です。\n", encoding="utf-8")

    out = manual_find(state, query="無関係語", manual_id="m3", expand_scope=False)
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates")
    unscanned = manual_hits(state, trace_id=out["trace_id"], kind="unscanned")

    assert out["summary"]["candidates"] == 0
    assert hits["total"] == 0
    assert any(item["reason"] == "stage_cap" for item in unscanned["items"])


def test_manual_find_stage4_expands_exceptions_for_exceptional_query(state) -> None:
    manual_dir = state.config.manuals_root / "m3"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "exceptions_only.md").write_text("# 補足\nこの場合は対象外です。\n", encoding="utf-8")

    out = manual_find(state, query="対象外", manual_id="m3", expand_scope=True)
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


def test_manual_find_uses_next_actions_planner_output_when_valid(state) -> None:
    captured: dict[str, object] = {}

    def planner(payload: dict[str, object]) -> list[dict[str, object]]:
        captured.update(payload)
        return [{"type": "manual_hits", "confidence": 0.9, "params": {"kind": "gaps", "offset": 0, "limit": 5}}]

    state.next_actions_planner = planner
    out = manual_find(state, query="対象外")
    assert captured["query"] == "対象外"
    assert isinstance(captured["summary"], dict)
    assert out["next_actions"] == [{"type": "manual_hits", "confidence": 0.9, "params": {"kind": "gaps", "offset": 0, "limit": 5}}]


def test_manual_find_falls_back_when_next_actions_planner_returns_invalid_schema(state) -> None:
    state.next_actions_planner = lambda payload: [{"type": "stop", "confidence": 0.8, "params": None}]
    out = manual_find(state, query="対象外")
    assert out["next_actions"][0]["type"] in {"manual_hits", "manual_read"}


def test_manual_find_falls_back_when_next_actions_planner_raises(state) -> None:
    def planner(_: dict[str, object]) -> list[dict[str, object]]:
        raise RuntimeError("planner failed")

    state.next_actions_planner = planner
    out = manual_find(state, query="対象外")
    assert out["next_actions"][0]["type"] in {"manual_hits", "manual_read"}


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
    out = manual_find(state, query="対象外", include_claim_graph=True)
    assert "claim_graph" in out
    assert "claims" in out["claim_graph"]
    assert "evidences" in out["claim_graph"]
    assert "edges" in out["claim_graph"]
    assert "facets" in out["claim_graph"]


def test_manual_find_omits_claim_graph_by_default(state) -> None:
    out = manual_find(state, query="対象外")
    assert "claim_graph" not in out


def test_manual_hits_supports_claim_graph_kinds(state) -> None:
    out = manual_find(state, query="対象外")
    trace_id = out["trace_id"]
    claims = manual_hits(state, trace_id=trace_id, kind="claims")
    evidences = manual_hits(state, trace_id=trace_id, kind="evidences")
    edges = manual_hits(state, trace_id=trace_id, kind="edges")
    assert claims["total"] >= 1
    assert evidences["total"] >= 1
    assert edges["total"] >= 1


def test_manual_find_conflict_count_matches_conflict_hits(state) -> None:
    out = manual_find(state, query="対象外", manual_id="m1", expand_scope=True)
    conflicts = manual_hits(state, trace_id=out["trace_id"], kind="conflicts")
    assert out["summary"]["conflict_count"] == conflicts["total"]


def test_manual_find_gap_count_matches_gap_hits(state) -> None:
    out = manual_find(state, query="対象外の条件と手順を教えて", manual_id="m1", expand_scope=True)
    gaps = manual_hits(state, trace_id=out["trace_id"], kind="gaps")
    assert out["summary"]["gap_count"] == gaps["total"]


def test_manual_find_builds_multiple_claims_from_multi_facet_query(state) -> None:
    out = manual_find(state, query="対象外の条件と手順を教えて", manual_id="m1", expand_scope=True)
    claims = manual_hits(state, trace_id=out["trace_id"], kind="claims")
    facets = {item["facet"] for item in claims["items"]}
    assert claims["total"] >= 2
    assert "exceptions" in facets
    assert "procedure" in facets


def test_manual_find_stage_cap_marks_unscanned_sections(state) -> None:
    out = manual_find(state, query="zzz", manual_id="m2", expand_scope=False)
    unscanned = manual_hits(state, trace_id=out["trace_id"], kind="unscanned")
    assert any(item["reason"] == "stage_cap" for item in unscanned["items"])


def test_manual_find_uses_exact_cache_hit_when_enabled(state, monkeypatch) -> None:
    monkeypatch.setenv("SEM_CACHE_ENABLED", "true")
    local_state = create_state(Config.from_env())

    original = tools_manual_module._run_find_pass
    calls = {"count": 0}

    def counting_run_find_pass(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(tools_manual_module, "_run_find_pass", counting_run_find_pass)
    manual_find(local_state, query="対象外", manual_id="m1")
    assert calls["count"] >= 1

    calls["count"] = 0
    out = manual_find(local_state, query="対象外", manual_id="m1")
    assert calls["count"] == 0
    assert manual_hits(local_state, trace_id=out["trace_id"], kind="candidates")["total"] >= 1


def test_manual_find_can_bypass_cache_per_request(state, monkeypatch) -> None:
    monkeypatch.setenv("SEM_CACHE_ENABLED", "true")
    local_state = create_state(Config.from_env())

    manual_find(local_state, query="対象外", manual_id="m1")
    original = tools_manual_module._run_find_pass
    calls = {"count": 0}

    def counting_run_find_pass(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(tools_manual_module, "_run_find_pass", counting_run_find_pass)
    out = manual_find(local_state, query="対象外", manual_id="m1", use_cache=False)
    assert calls["count"] >= 1
    assert manual_hits(local_state, trace_id=out["trace_id"], kind="candidates")["total"] >= 1


def test_manual_find_uses_semantic_cache_hit_when_enabled(state, monkeypatch) -> None:
    monkeypatch.setenv("SEM_CACHE_ENABLED", "true")
    local_state = create_state(Config.from_env())

    class DummyEmbeddingProvider:
        def embed(self, text: str) -> list[float] | None:
            if "対象外" in text or "除外" in text:
                return [1.0, 0.0]
            return [0.0, 1.0]

    local_state.semantic_cache = SemanticCacheStore(
        max_keep=local_state.config.sem_cache_max_keep,
        ttl_sec=local_state.config.sem_cache_ttl_sec,
        embedding_provider=DummyEmbeddingProvider(),
    )

    original = tools_manual_module._run_find_pass
    calls = {"count": 0}

    def counting_run_find_pass(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(tools_manual_module, "_run_find_pass", counting_run_find_pass)
    manual_find(local_state, query="対象外", manual_id="m1")
    assert calls["count"] >= 1

    calls["count"] = 0
    out = manual_find(local_state, query="除外", manual_id="m1")
    assert calls["count"] == 0
    assert manual_hits(local_state, trace_id=out["trace_id"], kind="candidates")["total"] >= 1


def test_manual_find_revalidates_cached_summary_with_gap(state, monkeypatch) -> None:
    monkeypatch.setenv("SEM_CACHE_ENABLED", "true")
    monkeypatch.setenv("SEM_CACHE_MAX_SUMMARY_GAP", "0")
    local_state = create_state(Config.from_env())
    _ = manual_find(local_state, query="対象外", manual_id="m1")
    # Simulate stale/low-quality cached summary and ensure a fresh search runs.
    cache_store = local_state.semantic_cache
    first_key = next(iter(cache_store._items.keys()))
    cached_payload = cache_store._items[first_key].payload
    trace_payload = cached_payload.get("trace_payload") or {}
    summary = trace_payload.get("summary") or {}
    summary["gap_count"] = 1

    original = tools_manual_module._run_find_pass
    calls = {"count": 0}

    def counting_run_find_pass(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(tools_manual_module, "_run_find_pass", counting_run_find_pass)
    manual_find(local_state, query="対象外", manual_id="m1")
    assert calls["count"] >= 1


def test_manual_find_cache_invalidates_when_manual_changes(state, monkeypatch) -> None:
    monkeypatch.setenv("SEM_CACHE_ENABLED", "true")
    local_state = create_state(Config.from_env())
    manual_file = local_state.config.manuals_root / "m1" / "rules.md"

    manual_find(local_state, query="対象外", manual_id="m1")
    manual_file.write_text(
        "# 総則\n対象です。\n## 例外\nこの場合は対象外です。\n### 参照\n別表を参照。\n更新あり\n",
        encoding="utf-8",
    )

    original = tools_manual_module._run_find_pass
    calls = {"count": 0}

    def counting_run_find_pass(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(tools_manual_module, "_run_find_pass", counting_run_find_pass)
    manual_find(local_state, query="対象外", manual_id="m1")
    assert calls["count"] >= 1


def test_manual_find_adaptive_stats_records_sem_cache_fields_on_miss(state, monkeypatch) -> None:
    monkeypatch.setenv("SEM_CACHE_ENABLED", "true")
    local_state = create_state(Config.from_env())

    manual_find(local_state, query="対象外", manual_id="m1")
    lines = local_state.config.adaptive_stats_path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[-1])

    assert row["sem_cache_hit"] is False
    assert row["sem_cache_mode"] == "miss"
    assert row["sem_cache_score"] is None
    assert row["latency_saved_ms"] is None
    assert row["scoring_mode"] == "bm25"
    assert isinstance(row["index_rebuilt"], bool)
    assert isinstance(row["index_docs"], int)


def test_manual_find_adaptive_stats_records_sem_cache_hit(state, monkeypatch) -> None:
    monkeypatch.setenv("SEM_CACHE_ENABLED", "true")
    local_state = create_state(Config.from_env())

    manual_find(local_state, query="対象外", manual_id="m1")
    manual_find(local_state, query="対象外", manual_id="m1")
    lines = local_state.config.adaptive_stats_path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[-1])

    assert row["sem_cache_hit"] is True
    assert row["sem_cache_mode"] == "exact"
    assert row["cutoff_reason"] is None
    assert isinstance(row["latency_saved_ms"], int)
    assert row["latency_saved_ms"] >= 0
    assert row["scoring_mode"] == "cache"


def test_manual_find_uses_default_manual_id_when_omitted(state, monkeypatch) -> None:
    monkeypatch.setenv("DEFAULT_MANUAL_ID", "missing_manual")
    local_state = create_state(Config.from_env())
    with pytest.raises(ToolError) as e:
        manual_find(local_state, query="対象外")
    assert e.value.code == "not_found"


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


def test_adaptive_thresholds_respect_min_recall_floor(tmp_path) -> None:
    writer = AdaptiveStatsWriter(tmp_path / "adaptive_stats.jsonl")
    now_ms = int(time.time() * 1000)

    for i in range(120):
        writer.append(
            {
                "ts": now_ms - 1000 + i,
                "candidates": 4,
                "cutoff_reason": None,
                "candidate_low_threshold": 4,
                "file_bias_threshold": 0.82,
            }
        )
    for i in range(100):
        writer.append(
            {
                "ts": now_ms + i,
                "candidates": 0,
                "cutoff_reason": None,
                "candidate_low_threshold": 4,
                "file_bias_threshold": 0.82,
            }
        )

    candidate_low, file_bias = writer.manual_find_thresholds(
        base_candidate_low=3,
        base_file_bias=0.80,
        adaptive_tuning=True,
        min_recall=0.90,
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


def test_adaptive_thresholds_tolerate_malformed_numeric_rows(tmp_path) -> None:
    writer = AdaptiveStatsWriter(tmp_path / "adaptive_stats.jsonl")
    writer.append(
        {
            "ts": "invalid_ts",
            "candidates": "invalid_candidates",
            "cutoff_reason": None,
            "candidate_low_threshold": "invalid_candidate_low",
            "file_bias_threshold": "invalid_file_bias",
            "added_evidence_count": "invalid_added",
        }
    )

    candidate_low, file_bias = writer.manual_find_thresholds(
        base_candidate_low=3,
        base_file_bias=0.80,
        adaptive_tuning=True,
    )

    assert 2 <= candidate_low <= 6
    assert 0.70 <= file_bias <= 0.90


def test_adaptive_thresholds_ignore_non_object_json_rows(tmp_path) -> None:
    path = tmp_path / "adaptive_stats.jsonl"
    path.write_text("[]\n\"text\"\n1\n", encoding="utf-8")
    writer = AdaptiveStatsWriter(path)

    candidate_low, file_bias = writer.manual_find_thresholds(
        base_candidate_low=3,
        base_file_bias=0.80,
        adaptive_tuning=True,
    )

    assert candidate_low == 3
    assert file_bias == 0.80
