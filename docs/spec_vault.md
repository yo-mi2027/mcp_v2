# 統合MCPサーバ v2 Vault仕様（現行）

最終更新: 2026-02-11

## 1. Tool Catalog

- `vault_create({ path, content })`
- `vault_read({ path, full?, range?, limits? })`
- `vault_scan({ path, cursor?, chunk_lines?, limits? })`
- `vault_replace({ path, find, replace, max_replacements? })`

## 2. `vault_create`

Input:

```json
{
  "path": "string",
  "content": "string (required, non-empty)"
}
```

固定ルール:

- 既存ファイルは `conflict`
- `.system/` 配下は `forbidden`
- `daily/` 配下は `daily/YYYY-MM-DD.md` の命名制約を適用

Output:

```json
{
  "written_path": "string",
  "written_bytes": "number"
}
```

## 3. `vault_read`

Input:

```json
{
  "path": "string",
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
- `full=false` のとき `range` 必須
- `range.start_line` / `range.end_line` は整数かつ `>= 1`
- `range.start_line <= range.end_line`
- `range.start_line` は対象ファイルの総行数範囲内
- `limits.max_chars` は整数かつ `>= 1`、`HARD_MAX_CHARS` で上限制限

Output:

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
  "truncated_reason": "none|range_end|max_chars|hard_limit"
}
```

## 4. `vault_scan`

Input:

```json
{
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

- `cursor.start_line` 既定: `1`
- `cursor.start_line` は整数かつ対象ファイルの総行数範囲内
- `chunk_lines` 既定: `VAULT_SCAN_DEFAULT_CHUNK_LINES`
- `chunk_lines` は整数かつ `1..VAULT_SCAN_MAX_CHUNK_LINES`
- `limits.max_chars` は整数かつ `>= 1`、`HARD_MAX_CHARS` で上限制限

Output:

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
  "truncated_reason": "none|chunk_end|max_chars|hard_limit"
}
```

## 5. `vault_replace`

Input:

```json
{
  "path": "string",
  "find": "string (required, non-empty)",
  "replace": "string",
  "max_replacements": "number | null"
}
```

固定ルール:

- `.system/` 配下は `forbidden`
- `daily/` 配下は `forbidden`
- `max_replacements` 未指定は `1`
- `max_replacements` は整数かつ `>= 0`

Output:

```json
{
  "written_path": "string",
  "replacements": "number"
}
```
