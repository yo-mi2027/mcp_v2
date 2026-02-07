# 統合MCPサーバ v2 Vault仕様（現行）

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
- `limits.max_chars` は `HARD_MAX_CHARS` で上限制限
- 常に `next_actions` で `vault_replace` を提案

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
  "truncated_reason": "none|range_end|max_chars|hard_limit",
  "next_actions": [
    {
      "type": "vault_replace",
      "confidence": "number",
      "params": {
        "path": "string"
      }
    }
  ]
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
- `chunk_lines` 既定: `VAULT_SCAN_DEFAULT_CHUNK_LINES`
- `chunk_lines` 許容: `1..VAULT_SCAN_MAX_CHUNK_LINES`
- `limits.max_chars` は `HARD_MAX_CHARS` で上限制限

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
  "truncated_reason": "none|chunk_end|max_chars|hard_limit",
  "next_actions": [
    {
      "type": "vault_scan|stop",
      "confidence": "number",
      "params": "object | null"
    }
  ]
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

Output:

```json
{
  "written_path": "string",
  "replacements": "number"
}
```
