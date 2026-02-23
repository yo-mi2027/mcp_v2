from __future__ import annotations

import pytest

from mcp_v2_server.app import _execute
from mcp_v2_server.errors import ToolError
from mcp_v2_server.tools_vault import (
    vault_create,
    vault_ls,
    vault_read,
    vault_replace,
    vault_scan,
)


def test_vault_read_requires_range_when_not_full(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_read(state, path="source.md", full=False, range=None)
    assert e.value.code == "invalid_parameter"


def test_vault_scan_uses_fixed_max_chars(state) -> None:
    out = vault_scan(state, path="source.md")
    assert out["applied"]["max_chars"] == 12000


def test_vault_read_rejects_out_of_range_start_line(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_read(state, path="source.md", full=False, range={"start_line": 100, "end_line": 120})
    assert e.value.code == "invalid_parameter"


def test_vault_read_rejects_invalid_range_numbers(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_read(state, path="source.md", full=False, range={"start_line": "abc", "end_line": 2})
    assert e.value.code == "invalid_parameter"


def test_vault_read_rejects_non_boolean_full(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_read(state, path="source.md", full="false", range={"start_line": 1, "end_line": 2})
    assert e.value.code == "invalid_parameter"


def test_vault_read_rejects_non_string_path(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_read(state, path=123, full=False, range={"start_line": 1, "end_line": 2})  # type: ignore[arg-type]
    assert e.value.code == "invalid_path"


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


def test_vault_create_rejects_non_string_content(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_create(state, path="project-a/out.md", content=123)  # type: ignore[arg-type]
    assert e.value.code == "invalid_parameter"


def test_vault_replace_rejects_non_integer_max_replacements(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_replace(state, path="source.md", find="line", replace="x", max_replacements="abc")
    assert e.value.code == "invalid_parameter"


def test_vault_replace_rejects_boolean_max_replacements(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_replace(state, path="source.md", find="line", replace="x", max_replacements=True)
    assert e.value.code == "invalid_parameter"


def test_vault_replace_rejects_non_string_find(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_replace(state, path="source.md", find=123, replace="x")  # type: ignore[arg-type]
    assert e.value.code == "invalid_parameter"


def test_vault_scan_rejects_non_integer_cursor_start_line(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_scan(state, path="source.md", cursor={"start_line": "abc"})
    assert e.value.code == "invalid_parameter"


def test_vault_read_returns_cursor_without_next_actions(state) -> None:
    out = vault_read(state, path="source.md", full=False, range={"start_line": 1, "end_line": 2})
    assert out["applied_range"]["start_line"] == 1
    assert out["next_cursor"]["char_offset"] == 12
    assert out["applied"]["full"] is False
    assert "next_actions" not in out


def test_vault_scan_accepts_start_line_without_cursor(state) -> None:
    out = vault_scan(state, path="source.md", start_line=3)
    assert out["applied_range"] == {"start_line": 3, "end_line": 5}


def test_vault_scan_start_line_takes_precedence_over_cursor(state) -> None:
    out = vault_scan(state, path="source.md", start_line=4, cursor={"start_line": 1})
    assert out["applied_range"] == {"start_line": 4, "end_line": 5}


def test_vault_scan_start_line_takes_precedence_over_cursor_char_offset(state) -> None:
    out = vault_scan(state, path="source.md", start_line=4, cursor={"char_offset": 0, "start_line": 1})
    assert out["applied_range"]["start_line"] == 4


def test_vault_scan_paginates_with_char_offset_cursor(state) -> None:
    text = ("b" * 12050) + "\nend\n"
    (state.config.vault_root / "long.md").write_text(text, encoding="utf-8")
    first = vault_scan(state, path="long.md")
    assert first["truncated_reason"] == "max_chars"
    assert first["next_cursor"]["char_offset"] == 12000

    second = vault_scan(state, path="long.md", cursor=first["next_cursor"])
    assert second["eof"] is True
    assert second["next_cursor"]["char_offset"] is None
    assert len(first["text"]) + len(second["text"]) == len(text)


def test_vault_scan_rejects_non_object_cursor(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_scan(state, path="source.md", cursor=1)  # type: ignore[arg-type]
    assert e.value.code == "invalid_parameter"


def test_vault_ls_lists_root_when_path_omitted(state) -> None:
    (state.config.vault_root / "daily").mkdir(parents=True, exist_ok=True)
    (state.config.vault_root / ".system").mkdir(parents=True, exist_ok=True)
    out = vault_ls(state)
    assert out["base_path"] is None
    assert out["items"] == [
        {"name": ".system", "path": ".system", "kind": "dir"},
        {"name": "daily", "path": "daily", "kind": "dir"},
        {"name": "notes.md", "path": "notes.md", "kind": "file"},
        {"name": "report.md", "path": "report.md", "kind": "file"},
        {"name": "source.md", "path": "source.md", "kind": "file"},
    ]


def test_vault_ls_lists_directory_when_path_is_provided(state) -> None:
    (state.config.vault_root / "project-a").mkdir(parents=True, exist_ok=True)
    (state.config.vault_root / "project-a" / "todo.md").write_text("x", encoding="utf-8")

    out = vault_ls(state, path="project-a")
    assert out["base_path"] == "project-a"
    assert out["items"] == [{"name": "todo.md", "path": "project-a/todo.md", "kind": "file"}]


def test_vault_ls_filters_os_noise_files(state) -> None:
    (state.config.vault_root / ".system").mkdir(parents=True, exist_ok=True)
    (state.config.vault_root / ".DS_Store").write_text("x", encoding="utf-8")
    (state.config.vault_root / "Thumbs.db").write_text("x", encoding="utf-8")
    (state.config.vault_root / "._notes.md").write_text("x", encoding="utf-8")
    out = vault_ls(state)
    names = {item["name"] for item in out["items"]}
    assert ".DS_Store" not in names
    assert "Thumbs.db" not in names
    assert "._notes.md" not in names
    assert ".system" in names


def test_vault_ls_rejects_file_path(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_ls(state, path="source.md")
    assert e.value.code == "not_found"


def test_vault_ls_rejects_non_string_path(state) -> None:
    with pytest.raises(ToolError) as e:
        vault_ls(state, path=123)  # type: ignore[arg-type]
    assert e.value.code == "invalid_path"


def test_execute_does_not_log_successful_calls(state, capsys) -> None:
    _execute(
        state,
        "vault_ls",
        lambda *, path: {"items": [{"name": "x", "path": "x", "kind": "file"}]},
        path=None,
    )
    _execute(
        state,
        "manual_ls",
        lambda: {"id": "manuals", "items": [{"id": "m1", "name": "m1", "kind": "dir"}]},
    )
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
    assert capsys.readouterr().err == ""


def test_execute_requires_manual_ls_before_manual_tools(state) -> None:
    out = _execute(
        state,
        "manual_scan",
        lambda *, manual_id, path: {"manual_id": manual_id, "path": path},
        manual_id="m1",
        path="rules.md",
    )
    assert out["code"] == "invalid_parameter"
    assert out["details"] == {"required_first_call": "manual_ls"}

    _execute(state, "manual_ls", lambda: {"id": "manuals", "items": []})
    ok = _execute(
        state,
        "manual_scan",
        lambda *, manual_id, path: {"manual_id": manual_id, "path": path},
        manual_id="m1",
        path="rules.md",
    )
    assert ok["manual_id"] == "m1"


def test_execute_allows_vault_tools_without_vault_ls(state) -> None:
    out = _execute(
        state,
        "vault_read",
        lambda *, path: {"path": path},
        path="source.md",
    )
    assert out["path"] == "source.md"
