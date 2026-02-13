# RAG設計書 v2（現行実装ベース）

最終更新: 2026-02-13

本書は `manual_find` を中心にした探索設計の説明資料である。  
入出力の正本契約は `spec_manuals.md` を参照。

## 1. 目的

- 抜け漏れを減らした manual 探索を提供する。
- LLMへの転送量を抑えるため、探索結果は `trace_id` 中心で返す。
- 詳細データは `manual_hits` / `manual_read` / `manual_scan` で段階的に回収する。

## 2. 探索フロー（`manual_find`）

1. 入力検証
- `query` 必須
- `expand_scope` は boolean（未指定時 `true`）
- `budget.time_ms` / `budget.max_candidates` は整数かつ `>= 1`

2. Semantic Cache 照合（有効時）
- `only_unscanned_from_trace_id` 未指定時のみ lookup
- `exact` -> `semantic` の順で照合
- hit 時は保存済み `trace_payload` から新規 `trace_id` を発行して返却
- `manual_id` / `expand_scope` / `budget` / `manuals_fingerprint` でスコープ分離

3. 候補抽出（cache miss 時）
- 対象 manual 群を決定
- `.md` は見出しノード単位、`.json` はファイル単位で走査
- シグナル（`heading`, `normalized`, `loose`, `exceptions`）を評価
- 厳密一致シグナル（`heading|normalized|loose`）がある候補のみ採用（`exceptions` 単独では採用しない）

4. 必要時の拡張
- `expand_scope=true` の場合、条件に応じて探索スコープを拡張
- クエリ語彙と候補分布をもとに例外語彙中心の補助パスを段階実行
- `only_unscanned_from_trace_id` 指定時は未探索セクションを優先

5. 統合判断
- 候補から `claim_graph`（claims/evidences/edges/facets）を構築
- `summary`（candidates, gap_count, conflict_count 等）を算出
- `next_actions` を生成

6. 保存
- trace payload をメモリストアへ保存して `trace_id` を返す
- 軽量統計を JSONL へ追記
- cache miss 時は `trace_payload` と `source_latency_ms` を Semantic Cache へ保存

## 3. 返却設計

- `manual_find` の標準返却:
  - `trace_id`
  - `summary`
  - `next_actions`
- `include_claim_graph=true` の場合のみ `claim_graph` を返す
- 候補詳細は `manual_hits` でページング取得

## 4. 代表的な追跡パターン

1. 検索 -> 候補確認
- `manual_find`
- `manual_hits(kind="candidates")`

2. 候補本文の最小取得
- `manual_read(scope="section")`
- 必要なら `manual_scan` で続き取得

3. 不足時の再探索
- `manual_find(only_unscanned_from_trace_id=...)`

## 5. 境界条件

- `manual_hits` は `offset >= 0`, `limit >= 1`
- `manual_scan` は `max_chars=12000` 固定で `next_cursor.char_offset` による継続取得を行う
- `start_line` は対象行数の範囲内
- 不正な数値は `invalid_parameter`

## 6. 観測項目（`ADAPTIVE_STATS_PATH`）

- `sem_cache_hit`: cache hit の有無
- `sem_cache_mode`: `bypass|miss|exact|semantic`
- `sem_cache_score`: semantic 類似度スコア（exact/miss時は `null` 可）
- `latency_saved_ms`: cache hit 時の推定短縮時間（ms）

## 7. 非対象（本書）

- `vault_find`, `vault_coverage`, `vault_audit` などの未公開機能
- `bridge_*` や `get_tooling_guide` の導線

## 8. 補足

- 詳細なI/Oは `spec_manuals.md` を正本として扱う。
- 共通契約・エラーコードは `spec_v2.md` を参照。
