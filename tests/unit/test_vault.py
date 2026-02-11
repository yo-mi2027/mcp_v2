from __future__ import annotations

import json

import pytest

from mcp_v2_server.app import _execute
from mcp_v2_server.errors import ToolError
from mcp_v2_server.tools_vault import (
    vault_create,
    vault_read,
    vault_replace,
    vault_scan,
)


def test_vault_read_requires_range_when_not_full(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_read(state, path="source.md", full=False, range=None)
    assert e.value.code == "invalid_parameter"


def test_vault_scan_chunk_lines_range(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_scan(state, path="source.md", chunk_lines=999)
    assert e.value.code == "invalid_parameter"


def test_vault_read_rejects_out_of_range_start_line(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_read(state, path="source.md", full=False, range={"start_line": 100, "end_line": 120})
    assert e.value.code == "invalid_parameter"


@pytest.mark.parametrize(
    ("range_obj", "limits"),
    [
        ({"start_line": "abc", "end_line": 2}, None),
        ({"start_line": 1, "end_line": 2}, {"max_chars": -1}),
    ],
)
def test_vault_read_rejects_invalid_numeric_parameters(state, range_obj, limits) -> None:
    with pytest.raises(ToolError) as e:
        vault_read(state, path="source.md", full=False, range=range_obj, limits=limits)
    assert e.value.code == "invalid_parameter"


def test_daily_file_forbids_overwrite_and_replace(state) -> None:
    vault_create(state, path="daily/2026-02-06.md", content="first\n")
    with pytest.raises(ToolError) as e2:
        vault_replace(state, path="daily/2026-02-06.md", find="a", replace="b")
    assert e2.value.code == "forbidden"


def test_system_path_is_reserved(state) -> None:
    with pytest.raises(ToolError) as e1:
        vault_create(state, path=".system/manual_note.md", content="nope")
    assert e1.value.code == "forbidden"

    (state.config.vault_root / ".system").mkdir(parents=True, exist_ok=True)
    (state.config.vault_root / ".system" / "existing.md").write_text("x", encoding="utf-8")
    with pytest.raises(ToolError) as e3:
        vault_replace(state, path=".system/existing.md", find="x", replace="y")
    assert e3.value.code == "forbidden"


def test_vault_create_requires_non_empty_content(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_create(state, path="project-a/empty.md", content="")
    assert e.value.code == "invalid_parameter"


def test_vault_replace_rejects_non_integer_max_replacements(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_replace(state, path="source.md", find="line", replace="x", max_replacements="abc")
    assert e.value.code == "invalid_parameter"


def test_vault_scan_rejects_non_integer_cursor_start_line(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_scan(state, path="source.md", cursor={"start_line": "abc"})
    assert e.value.code == "invalid_parameter"


def test_vault_read_returns_cursor_without_next_actions(state) -> None:
    out = vault_read(state, path="source.md", full=False, range={"start_line": 1, "end_line": 2})
    assert out["applied_range"]["start_line"] == 1
    assert out["next_cursor"]["start_line"] == 3
    assert out["applied"]["full"] is False
    assert "next_actions" not in out


def test_vault_scan_accepts_start_line_without_cursor(state) -> None:
    out = vault_scan(state, path="source.md", start_line=3, chunk_lines=2)
    assert out["applied_range"] == {"start_line": 3, "end_line": 4}
    assert out["applied"]["chunk_lines"] == 2


def test_vault_scan_start_line_takes_precedence_over_cursor(state) -> None:
    out = vault_scan(state, path="source.md", start_line=4, cursor={"start_line": 1}, chunk_lines=1)
    assert out["applied_range"] == {"start_line": 4, "end_line": 4}


def test_execute_logs_vault_extension_fields(state, capsys) -> None:
    _execute(
        state,
        "vault_read",
        lambda *, path: {"truncated": True},
        path="notes.md",
    )
    _execute(
        state,
        "vault_create",
        lambda: {"written_path": "project-a/out.md", "written_bytes": 10},
    )
    _execute(
        state,
        "vault_scan",
        lambda *, path: {"applied_range": {"start_line": 1, "end_line": 2}, "eof": False, "truncated_reason": "chunk_end"},
        path="source.md",
    )
    _execute(
        state,
        "manual_scan",
        lambda *, manual_id, path: {
            "applied_range": {"start_line": 3, "end_line": 4},
            "eof": False,
            "truncated_reason": "chunk_end",
        },
        manual_id="m1",
        path="rules.md",
    )
    lines = capsys.readouterr().err.strip().splitlines()
    payloads = [json.loads(line) for line in lines[-4:]]

    assert payloads[0]["tool"] == "vault_read"
    assert payloads[0]["path"] == "notes.md"
    assert payloads[0]["truncated"] is True

    assert payloads[1]["tool"] == "vault_create"
    assert payloads[1]["written_bytes"] == 10

    assert payloads[2]["tool"] == "vault_scan"
    assert payloads[2]["path"] == "source.md"
    assert payloads[2]["truncated_reason"] == "chunk_end"

    assert payloads[3]["tool"] == "manual_scan"
    assert payloads[3]["manual_id"] == "m1"
    assert payloads[3]["path"] == "rules.md"
    assert payloads[3]["truncated_reason"] == "chunk_end"
