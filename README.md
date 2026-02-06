# mcp_v2

統合版MCPサーバ（v2）の設計・要件定義を置くフォルダです。

前提ディレクトリ構造（採用）:

- `manuals/<manual_id>/`
- `vault/`

## Docs

- `requirements.md`: 要件定義（目的、スコープ、機能/非機能、評価指標）
- `spec_v2.md`: 共通基盤仕様（Config、安全要件、共通ログ/出力、`next_actions`/`ref` モデル）
- `spec_manuals.md`: `manual_*` 仕様（探索Stage、I/Oスキーマ）
- `spec_vault.md`: `vault_*` / `artifact_audit` 仕様（行レンジ走査、カバレッジ監査、I/Oスキーマ）
- `spec_bridge.md`: `bridge_*` 仕様（manual->vaultコピー、I/Oスキーマ）
- `spec_tooling.md`: `get_tooling_guide` 仕様（固定カタログと `first_tool`）
- `rag_design_v2.md`: RAG設計（探索ワークフロー、検索実装仕様、見落とし検知）

## Implementation

Python/FastMCP実装は `src/mcp_v2_server` にあります。
実行ターゲットは Python `3.12` です。

### Setup

`uv` を使う場合:

```bash
uv sync
```

`pip` を使う場合（`requirements.txt`）:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run (stdio)

`uv` の場合:

```bash
uv run python -m mcp_v2_server.app --stdio
```

`pip` の場合:

```bash
source .venv/bin/activate
PYTHONPATH=src python -m mcp_v2_server.app --stdio
```

### Test

`uv` の場合:

```bash
uv run pytest -q
```

`pip` の場合:

```bash
source .venv/bin/activate
PYTHONPATH=src pytest -q
```

## Environment Variables

- `WORKSPACE_ROOT` (default: `.`)
- `MANUALS_ROOT` (default: `${WORKSPACE_ROOT}/manuals`)
- `VAULT_ROOT` (default: `${WORKSPACE_ROOT}/vault`)
- `LOG_LEVEL` (default: `info`)
- `ADAPTIVE_TUNING` (default: `true`)
- `ADAPTIVE_STATS_PATH` (default: `${VAULT_ROOT}/.system/adaptive_stats.jsonl`)
- `ADAPTIVE_MIN_RECALL` (default: `0.90`)
- `ADAPTIVE_CANDIDATE_LOW_BASE` (default: `3`)
- `ADAPTIVE_FILE_BIAS_BASE` (default: `0.80`)
- `VAULT_SCAN_DEFAULT_CHUNK_LINES` (default: `80`)
- `VAULT_SCAN_MAX_CHUNK_LINES` (default: `200`)
- `COVERAGE_MIN_RATIO` (default: `0.90`)
- `MARGINAL_GAIN_MIN` (default: `0.02`)
- `TRACE_MAX_KEEP` (default: `100`)
- `TRACE_TTL_SEC` (default: `1800`)
- `ALLOW_FILE_SCOPE` (default: `false`)
- `HARD_MAX_SECTIONS` (default: `20`)
- `HARD_MAX_CHARS` (default: `20000`)
- `DEFAULT_MAX_STAGE` (default: `4`)
- `HARD_MAX_STAGE` (default: `4`)
- `ARTIFACTS_DIR` (default: `artifacts`)
