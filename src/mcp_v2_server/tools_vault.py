from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import ToolError, ensure
from .path_guard import (
    is_daily_path_under_root,
    is_system_path_under_root,
    normalize_relative_path,
    resolve_inside_root,
    validate_daily_filename,
)
from .state import AppState


def _parse_int_param(
    value: Any,
    *,
    name: str,
    default: int,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    raw = default if value is None else value
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        raise ToolError("invalid_parameter", f"{name} must be an integer")
    if min_value is not None and parsed < min_value:
        raise ToolError("invalid_parameter", f"{name} must be >= {min_value}")
    if max_value is not None and parsed > max_value:
        raise ToolError("invalid_parameter", f"{name} must be <= {max_value}")
    return parsed


def _resolve_max_chars(state: AppState, limits: dict[str, Any] | None) -> int:
    max_chars = _parse_int_param(
        (limits or {}).get("max_chars"),
        name="limits.max_chars",
        default=state.config.hard_max_chars,
        min_value=1,
    )
    return min(max_chars, state.config.hard_max_chars)


def _range_from_lines(total: int, range_obj: dict[str, Any] | None) -> tuple[int, int]:
    ensure(range_obj is not None, "invalid_parameter", "range is required when full=false")
    start = _parse_int_param(range_obj.get("start_line"), name="range.start_line", default=1, min_value=1)
    end = _parse_int_param(range_obj.get("end_line"), name="range.end_line", default=total, min_value=1)
    ensure(start >= 1 and end >= start, "invalid_parameter", "invalid range")
    ensure(start <= total, "invalid_parameter", "range.start_line out of range")
    return start, min(end, total)


def vault_read(
    state: AppState,
    path: str,
    full: bool | None = None,
    range: dict[str, Any] | None = None,
    limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    full = bool(full)
    normalized = normalize_relative_path(path)
    file_path = resolve_inside_root(state.config.vault_root, normalized, must_exist=True)
    ensure(file_path.is_file(), "not_found", "file not found", {"path": normalized})

    max_chars = _resolve_max_chars(state, limits)
    text = file_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    total = len(lines)

    if full:
        start_line, end_line = 1, max(1, total)
    else:
        start_line, end_line = _range_from_lines(max(1, total), range)

    selected = "\n".join(lines[start_line - 1 : end_line])
    truncated_reason = "none"
    if len(selected) > max_chars:
        selected = selected[:max_chars]
        truncated_reason = "hard_limit" if max_chars >= state.config.hard_max_chars else "max_chars"
    elif not full and end_line < total:
        truncated_reason = "range_end"

    return {
        "text": selected,
        "truncated": truncated_reason != "none",
        "returned_chars": len(selected),
        "applied_range": {"start_line": start_line, "end_line": end_line},
        "next_cursor": {"start_line": None if end_line >= total else end_line + 1},
        "truncated_reason": truncated_reason,
        "applied": {"full": full, "max_chars": max_chars},
    }


def _enforce_vault_policy_on_create(vault_root: Path, path: str) -> None:
    if is_system_path_under_root(vault_root, path):
        raise ToolError("forbidden", ".system path is reserved for system-managed files", {"path": path})
    if is_daily_path_under_root(vault_root, path):
        validate_daily_filename(path)


def _enforce_vault_policy_on_replace(vault_root: Path, path: str) -> None:
    if is_system_path_under_root(vault_root, path):
        raise ToolError("forbidden", ".system path is reserved for system-managed files", {"path": path})
    if is_daily_path_under_root(vault_root, path):
        raise ToolError("forbidden", "replace is forbidden under daily/")


def vault_create(state: AppState, path: str, content: str) -> dict[str, Any]:
    ensure(bool(content), "invalid_parameter", "content is required")
    normalized = normalize_relative_path(path)
    _enforce_vault_policy_on_create(state.config.vault_root, normalized)
    target = resolve_inside_root(state.config.vault_root, normalized, must_exist=False)
    ensure(not target.exists(), "conflict", "file already exists", {"path": normalized})
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"written_path": normalized, "written_bytes": len(content.encode("utf-8"))}


def vault_replace(state: AppState, path: str, find: str, replace: str, max_replacements: int | None = None) -> dict[str, Any]:
    ensure(bool(find), "invalid_parameter", "find is required")
    normalized = normalize_relative_path(path)
    _enforce_vault_policy_on_replace(state.config.vault_root, normalized)
    target = resolve_inside_root(state.config.vault_root, normalized, must_exist=True)
    ensure(target.is_file(), "not_found", "target is not a file", {"path": normalized})
    data = target.read_text(encoding="utf-8")
    max_count = _parse_int_param(max_replacements, name="max_replacements", default=1, min_value=0)
    total_hits = data.count(find)
    count = min(total_hits, max_count)
    new_data = data.replace(find, replace, max_count)
    target.write_text(new_data, encoding="utf-8")
    return {"written_path": normalized, "replacements": count}


def vault_scan(
    state: AppState,
    path: str,
    start_line: int | None = None,
    cursor: dict[str, Any] | None = None,
    chunk_lines: int | None = None,
    limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_relative_path(path)
    target = resolve_inside_root(state.config.vault_root, normalized, must_exist=True)
    text = target.read_text(encoding="utf-8")
    lines = text.splitlines()
    total = max(1, len(lines))

    applied_start_line = _parse_int_param(
        start_line if start_line is not None else (cursor or {}).get("start_line"),
        name="start_line",
        default=1,
        min_value=1,
    )
    applied_chunk = _parse_int_param(
        chunk_lines,
        name="chunk_lines",
        default=state.config.vault_scan_default_chunk_lines,
        min_value=1,
        max_value=state.config.vault_scan_max_chunk_lines,
    )
    ensure(
        1 <= applied_chunk <= state.config.vault_scan_max_chunk_lines,
        "invalid_parameter",
        "chunk_lines out of range",
    )
    ensure(1 <= applied_start_line <= total, "invalid_parameter", "start_line out of range")

    end_line = min(total, applied_start_line + applied_chunk - 1)
    chunk_text = "\n".join(lines[applied_start_line - 1 : end_line])
    max_chars = _resolve_max_chars(state, limits)

    truncated_reason = "none"
    if len(chunk_text) > max_chars:
        chunk_text = chunk_text[:max_chars]
        truncated_reason = "hard_limit" if max_chars >= state.config.hard_max_chars else "max_chars"
    elif end_line < total:
        truncated_reason = "chunk_end"
    eof = end_line >= total

    return {
        "text": chunk_text,
        "applied_range": {"start_line": applied_start_line, "end_line": end_line},
        "next_cursor": {"start_line": None if eof else end_line + 1},
        "eof": eof,
        "truncated": truncated_reason != "none",
        "truncated_reason": truncated_reason,
        "applied": {"chunk_lines": applied_chunk, "max_chars": max_chars},
    }
