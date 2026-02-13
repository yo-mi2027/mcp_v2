from __future__ import annotations

import hashlib
import time
import base64
from collections import Counter
from pathlib import Path
from typing import Any

from .errors import ToolError, ensure
from .manual_index import (
    MdNode,
    discover_manual_ids,
    list_manual_files,
    parse_markdown_toc,
)
from .normalization import loose_contains, normalize_text, split_terms
from .path_guard import normalize_relative_path, resolve_inside_root
from .sparse_index import bm25_scores
from .state import AppState

EXCEPTION_WORDS = [
    "注意",
    "留意",
    "対象外",
    "除外",
    "不適用",
    "支払われない",
]
NORMALIZED_EXCEPTION_WORDS = [normalize_text(w) for w in EXCEPTION_WORDS]

SYNONYMS = {
    "対象外": ["除外", "不適用"],
    "手順": ["フロー", "手続き"],
}
FACET_ORDER = ["definition", "procedure", "eligibility", "exceptions", "compare", "unknown"]
FACET_HINTS_RAW: dict[str, list[str]] = {
    "definition": ["定義", "とは", "意味", "概要", "基本"],
    "procedure": ["手順", "フロー", "手続き", "ステップ", "方法"],
    "eligibility": ["条件", "要件", "対象", "可否", "適用"],
    "exceptions": ["例外", "対象外", "除外", "不適用", "ただし", "但し"],
    "compare": ["比較", "違い", "差分", "優先", "どちら"],
}
FACET_HINTS = {
    key: [normalize_text(word) for word in words]
    for key, words in FACET_HINTS_RAW.items()
}
SCAN_MAX_CHARS = 12000
READ_MAX_SECTIONS = 20
READ_MAX_CHARS = 12000
SCORE_WEIGHT_NORMALIZED = 1.0
SCORE_WEIGHT_LOOSE = 0.7
SCORE_WEIGHT_EXCEPTIONS = 0.2
SCORE_WEIGHT_HEADING_FOCUS = 0.5
SCORE_WEIGHT_BM25 = 1.0
BM25_K1 = 1.2
BM25_B = 0.75


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


def _parse_bool_param(value: Any, *, name: str, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ToolError("invalid_parameter", f"{name} must be boolean")


def _cacheable_query(query: str) -> str:
    normalized = normalize_text(query)
    if normalized:
        return normalized
    return query.strip().lower()


def _manual_find_scope_key(
    *,
    manual_id: str | None,
    expand_scope: bool,
    max_candidates: int,
    budget_time_ms: int,
) -> str:
    scope_manual_id = manual_id or "*"
    return (
        f"manual_id={scope_manual_id}"
        f"|expand_scope={int(expand_scope)}"
        f"|max_candidates={max_candidates}"
        f"|budget_time_ms={budget_time_ms}"
    )


def _manuals_fingerprint(state: AppState, manual_ids: list[str]) -> str:
    digest = hashlib.sha256()
    for mid in sorted(set(manual_ids)):
        digest.update(mid.encode("utf-8"))
        digest.update(b"\0")
        manual_root = state.config.manuals_root / mid
        rows = list_manual_files(state.config.manuals_root, manual_id=mid)
        for row in rows:
            digest.update(row.path.encode("utf-8"))
            digest.update(b"\0")
            full_path = manual_root / row.path
            try:
                stat = full_path.stat()
                digest.update(str(stat.st_mtime_ns).encode("ascii"))
                digest.update(b":")
                digest.update(str(stat.st_size).encode("ascii"))
            except Exception:
                digest.update(b"missing")
            digest.update(b"\0")
    return digest.hexdigest()


def _out_from_trace_payload(
    *,
    trace_id: str,
    trace_payload: dict[str, Any],
    include_claim_graph: bool,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "trace_id": trace_id,
        "summary": trace_payload.get("summary") or {},
        "next_actions": trace_payload.get("next_actions") or [],
    }
    if include_claim_graph:
        out["claim_graph"] = (trace_payload.get("claim_graph") or {})
    return out


def _cached_trace_payload_and_source_latency(value: Any) -> tuple[dict[str, Any] | None, int | None]:
    if not isinstance(value, dict):
        return None, None
    trace_payload = value.get("trace_payload")
    if not isinstance(trace_payload, dict):
        return None, None
    source_latency_raw = value.get("source_latency_ms")
    source_latency_ms: int | None = None
    if isinstance(source_latency_raw, (int, float)) and not isinstance(source_latency_raw, bool):
        source_latency_ms = max(0, int(source_latency_raw))
    return trace_payload, source_latency_ms


def _cached_summary_is_acceptable(state: AppState, summary: dict[str, Any]) -> bool:
    try:
        gap_count = int(summary.get("gap_count", 0))
    except (TypeError, ValueError):
        gap_count = 0
    try:
        conflict_count = int(summary.get("conflict_count", 0))
    except (TypeError, ValueError):
        conflict_count = 0
    if state.config.sem_cache_max_summary_gap >= 0 and gap_count > state.config.sem_cache_max_summary_gap:
        return False
    if state.config.sem_cache_max_summary_conflict >= 0 and conflict_count > state.config.sem_cache_max_summary_conflict:
        return False
    return True


def _record_manual_find_stats(
    state: AppState,
    *,
    query: str,
    summary: dict[str, Any],
    scanned_files: int,
    scanned_nodes: int,
    candidates_count: int,
    warnings: int,
    max_stage_applied: int,
    scope_expanded: bool,
    cutoff_reason: str | None,
    unscanned_sections_count: int,
    candidate_low_threshold: int,
    file_bias_threshold: float,
    sem_cache_hit: bool,
    sem_cache_mode: str,
    sem_cache_score: float | None,
    latency_saved_ms: int | None,
    scoring_mode: str = "heuristic",
    index_rebuilt: bool = False,
    index_docs: int | None = None,
) -> None:
    chars_in = len(query)
    chars_out = len(str(summary))
    added_est_tokens = chars_out // 4
    marginal_gain = (candidates_count / added_est_tokens) if added_est_tokens > 0 else None
    state.adaptive_stats.append(
        {
            "ts": int(time.time() * 1000),
            "query_hash": hashlib.sha256(query.encode("utf-8")).hexdigest()[:16],
            "scanned_files": scanned_files,
            "scanned_nodes": scanned_nodes,
            "candidates": candidates_count,
            "warnings": warnings,
            "max_stage_applied": max_stage_applied,
            "scope_expanded": scope_expanded,
            "cutoff_reason": cutoff_reason,
            "unscanned_sections_count": unscanned_sections_count,
            "est_tokens": (chars_in + chars_out + 3) // 4,
            "est_tokens_in": (chars_in + 3) // 4,
            "est_tokens_out": (chars_out + 3) // 4,
            "added_evidence_count": candidates_count,
            "added_est_tokens": added_est_tokens,
            "marginal_gain": round(marginal_gain, 4) if marginal_gain is not None else None,
            "candidate_low_threshold": candidate_low_threshold,
            "file_bias_threshold": file_bias_threshold,
            "sem_cache_hit": sem_cache_hit,
            "sem_cache_mode": sem_cache_mode,
            "sem_cache_score": round(sem_cache_score, 4) if sem_cache_score is not None else None,
            "latency_saved_ms": latency_saved_ms,
            "scoring_mode": scoring_mode,
            "index_rebuilt": index_rebuilt,
            "index_docs": index_docs,
        }
    )


def _trim_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _encode_node_segment(value: str) -> str:
    raw = value.encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_node_segment(value: str) -> str:
    padded = value + ("=" * ((4 - (len(value) % 4)) % 4))
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except Exception:
        raise ToolError("invalid_parameter", "invalid id")


def _manual_root_id(manual_id: str) -> str:
    return manual_id


def _manual_dir_id(manual_id: str, relative_dir: str) -> str:
    return f"dir::{manual_id}::{_encode_node_segment(relative_dir)}"


def _manual_file_id(manual_id: str, relative_path: str) -> str:
    return f"file::{manual_id}::{_encode_node_segment(relative_path)}"


def _parse_manual_ls_id(id_value: str) -> tuple[str, str, str | None]:
    if id_value == "manuals":
        return "manuals", "", None
    if id_value.startswith("dir::"):
        parts = id_value.split("::", 2)
        ensure(len(parts) == 3, "invalid_parameter", "invalid id")
        head, manual_id, encoded = parts
        ensure(head == "dir" and bool(manual_id) and bool(encoded), "invalid_parameter", "invalid id")
        relative_dir = _decode_node_segment(encoded)
        return "dir", manual_id, normalize_relative_path(relative_dir)
    if id_value.startswith("file::"):
        parts = id_value.split("::", 2)
        ensure(len(parts) == 3, "invalid_parameter", "invalid id")
        head, manual_id, encoded = parts
        ensure(head == "file" and bool(manual_id) and bool(encoded), "invalid_parameter", "invalid id")
        relative_path = _decode_node_segment(encoded)
        return "file", manual_id, normalize_relative_path(relative_path)
    # Plain manual id (ex: "m1") for top-level manual nodes.
    return "manual", id_value, ""


def manual_ls(state: AppState, id: str | None = None) -> dict[str, Any]:
    applied_id = id or "manuals"
    node_kind, manual_id, relative = _parse_manual_ls_id(applied_id)

    if node_kind == "manuals":
        manual_ids = discover_manual_ids(state.config.manuals_root)
        return {
            "id": "manuals",
            "items": [
                {"id": _manual_root_id(mid), "name": mid, "kind": "dir"}
                for mid in manual_ids
            ],
        }

    ensure(_manual_exists(state.config.manuals_root, manual_id), "not_found", "manual_id not found", {"manual_id": manual_id})
    manual_root = state.config.manuals_root / manual_id

    if node_kind == "file":
        raise ToolError("invalid_parameter", "file id cannot be expanded")

    base_dir = manual_root if not relative else resolve_inside_root(manual_root, relative, must_exist=True)
    ensure(base_dir.is_dir(), "not_found", "directory not found")

    items: list[dict[str, Any]] = []
    for child in sorted(base_dir.iterdir(), key=lambda p: p.name):
        if child.is_symlink():
            continue
        child_name = child.name
        child_rel = child_name if not relative else f"{relative}/{child_name}"
        if child.is_dir():
            items.append(
                {
                    "id": _manual_dir_id(manual_id, child_rel),
                    "name": child_name,
                    "kind": "dir",
                }
            )
            continue
        if not child.is_file():
            continue
        suffix = child.suffix.casefold()
        if suffix not in {".md", ".json"}:
            continue
        items.append(
            {
                "id": _manual_file_id(manual_id, child_rel),
                "name": child_name,
                "kind": "file",
                "path": child_rel,
                "file_type": suffix[1:],
            }
        )

    return {"id": applied_id, "items": items}


def _manual_exists(root: Path, manual_id: str) -> bool:
    return (root / manual_id).is_dir()


def manual_toc(state: AppState, manual_id: str) -> dict[str, Any]:
    ensure(_manual_exists(state.config.manuals_root, manual_id), "not_found", "manual_id not found", {"manual_id": manual_id})
    items: list[dict[str, Any]] = []
    files = list_manual_files(state.config.manuals_root, manual_id=manual_id)
    for row in files:
        file_path = resolve_inside_root(state.config.manuals_root / manual_id, row.path, must_exist=True)
        text = file_path.read_text(encoding="utf-8")
        headings: list[dict[str, Any]] = []
        if row.file_type == "md":
            for node in parse_markdown_toc(row.path, text):
                headings.append({"title": node.title, "line_start": node.line_start})
        else:
            headings.append({"title": Path(row.path).name, "line_start": 1})
        items.append({"path": row.path, "headings": headings})
    items.sort(key=lambda x: x["path"])
    return {"items": items}


def _find_md_node(nodes: list[MdNode], start_line: int | None) -> MdNode:
    if start_line is None:
        return nodes[0]
    for node in nodes:
        if node.line_start == start_line:
            return node
    return nodes[0]


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


def _normalize_scan_cursor(cursor: Any) -> dict[str, Any]:
    if cursor is None:
        return {}
    if isinstance(cursor, dict):
        return cursor
    if isinstance(cursor, (int, str)):
        return {
            "char_offset": _parse_int_param(
                cursor,
                name="cursor",
                default=0,
                min_value=0,
            )
        }
    raise ToolError(
        "invalid_parameter",
        "cursor must be an object (char_offset/start_line) or an integer/string char_offset",
    )


def manual_read(
    state: AppState,
    ref: dict[str, Any],
    scope: str | None = None,
    allow_file: bool | None = None,
    expand: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ref = dict(ref)
    ref.pop("target", None)
    manual_id = ref.get("manual_id")
    ensure(bool(manual_id), "invalid_parameter", "ref.manual_id is required")
    ensure(_manual_exists(state.config.manuals_root, manual_id), "not_found", "manual_id not found", {"manual_id": manual_id})
    relative_path = normalize_relative_path(str(ref.get("path", "")))
    full_path = resolve_inside_root(state.config.manuals_root / manual_id, relative_path, must_exist=True)
    ensure(full_path.exists() and full_path.is_file(), "not_found", "manual file not found", {"path": relative_path})

    suffix = full_path.suffix.casefold()
    text = full_path.read_text(encoding="utf-8")
    default_scope = "file" if suffix == ".json" else "section"
    applied_scope = scope or default_scope
    ensure(applied_scope in {"snippet", "section", "sections", "file"}, "invalid_parameter", "invalid scope")
    max_sections, max_chars = READ_MAX_SECTIONS, READ_MAX_CHARS
    applied_allow_file = _parse_bool_param(allow_file, name="allow_file", default=False)
    truncated = False
    output = ""
    applied_mode = "read"

    if suffix == ".json":
        if applied_scope in {"section", "sections"}:
            raise ToolError("invalid_scope", "json does not support section scopes")
        if applied_scope != "file":
            applied_scope = "file"
        output, truncated = _trim_text(text, max_chars)
    else:
        lines = text.splitlines()
        nodes = parse_markdown_toc(relative_path, text)
        target = _find_md_node(nodes, ref.get("start_line"))
        if applied_scope == "file":
            if not state.config.allow_file_scope or not applied_allow_file:
                raise ToolError("forbidden", "md file scope requires ALLOW_FILE_SCOPE=true and allow_file=true")
            output = text
        elif applied_scope == "section":
            key = f"{manual_id}:{relative_path}"
            section_start = target.line_start
            section_end = target.line_end
            progress = state.read_progress.get(key)
            overlap = bool(
                progress
                and progress.get("last_section_start") is not None
                and progress.get("last_section_end") is not None
                and not (
                    section_end < int(progress["last_section_start"] or 1)
                    or section_start > int(progress["last_section_end"] or 1)
                )
            )
            if overlap:
                fallback_start = int(progress.get("next_scan_start") or (section_end + 1))
                if fallback_start <= len(lines):
                    scan = manual_scan(
                        state,
                        manual_id=manual_id,
                        path=relative_path,
                        start_line=fallback_start,
                    )
                    output = str(scan.get("text") or "")
                    truncated = bool(scan.get("truncated"))
                    applied_mode = "scan_fallback"
                    applied_range = scan.get("applied_range") or {}
                    eof = bool(scan.get("eof"))
                    next_scan_start = (len(lines) + 1) if eof else (int(applied_range.get("end_line") or section_end) + 1)
                    state.read_progress[key] = {
                        "last_section_start": section_start,
                        "last_section_end": section_end,
                        "next_scan_start": int(next_scan_start),
                    }
                else:
                    output = "\n".join(lines[target.line_start - 1 : target.line_end])
            else:
                output = "\n".join(lines[target.line_start - 1 : target.line_end])
                state.read_progress[key] = {
                    "last_section_start": section_start,
                    "last_section_end": section_end,
                    "next_scan_start": section_end + 1,
                }
        elif applied_scope == "sections":
            selected: list[str] = []
            start_idx = next((i for i, n in enumerate(nodes) if n.node_id == target.node_id), 0)
            for node in nodes[start_idx : start_idx + max_sections]:
                selected.append("\n".join(lines[node.line_start - 1 : node.line_end]))
            output = "\n\n".join(selected)
        else:
            # snippet
            line_no = _parse_int_param(ref.get("start_line"), name="ref.start_line", default=1, min_value=1)
            before_chars = 240
            after_chars = 240
            if expand:
                before_chars = _parse_int_param(
                    expand.get("before_chars"),
                    name="expand.before_chars",
                    default=before_chars,
                    min_value=0,
                )
                after_chars = _parse_int_param(
                    expand.get("after_chars"),
                    name="expand.after_chars",
                    default=after_chars,
                    min_value=0,
                )
            line_no = min(max(1, line_no), max(1, len(lines)))
            char_cursor = 0
            for i, line in enumerate(lines, start=1):
                if i >= line_no:
                    break
                char_cursor += len(line) + 1
            start_char = max(0, char_cursor - before_chars)
            end_char = min(len(text), char_cursor + len(lines[line_no - 1]) + after_chars)
            output = text[start_char:end_char]
            applied_scope = "snippet"

        if applied_mode == "read":
            output, truncated = _trim_text(output, max_chars)

    return {
        "text": output,
        "truncated": truncated,
        "applied": {
            "scope": applied_scope,
            "max_sections": max_sections if applied_scope in {"sections", "file"} else None,
            "max_chars": max_chars,
            "mode": applied_mode,
        },
    }


def manual_scan(
    state: AppState,
    manual_id: str,
    path: str,
    start_line: int | None = None,
    cursor: dict[str, Any] | int | str | None = None,
) -> dict[str, Any]:
    ensure(_manual_exists(state.config.manuals_root, manual_id), "not_found", "manual_id not found", {"manual_id": manual_id})
    relative_path = normalize_relative_path(path)
    full_path = resolve_inside_root(state.config.manuals_root / manual_id, relative_path, must_exist=True)
    ensure(full_path.exists() and full_path.is_file(), "not_found", "manual file not found", {"path": relative_path})

    text = full_path.read_text(encoding="utf-8")
    max_chars = SCAN_MAX_CHARS
    normalized_cursor = _normalize_scan_cursor(cursor)
    if start_line is not None:
        parsed_start_line = _parse_int_param(
            start_line,
            name="start_line",
            default=1,
            min_value=1,
        )
        applied_start_offset = _char_offset_from_line(text, parsed_start_line)
    elif normalized_cursor.get("char_offset") is not None:
        applied_start_offset = _parse_int_param(
            normalized_cursor.get("char_offset"),
            name="cursor.char_offset",
            default=0,
            min_value=0,
        )
    elif normalized_cursor.get("start_line") is not None:
        parsed_start_line = _parse_int_param(
            normalized_cursor.get("start_line"),
            name="cursor.start_line",
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
        "manual_id": manual_id,
        "path": relative_path,
        "text": chunk_text,
        "applied_range": {"start_line": start_line_no, "end_line": end_line_no},
        "next_cursor": {"char_offset": None if eof else end_offset},
        "eof": eof,
        "truncated": truncated_reason != "none",
        "truncated_reason": truncated_reason,
        "applied": {"max_chars": max_chars},
    }


def _candidate_key(item: dict[str, Any]) -> str:
    ref = item["ref"]
    return f'{ref["manual_id"]}|{ref["path"]}|{ref.get("start_line") or 1}'


def _score_from_signals(signals: set[str]) -> float:
    score = 0.0
    if "normalized" in signals:
        score += SCORE_WEIGHT_NORMALIZED
    if "loose" in signals:
        score += SCORE_WEIGHT_LOOSE
    if "exceptions" in signals:
        score += SCORE_WEIGHT_EXCEPTIONS
    return round(score, 4)


def _focus_group_key(item: dict[str, Any]) -> str:
    ref = item.get("ref") or {}
    heading_id = str(ref.get("heading_id") or "").strip()
    if heading_id:
        return f"heading:{heading_id}"
    page_no = ref.get("page_no")
    if page_no is not None and str(page_no).strip():
        return f'page:{ref.get("manual_id")}:{ref.get("path")}:{page_no}'
    return f'path:{ref.get("manual_id")}:{ref.get("path")}'


def _apply_heading_focus(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candidates:
        return candidates

    group_score: dict[str, float] = {}
    group_count: dict[str, int] = {}
    for item in candidates:
        key = _focus_group_key(item)
        group_score[key] = group_score.get(key, 0.0) + float(item.get("score") or 0.0)
        group_count[key] = group_count.get(key, 0) + 1

    ordered_groups = sorted(
        group_score.keys(),
        key=lambda key: (-group_score[key], -group_count[key], key),
    )
    if not ordered_groups:
        return candidates
    if len(ordered_groups) <= 2:
        focus_group_count = 1
    else:
        focus_group_count = min(3, len(ordered_groups) - 1)
    focused_groups = set(ordered_groups[:focus_group_count])

    updated: list[dict[str, Any]] = []
    for item in candidates:
        key = _focus_group_key(item)
        ref = dict(item.get("ref") or {})
        signals = set(item.get("signals") or [])
        ref_signals = set(ref.get("signals") or [])
        score = float(item.get("score") or 0.0)
        if key in focused_groups:
            signals.add("heading_focus")
            ref_signals.add("heading_focus")
            score += SCORE_WEIGHT_HEADING_FOCUS
        item["signals"] = sorted(signals)
        ref["signals"] = sorted(ref_signals if ref_signals else signals)
        item["ref"] = ref
        item["score"] = round(score, 4)
        updated.append(item)
    return sorted(updated, key=lambda x: x["score"], reverse=True)


def _query_prefers_exceptions(query: str) -> bool:
    query_norm = normalize_text(query)
    return any(hint in query_norm for hint in FACET_HINTS.get("exceptions", []))


def _infer_claim_facets(query: str, candidates: list[dict[str, Any]]) -> list[str]:
    query_norm = normalize_text(query)
    ordered: list[str] = []

    def add(facet: str) -> None:
        if facet in FACET_ORDER and facet not in ordered:
            ordered.append(facet)

    for facet, hints in FACET_HINTS.items():
        if any(hint in query_norm for hint in hints):
            add(facet)

    if any("exceptions" in (item.get("signals") or []) for item in candidates):
        add("exceptions")

    if not ordered:
        add("unknown")

    return [facet for facet in FACET_ORDER if facet in ordered] or ["unknown"]


def _relation_for_facet(facet: str, candidate: dict[str, Any]) -> tuple[str, float]:
    signals = set(candidate.get("signals") or [])
    normalized_title = normalize_text(str((candidate.get("ref") or {}).get("title") or ""))
    hint_hit = any(word in normalized_title for word in FACET_HINTS.get(facet, []))
    strong_hit = "normalized" in signals or "loose" in signals
    heading_hit = "heading" in signals

    if facet == "exceptions":
        if "exceptions" in signals:
            return "supports", 0.82
        if strong_hit and not hint_hit:
            return "contradicts", 0.70
        if heading_hit:
            return "requires_followup", 0.55
        return "requires_followup", 0.50

    if facet == "eligibility" and "exceptions" in signals and not hint_hit:
        return "contradicts", 0.68
    if strong_hit and hint_hit:
        return "supports", 0.82
    if strong_hit or hint_hit:
        return "supports", 0.72
    if heading_hit:
        return "requires_followup", 0.55
    return "requires_followup", 0.50


def _build_claim_graph(
    *,
    query: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    facets = _infer_claim_facets(query, candidates)
    claims: list[dict[str, Any]] = []
    claim_by_facet: dict[str, str] = {}
    for idx, facet in enumerate(facets, start=1):
        claim_id = f"claim:{facet}:{idx}"
        claim_by_facet[facet] = claim_id
        claims.append(
            {
                "claim_id": claim_id,
                "facet": facet,
                "text": f"{query} [{facet}]",
                "status": "unresolved",
                "confidence": 0.0,
            }
        )

    evidences: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    claim_stats: dict[str, dict[str, float]] = {
        claim["claim_id"]: {"supports": 0, "contradicts": 0, "followups": 0, "support_score_sum": 0.0}
        for claim in claims
    }

    for idx, candidate in enumerate(candidates, start=1):
        ref = candidate["ref"]
        evidence_id = f"ev:{idx}"
        score = float(candidate.get("score") or 0.0)
        signals = sorted(set(candidate.get("signals") or []))
        digest_input = f'{ref.get("manual_id")}|{ref.get("path")}|{ref.get("start_line") or 1}|{",".join(signals)}|{score}'
        evidences.append(
            {
                "evidence_id": evidence_id,
                "ref": {
                    "target": "manual",
                    "manual_id": ref.get("manual_id"),
                    "path": ref.get("path"),
                    "start_line": ref.get("start_line") or 1,
                },
                "signals": signals,
                "score": round(score, 4),
                "snippet_digest": hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:16],
            }
        )

        for facet in facets:
            claim_id = claim_by_facet[facet]
            relation, edge_confidence = _relation_for_facet(facet, candidate)
            edges.append(
                {
                    "from_claim_id": claim_id,
                    "to_evidence_id": evidence_id,
                    "relation": relation,
                    "confidence": round(edge_confidence, 2),
                }
            )
            stats = claim_stats[claim_id]
            if relation == "supports":
                stats["supports"] += 1
                stats["support_score_sum"] += ((score + edge_confidence) / 2.0)
            elif relation == "contradicts":
                stats["contradicts"] += 1
            else:
                stats["followups"] += 1

    for claim in claims:
        stats = claim_stats[claim["claim_id"]]
        supports = int(stats["supports"])
        contradicts = int(stats["contradicts"])
        followups = int(stats["followups"])
        if supports > 0 and contradicts > 0:
            status = "conflicted"
        elif supports > 0:
            status = "supported"
        elif contradicts > 0 or followups > 0:
            status = "unresolved"
        else:
            status = "unresolved"
        confidence = (stats["support_score_sum"] / supports) if supports > 0 else 0.0
        claim["status"] = status
        claim["confidence"] = round(min(1.0, confidence), 4)

    facet_rows: list[dict[str, Any]] = []
    for facet in facets:
        rows = [claim for claim in claims if claim["facet"] == facet]
        supported = sum(1 for claim in rows if claim["status"] == "supported")
        conflicted = sum(1 for claim in rows if claim["status"] == "conflicted")
        unresolved = sum(1 for claim in rows if claim["status"] == "unresolved")
        coverage_status = "covered" if supported > 0 else ("partial" if (conflicted > 0 or unresolved > 0) else "missing")
        facet_rows.append(
            {
                "facet": facet,
                "claim_count": len(rows),
                "supported_count": supported,
                "conflicted_count": conflicted,
                "unresolved_count": unresolved,
                "coverage_status": coverage_status,
            }
        )

    return {
        "claims": claims,
        "evidences": evidences,
        "edges": edges,
        "facets": facet_rows,
    }


def _build_summary(
    claim_graph: dict[str, Any],
    candidates: list[dict[str, Any]],
    scanned_files: int,
    scanned_nodes: int,
    candidate_low_threshold: int,
    file_bias_threshold: float,
) -> dict[str, Any]:
    signal_counts = Counter()
    file_counts = Counter()
    for c in candidates:
        for s in c["signals"]:
            signal_counts[s] += 1
        file_counts[c["path"]] += 1
    total = len(candidates)
    file_bias = (max(file_counts.values()) / total) if total else 0.0
    exception_hits = signal_counts.get("exceptions", 0)
    claims = claim_graph.get("claims", [])
    edges = claim_graph.get("edges", [])
    conflicted_claim_count = sum(1 for c in claims if c.get("status") == "conflicted")
    unresolved_claim_count = sum(1 for c in claims if c.get("status") == "unresolved")
    contradict_claim_count = len({e.get("from_claim_id") for e in edges if e.get("relation") == "contradicts"})
    followup_claim_count = len({e.get("from_claim_id") for e in edges if e.get("relation") == "requires_followup"})

    heuristic_gap_count = 0
    if (
        total == 0
        or total < candidate_low_threshold
        or (total >= 5 and file_bias >= file_bias_threshold)
    ):
        heuristic_gap_count = 1

    gap_count = max(heuristic_gap_count, unresolved_claim_count, followup_claim_count)
    conflict_count = max(conflicted_claim_count, contradict_claim_count)

    sufficiency_score = min(1.0, total / 5.0) * (1.0 - min(file_bias, 1.0) * 0.2)
    status = "ready" if (sufficiency_score >= 0.6 and gap_count == 0 and conflict_count == 0) else "needs_followup"
    if total == 0:
        status = "blocked"
    summary: dict[str, Any] = {
        "scanned_files": scanned_files,
        "scanned_nodes": scanned_nodes,
        "candidates": total,
        "file_bias_ratio": round(file_bias, 4),
        "conflict_count": conflict_count,
        "gap_count": gap_count,
        "integration_status": status,
    }
    return summary


def _claim_coverage_ratio(claim_graph: dict[str, Any]) -> float:
    claims = claim_graph.get("claims") or []
    if not isinstance(claims, list) or not claims:
        return 0.0
    supported = sum(1 for claim in claims if claim.get("status") == "supported")
    return supported / len(claims)


def _candidate_metrics(candidates: list[dict[str, Any]]) -> tuple[int, float, int]:
    total = len(candidates)
    if total == 0:
        return 0, 0.0, 0
    file_counts = Counter(item["path"] for item in candidates)
    file_bias = max(file_counts.values()) / total
    exception_hits = sum(1 for item in candidates if "exceptions" in item["signals"])
    return total, file_bias, exception_hits


def _should_expand_scope(
    *,
    total: int,
    file_bias: float,
    exception_hits: int,
    candidate_low_threshold: int,
    file_bias_threshold: float,
) -> bool:
    del exception_hits
    return (
        total == 0
        or total < candidate_low_threshold
        or (total >= 5 and file_bias >= file_bias_threshold)
    )


def _plan_next_actions(summary: dict[str, Any], query: str, max_stage: int) -> list[dict[str, Any]]:
    del query, max_stage
    if summary["conflict_count"] > 0:
        return [{"type": "manual_read", "confidence": 0.7, "params": {"scope": "section"}}]
    return [{"type": "manual_hits", "confidence": 0.7, "params": {"kind": "integrated_top", "offset": 0, "limit": 20}}]


def _validate_next_actions(actions: Any) -> list[dict[str, Any]]:
    if not isinstance(actions, list):
        raise ToolError("invalid_parameter", "next_actions must be a list")
    validated: list[dict[str, Any]] = []
    for item in actions:
        if not isinstance(item, dict):
            raise ToolError("invalid_parameter", "next_actions item must be an object")
        action_type = item.get("type")
        confidence = item.get("confidence")
        params = item.get("params")
        if action_type not in {"manual_hits", "manual_read", "manual_find"}:
            raise ToolError("invalid_parameter", "next_actions.type is invalid")
        if confidence is not None and not isinstance(confidence, (int, float)):
            raise ToolError("invalid_parameter", "next_actions.confidence must be a number or null")
        if isinstance(confidence, (int, float)) and not (0.0 <= float(confidence) <= 1.0):
            raise ToolError("invalid_parameter", "next_actions.confidence must be between 0 and 1")
        if params is not None and not isinstance(params, dict):
            raise ToolError("invalid_parameter", "next_actions.params must be an object or null")
        validated.append({"type": action_type, "confidence": confidence, "params": params})
    return validated


def _plan_next_actions_with_planner(
    state: AppState,
    summary: dict[str, Any],
    query: str,
    max_stage: int,
) -> list[dict[str, Any]]:
    planner = state.next_actions_planner
    if planner is None:
        return _plan_next_actions(summary, query, max_stage)
    try:
        raw_actions = planner({"summary": summary, "query": query})
        return _validate_next_actions(raw_actions)
    except Exception:
        return _plan_next_actions(summary, query, max_stage)


def _run_find_pass(
    state: AppState,
    manual_ids: list[str],
    query: str,
    max_stage: int,
    budget_time_ms: int,
    max_candidates: int,
    prioritize_paths: dict[str, set[str]] | None = None,
    allowed_paths: dict[str, set[str]] | None = None,
) -> tuple[list[dict[str, Any]], int, int, int, str | None, list[dict[str, Any]], bool, int]:
    del max_stage
    start = time.monotonic()
    candidates: dict[str, dict[str, Any]] = {}
    warnings = 0
    scanned_files = 0
    scanned_nodes = 0
    cutoff_reason: str | None = None
    unscanned_sections: list[dict[str, Any]] = []

    base_terms = split_terms(query)
    expanded_terms = set(base_terms)
    for term in base_terms:
        for synonym in SYNONYMS.get(term, []):
            expanded_terms.add(normalize_text(synonym))
    weighted_terms = {term for term in expanded_terms if term}

    manuals_fp = _manuals_fingerprint(state, manual_ids)
    sparse_index, index_rebuilt = state.sparse_index.get_or_build(manual_ids=manual_ids, fingerprint=manuals_fp)
    bm25_map = bm25_scores(
        sparse_index,
        query_terms=weighted_terms,
        k1=BM25_K1,
        b=BM25_B,
    )
    index_docs = sparse_index.total_docs

    files_by_manual: dict[str, list[Any]] = {}
    for manual_id in manual_ids:
        files = list_manual_files(state.config.manuals_root, manual_id=manual_id)
        if prioritize_paths and manual_id in prioritize_paths:
            preferred = prioritize_paths[manual_id]
            files.sort(key=lambda r: (r.path not in preferred, r.path))
        if allowed_paths is not None:
            files = [row for row in files if row.path in allowed_paths.get(manual_id, set())]
        files_by_manual[manual_id] = files

    seen_unscanned: set[tuple[str, str]] = set()

    def append_remaining_unscanned(start_manual_idx: int, start_file_idx: int, reason: str) -> None:
        for mi in range(start_manual_idx, len(manual_ids)):
            mid = manual_ids[mi]
            rows = files_by_manual.get(mid, [])
            from_idx = start_file_idx if mi == start_manual_idx else 0
            for row in rows[from_idx:]:
                key = (mid, row.path)
                if key in seen_unscanned:
                    continue
                seen_unscanned.add(key)
                unscanned_sections.append({"manual_id": mid, "path": row.path, "reason": reason})

    for manual_idx, manual_id in enumerate(manual_ids):
        files = files_by_manual.get(manual_id, [])
        for row_idx, row in enumerate(files):
            elapsed_ms = int((time.monotonic() - start) * 1000)
            if elapsed_ms > budget_time_ms:
                cutoff_reason = "time_budget"
                append_remaining_unscanned(manual_idx, row_idx, "time_budget")
                break
            if len(candidates) >= max_candidates:
                cutoff_reason = "candidate_cap"
                append_remaining_unscanned(manual_idx, row_idx, "candidate_cap")
                break
            scanned_files += 1
            doc_ids = sparse_index.docs_by_file.get((manual_id, row.path), [])
            if row.file_type == "md":
                scanned_nodes += len(doc_ids)
            elif doc_ids:
                scanned_nodes += 1
            else:
                # Index build may skip unreadable files; treat as warning on query pass.
                warnings += 1
                continue

            for doc_id in doc_ids:
                doc = sparse_index.docs[doc_id]
                signals: set[str] = set()
                strict_signals = 0
                if any(term in doc.normalized_title for term in base_terms):
                    signals.add("heading")
                if any(term in doc.normalized_text for term in expanded_terms):
                    signals.add("normalized")
                    strict_signals += 1
                if any(loose_contains(term, doc.raw_text) for term in expanded_terms):
                    signals.add("loose")
                    strict_signals += 1
                if any(word in doc.normalized_text for word in NORMALIZED_EXCEPTION_WORDS):
                    signals.add("exceptions")
                if strict_signals == 0:
                    continue
                score = (SCORE_WEIGHT_BM25 * float(bm25_map.get(doc_id, 0.0))) + _score_from_signals(signals)
                item = {
                    "ref": {
                        "target": "manual",
                        "manual_id": manual_id,
                        "path": row.path,
                        "start_line": doc.start_line,
                        "heading_id": doc.heading_id,
                        "json_path": None,
                        "title": doc.title,
                        "signals": sorted(signals),
                    },
                    "path": row.path,
                    "start_line": doc.start_line,
                    "reason": None,
                    "signals": sorted(signals),
                    "score": round(score, 4),
                    "conflict_with": [],
                    "gap_hint": None,
                }
                key = _candidate_key(item)
                prev = candidates.get(key)
                if prev is None or item["score"] > prev["score"]:
                    candidates[key] = item
        if cutoff_reason:
            break

    ordered = sorted(
        candidates.values(),
        key=lambda x: (-float(x["score"]), str(x["path"]), int(x.get("start_line") or 1)),
    )
    return ordered, scanned_files, scanned_nodes, warnings, cutoff_reason, unscanned_sections, index_rebuilt, index_docs


def _run_exceptions_expand_pass(
    state: AppState,
    manual_ids: list[str],
    budget_time_ms: int,
    max_candidates: int,
    existing_count: int,
    allowed_paths: dict[str, set[str]] | None = None,
) -> tuple[list[dict[str, Any]], int, int, int, str | None, list[dict[str, Any]]]:
    start = time.monotonic()
    candidates: dict[str, dict[str, Any]] = {}
    warnings = 0
    scanned_files = 0
    scanned_nodes = 0
    cutoff_reason: str | None = None
    unscanned_sections: list[dict[str, Any]] = []

    files_by_manual: dict[str, list[Any]] = {}
    for manual_id in manual_ids:
        files = list_manual_files(state.config.manuals_root, manual_id=manual_id)
        if allowed_paths is not None:
            files = [row for row in files if row.path in allowed_paths.get(manual_id, set())]
        files_by_manual[manual_id] = files

    seen_unscanned: set[tuple[str, str]] = set()

    def append_remaining_unscanned(start_manual_idx: int, start_file_idx: int, reason: str) -> None:
        for mi in range(start_manual_idx, len(manual_ids)):
            mid = manual_ids[mi]
            rows = files_by_manual.get(mid, [])
            from_idx = start_file_idx if mi == start_manual_idx else 0
            for row in rows[from_idx:]:
                key = (mid, row.path)
                if key in seen_unscanned:
                    continue
                seen_unscanned.add(key)
                unscanned_sections.append({"manual_id": mid, "path": row.path, "reason": reason})

    for manual_idx, manual_id in enumerate(manual_ids):
        files = files_by_manual.get(manual_id, [])
        for row_idx, row in enumerate(files):
            elapsed_ms = int((time.monotonic() - start) * 1000)
            if elapsed_ms > budget_time_ms:
                cutoff_reason = "time_budget"
                append_remaining_unscanned(manual_idx, row_idx, "time_budget")
                break
            if existing_count + len(candidates) >= max_candidates:
                cutoff_reason = "candidate_cap"
                append_remaining_unscanned(manual_idx, row_idx, "candidate_cap")
                break

            scanned_files += 1
            full_path = resolve_inside_root(state.config.manuals_root / manual_id, row.path, must_exist=True)
            try:
                text = full_path.read_text(encoding="utf-8")
            except Exception:
                warnings += 1
                continue

            if row.file_type == "md":
                nodes = parse_markdown_toc(row.path, text)
                lines = text.splitlines()
                for node in nodes:
                    scanned_nodes += 1
                    node_lines = lines[node.line_start - 1 : node.line_end]
                    body_text = "\n".join(node_lines[1:]) if len(node_lines) > 1 else ""
                    normalized_text = normalize_text(body_text)
                    if not any(word in normalized_text for word in NORMALIZED_EXCEPTION_WORDS):
                        continue
                    item = {
                        "ref": {
                            "target": "manual",
                            "manual_id": manual_id,
                            "path": row.path,
                            "start_line": node.line_start,
                            "heading_id": node.node_id,
                            "json_path": None,
                            "title": node.title,
                            "signals": ["exceptions"],
                        },
                        "path": row.path,
                        "start_line": node.line_start,
                        "reason": "exceptions_expanded",
                        "signals": ["exceptions"],
                        "score": _score_from_signals({"exceptions"}),
                        "conflict_with": [],
                        "gap_hint": None,
                    }
                    candidates[_candidate_key(item)] = item
            else:
                scanned_nodes += 1
                normalized_text = normalize_text(text)
                if not any(word in normalized_text for word in NORMALIZED_EXCEPTION_WORDS):
                    continue
                item = {
                    "ref": {
                        "target": "manual",
                        "manual_id": manual_id,
                        "path": row.path,
                        "start_line": 1,
                        "json_path": None,
                        "title": Path(row.path).name,
                        "signals": ["exceptions"],
                    },
                    "path": row.path,
                    "start_line": 1,
                    "reason": "exceptions_expanded",
                    "signals": ["exceptions"],
                    "score": _score_from_signals({"exceptions"}),
                    "conflict_with": [],
                    "gap_hint": None,
                }
                candidates[_candidate_key(item)] = item
        if cutoff_reason:
            break

    ordered = sorted(candidates.values(), key=lambda x: x["score"], reverse=True)
    return ordered, scanned_files, scanned_nodes, warnings, cutoff_reason, unscanned_sections


def manual_find(
    state: AppState,
    query: str,
    manual_id: str | None = None,
    expand_scope: bool | None = None,
    only_unscanned_from_trace_id: str | None = None,
    budget: dict[str, Any] | None = None,
    include_claim_graph: bool | None = None,
    use_cache: bool | None = None,
    record_adaptive_stats: bool = True,
) -> dict[str, Any]:
    started_at = time.monotonic()
    ensure(bool(query and query.strip()), "invalid_parameter", "query is required")
    ensure(expand_scope is None or isinstance(expand_scope, bool), "invalid_parameter", "expand_scope must be boolean")
    applied_expand_scope = True if expand_scope is None else bool(expand_scope)
    applied_include_claim_graph = _parse_bool_param(
        include_claim_graph,
        name="include_claim_graph",
        default=False,
    )
    applied_use_cache = _parse_bool_param(
        use_cache,
        name="use_cache",
        default=state.config.sem_cache_enabled,
    )
    applied_max_stage = 4 if applied_expand_scope else 3

    budget = budget or {}
    budget_time_ms = _parse_int_param(budget.get("time_ms"), name="budget.time_ms", default=60000, min_value=1)
    max_candidates = _parse_int_param(
        budget.get("max_candidates"),
        name="budget.max_candidates",
        default=200,
        min_value=1,
    )
    candidate_low_threshold, file_bias_threshold = state.adaptive_stats.manual_find_thresholds(
        base_candidate_low=state.config.adaptive_candidate_low_base,
        base_file_bias=state.config.adaptive_file_bias_base,
        adaptive_tuning=state.config.adaptive_tuning,
        min_recall=state.config.adaptive_min_recall,
    )

    applied_manual_id = manual_id or state.config.default_manual_id
    selected_manual_ids = [applied_manual_id] if applied_manual_id else discover_manual_ids(state.config.manuals_root)
    prioritize_paths: dict[str, set[str]] | None = None
    allowed_paths: dict[str, set[str]] | None = None
    escalation_reasons: list[str] = []
    use_semantic_cache = applied_use_cache and not bool(only_unscanned_from_trace_id)
    cache_scope_key: str | None = None
    cache_query: str | None = None
    manuals_fp_lookup: str | None = None
    cache_manual_ids_for_put = list(selected_manual_ids)
    sem_cache_hit = False
    sem_cache_mode = "miss" if use_semantic_cache else "bypass"
    sem_cache_score: float | None = None
    latency_saved_ms: int | None = None
    if applied_manual_id:
        ensure(
            _manual_exists(state.config.manuals_root, applied_manual_id),
            "not_found",
            "manual_id not found",
            {"manual_id": applied_manual_id},
        )

    if only_unscanned_from_trace_id:
        trace = state.traces.get(only_unscanned_from_trace_id)
        if trace is None:
            raise ToolError("not_found", "trace_id not found", {"trace_id": only_unscanned_from_trace_id})
        targets = trace.get("unscanned_sections", [])
        prioritize_paths = {}
        for item in targets:
            m = item.get("manual_id")
            p = item.get("path")
            if not m or not p:
                continue
            if applied_manual_id and m != applied_manual_id:
                continue
            prioritize_paths.setdefault(m, set()).add(p)
        if prioritize_paths:
            escalation_reasons.append("prioritized_unscanned_sections")

    if use_semantic_cache:
        cache_scope_key = _manual_find_scope_key(
            manual_id=applied_manual_id,
            expand_scope=applied_expand_scope,
            max_candidates=max_candidates,
            budget_time_ms=budget_time_ms,
        )
        cache_query = _cacheable_query(query)
        manuals_fp_lookup = _manuals_fingerprint(state, selected_manual_ids)
        exact_cached = state.semantic_cache.lookup_exact(
            scope_key=cache_scope_key,
            normalized_query=cache_query,
            manuals_fingerprint=manuals_fp_lookup,
        )
        if exact_cached.hit:
            cached_trace_payload, source_latency_ms = _cached_trace_payload_and_source_latency(exact_cached.value)
            if cached_trace_payload is not None:
                cached_summary = cached_trace_payload.get("summary")
                if isinstance(cached_summary, dict) and _cached_summary_is_acceptable(state, cached_summary):
                    sem_cache_hit = True
                    sem_cache_mode = "exact"
                    sem_cache_score = exact_cached.score
                    if source_latency_ms is not None:
                        elapsed_ms = int((time.monotonic() - started_at) * 1000)
                        latency_saved_ms = max(0, source_latency_ms - elapsed_ms)
                    if record_adaptive_stats:
                        cached_candidates = cached_trace_payload.get("candidates")
                        cached_unscanned = cached_trace_payload.get("unscanned_sections")
                        _record_manual_find_stats(
                            state,
                            query=query,
                            summary=cached_summary,
                            scanned_files=0,
                            scanned_nodes=0,
                            candidates_count=len(cached_candidates) if isinstance(cached_candidates, list) else 0,
                            warnings=0,
                            max_stage_applied=applied_max_stage,
                            scope_expanded=False,
                            cutoff_reason=None,
                            unscanned_sections_count=len(cached_unscanned) if isinstance(cached_unscanned, list) else 0,
                            candidate_low_threshold=candidate_low_threshold,
                            file_bias_threshold=file_bias_threshold,
                            sem_cache_hit=sem_cache_hit,
                            sem_cache_mode=sem_cache_mode,
                            sem_cache_score=sem_cache_score,
                            latency_saved_ms=latency_saved_ms,
                            scoring_mode="cache",
                        )
                    trace_id = state.traces.create(cached_trace_payload)
                    return _out_from_trace_payload(
                        trace_id=trace_id,
                        trace_payload=cached_trace_payload,
                        include_claim_graph=applied_include_claim_graph,
                    )
                sem_cache_mode = "guard_revalidate"

        semantic_cached = state.semantic_cache.lookup_semantic(
            scope_key=cache_scope_key,
            normalized_query=cache_query,
            manuals_fingerprint=manuals_fp_lookup,
            sim_threshold=state.config.sem_cache_sim_threshold,
        )
        if semantic_cached.hit:
            cached_trace_payload, source_latency_ms = _cached_trace_payload_and_source_latency(semantic_cached.value)
            if cached_trace_payload is not None:
                cached_summary = cached_trace_payload.get("summary")
                if isinstance(cached_summary, dict) and _cached_summary_is_acceptable(state, cached_summary):
                    sem_cache_hit = True
                    sem_cache_mode = "semantic"
                    sem_cache_score = semantic_cached.score
                    if source_latency_ms is not None:
                        elapsed_ms = int((time.monotonic() - started_at) * 1000)
                        latency_saved_ms = max(0, source_latency_ms - elapsed_ms)
                    if record_adaptive_stats:
                        cached_candidates = cached_trace_payload.get("candidates")
                        cached_unscanned = cached_trace_payload.get("unscanned_sections")
                        _record_manual_find_stats(
                            state,
                            query=query,
                            summary=cached_summary,
                            scanned_files=0,
                            scanned_nodes=0,
                            candidates_count=len(cached_candidates) if isinstance(cached_candidates, list) else 0,
                            warnings=0,
                            max_stage_applied=applied_max_stage,
                            scope_expanded=False,
                            cutoff_reason=None,
                            unscanned_sections_count=len(cached_unscanned) if isinstance(cached_unscanned, list) else 0,
                            candidate_low_threshold=candidate_low_threshold,
                            file_bias_threshold=file_bias_threshold,
                            sem_cache_hit=sem_cache_hit,
                            sem_cache_mode=sem_cache_mode,
                            sem_cache_score=sem_cache_score,
                            latency_saved_ms=latency_saved_ms,
                            scoring_mode="cache",
                        )
                    trace_id = state.traces.create(cached_trace_payload)
                    return _out_from_trace_payload(
                        trace_id=trace_id,
                        trace_payload=cached_trace_payload,
                        include_claim_graph=applied_include_claim_graph,
                    )
                sem_cache_mode = "guard_revalidate"

    candidates, scanned_files, scanned_nodes, warnings, cutoff_reason, unscanned, index_rebuilt, index_docs = _run_find_pass(
        state=state,
        manual_ids=selected_manual_ids,
        query=query,
        max_stage=applied_max_stage,
        budget_time_ms=budget_time_ms,
        max_candidates=max_candidates,
        prioritize_paths=prioritize_paths,
        allowed_paths=allowed_paths,
    )

    total, file_bias, exception_hits = _candidate_metrics(candidates)
    should_expand = _should_expand_scope(
        total=total,
        file_bias=file_bias,
        exception_hits=exception_hits,
        candidate_low_threshold=candidate_low_threshold,
        file_bias_threshold=file_bias_threshold,
    )
    prefers_exceptions = _query_prefers_exceptions(query) or exception_hits > 0

    scope_expanded = False
    merged_candidates = {(_candidate_key(item)): item for item in candidates}
    if applied_max_stage == 4:
        if should_expand and prefers_exceptions:
            local_paths: dict[str, set[str]] | None = None
            if applied_manual_id:
                seed_paths = {
                    item["path"]
                    for item in candidates
                    if item["ref"]["manual_id"] == applied_manual_id
                }
                if seed_paths:
                    local_paths = {applied_manual_id: seed_paths}
            if local_paths:
                extra, sf2, sn2, w2, cutoff2, unscanned2 = _run_exceptions_expand_pass(
                    state=state,
                    manual_ids=[applied_manual_id] if applied_manual_id else selected_manual_ids,
                    budget_time_ms=budget_time_ms,
                    max_candidates=max_candidates,
                    existing_count=len(merged_candidates),
                    allowed_paths=local_paths,
                )
                for item in extra:
                    merged_candidates[_candidate_key(item)] = item
                candidates = sorted(merged_candidates.values(), key=lambda x: x["score"], reverse=True)
                scanned_files += sf2
                scanned_nodes += sn2
                warnings += w2
                cutoff_reason = cutoff_reason or cutoff2
                unscanned.extend(unscanned2)
                scope_expanded = scope_expanded or bool(extra)
                escalation_reasons.append("exceptions_local_expanded")
                total, file_bias, exception_hits = _candidate_metrics(candidates)
                should_expand = _should_expand_scope(
                    total=total,
                    file_bias=file_bias,
                    exception_hits=exception_hits,
                    candidate_low_threshold=candidate_low_threshold,
                    file_bias_threshold=file_bias_threshold,
                )

            if should_expand and applied_manual_id:
                extra, sf2, sn2, w2, cutoff2, unscanned2 = _run_exceptions_expand_pass(
                    state=state,
                    manual_ids=[applied_manual_id],
                    budget_time_ms=budget_time_ms,
                    max_candidates=max_candidates,
                    existing_count=len(merged_candidates),
                )
                for item in extra:
                    merged_candidates[_candidate_key(item)] = item
                candidates = sorted(merged_candidates.values(), key=lambda x: x["score"], reverse=True)
                scanned_files += sf2
                scanned_nodes += sn2
                warnings += w2
                cutoff_reason = cutoff_reason or cutoff2
                unscanned.extend(unscanned2)
                scope_expanded = scope_expanded or bool(extra)
                escalation_reasons.append("exceptions_manual_expanded")
                total, file_bias, exception_hits = _candidate_metrics(candidates)
                should_expand = _should_expand_scope(
                    total=total,
                    file_bias=file_bias,
                    exception_hits=exception_hits,
                    candidate_low_threshold=candidate_low_threshold,
                    file_bias_threshold=file_bias_threshold,
                )

            if should_expand:
                global_ids = discover_manual_ids(state.config.manuals_root)
                cache_manual_ids_for_put = global_ids
                extra, sf2, sn2, w2, cutoff2, unscanned2 = _run_exceptions_expand_pass(
                    state=state,
                    manual_ids=global_ids,
                    budget_time_ms=budget_time_ms,
                    max_candidates=max_candidates,
                    existing_count=len(merged_candidates),
                )
                for item in extra:
                    merged_candidates[_candidate_key(item)] = item
                candidates = sorted(merged_candidates.values(), key=lambda x: x["score"], reverse=True)
                scanned_files += sf2
                scanned_nodes += sn2
                warnings += w2
                cutoff_reason = cutoff_reason or cutoff2
                unscanned.extend(unscanned2)
                scope_expanded = scope_expanded or bool(extra)
                escalation_reasons.append("exceptions_global_expanded")
                total, file_bias, exception_hits = _candidate_metrics(candidates)
                should_expand = _should_expand_scope(
                    total=total,
                    file_bias=file_bias,
                    exception_hits=exception_hits,
                    candidate_low_threshold=candidate_low_threshold,
                    file_bias_threshold=file_bias_threshold,
                )

        if should_expand and applied_manual_id:
            if total == 0:
                escalation_reasons.append("zero_candidates")
            if total < candidate_low_threshold:
                escalation_reasons.append("low_candidates")
            if total >= 5 and file_bias >= file_bias_threshold:
                escalation_reasons.append("file_bias")
            expanded_ids = discover_manual_ids(state.config.manuals_root)
            cache_manual_ids_for_put = expanded_ids
            extra, sf2, sn2, w2, cutoff2, unscanned2, index_rebuilt2, index_docs2 = _run_find_pass(
                state=state,
                manual_ids=expanded_ids,
                query=query,
                max_stage=applied_max_stage,
                budget_time_ms=budget_time_ms,
                max_candidates=max_candidates,
                prioritize_paths=prioritize_paths,
                allowed_paths=allowed_paths,
            )
            for item in extra:
                merged_candidates[_candidate_key(item)] = item
            candidates = sorted(merged_candidates.values(), key=lambda x: x["score"], reverse=True)
            scanned_files += sf2
            scanned_nodes += sn2
            warnings += w2
            cutoff_reason = cutoff_reason or cutoff2
            unscanned.extend(unscanned2)
            index_rebuilt = index_rebuilt or index_rebuilt2
            index_docs = max(index_docs, index_docs2)
            scope_expanded = True
            escalation_reasons.append("manual_scope_expanded")
    elif applied_manual_id and should_expand:
        if total == 0:
            escalation_reasons.append("zero_candidates")
        if total < candidate_low_threshold:
            escalation_reasons.append("low_candidates")
        if total >= 5 and file_bias >= file_bias_threshold:
            escalation_reasons.append("file_bias")
        cutoff_reason = cutoff_reason or "stage_cap"
        escalation_reasons.append("stage_cap")
        # max_stage=3 の場合、他manualへの拡張を未実行として unscanned に残す。
        cache_manual_ids_for_put = discover_manual_ids(state.config.manuals_root)
        for extra_id in cache_manual_ids_for_put:
            if extra_id == applied_manual_id:
                continue
            for row in list_manual_files(state.config.manuals_root, manual_id=extra_id):
                unscanned.append({"manual_id": extra_id, "path": row.path, "reason": "stage_cap"})
    if should_expand and not applied_manual_id:
        if total == 0:
            escalation_reasons.append("zero_candidates")
        if total < candidate_low_threshold:
            escalation_reasons.append("low_candidates")
        if total >= 5 and file_bias >= file_bias_threshold:
            escalation_reasons.append("file_bias")

    candidates = _apply_heading_focus(candidates)

    claim_graph = _build_claim_graph(
        query=query,
        candidates=candidates,
    )
    summary = _build_summary(
        claim_graph=claim_graph,
        candidates=candidates,
        scanned_files=scanned_files,
        scanned_nodes=scanned_nodes,
        candidate_low_threshold=candidate_low_threshold,
        file_bias_threshold=file_bias_threshold,
    )
    coverage_ratio = _claim_coverage_ratio(claim_graph)
    if coverage_ratio < state.config.coverage_min_ratio and summary["integration_status"] == "ready":
        summary["integration_status"] = "needs_followup"
        escalation_reasons.append("coverage_below_threshold")
    summary_token_estimate = max(1, len(str(summary)) // 4)
    marginal_gain = len(candidates) / summary_token_estimate
    if marginal_gain < state.config.marginal_gain_min and summary["integration_status"] == "ready":
        summary["integration_status"] = "needs_followup"
        escalation_reasons.append("low_marginal_gain")
    next_actions = _plan_next_actions_with_planner(
        state=state,
        summary=summary,
        query=query,
        max_stage=applied_max_stage,
    )
    evidences_by_id = {item["evidence_id"]: item for item in claim_graph.get("evidences", [])}
    conflict_edges = [item for item in claim_graph.get("edges", []) if item.get("relation") == "contradicts"]
    followup_edges = [item for item in claim_graph.get("edges", []) if item.get("relation") == "requires_followup"]
    conflict_by_claim: dict[str, dict[str, Any]] = {}
    for edge in conflict_edges:
        claim_id = str(edge.get("from_claim_id") or "")
        if claim_id and claim_id not in conflict_by_claim:
            conflict_by_claim[claim_id] = edge
    followup_by_claim: dict[str, dict[str, Any]] = {}
    for edge in followup_edges:
        claim_id = str(edge.get("from_claim_id") or "")
        if claim_id and claim_id not in followup_by_claim:
            followup_by_claim[claim_id] = edge
    gap_rows = [
        {
            "ref": None,
            "path": None,
            "start_line": None,
            "reason": "gap",
            "signals": [],
            "score": None,
            "conflict_with": [],
            "gap_hint": f"followup required for claim {claim_id}",
        }
        for claim_id in sorted(followup_by_claim.keys())
    ]
    while len(gap_rows) < summary["gap_count"]:
        gap_rows.append(
            {
                "ref": None,
                "path": None,
                "start_line": None,
                "reason": "gap",
                "signals": [],
                "score": None,
                "conflict_with": [],
                "gap_hint": "no candidates matched the current query scope",
            }
        )

    trace_payload = {
        "query": query,
        "manual_id": applied_manual_id,
        "claim_graph": claim_graph,
        "summary": summary,
        "next_actions": next_actions,
        "candidates": candidates,
        "unscanned_sections": [
            {
                "manual_id": item.get("manual_id") or applied_manual_id,
                "path": item["path"],
                "start_line": None,
                "reason": item.get("reason") or "time_budget",
                "signals": [],
                "score": None,
                "ref": None,
                "conflict_with": [],
                "gap_hint": None,
            }
            for item in unscanned
        ],
        "conflicts": [
            {
                "ref": (evidences_by_id.get(edge["to_evidence_id"]) or {}).get("ref"),
                "path": ((evidences_by_id.get(edge["to_evidence_id"]) or {}).get("ref") or {}).get("path"),
                "start_line": ((evidences_by_id.get(edge["to_evidence_id"]) or {}).get("ref") or {}).get("start_line"),
                "reason": "claim_conflict",
                "signals": (evidences_by_id.get(edge["to_evidence_id"]) or {}).get("signals") or [],
                "score": (evidences_by_id.get(edge["to_evidence_id"]) or {}).get("score"),
                "conflict_with": [edge["from_claim_id"]],
                "gap_hint": None,
            }
            for edge in conflict_by_claim.values()
        ],
        "gaps": gap_rows,
        "integrated_top": [
            {**item, "reason": "ranked_by_integration"}
            for item in candidates[:20]
        ],
        "escalation_reasons": sorted(set(escalation_reasons)),
        "cutoff_reason": cutoff_reason,
    }
    trace_id = state.traces.create(trace_payload)
    if record_adaptive_stats:
        _record_manual_find_stats(
            state,
            query=query,
            summary=summary,
            scanned_files=scanned_files,
            scanned_nodes=scanned_nodes,
            candidates_count=len(candidates),
            warnings=warnings,
            max_stage_applied=applied_max_stage,
            scope_expanded=scope_expanded,
            cutoff_reason=cutoff_reason,
            unscanned_sections_count=len(unscanned),
            candidate_low_threshold=candidate_low_threshold,
            file_bias_threshold=file_bias_threshold,
            sem_cache_hit=sem_cache_hit,
            sem_cache_mode=sem_cache_mode,
            sem_cache_score=sem_cache_score,
            latency_saved_ms=latency_saved_ms,
            scoring_mode="bm25",
            index_rebuilt=index_rebuilt,
            index_docs=index_docs,
        )

    if use_semantic_cache and cache_scope_key and cache_query:
        manuals_fp_put = _manuals_fingerprint(state, cache_manual_ids_for_put)
        source_latency_ms = int((time.monotonic() - started_at) * 1000)
        state.semantic_cache.put(
            scope_key=cache_scope_key,
            normalized_query=cache_query,
            manuals_fingerprint=manuals_fp_put,
            payload={"trace_payload": trace_payload, "source_latency_ms": source_latency_ms},
        )

    out: dict[str, Any] = {"trace_id": trace_id, "summary": summary, "next_actions": next_actions}
    if applied_include_claim_graph:
        out["claim_graph"] = claim_graph
    return out


def manual_hits(
    state: AppState,
    trace_id: str,
    kind: str | None = None,
    offset: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    ensure(bool(trace_id), "invalid_parameter", "trace_id is required")
    payload = state.traces.get(trace_id)
    if payload is None:
        raise ToolError("not_found", "trace_id not found", {"trace_id": trace_id})
    applied_kind = kind or "candidates"
    ensure(
        applied_kind in {"candidates", "unscanned", "conflicts", "gaps", "integrated_top", "claims", "evidences", "edges"},
        "invalid_parameter",
        "invalid kind",
    )
    applied_offset = _parse_int_param(offset, name="offset", default=0, min_value=0)
    applied_limit = _parse_int_param(limit, name="limit", default=50, min_value=1)

    key_map = {
        "candidates": "candidates",
        "unscanned": "unscanned_sections",
        "conflicts": "conflicts",
        "gaps": "gaps",
        "integrated_top": "integrated_top",
        "claims": "claim_graph.claims",
        "evidences": "claim_graph.evidences",
        "edges": "claim_graph.edges",
    }
    mapped_key = key_map[applied_kind]
    if "." in mapped_key:
        parent, child = mapped_key.split(".", 1)
        rows = (payload.get(parent) or {}).get(child, [])
    else:
        rows = payload.get(mapped_key, [])
    shared_manual_id: str | None = None
    if applied_kind == "candidates":
        manual_ids = {
            str(((item.get("ref") or {}).get("manual_id")))
            for item in rows
            if (item.get("ref") or {}).get("manual_id")
        }
        shared_manual_id = next(iter(manual_ids)) if len(manual_ids) == 1 else None
        compact_rows: list[dict[str, Any]] = []
        for item in rows:
            ref = dict(item.get("ref") or {})
            compact_ref: dict[str, Any] = {}
            if not shared_manual_id and ref.get("manual_id"):
                compact_ref["manual_id"] = ref["manual_id"]
            if ref.get("path"):
                compact_ref["path"] = ref["path"]
            if ref.get("start_line") is not None:
                compact_ref["start_line"] = ref["start_line"]
            if ref.get("title"):
                compact_ref["title"] = ref["title"]
            if ref.get("signals"):
                compact_ref["signals"] = ref["signals"]

            compact_item: dict[str, Any] = {"ref": compact_ref}
            score = item.get("score")
            if score is not None:
                compact_item["score"] = score
            reason = item.get("reason")
            if reason is not None:
                compact_item["reason"] = reason
            conflict_with = item.get("conflict_with") or []
            if conflict_with:
                compact_item["conflict_with"] = conflict_with
            gap_hint = item.get("gap_hint")
            if gap_hint is not None:
                compact_item["gap_hint"] = gap_hint
            compact_rows.append(compact_item)
        rows = compact_rows
    sliced = rows[applied_offset : applied_offset + applied_limit]
    out = {
        "trace_id": trace_id,
        "kind": applied_kind,
        "offset": applied_offset,
        "limit": applied_limit,
        "total": len(rows),
        "items": sliced,
    }
    if applied_kind == "candidates" and shared_manual_id:
        out["manual_id"] = shared_manual_id
    return out
