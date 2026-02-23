# RAG設計書 v2（現行実装ベース）

最終更新: 2026-02-23

本書は `manual_find` を中心にした探索設計の説明資料である。  
入出力の正本契約は `spec_manuals.md` を参照。

## 0. 文書責務（正本/要約）

- 本書は探索アルゴリズム、設計意図、性能/トークン最適化方針の説明を目的とする。
- `manual_*` ツールのI/O契約・入力値域・エラー条件の正本は `spec_manuals.md`。
- 共通エラー契約・vault系I/Oの正本は `spec_v2.md` / `spec_vault.md`。
- 本書にある入力検証や返却形状の記述は設計理解のための要約であり、仕様変更時の更新起点にはしない。

## 1. 目的

- 抜け漏れを減らした manual 探索を提供する。
- LLMへの転送量を抑えるため、探索結果は `trace_id` 中心で返す。
- 詳細データは `manual_hits` / `manual_read` / `manual_scan` で段階的に回収する。

## 2. 探索フロー（`manual_find`）

1. 入力検証
- 詳細なパラメータ型/値域/エラーコードは `spec_manuals.md` を正本とし、本章は探索フロー理解のための概要のみ記す。
- `manual_find` は `query` / `manual_id` / `required_terms` を受け取り、`required_terms` 前提の探索フローへ進む。
- 進行方針は「`manual_find` 系（`g0 + g_req` 融合）」を基本とし、網羅要求時のみ `manual_scan` 優先導線へ寄せる。

2. Semantic Cache 照合（有効時）
- `only_unscanned_from_trace_id` 未指定時のみ lookup
- `include_claim_graph=true` 指定時と公開MCP compact経路では cache をバイパス（trace payloadの意味/サイズ差を混在させない）
- `exact` -> `semantic` の順で照合
- hit 時は保存済み `trace_payload` から新規 `trace_id` を発行して返却
- `manual_id` / `budget` / `manuals_fingerprint`（+ `required_terms`）でスコープ分離
- `stage_cap` を含む結果も同一スコープで cache 再利用対象にする

3. 候補抽出（cache miss 時）
- 対象 manual 群を決定
- `.md` は見出しノード単位、`.json` はファイル単位で走査
- `required_terms` 指定時は候補採用条件に必須語一致を統合し、必要に応じて複数pass（例: 2語時）をRRF統合する
- `required_terms` は検索前にDFガードを適用し、診断情報を `applied` に記録する
- `manual_find` は `g0`（requiredなし）と `g_req`（requiredあり）を実行し、RRF融合で候補を統合する
- `g_req` が0件のときのみ `g0` を採用し、required語の緩和を診断値へ記録する
- `applied.selected_gate` と `applied.gate_selection_reason` で最終採用ゲートを追跡できる
- `applied.required_effect_status` などの診断値で、required語が有効だったかを追跡できる
- relax後候補にはノイズ抑制フィルタを適用し、弱一致のみの場合は0件を返す
- lexical シグナル群を評価し、query coverage / phrase / proximity / code一致などを加味して再ランキングする
- BM25 を基礎に query coverage 補正（`SPARSE_QUERY_COVERAGE_WEIGHT` / `LEXICAL_COVERAGE_WEIGHT`）を加点
- Query Decomposition + RRF（`MANUAL_FIND_QUERY_DECOMP_ENABLED`）は既定ON。比較構文に一致した場合のみ sub-query 分解を実行し、部分失敗は許容して継続する。結合時は `base` と `rrf` を正規化混合（`MANUAL_FIND_QUERY_DECOMP_BASE_WEIGHT`）して再スコアする
- 最終ランキングで同一 `path` の過度な集中を抑える多様性リランキングを適用
- 探索中の候補走査量には上限を設ける（詳細閾値・式は実装/仕様を参照）
- 返却直前に動的カットオフを適用し、候補数を縮小しうる（詳細上限は `spec_manuals.md`）
- lexical 一致がある候補のみ採用（`exceptions` 単独では採用しない）

4. 不足時の補助
- `only_unscanned_from_trace_id` 指定時は未探索セクションを優先
- 候補不足（0件 / 低件数 / 高偏り）では `stage_cap` を記録し、未探索候補を `unscanned` に積む

5. 要約・診断
- `summary`（candidates, gap_count, conflict_count, integration_status 等）を retrieval-only で算出
- `claim_graph`（claims/evidences/edges/facets）は `include_claim_graph=true` のときのみ構築
- `next_actions` は非compact経路でのみ生成（公開MCP compactでは `[]` 固定）

6. 保存
- trace payload をメモリストアへ保存して `trace_id` を返す
- `trace_payload` の required/gate 診断（`required_*`, `selected_gate`, `gate_selection_reason`）は `applied` に集約し、トップレベルへは重複保存しない
- 軽量統計を JSONL へ追記
- cache miss 時は `trace_payload` と `source_latency_ms` を Semantic Cache へ保存

## 3. 返却設計

- `manual_find` は `trace_id` 中心の返却を基本とし、詳細候補は `manual_hits` で段階取得する。
- 非compact 経路では `summary` / `next_actions` を返す。
- `include_claim_graph=true` の場合のみ `claim_graph` を返す（未指定時は trace payload 上も空）
- 公開MCPの compact `manual_find` では `next_actions` は常に `[]`。必要時は `inline_hits`（`integrated_top` の先頭ページ、最大5件）を同梱できる

## 4. 代表的な追跡パターン

1. 検索 -> 候補確認
- `manual_find`
- （公開MCP compact では `inline_hits` があればまずそれを使う）
- `manual_hits(kind="candidates")`

2. 候補本文の最小取得
- `manual_read`（section-only）
- 必要なら `manual_scan` で続き取得

3. 不足時の再探索
- `manual_find(only_unscanned_from_trace_id=...)`

## 5. 境界条件

- 数値パラメータの値域/型/エラー条件は `spec_manuals.md` / `spec_v2.md` を正本とする。
- `manual_read` / `manual_scan` のI/O制約（`max_chars` 等）は `spec_manuals.md` を参照。
- `manual_find` は `cutoff_reason` として `time_budget|candidate_cap|dynamic_cutoff|stage_cap` を取りうる

## 6. 観測項目（`ADAPTIVE_STATS_PATH`）

- `sem_cache_hit`: cache hit の有無
- `sem_cache_mode`: `bypass|miss|exact|semantic|guard_revalidate`
- `sem_cache_score`: semantic 類似度スコア（exact/miss時は `null` 可）
- `latency_saved_ms`: cache hit 時の推定短縮時間（ms）
- `scoring_mode`: `lexical|query_decomp_rrf|gate_rrf|cache`

現状メモ:

- 実装済みの `SEM_CACHE_EMBEDDING_PROVIDER` は `none` のみのため、`sem_cache_mode=semantic` は通常発生しない。
- 当面の運用評価では `exact` hit 率と `latency_saved_ms` を主指標として扱う。

## 7. 非対象（本書）

- `vault_find`, `vault_coverage`, `vault_audit` などの未公開機能
- `bridge_*` や `get_tooling_guide` の導線

## 8. 補足

- 詳細なI/Oは `spec_manuals.md` を正本として扱う。
- 共通契約・エラーコードは `spec_v2.md` を参照。

## 9. 設計判断メモ（2026-02-23）

採用:

- `summary` を `claim_graph` 非依存にして、検索器の主経路を軽量化した。
- `claim_graph` は on-demand 診断に限定し、A/B比較や詳細調査時にのみ使う。

棄却:

- `claim_graph` を `manual_find` の標準判定（`summary/gap_count/conflict_count`）に使い続ける案:
  - retrieval指標の改善が確認できず、`needs_followup` だけ増えやすかったため。
- `compare/unknown` へ facet を追加拡張して運用継続する案:
  - facet辞書のドメイン依存が強く、汎用MCPという設計方針と衝突しやすいため。
