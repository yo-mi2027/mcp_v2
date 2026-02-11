# 統合MCPサーバ v2 要件定義（現行運用版）

最終更新: 2026-02-11

本書は現行実装の要件を運用向けに整理した文書である。  
入出力の正本契約は `spec_v2.md` / `spec_manuals.md` / `spec_vault.md` を優先する。

## 1. 背景

- manual群（`manuals/<manual_id>/`）を横断して、抜け漏れを抑えた探索を行いたい。
- LLMへの本文転送量を抑え、必要な箇所だけを段階取得したい。
- 成果物やメモは `vault/` 配下に安全に保存・更新したい。

## 2. 現行公開スコープ

公開ツールは次の10個のみ:

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

非公開/廃止（本リリースでは提供しない）:

- `manual_list`
- `manual_excepts`
- `vault_ls`
- `vault_find`
- `vault_write`
- `vault_search`
- `vault_coverage`
- `vault_audit`
- `bridge_*`
- `get_tooling_guide`

## 3. 機能要件

### 3.1 Manual探索

- `manual_find` は複数シグナル（見出し一致、正規化一致、loose一致、例外語彙）を統合して候補化する。
- 結果は `trace_id` を中心に返し、詳細は `manual_hits` で段階取得できる。
- 統合判断として `claim_graph` を内部生成し、要約として `summary` と `next_actions` を返す。
- `max_stage` は `3|4` のみ許可。`4` では必要時に探索拡張を行う。

### 3.2 Manual本文取得

- `manual_read` は `snippet|section|sections|file` の段階取得を提供する。
- `.md` の `file` は安全制約（`ALLOW_FILE_SCOPE=true` かつ `limits.allow_file=true`）を満たす場合のみ許可。
- `.json` は `file` 読みを基本とし、`section|sections` は不許可。

### 3.3 Vault操作

- `vault_create` は新規作成専用（既存ファイルは `conflict`）。
- `vault_read` は範囲読みを基本とし、必要時のみ `full=true`。
- `vault_scan` は行単位の逐次取得を提供する。
- `vault_replace` は文字列置換を提供し、`daily/` と `.system/` は禁止。

## 4. 安全要件

- パスは相対指定のみ。`..`、絶対パス、ルート外アクセスは禁止。
- symlink 経由アクセスは禁止。
- `.system/` は予約領域としてユーザー変更を禁止。
- `daily/` 作成時は `daily/YYYY-MM-DD.md` 形式のみ許可。

## 5. 入力バリデーション要件

- 整数型パラメータに非整数を渡した場合は `invalid_parameter` を返す。
- 下限/上限違反も `invalid_parameter` を返す。
- 実装内部の変換失敗を `conflict` にフォールバックしない。

主な境界条件:

- `budget.time_ms >= 1`
- `budget.max_candidates >= 1`
- `offset >= 0`
- `limit >= 1`
- `limits.max_sections >= 1`
- `limits.max_chars >= 1`
- `chunk_lines in [1, VAULT_SCAN_MAX_CHUNK_LINES]`
- `start_line >= 1`
- `max_replacements >= 0`

## 6. 観測性要件

- ツール呼び出しログは JSONL で stderr に出力する。
- `manual_find` の軽量統計は `ADAPTIVE_STATS_PATH` に永続化する。
- 統計には本文を保存しない（メタ情報のみ）。

## 7. 非機能要件

- Python `>=3.12,<3.13`
- stdio 実行を標準運用とする。
- 既定設定で開発環境起動できること。
- 単体テストとE2Eテストが通ること。

## 8. 本書の位置づけ

- 本書は運用要件をまとめたガイドであり、I/O契約の唯一の正本ではない。
- 仕様差分がある場合は `spec_v2.md` / `spec_manuals.md` / `spec_vault.md` を優先する。
