# 統合MCPサーバ v2 Bridge仕様（ドラフト）

## 1. スコープ

本ファイルは `bridge_*` ツールの仕様を定義する。共通事項は `spec_v2.md` を参照。

## 2. Tool Catalog

- `bridge_copy_section({ from_ref, to_path, mode, limits? })`
- `bridge_copy_file({ from_path, manual_id, to_path, mode, limits? })`

MVP方針:

- 本文をLLMへ返さず、サーバ内でコピーを完結させる
- 返却はメタ情報のみ

## 3. I/O Schemas

### `bridge_copy_section` Input

```json
{
  "from_ref": "object (required)",
  "to_path": "string (required)",
  "mode": "overwrite|append (required)",
  "limits": {
    "max_sections": "number | null",
    "max_chars": "number | null",
    "allow_file": "boolean | null"
  }
}
```

固定ルール:

- コピー元には `manual_read` と同等のガードを適用
- `limits` 未指定時は安全デフォルト + ハードリミット適用
- 返却本文は含めない

### `bridge_copy_section` Output

```json
{
  "written_path": "string",
  "written_bytes": "number",
  "written_sections": "number",
  "truncated": "boolean"
}
```

### `bridge_copy_file` Input

```json
{
  "from_path": "string (required)",
  "manual_id": "string (required)",
  "to_path": "string (required)",
  "mode": "overwrite|append (required)",
  "limits": {
    "max_sections": "number | null",
    "max_chars": "number | null",
    "allow_file": "boolean | null"
  }
}
```

固定ルール:

- `from_path` は manual root 配下の相対パスのみ
- `manual_id` は必須（指定manual配下に限定）
- `.md` は `ALLOW_FILE_SCOPE=true` かつ `limits.allow_file=true` が必須
- `.json` は `limits.allow_file` なしで許可
- 返却本文は含めない

### `bridge_copy_file` Output

```json
{
  "written_path": "string",
  "written_bytes": "number",
  "truncated": "boolean"
}
```

## 4. bridgeログ拡張（info）

- `bridge_copy_section`: `written_path`, `mode`, `written_bytes`, `written_sections`, `truncated`
- `bridge_copy_file`: `written_path`, `mode`, `written_bytes`, `truncated`
