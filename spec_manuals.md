# 統合MCPサーバ v2 Manual仕様（ドラフト）

## 1. スコープ

本ファイルは `manual_*` ツールの仕様を定義する。共通事項は `spec_v2.md` を参照。

## 2. Tool Catalog

- `manual_list()`
- `manual_ls({ manual_id? })`
- `manual_toc({ manual_id })`
- `manual_find({ manual_id?, query, intent?, max_stage?, only_unscanned_from_trace_id?, budget? })`
- `manual_read({ ref, scope, limits?, expand? })`
- `manual_excepts({ manual_id, section_id? })`
- `manual_hits({ trace_id, kind?, offset?, limit? })`

## 3. 探索固定ルール

- Stage 0: 正規化一致（本文） + 見出し一致
- Stage 1: loose一致 + 同義語/言い換え展開
- Stage 2: 例外語彙スキャン
- Stage 3: 参照追跡
- Stage 3.5: 統合判断（Stage 0〜3候補の統合・矛盾/欠落判定）
- Stage 4: 範囲拡張（条件成立時のみ）

`max_stage` ルール:

- `3|4` のみ許可
- `3`: Stage 4無効
- `4`: Stage 4条件付き有効
- Stage 3.5（統合判断）は `max_stage` に関わらず常時実行

Stage 4発火条件（初期）:

- 候補0件
- 候補3件未満
- 1ファイル偏重（80%以上、かつ候補総数5件以上）
- `intent=exceptions` で例外ヒット0

## 4. I/O Schemas

### `manual_list` Input

```json
{}
```

### `manual_list` Output

```json
{
  "items": [
    { "manual_id": "string" }
  ]
}
```

固定ルール:

- `items` は `manual_id` 昇順で安定ソート

### `manual_ls` Input

```json
{
  "manual_id": "string | null"
}
```

### `manual_ls` Output

```json
{
  "items": [
    {
      "manual_id": "string",
      "path": "string",
      "file_type": "md|json"
    }
  ]
}
```

固定ルール:

- `manual_id` 未指定時は全manual対象
- 指定時は当該manual配下のみ
- `items` は `manual_id + path` 昇順

### `manual_toc` Input

```json
{
  "manual_id": "string (required)"
}
```

### `manual_toc` Output

```json
{
  "items": [
    {
      "kind": "heading|json_file",
      "node_id": "string",
      "path": "string",
      "title": "string",
      "level": "number",
      "parent_id": "string | null",
      "line_start": "number",
      "line_end": "number"
    }
  ]
}
```

### `manual_find` Input

```json
{
  "manual_id": "string | null",
  "query": "string (required, non-empty)",
  "intent": "definition | procedure | eligibility | exceptions | compare | unknown | null",
  "max_stage": "number | null",
  "only_unscanned_from_trace_id": "string | null",
  "budget": {
    "max_candidates": "number | null",
    "time_ms": "number | null"
  }
}
```

固定ルール:

- `budget.time_ms` 未指定は `60000`
- `budget.max_candidates` 未指定は `200`
- `max_stage` 未指定は `DEFAULT_MAX_STAGE`（MVP既定: `4`）
- `only_unscanned_from_trace_id` 指定で trace が無効なら `not_found`
- 正規化（NFKC, 全半角, casefold, 改行/空白統一, 記号ゆらぎ吸収）を固定適用

### `manual_find` Output

```json
{
  "trace_id": "string",
  "summary": {
    "scanned_files": "number",
    "scanned_nodes": "number",
    "candidates": "number",
    "warnings": "number",
    "max_stage_applied": "number",
    "scope_expanded": "boolean",
    "unscanned_sections_count": "number",
    "integrated_nodes": "number",
    "signal_coverage": {
      "heading": "number",
      "normalized": "number",
      "loose": "number",
      "exceptions": "number",
      "reference": "number"
    },
    "file_bias_ratio": "number (0.0..1.0)",
    "conflict_count": "number",
    "gap_count": "number",
    "sufficiency_score": "number (0.0..1.0)",
    "integration_status": "ready | needs_followup | blocked",
    "cutoff_reason": "time_budget | candidate_cap | stage_cap | hard_limit | null"
  },
  "next_actions": [
    {
      "type": "manual_hits|manual_read|manual_find|stop",
      "reason": "string | null",
      "confidence": "number (0.0..1.0) | null",
      "params": "object | null"
    }
  ]
}
```

固定ルール:

- `next_actions` は必須（提案なしは `[]`）
- `next_actions.params` は最小パラメータのみ
- `next_actions.type` は次に呼ぶツール名を返す
- 候補一覧の取得は `type="manual_hits"` + `params.kind="candidates"`
- 未探索一覧の取得は `type="manual_hits"` + `params.kind="unscanned"`
- 衝突一覧の取得は `type="manual_hits"` + `params.kind="conflicts"`
- 欠落一覧の取得は `type="manual_hits"` + `params.kind="gaps"`
- 統合上位候補の取得は `type="manual_hits"` + `params.kind="integrated_top"`
- section読取は `type="manual_read"` + `params.scope="section"`
- file読取は `type="manual_read"` + `params.scope="file"`
- 統合判断からの推奨理由語彙:
  - `insufficient_candidates`: 候補不足のため追加探索または詳細確認を推奨
  - `resolve_conflicts`: `manual_read` で衝突候補の本文確認を優先
  - `fill_gaps`: `manual_find` で不足観点を再探索
  - `reduce_file_bias`: `manual_find` で偏り緩和の追加探索
  - `manual_completed`: 十分性条件を満たしたため停止

### `manual_read` Input

```json
{
  "ref": "object (required: {target, path, start_line?, json_path?})",
  "scope": "snippet|section|sections|file | null",
  "limits": {
    "max_sections": "number | null",
    "max_chars": "number | null",
    "allow_file": "boolean | null"
  },
  "expand": {
    "before_chars": "number | null",
    "after_chars": "number | null"
  }
}
```

固定ルール:

- 既定 `scope=snippet`
- `.md` の `scope=section` は当該見出し配下の本文（全子孫見出しを含む）を返す
- `scope=sections` は複数sectionをまとめて返す（取得対象は `ref`/実装定義に従う）
- `.md` の `scope=file` は `ALLOW_FILE_SCOPE=true` かつ `limits.allow_file=true` 必須
- `.json` の `scope=file` は許可
- `.json` の `scope=section|sections` は `invalid_scope_for_json`
- `limits.max_sections` 既定: `sections=20`, `file=20`
- `limits.max_chars` 既定: `8000`

### `manual_read` Output

```json
{
  "text": "string",
  "truncated": "boolean",
  "applied": {
    "scope": "snippet|section|sections|file",
    "max_sections": "number | null",
    "max_chars": "number | null"
  }
}
```

### `manual_excepts` Input

```json
{
  "manual_id": "string (required)",
  "section_id": "string | null"
}
```

### `manual_excepts` Output

```json
{
  "items": [
    {
      "path": "string",
      "start_line": "number",
      "snippet": "string"
    }
  ]
}
```

### `manual_hits` Input

```json
{
  "trace_id": "string (required)",
  "kind": "candidates | unscanned | conflicts | gaps | integrated_top | null",
  "offset": "number | null",
  "limit": "number | null"
}
```

固定ルール:

- `offset` 既定: `0`
- `kind` 既定: `candidates`
- `limit` 既定: `50`
- trace保持: 共通Config（`TRACE_MAX_KEEP`, `TRACE_TTL_SEC`）に従う

### `manual_hits` Output

```json
{
  "trace_id": "string",
  "kind": "candidates|unscanned|conflicts|gaps|integrated_top",
  "offset": "number",
  "limit": "number",
  "total": "number",
  "items": [
    {
      "ref": "object | null",
      "path": "string | null",
      "start_line": "number | null",
      "reason": "time_budget | candidate_cap | stage_cap | hard_limit | conflict | gap | ranked_by_integration | null",
      "signals": ["string"],
      "score": "number | null",
      "conflict_with": ["object"],
      "gap_hint": "string | null"
    }
  ]
}
```

## 5. manualログ拡張（info/warn）

### `manual_find` info

- `trace_id`, `scanned_files`, `scanned_nodes`, `candidates`, `warnings`
- `max_stage_applied`, `scope_expanded`, `cutoff_reason`, `unscanned_sections_count`
- `integrated_nodes`, `file_bias_ratio`, `conflict_count`, `gap_count`
- `sufficiency_score`, `integration_status`
- `next_actions`

### `manual_find` warn

- `escalation_reasons`
- `counts_by_signal`
