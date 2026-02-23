# 統合MCPサーバ v2 Manual仕様（現行）

最終更新: 2026-02-23

本書は `manual_*` ツールのI/O契約・入力制約・返却形式の正本である。  
設計意図/探索フローは `rag_design_v2.md`、運用要件/SLO/観測性は `requirements.md` を参照。

重複防止ルール:

- `manual_*` のパラメータ型、既定値、値域、返却shape、エラー条件は本書を正本とする。
- `rag_design_v2.md` / `requirements.md` に同内容を再掲する場合は要約のみとし、詳細値は本書へリンクする。
- 文書間で差分が出た場合は本書を優先し、他文書は追随修正する。

## 1. Tool Catalog

- `manual_ls({ id? })`
- `manual_toc({ manual_id, path_prefix?, max_files?, cursor?, depth?, max_headings_per_file? })`
- `manual_find({ query, manual_id, required_terms, expand_scope?, only_unscanned_from_trace_id?, budget?, include_claim_graph?, use_cache?, inline_hits? })`
- `manual_hits({ trace_id, kind?, offset?, limit? })`
- `manual_read({ ref, max_chars? })`
- `manual_scan({ manual_id, path, start_line?, cursor?, max_chars? })`

## 2. `manual_ls`

Input:

```json
{
  "id": "string | null"
}
```

Output:

```json
{
  "id": "string",
  "items": [
    {
      "id": "string",
      "name": "string",
      "kind": "dir|file",
      "path?": "string",
      "file_type?": "md|json"
    }
  ]
}
```

固定ルール:

- `id` 未指定時は `manuals` を適用する。
- `id=manuals` は `MANUALS_ROOT` 直下の manual ディレクトリのみ返す（1階層）。
- `manual_ls(id="manuals")` は繰り返し呼び出し可能。各呼び出しで最新の manual 一覧を返す。
- `manual_toc` / `manual_find` / `manual_read` / `manual_scan` の前に、同一セッションで `manual_ls` の成功呼び出しが必須。
- manual ディレクトリ `id` を指定すると、その直下の子要素のみ返す（再帰しない）。
- ディレクトリ子は `kind=dir`、対象拡張子（`.md/.json`）のファイルは `kind=file` を返す。
- file `id` は展開不可（`invalid_parameter`）。

## 3. `manual_toc`

Input:

```json
{
  "manual_id": "string",
  "path_prefix": "string | null",
  "max_files": "number | null",
  "cursor": {
    "offset": "number"
  } | "number | string (offset shorthand) | null",
  "depth": "shallow | deep | null",
  "max_headings_per_file": "number | null"
}
```

Output:

```json
{
  "applied": {
    "manual_id": "string",
    "path_prefix": "string",
    "depth": "shallow | deep",
    "max_files": "number",
    "include_headings": "boolean",
    "max_headings_per_file": "number",
    "offset": "number"
  },
  "total_files": "number",
  "next_cursor": {
    "offset": "number"
  },
  "items": [
    {
      "path": "string",
      "headings": [
        {
          "title": "string",
          "line_start": "number"
        }
      ]
    }
  ]
}
```

固定ルール:

- `depth` 既定値は `shallow`。`deep` は見出しを返す。
- `depth=deep` の場合、`path_prefix` は必須。
- `path_prefix` を空にする場合、`max_files <= 50` を必須とする。
- `depth=deep` の場合、`max_files <= 50` を必須とする。
- `include_headings` は廃止。指定された場合は受理しない。
- `manual_id="manuals"`（root id）は受理しない。`manual_ls(id="manuals").items[].id` を使用する。
- 対象ファイル数がハード上限（200件）を超える場合は `needs_narrow_scope` を返す。

## 4. `manual_find`

Input:

```json
{
  "query": "string",
  "manual_id": "string",
  "expand_scope": "boolean | null",
  "required_terms": ["string"],
  "only_unscanned_from_trace_id": "string | null",
  "include_claim_graph": "boolean | null",
  "use_cache": "boolean | null",
  "inline_hits": {
    "limit": "number | null"
  },
  "budget": {
    "max_candidates": "number | null",
    "time_ms": "number | null"
  }
}
```

Output:

```json
{
  "trace_id": "string",
  "applied": {
    "manual_id": "string",
    "requested_expand_scope": "boolean | null",
    "expand_scope": "boolean",
    "required_terms_source": "user",
    "required_terms_decision_reason": "string | null",
    "requested_required_terms": ["string"],
    "required_terms": ["string"],
    "required_terms_df_filtered": [
      {
        "term": "string",
        "doc_freq": "number",
        "doc_freq_ratio": "number",
        "reason": "too_rare|too_common",
        "dropped": "boolean"
      }
    ],
    "required_terms_relaxed": "boolean",
    "required_terms_relax_reason": "string | null",
    "required_effect_status": "required_effective|term_dropped_or_weakened|required_none_matched|required_fallback",
    "required_failure_reason": "string | null",
    "required_strict_candidates": "number",
    "required_filtered_candidates": "number",
    "required_terms_match_stats": [
      {
        "term": "string",
        "matched_docs": "number",
        "matched_doc_ratio": "number"
      }
    ],
    "required_terms_missing": ["string"],
    "required_top_k": "number",
    "required_top_hits": "number",
    "selected_gate": "single|single_base|single_required|g0|g_req",
    "gate_selection_reason": "string | null",
    "sem_cache_used": "boolean",
    "sem_cache_hit": "boolean",
    "sem_cache_mode": "bypass|miss|exact|semantic|guard_revalidate",
    "sem_cache_score": "number | null",
    "sem_cache_latency_saved_ms": "number | null"
  },
  "claim_graph?": {
    "claims": [
      {
        "claim_id": "string",
        "facet": "definition|procedure|eligibility|exceptions|compare|unknown",
        "text": "string",
        "status": "supported|conflicted|unresolved",
        "confidence": "number"
      }
    ],
    "evidences": [
      {
        "evidence_id": "string",
        "ref": {
          "target": "manual",
          "manual_id": "string",
          "path": "string",
          "start_line": "number"
        },
        "signals": ["exact|required_term|required_term_and|required_terms_rrf|gate_rrf|phrase|anchor|number_context|proximity|exceptions|code_exact|prf|exploration|query_decomp_rrf|definition_title"],
        "score": "number",
        "snippet_digest": "string"
      }
    ],
    "edges": [
      {
        "from_claim_id": "string",
        "to_evidence_id": "string",
        "relation": "supports|contradicts|requires_followup",
        "confidence": "number"
      }
    ],
    "facets": [
      {
        "facet": "definition|procedure|eligibility|exceptions|compare|unknown",
        "claim_count": "number",
        "supported_count": "number",
        "conflicted_count": "number",
        "unresolved_count": "number",
        "coverage_status": "covered|partial|missing"
      }
    ]
  },
  "summary": {
    "scanned_files": "number",
    "scanned_nodes": "number",
    "candidates": "number",
    "file_bias_ratio": "number",
    "conflict_count": "number",
    "gap_count": "number",
    "integration_status": "ready|needs_followup|blocked"
  },
  "next_actions": [
    {
      "type": "manual_hits|manual_read|manual_find|manual_scan",
      "confidence": "number | null",
      "params": "object | null"
    }
  ]
}
```

公開MCPツール（`app.py`）の返却形式:

```json
{
  "trace_id": "string",
  "candidates": "number",
  "status": "required_effective|term_dropped_or_weakened|required_none_matched|required_fallback",
  "failure_reason": "string | null",
  "inline_hits?": {
    "trace_id": "string",
    "kind": "integrated_top",
    "offset": "0",
    "limit": "number",
    "total": "number",
    "items": "array"
  },
  "next_actions": [
    {
      "type": "manual_hits|manual_read|manual_find|manual_scan",
      "params": "object | null"
    }
  ]
}
```

固定ルール:

- `summary` は retrieval-only の軽量診断であり、`claim_graph` 非依存に算出する。
- 一次候補の検索スコアは lexical-only（`idf × tf`）を基礎とし、coverage/phrase/number-context/proximity の加点とノイズ減点で構成する。
- lexical加減点の係数は環境変数（`LEXICAL_*`）で調整可能。
- `MANUAL_FIND_EXPLORATION_*` により、候補集合に探索バケット（低prior候補）を固定比率で混在させる。
- `include_claim_graph=true` のときのみ `claim_graph` を構築して返す（feature flag有効時）。
- `include_claim_graph=true` でも feature flag（`MANUAL_FIND_CLAIM_GRAPH_ENABLED`）が無効な場合は空の `claim_graph` を返す。
- 公開MCPツール（`app.py`）では常時最小レスポンスを返すため、`include_claim_graph=true` でも `claim_graph` は返さない。
- `summary.conflict_count` と `manual_hits(kind="conflicts").total` は一致する（現行 retrieval-only 実装では通常 `0`）。
- `summary.gap_count` と `manual_hits(kind="gaps").total` は一致する（`gaps` は retrieval-gap のプレースホルダ行を返しうる）。
- `manual_id` は必須（未指定/空文字は `invalid_parameter`）。
- `manual_id="manuals"`（root id）は受理しない。`manual_ls(id="manuals").items[].id` を使用する。
- 検索スコープは常に指定 `manual_id` 配下に限定する（manual間の自動拡張は行わない）。
- `required_terms` は必須。文字列配列のみ許可し、`1..2` 語（空文字不可）。
- `manual_find` は `g0(requiredなし)` と `g_req(requiredあり)` を常時実行し、RRF統合で最終候補を決定する。
- `g_req` が0件の場合は `g0` を採用し、`applied.required_terms_relaxed=true` を返す。
- `applied.required_terms_source` は `user` を返す。
- `applied.required_terms_decision_reason` は `provided_by_caller` を返す。
- `applied.requested_required_terms` は入力値（正規化後）を返す。
- `applied.required_terms` は実際に検索へ適用した語を返す。
- `applied.selected_gate` は最終採用ゲート（`g0|g_req` 等）を返す。
- `applied.gate_selection_reason` はゲート採用理由を返す。
- `required_terms` は検索前にDFガードを適用し、診断情報を `applied.required_terms_df_filtered` に記録する。
- `applied.required_terms_df_filtered[].dropped=true` は検索語から除外されたことを示す（現行は `too_common` が対象）。
- `applied.required_terms_df_filtered[].dropped=false` は検索語として保持されたことを示す（現行は `too_rare` が対象）。
- `applied.required_effect_status` は strict/filtered required 候補数と最終上位（top-k）への残存状況を加味した診断結果を返す。
- `applied.required_strict_candidates` と `applied.required_filtered_candidates` は診断に使った各候補数を返す。
- `applied.required_terms_match_stats` は要求語ごとの manual 内 doc hit 数を返す。
- `applied.required_terms_missing` は manual 内で未検出だった要求語を返す。
- `applied.required_top_k` と `applied.required_top_hits` は最終候補上位における required signal 残存数を返す。
- 網羅要求（例: 「網羅的」「全て参照してから」）では `next_actions` が `manual_scan` を優先しうる。
- `required_terms` を2語指定した場合は `A` / `B` / `A+B` の3passで評価し、RRF統合で再ランキングする。
- `g_req` が0件の場合、`applied.required_terms_relaxed=true` と `applied.required_terms_relax_reason=zero_candidates_with_required_terms` を返す。
- レスポンスの `applied.requested_expand_scope` はリクエスト入力値（未指定なら `null`）を返す。
- レスポンスの `applied.expand_scope` は実適用値を返す（現行実装では常に `false`）。
- Query decomposition + RRF 有効時は、sub-query 失敗を許容して継続し、全sub-query失敗または結合候補0件時のみ通常検索にフォールバックする。
- `expand_scope` と `include_claim_graph` と `use_cache` は boolean のみ許可（非booleanは `invalid_parameter`）。
- `use_cache` 未指定時は `SEM_CACHE_ENABLED` 設定値を適用する。
- `include_claim_graph=true` の場合は semantic cache をバイパスする（payload形状/意味の混在を避けるため）。
- 公開MCPツール（`app.py`）の `manual_find` 出力は最小形式（`trace_id`, `candidates`, `status`, `failure_reason`, `next_actions`）を返す。
- `inline_hits` を指定した場合、公開MCPツール（`app.py`）の `manual_find` は `manual_hits(kind="integrated_top", offset=0, compact=true)` と同形の `inline_hits` を同梱する。
- `inline_hits.limit` は整数かつ `>= 1`。公開MCPツールでは最大 `5` 件に制限する（超過値は `5` に丸める）。
- 公開MCPツール（`app.py`）の compact `next_actions` は常に空配列（`[]`）を返す（誘導は返さない）。
- 公開MCPツール（`app.py`）の `next_actions[]` は `type` と `params` のみを返し、`confidence` は省略する。
- cache hit でも `summary.gap_count/conflict_count` が閾値（`SEM_CACHE_MAX_SUMMARY_GAP/SEM_CACHE_MAX_SUMMARY_CONFLICT`）を超える場合は再探索する。
- 非compact `manual_find` は `applied.sem_cache_*` 診断（used/hit/mode/score/latency_saved_ms）を返す。
- 公開MCPツール（`app.py`）の compact `manual_find` は `applied` を返さないため、`sem_cache_*` 診断は返さない。
- 内部 `trace_payload` / Semantic Cache 保存payloadでは、required/gate 診断（`required_*`, `selected_gate`, `gate_selection_reason`）は `applied` に集約して保持し、トップレベルへ重複保存しない。
- 旧形式trace（トップレベルに同診断キーを持つpayload）を読み出した場合は、実装側で `applied` へ後方互換補完して返却する。
- `budget.time_ms` 既定値は `60000`、`budget.max_candidates` 既定値は `200`。
- `budget.time_ms` と `budget.max_candidates` は整数かつ `>= 1`。
- `manual_find` は動的カットオフを適用し、返却候補数は `min(budget.max_candidates, 50)` を上限として score/coverage に応じてさらに縮小しうる。
- 最終ランキングでは同一 `path` の過度な集中を抑える多様性リランキング（同一pathへの減衰）を適用する。
- 整数変換不能値と `true/false` は `invalid_parameter`。
- `manual_hits(kind="candidates")` の各 item は `matched_tokens` / `token_hits` / `match_coverage` / `rank_explain` を含みうる。

## 5. `manual_hits`

Input:

```json
{
  "trace_id": "string",
  "kind": "candidates|unscanned|conflicts|gaps|integrated_top|claims|evidences|edges|gate_runs|fusion_debug|null",
  "offset": "number | null",
  "limit": "number | null"
}
```

Output:

```json
{
  "trace_id": "string",
  "kind": "string",
  "offset": "number",
  "limit": "number",
  "total": "number",
  "items": "array"
}
```

固定ルール:

- `kind` 未指定時は `candidates`。
- `offset` 未指定時は `0`、`limit` 未指定時は `50`。
- `offset` は整数かつ `>= 0`、`limit` は整数かつ `>= 1`。
- `offset` / `limit` は `true/false` を許可しない（`invalid_parameter`）。
- `kind=candidates` の `items[]` は圧縮形式を返す（`target/json_path/title` は返さない）。
- `kind=candidates` で全件の `manual_id` が同一なら、`manual_id` はレスポンス上位に1回だけ返し、`items[].ref.manual_id` は省略する。
- `kind in {"claims","evidences","edges"}` は、直前の `manual_find` で `include_claim_graph=true` を指定しなかった場合は空配列を返す。
- `kind in {"claims","evidences","edges"}` は、feature flag により `claim_graph` 構築が無効な場合も空配列を返す。
- `kind=gaps` は retrieval-only `summary.gap_count` に合わせた診断行（プレースホルダ含む）を返す。
- `kind=conflicts` は retrieval-only `summary.conflict_count` に合わせた診断行を返す（現行実装では通常空）。
- 公開MCPツール（`app.py`）の `kind in {"candidates","integrated_top"}` は常に最小形式（`ref`, `score`, `matched_tokens` と、`integrated_top` のみ `title`）を返す。

## 6. `manual_read`

Input:

```json
{
  "ref": {
    "manual_id": "string",
    "path": "string",
    "start_line": "number | null"
  },
  "max_chars": "number | null"
}
```

固定ルール:

- `manual_read` は markdown の section 取得専用。`.json` は対象外（`manual_scan` を使用する）。
- `ref.manual_id="manuals"`（root id）は受理しない。`manual_ls(id="manuals").items[].id` を使用する。
- `scope` は後方互換入力として `null|"section"` のみ許可し、それ以外は `invalid_parameter`。
- `allow_file` / `expand` は受理しない（`invalid_parameter`）。
- `max_chars` は整数のみ許可。未指定時は既定値 `12000`。範囲は `256..50000`。
- `ref.start_line` は見出し開始行に一致する section 指定として扱う。未指定時は先頭section。
- 同一セクション再要求を検知した場合は、同一ファイルの次行から `manual_scan` 相当の自動フォールバックを行う。

Output:

```json
{
  "text": "string",
  "truncated": "boolean",
  "applied": {
    "scope": "section",
    "max_sections": "null",
    "max_chars": "number",
    "mode": "read|scan_fallback"
  }
}
```

## 7. `manual_scan`

Input:

```json
{
  "manual_id": "string",
  "path": "string",
  "start_line": "number | null",
  "cursor": {
    "start_line": "number | null",
    "char_offset": "number | null"
  } | "number | string (char_offset shorthand) | null",
  "max_chars": "number | null"
}
```

固定ルール:

- `start_line` 指定時はそれを優先し、未指定時は `cursor.start_line`（未指定なら1）を使う。
- `cursor` は object 形式に加えて、`char_offset` の shorthand として `number|string` も許可する。
- `manual_id="manuals"`（root id）は受理しない。`manual_ls(id="manuals").items[].id` を使用する。
- `start_line`（または `cursor.start_line`）は整数かつ対象ファイルの総行数範囲内のみ許可。
- `max_chars` は整数のみ許可。未指定時は既定値 `12000`（`SCAN_MAX_CHARS`）。範囲は `256..50000`。

Output:

```json
{
  "manual_id": "string",
  "path": "string",
  "text": "string",
  "applied_range": {
    "start_line": "number",
    "end_line": "number"
  },
  "next_cursor": {
    "char_offset": "number | null"
  },
  "eof": "boolean",
  "truncated": "boolean",
  "truncated_reason": "none|max_chars",
  "applied": {
    "max_chars": "number"
  }
}
```

## 8. 設計判断メモ（2026-02-23）

採用:

- `summary` は retrieval-only 診断として扱い、`claim_graph` 非依存で算出する。
- `claim_graph` は `include_claim_graph=true` の要求時のみ構築する（通常経路の負荷削減）。
- `manual_read` は section-only に限定し、逐次読解は `manual_scan` に寄せる。

棄却:

- `claim_graph` 常時構築 + `summary` への常時反映案:
  - retrieval 指標の改善が確認できず、`needs_followup` の過剰化とレイテンシ増を招きやすかったため。
- `manual_read` で `snippet|sections|file` を維持する案:
  - APIの複雑性に対して利用価値が低く、`manual_scan` と役割が重複していたため。
- `claim_graph` の facet を増やして（例 `condition`, `amount`）継続利用する案:
  - 辞書依存が強まり汎用性を損ないやすく、unknown低減の効果が未検証だったため。
