# RAG設計書 v2（現行実装ベース）

最終更新: 2026-02-20

本書は `manual_find` を中心にした探索設計の説明資料である。  
入出力の正本契約は `spec_manuals.md` を参照。

## 1. 目的

- 抜け漏れを減らした manual 探索を提供する。
- LLMへの転送量を抑えるため、探索結果は `trace_id` 中心で返す。
- 詳細データは `manual_hits` / `manual_read` / `manual_scan` で段階的に回収する。

## 2. 探索フロー（`manual_find`）

1. 入力検証
- `query` 必須
- `manual_id` は必須。
- `expand_scope` は boolean 入力のみ許可（未指定は `null` 扱い）。
- `required_terms` は必須。文字列配列（`1..2` 語）を受理する。
- 進行方針は「`manual_find` 系（`g0 + g_req` 融合）」を基本とし、網羅要求時のみ `manual_scan` を優先する。
- `budget.time_ms` / `budget.max_candidates` は整数かつ `>= 1`

2. Semantic Cache 照合（有効時）
- `only_unscanned_from_trace_id` 未指定時のみ lookup
- `exact` -> `semantic` の順で照合
- hit 時は保存済み `trace_payload` から新規 `trace_id` を発行して返却
- `manual_id` / `budget` / `manuals_fingerprint`（+ `required_terms`）でスコープ分離
- `stage_cap` を含む結果も同一スコープで cache 再利用対象にする

3. 候補抽出（cache miss 時）
- 対象 manual 群を決定
- `.md` は見出しノード単位、`.json` はファイル単位で走査
- `required_terms` 指定時は候補採用条件に必須語一致を統合し、2語時は `A` / `B` / `A+B` の3pass結果をRRF統合
- `required_terms` は検索前にDFガードを適用し、`applied.required_terms_df_filtered` に診断情報を記録する（`too_common` は除外、`too_rare` は保持）
- `manual_find` は `g0`（requiredなし）と `g_req`（requiredあり）を実行し、RRF融合で候補を統合する
- `g_req` が0件のときのみ `g0` を採用し、`applied.required_terms_relaxed=true` を返す
- `applied.selected_gate` と `applied.gate_selection_reason` で最終採用ゲートを追跡できる
- `applied.required_effect_status` / `required_failure_reason` / `required_strict_candidates` / `required_filtered_candidates` で、required語が有効だったかを診断できる
- relax後候補にはノイズ抑制フィルタを適用し、弱一致のみの場合は0件を返す
- lexical シグナル（`exact/required_term/required_term_and/required_terms_rrf/phrase/anchor/number_context/proximity/code_exact/prf/exceptions/definition_title`）を評価
- BM25 を基礎に query coverage 補正（`SPARSE_QUERY_COVERAGE_WEIGHT` / `LEXICAL_COVERAGE_WEIGHT`）を加点
- Query Decomposition + RRF（`MANUAL_FIND_QUERY_DECOMP_ENABLED`）は既定ON。比較構文に一致した場合のみ sub-query 分解を実行し、部分失敗は許容して継続する。結合時は `base` と `rrf` を正規化混合（`MANUAL_FIND_QUERY_DECOMP_BASE_WEIGHT`）して再スコアする
- 最終ランキングで同一 `path` の過度な集中を抑える多様性リランキングを適用
- 探索中の `candidate_cap` は `min(MANUAL_FIND_SCAN_HARD_CAP, max(50, budget.max_candidates*20))` で制御
- 返却直前に動的カットオフを適用し、返却候補上限は `min(budget.max_candidates, 50)`（さらに score/coverage 条件で縮小）
- lexical 一致がある候補のみ採用（`exceptions` 単独では採用しない）

4. 不足時の補助
- `only_unscanned_from_trace_id` 指定時は未探索セクションを優先
- 候補不足（0件 / 低件数 / 高偏り）では `stage_cap` を記録し、未探索候補を `unscanned` に積む

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
- 不正な数値（`true/false` 含む）は `invalid_parameter`
- `manual_find` は `cutoff_reason` として `time_budget|candidate_cap|dynamic_cutoff|stage_cap` を取りうる

## 6. 観測項目（`ADAPTIVE_STATS_PATH`）

- `sem_cache_hit`: cache hit の有無
- `sem_cache_mode`: `bypass|miss|exact|semantic|guard_revalidate`
- `sem_cache_score`: semantic 類似度スコア（exact/miss時は `null` 可）
- `latency_saved_ms`: cache hit 時の推定短縮時間（ms）
- `scoring_mode`: `lexical|query_decomp_rrf|gate_rrf|cache`

## 7. 非対象（本書）

- `vault_find`, `vault_coverage`, `vault_audit` などの未公開機能
- `bridge_*` や `get_tooling_guide` の導線

## 8. 補足

- 詳細なI/Oは `spec_manuals.md` を正本として扱う。
- 共通契約・エラーコードは `spec_v2.md` を参照。
