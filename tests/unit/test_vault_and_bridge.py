from __future__ import annotations

import json

import pytest

from mcp_v2_server.app import _execute
from mcp_v2_server.errors import ToolError
from mcp_v2_server.tools_bridge import bridge_copy_file, bridge_copy_section
from mcp_v2_server.tools_vault import (
    artifact_audit,
    vault_coverage,
    vault_create,
    vault_find,
    vault_read,
    vault_replace,
    vault_scan,
    vault_write,
)


def test_vault_read_requires_range_when_not_full(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_read(state, path="source.md", full=False, range=None)
    assert e.value.code == "invalid_parameter"


def test_vault_scan_chunk_lines_range(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_scan(state, path="source.md", chunk_lines=999)
    assert e.value.code == "invalid_parameter"


def test_vault_find_retries_find_when_file_bias_is_high(state) -> None:
    out = vault_find(state, query="line3", scope={"glob": "**/*.md"})
    action = next(a for a in out["next_actions"] if a["type"] == "vault_find")
    assert action["params"]["scope"] == {"glob": "**/*.md"}


def test_vault_find_returns_stop_when_sufficient(state) -> None:
    root = state.config.vault_root
    for i in range(1, 6):
        (root / f"topic{i}.md").write_text(f"needle topic {i}\n", encoding="utf-8")

    out = vault_find(state, query="needle", scope={"glob": "**/*.md"})
    assert out["summary"]["integration_status"] == "ready"
    assert out["next_actions"][0]["type"] == "stop"
    assert out["next_actions"][0]["params"] is None


def test_vault_find_suggests_scan_when_no_candidates(state) -> None:
    out = vault_find(state, query="definitely-not-found", scope={"glob": "**/*.md"})
    assert out["summary"]["gap_ranges_count"] == 1
    assert out["next_actions"][0]["type"] == "vault_scan"


def test_vault_coverage_computes_ratio(state) -> None:
    out = vault_coverage(state, path="source.md", cited_ranges=[{"start_line": 1, "end_line": 2}])
    assert out["total_lines"] == 5
    assert out["coverage_ratio"] == 0.4
    assert out["meets_min_coverage"] is False


def test_vault_read_rejects_out_of_range_start_line(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_read(state, path="source.md", full=False, range={"start_line": 100, "end_line": 120})
    assert e.value.code == "invalid_parameter"


def test_vault_coverage_normalizes_out_of_bound_and_adjacent_ranges(state) -> None:
    out = vault_coverage(
        state,
        path="source.md",
        cited_ranges=[
            {"start_line": 1, "end_line": 2},
            {"start_line": 3, "end_line": 10},
            {"start_line": 100, "end_line": 200},
        ],
    )
    assert out["covered_ranges"] == [{"start_line": 1, "end_line": 5}]
    assert out["coverage_ratio"] == 1.0


def test_artifact_audit_detects_findings(state) -> None:
    out = artifact_audit(state, artifact_path="artifact.md", source_path="source.md")
    assert out["rootless_nodes"] >= 1
    assert out["needs_forced_full_scan"] is True


def test_daily_file_forbids_overwrite_and_replace(state) -> None:
    vault_create(state, path="daily/2026-02-06.md", content="first\n")
    with pytest.raises(ToolError) as e1:
        vault_write(state, path="daily/2026-02-06.md", content="x", mode="overwrite")
    assert e1.value.code == "forbidden"
    with pytest.raises(ToolError) as e2:
        vault_replace(state, path="daily/2026-02-06.md", find="a", replace="b")
    assert e2.value.code == "forbidden"


def test_bridge_copy_file_md_requires_allow_file_scope(state, monkeypatch) -> None:
    with pytest.raises(ToolError) as e:
        bridge_copy_file(
            state,
            from_path="rules.md",
            manual_id="m1",
            to_path="project-a/copied.md",
            mode="overwrite",
            limits={"allow_file": True},
        )
    assert e.value.code == "forbidden"

    monkeypatch.setenv("ALLOW_FILE_SCOPE", "true")
    state.config = state.config.__class__.from_env()  # type: ignore[misc]
    out = bridge_copy_file(
        state,
        from_path="rules.md",
        manual_id="m1",
        to_path="project-a/copied.md",
        mode="overwrite",
        limits={"allow_file": True},
    )
    assert out["written_bytes"] > 0


def test_bridge_copy_file_json_allows_file_scope_without_allow_file(state) -> None:
    out = bridge_copy_file(
        state,
        from_path="policy.json",
        manual_id="m1",
        to_path="project-a/copied.json",
        mode="overwrite",
        limits={},
    )
    assert out["written_bytes"] > 0


def test_bridge_copy_section_requires_object_ref(state) -> None:
    with pytest.raises(ToolError) as e:
        bridge_copy_section(state, from_ref=None, to_path="project-a/copied.md", mode="overwrite")  # type: ignore[arg-type]
    assert e.value.code == "invalid_parameter"


def test_system_path_is_reserved(state) -> None:
    with pytest.raises(ToolError) as e1:
        vault_create(state, path=".system/manual_note.md", content="nope")
    assert e1.value.code == "forbidden"

    (state.config.vault_root / ".system").mkdir(parents=True, exist_ok=True)
    (state.config.vault_root / ".system" / "existing.md").write_text("x", encoding="utf-8")
    with pytest.raises(ToolError) as e2:
        vault_write(state, path=".system/existing.md", content="y", mode="append")
    assert e2.value.code == "forbidden"
    with pytest.raises(ToolError) as e3:
        vault_replace(state, path=".system/existing.md", find="x", replace="y")
    assert e3.value.code == "forbidden"


def test_execute_logs_bridge_extension_fields(state, capsys) -> None:
    out = _execute(
        state,
        "bridge_copy_section",
        lambda *, mode: {
            "written_path": "project-a/copied.md",
            "written_bytes": 12,
            "written_sections": 1,
            "truncated": False,
        },
        mode="append",
    )
    assert out["written_bytes"] == 12
    line = capsys.readouterr().err.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["tool"] == "bridge_copy_section"
    assert payload["written_path"] == "project-a/copied.md"
    assert payload["mode"] == "append"
    assert payload["written_sections"] == 1


def test_execute_logs_vault_extension_fields(state, capsys) -> None:
    _execute(
        state,
        "vault_read",
        lambda *, path: {"truncated": True},
        path="notes.md",
    )
    _execute(
        state,
        "vault_write",
        lambda *, mode: {"written_path": "project-a/out.md", "written_bytes": 10},
        mode="append",
    )
    _execute(
        state,
        "vault_scan",
        lambda *, path: {"applied_range": {"start_line": 1, "end_line": 2}, "eof": False, "truncated_reason": "chunk_end"},
        path="source.md",
    )
    _execute(
        state,
        "vault_coverage",
        lambda: {
            "path": "source.md",
            "coverage_ratio": 0.4,
            "covered_ranges": [{"start_line": 1, "end_line": 2}],
            "uncovered_ranges": [{"start_line": 3, "end_line": 5}],
        },
    )
    lines = capsys.readouterr().err.strip().splitlines()
    payloads = [json.loads(line) for line in lines[-4:]]

    assert payloads[0]["tool"] == "vault_read"
    assert payloads[0]["path"] == "notes.md"
    assert payloads[0]["truncated"] is True

    assert payloads[1]["tool"] == "vault_write"
    assert payloads[1]["mode"] == "append"
    assert payloads[1]["written_bytes"] == 10

    assert payloads[2]["tool"] == "vault_scan"
    assert payloads[2]["path"] == "source.md"
    assert payloads[2]["truncated_reason"] == "chunk_end"

    assert payloads[3]["tool"] == "vault_coverage"
    assert payloads[3]["coverage_ratio"] == 0.4
    assert payloads[3]["covered_ranges"][0]["start_line"] == 1
    assert payloads[3]["uncovered_ranges"][0]["end_line"] == 5
