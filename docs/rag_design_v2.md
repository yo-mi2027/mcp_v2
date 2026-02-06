# RAG設計書 v2（探索オーケストレーター方式）

## 1. 目的

「特定のものに関する情報を網羅的に取得して」という要求で抜け漏れが出やすい問題を、サーバ側の探索ワークフロー固定化で改善する。

同時に、LLMへ返す本文量を減らし（トークン節約）、manual->vault転記をサーバ内で完結できるようにする。

## 2. 前提

- manual形式は `.md` / `.json` に限定する
- データ配置は `manuals/<manual_id>/` と `vault/` を標準とし、manual探索は `manual_id` 指定時のみ対象manualに限定する
- `.md` は見出し（`#`〜`######`）単位で section（ノード）化して扱う
  - LLMが呼ぶ主要ツールは `manual_find` とし、低レイヤの呼び分けを減らす
- `.json` は任意構造を許容し、section 抽出には依存しない（検索は文字列化テキストに対して行う）

## 3. 全体像

入力（ユーザー要求）から、探索計画（Query Plan）を作り、複数の探索戦略を合算して候補を作る。

初手で `get_tooling_guide` を呼び、`first_tool` を取得してから実処理に入ってよい（任意）。

1. Prompt Parse（意図・制約・重要語の抽出）
2. Query Plan（検索語セット + 戦略セット + 見落とし検知ルール）
3. Execute（戦略を実行し、候補を合算）
4. Integrate & Judge（Stage 0〜3候補を統合し、十分性/矛盾/欠落を判定）
5. Diagnose（統合判定の結果を受け、必要なら自動で再探索）
6. Output（trace_id + summary）

追加:

- 候補一覧（S0）をLLMが確認する必要がある場合は、`manual_hits(trace_id, offset, limit)` を使ってページング取得する（structuredContent肥大化を避ける）。
- 未探索セクション（`unscanned_sections`）の詳細が必要な場合も、`manual_hits` でページング取得する。
- MVPでは trace を複数件保持する（共通Configの `TRACE_MAX_KEEP`, `TRACE_TTL_SEC`）前提とする。

## 3.1 検索の実装仕様（固定）

v2では「LLMがその場で検索のやり方を組み立てる」のではなく、`manual_find` 内部の検索手順を固定する。
これにより、プロンプト/モデル差による揺れを減らし、抜け漏れ（再現率）を改善する。

### 用語

- `doc`: 1ファイル（`.md` / `.json`）
- `node`:
  - `.md`: 見出しノード（`#`〜`######`）
  - `.json`: 探索時も読み取り時もファイル全体を扱う（MVP）
- `target_text`: 実検索対象のテキスト
  - `.md`: nodeごとの本文（既定: 当該見出し配下の本文。全子孫見出しを含む）
  - `.json`: `JSON.stringify` 相当の文字列

注記:

- `manual_read(scope="section")` の既定も、探索対象テキスト定義と同様に「当該見出し配下の本文（全子孫見出しを含む）」を返す。
- `scope="sections"` は複数sectionをまとめて取得するために使う。
- `.json` は MVPでは `scope="file"` を基本とし、`ref.json_path` に依存しない。

### 正規化（Normalization）

検索は、原文そのものではなく「正規化済みテキスト」に対して行う。

- 文字正規化: Unicode正規化 `NFKC`
- 全半角統一: 英数字・記号・カナのゆらぎを吸収
- 英字の大小文字: casefold で統一
- 改行: `\r\n` / `\r` を `\n` に統一
- 空白: 全角スペース/タブを含め、連続空白は1個に圧縮
- 記号のゆらぎ: 代表的なハイフン類（`-`/`‐`/`‑`/`–`/`—`/`−`）を `-` に寄せる
- 代表的な中点類（`・`/`･`）は `・` に寄せる
- 括弧/スラッシュ類のゆらぎ: 代表形へ寄せる（例: `（`/`(`、`／`/`/`）
- 数字表記のゆらぎ: 全角/半角、ローマ数字等を可能な範囲で同一視する（例: `第３条`/`第3条`）

※同義語展開は正規化には含めず、別戦略（辞書展開）として扱う。  
※漢字変換・形態素解析はMVPでは行わない。

### 検索戦略セット（Stage方式）

v2では探索モード分岐は設けず、常に「抜け漏れを減らす探索」を既定にする。
既定挙動は manual で Stage 0〜3 + 3.5 を常時実行し、Stage 4 のみ見落とし検知条件を満たしたときに実行する。vault では Stage 0〜1 + 1.5 を常時実行する。

- Stage 0（ベース）:
  - `.md` node単位の「正規化部分一致（本文）」+「見出し一致（heading）」
- Stage 1（表記ゆれ強化）:
  - `loose` を追加で実行
  - 同義語/言い換え辞書で `soft_terms` を拡張し、拡張語にも正規化一致/loose一致を適用
- Stage 2（例外特化）:
  - 例外語彙スキャン（exceptions辞書）
- Stage 3（参照追跡）:
  - “第X章参照”“別表”“〜に準ずる”等の参照を検出し候補追加
- Stage 3.5（統合判断）:
  - Stage 0〜3の候補を `node_key(manual_id + path + start_line)` で統合
  - `signals` と `hit_count` を集約し、矛盾候補（conflicts）と欠落候補（gaps）を抽出
  - 十分性指標（`sufficiency_score`）を算出し `next_actions` の初期案を生成
- Stage 4（範囲拡張）:
  - `manual_id` 指定がある場合は対象範囲を拡張（例: `MANUALS_ROOT` 配下の全manualへ）
  - または探索対象のファイル集合を拡張（設計次第。MVPでは「manual_id指定の解除」を優先）
  
注記:
- Stage 0〜3 は 1 ツールに集約し、Stage 2〜3 が失敗しても Stage 0〜1 の結果サマリは返す（部分成功）
- Stage 3.5 は Stage 0〜3 の結果が部分成功でも実行し、利用可能候補のみで統合判断する
- `manual_find.max_stage` は `3|4` のみ許可する（`3` は Stage 4無効、`4` は Stage 4条件付き有効、`0|1|2` は `invalid_parameter`）
- Stage 3.5 は `max_stage` の値に関わらず常時実行する（`max_stage` は Stage 4 の許可有無のみを制御）
- Stage 4 の発火条件（閾値）:
  - 候補が 0 件
  - 候補が 3 件未満
  - 1ファイル偏重（候補の 80%以上が同一ファイル。かつ候補総数が 5 件以上）
  - `intent=exceptions` で `exception` ヒットが 0
- 追加拡張のAI裁量は許可するが、理由は `escalation_reasons` に必ず記録する
- `only_unscanned_from_trace_id` が指定された場合は、通常の範囲拡張より前に当該 trace の `unscanned_sections` を優先探索する

### Vault探索（成果物向け）

vault内の成果物探索は `vault_find` を用いる。内容の性質上、例外語彙スキャン等は実施せず、Stage 0〜1（正規化一致 / loose一致）を実行した後、Stage 1.5（統合判断）で候補統合・偏り/不足判定を行う。

vault向け Stage 1.5（統合判断）:

- 候補を `path + line_range` 単位で統合し、重複ヒットを集約する
- `signal_coverage(normalized, loose)` と `file_bias_ratio` を算出する
- `gap_ranges_count` と `sufficiency_score` を算出し、`next_actions` の初期案を生成する
- 統合判断の意図は `next_actions.type` と `next_actions.params` で表現する

### ルーズ一致（loose）

`loose` は、空白や一部記号を無視して一致させる（OCR対策由来だが表記揺れにも効く）。
実装は「クエリ文字列の各文字の間に、空白/中点/スラッシュ/ハイフン等を許容する正規表現を生成する」方式でよい。

### 候補生成と重複排除

- 候補は node 単位（`.json` はファイル単位）で生成する。
- 同一nodeへの複数ヒットは統合し、`signals` と `hit_count` を集約する。
- 候補集合S0は、以下の順で追加する（順序は安定化のため固定）。
  1. `must_include_terms` の一致（強シグナル）
  2. `soft_terms` の一致（正規化）
  3. 同義語/言い換え展開語の一致（正規化）
  4. `soft_terms` の一致（loose）
  5. 同義語/言い換え展開語の一致（loose）
  6. 例外語彙スキャン（exceptions辞書）
  7. 参照追跡（“第X章参照” 等）

### 見落とし検知（Diagnose）とエスカレーション

既定で Stage 0〜3.5 は実施済みとし、以下の条件でエスカレーションする。

- Stage 4 の初期発火条件（固定）:
  - S0が0件
  - S0が少ない（例: 3件未満）
  - 1ファイルに偏っている（例: S0の80%以上が同一ファイル）
  - `intent=exceptions` で例外ヒットが0
- 統合判断由来の追加条件:
  - `conflict_count > 0`（要追加読取または追加探索）
  - `gap_count > 0`（未カバー観点あり）

エスカレーションの打ち手（順序固定）:

1. `conflict_count > 0` の場合は `manual_read(scope="section")` を優先提案
2. `gap_count > 0` または偏り過多の場合は `manual_find` 再探索を提案
3. それでも不足時に Stage 4（範囲拡張）を実行
4. 追加拡張を行う場合は `escalation_reasons` に理由を記録する
5. `only_unscanned_from_trace_id` 指定時は、未探索セクションの回収を優先し、全体拡張は必要時のみ行う

vault側の Diagnose:

- `vault_find` は Stage 1.5 後に、`gap_ranges_count > 0` なら `vault_scan` を優先提案する
- `file_bias_ratio` が閾値超過なら `vault_find` の再実行を提案し、探索範囲/条件を調整する
- `sufficiency_score` が高く `gap_ranges_count = 0` の場合は `vault_coverage` または `stop` を提案する

### 出力（トークン節約の固定）

通常出力は `ref` を露出しない。
候補ごとに返すテキストは短く固定する（例: snippetは前後N文字まで）。

## 4. Prompt Parse（思考ワークフロー）

ツール内部で、最低限以下を抽出する（LLMに任せない）。

- `intent`:
  - `definition`（定義）
  - `procedure`（手順）
  - `eligibility`（条件/可否）
  - `exceptions`（例外/対象外）
  - `compare`（比較/優先順位）
  - `unknown`
- `must_include_terms`（厳密一致候補。固有名詞・型番・条文番号など）
- `soft_terms`（ゆるく一致させたい語。表記揺れを想定）
- `negative_terms`（除外したい語があれば）

注意:

- 「厳密一致検索」は強力だが、それだけに依存しない（抜け漏れを減らすため）。
- 厳密一致は“強いシグナル”として候補追加に使い、補助戦略（正規化/ルーズ/見出し補完）を必ず併用する。

## 5. 探索戦略（最低限の合成セット）

### 5.1 構造（見出し/ToC）由来

- 見出しタイトルに対する一致（`must_include_terms` と `soft_terms`）
- 見出し語彙の共起（例: “対象外/除外/適用しない” が含まれる見出しを exceptions に優先）
- 重要: 見出し一覧は探索の内部シグナルとして利用するが、LLMへの出力として“見出し一覧そのもの”を返す必要はない（ノイズ/トークン増のため）。
  - 出力は、見出し一致を根拠に候補化された `ref` のみ（`signals=["heading"]` 等で十分）。

### 5.2 本文検索

`soft_terms` を中心に、以下を合算する。

- 正規化一致（全半角、ハイフン等のゆらぎ）
- ルーズ一致（区切り記号や空白を無視）
- 正規表現（必要時のみ。コストが上がるため）

### 5.3 例外語彙スキャン

exceptions辞書に基づき、行/段落を走査して候補を生成する。

### 5.4 参照の追跡（軽量）

“第X章参照”“〜に準ずる”“別表”などの参照語を検出し、候補を追加する（再現率向上の安全ネット）。

## 6. 見落とし検知（自動エスカレーション）

本章の Diagnose ルールは、重複定義を避けるため 3章の以下に一本化する。

- manual側: 「見落とし検知（Diagnose）とエスカレーション」（3章）
- vault側: 「vault側の Diagnose」（3章）

固定方針:

- `next_actions.type` は「次に呼ぶツール名」を返す
- 意図や理由は `next_actions.params` と summary 指標で表現する

運用最適化（軽量・永続）:

- `manual_find` 実行ごとに軽量統計（`candidates`, `warnings`, `max_stage_applied`, `scope_expanded`, `cutoff_reason`, 推定トークン量）を保存し、次回以降の Stage 4 発火閾値を小幅に調整してよい。
- `unscanned_sections_count` も軽量統計に含めてよい（本文は保存しない）。
- 本文・スニペット・候補本文は保存しない。
- 悪化が検知された場合は既定閾値へロールバックする。
- 運用ポリシーは「Recall 下限を満たす範囲で推定トークン量が最小の設定を採用する」とする。
- 調整の固定値:
  - `candidate_low_threshold`: 初期3、変化幅±1/24h、範囲2..6
  - `file_bias_threshold`: 初期0.80、変化幅±0.03/24h、範囲0.70..0.90
  - ロールバック: 直近100件で再現率proxyが3%以上低下、または `cutoff_reason` 発生率が5%以上悪化
- 再現率proxyは行動ベースで算出する:
  - `C_q`: `manual_find` の候補 `ref` 集合
  - `U_q`: 同一 `trace_id` で後続利用された `ref` 集合（`manual_read` / `bridge_copy_*`）
  - `recall_proxy_q = |C_q ∩ U_q| / |U_q|`（`|U_q|=0` は `null` として集計除外）
  - `recall_proxy_100 = avg(recall_proxy_q)`（直近100件、`null`除外）をロールバック判定に使う
- 推定トークン量は `est_tokens = ceil((chars_in + chars_out) / 4)` を用い、`est_tokens_in` / `est_tokens_out` を併記する。

## 7. 出力（LLMへ渡す情報を最小化）

`manual_find` は原則本文を返さない。

- ユーザー向け出力（text）: `trace_id` / 指標サマリのみ
- ツール間連携の内部情報（structuredContent）: 候補IDや短いダイジェスト等（ユーザー表示には出さない）
- `unscanned_sections` は summary では件数中心とし、詳細列挙は `manual_hits(kind="unscanned")` に委譲する。
- 統合判断で抽出した `conflicts` / `gaps` の詳細は `manual_hits(kind="conflicts|gaps")` に委譲する。

本文が必要な場合のみ、後続で `manual_read(...)` を呼ぶ。

### レポート出力（サーバ側ファイル）

MVPではExploreのサーバ側レポート出力は行わない（チャット返却を最小化する）。

### 出力最小化と判断材料の分離

- ユーザー向け出力（text）は最小化し、`trace_id` / 指標サマリのみ返す。
- LLMの後続判断のための材料（候補ID、上位N件の短いダイジェスト等）は `structuredContent` にのみ含め、ユーザー表示には出さない。
- 打ち切りが発生した場合は `cutoff_reason` を summary に含め、それ以外は省略する。
- 整理済みの説明や図解などの成果物が必要な場合は、`vault_create` / `vault_write` / `vault_replace` 等の `vault_*` ツールで `VAULT_ROOT` 配下の任意プロジェクトフォルダに保存/更新し、チャットは要約＋保存先のみとする（Produce）。
- 「成果物」だけでなく、単なる疑問点解消のための詳細説明をファイルに残したい場合も同様に `vault_*` を使う（Produce）。
- manual修正が必要な場合は、`bridge_copy_file` に `manual_id + path` を渡して vault に全文転記し、以後の編集は vault 側で行う。
