# 統合MCPサーバ v2 Manual仕様（現行）

最終更新: 2026-02-13

## 1. Tool Catalog

- `manual_ls({ id? })`
- `manual_toc({ manual_id })`
- `manual_find({ query, manual_id?, expand_scope?, only_unscanned_from_trace_id?, budget?, include_claim_graph?, use_cache? })`
- `manual_hits({ trace_id, kind?, offset?, limit? })`
- `manual_read({ ref, scope?, allow_file?, expand? })`
- `manual_scan({ manual_id, path, start_line?, cursor? })`

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
  "expand_scope": "boolean | null",
  "only_unscanned_from_trace_id": "string | null",
  "include_claim_graph": "boolean | null",
  "use_cache": "boolean | null",
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
        "signals": ["heading|heading_focus|normalized|loose|exceptions|late_rerank"],
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
      "type": "manual_hits|manual_read|manual_find",
      "confidence": "number | null",
      "params": "object | null"
    }
  ]
}
```

固定ルール:

- `claim_graph` が統合の本体で、`summary` は `claim_graph` 由来の派生指標。
- 一次候補の検索スコアは重み付き疎ベクトル（BM25）を基礎とし、`heading` / `exceptions` などのシグナル補正と query coverage 補正（`SPARSE_QUERY_COVERAGE_WEIGHT`）を加える。
- `LATE_RERANK_ENABLED=true` または `late_reranker` hook が設定されている場合、候補上位に late interaction rerank を適用する。
- `include_claim_graph=true` のときのみ `claim_graph` を返す。
- `summary.conflict_count` と `manual_hits(kind="conflicts").total` は一致する。
- `summary.gap_count` と `manual_hits(kind="gaps").total` は一致する。
- `expand_scope` 未指定時は `true` を適用する。
- `CORRECTIVE_ENABLED=true` の場合、stage3 結果の品質（`gap/conflict/coverage/top-score margin/candidates`）に応じて stage4 へ昇格する。
- `manual_id` 未指定かつ `DEFAULT_MANUAL_ID` が設定されている場合は、その `manual_id` を適用する。
- `expand_scope` と `include_claim_graph` と `use_cache` は boolean のみ許可（非booleanは `invalid_parameter`）。
- `use_cache` 未指定時は `SEM_CACHE_ENABLED` 設定値を適用する。
- cache hit でも `summary.gap_count/conflict_count` が閾値（`SEM_CACHE_MAX_SUMMARY_GAP/SEM_CACHE_MAX_SUMMARY_CONFLICT`）を超える場合は再探索する。
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
    "manual_id": "string",
    "path": "string",
    "start_line": "number | null"
  },
  "scope": "snippet|section|sections|file|null",
  "allow_file": "boolean | null",
  "expand": {
    "before_chars": "number | null",
    "after_chars": "number | null"
  }
}
```

固定ルール:

- `.md` 既定 `scope=section`、`.json` 既定 `scope=file`。
- `.md` の `scope=file` は `ALLOW_FILE_SCOPE=true` かつ `allow_file=true` 必須。
- `allow_file` は boolean のみ許可（非booleanは `invalid_parameter`）。
- `.json` で `scope=section|sections` は `invalid_scope`。
- `max_sections` は固定値 `20`、`max_chars` は固定値 `12000`。
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
    "start_line": "number | null",
    "char_offset": "number | null"
  } | "number | string (char_offset shorthand) | null"
}
```

固定ルール:

- `start_line` 指定時はそれを優先し、未指定時は `cursor.start_line`（未指定なら1）を使う。
- `cursor` は object 形式に加えて、`char_offset` の shorthand として `number|string` も許可する。
- `start_line`（または `cursor.start_line`）は整数かつ対象ファイルの総行数範囲内のみ許可。
- `max_chars` は固定値 `12000`（`SCAN_MAX_CHARS`）で、入力から変更不可。

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
    "char_offset": "number | null"
  },
  "eof": "boolean",
  "truncated": "boolean",
  "truncated_reason": "none|max_chars",
  "applied": {
    "max_chars": "number"
  }
}
```
