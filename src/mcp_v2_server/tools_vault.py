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

SCAN_MAX_CHARS = 9000


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


def _resolve_scan_max_chars(limits: dict[str, Any] | None) -> int:
    if limits and limits.get("max_chars") is not None:
        raise ToolError("invalid_parameter", "limits.max_chars is fixed and cannot be changed")
    return SCAN_MAX_CHARS


def _char_offset_from_line(text: str, line_no: int) -> int:
    if line_no <= 1:
        return 0
    offset = 0
    for _ in range(1, line_no):
        next_break = text.find("\n", offset)
        if next_break < 0:
            raise ToolError("invalid_parameter", "start_line out of range")
        offset = next_break + 1
    return offset


def _line_from_char_offset(text: str, offset: int) -> int:
    if not text:
        return 1
    bounded = min(max(0, offset), len(text))
    return text.count("\n", 0, bounded) + 1


def _char_offset_after_line(text: str, line_no: int) -> int:
    start = _char_offset_from_line(text, line_no)
    next_break = text.find("\n", start)
    if next_break < 0:
        return len(text)
    return next_break + 1


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

    next_cursor = None if end_line >= total else _char_offset_after_line(text, end_line)

    return {
        "text": selected,
        "truncated": truncated_reason != "none",
        "returned_chars": len(selected),
        "applied_range": {"start_line": start_line, "end_line": end_line},
        "next_cursor": {"char_offset": next_cursor},
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
    limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_relative_path(path)
    target = resolve_inside_root(state.config.vault_root, normalized, must_exist=True)
    text = target.read_text(encoding="utf-8")
    max_chars = _resolve_scan_max_chars(limits)
    legacy_start_line = start_line if start_line is not None else (cursor or {}).get("start_line")

    if (cursor or {}).get("char_offset") is not None:
        applied_start_offset = _parse_int_param(
            (cursor or {}).get("char_offset"),
            name="cursor.char_offset",
            default=0,
            min_value=0,
        )
    elif legacy_start_line is not None:
        parsed_start_line = _parse_int_param(
            legacy_start_line,
            name="start_line",
            default=1,
            min_value=1,
        )
        applied_start_offset = _char_offset_from_line(text, parsed_start_line)
    else:
        applied_start_offset = 0

    ensure(applied_start_offset <= len(text), "invalid_parameter", "cursor.char_offset out of range")
    end_offset = min(len(text), applied_start_offset + max_chars)
    chunk_text = text[applied_start_offset:end_offset]
    start_line_no = _line_from_char_offset(text, applied_start_offset)
    if end_offset <= applied_start_offset:
        end_line_no = start_line_no
    else:
        end_line_no = _line_from_char_offset(text, end_offset - 1)

    truncated_reason = "none" if end_offset >= len(text) else "max_chars"
    eof = end_offset >= len(text)

    return {
        "text": chunk_text,
        "applied_range": {"start_line": start_line_no, "end_line": end_line_no},
        "next_cursor": {"char_offset": None if eof else end_offset},
        "eof": eof,
        "truncated": truncated_reason != "none",
        "truncated_reason": truncated_reason,
        "applied": {"max_chars": max_chars},
    }
