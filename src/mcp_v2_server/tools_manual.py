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
ALLOWED_INTENTS = {"definition", "procedure", "eligibility", "exceptions", "compare", "unknown", None}
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
    return f"manual::{manual_id}"


def _manual_dir_id(manual_id: str, relative_dir: str) -> str:
    return f"dir::{manual_id}::{_encode_node_segment(relative_dir)}"


def _manual_file_id(manual_id: str, relative_path: str) -> str:
    return f"file::{manual_id}::{_encode_node_segment(relative_path)}"


def _parse_manual_ls_id(id_value: str) -> tuple[str, str, str | None]:
    if id_value == "manuals":
        return "manuals", "", None
    if id_value.startswith("manual::"):
        manual_id = id_value[len("manual::") :]
        ensure(bool(manual_id), "invalid_parameter", "invalid id")
        return "manual", manual_id, ""
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
    raise ToolError("invalid_parameter", "invalid id")


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


def _resolve_read_limits(state: AppState, limits: dict[str, Any] | None) -> tuple[int, int]:
    limits = limits or {}
    max_sections = _parse_int_param(limits.get("max_sections"), name="limits.max_sections", default=20, min_value=1)
    max_chars = _parse_int_param(limits.get("max_chars"), name="limits.max_chars", default=8000, min_value=1)
    max_sections = min(max_sections, state.config.hard_max_sections)
    max_chars = min(max_chars, state.config.hard_max_chars)
    return max_sections, max_chars


def _resolve_scan_max_chars(state: AppState, limits: dict[str, Any] | None) -> int:
    max_chars = _parse_int_param(
        (limits or {}).get("max_chars"),
        name="limits.max_chars",
        default=state.config.hard_max_chars,
        min_value=1,
    )
    return min(max_chars, state.config.hard_max_chars)


def manual_read(
    state: AppState,
    ref: dict[str, Any],
    scope: str | None = None,
    limits: dict[str, Any] | None = None,
    expand: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ref = dict(ref)
    target = ref.get("target")
    if target is None:
        ref["target"] = "manual"
    else:
        ensure(target == "manual", "invalid_parameter", "ref.target must be manual")
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
    max_sections, max_chars = _resolve_read_limits(state, limits)
    allow_file = bool((limits or {}).get("allow_file"))
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
            if not state.config.allow_file_scope or not allow_file:
                raise ToolError("forbidden", "md file scope requires ALLOW_FILE_SCOPE=true and limits.allow_file=true")
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
                        chunk_lines=state.config.vault_scan_default_chunk_lines,
                        limits={"max_chars": max_chars},
                    )
                    output = str(scan.get("text") or "")
                    truncated = bool(scan.get("truncated"))
                    applied_mode = "scan_fallback"
                    next_scan_start = ((scan.get("next_cursor") or {}).get("start_line")) or (len(lines) + 1)
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
    cursor: dict[str, Any] | None = None,
    chunk_lines: int | None = None,
    limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure(_manual_exists(state.config.manuals_root, manual_id), "not_found", "manual_id not found", {"manual_id": manual_id})
    relative_path = normalize_relative_path(path)
    full_path = resolve_inside_root(state.config.manuals_root / manual_id, relative_path, must_exist=True)
    ensure(full_path.exists() and full_path.is_file(), "not_found", "manual file not found", {"path": relative_path})

    text = full_path.read_text(encoding="utf-8")
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
    max_chars = _resolve_scan_max_chars(state, limits)

    truncated_reason = "none"
    if len(chunk_text) > max_chars:
        chunk_text = chunk_text[:max_chars]
        truncated_reason = "hard_limit" if max_chars >= state.config.hard_max_chars else "max_chars"
    elif end_line < total:
        truncated_reason = "chunk_end"
    eof = end_line >= total

    return {
        "manual_id": manual_id,
        "path": relative_path,
        "text": chunk_text,
        "applied_range": {"start_line": applied_start_line, "end_line": end_line},
        "next_cursor": {"start_line": None if eof else end_line + 1},
        "eof": eof,
        "truncated": truncated_reason != "none",
        "truncated_reason": truncated_reason,
        "applied": {"chunk_lines": applied_chunk, "max_chars": max_chars},
    }


def _candidate_key(item: dict[str, Any]) -> str:
    ref = item["ref"]
    return f'{ref["manual_id"]}|{ref["path"]}|{ref.get("start_line") or 1}'


def _infer_claim_facets(query: str, intent: str | None, candidates: list[dict[str, Any]]) -> list[str]:
    query_norm = normalize_text(query)
    ordered: list[str] = []

    def add(facet: str) -> None:
        if facet in FACET_ORDER and facet not in ordered:
            ordered.append(facet)

    if intent and intent != "unknown":
        add(intent)

    for facet, hints in FACET_HINTS.items():
        if any(hint in query_norm for hint in hints):
            add(facet)

    if any("exceptions" in (item.get("signals") or []) for item in candidates):
        add("exceptions")

    if not ordered:
        add(intent or "unknown")

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
    intent: str | None,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    facets = _infer_claim_facets(query, intent, candidates)
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
    intent: str | None,
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
        or (intent == "exceptions" and exception_hits == 0)
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
    intent: str | None,
    exception_hits: int,
    candidate_low_threshold: int,
    file_bias_threshold: float,
) -> bool:
    return (
        total == 0
        or total < candidate_low_threshold
        or (total >= 5 and file_bias >= file_bias_threshold)
        or (intent == "exceptions" and exception_hits == 0)
    )


def _plan_next_actions(summary: dict[str, Any], query: str, intent: str | None, max_stage: int) -> list[dict[str, Any]]:
    if summary["conflict_count"] > 0:
        return [{"type": "manual_read", "confidence": 0.7, "params": {"scope": "section"}}]
    if summary["gap_count"] > 0:
        params: dict[str, Any] = {"query": query}
        if intent:
            params["intent"] = intent
        if max_stage < 4:
            params["max_stage"] = 4
        return [{"type": "manual_find", "confidence": 0.6, "params": params}]
    if summary["integration_status"] == "ready":
        return [{"type": "stop", "confidence": 0.8, "params": None}]
    return [{"type": "manual_hits", "confidence": 0.7, "params": {"kind": "integrated_top", "offset": 0, "limit": 20}}]


def _run_find_pass(
    state: AppState,
    manual_ids: list[str],
    query: str,
    intent: str | None,
    max_stage: int,
    budget_time_ms: int,
    max_candidates: int,
    prioritize_paths: dict[str, set[str]] | None = None,
    allowed_paths: dict[str, set[str]] | None = None,
) -> tuple[list[dict[str, Any]], int, int, int, str | None, list[dict[str, Any]]]:
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
                    node_text = "\n".join(lines[node.line_start - 1 : node.line_end])
                    normalized_title = normalize_text(node.title)
                    normalized_text = normalize_text(node_text)
                    signals: set[str] = set()
                    strict_signals = 0

                    if any(term in normalized_title for term in base_terms):
                        signals.add("heading")
                        strict_signals += 1
                    if any(term in normalized_text for term in expanded_terms):
                        signals.add("normalized")
                        strict_signals += 1
                    if any(loose_contains(term, node_text) for term in expanded_terms):
                        signals.add("loose")
                        strict_signals += 1
                    if any(word in normalized_text for word in NORMALIZED_EXCEPTION_WORDS):
                        signals.add("exceptions")

                    # Stage 2 の exceptions は注釈シグナルとして扱い、候補化トリガーにしない。
                    if strict_signals == 0:
                        continue
                    ref = {
                        "target": "manual",
                        "manual_id": manual_id,
                        "path": row.path,
                        "start_line": node.line_start,
                        "json_path": None,
                        "title": node.title,
                        "signals": sorted(signals),
                    }
                    item = {
                        "ref": ref,
                        "path": row.path,
                        "start_line": node.line_start,
                        "reason": None,
                        "signals": sorted(signals),
                        "score": round(len(signals) / 4.0, 4),
                        "conflict_with": [],
                        "gap_hint": None,
                    }
                    key = _candidate_key(item)
                    prev = candidates.get(key)
                    if prev is None or item["score"] > prev["score"]:
                        candidates[key] = item
            else:
                scanned_nodes += 1
                normalized_text = normalize_text(text)
                signals: set[str] = set()
                strict_signals = 0
                if any(term in normalized_text for term in expanded_terms):
                    signals.add("normalized")
                    strict_signals += 1
                if any(loose_contains(term, text) for term in expanded_terms):
                    signals.add("loose")
                    strict_signals += 1
                if any(word in normalized_text for word in NORMALIZED_EXCEPTION_WORDS):
                    signals.add("exceptions")
                if strict_signals > 0:
                    item = {
                        "ref": {
                            "target": "manual",
                            "manual_id": manual_id,
                            "path": row.path,
                            "start_line": 1,
                            "json_path": None,
                            "title": Path(row.path).name,
                            "signals": sorted(signals),
                        },
                        "path": row.path,
                        "start_line": 1,
                        "reason": None,
                        "signals": sorted(signals),
                        "score": round(len(signals) / 4.0, 4),
                        "conflict_with": [],
                        "gap_hint": None,
                    }
                    candidates[_candidate_key(item)] = item
        if cutoff_reason:
            break

    ordered = sorted(candidates.values(), key=lambda x: x["score"], reverse=True)
    return ordered, scanned_files, scanned_nodes, warnings, cutoff_reason, unscanned_sections


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
                    node_text = "\n".join(lines[node.line_start - 1 : node.line_end])
                    normalized_text = normalize_text(node_text)
                    if not any(word in normalized_text for word in NORMALIZED_EXCEPTION_WORDS):
                        continue
                    item = {
                        "ref": {
                            "target": "manual",
                            "manual_id": manual_id,
                            "path": row.path,
                            "start_line": node.line_start,
                            "json_path": None,
                            "title": node.title,
                            "signals": ["exceptions"],
                        },
                        "path": row.path,
                        "start_line": node.line_start,
                        "reason": "exceptions_expanded",
                        "signals": ["exceptions"],
                        "score": round(1 / 4.0, 4),
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
                    "score": round(1 / 4.0, 4),
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
    intent: str | None = None,
    max_stage: int | None = None,
    only_unscanned_from_trace_id: str | None = None,
    budget: dict[str, Any] | None = None,
    include_claim_graph: bool | None = None,
) -> dict[str, Any]:
    ensure(bool(query and query.strip()), "invalid_parameter", "query is required")
    ensure(intent in ALLOWED_INTENTS, "invalid_parameter", "invalid intent")
    applied_max_stage = _parse_int_param(max_stage, name="max_stage", default=state.config.default_max_stage)
    ensure(applied_max_stage in {3, 4}, "invalid_parameter", "max_stage must be 3 or 4")

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
    )

    selected_manual_ids = [manual_id] if manual_id else discover_manual_ids(state.config.manuals_root)
    prioritize_paths: dict[str, set[str]] | None = None
    allowed_paths: dict[str, set[str]] | None = None
    escalation_reasons: list[str] = []
    if manual_id:
        ensure(_manual_exists(state.config.manuals_root, manual_id), "not_found", "manual_id not found", {"manual_id": manual_id})

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
            if manual_id and m != manual_id:
                continue
            prioritize_paths.setdefault(m, set()).add(p)
        if prioritize_paths:
            escalation_reasons.append("prioritized_unscanned_sections")

    candidates, scanned_files, scanned_nodes, warnings, cutoff_reason, unscanned = _run_find_pass(
        state=state,
        manual_ids=selected_manual_ids,
        query=query,
        intent=intent,
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
        intent=intent,
        exception_hits=exception_hits,
        candidate_low_threshold=candidate_low_threshold,
        file_bias_threshold=file_bias_threshold,
    )

    scope_expanded = False
    merged_candidates = {(_candidate_key(item)): item for item in candidates}
    if applied_max_stage == 4:
        if should_expand and intent == "exceptions":
            local_paths: dict[str, set[str]] | None = None
            if manual_id:
                seed_paths = {
                    item["path"]
                    for item in candidates
                    if item["ref"]["manual_id"] == manual_id
                }
                if seed_paths:
                    local_paths = {manual_id: seed_paths}
            if local_paths:
                extra, sf2, sn2, w2, cutoff2, unscanned2 = _run_exceptions_expand_pass(
                    state=state,
                    manual_ids=[manual_id] if manual_id else selected_manual_ids,
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
                    intent=intent,
                    exception_hits=exception_hits,
                    candidate_low_threshold=candidate_low_threshold,
                    file_bias_threshold=file_bias_threshold,
                )

            if should_expand and manual_id:
                extra, sf2, sn2, w2, cutoff2, unscanned2 = _run_exceptions_expand_pass(
                    state=state,
                    manual_ids=[manual_id],
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
                    intent=intent,
                    exception_hits=exception_hits,
                    candidate_low_threshold=candidate_low_threshold,
                    file_bias_threshold=file_bias_threshold,
                )

            if should_expand:
                global_ids = discover_manual_ids(state.config.manuals_root)
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
                    intent=intent,
                    exception_hits=exception_hits,
                    candidate_low_threshold=candidate_low_threshold,
                    file_bias_threshold=file_bias_threshold,
                )

        if should_expand and manual_id:
            if total == 0:
                escalation_reasons.append("zero_candidates")
            if total < candidate_low_threshold:
                escalation_reasons.append("low_candidates")
            if total >= 5 and file_bias >= file_bias_threshold:
                escalation_reasons.append("file_bias")
            if intent == "exceptions" and exception_hits == 0:
                escalation_reasons.append("exceptions_missing")
            expanded_ids = discover_manual_ids(state.config.manuals_root)
            extra, sf2, sn2, w2, cutoff2, unscanned2 = _run_find_pass(
                state=state,
                manual_ids=expanded_ids,
                query=query,
                intent=intent,
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
            scope_expanded = True
            escalation_reasons.append("manual_scope_expanded")
    elif manual_id and should_expand:
        if total == 0:
            escalation_reasons.append("zero_candidates")
        if total < candidate_low_threshold:
            escalation_reasons.append("low_candidates")
        if total >= 5 and file_bias >= file_bias_threshold:
            escalation_reasons.append("file_bias")
        if intent == "exceptions" and exception_hits == 0:
            escalation_reasons.append("exceptions_missing")
        cutoff_reason = cutoff_reason or "stage_cap"
        escalation_reasons.append("stage_cap")
        # max_stage=3 の場合、他manualへの拡張を未実行として unscanned に残す。
        for extra_id in discover_manual_ids(state.config.manuals_root):
            if extra_id == manual_id:
                continue
            for row in list_manual_files(state.config.manuals_root, manual_id=extra_id):
                unscanned.append({"manual_id": extra_id, "path": row.path, "reason": "stage_cap"})
    if should_expand and not manual_id:
        if total == 0:
            escalation_reasons.append("zero_candidates")
        if total < candidate_low_threshold:
            escalation_reasons.append("low_candidates")
        if total >= 5 and file_bias >= file_bias_threshold:
            escalation_reasons.append("file_bias")
        if intent == "exceptions" and exception_hits == 0:
            escalation_reasons.append("exceptions_missing")

    claim_graph = _build_claim_graph(
        query=query,
        intent=intent,
        candidates=candidates,
    )
    summary = _build_summary(
        claim_graph=claim_graph,
        candidates=candidates,
        scanned_files=scanned_files,
        scanned_nodes=scanned_nodes,
        intent=intent,
        candidate_low_threshold=candidate_low_threshold,
        file_bias_threshold=file_bias_threshold,
    )
    next_actions = _plan_next_actions(summary, query, intent, applied_max_stage)
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
        "manual_id": manual_id,
        "claim_graph": claim_graph,
        "summary": summary,
        "candidates": candidates,
        "unscanned_sections": [
            {
                "manual_id": item.get("manual_id") or manual_id,
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
    chars_in = len(query)
    chars_out = len(str(summary))
    added_est_tokens = chars_out // 4
    marginal_gain = (len(candidates) / added_est_tokens) if added_est_tokens > 0 else None
    state.adaptive_stats.append(
        {
            "ts": int(time.time() * 1000),
            "query_hash": hashlib.sha256(query.encode("utf-8")).hexdigest()[:16],
            "scanned_files": scanned_files,
            "candidates": len(candidates),
            "warnings": warnings,
            "max_stage_applied": applied_max_stage,
            "scope_expanded": scope_expanded,
            "cutoff_reason": cutoff_reason,
            "unscanned_sections_count": len(unscanned),
            "est_tokens": (chars_in + chars_out + 3) // 4,
            "est_tokens_in": (chars_in + 3) // 4,
            "est_tokens_out": (chars_out + 3) // 4,
            "added_evidence_count": len(candidates),
            "added_est_tokens": added_est_tokens,
            "marginal_gain": round(marginal_gain, 4) if marginal_gain is not None else None,
            "candidate_low_threshold": candidate_low_threshold,
            "file_bias_threshold": file_bias_threshold,
        }
    )

    out: dict[str, Any] = {"trace_id": trace_id, "summary": summary, "next_actions": next_actions}
    if bool(include_claim_graph):
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
