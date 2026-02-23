# 統合MCPサーバ v2 要件定義（現行運用版）

最終更新: 2026-02-23

本書は現行実装の要件を運用向けに整理した文書である。  
入出力の正本契約は `spec_v2.md` / `spec_manuals.md` / `spec_vault.md` を優先する。

## 0. 文書責務（正本/要約）

- 本書は公開スコープ、非機能要件、運用要件、評価指標、観測性の要件整理を担当する。
- ツールI/O契約（型、既定値、値域、返却shape、エラー条件）の正本は `spec_v2.md` / `spec_manuals.md` / `spec_vault.md`。
- 本書に出てくるツール仕様の記述は運用観点の要約であり、厳密な契約定義としては扱わない。
- 同一ルールの詳細値を更新する場合はまず spec 系を更新し、本書は必要な範囲だけ追随更新する。

## 1. 背景

- manual群（`manuals/<manual_id>/`）を横断して、抜け漏れを抑えた探索を行いたい。
- LLMへの本文転送量を抑え、必要な箇所だけを段階取得したい。
- 成果物やメモは `vault/` 配下に安全に保存・更新したい。
- 探索品質を継続的に改善するため、評価（Eval）を先に定義し、変更を指標で判定したい。

## 2. 現行公開スコープ

公開ツールは次の11個のみ:

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

非公開/廃止（本リリースでは提供しない）:

- `manual_list`
- `manual_excepts`
- `vault_find`
- `vault_write`
- `vault_search`
- `vault_coverage`
- `vault_audit`
- `bridge_*`
- `get_tooling_guide`

## 3. 機能要件

### 3.1 Manual探索

本節は運用要件の要約であり、`manual_*` の厳密なI/O/制約/エラー契約は `spec_manuals.md` を正本とする。

- `manual_find` は `required_terms` 前提の探索で `g0/g_req` を統合し、required語の効き具合を診断値として返す。
- 一次候補は lexical中心に候補化し、coverage/proximity等のシグナルで再ランキングする（詳細な係数・シグナル定義は `rag_design_v2.md` と実装を参照）。
- 導線は `manual_find`（+ `manual_hits`）を基本とし、網羅要求の入力時のみ `manual_scan` 優先導線を許可する。
- 結果は `trace_id` 中心で返し、候補詳細は `manual_hits`、本文は `manual_read` / `manual_scan` で段階取得する。
- 公開MCPツール（`app.py`）の `manual_find` / `manual_hits` は常時 compact 経路を使い、返答トークンを抑制する。
- compact `manual_find` は `next_actions=[]` を返し、既定で `inline_hits`（`integrated_top` 先頭ページ）を同梱する。
- `summary` は retrieval-only の軽量診断であり、`claim_graph` は任意の詳細診断（`include_claim_graph=true` 時のみ）として扱う。
- `manual_find` は semantic cache（exact -> semantic）を利用可能とし、manual更新時は fingerprint ベースで自動無効化する。
- `only_unscanned_from_trace_id` 指定時は cache をバイパスし、未探索優先フローを維持する。
- `manual_toc` は対象ファイル数が大きい場合に `needs_narrow_scope` を返し、`path_prefix` による段階的な絞り込みを要求する。

### 3.2 Manual本文取得

- `manual_read` は markdown の `section` 取得専用とする（JSONは対象外）。
- `manual_read` の同一セクション再要求時は、同一ファイルの次行から `manual_scan` 相当の自動フォールバックを行う。
- `manual_scan` は逐次取得の主手段であり、ページング/継続取得のI/O契約は `spec_manuals.md` を正本とする。

### 3.3 Vault操作

- `vault_ls` は非再帰の1階層一覧を返す（`path` 未指定時はルート）。
- `vault_create` は新規作成専用（既存ファイルは `conflict`）。
- `vault_read` は範囲読みを基本とし、必要時のみ `full=true`。
- `vault_scan` は行単位の逐次取得を提供する。
- `vault_replace` は文字列置換を提供し、`daily/` と `.system/` は禁止。

### 3.4 Eval駆動RAG（manual_find系）

- 評価は `manual_find` を中心とし、取得品質（retrieval）と軽量診断（`summary`）を分離して測定する。
- 評価データセットは少なくとも次を持つ:
  - `query`
  - `manual_id`（必須）
  - `expected_paths`（期待される根拠パス群）
  - `forbidden_paths`（誤検知として扱うパス群、任意）
- 評価実行は `manual_find` -> `manual_hits(kind="candidates")` を基本導線とする。
- 比較評価では `SEM_CACHE_ENABLED=false/true` の2条件を同一データセット・同一設定で実行し、差分を比較する。
- 評価指標は少なくとも次を算出する:
  - `hit_rate@k`（`expected_paths` が上位k件に含まれる割合）
  - `recall@k`（`expected_paths` の回収率）
  - `mrr@k`（最初の正解ヒット順位の逆数平均）
  - `precision@k`（上位k件の適合率）
  - `gap_rate`（`summary.gap_count > 0` の割合）
  - `conflict_rate`（`summary.conflict_count > 0` の割合）
  - `p95_latency_ms`（評価対象呼び出しの95パーセンタイル遅延）
  - `tokens_per_query`（要約+候補メタ情報から推定した1クエリ当たりトークン量）
- CIゲートは閾値ベースで実施し、閾値未達時は失敗とする。
- 本番運用のトークン消費とレイテンシを抑えるため、通常運用では `include_claim_graph=false` を既定運用とし、詳細評価はバッチ/CIで `include_claim_graph=true` を明示して実行する。

## 4. 安全要件

- パスは相対指定のみ。`..`、絶対パス、ルート外アクセスは禁止。
- symlink 経由アクセスは禁止。
- `.system/` は予約領域としてユーザー変更を禁止。
- `daily/` 作成時は `daily/YYYY-MM-DD.md` 形式のみ許可。

## 5. 入力バリデーション要件

- 整数型パラメータに非整数を渡した場合は `invalid_parameter` を返す。
- 整数型パラメータに `true/false` を渡した場合も `invalid_parameter` を返す。
- 下限/上限違反も `invalid_parameter` を返す。
- 実装内部の変換失敗を `conflict` にフォールバックしない。
- 具体的なパラメータ別の値域・既定値は `spec_manuals.md` / `spec_vault.md` / `spec_v2.md` を正本とする（本書では再掲しない）。

## 6. 観測性要件

- ツール呼び出しのエラーログは JSONL で stderr に出力する（成功呼び出しは既定では出力しない）。
- `manual_find` の軽量統計は `ADAPTIVE_STATS_PATH` に永続化する。
- 統計には本文を保存しない（メタ情報のみ）。
- `manual_find` 統計には少なくとも `sem_cache_hit`, `sem_cache_mode`, `sem_cache_score`, `latency_saved_ms`, `scoring_mode` を含める。
- `TraceStore` / Semantic Cache に保存する `manual_find` の `trace_payload` は、required/gate診断を `applied` 配下へ集約し、トップレベル重複を避ける。
- Eval実行結果は再現可能な形式（JSON/JSONL）で保存し、比較可能なサマリを生成する。
- Eval結果には少なくとも次を含める: 実行日時、評価データセットID（またはハッシュ）、指標値、しきい値判定結果。

## 7. 非機能要件

- Python `>=3.12,<3.13`
- stdio 実行を標準運用とする。
- 既定設定で開発環境起動できること。
- 単体テストとE2Eテストが通ること。
- Evalジョブは通常運用パスと分離し、運用時の応答性能に恒常的な影響を与えないこと。
- 同一データセット・同一コードで評価を再実行した場合、指標差分が説明可能な範囲に収まること。

## 8. Eval受け入れ基準（初期）

- 初期導入時に、評価データセットと評価ランナーがリポジトリ内で管理されていること。
- CIで最低1つのEvalゲートが有効化され、失敗時に原因追跡可能な出力が残ること。
- 主要指標の暫定閾値（例: `hit_rate@5`, `gap_rate`）が明記され、変更時にレビュー対象となること。
- 初期閾値は次を採用する:
  - `hit_rate@5 >= 0.80`
  - `recall@5 >= 0.80`
  - `mrr@5 >= 0.60`
  - `precision@5 >= 0.50`
  - `gap_rate <= 0.25`
  - `conflict_rate <= 0.20`
  - `p95_latency_ms <= 1200`
  - `error_rate == 0`
- 初期導入後2週間は閾値を固定し、その後の改定は週次レビューで行う。
- 現行運用プロファイル（現在使用しているPCを含む標準環境）では embedding provider 導入は要件外とし、embedding 導入可否の受け入れ基準は本書では定義しない。

## 9. Eval初期運用プロファイル

- 評価データセット初期母数は `30問` とする。
- 初期配分は `definition/procedure/eligibility/exceptions/compare/unknown` を各 `5問` 目安とする。
- ゴールド正解は `path` 単位で定義し、`start_line` は参考情報（非ゲート）として扱う。
- 評価時の `manual_find` 実行条件は次で固定する:
  - `expand_scope=true`（後方互換パラメータとして受理）
  - `include_claim_graph=false`
  - `budget.time_ms=60000`
  - `budget.max_candidates=200`
- CI失敗ポリシーは2段階とする:
  - 導入後2週間は warning 運用（レポート出力のみ）
  - 以後は hard fail（閾値未達でCI失敗）
- Eval結果はファイル保存せず、CLI標準出力で確認する。
- Semantic Cache比較時は `scripts/eval_manual_find.py --compare-sem-cache` を利用し、`baseline` と `with_sem_cache` の差分 (`metrics_delta`) を記録する。
- `sem_cache_compare` の比較サマリには少なくとも `tokens_per_query_delta` と cache 効率指標（例: `sem_cache_hit_rate`, `sem_cache_exact_hit_rate`, 推定短縮時間差分）を含める。
- 現行運用プロファイル（現在使用しているPCを含む標準環境）では `SEM_CACHE_EMBEDDING_PROVIDER=none` を固定運用とし、embedding provider は導入しない。
- したがって当面の比較は正規化後 exact cache 中心の評価として扱う（`semantic` hit は通常 0）。
- `SEM_CACHE_SIM_THRESHOLD` は互換性維持・将来拡張余地のために残している設定値であり、現行運用での導入ロードマップを意味しない。
- cache 改善はまず `normalize_text()` / `_cacheable_query()` の安全な表記ゆれ吸収（正規化後 exact の強化）を対象とし、意味類似ベースの再利用は別プロファイル/別合意なしに採用しない。
- Eval結果JSONには少なくとも次を含める:
  - `dataset_hash`
  - `metrics`
  - `thresholds`
  - `pass_fail`
  - `failed_cases`

## 10. 本書の位置づけ

- 本書は運用要件をまとめたガイドであり、I/O契約の唯一の正本ではない。
- 仕様差分がある場合は `spec_v2.md` / `spec_manuals.md` / `spec_vault.md` を優先する。

## 11. 改訂提案（参考）

- `manual_find` の lexical-only 再設計案（未実装）: `docs/proposals/manual_find_lexical_rebuild.md`

## 12. 設計判断（2026-02-23）

採用した方針:

- `manual_find.summary` は `claim_graph` 非依存の retrieval-only 診断にする。
- `claim_graph` は `include_claim_graph=true` 時のみ構築する（on-demand）。
- `include_claim_graph=true` 時は semantic cache をバイパスし、payload混在を避ける。
- 公開MCP compact経路では `next_actions=[]` を維持し、既定で同梱される `inline_hits` を主導線にする。
- 現行運用プロファイルでは semantic cache の非完全一致（embedding 類似検索）は採用せず、正規化後 exact cache を前提に運用する。

棄却した案（理由つき）:

- `claim_graph` を常時構築したまま `summary` だけ使う案:
  - 検索精度の改善がA/Bで確認できず、CPU/レイテンシだけ増えやすかったため。
- `claim_graph` を即時完全削除する案:
  - `include_claim_graph=true` の診断用途と比較評価（A/B）を一度に失い、移行リスクが高いため。まず必須経路から外した。
- facet を `condition` / `amount` へ即拡張する案:
  - ドメイン依存が強まり汎用性を下げる一方、unknown低減の効果量が未計測だったため。先に `claim_graph` 自体を任意機能へ降格した。
