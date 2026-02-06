# 統合MCPサーバ v2 Tooling Guide仕様（ドラフト）

## 1. スコープ

本ファイルは `get_tooling_guide`（固定ツールカタログ + 初手ツール提案）の仕様を定義する。共通事項は `spec_v2.md` を参照。

## 2. Tool Catalog

- `get_tooling_guide({ intent?, target? })`

MVP方針:

- 返却は最小限に固定する（毎回同じカタログ + `first_tool`）
- ツール選択そのものは LLM が行う
- 本ツールは副作用を持たない

## 3. I/O Schemas

### `get_tooling_guide` Input

```json
{
  "intent": "explore | produce | revise | audit | unknown | null",
  "target": "manual | vault | unknown | null"
}
```

固定ルール:

- 入力はすべて任意（未指定可）
- `intent`/`target` は `first_tool` を決めるためだけに使う

### `get_tooling_guide` Output

```json
{
  "first_tool": "string",
  "tools": [
    {
      "tool_name": "string",
      "when_to_use": "string",
      "required_inputs": ["string"],
      "safe_defaults": "object",
      "common_errors": [
        {
          "code": "string",
          "fix": "string"
        }
      ]
    }
  ]
}
```

固定ルール:

- `tools` は固定カタログ（MVP対象ツールの要約）を返す
- `required_inputs` は最小キーのみ
- `safe_defaults` は既定値のみ（詳細説明は含めない）
- `common_errors` は各ツール最大2件
- `first_tool` は必ず1つ返す

## 4. `first_tool` 決定ルール（MVP固定）

- `intent=explore`:
  - `target=vault` なら `vault_find`
  - それ以外は `manual_find`
- `intent=produce`: `vault_create`
- `intent=revise`: `vault_search`
- `intent=audit`: `vault_coverage`
- `intent=unknown|null`: `manual_find`

## 5. エラー規約（MVP）

- `invalid_parameter`: 許容語彙外の `intent`/`target`

## 6. ログ拡張（info）

- `first_tool`
- `intent`
- `target`
