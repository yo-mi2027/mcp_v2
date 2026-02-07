# 統合MCPサーバ v2 Manual仕様（現行）

## 1. Tool Catalog

- `manual_ls({ manual_id? })`
- `manual_toc({ manual_id })`
- `manual_find({ query, manual_id?, intent?, max_stage?, only_unscanned_from_trace_id?, budget? })`
- `manual_hits({ trace_id, kind?, offset?, limit? })`
- `manual_read({ ref, scope?, limits?, expand? })`
- `manual_scan({ manual_id, path, cursor?, chunk_lines?, limits? })`

## 2. `manual_ls`

Input:

```json
{
  "manual_id": "string | null"
}
```

Output:

```json
{
  "items": [
    {
      "manual_id": "string",
      "path": "string",
      "file_type": "md|json"
    }
  ]
}
```

## 3. `manual_toc`

Input:

```json
{
  "manual_id": "string"
}
```

Output:

```json
{
  "items": [
    {
      "kind": "heading|json_file",
      "node_id": "string",
      "path": "string",
      "title": "string",
      "level": "number",
      "parent_id": "string | null",
      "line_start": "number",
      "line_end": "number"
    }
  ]
}
```

## 4. `manual_find`

Input:

```json
{
  "query": "string",
  "manual_id": "string | null",
  "intent": "definition|procedure|eligibility|exceptions|compare|unknown|null",
  "max_stage": "number | null",
  "only_unscanned_from_trace_id": "string | null",
  "budget": {
    "max_candidates": "number | null",
    "time_ms": "number | null"
  }
}
```

Output:

```json
{
  "trace_id": "string",
  "claim_graph": {
    "claims": [
      {
        "claim_id": "string",
        "facet": "definition|procedure|eligibility|exceptions|compare|unknown",
        "text": "string",
        "status": "supported|conflicted|unresolved",
        "confidence": "number"
      }
    ],
    "evidences": [
      {
        "evidence_id": "string",
        "ref": {
          "target": "manual",
          "manual_id": "string",
          "path": "string",
          "start_line": "number"
        },
        "signals": ["heading|normalized|loose|exceptions"],
        "score": "number",
        "snippet_digest": "string"
      }
    ],
    "edges": [
      {
        "from_claim_id": "string",
        "to_evidence_id": "string",
        "relation": "supports|contradicts|requires_followup",
        "confidence": "number"
      }
    ],
    "facets": [
      {
        "facet": "definition|procedure|eligibility|exceptions|compare|unknown",
        "claim_count": "number",
        "supported_count": "number",
        "conflicted_count": "number",
        "unresolved_count": "number",
        "coverage_status": "covered|partial|missing"
      }
    ]
  },
  "summary": {
    "scanned_files": "number",
    "scanned_nodes": "number",
    "candidates": "number",
    "warnings": "number",
    "max_stage_applied": "number",
    "scope_expanded": "boolean",
    "unscanned_sections_count": "number",
    "integrated_candidates": "number",
    "integrated_nodes": "number",
    "signal_coverage": {
      "heading": "number",
      "normalized": "number",
      "loose": "number",
      "exceptions": "number"
    },
    "file_bias_ratio": "number",
    "conflict_count": "number",
    "gap_count": "number",
    "claim_count": "number",
    "supported_claim_count": "number",
    "conflicted_claim_count": "number",
    "unresolved_claim_count": "number",
    "sufficiency_score": "number",
    "integration_status": "ready|needs_followup|blocked",
    "cutoff_reason?": "time_budget|candidate_cap|stage_cap|hard_limit",
    "escalation_reasons?": ["string"]
  },
  "next_actions": [
    {
      "type": "manual_hits|manual_read|manual_find|stop",
      "confidence": "number | null",
      "params": "object | null"
    }
  ]
}
```

固定ルール:

- `claim_graph` が統合の本体で、`summary` は `claim_graph` から計算する派生指標とする。
- 互換性維持のため、既存 `summary` フィールド（`candidates`, `integrated_candidates`, `signal_coverage` など）は当面維持する。
- `claim_graph.claims` は複数件になりうる（facet単位で生成される）。
- `summary.conflict_count` と `manual_hits(kind="conflicts").total` は一致する。
- `summary.gap_count` と `manual_hits(kind="gaps").total` は一致する。
- `budget.time_ms` 既定値は `60000`、`budget.max_candidates` 既定値は `200`。
- `max_stage` は `3|4` のみ許可し、未指定時は `DEFAULT_MAX_STAGE` を適用する。

## 5. `manual_hits`

Input:

```json
{
  "trace_id": "string",
  "kind": "candidates|unscanned|conflicts|gaps|integrated_top|claims|evidences|edges|null",
  "offset": "number | null",
  "limit": "number | null"
}
```

Output:

```json
{
  "trace_id": "string",
  "kind": "string",
  "offset": "number",
  "limit": "number",
  "total": "number",
  "items": "array"
}
```

固定ルール:

- `kind` 未指定時は `candidates` を適用する。
- `offset` 未指定時は `0`、`limit` 未指定時は `50` を適用する。
- `claims|evidences|edges` は当該 `trace_id` の `claim_graph` からページング取得する。

## 6. `manual_read`

Input:

```json
{
  "ref": {
    "target": "manual (or omitted)",
    "manual_id": "string",
    "path": "string",
    "start_line": "number | null",
    "json_path": "string | null"
  },
  "scope": "snippet|section|sections|file|null",
  "limits": {
    "max_sections": "number | null",
    "max_chars": "number | null",
    "allow_file": "boolean | null"
  },
  "expand": {
    "before_chars": "number | null",
    "after_chars": "number | null"
  }
}
```

固定ルール:

- `ref.target` 未指定時は `manual` を補完
- `ref.target != manual` は `invalid_parameter`
- `.md` 既定 `scope=snippet`、`.json` 既定 `scope=file`
- `.md` の `scope=file` は `ALLOW_FILE_SCOPE=true` かつ `limits.allow_file=true` 必須
- `.json` で `scope=section|sections` は `invalid_scope`
- `limits.max_sections` 既定値は `20`、`limits.max_chars` 既定値は `8000`
- `limits` の上限は `HARD_MAX_SECTIONS` / `HARD_MAX_CHARS` でクランプする

Output:

```json
{
  "text": "string",
  "truncated": "boolean",
  "applied": {
    "scope": "snippet|section|sections|file",
    "max_sections": "number | null",
    "max_chars": "number"
  }
}
```

## 7. `manual_scan`

Input:

```json
{
  "manual_id": "string",
  "path": "string",
  "cursor": {
    "start_line": "number | null"
  },
  "chunk_lines": "number | null",
  "limits": {
    "max_chars": "number | null"
  }
}
```

固定ルール:

- `chunk_lines` 未指定時は `VAULT_SCAN_DEFAULT_CHUNK_LINES` を適用する。
- `chunk_lines` は `1..VAULT_SCAN_MAX_CHUNK_LINES` の範囲のみ許可する。
- `cursor.start_line` は対象ファイルの総行数範囲内のみ許可する。

Output:

```json
{
  "manual_id": "string",
  "path": "string",
  "text": "string",
  "applied_range": {
    "start_line": "number",
    "end_line": "number"
  },
  "next_cursor": {
    "start_line": "number | null"
  },
  "eof": "boolean",
  "truncated": "boolean",
  "truncated_reason": "none|chunk_end|max_chars|hard_limit",
  "applied": {
    "chunk_lines": "number",
    "max_chars": "number"
  },
  "next_actions": [
    {
      "type": "manual_scan|stop",
      "confidence": "number",
      "params": "object | null"
    }
  ]
}
```
