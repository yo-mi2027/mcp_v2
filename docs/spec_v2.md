# 統合MCPサーバ v2 共通仕様（現行）

最終更新: 2026-02-11

## 1. スコープ

本ファイルは現行の公開ツールと共通契約を定義する。  
詳細I/Oは `spec_manuals.md` と `spec_vault.md` を参照。

## 2. 現行公開ツール

- `manual_ls`
- `manual_toc`
- `manual_find`
- `manual_hits`
- `manual_read`
- `manual_scan`
- `vault_create`
- `vault_read`
- `vault_scan`
- `vault_replace`

## 3. 共通Config（主要）

- `WORKSPACE_ROOT`（既定: `.`）
- `MANUALS_ROOT`（既定: `${WORKSPACE_ROOT}/manuals`）
- `VAULT_ROOT`（既定: `${WORKSPACE_ROOT}/vault`）
- `LOG_LEVEL`（既定: `info`）
- `ADAPTIVE_TUNING`（既定: `true`）
- `ADAPTIVE_STATS_PATH`（既定: `${VAULT_ROOT}/.system/adaptive_stats.jsonl`）
- `ADAPTIVE_MIN_RECALL`（既定: `0.90`）
- `ADAPTIVE_CANDIDATE_LOW_BASE`（既定: `3`）
- `ADAPTIVE_FILE_BIAS_BASE`（既定: `0.80`）
- `COVERAGE_MIN_RATIO`（既定: `0.90`）
- `MARGINAL_GAIN_MIN`（既定: `0.02`）
- `TRACE_MAX_KEEP`（既定: `100`）
- `TRACE_TTL_SEC`（既定: `1800`）
- `ALLOW_FILE_SCOPE`（既定: `false`）
- `HARD_MAX_SECTIONS`（既定: `20`）
- `HARD_MAX_CHARS`（既定: `20000`）
- `DEFAULT_MAX_STAGE`（既定: `4`、許容値: `3|4`）
- `VAULT_SCAN_DEFAULT_CHUNK_LINES`（既定: `80`）
- `VAULT_SCAN_MAX_CHUNK_LINES`（既定: `200`）

## 4. 共通安全要件

- 相対パスのみ許可（絶対パス禁止）
- `..` を含むパスは禁止
- `MANUALS_ROOT` / `VAULT_ROOT` 外アクセス禁止
- symlink 経由アクセスは禁止
- `vault/.system/` は予約領域（create/replace禁止）
- `vault/daily/` は作成時に `daily/YYYY-MM-DD.md` の命名制約を適用

## 5. 共通エラーコード

- `invalid_parameter`
- `invalid_path`
- `out_of_scope`
- `not_found`
- `forbidden`
- `invalid_scope`
- `conflict`

数値系パラメータの共通契約:

- 整数が必要な項目に非整数を渡した場合は `invalid_parameter`。
- 下限/上限違反も `invalid_parameter`。
- 実装内部の型変換失敗を `conflict` にマップしない。

## 6. `next_actions` 契約（現行）

`next_actions` を返す現行ツール:

- `manual_find`

Actionオブジェクト:

```json
{
  "type": "string",
  "confidence": "number | null",
  "params": "object | null"
}
```

固定ルール:

- `type` は次に呼ぶ現行ツール名か `stop`
- `params` は最小パラメータのみ返す

## 7. 非公開/廃止

以下は現行公開ツールではない:

- `manual_list`
- `manual_excepts`
- `vault_ls`
- `vault_find`
- `vault_search`
- `vault_write`
- `vault_coverage`
- `vault_audit`
- `bridge_copy_section`
- `bridge_copy_file`
- `get_tooling_guide`
