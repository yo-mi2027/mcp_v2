from __future__ import annotations

from mcp_v2_server.tools_bridge import bridge_copy_section
from mcp_v2_server.tools_manual import manual_find, manual_hits, manual_read
from mcp_v2_server.tools_vault import vault_audit, vault_coverage, vault_find, vault_scan


def test_manual_flow_e2e(state) -> None:
    found = manual_find(state, query="対象外", manual_id="m1", max_stage=4)
    assert "trace_id" in found
    hits = manual_hits(state, trace_id=found["trace_id"], kind="candidates", limit=10)
    assert hits["total"] >= 1
    top = next((x for x in hits["items"] if x["ref"]["path"].endswith(".md")), hits["items"][0])
    scope = "section" if top["ref"]["path"].endswith(".md") else "file"
    read = manual_read(state, ref=top["ref"], scope=scope)
    assert len(read["text"]) > 0
    copied = bridge_copy_section(
        state,
        from_ref=top["ref"],
        to_path="project-a/result.md",
        mode="overwrite",
    )
    assert copied["written_bytes"] > 0


def test_vault_flow_e2e(state) -> None:
    found = vault_find(state, query="line3", scope={"glob": "**/*.md"})
    assert "trace_id" in found
    scan = vault_scan(state, path="source.md", cursor={"start_line": 1}, chunk_lines=2)
    assert scan["applied_range"]["start_line"] == 1
    cov = vault_coverage(state, path="source.md", cited_ranges=[{"start_line": 1, "end_line": 3}])
    assert cov["coverage_ratio"] == 0.6
    audit = vault_audit(
        state,
        report_path="report.md",
        source_path="source.md",
        cited_ranges=[{"start_line": 1, "end_line": 3}],
    )
    assert "next_actions" in audit
