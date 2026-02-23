# 統合MCPサーバ v2 Vault仕様（現行）

最終更新: 2026-02-23

更新注記（2026-02-23）:

- 今回の改訂は主に manual 系（`manual_find` / `manual_read` / `manual_scan`）の設計整理であり、Vault I/O 契約の変更はない。
- 採用/棄却した案の理由は `requirements.md` の設計判断節を参照。

## 1. Tool Catalog

- `vault_create({ path, content })`
- `vault_read({ path, full?, range? })`
- `vault_scan({ path, start_line?, cursor? })`
- `vault_replace({ path, find, replace, max_replacements? })`
- `vault_ls({ path? })`

固定ルール:

- `vault_ls` は探索用途の任意ツールであり、`vault_read` / `vault_scan` / `vault_create` / `vault_replace` の前提ではない。

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
  }
}
```

固定ルール:

- 既定 `full=false`
- `full` は boolean のみ許可（非booleanは `invalid_parameter`）
- `full=false` のとき `range` 必須
- `range.start_line` / `range.end_line` は整数かつ `>= 1`
- `range.start_line` / `range.end_line` に `true/false` は許可しない（`invalid_parameter`）
- `range.start_line <= range.end_line`
- `range.start_line` は対象ファイルの総行数範囲内
- `max_chars` は固定値 `12000` で、入力から変更不可

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
  "next_cursor": {
    "char_offset": "number | null"
  },
  "truncated_reason": "none|range_end|max_chars",
  "applied": {
    "full": "boolean",
    "max_chars": "number"
  }
}
```

## 4. `vault_scan`

Input:

```json
{
  "path": "string",
  "start_line": "number | null",
  "cursor": {
    "start_line": "number | null",
    "char_offset": "number | null"
  }
}
```

固定ルール:

- `start_line` 指定時はそれを優先し、未指定時は `cursor.start_line`（未指定なら1）を使う
- `start_line`（または `cursor.start_line`）は整数かつ対象ファイルの総行数範囲内
- `start_line` / `cursor.start_line` / `cursor.char_offset` に `true/false` は許可しない（`invalid_parameter`）
- `max_chars` は固定値 `12000`（`SCAN_MAX_CHARS`）で、入力から変更不可

Output:

```json
{
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
- `max_replacements` に `true/false` は許可しない（`invalid_parameter`）

Output:

```json
{
  "written_path": "string",
  "replacements": "number"
}
```

## 6. `vault_ls`

Input:

```json
{
  "path": "string | null"
}
```

固定ルール:

- `path` 省略時は `VAULT_ROOT` 直下を列挙
- 非再帰で1階層のみ返却
- 返却順は `dir -> file`、同種内は名前の安定ソート
- symlink は返却しない

Output:

```json
{
  "base_path": "string | null",
  "items": [
    {
      "name": "string",
      "path": "string",
      "kind": "dir|file"
    }
  ]
}
```
