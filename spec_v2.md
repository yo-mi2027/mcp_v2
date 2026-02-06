# 統合MCPサーバ v2 共通仕様（ドラフト）

## 1. 目的と分割方針

本ファイルは v2 の共通基盤のみを定義する。領域別の詳細I/Oは以下へ分離する。

- `spec_manuals.md`: `manual_*` ツール仕様
- `spec_vault.md`: `vault_*` / `artifact_audit` ツール仕様
- `spec_bridge.md`: `bridge_*` ツール仕様
- `spec_tooling.md`: `get_tooling_guide` 仕様

## 2. 実装ターゲット（MVP）

- 実装基盤: FastMCP（Python）
- トランスポート: stdio
- HTTPサーバはMVP対象外

## 3. 共通Config

### 3.1 ルート

- `WORKSPACE_ROOT`（既定: `.`）
- `MANUALS_ROOT`（既定: `${WORKSPACE_ROOT}/manuals`）
- `VAULT_ROOT`（既定: `${WORKSPACE_ROOT}/vault`）
- `DEFAULT_MANUAL_ID`（任意）

標準構造:

- `manuals/<manual_id>/`
- `vault/`

### 3.2 共通運用

- `LOG_LEVEL`（既定: `info`）
- `ADAPTIVE_TUNING`（既定: `true`）
- `ADAPTIVE_STATS_PATH`（既定: `${VAULT_ROOT}/.system/adaptive_stats.jsonl`）
- `ADAPTIVE_MIN_RECALL`（既定: `0.90`）
- `ADAPTIVE_CANDIDATE_LOW_BASE`（既定: `3`）
- `ADAPTIVE_FILE_BIAS_BASE`（既定: `0.80`）
- `VAULT_SCAN_DEFAULT_CHUNK_LINES`（既定: `80`）
- `VAULT_SCAN_MAX_CHUNK_LINES`（既定: `200`）
- `COVERAGE_MIN_RATIO`（既定: `0.90`）
- `MARGINAL_GAIN_MIN`（既定: `0.02`）
- `TRACE_MAX_KEEP`（既定: `100`）
- `TRACE_TTL_SEC`（既定: `1800`）
- `ALLOW_FILE_SCOPE`（既定: `false`）
  - `.md` の `scope=file` を許可するか
  - `limits.allow_file=true` と組み合わせて有効化
  - `.json` はMVP方針で `scope=file` を基本許可

### 3.3 ハードリミット

- `HARD_MAX_SECTIONS`（既定: `20`）
- `HARD_MAX_CHARS`（既定: `20000`）
- `DEFAULT_MAX_STAGE`（既定: `4`）
- `HARD_MAX_STAGE`（既定: `4`）
- `ARTIFACTS_DIR`（固定: `artifacts`）

## 4. 共通安全要件

- 相対パスのみ許可（絶対パス禁止）
- `..` を含むパスは禁止
- `MANUALS_ROOT` / `VAULT_ROOT` 外へのアクセス禁止
- アクセス時に symlink は辿らない

`artifacts/daily/` 判定ルール（固定）:

- `path` を正規化（区切り統一、`.` 除去、`..` 禁止、絶対パス拒否）
- `VAULT_ROOT` と結合した実体パスで境界判定（判定用途のみ）
- `daily_root = realpath(VAULT_ROOT/artifacts/daily)` 配下判定
- 比較は casefold 後に実施

## 5. 共通出力方針

- Explore返却は最小化（`trace_id` + 指標サマリ + `next_actions`）
- Produceは `VAULT_ROOT/artifacts/` へ保存
- 本文は必要時のみ取得（段階的取得）

## 6. 共通オーケストレーション

- 1プロンプト内で Explore -> Produce を連続実行してよい
- 初手で `get_tooling_guide` を呼び、`first_tool` を取得してから実処理に入ってよい
- manual探索では `manual_find` 内で Stage 0〜3 実行後に Stage 3.5（統合判断）を行い、`next_actions` を確定する
- vault探索では `vault_find` 内で Stage 0〜1 実行後に Stage 1.5（統合判断）を行い、`next_actions` を確定する
- vaultの抜け漏れ疑いでは `vault_find -> vault_scan -> vault_coverage -> artifact_audit` を段階実行する
  - 初回 `vault_scan` は先頭行から開始
  - 2周目以降は `vault_coverage.uncovered_ranges` 優先

## 7. 共通 `next_actions` 契約

`next_actions` は次の全ツールで必須（提案なしは `[]`）:

- `manual_find`
- `vault_find`
- `vault_scan`
- `vault_coverage`
- `artifact_audit`

共通Actionオブジェクト:

```json
{
  "type": "string",
  "confidence": "number (0.0..1.0) | null",
  "params": "object | null"
}
```

共通ルール:

- `params` は次呼び出しに必要な最小値のみ含める
- 停止判定時は `type="stop"`
- `stop` に専用の理由語彙は設けない
- `manual_find` / `vault` 系の十分性・不足・偏りの判断は `type` と `params` に反映する（`reason` フィールドは使わない）

### 7.1 共通エラー規約（MVP）

共通エラーコード:

- `invalid_parameter`: 入力値が許容語彙/範囲外
- `not_found`: 対象リソースや trace が存在しない
- `invalid_path`: パス形式不正（絶対パス、`..` 含有など）
- `out_of_scope`: `MANUALS_ROOT` / `VAULT_ROOT` 外を指している
- `forbidden`: ポリシー上禁止された操作
- `invalid_scope`: 対象形式に対して不正な scope
- `conflict`: 前提不一致や同時更新競合

固定ルール:

- エラー時は `code`（上記語彙）と簡潔な `message` を必須で返す
- 必要に応じて `details`（不足パラメータ、許容値、対象パス等）を返してよい

## 8. 共通ログ方針（MVP）

- 出力先: stderr（JSON Lines）
- 本文・候補本文はログに保存しない
- 候補一覧の大量列挙はログに保存しない

### 8.1 最小共通フィールド

- `ts`
- `level`（`error|warn|info`）
- `tool`
- `ok`
- `elapsed_ms`

### 8.2 適応統計（軽量・永続）

保存先: `ADAPTIVE_STATS_PATH`

最小フィールド:

- `ts`, `query_hash`, `scanned_files`, `candidates`, `warnings`
- `max_stage_applied`, `scope_expanded`, `cutoff_reason`, `unscanned_sections_count`
- `est_tokens`, `est_tokens_in`, `est_tokens_out`
- `added_evidence_count`, `added_est_tokens`, `marginal_gain`

固定ルール:

- `candidate_low_threshold`: 初期 `3`, 変化幅 `±1/24h`, 範囲 `2..6`
- `file_bias_threshold`: 初期 `0.80`, 変化幅 `±0.03/24h`, 範囲 `0.70..0.90`
- ロールバック: 直近100件で再現率proxyが `-3%` 超低下、または `cutoff_reason` 発生率が `+5%` 超悪化
- `est_tokens = ceil((chars_in + chars_out)/4)`
- `marginal_gain = added_evidence_count / added_est_tokens`（`added_est_tokens=0` は `null`）

## 9. 共通データモデル

### 9.1 `ref`

```json
{
  "target": "manual|vault",
  "manual_id": "string | null",
  "path": "string",
  "start_line": "number | null",
  "json_path": "string | null"
}
```

一意性:

- `.md`: `manual_id + path + start_line`
- `.json`: `manual_id + path`
- 見出しなし `.md`: `start_line=1`

固定ルール:

- `target=manual` の場合は `manual_id` 必須
- `target=vault` の場合は `manual_id=null`

任意フィールド:

- `title`
- `signals`

## 10. 領域別仕様への導線

- manual詳細: `spec_manuals.md`
- vault詳細: `spec_vault.md`
- bridge詳細: `spec_bridge.md`
- tooling詳細: `spec_tooling.md`
