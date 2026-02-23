# mcp_v2

`mcp_v2` は、manual と vault を扱う FastMCP サーバーです。  
現行の公開ツールは次の11個です。

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
3. `manual_toc` は既定 `depth=shallow` で構造把握し、必要時のみ `depth=deep` と `path_prefix` で見出し取得
4. `manual_find`（`required_terms` は必須: 1〜2語）の結果 `trace_id` を `manual_hits` に渡す
   - 公開MCPツールでは `manual_find` / `manual_hits` は常時軽量レスポンス（compact固定）
5. vault は必要に応じて `vault_ls({ path: null })` で探索してから、`vault_read` / `vault_scan` / `vault_create` / `vault_replace` を呼ぶ

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

### 4) `manual_toc` で `needs_narrow_scope` が返る

症状:

- `toc scope too large` / `needs_narrow_scope` が返る

対処:

- `path_prefix` を指定して対象ファイルを絞ってください。
- `path_prefix` が空の場合は `max_files <= 50` を守ってください。

### 5) 数値パラメータに `true` / `false` を渡して `invalid_parameter` になる

症状:

- `offset` / `limit` / `budget.*` / `max_replacements` などで `invalid_parameter` が返る

対処:

- `true` / `false` ではなく整数値を渡してください。

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

## 11. Eval駆動RAG（manual_find）実行

評価データセット（30問）は `evals/manual_find_gold.jsonl` にあります。  
次のコマンドで評価を実行できます（結果は標準出力のみで、ファイル保存しません）。

```bash
source .venv/bin/activate
python scripts/eval_manual_find.py
```

閾値をCIで強制したい場合:

```bash
python scripts/eval_manual_find.py --enforce-thresholds
```

Semantic Cache の有無を比較する場合:

```bash
python scripts/eval_manual_find.py --compare-sem-cache
```

比較モードでは次を同一条件で2回実行します。

- `SEM_CACHE_ENABLED=false`（baseline）
- `SEM_CACHE_ENABLED=true`（with_sem_cache）

出力レポートには `baseline` / `with_sem_cache` / `metrics_delta` が含まれます。

Query decomposition + RRF の有無を比較する場合:

```bash
python scripts/eval_manual_find.py --compare-query-decomp
```

比較モードでは次を同一条件で2回実行します。

- `MANUAL_FIND_QUERY_DECOMP_ENABLED=false`（baseline）
- `MANUAL_FIND_QUERY_DECOMP_ENABLED=true`（with_query_decomp）

## 12. ディレクトリ構成

- `manuals/<manual_id>/`
- `vault/`
- `src/mcp_v2_server/`（実装本体）
- `docs/`（仕様・要件）
- `tests/`

## 13. 主なドキュメント

- `docs/spec_v2.md`（共通仕様）
- `docs/spec_manuals.md`（manual系仕様）
- `docs/spec_vault.md`（vault系仕様）
- `docs/requirements.md`（要件）
- `docs/rag_design_v2.md`（探索設計メモ）

## 14. 環境変数

- `WORKSPACE_ROOT` (default: `.`)
- `MANUALS_ROOT` (default: `${WORKSPACE_ROOT}/manuals`)
- `VAULT_ROOT` (default: `${WORKSPACE_ROOT}/vault`)
- `ADAPTIVE_TUNING` (default: `true`)
- `ADAPTIVE_STATS_PATH` (default: `${VAULT_ROOT}/.system/adaptive_stats.jsonl`)
- `ADAPTIVE_MIN_RECALL` (default: `0.90`)
- `ADAPTIVE_CANDIDATE_LOW_BASE` (default: `3`)
- `ADAPTIVE_FILE_BIAS_BASE` (default: `0.80`)
- `COVERAGE_MIN_RATIO` (default: `0.90`)
- `MARGINAL_GAIN_MIN` (default: `0.02`)
- `SPARSE_QUERY_COVERAGE_WEIGHT` (default: `0.35`)
- `LEXICAL_COVERAGE_WEIGHT` (default: `0.50`)
- `LEXICAL_PHRASE_WEIGHT` (default: `0.50`)
- `LEXICAL_NUMBER_CONTEXT_BONUS` (default: `0.80`)
- `LEXICAL_PROXIMITY_BONUS_NEAR` (default: `1.00`)
- `LEXICAL_PROXIMITY_BONUS_FAR` (default: `0.50`)
- `LEXICAL_LENGTH_PENALTY_WEIGHT` (default: `0.20`)
- `MANUAL_FIND_EXPLORATION_ENABLED` (default: `true`)
- `MANUAL_FIND_EXPLORATION_RATIO` (default: `0.20`)
- `MANUAL_FIND_EXPLORATION_MIN_CANDIDATES` (default: `2`)
- `MANUAL_FIND_EXPLORATION_SCORE_SCALE` (default: `0.35`)
- `MANUAL_FIND_QUERY_DECOMP_ENABLED` (default: `true`)
- `MANUAL_FIND_QUERY_DECOMP_MAX_SUB_QUERIES` (default: `3`)
- `MANUAL_FIND_QUERY_DECOMP_RRF_K` (default: `60`)
- `MANUAL_FIND_QUERY_DECOMP_BASE_WEIGHT` (default: `0.30`, `0.0` でRRF寄り、`1.0` でbase寄り)
- `MANUAL_FIND_SCAN_HARD_CAP` (default: `5000`)
- `MANUAL_FIND_PER_FILE_CANDIDATE_CAP` (default: `8`)
- `MANUAL_FIND_FILE_PRESCAN_ENABLED` (default: `true`)
- `TRACE_MAX_KEEP` (default: `100`)
- `TRACE_TTL_SEC` (default: `1800`)
- `ALLOW_FILE_SCOPE` (default: `false`)
- `SEM_CACHE_ENABLED` (default: `true`)
- `SEM_CACHE_TTL_SEC` (default: `1800`)
- `SEM_CACHE_MAX_KEEP` (default: `500`)
- `SEM_CACHE_SIM_THRESHOLD` (default: `0.92`)
- `SEM_CACHE_EMBEDDING_PROVIDER` (default: `none`)
- `SEM_CACHE_MAX_SUMMARY_GAP` (default: `-1`, `-1` は無効)
- `SEM_CACHE_MAX_SUMMARY_CONFLICT` (default: `-1`, `-1` は無効)

`SEM_CACHE_EMBEDDING_PROVIDER` は現時点では `none` のみサポートします。
このため現行の `--compare-sem-cache` は主に exact cache の効果測定になります（`semantic` hit は通常発生しません）。
`manual_find` の `use_cache` パラメータで、リクエスト単位の cache バイパスができます。
semantic cache はプロセス内メモリ保持です。サーバ再起動でキャッシュはクリアされます。
