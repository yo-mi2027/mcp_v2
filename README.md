# mcp_v2

`mcp_v2` は、manual/vault/bridge/tooling 系ツールを統合した FastMCP サーバーです。

## Requirements

- Python `3.12.x` (必須: `>=3.12,<3.13`)
- macOS / Linux / Windows (stdio 実行を想定)

## Quick Start (pip)

macOS / Linux:

```bash
cd /path/to/mcp_v2
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -r requirements.txt
```

Windows (PowerShell):

```powershell
cd C:\path\to\mcp_v2
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.txt
```

`requirements.txt` には `-e .` を含めているため、`mcp_v2_server` パッケージが editable install されます。

## Quick Start (uv)

```bash
cd /path/to/mcp_v2
uv sync
```

## Run Server (stdio)

macOS / Linux:

```bash
source .venv/bin/activate
python -m mcp_v2_server.app --stdio
```

Windows (PowerShell):

```powershell
.venv\Scripts\Activate.ps1
python -m mcp_v2_server.app --stdio
```

`uv` の場合:

```bash
uv run python -m mcp_v2_server.app --stdio
```

## MCP Client Config Example (Codex)

`config.toml` 例:

```toml
[mcp_servers.mcp-v2]
command = "/absolute/path/to/mcp_v2/.venv/bin/python"
args = ["-m", "mcp_v2_server.app", "--stdio"]
```

Windows 例 (`'...'` は TOML literal string):

```toml
[mcp_servers.mcp-v2]
command = 'C:\absolute\path\to\mcp_v2\.venv\Scripts\python.exe'
args = ["-m", "mcp_v2_server.app", "--stdio"]
```

注意:
- Python が 3.12 以外（例: 3.14）の venv だと起動前に失敗します。
- もし import エラーが出る場合は `pip install -r requirements.txt` を再実行してください。

## Directory Layout

- `manuals/<manual_id>/`
- `vault/`
- `src/mcp_v2_server/` (実装本体)
- `docs/` (仕様・要件)

## Test

macOS / Linux:

```bash
source .venv/bin/activate
pytest -q
```

Windows (PowerShell):

```powershell
.venv\Scripts\Activate.ps1
pytest -q
```

`uv` の場合:

```bash
uv run pytest -q
```

## Main Docs

- `docs/requirements.md`
- `docs/spec_v2.md`
- `docs/spec_manuals.md`
- `docs/spec_vault.md`
- `docs/spec_bridge.md`
- `docs/spec_tooling.md`
- `docs/rag_design_v2.md`

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
