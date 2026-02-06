from __future__ import annotations

import hashlib
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .errors import ToolError, ensure
from .manual_index import (
    MdNode,
    discover_manual_ids,
    json_line_count,
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

REFERENCE_WORDS = ["参照", "別表", "準ずる"]
SYNONYMS = {
    "対象外": ["除外", "不適用"],
    "手順": ["フロー", "手続き"],
}
ALLOWED_INTENTS = {"definition", "procedure", "eligibility", "exceptions", "compare", "unknown", None}


def _trim_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def manual_list(state: AppState) -> dict[str, Any]:
    return {"items": [{"manual_id": m} for m in discover_manual_ids(state.config.manuals_root)]}


def manual_ls(state: AppState, manual_id: str | None = None) -> dict[str, Any]:
    rows = list_manual_files(state.config.manuals_root, manual_id=manual_id)
    return {
        "items": [
            {"manual_id": row.manual_id, "path": row.path, "file_type": row.file_type}
            for row in rows
        ]
    }


def _manual_exists(root: Path, manual_id: str) -> bool:
    return (root / manual_id).is_dir()


def manual_toc(state: AppState, manual_id: str) -> dict[str, Any]:
    ensure(_manual_exists(state.config.manuals_root, manual_id), "not_found", "manual_id not found", {"manual_id": manual_id})
    items: list[dict[str, Any]] = []
    files = list_manual_files(state.config.manuals_root, manual_id=manual_id)
    for row in files:
        file_path = resolve_inside_root(state.config.manuals_root / manual_id, row.path, must_exist=True)
        text = file_path.read_text(encoding="utf-8")
        if row.file_type == "md":
            for node in parse_markdown_toc(row.path, text):
                items.append(
                    {
                        "kind": node.kind,
                        "node_id": node.node_id,
                        "path": node.path,
                        "title": node.title,
                        "level": node.level,
                        "parent_id": node.parent_id,
                        "line_start": node.line_start,
                        "line_end": node.line_end,
                    }
                )
        else:
            items.append(
                {
                    "kind": "json_file",
                    "node_id": f"{row.path}#file",
                    "path": row.path,
                    "title": Path(row.path).name,
                    "level": 1,
                    "parent_id": None,
                    "line_start": 1,
                    "line_end": json_line_count(text),
                }
            )
    items.sort(key=lambda x: (x["path"], x["line_start"]))
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
    max_sections = int(limits.get("max_sections") or 20)
    max_chars = int(limits.get("max_chars") or 8000)
    max_sections = min(max_sections, state.config.hard_max_sections)
    max_chars = min(max_chars, state.config.hard_max_chars)
    return max_sections, max_chars


def manual_read(
    state: AppState,
    ref: dict[str, Any],
    scope: str | None = None,
    limits: dict[str, Any] | None = None,
    expand: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure(ref.get("target") == "manual", "invalid_parameter", "ref.target must be manual")
    manual_id = ref.get("manual_id")
    ensure(bool(manual_id), "invalid_parameter", "ref.manual_id is required")
    ensure(_manual_exists(state.config.manuals_root, manual_id), "not_found", "manual_id not found", {"manual_id": manual_id})
    relative_path = normalize_relative_path(str(ref.get("path", "")))
    full_path = resolve_inside_root(state.config.manuals_root / manual_id, relative_path, must_exist=True)
    ensure(full_path.exists() and full_path.is_file(), "not_found", "manual file not found", {"path": relative_path})

    suffix = full_path.suffix.casefold()
    text = full_path.read_text(encoding="utf-8")
    default_scope = "file" if suffix == ".json" else "snippet"
    applied_scope = scope or default_scope
    ensure(applied_scope in {"snippet", "section", "sections", "file"}, "invalid_parameter", "invalid scope")
    max_sections, max_chars = _resolve_read_limits(state, limits)
    allow_file = bool((limits or {}).get("allow_file"))
    truncated = False
    output = ""

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
            output = "\n".join(lines[target.line_start - 1 : target.line_end])
        elif applied_scope == "sections":
            selected: list[str] = []
            start_idx = next((i for i, n in enumerate(nodes) if n.node_id == target.node_id), 0)
            for node in nodes[start_idx : start_idx + max_sections]:
                selected.append("\n".join(lines[node.line_start - 1 : node.line_end]))
            output = "\n\n".join(selected)
        else:
            # snippet
            line_no = int(ref.get("start_line") or 1)
            before_chars = 240
            after_chars = 240
            if expand:
                before_chars = max(0, int(expand.get("before_chars") or before_chars))
                after_chars = max(0, int(expand.get("after_chars") or after_chars))
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

        output, truncated = _trim_text(output, max_chars)

    return {
        "text": output,
        "truncated": truncated,
        "applied": {
            "scope": applied_scope,
            "max_sections": max_sections if applied_scope in {"sections", "file"} else None,
            "max_chars": max_chars,
        },
    }


def manual_excepts(state: AppState, manual_id: str, node_id: str | None = None) -> dict[str, Any]:
    ensure(_manual_exists(state.config.manuals_root, manual_id), "not_found", "manual_id not found", {"manual_id": manual_id})
    items: list[dict[str, Any]] = []
    files = list_manual_files(state.config.manuals_root, manual_id=manual_id)
    for row in files:
        file_path = resolve_inside_root(state.config.manuals_root / manual_id, row.path, must_exist=True)
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines, start=1):
            if any(word in line for word in EXCEPTION_WORDS):
                current_node_id = f"{row.path}#L{i}"
                if node_id and node_id != current_node_id:
                    continue
                items.append({"path": row.path, "start_line": i, "snippet": line[:200]})
    return {"items": items}


def _candidate_key(item: dict[str, Any]) -> str:
    ref = item["ref"]
    return f'{ref["manual_id"]}|{ref["path"]}|{ref.get("start_line") or 1}'


def _build_summary(
    candidates: list[dict[str, Any]],
    scanned_files: int,
    scanned_nodes: int,
    warnings: int,
    max_stage: int,
    scope_expanded: bool,
    cutoff_reason: str | None,
    unscanned_count: int,
    intent: str | None,
    candidate_low_threshold: int,
    file_bias_threshold: float,
    escalation_reasons: list[str],
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
    gap_count = 0
    if (
        total == 0
        or total < candidate_low_threshold
        or (total >= 5 and file_bias >= file_bias_threshold)
        or (intent == "exceptions" and exception_hits == 0)
    ):
        gap_count = 1

    sufficiency_score = min(1.0, total / 5.0) * (1.0 - min(file_bias, 1.0) * 0.2)
    status = "ready" if (sufficiency_score >= 0.6 and gap_count == 0) else "needs_followup"
    if total == 0:
        status = "blocked"
    summary: dict[str, Any] = {
        "scanned_files": scanned_files,
        "scanned_nodes": scanned_nodes,
        "candidates": total,
        "warnings": warnings,
        "max_stage_applied": max_stage,
        "scope_expanded": scope_expanded,
        "unscanned_sections_count": unscanned_count,
        "integrated_candidates": total,
        "integrated_nodes": total,
        "signal_coverage": {
            "heading": signal_counts.get("heading", 0),
            "normalized": signal_counts.get("normalized", 0),
            "loose": signal_counts.get("loose", 0),
            "exceptions": signal_counts.get("exceptions", 0),
            "reference": signal_counts.get("reference", 0),
        },
        "file_bias_ratio": round(file_bias, 4),
        "conflict_count": 0,
        "gap_count": gap_count,
        "sufficiency_score": round(sufficiency_score, 4),
        "integration_status": status,
    }
    if cutoff_reason:
        summary["cutoff_reason"] = cutoff_reason
    if escalation_reasons:
        summary["escalation_reasons"] = escalation_reasons
    return summary


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

    for manual_id in manual_ids:
        files = list_manual_files(state.config.manuals_root, manual_id=manual_id)
        if prioritize_paths and manual_id in prioritize_paths:
            preferred = prioritize_paths[manual_id]
            files.sort(key=lambda r: (r.path not in preferred, r.path))
        for row in files:
            if allowed_paths is not None and row.path not in allowed_paths.get(manual_id, set()):
                continue
            elapsed_ms = int((time.monotonic() - start) * 1000)
            if elapsed_ms > budget_time_ms:
                cutoff_reason = "time_budget"
                unscanned_sections.append({"manual_id": manual_id, "path": row.path, "reason": "time_budget"})
                break
            if len(candidates) >= max_candidates:
                cutoff_reason = "candidate_cap"
                unscanned_sections.append({"manual_id": manual_id, "path": row.path, "reason": "candidate_cap"})
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

                    if any(term in normalized_title for term in base_terms):
                        signals.add("heading")
                    if any(term in normalized_text for term in expanded_terms):
                        signals.add("normalized")
                    if any(loose_contains(term, node_text) for term in expanded_terms):
                        signals.add("loose")
                    if any(word.casefold() in normalized_text for word in [normalize_text(w) for w in EXCEPTION_WORDS]):
                        signals.add("exceptions")
                    if any(word.casefold() in normalized_text for word in [normalize_text(w) for w in REFERENCE_WORDS]):
                        signals.add("reference")

                    if not signals:
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
                        "score": round(len(signals) / 5.0, 4),
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
                if any(term in normalized_text for term in expanded_terms):
                    signals.add("normalized")
                if any(loose_contains(term, text) for term in expanded_terms):
                    signals.add("loose")
                if any(normalize_text(w) in normalized_text for w in EXCEPTION_WORDS):
                    signals.add("exceptions")
                if any(normalize_text(w) in normalized_text for w in REFERENCE_WORDS):
                    signals.add("reference")
                if signals:
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
                        "score": round(len(signals) / 5.0, 4),
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
) -> dict[str, Any]:
    ensure(bool(query and query.strip()), "invalid_parameter", "query is required")
    ensure(intent in ALLOWED_INTENTS, "invalid_parameter", "invalid intent")
    applied_max_stage = int(max_stage if max_stage is not None else state.config.default_max_stage)
    ensure(applied_max_stage in {3, 4}, "invalid_parameter", "max_stage must be 3 or 4")

    budget = budget or {}
    budget_time_ms = int(budget.get("time_ms") or 60000)
    max_candidates = int(budget.get("max_candidates") or 200)
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
            allowed_paths = {m: set(paths) for m, paths in prioritize_paths.items()}
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

    total = len(candidates)
    file_counts = Counter(item["path"] for item in candidates)
    file_bias = (max(file_counts.values()) / total) if total else 0.0
    exception_hits = sum(1 for item in candidates if "exceptions" in item["signals"])
    should_expand = _should_expand_scope(
        total=total,
        file_bias=file_bias,
        intent=intent,
        exception_hits=exception_hits,
        candidate_low_threshold=candidate_low_threshold,
        file_bias_threshold=file_bias_threshold,
    )

    scope_expanded = False
    if applied_max_stage == 4:
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
            merged = {(_candidate_key(item)): item for item in candidates}
            for item in extra:
                merged[_candidate_key(item)] = item
            candidates = sorted(merged.values(), key=lambda x: x["score"], reverse=True)
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

    summary = _build_summary(
        candidates=candidates,
        scanned_files=scanned_files,
        scanned_nodes=scanned_nodes,
        warnings=warnings,
        max_stage=applied_max_stage,
        scope_expanded=scope_expanded,
        cutoff_reason=cutoff_reason,
        unscanned_count=len(unscanned),
        intent=intent,
        candidate_low_threshold=candidate_low_threshold,
        file_bias_threshold=file_bias_threshold,
        escalation_reasons=sorted(set(escalation_reasons)),
    )
    next_actions = _plan_next_actions(summary, query, intent, applied_max_stage)

    trace_payload = {
        "query": query,
        "manual_id": manual_id,
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
        "conflicts": [],
        "gaps": (
            [
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
            ]
            if summary["gap_count"] > 0
            else []
        ),
        "integrated_top": [
            {**item, "reason": "ranked_by_integration"}
            for item in candidates[:20]
        ],
        "escalation_reasons": summary.get("escalation_reasons", []),
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
            "max_stage_applied": summary["max_stage_applied"],
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

    return {"trace_id": trace_id, "summary": summary, "next_actions": next_actions}


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
    ensure(applied_kind in {"candidates", "unscanned", "conflicts", "gaps", "integrated_top"}, "invalid_parameter", "invalid kind")
    applied_offset = max(0, int(offset or 0))
    applied_limit = max(1, int(limit or 50))

    key_map = {
        "candidates": "candidates",
        "unscanned": "unscanned_sections",
        "conflicts": "conflicts",
        "gaps": "gaps",
        "integrated_top": "integrated_top",
    }
    rows = payload.get(key_map[applied_kind], [])
    sliced = rows[applied_offset : applied_offset + applied_limit]
    return {
        "trace_id": trace_id,
        "kind": applied_kind,
        "offset": applied_offset,
        "limit": applied_limit,
        "total": len(rows),
        "items": sliced,
    }
