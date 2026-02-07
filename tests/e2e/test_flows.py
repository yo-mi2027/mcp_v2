from __future__ import annotations

from mcp_v2_server.tools_manual import manual_find, manual_hits, manual_read
from mcp_v2_server.tools_vault import vault_scan


def test_manual_flow_e2e(state) -> None:
    found = manual_find(state, query="対象外", manual_id="m1", max_stage=4)
    assert "trace_id" in found
    hits = manual_hits(state, trace_id=found["trace_id"], kind="candidates", limit=10)
    assert hits["total"] >= 1
    top = next((x for x in hits["items"] if x["ref"]["path"].endswith(".md")), hits["items"][0])
    scope = "section" if top["ref"]["path"].endswith(".md") else "file"
    read = manual_read(state, ref=top["ref"], scope=scope)
    assert len(read["text"]) > 0


def test_vault_flow_e2e(state) -> None:
    scan = vault_scan(state, path="source.md", cursor={"start_line": 1}, chunk_lines=2)
    assert scan["applied_range"]["start_line"] == 1
    assert "next_actions" in scan
