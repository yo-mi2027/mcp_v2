# 統合MCPサーバ v2 Vault仕様（ドラフト）

## 1. スコープ

本ファイルは `vault_*` および `artifact_audit` の仕様を定義する。共通事項は `spec_v2.md` を参照。

## 2. Tool Catalog

- `vault_ls({ relative_dir? })`
- `vault_read({ path, full?, range?, limits? })`
- `vault_find({ query, scope?, budget? })`
- `vault_scan({ path, cursor?, chunk_lines?, limits? })`
- `vault_coverage({ path, cited_ranges[] })`
- `artifact_audit({ artifact_path, source_path, cited_ranges[]? })`
- `vault_create({ path, content })`
- `vault_write({ path, content, mode })`
- `vault_replace({ path, find, replace, max_replacements? })`
- `vault_search({ query, mode?, glob?, relative_dir?, limit? })`

注記:

- `artifact_audit` は `manual_` / `vault_` / `bridge_` の命名規約に対する例外として、cross-domain 監査ツール名を採用する。

## 3. Vault探索と監査フロー

段階実行:

1. `vault_find`
2. `vault_scan`
3. `vault_coverage`
4. `artifact_audit`

ルール:

- `vault_find` 内で Stage 0〜1 の候補を統合判断（Stage 1.5）して `next_actions` を確定する
- 初回 `vault_scan` は先頭行から開始
- 2周目以降は `uncovered_ranges` 優先
- 停止判定は `coverage_ratio` と `marginal_gain`
- 停止時は `next_actions` に `type="stop"`

## 4. I/O Schemas

### `vault_ls` Input

```json
{
  "relative_dir": "string | null"
}
```

### `vault_ls` Output

```json
{
  "entries": [
    {
      "path": "string",
      "type": "file|dir",
      "size_bytes": "number | null"
    }
  ]
}
```

### `vault_read` Input

```json
{
  "path": "string (required)",
  "full": "boolean | null",
  "range": {
    "start_line": "number | null",
    "end_line": "number | null"
  },
  "limits": {
    "max_chars": "number | null"
  }
}
```

固定ルール:

- 既定 `full=false`
- `full=false` では `range` 必須
- `full=true` でも `limits.max_chars` とハードリミットを適用

### `vault_read` Output

```json
{
  "text": "string",
  "truncated": "boolean",
  "returned_chars": "number",
  "applied_range": {
    "start_line": "number",
    "end_line": "number"
  },
  "next_offset": {
    "start_line": "number | null"
  },
  "truncated_reason": "max_chars|range_end|hard_limit|none"
}
```

### `vault_find` Input

```json
{
  "query": "string (required, non-empty)",
  "scope": {
    "relative_dir": "string | null",
    "glob": "string | null"
  },
  "budget": {
    "max_candidates": "number | null",
    "time_ms": "number | null"
  }
}
```

固定ルール:

- 既定 `budget.time_ms=60000`
- 既定 `budget.max_candidates=200`
- Stage 0: 正規化一致
- Stage 1: loose一致
- Stage 1.5: 統合判断（候補統合、偏り/不足判定、次アクション提案）

### `vault_find` Output

```json
{
  "trace_id": "string",
  "summary": {
    "scanned_files": "number",
    "scanned_nodes": "number",
    "candidates": "number",
    "warnings": "number",
    "max_stage_applied": "number",
    "scope_expanded": "boolean",
    "integrated_candidates": "number",
    "signal_coverage": {
      "normalized": "number",
      "loose": "number"
    },
    "file_bias_ratio": "number (0.0..1.0)",
    "gap_ranges_count": "number",
    "sufficiency_score": "number (0.0..1.0)",
    "integration_status": "ready | needs_followup | blocked",
    "cutoff_reason?": "time_budget | candidate_cap | stage_cap | hard_limit"
  },
  "next_actions": [
    {
      "type": "vault_read|vault_search|vault_scan|vault_coverage|artifact_audit|vault_find|stop",
      "confidence": "number (0.0..1.0) | null",
      "params": "object | null"
    }
  ]
}
```

固定ルール:

- `next_actions` は必須（提案なしは `[]`）
- `next_actions.params` は最小パラメータのみ
- `next_actions.type` は次に呼ぶツール名を返す
- `cutoff_reason` は打ち切り時のみ返し、打ち切りがない場合はキー自体を省略する
- 統合判断の意図は `next_actions.type` と `next_actions.params` で表現する（`reason` は使用しない）
- 十分性条件を満たした場合は `type="stop"` を返す

### `vault_scan` Input

```json
{
  "path": "string (required)",
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

- `chunk_lines` 既定: `VAULT_SCAN_DEFAULT_CHUNK_LINES`
- `chunk_lines` 範囲: `1..VAULT_SCAN_MAX_CHUNK_LINES`
- `cursor.start_line` 既定: `1`

### `vault_scan` Output

```json
{
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
  "truncated_reason": "max_chars|chunk_end|hard_limit|none",
  "next_actions": [
    {
      "type": "vault_scan|vault_coverage|artifact_audit|stop",
      "confidence": "number (0.0..1.0) | null",
      "params": "object | null"
    }
  ]
}
```

### `vault_coverage` Input

```json
{
  "path": "string (required)",
  "cited_ranges": [
    {
      "start_line": "number (required)",
      "end_line": "number (required)"
    }
  ]
}
```

固定ルール:

- `cited_ranges` は重複/隣接を正規化
- `coverage_ratio = covered_lines / total_lines`（`total_lines=0` は `1.0`）

### `vault_coverage` Output

```json
{
  "path": "string",
  "total_lines": "number",
  "covered_lines": "number",
  "coverage_ratio": "number (0.0..1.0)",
  "covered_ranges": [
    {
      "start_line": "number",
      "end_line": "number"
    }
  ],
  "uncovered_ranges": [
    {
      "start_line": "number",
      "end_line": "number"
    }
  ],
  "meets_min_coverage": "boolean",
  "next_actions": [
    {
      "type": "vault_scan|artifact_audit|stop",
      "confidence": "number (0.0..1.0) | null",
      "params": "object | null"
    }
  ]
}
```

固定ルール:

- `meets_min_coverage = coverage_ratio >= COVERAGE_MIN_RATIO`

### `artifact_audit` Input

```json
{
  "artifact_path": "string (required)",
  "source_path": "string (required)",
  "cited_ranges": "array<{start_line:number,end_line:number}> | null"
}
```

固定ルール:

- `cited_ranges` 未指定時は `source_lines` から抽出を試行（不可なら空配列）
- 検出: `rootless_nodes`, `orphan_branches`, `one_way_refs`
- 用語マッピング: `rootless_node=根拠なし要素`, `orphan_branch=孤立分岐`, `one_way_ref=片方向参照`

### `artifact_audit` Output

```json
{
  "artifact_path": "string",
  "source_path": "string",
  "rootless_nodes": "number",
  "orphan_branches": "number",
  "one_way_refs": "number",
  "coverage_ratio": "number (0.0..1.0) | null",
  "uncovered_ranges_count": "number",
  "marginal_gain": "number | null",
  "needs_forced_full_scan": "boolean",
  "next_actions": [
    {
      "type": "vault_scan|vault_coverage|artifact_audit|stop",
      "confidence": "number (0.0..1.0) | null",
      "params": "object | null"
    }
  ],
  "findings": [
    {
      "kind": "rootless_node|orphan_branch|one_way_ref",
      "message": "string",
      "node_id": "string | null"
    }
  ]
}
```

固定ルール:

- `needs_forced_full_scan` は以下で `true`:
  - `coverage_ratio < COVERAGE_MIN_RATIO`
  - `rootless_nodes|orphan_branches|one_way_refs` のいずれかが1以上
  - `marginal_gain >= MARGINAL_GAIN_MIN` かつ `uncovered_ranges_count > 0`

### `vault_create` Input

```json
{
  "path": "string (required)",
  "content": "string (required)"
}
```

### `vault_create` Output

```json
{
  "written_path": "string",
  "written_bytes": "number"
}
```

固定ルール:

- `.system/` 配下は予約領域のため作成禁止
- `path` が `daily/` 配下の場合、新規作成は `daily/YYYY-MM-DD.md` 形式のみ許可

### `vault_write` Input

```json
{
  "path": "string (required)",
  "content": "string (required)",
  "mode": "overwrite|append (required)"
}
```

固定ルール:

- 原則 `mode=overwrite|append` は既存ファイルのみ
- `.system/` 配下は予約領域のため更新禁止
- `daily/` 配下は `append` のみ許可
- `daily/` の初回作成は `vault_create` を使用する
- `daily/YYYY-MM-DD.md` 形式のみ許可

### `vault_write` Output

```json
{
  "written_path": "string",
  "written_bytes": "number"
}
```

### `vault_replace` Input

```json
{
  "path": "string (required)",
  "find": "string (required)",
  "replace": "string (required)",
  "max_replacements": "number | null"
}
```

固定ルール:

- `max_replacements` 既定: `1`
- `.system/` / `daily/` 配下は置換禁止

### `vault_replace` Output

```json
{
  "written_path": "string",
  "replacements": "number"
}
```

### `vault_search` Input

```json
{
  "query": "string (required, non-empty)",
  "mode": "plain | regex | loose | null",
  "glob": "string | null",
  "relative_dir": "string | null",
  "limit": "number | null"
}
```

固定ルール:

- `mode` 既定: `regex`（不正時は `plain` 相当へフォールバック可）
- `limit` 既定: `50`

### `vault_search` Output

```json
{
  "results": [
    {
      "path": "string",
      "snippet": "string"
    }
  ]
}
```

## 5. エラー規約（MVP）

- 本ファイルのツールエラーは `spec_v2.md` の共通エラー規約に従う
- 代表例:
  - `invalid_parameter`: `chunk_lines` や `limit` が許容範囲外
  - `invalid_path` / `out_of_scope`: パス検証違反
  - `not_found`: 対象ファイル不存在
  - `forbidden`: `.system/` 予約領域への操作、`daily/` 更新ポリシー違反
  - `conflict`: `vault_write` が既存ファイル前提に反するなど前提不一致

## 6. vaultログ拡張（info）

- `vault_read`: `path`, `truncated`
- `vault_create`: `path`, `written_bytes`
- `vault_write`: `path`, `mode`, `written_bytes`
- `vault_replace`: `path`, `replacements`
- `vault_find`: `trace_id`, `candidates`, `integrated_candidates`, `file_bias_ratio`, `gap_ranges_count`, `sufficiency_score`, `integration_status`, `next_actions`
- `vault_scan`: `path`, `applied_range`, `eof`, `truncated_reason`
- `vault_coverage`: `path`, `coverage_ratio`, `covered_ranges`, `uncovered_ranges`
- `artifact_audit`: `artifact_path`, `source_path`, `rootless_nodes`, `orphan_branches`, `one_way_refs`
