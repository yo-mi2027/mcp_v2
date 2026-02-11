# 統合MCPサーバ v2 Manual仕様（現行）

最終更新: 2026-02-11

## 1. Tool Catalog

- `manual_ls({ id? })`
- `manual_toc({ manual_id })`
- `manual_find({ query, manual_id?, intent?, max_stage?, only_unscanned_from_trace_id?, budget?, include_claim_graph? })`
- `manual_hits({ trace_id, kind?, offset?, limit? })`
- `manual_read({ ref, scope?, limits?, expand? })`
- `manual_scan({ manual_id, path, start_line?, cursor?, chunk_lines?, limits? })`

## 2. `manual_ls`

Input:

```json
{
  "id": "string | null"
}
```

Output:

```json
{
  "id": "string",
  "items": [
    {
      "id": "string",
      "name": "string",
      "kind": "dir|file",
      "path?": "string",
      "file_type?": "md|json"
    }
  ]
}
```

固定ルール:

- `id` 未指定時は `manuals` を適用する。
- `id=manuals` は `MANUALS_ROOT` 直下の manual ディレクトリのみ返す（1階層）。
- manual ディレクトリ `id` を指定すると、その直下の子要素のみ返す（再帰しない）。
- ディレクトリ子は `kind=dir`、対象拡張子（`.md/.json`）のファイルは `kind=file` を返す。
- file `id` は展開不可（`invalid_parameter`）。

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
      "path": "string",
      "headings": [
        {
          "title": "string",
          "line_start": "number"
        }
      ]
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
  "include_claim_graph": "boolean | null",
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
  "claim_graph?": {
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
    "file_bias_ratio": "number",
    "conflict_count": "number",
    "gap_count": "number",
    "integration_status": "ready|needs_followup|blocked"
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

- `claim_graph` が統合の本体で、`summary` は `claim_graph` 由来の派生指標。
- `include_claim_graph=true` のときのみ `claim_graph` を返す。
- `summary.conflict_count` と `manual_hits(kind="conflicts").total` は一致する。
- `summary.gap_count` と `manual_hits(kind="gaps").total` は一致する。
- `max_stage` は `3|4` のみ許可し、未指定時は `DEFAULT_MAX_STAGE` を適用する。
- `budget.time_ms` 既定値は `60000`、`budget.max_candidates` 既定値は `200`。
- `budget.time_ms` と `budget.max_candidates` は整数かつ `>= 1`。
- 整数変換不能値は `invalid_parameter`。

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

- `kind` 未指定時は `candidates`。
- `offset` 未指定時は `0`、`limit` 未指定時は `50`。
- `offset` は整数かつ `>= 0`、`limit` は整数かつ `>= 1`。
- `kind=candidates` の `items[]` は圧縮形式を返す（`target/json_path` は返さない）。
- `kind=candidates` で全件の `manual_id` が同一なら、`manual_id` はレスポンス上位に1回だけ返し、`items[].ref.manual_id` は省略する。

## 6. `manual_read`

Input:

```json
{
  "ref": {
    "target": "manual (or omitted)",
    "manual_id": "string",
    "path": "string",
    "start_line": "number | null"
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

- `ref.target` 未指定時は `manual` を補完。
- `ref.target != manual` は `invalid_parameter`。
- `.md` 既定 `scope=section`、`.json` 既定 `scope=file`。
- `.md` の `scope=file` は `ALLOW_FILE_SCOPE=true` かつ `limits.allow_file=true` 必須。
- `.json` で `scope=section|sections` は `invalid_scope`。
- `limits.max_sections` 既定値は `20`、`limits.max_chars` 既定値は `8000`。
- `limits.max_sections` は整数かつ `>= 1`、`limits.max_chars` は整数かつ `>= 1`。
- `limits` の上限は `HARD_MAX_SECTIONS` / `HARD_MAX_CHARS` でクランプする。
- `expand.before_chars` / `expand.after_chars` は整数かつ `>= 0`。
- `.md` の `scope=section` で同一セクション再要求を検知した場合は、同一ファイルの次行から `manual_scan` 相当の自動フォールバックを行う。

Output:

```json
{
  "text": "string",
  "truncated": "boolean",
  "applied": {
    "scope": "snippet|section|sections|file",
    "max_sections": "number | null",
    "max_chars": "number",
    "mode": "read|scan_fallback"
  }
}
```

## 7. `manual_scan`

Input:

```json
{
  "manual_id": "string",
  "path": "string",
  "start_line": "number | null",
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
- `chunk_lines` は整数かつ `1..VAULT_SCAN_MAX_CHUNK_LINES` の範囲のみ許可。
- `start_line` 指定時はそれを優先し、未指定時は `cursor.start_line`（未指定なら1）を使う。
- `start_line`（または `cursor.start_line`）は整数かつ対象ファイルの総行数範囲内のみ許可。
- `limits.max_chars` は整数かつ `>= 1`、`HARD_MAX_CHARS` で上限制限。

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
  }
}
```
