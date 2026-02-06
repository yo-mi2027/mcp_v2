from __future__ import annotations

import re
import time
import os
from collections import Counter
from pathlib import Path
from typing import Any

from .errors import ToolError, ensure
from .normalization import loose_contains, normalize_text, split_terms
from .path_guard import (
    is_daily_path_under_root,
    is_system_path_under_root,
    normalize_relative_path,
    resolve_inside_root,
    validate_daily_filename,
)
from .state import AppState

SOURCE_LINES_RE = re.compile(r"source_lines\s*:\s*(\d+)\s*-\s*(\d+)", re.IGNORECASE)


def _collect_text_files(root: Path, relative_dir: str | None = None, glob: str | None = None) -> list[Path]:
    start = root
    if relative_dir:
        rel = normalize_relative_path(relative_dir)
        start = root / rel
    if not start.exists():
        return []
    pattern = glob or "**/*"

    def _matches(rel: str) -> bool:
        if pattern == "**/*":
            return True
        if Path(rel).match(pattern):
            return True
        if pattern.startswith("**/") and Path(rel).match(pattern[3:]):
            return True
        return False

    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(start, followlinks=False):
        base = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not (base / d).is_symlink()]
        for name in filenames:
            p = base / name
            if p.is_symlink():
                continue
            rel = p.relative_to(start).as_posix()
            if _matches(rel):
                files.append(p)
    return sorted(files)


def _resolve_max_chars(state: AppState, limits: dict[str, Any] | None) -> int:
    max_chars = int((limits or {}).get("max_chars") or state.config.hard_max_chars)
    return min(max_chars, state.config.hard_max_chars)


def _range_from_lines(total: int, range_obj: dict[str, Any] | None) -> tuple[int, int]:
    ensure(range_obj is not None, "invalid_parameter", "range is required when full=false")
    start = int(range_obj.get("start_line") or 1)
    end = int(range_obj.get("end_line") or total)
    ensure(start >= 1 and end >= start, "invalid_parameter", "invalid range")
    ensure(start <= total, "invalid_parameter", "range.start_line out of range")
    return start, min(end, total)


def _merge_ranges(ranges: list[dict[str, int]]) -> list[dict[str, int]]:
    if not ranges:
        return []
    sorted_ranges = sorted(
        [{"start_line": int(r["start_line"]), "end_line": int(r["end_line"])} for r in ranges],
        key=lambda x: x["start_line"],
    )
    merged = [sorted_ranges[0]]
    for curr in sorted_ranges[1:]:
        prev = merged[-1]
        if curr["start_line"] <= prev["end_line"] + 1:
            prev["end_line"] = max(prev["end_line"], curr["end_line"])
        else:
            merged.append(curr)
    return merged


def _normalize_cited_ranges(total_lines: int, cited_ranges: list[dict[str, int]]) -> list[dict[str, int]]:
    normalized: list[dict[str, int]] = []
    for r in cited_ranges:
        ensure("start_line" in r and "end_line" in r, "invalid_parameter", "cited_ranges item is missing required fields")
        start = int(r["start_line"])
        end = int(r["end_line"])
        ensure(start >= 1 and end >= start, "invalid_parameter", "invalid cited range")
        if total_lines == 0:
            continue
        clamped_start = min(start, total_lines)
        clamped_end = min(end, total_lines)
        if clamped_end >= clamped_start:
            normalized.append({"start_line": clamped_start, "end_line": clamped_end})
    return _merge_ranges(normalized)


def vault_ls(state: AppState, relative_dir: str | None = None) -> dict[str, Any]:
    root = state.config.vault_root
    base = root
    if relative_dir:
        base = resolve_inside_root(root, relative_dir, must_exist=False)
    entries: list[dict[str, Any]] = []
    if not base.exists():
        return {"entries": []}
    for p in sorted(base.iterdir()):
        path = p.relative_to(root).as_posix()
        entries.append(
            {
                "path": path,
                "type": "dir" if p.is_dir() else "file",
                "size_bytes": None if p.is_dir() else p.stat().st_size,
            }
        )
    return {"entries": entries}


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
        "next_offset": {"start_line": None if end_line >= total else end_line + 1},
        "truncated_reason": truncated_reason,
    }


def vault_search(
    state: AppState,
    query: str,
    mode: str | None = None,
    glob: str | None = None,
    relative_dir: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    ensure(bool(query and query.strip()), "invalid_parameter", "query is required")
    applied_mode = (mode or "regex").casefold()
    if applied_mode not in {"plain", "regex", "loose"}:
        applied_mode = "plain"
    applied_limit = int(limit or 50)
    ensure(applied_limit > 0, "invalid_parameter", "limit must be positive")

    results: list[dict[str, Any]] = []
    files = _collect_text_files(state.config.vault_root, relative_dir=relative_dir, glob=glob)
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        matched = False
        snippet = ""
        if applied_mode == "plain":
            idx = text.find(query)
            matched = idx >= 0
            if matched:
                snippet = text[max(0, idx - 40) : idx + 120]
        elif applied_mode == "regex":
            try:
                m = re.search(query, text, flags=re.IGNORECASE | re.MULTILINE)
            except re.error:
                m = re.search(re.escape(query), text, flags=re.IGNORECASE)
            matched = m is not None
            if m:
                snippet = text[max(0, m.start() - 40) : m.end() + 120]
        else:
            matched = loose_contains(query, text)
            if matched:
                snippet = text[:160]

        if matched:
            results.append({"path": path.relative_to(state.config.vault_root).as_posix(), "snippet": snippet})
            if len(results) >= applied_limit:
                break

    return {"results": results}


def _enforce_vault_policy_on_create(vault_root: Path, path: str) -> None:
    if is_system_path_under_root(vault_root, path):
        raise ToolError("forbidden", ".system path is reserved for system-managed files", {"path": path})
    if is_daily_path_under_root(vault_root, path):
        validate_daily_filename(path)


def _enforce_vault_policy_on_write(vault_root: Path, path: str, mode: str) -> None:
    if is_system_path_under_root(vault_root, path):
        raise ToolError("forbidden", ".system path is reserved for system-managed files", {"path": path})
    if is_daily_path_under_root(vault_root, path):
        validate_daily_filename(path)
        ensure(mode == "append", "forbidden", "daily files allow append only")


def _enforce_vault_policy_on_replace(vault_root: Path, path: str) -> None:
    if is_system_path_under_root(vault_root, path):
        raise ToolError("forbidden", ".system path is reserved for system-managed files", {"path": path})
    if is_daily_path_under_root(vault_root, path):
        raise ToolError("forbidden", "replace is forbidden under daily/")


def vault_create(state: AppState, path: str, content: str) -> dict[str, Any]:
    normalized = normalize_relative_path(path)
    _enforce_vault_policy_on_create(state.config.vault_root, normalized)
    target = resolve_inside_root(state.config.vault_root, normalized, must_exist=False)
    ensure(not target.exists(), "conflict", "file already exists", {"path": normalized})
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"written_path": normalized, "written_bytes": len(content.encode("utf-8"))}


def vault_write(state: AppState, path: str, content: str, mode: str) -> dict[str, Any]:
    ensure(mode in {"overwrite", "append"}, "invalid_parameter", "mode must be overwrite or append")
    normalized = normalize_relative_path(path)
    _enforce_vault_policy_on_write(state.config.vault_root, normalized, mode)
    target = resolve_inside_root(state.config.vault_root, normalized, must_exist=False)
    ensure(target.exists(), "conflict", "vault_write requires existing file", {"path": normalized})
    ensure(target.is_file(), "not_found", "target is not a file", {"path": normalized})
    write_mode = "a" if mode == "append" else "w"
    with target.open(write_mode, encoding="utf-8") as f:
        f.write(content)
    return {"written_path": normalized, "written_bytes": len(content.encode("utf-8"))}


def vault_replace(state: AppState, path: str, find: str, replace: str, max_replacements: int | None = None) -> dict[str, Any]:
    normalized = normalize_relative_path(path)
    _enforce_vault_policy_on_replace(state.config.vault_root, normalized)
    target = resolve_inside_root(state.config.vault_root, normalized, must_exist=True)
    ensure(target.is_file(), "not_found", "target is not a file", {"path": normalized})
    data = target.read_text(encoding="utf-8")
    max_count = int(max_replacements or 1)
    new_data, count = re.subn(re.escape(find), replace, data, count=max_count)
    target.write_text(new_data, encoding="utf-8")
    return {"written_path": normalized, "replacements": count}


def vault_find(
    state: AppState,
    query: str,
    scope: dict[str, Any] | None = None,
    budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure(bool(query and query.strip()), "invalid_parameter", "query is required")
    scope = scope or {}
    budget = budget or {}
    max_candidates = int(budget.get("max_candidates") or 200)
    time_ms = int(budget.get("time_ms") or 60000)
    files = _collect_text_files(
        state.config.vault_root,
        relative_dir=scope.get("relative_dir"),
        glob=scope.get("glob") or "**/*",
    )
    start = time.monotonic()
    terms = split_terms(query)
    if not terms:
        terms = [normalize_text(query)]
    candidates: list[dict[str, Any]] = []
    warnings = 0
    cutoff_reason: str | None = None
    for p in files:
        if int((time.monotonic() - start) * 1000) > time_ms:
            cutoff_reason = "time_budget"
            break
        if len(candidates) >= max_candidates:
            cutoff_reason = "candidate_cap"
            break
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            warnings += 1
            continue
        normalized = normalize_text(text)
        signals: set[str] = set()
        if any(t in normalized for t in terms):
            signals.add("normalized")
        if any(loose_contains(t, text) for t in terms):
            signals.add("loose")
        if not signals:
            continue
        lines = text.splitlines()
        start_line = 1
        for i, line in enumerate(lines, start=1):
            if any(t in normalize_text(line) for t in terms):
                start_line = i
                break
        candidates.append(
            {
                "path": p.relative_to(state.config.vault_root).as_posix(),
                "start_line": start_line,
                "signals": sorted(signals),
                "score": round(len(signals) / 2.0, 4),
            }
        )
    file_counts = Counter(c["path"] for c in candidates)
    total = len(candidates)
    file_bias = (max(file_counts.values()) / total) if total else 0.0
    sufficiency = min(1.0, total / 5.0) * (1.0 - min(file_bias, 1.0) * 0.2)
    status = "ready" if sufficiency >= 0.6 else "needs_followup"
    if total == 0:
        status = "blocked"
    summary: dict[str, Any] = {
        "scanned_files": len(files),
        "scanned_nodes": len(files),
        "candidates": total,
        "warnings": warnings,
        "max_stage_applied": 1.5,
        "scope_expanded": False,
        "integrated_candidates": total,
        "signal_coverage": {
            "normalized": sum(1 for c in candidates if "normalized" in c["signals"]),
            "loose": sum(1 for c in candidates if "loose" in c["signals"]),
        },
        "file_bias_ratio": round(file_bias, 4),
        "gap_ranges_count": 0 if total else 1,
        "sufficiency_score": round(sufficiency, 4),
        "integration_status": status,
    }
    if cutoff_reason:
        summary["cutoff_reason"] = cutoff_reason
    if summary["gap_ranges_count"] > 0:
        scan_path = candidates[0]["path"] if candidates else (files[0].relative_to(state.config.vault_root).as_posix() if files else None)
        if scan_path:
            next_actions = [
                {
                    "type": "vault_scan",
                    "confidence": 0.7,
                    "params": {"path": scan_path, "cursor": {"start_line": 1}},
                }
            ]
        else:
            next_actions = [{"type": "vault_find", "confidence": 0.5, "params": {"query": query, "scope": scope}}]
    elif file_bias >= 0.80:
        next_actions = [{"type": "vault_find", "confidence": 0.6, "params": {"query": query, "scope": scope}}]
    elif status == "ready":
        next_actions = [{"type": "stop", "confidence": 0.9, "params": None}]
    else:
        next_actions = [{"type": "stop", "confidence": 0.7, "params": None}]
    trace_id = state.traces.create({"kind": "vault_find", "query": query, "candidates": candidates, "summary": summary})
    return {"trace_id": trace_id, "summary": summary, "next_actions": next_actions}


def vault_scan(
    state: AppState,
    path: str,
    cursor: dict[str, Any] | None = None,
    chunk_lines: int | None = None,
    limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_relative_path(path)
    target = resolve_inside_root(state.config.vault_root, normalized, must_exist=True)
    text = target.read_text(encoding="utf-8")
    lines = text.splitlines()
    total = max(1, len(lines))

    start_line = int((cursor or {}).get("start_line") or 1)
    applied_chunk = int(chunk_lines or state.config.vault_scan_default_chunk_lines)
    ensure(
        1 <= applied_chunk <= state.config.vault_scan_max_chunk_lines,
        "invalid_parameter",
        "chunk_lines out of range",
    )
    ensure(1 <= start_line <= total, "invalid_parameter", "cursor.start_line out of range")
    end_line = min(total, start_line + applied_chunk - 1)
    chunk_text = "\n".join(lines[start_line - 1 : end_line])
    max_chars = _resolve_max_chars(state, limits)

    truncated_reason = "none"
    if len(chunk_text) > max_chars:
        chunk_text = chunk_text[:max_chars]
        truncated_reason = "hard_limit" if max_chars >= state.config.hard_max_chars else "max_chars"
    elif end_line < total:
        truncated_reason = "chunk_end"
    eof = end_line >= total

    if eof:
        next_actions = [
            {
                "type": "vault_coverage",
                "confidence": 0.7,
                "params": {"path": normalized, "cited_ranges": [{"start_line": start_line, "end_line": end_line}]},
            }
        ]
    else:
        next_actions = [
            {
                "type": "vault_scan",
                "confidence": 0.8,
                "params": {"path": normalized, "cursor": {"start_line": end_line + 1}, "chunk_lines": applied_chunk},
            }
        ]

    return {
        "text": chunk_text,
        "applied_range": {"start_line": start_line, "end_line": end_line},
        "next_cursor": {"start_line": None if eof else end_line + 1},
        "eof": eof,
        "truncated": truncated_reason != "none",
        "truncated_reason": truncated_reason,
        "next_actions": next_actions,
    }


def vault_coverage(state: AppState, path: str, cited_ranges: list[dict[str, int]]) -> dict[str, Any]:
    normalized = normalize_relative_path(path)
    target = resolve_inside_root(state.config.vault_root, normalized, must_exist=True)
    text = target.read_text(encoding="utf-8")
    total_lines = len(text.splitlines())
    merged = _normalize_cited_ranges(total_lines, cited_ranges)
    covered_lines = sum((r["end_line"] - r["start_line"] + 1) for r in merged)
    coverage_ratio = covered_lines / total_lines if total_lines else 1.0

    uncovered: list[dict[str, int]] = []
    current = 1
    for r in merged:
        if r["start_line"] > current:
            uncovered.append({"start_line": current, "end_line": r["start_line"] - 1})
        current = max(current, r["end_line"] + 1)
    if total_lines and current <= total_lines:
        uncovered.append({"start_line": current, "end_line": total_lines})

    meets = coverage_ratio >= state.config.coverage_min_ratio
    if meets:
        next_actions = [{"type": "vault_audit", "confidence": 0.6, "params": None}]
    else:
        params = {"path": normalized, "cursor": {"start_line": uncovered[0]["start_line"]}} if uncovered else None
        next_actions = [{"type": "vault_scan", "confidence": 0.8, "params": params}]
    return {
        "path": normalized,
        "total_lines": total_lines,
        "covered_lines": covered_lines,
        "coverage_ratio": round(coverage_ratio, 4),
        "covered_ranges": merged,
        "uncovered_ranges": uncovered,
        "meets_min_coverage": meets,
        "next_actions": next_actions,
    }


def _extract_cited_ranges_from_report(report_text: str) -> list[dict[str, int]]:
    ranges: list[dict[str, int]] = []
    for m in SOURCE_LINES_RE.finditer(report_text):
        ranges.append({"start_line": int(m.group(1)), "end_line": int(m.group(2))})
    return ranges


def vault_audit(
    state: AppState,
    report_path: str,
    source_path: str,
    cited_ranges: list[dict[str, int]] | None = None,
) -> dict[str, Any]:
    report_rel = normalize_relative_path(report_path)
    source_rel = normalize_relative_path(source_path)
    report_abs = resolve_inside_root(state.config.vault_root, report_rel, must_exist=True)
    resolve_inside_root(state.config.vault_root, source_rel, must_exist=True)

    report_text = report_abs.read_text(encoding="utf-8")
    if cited_ranges is None:
        cited_ranges = _extract_cited_ranges_from_report(report_text)
    coverage = vault_coverage(state, source_rel, cited_ranges)
    coverage_ratio = coverage["coverage_ratio"] if cited_ranges else None

    lines = report_text.splitlines()
    rootless_nodes = sum(1 for line in lines if ("rootless_node" in line or "根拠なし要素" in line))
    orphan_branches = sum(1 for line in lines if ("orphan_branch" in line or "孤立分岐" in line))
    one_way_refs = sum(1 for line in lines if ("one_way_ref" in line or "片方向参照" in line))

    findings: list[dict[str, Any]] = []
    if rootless_nodes:
        findings.append({"kind": "rootless_node", "message": "rootless nodes detected", "node_id": None})
    if orphan_branches:
        findings.append({"kind": "orphan_branch", "message": "orphan branches detected", "node_id": None})
    if one_way_refs:
        findings.append({"kind": "one_way_ref", "message": "one-way refs detected", "node_id": None})

    uncovered_count = len(coverage["uncovered_ranges"])
    added_evidence = len(cited_ranges or [])
    added_tokens = max(1, len(str(cited_ranges or [])) // 4)
    marginal_gain = added_evidence / added_tokens if added_tokens else None
    needs_forced = bool(
        (coverage_ratio is not None and coverage_ratio < state.config.coverage_min_ratio)
        or rootless_nodes > 0
        or orphan_branches > 0
        or one_way_refs > 0
        or ((marginal_gain or 0) >= state.config.marginal_gain_min and uncovered_count > 0)
    )

    if needs_forced:
        next_actions = [{"type": "vault_scan", "confidence": 0.8, "params": {"path": source_rel}}]
    else:
        next_actions = [{"type": "stop", "confidence": 0.9, "params": None}]

    return {
        "report_path": report_rel,
        "source_path": source_rel,
        "rootless_nodes": rootless_nodes,
        "orphan_branches": orphan_branches,
        "one_way_refs": one_way_refs,
        "coverage_ratio": coverage_ratio,
        "uncovered_ranges_count": uncovered_count,
        "marginal_gain": round(marginal_gain, 4) if marginal_gain is not None else None,
        "needs_forced_full_scan": needs_forced,
        "next_actions": next_actions,
        "findings": findings,
    }
