# 統合MCPサーバ v2 要件定義（ドラフト）

作成日: 2026-02-03

## 1. 背景 / 課題

- 「特定のものに関する情報を網羅的に取得して」という依頼に対して、既存フロー（`search_text` -> `get_section` -> 例外取得）でも抜け漏れが発生する。
- `read_file` の多用により、LLM側トークン消費が大きい（=コスト/遅延/コンテキスト圧迫）。
- `manuals` の内容をそのまま vault 側へ転記したい（LLMに本文を渡さずに完結させたい）。
- 共有時に複数プロジェクトを渡すのが面倒なので、1つのリポジトリに統合したい。

## 2. ゴール（達成したいこと）

- 1つのMCPサーバで、manual群の参照（必要に応じてmanual指定）と共通vaultへの書き込みを完結できる。
- 「網羅的に取得」に対して、抜け漏れを減らすための“探索戦略の合成”と“見落とし検知”を組み込む。
- LLMに渡す文字量を最小化しつつ、必要な操作（転記・抽出・保存）をサーバ側で完結できる。
- ユーザー向け出力は“参照IDの羅列”ではなく、指標サマリと最小限の診断情報中心にする（参照はツール間連携の内部情報として扱う）。本文や抜粋は必要時のみ段階的に取得する。
- トークン消費量を抑えるため、Exploreの返却は最小限にする。
  - MVPではExploreのサーバ側レポート出力は行わない（チャット返却は `trace_id` / 指標サマリ / `next_actions`（必須、空配列可））
- ユーザーが「整理して説明して」「フローチャートを作って」「JSONスキーマを出して」等の成果物を求める場合は、成果物を `VAULT_ROOT/artifacts/` 配下に保存し、チャットには要約＋保存先のみ返す
  - MVPでは成果物の保存/更新は `vault_create` / `vault_write` / `vault_replace` 等の `vault_*` ツールで行う
  - `.md` / `.json` を許容する
  - 「成果物」以外で、単なる疑問点解消のための詳細説明を残す場合も `vault_*` を使う
  - 日次運用では `VAULT_ROOT/artifacts/daily/YYYY-MM-DD.md` を既定保存先とし、当日ファイルが無ければ `vault_create` で新規作成、あれば `vault_write(mode="append")` で追記する
  - 日次運用の保存は append-only とし、`artifacts/daily/` 配下では「新規作成は `vault_create` のみ」「更新は `vault_write(mode="append")` のみ」を許可する（`overwrite` / `vault_replace` は禁止）
  - `artifacts/daily/` 判定は文字列比較ではなく、`path` 正規化（区切り統一、`.` 除去、`..` 禁止、絶対パス拒否）後に `VAULT_ROOT` 結合 -> 実体パス解決（判定用途のみ。アクセス時のsymlink追跡を許可する意味ではない） -> `daily_root` 配下判定で行う
  - 大小文字差や相対パス揺れによる迂回を防ぐため、境界比較は casefold 後の同一規則で評価する
  - 日次ログの許可ファイル名は `artifacts/daily/YYYY-MM-DD.md` に固定する
  - 日次ログはノイズ抑制のため、`vault_find` の通常探索対象から外せるように `scope.relative_dir` / `glob` を使って探索範囲を制御する

## 3. 非ゴール（当面やらない）

- いま存在する既存MCPサーバ実装のプロキシ/再利用（概念は踏襲するが、実装は新規）。
- PDFや画像など、md/json以外のマニュアル形式対応。
- “トークン消費最適化”の最終形（高度なキャッシュ・圧縮・要約パイプライン等）はMVPでは後回し。

## 4. 対象データ

- Manual:
  - 形式: `md` と `json` のみ（この2形式に限定する）
  - ルートディレクトリは設定で指定（既定: `WORKSPACE_ROOT/manuals`）
  - 標準構造: `manuals/<manual_id>/` と `vault/` を採用する
  - 発見ルール（MVP）: `MANUALS_ROOT` 配下の `.md` / `.json` を再帰的に探索対象とする（ワークスペース内で完結）
  - `md` は「見出し単位（`#`〜`######`）」で section 化して扱う（ファイル単位ではない）
    - section は“見出しノード”を指し、階層構造を持つ
    - `manual_read(scope="section")` は原則として「当該見出し配下の本文（全子孫見出しを含む）」を返す
  - `json` は任意構造を許容する
    - 検索/スニペット生成は JSON を文字列化したテキスト（例: `JSON.stringify` 相当）に対して行う
    - MVPでは `manual_read` は `scope="file"` を基本とし、`json_path` に依存しない
- Vault:
  - ルートディレクトリは設定で指定（既定: `WORKSPACE_ROOT/vault`）
  - ディレクトリは manual別に分割しない（出し入れしやすさを優先し、共通の用途別構成にする）
  - 推奨用途別構成: `artifacts/`, `drafts/`, `notes/`, `.system/`
  - manualとの紐付けが必要な場合は、ディレクトリ分割ではなくファイル内メタデータ（例: `source_manual_ids`）で管理する

## 5. 主要ユースケース（MVP）

1. あるトピックに関してmanual群から関連箇所を“漏れにくく”探索し、探索指標（件数・再試行・警告等）を返す（診断ログはMVPでは stderr 出力、レポートのファイル永続化はしない。別途、軽量統計を永続化して閾値微調整に使ってよい。対象は `manual_find` を主とし、vault網羅監査の停止判定に必要な最小集計値のみ追加で含める）。
2. manual内の特定セクション/範囲を、LLMに本文を返さずにvaultへコピーする。
3. vault内の既存ファイルを検索・部分更新し、下書き/成果物を蓄積する。

## 6. 機能要件（MVP）

### 6.1 Manual検索（抜け漏れ対策）

- 単一戦略に依存しない（例: キーワード検索のみで終わらない）。
- 最低限、以下の探索戦略を“合算”して候補を作る。
  - 文字正規化（`NFKC`、全半角統一、casefold、改行/空白統一、ハイフン/中点/括弧/スラッシュ類の代表化、数字表記ゆらぎの吸収）を考慮した検索
  - ルーズ検索（区切り無視）
  - 同義語/言い換えの辞書展開（正規化とは分離して適用）
  - 例外語彙スキャン（注意/除外/対象外 等）
  - TOC/見出し語からの候補補完（検索ヒット0件でも探索を止めない）
- “見落とし検知”を実装する（例: 候補が極端に少ない、同一章に偏る、などのときに自動で探索範囲を広げる）。
  - Stage 4 発火の初期閾値: 候補0件 / 候補3件未満 / 1ファイル偏重（80%以上、かつ候補総数5件以上） / `intent=exceptions` で例外ヒット0
- ユーザーのプロンプトから「検索意図」と「検索語（厳密一致候補）」を抽出し、検索計画に反映できる（ツール内のワークフローとして固定化する）。
  - ただし厳密一致のみには依存せず、必ず補助戦略（正規化/ルーズ/見出し補完等）を併用する（抜け漏れ優先）。
- Stage 0〜3 は 1 ツールに集約し、Stage 2〜3 が失敗しても Stage 0〜1 の結果サマリは返す（部分成功）。
- Stage 0〜3 の後に Stage 3.5（統合判断）を常時実行し、候補統合・矛盾/欠落判定・十分性評価を行う。
- `next_actions` は Stage 3.5 の統合判断結果を起点に返す（`insufficient_candidates` / `resolve_conflicts` / `fill_gaps` / `reduce_file_bias` / `manual_completed`）。
- 探索予算の既定は `budget.time_ms=60000`（1分）、`budget.max_candidates=200` とする。
- 打ち切り時は `cutoff_reason` をユーザー向け summary に含め、打ち切りがない場合は省略する。
- 打ち切りや上限制約で探索しきれなかった対象は `unscanned_sections` として扱い、`reason`（`time_budget` / `candidate_cap` / `stage_cap` / `hard_limit`）付きで識別できるようにする。
- 再探索のため、`manual_find` は `only_unscanned_from_trace_id` を受け付け、指定時は当該 trace の `unscanned_sections` を優先対象に探索する。
  - `trace_id` が期限切れ・未存在の場合は `not_found` を返し、暗黙フォールバック（全体探索への自動切替）はしない。
- 軽量統計は `manual_find` を主対象に永続化し、次回以降の Stage 4 発火閾値を微調整できるようにする。vault網羅監査の停止判定に必要な最小集計値（例: `added_evidence_count`, `added_est_tokens`, `marginal_gain`）のみ追加で含めてよい。
  - 本文・スニペット・候補本文は統計ファイルに保存しない。
  - 調整幅と範囲は固定する（`candidate_low_threshold`: 初期3、変化幅±1/24h、範囲2..6。`file_bias_threshold`: 初期0.80、変化幅±0.03/24h、範囲0.70..0.90）。
  - 再現率proxyは `C_q`（`manual_find` 候補集合）と `U_q`（同一 `trace_id` で後続利用された `ref` 集合）から `|C_q ∩ U_q| / |U_q|` で算出し、`|U_q|=0` は集計除外する。
  - 悪化時は既定閾値へロールバックできること（直近100件で再現率指標が3%以上低下、または `cutoff_reason` 発生率が5%以上悪化）。

### 6.1.1 Vault探索（成果物の抜け漏れ精査）

- vault内の成果物を探索するため `vault_find` を用意する。
- Stage 0〜1（正規化一致 / loose一致）を実行し、続いて Stage 1.5（統合判断）で候補統合・偏り/不足判定を行う。
- `next_actions` は Stage 1.5 の統合判断結果を起点に返す（`verify_coverage` / `fill_gaps` / `reduce_file_bias` / `vault_completed`）。
- 探索予算の既定は `budget.time_ms=60000`（1分）、`budget.max_candidates=200` とする。
- 運用上、日次追記ログ（例: `artifacts/daily/`）を探索から除外したい場合は、`scope.relative_dir` / `glob` で対象を限定する。

### 6.1.2 Vault網羅走査（行レンジベース）

- 「抜け漏れ疑い」に対しては、検索語依存を避けるため `vault_scan` による行レンジ走査を提供する。
- `vault_scan` はセクション有無に依存せず、任意のテキストファイルを `start_line/end_line` 単位で逐次取得できること。
- 入力は `path`, `cursor`, `chunk_lines` を受け取り、出力は `applied_range`, `next_cursor`, `eof`, `truncated_reason` を返す。
- `vault_find` / `vault_scan` / `vault_coverage` / `artifact_audit` は、次ステップを示す `next_actions` を必須で返す（提案なしは空配列）。
- 既定は「未カバー領域（後述 `vault_coverage`）」を優先して走査し、必要時のみ全行走査へ昇格する。
- 初回走査（未カバー情報が未作成）は先頭行から開始し、2周目以降は `uncovered_ranges` を優先対象にする。
- 走査停止は固定回数ではなく、`coverage_ratio` と `marginal_gain`（追加根拠数/追加トークン）で判定する。

### 6.1.3 Coverage監査（成果物整合）

- 成果物（例: フローチャート）の各要素に `source_lines`（出典行範囲）を必須化できる監査ツールを提供する。
- `vault_coverage` は「参照済み行範囲」と「未参照行範囲（uncovered_ranges）」を返し、`coverage_ratio` を算出する。
- `artifact_audit` は最低限、`根拠なし要素` / `孤立分岐` / `片方向参照` を検出して返す。
- `coverage_ratio` が閾値未満、または `artifact_audit` に重大項目がある場合は `vault_scan` の全行走査を推奨する。

### 6.2 転送量削減（トークン消費抑制）

- 既定動作として「全文返却」を避ける（ツールが返すのは基本 snippet / 範囲 / メタデータ）。
  - ただし `.json` はMVPで `scope="file"` を基本とし、`max_chars` とハードリミットで返却量を制御する。
- サーバ内完結の操作（コピー、抽出、保存）を優先して用意する。
- “全文読み” を誘発するツールを避け、段階的取得（snippet -> section -> file）で必要な分だけ取得できるようにする。
  - 無制限の `read_file`（1ファイル全文取得）は提供しない。
- `manual_read` の既定上限は `max_sections=20`、`max_chars=8000` とする。

### 6.3 Manual -> Vault の転記（サーバ内完結）

- manualの特定セクション、または検索ヒット箇所（範囲）をvaultへ書き込む。
- この操作は本文をLLMへ返さずに実行可能であること（結果は「書き込んだ先」「件数」「参照元」などのメタ情報中心）。
- manualファイル全体を `manual_id + path` 指定で vault へ転記する経路を用意し、以後の修正はvault側で行えるようにする。

### 6.4 Vault操作

- 一覧、読み取り（範囲指定）、書き込み、置換、検索。
- 書き込みのモード（上書き/追記）を明示する。
- 新規作成は専用ツール（`vault_create`）で行い、上書き/追記は既存ファイルに対してのみ許可する。
- 成果物保存先（`VAULT_ROOT/artifacts/`）では `.md` / `.json` のみ許可する。

## 7. 非機能要件

- 安全性:
  - `MANUALS_ROOT` / `VAULT_ROOT` 外へのアクセス禁止
  - パス正規化（`..`、絶対パス拒否）
  - 書き込み対象拡張子は原則制限しない（MVP）
  - ただし `VAULT_ROOT/artifacts/` 配下は `.md` / `.json` のみ許可する
- エラーハンドリング:
  - ツールごとのエラーコード/メッセージ規約
  - 失敗時に“何が足りないか”が分かる情報を返す（パス不正、権限、該当なし等）
- ログ:
  - 操作ログ（どのツールがどのパスに何をしたか）を最小限出す
  - 操作診断ログはMVPでは永続化せず、stderr へ出力する
  - 最適化用途の軽量統計（本文非含有）のみ `ADAPTIVE_STATS_PATH` へ永続化を許可する
  - 軽量統計は `manual_find` を主対象とし、vault網羅監査の停止判定に必要な集計値（例: `added_evidence_count`, `added_est_tokens`, `marginal_gain`）のみ追加で含めてよい
- 配布:
  - GitHubで公開する単一リポジトリ
  - 初期導入手順が短い（実行コマンドが少ない）
  - 設定は極力少なくし、未指定でも“使いやすい既定値”で動作する（利用者がMCPに不慣れな前提）

## 8. ツール設計（案）

※ツール名は衝突回避/誤用防止のため “manual_” “vault_” “bridge_” prefix を付ける（例外: `artifact_audit` は cross-domain 監査ツールとして prefix なしを許可）。
※同名ツールの多重定義はしない（例: `search_text` を manual/vault で共用しない）。
※命名は「動詞+目的語」を基本にする（例: `manual_find`, `vault_read`）。

- Manual
  - `manual_list`（manual ID一覧。ファイルは返さない）
  - `manual_ls`（manual配下の対象ファイル一覧。`.md` / `.json` を含む。manual ID一覧は返さない）
  - `manual_toc`（目次取得。`.md` は見出しノード、`.json` はファイルノードを返す。機械処理の最小セットは `kind/node_id/path/title/level/parent_id/line_start/line_end`）
  - `manual_find`（探索オーケストレーター。戦略合算+見落とし検知。通常返却は `trace_id` / 指標サマリ / `next_actions`（必須、空配列可））
  - `manual_hits`（探索候補や `unscanned_sections` 詳細をページング取得する。structuredContent肥大化を避ける）
  - `manual_read`（段階的取得。`snippet/section/sections/file` を明示し、上限付きでのみテキストを返す）
  - `manual_excepts`（例外語彙抽出）
- Vault
  - `vault_ls`
  - `vault_read`（既定: 範囲指定。全文は明示フラグ）
  - `vault_find`
  - `vault_scan`（行レンジ単位の逐次走査）
  - `vault_coverage`（参照済み/未参照の行レンジ監査）
  - `artifact_audit`（成果物の根拠・整合監査）
  - `vault_create`
  - `vault_write`
  - `vault_replace`
  - `vault_search`
- Bridge（統合専用）
  - `bridge_copy_section`
  - `bridge_copy_file`
- Workflow（MVP）
  - （MVPではworkflow専用ツールは作らない）

## 9. 評価指標（まず“測れる形”にする）

- 再現率（抜け漏れ）:
  - 可変コーパスの実運用クエリに対して、期待される section/ファイルが候補に含まれる割合
  - 成果物監査では `coverage_ratio`（根拠行付き要素の割合）と `uncovered_ranges` の減少率を併用する
- 転送量:
  - ツールレスポンスの文字数/バイト数（LLMに渡す量の代理指標）
  - “bridge転記”利用時に本文返却が起きていないこと
- 運用最適化:
- 軽量統計（`manual_find` 主対象 + vault網羅監査の最小集計値）に基づく Stage 4 発火率と、再現率/推定トークン量の推移を確認する
  - 運用ポリシーは「再現率の下限を満たす範囲で推定トークン量が最小の設定を採用する（Recall下限付き最小Cost）」とする
  - 推定トークン量は `est_tokens = ceil((chars_in + chars_out) / 4)` で算出し、`est_tokens_in` / `est_tokens_out` を併記して記録する
  - 追加で `marginal_gain = added_evidence_count / added_est_tokens` を記録し、低下時の停止判定に使えること
