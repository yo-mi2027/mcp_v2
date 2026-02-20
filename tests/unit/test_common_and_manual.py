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
    out = manual_toc(
        state,
        manual_id="m1",
        path_prefix="rules.md",
        depth="deep",
    )
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


def test_manual_ls_rejects_repeated_root_call_without_selection(state) -> None:
    _ = manual_ls(state, id="manuals")
    with pytest.raises(ToolError) as e:
        manual_ls(state, id="manuals")
    assert e.value.code == "invalid_parameter"
    assert "items[].id" in e.value.message


def test_manual_ls_rejects_non_string_id(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_ls(state, id=123)  # type: ignore[arg-type]
    assert e.value.code == "invalid_parameter"


def test_manual_ls_allows_root_call_again_after_selection(state) -> None:
    root = manual_ls(state, id="manuals")
    selected = root["items"][0]["id"]
    _ = manual_ls(state, id=selected)
    again = manual_ls(state, id="manuals")
    assert again["id"] == "manuals"


def test_manual_toc_defaults_to_shallow(state) -> None:
    out = manual_toc(state, manual_id="m1")
    first = out["items"][0]
    assert first["headings"] == []
    assert out["applied"]["depth"] == "shallow"


def test_manual_toc_accepts_manual_id_with_outer_spaces(state) -> None:
    out = manual_toc(state, manual_id=" m1 ", max_files=1)
    assert out["applied"]["manual_id"] == "m1"
    assert len(out["items"]) == 1


def test_manual_toc_deep_requires_path_prefix(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_toc(state, manual_id="m1", depth="deep")
    assert e.value.code == "invalid_parameter"


def test_manual_toc_rejects_root_manuals_id_with_guidance(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_toc(state, manual_id="manuals")
    assert e.value.code == "invalid_parameter"
    assert "items[].id" in e.value.message


def test_manual_toc_path_prefix_filters_scope(state) -> None:
    manual_dir = state.config.manuals_root / "m1"
    (manual_dir / "sub").mkdir(parents=True, exist_ok=True)
    (manual_dir / "sub" / "inside.md").write_text("# in\n", encoding="utf-8")
    out = manual_toc(state, manual_id="m1", path_prefix="sub")
    assert {item["path"] for item in out["items"]} == {"sub/inside.md"}


def test_manual_toc_supports_pagination(state) -> None:
    manual_dir = state.config.manuals_root / "m1" / "docs"
    manual_dir.mkdir(parents=True, exist_ok=True)
    for idx in range(3):
        (manual_dir / f"f{idx}.md").write_text(f"# H{idx}\n", encoding="utf-8")
    first = manual_toc(
        state,
        manual_id="m1",
        path_prefix="docs",
        depth="deep",
        max_files=2,
    )
    assert len(first["items"]) == 2
    assert first["next_cursor"] == {"offset": 2}
    second = manual_toc(
        state,
        manual_id="m1",
        path_prefix="docs",
        depth="deep",
        max_files=2,
        cursor=first["next_cursor"],
    )
    assert len(second["items"]) == 1
    assert second["next_cursor"] is None


def test_manual_toc_rejects_large_max_files_without_path_prefix(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_toc(
            state,
            manual_id="m1",
            max_files=51,
        )
    assert e.value.code == "invalid_parameter"


def test_manual_toc_rejects_large_max_files_with_deep(state) -> None:
    manual_dir = state.config.manuals_root / "m1" / "docs"
    manual_dir.mkdir(parents=True, exist_ok=True)
    for idx in range(51):
        (manual_dir / f"f{idx}.md").write_text(f"# H{idx}\n", encoding="utf-8")
    with pytest.raises(ToolError) as e:
        manual_toc(
            state,
            manual_id="m1",
            path_prefix="docs",
            depth="deep",
            max_files=51,
        )
    assert e.value.code == "invalid_parameter"


def test_manual_toc_returns_needs_narrow_scope_on_hard_limit(state) -> None:
    manual_dir = state.config.manuals_root / "m1" / "wide"
    manual_dir.mkdir(parents=True, exist_ok=True)
    for idx in range(201):
        (manual_dir / f"f{idx}.md").write_text("# H\n", encoding="utf-8")
    with pytest.raises(ToolError) as e:
        manual_toc(
            state,
            manual_id="m1",
            path_prefix="wide",
            max_files=50,
        )
    assert e.value.code == "needs_narrow_scope"


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


def test_manual_read_rejects_non_object_ref(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_read(state, ref=123)  # type: ignore[arg-type]
    assert e.value.code == "invalid_parameter"


def test_manual_read_rejects_non_string_ref_path(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_read(state, ref={"manual_id": "m1", "path": 123})  # type: ignore[dict-item]
    assert e.value.code == "invalid_path"


def test_manual_read_rejects_root_manuals_id_with_guidance(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_read(
            state,
            ref={"manual_id": "manuals", "path": "rules.md"},
            scope="section",
        )
    assert e.value.code == "invalid_parameter"
    assert "items[].id" in e.value.message


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


def test_manual_scan_rejects_root_manuals_id_with_guidance(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_scan(state, manual_id="manuals", path="rules.md")
    assert e.value.code == "invalid_parameter"
    assert "manual_ls(id='manuals')" in e.value.message


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
        manual_find(state, query="対象外", manual_id="m1", expand_scope="yes")
    assert e.value.code == "invalid_parameter"


def test_manual_find_rejects_non_string_manual_id(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_find(state, query="対象外", manual_id=123)  # type: ignore[arg-type]
    assert e.value.code == "invalid_parameter"


def test_manual_find_rejects_root_manuals_id_with_guidance(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_find(state, query="対象外", manual_id="manuals")
    assert e.value.code == "invalid_parameter"
    assert "items[].id" in e.value.message


def test_manual_find_rejects_non_array_required_terms(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_find(state, query="対象外", manual_id="m1", required_terms="対象外")
    assert e.value.code == "invalid_parameter"


def test_manual_find_rejects_more_than_two_required_terms(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_find(state, query="対象外", manual_id="m1", required_terms=["対象外", "手順", "条件"])
    assert e.value.code == "invalid_parameter"


def test_manual_find_applies_required_term_filter(state) -> None:
    out = manual_find(state, query="対象外", manual_id="m1", required_terms=["対象外"])
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates")

    assert out["applied"]["required_terms"] == ["対象外"]
    assert hits["total"] >= 1
    assert all("required_term" in ((item.get("ref") or {}).get("signals") or []) for item in hits["items"])


def test_manual_find_runs_three_pass_merge_for_two_required_terms(state) -> None:
    out = manual_find(
        state,
        query="対象外の条件と手順を教えて",
        manual_id="m1",
        expand_scope=False,
        required_terms=["対象外", "手順"],
    )
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates")

    assert out["applied"]["required_terms"] == ["対象外", "手順"]
    assert hits["total"] >= 1
    assert any("required_terms_rrf" in ((item.get("ref") or {}).get("signals") or []) for item in hits["items"])


def test_query_decomp_subqueries_supports_compare_pattern() -> None:
    sub_queries = tools_manual_module._query_decomp_subqueries(
        "入院給付金と通院給付金の違い",
        max_sub_queries=3,
    )
    assert sub_queries == ["入院給付金と通院給付金の違い", "入院給付金", "通院給付金"]


def test_query_decomp_subqueries_supports_simple_compare_pattern() -> None:
    sub_queries = tools_manual_module._query_decomp_subqueries(
        "入院給付金と通院給付金",
        max_sub_queries=3,
    )
    assert sub_queries == ["入院給付金と通院給付金", "入院給付金", "通院給付金"]


def test_query_decomp_subqueries_supports_case_pattern() -> None:
    sub_queries = tools_manual_module._query_decomp_subqueries(
        "退院後の場合の通院日数制限",
        max_sub_queries=3,
    )
    assert sub_queries == ["退院後の場合の通院日数制限", "退院後 通院日数制限", "通院日数制限"]


def test_query_decomp_subqueries_supports_compare_keyword_pattern() -> None:
    sub_queries = tools_manual_module._query_decomp_subqueries(
        "入院給付金と通院給付金を比較",
        max_sub_queries=3,
    )
    assert sub_queries == ["入院給付金と通院給付金を比較", "入院給付金", "通院給付金"]


def test_query_decomp_subqueries_supports_vs_pattern() -> None:
    sub_queries = tools_manual_module._query_decomp_subqueries(
        "入院給付金 vs 通院給付金",
        max_sub_queries=3,
    )
    assert sub_queries == ["入院給付金 vs 通院給付金", "入院給付金", "通院給付金"]


def test_manual_find_applies_query_decomp_rrf_signal_when_enabled(state, monkeypatch) -> None:
    monkeypatch.setenv("MANUAL_FIND_QUERY_DECOMP_ENABLED", "true")
    monkeypatch.setenv("MANUAL_FIND_QUERY_DECOMP_MAX_SUB_QUERIES", "3")
    local_state = create_state(Config.from_env())
    (local_state.config.manuals_root / "m1" / "proc.md").write_text("# 手順\n手順があります。\n", encoding="utf-8")
    calls: list[str] = []

    def fake_run_find_pass(*args, **kwargs):
        query = kwargs.get("query") if "query" in kwargs else args[2]
        manual_id = kwargs.get("manual_ids", [])[0] if "manual_ids" in kwargs else args[1][0]
        calls.append(str(query))
        if query == "対象外と手順の違い":
            rows = []
        elif query == "対象外":
            rows = [
                {
                    "ref": {
                        "target": "manual",
                        "manual_id": manual_id,
                        "path": "rules.md",
                        "start_line": 3,
                        "heading_id": "h-ex",
                        "json_path": None,
                        "title": "例外",
                        "signals": ["exact"],
                    },
                    "path": "rules.md",
                    "start_line": 3,
                    "reason": None,
                    "signals": ["exact"],
                    "score": 1.2,
                    "conflict_with": [],
                    "gap_hint": None,
                    "matched_tokens": ["対象外"],
                    "token_hits": {"対象外": 1},
                    "match_coverage": 1.0,
                    "rank_explain": ["base=1.2"],
                }
            ]
        elif query == "手順":
            rows = [
                {
                    "ref": {
                        "target": "manual",
                        "manual_id": manual_id,
                        "path": "proc.md",
                        "start_line": 1,
                        "heading_id": "h-proc",
                        "json_path": None,
                        "title": "手順",
                        "signals": ["exact"],
                    },
                    "path": "proc.md",
                    "start_line": 1,
                    "reason": None,
                    "signals": ["exact"],
                    "score": 1.0,
                    "conflict_with": [],
                    "gap_hint": None,
                    "matched_tokens": ["手順"],
                    "token_hits": {"手順": 1},
                    "match_coverage": 1.0,
                    "rank_explain": ["base=1.0"],
                }
            ]
        else:
            rows = []
        return rows, 1, max(1, len(rows)), 0, None, [], False, 2

    monkeypatch.setattr(tools_manual_module, "_run_find_pass", fake_run_find_pass)

    out = manual_find(local_state, query="対象外と手順の違い", manual_id="m1", expand_scope=False)
    hits = manual_hits(local_state, trace_id=out["trace_id"], kind="candidates")
    assert set(calls) == {"対象外と手順の違い", "対象外", "手順"}
    assert hits["total"] == 2
    assert all("query_decomp_rrf" in ((item.get("ref") or {}).get("signals") or []) for item in hits["items"])


def test_manual_find_query_decomp_rrf_can_promote_multi_hit_candidate_with_normalized_mix(state, monkeypatch) -> None:
    monkeypatch.setenv("MANUAL_FIND_QUERY_DECOMP_ENABLED", "true")
    monkeypatch.setenv("MANUAL_FIND_QUERY_DECOMP_BASE_WEIGHT", "0.2")
    local_state = create_state(Config.from_env())

    def row(*, manual_id: str, path: str, start_line: int, score: float, token: str) -> dict[str, object]:
        return {
            "ref": {
                "target": "manual",
                "manual_id": manual_id,
                "path": path,
                "start_line": start_line,
                "heading_id": None,
                "json_path": None,
                "title": path,
                "signals": ["exact"],
            },
            "path": path,
            "start_line": start_line,
            "reason": None,
            "signals": ["exact"],
            "score": score,
            "conflict_with": [],
            "gap_hint": None,
            "matched_tokens": [token],
            "token_hits": {token: 1},
            "match_coverage": 1.0,
            "rank_explain": [f"base={score}"],
        }

    def fake_run_find_pass(*args, **kwargs):
        query = kwargs.get("query") if "query" in kwargs else args[2]
        manual_id = kwargs.get("manual_ids", [])[0] if "manual_ids" in kwargs else args[1][0]
        if query == "対象外と手順の違い":
            rows = []
        elif query == "対象外":
            rows = [
                row(manual_id=manual_id, path="low_rrf.md", start_line=1, score=1.0, token="対象外"),
                row(manual_id=manual_id, path="high_base.md", start_line=2, score=100.0, token="対象外"),
            ]
        elif query == "手順":
            rows = [row(manual_id=manual_id, path="low_rrf.md", start_line=1, score=1.0, token="手順")]
        else:
            rows = []
        return rows, 1, max(1, len(rows)), 0, None, [], False, 2

    monkeypatch.setattr(tools_manual_module, "_run_find_pass", fake_run_find_pass)
    out = manual_find(local_state, query="対象外と手順の違い", manual_id="m1", expand_scope=False)
    hits = manual_hits(local_state, trace_id=out["trace_id"], kind="candidates")

    assert hits["total"] == 2
    assert hits["items"][0]["ref"]["path"] == "low_rrf.md"


def test_manual_find_query_decomp_falls_back_when_all_subqueries_fail(state, monkeypatch) -> None:
    monkeypatch.setenv("MANUAL_FIND_QUERY_DECOMP_ENABLED", "true")
    local_state = create_state(Config.from_env())
    attempts: dict[str, int] = {}

    def fake_run_find_pass(*args, **kwargs):
        query = kwargs.get("query") if "query" in kwargs else args[2]
        manual_id = kwargs.get("manual_ids", [])[0] if "manual_ids" in kwargs else args[1][0]
        attempts[query] = attempts.get(query, 0) + 1
        if attempts[query] == 1:
            raise RuntimeError("simulated sub-query failure")
        return (
            [
                {
                    "ref": {
                        "target": "manual",
                        "manual_id": manual_id,
                        "path": "fallback.md",
                        "start_line": 1,
                        "heading_id": None,
                        "json_path": None,
                        "title": "fallback",
                        "signals": ["exact"],
                    },
                    "path": "fallback.md",
                    "start_line": 1,
                    "reason": None,
                    "signals": ["exact"],
                    "score": 2.0,
                    "conflict_with": [],
                    "gap_hint": None,
                    "matched_tokens": ["対象外"],
                    "token_hits": {"対象外": 1},
                    "match_coverage": 1.0,
                    "rank_explain": ["base=2.0"],
                }
            ],
            1,
            1,
            0,
            None,
            [],
            False,
            2,
        )

    monkeypatch.setattr(tools_manual_module, "_run_find_pass", fake_run_find_pass)
    out = manual_find(local_state, query="対象外と手順の違い", manual_id="m1", expand_scope=False)
    hits = manual_hits(local_state, trace_id=out["trace_id"], kind="candidates")

    assert hits["total"] == 1
    assert hits["items"][0]["ref"]["path"] == "fallback.md"
    assert "query_decomp_rrf" not in ((hits["items"][0].get("ref") or {}).get("signals") or [])


def test_manual_find_rejects_non_integer_budget_time_ms(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_find(state, query="対象外", manual_id="m1", budget={"time_ms": "abc"})
    assert e.value.code == "invalid_parameter"


def test_manual_find_rejects_negative_budget_max_candidates(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_find(state, query="対象外", manual_id="m1", budget={"max_candidates": -1})
    assert e.value.code == "invalid_parameter"


def test_manual_find_rejects_non_boolean_include_claim_graph(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_find(state, query="対象外", manual_id="m1", include_claim_graph="true")
    assert e.value.code == "invalid_parameter"


def test_manual_find_requires_manual_id(state) -> None:
    with pytest.raises(ToolError) as e:
        manual_find(state, query="対象外")
    assert e.value.code == "invalid_parameter"


def test_manual_find_stage3_returns_lexical_exact_signal(state) -> None:
    out = manual_find(state, query="対 象外", manual_id="m1", expand_scope=False)
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates")
    assert any("exact" in (item["ref"] or {}).get("signals", []) for item in hits["items"])


def test_manual_find_does_not_candidateize_heading_only_match(state) -> None:
    manual_dir = state.config.manuals_root / "m4"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "heading_only.md").write_text("# 対象外\n", encoding="utf-8")

    out = manual_find(state, query="対象外", manual_id="m4", expand_scope=False)
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates")

    assert out["summary"]["candidates"] == 0
    assert hits["total"] == 0


def test_manual_find_does_not_add_heading_focus_signal(state) -> None:
    manual_dir = state.config.manuals_root / "m5"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "focus.md").write_text(
        "# 高スコア\n対象外です。\n\n# 低スコア\n対-象-外です。\n",
        encoding="utf-8",
    )

    out = manual_find(state, query="対象外", manual_id="m5", expand_scope=False)
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates")

    assert hits["total"] == 2
    assert all("heading_focus" not in ((item.get("ref") or {}).get("signals") or []) for item in hits["items"])


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


def test_manual_find_runs_limited_stage4_when_expand_scope_true(state) -> None:
    manual_dir = state.config.manuals_root / "m3"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "exceptions_only.md").write_text("# 補足\nこの場合は対象外です。\n", encoding="utf-8")

    out_true = manual_find(state, query="対象外", manual_id="m3", expand_scope=True)
    out_false = manual_find(state, query="対象外", manual_id="m3", expand_scope=False)
    hits_true = manual_hits(state, trace_id=out_true["trace_id"], kind="candidates")
    hits_false = manual_hits(state, trace_id=out_false["trace_id"], kind="candidates")

    assert out_true["summary"]["candidates"] >= out_false["summary"]["candidates"]
    assert hits_true["total"] >= hits_false["total"]
    assert any("expanded_scope" in ((item.get("ref") or {}).get("signals") or []) for item in hits_true["items"])
    assert out_true["applied"]["requested_expand_scope"] is True
    assert out_true["applied"]["expand_scope"] is True
    assert out_false["applied"]["requested_expand_scope"] is False
    assert out_false["applied"]["expand_scope"] is False


def test_manual_find_requested_expand_scope_is_null_when_omitted(state) -> None:
    out = manual_find(state, query="対象外", manual_id="m1")
    assert out["applied"]["requested_expand_scope"] is None


def test_manual_find_corrective_triggers_limited_stage4_when_expand_scope_true(state, monkeypatch) -> None:
    monkeypatch.setenv("CORRECTIVE_ENABLED", "true")
    monkeypatch.setenv("CORRECTIVE_MARGIN_MIN", "1.5")
    local_state = create_state(Config.from_env())

    monkeypatch.setattr(tools_manual_module, "_should_expand_scope", lambda **kwargs: False)
    original = tools_manual_module._run_find_pass
    calls = {"count": 0}

    def counting_run_find_pass(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(tools_manual_module, "_run_find_pass", counting_run_find_pass)
    _ = manual_find(local_state, query="対象外", manual_id="m1", expand_scope=True)

    assert calls["count"] == 2
    lines = local_state.config.adaptive_stats_path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[-1])
    assert row["corrective_triggered"] is True
    assert row["stage4_executed"] is True
    assert "corrective_low_margin" in row["corrective_reasons"]


def test_manual_find_applies_late_rerank_when_enabled(state, monkeypatch) -> None:
    monkeypatch.setenv("LATE_RERANK_ENABLED", "true")
    monkeypatch.setenv("LATE_RERANK_WEIGHT", "1.0")
    local_state = create_state(Config.from_env())

    out = manual_find(local_state, query="対象外", manual_id="m1", expand_scope=False)
    hits = manual_hits(local_state, trace_id=out["trace_id"], kind="candidates")

    assert hits["total"] >= 1
    assert any("late_rerank" in (item["ref"] or {}).get("signals", []) for item in hits["items"])


def test_manual_find_uses_late_reranker_hook(state) -> None:
    manual_dir = state.config.manuals_root / "m6"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "hook.md").write_text(
        "# 上位候補\n対象外です。\n\n# 下位候補\n対象外です。\n",
        encoding="utf-8",
    )
    baseline = manual_find(state, query="対象外", manual_id="m6", expand_scope=False)
    baseline_hits = manual_hits(state, trace_id=baseline["trace_id"], kind="candidates")
    assert baseline_hits["total"] == 2
    baseline_top_start = baseline_hits["items"][0]["ref"]["start_line"]

    def reranker(payload: dict[str, object]) -> list[dict[str, object]]:
        rows = payload.get("candidates")
        assert isinstance(rows, list)
        reversed_rows = list(reversed(rows))
        out: list[dict[str, object]] = []
        for idx, item in enumerate(reversed_rows):
            if not isinstance(item, dict):
                continue
            row = dict(item)
            if idx == 0:
                row["score"] = 999.0
            out.append(row)
        return out

    state.late_reranker = reranker
    out = manual_find(state, query="対象外", manual_id="m6", expand_scope=False)
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates")
    assert hits["items"][0]["ref"]["start_line"] != baseline_top_start


def test_manual_find_only_unscanned_prioritizes_without_strict_filtering(state, monkeypatch) -> None:
    monkeypatch.setenv("MANUAL_FIND_SCAN_HARD_CAP", "1")
    local_state = create_state(Config.from_env())

    initial = manual_find(local_state, query="対象外", manual_id="m1", budget={"max_candidates": 1})
    initial_unscanned = manual_hits(local_state, trace_id=initial["trace_id"], kind="unscanned")
    unscanned_keys = {
        f'{item["manual_id"]}:{item["path"]}'
        for item in initial_unscanned["items"]
    }
    followup = manual_find(
        local_state,
        query="対象外",
        manual_id="m1",
        only_unscanned_from_trace_id=initial["trace_id"],
    )
    hits = manual_hits(local_state, trace_id=followup["trace_id"], kind="candidates")
    assert hits["total"] >= 1
    followup_keys = {
        f'{(item["ref"] or {}).get("manual_id") or hits.get("manual_id")}:{item["ref"]["path"]}'
        for item in hits["items"]
    }
    assert any(key in unscanned_keys for key in followup_keys)


def test_manual_find_unscanned_collects_remaining_scope_after_cutoff(state, monkeypatch) -> None:
    monkeypatch.setenv("MANUAL_FIND_SCAN_HARD_CAP", "1")
    local_state = create_state(Config.from_env())

    out = manual_find(local_state, query="対象外", manual_id="m1", budget={"max_candidates": 1})
    unscanned = manual_hits(local_state, trace_id=out["trace_id"], kind="unscanned")
    keys = {f'{item["manual_id"]}:{item["path"]}' for item in unscanned["items"]}
    assert "m1:rules.md" in keys
    assert "m2:appendix.md" in keys


def test_manual_find_summary_uses_minimal_fields(state) -> None:
    out = manual_find(state, query="対象外", manual_id="m1")
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
    out = manual_find(state, query="対象外", manual_id="m1")
    assert captured["query"] == "対象外"
    assert isinstance(captured["summary"], dict)
    assert out["next_actions"] == [{"type": "manual_hits", "confidence": 0.9, "params": {"kind": "gaps", "offset": 0, "limit": 5}}]


def test_manual_find_falls_back_when_next_actions_planner_returns_invalid_schema(state) -> None:
    state.next_actions_planner = lambda payload: [{"type": "stop", "confidence": 0.8, "params": None}]
    out = manual_find(state, query="対象外", manual_id="m1")
    assert out["next_actions"][0]["type"] in {"manual_hits", "manual_read"}


def test_manual_find_falls_back_when_next_actions_planner_raises(state) -> None:
    def planner(_: dict[str, object]) -> list[dict[str, object]]:
        raise RuntimeError("planner failed")

    state.next_actions_planner = planner
    out = manual_find(state, query="対象外", manual_id="m1")
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


def test_manual_hits_candidates_include_lexical_match_fields(state) -> None:
    out = manual_find(state, query="対象外", manual_id="m1")
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates", limit=1)
    first = hits["items"][0]
    assert isinstance(first.get("matched_tokens"), list)
    assert isinstance(first.get("token_hits"), dict)
    assert isinstance(first.get("match_coverage"), float)
    assert isinstance(first.get("rank_explain"), list)


def test_manual_find_reflects_term_frequency_in_scores(state) -> None:
    manual_dir = state.config.manuals_root / "m8"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "tf.md").write_text(
        "# 高頻度\n"
        "支払 支払 支払 支払 支払 支払 支払 支払 支払 支払 支払 支払 支払\n"
        "## 低頻度\n"
        "支払 支払 支払\n",
        encoding="utf-8",
    )

    out = manual_find(state, query="支払", manual_id="m8", expand_scope=False, budget={"max_candidates": 5})
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates", limit=2)

    assert hits["total"] >= 2
    first = hits["items"][0]
    second = hits["items"][1]
    assert first["ref"]["path"] == "tf.md"
    assert second["ref"]["path"] == "tf.md"
    assert float(first["score"]) > float(second["score"])
    assert int(first["token_hits"]["支払"]) > int(second["token_hits"]["支払"])


def test_candidate_sort_key_breaks_score_ties_by_token_hits() -> None:
    weaker = {
        "score": 6.6444,
        "match_coverage": 1.0,
        "matched_tokens": ["支払条件"],
        "token_hits": {"支払条件": 3},
        "path": "a.md",
        "start_line": 1,
    }
    stronger = {
        "score": 6.6444,
        "match_coverage": 1.0,
        "matched_tokens": ["支払条件"],
        "token_hits": {"支払条件": 13},
        "path": "z.md",
        "start_line": 99,
    }

    ordered = sorted([weaker, stronger], key=tools_manual_module._candidate_sort_key)
    assert ordered[0] is stronger


def test_effective_scan_hard_cap_scales_with_budget() -> None:
    assert tools_manual_module._effective_scan_hard_cap(5000, 1) == 50
    assert tools_manual_module._effective_scan_hard_cap(5000, 200) == 4000
    assert tools_manual_module._effective_scan_hard_cap(100, 200) == 100


def test_file_diversity_rerank_reduces_same_path_dominance() -> None:
    candidates = [
        {
            "path": "a.md",
            "start_line": 1,
            "score": 10.0,
            "_rank_score": 10.0,
            "match_coverage": 1.0,
            "matched_tokens": ["通院"],
            "token_hits": {"通院": 2},
        },
        {
            "path": "a.md",
            "start_line": 2,
            "score": 9.9,
            "_rank_score": 9.9,
            "match_coverage": 1.0,
            "matched_tokens": ["通院"],
            "token_hits": {"通院": 2},
        },
        {
            "path": "b.md",
            "start_line": 1,
            "score": 9.6,
            "_rank_score": 9.6,
            "match_coverage": 1.0,
            "matched_tokens": ["通院"],
            "token_hits": {"通院": 1},
        },
    ]

    reranked = tools_manual_module._apply_file_diversity_rerank(candidates)

    assert [item["path"] for item in reranked] == ["a.md", "b.md", "a.md"]
    assert any("file_diversity=" in part for part in (reranked[2].get("rank_explain") or []))


def test_manual_find_multilayer_tokenization_matches_compound_japanese_query(state) -> None:
    manual_dir = state.config.manuals_root / "m9"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "compound.md").write_text(
        "# 条件\n"
        "この給付金の支払 条件を確認します。\n",
        encoding="utf-8",
    )

    out = manual_find(state, query="支払条件", manual_id="m9", expand_scope=False)
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates", limit=1)

    assert hits["total"] >= 1
    first = hits["items"][0]
    assert first["ref"]["path"] == "compound.md"
    assert float(first.get("match_coverage") or 0.0) >= 1.0


def test_manual_find_multilayer_tokenization_matches_alnum_cjk_compound_query(state) -> None:
    manual_dir = state.config.manuals_root / "m10"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "code.md").write_text(
        "# 手術番号\n"
        "対象となる手術番号は K867 です。\n",
        encoding="utf-8",
    )

    out = manual_find(state, query="K867手術番号", manual_id="m10", expand_scope=False)
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates", limit=1)

    assert hits["total"] >= 1
    first = hits["items"][0]
    assert first["ref"]["path"] == "code.md"
    matched_tokens = set(first.get("matched_tokens") or [])
    assert "k867" in matched_tokens


def test_segment_query_term_avoids_artificial_cjk_fragments() -> None:
    tokens = tools_manual_module._segment_query_term("がん手術給付金")
    assert "がん手" not in tokens
    assert "がん" in tokens
    assert "手術" in tokens
    assert "給付金" in tokens


def test_manual_find_scans_later_files_even_with_small_budget_max_candidates(state) -> None:
    manual_dir = state.config.manuals_root / "m14"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "01_early.md").write_text(
        "# 先頭\n"
        + "\n".join(f"## 行{idx}\n入院 給付金 120日 です。" for idx in range(40)),
        encoding="utf-8",
    )
    (manual_dir / "99_late.md").write_text(
        "# 通院特約\n退院後120日以内の通院給付金を支払います。\n",
        encoding="utf-8",
    )

    out = manual_find(
        state,
        query="通院特約 120日",
        manual_id="m14",
        expand_scope=False,
        budget={"max_candidates": 5},
    )
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates", limit=5)
    assert hits["total"] >= 1
    assert any(item["ref"]["path"] == "99_late.md" for item in hits["items"])


def test_prf_expand_terms_uses_corpus_context_without_synonym_dictionary(state) -> None:
    manual_dir = state.config.manuals_root / "m11"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "prf.md").write_text(
        "# 先進\n"
        "先進医療 負担額 負担額 負担額\n"
        "## 一般\n"
        "先進医療 特約\n",
        encoding="utf-8",
    )
    manuals_fp = tools_manual_module._manuals_fingerprint(state, ["m11"])
    sparse_index, _ = state.sparse_index.get_or_build(manual_ids=["m11"], fingerprint=manuals_fp)

    expanded = tools_manual_module._prf_expand_terms(
        sparse_index=sparse_index,
        query_terms={"先進医療", "自己負担"},
        missing_terms={"自己負担"},
    )

    assert "負担額" in expanded
    assert all(not term.isdigit() for term in expanded)
    assert all(":" not in term and "-" not in term and "|" not in term for term in expanded)


def test_manual_find_boosts_code_exact_match(state) -> None:
    manual_dir = state.config.manuals_root / "m12"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "codes.md").write_text(
        "# 一般\n"
        "手術番号 手術番号 手術番号 手術番号 手術番号 手術番号 手術番号\n"
        "## コード\n"
        "対象となる手術番号は K867 です。\n",
        encoding="utf-8",
    )

    out = manual_find(state, query="K867 手術番号", manual_id="m12", expand_scope=False)
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates", limit=1)

    assert hits["total"] >= 1
    first = hits["items"][0]
    assert first["ref"]["path"] == "codes.md"
    assert "k867" in set(first.get("matched_tokens") or [])
    assert "code_exact" in set((first.get("ref") or {}).get("signals") or [])


def test_manual_find_skips_toc_like_paths(state) -> None:
    manual_dir = state.config.manuals_root / "m7"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "00_目次.md").write_text("# 目次\n対象外です。\n", encoding="utf-8")
    (manual_dir / "rules.md").write_text("# 本文\n対象外です。\n", encoding="utf-8")

    out = manual_find(state, query="対象外", manual_id="m7")
    hits = manual_hits(state, trace_id=out["trace_id"], kind="candidates")

    assert hits["total"] >= 1
    assert all(item["ref"]["path"] != "00_目次.md" for item in hits["items"])


def test_manual_find_applies_dynamic_cutoff_max_50(state) -> None:
    manual_dir = state.config.manuals_root / "m13"
    manual_dir.mkdir(parents=True, exist_ok=True)
    for idx in range(80):
        (manual_dir / f"row_{idx:03d}.md").write_text(
            f"# 行{idx}\n入院 給付金 対象です。\n",
            encoding="utf-8",
        )

    out = manual_find(state, query="入院 給付金", manual_id="m13", budget={"max_candidates": 200})
    assert out["summary"]["candidates"] <= 50
    payload = state.traces.get(out["trace_id"]) or {}
    assert payload.get("cutoff_reason") == "dynamic_cutoff"


def test_manual_find_candidate_cap_means_scan_hard_cap(state, monkeypatch) -> None:
    monkeypatch.setenv("MANUAL_FIND_SCAN_HARD_CAP", "1")
    local_state = create_state(Config.from_env())

    out = manual_find(local_state, query="対象外", manual_id="m1", budget={"max_candidates": 200})
    payload = local_state.traces.get(out["trace_id"]) or {}
    assert payload.get("cutoff_reason") == "candidate_cap"
    unscanned = manual_hits(local_state, trace_id=out["trace_id"], kind="unscanned")
    assert unscanned["total"] >= 1


def test_manual_find_returns_claim_graph_when_requested(state) -> None:
    out = manual_find(state, query="対象外", manual_id="m1", include_claim_graph=True)
    assert "claim_graph" in out
    assert "claims" in out["claim_graph"]
    assert "evidences" in out["claim_graph"]
    assert "edges" in out["claim_graph"]
    assert "facets" in out["claim_graph"]


def test_manual_find_omits_claim_graph_by_default(state) -> None:
    out = manual_find(state, query="対象外", manual_id="m1")
    assert "claim_graph" not in out


def test_manual_hits_supports_claim_graph_kinds(state) -> None:
    out = manual_find(state, query="対象外", manual_id="m1")
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


def test_manual_find_stage_cap_result_is_cacheable(state, monkeypatch) -> None:
    monkeypatch.setenv("SEM_CACHE_ENABLED", "true")
    local_state = create_state(Config.from_env())

    original = tools_manual_module._run_find_pass
    calls = {"count": 0}

    def counting_run_find_pass(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(tools_manual_module, "_run_find_pass", counting_run_find_pass)
    first = manual_find(local_state, query="zzz", manual_id="m2")
    assert calls["count"] >= 1
    assert any(item["reason"] == "stage_cap" for item in manual_hits(local_state, trace_id=first["trace_id"], kind="unscanned")["items"])

    calls["count"] = 0
    second = manual_find(local_state, query="zzz", manual_id="m2")
    assert calls["count"] == 0
    assert any(
        item["reason"] == "stage_cap"
        for item in manual_hits(local_state, trace_id=second["trace_id"], kind="unscanned")["items"]
    )


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
    assert row["scoring_mode"] == "lexical"
    assert isinstance(row["index_rebuilt"], bool)
    assert isinstance(row["index_docs"], int)
    assert isinstance(row["corrective_triggered"], bool)
    assert isinstance(row["corrective_reasons"], list)
    assert isinstance(row["stage4_executed"], bool)


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
    assert isinstance(row["corrective_triggered"], bool)
    assert isinstance(row["corrective_reasons"], list)
    assert isinstance(row["stage4_executed"], bool)


def test_manual_hits_not_found_after_ttl(state) -> None:
    out = manual_find(state, query="対象外", manual_id="m1")
    trace_id = out["trace_id"]
    assert manual_hits(state, trace_id=trace_id)["total"] >= 1
    time.sleep(1.2)
    with pytest.raises(ToolError) as e:
        manual_hits(state, trace_id=trace_id)
    assert e.value.code == "not_found"


def test_manual_hits_rejects_non_integer_offset(state) -> None:
    out = manual_find(state, query="対象外", manual_id="m1")
    with pytest.raises(ToolError) as e:
        manual_hits(state, trace_id=out["trace_id"], offset="abc")
    assert e.value.code == "invalid_parameter"


def test_manual_hits_rejects_negative_limit(state) -> None:
    out = manual_find(state, query="対象外", manual_id="m1")
    with pytest.raises(ToolError) as e:
        manual_hits(state, trace_id=out["trace_id"], limit=-1)
    assert e.value.code == "invalid_parameter"


def test_manual_hits_rejects_boolean_limit(state) -> None:
    out = manual_find(state, query="対象外", manual_id="m1")
    with pytest.raises(ToolError) as e:
        manual_hits(state, trace_id=out["trace_id"], limit=True)
    assert e.value.code == "invalid_parameter"


def test_manual_hits_rejects_non_string_kind(state) -> None:
    out = manual_find(state, query="対象外", manual_id="m1")
    with pytest.raises(ToolError) as e:
        manual_hits(state, trace_id=out["trace_id"], kind=["candidates"])  # type: ignore[arg-type]
    assert e.value.code == "invalid_parameter"


def test_config_default_adaptive_stats_path_under_system(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("VAULT_ROOT", raising=False)
    monkeypatch.delenv("ADAPTIVE_STATS_PATH", raising=False)
    cfg = Config.from_env()
    assert cfg.adaptive_stats_path == (cfg.vault_root / ".system" / "adaptive_stats.jsonl")


def test_config_corrective_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("MANUALS_ROOT", raising=False)
    monkeypatch.delenv("VAULT_ROOT", raising=False)
    monkeypatch.delenv("CORRECTIVE_ENABLED", raising=False)
    monkeypatch.delenv("CORRECTIVE_COVERAGE_MIN", raising=False)
    monkeypatch.delenv("CORRECTIVE_MARGIN_MIN", raising=False)
    monkeypatch.delenv("CORRECTIVE_MIN_CANDIDATES", raising=False)
    monkeypatch.delenv("CORRECTIVE_ON_CONFLICT", raising=False)
    monkeypatch.delenv("LEXICAL_COVERAGE_WEIGHT", raising=False)
    monkeypatch.delenv("LEXICAL_PHRASE_WEIGHT", raising=False)
    monkeypatch.delenv("LEXICAL_NUMBER_CONTEXT_BONUS", raising=False)
    monkeypatch.delenv("LEXICAL_PROXIMITY_BONUS_NEAR", raising=False)
    monkeypatch.delenv("LEXICAL_PROXIMITY_BONUS_FAR", raising=False)
    monkeypatch.delenv("LEXICAL_LENGTH_PENALTY_WEIGHT", raising=False)
    monkeypatch.delenv("MANUAL_FIND_EXPLORATION_ENABLED", raising=False)
    monkeypatch.delenv("MANUAL_FIND_EXPLORATION_RATIO", raising=False)
    monkeypatch.delenv("MANUAL_FIND_EXPLORATION_MIN_CANDIDATES", raising=False)
    monkeypatch.delenv("MANUAL_FIND_EXPLORATION_SCORE_SCALE", raising=False)
    monkeypatch.delenv("MANUAL_FIND_STAGE4_ENABLED", raising=False)
    monkeypatch.delenv("MANUAL_FIND_STAGE4_NEIGHBOR_LIMIT", raising=False)
    monkeypatch.delenv("MANUAL_FIND_STAGE4_BUDGET_TIME_MS", raising=False)
    monkeypatch.delenv("MANUAL_FIND_STAGE4_SCORE_PENALTY", raising=False)
    monkeypatch.delenv("MANUAL_FIND_QUERY_DECOMP_ENABLED", raising=False)
    monkeypatch.delenv("MANUAL_FIND_QUERY_DECOMP_MAX_SUB_QUERIES", raising=False)
    monkeypatch.delenv("MANUAL_FIND_QUERY_DECOMP_RRF_K", raising=False)
    monkeypatch.delenv("MANUAL_FIND_QUERY_DECOMP_BASE_WEIGHT", raising=False)
    monkeypatch.delenv("MANUAL_FIND_SCAN_HARD_CAP", raising=False)
    monkeypatch.delenv("MANUAL_FIND_PER_FILE_CANDIDATE_CAP", raising=False)
    monkeypatch.delenv("MANUAL_FIND_FILE_PRESCAN_ENABLED", raising=False)

    cfg = Config.from_env()
    assert cfg.corrective_enabled is False
    assert cfg.corrective_coverage_min == 0.90
    assert cfg.corrective_margin_min == 0.15
    assert cfg.corrective_min_candidates == 3
    assert cfg.corrective_on_conflict is True
    assert cfg.sparse_query_coverage_weight == 0.35
    assert cfg.lexical_coverage_weight == 0.50
    assert cfg.lexical_phrase_weight == 0.50
    assert cfg.lexical_number_context_bonus == 0.80
    assert cfg.lexical_proximity_bonus_near == 1.00
    assert cfg.lexical_proximity_bonus_far == 0.50
    assert cfg.lexical_length_penalty_weight == 0.20
    assert cfg.manual_find_exploration_enabled is True
    assert cfg.manual_find_exploration_ratio == 0.20
    assert cfg.manual_find_exploration_min_candidates == 2
    assert cfg.manual_find_exploration_score_scale == 0.35
    assert cfg.manual_find_stage4_enabled is True
    assert cfg.manual_find_stage4_neighbor_limit == 2
    assert cfg.manual_find_stage4_budget_time_ms == 15000
    assert cfg.manual_find_stage4_score_penalty == 0.15
    assert cfg.manual_find_query_decomp_enabled is True
    assert cfg.manual_find_query_decomp_max_sub_queries == 3
    assert cfg.manual_find_query_decomp_rrf_k == 60
    assert cfg.manual_find_query_decomp_base_weight == 0.30
    assert cfg.manual_find_scan_hard_cap == 5000
    assert cfg.manual_find_per_file_candidate_cap == 8
    assert cfg.manual_find_file_prescan_enabled is True
    assert cfg.late_rerank_enabled is False
    assert cfg.late_rerank_top_n == 50
    assert cfg.late_rerank_weight == 0.60


def test_config_lexical_weights_accept_env_overrides(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("LEXICAL_COVERAGE_WEIGHT", "0.9")
    monkeypatch.setenv("LEXICAL_PHRASE_WEIGHT", "0.3")
    monkeypatch.setenv("LEXICAL_NUMBER_CONTEXT_BONUS", "1.4")
    monkeypatch.setenv("LEXICAL_PROXIMITY_BONUS_NEAR", "1.8")
    monkeypatch.setenv("LEXICAL_PROXIMITY_BONUS_FAR", "0.7")
    monkeypatch.setenv("LEXICAL_LENGTH_PENALTY_WEIGHT", "0.1")

    cfg = Config.from_env()
    assert cfg.lexical_coverage_weight == 0.9
    assert cfg.lexical_phrase_weight == 0.3
    assert cfg.lexical_number_context_bonus == 1.4
    assert cfg.lexical_proximity_bonus_near == 1.8
    assert cfg.lexical_proximity_bonus_far == 0.7
    assert cfg.lexical_length_penalty_weight == 0.1


def test_config_manual_find_expansion_and_exploration_accept_env_overrides(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("MANUAL_FIND_EXPLORATION_ENABLED", "false")
    monkeypatch.setenv("MANUAL_FIND_EXPLORATION_RATIO", "0.4")
    monkeypatch.setenv("MANUAL_FIND_EXPLORATION_MIN_CANDIDATES", "5")
    monkeypatch.setenv("MANUAL_FIND_EXPLORATION_SCORE_SCALE", "0.6")
    monkeypatch.setenv("MANUAL_FIND_STAGE4_ENABLED", "false")
    monkeypatch.setenv("MANUAL_FIND_STAGE4_NEIGHBOR_LIMIT", "3")
    monkeypatch.setenv("MANUAL_FIND_STAGE4_BUDGET_TIME_MS", "1200")
    monkeypatch.setenv("MANUAL_FIND_STAGE4_SCORE_PENALTY", "0.25")
    monkeypatch.setenv("MANUAL_FIND_QUERY_DECOMP_ENABLED", "true")
    monkeypatch.setenv("MANUAL_FIND_QUERY_DECOMP_MAX_SUB_QUERIES", "2")
    monkeypatch.setenv("MANUAL_FIND_QUERY_DECOMP_RRF_K", "50")
    monkeypatch.setenv("MANUAL_FIND_QUERY_DECOMP_BASE_WEIGHT", "0.3")
    monkeypatch.setenv("MANUAL_FIND_SCAN_HARD_CAP", "777")
    monkeypatch.setenv("MANUAL_FIND_PER_FILE_CANDIDATE_CAP", "4")
    monkeypatch.setenv("MANUAL_FIND_FILE_PRESCAN_ENABLED", "false")

    cfg = Config.from_env()
    assert cfg.manual_find_exploration_enabled is False
    assert cfg.manual_find_exploration_ratio == 0.4
    assert cfg.manual_find_exploration_min_candidates == 5
    assert cfg.manual_find_exploration_score_scale == 0.6
    assert cfg.manual_find_stage4_enabled is False
    assert cfg.manual_find_stage4_neighbor_limit == 3
    assert cfg.manual_find_stage4_budget_time_ms == 1200
    assert cfg.manual_find_stage4_score_penalty == 0.25
    assert cfg.manual_find_query_decomp_enabled is True
    assert cfg.manual_find_query_decomp_max_sub_queries == 2
    assert cfg.manual_find_query_decomp_rrf_k == 50
    assert cfg.manual_find_query_decomp_base_weight == 0.3
    assert cfg.manual_find_scan_hard_cap == 777
    assert cfg.manual_find_per_file_candidate_cap == 4
    assert cfg.manual_find_file_prescan_enabled is False


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
