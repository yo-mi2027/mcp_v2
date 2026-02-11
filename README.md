# mcp_v2

`mcp_v2` は、manual と vault を扱う FastMCP サーバーです。  
現行の公開ツールは次の10個です。

- `manual_ls`
- `manual_toc`
- `manual_find`
- `manual_hits`
- `manual_read`
- `manual_scan`
- `vault_create`
- `vault_read`
- `vault_scan`
- `vault_replace`

## 1. このREADMEでできること

この手順だけで、初心者でも次を完了できます。

1. Python 環境の準備
2. 依存パッケージのインストール
3. MCPサーバーの起動
4. Codex などのMCPクライアント接続設定
5. 動作確認

## 2. 前提

- Python `3.12.x`（必須: `>=3.12,<3.13`）
- OS: macOS / Linux / Windows
- ターミナルを使えること

Pythonバージョン確認:

```bash
python --version
```

Windowsで `python` が 3.12 以外の場合:

```powershell
py -3.12 --version
```

## 3. プロジェクト取得

GitHub から取得する場合:

```bash
git clone https://github.com/yo-mi2027/mcp_v2.git
cd mcp_v2
```

すでに取得済みなら、`mcp_v2` ディレクトリへ移動してください。

## 4. セットアップ（pip推奨）

### macOS / Linux

```bash
cd /path/to/mcp_v2
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -r requirements.txt
```

### Windows (PowerShell)

```powershell
cd C:\path\to\mcp_v2
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.txt
```

補足:

- `requirements.txt` には `-e .` を含むため、`mcp_v2_server` は editable install されます。

## 5. セットアップ（uvを使う場合）

`uv` を使う場合は次だけで準備できます。

```bash
cd /path/to/mcp_v2
uv sync
```

## 6. サーバー起動（stdio）

### macOS / Linux

```bash
source .venv/bin/activate
python -m mcp_v2_server.app --stdio
```

### Windows (PowerShell)

```powershell
.venv\Scripts\Activate.ps1
python -m mcp_v2_server.app --stdio
```

### uv の場合

```bash
uv run python -m mcp_v2_server.app --stdio
```

`--stdio` で起動すると、MCPクライアントから接続されるまで待機します。  
終了するには `Ctrl + C` を押してください。

## 7. Codex のMCP設定例

`config.toml` に以下を追加します。

### macOS / Linux

```toml
[mcp_servers.mcp-v2]
command = "/absolute/path/to/mcp_v2/.venv/bin/python"
args = ["-m", "mcp_v2_server.app", "--stdio"]
```

### Windows

```toml
[mcp_servers.mcp-v2]
command = 'C:\absolute\path\to\mcp_v2\.venv\Scripts\python.exe'
args = ["-m", "mcp_v2_server.app", "--stdio"]
```

## 8. はじめての動作確認

MCPクライアントから次を順に呼び出してください。

1. `manual_ls({ id: "manuals" })` で manual 一覧を取得
2. 返ってきた `dir` の `id` を `manual_ls` に渡して、1階層ずつ辿る
3. 必要な manual を `manual_toc` / `manual_find` で読む
4. `manual_find` の結果 `trace_id` を `manual_hits` に渡す

これで「一覧 -> 検索 -> 詳細取得」の一連動作を確認できます。

## 9. よくあるエラーと対処

### 1) Pythonバージョン不一致

症状:

- 起動時に依存関係や互換性エラーが出る

対処:

- Python 3.12 で venv を作り直してください。

### 2) `ModuleNotFoundError` / import エラー

対処:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

Windows:

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3) クライアントから接続できない

確認ポイント:

- `command` が実在する Python 実行ファイルを指しているか
- `args` が `-m mcp_v2_server.app --stdio` になっているか
- クライアント再起動後に反映されるか

## 10. テスト実行

### macOS / Linux

```bash
source .venv/bin/activate
pytest -q
```

### Windows (PowerShell)

```powershell
.venv\Scripts\Activate.ps1
pytest -q
```

### uv

```bash
uv run pytest -q
```

## 11. ディレクトリ構成

- `manuals/<manual_id>/`
- `vault/`
- `src/mcp_v2_server/`（実装本体）
- `docs/`（仕様・要件）
- `tests/`

## 12. 主なドキュメント

- `docs/spec_v2.md`（共通仕様）
- `docs/spec_manuals.md`（manual系仕様）
- `docs/spec_vault.md`（vault系仕様）
- `docs/requirements.md`（要件）
- `docs/rag_design_v2.md`（探索設計メモ）

## 13. 環境変数

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
- `DEFAULT_MAX_STAGE` (default: `4`, allowed: `3|4`)
