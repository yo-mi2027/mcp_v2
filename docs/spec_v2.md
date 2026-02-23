# 統合MCPサーバ v2 共通仕様（現行）

最終更新: 2026-02-23

## 1. スコープ

本ファイルは現行の公開ツールと共通契約を定義する。  
詳細I/Oは `spec_manuals.md` と `spec_vault.md` を参照。

更新注記（2026-02-23）:

- `manual_find.summary` / `claim_graph` の設計整理は `spec_manuals.md` を正本とする。
- 採用/棄却した案の理由は `requirements.md` の設計判断節を参照。

## 2. 現行公開ツール

- `manual_ls`
- `manual_toc`
- `manual_find`
- `manual_hits`
- `manual_read`
- `manual_scan`
- `vault_ls`
- `vault_create`
- `vault_read`
- `vault_scan`
- `vault_replace`

## 3. 共通Config（主要）

- `WORKSPACE_ROOT`（既定: `.`）
- `MANUALS_ROOT`（既定: `${WORKSPACE_ROOT}/manuals`）
- `VAULT_ROOT`（既定: `${WORKSPACE_ROOT}/vault`）
- `ADAPTIVE_TUNING`（既定: `true`）
- `ADAPTIVE_STATS_PATH`（既定: `${VAULT_ROOT}/.system/adaptive_stats.jsonl`）
- `ADAPTIVE_MIN_RECALL`（既定: `0.90`）
- `ADAPTIVE_CANDIDATE_LOW_BASE`（既定: `3`）
- `ADAPTIVE_FILE_BIAS_BASE`（既定: `0.80`）
- `COVERAGE_MIN_RATIO`（既定: `0.90`）
- `MARGINAL_GAIN_MIN`（既定: `0.02`）
- `SPARSE_QUERY_COVERAGE_WEIGHT`（既定: `0.35`）
- `LEXICAL_COVERAGE_WEIGHT`（既定: `0.50`）
- `LEXICAL_PHRASE_WEIGHT`（既定: `0.50`）
- `LEXICAL_NUMBER_CONTEXT_BONUS`（既定: `0.80`）
- `LEXICAL_PROXIMITY_BONUS_NEAR`（既定: `1.00`）
- `LEXICAL_PROXIMITY_BONUS_FAR`（既定: `0.50`）
- `LEXICAL_LENGTH_PENALTY_WEIGHT`（既定: `0.20`）
- `MANUAL_FIND_EXPLORATION_ENABLED`（既定: `true`）
- `MANUAL_FIND_EXPLORATION_RATIO`（既定: `0.20`）
- `MANUAL_FIND_EXPLORATION_MIN_CANDIDATES`（既定: `2`）
- `MANUAL_FIND_EXPLORATION_SCORE_SCALE`（既定: `0.35`）
- `MANUAL_FIND_QUERY_DECOMP_ENABLED`（既定: `true`）
- `MANUAL_FIND_QUERY_DECOMP_MAX_SUB_QUERIES`（既定: `3`）
- `MANUAL_FIND_QUERY_DECOMP_RRF_K`（既定: `60`）
- `MANUAL_FIND_QUERY_DECOMP_BASE_WEIGHT`（既定: `0.30`）
- `MANUAL_FIND_SCAN_HARD_CAP`（既定: `5000`）
- `MANUAL_FIND_PER_FILE_CANDIDATE_CAP`（既定: `8`）
- `MANUAL_FIND_FILE_PRESCAN_ENABLED`（既定: `true`）
- `TRACE_MAX_KEEP`（既定: `100`）
- `TRACE_TTL_SEC`（既定: `1800`）
- `ALLOW_FILE_SCOPE`（既定: `false`）
- `SEM_CACHE_ENABLED`（既定: `true`）
- `SEM_CACHE_TTL_SEC`（既定: `1800`）
- `SEM_CACHE_MAX_KEEP`（既定: `500`）
- `SEM_CACHE_SIM_THRESHOLD`（既定: `0.92`）
- `SEM_CACHE_EMBEDDING_PROVIDER`（既定: `none`）
- `SEM_CACHE_MAX_SUMMARY_GAP`（既定: `-1`）
- `SEM_CACHE_MAX_SUMMARY_CONFLICT`（既定: `-1`）

注:

- 現行実装の `SEM_CACHE_EMBEDDING_PROVIDER` は `none` のみサポート。
- 現行運用プロファイル（現在使用しているPCを含む標準環境）では `SEM_CACHE_EMBEDDING_PROVIDER=none` を固定運用とし、embedding provider は要件外。
- そのため既定運用では semantic cache の類似検索は実質無効で、cache hit は正規化後 exact match 中心となる。
- `SEM_CACHE_SIM_THRESHOLD` は互換性維持・将来拡張余地のために残している設定値であり、現行運用での導入予定を意味しない。

## 4. 共通安全要件

- 相対パスのみ許可（絶対パス禁止）
- `..` を含むパスは禁止
- `MANUALS_ROOT` / `VAULT_ROOT` 外アクセス禁止
- symlink 経由アクセスは禁止
- `vault/.system/` は予約領域（create/replace禁止）
- `vault/daily/` は作成時に `daily/YYYY-MM-DD.md` の命名制約を適用

## 5. 共通導線要件（Discovery First）

- manuals 側探索では、`manual_toc` / `manual_find` / `manual_read` / `manual_scan` の前に `manual_ls` を成功させること。
- vault 側探索で `vault_ls` は任意（`vault_read` / `vault_scan` / `vault_create` / `vault_replace` は単独で実行可能）。
- 前提未達で `invalid_parameter` を返すのは manuals 側のみ。

## 6. 共通エラーコード

- `invalid_parameter`
- `invalid_path`
- `out_of_scope`
- `needs_narrow_scope`
- `not_found`
- `forbidden`
- `invalid_scope`
- `conflict`

数値系パラメータの共通契約:

- 整数が必要な項目に非整数を渡した場合は `invalid_parameter`。
- `true/false` は整数として扱わず `invalid_parameter` とする。
- 下限/上限違反も `invalid_parameter`。
- 実装内部の型変換失敗を `conflict` にマップしない。

## 7. `next_actions` 契約（現行）

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

- `type` は次に呼ぶ現行ツール名
- `params` は最小パラメータのみ返す
- manuals系では `manual_hits|manual_read|manual_find|manual_scan` を取りうる
- 公開MCPツール（`app.py`）では `manual_find` / `manual_hits` は常時compact返却（選択不可）
- 公開MCPツール（`app.py`）の compact `manual_find` は `next_actions` を返却契約上は保持するが、常に空配列（`[]`）を返す
- 公開MCPツール（`app.py`）の compact `manual_find` は既定で `inline_hits`（`manual_hits(kind="integrated_top", offset=0, compact=true)` と同形、既定 `limit=5`、`limit<=5`）を同梱する

## 8. 非公開/廃止

以下は現行公開ツールではない:

- `manual_list`
- `manual_excepts`
- `vault_find`
- `vault_search`
- `vault_write`
- `vault_coverage`
- `vault_audit`
- `bridge_copy_section`
- `bridge_copy_file`
- `get_tooling_guide`
